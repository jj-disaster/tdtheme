"""Tests for the icon generator: the TIFF writer, the ops, and the recipes.

This is the writing half of the icon layer, and it is the half that can fail
silently. A generated icon that decodes in this tool but not in TouchDesigner,
or that decodes and renders too dark because its alpha convention is wrong,
looks like a working icon set. So the assertions here are mostly about the ways
that goes quietly wrong, and the codec is checked against `sips` - Apple's
decoder, which shares no code with this file - rather than only against
itself.

The most important test in the file is the last one: every committed theme's
icon set must be reproducible, byte for byte, from its recipe. That is what
makes a recipe a real source description rather than a historical note.

Run:  python3 tests/test_thememaker.py
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent          # the repository root
sys.path.insert(0, str(ROOT))

import tdtheme as T  # noqa: E402
from tdthememaker import icons, theme as G  # noqa: E402

PASS, FAIL = "  ok  ", " FAIL "
failures: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"[{PASS if condition else FAIL}] {label}")
    if not condition:
        failures.append(label)


def raises(exc_type, fn, label: str):
    try:
        fn()
    except exc_type as exc:
        check(True, f"{label} (raised {type(exc).__name__})")
        return exc
    except Exception as exc:  # noqa: BLE001
        check(False, f"{label} (raised {type(exc).__name__}: {exc})")
        return None
    check(False, f"{label} (did not raise)")
    return None


def section(title: str) -> None:
    print()
    print(title)
    print("-" * 60)


PROJECT = HERE.parent
RECIPES = PROJECT / "recipes"
BASE = ROOT / "baseline" / "Icons"
THEMES = ["default", "defaultnowarn", "midnight", "sunset", "mono", "bnw"]


def theme_icons_dir(name: str) -> Path:
    """Where a theme's generated set lives: the artifact `tdtheme apply` reads."""
    return ROOT / "themes" / name / "Icons"


def theme_recipe_path(name: str) -> Path:
    return RECIPES / f"{name}.recipe.json"


tmp = Path(tempfile.mkdtemp(prefix="tdthememaker-"))
names = icons.icon_names(BASE)
if not names:
    raise SystemExit(f"no baseline icons in {BASE}")

# ---------------------------------------------------------------- the codec

section("TIFF codec")

check(icons.COMPRESSION_NONE == 1 and icons.COMPRESSION_LZW == 5,
      "compression constants match the TIFF spec")

# LZW is the part worth testing hardest. Three bugs in it were found and fixed
# while building this, and all three produced plausible-looking garbage rather
# than an error, so each has a named test here:
#   - the decoder has to widen its code width when len(table) >= 4094, not at
#     exactly 4096, or codes written at 12 bits get read back at 13;
#   - the encoder has to stop handing out new codes once the table is full,
#     not write a 13-bit code into a 12-bit field;
#   - a TIFF may split its data across several strips, and the shipped set
#     contains five files that do.
sample = bytes(range(256)) * 40
check(icons.lzw_decode(icons.lzw_encode(sample)) == sample,
      "LZW round-trips a payload longer than one code table")
check(icons.lzw_decode(icons.lzw_encode(b"")) == b"",
      "LZW round-trips an empty payload")
flat = bytes([7]) * 100000
check(icons.lzw_decode(icons.lzw_encode(flat)) == flat,
      "LZW round-trips a highly repetitive payload (long runs, one code)")
ramp = bytes((i * 7919) % 256 for i in range(50000))
check(icons.lzw_decode(icons.lzw_encode(ramp)) == ramp,
      "LZW round-trips an incompressible payload (worst case for the table)")

# The table-full path, which the payloads above all miss. They compress well
# enough to stay under 4096 codes, so a frozen dictionary never happens and a
# round trip is happy either way - which is exactly why this went unnoticed
# until libtiff refused to read two of the shipped overlays.
#
# `checks` counts the Clear codes the encoder emits, which is the observable
# difference between a correct encoder and one that silently freezes.
def count_clears(data):
    """Re-run the encoder's table policy and report how often it must reset."""
    table, clears = {}, 0
    next_code, bits = icons._FIRST_CODE, icons._MIN_BITS
    max_code = (1 << bits) - 1
    prefix = data[0]
    for byte in data[1:]:
        key = (prefix << 8) | byte
        found = table.get(key)
        if found is not None:
            prefix = found
            continue
        if next_code < icons._MAX_CODE:
            table[key] = next_code
            next_code += 1
            if next_code > max_code and bits < icons._MAX_BITS:
                bits += 1
                max_code = (1 << bits) - 1
        else:
            clears += 1
            table = {}
            next_code, bits = icons._FIRST_CODE, icons._MIN_BITS
            max_code = (1 << bits) - 1
        prefix = byte
    return clears

filler = bytes(range(256)) * 2000          # 512000 bytes, far past 4096 codes
check(count_clears(filler) > 1,
      f"a payload this size exhausts the code table repeatedly "
      f"({count_clears(filler)} resets needed)")
check(icons.lzw_decode(icons.lzw_encode(filler)) == filler,
      "LZW round-trips a payload that fills the table many times over")
check(icons.lzw_decode(icons.lzw_encode(bytes([7]) * 500000))
      == bytes([7]) * 500000,
      "LZW round-trips a half-megabyte of one repeated byte across resets")

# The two real files that used to break libtiff, by size rather than by name,
# so the test keeps its meaning if the baseline is ever re-captured.
big = sorted(names, key=lambda n: -(icons.read_tiff(
    (BASE / n).read_bytes()).width * icons.read_tiff(
    (BASE / n).read_bytes()).height))[:4]
fills = [n for n in big if count_clears(
    icons.read_tiff((BASE / n).read_bytes()).pixels) > 0]
