"""Generate TouchDesigner icon sets from recipes.

This is the authoring half of what used to be one tool. `tdtheme` loads and
installs finished themes; this builds them. It owns the pixel side of
theming: a TIFF codec, a set of recolour and adjustment ops, and the recipe
engine that combines them into a complete 97-icon directory.

Import it as `tdthememaker.icons`; the command line lives in `cli.py`.

    python3 -m tdthememaker.cli build midnight
    python3 -m tdthememaker.cli build midnight --check
    python3 -m tdthememaker.cli preview midnight

What the recipes are for
------------------------

TouchDesigner reads its UI icons from

    TouchDesigner.app/Contents/Resources/tfs/Config/Icons/*.tiff

97 TIFF files, no fallback, loaded once and cached for the process lifetime
(see `../docs/reverse-engineering.md` for how that path was recovered
from `libUI.dylib`). Colour themes are text overlays, but icons are not text,
so a theme author has to produce real files. A recipe is the reviewable form
of that work: a short list of ops that, applied to the shipped baseline,
determines the whole set. `midnight` is 2.5 KB of recipe against 128 KB of
TIFF, and the diff shows what changed and why.

The recipe is the source; the generated directory is the artifact. A theme
that is going to be sent to someone else ships the directory.

Why a hand-written codec rather than a library
----------------------------------------------

The project has a hard rule: no third-party dependencies. Neither Pillow nor
tifffile is installed here, and asking for them would break that rule. The
subset needed is small and completely known: 8bpc, RGB/RGBA, photometric 2,
single strip, LZW or no compression. That is a few hundred lines of `struct`
arithmetic, and the round-trip is testable - `../tests/test_icons.py` decodes all
97 shipped files, re-encodes them, and asserts the pixels survive. Where an
independent check is wanted, `sips` (macOS ImageIO, i.e. a different libtiff)
is asked to read what we wrote.

The codec is duplicated from `../tdicons.py` rather than imported, so
this tool stands alone and can move to its own repository. Only the read side
is duplicated; `tdtheme` keeps a reader for validation and never writes TIFFs,
so the subtle parts - alpha-convention detection, premultiplied round-tripping
- have exactly one implementation that matters at install time, and the copies
are held to the same test suite.

The two alpha traps, in one paragraph
-------------------------------------

Reading: 23 of the 95 icons declaring premultiplied alpha contain straight
samples, and the tag lies about them. It is decidable - in genuine
premultiplied data no channel can exceed alpha, so one pixel with
`max(RGB) > alpha` refutes the tag. Writing: output must be premultiplied,
because TouchDesigner's compositor evaluates `src + bg*(1-a)` and straight
data draws every antialiased edge at full strength, making icons read
blocky. Getting this wrong is silent: the file is valid, it decodes fine, and
it renders wrong. Both directions are exercised against `sips` in the tests.

Storage model
-------------

Deliberately wasteful, and the reason the two tools are split. A generated set
is a **complete copy** of the 97 icons, not a sparse diff, so a theme
directory is self-contained, an icon can be moved between themes with a plain
file copy, and `tdtheme apply` can stay a dumb total overwrite with no patch
logic that could half-apply. The cost is ~780 KB per theme, which is
irrelevant next to a 764 KB install.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "IconError", "TiffImage", "Icon",
    "read_tiff", "write_tiff", "describe_tiff",
    "lzw_decode", "lzw_encode",
    "png_bytes", "contact_sheet",
    "icon_names", "read_icon", "icon_manifest", "capture_icons",
    "diff_icons", "pixel_diff",
    "load_recipe", "apply_recipe", "RECIPE_VERSION",
]


class IconError(Exception):
    """A TIFF file could not be read, written, or understood."""


# ==========================================================================
# TIFF constants
# ==========================================================================

#: Field types and their byte widths, for parsing the values in an IFD entry.
_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8,
              11: 4, 12: 8, 13: 4}

TAG_WIDTH = 256
TAG_HEIGHT = 257
TAG_BITS_PER_SAMPLE = 258
TAG_COMPRESSION = 259
TAG_PHOTOMETRIC = 262
TAG_STRIP_OFFSETS = 273
TAG_SAMPLES_PER_PIXEL = 277
TAG_ROWS_PER_STRIP = 278
TAG_STRIP_BYTE_COUNTS = 279
TAG_PLANAR_CONFIG = 284
TAG_EXTRA_SAMPLES = 338

COMPRESSION_NONE = 1
COMPRESSION_LZW = 5

PHOTOMETRIC_RGB = 2

#: Alpha handling. A TIFF with an alpha channel may declare it as "associated"
#: (premultiplied) or "unassociated" (straight). TouchDesigner's own files
#: declare ExtraSamples=1, i.e. *associated*, so the stored RGB is already
#: multiplied by alpha. The transforms below are written against straight
#: alpha and un-premultiply on the way in, so recolouring an icon cannot
#: brighten its own transparent pixels.
EXTRA_SAMPLES_UNSPECIFIED = 0
EXTRA_SAMPLES_ASSOCIATED = 1
EXTRA_SAMPLES_UNASSOCIATED = 2

# TIFF LZW
_CLEAR_CODE = 256
_EOI_CODE = 257
_FIRST_CODE = 258
_MAX_CODE = 4096
_MIN_BITS = 9
_MAX_BITS = 12


# ==========================================================================
# LZW
# ==========================================================================

def lzw_decode(data: bytes) -> bytes:
    """Decode one TIFF LZW strip (MSB-first).

    The one genuinely tricky detail is the code-width growth, and getting it
    wrong is silent: the stream desynchronises and you get plausible-looking
    garbage rather than an error. The rule is that the decoder's table is
    always *one entry behind* the encoder's, because the encoder adds a phrase
    when it emits a code while the decoder only learns that phrase when it
    reads the following code. So the encoder grows the width when the next
    code to assign exceeds the maximum, and the decoder grows it one code
    earlier:

        encoder:  next_code >  max_code   ->  widen
        decoder:  len(table) >= max_code  ->  widen

    Both were measured, not recalled: with the decoder using `>` to match the
    encoder, 43 of the 89 LZW strips shipped in the install decode to the
    right length and 46 do not. With `>=`, all 89 do. See
    `tests/test_icons.py`, which asserts this against the real files so the
    constant cannot be "tidied" back into a broken state.

    Validated by decoding all 89 LZW strips TouchDesigner ships and checking
    the output length equals width*height*samples exactly.
    """
    out = bytearray()
    table: "list[bytes]" = []
    previous = b""

    bits = _MIN_BITS
    max_code = (1 << _MIN_BITS) - 1  # 511

    buffer = 0
    buffered = 0
    position = 0
    length = len(data)

    while True:
        while buffered < bits:
            if position >= length:
                return bytes(out)
            buffer = (buffer << 8) | data[position]
            position += 1
            buffered += 8
        buffered -= bits
        code = (buffer >> buffered) & ((1 << bits) - 1)

        if code == _EOI_CODE:
            break
        if code == _CLEAR_CODE:
            table = [bytes([i]) for i in range(256)] + [b"", b""]
            bits = _MIN_BITS
            max_code = (1 << _MIN_BITS) - 1
            previous = b""
            continue

        if code < len(table) and (code < 256 or table[code]):
            entry = table[code]
        elif previous:
            # The KwKwK case: the code refers to the entry currently being
            # built, so it is `previous + previous[0]`.
            entry = previous + previous[:1]
        else:
            raise IconError("LZW stream starts with an undefined code")

        out += entry

        if previous:
            if len(table) < _MAX_CODE:
                table.append(previous + entry[:1])
                if len(table) >= max_code and bits < _MAX_BITS:
                    bits += 1
                    max_code = (1 << bits) - 1
        previous = entry

    return bytes(out)


def lzw_encode(data: bytes) -> bytes:
    """Encode one strip as TIFF LZW. Inverse of `lzw_decode`.

    Two details that are not obvious:

    - The dictionary is keyed on `(code << 8) | byte` integers rather than
      byte strings. Same algorithm, but integer hashing in CPython is roughly
      an order of magnitude cheaper than slicing and hashing `bytes` objects,
      and this runs over ~1.9 MB of pixel data per full-set rebuild.
    - Once the 4096-code table is full no new phrases may be added, but
      encoding continues. Letting `next_code` run past 4095 emits codes that
      do not fit in 12 bits and corrupts the rest of the stream - which is
      what the first version of this function did, on the one shipped icon
      whose pixel data is compressible enough to fill the table.
    """
    out = bytearray()
    buffer = 0
    buffered = 0

    def emit(code: int, width: int) -> None:
        nonlocal buffer, buffered
        buffer = (buffer << width) | code
        buffered += width
        while buffered >= 8:
            out.append((buffer >> (buffered - 8)) & 0xFF)
            buffered -= 8

    table: "dict[int, int]" = {}
    next_code = _FIRST_CODE
    bits = _MIN_BITS
    max_code = (1 << _MIN_BITS) - 1  # 511

    emit(_CLEAR_CODE, bits)

    if data:
        prefix = data[0]
        for byte in data[1:]:
            key = (prefix << 8) | byte
            found = table.get(key)
            if found is not None:
                prefix = found
                continue
            emit(prefix, bits)
            if next_code < _MAX_CODE:
                table[key] = next_code
                next_code += 1
                if next_code > max_code and bits < _MAX_BITS:
                    bits += 1
                    max_code = (1 << bits) - 1
            else:
                # The table is full, and this is where the format requires a
                # Clear code: emit one at the current width, start over.
                #
                # Carrying on with a frozen dictionary appears to work and does
                # not. This module's own decoder tolerates it, so a round-trip
                # test passes and proves nothing - but libtiff rejects the strip
                # outright, with no usable error message. That is how 2 of the 97
                # shipped icons came to be re-encodable by us and readable by
                # nobody: they were the two files big enough to use all 4096
                # codes, while the largest that came close, at 3954, was fine.
                #
                # The phrase just consumed is deliberately not re-registered.
                # The decoder resets to 258 literals on Clear and only creates
                # its first new entry when it reads the *following* code, so
                # registering here would leave the encoder one entry ahead and
                # desynchronise the numbering. The normal path re-adds it on the
                # next miss, which keeps both sides in step.
                emit(_CLEAR_CODE, bits)
                table = {}
                next_code = _FIRST_CODE
                bits = _MIN_BITS
                max_code = (1 << _MIN_BITS) - 1
            prefix = byte
        emit(prefix, bits)

    emit(_EOI_CODE, bits)
    if buffered:
        out.append((buffer << (8 - buffered)) & 0xFF)
    return bytes(out)


# ==========================================================================
# TIFF container
# ==========================================================================

@dataclass
class TiffImage:
    """An 8-bit-per-sample image, always stored as straight-alpha RGBA."""

    width: int
    height: int
    pixels: bytes  # len == width * height * 4, RGBA, alpha not premultiplied

    def __len__(self) -> int:
        return len(self.pixels)

    @property
    def size(self) -> "tuple[int, int]":
        return self.width, self.height

    def replace_pixels(self, pixels: bytes) -> "TiffImage":
        if len(pixels) != len(self.pixels):
            raise IconError(
                f"pixel buffer is {len(pixels)} bytes, expected "
                f"{len(self.pixels)} for {self.width}x{self.height} RGBA"
            )
        return TiffImage(self.width, self.height, bytes(pixels))


def _ifd_entries(raw: bytes, offset: int) -> "dict[int, tuple[int, int, bytes]]":
    """Read one IFD into {tag: (type, count, raw value bytes)}."""
    if offset + 2 > len(raw):
        raise IconError(f"IFD offset {offset} is past the end of the file")
    (count,) = struct.unpack_from("<H", raw, offset)
    entries = {}
    for index in range(count):
        start = offset + 2 + index * 12
        if start + 12 > len(raw):
            raise IconError(f"IFD entry {index} runs past the end of the file")
        tag, field_type, n = struct.unpack_from("<HHI", raw, start)
        size = _TYPE_SIZE.get(field_type, 1) * n
        if size > 4:
            (value_offset,) = struct.unpack_from("<I", raw, start + 8)
            value = raw[value_offset:value_offset + size]
        else:
            value = raw[start + 8:start + 8 + size]
        entries[tag] = (field_type, n, value)
    return entries


def _tag_ints(entry: "tuple[int, int, bytes]") -> "list[int]":
    field_type, n, value = entry
    if field_type == 3:  # SHORT
        return list(struct.unpack(f"<{n}H", value[:2 * n]))
    if field_type in (1, 6, 7):  # BYTE, SBYTE, UNDEFINED
        return list(value[:n])
    if field_type in (4, 9):  # LONG, SLONG
        return list(struct.unpack(f"<{n}I", value[:4 * n]))
    raise IconError(f"unexpected TIFF field type {field_type} for an integer tag")


def _first_ints(entries: dict, tag: int, default: int) -> list:
    if tag not in entries:
        return [default]
    return _tag_ints(entries[tag])


def describe_tiff(raw: bytes) -> "dict[str, int | str]":
    """Structural facts about a TIFF, without decoding any pixels.

    `read_tiff` normalises everything to straight-alpha RGBA and hands back a
    `TiffImage` that no longer records how the file was actually stored, so the
    shape of the *shipped* set - how many files are multi-strip, how many are
    RGB rather than RGBA, what compression they use - is invisible through it.
    That shape is worth knowing: multi-strip and premultiplied-alpha are both
    cases a straightforward reader gets wrong, and a theming tool that silently
    mishandles them corrupts glyphs rather than failing. The manifest records
    these so the claim can be checked instead of asserted in prose.

    Cheap: parses the header and IFD, touches no strip data.
    """
    if len(raw) < 8:
        raise IconError("file is too short to be a TIFF")
    if raw[:2] == b"II":
        endian = "<"
    elif raw[:2] == b"MM":
        endian = ">"
    else:
        raise IconError(f"not a TIFF: bad byte-order mark {raw[:2]!r}")
    (version,) = struct.unpack(endian + "H", raw[2:4])
    if version != 42:
        raise IconError(f"unsupported TIFF version {version} (expected classic 42)")
    (ifd_offset,) = struct.unpack(endian + "I", raw[4:8])
    entries = _ifd_entries(raw, ifd_offset)

    # RowsPerStrip is the reliable route to a strip count; StripByteCounts is
    # only a cross-check, and is empty for a single-strip file in practice.
    counts = _first_ints(entries, TAG_STRIP_BYTE_COUNTS, [])
    strips = 1
    rows = _first_ints(entries, TAG_ROWS_PER_STRIP, [0])[0]
    height = _first_ints(entries, TAG_HEIGHT, [0])[0]
    if rows and height:
        strips = -(-height // rows)
    elif len(counts) > 1:
        strips = len(counts)
    samples = _first_ints(entries, TAG_SAMPLES_PER_PIXEL, [3])[0]
    extra = _first_ints(entries, TAG_EXTRA_SAMPLES, [])
    if not extra:
        alpha = "none" if samples == 3 else "undeclared"
    elif extra[0] == EXTRA_SAMPLES_ASSOCIATED:
        alpha = "associated"
    elif extra[0] == EXTRA_SAMPLES_UNASSOCIATED:
        alpha = "unassociated"
    else:
        alpha = "unspecified"
    return {
        "width": _first_ints(entries, TAG_WIDTH, [0])[0],
        "height": _first_ints(entries, TAG_HEIGHT, [0])[0],
        "bits": sorted(set(_first_ints(entries, TAG_BITS_PER_SAMPLE, [8])))[0],
        "samples": samples,
        "compression": _first_ints(entries, TAG_COMPRESSION, [1])[0],
        "strips": strips,
        "strip_offsets": len(_first_ints(entries, TAG_STRIP_OFFSETS, [])),
        "alpha": alpha,
        "endian": "little" if endian == "<" else "big",
    }


def read_tiff(raw: bytes) -> TiffImage:
    """Decode a TIFF into a straight-alpha RGBA `TiffImage`.

    Handles exactly what TouchDesigner ships: little-endian classic TIFF,
    single page, 8 bits per sample, photometric RGB, one strip, LZW or no
    compression, 3 or 4 samples. Anything else raises rather than guessing -
    a silently mis-decoded icon is worse than a loud failure.
    """
    if len(raw) < 8:
        raise IconError("file is too short to be a TIFF")
    if raw[:2] == b"II":
        endian = "<"
    elif raw[:2] == b"MM":
        endian = ">"
    else:
        raise IconError(f"not a TIFF: bad byte-order mark {raw[:2]!r}")
    (version,) = struct.unpack(endian + "H", raw[2:4])
    if version != 42:
        raise IconError(f"unsupported TIFF version {version} (expected classic 42)")
    (ifd_offset,) = struct.unpack(endian + "I", raw[4:8])
    if endian != "<":
        raise IconError("big-endian TIFF is not produced by TouchDesigner and is "
                        "not supported")

    entries = _ifd_entries(raw, ifd_offset)
    if TAG_PLANAR_CONFIG in entries and _tag_ints(entries[TAG_PLANAR_CONFIG])[0] != 1:
        raise IconError("planar TIFFs are not supported")

    width = _first_ints(entries, TAG_WIDTH, 0)[0]
    height = _first_ints(entries, TAG_HEIGHT, 0)[0]
    if not width or not height:
        raise IconError("TIFF has no ImageWidth/ImageLength")
    if width * height > 64_000_000:
        raise IconError(f"implausible image size {width}x{height}")

    bits = set(_first_ints(entries, TAG_BITS_PER_SAMPLE, 8))
    if bits != {8}:
        raise IconError(f"only 8 bits per sample is supported, got {sorted(bits)}")

    samples = _first_ints(entries, TAG_SAMPLES_PER_PIXEL, 3)[0]
    if samples not in (3, 4):
        raise IconError(f"only 3 or 4 samples per pixel are supported, got {samples}")

    photometric = _first_ints(entries, TAG_PHOTOMETRIC, PHOTOMETRIC_RGB)[0]
    if photometric != PHOTOMETRIC_RGB:
        raise IconError(f"only photometric RGB (2) is supported, got {photometric}")

    compression = _first_ints(entries, TAG_COMPRESSION, 1)[0]
    if compression not in (COMPRESSION_NONE, COMPRESSION_LZW):
        raise IconError(f"unsupported TIFF compression {compression}")
    offsets = _tag_ints(entries[273]) if TAG_STRIP_OFFSETS in entries else None
    counts = _tag_ints(entries[279]) if TAG_STRIP_BYTE_COUNTS in entries else None
    if offsets is None or counts is None:
        raise IconError("TIFF has no strip offsets/byte counts")
    if len(offsets) != len(counts):
        raise IconError(f"{len(offsets)} strip offsets but {len(counts)} byte counts")

    # Multi-strip is real, not hypothetical: five of the 97 shipped icons are
    # 256x256 with a RowsPerStrip of 16 or 64, so they arrive as 4 or 16
    # separate strips. Each strip is an independent LZW stream - the encoder
    # emits a Clear code at the head of each - so the obvious implementation
    # of concatenating the compressed strips and decoding once desynchronises
    # and yields garbage. Decode strip by strip.
    chunks = []
    for start, length in zip(offsets, counts):
        strip = raw[start:start + length]
        if len(strip) != length:
            raise IconError(f"strip at {start} is truncated: wanted {length} "
                            f"bytes, file has {len(raw)}")
        chunks.append(strip if compression == COMPRESSION_NONE else lzw_decode(strip))
    data = b"".join(chunks)

    expected = width * height * samples
    if len(data) < expected:
        raise IconError(f"decoded {len(data)} bytes, expected {expected} for "
                        f"{width}x{height}x{samples}")
    data = data[:expected]

    extra = _first_ints(entries, TAG_EXTRA_SAMPLES, [EXTRA_SAMPLES_UNSPECIFIED])
    declared = extra[0] if extra else EXTRA_SAMPLES_UNSPECIFIED

    # Decide the alpha convention from the DATA, not from the tag.
    #
    # 23 of the 97 shipped icons declare ExtraSamples=1 (associated /
    # premultiplied) but contain straight-alpha samples, and that is decidable
    # rather than a matter of opinion: in genuine premultiplied data a channel
    # can never exceed alpha, so a single pixel with max(RGB) > alpha proves
    # the tag is lying. In Bypass.tiff, 98 of 102 partial-alpha pixels violate
    # it. The 68 genuinely premultiplied icons violate it zero times, so the two
    # populations are cleanly separated and no threshold is needed beyond "any".
    #
    # Trusting the tag here is what turned those 23 icons' soft edges into solid
    # white: un-premultiplying (102,102,102,a=91) gives (255,255,255,91) after
    # clamping, and the whole antialiasing ramp is destroyed. A tag is metadata
    # about intent; the samples are the image.
    raw = data
    partial = 0
    violating = 0
    for index in range(width * height):
        source = index * samples
        if samples != 4:
            break
        alpha = raw[source + 3]
        if alpha == 0 or alpha == 255:
            continue
        partial += 1
        if (raw[source] > alpha or raw[source + 1] > alpha
                or raw[source + 2] > alpha):
            violating += 1
    associated = declared == EXTRA_SAMPLES_ASSOCIATED
    if violating:
        associated = False
    elif not associated and partial:
        # No violations, so the samples are at least *consistent* with
        # premultiplied. Honour that even where the tag is silent or says
        # otherwise, since the data is the more reliable witness.
        associated = _looks_premultiplied(raw, width, height, samples)

    pixels = bytearray(expected if samples == 4 else expected + width * height)
    for index in range(width * height):
        source = index * samples
        target = index * 4
        pixels[target] = data[source]
        pixels[target + 1] = data[source + 1]
        pixels[target + 2] = data[source + 2]
        if samples == 4:
            alpha = data[source + 3]
            if associated and alpha:
                # Undo premultiplication so the transforms below, which reason
                # about straight alpha, see the colour the designer picked.
                r, g, b = pixels[target], pixels[target + 1], pixels[target + 2]
                pixels[target] = min(255, (r * 255 + alpha // 2) // alpha)
                pixels[target + 1] = min(255, (g * 255 + alpha // 2) // alpha)
                pixels[target + 2] = min(255, (b * 255 + alpha // 2) // alpha)
            pixels[target + 3] = alpha
        else:
            pixels[target + 3] = 255

    return TiffImage(width, height, bytes(pixels))


def _survived_roundtrip(after: bytes, before: bytes, tolerance: int = 1) -> bool:
    """Whether the file we wrote really holds the image we meant to write.

    The comparison is made in *premultiplied* space, and it has to be. Straight
    space is the wrong invariant here for a reason worth stating, because
    getting it wrong looks like a codec bug:

        (100,100,100,a=2) -> premultiply -> (1,1,1,2) -> un-premultiply
                           -> (128,128,128,2)

    A 28-point error, from a file that is perfectly correct. Premultiplied
    8-bit storage simply cannot carry the colour of a nearly-transparent pixel,
    so un-premultiplying one divides by a tiny number and the result is
    numerically meaningless. Judged in premultiplied space the same round trip
    is exact - (1,1,1,2) is precisely the right premultiplied form of
    (100,100,100,2) - and since that is the space TouchDesigner composites in,
    it is also the space where fidelity is what actually matters.

    So: re-premultiply what we read back and compare against what we intended
    to store, allowing one unit for the two independent roundings. Anything
    beyond that is a genuine failure - a wrong strip length, a desynchronised
    LZW stream, a bad dimension - and catching it at generation time, where
    the fix is still cheap, is the entire point.
    """
    if len(after) != len(before):
        return False
    want = _premultiply(before)
    got = _premultiply(after)
    for i in range(len(want)):
        if abs(want[i] - got[i]) > tolerance:
            return False
    return True


def _premultiply(pixels: bytes) -> bytes:
    """Straight RGBA -> premultiplied RGBA.

    Every pixel is scaled, including a fully transparent one, and that is the
    whole point of the function rather than an incidental detail. A
    premultiplied compositor evaluates `src + dst*(1-a)`, so a pixel stored as
    (200,100,50,a=0) does not vanish - with a=0 the destination term vanishes
    instead, and the pixel renders as a solid orange block. Leaving the colour
    of a transparent pixel alone is not a harmless shortcut, it inverts the
    pixel from invisible to fully opaque.

    Four of the 97 shipped icons carry colour under a=0 (they are among the 23
    that declare premultiplied and are not), so this is reachable from real
    data rather than hypothetical: 50 pixels across a generated set.
    """
    out = bytearray(len(pixels))
    for i in range(0, len(pixels), 4):
        a = pixels[i + 3]
        if a == 255:
            out[i:i + 4] = pixels[i:i + 4]
            continue
        for k in range(3):
            out[i + k] = (pixels[i + k] * a + 127) // 255
        out[i + 3] = a
    return bytes(out)


def _looks_premultiplied(data: bytes, width: int, height: int, samples: int) -> bool:
    """True if `data` holds premultiplied samples, judged by its own content.

    A premultiplied image has a flat, characteristic profile: bright interiors
    sitting at or just under alpha=255, with dimmer fringes. A straight image
    of the same artwork keeps full-strength colour in its antialiased pixels,
    so a large majority of its partial-alpha pixels have a channel above alpha.
    Counting that majority separates the two populations with a wide margin
    (the 68 real cases sit at 0%, the 23 mislabeled ones at 96%+), which makes
    the threshold unimportant - it only has to not be near either population.
    """
    if samples != 4:
        return False
    partial = above = 0
    for index in range(width * height):
        source = index * samples
        alpha = data[source + 3]
        if alpha == 0 or alpha == 255:
            continue
        partial += 1
        if (data[source] > alpha or data[source + 1] > alpha
                or data[source + 2] > alpha):
            above += 1
    if not partial:
        return False
    return above * 100 < partial * 20


def write_tiff(image: TiffImage, *, compression: int = COMPRESSION_LZW,
               premultiplied: bool = True) -> bytes:
    """Encode a `TiffImage` as a classic little-endian TIFF.

    The tag set is deliberately minimal - width, height, bit depth, compression,
    photometric, one strip, resolution, and the usual planar/RGB markers. The
    shipped icons carry ~5 KB of Photoshop XMP and IPTC blobs (tag 34377 and
    friends) that contribute nothing to rendering; dropping them makes a
    regenerated icon both smaller and easier to reason about.

    `image.pixels` is always straight (unassociated) RGBA, whatever the
    `premultiplied` flag says; the conversion happens here, on the way out.

    `premultiplied=True` is the default because that is the convention
    TouchDesigner actually composites in, and matching it is the only thing
    that matters. 95 of the 97 shipped icons are premultiplied, and
    libPOP.dylib - the panel and icon-drawing library - carries explicit
    `premult`, `premultcolor` and `premultrgbbyalpha` handling, so the
    renderer is built around premultiplied samples.

    Writing straight alpha into that renderer is not a cosmetic mismatch, it
    is a visible bug, and it is silent: the file decodes, `sips` agrees with
    it, and the image still looks like an icon. But a premultiplied
    compositor computes `src + bg*(1-a)`, so a pixel with alpha 13/255 whose
    colour is the full tint (140,172,255) lands at luma 171 instead of the
    8.8 it should be. Every antialiased edge pixel is drawn at full strength,
    which closes the gaps between strokes and turns smooth 16x16 glyphs into
    hard, blocky silhouettes - small detail fills in and the icons read as
    pixelated. Measured on a 24x24 glyph, the whole icon came out 2.83x too
    heavy.

    An earlier version of this module reasoned the opposite way: that because
    TIFF leaves the convention undefined and generic decoders assume
    associated, the safe choice was to declare ExtraSamples=2 explicitly. That
    is true about libtiff, Photoshop and image viewers, and irrelevant here.
    The one decoder that matters is TouchDesigner's, and it wants the same
    convention as the files it already ships. Being unambiguous for third
    parties is worth nothing if the renderer draws the result wrong.
    """
    if compression not in (COMPRESSION_NONE, COMPRESSION_LZW):
        raise IconError(f"unsupported output compression {compression}")
    if not image.pixels or len(image.pixels) != image.width * image.height * 4:
        raise IconError("image pixels are not width*height*4 bytes of RGBA")

    data = _premultiply(image.pixels) if premultiplied else image.pixels
    if compression == COMPRESSION_LZW:
        data = lzw_encode(data)

    header = struct.pack("<2sHI", b"II", 42, 8)

    # BitsPerSample has 4 entries, so it needs an out-of-line value block.
    bits_block = struct.pack("<4H", 8, 8, 8, 8)
    # ResolutionUnit, and the two rational resolutions, are each <= 4 bytes.
    x_res = struct.pack("<2I", 72, 1)
    y_res = struct.pack("<2I", 72, 1)

    # Layout: header, IFD, out-of-line values, then the single strip.
    n_entries = 13
    ifd_size = 2 + n_entries * 12 + 4
    bits_offset = 8 + ifd_size
    x_res_offset = bits_offset + len(bits_block)
    y_res_offset = x_res_offset + len(x_res)
    strip_offset = y_res_offset + len(y_res)
    if strip_offset % 2:
        strip_offset += 1
        padding = b"\0"
    else:
        padding = b""

    entries = [
        (TAG_WIDTH, 3, 1, struct.pack("<HH", image.width, 0)),
        (TAG_HEIGHT, 3, 1, struct.pack("<HH", image.height, 0)),
        (TAG_BITS_PER_SAMPLE, 3, 4, struct.pack("<I", bits_offset)),
        (TAG_COMPRESSION, 3, 1, struct.pack("<HH", compression, 0)),
        (TAG_PHOTOMETRIC, 3, 1, struct.pack("<HH", PHOTOMETRIC_RGB, 0)),
        (TAG_STRIP_OFFSETS, 4, 1, struct.pack("<I", strip_offset)),
        (TAG_SAMPLES_PER_PIXEL, 3, 1, struct.pack("<HH", 4, 0)),
        (TAG_ROWS_PER_STRIP, 3, 1, struct.pack("<HH", image.height, 0)),
        (TAG_STRIP_BYTE_COUNTS, 4, 1, struct.pack("<I", len(data))),
        (TAG_PLANAR_CONFIG, 3, 1, struct.pack("<HH", 1, 0)),
        (TAG_EXTRA_SAMPLES, 3, 1, struct.pack(
            "<HH", EXTRA_SAMPLES_ASSOCIATED if premultiplied else EXTRA_SAMPLES_UNASSOCIATED, 0)),
        (282, 5, 1, struct.pack("<I", x_res_offset)),   # XResolution
        (283, 5, 1, struct.pack("<I", y_res_offset)),   # YResolution
    ]
    entries.sort(key=lambda item: item[0])

    ifd = struct.pack("<H", n_entries)
    for tag, field_type, n, value in entries:
        if len(value) < 4:
            value = value + b"\0" * (4 - len(value))
        ifd += struct.pack("<HHI", tag, field_type, n) + value[:4]
    ifd += struct.pack("<I", 0)  # no next IFD

    return b"".join([header, ifd, bits_block, x_res, y_res, padding, data])


# ==========================================================================
# PNG (stdlib zlib only) - for previews, never for the install
# ==========================================================================

def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def png_bytes(image: TiffImage) -> bytes:
    """Encode a `TiffImage` as an RGBA PNG. Used for previews only.

    `zlib` is in the standard library, so this costs nothing in dependency
    terms, and being able to *look* at a generated icon set is the difference
    between a prototype that can be checked and one that has to be taken on
    faith.
    """
    raw = bytearray()
    stride = image.width * 4
    for row in range(image.height):
        raw.append(0)  # filter type 0 (None)
        raw += image.pixels[row * stride:(row + 1) * stride]
    header = struct.pack(">IIBBBBB", image.width, image.height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", header)
            + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + _png_chunk(b"IEND", b""))


def contact_sheet(images: "list[tuple[str, TiffImage]]", *, columns: int = 10,
                  cell: int = 72, background: "tuple[int,int,int,int]" = (28, 28, 32, 255),
                  label_height: int = 0) -> TiffImage:
    """Tile images into one RGBA sheet on a solid background.

    Nearest-neighbour, no filtering: these are 16-256 px UI glyphs being
    inspected for legibility, and any smoothing would misrepresent them.
    """
    rows = (len(images) + columns - 1) // columns
    width = columns * cell
    height = rows * (cell + label_height)
    sheet = bytearray(bytes(background) * (width * height))

    for index, (_, image) in enumerate(images):
        origin_x = (index % columns) * cell
        origin_y = (index // columns) * (cell + label_height)
        for y in range(cell):
            source_y = y * image.height // cell
            row_base = source_y * image.width * 4
            target = ((origin_y + y) * width + origin_x) * 4
            for x in range(cell):
                source_x = x * image.width // cell
                offset = row_base + source_x * 4
                r, g, b, a = image.pixels[offset:offset + 4]
                # Composite over the background so transparent regions of the
                # glyph read as "nothing here" rather than as black ink.
                if a == 255:
                    sheet[target:target + 4] = bytes((r, g, b, 255))
                elif a:
                    for channel, value in enumerate((r, g, b)):
                        sheet[target + channel] = (
                            value * a + background[channel] * (255 - a)) // 255
                    sheet[target + 3] = 255
                target += 4

    return TiffImage(width, height, bytes(sheet))


# ==========================================================================
# Colour transforms
# ==========================================================================
#
# All of these take and return straight-alpha RGBA and leave alpha untouched.
# Alpha is the icon's shape; a theme changes what colour the shape is painted,
# never which pixels exist. Recolouring a fully transparent pixel is a no-op by
# construction because its colour is undefined and never sampled.

def _clamp8(value: float) -> int:
    if value <= 0:
        return 0
    if value >= 255:
        return 255
    return int(value + 0.5)


def _each_pixel(image: TiffImage, fn) -> TiffImage:
    source = image.pixels
    out = bytearray(len(source))
    for offset in range(0, len(source), 4):
        r, g, b, a = source[offset:offset + 4]
        if a == 0:
            out[offset:offset + 4] = source[offset:offset + 4]
            continue
        nr, ng, nb = fn(r, g, b, a)
        out[offset:offset + 3] = bytes((_clamp8(nr), _clamp8(ng), _clamp8(nb)))
        out[offset + 3] = a
    return image.replace_pixels(bytes(out))


def _luma(r: float, g: float, b: float) -> float:
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def op_grayscale(image: TiffImage, amount: float = 1.0) -> TiffImage:
    """Blend each pixel toward its own luminance. amount=1 is fully grey."""
    amount = max(0.0, min(1.0, amount))

    def convert(r, g, b, a):
        y = _luma(r, g, b)
        return (r + (y - r) * amount, g + (y - g) * amount, b + (y - b) * amount)

    return _each_pixel(image, convert)


def op_tint(image: TiffImage, color, strength: float = 1.0) -> TiffImage:
    """Repaint every pixel with `color`, keeping each pixel's own brightness.

    This is the transform that actually makes a theme read as a theme: the
    glyph's shading and anti-aliasing survive, but its hue comes from the
    theme rather than from the stock palette. `strength` blends back towards
    the original, which is how you get a subtly tinted set rather than a flat
    silhouette.
    """
    tr, tg, tb = (float(c) for c in color)
    # Normalise the target to unit brightness so a dark accent does not also
    # darken the icon; brightness is the glyph's job, hue is the theme's.
    scale = 255.0 / max(tr, tg, tb, 1.0)
    tr, tg, tb = tr * scale, tg * scale, tb * scale
    strength = max(0.0, min(1.0, strength))

    def convert(r, g, b, a):
        y = _luma(r, g, b) / 255.0
        nr, ng, nb = tr * y, tg * y, tb * y
        return (r + (nr - r) * strength, g + (ng - g) * strength, b + (nb - b) * strength)

    return _each_pixel(image, convert)


def op_hue_rotate(image: TiffImage, degrees: float) -> TiffImage:
    """Rotate hue, preserving saturation and value. Cheap, and reversible."""
    import colorsys
    turn = (degrees % 360.0) / 360.0

    def convert(r, g, b, a):
        h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        h = (h + turn) % 1.0
        nr, ng, nb = colorsys.hsv_to_rgb(h, s, v)
        return (nr * 255.0, ng * 255.0, nb * 255.0)

    return _each_pixel(image, convert)


def op_saturate(image: TiffImage, amount: float) -> TiffImage:
    def convert(r, g, b, a):
        y = _luma(r, g, b)
        return (y + (r - y) * amount, y + (g - y) * amount, y + (b - y) * amount)

    return _each_pixel(image, convert)


def op_brightness(image: TiffImage, factor: float) -> TiffImage:
    def convert(r, g, b, a):
        return r * factor, g * factor, b * factor

    return _each_pixel(image, convert)


def op_contrast(image: TiffImage, amount: float, pivot: float = 128.0) -> TiffImage:
    """Push each channel away from `pivot` by `amount`.

    Grayscale alone leaves an icon as a set of mid-greys, which is a different
    problem from a coloured one: the glyphs go soft and stop reading at 20x20.
    A theme that removes mid-tones from the interface - see the `bnw` recipe -
    needs the same treatment applied to the icons, or the icons end up as the
    only soft thing left on screen.
    """
    def convert(r, g, b, a):
        return ((r - pivot) * amount + pivot,
                (g - pivot) * amount + pivot,
                (b - pivot) * amount + pivot)

    return _each_pixel(image, convert)


def op_solid(image: TiffImage, color) -> TiffImage:
    """Flatten the glyph to one flat colour - a true silhouette."""
    cr, cg, cb = (float(c) for c in color)

    def convert(r, g, b, a):
        return cr, cg, cb

    return _each_pixel(image, convert)


#: Every op a recipe may name, and the argument names each one takes. Recipes
#: are hand-edited JSON, so an unknown op has to fail loudly rather than be
#: skipped - a silently ignored line means an icon set that is subtly wrong.
#:
#: The accepted and required argument names are derived from each function's
#: signature rather than restated here, because a hand-kept list drifts: it
#: briefly claimed `strength` was required (breaking three recipes) and then
#: that it was unknown (breaking them differently). Deriving both from the
#: signature means an optional argument like `strength` or `pivot` can be
#: omitted, an unknown one is still rejected, and adding a parameter to an op
#: cannot leave this table out of step.
_OPS = {
    "grayscale": op_grayscale,
    "tint": op_tint,
    "hue": op_hue_rotate,
    "saturate": op_saturate,
    "brightness": op_brightness,
    "contrast": op_contrast,
    "solid": op_solid,
}


def _op_args(function) -> "tuple[set[str], list[str]]":
    """(accepted argument names, required ones) for an op.

    The leading `image` parameter is passed positionally by `_run_op` and is
    not something a recipe may name, so it is dropped.
    """
    import inspect
    parameters = [p for p in inspect.signature(function).parameters.values()
                  if p.kind is p.POSITIONAL_OR_KEYWORD][1:]
    accepted = {p.name for p in parameters}
    required = [p.name for p in parameters if p.default is inspect.Parameter.empty]
    return accepted, required



# ==========================================================================
# Icon sets on disk
# ==========================================================================
# Recipes
# ==========================================================================
#
# A theme's icons are *derived* from the baseline by a small ordered list of
# transforms, recorded in `recipes/<name>.recipe.json`:
#
#   {
#     "version": 1,
#     "ops": [
#       {"match": "*",              "op": "grayscale", "amount": 1.0},
#       {"match": "*",              "op": "tint", "color": [214, 138, 74]},
#       {"match": "Error*",         "op": "tint", "color": [232, 96, 84],
#                                  "strength": 1.0}
#     ]
#   }
#
# `match` is an fnmatch glob on the icon name without its extension. Ops apply
# in order, so a later op can override an earlier one - which is how a theme
# says "tint everything amber, except keep errors red".
#
# Recipes rather than committed binaries because the alternative is a
# 764 KB opaque blob per theme that nobody can review or tweak. A recipe is
# twelve lines, it regenerates deterministically, and editing "make the warn
# icons more orange" is a one-token change. The generated `.tiff` files are
# still written to disk, and committed, because `tdtheme apply` must not
# depend on a codec being correct at apply time - and because a theme has to
# be sendable to someone who has never heard of this tool.

RECIPE_VERSION = 1


# ==========================================================================

@dataclass
class Icon:
    """One icon file: its name, decoded pixels, and where it came from."""

    name: str
    image: TiffImage
    raw: bytes

    @property
    def width(self) -> int:
        return self.image.width

    @property
    def height(self) -> int:
        return self.image.height

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()


def icon_names(directory) -> "list[str]":
    """Every `.tiff` in `directory`, sorted. Non-TIFF files are ignored."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.iterdir()
                  if p.is_file() and p.suffix.lower() in (".tiff", ".tif"))


