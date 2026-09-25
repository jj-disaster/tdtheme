"""Core library for TouchDesigner theme management.

Deliberately free of CLI concerns: no argparse, no print, no sys.exit. The
public functions return data and raise ThemeError subclasses, so the same
code can back a future GUI or an in-TouchDesigner Text DAT.

File formats (both reverse-engineered, both verified byte-exact):

  TouchColors   key <TAB> r <TAB> g <TAB> b <CRLF>
  TouchOptions  key <TAB> value <CRLF>

The central design decision is that a parsed file keeps the *full list of
fields after the key* rather than a decoded RGB triple or scalar. That is
what makes a byte-identical round-trip possible, because two shipped
TouchColors lines carry an extra empty second field:

  dialog.commenthint        <TAB> <TAB> 0.2 <TAB> 0.2 <TAB> 0.2
  dialog.commenthint.comp   <TAB> <TAB> 0.5 <TAB> 0.5 <TAB> 0.5

A parser that reads "the last three fields" gets the colour right, but a
serializer that writes back only RGB would silently drop the empty field
and change the file. Storing whole field lists makes that impossible.
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

__all__ = [
    "ThemeError", "FileFormatError", "ThemeNotFound", "ValidationError",
    "TdFile", "Finding", "Status",
    "parse", "serialize", "load_file", "read_bytes", "write_file",
    "merge", "validate", "diff",
    "load_overlay", "dump_overlay",
    "root", "baseline_dir", "themes_dir", "backups_dir", "config_dir",
    "td_version", "td_running",
    "capture", "apply", "export", "status",
]


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

TD_APP = Path("/Applications/TouchDesigner.app")
TD_CONFIG = TD_APP / "Contents/Resources/tfs/Config"

TOUCHCOLORS = "TouchColors"
TOUCHOPTIONS = "TouchOptions"
STORE_FILES = (TOUCHCOLORS, TOUCHOPTIONS)

#: Files whose values are RGB triples rather than opaque strings.
COLOR_FILES = frozenset({TOUCHCOLORS})

root = Path(__file__).resolve().parent
baseline_dir = root / "baseline"
themes_dir = root / "themes"
backups_dir = root / "backups"


def config_dir() -> Path:
    """Where TouchDesigner actually keeps the two stores.

    Overridable via the TDTHEME_CONFIG environment variable, which is what
    the test-suite uses so it never touches the real install.
    """
    return Path(os.environ.get("TDTHEME_CONFIG", TD_CONFIG))


# --------------------------------------------------------------------------
# Parsing / serialising
# --------------------------------------------------------------------------

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

    A blank line is preserved rather than skipped, so it round-trips: it
    appears as the empty key ``""`` with an empty field list. Neither shipped
    file contains one, but silently dropping a line would be exactly the kind
    of unrequested change this module exists to prevent.
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

    def number(self, key: str) -> "float | None":
        value = self.data.get(key)
        if value is None or not value or not value[0]:
            return None
        try:
            return float(value[0])
        except ValueError:
            return None

    def set(self, key: str, value: "list[str] | str") -> None:
        self.data[key] = [value] if isinstance(value, str) else list(value)


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


# --------------------------------------------------------------------------
# Overlay (sparse theme) format
# --------------------------------------------------------------------------
#
# Emitted form is a deliberately restricted subset of YAML so that it parses
# identically with or without PyYAML:
#
#   # comment line
#   key: ["1", "0", "0"]      <- colour stores (always a list)
#   tile.border.size: "5"     <- option stores (a bare quoted scalar)
#
# PyYAML is used when importable (TouchDesigner ships 6.0.3); otherwise a
# minimal loader handles exactly the subset emitted above. The tool therefore
# has no third-party dependencies.

def _have_yaml() -> bool:
    try:
        import yaml  # noqa: F401
    except ImportError:
        return False
    return True


def dump_overlay(data: "OrderedDict[str, list[str]]", name: str = "") -> str:
    """Render a sparse overlay as restricted YAML.

    An overlay describes key overrides, not a file image, so the empty key
    (a blank line) is skipped rather than emitted as invalid YAML. Blank
    lines present in the baseline survive `apply` because merging starts
    from the baseline.
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


def _load_overlay_fallback(text: str, name: str) -> "OrderedDict[str, list[str]]":
    """Zero-dependency loader for the restricted subset dump_overlay emits."""
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
            try:
                value = json.loads(rest)
            except json.JSONDecodeError as exc:
                raise FileFormatError(
                    f"{name or 'overlay'}: line {lineno} bad list: {exc}"
                ) from exc
        else:
            try:
                value = json.loads(rest)
            except json.JSONDecodeError:
                value = rest
        data[key] = _coerce_overlay_value(key, value, name)
    return data


# --------------------------------------------------------------------------
# Merge / diff
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

#: Any key that looks like a physical size.
SIZE_KEY_RE = re.compile(r"(?:\.size|\.origsize)$")

#: Tile geometry specifically. A zero here silently destroys layout:
#: `tile.inout.origsize 0` collapsed both the connector and the top border,
#: with no error from TouchDesigner. These are hard errors.
TILE_GEOMETRY_RE = re.compile(r"^tile\..*\.(?:size|origsize)$")


@dataclass
class Finding:
    severity: str  # "error" or "warning"
    key: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.key}: {self.message}"


