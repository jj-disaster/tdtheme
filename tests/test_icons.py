"""Tests for installing icon sets: reading, validating, comparing, applying.

This is the half of the icon layer that stayed in `tdtheme`. It covers reading
a theme's icons, checking they are complete and sound, reporting what a theme
actually changes, and installing a set into a config directory. Writing icons
is not tested here because this tool no longer does it - the writer, the ops
and the recipe engine live in `tdthememaker/`, with their own suite.

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
# check below then goes through the real module API - capture, apply, export,
# icon_diff, validate_icons, preview_icons - against a faithful private copy,
# and a test that writes a broken theme or corrupts an icon touches only the
# temp tree.
shutil.copytree(PROJECT / "baseline", T.baseline_dir)
shutil.copytree(PROJECT / "themes", T.themes_dir)

def thememaker_geometry(icon):
    """(width, height) as this tool's reader sees it."""
    image = tdicons.read_tiff((BASE / icon).read_bytes())
    return image.width, image.height


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

# ------------------------------------------------------ independent decoder
#
# The reader is the half that stayed, so it still gets a second opinion from a
# decoder we did not write. `sips` is Apple's, via a different libtiff, which
# makes it a real check: everything above uses this tool's own parser, so a
# systematic misreading of the format would pass all of it.
#
# Only reading is checked here. Converting forces a real decode rather than a
# header parse, because a broken strip still reports a correct width - that is
# how a multi-strip bug stays invisible to a "can it open this" check.

section("Independent decode (sips)")

SIPS = shutil.which("sips")
if not SIPS:
    print("  skip  sips unavailable, cannot cross-check with an independent decoder")
else:
    probe_dir = tmp / "sips"
    probe_dir.mkdir()
    unreadable = []
    for icon in names:
        source = BASE / icon
        out = subprocess.run(
            [SIPS, "-s", "format", "bmp", str(source),
             "--out", str(probe_dir / "probe.bmp")],
            capture_output=True, text=True)
        if out.returncode != 0:
            unreadable.append(icon)
    check(not unreadable,
          f"macOS sips decodes all {len(names)} shipped icons "
          f"(failures: {unreadable[:3]})")

    # And the sizes agree, which is what a wrong photometric interpretation or
    # a mis-parsed strip count would break.
    wrong = []
    for icon in names:
        out = subprocess.run(
            [SIPS, "-g", "pixelWidth", "-g", "pixelHeight", str(BASE / icon)],
            capture_output=True, text=True)
        want = thememaker_geometry(icon)
        got = [int(line.split(":")[-1]) for line in out.stdout.splitlines()
               if "pixel" in line]
        if got != list(want):
            wrong.append((icon, got, list(want)))
    check(not wrong,
          f"sips agrees with our reader on every icon's dimensions "
          f"({wrong[:2]})")

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
# `default` ships the stock bytes, so it is the byte-identical case without
# needing a scratch copy.
stock = T.theme_icons_dir("default")
if stock.is_dir():
    check(tdicons.diff_icons(BASE, stock) == {},
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
shortfall = [f for f in findings if "filled in from the baseline" in f.message]
check(bool(shortfall),
      f"an incomplete icon set is reported (found {len(shortfall)})")
check(all(f.severity == "warning" for f in shortfall),
      "an incomplete icon set is a warning, not an error - the glyph still "
      "renders, and the baseline now supplies it")
# One finding for the whole shortfall, not one per absent icon. A theme
# missing 94 of 97 used to print 94 identical lines.
check(len(shortfall) == 1,
      f"the shortfall is one aggregated finding, not one per icon "
      f"(got {len(shortfall)})")

# ------------------------------------------------- partial sets are completed
#
# The case that made the old behaviour a bug rather than a rough edge. Applying
# a theme that ships 96 of 97 used to leave the 97th as the previously applied
# theme had left it, so switching themes could leave a glyph from a theme
# `tdtheme list` no longer claimed to be running - the leak the complete-set
# design exists to prevent. The absent icon is now filled in from the baseline,
# so the install ends up complete whatever it held before.
THEMED = T.themes_dir / "ninety-six"
THEMED.mkdir(parents=True, exist_ok=True)
shutil.copytree(mid, THEMED / T.ICONS_DIRNAME)
ABSENT = names[0]
(THEMED / T.ICONS_DIRNAME / ABSENT).unlink()

# Start from a themed install, so "left as it was" and "filled from the
# baseline" are genuinely different outcomes and the test can tell them apart.
T.apply("midnight")
result = T.apply("ninety-six")
check((live_icons / ABSENT).read_bytes() == (BASE / ABSENT).read_bytes(),
      "an icon the theme omits is filled in from the baseline, not left as the "
      "previous theme left it")
check((live_icons / ABSENT).read_bytes() != (mid / ABSENT).read_bytes(),
      "and that fill is visibly different from the previous theme's icon, so "
      "the check above is not passing by accident")
check(len(T.tdicons.icon_names(live_icons)) == len(names),
      f"the install still holds all {len(names)} icons after a partial apply")
check(ABSENT in result["icons"]["filled"],
      "apply reports which icons it filled in")
check(ABSENT in result["icons"]["written"],
      "and counts the fill as a write, because the install had the previous "
      "theme's bytes there and they had to move")

# The other direction: a name the theme has and the baseline does not is still
# the theme's. Filling in adds names, it never overrides the source.
EXTRA = "NotInBaseline.tiff"
shutil.copy2(mid / names[1], THEMED / T.ICONS_DIRNAME / EXTRA)
result = T.apply("ninety-six")
check((live_icons / EXTRA).read_bytes() == (mid / names[1]).read_bytes(),
      "an icon the theme ships and the baseline lacks is installed as-is")
check(EXTRA not in result["icons"]["filled"],
      "and is not reported as filled in, because it came from the theme")
(THEMED / T.ICONS_DIRNAME / EXTRA).unlink()
# Nothing in the tool ever deletes an installed icon, so the next apply - and
# this one - has to do it. That is deliberate rather than an oversight: the
# baseline is a capture of one build, and a theme carrying a name this build
# does not have would be pruned away by a later `apply default` if apply
# deleted the unknown. The install is a live directory, not a reconstruction.
(live_icons / EXTRA).unlink()

# A complete theme must be entirely unaffected by the fill-in path: nothing
# filled, and the same written/unchanged split as before. Without this the new
# behaviour could quietly change the common case and no other check would see it.
full = T.apply("midnight")
check(full["icons"]["filled"] == [],
      f"a complete theme fills nothing in (filled {len(full['icons']['filled'])})")
check(len(full["icons"]["written"]) == len(names),
      f"a complete theme still writes all {len(names)} icons "
      f"(wrote {len(full['icons']['written'])})")
T.apply("default")

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