def read_icon(path) -> Icon:
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise IconError(f"cannot read icon {path}: {exc}") from exc
    return Icon(path.name, read_tiff(raw), raw)


def _atomic_write(path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def icon_manifest(directory) -> "dict[str, dict]":
    """{name: {sha256, width, height, bytes, ...}} for a directory of icons.

    Used to answer "how does this theme's icon set differ from the baseline"
    without decoding any pixels, and to notice an icon that has gone missing.
    The storage fields come from `describe_tiff` and are recorded because a
    regenerated set is deliberately *not* shaped like the shipped one - one
    strip, always RGBA, no Photoshop metadata - so a diff between the two is
    only meaningful over the pixel data, and a reader needs to see why.
    """
    directory = Path(directory)
    manifest: "dict[str, dict]" = {}
    for name in icon_names(directory):
        path = directory / name
        raw = path.read_bytes()
        entry: "dict[str, object]" = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }
        try:
            entry.update(describe_tiff(raw))
        except IconError as exc:
            entry["error"] = str(exc)
        manifest[name] = entry
    return manifest


def capture_icons(source, destination) -> "dict[str, Path]":
    """Copy an install's icon directory into `destination`, verbatim.

    Verbatim on purpose. When this is used to author a theme, the icons are
    whatever the user has installed, and re-encoding them would make it
    impossible to tell later whether the install changed or this tool's codec
    did. The same argument applies to a baseline.

    This is also why a recipe cannot always reproduce an exported set: an edit
    made by hand, or by any tool other than this one, leaves no trace in a
    recipe. See the `defaultnowarn` case in the README.
    """
    source = Path(source)
    destination = Path(destination)
    names = icon_names(source)
    if not names:
        raise IconError(f"no .tiff files in {source}")
    destination.mkdir(parents=True, exist_ok=True)
    written = {}
    for name in names:
        target = destination / name
        shutil.copy2(source / name, target)
        written[name] = target
    return written


