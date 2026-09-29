"""Tests for merge / diff / validate / capture / apply / export.

Everything runs against a throwaway copy of the stores in a temp directory,
selected via the TDTHEME_CONFIG environment variable; the real TouchDesigner
config is only ever read. The copy is seeded from the pristine `baseline/` - see
the fixture below for why that matters.

Run:  python3 tests/test_tdtheme.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli as tdtheme_cli
import tdtheme as T


def overlay_text(data, name=""):
    """A sparse overlay, in the subset format `tdthememaker export` writes.

    Written out by hand on purpose: a theme is a human-editable artifact, so
    what is worth testing is that apply reads a file a person wrote, not that a
    writer and a reader agree with each other.
    """
    lines = ["# tdtheme sparse overlay - only keys that differ from baseline.",
             f"# file: {name}", ""]
    for key, value in data.items():
        if key:
            lines.append(f"{key}: [{', '.join(json.dumps(v) for v in value)}]")
    return "\n".join(lines) + "\n"

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

PROJECT = Path(__file__).resolve().parent.parent

# Seed the throwaway install from the *pristine baseline*, not the live config.
# Assertions below about what the vendor ships (`POP.hilite` above 1.0,
# `tile.inout.origsize` 10, no findings at all) failed whenever a theme happened
# to be applied, asserting the absence of a theme rather than the correctness of
# the code. The baseline is the pristine capture, tracked in git, and what every
# theme diffs against; it falls back to the live install only if it is missing.
SEED = PROJECT / "baseline"
if not all((SEED / store).exists() for store in T.STORE_FILES):
    SEED = T.TD_CONFIG

tmp = Path(tempfile.mkdtemp(prefix="tdtheme-test-"))
install = tmp / "install"
install.mkdir()
for store in T.STORE_FILES:
    shutil.copy2(SEED / store, install / store)

os.environ["TDTHEME_CONFIG"] = str(install)
# Patch every path root so no test artefact escapes into the real project dir
# (notably .applied.json, which lives under root rather than baseline_dir).
T.root = tmp
T.baseline_dir = tmp / "baseline"
# These two are resolved from `root` at import and are NOT derived from
# `baseline_dir`, so patching only the one leaves them pointing at the real
# project. `capture --local` writes through `baseline_shadow_dir`, so without
# this it wrote a 2.2 MB shadow into the checkout this test runs in.
T.baseline_shipped_dir = T.baseline_dir
T.baseline_shadow_dir = tmp / "baseline.local"
T.themes_dir = tmp / "themes"
T.backups_dir = tmp / "backups"

print("Fixture")
print("-" * 60)
check((install / T.TOUCHCOLORS).exists(), f"temp install populated at {install}")
check((install / T.TOUCHCOLORS).read_bytes() == (SEED / T.TOUCHCOLORS).read_bytes(),
      f"temp install seeded from the pristine baseline ({SEED.name})")

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
# The writer for this subset moved to tdthememaker with `export`; the reader
# stayed, because applying a theme needs it.
text = ("# tdtheme sparse overlay - only keys that differ from baseline.\n"
        "# file: TouchOptions\n"
        "\n"
        'tile.border.size: ["5"]\n'
        'font.default.face: [""]\n'
        'tile.connection.hilite1: ["1", "0", "0"]\n'
        'key.with"quote: ["a\\"b", "", "x y"]\n')
loaded = T.load_overlay(text, "TouchOptions")
check(loaded == sample, "overlay round-trips through loader")

# The same text through the zero-dependency path.
manual = T._load_overlay_fallback(text, "TouchOptions")
check(manual == sample, "overlay round-trips through the fallback loader")

# Whether PyYAML is importable is a property of the machine, not of this code.
# Asserting it outright made the suite fail on a correctly provisioned host, so
# it is only recorded: the fallback is exercised exactly when yaml is absent.
print(f"  note: PyYAML {'present' if T._have_yaml() else 'absent'}"
      f" - fallback loader {'NOT ' if T._have_yaml() else ''}exercised")

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

# --- the two loaders must not disagree, on real files ----------------------
#
# A theme installs the same bytes whichever interpreter runs the tool, so both
# loaders have to return the same data for the same file. They agree by
# construction on anything `json.loads` accepts, since JSON is a subset of YAML.
# They used to disagree on everything else: the fallback is `json.loads(rest)`
# and otherwise keeps the raw string, so a single-quoted scalar came back with
# its quotes still attached. Nothing compared the two, so the suite stayed green
# on both interpreters.
#
# Compared over the committed overlays rather than a literal, because the
# failure mode is "a file someone actually committed parses differently".
themes_dir = PROJECT / "themes"
overlays = sorted(p for p in themes_dir.rglob("Touch*.yaml"))
check(bool(overlays), f"found committed overlays to cross-check ({len(overlays)})")
disagree = []
for path in overlays:
    body = path.read_text(encoding="utf-8")
    try:
        via_fallback = T._load_overlay_fallback(body, path.name)
    except T.FileFormatError as exc:
        via_fallback = f"error: {exc}"
    try:
        via_yaml = T.load_overlay(body, path.name)
    except T.FileFormatError as exc:
        via_yaml = f"error: {exc}"
    if via_fallback != via_yaml:
        disagree.append(path.name)
check(not disagree,
      f"fallback and PyYAML agree on all {len(overlays)} committed overlays"
      + (f" (differ: {disagree})" if disagree else ""))

# Single quotes are the case that actually bit: `origsize: '11'` loaded as the
# three characters '11' under the fallback and as 11 under PyYAML, so the same
# theme installed differently on different machines. The fallback now refuses it
# and names the forms that mean the same thing in both loaders.
for _label, _text in (("scalar", "tile.inout.origsize: '11'\n"),
                      ("list", "worksheet.bg: ['0.1', '0.2']\n")):
    raises(T.FileFormatError,
          lambda t=_text: T._load_overlay_fallback(t, "x"),
          f"single-quoted {_label} is refused by the fallback loader")
    try:
        T._load_overlay_fallback(_text, "x")
    except T.FileFormatError as exc:
        check("double-quoted" in str(exc),
              f"the single-quoted {_label} error names the double-quoted form")

# A value that merely contains an apostrophe is not a quoted scalar, and
# refusing it would be over-blocking: `it's fine` is a perfectly good value.
check(T._load_overlay_fallback("tile.inout.label: it's fine\n", "x")
      == OrderedDict([("tile.inout.label", ["it's fine"])]),
      "an apostrophe inside a value is not mistaken for quoting")

# The rest of the class. Each of these is a YAML construct the restricted
# loader cannot replicate, and each used to be kept as literal text while PyYAML
# interpreted it - a silent disagreement, worse than the single-quote case
# because nothing looks wrong. A flow mapping installed as the string `{a: 1}`;
# an alias installed as `*x` where PyYAML resolved it to the anchored value.
for _label, _text in (("flow mapping", "k: {a: 1}\n"),
                      ("anchor", "a: &x 1\n"),
                      ("alias", "b: *x\n"),
                      ("tag", "k: !!str 1\n"),
                      ("block scalar", "k: |\n  a\n  b\n"),
                      ("reserved @", "k: @text\n"),
                      ("backtick", "k: `text\n"),
                      ("bare list items", "k: [a, b]\n")):
    raises(T.FileFormatError,
          lambda t=_text: T._load_overlay_fallback(t, "x"),
          f"{_label} is refused rather than kept as literal text")
    try:
        T._load_overlay_fallback(_text, "x")
    except T.FileFormatError as exc:
        # The refusal has to be actionable: it should name a form to write
        # instead, not just report that the input was rejected.
        check(('not general YAML' in str(exc)) or ('must be quoted' in str(exc)),
              f"the refusal for {_label} names a form to write instead")

# A leading '-' is not a YAML construct, so a negative number and a hyphenated
# word must both still load. This is the over-blocking case for the guard above.
for _label, _text in (("negative number", "k: -0.5\n"),
                      ("hyphenated word", "k: a-b\n")):
    try:
        T._load_overlay_fallback(_text, "x")
        check(True, f"{_label} still loads (the guard is not over-broad)")
    except T.FileFormatError as exc:
        check(False, f"{_label} still loads (the guard is not over-broad): {exc}")

# The documented forms must keep working, and identically, through both.
for _label, _text in (("bare", "tile.inout.origsize: 11\n"),
                      ("double-quoted", 'tile.inout.origsize: "11"\n'),
                      ("json list", 'worksheet.bg: ["0.1", "0.2", "0.3"]\n')):
    check(T._load_overlay_fallback(_text, "x") == T.load_overlay(_text, "x"),
          f"documented {_label} form loads identically in both loaders")

# A non-numeric size is an error, not a warning: this is the other half of the
# same bug. `apply` gates on severity "error" only, so as a warning the quoted
# size sailed through and was written into the install, and TouchDesigner then
# read an unparseable geometry.
_options_base = T.load_file(SEED / T.TOUCHOPTIONS, T.TOUCHOPTIONS)
_quoted = _options_base.copy()
_quoted.data["tile.inout.origsize"] = ["'11'"]
_quoted_findings = T.validate(_quoted, _options_base)
check(any(f.severity == "error" for f in _quoted_findings),
      "a non-numeric size is an error, so apply refuses it")
check(not [f for f in _quoted_findings
           if f.severity == "warning" and "not numeric" in f.message],
      "the non-numeric size is no longer reported as a mere warning")

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
# positives. An unconditional "size > 0" rule flags the shipped
# `font.relative.size 0`, which would make every apply fail.
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

# ------------------------------------------------- colour field integrity
#
# Regression cover for a real corruption: `worksheet.grid` was hand-edited to
# `0.317 0.189 <TAB> 0.15`, merging two channels into one space-separated
# field. parse() accepted it, serialize() reproduced it, export() captured it
# and validate() reported nothing - so the install ended up holding a file
# TouchDesigner cannot read.

print()
print("Colour field integrity")
print("-" * 60)

# The guard is stated against the pristine baseline, not the live install: the
# point is that a *shipped* file is clean. (T.baseline_dir / T.themes_dir are
# repointed at the tmpdir by the fixture, so the real project copies are reached
# via PROJECT.) Using the install here would make a future corruption read as a
# validation failure of this rule.
pristine_colors = T.load_file(PROJECT / "baseline" / T.TOUCHCOLORS, T.TOUCHCOLORS)
check(not T.validate(pristine_colors, pristine_colors),
      f"the pristine shipped TouchColors baseline produces no findings at all "
      f"({len(pristine_colors)} keys)")

arity_hist = {}
for value in pristine_colors.data.values():
    arity_hist[len(value)] = arity_hist.get(len(value), 0) + 1
print(f"         (field-count histogram {arity_hist} - the 4s are the two "
      f"dialog.commenthint* keys)")

# The exact corruption that shipped, reproduced from the fixed theme.
grid_corrupt = T.merge(pristine_colors,
                       OrderedDict([("worksheet.grid", ["0.317 0.189", "0.15"])]))
grid_errs = [f for f in T.validate(grid_corrupt, pristine_colors)
             if f.severity == "error"]
check(bool(grid_errs), "a space-merged colour field is caught as an error")
check(any(f.key == "worksheet.grid" for f in grid_errs),
      "the offending colour key is named in the error")
check(any("tab" in f.message for f in grid_errs),
      "the error names the cause (tab vs space), not just the rule")

# Right field count, wrong content: the space check is not merely a count check.
short = T.merge(pristine_colors, OrderedDict([("worksheet.bg", ["0.1", "0.1"])]))
check([f for f in T.validate(short, pristine_colors) if f.severity == "error"],
      "a colour with too few fields is caught")

nonnumeric = T.merge(pristine_colors, OrderedDict([("tile.bg", ["black", "0", "0"])]))
check([f for f in T.validate(nonnumeric, pristine_colors) if f.severity == "error"],
      "a non-numeric colour channel is caught")

# The two shipped keys with the stray empty leading field must stay valid, or
# every apply of an untouched theme would fail.
quirk_keys = [k for k, v in pristine_colors.data.items() if len(v) != 3]
for key in quirk_keys:
    kept = T.merge(pristine_colors, OrderedDict([(key, pristine_colors.get(key))]))
    check(not [f for f in T.validate(kept, pristine_colors)
               if f.severity == "error"],
          f"{key} keeps its 4-field shape without tripping the rule")

check(not [f for f in T.validate(over, colors_base) if f.severity == "error"],
      "an out-of-range channel is not mistaken for a malformed field")

# A well-formed edit must not warn. Uses the project themes, which are the
# artefacts this rule actually protects.
_sunset_path = PROJECT / "themes" / "sunset" / f"{T.TOUCHCOLORS}.yaml"
if _sunset_path.exists():
    _applied = T.merge(colors_base,
                       T.load_overlay(_sunset_path.read_text(), _sunset_path.name))
    check(not [f for f in T.validate(_applied, colors_base)
               if f.severity == "error"],
          "the shipped sunset theme applies with no field-integrity error")

# ------------------------------------------------------- monochrome themes
#
# `mono` and `bnw` are two passes at the same brief, so the claims they share
# are asserted once and each theme's own claims follow. The design claims live
# in prose in each theme's header; these tests exist so a future edit that
# breaks one is called out.

print()
print("Monochrome themes (mono, bnw)")
print("-" * 60)


def _srgb_lum(rgb):
    def c(v):
        v = float(v)
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (c(x) for x in rgb[-3:])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(fg, bg):
    la, lb = _srgb_lum(fg), _srgb_lum(bg)
    if la < lb:
        la, lb = lb, la
    return (la + 0.05) / (lb + 0.05)


def _desat_lum(rgb):
    """The greyscale the themes are actually built from: a weighted sum of the
    raw sRGB channels, with no linearisation. Distinct from `_srgb_lum`, the
    WCAG relative luminance used for contrast - the two disagree for anything
    not already grey, and a key on plain luminance has to be compared against
    this one."""
    r, g, b = (float(x) for x in rgb[-3:])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _param_draws(theme):
    """Every (fg, bg) pair the parameter dialog can paint.

    A widget is drawn as a pair, so a family with `bg[SUFF]` but no `fg[SUFF]`
    falls back to the bare `fg`; that combination is invisible to a per-suffix
    check and is included here.
    """
    fams = {}
    for k in theme.keys():
        m = re.match(r"(parms\.[\w.]*?)\.(fg|bg)((?:\.[\w]+)*)$", k)
        if m:
            fams.setdefault(m.group(1), set()).add((m.group(2), m.group(3)))
    draws = []
    for fam, states in fams.items():
        bgs = {s for r, s in states if r == "bg"}
        fgs = {s for r, s in states if r == "fg"}
        for s in bgs:
            bg = f"{fam}.bg{s}"
            if bg not in theme.data:
                continue
            if f"{fam}.fg{s}" in theme.data:
                draws.append((f"{fam}.fg{s}", bg))
            elif s and "" in fgs:
                draws.append((f"{fam}.fg", bg))
    return draws


_EXPECTED_COLOUR = {"ramp.red", "ramp.green", "ramp.blue", "ramp.saturation"}

# `bnw` additionally declines to restyle interface text, so it inherits eight
# keys that ship with a hue, each a status or marker glyph where the colour is
# the message: green tick, red cross, red bypass, yellow notch. Listed rather
# than derived, because the claim is that this set has not grown.
_SHIPPED_TEXT_COLOUR = {
    "circle.check", "circle.minus", "knob.notch", "tile.flag.bypass.cross",
    "default.label", "geodetail.fg", "swatch.none.text", "tooltip.fg.value",
}
_STATE_SUFFIXES = (".loc", ".sel", ".selected", ".off", ".on",
                   ".disabled", ".editing", ".editable")
_TYPES = ["CHOP", "COMP", "DAT", "MAT", "POP", "SOP", "TOP"]

loaded = {}
for _name in ("mono", "bnw"):
    _path = PROJECT / "themes" / _name / f"{T.TOUCHCOLORS}.yaml"
    if not _path.exists():
        print(f"  skip  themes/{_name} not present")
        continue
    loaded[_name] = (T.load_overlay(_path.read_text(), _path.name),
                     T.merge(pristine_colors,
                             T.load_overlay(_path.read_text(), _path.name)))

for _name, (_overlay, _theme) in loaded.items():

    def _appearance(key, _theme=_theme):
        """What a widget actually paints for `key`: its own tone, plus the
        tone of the fg/bg sibling it is drawn against, if there is one.

        The sibling lookup has to fall back. `parms.field.string.bg.selected`
        is drawn with `parms.field.string.fg`, not with a missing
        `parms.field.string.fg.selected`, so a helper that gives up when the
        suffixed partner is absent compares a pair against a bare value and
        calls a collapsed field healthy.
        """
        own = _theme.get(key)[-1]
        for _role, _other in (("fg", "bg"), ("bg", "fg")):
            _marker = "." + _role
            _idx = key.rfind(_marker)
            if _idx == -1:
                continue
            # "parms.label.fg.loc" -> stem "parms.label.fg", rest ".loc",
            # so the partner is "parms.label.bg.loc", then "parms.label.bg"
            _stem = key[:_idx + len(_marker)]
            _rest = key[_idx + len(_marker):]
            for _partner in (_stem[:-len(_role)] + _other + _rest, _stem[:-len(_role)] + _other):
                if _partner in _theme.data:
                    return (own, _theme.get(_partner)[-1])
            return (own,)
        return (own,)

    print(f"\n{_name} ({len(_overlay)} keys)")

    # Shared claim 1: it applies, and it desaturates everything except the
    # four ramp keys. Those identify a hue channel in the Ramp node's picker;
    # a gray "red ramp" is indistinguishable from a gray "green ramp".
    _findings = T.validate(_theme, pristine_colors)
    check(not [f for f in _findings if f.severity == "error"],
          f"{_name} applies with no error")
    _coloured = {k for k in _theme.keys()
                 if k and len(set(_theme.get(k)[-3:])) != 1}
    _want = set(_EXPECTED_COLOUR)
    if _name == "bnw":
        _want |= _SHIPPED_TEXT_COLOUR
    check(_coloured == _want,
          f"{_name} is gray apart from {len(_want)} documented keys "
          f"(found {sorted(_coloured)})")

    # The exemption above is only defensible because every coloured key it
    # allows is a glyph or a text colour; a coloured *fill* is a different claim
    # entirely - a tinted panel - and not what the header says this theme does.
    check(not [k for k in _coloured if k.endswith(".bg")],
          f"{_name} keeps hue out of every fill "
          f"(coloured fills: {sorted(k for k in _coloured if k.endswith('.bg'))})")

    # Shared claim 2: a state sibling must not become *invisible*, a weaker and
    # more honest requirement than "must not share a tone". A widget is drawn as
    # a (fg, bg) pair, so `parms.button.bg` collapsing into
    # `parms.button.bg.disabled` costs nothing as long as `.fg.disabled`
    # differs; comparing single channels penalises that case and misses the real
    # one, where a bg collapses and the fg collapses with it.
    #
    # Pairs the vendor ships identical (`parms.button.border` and
    # `.border.disabled` are both 0.2) are their choice, not a collapse this
    # theme introduced, so they are excluded.
    _invisible = []
    _pairs = 0
    for _key in _theme.keys():
        for _suffix in _STATE_SUFFIXES:
            if _key.endswith(_suffix):
                _base = _key[:-len(_suffix)]
                if _base in _theme.data:
                    _a = pristine_colors.get(_key)[-1]
                    _b = pristine_colors.get(_base)[-1]
                    if abs(float(_a) - float(_b)) < 0.01:
                        break           # vendor ships these two the same
                    _pairs += 1
                    if _appearance(_key) == _appearance(_base):
                        _invisible.append((_key, _base))
                break
    check(_pairs >= 50, f"found {_pairs} vendor-distinguished state-sibling pairs")
    check(not _invisible,
          f"{_name} keeps all {_pairs} of them visibly distinct "
          f"(invisible: {_invisible[:3]})")

    # Shared claim 3: the seven node base colours have to stay far enough
    # apart to survive the OP.hi.val 1.7 multiplier that derives every node
    # highlight, and they have to move together or the derived shades desync.
    _tones = sorted(float(_theme.get(_t)[-1]) for _t in _TYPES)
    _gaps = [b - a for a, b in zip(_tones, _tones[1:])]
    check(min(_gaps) >= 0.05,
          f"the 7 node base colours are evenly spread (smallest gap "
          f"{min(_gaps):.2f}, tones {[_tones[0], _tones[-1]]})")
    check(all(len(set(_theme.get(_t)[-3:])) == 1 for _t in _TYPES),
          "all 7 node base colours moved together, as section 5.4 requires")

    # Shared claim 4: the field arity of the two quirky keys survives a merge.
    for _key in ("dialog.commenthint", "dialog.commenthint.comp"):
        check(len(_theme.get(_key)) == 4,
              f"{_key} keeps its 4 fields through the {_name} merge")

    # --- claims specific to this theme ----------------------------------
    _distinct = len({_theme.get(_k)[-1] for _k in _theme.keys() if _k})
    _at_end = sum(1 for _k in _theme.keys()
                  if _k and _theme.get(_k)[-1] in ("0", "1"))
    _share = _at_end / len([k for k in _theme.keys() if k])

    if _name == "mono":
        # Claim: this is a scale, not a palette. It keeps each key's shipped
        # brightness, so it is supposed to need many tones. The guard is
        # two-sided - if this number drops, the theme has quietly become bnw,
        # and if it rises, the ordering guarantee below is what broke.
        check(_distinct >= 40,
              f"mono stays a luminance scale, not a palette "
              f"({_distinct} distinct tones)")

        # Claim: among the keys that take plain luminance, the vendor's
        # light/dark ordering is preserved. This is the theme's whole
        # justification, so it is checked directly rather than inferred from
        # the tone count. Hand-authored keys are excluded - overriding the
        # ordering is the point of overriding.
        _lum = {k: _srgb_lum(_theme.get(k)) for k in _theme.keys() if k}
        _ship = {k: _srgb_lum(pristine_colors.get(k)) for k in _lum}
        _derived = sorted(k for k in _lum
                          if abs(_lum[k] - _ship[k]) <= 0.01)
        # Inverted means meaningfully brighter in the shipped file and
        # meaningfully darker here. Without a tolerance this measures rounding:
        # luminance is stored to two decimals, so keys 0.004 apart can land
        # either way round.
        _tol = 0.02
        _order = sorted(_derived, key=lambda k: -_ship[k])
        _flips = []
        _best_after = None
        for _k in reversed(_order):
            if _best_after is not None and _best_after > _lum[_k] + _tol:
                _flips.append(_k)
            _best_after = max(_best_after, _lum[_k]) if _best_after is not None else _lum[_k]
        check(not _flips,
              f"mono preserves the shipped light/dark ordering on all "
              f"{len(_derived)} luminance-derived keys (flips: {_flips[:2]})")

    if _name == "bnw":
        # The parameter window is deliberately exempt from the four-tone palette
        # (see its header), so these claims are about the rest of the interface:
        # scoring the whole file would let 100 luminance keys quietly prove a
        # claim about a screen they are not on. Interface text is exempt too,
        # for a stronger reason - those keys are not this theme's palette at
        # all but whatever TouchDesigner ships - so the claim is made over the
        # keys the overlay actually authors.
        _ov = [k for k in loaded[_name][0].keys()
               if k and not k.startswith("parms.")]
        _ov_tones = {round(float(_theme.get(k)[-1]), 4) for k in _ov}
        _out = [k for k in _theme.keys() if k and not k.startswith("parms.")]
        _out_end = sum(1 for k in _out
                       if round(float(_theme.get(k)[-1]), 4) in (0.0, 1.0))
        check(len(_ov_tones) <= 24,
              f"bnw keeps a bounded palette outside the parameter window "
              f"({len(_ov_tones)} distinct tones over the {len(_ov)} "
              f"keys it authors)")
        check(_out_end / len(_out) > 0.4,
              f"most non-parameter keys are pure black or white "
              f"({_out_end / len(_out):.0%})")

        # Claim: the graph grid is structure, not text. The role table maps
        # `.axes` to PAPER and the cliff sends `.axes.main` to INK, so on a 0.0
        # graph background that is a white minor grid and an invisible main
        # axis. Neither is guarded by the palette checks above, both being legal
        # four-tone values; only their relationship is wrong.
        _grid = {k: round(float(_theme.get(k)[-1]), 4) for k in
                 ("graph.grid.axes", "graph.grid.axes.main")}
        check(_grid["graph.grid.axes"] < 1.0,
              f"the CHOP graph's minor grid is not PAPER "
              f"({_grid['graph.grid.axes']})")
        check(_grid["graph.grid.axes.main"] > _grid["graph.grid.axes"],
              f"the main axis reads stronger than the minor grid, and both "
              f"stay off the endpoints ({_grid})")

        # Claim: the text exemption never costs legibility. Leaving text alone
        # is only safe while the fill under it is also left alone; where this
        # theme inverts a fill, the shipped text colour can land on top of it.
        # This is the check that found the three keys in TEXT_CONTRAST_RESCUE.
        #
        # The bar is "readable", not "unchanged": this theme inverts fills on
        # purpose, so a pair may get worse and still be fine - `tile.name.fg` on
        # the LINE tile is 4.65:1, down from 6.89:1. Being both worse than
        # shipped *and* under 4.5:1 is what is not allowed, and that is how the
        # three rescued keys were found.
        _drawn = []
        for _k in _theme.keys():
            if not _k or not _k.endswith(".fg"):
                continue
            _fam = _k[:-3]
            for _cand in (f"{_fam}.bg", f"{_k}.bg"):
                if _cand in _theme.data:
                    _drawn.append((_k, _cand))
                    break
        _hurt = []
        for _f, _b in _drawn:
            _now = _contrast(_theme.get(_f), _theme.get(_b))
            _was = _contrast(pristine_colors.get(_f), pristine_colors.get(_b))
            if _now < _was - 0.5 and _now < 4.5:
                _hurt.append((round(_now - _was, 2), _f, _b))
        check(not _hurt,
              f"no text/fill pair is both worse than shipped and under 4.5:1 "
              f"({len(_drawn)} pairs checked, offenders: {sorted(_hurt)[:2]})")

        # Claim: the parameter window is honest to the shipped luminance. This
        # is the decision that made the window readable, so it is checked
        # against the baseline rather than trusted - including the exact list
        # of keys allowed to deviate, where a hand-set value would creep in.
        _HAND = {
            "parms.bind.fg", "parms.bind.bg.enabled",
            "parms.err.bg", "parms.err.fg",
            "parms.disabled.err.bg", "parms.disabled.err.fg",
            "parms.expr.bg.selected", "parms.expr.bg.on.selected",
            "parms.expr.bg.off.selected",
        }
        _off = []
        for _k in _theme.keys():
            if not _k or not _k.startswith("parms.") or _k in _HAND:
                continue
            # 0.01 rather than 0.005: luminance is stored to two decimals, so a
            # key built from it is always that far from the true value, while a
            # genuine hand-set key lands much further away.
            _want = _desat_lum(pristine_colors.get(_k))
            if abs(float(_theme.get(_k)[-1]) - _want) > 0.01:
                _off.append((_k, _theme.get(_k)[-1], round(_want, 3)))
        check(not _off,
              f"the parameter window is plain shipped luminance on every key "
              f"except the {len(_HAND)} named ones (offenders: {_off[:2]})")

        # Claim: nothing in the window is invisible, and the window is not worse
        # than what TouchDesigner ships. Luminance alone gives neither - the
        # shipped file contains pairs that are 1.0:1, and desaturating can lose
        # ground where the shipped difference was carried by hue. 0.5 ratio
        # points is above this theme's worst rounding drift (0.25) and far below
        # a real regression.
        _draws = _param_draws(_theme)
        _ratios = [(_contrast(_theme.get(f), _theme.get(b)), f, b)
                   for f, b in _draws]
        _blind = [(r, f, b) for r, f, b in _ratios if r < 1.05]
        check(not _blind,
              f"no parameter-dialog pair is invisible; the worst is "
              f"{min(r for r, _, _ in _ratios):.2f}:1 (blind: {_blind[:2]})")
        _lost = [(cr - _contrast(pristine_colors.get(f),
                                 pristine_colors.get(b)), f, b)
                 for (cr, f, b) in _ratios
                 if cr < _contrast(pristine_colors.get(f),
                                   pristine_colors.get(b)) - 0.5]
        _max_loss = max(
            [_contrast(pristine_colors.get(f), pristine_colors.get(b)) - cr
             for cr, f, b in _ratios] + [0.0])
        check(not _lost,
              f"the parameter window is never meaningfully worse than shipped "
              f"(worst loss {_max_loss:.2f} ratio points, "
              f"regressions: {_lost[:2]})")

# The two themes must actually differ, or one of them is redundant. The
# interesting part is *where*: `bnw` puts the parameter window on plain
# luminance, so from here that window is largely shared with `mono` by design
# and the themes are distinguished by the rest of the interface. Asserting
# "they differ on most of the file" alone would let the parameter window drift
# back to the four-tone palette unnoticed, so both halves are stated.
if len(loaded) == 2:
    # [1] is the merged theme; [0] is the overlay, which only holds the keys
    # the theme bothers to change.
    _a, _b = loaded["mono"][1], loaded["bnw"][1]
    _out = [k for k in _b.keys() if k and not k.startswith("parms.")]
    _par = [k for k in _b.keys() if k and k.startswith("parms.")]
    _diff = lambda k: abs(float(_a.get(k)[-1]) - float(_b.get(k)[-1])) > 0.005
    _out_diff = [k for k in _out if _diff(k)]
    _par_same = [k for k in _par if not _diff(k)]
    check(len(_out_diff) / len(_out) > 0.7,
          f"mono and bnw differ on most of the interface "
          f"({len(_out_diff)}/{len(_out)} non-parameter keys)")
    check(len(_par_same) / len(_par) > 0.5,
          f"while sharing the parameter window, which bnw keeps on luminance "
          f"({len(_par_same)}/{len(_par)} parms keys agree)")

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

# `capture --force` against the *committed* baseline is a change every other
# user of the checkout inherits, and `git add -A` is the accident that commits
# 2.2 MB of one machine's build. So `--force` alone is not the whole
# acknowledgement: it only says "overwrite something", which is equally true of
# the private shadow, so it cannot distinguish the two destinations.
#
# This was broken: the refusal named `--force` as the way forward while refusing
# `--force`, so the documented escape did not exist. Both halves are pinned.
_shared = T.baseline_shipped_dir
_unchanged = {p: p.read_bytes() for p in sorted(_shared.rglob("*")) if p.is_file()}
with contextlib.redirect_stderr(io.StringIO()) as refusal:
    code = tdtheme_cli.main(["capture", "--force"])
check(code == tdtheme_cli.EXIT_ERROR, f"capture --force alone is refused ({code})")
check("--local" in refusal.getvalue() and "baseline.local" in refusal.getvalue(),
      "and the refusal names the private path, which needs no acknowledgement")
check("--i-know-this-is-shared" in refusal.getvalue(),
      "and names the explicit acknowledgement, not the flag that was just refused")
check(all(p.read_bytes() == b for p, b in _unchanged.items()),
      "and the committed baseline is byte-identical afterwards")

with contextlib.redirect_stdout(io.StringIO()):
    code = tdtheme_cli.main(["capture", "--force", "--i-know-this-is-shared"])
check(code == 0, f"capture --force --i-know-this-is-shared is how you say it "
      f"deliberately ({code})")
T.capture(force=True)   # put the committed fixture back for the checks below

check((T.baseline_dir / T.TOUCHCOLORS).read_bytes()
      == (install / T.TOUCHCOLORS).read_bytes(),
      "baseline bytes match the install exactly")

# ---------------------------------------------------------------- apply

print()
print("Apply")
print("-" * 60)

# The theme to apply: `export` used to build this one and now lives in
# tdthememaker, so it is hand-written here - which is what a theme usually is.
sparse = OrderedDict([
    ("tile.connection.hilite1", ["0.25", "0.5", "0.75"]),
    ("default.tile.line", ["0.1", "0.2", "0.3"]),
])
T.themes_dir.mkdir(parents=True, exist_ok=True)
probe_dir = T.themes_dir / "probe"
probe_dir.mkdir(parents=True, exist_ok=True)
for store in T.STORE_FILES:
    T.write_file(T.theme_path("probe", store),
                 overlay_text(sparse if store == T.TOUCHCOLORS else OrderedDict(),
                              store).encode())

# Applying a theme exported from the current install would be a no-op, so move
# the install somewhere else first - that also makes the backup assertion
# meaningful.
THIRD_VALUE = ["0.9", "0.8", "0.7"]
(install / T.TOUCHCOLORS).write_bytes(
    T.merge(colors_base, OrderedDict([("tile.connection.hilite1", THIRD_VALUE)])).to_bytes()
)

result = T.apply("probe", backup=True)
check((install / T.TOUCHCOLORS).read_bytes() == T.merge(
    colors_base, sparse).to_bytes(), "apply writes the merged file")
check(result["backup"].exists(), "apply creates a backup when asked")
check((result["backup"] / T.TOUCHCOLORS).exists(), "backup contains TouchColors")
check((result["backup"] / T.TOUCHCOLORS).read_bytes()
      != (install / T.TOUCHCOLORS).read_bytes(),
      "backup holds the pre-apply bytes, not the post-apply ones")
check(T.load_file(result["backup"] / T.TOUCHCOLORS).get("tile.connection.hilite1")
      == THIRD_VALUE,
      "backup captures exactly the pre-apply state")

# The default, the opposite of the above and just as load-bearing. Backups are
# opt-in because the install is reconstructible from git, so an ordinary apply
# must touch nothing under backups/ - and must say so, since a line that went
# missing entirely would read as "none was needed".
sets_before = set(T.backups_dir.glob("*"))
plain = T.apply("probe")
check(plain["backup"] is None, "apply reports no backup without --backup")
check(set(T.backups_dir.glob("*")) == sets_before,
      "apply without --backup adds nothing under backups/")
check(plain["icons"]["backed_up"] == 0,
      "and backs up no icons either, not just no stores")

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
raises(T.ValidationError, lambda: T.apply("broken"),
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

# Writing a theme from a modified install moved to tdthememaker (`export`); what
# remains is the half this tool owns: apply a theme, and land it exactly.
(T.themes_dir / "e2e").mkdir(parents=True, exist_ok=True)
for store in T.STORE_FILES:
    sparse = T.diff(T.load_baseline()[store], T.load_file(install / store, store))
    T.write_file(T.theme_path("e2e", store),
                 overlay_text(sparse, store).encode())
T.apply("e2e")
check((install / T.TOUCHCOLORS).read_bytes() == edited.to_bytes(),
      "apply reproduces the edited file byte-for-byte")

# "Clean" means "matches baseline", so a theme that genuinely changes
# something should still show drift. The applied-theme marker is what
# distinguishes an intended difference from a stray one.
final = T.status()
check(final.applied == "e2e", f"status records the last applied theme (got {final.applied})")
check(final.drift[T.TOUCHCOLORS] == 1,
      "status still reports the intended difference from baseline")
check(not [f for f in T.plan("e2e")["findings"]], "the e2e theme validates with no findings")

# --------------------------------------------------------------------- reset
#
# `reset` is an alias for `apply default`, so what needs testing is the alias:
# that it is wired up, that it names `default`, and that from the same starting
# state it lands the same bytes as the long form. Re-testing `apply default` on
# its own would be testing the wrong thing.
#
# The icons are the reason this section exists at all. `default` is empty
# overlays plus a verbatim copy of the baseline icons, so most of what a reset
# *does* is put the icons back - and this suite builds no icon directory, so
# `icons_available()` is False for every check above and the icon half of apply
# has never run in any test. A reset that quietly skipped the icons would leave
# the install looking themed, and would have passed every check in this file.
# (`contextlib`, `io` and `cli` are imported at the top now: the capture checks
# above need them, and an import placed mid-file for the benefit of only the
# later checks is a trap for whoever adds a check earlier in the file.)

# The real shipped reset theme, not a hand-rolled stand-in: `themes_dir` points
# at tmp, and the point is that the theme that ships is the one that resets.
shutil.copytree(PROJECT / "themes" / "default", T.themes_dir / "default")
# A baseline icon set, so the "diff against baseline" optimisation inside
# copy_icons has something to compare against.
shutil.copytree(PROJECT / "baseline" / "Icons", T.baseline_dir / "Icons")
shutil.copytree(T.baseline_dir / "Icons", install / "Icons")
check(T.icons_available(),
      "the icon half of apply is reachable in this suite, not silently skipped")

BASELINE_ICON_COUNT = 97
baseline_icons = T.baseline_dir / "Icons"
install_icons = install / "Icons"
DRIFTED_ICONS = ("ActivateOn.tiff", "BookmarkButton.tiff")


def make_dirty() -> None:
    """Put the install into a themed state: two icons and one colour off-baseline."""
    (install / T.TOUCHCOLORS).write_bytes(
        T.merge(colors_base, OrderedDict([("tile.connection.hilite1", THIRD_VALUE)])).to_bytes()
    )
    for name in DRIFTED_ICONS:
        (install_icons / name).write_bytes(b"not the stock icon")


def installed_state() -> dict:
    """Everything a reset is supposed to control, as comparable bytes."""
    state = {store: (install / store).read_bytes() for store in T.STORE_FILES}
    state["icons"] = {p.name: p.read_bytes() for p in sorted(install_icons.glob("*.tiff"))}
    return state


STOCK = {store: (T.baseline_dir / store).read_bytes() for store in T.STORE_FILES}
STOCK["icons"] = {p.name: p.read_bytes() for p in sorted(baseline_icons.glob("*.tiff"))}

# The long form first, from a known-dirty state, so the alias has something to
# be compared against rather than being checked against its own output.
make_dirty()
T.apply("default")
long_form = installed_state()
check(long_form == STOCK, "apply default returns the install to the baseline")

# Now the same starting state through the alias, via the real argparse entry
# point, so the wiring is exercised and not just the function body.
make_dirty()
with contextlib.redirect_stdout(io.StringIO()) as alias_output:
    alias_code = tdtheme_cli.main(["reset"])
aliased = installed_state()
check(alias_code == 0, f"reset exits 0 (got {alias_code})")
check(aliased == long_form, "reset and 'apply default' produce identical bytes")
check(alias_output.getvalue().count("'default'") == 1,
      "reset reports the theme it applied, so the alias is not hiding its target")

check(T.status().applied == "default",
      f"reset records 'default' as the applied theme (got {T.status().applied})")

# `only_changed_against` means untouched icons are not rewritten, so the counts
# pin the mechanism from both sides: a reset from a dirty state rewrites exactly
# the two drifted icons, and the next has nothing to do. (The alias reset above
# already left the install at the baseline, so this has to re-dirty it - or both
# runs below measure an install with no work to do and the first check passes
# for the wrong reason.)
make_dirty()
first_reset = T.apply("default")
check(sorted(first_reset["icons"]["written"]) == sorted(DRIFTED_ICONS)
      and len(first_reset["icons"]["unchanged"]) == BASELINE_ICON_COUNT - len(DRIFTED_ICONS),
      f"the first reset rewrites only the drifted icons "
      f"({sorted(first_reset['icons']['written'])})")
second_reset = T.apply("default")
check(not second_reset["icons"]["written"]
      and len(second_reset["icons"]["unchanged"]) == BASELINE_ICON_COUNT,
      f"a second reset rewrites nothing "
      f"({len(second_reset['icons']['written'])} written)")

# `reset` is the command people reach for when something has already gone wrong,
# so --backup has to reach it too - and since it is an alias, what is really
# being checked is that cmd_reset forwards the flag rather than dropping it.
make_dirty()
result = T.apply("default", backup=True)
backups = sorted(T.backups_dir.glob("*/" + T.TOUCHCOLORS))
check(bool(backups) and backups[-1].read_bytes()
      == T.merge(colors_base, OrderedDict([("tile.connection.hilite1", THIRD_VALUE)])).to_bytes(),
      "reset backs up the pre-reset stores rather than overwriting them")

# Flags are forwarded to apply, which is the whole reason the alias holds the
# name instead of duplicating the command.
make_dirty()
with contextlib.redirect_stdout(io.StringIO()):
    code = tdtheme_cli.main(["reset", "--no-icons"])
check(code == 0 and (install_icons / DRIFTED_ICONS[0]).read_bytes() == b"not the stock icon",
      "reset --no-icons resets the stores and leaves the icons alone")
check((install / T.TOUCHCOLORS).read_bytes() == STOCK[T.TOUCHCOLORS],
      "reset --no-icons still returns the stores to the baseline")

# The output line, in both directions. Pinned as an *absence*, which is the
# part worth pinning: an earlier version printed "backup: none (...)" on every
# apply, on the argument that silence would read as "no backup was needed". That
# was a deliberate change of mind - `--backup` is documented in the command's own
# help, the recovery path is "re-apply", and `status` names the theme to
# re-apply - so a line on every run to say nothing happened is noise. Pin it so
# a well-meaning reader does not add it back, and so that revisiting the decision
# changes this check rather than inserting a quiet line.
make_dirty()
with contextlib.redirect_stdout(io.StringIO()) as no_backup_output:
    code = tdtheme_cli.main(["reset"])
check(code == 0, "reset without --backup succeeds")
# Matched on the label, not on the bare word: the icons summary says "N backed
# up" when there is something to say, and that is a different thing entirely.
check(not [ln for ln in no_backup_output.getvalue().splitlines()
           if "backup:" in ln.lower()],
      f"and prints no backup line at all ({no_backup_output.getvalue()!r})")

make_dirty()
with contextlib.redirect_stdout(io.StringIO()) as backup_output:
    code = tdtheme_cli.main(["reset", "--backup"])
reported = [ln for ln in backup_output.getvalue().splitlines() if "backup:" in ln]
check(code == 0 and reported and str(T.backups_dir) in reported[0],
      f"with --backup the output names the set it wrote ({reported})")
check("none" not in reported[0].lower(),
      "and names it as what it is, not as an absence")

# Put the install back to stock for the wrapper section that follows.
T.apply("default")
check(installed_state() == STOCK,
      "the install is back at the baseline before the change report below")

# ---------------------------------------------------------- what apply changed
#
# The per-store summary line, counted against the *installed* file rather than
# the theme's overlay, because the question the line answers is "what did this
# write do", not "what does the theme contain" - which is `tdtheme diff`.
#
# Synthetic themes rather than the shipped ones, because this section's
# themes_dir holds only `default` plus a few stand-ins, and copying two more
# full icon sets to test a line about TouchColors would be a poor trade.
T.apply("default")
recolours = T.themes_dir / "recolours"
recolours.mkdir(parents=True, exist_ok=True)
(recolours / f"{T.TOUCHCOLORS}.yaml").write_text(
    'tile.connection.hilite1: ["0.11", "0.22", "0.33"]\n'
    'worksheet.bg: ["0.44", "0.55", "0.66"]\n')
(recolours / f"{T.TOUCHOPTIONS}.yaml").write_text("CHOP.height: 77\n")

r_changes = T.apply("recolours")["changes"]
check(set(r_changes) == set(T.STORE_FILES),
      f"apply reports changes for both stores ({sorted(r_changes)})")
check(sorted(r_changes[T.TOUCHCOLORS]["changed"]) == ["tile.connection.hilite1",
                                                       "worksheet.bg"],
      f"and names the keys it actually set ({sorted(r_changes[T.TOUCHCOLORS]['changed'])})")
check(sorted(r_changes[T.TOUCHOPTIONS]["changed"]) == ["CHOP.height"],
      f"TouchOptions is reported the same way ({sorted(r_changes[T.TOUCHOPTIONS]['changed'])})")
check(not r_changes[T.TOUCHCOLORS]["removed"],
      f"and nothing is reported removed coming off the baseline ({r_changes[T.TOUCHCOLORS]['removed']})")
again = T.apply("recolours")["changes"]
check(not again[T.TOUCHCOLORS]["changed"] and not again[T.TOUCHOPTIONS]["changed"],
      f"re-applying the theme already installed reports nothing changed "
      f"({len(again[T.TOUCHCOLORS]['changed'])} keys)")

# The direction `diff` does not cover. An overlay may add a key the baseline
# lacks, and a later theme that does not carry it drops that key - the icon-fill
# leak in another guise, and silent if only `diff` were consulted, since the
# dropped key is absent from the file being compared.
addskey = T.themes_dir / "addskey"
addskey.mkdir(parents=True, exist_ok=True)
(addskey / f"{T.TOUCHCOLORS}.yaml").write_text('zzcustom.thing: ["1", "2", "3"]\n')
(addskey / f"{T.TOUCHOPTIONS}.yaml").write_text("")
T.apply("addskey", force=True)
check("zzcustom.thing" in T.load_file(install / T.TOUCHCOLORS),
      "a theme can add a key the baseline does not have")
dropped = T.apply("default")["changes"]
check(dropped[T.TOUCHCOLORS]["removed"] == ["zzcustom.thing"],
      f"and the next theme reports it as removed ({dropped[T.TOUCHCOLORS]['removed']})")
check("zzcustom.thing" not in T.load_file(install / T.TOUCHCOLORS),
      "so the removal is visible in the report and not only in the bytes")

# A count, not a list. The line names the store and how many lines moved and
# stops there: the largest shipped theme changes 460 keys, so naming them
# produces a line nobody reads, and `tdtheme diff` is already the listing.
# Both stores always get a line, so "unchanged" is the answer to "did this theme
# have an opinion there" rather than silence that reads as a bug.
with contextlib.redirect_stdout(io.StringIO()) as counted_output:
    code = tdtheme_cli.main(["apply", "recolours"])
printed = counted_output.getvalue()
store_lines = {store: [ln.strip() for ln in printed.splitlines() if store in ln]
               for store in T.STORE_FILES}
check(code == 0, "apply succeeds")
check(all(len(v) == 1 for v in store_lines.values()),
      f"and prints exactly one line per store ({printed!r})")
check((store_lines[T.TOUCHCOLORS] or [""])[0] == f"{T.TOUCHCOLORS}: 2 changed",
      f"TouchColors reports how many lines it changed "
      f"({(store_lines[T.TOUCHCOLORS] or [''])[0]!r})")
check((store_lines[T.TOUCHOPTIONS] or [""])[0] == f"{T.TOUCHOPTIONS}: 1 changed",
      f"and TouchOptions the same way ({(store_lines[T.TOUCHOPTIONS] or [''])[0]!r})")
check(not [k for k in ("tile.connection.hilite1", "worksheet.bg", "CHOP.height")
           if k in printed],
      f"but names no keys, only how many ({printed!r})")
# The count must come off the same pre-apply state the keys do, so re-applying the
# theme now installed is the case that would expose a comparison run afterwards.
with contextlib.redirect_stdout(io.StringIO()) as idempotent_output:
    tdtheme_cli.main(["apply", "recolours"])
idem = idempotent_output.getvalue()
reapply_lines = [ln.strip() for store in T.STORE_FILES
                 for ln in idem.splitlines() if store in ln]
check(len(reapply_lines) == len(T.STORE_FILES)
      and all("unchanged" in ln for ln in reapply_lines),
      f"a re-apply says unchanged for both rather than going quiet ({reapply_lines})")


# A store the install does not have cannot be compared against, and calling
# every one of its keys a change would be a lie about a file that did not exist.
(install / T.TOUCHOPTIONS).unlink()
check(T.TOUCHOPTIONS not in T.apply("default")["changes"],
      "a store missing from the install is absent from the report, not all-changed")
T.apply("default")
check(T.TOUCHOPTIONS in T.apply("default")["changes"],
      "and present again once the file exists")
T.apply("default")
check(installed_state() == STOCK,
      "and the install is back at the baseline, the store apply recreated "
      "included")


# ------------------------------------------------------------------- ui.tox
#
# The UI layout file, and the reason it needs a section of its own: the one
# artefact this tool installs that it cannot read. `ui.tox` is a `.tox` -
# TouchDesigner's own binary project format - so there is no parser, no diff,
# and no way to check that a write did what was wanted. apply copies the bytes
# and says which file they came from; that is the whole contract, and this suite
# deliberately asserts nothing about the contents.
#
# What is testable is *which* file gets written, because the one hazard here is
# silent rather than loud: a theme shipping no ui.tox, applied over the previous
# theme's, produces an install that looks themed to the wrong theme and nothing
# about it is wrong enough to notice. The fallback to `default` rules that out,
# and these checks are what hold it there.
print()
print("ui.tox")
print("-" * 60)

install_tox = install / T.SYSTEM_DIRNAME / T.UI_TOX
default_tox = T.theme_ui_tox(T.DEFAULT_THEME)

check(default_tox.is_file(),
      "the default theme ships a ui.tox - it is what the fallback resolves to")
check((PROJECT / "themes" / T.DEFAULT_THEME / T.UI_TOX).read_bytes()
      == (PROJECT / "baseline" / T.SYSTEM_DIRNAME / T.UI_TOX).read_bytes(),
      "default's ui.tox and the baseline copy are the same stock bytes")

# Two themes, and the difference between them is the thing under test. `uitest`
# holds a ui.tox and nothing else, so it is also the only way to see that
# `list_themes` counts a directory containing nothing but this file. `uibare` is
# an ordinary overlay theme shipping no ui.tox - the case the fallback exists for.
TOX_BYTES = b"\x00\x01tox-bytes-for-this-theme\x00"
own_dir = T.themes_dir / "uitest"
own_dir.mkdir(parents=True, exist_ok=True)
(own_dir / T.UI_TOX).write_bytes(TOX_BYTES)
bare_dir = T.themes_dir / "uibare"
bare_dir.mkdir(parents=True, exist_ok=True)
T.write_file(T.theme_path("uibare", T.TOUCHCOLORS),
             overlay_text(OrderedDict([("tile.connection.hilite1", THIRD_VALUE)]),
                          T.TOUCHCOLORS).encode())

check(T.ui_tox_source("uitest") == T.theme_ui_tox("uitest"),
      "a theme with its own ui.tox resolves to that file")
check(T.ui_tox_source("uibare") == default_tox,
      f"a theme without one falls back to {T.DEFAULT_THEME}'s")
check("uitest" in T.list_themes(),
      "a theme directory holding only a ui.tox is still a theme")

T.apply("uitest")
check(install_tox.read_bytes() == TOX_BYTES,
      "apply writes the theme's own ui.tox over the install's")

# The leak, stated directly: the install is holding `uitest`'s bytes right now,
# and applying a theme that has nothing to say about the UI must clear them.
T.apply("uibare")
check(install_tox.read_bytes() == default_tox.read_bytes(),
      "applying a theme with no ui.tox installs the stock one, not the previous "
      "theme's")

# --no-icons is about the icon set. A theme switch that quietly skipped the UI
# would leave the previous theme's dialog geometry on screen while reporting
# the new theme, so the flag has to leave this alone.
T.apply("uitest")
with contextlib.redirect_stdout(io.StringIO()) as no_icons_output:
    tdtheme_cli.main(["apply", "uibare", "--no-icons"])
check(install_tox.read_bytes() == default_tox.read_bytes(),
      "apply --no-icons still installs the ui.tox")

# No backup for ui.tox, on purpose - see _apply_ui_tox. Pinned because a later
# reader would otherwise read the omission as an oversight and "fix" it into
# 1.1 MB per apply, when the outgoing file is already in git in its own theme's
# folder. Backups are opt-in now, so this also has to assert the precondition:
# the absence only means something if an apply really did ask for one and get a
# set. Otherwise it passes on an empty backups/, which proves nothing.
tox_result = T.apply("uitest", backup=True)
check(tox_result["backup"] is not None, "the ui.tox apply did get a backup set")
check((tox_result["backup"] / T.TOUCHCOLORS).exists(),
      "and that set does hold the stores")
check(not (tox_result["backup"] / T.UI_TOX).exists()
      and not (tox_result["backup"] / T.SYSTEM_DIRNAME / T.UI_TOX).exists(),
      "ui.tox is left out even when --backup asks, unlike the stores and icons")
check(not list(T.backups_dir.glob(f"*/{T.UI_TOX}"))
      and not list(T.backups_dir.glob(f"*/{T.SYSTEM_DIRNAME}/{T.UI_TOX}")),
      "no apply leaked a ui.tox into backups/ under either path")

# The CLI line. The two cases have to be distinguishable, because they install
# different dialog geometry and say the same thing otherwise.
with contextlib.redirect_stdout(io.StringIO()) as own_output:
    tdtheme_cli.main(["apply", "uitest"])
with contextlib.redirect_stdout(io.StringIO()) as bare_output:
    tdtheme_cli.main(["apply", "uibare"])
check(T.UI_TOX in own_output.getvalue() and "uitest" in own_output.getvalue(),
      "apply names the ui.tox it wrote, and the theme it came from")
check(T.DEFAULT_THEME in bare_output.getvalue(),
      f"apply says when the ui.tox came from {T.DEFAULT_THEME} rather than the theme")

# With nothing to fall back to there is nothing to install, and saying so beats
# leaving the previous theme's file in place unremarked.
stashed = T.theme_ui_tox(T.DEFAULT_THEME).read_bytes()
default_tox.unlink()
try:
    result = T.apply("uibare")
    check(not result["ui_tox"]["applied"] and T.UI_TOX in result["ui_tox"]["reason"],
          "with no ui.tox anywhere, apply reports that rather than guessing")
    check(install_tox.read_bytes() == stashed,
          "and the install keeps what it had instead of being blanked")
finally:
    default_tox.write_bytes(stashed)

# capture keeps a verbatim copy, so the pristine file is recoverable from the
# baseline and not only from the theme that happens to ship it.
T.apply("uitest")
T.capture(force=True)
check(T.baseline_ui_tox().read_bytes() == TOX_BYTES,
      "capture copies the install's ui.tox into the baseline verbatim")
check(T.baseline_ui_tox().parent.name == T.SYSTEM_DIRNAME,
      "the baseline copy keeps the install's subdirectory, so Config/System "
      "mirrors the layout it was captured from")

T.apply("default")
check(install_tox.read_bytes() == stashed,
      "the install is back to the stock ui.tox to end on")
check(installed_state() == STOCK,
      "and the stores and icons are too, so the wrapper section starts stock")

# --------------------------------------------------------------- the wrapper
#
# `tdtheme` is the entry point anyone actually types, and it is a shell script
# that has to locate its own cli.py. It gets onto PATH as a symlink, so
# `dirname $0` is the directory holding the *link*, not the repository - the
# wrapper has to follow the chain itself. Nothing else here would notice if it
# stopped: every other check imports the library directly, and the failure mode
# is a "can't open file" from a foreign working directory.
WRAPPER = PROJECT / "tdtheme"
check(WRAPPER.is_file() and os.access(WRAPPER, os.X_OK),
      "the ./tdtheme wrapper exists and is executable")

away = tmp / "elsewhere"
away.mkdir(parents=True, exist_ok=True)
links = tmp / "links"
links.mkdir(parents=True, exist_ok=True)


def run_wrapper(argv, cwd):
    return subprocess.run(argv, capture_output=True, text=True, cwd=cwd,
                          env={**os.environ, "PATH": f"{links}{os.pathsep}"
                               + os.environ.get("PATH", "")})


direct = run_wrapper([str(WRAPPER), "list"], away)
check(direct.returncode == 0 and "defaultnowarn" in direct.stdout,
      f"the wrapper runs from an unrelated directory and finds cli.py "
      f"(exit {direct.returncode}: {direct.stderr.strip()[:70]})")

# The three link shapes the resolution loop has to handle: absolute, relative to
# the link's own directory, and a link pointing at another link.
#
# The relative path is computed between *realpaths*, and it has to be. This
# suite's temp dir is reached through /var -> /private/var, so relpath between
# the logical paths yields one `..` too few and the link dangles - a broken test
# that looks like a broken wrapper. Same trap as any other symlinked ancestor.
(links / "absolute").symlink_to(WRAPPER)
(links / "relative").symlink_to(
    os.path.relpath(os.path.realpath(WRAPPER), os.path.realpath(links)))
(links / "chained").symlink_to("absolute")
for name in ("absolute", "relative", "chained"):
    result = run_wrapper([str(links / name), "list"], away)
    check(result.returncode == 0,
          f"the wrapper resolves a {name} symlink to itself "
          f"(exit {result.returncode}: {result.stderr.strip()[:60]})")

# And the real invocation: a bare name found on PATH, with nothing in the command
# mentioning the repository at all.
#
# The link has to exist under the name the command answers to, *inside* this temp
# dir. Without it the search falls through to whatever `tdtheme` the machine
# happens to have installed - so the check passes on a developer box that took
# the symlink step and fails on a clean checkout, the worst order for a test to
# fail in.
(links / "tdtheme").symlink_to("absolute")
bare = run_wrapper(["tdtheme", "list"], away)
check(bare.returncode == 0 and "defaultnowarn" in bare.stdout,
      f"`tdtheme` works as a bare command found on PATH "
      f"(exit {bare.returncode}: {bare.stderr.strip()[:70]})")

# The interpreter. On a Mac without the Command Line Tools, `/usr/bin/python3` is
# a 118 KB stub that opens a GUI installer rather than running anything, so "there
# is a python3" and "python3 runs" are different questions. The wrapper falls back
# to TouchDesigner's bundled interpreter, which is a dependency anyone using this
# tool already has.
#
# The PATH built here holds the shell utilities the wrapper genuinely needs to
# find itself - `dirname` and `readlink` - and deliberately no interpreter. An
# entirely empty PATH would be a different test: it would fail on `dirname` before
# ever reaching the interpreter choice, which is a property of `#!/bin/sh` rather
# than of this fallback, and it would pass or fail for the wrong reason.
no_python_bin = tmp / "bin-without-python"
no_python_bin.mkdir(exist_ok=True)
for utility in ("dirname", "readlink"):
    real = shutil.which(utility)
    if real:
        (no_python_bin / utility).symlink_to(real)
(links / "python3").unlink(missing_ok=True)  # in case a future check linked one
no_python = subprocess.run(
    [str(links / "tdtheme"), "list"], capture_output=True, text=True, cwd=away,
    # PATH loses the interpreter but the rest of the environment stays: `list`
    # still needs TDTHEME_CONFIG to know which install it is talking about, and
    # that is not what this check is about.
    env={**os.environ, "PATH": os.pathsep.join([str(links), str(no_python_bin)])})
check(no_python.returncode == 0 and "defaultnowarn" in no_python.stdout,
      f"and falls back to TouchDesigner's interpreter when PATH has no python3 "
      f"(exit {no_python.returncode}: {no_python.stderr.strip()[:70]})")

# A python3 that is on PATH but will not run - the CLT stub's shape, and the
# case `command -v` cannot see. The wrapper has to try it and fall back, not
# exec it into a failure.
#
# This assertion was `returncode != 0`, which passed for the wrong reason: the
# old wrapper found the stub with `command -v`, exec'd it, and reported the
# stub's exit status. So the test was satisfied by a *failure*, and named the
# absence of one. What matters is that the command works, which is what it says
# now - a stub on PATH is a reason to use TouchDesigner's interpreter, not a
# reason to fail.
stub_bin = tmp / "bin-with-broken-python"
stub_bin.mkdir(exist_ok=True)
for utility in ("dirname", "readlink"):
    real = shutil.which(utility)
    if real:
        (stub_bin / utility).symlink_to(real)
(stub_bin / "python3").write_text("#!/bin/sh\nexit 1\n")
os.chmod(stub_bin / "python3", 0o755)
not_runnable = subprocess.run(
    [str(links / "tdtheme"), "list"], capture_output=True, text=True, cwd=away,
    env={**os.environ, "PATH": os.pathsep.join([str(stub_bin), str(links)])})
check(not_runnable.returncode == 0 and "defaultnowarn" in not_runnable.stdout,
      f"and a python3 on PATH that cannot run is not trusted, but the command "
      f"still works (exit {not_runnable.returncode}: "
      f"{not_runnable.stderr.strip()[:70]})")

# ------------------------------------------------- uninstall and update
#
# Both of these can destroy work, so both refuse rather than warn, and both are
# tested against a scratch git remote rather than the network.
print()
print("Uninstall and update")
print("-" * 60)

# `update` is `git pull`, and the three outcomes that matter are: refuses on a
# dirty tree, fast-forwards, and refuses to invent a merge. Each gets a real
# upstream, because the first version of this reported "Already up to date" on a
# pull that had genuinely moved - a fixture problem and a code problem at once,
# and only running git for real distinguishes them.
upstream = tmp / "upstream.git"
work = tmp / "upstream-work"
subprocess.run(["git", "init", "-q", "--bare", str(upstream)], check=True)
subprocess.run(["git", "clone", "-q", str(upstream), str(work)], check=True)
for cfg in (("user.email", "test@example.invalid"), ("user.name", "test")):
    subprocess.run(["git", "-C", str(work), "config", *cfg], check=True)
subprocess.run(["git", "-C", str(work), "remote", "add", "src", str(PROJECT)],
               check=True)
subprocess.run(["git", "-C", str(work), "fetch", "-q", "src", "main"], check=True)
subprocess.run(["git", "-C", str(work), "checkout", "-q", "-B", "main", "FETCH_HEAD"],
               check=True)
# Seed the upstream from the *working tree*, not from PROJECT's HEAD. Otherwise
# every clone under test runs the previously committed cli.py, so a fix in an
# uncommitted file is not exercised at all - which is exactly what happened: the
# clone fast-forwarded correctly and still printed "Already up to date", because
# the old return-order bug was the code actually running.
subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
# --allow-empty so this succeeds whether or not the working tree differed, which
# depends on whether cli.py happens to be committed at the time the suite runs.
subprocess.run(["git", "-C", str(work), "commit", "-q", "--allow-empty", "-m",
                "test: the working tree as upstream"], check=True)
subprocess.run(["git", "-C", str(work), "push", "-q", str(upstream), "main"], check=True)


def clone_to(name):
    """A fresh clone of the scratch upstream, so each case starts clean."""
    dest = tmp / name
    subprocess.run(["git", "clone", "-q", str(upstream), str(dest)], check=True)
    return dest


def run_in(repo, argv):
    """Run the CLI inside a scratch clone, with the real network unreachable."""
    return subprocess.run([sys.executable, str(repo / "cli.py"), *argv],
                          capture_output=True, text=True, cwd=str(repo),
                          env={**os.environ})


# A dirty tree is the case that matters: a theme edited on disk and not yet
# committed is invisible to git, so a pull that overwrites it loses work that no
# `git reflog` can bring back.
dirty = clone_to("up-dirty")
(dirty / "README.md").write_text("a local edit\n")
r = run_in(dirty, ["update"])
check(r.returncode != 0, f"update refuses on an uncommitted change ({r.returncode})")
check("uncommitted" in r.stderr, "and says why")
check("README.md" in r.stderr, "and names what is modified")
check("git stash" in r.stderr, "and gives a way forward that does not lose it")
check((dirty / "README.md").read_text() == "a local edit\n",
      "and the local edit is untouched")

# A clean tree strictly behind: the ordinary case, and the one that must report
# what it did rather than shrugging.
behind = clone_to("up-behind")
subprocess.run(["git", "-C", str(work), "commit", "-q", "--allow-empty",
                "-m", "upstream moved on"], check=True)
subprocess.run(["git", "-C", str(work), "push", "-q", str(upstream), "main"], check=True)
was = subprocess.run(["git", "-C", str(behind), "rev-parse", "--short", "HEAD"],
                     capture_output=True, text=True).stdout.strip()
r = run_in(behind, ["update"])
now = subprocess.run(["git", "-C", str(behind), "rev-parse", "--short", "HEAD"],
                     capture_output=True, text=True).stdout.strip()
check(r.returncode == 0, f"update succeeds on a clean tree ({r.returncode}: "
      f"{r.stderr.strip()[:60]})")
check(now != was, f"and the checkout really moved ({was} -> {now})")
check(was in r.stdout and now in r.stdout,
      f"and it reports the move it made ({r.stdout.strip()[:60]!r})")
check("Already up to date" not in r.stdout,
      "and does not claim there was nothing to do")

# Already current: the message is different and must not claim a move.
r = run_in(behind, ["update"])
check(r.returncode == 0 and "Already up to date" in r.stdout,
      f"a second run says there is nothing to do ({r.returncode})")
check(was not in r.stdout.split("Already up to date")[0],
      "and does not invent a version change")

# Diverged: both sides moved, so there is no fast-forward. Resolving a merge on
# the user's behalf is exactly the thing that produces a conflict they have no
# memory of, so this stops.
diverge = clone_to("up-diverge")
subprocess.run(["git", "-C", str(diverge), "commit", "-q", "--allow-empty",
                "-m", "local work"], check=True)
subprocess.run(["git", "-C", str(work), "commit", "-q", "--allow-empty",
                "-m", "upstream work"], check=True)
subprocess.run(["git", "-C", str(work), "push", "-q", str(upstream), "main"], check=True)
r = run_in(diverge, ["update"])
check(r.returncode != 0, f"update refuses when the branches diverged ({r.returncode})")
check("rebase" in r.stderr or "merge" in r.stderr,
      "and explains that there is no fast-forward")
head = subprocess.run(["git", "-C", str(diverge), "rev-parse", "HEAD"],
                      capture_output=True, text=True).stdout.strip()
check(subprocess.run(["git", "-C", str(diverge), "status", "--porcelain"],
                     capture_output=True, text=True).stdout.strip() == "",
      "and left the tree alone, with no half-finished merge")
check("MERGE_HEAD" not in subprocess.run(
    ["git", "-C", str(diverge), "rev-parse", "--verify", "-q", "MERGE_HEAD"],
    capture_output=True, text=True).stdout, "and no merge in progress")

# Not a git checkout at all: there is nothing to pull, and saying so is more
# useful than a git error.
nogit = tmp / "not-a-checkout"
nogit.mkdir()
for f in ("cli.py", "tdtheme.py", "tdicons.py", "tdtiff.py"):
    shutil.copy2(PROJECT / f, nogit / f)
r = subprocess.run([sys.executable, str(nogit / "cli.py"), "update"],
                   capture_output=True, text=True, cwd=str(nogit),
                   env={**os.environ})
check(r.returncode != 0, f"update refuses outside a checkout ({r.returncode})")
check("git clone" in r.stderr, "and points at re-cloning instead")

# `uninstall` restores the install first, because that is the one step the user
# cannot undo once the links are gone.
#
# These run out of process, against their own config dirs, so the byte
# comparisons read the files directly rather than through the module-level
# `install`/`install_icons` paths the rest of this file uses. TouchDesigner's
# real config is never involved: TDTHEME_CONFIG points somewhere disposable.
def seed_config(where):
    """A pristine stock install in `where`, so apply/reset have something to act on."""
    where.mkdir(parents=True, exist_ok=True)
    for store in T.STORE_FILES:
        shutil.copy2(T.baseline_dir / store, where / store)
    (where / T.SYSTEM_DIRNAME).mkdir(exist_ok=True)
    shutil.copy2(T.baseline_dir / T.SYSTEM_DIRNAME / T.UI_TOX,
                 where / T.SYSTEM_DIRNAME / T.UI_TOX)
    icons = where / T.ICONS_DIRNAME
    icons.mkdir(exist_ok=True)
    for tiff in sorted(T.baseline_icons_dir().glob("*.tiff")):
        shutil.copy2(tiff, icons / tiff.name)


def state_of(where):
    """The bytes uninstall is supposed to control, for direct comparison."""
    out = {store: (where / store).read_bytes() for store in T.STORE_FILES}
    out["icons"] = {p.name: p.read_bytes()
                    for p in sorted((where / T.ICONS_DIRNAME).glob("*.tiff"))}
    return out


def stock_state():
    out = {store: (T.baseline_dir / store).read_bytes() for store in T.STORE_FILES}
    out["icons"] = {p.name: p.read_bytes()
                    for p in sorted(T.baseline_icons_dir().glob("*.tiff"))}
    return out


STOCK_BYTES = stock_state()

u_repo = clone_to("uninstall-repo")
u_config = tmp / "uninstall-config"
seed_config(u_config)
subprocess.run([sys.executable, str(u_repo / "cli.py"), "apply", "midnight"],
               capture_output=True, text=True, cwd=str(u_repo),
               env={**os.environ, "TDTHEME_CONFIG": str(u_config)})
check(state_of(u_config) != STOCK_BYTES,
      "test uninstall: the install really is themed to start with")

# A sandbox bin dir holding links to this checkout, and a PATH that cannot reach
# the real /opt ones. A sandbox link and a real link resolve to the same file,
# so an earlier version reported - and unlinked - the real one too, twice.
u_bin = tmp / "uninstall-bin"
u_bin.mkdir()
for link, target in (("tdtheme", "tdtheme"), ("tdthememaker", "tdthememaker-cli"),
                     ("check-td-writes", "check-td-writes")):
    (u_bin / link).symlink_to(u_repo / target)
r = subprocess.run([sys.executable, str(u_repo / "cli.py"), "uninstall"],
                   capture_output=True, text=True, cwd=str(u_repo),
                   env={**os.environ, "PATH": f"{u_bin}:/usr/bin:/bin",
                        "TDTHEME_CONFIG": str(u_config)})
check(r.returncode == 0, f"uninstall succeeds ({r.returncode}: {r.stderr.strip()[:60]})")
check(state_of(u_config) == STOCK_BYTES,
      "and puts TouchDesigner's files back to stock")
check(not list(u_bin.iterdir()), "and removes the links")
check(not (u_repo / ".applied.json").exists(), "and clears the applied-theme record")
check(u_repo.exists() and (u_repo / "tdtheme.py").exists(),
      "and does NOT delete the checkout - it holds the user's themes")
check("rm -rf" in r.stdout, "but says how to remove it, leaving the choice to them")

# A link to somewhere else is not this tool's to delete.
other = tmp / "someone-elses-checkout"
other.mkdir()
(other / "tdtheme").write_text("#!/bin/sh\n")
os.chmod(other / "tdtheme", 0o755)
keep_bin = tmp / "uninstall-keep-bin"
keep_bin.mkdir()
(keep_bin / "tdtheme").symlink_to(other / "tdtheme")
r = subprocess.run([sys.executable, str(u_repo / "cli.py"), "uninstall", "--keep-files"],
                   capture_output=True, text=True, cwd=str(u_repo),
                   env={**os.environ, "PATH": f"{keep_bin}:/usr/bin:/bin",
                        "TDTHEME_CONFIG": str(u_config)})
check((keep_bin / "tdtheme").is_symlink(),
      "uninstall leaves a link to another checkout alone")
check("No links to this checkout" in r.stdout, "and says there was nothing of its own")

# --keep-files removes the commands but leaves TouchDesigner themed, which is
# the one case where the order in the docstring is deliberately inverted.
k_repo = clone_to("uninstall-keep-repo")
k_config = tmp / "uninstall-keep-config"
seed_config(k_config)
subprocess.run([sys.executable, str(k_repo / "cli.py"), "apply", "midnight"],
               capture_output=True, text=True, cwd=str(k_repo),
               env={**os.environ, "TDTHEME_CONFIG": str(k_config)})
k_bin = tmp / "uninstall-keep2-bin"
k_bin.mkdir()
(k_bin / "tdtheme").symlink_to(k_repo / "tdtheme")
r = subprocess.run([sys.executable, str(k_repo / "cli.py"), "uninstall", "--keep-files"],
                   capture_output=True, text=True, cwd=str(k_repo),
                   env={**os.environ, "PATH": f"{k_bin}:/usr/bin:/bin",
                        "TDTHEME_CONFIG": str(k_config)})
check(not list(k_bin.iterdir()), "--keep-files still removes the links")
check(state_of(k_config) != STOCK_BYTES, "--keep-files leaves TouchDesigner themed")

# ------------------------------------------------------------------- setup
#
# `setup` is the one-shot installer, and it exists because of the two ways an
# installed `tdtheme` can answer "permission denied" while the symlink looks
# fine. Both are reproduced here, because both were found on a real machine
# rather than reasoned about, and a test that only covers the happy path would
# have shipped both bugs.
SETUP = PROJECT / "setup"
check(SETUP.is_file() and os.access(SETUP, os.X_OK),
      "the ./setup installer exists and is executable")


def run_setup(args, bin_dir, path=None, debug=False):
    """Run setup against a scratch bin dir, never the real one."""
    return subprocess.run(
        [str(SETUP), *args, str(bin_dir)], capture_output=True, text=True,
        cwd=away, env={**os.environ, "PATH": path or os.environ["PATH"],
                       "SETUP_DEBUG": "1" if debug else "0"})


def fresh_checkout(name):
    """A private copy of the repo, so a mode or a link can be broken safely."""
    dest = tmp / name
    shutil.copytree(PROJECT, dest, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", "backups", "baseline.local", "testiconsforagents"))
    return dest


# The happy path. Also the idempotence check, because a setup script people run
# twice must not become the thing that breaks their install.
s_bin = tmp / "setup-bin"
s_bin.mkdir(parents=True, exist_ok=True)
s_repo = fresh_checkout("setup-happy")
first = subprocess.run([str(s_repo / "setup"), str(s_bin)],
                       capture_output=True, text=True, cwd=str(s_repo),
                       env={**os.environ, "SETUP_DEBUG": "0"})
check(first.returncode == 0, f"setup succeeds from a clean checkout "
      f"(exit {first.returncode}: {first.stderr.strip()[:70]})")
for name, source in (("tdtheme", "tdtheme"), ("tdthememaker", "tdthememaker-cli"),
                     ("check-td-writes", "check-td-writes")):
    link = s_bin / name
    check(link.is_symlink() and os.readlink(link) == str(s_repo / source),
          f"setup links {name} -> {source}")
check((s_bin / "tdtheme").is_file() and os.access(s_bin / "tdtheme", os.X_OK),
      "and the link resolves to something executable")

second = subprocess.run([str(s_repo / "setup"), str(s_bin)],
                        capture_output=True, text=True, cwd=str(s_repo),
                        env={**os.environ, "SETUP_DEBUG": "0"})
check(second.returncode == 0 and "already linked" in second.stdout,
      f"and running it again is a no-op, not a second link "
      f"(exit {second.returncode})")

# Failure mode 1: the exec bit lost in transfer. Git records it, so a clone keeps
# it - but exFAT, a cloud sync and a zip all drop it, and the symptom is zsh
# saying "permission denied" about a link that is perfectly well formed.
lost = fresh_checkout("setup-noexec")
(lost / "tdtheme").chmod(0o644)
check(not os.access(lost / "tdtheme", os.X_OK), "test setup: the exec bit is gone")
r = subprocess.run([str(lost / "setup"), str(s_bin)], capture_output=True,
                   text=True, cwd=str(lost), env={**os.environ, "SETUP_DEBUG": "0"})
check(r.returncode == 0 and os.access(lost / "tdtheme", os.X_OK),
      f"setup restores the executable bit and then succeeds "
      f"(exit {r.returncode}: {r.stderr.strip()[:60]})")
check("restoring the executable bit" in r.stdout,
      "and says that it did, rather than fixing it silently")

# Failure mode 2, and the sneakier of the two: `ln -s target dir/name` does not
# fail when dir/name is already a directory, it nests the link inside it. PATH
# then finds a directory where a command should be, and exec'ing a directory is
# EACCES - the same "permission denied" as mode 1, for a completely different
# reason. This was reported from a real machine.
shadowed = fresh_checkout("setup-shadowed")
d_bin = tmp / "setup-dir-bin"
d_bin.mkdir(parents=True, exist_ok=True)
(d_bin / "tdtheme").mkdir()          # a directory already owns the name
r = subprocess.run([str(shadowed / "setup"), str(d_bin)], capture_output=True,
                   text=True, cwd=str(shadowed), env={**os.environ, "SETUP_DEBUG": "0"})
check(r.returncode != 0, f"setup refuses rather than nesting a link inside it "
      f"(exit {r.returncode})")
check("is a directory" in r.stderr,
      f"and says what it found ({r.stderr.strip()[:80]!r})")
check("permission denied" in r.stderr,
      "and connects it to the error the user actually sees")
check(not (d_bin / "tdtheme" / "tdtheme").exists(),
      "and did not create the nested link ln -s would have made silently")
check("rm -rf" in r.stderr, "and gives the exact command to recover")
# An empty directory is unambiguous, so the advice is that removing it is safe.
check("safe" in r.stderr, "and says the empty directory is safe to remove")

# A directory that is NOT empty is someone else's data, and must not be removed
# or called safe.
busy_bin = tmp / "setup-busy-bin"
busy_bin.mkdir(parents=True, exist_ok=True)
(busy_bin / "tdtheme").mkdir()
(busy_bin / "tdtheme" / "important.txt").write_text("not ours\n")
r = subprocess.run([str(shadowed / "setup"), str(busy_bin)], capture_output=True,
                   text=True, cwd=str(shadowed), env={**os.environ, "SETUP_DEBUG": "0"})
check(r.returncode != 0 and (busy_bin / "tdtheme" / "important.txt").exists(),
      "a non-empty directory is left completely alone")
check("NOT empty" in r.stderr, "and is not described as safe to remove")

# A link pointing at a different checkout is a stale install, and the failure it
# causes - a command that runs the wrong themes - is invisible until it matters.
stale_bin = tmp / "setup-stale-bin"
stale_bin.mkdir(parents=True, exist_ok=True)
elsewhere = fresh_checkout("setup-elsewhere")
(stale_bin / "tdtheme").symlink_to(elsewhere / "tdtheme")
r = subprocess.run([str(s_repo / "setup"), str(stale_bin)], capture_output=True,
                   text=True, cwd=str(s_repo), env={**os.environ, "SETUP_DEBUG": "0"})
check(r.returncode == 0 and os.readlink(stale_bin / "tdtheme") == str(s_repo / "tdtheme"),
      f"a link to another checkout is repointed ({r.returncode})")
check("replacing it" in r.stdout, "and the swap is reported, not silent")

# --check must not touch anything. A "what would you do" flag that writes is
# worse than not having it.
probe_bin = tmp / "setup-probe-bin"
probe_bin.mkdir(parents=True, exist_ok=True)
r = subprocess.run([str(s_repo / "setup"), "--check", str(probe_bin)],
                   capture_output=True, text=True, cwd=str(s_repo),
                   env={**os.environ, "SETUP_DEBUG": "0"})
check(r.returncode == 0 and not list(probe_bin.iterdir()),
      f"--check changes nothing ({r.returncode})")
check("would be created" in r.stdout, "and says what it would have done")

# --uninstall removes only what setup made. Someone else's link under the same
# name is left for them.
keep_bin = tmp / "setup-keep-bin"
keep_bin.mkdir(parents=True, exist_ok=True)
(keep_bin / "tdtheme").symlink_to(elsewhere / "tdtheme")
r = subprocess.run([str(s_repo / "setup"), "--uninstall", str(keep_bin)],
                   capture_output=True, text=True, cwd=str(s_repo),
                   env={**os.environ, "SETUP_DEBUG": "0"})
check((keep_bin / "tdtheme").is_symlink(), "--uninstall leaves a foreign link alone")
check("pointing elsewhere" in r.stdout or "not a symlink" in r.stdout,
      "and says why it left it")

# The interpreter preflight runs before anything is created, so a machine with
# no python3 is not left with three commands that cannot run.
no_py_repo = fresh_checkout("setup-nopy")
no_py_bin = tmp / "setup-nopy-bin"
no_py_bin.mkdir(parents=True, exist_ok=True)
_no_py = no_py_repo / "setup"
# Point the TouchDesigner fallback at a path that cannot exist, so the machine
# looks like it has neither a working PATH python3 nor TouchDesigner.
_text = _no_py.read_text().replace(
    "/Applications/TouchDesigner.app", "/nonexistent/TouchDesigner.app")
_no_py.write_text(_text)
os.chmod(_no_py, 0o755)
_bare = tmp / "setup-bare-bin"   # coreutils only, no interpreter
_bare.mkdir(parents=True, exist_ok=True)
for _u in ("ls", "sed", "chmod", "ln", "readlink", "dirname", "rm", "mkdir",
           "mktemp", "command", "stat", "cp"):
    _real = shutil.which(_u)
    if _real:
        (_bare / _u).symlink_to(_real)
r = subprocess.run([str(_no_py), str(no_py_bin)], capture_output=True, text=True,
                   cwd=str(no_py_repo),
                   env={**os.environ, "PATH": str(_bare), "SETUP_DEBUG": "0"})
check(r.returncode != 0, f"setup refuses with no usable interpreter ({r.returncode})")
check("python3" in r.stderr, "and names the missing interpreter")
check("xcode-select" in r.stderr, "and gives a command that fixes it")
check(not list(no_py_bin.iterdir()),
      "and creates nothing, rather than three commands that cannot run")

# A python3 that exists, is executable, and does nothing. This is the Command
# Line Tools stub: `command -v` finds it, so every existence check passes, and
# then exec'ing it opens an installer and exits non-zero.
#
# `tdtheme`'s half of this is covered above, against the same stub. This is here
# for `tdthememaker`, which carried no fallback at all - it exec'd `python3`
# bare, so a stub on PATH took the authoring tool down with it, and silently,
# which is the worst shape for it to fail in. The two wrappers hold their own
# copies of the fallback rather than sharing one, so both have to be covered or
# they will drift.
stub_bin = tmp / "setup-stub-bin"
stub_bin.mkdir(parents=True, exist_ok=True)
for _u in ("dirname", "readlink", "ls", "sed", "chmod", "ln", "rm", "mkdir",
           "mktemp", "command", "stat", "cp"):
    _real = shutil.which(_u)
    if _real:
        (stub_bin / _u).symlink_to(_real)
_stub = stub_bin / "python3"
_stub.write_text("#!/bin/sh\nexit 1\n")     # present, executable, useless
_stub.chmod(0o755)
check(shutil.which("python3", path=str(stub_bin)) is not None,
      "test setup: the stub is found by `command -v`, and would fool it")

# The real python3 stays reachable further down PATH, because the fallback has
# to beat a *found* python3, not only cover a missing one.
stub_repo = fresh_checkout("setup-stub")
r = subprocess.run([str(stub_repo / "tdthememaker-cli"), "--help"],
                   capture_output=True, text=True, cwd=str(stub_repo),
                   env={**os.environ,
                        "PATH": f"{stub_bin}{os.pathsep}{os.environ.get('PATH','')}",
                        "PYTHONPATH": ""})
check(r.returncode == 0 and "usage" in r.stdout.lower(),
      f"tdthememaker runs despite a python3 on PATH that does nothing "
      f"(exit {r.returncode}: {(r.stderr or r.stdout).strip()[:70]})")

# With nothing usable at all, the message has to name the stub specifically.
# "no python3 found" is the wrong sentence for a python3 that is right there and
# does nothing, and the fix differs: install the real interpreter, don't go
# looking for one.
_nopy2 = stub_repo / "tdthememaker-cli"
_text2 = _nopy2.read_text().replace(
    "/Applications/TouchDesigner.app", "/nonexistent/TouchDesigner.app")
_nopy2.write_text(_text2)
os.chmod(_nopy2, 0o755)
r = subprocess.run([str(_nopy2), "--help"], capture_output=True, text=True,
                   cwd=str(stub_repo), env={**os.environ, "PATH": str(stub_bin)})
check(r.returncode == 127,
      f"and exits 127 with no usable interpreter ({r.returncode})")
check("xcode-select" in r.stderr,
      "and still gives a command that fixes it")

# The verification must not be satisfiable by some other tdtheme further down
# PATH, and it must still run when a link is present but broken. Getting here
# needs a *symlink* at the name, not a directory: the preflight refuses a
# directory before any link work, which is the right order but means the
# directory case above never reaches the verification block at all. So this
# builds the situation the verification exists for - a link whose target does
# not run - and checks it is caught.
v_repo = fresh_checkout("setup-verify")
v_bin = tmp / "setup-verify-bin"
v_bin.mkdir(parents=True, exist_ok=True)
# A symlink to a working command somewhere else on PATH. Install correctly
# repoints this, so the run itself is not the test - the test is that a *broken*
# link is caught, which needs the link to stay broken. A directory cannot be
# used: the preflight refuses it before any link work, which is the right order
# but leaves nothing for the verification block to catch.
#
# So: point the link at a file that exists, is not executable, and that setup
# has no reason to repair - outside the checkout entirely. An earlier version
# used cli.py, and setup's exec-bit repair then made it runnable, so the test
# passed for the wrong reason. A fixture the tool under test can edit is not a
# fixture.
_not_exec = tmp / "not-executable"
_not_exec.write_text("#!/bin/sh\nexit 0\n")
os.chmod(_not_exec, 0o644)
(v_bin / "tdtheme").symlink_to(_not_exec)
check((v_bin / "tdtheme").is_symlink() and not os.access(v_bin / "tdtheme", os.X_OK),
      "test setup: a resolvable but non-executable link is in place")
r = subprocess.run([str(v_repo / "setup"), str(v_bin)], capture_output=True,
                   text=True, cwd=str(v_repo), env={**os.environ, "SETUP_DEBUG": "0"})
# Install repointed it, so the run is expected to succeed. What matters is that
# it succeeded *by fixing the link*, not by running something else: a real
# tdtheme, on this machine, is already installed at /opt/homebrew/bin, so a
# check that ran `tdtheme` by name would pass even if the link were a directory.
check(r.returncode == 0, f"a stale link is repaired, and that succeeds "
      f"({r.returncode})")
check("replacing it" in r.stdout, "and the repair is reported")

# Now the property that actually matters, tested directly: the verification must
# be running the link, not a name. Replace the link with a directory *after* the
# script's preflight has already been satisfied is not possible from outside, so
# the check is made where it can be: a link whose target is not runnable, with
# the run stopped from repairing it. `--check` never repairs, so the link is
# still broken when the script is done with it.
dead_bin = tmp / "setup-dead-bin"
dead_bin.mkdir(parents=True, exist_ok=True)
(dead_bin / "tdtheme").symlink_to(_not_exec)
r = subprocess.run([str(v_repo / "setup"), "--check", str(dead_bin)],
                   capture_output=True, text=True, cwd=str(v_repo),
                   env={**os.environ, "SETUP_DEBUG": "0"})
check("would be created" in r.stdout or "->" in r.stdout,
      "--check reports a stale link rather than acting on it")
check(os.readlink(dead_bin / "tdtheme") == str(_not_exec),
      "and left the broken link exactly as it found it")

# The bug this whole design is guarding against, tested the way it happened.
# Verification that runs `tdtheme` by name is answered by whatever is first on
# PATH, so a healthy tdtheme somewhere earlier makes a broken link look fine.
# The first version of this check did exactly that and reported success against
# a directory sitting where the link should be.
decoy_dir = tmp / "setup-decoy-bin"
decoy_dir.mkdir(parents=True, exist_ok=True)
_decoy = decoy_dir / "tdtheme"
_decoy.write_text("#!/bin/sh\nexit 0\n")     # a perfectly healthy command
os.chmod(_decoy, 0o755)
shadow_bin = tmp / "setup-shadow-bin"
shadow_bin.mkdir(parents=True, exist_ok=True)
(shadow_bin / "tdtheme").mkdir()              # ... and a directory at the real spot
r = subprocess.run([str(v_repo / "setup"), str(shadow_bin)], capture_output=True,
                   text=True, cwd=str(v_repo),
                   env={**os.environ, "SETUP_DEBUG": "0",
                        # The decoy is first on PATH, exactly as an unrelated
                        # install would be.
                        "PATH": f"{decoy_dir}:{os.environ['PATH']}"})
check(r.returncode != 0,
      f"a healthy tdtheme earlier on PATH cannot satisfy verification "
      f"(exit {r.returncode})")
check("is a directory" in r.stderr,
      "and the real obstruction is reported instead of a pass")

# A directory that is on PATH but not writable must be skipped rather than
# escalated to sudo, and the choice must be reported.
probe = subprocess.run([str(SETUP), "--check"], capture_output=True, text=True,
                       cwd=str(PROJECT), env={**os.environ, "SETUP_DEBUG": "1",
                                               "HOMEBREW_BIN": "/nonexistent"})
check("bin dir" in probe.stdout,
      f"setup chooses a directory without being told one "
      f"({probe.stdout.strip()[:60]!r})")
check("skipping" in probe.stderr or "chose" in probe.stderr,
      "and traces the probes it made when asked to")

# ---------------------------------------------------------------- cleanup

# ------------------------------------------- a key the baseline has not seen
#
# The dangerous direction. `apply` writes merge(baseline, theme), so a key that
# exists only in the install is in neither input and would be deleted by every
# apply - silently, exit 0, with nothing in the output but a count that reads as
# a fact about the theme. On a TouchDesigner build newer than the baseline that
# is data loss, and the install is quietly rolled back towards a build it is no
# longer running.
#
# Carrying such keys through would fix that and break something else: a key added
# by a *theme* overlay is indistinguishable from a key added by a build, and
# preserving both makes themes permanently additive. So the keys are not carried
# through; `apply` refuses instead, and `capture --local` is the fix.
#
# Refusing rather than warning is the load-bearing part, and it was arrived at by
# measuring the warning: it arrived on the same run as the loss, so by the time
# the user could act on it the key was already gone and a second `apply` had
# nothing left to report. These checks pin the refusal, the absence of any
# write, and the fact that the advice still works afterwards.

NEW_BUILD_KEY = "key.only.the.new.build"
NEW_BUILD_VALUE = ["0.11", "0.22", "0.33"]

shutil.copytree(PROJECT / "themes" / "midnight", T.themes_dir / "midnight")

T.apply("default")
colors_path = install / T.TOUCHCOLORS
planted = T.load_file(colors_path, T.TOUCHCOLORS)
planted.data[NEW_BUILD_KEY] = NEW_BUILD_VALUE
colors_path.write_bytes(planted.to_bytes())
check(NEW_BUILD_KEY in T.load_file(colors_path, T.TOUCHCOLORS).data,
      "test setup: the install holds a key the baseline does not")
check(NEW_BUILD_KEY not in T.load_baseline()[T.TOUCHCOLORS].data,
      "test setup: and the baseline really does not")

orphans = T.install_only_keys(T.load_baseline())
check(orphans.get(T.TOUCHCOLORS) == [NEW_BUILD_KEY],
      f"install_only_keys names the key the baseline has not seen ({orphans})")
check(T.TOUCHOPTIONS not in orphans, "and only the store that actually has one")

# It must stop the apply. A warning nobody reads is the same as the bug, and a
# warning printed by the command doing the damage is worse - it reads as
# actionable when the window to act has already closed.
before_refusal = {f.name: f.read_bytes() for f in install.rglob("*") if f.is_file()}
with contextlib.redirect_stdout(io.StringIO()) as orphan_output:
    with contextlib.redirect_stderr(orphan_output):
        orphan_code = tdtheme_cli.main(["apply", "midnight"])
orphan_text = orphan_output.getvalue()
check(orphan_code != 0, f"apply refuses rather than writing ({orphan_code})")
check("never seen" in orphan_text and "TouchColors" in orphan_text,
      f"and says which store holds the key ({orphan_text!r})")
check("capture --local" in orphan_text,
      "and names the command that fixes it, rather than just complaining")
check("allow-unknown" in orphan_text,
      "and offers the explicit opt-out, for a build whose extra keys are junk")

# The property a warning could not have: nothing was written. Compared as bytes
# over the whole install rather than by key name, so a partial write - a store
# replaced and then a failure, or a backup set made and then abandoned - fails
# here too.
after_refusal = {f.name: f.read_bytes() for f in install.rglob("*") if f.is_file()}
check(after_refusal == before_refusal,
      "and wrote nothing at all, so the advice it gave is still available")
check(NEW_BUILD_KEY in T.load_file(colors_path, T.TOUCHCOLORS).data,
      "the key the baseline has not seen is still there - the data loss, avoided")
check(T.install_only_keys(T.load_baseline()).get(T.TOUCHCOLORS) == [NEW_BUILD_KEY],
      "and is therefore still reportable, unlike after a warning-and-proceed")

# The opt-out is real, not decorative. Someone whose build genuinely added junk
# must be able to say so explicitly, and the deletion is still total.
with contextlib.redirect_stdout(io.StringIO()) as allowed_output:
    with contextlib.redirect_stderr(io.StringIO()):
        allowed_code = tdtheme_cli.main(["apply", "midnight", "--allow-unknown"])
check(allowed_code == 0, f"--allow-unknown overrides ({allowed_code})")
check("never seen" not in allowed_output.getvalue(),
      "and then says nothing, because the user already knows")
T.load_file(colors_path, T.TOUCHCOLORS)  # parses, so a corrupt file fails loudly
check(NEW_BUILD_KEY not in T.load_file(colors_path, T.TOUCHCOLORS).data,
      "and does drop the key - the total-overwrite property is unchanged")
check("worksheet.bg" in T.load_file(colors_path, T.TOUCHCOLORS).data,
      "while the keys the baseline does know are written normally")

# The alternative to refusing. Total-overwrite is what `reset` and every theme
# switch depend on, and the test below is the one that was written for it: a key
# a *theme* adds must be droppable by the next theme. If a future change ever
# preserves install-only keys, this is what catches it.
addskey = T.themes_dir / "orphancheck"
addskey.mkdir(parents=True, exist_ok=True)
(addskey / f"{T.TOUCHCOLORS}.yaml").write_text(f'{NEW_BUILD_KEY}: ["9", "9", "9"]\n')
(addskey / f"{T.TOUCHOPTIONS}.yaml").write_text("")
T.apply("orphancheck", force=True, allow_unknown=True)
check(T.load_file(colors_path, T.TOUCHCOLORS).data.get(NEW_BUILD_KEY) == ["9", "9", "9"],
      "a theme can write a key the baseline has never seen")
T.apply("default", allow_unknown=True)
check(NEW_BUILD_KEY not in T.load_file(colors_path, T.TOUCHCOLORS).data,
      "and the next theme drops it - themes do not accumulate")

# The re-baseline route, which is what the refusal tells a tester to do. It has
# to work, and it has to turn the key into an ordinary baseline key so the key
# `reset` writes back is a key the baseline knows about.
check(T.install_only_keys(T.load_baseline()).get(T.TOUCHCOLORS) is None,
      "test setup: no orphans to re-baseline yet")
planted = T.load_file(colors_path, T.TOUCHCOLORS)
planted.data[NEW_BUILD_KEY] = NEW_BUILD_VALUE
colors_path.write_bytes(planted.to_bytes())
check(T.install_only_keys(T.load_baseline()).get(T.TOUCHCOLORS) == [NEW_BUILD_KEY],
      "test setup: the install holds the new build's key again")

T.capture(force=True, into_shadow=True)
check((T.baseline_shadow_dir / T.TOUCHCOLORS).exists(),
      "capture --local writes into baseline.local/")
check(T.load_file(T.baseline_shadow_dir / T.TOUCHCOLORS,
                  T.TOUCHCOLORS).data.get(NEW_BUILD_KEY) == NEW_BUILD_VALUE,
      "and captures the key into it, which is the point")
check(NEW_BUILD_KEY not in T.load_file(T.baseline_shipped_dir / T.TOUCHCOLORS,
                                       T.TOUCHCOLORS).data,
      "and leaves the committed baseline alone")

# The build number `cmd_capture` prints must come from the file it just wrote.
# It read `baseline_version()` instead, which resolves `baseline_dir` - frozen at
# import, before this capture created the shadow - so it reported the *committed*
# baseline's build. Invisible while both builds matched, and wrong exactly when
# someone runs this on a different build, which is the only reason to.
# The shipped fixture is given a deliberately impossible build so the two cannot
# be confused, then restored.
_shipped_version = T.baseline_shipped_dir / "version.json"
_shipped_bytes = _shipped_version.read_bytes()
try:
    _shipped_version.write_bytes(json.dumps(
        {"td_build": "0000.00000", "captured": "x", "icons": 1}).encode())
    with contextlib.redirect_stdout(io.StringIO()) as local_capture:
        tdtheme_cli.main(["capture", "--local", "--force"])
    _printed = [l for l in local_capture.getvalue().splitlines() if "build" in l]
    _recorded = json.loads(
        (T.baseline_shadow_dir / "version.json").read_text())["td_build"]
    check(_printed and _recorded in _printed[0],
          f"capture --local reports the build it just recorded, not the "
          f"committed baseline's ({_printed})")
    check("0000.00000" not in local_capture.getvalue(),
          "and does not report the committed baseline's build at all")
finally:
    _shipped_version.write_bytes(_shipped_bytes)
    # The capture above rewrote the shadow's stores; recapture so the checks
    # below still start from the state they were written against.
    T.capture(force=True, into_shadow=True)
# The shadow is resolved at import, so it only takes effect for a later process.
# Point the module at it the way a fresh run would, or the checks below would
# silently exercise the shipped baseline and pass for the wrong reason.
T.baseline_dir = T.baseline_shadow_dir
check(T.baseline_is_shadowed(),
      "and the shadow is what later commands use")
check(T.install_only_keys(T.load_baseline()).get(T.TOUCHCOLORS) is None,
      "so the key is no longer an orphan - it is a baseline key now")
with contextlib.redirect_stdout(io.StringIO()) as post_capture:
    with contextlib.redirect_stderr(io.StringIO()):
        post_code = tdtheme_cli.main(["apply", "default"])
check(post_code == 0, f"and the apply the refusal blocked now works ({post_code})")
check(NEW_BUILD_KEY in T.load_file(colors_path, T.TOUCHCOLORS).data,
      "and the key survives it")

# Put the install back to stock first, while the shadow still knows the key:
# dropping the shadow while the key is in the install would leave the teardown
# below facing exactly the refusal this section is about.
T.apply("default")
(colors_path).write_bytes(STOCK[T.TOUCHCOLORS])
check(T.status().clean, "the install is clean to end on")

# Now drop the shadow and point the module back, so this section leaves no trace
# and the shadow cannot affect anything that runs after it.
check(not T.baseline_shipped_dir.exists() or
      T.baseline_shipped_dir != T.baseline_shadow_dir,
      "test setup: the shipped baseline and the shadow are distinct directories")
shutil.rmtree(T.baseline_shadow_dir, ignore_errors=True)
T.baseline_dir = T.baseline_shipped_dir
check(not T.install_only_keys(T.load_baseline()),
      "and with it gone, the install is back to a state the baseline fully describes")

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