def validate(target: TdFile, baseline: "TdFile | None" = None) -> "list[Finding]":
    """Check a fully-merged file. Returns findings; errors block the write."""
    findings: "list[Finding]" = []
    keys = set(target.data)
    keys.discard("")  # blank line, not a real key

    # Only the option store has size keys; a colour store has no geometry.
    #
    # The rule is deliberately asymmetric. Tile geometry is a hard error at
    # zero, because that is a bug class we hit for real. Every other `.size`
    # key is judged only *relative to the baseline*, because the shipped
    # `font.relative.size` is legitimately 0 - it is a delta from the base
    # font size, not an absolute size. An unconditional "> 0" rule produces
    # a false positive on the untouched shipped file and would make every
    # single apply fail validation.
    if target.name not in COLOR_FILES:
        baseline_values = baseline.data if baseline is not None else None
        for key, value in target.data.items():
            if not key or not SIZE_KEY_RE.search(key) or not value:
                continue
            try:
                number = float(value[0])
            except ValueError:
                findings.append(Finding("warning", key,
                                        f"size is not numeric: {value[0]!r}"))
                continue
            if number > 0:
                continue
            if TILE_GEOMETRY_RE.search(key):
                findings.append(Finding(
                    "error", key,
                    f"size must be > 0, got {number:g}. This silently destroys "
                    f"layout (see tile.inout.origsize)."
                ))
            elif baseline_values is None or baseline_values.get(key) != value:
                findings.append(Finding(
                    "warning", key,
                    f"size is {number:g}, and the baseline value for this key is "
                    f"not zero - this is likely to break layout"
                ))

    if baseline is not None:
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


# --------------------------------------------------------------------------
# TouchDesigner environment
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# High-level operations
# --------------------------------------------------------------------------

def _baseline_paths() -> "dict[str, Path]":
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
    if existing and not force:
        raise ThemeError(
            f"baseline already exists ({', '.join(existing)}). Re-capturing changes "
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

    write_file(_version_path(), json.dumps({
        "td_build": td_version(),
        "captured": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, indent=2).encode() + b"\n")
    return written


def theme_path(name: str, store: str) -> Path:
    return themes_dir / name / f"{store}.yaml"


def list_themes() -> "list[str]":
    if not themes_dir.exists():
        return []
    return sorted(p.name for p in themes_dir.iterdir()
                  if p.is_dir() and any(p.glob("*.yaml")))


def _load_theme(name: str) -> "dict[str, OrderedDict]":
    if name not in list_themes():
        available = list_themes()
        raise ThemeNotFound(
            f"no theme {name!r}" + (f". Available: {', '.join(available)}"
                                     if available else ". No themes defined yet.")
        )
    overlays = {}
    for store in STORE_FILES:
        path = theme_path(name, store)
        overlays[store] = load_overlay(path.read_text(), path.name) if path.exists() \
            else OrderedDict()
    return overlays


def plan(name: str) -> "dict[str, TdFile]":
    """Merge theme over baseline and validate, without touching the install."""
    baseline = load_baseline()
    overlays = _load_theme(name)
    merged, findings = {}, []
    for store in STORE_FILES:
        merged[store] = merge(baseline[store], overlays[store])
        findings += validate(merged[store], baseline[store])
    return {"files": merged, "findings": findings}


def apply(name: str, *, force: bool = False) -> dict:
    """Merge, validate, back up, and write. Refuses on validation errors."""
    result = plan(name)
    findings = result["findings"]
    errors = [f for f in findings if f.severity == "error"]
    if errors and not force:
        raise ValidationError(
            f"theme {name!r} has {len(errors)} validation error(s); nothing written",
            findings,
        )

    cfg = config_dir()
    running = td_running()
    # TouchDesigner reads both stores at startup, so a running instance keeps
    # showing the old appearance until it is restarted. That is a display
    # lag, not data loss: there is no evidence TouchDesigner ever writes these
    # files back, so editing them while it runs is safe. See README.
    warnings = []
    if running:
        warnings.append(
            f"TouchDesigner is running (pid {', '.join(map(str, running))}). "
            f"It reads these files at startup, so it will keep showing the "
            f"current appearance until you restart it."
        )

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = backups_dir / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    for store in STORE_FILES:
        installed = cfg / store
        if installed.exists():
            shutil.copy2(installed, backup_dir / store)

    for store in STORE_FILES:
        write_file(cfg / store, result["files"][store].to_bytes())

    record_applied(name)

    return {
        "theme": name,
        "files": list(STORE_FILES),
        "backup": backup_dir,
        "findings": findings,
        "warnings": warnings,
        "td_running": running,
    }


def export(name: str, *, force: bool = False) -> "dict[str, Path]":
    """Write the currently installed stores out as a sparse theme."""
    if not force and name in list_themes():
        raise ThemeError(
            f"theme {name!r} already exists. Use --force to overwrite it."
        )
    baseline = load_baseline()
    cfg = config_dir()
    written = {}
    directory = themes_dir / name
    directory.mkdir(parents=True, exist_ok=True)
    for store in STORE_FILES:
        installed = load_file(cfg / store, store)
        sparse = diff(baseline[store], installed)
        path = theme_path(name, store)
        write_file(path, dump_overlay(sparse, store).encode())
        written[store] = path
    return written


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

    @property
    def clean(self) -> bool:
        # `drift` is a dict that is non-empty even when every count is zero,
        # so test the values rather than the dict's truthiness.
        return not self.missing and not any(self.drift.values())
