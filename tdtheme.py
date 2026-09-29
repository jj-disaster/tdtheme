"""Core library for TouchDesigner theme management.

Deliberately free of CLI concerns - no argparse, no print, no sys.exit. The public
functions return data and raise ThemeError subclasses, so the same code can back
a future GUI or an in-TouchDesigner Text DAT.

File formats (both reverse-engineered, both verified byte-exact):

  TouchColors   key <TAB> r <TAB> g <TAB> b <CRLF>
  TouchOptions  key <TAB> value <CRLF>

A parsed file keeps the *full list of fields after the key* rather than a
decoded RGB triple, which is what makes a byte-identical round-trip possible:
two shipped TouchColors lines carry an extra empty second field

  dialog.commenthint        <TAB> <TAB> 0.2 <TAB> 0.2 <TAB> 0.2
  dialog.commenthint.comp   <TAB> <TAB> 0.5 <TAB> 0.5 <TAB> 0.5

A parser reading "the last three fields" gets the colour right, but a
serializer writing back only RGB would drop the empty field.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import tdicons

__all__ = [
    "ThemeError", "FileFormatError", "ThemeNotFound", "ValidationError",
    "TdFile", "Finding", "Status", "IconFinding",
    "parse", "serialize", "load_file", "read_bytes", "write_file",
    "merge", "validate", "diff", "store_changes",
    "load_overlay",
    "root", "baseline_dir", "themes_dir", "backups_dir", "config_dir",
    "icons_dir", "baseline_icons_dir", "theme_icons_dir",
    "ui_tox_path", "baseline_ui_tox", "theme_ui_tox", "ui_tox_source",
    "td_version", "td_running",
    "list_themes", "require_theme", "theme_path",
    "capture", "apply", "status", "plan",
    "icons_available", "icon_diff", "validate_icons",
    "preview_icons",
]


# Errors -------------------------------------------------------------------

class ThemeError(Exception):
    """Base for every error this module raises."""


class FileFormatError(ThemeError):
    """A TouchColors/TouchOptions file could not be parsed."""


class ThemeNotFound(ThemeError):
    """The requested theme does not exist."""


class ValidationError(ThemeError):
    """A theme failed validation. Carries the offending findings."""

    def __init__(self, message: str, findings: "list[Finding]"):
        super().__init__(message)
        self.findings = findings


# Paths --------------------------------------------------------------------

TD_APP = Path("/Applications/TouchDesigner.app")
TD_CONFIG = TD_APP / "Contents/Resources/tfs/Config"

TOUCHCOLORS = "TouchColors"
TOUCHOPTIONS = "TouchOptions"
STORE_FILES = (TOUCHCOLORS, TOUCHOPTIONS)

#: Files whose values are RGB triples rather than opaque strings.
COLOR_FILES = frozenset({TOUCHCOLORS})

#: Name of the icon directory in the same config tree. Not a guess: see the
#: module docstring of tdicons.py for the `libUI.dylib` strings and the
#: `ICO_Manager::loadIcon` call site building `<ConfigDir>/Icons/<Name>.tiff`.
ICONS_DIRNAME = "Icons"

#: The subdirectory of the config tree that holds the UI layout file.
SYSTEM_DIRNAME = "System"

#: Everything about the UI that is not a colour or an option - dialog and window
#: geometry, column widths, open panes - lives in this one file, a `.tox`:
#: TouchDesigner's own binary project format, so no value in it can be edited by
#: this tool. It can still be *installed*: a theme ships one and `apply` copies it
#: over the top. Nothing parses, decodes or checks it. 1.1 MB of opaque bytes, with
#: no way to tell from outside whether TouchDesigner liked them, so a check could
#: only report that bytes arrived - what `write_file` already guarantees. Getting
#: *which* file is written right is the one thing that matters, since two themes
#: silently sharing one is the failure nobody would notice.
UI_TOX = "ui.tox"

#: The theme whose `ui.tox` is the fallback for every other theme. Fixed rather
#: than "first alphabetically", because `apply` on a theme that ships no UI file
#: has to mean the stock UI specifically - that is what makes a total overwrite
#: safe.
DEFAULT_THEME = "default"

root = Path(__file__).resolve().parent
baseline_dir = root / "baseline"
themes_dir = root / "themes"
backups_dir = root / "backups"


def config_dir() -> Path:
    """Where TouchDesigner actually keeps the two stores.

    Overridable via TDTHEME_CONFIG, which is how the test-suite avoids the
    real install.
    """
    return Path(os.environ.get("TDTHEME_CONFIG", TD_CONFIG))


def icons_dir() -> Path:
    """Where TouchDesigner keeps its 97 UI icon TIFFs."""
    return config_dir() / ICONS_DIRNAME


def baseline_icons_dir() -> Path:
    return baseline_dir / ICONS_DIRNAME


def theme_icons_dir(name: str) -> Path:
    return themes_dir / name / ICONS_DIRNAME


def ui_tox_path() -> Path:
    """The `ui.tox` in the live install."""
    return config_dir() / SYSTEM_DIRNAME / UI_TOX


def baseline_ui_tox() -> Path:
    return baseline_dir / SYSTEM_DIRNAME / UI_TOX


def theme_ui_tox(name: str) -> Path:
    return themes_dir / name / UI_TOX


def ui_tox_source(name: str) -> "Path | None":
    """The `ui.tox` a theme installs, or None when there is nothing to install.

    A theme shipping no `ui.tox` falls back to `default`'s, the pristine one.
    That fallback is the point, not a convenience: writing nothing leaves the
    install holding the *previous* theme's dialogs while `tdtheme list` reports
    the new one, so "no opinion about the UI" has to resolve to the stock UI. A
    theme that does ship one always wins over the fallback, `default` included:
    its file is a copy of the stock install, not a reference to the baseline.
    """
    own = theme_ui_tox(name)
    if own.is_file():
        return own
    fallback = theme_ui_tox(DEFAULT_THEME)
    return fallback if fallback.is_file() else None


# Parsing / serialising ----------------------------------------------------

def _split_lines(text: str) -> "tuple[list[str], str, bool]":
    """Split into (lines, terminator, had_trailing_newline)."""
    if "\r\n" in text:
        terminator = "\r\n"
    elif "\n" in text:
        terminator = "\n"
    else:
        return ([text] if text else []), "", False
    lines = text.split(terminator)
    trailing = bool(lines) and lines[-1] == ""
    if trailing:
        lines.pop()
    return lines, terminator, trailing


def parse(raw: bytes) -> "OrderedDict[str, list[str]]":
    """Parse a TouchColors/TouchOptions file into key -> list-of-fields.

    A blank line is preserved as the empty key ``""`` so it round-trips; neither
    shipped file has one, but dropping a line is exactly the unrequested change
    this module exists to prevent.
    """
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise FileFormatError(f"not ASCII: {exc}") from exc

    lines, _, _ = _split_lines(text)
    data: "OrderedDict[str, list[str]]" = OrderedDict()
    for lineno, line in enumerate(lines, 1):
        parts = line.split("\t")
        key = parts[0]
        if key in data:
            raise FileFormatError(f"duplicate key {key!r} on line {lineno}")
        data[key] = parts[1:]
    return data


def serialize(data: "OrderedDict[str, list[str]]", *, terminator: str = "\r\n",
              trailing_newline: bool = True) -> bytes:
    """Inverse of parse(). Byte-identical for any file parse() accepted."""
    body = terminator.join("\t".join([key] + list(value)) for key, value in data.items())
    if trailing_newline:
        body += terminator
    return body.encode("ascii")


@dataclass
class TdFile:
    """A parsed store, carrying the formatting metadata needed to rewrite it."""

    name: str
    data: "OrderedDict[str, list[str]]" = field(default_factory=OrderedDict)
    terminator: str = "\r\n"
    trailing_newline: bool = True

    @classmethod
    def parse(cls, raw: bytes, name: str = "") -> "TdFile":
        text = raw.decode("ascii", errors="strict")
        _, terminator, trailing = _split_lines(text)
        return cls(name=name, data=parse(raw), terminator=terminator,
                   trailing_newline=trailing)

    def to_bytes(self) -> bytes:
        return serialize(self.data, terminator=self.terminator,
                         trailing_newline=self.trailing_newline)

    def copy(self) -> "TdFile":
        return TdFile(self.name, OrderedDict(self.data), self.terminator,
                      self.trailing_newline)

    def __contains__(self, key: str) -> bool:
        return key in self.data

    def __len__(self) -> int:
        return len(self.data)

    def keys(self):
        return self.data.keys()

    def get(self, key: str) -> "list[str] | None":
        return self.data.get(key)

    def rgb(self, key: str) -> "tuple[float, float, float] | None":
        """Last three fields as floats, or None if not numeric."""
        value = self.data.get(key)
        if value is None or len(value) < 3:
            return None
        try:
            return tuple(float(v) for v in value[-3:])  # type: ignore[return-value]
        except ValueError:
            return None


def read_bytes(path: Path) -> bytes:
    try:
        return Path(path).read_bytes()
    except FileNotFoundError as exc:
        raise ThemeError(f"missing file: {path}") from exc


def load_file(path: Path, name: str = "") -> TdFile:
    path = Path(path)
    return TdFile.parse(read_bytes(path), name=name or path.name)


def write_file(path: Path, raw: bytes) -> None:
    """Write atomically: temp file in the same dir, then rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    try:
        tmp.write_bytes(raw)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


