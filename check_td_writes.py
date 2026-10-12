#!/usr/bin/env python3
"""Settle an open question: does TouchDesigner ever WRITE TouchColors,
TouchOptions or the Icons directory, or only read them?

Portable twin of ./check-td-writes (sh). Same snapshot file, same line
format, so a snapshot recorded by one can be checked by the other; the only
difference is how the paths are found and the hashes computed. The Windows
shim (check-td-writes.cmd, written by ./setup) runs this file, because a
native shell cannot exec the sh script.

Usage:
  check_td_writes.py record    # run BEFORE launching TouchDesigner
  check_td_writes.py check     # run AFTER quitting TouchDesigner

If the checksums match, TouchDesigner did not write any of them, and applying
a theme while it runs is safe. If they differ, it does write, and the tool
needs a "re-apply on quit" mechanism.

The Icons directory is summed rather than listed: 97 files is too many rows
to read, and the only question is whether the set changed at all.
TouchDesigner caches icons on first use and never writes them back, so it is
expected to be the quiet one.

Standard library only, like the rest of this tool. Paths and the
is-it-running check come from tdtheme itself, so this follows TDTHEME_CONFIG
and the Windows install location automatically.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import tdtheme as T

SNAP = T.root / ".tdwrite-snapshot"


def _md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot() -> "list[tuple[str, str]]":
    """One (digest, label) pair per tracked artefact, in stable order."""
    cfg = T.config_dir()
    rows = []
    for store in T.STORE_FILES:
        path = cfg / store
        rows.append((_md5_file(path) if path.is_file() else "(missing)", store))
    icons = cfg / T.ICONS_DIRNAME
    if icons.is_dir():
        digest = hashlib.md5()
        # Sorted by name, explicitly: sorting Path objects compares
        # case-insensitively on Windows, which silently reorders the concat
        # versus the byte order `cat Icons/*.tiff` produces in a C locale.
        # The order is the hash, so it has to be pinned, not inherited.
        for child in sorted(icons.glob("*.tiff"), key=lambda p: p.name):
            with child.open("rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
        # Concatenated in sorted-name order, the same bytes `cat Icons/*.tiff`
        # hashes in a C locale (byte order) - which is what makes a snapshot
        # recorded by the sh script comparable to one recorded here.
        rows.append((digest.hexdigest(), T.ICONS_DIRNAME))
    else:
        rows.append(("(missing)", T.ICONS_DIRNAME))
    return rows


def _write_snapshot(rows) -> None:
    SNAP.write_text("".join(f"{digest} {label}\n" for digest, label in rows))


def _read_snapshot():
    try:
        lines = SNAP.read_text().splitlines()
    except OSError:
        return None
    return [tuple(line.split(None, 1)) for line in lines if line.split()]


def cmd_record() -> int:
    import datetime
    rows = snapshot()
    _write_snapshot(rows)
    print(f"recorded {datetime.datetime.now():%H:%M:%S}:")
    for digest, label in rows:
        print(f"  {digest} {label}")
    print()
    print("Now launch TouchDesigner, use it however you like, and quit it.")
    print("Then run:  check-td-writes check")
    return 0


def cmd_check() -> int:
    before = _read_snapshot()
    if before is None:
        print("no snapshot found - run 'check-td-writes record' first",
              file=sys.stderr)
        return 1
    if T.td_running():
        print("TouchDesigner is still running - quit it first, then re-run.",
              file=sys.stderr)
        return 1
    after = dict((label, digest) for digest, label in snapshot())

    print()
    print("artefact        before                                 after"
          "                                  verdict")
    for digest, label in before:
        current = after.get(label, "(missing)")
        verdict = "same" if current == digest else "CHANGED"
        print(f"  {label:<12} {digest:<36} {current:<36} {verdict}")

    if all(after.get(label) == digest for digest, label in before):
        print()
        print("RESULT: unchanged. TouchDesigner wrote none of them.")
        print("        Applying a theme while it runs is safe; a restart is only")
        print("        needed for the change to become visible.")
    else:
        print()
        print("RESULT: CHANGED. TouchDesigner does write at least one of these.")
        print("        The tool needs to re-apply after quit, or refuse to run")
        print("        while TouchDesigner is open.")
    return 0


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["record"]:
        return cmd_record()
    if args == ["check"]:
        return cmd_check()
    print(f"usage: {Path(sys.argv[0]).name} record | check", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
