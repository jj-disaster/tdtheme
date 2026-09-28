"""Tests for merge / diff / validate / capture / apply / export.

Everything runs against a throwaway copy of the stores in a temp directory,
selected via the TDTHEME_CONFIG environment variable. The real TouchDesigner
config is only ever read. The copy is seeded from the project's pristine
`baseline/`, so the suite is independent of which theme is currently applied to
the real install.

Run:  python3 tests/test_tdtheme.py
"""

from __future__ import annotations

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

import tdtheme as T


def overlay_text(data, name=""):
    """A sparse overlay, in the subset format `tdthememaker export` writes.

    Written out by hand here on purpose. A theme is a human-editable artifact,
    so the thing worth testing is that apply reads a file a person wrote, not
    that a writer and a reader agree with each other.
    """
    lines = ["# tdtheme sparse overlay - only keys that differ from baseline.",
             f"# file: {name}", ""]
    for key, value in data.items():
        if key:
            lines.append(f"{key}: [{', '.join(json.dumps(v) for v in value)}]")
    return "\n".join(lines) + "\n"  # noqa: E402

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

# Seed the throwaway install from the *pristine baseline*, not from the live
# TouchDesigner config. Several assertions below are statements about what the
# vendor ships (`POP.hilite` above 1.0, `tile.inout.origsize` 10, no findings at
# all). Reading those from the live install made the suite fail whenever a theme
# happened to be applied - the suite was asserting the absence of a theme, not
# the correctness of the code. The baseline is the pristine capture, tracked in
# git, and is what every theme diffs against, so it is the right reference.
# Falls back to the live install only if the baseline is missing.
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
# stayed, because applying a theme needs it. A literal stands in for the writer
# here, and tdthememaker's suite checks that the two agree.
text = ("# tdtheme sparse overlay - only keys that differ from baseline.\n"
        "# file: TouchOptions\n"
        "\n"
        'tile.border.size: ["5"]\n'
        'font.default.face: [""]\n'
        'tile.connection.hilite1: ["1", "0", "0"]\n'
        'key.with"quote: ["a\\"b", "", "x y"]\n')
loaded = T.load_overlay(text, "TouchOptions")
check(loaded == sample, "overlay round-trips through loader")

# The same text through the zero-dependency path (no PyYAML here).
manual = T._load_overlay_fallback(text, "TouchOptions")
check(manual == sample, "overlay round-trips through the fallback loader")
check(manual == loaded, "fallback and yaml loaders agree (when yaml absent)")

# Whether PyYAML is importable is a property of the machine, not of this code.
# Asserting it outright made the suite fail on a correctly provisioned host, so
# record it instead: the fallback is exercised exactly when yaml is absent.
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
# A theme installs the same bytes whichever interpreter runs the tool, so the
# PyYAML path and the fallback path have to return the same data for the same
# file. They agree by construction on anything `json.loads` accepts, because
# JSON is a subset of YAML. They used to disagree on everything else: the
# fallback is `json.loads(rest)` and otherwise keeps the raw string, so a
# single-quoted scalar came back with its quotes still attached. Nothing
# compared the two, so the suite stayed green on both interpreters.
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
# theme installed differently on different machines. The fallback now refuses
# it and names the forms that mean the same thing in both loaders.
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

# The same reasoning closes the rest of the class. Each of these is a YAML
# construct the restricted loader cannot replicate, and each used to be kept as
# literal text while PyYAML interpreted it - a silent disagreement, which is
# worse than the single-quote case because nothing looks wrong. A flow mapping
# installed as the string `{a: 1}`; an alias installed as `*x` where PyYAML
# resolved it to the anchored value.
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

# --- a non-numeric size is an error, not a warning --------------------------
#
# This is the other half of the same bug. `apply` gates on severity "error"
# only, so as a warning the quoted size sailed through and was written into the
# install; TouchDesigner then read an unparseable geometry.
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

# ------------------------------------------------- colour field integrity
#
# Regression cover for a real corruption: `worksheet.grid` was hand-edited to
# `0.317 0.189 <TAB> 0.15`, merging two channels into one space-separated
# field. parse() accepted it, serialize() reproduced it, export() captured it
# and validate() reported nothing. The install ended up holding a file
# TouchDesigner cannot read.

print()
print("Colour field integrity")
print("-" * 60)

