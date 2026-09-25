"""Tests for merge / diff / validate / capture / apply / export.

Everything runs against a throwaway copy of the install in a temp directory,
selected via the TDTHEME_CONFIG environment variable. The real TouchDesigner
config is only ever read.

Run:  python3 tests/test_tdtheme.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


# ---------------------------------------------------------------- fixtures

REAL = T.TD_CONFIG
tmp = Path(tempfile.mkdtemp(prefix="tdtheme-test-"))
install = tmp / "install"
install.mkdir()
for store in T.STORE_FILES:
    shutil.copy2(REAL / store, install / store)

os.environ["TDTHEME_CONFIG"] = str(install)
# Patch every path root so no test artefact escapes into the real project dir
# (notably .applied.json, which lives under root rather than baseline_dir).
T.root = tmp
T.baseline_dir = tmp / "baseline"
T.themes_dir = tmp / "themes"
T.backups_dir = tmp / "backups"

print("Fixture")
print("-" * 60)
check((install / T.TOUCHCOLORS).exists(), f"temp install populated at {install}")

# ---------------------------------------------------------------- overlay io

print()
print("Overlay format")
print("-" * 60)

sample = OrderedDict([
    ("tile.border.size", ["5"]),
    ("font.default.face", [""]),
    ("tile.connection.hilite1", ["1", "0", "0"]),
    ('key.with"quote', ['a"b', "", "x y"]),
])
text = T.dump_overlay(sample, "TouchOptions")
loaded = T.load_overlay(text, "TouchOptions")
check(loaded == sample, "overlay round-trips through loader")

# The same text through the zero-dependency path (no PyYAML here).
manual = T._load_overlay_fallback(text, "TouchOptions")
check(manual == sample, "overlay round-trips through the fallback loader")
check(manual == loaded, "fallback and yaml loaders agree (when yaml absent)")

check(T._have_yaml() is False, "PyYAML genuinely absent, so fallback was exercised")

raises(T.FileFormatError,
      lambda: T.load_overlay("a.b:\n", "x"),
      "null value is rejected with a clear error")

try:
    T.load_overlay("a.b:\n", "x")
except T.FileFormatError as exc:
    check("not supported in v1" in str(exc), "null value explains deletion is unsupported")

# A bare scalar is accepted as a one-field list.
check(T.load_overlay('a.b: "5"\n', "x") == OrderedDict([("a.b", ["5"])]),
      "bare quoted scalar loads as a one-field list")
check(T.load_overlay("a.b: 5\n", "x") == OrderedDict([("a.b", ["5"])]),
      "bare unquoted scalar loads as a one-field list")

# ---------------------------------------------------------------- merge/diff

print()
print("Merge and diff")
print("-" * 60)

base = T.load_file(install / T.TOUCHOPTIONS, T.TOUCHOPTIONS)
merged = T.merge(base, OrderedDict([("tile.border.size", ["9"])]))
check(merged.get("tile.border.size") == ["9"], "merge overrides one key")
check(merged.get("tile.inout.origsize") == ["10"], "merge leaves other keys alone")
check(base.get("tile.border.size") == ["5"], "merge does not mutate the base")
check(len(merged) == len(base), "merge does not add or drop keys")
check(merged.to_bytes() != base.to_bytes(), "merged file differs from base")

check(T.diff(base, merged) == OrderedDict([("tile.border.size", ["9"])]),
      "diff finds exactly the changed key")
check(T.diff(base, base) == OrderedDict(), "diff of a file against itself is empty")

remerged = T.merge(base, T.diff(base, merged))
check(remerged.to_bytes() == merged.to_bytes(),
      "baseline + diff(base, other) reproduces other exactly")

# ---------------------------------------------------------------- validation

print()
print("Validation")
print("-" * 60)

good = T.merge(base, OrderedDict([("tile.border.size", ["8"])]))
check(not [f for f in T.validate(good, base) if f.severity == "error"],
      "a valid change produces no errors")

bad = T.merge(base, OrderedDict([("tile.inout.origsize", ["0"])]))
errs = [f for f in T.validate(bad, base) if f.severity == "error"]
check(bool(errs), "tile.inout.origsize 0 is caught as an error")
check(any("tile.inout.origsize" == f.key for f in errs),
      "the offending key is named in the error")
check(any("destroys" in f.message for f in errs),
      "the error explains the consequence, not just the rule")

neg = T.merge(base, OrderedDict([("tile.flagv.origsize", ["-5"])]))
check([f for f in T.validate(neg, base) if f.severity == "error"],
      "negative size is caught too")

# The real shipped baseline must validate completely clean - no false
# positives. Regression guard: an unconditional "size > 0" rule flags the
# shipped `font.relative.size 0`, which would make every apply fail.
real_base = T.load_file(install / T.TOUCHOPTIONS, T.TOUCHOPTIONS)
shipped_findings = T.validate(real_base, real_base)
check(not shipped_findings,
      f"the shipped TouchOptions baseline produces no findings at all "
      f"({len(shipped_findings)} found)")
size_keys = [k for k in real_base.keys() if T.SIZE_KEY_RE.search(k)]
print(f"         ({len(size_keys)} size keys guarded: "
      f"{', '.join(sorted(size_keys)[:4])}, ...)")

unknown = T.merge(base, OrderedDict([("tile.bordr.size", ["7"])]))
warns = [f for f in T.validate(unknown, base) if f.severity == "warning"]
check(any(f.key == "tile.bordr.size" for f in warns), "typo'd key warns")

both = T.merge(base, OrderedDict([("tile.current", ["1", "1", "1"])]))
colors_base = T.load_file(install / T.TOUCHCOLORS, T.TOUCHCOLORS)
# No shipped default.* key has a specific twin, so the collision has to be
# built synthetically to exercise the rule.
synth = T.merge(colors_base,
                OrderedDict([("default.tile.error", colors_base.get("tile.error"))]))
tiered = T.merge(synth, OrderedDict([("tile.error", ["1", "0", "0"])]))
tier_warns = [f for f in T.validate(tiered, synth)
              if f.severity == "warning" and f.key == "default.tile.error"]
check(bool(tier_warns), "setting X alongside default.X warns about precedence")

# Setting only the default tier must be silent.
only_default = T.merge(colors_base, OrderedDict([("default.tile.error", ["1", "0", "0"])]))
del only_default.data["tile.error"]
check(not [f for f in T.validate(only_default, only_default)
           if f.key == "default.tile.error"],
      "the warning needs both tiers set, not just the default tier")
dflt = [k for k in colors_base.keys() if k.startswith("default.")]
twins = [k for k in dflt if k[len("default."):] in colors_base]
check(not twins,
      f"real: none of the {len(dflt)} shipped default.* keys has a specific "
      f"twin, so the collision rule is a safety net rather than routine")

# Colour channels must never be clamped.
over = T.merge(colors_base, OrderedDict([("POP.hilite", ["0.56", "0.6", "9.9"])]))
check(T.diff(colors_base, over)["POP.hilite"] == ["0.56", "0.6", "9.9"],
      "a channel above 1.0 is stored verbatim, never clamped")

# ---------------------------------------------------------------- capture

print()
print("Capture")
print("-" * 60)

written = T.capture()
check(all(p.exists() for p in written.values()), "capture writes a baseline")
check(T._version_path().exists(), "capture writes version.json")
check(T.baseline_version().get("td_build") == T.td_version(),
      "capture records the live TouchDesigner build")
raises(T.ThemeError, lambda: T.capture(), "capture refuses to clobber an existing baseline")

try:
    T.capture()
except T.ThemeError as exc:
    check("--force" in str(exc), "clobber refusal points at --force")

check((T.baseline_dir / T.TOUCHCOLORS).read_bytes()
      == (install / T.TOUCHCOLORS).read_bytes(),
      "baseline bytes match the install exactly")

# ---------------------------------------------------------------- export

print()
print("Export")
print("-" * 60)

exported = T.export("empty")
for store, path in exported.items():
    overlay = T.load_overlay(path.read_text(), path.name)
    check(overlay == OrderedDict(), f"export of an unmodified install is empty ({store})")
raises(T.ThemeError, lambda: T.export("empty"), "export refuses to clobber an existing theme")

# Use sentinel values that cannot collide with whatever the real install
# currently holds, so the test does not depend on the install being pristine.
SENTINEL_A = ["0.25", "0.5", "0.75"]
SENTINEL_B = ["0.1", "0.2", "0.3"]
(install / T.TOUCHCOLORS).write_bytes(
    T.merge(colors_base, OrderedDict([
        ("tile.connection.hilite1", SENTINEL_A),
        ("default.tile.line", SENTINEL_B),
    ])).to_bytes()
)
exported = T.export("probe")
sparse = T.load_overlay(T.theme_path("probe", T.TOUCHCOLORS).read_text())
check(set(sparse) == {"tile.connection.hilite1", "default.tile.line"},
      "export records only the changed keys")
check(sparse["tile.connection.hilite1"] == SENTINEL_A,
      "export records the new value verbatim")
check(T.theme_path("probe", T.TOUCHOPTIONS).exists(),
      "export writes a file for both stores even when one is unchanged")

# ---------------------------------------------------------------- apply

print()
print("Apply")
print("-" * 60)

# Applying a theme that was exported from the current install would be a
# no-op, so move the install somewhere else first. That makes the backup
# assertion meaningful.
THIRD_VALUE = ["0.9", "0.8", "0.7"]
(install / T.TOUCHCOLORS).write_bytes(
    T.merge(colors_base, OrderedDict([("tile.connection.hilite1", THIRD_VALUE)])).to_bytes()
)

result = T.apply("probe")
check((install / T.TOUCHCOLORS).read_bytes() == T.merge(
    colors_base, sparse).to_bytes(), "apply writes the merged file")
check(result["backup"].exists(), "apply creates a backup")
check((result["backup"] / T.TOUCHCOLORS).exists(), "backup contains TouchColors")
check((result["backup"] / T.TOUCHCOLORS).read_bytes()
      != (install / T.TOUCHCOLORS).read_bytes(),
      "backup holds the pre-apply bytes, not the post-apply ones")
check(T.load_file(result["backup"] / T.TOUCHCOLORS).get("tile.connection.hilite1")
      == THIRD_VALUE,
      "backup captures exactly the pre-apply state")

# Re-applying must be idempotent.
before = (install / T.TOUCHCOLORS).read_bytes()
T.apply("probe")
check((install / T.TOUCHCOLORS).read_bytes() == before, "apply is idempotent")

# A theme with a bad size must not reach the install.
(install / T.TOUCHOPTIONS).write_bytes(base.to_bytes())
T.themes_dir.mkdir(parents=True, exist_ok=True)
bad_dir = T.themes_dir / "broken"
bad_dir.mkdir(parents=True, exist_ok=True)
(bad_dir / f"{T.TOUCHOPTIONS}.yaml").write_text('tile.inout.origsize: ["0"]\n')
ex = raises(T.ValidationError, lambda: T.apply("broken"),
            "apply refuses a theme that fails validation")
check((install / T.TOUCHOPTIONS).read_bytes() == base.to_bytes(),
      "the rejected apply left the install untouched")
check(T.apply("broken", force=True)["theme"] == "broken",
      "--force writes anyway when explicitly asked")

# Unknown theme name.
raises(T.ThemeNotFound, lambda: T.apply("nope"), "applying a missing theme is a clear error")
try:
    T.apply("nope")
except T.ThemeNotFound as exc:
    check("probe" in str(exc), "missing-theme error lists available themes")

# ---------------------------------------------------------------- round trip through apply

print()
print("End to end")
print("-" * 60)

(install / T.TOUCHCOLORS).write_bytes(colors_base.to_bytes())
(install / T.TOUCHOPTIONS).write_bytes(base.to_bytes())
check(T.status().clean, "a freshly captured install reports clean")

edited = T.merge(colors_base, OrderedDict([("dat.table.select.outline", ["0", "1", "0"])]))
(install / T.TOUCHCOLORS).write_bytes(edited.to_bytes())
state = T.status()
check(not state.clean, "status detects drift after an external edit")
check(state.drift[T.TOUCHCOLORS] == 1, "status counts the drifted key")

T.export("e2e", force=True)
T.apply("e2e")
check((install / T.TOUCHCOLORS).read_bytes() == edited.to_bytes(),
      "export then apply reproduces the edited file byte-for-byte")

# "Clean" means "matches baseline", so a theme that genuinely changes
# something should still show drift. The applied-theme marker is what
# distinguishes an intended difference from a stray one.
final = T.status()
check(final.applied == "e2e", f"status records the last applied theme (got {final.applied})")
check(final.drift[T.TOUCHCOLORS] == 1,
      "status still reports the intended difference from baseline")
check(not [f for f in T.plan("e2e")["findings"]], "the e2e theme validates with no findings")

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
print("all library tests PASSED")