# Overlay (sparse theme) format --------------------------------------------
#
# A deliberately restricted subset of YAML, so both loaders agree: PyYAML when
# importable (TouchDesigner ships 6.0.3), otherwise a minimal loader handling
# exactly the subset emitted below. No third-party dependencies either way.
#
#   # comment line
#   key: ["1", "0", "0"]      <- colour stores (always a list)
#   tile.border.size: "5"     <- option stores (a bare quoted scalar)

def _have_yaml() -> bool:
    try:
        import yaml  # noqa: F401
    except ImportError:
        return False
    return True


def load_overlay(text: str, name: str = "") -> "OrderedDict[str, list[str]]":
    """Parse restricted-YAML overlay text. Accepts bare scalars as 1-field lists."""
    try:
        import yaml
    except ImportError:
        return _load_overlay_fallback(text, name)

    try:
        loaded = yaml.safe_load(text) or {}
    except Exception as exc:  # yaml raises many types
        raise FileFormatError(f"{name or 'overlay'}: {exc}") from exc

    if not isinstance(loaded, dict):
        raise FileFormatError(f"{name or 'overlay'}: expected a mapping")

    data: "OrderedDict[str, list[str]]" = OrderedDict()
    for key, value in loaded.items():
        data[str(key)] = _coerce_overlay_value(str(key), value, name)
    return data


