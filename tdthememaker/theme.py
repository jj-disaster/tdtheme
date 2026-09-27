"""Author a theme from what is currently installed.

`tdtheme` installs themes. This is the other direction: take the live
TouchDesigner configuration and write it out as a new theme directory, so it
can be edited, reviewed, and sent to someone else.

Two things are recorded, and they are recorded differently on purpose:

- **The colour stores** become a *sparse* overlay - only the keys that differ
  from the baseline, in restricted YAML that is a valid subset of the real
  format. That is what makes the result reviewable in a diff and editable by
  hand.
- **The icons** are copied byte for byte. Re-encoding them would make it
  impossible to tell later whether the install changed or this tool's codec
  did, and a TIFF is not reviewable anyway.

The consequence is that an exported theme is reproducible only as bytes. If
the icons were made by a recipe, keep that recipe; if they were placed by hand,
no recipe can describe them, and rebuilding from one would quietly revert them.
`build --check` is how you find out which case you are in.

The store layer - the restricted YAML parser, the sparse differ, the field
integrity rules - belongs to `tdtheme` and is imported rather than copied, so
there is exactly one implementation of the `TouchColors` format. That matters
more here than anywhere else: this module *writes* that format, so a second
implementation would mean themes this tool produces that `tdtheme` cannot read.
"""

from __future__ import annotations

import json
import sys
from collections import OrderedDict
from pathlib import Path

import tdthememaker as F

HERE = Path(__file__).resolve().parent
TDTHEME = HERE.parent / "tdtheme"

if str(TDTHEME) not in sys.path:
    sys.path.insert(0, str(TDTHEME))

import tdtheme as T  # noqa: E402


#: Re-exported so a caller can catch every failure mode from one place.
ThemeError = T.ThemeError
STORE_FILES = T.STORE_FILES


class ExportError(Exception):
    """A theme could not be exported."""


def dump_overlay(data: "OrderedDict[str, list[str]]", name: str = "") -> str:
    """Render a sparse overlay as restricted YAML.

    An overlay describes key overrides, not a file image, so the empty key
    (a blank line) is skipped rather than emitted as invalid YAML. Blank lines
    present in the baseline survive `apply` because merging starts from the
    baseline.
    """
    out = ["# tdtheme sparse overlay - only keys that differ from baseline.",
           f"# file: {name}" if name else "# file:",
           ""]
    for key, value in data.items():
        if not key:
            continue
        rendered = ", ".join(json.dumps(v) for v in value)
        out.append(f"{key}: [{rendered}]")
    return "\n".join(out) + "\n"


def export_theme(name: str, *, force: bool = False) -> "dict[str, Path]":
    """Write the currently installed stores out as a sparse theme.

    Both store files are always written, even when one has no differences, so
    that the theme directory is self-describing: a reader can tell "this theme
    does not touch TouchOptions" from "this theme is missing TouchOptions".
    """
    if not name or any(c in name for c in "/\\"):
        raise ExportError(f"{name!r} is not a usable theme name")
    if not force and name in T.list_themes():
        raise ExportError(
            f"theme {name!r} already exists. Use --force to overwrite it."
        )

    try:
        baseline = T.load_baseline()
    except T.ThemeError as exc:
        raise ExportError(
            f"cannot export without a baseline: {exc}"
        ) from exc

    config = T.config_dir()
    missing = [store for store in T.STORE_FILES if not (config / store).exists()]
    if missing:
        raise ExportError(
            f"the install at {config} has no {', '.join(missing)}. Nothing to "
            f"export."
        )

    directory = T.themes_dir / name
    directory.mkdir(parents=True, exist_ok=True)

    written: "dict[str, Path]" = {}
    for store in T.STORE_FILES:
        installed = T.load_file(config / store, store)
        sparse = T.diff(baseline[store], installed)
        path = T.theme_path(name, store)
        T.write_file(path, dump_overlay(sparse, store).encode())
        written[store] = path

    if T.icons_dir().is_dir():
        written.update(F.capture_icons(T.icons_dir(), T.theme_icons_dir(name)))
    return written


def export_report(name: str, written: "dict[str, Path]") -> "list[str]":
    """Lines describing what an export wrote, for the CLI to print."""
    lines = []
    for store in T.STORE_FILES:
        if store not in written:
            continue
        path = written[store]
        overlay = T.load_overlay(path.read_text(), path.name)
        lines.append(f"    {store:<14} {len(overlay)} key(s) -> {path}")

    icons = [n for n in written if n not in T.STORE_FILES]
    if icons:
        lines.append(f"    {'Icons':<14} {len(icons)} file(s) copied verbatim "
                     f"-> {T.theme_icons_dir(name)}")
    return lines