def diff_icons(baseline, other) -> "dict[str, str]":
    """Names whose bytes differ between two icon directories.

    Present in `other` but not `baseline` is reported as ``"added"``; present
    in `baseline` but not `other` as ``"absent"``. The distinction matters: a
    TouchDesigner update that adds an icon must not read as "this theme deleted
    it".
    """
    base = {name: hashlib.sha256((Path(baseline) / name).read_bytes()).hexdigest()
            for name in icon_names(baseline)}
    theirs = {name: hashlib.sha256((Path(other) / name).read_bytes()).hexdigest()
              for name in icon_names(other)}
    out = {}
    for name in sorted(set(base) | set(theirs)):
        if name not in theirs:
            out[name] = "absent"
        elif name not in base:
            out[name] = "added"
        elif base[name] != theirs[name]:
            out[name] = "changed"
    return out


def pixel_diff(baseline, other) -> "dict[str, str]":
    """Names whose *pixels* differ between two icon directories.

    Byte equality is the wrong question for a regenerated set. A generated icon
    is always a different file from the shipped one - single-strip, explicitly
    straight alpha, and stripped of ~5 KB of Photoshop metadata - so a byte diff
    reports all 97 files as changed even when the recipe did nothing to them.
    For `mono`, which is pure `grayscale`, 79 of 97 icons come out
    pixel-for-pixel identical while every one of them differs in bytes.

    Decoding is the expensive part (~0.4s for a 97-icon set in pure Python), so
    this is not on the path of `tdtheme list`. It belongs where someone has
    actually asked whether a recipe did anything.

    `added` and `absent` are reported as in `diff_icons`: a missing or extra
    file is a structural fact, and cannot be settled by comparing pixels.
    """
    base_dir, other_dir = Path(baseline), Path(other)
    out: "dict[str, str]" = {}
    for name, state in diff_icons(base_dir, other_dir).items():
        if state in ("added", "absent"):
            out[name] = state
            continue
        try:
            before = read_tiff((base_dir / name).read_bytes())
            after = read_tiff((other_dir / name).read_bytes())
        except IconError as exc:
            out[name] = f"unreadable: {exc}"
            continue
        out[name] = "identical" if before.pixels == after.pixels else "changed"
    return out