def _coerce_overlay_value(key: str, value, where: str) -> "list[str]":
    if value is None:
        raise FileFormatError(
            f"{where or 'overlay'}: key {key!r} is null. Deleting keys is not "
            f"supported in v1 - remove the line instead."
        )
    if isinstance(value, str):
        return [value]
    if isinstance(value, bool):
        return ["true" if value else "false"]
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, (list, tuple)):
        return [("" if v is None else str(v)) for v in value]
    raise FileFormatError(
        f"{where or 'overlay'}: key {key!r} has unsupported value type "
        f"{type(value).__name__}"
    )


#: The YAML indicator characters that begin a construct the restricted loader
#: cannot replicate. Refused rather than kept as literal text, so the fallback
#: and a PyYAML-backed run can never disagree about what a value means.
_YAML_ONLY_INDICATORS = frozenset("{&*!|>%@`")

def _load_overlay_fallback(text: str, name: str) -> "OrderedDict[str, list[str]]":
    """Zero-dependency loader for the restricted subset a theme overlay uses."""
    data: "OrderedDict[str, list[str]]" = OrderedDict()
    for lineno, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise FileFormatError(
                f"{name or 'overlay'}: line {lineno} is not 'key: value': {raw_line!r}"
            )
        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()
        if not rest:
            # Matches the message the PyYAML path gives for an explicit null,
            # so the guidance is identical whichever loader is in use.
            raise FileFormatError(
                f"{name or 'overlay'}: key {key!r} has no value. Deleting keys "
                f"is not supported in v1 - remove the line instead."
            )
        if rest.startswith("["):
            if "'" in rest:
                raise FileFormatError(
                    f"{name or 'overlay'}: line {lineno} list is single-quoted: "
                    f"{rest!r}. This loader reads a bare or double-quoted list, "
                    f"so the quotes would have to be written into the store. "
                    f'Use ["a", "b"] or [a, b] instead.'
                )
            try:
                value = json.loads(rest)
            except json.JSONDecodeError as exc:
                # `json`'s own message ("Expecting value: line 1 column 2") is
                # accurate and useless: the usual cause is a bare item, `[a, b]`,
                # valid YAML but not valid JSON. Say what to write instead.
                raise FileFormatError(
                    f"{name or 'overlay'}: line {lineno} could not read the "
                    f"list {rest!r}: {exc}. List items must be quoted - write "
                    f'["a", "b"], not [a, b].'
                ) from exc
        elif rest.startswith("'"):
            # A YAML single-quoted scalar, which this loader cannot unquote.
            # Keeping the quotes writes them into the store, so `origsize: '11'`
            # would install the three characters '11' where PyYAML installs 11 -
            # the same file behaving differently per interpreter. Refuse it and
            # name the two forms both loaders agree on. A value merely
            # *containing* an apostrophe is fine: only a leading quote is ambiguous.
            inner = rest[1:-1] if rest.endswith("'") and len(rest) > 1 else rest[1:]
            raise FileFormatError(
                f"{name or 'overlay'}: line {lineno} value is single-quoted: "
                f"{rest!r}. This loader accepts a bare value or a "
                f'double-quoted string, and cannot strip single quotes - '
                f'write {inner!r} or "{inner}" instead.'
            )
        elif rest[0] in _YAML_ONLY_INDICATORS:
            # Constructs the restricted loader cannot replicate, so each is a place
            # the two loaders could silently return different data for one file -
            # kept here as literal text where PyYAML would resolve anchors, tags
            # and block scalars instead. `kind` below names them. Refusing is the
            # point: a theme must install the same bytes whichever interpreter
            # reads it, so an ambiguous value is an error rather than a guess. Which
            # construct it is barely matters - the value is not one this tool
            # documents, and the message says so.
            kind = {"{": "a flow mapping", "&": "an anchor", "*": "an alias",
                    "!": "a tag", "|": "a block scalar", ">": "a folded scalar",
                    "%": "a directive", "@": "a reserved indicator",
                    "`": "a reserved indicator"}[rest[0]]
            raise FileFormatError(
                f"{name or 'overlay'}: line {lineno} value starts with "
                f"{rest[0]!r}, which is {kind}: {rest!r}. This tool reads a "
                f"restricted subset, not general YAML, and would keep the "
                f"characters as literal text. Use a bare value, a "
                f'double-quoted string, or a list like ["a", "b"].'
            )
        else:
            try:
                value = json.loads(rest)
            except json.JSONDecodeError:
                value = rest
        data[key] = _coerce_overlay_value(key, value, name)
    return data


# Merge / diff -------------------------------------------------------------

def merge(base: TdFile, overlay: "OrderedDict[str, list[str]]") -> TdFile:
    """Apply a sparse overlay onto a full file. Returns a new TdFile."""
    result = base.copy()
    for key, value in overlay.items():
        result.data[key] = list(value)
    return result


def diff(base: TdFile, other: TdFile) -> "OrderedDict[str, list[str]]":
    """Keys in `other` that differ from `base` (added or changed)."""
    out: "OrderedDict[str, list[str]]" = OrderedDict()
    for key, value in other.data.items():
        if key not in base.data or base.data[key] != value:
            out[key] = list(value)
    return out