check(len(fills) >= 3,
      f"the largest shipped icons are the ones that exhaust the table "
      f"({len(fills)} of the 4 largest)")

# Every shipped icon, both compressions, pixels in and pixels out. This is the
# test that would have caught the strip and code-width bugs.
bad_decode, bad_lzw, bad_none = [], [], []
for icon in names:
    raw = (BASE / icon).read_bytes()
    try:
        original = icons.read_tiff(raw)
    except icons.IconError as exc:
        bad_decode.append(f"{icon}: {exc}")
        continue
    for comp, bucket in ((icons.COMPRESSION_LZW, bad_lzw),
                         (icons.COMPRESSION_NONE, bad_none)):
        try:
            # Compared through the premultiplied form, not byte-for-byte.
            # Re-encoding is genuinely lossy for a partially transparent pixel -
            # premultiplied 8-bit cannot carry its colour - and demanding exact
            # equality would be asserting something untrue rather than something
            # correct. `_survived_roundtrip` states the real invariant.
            if not icons._survived_roundtrip(
                    icons.read_tiff(
                        icons.write_tiff(original, compression=comp)).pixels,
                    original.pixels):
                bucket.append(f"{icon}: pixels differ")
        except icons.IconError as exc:
            bucket.append(f"{icon}: {exc}")
check(not bad_decode,
      f"all {len(names)} shipped icons decode (failures: {bad_decode[:2]})")
check(not bad_lzw,
      f"all {len(names)} survive an LZW re-encode (failures: {bad_lzw[:2]})")
check(not bad_none,
      f"all {len(names)} survive an uncompressed re-encode (failures: {bad_none[:2]})")

# The five multi-strip files are the ones a naive single-strip reader silently
# truncates, so they are named rather than left to the aggregate count above.
MULTI_STRIP = ["BypassOverlay.tiff", "Collapse.tiff", "ErrorFaceOverlay.tiff",
               "NoCookOverlay.tiff", "WarnFaceOverlay.tiff"]
shipped = icons.icon_manifest(BASE)
found_multi = sorted(n for n, v in shipped.items() if v["strips"] > 1)
check(found_multi == sorted(MULTI_STRIP),
      f"exactly the 5 overlay icons are multi-strip, and they are decoded "
      f"strip by strip (found {len(found_multi)}: {found_multi[:2]}...)")

# 95 of the 97 shipped icons are premultiplied, which is the single most
# consequential fact about this file format. A reader that treats them as
# straight alpha renders every glyph too light; one that treats straight data
# as premultiplied renders it too dark. Both are silent.
alpha_hist = {}
for v in shipped.values():
    alpha_hist[v["alpha"]] = alpha_hist.get(v["alpha"], 0) + 1
check(alpha_hist.get("associated") == 95,
      f"95 shipped icons declare premultiplied alpha (got {alpha_hist})")

# The regenerated sets must declare the *same* convention as the shipped ones:
# premultiplied. TouchDesigner composites premultiplied, so this is what the
# renderer expects, and getting it wrong is silent - the file decodes, sips
# agrees with it, and the icon still looks like an icon. But every antialiased
# edge pixel then draws at full strength, which closes the gaps between strokes
# and makes small glyphs read as blocky. See write_tiff for the measurement.
for theme in THEMES:
    recipe_file = theme_recipe_path(theme)
    if not recipe_file.exists():
        continue
    # A theme with no ops is copied byte for byte from the baseline, so it
    # keeps whatever convention the vendor shipped and there is nothing to
    # assert. `default` and `defaultnowarn` are both in that position, and
    # `defaultnowarn` additionally carries one hand-placed icon.
    if not (json.loads(recipe_file.read_text()).get("ops") or []):
        continue
    regen = icons.icon_manifest(theme_icons_dir(theme))
    kinds = {v["alpha"] for v in regen.values()}
    check(kinds == {"associated"},
          f"{theme}: regenerated icons declare premultiplied alpha, matching "
          f"the shipped set (got {sorted(kinds)})")

# The tag is not to be trusted, and 23 of the 97 shipped icons prove it. They
# declare premultiplied alpha and contain straight samples. This is decidable
# rather than a matter of taste: in genuine premultiplied data no channel can
# exceed alpha, so one pixel with max(RGB) > alpha refutes the tag outright.
#
# Believing the tag cost those 23 icons their antialiasing. Un-premultiplying
# (102,102,102,a=91) yields (285,285,285), which clamps to white, and the whole
# edge ramp collapses to solid white - a 16x16 glyph turns into a blocky
# silhouette. So the reader decides from the data.
def stored_samples(raw):
    """The samples as they sit in the file, before any alpha interpretation."""
    entries = icons._ifd_entries(raw, int.from_bytes(raw[4:8], "little"))
    width = icons._first_ints(entries, 256, 0)[0]
    height = icons._first_ints(entries, 257, 0)[0]
    comp = icons._first_ints(entries, 259, 1)[0]
    samples = icons._first_ints(entries, 277, 1)[0]
    offsets = icons._tag_ints(entries[273])
    counts = icons._tag_ints(entries[279])
    # Each strip is an independent LZW stream with its own Clear code, so a
    # multi-strip file has to be decoded strip by strip rather than as one
    # concatenated blob. Five of the shipped overlays are split this way.
    strips = [raw[o:o + c] for o, c in zip(offsets, counts)]
    if comp == 5:
        data = b"".join(icons.lzw_decode(strip) for strip in strips)
    else:
        data = b"".join(strips)
    return data, width, height, samples

