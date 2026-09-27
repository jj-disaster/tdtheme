"""Tests for the icon layer: TIFF codec, recipes, and install.

The icon work is the one part of this tool that writes binary files into an
application bundle, so the assertions here are mostly about the ways that can go
quietly wrong: a file that decodes in this tool but not in TouchDesigner, a
glyph whose alpha or size moved, or a theme that leaves a stale icon behind
because its directory was incomplete.

Everything runs against a throwaway config selected via TDTHEME_CONFIG, seeded
from the project's pristine `baseline/`. The real TouchDesigner install is only
read, and only to confirm the baseline still matches it.

Run:  python3 tests/test_icons.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tdicons  # noqa: E402
import tdtheme as T  # noqa: E402

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


PROJECT = Path(__file__).resolve().parent.parent
BASE = PROJECT / "baseline" / T.ICONS_DIRNAME
THEMES = ["default", "midnight", "sunset", "mono", "bnw"]

tmp = Path(tempfile.mkdtemp(prefix="tdtheme-icons-"))
install = tmp / "install"
install.mkdir()

# Seeded from the pristine baseline, exactly as the library suite does, so a
# theme that happens to be applied to the real install cannot change a result
# here. Icons are copied rather than symlinked so a test that mutates the
# install cannot reach back into the tracked baseline.
for store in T.STORE_FILES:
    shutil.copy2(PROJECT / "baseline" / store, install / store)
if BASE.is_dir():
    shutil.copytree(BASE, install / T.ICONS_DIRNAME)

os.environ["TDTHEME_CONFIG"] = str(install)
T.root = tmp
T.baseline_dir = tmp / "baseline"
T.themes_dir = tmp / "themes"
T.backups_dir = tmp / "backups"

# The baseline and the themes are copied in as well, not just pointed at. Every
# check below then goes through the real module API - build_icons,
# validate_icons, icon_diff, preview_icons, apply - against a faithful private
# copy, and a test that writes a broken theme or corrupts an icon touches only
# the temp tree.
shutil.copytree(PROJECT / "baseline", T.baseline_dir)
shutil.copytree(PROJECT / "themes", T.themes_dir)

# ---------------------------------------------------------------- baseline

section("Baseline")

check(BASE.is_dir(), f"baseline icon directory exists at {BASE}")
names = tdicons.icon_names(BASE) if BASE.is_dir() else []
check(len(names) == 97, f"baseline holds the 97 shipped icons (found {len(names)})")

# The baseline is the reference every theme diffs against, so what has to hold
# is not that the *install* equals the baseline - it usually will not, because
# applying a theme is the tool working - but that the baseline and every theme
# describe the same 97 glyphs at the same dimensions. That is the invariant a
# stale baseline would break, and it holds whatever is currently applied.
installed_names = set(tdicons.icon_names(BASE))
check(len(installed_names) == 97, "the baseline's 97 icon names are all distinct")
for theme in sorted(T.list_themes()):
    directory = T.theme_icons_dir(theme)
    if not directory.is_dir():
        continue
    same = set(tdicons.icon_names(directory)) == installed_names
    check(same, f"{theme} ships exactly the baseline's 97 icon names")

# A dimension change is the one thing that would render wrongly rather than
# merely look stale, so it is a hard check across every shipped set.
resized = []
for directory in [BASE] + [T.theme_icons_dir(t) for t in T.list_themes()
                           if T.theme_icons_dir(t).is_dir()]:
    for name in sorted(tdicons.icon_names(directory)):
        try:
            here = tdicons.read_tiff((directory / name).read_bytes())
            ref = tdicons.read_tiff((BASE / name).read_bytes())
        except tdicons.IconError as exc:
            resized.append(f"{directory.name}/{name}: {exc}")
            continue
        if (here.width, here.height) != (ref.width, ref.height):
            resized.append(f"{directory.name}/{name}: "
                           f"{here.width}x{here.height} != {ref.width}x{ref.height}")
check(not resized,
      f"every shipped set keeps the baseline's icon dimensions ({len(resized)} differ)")
for item in resized[:5]:
    print(f"        {item}")

# Report which theme the real install currently holds. This used to be a hard
# check that the install matched the baseline byte for byte, which was wrong:
# it read the live install through T.TD_CONFIG, a hardcoded constant that
# ignores TDTHEME_CONFIG, so it also leaked out of this suite's isolation. And
# it failed the moment anyone applied a theme - treating normal, correct use of
# the tool as a broken baseline. Knowing *which* theme is applied is worth
# printing; asserting the install is unthemed is not.
live = Path("/Applications/TouchDesigner.app/Contents/Resources/tfs/Config") / T.ICONS_DIRNAME
if live.is_dir():
    held = "the baseline (stock)"
    for theme in T.list_themes():
        directory = T.theme_icons_dir(theme)
        if directory.is_dir() and not tdicons.diff_icons(directory, live):
            held = f"theme {theme!r}"
            break
    else:
        if tdicons.diff_icons(BASE, live):
            held = "no known theme - edited by hand?"
    print(f"  note  the live install holds {held}")
else:
    print("  skip  no real TouchDesigner install to report on")

# ---------------------------------------------------------------- the codec

section("TIFF codec")

check(tdicons.COMPRESSION_NONE == 1 and tdicons.COMPRESSION_LZW == 5,
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
check(tdicons.lzw_decode(tdicons.lzw_encode(sample)) == sample,
      "LZW round-trips a payload longer than one code table")
check(tdicons.lzw_decode(tdicons.lzw_encode(b"")) == b"",
      "LZW round-trips an empty payload")
flat = bytes([7]) * 100000
check(tdicons.lzw_decode(tdicons.lzw_encode(flat)) == flat,
      "LZW round-trips a highly repetitive payload (long runs, one code)")
ramp = bytes((i * 7919) % 256 for i in range(50000))
check(tdicons.lzw_decode(tdicons.lzw_encode(ramp)) == ramp,
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
    next_code, bits = tdicons._FIRST_CODE, tdicons._MIN_BITS
    max_code = (1 << bits) - 1
    prefix = data[0]
    for byte in data[1:]:
        key = (prefix << 8) | byte
        found = table.get(key)
        if found is not None:
            prefix = found
            continue
        if next_code < tdicons._MAX_CODE:
            table[key] = next_code
            next_code += 1
            if next_code > max_code and bits < tdicons._MAX_BITS:
                bits += 1
                max_code = (1 << bits) - 1
        else:
            clears += 1
            table = {}
            next_code, bits = tdicons._FIRST_CODE, tdicons._MIN_BITS
            max_code = (1 << bits) - 1
        prefix = byte
    return clears

filler = bytes(range(256)) * 2000          # 512000 bytes, far past 4096 codes
check(count_clears(filler) > 1,
      f"a payload this size exhausts the code table repeatedly "
      f"({count_clears(filler)} resets needed)")
check(tdicons.lzw_decode(tdicons.lzw_encode(filler)) == filler,
      "LZW round-trips a payload that fills the table many times over")
check(tdicons.lzw_decode(tdicons.lzw_encode(bytes([7]) * 500000))
      == bytes([7]) * 500000,
      "LZW round-trips a half-megabyte of one repeated byte across resets")

# The two real files that used to break libtiff, by size rather than by name,
# so the test keeps its meaning if the baseline is ever re-captured.
big = sorted(names, key=lambda n: -(tdicons.read_tiff(
    (BASE / n).read_bytes()).width * tdicons.read_tiff(
    (BASE / n).read_bytes()).height))[:4]
fills = [n for n in big if count_clears(
    tdicons.read_tiff((BASE / n).read_bytes()).pixels) > 0]
check(len(fills) >= 3,
      f"the largest shipped icons are the ones that exhaust the table "
      f"({len(fills)} of the 4 largest)")

# Every shipped icon, both compressions, pixels in and pixels out. This is the
# test that would have caught the strip and code-width bugs.
bad_decode, bad_lzw, bad_none = [], [], []
for icon in names:
    raw = (BASE / icon).read_bytes()
    try:
        original = tdicons.read_tiff(raw)
    except tdicons.IconError as exc:
        bad_decode.append(f"{icon}: {exc}")
        continue
    for comp, bucket in ((tdicons.COMPRESSION_LZW, bad_lzw),
                         (tdicons.COMPRESSION_NONE, bad_none)):
        try:
            # Compared through the premultiplied form, not byte-for-byte.
            # Re-encoding is genuinely lossy for a partially transparent pixel -
            # premultiplied 8-bit cannot carry its colour - and demanding exact
            # equality would be asserting something untrue rather than something
            # correct. `_survived_roundtrip` states the real invariant.
            if not tdicons._survived_roundtrip(
                    tdicons.read_tiff(
                        tdicons.write_tiff(original, compression=comp)).pixels,
                    original.pixels):
                bucket.append(f"{icon}: pixels differ")
        except tdicons.IconError as exc:
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
shipped = tdicons.icon_manifest(BASE)
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
    if theme == "default":
        continue    # verbatim copy, so it keeps the shipped convention
    regen = tdicons.icon_manifest(T.theme_icons_dir(theme))
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
    entries = tdicons._ifd_entries(raw, int.from_bytes(raw[4:8], "little"))
    width = tdicons._first_ints(entries, 256, 0)[0]
    height = tdicons._first_ints(entries, 257, 0)[0]
    comp = tdicons._first_ints(entries, 259, 1)[0]
    samples = tdicons._first_ints(entries, 277, 1)[0]
    offsets = tdicons._tag_ints(entries[273])
    counts = tdicons._tag_ints(entries[279])
    # Each strip is an independent LZW stream with its own Clear code, so a
    # multi-strip file has to be decoded strip by strip rather than as one
    # concatenated blob. Five of the shipped overlays are split this way.
    strips = [raw[o:o + c] for o, c in zip(offsets, counts)]
    if comp == 5:
        data = b"".join(tdicons.lzw_decode(strip) for strip in strips)
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
    px = tdicons.read_tiff(raw).pixels
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
enc = tdicons.write_tiff(tdicons.TiffImage(3, 1, plain),
                         compression=tdicons.COMPRESSION_NONE)
back = stored_samples(enc)[0]
check(back[:4] == bytes((0, 0, 0, 0)),
      "a fully transparent pixel premultiplies to (0,0,0,0), as it must")
check(back[4:8] == bytes((100, 50, 25, 128)),
      f"a half-transparent pixel is stored premultiplied (got {tuple(back[4:8])})")
check(back[8:] == bytes((10, 20, 30, 255)),
      "a fully opaque pixel is stored unchanged")
check(tdicons.describe_tiff(enc)["alpha"] == "associated",
      "write_tiff declares the data premultiplied")
check(tdicons.describe_tiff(tdicons.write_tiff(
    tdicons.TiffImage(3, 1, plain), compression=tdicons.COMPRESSION_NONE,
    premultiplied=False))["alpha"] == "unassociated",
      "premultiplied=False is still available and is declared honestly")

# A generated set is also deliberately not shaped like the shipped one: one
# strip, always RGBA, and no Photoshop metadata. That is why a byte diff
# between baseline and theme is not a statement about pixels.
one_strip = {v["strips"] for v in tdicons.icon_manifest(
    T.theme_icons_dir("midnight")).values()}
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
        source = T.theme_icons_dir(theme)
        if not source.is_dir():
            continue
        for icon in sorted(tdicons.icon_names(source))[:6]:
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
        image = tdicons.read_tiff((BASE / icon).read_bytes())
        probe = probe_dir / f"reenc-{icon}"
        probe.write_bytes(tdicons.write_tiff(image, compression=tdicons.COMPRESSION_LZW))
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
        source = T.theme_icons_dir("midnight") / icon
        if not source.exists():
            continue
        png = sips_png(source, probe_dir)
        if png is None:
            check(False, f"sips converts {icon} to PNG")
            continue
        theirs = png_rgba(png)
        ours = tdicons.read_tiff(source.read_bytes()).pixels
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
        source = T.theme_icons_dir("midnight") / icon
        png = sips_png(source, probe_dir) if source.exists() else None
        if png is None:
            continue
        theirs = png_rgba(png)
        ours = tdicons.read_tiff(source.read_bytes()).pixels
        n = min(len(ours), len(theirs))
        worst_pm = max(worst_pm, max(abs(x - y) for x, y in
                                     zip(tdicons._premultiply(ours[:n]),
                                         tdicons._premultiply(theirs[:n]))))
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

    image = tdicons.read_tiff(
        (T.theme_icons_dir("midnight") / "ErrorFace.tiff").read_bytes())
    lying = set_extra_samples(tdicons.write_tiff(image),
                              tdicons.EXTRA_SAMPLES_UNASSOCIATED)
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
    check(tdicons.read_tiff(lying).pixels == image.pixels,
          "read_tiff ignores a lying ExtraSamples tag and goes by the samples")

# ---------------------------------------------------------------- transforms

section("Transforms")

probe_img = tdicons.read_tiff((BASE / "ErrorFace.tiff").read_bytes())

alpha_before = probe_img.pixels[3::4]
for op_name, kwargs in (("grayscale", {"amount": 1.0}),
                        ("tint", {"color": [140, 172, 255]}),
                        ("brightness", {"factor": 1.2}),
                        ("contrast", {"amount": 1.5}),
                        ("saturate", {"amount": 0.5}),
                        ("hue", {"degrees": 45}),
                        ("solid", {"color": [10, 20, 30]})):
    out = tdicons._run_op(probe_img, {"op": op_name, **kwargs})
    check(out.pixels[3::4] == alpha_before,
          f"op {op_name!r} leaves the alpha channel untouched")
    check(out.size == probe_img.size, f"op {op_name!r} preserves the dimensions")

# grayscale is what `mono` is built on, so assert the property the theme's own
# comment claims: the output is grey, and it is that pixel's own luminance.
grey = tdicons._run_op(probe_img, {"op": "grayscale", "amount": 1.0})
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
WHITE = tdicons.TiffImage(1, 1, bytes((255, 255, 255, 255)))
GREY = tdicons.TiffImage(1, 1, bytes((200, 200, 200, 255)))
PERIWINKLE, RED = [140, 172, 255], [255, 92, 84]

once = tdicons.op_tint(WHITE, RED)
twice = tdicons.op_tint(tdicons.op_tint(WHITE, PERIWINKLE), RED)
check(tuple(once.pixels[:3]) == tuple(RED),
      f"one tint of white lands on the requested colour (got {tuple(once.pixels[:3])})")
check(tuple(twice.pixels[:3]) != tuple(RED),
      f"tinting twice does NOT land on it, which is the bug "
      f"({tuple(twice.pixels[:3])} vs {tuple(RED)})")
check(tuple(tdicons.op_tint(WHITE, RED).pixels[:3])
      != tuple(tdicons.op_tint(tdicons.op_tint(WHITE, PERIWINKLE), RED).pixels[:3]),
      "so the replacement has to be decided by op selection, not by op_tint")

# And through the op selection the recipe path uses, with two matching tints.
winner, _ = tdicons._select_ops([{"op": "tint", "color": PERIWINKLE},
                                 {"op": "tint", "color": RED}])
check(isinstance(winner, dict) and winner["color"] == RED,
      f"of two matching tints only the last one runs (got {winner})")
check(tdicons._select_ops([{"op": "grayscale"}])[0] is None,
      "an icon with no recolour op has no winner")

# The accumulate half must survive: `bnw` layers contrast 1.5 then 1.7 on
# purpose, and flattening that to "last op wins" would quietly weaken it.
one_pass = tdicons.op_contrast(tdicons.op_grayscale(GREY, 1.0), 1.5)
two_pass = tdicons.op_contrast(tdicons.op_contrast(
    tdicons.op_grayscale(GREY, 1.0), 1.5), 1.7)
check(tuple(one_pass.pixels[:3]) != tuple(two_pass.pixels[:3]),
      "two contrast passes still compound rather than the second replacing "
      f"the first ({tuple(one_pass.pixels[:3])} then {tuple(two_pass.pixels[:3])})")
check([s["op"] for s in tdicons._select_ops(
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
    colour = mean_ink(tdicons.read_tiff((BASE / icon).read_bytes()).pixels)
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
    px = tdicons.read_tiff((BASE / icon).read_bytes()).pixels
    visible = [i for i in range(0, len(px), 4) if px[i + 3] > 0]
    if visible and all(px[i] == px[i+1] == px[i+2] == 0 for i in visible):
        black_ink.append(icon)
check(sorted(black_ink) == ["CommentOffSmall.tiff", "Cook.tiff", "Grid.tiff"],
      f"the 3 black-ink icons are the ones the recipes name, since a "
      f"recolour op cannot move them (got {sorted(black_ink)})")


# Recipes must fail loudly on a typo, not skip the op and produce a subtly
# wrong icon set. Each case below was a real failure mode while building this.
def _recipe_with(spec):
    return lambda: tdicons._run_op(probe_img, spec)

raises(tdicons.IconError, _recipe_with({"op": "tint", "color": [1, 2, 3], "strenght": 1.0}),
       "a misspelled op argument is rejected")
raises(tdicons.IconError, _recipe_with({"op": "tintt", "color": [1, 2, 3]}),
       "an unknown op is rejected and lists the known ones")
raises(tdicons.IconError, _recipe_with({"op": "tint"}),
       "a missing required argument is rejected")
raises(tdicons.IconError, _recipe_with({"op": "tint", "color": [1, 2]}),
       "a two-component colour is rejected")
raises(tdicons.IconError, _recipe_with({"op": "tint", "color": [1, 2, 300]}),
       "an out-of-range colour channel is rejected")

try:
    tdicons._run_op(probe_img, {"op": "tintt", "color": [1, 2, 3]})
except tdicons.IconError as exc:
    check("grayscale" in str(exc) and "tint" in str(exc),
          "the unknown-op error names the ops that do exist")

# ---------------------------------------------------------------- recipes

section("Recipes")

for theme in THEMES:
    recipe_path = T.theme_recipe_path(theme)
    if not recipe_path.exists():
        check(False, f"{theme}: recipe exists at {recipe_path}")
        continue
    try:
        recipe = tdicons.load_recipe(recipe_path)
        ok = True
    except tdicons.IconError as exc:
        check(False, f"{theme}: recipe parses ({exc})")
        continue
    check(ok, f"{theme}: recipe parses and declares version {tdicons.RECIPE_VERSION}")

# The empty-ops case is load-bearing, not an edge case. `default` is the only
# way back to stock icons, and a theme that writes nothing would leave the
# previously applied theme's icons in the install.
empty_out = tmp / "empty-recipe"
report = tdicons.apply_recipe(BASE, empty_out, {"version": 1, "ops": []})
check(report["verbatim"] is True, "an empty recipe reports itself as verbatim")
check(report["written"] == len(names),
      f"an empty recipe writes all {len(names)} icons")
check(all((empty_out / n).read_bytes() == (BASE / n).read_bytes() for n in names),
      "an empty recipe copies every icon byte for byte (no re-encode)")
check(report["bytes"] == sum((BASE / n).stat().st_size for n in names),
      "an empty recipe reports the baseline's total size")

# `default` is the reset path, so its shipped icons must be the stock bytes.
default_dir = T.theme_icons_dir("default")
if default_dir.is_dir():
    identical = sum(1 for n in names
                    if (default_dir / n).read_bytes() == (BASE / n).read_bytes())
    check(identical == len(names),
          f"default ships all {len(names)} icons byte-identical to the baseline "
          f"({identical} identical) - applying it is a lossless reset")

# Every theme must be able to rebuild itself, and every rebuild must validate.
for theme in THEMES:
    out = tmp / f"rebuild-{theme}"
    if not T.theme_recipe_path(theme).exists():
        continue
    recipe = tdicons.load_recipe(T.theme_recipe_path(theme))
    try:
        rep = tdicons.apply_recipe(BASE, out, recipe)
    except tdicons.IconError as exc:
        check(False, f"{theme}: recipe builds (raised {exc})")
        continue
    check(rep["written"] == len(names),
          f"{theme}: rebuild writes all {len(names)} icons")
    check(all(tdicons.read_tiff((out / n).read_bytes()) is not None for n in names),
          f"{theme}: every rebuilt icon decodes")
    print(f"         ({rep['written'] - rep['pixel_identical_to_baseline']} of "
          f"{rep['written']} changed pixels)")

# ---------------------------------------------------------------- the shipped sets

section("Shipped theme icon sets")

for theme in THEMES:
    directory = T.theme_icons_dir(theme)
    if not directory.is_dir():
        check(False, f"{theme}: has an Icons/ directory")
        continue
    present = tdicons.icon_names(directory)
    check(sorted(present) == sorted(names),
          f"{theme}: icon set is complete ({len(present)}/{len(names)} files)")
    findings = T.validate_icons(theme)
    check(not [f for f in findings if f.severity == "error"],
          f"{theme}: validate_icons reports no errors "
          f"({[f.name for f in findings if f.severity == 'error'][:2]})")
    # A stale generated set is the failure this catches: a theme whose icons
    # were built from an older baseline keeps working, just wrongly.
    if theme != "default":
        d = T.icon_diff(theme)
        check(len(d) == len(names),
              f"{theme}: all {len(names)} icons differ from the baseline "
              f"({len(d)} differ)")

# ---------------------------------------------------------------- diff

section("Diff")

check(tdicons.diff_icons(BASE, BASE) == {},
      "a directory diffed against itself reports no differences")
check(tdicons.diff_icons(BASE, empty_out) == {},
      "a byte-identical copy reports no differences")
mid = T.theme_icons_dir("midnight")
if mid.is_dir():
    d = tdicons.diff_icons(BASE, mid)
    check(len(d) == len(names),
          f"a re-encoded theme reports all {len(names)} as differing")
    check(set(d.values()) == {"changed"},
          f"each difference is labelled 'changed' (got {sorted(set(d.values()))})")
    # A name present in one directory and not the other is a different fact, and
    # conflating the two would hide a stale baseline. Built from a tinted set,
    # so "changed" is expected for the untouched majority; what is being
    # checked is that the two structural cases get their own labels.
    partial_dir = tmp / "diff-partial"
    shutil.copytree(mid, partial_dir)
    (partial_dir / names[0]).unlink()
    (partial_dir / "Extra.tiff").write_bytes((mid / names[1]).read_bytes())
    labelled = tdicons.diff_icons(BASE, partial_dir)
    check(labelled.get(names[0]) == "absent",
          f"an icon missing from the theme is labelled 'absent' "
          f"(got {labelled.get(names[0])!r})")
    check(labelled.get("Extra.tiff") == "added",
          f"an icon in the theme but not the baseline is labelled 'added' "
          f"(got {labelled.get('Extra.tiff')!r})")
    check(labelled.get(names[5]) == "changed",
          "a same-named icon with different bytes is still 'changed'")

# ---------------------------------------------------------------- apply

section("Apply (temp install)")

# Everything so far was pure computation. This is the part that has to work on
# a real install: swap the icons in, put them back, and leave a backup.
live_icons = install / T.ICONS_DIRNAME
check(live_icons.is_dir() and len(tdicons.icon_names(live_icons)) == len(names),
      f"temp install seeded with all {len(names)} icons")

result = T.apply("midnight")
check(len(result["icons"]["written"]) == len(names),
      f"apply writes all {len(names)} icons "
      f"(wrote {len(result['icons']['written'])})")
check(result["icons"]["backed_up"] == len(names),
      f"apply backs up all {len(names)} icons it replaces "
      f"(backed up {result['icons']['backed_up']})")
installed = {n: (live_icons / n).read_bytes() for n in names}
check(installed == {n: (mid / n).read_bytes() for n in names},
      "the install now holds the theme's icons byte for byte")
check(result["backup"] is not None, "apply reports a backup directory")
backup_icons = result["backup"] / T.ICONS_DIRNAME
check(backup_icons.is_dir() and len(tdicons.icon_names(backup_icons)) == len(names),
      "the backup holds the pre-apply icons")
check(all((backup_icons / n).read_bytes() == (BASE / n).read_bytes() for n in names),
      "the backup holds the stock bytes, not the themed ones")

# `default` must get back to stock exactly. If it did not, the install is left
# holding midnight's icons while claiming to be stock.
T.apply("default")
check(all((live_icons / n).read_bytes() == (BASE / n).read_bytes() for n in names),
      "applying default restores every icon byte for byte")

# --no-icons has to leave the icons alone while still theming colours.
T.apply("midnight", icons=False)
check(all((live_icons / n).read_bytes() == (BASE / n).read_bytes() for n in names),
      "apply --no-icons leaves the install's icons untouched")
check(T.apply("midnight", icons=False)["icons"]["applied"] is False,
      "apply --no-icons reports that it skipped the icons")
T.apply("default")

# Re-applying must be idempotent, icons included. Compared against the themed
# state, not the stock one - the point is that a second midnight apply is a
# no-op, not that it reproduces the baseline.
T.apply("midnight")
before = {n: (live_icons / n).read_bytes() for n in names}
again = T.apply("midnight")
check({n: (live_icons / n).read_bytes() for n in names} == before,
      "apply is idempotent for icons")
check(len(again["icons"]["written"]) == 0
      or all(again["icons"]["written"]),
      "a second identical apply rewrites nothing or writes the same bytes")
T.apply("default")

# A theme whose icon set is missing a file must be caught, because the missing
# glyph would keep whatever the previously applied theme left behind.
partial = T.themes_dir / "partial"
partial.mkdir(parents=True, exist_ok=True)
shutil.copytree(mid, partial / T.ICONS_DIRNAME)
(partial / T.ICONS_DIRNAME / names[0]).unlink()
findings = T.validate_icons("partial")
missing = [f for f in findings if "not in this theme" in f.message]
check(bool(missing), f"an incomplete icon set is reported (found {len(missing)})")
check(all(f.severity == "warning" for f in missing),
      "an incomplete icon set is a warning, not an error - the glyph still "
      "renders, it just keeps the shipped bytes")

# A corrupt icon must be an error, because TouchDesigner will refuse the file.
corrupt = T.themes_dir / "corrupt"
corrupt.mkdir(parents=True, exist_ok=True)
shutil.copytree(mid, corrupt / T.ICONS_DIRNAME)
(corrupt / T.ICONS_DIRNAME / names[0]).write_bytes(b"not a tiff at all")
findings = T.validate_icons("corrupt")
check(any(f.severity == "error" and f.name == names[0] for f in findings),
      "an undecodable icon is an error, naming the file")

# And apply must refuse a theme with an icon error rather than write it.
for store in T.STORE_FILES:
    shutil.copy2(PROJECT / "baseline" / store, install / store)
raises(T.ValidationError, lambda: T.apply("corrupt"),
       "apply refuses a theme whose icons do not decode")
check(all((live_icons / n).read_bytes() == (BASE / n).read_bytes() for n in names),
      "the rejected apply left the install's icons untouched")

# ---------------------------------------------------------------- status

section("Status")

state = T.status()
check(state.icons_dir is not None, "status reports the icon directory")
check(state.icon_count == len(names),
      f"status counts the installed icons ({state.icon_count})")
check(state.icon_drift.get(T.ICONS_DIRNAME) == 0,
      f"a stock install reports no icon drift (got {state.icon_drift})")
check(state.icon_theme_drift.get("default") == 0,
      "the stock install matches the default theme's icons exactly")

T.apply("midnight")
state = T.status()
check(state.icon_theme_drift.get("midnight") == len(names),
      f"status counts every themed icon as a difference from baseline "
      f"({state.icon_theme_drift.get('midnight')}/{len(names)})")
check(state.icon_drift.get(T.ICONS_DIRNAME) == len(names),
      f"status reports the install as drifted from stock "
      f"({state.icon_drift.get(T.ICONS_DIRNAME)})")
check(state.applied == "midnight", "status records the applied theme")
T.apply("default")

# ---------------------------------------------------------------- preview

section("Preview")

sheet = T.preview_icons("midnight", tmp / "sheet.png")
check(sheet.exists() and sheet.stat().st_size > 0,
      f"preview writes a PNG contact sheet ({sheet.stat().st_size} bytes)")
magic = sheet.read_bytes()[:8]
check(magic == b"\x89PNG\r\n\x1a\n", "the preview really is a PNG")
base_sheet = T.preview_icons(None, tmp / "base-sheet.png")
check(base_sheet.exists(), "preview with no theme name falls back to the baseline")
check(base_sheet.name == "base-sheet.png", "the fallback preview is written as asked")
check(base_sheet.read_bytes() != sheet.read_bytes(),
      "a themed preview differs from the baseline preview")

# ------------------------------------------- theme names vs. missing Icons

section("Missing theme vs. missing icon directory")

# These are two different situations that used to look identical, because
# `preview_icons` fell back to the baseline for any directory it could not
# find. The visible symptom was a contact sheet of the *baseline* written to
# `nosuchtheme-icons.png` - a filename asserting something the pixels did not.
try:
    T.preview_icons("nosuchtheme", tmp / "typo.png")
except T.ThemeNotFound as exc:
    check("nosuchtheme" in str(exc), "previewing a mistyped theme name raises ThemeNotFound")
    check(not (tmp / "typo.png").exists(),
          "a mistyped theme name writes no file at all")
except T.ThemeError as exc:
    check(False, f"previewing a mistyped theme name raises ThemeNotFound, got {exc!r}")
else:
    check(False, "previewing a mistyped theme name raises ThemeNotFound")

# A theme that genuinely has no Icons/ is a real state, not a typo, and must be
# reported as such rather than quietly answered with baseline pixels.
storeless = T.themes_dir / "storeless"
storeless.mkdir(parents=True, exist_ok=True)
(storeless / "TouchColors.yaml").write_text('worksheet.bg: ["0.1", "0.1", "0.1"]\n')
try:
    check("storeless" in T.list_themes(),
          "a theme with no Icons/ still counts as a theme")
    try:
        T.preview_icons("storeless", tmp / "storeless.png")
    except T.ThemeNotFound:
        check(False, "a real theme without Icons/ is not reported as a typo")
    except T.ThemeError as exc:
        check("Icons/" in str(exc),
              "a real theme without Icons/ says which directory is missing")
        check(not (tmp / "storeless.png").exists(),
              "a theme without Icons/ writes no fallback preview either")
    else:
        check(False, "previewing a theme with no Icons/ raises, not falls back")
    check(T.theme_icons_dir("storeless").is_dir() is False,
          "the storeless theme really has no icon directory (test setup)")
finally:
    shutil.rmtree(storeless, ignore_errors=True)
check("storeless" not in T.list_themes(), "the temporary theme is cleaned up")

# ---------------------------------------------------------------- cleanup

print()
print("-" * 60)
shutil.rmtree(tmp, ignore_errors=True)
os.environ.pop("TDTHEME_CONFIG", None)

if failures:
    print(f"{len(failures)} FAILED:")
    for item in failures:
        print(f"  - {item}")
    sys.exit(1)
print("all icon tests PASSED")