# Validation ---------------------------------------------------------------

#: Any key that looks like a physical size.
SIZE_KEY_RE = re.compile(r"(?:\.size|\.origsize)$")

#: Tile geometry specifically. A zero here silently destroys layout:
#: `tile.inout.origsize 0` collapsed both the connector and the top border with
#: no error from TouchDesigner. Hard errors.
TILE_GEOMETRY_RE = re.compile(r"^tile\..*\.(?:size|origsize)$")


@dataclass
class Finding:
    severity: str  # "error" or "warning"
    key: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.key}: {self.message}"


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _check_color_fields(target: TdFile, baseline: TdFile) -> "list[Finding]":
    """Catch a colour value whose fields no longer match the shipped shape.

    Motivated by real corruption: a hand-edit wrote `worksheet.grid` as
    `0.317 0.189 <TAB> 0.15` - two channels merged into one field by a space where
    a tab belonged. `parse` accepts it, `serialize` reproduces it, and
    TouchDesigner cannot read the result, so the bad value survived a write and
    `validate` reported nothing. Nothing downstream checks field integrity.

    Every rule is relative to the baseline: an absolute "exactly three numeric
    fields" would false-positive the two shipped keys with a stray empty leading
    field (`dialog.commenthint`, `dialog.commenthint.comp`) and break again if a
    future build changed the shape. Same lesson as the size rule below - a rule
    that fires on the untouched shipped file is worse than no rule, because it
    fails every apply and trains the user towards `--force`.
    """
    findings: "list[Finding]" = []
    for key, value in target.data.items():
        if not key:
            continue
        reference = baseline.data.get(key)
        if reference is None:
            continue  # unknown key; the baseline-membership check warns already
        if len(value) != len(reference):
            findings.append(Finding(
                "error", key,
                f"has {len(value)} field(s) but the baseline ships "
                f"{len(reference)} ({reference!r}). Fields are tab-separated, "
                f"so a stray space merges two of them and TouchDesigner reads "
                f"the file as corrupt."
            ))
            continue
        for index, text in enumerate(value):
            if any(character.isspace() for character in text):
                findings.append(Finding(
                    "error", key,
                    f"field {index + 1} contains whitespace: {text!r}. A colour "
                    f"field is one number; two numbers in one field means a "
                    f"space was typed where a tab belonged."
                ))
            elif text and not _is_number(text):
                if _is_number(reference[index]):
                    findings.append(Finding(
                        "error", key,
                        f"field {index + 1} is not numeric: {text!r} "
                        f"(baseline has {reference[index]!r})"
                    ))
    return findings


def validate(target: TdFile, baseline: TdFile) -> "list[Finding]":
    """Check a fully-merged file against the baseline. Errors block the write.

    `baseline` is required, and every rule is relative to it: a key is judged
    by what the baseline ships. It used to default to None, which quietly
    disabled the colour-arity check - the strictest rule in the file - for any
    caller who forgot it. Making it required turns that silence into a
    TypeError.
    """
    findings: "list[Finding]" = []
    keys = set(target.data)
    keys.discard("")  # blank line, not a real key

    if target.name in COLOR_FILES:
        findings += _check_color_fields(target, baseline)

    # Only the option store has size keys; a colour store has no geometry.
    # Deliberately asymmetric: tile geometry is a hard error at zero, a bug class
    # we hit for real, while every other `.size` key is judged only *relative to
    # the baseline*, because the shipped `font.relative.size` is legitimately 0: a
    # delta from the base font size, not an absolute size. An unconditional "> 0"
    # rule would false-positive the untouched shipped file.
    if target.name not in COLOR_FILES:
        baseline_values = baseline.data
        for key, value in target.data.items():
            if not key or not SIZE_KEY_RE.search(key) or not value:
                continue
            try:
                number = float(value[0])
            except ValueError:
                # An error, not a warning: a `.size` that will not parse is
                # always a mistake, and it is the shape a mis-quoted value
                # takes - `origsize: '11'` reaches here as three characters
                # '11'. As a warning it sailed through `apply`, which gates on
                # errors only, and wrote the quotes into the install.
                findings.append(Finding(
                    "error", key,
                    f"size is not numeric: {value[0]!r}. If the value is "
                    f"quoted, write it bare or double-quoted - this tool does "
                    f"not accept single-quoted values, and they would be "
                    f"written through with their quotes intact"))
                continue
            if number > 0:
                continue
            if TILE_GEOMETRY_RE.search(key):
                findings.append(Finding(
                    "error", key,
                    f"size must be > 0, got {number:g}. This silently destroys "
                    f"layout (see tile.inout.origsize)."
                ))
            elif baseline_values.get(key) != value:
                findings.append(Finding(
                    "warning", key,
                    f"size is {number:g}, and the baseline value for this key is "
                    f"not zero - this is likely to break layout"
                ))

    known = set(baseline.data)
    for key in target.data:
        if key and key not in known:
            findings.append(Finding(
                "warning", key,
                "not present in baseline - typo, or added by a newer TouchDesigner"
            ))

    # Unresolved: when a theme sets both X and default.X we do not know
    # which wins, so say so rather than guessing.
    for key in target.data:
        if key.startswith("default."):
            specific = key[len("default."):]
            if specific in keys:
                findings.append(Finding(
                    "warning", key,
                    f"theme sets both {specific!r} and {key!r}; the precedence "
                    f"between the two tiers is unverified"
                ))
    return findings


