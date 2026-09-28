"""The TIFF layer both halves of this project share.

`tdtheme` installs and inspects; `tdthememaker` authors and exports. Both have
to read a TIFF forensically - TouchDesigner ships LZW-compressed, RGBA,
non-interleaved files whose `ExtraSamples` tag lies about the alpha convention
in 23 of the 97 icons - and both have to render a set to a PNG to show a human
what changed. That was 523 byte-identical lines living twice, once in
`tdicons.py` and once in `tdthememaker/icons.py`, free to drift apart: a fix
landed in one copy and the other kept the bug.

This module is the single copy. It sits at the repository root rather than
inside either half because it is the leaf of the import graph - it imports
nothing from this project - so neither half has to import the other to reach
it. `tdthememaker` writing a TIFF also needs to read one, and routing that
through `tdtheme` would invert the dependency.

Writing is not here. `write_tiff` and `lzw_encode` exist only to author, and
they stay in `tdthememaker`.

Both modules re-export every name below, so `tdicons.read_tiff` and
`tdthememaker.icons.read_tiff` keep working.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "IconError", "TiffImage",
    "read_tiff", "describe_tiff", "lzw_decode",
    "png_bytes", "contact_sheet",
    "icon_names", "icon_manifest", "capture_icons",
    "diff_icons", "pixel_diff", "_atomic_write",
]

class IconError(Exception):
    """A TIFF file could not be read, written, or understood."""

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

@dataclass
class TiffImage:
    """An 8-bit-per-sample image, always stored as straight-alpha RGBA."""

    width: int
    height: int
    pixels: bytes  # len == width * height * 4, RGBA, alpha not premultiplied


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

    # Decide the alpha convention from the DATA, not from the tag. The tag is
    # metadata about intent; the samples are the image, and for 23 of the 97
    # shipped icons the two disagree. A single pixel with max(RGB) > alpha
    # proves the tag is lying, since no channel can exceed alpha in genuinely
    # premultiplied data - so the two populations separate with no threshold
    # beyond "any". Trusting the tag instead is what turned those 23 icons'
    # soft edges into solid white, destroying the antialiasing ramp entirely.
    #
    # ../docs/reverse-engineering.md §5 has the derivation and the per-file
    # pixel counts behind this.
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

def icon_names(directory) -> "list[str]":
    """Every `.tiff` in `directory`, sorted. Non-TIFF files are ignored."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.iterdir()
                  if p.is_file() and p.suffix.lower() in (".tiff", ".tif"))

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
