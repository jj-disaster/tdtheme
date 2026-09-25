#!/usr/bin/env python3
"""Command-line front-end for tdtheme.

All the logic lives in tdtheme.py; this file only formats output and maps
exit codes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import tdtheme as T

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_VALIDATION = 2


def _finding_lines(findings, indent="    ") -> None:
    for finding in findings:
        print(f"{indent}{finding}")


def cmd_capture(args) -> int:
    written = T.capture(force=args.force)
    version = T.baseline_version()
    print("Captured baseline:")
    for name, path in written.items():
        print(f"    {name:<14} {path}")
    print(f"    TouchDesigner build {version.get('td_build')}")
    if not args.force:
        print("\nNote: re-running capture with --force changes what every "
              "existing theme diffs against.")
    return EXIT_OK


def cmd_list(args) -> int:
    themes = T.list_themes()
    if not themes:
        print("No themes yet. Create one with:  tdtheme export <name>")
        return EXIT_OK
    applied = T.applied_theme()
    for name in themes:
        counts = []
        for store in T.STORE_FILES:
            path = T.theme_path(name, store)
            if path.exists():
                overlay = T.load_overlay(path.read_text(), path.name)
                counts.append(f"{store} {len(overlay)}")
        marker = "*" if name == applied else " "
        print(f"  {marker} {name:<20} {', '.join(counts)}")
    if applied:
        print(f"\n* = last applied by tdtheme ({applied}).")
    return EXIT_OK


def cmd_diff(args) -> int:
    result = T.plan(args.name)
    baseline = T.load_baseline()
    overlays = T._load_theme(args.name)

    total = 0
    for store in T.STORE_FILES:
        sparse = overlays[store]
        print(f"{store}: {len(sparse)} key(s) changed from baseline")
        for key, value in sparse.items():
            before = baseline[store].get(key)
            after = T.merge(baseline[store], {key: value}).data[key]
            old_text = "\t".join(before) if before is not None else "(absent)"
            new_text = "\t".join(after)
            print(f"    {key}")
            print(f"        - {old_text}")
            print(f"        + {new_text}")
        total += len(sparse)
        print()
    if total == 0:
        print("This theme changes nothing.")
    _finding_lines(result["findings"])
    return EXIT_OK


def cmd_export(args) -> int:
    written = T.export(args.name, force=args.force)
    print(f"Exported current TouchDesigner state as theme {args.name!r}:")
    for store, path in written.items():
        overlay = T.load_overlay(path.read_text(), path.name)
        print(f"    {store:<14} {len(overlay)} key(s) -> {path}")
    print("\nThe theme records only what differs from baseline. Edit the "
          "YAML by hand to build it further.")
    return EXIT_OK


def cmd_apply(args) -> int:
    try:
        result = T.apply(args.name, force=args.force)
    except T.ValidationError as exc:
        print(f"Not applied: {exc}", file=sys.stderr)
        _finding_lines(exc.findings)
        print("\nFix these, or re-run with --force to override.", file=sys.stderr)
        return EXIT_VALIDATION

    print(f"Applied theme {result['theme']!r}")
    for warning in result["warnings"]:
        print(f"\n    WARNING: {warning}")
    errors = [f for f in result["findings"] if f.severity == "error"]
    warnings = [f for f in result["findings"] if f.severity == "warning"]
    if warnings:
        print(f"\n    {len(warnings)} warning(s):")
        _finding_lines(warnings)
    if args.force and errors:
        print(f"\n    {len(errors)} error(s) forced through:")
        _finding_lines(errors)
    print(f"\n    backup: {result['backup']}")
    if not result["td_running"]:
        print("    TouchDesigner is closed; changes are live on next launch.")
    return EXIT_OK


def cmd_status(args) -> int:
    state = T.status()
    print(f"TouchDesigner     {state.td_version or 'not found'}")
    if state.td_running:
        print(f"                  RUNNING (pid {', '.join(map(str, state.td_running))})")
    print(f"config dir        {state.config_dir}")
    print(f"baseline          {'captured' if state.baseline_present else 'MISSING - run capture'}")
    if state.baseline_present:
        if state.version_match:
            print(f"                  build {state.baseline_version} matches")
        else:
            print(f"                  build {state.baseline_version} != live "
                  f"{state.td_version}  (run `tdtheme capture --force`)")
        print(f"                  captured {state.baseline_captured}")
    print(f"themes            {', '.join(state.themes) if state.themes else 'none'}")
    if state.applied:
        print(f"                  last applied: {state.applied}")
    if state.missing:
        print(f"                  MISSING from install: {', '.join(state.missing)}")
    for store, count in state.drift.items():
        state_word = "matches baseline" if count == 0 else f"differs from baseline ({count} key(s))"
        print(f"  {store:<14} {state_word}")
    if state.td_running:
        print("\n    TouchDesigner is running. It reads these files at startup, "
              "so restart it to see any change.")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tdtheme",
        description="Manage TouchDesigner UI themes by editing TouchColors and "
                    "TouchOptions.",
        epilog="TouchDesigner reads these files at startup, so restart it to see "
               "a change take effect.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("capture", help="snapshot the installed stores as the baseline")
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing baseline")
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("list", help="list available themes")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("status", help="show build, baseline and drift")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("export", help="save the current state as a new theme")
    p.add_argument("name")
    p.add_argument("--force", action="store_true", help="overwrite an existing theme")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("diff", help="show what a theme changes")
    p.add_argument("name")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("apply", help="merge a theme into the install")
    p.add_argument("name")
    p.add_argument("--force", action="store_true",
                   help="write even if validation errors are present")
    p.set_defaults(func=cmd_apply)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except T.ThemeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