# TouchDesigner environment ------------------------------------------------

def td_version() -> "str | None":
    """CFBundleShortVersionString, e.g. '2025.33230'."""
    plist = TD_APP / "Contents" / "Info.plist"
    try:
        with plist.open("rb") as handle:
            return plistlib.load(handle).get("CFBundleShortVersionString")
    except (OSError, ValueError):
        return None


def td_running() -> "list[int]":
    """PIDs of running TouchDesigner processes. Empty when closed."""
    try:
        result = subprocess.run(["pgrep", "-x", "TouchDesigner"],
                                capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for token in result.stdout.split():
        if token.isdigit():
            pids.append(int(token))
    return pids


# High-level operations ----------------------------------------------------

def _baseline_paths() -> "dict[str, Path]":
    """The baseline store files, and only those.

    Deliberately not the icon directory: `load_baseline` parses every entry here
    as a TdFile, so `Icons` would be read as a text store. The icon set is
    tracked separately, by `baseline_icons_dir()`.
    """
    return {name: baseline_dir / name for name in STORE_FILES}


def _version_path() -> Path:
    return baseline_dir / "version.json"


def _applied_path() -> Path:
    return root / ".applied.json"


def record_applied(name: str) -> None:
    write_file(_applied_path(), json.dumps({
        "theme": name,
        "applied": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, indent=2).encode() + b"\n")


def applied_theme() -> "str | None":
    path = _applied_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text()).get("theme")
    except (OSError, ValueError):
        return None


def load_baseline() -> "dict[str, TdFile]":
    paths = _baseline_paths()
    missing = [n for n, p in paths.items() if not p.exists()]
    if missing:
        raise ThemeError(
            f"baseline incomplete, missing {missing}. Run `tdtheme capture` first."
        )
    return {name: load_file(path, name) for name, path in paths.items()}


def baseline_version() -> "dict":
    path = _version_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def capture(*, force: bool = False) -> "dict[str, Path]":
    """Snapshot the installed stores as the baseline. Refuses to clobber."""
    paths = _baseline_paths()
    existing = [n for n, p in paths.items() if p.exists()]
    # The icon set is part of the baseline too, so the guard names it. Otherwise
    # the refusal reads "baseline already exists (TouchColors, TouchOptions)" and
    # implies the 97 icons are not at stake, when re-capturing replaces all of them
    # and every theme's `icons diff` is computed against the result. `ui.tox` is in
    # the same position.
    icons_present = baseline_icons_dir().is_dir()
    ui_tox_present = baseline_ui_tox().is_file()
    if (existing or icons_present or ui_tox_present) and not force:
        held = list(existing)
        if icons_present:
            held.append(ICONS_DIRNAME)
        if ui_tox_present:
            held.append(f"{SYSTEM_DIRNAME}/{UI_TOX}")
        raise ThemeError(
            f"baseline already exists ({', '.join(held)}). Re-capturing changes "
            f"what every existing theme diffs against. Use --force if that is intended."
        )

    cfg = config_dir()
    written: "dict[str, Path]" = {}
    baseline_dir.mkdir(parents=True, exist_ok=True)
    for name in STORE_FILES:
        source = cfg / name
        raw = read_bytes(source)
        # Parse first: refuse to baseline a file we cannot round-trip.
        parsed = TdFile.parse(raw, name)
        if parsed.to_bytes() != raw:
            raise FileFormatError(f"{source} does not round-trip; refusing to baseline")
        destination = baseline_dir / name
        write_file(destination, raw)
        written[name] = destination

    # Icons are captured as a side effect rather than a gate: a missing or
    # unreadable icon directory must not stop someone baselining their colours.
    # The test is on the *install* only - `icons_available()` would be circular,
    # since it needs the baseline directory this line is about to create, so the
    # very first capture would silently skip icons and no later one would fix it.
    if icons_dir().is_dir():
        written.update(tdicons.capture_icons(icons_dir(), baseline_icons_dir()))

    # Verbatim, like the icons: the point is to be the file TouchDesigner shipped,
    # so a hand-edited ui.tox in the install is captured as-is and stays
    # distinguishable from it by hash. Nothing reads this copy - the fallback for a
    # theme without a ui.tox is `themes/default/ui.tox`, not the baseline, which is
    # a reference rather than something to install. It exists so `capture` does not
    # quietly lose the file and a reinstall can be compared against it.
    if ui_tox_path().is_file():
        baseline_ui_tox().parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ui_tox_path(), baseline_ui_tox())
        written[UI_TOX] = baseline_ui_tox()

    write_file(_version_path(), json.dumps({
        "td_build": td_version(),
        "captured": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "icons": len(tdicons.icon_names(baseline_icons_dir())),
    }, indent=2).encode() + b"\n")
    return written


def theme_path(name: str, store: str) -> Path:
    return themes_dir / name / f"{store}.yaml"