MISLABELLED = {
    "Bypass", "BypassNone", "CloneImmuneOff", "CloneImmuneOn",
    "CloneNetworkImmuneOn", "CommentOff", "CommentOff20x20", "CommentOn20x20",
    "CommentOnBright", "ErrorScript", "LockedReallyLoud", "NoCookOff", "NoCookOn",
    "ParPython", "SaveViewerOff", "SaveViewerOn", "SaveViewerOnBright",
    "SoftLock", "Storage20x20", "Unlocked", "ViewerOff", "ViewerOn",
    "ViewerOnBright",
}
lying, honest, opaque_only = [], [], []
for icon in names:
    raw = (BASE / icon).read_bytes()
    data, w, h, n_samples = stored_samples(raw)
    partial = above = 0
    for i in range(0, w * h * n_samples, n_samples) if n_samples == 4 else ():
        a = data[i + 3]
        if a in (0, 255):
            continue
        partial += 1
        if data[i] > a or data[i + 1] > a or data[i + 2] > a:
            above += 1
    if n_samples != 4 or not partial:
        # One shipped icon is plain RGB with no alpha channel at all, and six
        # more carry only 0 or 255. Neither population can contradict or
        # support the tag, so they are recorded rather than judged.
        opaque_only.append(icon)
    elif above:
        lying.append(icon)
    else:
        honest.append(above * 100 < partial * 20)
check({n[:-5] for n in lying} == MISLABELLED,
      f"exactly {len(MISLABELLED)} shipped icons declare premultiplied and "
      f"contain straight data (found {len(lying)})")
check(all(honest),
      "every icon that does not contradict the tag is genuinely premultiplied")
check(len(lying) + len(honest) + len(opaque_only) == len(names),
      f"the remaining {len(opaque_only)} icons have no partial alpha to judge "
      f"and are undecidable either way")