def _match(name: str, pattern: str) -> bool:
    from fnmatch import fnmatchcase
    return fnmatchcase(name, pattern)


def _run_op(image: TiffImage, spec: dict) -> TiffImage:
    op_name = spec.get("op")
    if op_name not in _OPS:
        raise IconError(
            f"unknown op {op_name!r}. Known ops: {', '.join(sorted(_OPS))}"
        )
    function = _OPS[op_name]
    accepted, required = _op_args(function)
    known = accepted | {"op", "match"}
    kwargs = {k: v for k, v in spec.items() if k in known and k not in ("op", "match")}
    unknown = set(spec) - known
    if unknown:
        raise IconError(f"op {op_name!r} does not take {sorted(unknown)}. "
                        f"It takes {sorted(accepted)}.")
    missing = [k for k in required if k not in kwargs]
    if missing:
        raise IconError(f"op {op_name!r} is missing {missing}")
    if "color" in kwargs:
        color = kwargs["color"]
        if not isinstance(color, (list, tuple)) or len(color) != 3 \
                or not all(isinstance(c, (int, float)) and 0 <= c <= 255 for c in color):
            raise IconError(f"color must be three numbers in 0-255, got {color!r}")
    return function(image, **kwargs)


def load_recipe(path) -> dict:
    path = Path(path)
    try:
        recipe = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise IconError(f"cannot read recipe {path}: {exc}") from exc
    if not isinstance(recipe, dict):
        raise IconError(f"{path}: expected a JSON object")
    version = recipe.get("version")
    if version != RECIPE_VERSION:
        raise IconError(f"{path}: recipe version {version!r}, this tool writes "
                        f"and reads version {RECIPE_VERSION}")
    if not isinstance(recipe.get("ops"), list):
        raise IconError(f"{path}: 'ops' must be a list")
    return recipe


