#!/usr/bin/env python3
"""Command-line front-end for tdtheme.

All the logic lives in tdtheme.py and tdicons.py; this file only formats
output and maps exit codes.
"""

from __future__ import annotations

import argparse
import sys

import tdicons
import tdtheme as T

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_VALIDATION = 2


def _finding_lines(findings, indent="    ") -> None:
    for finding in findings:
        print(f"{indent}{finding}")


def _icon_line(theme: str) -> str:
    """The icon-set summary `list` shows next to each theme.

    Byte-level on purpose. `tdtheme list` runs over every theme, and decoding
    each 97-icon set to compare pixels costs about 2s for the five shipped
    themes - too slow for a command whose job is to be glanced at. The byte
    count is therefore reported, and it is worded as bytes rather than as
    "changed from baseline", because for a regenerated set the two are very
    different numbers: `mono` is pure grayscale and leaves 79 of its 97 icons
    pixel-for-pixel identical, while all 97 differ in bytes. `tdtheme icons
    diff` is where the pixel count lives.
    """
    directory = T.theme_icons_dir(theme)
    if not directory.is_dir():
        return "icons none"
    changed = T.icon_diff(theme)
    total = len(tdicons.icon_names(directory))
    if not changed:
        return f"icons {total} (stock bytes)"
    kinds: dict[str, int] = {}
    for state in changed.values():
        kinds[state] = kinds.get(state, 0) + 1
    if set(kinds) == {"changed"}:
        # The overwhelmingly common case, and the one where the number is least
        # informative - every regenerated file differs in bytes whatever the
        # recipe did. Keep it to four words and let `icons diff` do the work.
        return f"icons {total} (all differ in bytes)"
    detail = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))
    return f"icons {total} ({detail})"


def cmd_capture(args) -> int:
    written = T.capture(force=args.force)
    version = T.baseline_version()
    stores = [n for n in written if n in T.STORE_FILES]
    icons = [n for n in written if n not in T.STORE_FILES]
    print("Captured baseline:")
    for name in stores:
        print(f"    {name:<14} {written[name]}")
    if icons:
        total = sum((T.baseline_icons_dir() / n).stat().st_size for n in icons)
        print(f"    {'Icons':<14} {len(icons)} file(s), {total / 1024:.0f} KB "
              f"-> {T.baseline_icons_dir()}")
    print(f"    TouchDesigner build {version.get('td_build')}")
    if not args.force:
        print("\nNote: re-running capture with --force changes what every "
              "existing theme diffs against.")
    return EXIT_OK


def cmd_list(args) -> int:
    themes = T.list_themes()
    if not themes:
        print("No themes yet. Create one with:  tdthememaker export <name>")
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
        print(f"  {marker} {name:<20} {', '.join(counts):<28} {_icon_line(name)}")
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

    icon_changes = T.icon_diff(args.name)
    if T.theme_icons_dir(args.name).is_dir():
        total_icons = len(tdicons.icon_names(T.theme_icons_dir(args.name)))
        print(f"{T.ICONS_DIRNAME}: {len(icon_changes)} of {total_icons} file(s) "
              f"differ from baseline")
        if icon_changes:
            for icon_name, state in icon_changes.items():
                print(f"    {icon_name} ({state})")
        print()
    elif T.icons_available():
        print(f"{T.ICONS_DIRNAME}: not themed by this theme (no "
              f"{T.theme_icons_dir(args.name).name}/ directory); the install "
              f"keeps its shipped icons\n")

    if total == 0 and not icon_changes:
        print("This theme changes nothing.")
    _finding_lines(result["findings"])
    _finding_lines(result["icon_findings"])
    return EXIT_OK


def cmd_apply(args) -> int:
    try:
        result = T.apply(args.name, force=args.force, icons=not args.no_icons)
    except T.ValidationError as exc:
        print(f"Not applied: {exc}", file=sys.stderr)
        _finding_lines(exc.findings)
        print("\nFix these, or re-run with --force to override.", file=sys.stderr)
        return EXIT_VALIDATION

    print(f"Applied theme {result['theme']!r}")
    for warning in result["warnings"]:
        print(f"\n    WARNING: {warning}")
    icons = result["icons"]
    if icons.get("applied"):
        summary = (f"    icons: {len(icons['written'])} written, "
                   f"{len(icons['unchanged'])} already matched the baseline")
        if icons.get("filled"):
            summary += f", {len(icons['filled'])} filled in from the baseline"
        if icons["backed_up"]:
            summary += f", {icons['backed_up']} backed up"
        print(summary)
    elif icons.get("reason"):
        print(f"    icons: {icons['reason']}")
    ui = result["ui_tox"]
    if ui.get("applied"):
        # The two cases have to be distinguishable, because they install
        # different dialog geometry and one line of output otherwise says the
        # same thing for both. The fallback is named as a fallback, not as a
        # source, so nobody reads it as this theme shipping a stock file.
        origin = "" if not ui["from_default"] else " (this theme has no ui.tox)"
        print(f"    {T.UI_TOX}: written from {ui['source'].parent.name}{origin}")
    elif ui.get("reason"):
        print(f"    {T.UI_TOX}: {ui['reason']}")
    errors = [f for f in result["findings"] + result["icon_findings"]
              if f.severity == "error"]
    warnings = [f for f in result["findings"] + result["icon_findings"]
                if f.severity == "warning"]
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