def list_themes() -> "list[str]":
    if not themes_dir.exists():
        return []
    # A theme is a directory that ships at least one thing this tool installs.
    # `ui.tox` counts: a theme that themes nothing but the UI is a legitimate
    # thing to want, and a directory holding only one would otherwise be
    # invisible to `list`, `diff` and `require_theme` alike.
    return sorted(p.name for p in themes_dir.iterdir()
                  if p.is_dir() and (any(p.glob("*.yaml")) or (p / ICONS_DIRNAME).is_dir()
                                     or (p / UI_TOX).is_file()))


def require_theme(name: str) -> None:
    """Raise `ThemeNotFound` unless `name` is a defined theme.

    The icon commands need this separately from `_load_theme`: a theme may
    legitimately have no `Icons/`, and "does not theme icons" is a real,
    reportable state. Without the check `icons diff` and `icons preview` cannot
    tell that from a typo and quietly answer about the wrong thing - `icons
    preview` once wrote a baseline contact sheet under the mistyped name.
    """
    if name in list_themes():
        return
    available = list_themes()
    raise ThemeNotFound(
        f"no theme {name!r}" + (f". Available: {', '.join(available)}"
                                 if available else ". No themes defined yet.")
    )


def _load_theme(name: str) -> "dict[str, OrderedDict]":
    require_theme(name)
    overlays = {}
    for store in STORE_FILES:
        path = theme_path(name, store)
        overlays[store] = load_overlay(path.read_text(), path.name) if path.exists() \
            else OrderedDict()
    return overlays


# Icons --------------------------------------------------------------------
#
# An optional third surface alongside the two text stores. Every entry point
# degrades to a no-op when the icon directory is absent, so a build without one -
# or a test fixture that only seeds the stores - behaves as it did before icons
# existed.

def icons_available() -> bool:
    return icons_dir().is_dir() and baseline_icons_dir().is_dir()


@dataclass
class IconFinding:
    """A problem with a theme's icon set. `severity` mirrors `Finding`."""

    severity: str  # "error" or "warning"
    name: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.name}: {self.message}"


def icon_diff(name: str) -> "dict[str, str]":
    """How a theme's icon set differs from the baseline, by sha256.

    Empty dict means the theme has no icon directory - "does not theme icons",
    not "icons match". An unknown theme name raises instead: an empty dict is a
    claim about a real theme.
    """
    require_theme(name)
    theme = theme_icons_dir(name)
    if not theme.is_dir():
        return {}
    return tdicons.diff_icons(baseline_icons_dir(), theme)


def validate_icons(name: str) -> "list[IconFinding]":
    """Check a theme's icon set before anything is written into the bundle.

    Three failure modes worth catching here rather than as a missing glyph: one
    that will not decode, leaving TouchDesigner unable to read it at all (it logs
    "Couldn't find icon" and draws nothing); one whose dimensions changed, since
    TouchDesigner sizes most of these glyphs from the file and a resized one is
    visibly wrong; and one in the baseline but absent from the theme, which now
    costs nothing because it is filled in from the baseline - but still usually
    means a theme generated from a stale baseline, or assembled by hand and
    interrupted. An unknown theme name raises rather than validating nothing,
    which would read as a clean bill of health.
    """
    require_theme(name)
    findings: "list[IconFinding]" = []
    theme = theme_icons_dir(name)
    if not theme.is_dir():
        return findings

    baseline = baseline_icons_dir()
    names = tdicons.icon_names(theme)
    if not names:
        findings.append(IconFinding(
            "error", ICONS_DIRNAME,
            f"{theme} contains no .tiff files. Applying it would be a no-op; "
            f"delete the directory or rebuild it."))
        return findings

    for icon_name in names:
        path = theme / icon_name
        try:
            image = tdicons.read_tiff(path.read_bytes())
        except tdicons.IconError as exc:
            findings.append(IconFinding("error", icon_name, f"unreadable: {exc}"))
            continue
        reference = baseline / icon_name
        if reference.exists():
            try:
                original = tdicons.read_tiff(reference.read_bytes())
            except tdicons.IconError:
                continue  # baseline problem, not a theme problem
            if original.size != image.size:
                findings.append(IconFinding(
                    "error", icon_name,
                    f"is {image.width}x{image.height} but the baseline ships "
                    f"{original.width}x{original.height}. TouchDesigner sizes "
                    f"these glyphs from the file, so a resized icon renders wrong."))
        else:
            findings.append(IconFinding(
                "warning", icon_name,
                "not in the baseline - a new name, or from a different build. "
                "It will be written, but nothing here can vouch for it."))

    # One finding for the whole shortfall, not one per icon: 94 identical lines
    # is a count wearing 94 costumes, and it pushed the real findings off the
    # screen.
    baseline_names = tdicons.icon_names(baseline)
    missing = [name for name in baseline_names if name not in names]
    if missing:
        findings.append(IconFinding(
            "warning", ICONS_DIRNAME,
            f"{len(names)} written, "
            f"the other {len(missing)} are from baseline."))
    return findings


def _icons_source_dir(name):
    """The icon directory a preview should read.

    `name=None` means the baseline, and it is not a cosmetic special case: the
    baseline is what every theme is diffed against, so it is the only preview
    that can answer "did this theme change the icon I think it changed, or was
    that glyph already like this". A *named* theme never falls back. It used to,
    which was worse than a crash: `preview_icons("typo")` rendered the baseline
    and wrote it to `typo-icons.png`, so the name asserted something the contents
    did not.
    """
    if name:
        source = theme_icons_dir(name)
        if not source.is_dir():
            raise ThemeError(
                f"theme {name!r} has no {ICONS_DIRNAME}/ directory to preview. "
                f"Generate one with tdthememaker: "
                f"`python3 -m tdthememaker.cli build {name}`."
            )
        return source
    baseline = baseline_icons_dir()
    if not baseline.is_dir():
        raise ThemeError("no baseline Icons/ to preview. Run "
                         "`tdtheme capture --force` first.")
    return baseline