#: Ops that decide a pixel's colour from its luminance, and so cannot be
#: composed - applying one twice is not "twice as much", it is a second
#: recolour of an already-recoloured image. Everything else is an adjustment
#: and accumulates normally.
_RECOLOUR_OPS = frozenset({"tint", "solid", "hue_rotate"})


def _select_ops(matching: list) -> "tuple[dict | None, list]":
    """Decide which of one icon's matching ops actually run.

    Returns `(winner, adjust)` where `winner` is the single recolouring op to
    apply - the last one, or None if the icon had none - and `adjust` is every
    adjusting op in recipe order. See apply_recipe for why the two halves are
    treated differently; keeping the decision here rather than in the caller
    means the policy lives in one place.
    """
    recolour = [s for s in matching if s.get("op") in _RECOLOUR_OPS]
    adjust = [s for s in matching if s.get("op") not in _RECOLOUR_OPS]
    return (recolour[-1] if recolour else None), adjust


def apply_recipe(baseline_dir, destination, recipe: dict, *,
                 compression: int = COMPRESSION_LZW,
                 progress=None) -> "dict[str, object]":
    """Generate a theme's icon set from the baseline. Returns a report.

    An empty `ops` list means "ship the baseline icons unchanged", and that is
    taken literally: the files are **copied byte for byte**, not decoded and
    re-encoded. This is what the `default` theme uses, and it matters more than
    it looks. Every theme carries a complete icon set precisely so that
    `apply` is a total overwrite - otherwise applying `sunset` and then
    `default` would leave the sunset icons in place, because a theme with no
    icon directory writes nothing. For that reset to be lossless, `default`
    has to restore the shipped bytes and not merely pixels that look the same.
    Re-encoding would also defeat the purpose: the generated files drop the
    ~5 KB of Photoshop metadata the shipped icons carry, so a re-encode is
    never byte-identical no matter how few ops ran.

    With ops present, every baseline icon is still written, whether or not any
    op matched it, so the set stays complete.
    """
    baseline_dir = Path(baseline_dir)
    destination = Path(destination)
    ops = recipe.get("ops") or []

    names = icon_names(baseline_dir)
    if not names:
        raise IconError(f"no baseline icons in {baseline_dir}")

    destination.mkdir(parents=True, exist_ok=True)
    touched: "list[str]" = []
    skipped: "list[str]" = []
    total_bytes = 0
    identical = 0
    pixel_identical = 0

    if not ops:
        for position, name in enumerate(names):
            raw = (baseline_dir / name).read_bytes()
            _atomic_write(destination / name, raw)
            total_bytes += len(raw)
            identical += 1
            pixel_identical += 1
            if progress is not None:
                progress(position + 1, len(names), name)
        return {
            "written": len(names),
            "transformed": 0,
            "untransformed": len(names),
            "byte_identical_to_baseline": identical,
            "pixel_identical_to_baseline": pixel_identical,
            "bytes": total_bytes,
            "destination": destination,
            "compression": COMPRESSION_NONE,
            "verbatim": True,
        }

    for position, name in enumerate(names):
        stem = re.sub(r"\.tiff?$", "", name, flags=re.IGNORECASE)
        source = (baseline_dir / name).read_bytes()
        try:
            image = read_tiff(source)
        except IconError as exc:
            raise IconError(f"baseline icon {name} cannot be decoded: {exc}") from exc

        original_image = image
        original_pixels = image.pixels
        matching = [spec for spec in ops if _match(stem, spec.get("match", "*"))]

        # Recolouring ops replace; adjusting ops accumulate. This distinction is
        # the whole reason a recipe can say "a later op wins" and mean it.
        #
        # `tint` maps a pixel to `colour * luma(pixel)`, so it is not
        # composable: running it twice reads the first result's luminance as
        # though it were the original shading, and multiplies by the target's
        # relative luminance a second time. A pixel that should land on
        # (255,92,84) for the error group instead landed on (171,62,56) - 33%
        # too dark - because the general periwinkle tint had already been
        # applied and then tinted again. That hit 19 of the 97 icons in
        # `midnight` and 27 in `sunset`, and silently, since every file was a
        # valid TIFF and `sips` agreed with all of it.
        #
        # So: take the last matching recolour op, run it on the *original*
        # image so it sees the artwork's true luminance, then apply the
        # adjusting ops in order on top. `bnw` depends on the accumulate half -
        # its contrast passes go 1.5 then 1.7 and are meant to compound - and
        # `midnight` depends on the replace half, where the semantic groups
        # exist precisely to overwrite the general tint.
        winner, adjust = _select_ops(matching)

        image = _run_op(original_image, winner) if winner else image
        for spec in adjust:
            image = _run_op(image, spec)
        applied = bool(matching)

        raw = write_tiff(image, compression=compression)
        # A generated file that will not decode is worse than no file: it
        # would be written into the app bundle and silently blank the icon.
        # Verify here, once, at generation time, where the fix is cheap.
        if not _survived_roundtrip(read_tiff(raw).pixels, image.pixels):
            raise IconError(f"internal error: {name} did not survive the TIFF "
                            f"round-trip and was not written")

        _atomic_write(destination / name, raw)
        total_bytes += len(raw)
        (touched if applied else skipped).append(name)
        if raw == source:
            identical += 1
        # Byte equality is close to useless as a "did this change?" signal for a
        # re-encoded theme, and actively misleading: every generated file
        # differs from the shipped one because the ~5 KB of Photoshop metadata
        # is dropped, so a byte diff reports all 97 icons changed even when a
        # recipe op did nothing to them. Pixels are the honest measure.
        if image.pixels == original_pixels:
            pixel_identical += 1
        if progress is not None:
            progress(position + 1, len(names), name)

    return {
        "written": len(names),
        "transformed": len(touched),
        "untransformed": len(skipped),
        "byte_identical_to_baseline": identical,
        "pixel_identical_to_baseline": pixel_identical,
        "bytes": total_bytes,
        "destination": destination,
        "compression": compression,
        "verbatim": False,
    }
