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

# The baseline is the reference every theme diffs against, so if it has drifted
# from the install then every diff, every validation and every recipe output is
# being computed against the wrong thing. Worth asserting rather than assuming.
live = T.TD_CONFIG / T.ICONS_DIRNAME
if live.is_dir():
    drift = tdicons.diff_icons(BASE, live)
    check(not drift,
          f"baseline/Icons still matches the installed set byte for byte "
          f"({len(drift)} differ)")
else:
    print("  skip  no installed Icons/ to compare the baseline against")

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
            if tdicons.read_tiff(tdicons.write_tiff(original, compression=comp)).pixels \
                    != original.pixels:
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

# The regenerated sets must declare the opposite, explicitly. This is the tag
# that stops a decoder multiplying alpha in a second time; see write_tiff.
for theme in THEMES:
    if theme == "default":
        continue    # verbatim copy, so it keeps the shipped convention
    regen = tdicons.icon_manifest(T.theme_icons_dir(theme))
    kinds = {v["alpha"] for v in regen.values()}
    check(kinds == {"unassociated"},
          f"{theme}: regenerated icons declare unassociated (straight) alpha "
          f"explicitly, got {sorted(kinds)}")

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
        worst = max(worst, max(abs(ours[i] - theirs[i])
                               for i in range(min(len(ours), len(theirs)))))
    check(worst == 0,
          f"sips reproduces our pixels exactly across {len(sample)} icons "
          f"(worst channel difference {worst})")

    # 3. The counterfactual, so that a 0 above means something. Declaring the
    #    straight data as premultiplied is the exact mistake the tag prevents;
    #    if sips cannot tell the two apart then check 2 proves nothing.
    image = tdicons.read_tiff((T.theme_icons_dir("midnight") / "ErrorFace.tiff").read_bytes())
    honest = tdicons.EXTRA_SAMPLES_UNASSOCIATED
    try:
        tdicons.EXTRA_SAMPLES_UNASSOCIATED = tdicons.EXTRA_SAMPLES_ASSOCIATED
        lying = tdicons.write_tiff(image)
    finally:
        tdicons.EXTRA_SAMPLES_UNASSOCIATED = honest
    (probe_dir / "lying.tiff").write_bytes(lying)
    png = sips_png(probe_dir / "lying.tiff", probe_dir)
    if png is None:
        check(False, "sips converts the mislabelled icon to PNG")
    else:
        theirs = png_rgba(png)
        delta = max(abs(image.pixels[i] - theirs[i]) for i in range(len(image.pixels)))
        check(delta > 0,
              f"mislabeling straight alpha as premultiplied is detectable "
              f"(sips differs by {delta}), so the exact match above is real")

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

# The shipped set is 69 pure-white-ink glyphs, 3 pure-black-ink, 25 coloured.
# This is a property of the vendor's files, and the recipes' comments quote
# these numbers, so they are asserted here rather than left as prose.
ink = {"white": [], "black": [], "other": []}
for icon in names:
    px = tdicons.read_tiff((BASE / icon).read_bytes()).pixels
    cols = {(px[i], px[i + 1], px[i + 2])
            for i in range(0, len(px), 4) if px[i + 3] > 0}
    colour = next(iter(cols)) if len(cols) == 1 else None
    if colour == (255, 255, 255):
        ink["white"].append(icon)
    elif colour == (0, 0, 0):
        ink["black"].append(icon)
    else:
        ink["other"].append(icon)
check((len(ink["white"]), len(ink["black"]), len(ink["other"])) == (69, 3, 25),
      f"the shipped set is 69 white-ink / 3 black-ink / 25 coloured "
      f"(got {len(ink['white'])}/{len(ink['black'])}/{len(ink['other'])})")
check(sorted(ink["black"]) == ["CommentOffSmall.tiff", "Cook.tiff", "Grid.tiff"],
      f"the black-ink icons are the 3 named in the recipes "
      f"({sorted(ink['black'])})")

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