# The payoff: those 23 keep their edge ramp instead of turning white.
clamped_before = kept_after = 0
for icon in sorted(MISLABELLED):
    raw = (BASE / f"{icon}.tiff").read_bytes()
    data, w, h, n_samples = stored_samples(raw)
    px = icons.read_tiff(raw).pixels
    for i in range(0, w * h * n_samples, n_samples):
        if data[i + 3] in (0, 255):
            continue
        clamped_before += 1
        naive = min(255, (data[i] * 255 + data[i + 3] // 2) // data[i + 3])
        if naive < 255 and px[i] != 255:
            kept_after += 1
check(kept_after > 0 and kept_after < clamped_before,
      f"the 23 mislabelled icons keep a real edge ramp "
      f"({kept_after} of {clamped_before} partial pixels are neither white nor "
      f"clamped by a naive un-premultiply)")

# And the write side: output is premultiplied, declared as such, and loses only
# what premultiplied 8-bit cannot carry - the colour under a=0.
plain = bytes((200, 100, 50, 0) + (200, 100, 50, 128) + (10, 20, 30, 255))
enc = icons.write_tiff(icons.TiffImage(3, 1, plain),
                         compression=icons.COMPRESSION_NONE)
back = stored_samples(enc)[0]
check(back[:4] == bytes((0, 0, 0, 0)),
      "a fully transparent pixel premultiplies to (0,0,0,0), as it must")
check(back[4:8] == bytes((100, 50, 25, 128)),
      f"a half-transparent pixel is stored premultiplied (got {tuple(back[4:8])})")
check(back[8:] == bytes((10, 20, 30, 255)),
      "a fully opaque pixel is stored unchanged")
check(icons.describe_tiff(enc)["alpha"] == "associated",
      "write_tiff declares the data premultiplied")
check(icons.describe_tiff(icons.write_tiff(
    icons.TiffImage(3, 1, plain), compression=icons.COMPRESSION_NONE,
    premultiplied=False))["alpha"] == "unassociated",
      "premultiplied=False is still available and is declared honestly")

# A generated set is also deliberately not shaped like the shipped one: one
# strip, always RGBA, and no Photoshop metadata. That is why a byte diff
# between baseline and theme is not a statement about pixels.
one_strip = {v["strips"] for v in icons.icon_manifest(
    theme_icons_dir("midnight")).values()}
check(one_strip == {1}, f"regenerated icons are single-strip (got {one_strip})")

# ------------------------------------------------------ independent decoder
#
# Everything above uses this tool's own reader, so a systematic misreading of
# the format would pass every one of those tests. `sips` is Apple's decoder and
# shares no code with this file, which makes it a real second opinion.
#
# It matters most for the alpha convention. 95 of the 97 shipped icons are
# premultiplied; we normalise to straight on read and transform in that space,
# so the output has to be *declared* straight or a decoder multiplies alpha in
# again. Nothing errors when that goes wrong - the file is a valid TIFF, it
# just renders too dark, and its thinnest strokes disappear. A test that only
# checked "sips can read it" would sail straight past it.

section("Independent decode (sips)")

SIPS = shutil.which("sips")
if not SIPS:
    print("  skip  sips unavailable, cannot cross-check with an independent decoder")
else:
    import struct
    import zlib

    def png_rgba(path):
        """Minimal non-interlaced 8-bit PNG reader. sips emits RGBA8 with no
        interlacing, which is the only shape this needs to handle."""
        blob = Path(path).read_bytes()
        assert blob[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
        offset, idat, header = 8, b"", None
        while offset < len(blob):
            (length,) = struct.unpack(">I", blob[offset:offset + 4])
            tag = blob[offset + 4:offset + 8]
            body = blob[offset + 8:offset + 8 + length]
            if tag == b"IHDR":
                header = body
            elif tag == b"IDAT":
                idat += body
            offset += 12 + length
        w, h, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", header)
        assert depth == 8 and interlace == 0 and colour in (2, 6)
        channels = 4 if colour == 6 else 3
        raw = zlib.decompress(idat)
        stride = w * channels
        out = bytearray(h * stride)
        previous = bytearray(stride)
        pos = 0
        for _ in range(h):
            filt = raw[pos]
            pos += 1
            line = bytearray(raw[pos:pos + stride])
            pos += stride
            for x in range(stride):
                a = line[x - channels] if x >= channels else 0
                b = previous[x]
                c = previous[x - channels] if x >= channels else 0
                if filt == 1:
                    line[x] = (line[x] + a) & 255
                elif filt == 2:
                    line[x] = (line[x] + b) & 255
                elif filt == 3:
                    line[x] = (line[x] + (a + b) // 2) & 255
                elif filt == 4:
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                    line[x] = (line[x] + pred) & 255
            out[:stride] = line
            out[_ * stride:(_ + 1) * stride] = line
            previous = line
        if channels == 3:
            widened = bytearray()
            for i in range(0, len(out), 3):
                widened += out[i:i + 3] + b"\xff"
            out = widened
        return bytes(out)

    def sips_png(tiff_path, out_dir):
        target = Path(out_dir) / (Path(tiff_path).stem + ".png")
        proc = subprocess.run(
            [SIPS, "-s", "format", "png", str(tiff_path), "--out", str(target)],
            capture_output=True, text=True)
        return target if proc.returncode == 0 and target.exists() else None

    probe_dir = tmp / "sips"
    probe_dir.mkdir()

    # 1. Every generated icon is readable by a decoder we did not write.
    made = []
    for theme in THEMES:
        source = theme_icons_dir(theme)
        if not source.is_dir():
            continue
        for icon in sorted(icons.icon_names(source))[:6]:
            target = probe_dir / f"{theme}-{icon}"
            shutil.copy2(source / icon, target)
            made.append(target)
    proc = subprocess.run(
        [SIPS, "-g", "pixelWidth", "-g", "pixelHeight", *map(str, made)],
        capture_output=True, text=True)
    check(proc.returncode == 0 and proc.stdout.count("pixelWidth") == len(made),
          f"macOS sips reads all {len(made)} sampled generated icons "
          f"(reported {proc.stdout.count('pixelWidth')})")

    # 1b. Every shipped icon, re-encoded by us and read back by libtiff. This is
    #     the test that was missing while the LZW encoder froze its dictionary
    #     instead of emitting a Clear code: our own decoder tolerates a frozen
    #     table, so the round-trip tests above all passed while sips rejected
    #     the two largest overlays outright. "Can it read the header" is not
    #     enough - a broken strip still reports a correct width - so this
    #     converts, which forces a real decode.
    libtiff_blind = []
    for icon in names:
        image = icons.read_tiff((BASE / icon).read_bytes())
        probe = probe_dir / f"reenc-{icon}"
        probe.write_bytes(icons.write_tiff(image, compression=icons.COMPRESSION_LZW))
        out = subprocess.run([SIPS, "-s", "format", "bmp", str(probe),
                              "--out", str(probe_dir / "probe.bmp")],
                             capture_output=True, text=True)
        if out.returncode != 0:
            libtiff_blind.append(icon)
    check(not libtiff_blind,
          f"libtiff can decode our LZW re-encode of all {len(names)} shipped "
          f"icons (blind to: {libtiff_blind[:3]})")

    # 2. The pixels sips sees are the pixels we meant to write. This is the
    #    check that catches the alpha-convention bug, and it is exact rather
    #    than approximate: max channel difference must be 0.
    sample = ["ErrorFace.tiff", "WarnFace.tiff", "Info.tiff", "Collapse.tiff",
              "Cook.tiff", "Dot.tiff"]
    worst = 0
    for icon in sample:
        source = theme_icons_dir("midnight") / icon
        if not source.exists():
            continue
        png = sips_png(source, probe_dir)
        if png is None:
            check(False, f"sips converts {icon} to PNG")
            continue
        theirs = png_rgba(png)
        ours = icons.read_tiff(source.read_bytes()).pixels
        for i in range(0, min(len(ours), len(theirs)), 4):
            if ours[i + 3] == 255:
                worst = max(worst, max(abs(ours[i + k] - theirs[i + k])
                                       for k in range(3)))
    check(worst == 0,
          f"sips reproduces our pixels exactly at full opacity across "
          f"{len(sample)} icons (worst channel difference {worst})")

    # And in premultiplied space, over every pixel including the near-
    # transparent ones, a small tolerance. sips un-premultiplies on the way to
    # PNG, and dividing by an alpha of 1 or 2 amplifies its own rounding, so a
    # few units of disagreement there is sips' arithmetic and not ours. The
    # vendor's own premultiplied files show the same behaviour, which is the
    # reason this is acceptable rather than merely tolerable.
    worst_pm = 0
    for icon in sample:
        source = theme_icons_dir("midnight") / icon
        png = sips_png(source, probe_dir) if source.exists() else None
        if png is None:
            continue
        theirs = png_rgba(png)
        ours = icons.read_tiff(source.read_bytes()).pixels
        n = min(len(ours), len(theirs))
        worst_pm = max(worst_pm, max(abs(x - y) for x, y in
                                     zip(icons._premultiply(ours[:n]),
                                         icons._premultiply(theirs[:n]))))
    check(worst_pm <= 8,
          f"sips agrees with us in premultiplied space across all pixels "
          f"(worst difference {worst_pm}, tolerance 8)")

    # 3. The counterfactual, so that a 0 above means something. The mistake
    #    that matters is now declaring premultiplied data as straight: sips then
    #    reads dark premultiplied samples as if they were already straight and
    #    the icon comes out far too heavy. If sips could not tell the two apart
    #    then check 2 would prove nothing.
    def set_extra_samples(raw, value):
        """Rewrite tag 338 (ExtraSamples) in place, to forge a mislabelled file."""
        endian = "<" if raw[:2] == b"II" else ">"
        import struct as _s
        (ifd,) = _s.unpack(endian + "I", raw[4:8])
        count = _s.unpack(endian + "H", raw[ifd:ifd + 2])[0]
        for k in range(count):
            at = ifd + 2 + k * 12
            tag, field_type, n = _s.unpack(endian + "HHI", raw[at:at + 8])
            if tag == 338:
                patched = bytearray(raw)
                patched[at + 8:at + 10] = _s.pack(endian + "H", value)
                return bytes(patched)
        raise AssertionError("no ExtraSamples tag to patch")

    image = icons.read_tiff(
        (theme_icons_dir("midnight") / "ErrorFace.tiff").read_bytes())
    lying = set_extra_samples(icons.write_tiff(image),
                              icons.EXTRA_SAMPLES_UNASSOCIATED)
    (probe_dir / "lying.tiff").write_bytes(lying)
    png = sips_png(probe_dir / "lying.tiff", probe_dir)
    if png is None:
        check(False, "sips converts the mislabelled icon to PNG")
    else:
        theirs = png_rgba(png)
        delta = max(abs(image.pixels[i] - theirs[i]) for i in range(len(image.pixels)))
        check(delta > 0,
              f"declaring premultiplied data as straight is detectable "
              f"(sips differs by {delta}), so the exact match above is real")

    # 4. ...and this module is not fooled by it either. A wrong ExtraSamples tag
    #    is metadata about intent; the samples are the image. Reading the forged
    #    file must give the same pixels as reading the honest one, because the
    #    detection in read_tiff looks at the data. This is not hypothetical: 23
    #    of the 97 shipped icons declare premultiplied and are not.
    check(icons.read_tiff(lying).pixels == image.pixels,
          "read_tiff ignores a lying ExtraSamples tag and goes by the samples")

# ---------------------------------------------------------------- transforms

section("Transforms")

probe_img = icons.read_tiff((BASE / "ErrorFace.tiff").read_bytes())

alpha_before = probe_img.pixels[3::4]
for op_name, kwargs in (("grayscale", {"amount": 1.0}),
                        ("tint", {"color": [140, 172, 255]}),
                        ("brightness", {"factor": 1.2}),
                        ("contrast", {"amount": 1.5}),
                        ("saturate", {"amount": 0.5}),
                        ("hue", {"degrees": 45}),
                        ("solid", {"color": [10, 20, 30]})):
    out = icons._run_op(probe_img, {"op": op_name, **kwargs})
    check(out.pixels[3::4] == alpha_before,
          f"op {op_name!r} leaves the alpha channel untouched")
    check(out.size == probe_img.size, f"op {op_name!r} preserves the dimensions")

# grayscale is what `mono` is built on, so assert the property the theme's own
# comment claims: the output is grey, and it is that pixel's own luminance.
grey = icons._run_op(probe_img, {"op": "grayscale", "amount": 1.0})
not_grey = [i for i in range(0, len(grey.pixels), 4)
            if not (grey.pixels[i] == grey.pixels[i + 1] == grey.pixels[i + 2])]
check(not not_grey, "grayscale leaves every pixel with r == g == b")

# Recolouring ops replace, adjusting ops accumulate. A recipe says "a later op
# wins", and that is only true of the recolouring half.
#
# `tint` maps a pixel to `colour * luma(pixel)`, which makes it incomposable:
# run it twice and the second pass reads the first result's luminance as if it
# were the artwork's original shading, multiplying by the target's relative
# luminance a second time. On a white pixel that turns the error group's
# (255,92,84) into (171,62,56) - 33% too dark - and it hit 19 of the 97 icons
# in `midnight` and 27 in `sunset`. Every file stayed a valid TIFF and sips
# agreed with all of it, so nothing noticed.
WHITE = icons.TiffImage(1, 1, bytes((255, 255, 255, 255)))
GREY = icons.TiffImage(1, 1, bytes((200, 200, 200, 255)))
PERIWINKLE, RED = [140, 172, 255], [255, 92, 84]

once = icons.op_tint(WHITE, RED)
twice = icons.op_tint(icons.op_tint(WHITE, PERIWINKLE), RED)
check(tuple(once.pixels[:3]) == tuple(RED),
      f"one tint of white lands on the requested colour (got {tuple(once.pixels[:3])})")
check(tuple(twice.pixels[:3]) != tuple(RED),
      f"tinting twice does NOT land on it, which is the bug "
      f"({tuple(twice.pixels[:3])} vs {tuple(RED)})")
check(tuple(icons.op_tint(WHITE, RED).pixels[:3])
      != tuple(icons.op_tint(icons.op_tint(WHITE, PERIWINKLE), RED).pixels[:3]),
      "so the replacement has to be decided by op selection, not by op_tint")

# And through the op selection the recipe path uses, with two matching tints.
winner, _ = icons._select_ops([{"op": "tint", "color": PERIWINKLE},
                                 {"op": "tint", "color": RED}])
check(isinstance(winner, dict) and winner["color"] == RED,
      f"of two matching tints only the last one runs (got {winner})")
check(icons._select_ops([{"op": "grayscale"}])[0] is None,
      "an icon with no recolour op has no winner")

# The accumulate half must survive: `bnw` layers contrast 1.5 then 1.7 on
# purpose, and flattening that to "last op wins" would quietly weaken it.
one_pass = icons.op_contrast(icons.op_grayscale(GREY, 1.0), 1.5)
two_pass = icons.op_contrast(icons.op_contrast(
    icons.op_grayscale(GREY, 1.0), 1.5), 1.7)
check(tuple(one_pass.pixels[:3]) != tuple(two_pass.pixels[:3]),
      "two contrast passes still compound rather than the second replacing "
      f"the first ({tuple(one_pass.pixels[:3])} then {tuple(two_pass.pixels[:3])})")
check([s["op"] for s in icons._select_ops(
    [{"op": "grayscale"},
     {"op": "contrast", "amount": 1.5},
     {"op": "contrast", "amount": 1.7}])[1]] == ["grayscale", "contrast", "contrast"],
      "adjusting ops are all kept, in recipe order")

# Of the 97 shipped icons, 83 have neutral ink and 14 are chromatic. This is
# the fact the recipes are built on: a hue or tint op moves a neutral glyph
# completely and a chromatic one only partially, which is why `midnight` tints
# the general case and then overwrites the semantic groups by name.
#
# The measure is the chromaticity of each icon's alpha-weighted mean ink colour,
# `sum(channel * alpha) / sum(alpha)`. That form is deliberate. Classifying by
# a single pixel's colour is ill-conditioned, because the strongest pixel is
# usually an antialiased edge one whose colour has to be recovered by dividing
# by a small alpha, and dividing by 1 or 2 amplifies rounding into nonsense.
#
# An earlier version of this test claimed 69 white / 3 black / 25 coloured. That
# count was an artefact of a real bug rather than a property of the files: 23 of
# the icons declare premultiplied alpha and are not, so un-premultiplying them
# drove their soft edges to 255 and made them look like flat white glyphs. With
# the alpha detected from the data instead, the two populations separate cleanly
# and the split holds for any chromaticity threshold from 6 to 20, with a gap
# from 4.5 to 23.0 between the 15th and 14th values.
def mean_ink(px):
    sr = sg = sb = sa = 0
    for i in range(0, len(px), 4):
        a = px[i + 3]
        if not a:
            continue
        sr += px[i] * a
        sg += px[i + 1] * a
        sb += px[i + 2] * a
        sa += a
    if not sa:
        return None
    return sr / sa, sg / sa, sb / sa

ink = {"neutral": [], "chromatic": []}
for icon in names:
    colour = mean_ink(icons.read_tiff((BASE / icon).read_bytes()).pixels)
    spread = max(colour) - min(colour)
    ink["chromatic" if spread > 12 else "neutral"].append(icon)
check((len(ink["neutral"]), len(ink["chromatic"])) == (83, 14),
      f"the shipped set is 83 neutral-ink / 14 chromatic "
      f"(got {len(ink['neutral'])}/{len(ink['chromatic'])})")

# The 14 chromatic ones are the semantic glyphs the recipes name individually,
# so the list is asserted rather than left to the count above to imply.
CHROMATIC = {"CommentOnSmall", "ErrorFace", "ErrorNetwork", "ErrorNetworkScript",
             "ErrorScript", "HelpPython20x20", "OriginX", "OriginY", "OriginZ",
             "ParPython", "ShowDockOffError", "ShowDockOffScriptError",
             "WarnFace", "WarnScript"}
check({n[:-5] for n in ink["chromatic"]} == CHROMATIC,
      "the 14 chromatic icons are exactly the semantic glyphs")

# Three icons have pure black ink rather than white or a colour, and the recipe
# comments name them, because a recolour op cannot move them: `tint` maps a
# pixel to `colour * luma` and black has no luminance. Asserting the set keeps
# those comments honest, and the property is decidable without a threshold -
# every visible pixel is exactly (0,0,0), whatever its alpha.
black_ink = []
for icon in names:
    px = icons.read_tiff((BASE / icon).read_bytes()).pixels
    visible = [i for i in range(0, len(px), 4) if px[i + 3] > 0]
    if visible and all(px[i] == px[i+1] == px[i+2] == 0 for i in visible):
        black_ink.append(icon)
check(sorted(black_ink) == ["CommentOffSmall.tiff", "Cook.tiff", "Grid.tiff"],
      f"the 3 black-ink icons are the ones the recipes name, since a "
      f"recolour op cannot move them (got {sorted(black_ink)})")


# Recipes must fail loudly on a typo, not skip the op and produce a subtly
# wrong icon set. Each case below was a real failure mode while building this.
def _recipe_with(spec):
    return lambda: icons._run_op(probe_img, spec)

raises(icons.IconError, _recipe_with({"op": "tint", "color": [1, 2, 3], "strenght": 1.0}),
       "a misspelled op argument is rejected")
raises(icons.IconError, _recipe_with({"op": "tintt", "color": [1, 2, 3]}),
       "an unknown op is rejected and lists the known ones")
raises(icons.IconError, _recipe_with({"op": "tint"}),
       "a missing required argument is rejected")
raises(icons.IconError, _recipe_with({"op": "tint", "color": [1, 2]}),
       "a two-component colour is rejected")
raises(icons.IconError, _recipe_with({"op": "tint", "color": [1, 2, 300]}),
       "an out-of-range colour channel is rejected")

try:
    icons._run_op(probe_img, {"op": "tintt", "color": [1, 2, 3]})
except icons.IconError as exc:
    check("grayscale" in str(exc) and "tint" in str(exc),
          "the unknown-op error names the ops that do exist")

# ---------------------------------------------------------------- recipes

section("Recipes")

for theme in THEMES:
    recipe_path = theme_recipe_path(theme)
    if not recipe_path.exists():
        check(False, f"{theme}: recipe exists at {recipe_path}")
        continue
    try:
        recipe = icons.load_recipe(recipe_path)
        ok = True
    except icons.IconError as exc:
        check(False, f"{theme}: recipe parses ({exc})")
        continue
    check(ok, f"{theme}: recipe parses and declares version {icons.RECIPE_VERSION}")

# The empty-ops case is load-bearing, not an edge case. `default` is the only
# way back to stock icons, and a theme that writes nothing would leave the
# previously applied theme's icons in the install.
empty_out = tmp / "empty-recipe"
report = icons.apply_recipe(BASE, empty_out, {"version": 1, "ops": []})
check(report["verbatim"] is True, "an empty recipe reports itself as verbatim")
check(report["written"] == len(names),
      f"an empty recipe writes all {len(names)} icons")
check(all((empty_out / n).read_bytes() == (BASE / n).read_bytes() for n in names),
      "an empty recipe copies every icon byte for byte (no re-encode)")
check(report["bytes"] == sum((BASE / n).stat().st_size for n in names),
      "an empty recipe reports the baseline's total size")

# `default` is the reset path, so its shipped icons must be the stock bytes.
default_dir = theme_icons_dir("default")
if default_dir.is_dir():
    identical = sum(1 for n in names
                    if (default_dir / n).read_bytes() == (BASE / n).read_bytes())
    check(identical == len(names),
          f"default ships all {len(names)} icons byte-identical to the baseline "
          f"({identical} identical) - applying it is a lossless reset")

# Every theme must be able to rebuild itself, and every rebuild must validate.
for theme in THEMES:
    out = tmp / f"rebuild-{theme}"
    if not theme_recipe_path(theme).exists():
        continue
    recipe = icons.load_recipe(theme_recipe_path(theme))
    try:
        rep = icons.apply_recipe(BASE, out, recipe)
    except icons.IconError as exc:
        check(False, f"{theme}: recipe builds (raised {exc})")
        continue
    check(rep["written"] == len(names),
          f"{theme}: rebuild writes all {len(names)} icons")
    check(all(icons.read_tiff((out / n).read_bytes()) is not None for n in names),
          f"{theme}: every rebuilt icon decodes")
    print(f"         ({rep['written'] - rep['pixel_identical_to_baseline']} of "
          f"{rep['written']} changed pixels)")

# ------------------------------------------------------- reproducibility
#
# The contract that makes a recipe worth having. A theme's committed icon set
# must be exactly what its recipe produces - not approximately, not visually
# equivalent, the same bytes - so that anyone can regenerate, review the recipe
# instead of 780 KB of TIFF, and get the same directory back.
#
# `defaultnowarn` is the deliberate exception and the reason this is a test
# rather than an assumption. Its recipe is empty, so a rebuild is a verbatim
# copy of the baseline, but its WarnFace is a hand-placed blanked face: 551 of
# its 4096 pixels differ from the stock icon. A recipe cannot describe an edit
# made outside the recipe system, so that one file is not reproducible, and
# `build` refuses to overwrite the set without `--force` for exactly this
# reason. The other 96 are byte-identical.

section("Reproducibility")

for theme in THEMES:
    recipe_file = theme_recipe_path(theme)
    if not recipe_file.exists():
        check(False, f"{theme}: recipe exists at {recipe_file}")
        continue
    out = tmp / f"repro-{theme}"
    try:
        icons.apply_recipe(BASE, out, json.loads(recipe_file.read_text()))
    except icons.IconError as exc:
        check(False, f"{theme}: recipe builds (raised {exc})")
        continue
    shipped = theme_icons_dir(theme)
    if not shipped.is_dir():
        check(False, f"{theme}: has a generated Icons/ directory")
        continue
    differing = [n for n in sorted(icons.icon_names(shipped))
                 if (out / n).read_bytes() != (shipped / n).read_bytes()]
    if theme == "defaultnowarn":
        check(differing == ["WarnFace.tiff"],
              f"defaultnowarn differs only in its hand-placed icon "
              f"({differing})")
    else:
        check(not differing,
              f"{theme}: recipe reproduces the committed set byte for byte "
              f"({len(differing)} differ: {differing[:2]})")

# And the failure this protects against is silent, so assert the guard too: a
# rebuild that would drop the hand-placed file has to be visible before anyone
# runs it.
from tdthememaker import cli as thememaker_cli  # noqa: E402

probe = subprocess.run(
    [sys.executable, "-m", "tdthememaker.cli", "build",
     "defaultnowarn", "--check"],
    capture_output=True, text=True, cwd=ROOT)
check(probe.returncode == 2 and "WarnFace.tiff" in probe.stdout,
      f"`build --check` flags the hand-placed icon instead of overwriting it "
      f"(exit {probe.returncode})")

refuse = subprocess.run(
    [sys.executable, "-m", "tdthememaker.cli", "build", "defaultnowarn"],
    capture_output=True, text=True, cwd=ROOT)
check(refuse.returncode == 1 and "--force" in refuse.stderr,
      "`build` refuses to overwrite an existing set without --force")
check(icons.read_tiff(
    (theme_icons_dir("defaultnowarn") / "WarnFace.tiff").read_bytes()) is not None,
    "the hand-placed icon survived every command above")

# ---------------------------------------------------------------- export

section("export")

# Export is the newest half of this tool and the one that writes a format
# another tool has to read, so the checks below are mostly about the handoff:
# a theme written here must be one `tdtheme apply` can install, byte for byte.

# The reader lives in tdtheme and the writer lives here. If those two ever
# disagree, themes this tool writes become unreadable, so pin the agreement.
sample = {
    "tile.border.size": ["5"],
    "font.default.face": [""],
    "tile.connection.hilite1": ["1", "0", "0"],
    'key.with"quote': ['a"b', "", "x y"],
}
check(T.load_overlay(G.dump_overlay(sample, "TouchOptions"), "TouchOptions")
      == sample,
      "tdtheme's overlay loader reads what this tool writes")
check(T._load_overlay_fallback(G.dump_overlay(sample, "TouchOptions"),
                               "TouchOptions") == sample,
      "the fallback loader agrees, so the writer needs no PyYAML")

# A fresh install in a temp dir, so the real one is never read or written.
export_tmp = Path(tempfile.mkdtemp(prefix="tdthememaker-export-"))
install_dir = export_tmp / "install"
install_dir.mkdir()
for store in ("TouchColors", "TouchOptions"):
    shutil.copy2(ROOT / "baseline" / store, install_dir / store)
shutil.copytree(BASE, install_dir / "Icons")
themes_dir = export_tmp / "themes"

# Every path `apply` and `export` reach for is redirected, including `root`,
# which is where the "last applied" marker lives. A test run must not write
# inside the real repository, and the marker is the easiest thing to miss:
# nothing else in the module mentions the repo, and a wrong value there looks
# like a real fact about the user's install.
T.root = export_tmp
T.themes_dir = themes_dir
T.backups_dir = export_tmp / "backups"
T.config_dir = lambda: install_dir
T.icons_dir = lambda: install_dir / "Icons"

written = G.export_theme("probe")
check(set(G.STORE_FILES) <= set(written),
      "export writes both colour stores")
check(len([n for n in written if n not in G.STORE_FILES]) == len(names),
      f"export copies all {len(names)} icons verbatim")
check(T.load_overlay(T.theme_path("probe", "TouchColors").read_text())
      == OrderedDict(),
      "an unmodified install exports an empty overlay")
check(T.theme_path("probe", "TouchOptions").exists(),
      "both stores are written even when one has no differences")

# Icons must be bytes, not re-encodes. Compare the whole set.
copied = T.theme_icons_dir("probe")
check(all((copied / n).read_bytes() == (BASE / n).read_bytes() for n in names),
      "every exported icon is byte-identical to the source")
check(not any(n.endswith(".tmp") for n in os.listdir(copied)),
      "no temp files are left behind in the exported icon set")

try:
    G.export_theme("probe")
    check(False, "export refuses to clobber without --force")
except G.ExportError:
    check(True, "export refuses to clobber without --force")
check(G.export_theme("probe", force=True).keys() == written.keys(),
      "--force overwrites an existing theme")

for bad in ("", "a/b", "..\\evil"):
    try:
        G.export_theme(bad, force=True)
        check(False, f"export rejects the unusable name {bad!r}")
    except G.ExportError:
        check(True, f"export rejects the unusable name {bad!r}")

# The point of the whole feature: a change made in the install comes back out
# as a sparse overlay, and applying it lands the same bytes.
edited = T.TdFile.parse((install_dir / "TouchColors").read_bytes())
edited.data["dat.table.select.outline"] = ["0", "1", "0"]
(install_dir / "TouchColors").write_bytes(edited.to_bytes())

G.export_theme("e2e")
overlay = T.load_overlay(T.theme_path("e2e", "TouchColors").read_text())
check(set(overlay) == {"dat.table.select.outline"},
      "the overlay holds only the changed key")
check(overlay["dat.table.select.outline"] == ["0", "1", "0"],
      "the overlay records the new value verbatim")

T.apply("e2e")
check((install_dir / "TouchColors").read_bytes() == edited.to_bytes(),
      "tdtheme apply installs what export wrote, byte for byte")

report = G.export_report("e2e", written)
check(any("Icons" in line for line in report),
      "the CLI report mentions the copied icons")

# An install missing a store is not exportable; saying so beats writing a
# theme that silently omits half the configuration.
(install_dir / "TouchOptions").unlink()
try:
    G.export_theme("nostore", force=True)
    check(False, "export refuses an install with a missing store")
except G.ExportError as exc:
    check("TouchOptions" in str(exc), f"export names the missing store ({exc})")
shutil.copy2(ROOT / "baseline" / "TouchOptions", install_dir / "TouchOptions")

# The CLI must fail with a message, not a traceback. Called in-process rather
# than through a subprocess: `themes_dir` has no environment override, so a
# subprocess would write into the real repository instead of this temp dir.
# An unusable name is rejected before anything is created.
cli_error = io.StringIO()
with contextlib.redirect_stderr(cli_error):
    exit_code = thememaker_cli.cmd_export(
        argparse.Namespace(name="a/b", force=True))
check(exit_code != 0 and "Traceback" not in cli_error.getvalue()
      and "a/b" in cli_error.getvalue(),
      f"`export` reports an error cleanly (exit {exit_code})")

shutil.rmtree(export_tmp, ignore_errors=True)

# This tool writes into the tdtheme repository, so confirm it did not. The
# paths above are all redirected, and this catches a new one being added.
check(sorted(p.name for p in (ROOT / "themes").iterdir() if p.is_dir())
      == sorted(THEMES),
      "the real themes directory is exactly the six committed themes")
check(not (ROOT / ".applied.json").read_text().count('"e2e"'),
      "the real last-applied marker was not overwritten")
check((ROOT / "backup_probe_marker").exists() is False,
      "no stray files were written into the tdtheme repository")

# The hand-placed icon check above ran against the real theme; make sure the
# temp-dir games above did not touch it.
check(icons.read_tiff(
    (theme_icons_dir("defaultnowarn") / "WarnFace.tiff").read_bytes()) is not None,
    "the hand-placed icon is still intact after the export tests")

shutil.rmtree(tmp, ignore_errors=True)

print()
print("-" * 60)
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for label in failures:
        print(f"  - {label}")
    sys.exit(1)
print("all thememaker tests PASSED")