def cmd_reset(args) -> int:
    """`reset` is `apply default`, for when the theme name is not the point.

    The name is fixed here and everything else is forwarded, so the two
    commands cannot drift apart: there is one implementation, and the flags
    on `reset` mean exactly what the same flags mean on `apply`.
    """
    args.name = "default"
    return cmd_apply(args)


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
    if state.icon_drift:
        # The install's own icons, compared to the baseline. This one is
        # meaningful at byte level: 0 means the shipped files are exactly as
        # TouchDesigner left them.
        print(f"  {T.ICONS_DIRNAME:<14} {state.icon_count} file(s) in {state.icons_dir}")
        for _key, count in state.icon_drift.items():
            word = ("matches baseline" if count == 0
                    else f"differs from baseline ({count} of {state.icon_count} file(s))")
            print(f"  {'':<14} {word}")
    if state.icon_theme_drift:
        # Per-theme, byte level, and worded as bytes. Every regenerated icon
        # differs from the shipped file no matter what the recipe did, so for
        # `mono` this reads 97 while only 18 icons are actually repainted.
        # Decoding all five sets to say otherwise costs about 2s, which is the
        # wrong trade for a status line; `tdtheme icons diff <theme>` gives the
        # pixel count for one theme.
        print("\n  themed icon sets (bytes; `icons diff` compares pixels):")
        for name, count in state.icon_theme_drift.items():
            word = "stock bytes" if count == 0 else f"{count} file(s) differ in bytes"
            print(f"    {name:<20} {word}")
    if state.td_running:
        print("\n    TouchDesigner is running. It reads these files at startup, "
              "so restart it to see any change.")
    return EXIT_OK


# --------------------------------------------------------------------------
# icons
# --------------------------------------------------------------------------

def cmd_icons(args) -> int:
    if args.icons_command == "list":
        return _icons_list(args)
    if args.icons_command == "diff":
        return _icons_diff(args)
    if args.icons_command == "preview":
        return _icons_preview(args)
    raise AssertionError(f"unhandled icons subcommand {args.icons_command!r}")
    # `icons` uses subparsers(required=True), so exactly one of the branches
    # above always returns and this is unreachable. It stays as a guard against
    # a future subcommand being added without a dispatch branch.


def _icons_list(args) -> int:
    source = T.theme_icons_dir(args.name) if args.name else T.baseline_icons_dir()
    if not source.is_dir():
        print(f"No icon directory at {source}", file=sys.stderr)
        return EXIT_ERROR
    manifest = tdicons.icon_manifest(source)
    print(f"{len(manifest)} icon(s) in {source}\n")
    for name, entry in manifest.items():
        if "error" in entry:
            print(f"  {name:<30} UNREADABLE: {entry['error']}")
            continue
        print(f"  {name:<30} {entry['width']:>4} x {entry['height']:<4} "
              f"{entry['bytes']:>7} B  {entry['sha256'][:12]}")
    return EXIT_OK


