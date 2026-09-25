"""Acceptance gate: a parsed file must serialise back to identical bytes.

This is the load-bearing invariant of the whole tool. If parse/serialize is
not byte-exact, `apply` would silently rewrite parts of TouchDesigner's files
that the user never asked to change.

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

# Fall back to the project baseline if TouchDesigner is not installed.
for store in (TOUCHCOLORS, TOUCHOPTIONS):
    path = TD_CONFIG / store
    if path.exists():
        roundtrip(path.read_bytes(), f"installed {store}")
    else:
        local = Path(__file__).resolve().parent.parent / "baseline" / store
        if local.exists():
            roundtrip(local.read_bytes(), f"baseline {store}")
        else:
            check(False, f"{store}: no file to test against")

print()
print("Parser edge cases")
print("-" * 60)

cases = [
    ("plain colour", b"a.b\t1\t0\t0\r\n", True),
    ("extra empty field", b"a.b\t\t0.2\t0.2\t0.2\r\n", True),
    ("many empty fields", b"a.b\t\t\t\t1\t2\t3\r\n", True),
    ("empty option value", b"font.face\t\r\n", True),
    ("no trailing newline", b"a.b\t1\t0\t0", False),
    ("bare LF", b"a.b\t1\t0\t0\n", False),
    ("no trailing newline, bare LF", b"a.b\t1\t0\t0", False),
    ("empty file", b"", False),
    ("single line no NL", b"a.b\t1\t0\t0", False),
    ("blank line preserved", b"a.b\t1\t0\t0\r\n\r\nc.d\t0\t1\t0\r\n", True),
]
for label, raw, expected_trailing in cases:
    parsed = TdFile.parse(raw, "TouchColors")
    check(parsed.to_bytes() == raw, f"edge: {label}")

# Terminator and trailing-newline must be carried through, not hard-coded.
lf = TdFile.parse(b"a.b\t1\t0\t0\nb.c\t0\t1\t0\n", "TouchColors")
check(lf.terminator == "\n", "edge: bare-LF terminator detected")
check(lf.to_bytes() == b"a.b\t1\t0\t0\nb.c\t0\t1\t0\n",
      "edge: bare-LF file round-trips without gaining CR")

nt = TdFile.parse(b"a.b\t1\t0\t0", "TouchColors")
check(nt.trailing_newline is False, "edge: missing trailing newline detected")
check(nt.to_bytes() == b"a.b\t1\t0\t0", "edge: no trailing newline is not added")

# The two real oddities must survive, not just round-trip.
colors_path = TD_CONFIG / TOUCHCOLORS
if colors_path.exists():
    real = TdFile.parse(colors_path.read_bytes(), TOUCHCOLORS)
    hint = real.get("dialog.commenthint")
    check(hint is not None and len(hint) == 4 and hint[0] == "",
          "real: dialog.commenthint keeps its empty second field")
    check(real.get("dialog.commenthint.comp") is not None,
          "real: dialog.commenthint.comp present")
    pop = real.rgb("POP.hilite")
    check(pop is not None and max(pop) > 1.0,
          f"real: POP.hilite >1.0 preserved unclamped (got {pop})")
    check(real.get("font.default.face") is None and
          real.get("tile.border.size") is None,
          "real: option keys absent from the colour store")

options_path = TD_CONFIG / TOUCHOPTIONS
if options_path.exists():
    opts = TdFile.parse(options_path.read_bytes(), TOUCHOPTIONS)
    check(opts.get("font.default.face") == [""],
          "real: empty option value round-trips as ['']")
    check(opts.get("font.mono.face") == [""],
          "real: font.mono.face empty value preserved")
    check(opts.get("tile.inout.origsize") == ["10"],
          f"real: tile.inout.origsize is 10 (got {opts.get('tile.inout.origsize')})")

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