def preview_icons(name, path=None, *, columns: int = 10, cell: int = 72):
    """Write a PNG contact sheet of a theme's icons. Returns the path written.

    Preview only - the install gets TIFFs. It exists because the point of
    regenerating 97 glyphs is that somebody has to be able to look at them.
    `name=None` previews the baseline. The theme name is validated here rather
    than trusted from the caller, because the output file is named after it: a
    name that does not resolve must never produce a picture.
    """
    if name is not None:
        require_theme(name)
    source = _icons_source_dir(name)
    images = []
    for icon_name in tdicons.icon_names(source):
        images.append((icon_name, tdicons.read_tiff((source / icon_name).read_bytes())))
    if not images:
        raise ThemeError(f"no icons in {source}")
    sheet = tdicons.contact_sheet(images, columns=columns, cell=cell)
    label = name if name else "baseline"
    destination = Path(path) if path else root / "testiconsforagents" / f"{label}-icons.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_file(destination, tdicons.png_bytes(sheet))
    return destination


def _apply_icon_set(name: str, backup_dir) -> dict:
    """Write a theme's icons into the install, backing up what it replaces.

    Anything the theme does not ship is filled from the baseline, not left as
    the previously applied theme left it, so the install always holds a complete
    set and never a mixture of two themes while `status` reports one.
    `backup_dir` is `None` when the caller did not ask for a backup, and
    `tdicons.copy_icons` reads `backup=None` as "back up nothing", so the
    no-backup path needs no branch here.
    """
    theme = theme_icons_dir(name)
    if not theme.is_dir():
        return {"applied": False, "reason": f"{name} has no icon directory",
                "written": [], "unchanged": [], "filled": [], "backed_up": 0}
    result = tdicons.copy_icons(theme, icons_dir(),
                                backup=None if backup_dir is None
                                else backup_dir / ICONS_DIRNAME,
                                only_changed_against=baseline_icons_dir(),
                                fill_from=baseline_icons_dir())
    result["applied"] = True
    return result


def _apply_ui_tox(name: str) -> dict:
    """Install a theme's `ui.tox`, or `default`'s, over the one in the install.

    Not backed up even under `--backup`, deliberately: 1.1 MB, and the only thing
    that ever writes it is `apply` itself - TouchDesigner reads it and never writes
    it, and a manual export lands wherever the user saved it, not over the
    install's copy. A backup here is the outgoing theme's file, already in git in
    that theme's own folder, once per apply, of bytes this repository has. The
    stores and icons are backed up only on request, for the same "already in git"
    reason; the size makes this the strongest case rather than a merely consistent
    one, since a store pair is 32 KB against 1.1 MB, so `--backup` is cheap there
    and expensive here.

    The write goes through `write_file`, so it is atomic and creates
    `Config/System/` if absent. Overwriting unconditionally is the whole design:
    the previous theme's UI must not survive a switch.
    """
    source = ui_tox_source(name)
    if source is None:
        return {"applied": False,
                "reason": f"neither this theme nor {DEFAULT_THEME!r} has a {UI_TOX}"}
    write_file(ui_tox_path(), source.read_bytes())
    return {"applied": True, "source": source, "path": ui_tox_path(),
            "from_default": source != theme_ui_tox(name)}


def store_changes(before: "TdFile", after: "TdFile") -> "dict":
    """What writing `after` over `before` did, as data rather than bytes.

    Two directions, because `diff` only reports keys present in `after`: an
    overlay may add a key the baseline lacks - which `diff` counts as a change -
    so a theme that adds one and a later theme that does not would drop it
    silently. Same class of leak the icon fill-in prevents, and it deserves to be
    counted rather than assumed away. `before` is the file as installed, not the
    baseline: an apply reports what *it* changed, so a store the user drifted by
    hand shows up as being corrected, which is the truth about the write that just
    happened.
    """
    changed = diff(before, after)
    removed = [key for key in before.data if key not in after.data]
    return {"changed": changed, "removed": removed}


def plan(name: str) -> "dict[str, TdFile]":
    """Merge theme over baseline and validate, without touching the install."""
    baseline = load_baseline()
    overlays = _load_theme(name)
    merged, findings = {}, []
    for store in STORE_FILES:
        merged[store] = merge(baseline[store], overlays[store])
        findings += validate(merged[store], baseline[store])
    return {"files": merged, "findings": findings, "icon_findings": validate_icons(name)}