def _icons_diff(args) -> int:
    # `name` is a required positional here, so it is always set. A theme with
    # no Icons/ is a real state, and indistinguishable from a typo unless the
    # name is checked first.
    T.require_theme(args.name)
    directory = T.theme_icons_dir(args.name)
    if not directory.is_dir():
        print(f"Theme {args.name!r} has no icon directory, so it does not "
              f"theme icons. The install keeps whatever it has.")
        return EXIT_OK

    total = len(tdicons.icon_names(directory))
    byte_changes = T.icon_diff(args.name)

    if args.bytes:
        print(f"{T.ICONS_DIRNAME}: {len(byte_changes)} of {total} file(s) differ "
              f"from baseline, by byte")
        for name, state in byte_changes.items():
            print(f"    {name:<30} {state}")
        if not byte_changes:
            print("    (this theme ships the stock icons byte for byte)")
        return EXIT_OK

    # Bytes and pixels disagree here, and only pixels answer the question
    # anyone is really asking. An authored icon always differs from the shipped
    # file - it is single-strip, re-compressed, and has the ~5 KB of Photoshop
    # metadata stripped - so the byte count is 97 for every themed icon set,
    # including `mono`, where a grayscale recipe leaves 58 of the 97
    # pixel-for-pixel identical. Reporting only the byte count would make every
    # recipe look equally aggressive.
    pixel_changes = tdicons.pixel_diff(T.baseline_icons_dir(), directory)
    kinds: dict[str, list[str]] = {}
    for name, state in pixel_changes.items():
        kinds.setdefault(state, []).append(name)
    changed = len(kinds.get("changed", ()))
    identical = len(kinds.get("identical", ()))

    # The byte-identical case has nothing to decode: `diff_icons` is empty, so
    # `pixel_diff` reports no per-name work. Equal bytes mean equal pixels, so
    # the honest count is all of them - and listing 97 names would bury the one
    # line that matters.
    if not byte_changes:
        changed, identical = 0, total
        note = ("byte-identical: this theme ships the stock files, so "
                "`apply` on it is a lossless reset")
    elif changed == total:
        note = "every icon was repainted"
    elif identical:
        note = ("every icon differs in bytes whether or not the recipe "
                "repainted it - a regenerated file is single-strip, "
                "straight-alpha and has the shipped Photoshop metadata stripped")
    else:
        note = None

    print(f"{T.ICONS_DIRNAME}: {total} file(s) vs baseline")
    print(f"    by byte:  {len(byte_changes):>3} of {total} differ")
    print(f"    by pixel: {changed:>3} changed, {identical} identical")
    if note:
        print(f"\n    ({note})")

    if identical and identical != total:
        plural = "icon is" if identical == 1 else "icons are"
        print(f"\n    {identical} {plural} pixel-for-pixel the shipped glyph:")
        for name in kinds["identical"]:
            print(f"        {name}")
    return EXIT_OK


def _icons_preview(args) -> int:
    if args.name is not None:
        T.require_theme(args.name)
    path = T.preview_icons(args.name, args.out, columns=args.columns, cell=args.cell)
    print(f"Wrote {path}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tdtheme",
        description="Install and inspect TouchDesigner UI themes. A theme is a "
                    "directory of TouchColors.yaml, TouchOptions.yaml and an "
                    "Icons/ directory of 97 TIFFs, which this tool merges, "
                    "validates and installs. Authoring - generating icon sets "
                    "from recipes, and exporting the current state as a new "
                    "theme - is a separate command, tdthememaker.",
        epilog="TouchDesigner reads TouchColors and TouchOptions at startup and "
               "caches each icon on first use, so restart it to see a change "
               "take effect.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("capture", help="snapshot the installed stores and icons as the baseline")
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing baseline")
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("list", help="list available themes")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("status", help="show build, baseline and drift")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("diff", help="show what a theme changes")
    p.add_argument("name")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("apply", help="merge a theme into the install")
    p.add_argument("name")
    p.add_argument("--force", action="store_true",
                   help="write even if validation errors are present")
    p.add_argument("--no-icons", action="store_true",
                   help="apply colours and options only, leaving the icon set alone")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("reset", help="restore the stock look (an alias for 'apply default')")
    p.add_argument("--force", action="store_true",
                   help="write even if validation errors are present")
    p.add_argument("--no-icons", action="store_true",
                   help="reset colours and options only, leaving the icon set alone")
    p.set_defaults(func=cmd_reset)

    icons = sub.add_parser("icons", help="inspect and rebuild icon sets")
    icons_sub = icons.add_subparsers(dest="icons_command", required=True)

    p = icons_sub.add_parser("list", help="list an icon set with sizes and hashes")
    p.add_argument("name", nargs="?",
                   help="theme name; omit to list the baseline set")
    p.set_defaults(func=cmd_icons)

    p = icons_sub.add_parser(
        "diff", help="show which icons a theme changes, by pixel or by byte")
    p.add_argument("name")
    p.add_argument("--bytes", action="store_true",
                   help="compare files byte for byte instead of decoding them "
                        "(faster, but every regenerated icon differs in bytes "
                        "regardless of what the recipe did)")
    p.set_defaults(func=cmd_icons)

    p = icons_sub.add_parser("preview", help="write a PNG contact sheet of a theme's icons")
    p.add_argument("name", nargs="?", help="theme name; omit for the baseline set")
    p.add_argument("-o", "--out", help="output path (default testiconsforagents/<name>-icons.png)")
    p.add_argument("--columns", type=int, default=10)
    p.add_argument("--cell", type=int, default=72)
    p.set_defaults(func=cmd_icons)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except tdicons.IconError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except T.ThemeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
