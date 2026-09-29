"""Acceptance gate: a parsed file must serialise back to identical bytes.

The load-bearing invariant of the whole tool: if parse/serialize is not
byte-exact, `apply` silently rewrites parts of TouchDesigner's files the user
never asked to change.

Run:  python3 tests/test_roundtrip.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tdtheme import (  # noqa: E402
    TOUCHCOLORS, TOUCHOPTIONS, TdFile, TD_CONFIG, _have_yaml,
)

PASS, FAIL = "  ok  ", " FAIL "
failures: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"[{PASS if condition else FAIL}] {label}")
    if not condition:
        failures.append(label)


def roundtrip(raw: bytes, name: str) -> None:
    parsed = TdFile.parse(raw, name)
    again = parsed.to_bytes()
    check(again == raw, f"{name}: round-trip is byte-identical "
                        f"({len(raw)} bytes, {len(parsed)} keys)")
    if again != raw:
        for index, (a, b) in enumerate(zip(raw, again)):
            if a != b:
                print(f"         first diff at byte {index}:")
                print(f"           original {raw[max(0,index-40):index+40]!r}")
                print(f"           rebuilt  {again[max(0,index-40):index+40]!r}")
                break
        else:
            print(f"         length differs: {len(raw)} vs {len(again)}")


print("TouchDesigner stores")
print("-" * 60)

# Both the installed files and the pristine baseline, when available: the
# installed copy is the one that has to survive a real apply, and testing the
# baseline too catches a corrupt install without making the result depend on
# which theme happens to be applied right now.
PROJECT = Path(__file__).resolve().parent.parent
BASELINE = PROJECT / "baseline"

sources = []
for label, directory in (("installed", TD_CONFIG), ("baseline", BASELINE)):
    for store in (TOUCHCOLORS, TOUCHOPTIONS):
        path = directory / store
        if path.exists():
            sources.append((f"{label} {store}", path))
        elif label == "installed":
            print(f"  skip  {label} {store} (not present)")

if not sources:
    check(False, "no TouchColors/TouchOptions to test against")

for label, path in sources:
    roundtrip(path.read_bytes(), label)

print()
print("Parser edge cases")
print("-" * 60)

cases = [
    ("plain colour", b"a.b\t1\t0\t0\r\n"),
    ("extra empty field", b"a.b\t\t0.2\t0.2\t0.2\r\n"),
    ("many empty fields", b"a.b\t\t\t\t1\t2\t3\r\n"),
    ("empty option value", b"font.face\t\r\n"),
    ("no trailing newline", b"a.b\t1\t0\t0"),
    ("bare LF", b"a.b\t1\t0\t0\n"),
    ("empty file", b""),
    ("blank line preserved", b"a.b\t1\t0\t0\r\n\r\nc.d\t0\t1\t0\r\n"),
]
for label, raw in cases:
    parsed = TdFile.parse(raw, "TouchColors")
    check(parsed.to_bytes() == raw, f"edge: {label}")

# The terminator and trailing-newline state are carried through, not hard-coded.
lf = TdFile.parse(b"a.b\t1\t0\t0\nb.c\t0\t1\t0\n", "TouchColors")
check(lf.terminator == "\n", "edge: bare-LF terminator detected")
check(lf.to_bytes() == b"a.b\t1\t0\t0\nb.c\t0\t1\t0\n",
      "edge: bare-LF file round-trips without gaining CR")

nt = TdFile.parse(b"a.b\t1\t0\t0", "TouchColors")
check(nt.trailing_newline is False, "edge: missing trailing newline detected")
check(nt.to_bytes() == b"a.b\t1\t0\t0", "edge: no trailing newline is not added")

# The two real oddities must survive, not just round-trip.
#
# Stated against the *baseline*, not the live install: these are claims about
# what the vendor ships, and a theme is allowed to change any of them, so
# reading the install would fail this gate for changing nothing it cares about.
colors_path = BASELINE / TOUCHCOLORS
check(colors_path.exists(), "baseline TouchColors present")
if colors_path.exists():
    real = TdFile.parse(colors_path.read_bytes(), TOUCHCOLORS)
    hint = real.get("dialog.commenthint")
    check(hint is not None and len(hint) == 4 and hint[0] == "",
          "shipped: dialog.commenthint keeps its empty second field")
    check(real.get("dialog.commenthint.comp") is not None,
          "shipped: dialog.commenthint.comp present")
    pop = real.rgb("POP.hilite")
    check(pop is not None and max(pop) > 1.0,
          f"shipped: POP.hilite >1.0 preserved unclamped (got {pop})")
    check(real.get("font.default.face") is None and
          real.get("tile.border.size") is None,
          "shipped: option keys absent from the colour store")

options_path = BASELINE / TOUCHOPTIONS
check(options_path.exists(), "baseline TouchOptions present")
if options_path.exists():
    opts = TdFile.parse(options_path.read_bytes(), TOUCHOPTIONS)
    check(opts.get("font.default.face") == [""],
          "shipped: empty option value round-trips as ['']")
    check(opts.get("font.mono.face") == [""],
          "shipped: font.mono.face empty value preserved")
    check(opts.get("tile.inout.origsize") == ["10"],
          f"shipped: tile.inout.origsize is 10 (got {opts.get('tile.inout.origsize')})")

print()
print(f"PyYAML available: {_have_yaml()}  "
      f"(the tool works either way; the fallback loader covers the emitted subset)")
print()
print("-" * 60)
if failures:
    print(f"{len(failures)} FAILED:")
    for item in failures:
        print(f"  - {item}")
    sys.exit(1)
print("round-trip gate PASSED")