def apply(name: str, *, force: bool = False, icons: bool = True,
          backup: bool = False) -> dict:
    """Merge, validate, and write. Refuses on validation errors.

    `backup` copies the outgoing stores and icons into `backups/<timestamp>/`
    first. Off by default because the install is reconstructible without it: the
    stores only ever hold `merge(baseline, theme)` and the icon set only ever
    holds a theme's set completed from the baseline, so both inputs are in git
    and `apply <the previous theme>` reproduces the outgoing bytes exactly. What a
    backup adds is cover for the one input git lacks - a theme edited on disk and
    not committed - and it is not free: a set runs 36 KB to 464 KB depending on
    how much the outgoing theme had changed, and nothing ever pruned `backups/`.
    Hence a flag, and `result["backup"]` is `None` when it is off.
    """
    result = plan(name)
    findings = result["findings"]
    icon_findings = result["icon_findings"]
    errors = [f for f in findings if f.severity == "error"]
    errors += [f for f in icon_findings if f.severity == "error"]
    if errors and not force:
        raise ValidationError(
            f"theme {name!r} has {len(errors)} validation error(s); nothing written",
            findings + icon_findings,
        )

    cfg = config_dir()
    running = td_running()
    # TouchDesigner reads both stores at startup, so a running instance shows the
    # old appearance until restart. A display lag, not data loss: no evidence
    # TouchDesigner ever writes these back. See README.
    warnings = []
    if running:
        warnings.append(
            f"TouchDesigner is running (pid {', '.join(map(str, running))}). "
            f"Restart to see changes"
        )

    changes = {}
    backup_dir = None
    if backup:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup_dir = backups_dir / stamp
        backup_dir.mkdir(parents=True, exist_ok=True)
        for store in STORE_FILES:
            installed = cfg / store
            if installed.exists():
                shutil.copy2(installed, backup_dir / store)

    # Read what is about to be overwritten: the report is about what this apply
    # changed, not what the overlay contains (that is `tdtheme diff`). Both reads
    # precede both writes, so the comparison is against the pre-apply state even
    # if a write fails later.
    for store in STORE_FILES:
        installed = cfg / store
        if installed.exists():
            changes[store] = store_changes(load_file(installed),
                                           result["files"][store])

    for store in STORE_FILES:
        write_file(cfg / store, result["files"][store].to_bytes())

    icon_result = {"applied": False, "reason": "skipped (--no-icons)",
                   "written": [], "unchanged": [], "backed_up": 0}
    if icons and icons_available():
        icon_result = _apply_icon_set(name, backup_dir)
    elif icons:
        icon_result["reason"] = f"no icon directory at {icons_dir()}"

    # Always, and regardless of --no-icons: that flag is about the icon set, and a
    # theme switch that skipped the UI would leave the previous theme's dialog
    # sizes on screen while reporting the new theme.
    ui_result = _apply_ui_tox(name)

    record_applied(name)

    return {
        "theme": name,
        "files": list(STORE_FILES),
        "backup": backup_dir,
        "changes": changes,
        "findings": findings,
        "icon_findings": icon_findings,
        "icons": icon_result,
        "ui_tox": ui_result,
        "warnings": warnings,
        "td_running": running,
    }


def status() -> Status:
    """Everything `tdtheme status` needs, as data."""
    baseline_present = _baseline_paths()[TOUCHCOLORS].exists()
    version = baseline_version()
    live = td_version()

    drift: "dict[str, int]" = {}
    missing: "list[str]" = []
    if baseline_present:
        try:
            baseline = load_baseline()
            cfg = config_dir()
            for store in STORE_FILES:
                path = cfg / store
                if not path.exists():
                    missing.append(store)
                    continue
                drift[store] = len(diff(baseline[store], load_file(path, store)))
        except ThemeError:
            baseline_present = False

    # Icons drift as a set, not a count of changed keys, but "how many files
    # differ" is the same idea, keyed by directory name.
    icon_drift: "dict[str, int]" = {}
    icons_installed: "list[str]" = []
    if baseline_icons_dir().is_dir():
        installed = icons_dir()
        icons_installed = tdicons.icon_names(installed)
        if not icons_installed:
            missing.append(ICONS_DIRNAME)
        else:
            changed = tdicons.diff_icons(baseline_icons_dir(), installed)
            icon_drift[ICONS_DIRNAME] = sum(
                1 for state in changed.values() if state == "changed")

    return Status(
        td_version=live,
        baseline_version=version.get("td_build"),
        baseline_captured=version.get("captured"),
        baseline_present=baseline_present,
        version_match=(baseline_present and version.get("td_build") == live),
        td_running=td_running(),
        config_dir=config_dir(),
        themes=list_themes(),
        drift=drift,
        missing=missing,
        applied=applied_theme(),
        icons_dir=icons_dir(),
        icon_count=len(icons_installed),
        icon_drift=icon_drift,
        icon_theme_drift={n: len(icon_diff(n)) for n in list_themes()
                          if theme_icons_dir(n).is_dir()},
    )


@dataclass
class Status:
    td_version: "str | None"
    baseline_version: "str | None"
    baseline_captured: "str | None"
    baseline_present: bool
    version_match: bool
    td_running: "list[int]"
    config_dir: Path
    themes: "list[str]"
    drift: "dict[str, int]"
    missing: "list[str]"
    applied: "str | None" = None
    icons_dir: "Path | None" = None
    icon_count: int = 0
    icon_drift: "dict[str, int]" = field(default_factory=dict)
    icon_theme_drift: "dict[str, int]" = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        # `drift` is a dict that is non-empty even when every count is zero,
        # so test the values rather than the dict's truthiness.
        return (not self.missing
                and not any(self.drift.values())
                and not any(self.icon_drift.values()))