# The guard is stated against the pristine baseline, not the live install: the
# point is that a *shipped* file is clean. Using the install here would make a
# future corruption read as a validation failure of this rule.
#
# T.baseline_dir / T.themes_dir are repointed at the tmpdir by the fixture, so
# reach the real project copies via PROJECT rather than through the module.
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
# `mono` and `bnw` are two passes at the same brief, so the claims they
# share are asserted once and each theme's own claims follow. The design
# claims live in prose in each theme's header; these tests exist so that if
# a future edit breaks one, something says so.

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
    raw sRGB channels, with no linearisation. Distinct from `_srgb_lum`, which
    is the WCAG relative luminance used for contrast - the two disagree for
    anything that is not already grey, and a key kept on plain luminance has
    to be compared against this one."""
    r, g, b = (float(x) for x in rgb[-3:])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _param_draws(theme):
    """Every (fg, bg) pair the parameter dialog can paint.

    A widget is drawn as a pair, so a family with `bg[SUFF]` but no
    `fg[SUFF]` falls back to the bare `fg`; that combination is invisible to
    a per-suffix check and is included here.
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
# keys that ship with a hue. Each one is a status or marker glyph where the
# colour is the message: green tick, red cross, red bypass, yellow notch. They
# are listed here rather than derived, because the claim worth making is that
# this set has not grown - a new coloured key means a new exemption crept in.
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
    # allows is a glyph or a text colour. A coloured *fill* would be a
    # different claim entirely - a tinted panel - and is not what the header
    # says this theme does.
    check(not [k for k in _coloured if k.endswith(".bg")],
          f"{_name} keeps hue out of every fill "
          f"(coloured fills: {sorted(k for k in _coloured if k.endswith('.bg'))})")

    # Shared claim 2: a state sibling must not become *invisible*, which is a
    # weaker and more honest requirement than "must not share a tone". A
    # widget is drawn as a (fg, bg) pair, so `parms.button.bg` collapsing
    # into `parms.button.bg.disabled` costs nothing as long as `.fg.disabled`
    # differs - the button still greys out. Comparing single channels
    # penalises that case and misses the real one, where a bg collapses and
    # the fg collapses with it.
    #
    # Pairs the vendor ships as identical (`parms.button.border` and
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
        # deliberately two-sided - if this number drops, the theme has
        # quietly become bnw, and if it rises, the ordering guarantee below
        # is the thing that broke.
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
        # A key is inverted only if it is meaningfully brighter in the
        # shipped file and meaningfully darker here. Without a tolerance this
        # measures rounding: luminance is stored to two decimals, so two keys
        # that ship 0.004 apart can land either way round.
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
        # The parameter window is deliberately exempt from the four-tone
        # palette (see its header), so these claims are about the rest of the
        # interface. Scoring the whole file would let 100 luminance keys
        # quietly prove a claim about a screen they are not on. Interface text
        # is exempt too, and for a stronger reason: those keys are not this
        # theme's palette at all, they are whatever TouchDesigner ships, so
        # the claim is made over the keys the overlay actually authors.
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
        # `.axes` to PAPER and the cliff sends `.axes.main` to INK, so on a
        # 0.0 graph background that is a white minor grid and an invisible
        # main axis. Neither is guarded by the palette checks above, because
        # both are legal four-tone values; only their relationship is wrong.
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
        # The bar is deliberately "readable", not "unchanged": this theme
        # inverts fills on purpose, so a pair may legitimately get worse and
        # still be fine - `tile.name.fg` on the LINE tile is 4.65:1, down from
        # 6.89:1. What is not allowed is being both worse than shipped *and*
        # under 4.5:1, which is how the three rescued keys were found.
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
        # of keys allowed to deviate, which is where a hand-set value would
        # otherwise creep in unnoticed.
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
            # 0.01 rather than 0.005: luminance is stored to two decimals, so
            # a key built from it is always that far from the true value, and
            # a genuine hand-set key lands much further away than that.
            _want = _desat_lum(pristine_colors.get(_k))
            if abs(float(_theme.get(_k)[-1]) - _want) > 0.01:
                _off.append((_k, _theme.get(_k)[-1], round(_want, 3)))
        check(not _off,
              f"the parameter window is plain shipped luminance on every key "
              f"except the {len(_HAND)} named ones (offenders: {_off[:2]})")

        # Claim: nothing in the window is invisible, and the window is not
        # worse than what TouchDesigner ships. Luminance alone does not give
        # either - the shipped file contains pairs that are 1.0:1, and
        # desaturating can lose ground where the shipped difference was
        # carried by hue. 0.5 ratio points is above the worst rounding drift
        # this theme actually has (0.25) and far below a real regression.
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
# interesting part is *where* they differ: `bnw` puts the parameter window on
# plain luminance, so from this change onward that window is largely shared
# with `mono` by design, and the two themes are distinguished by the rest of
# the interface. Asserting "they differ on most of the file" alone would let
# the parameter window drift back to the four-tone palette unnoticed, so both
# halves are stated.
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

check((T.baseline_dir / T.TOUCHCOLORS).read_bytes()
      == (install / T.TOUCHCOLORS).read_bytes(),
      "baseline bytes match the install exactly")

# ---------------------------------------------------------------- apply

print()
print("Apply")
print("-" * 60)

# The theme to apply. `export` used to create this one; it now lives in
# tdthememaker, so build it here as a hand-written overlay - which is what a
# theme usually is anyway.
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

# Writing a theme from a modified install moved to tdthememaker (`export`).
# What remains is the half this tool owns: apply a theme, and land it exactly.
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

# --------------------------------------------------------------- the wrapper
#
# `tdtheme` is the entry point anyone actually types, and it is a shell script
# that has to locate its own cli.py. It gets onto PATH as a symlink, so
# `dirname $0` is the directory holding the *link*, not the repository - the
# wrapper has to follow the chain itself. Nothing else in this suite would
# notice if it stopped: every other check here imports the library directly, and
# the failure mode is a "can't open file" from a foreign working directory.
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

# And the real invocation: a bare name found on PATH, with nothing in the
# command mentioning the repository at all.
bare = run_wrapper(["tdtheme", "list"], away)
check(bare.returncode == 0 and "defaultnowarn" in bare.stdout,
      f"`tdtheme` works as a bare command found on PATH "
      f"(exit {bare.returncode}: {bare.stderr.strip()[:70]})")

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
