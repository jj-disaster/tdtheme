#!/usr/bin/env python3
"""Command-line front-end for tdtheme.

All the logic lives in tdtheme.py and tdicons.py; this file only formats
output and maps exit codes.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import tdicons
import tdtheme as T

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_VALIDATION = 2


def _finding_lines(findings, indent="    ") -> None:
    for finding in findings:
        print(f"{indent}{finding}")


def _store_line(store: str, changes: dict) -> str:
    """How many lines this apply changed in one store, and nothing else.

    A count, never a list of keys. The largest shipped theme changes 460, and a
    line naming them is a line nobody reads; `tdtheme diff` is the listing.
    One key is one line in this format, so counting keys counts lines, and the
    removals are counted too - a key the theme dropped is a line that left the
    file, which `diff` cannot report because it only looks in `other`.
    """
    changed, removed = len(changes["changed"]), len(changes["removed"])
    if not changed and not removed:
        return f"{store}: unchanged"
    if removed:
        return f"{store}: {changed} changed, {removed} removed"
    return f"{store}: {changed} changed"


def _icon_line(theme: str) -> str:
    """The icon-set summary `list` shows next to each theme.

    Byte-level on purpose: `list` runs over every theme, and decoding each
    97-icon set to compare pixels costs about 2s for the five shipped themes -
    too slow for a command whose job is to be glanced at. So the byte count is
    reported, and worded as bytes rather than as "changed from baseline",
    because for a regenerated set the two are very different numbers: `mono` is
    pure grayscale and leaves 58 of its 97 icons pixel-for-pixel identical while
    all 97 differ in bytes. `tdtheme icons diff` is where the pixel count lives.
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
    # `baseline/` is tracked, so overwriting it is a change every other user of
    # this checkout inherits, and `git add -A` is exactly the accident that
    # commits 2.2 MB of one machine's build. `--force` only says "overwrite
    # *something*", which is true of the private shadow too, so it cannot be the
    # whole acknowledgement on its own. `--i-know-this-is-shared` is.
    #
    # The refusal names both ways forward, and neither of them is the flag the
    # user just typed: `--force` alone is refused, which is the whole point, so
    # repeating it in the message would be a loop.
    if args.force and not args.local and T.baseline_dir == T.baseline_shipped_dir:
        if not args.i_know_this_is_shared:
            print("Refusing: --force here would overwrite the committed "
                  "baseline/, which every other user of this checkout diffs "
                  "against.\n"
                  "  --local   capture into "
                  f"{T.baseline_shadow_dir.name}/ instead and leave it alone - "
                  "the right answer unless you mean to change the shared copy\n"
                  "  --i-know-this-is-shared   ... and yes, replace baseline/ for "
                  "everyone (with --force)",
                  file=sys.stderr)
            return EXIT_ERROR

    written = T.capture(force=args.force, into_shadow=args.local)
    # The directory the icons actually went to, which is the shadow when
    # capturing into one. Reading it back through `baseline_icons_dir()` is wrong
    # whenever the two differ, and they differ exactly when `--local` created the
    # shadow: the module resolved its baseline at import, before the directory it
    # would come to exist.
    icons_target = T.baseline_shadow_dir / T.ICONS_DIRNAME if args.local else T.baseline_icons_dir()
    # Read the build back from the file this capture just wrote, not through
    # `baseline_version()`. That reads `baseline_dir`, which was resolved at
    # import - before a `--local` capture created the shadow - so it reports the
    # committed baseline's build and not the one just recorded. Invisible while
    # both builds matched, and wrong precisely when the user is on a different
    # build, which is the only reason to run this command.
    captured = json.loads(
        (T.baseline_shadow_dir if args.local else T.baseline_dir)
        .joinpath("version.json").read_text())
    # `icons` used to be everything that is not a store, which swept in the
    # `ui.tox` capture and then tried to stat it inside the icon directory. It
    # stayed hidden while capture wrote to the same place it was reporting; with
    # a shadow the reported path and the written path can differ, and the
    # mismatch became a FileNotFoundError. Select the icons by where they were
    # written rather than by elimination.
    stores = [n for n in written if n in T.STORE_FILES]
    icons = [n for n in written if written[n].parent == icons_target]
    print("Captured baseline:")
    for name in stores:
        print(f"    {name:<14} {written[name]}")
    if icons:
        total = sum((icons_target / n).stat().st_size for n in icons)
        print(f"    {'Icons':<14} {len(icons)} file(s), {total / 1024:.0f} KB "
              f"-> {icons_target}")
    if T.UI_TOX in written:
        print(f"    {T.SYSTEM_DIRNAME}/{T.UI_TOX:<9} {written[T.UI_TOX]}")
    print(f"    TouchDesigner build {captured.get('td_build')}")
    if args.local:
        print(f"\nEvery later command now merges over {T.baseline_shadow_dir.name}/, "
              f"not the committed baseline.\nIt is gitignored and local to this checkout. "
              f"Delete it to go back.")
    elif not args.force:
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
        result = T.apply(args.name, force=args.force, icons=not args.no_icons,
                         backup=args.backup,
                         allow_unknown=args.allow_unknown)
    except T.ValidationError as exc:
        print(f"Not applied: {exc}", file=sys.stderr)
        _finding_lines(exc.findings)
        print("\nFix these, or re-run with --force to override.", file=sys.stderr)
        return EXIT_VALIDATION
    except T.UnknownKeysError as exc:
        # This is the one refusal where the offending keys are named as a count
        # and not a list, for the same reason `apply` prints counts and not
        # names: a build's added keys are unbounded, and the remedy is the same
        # regardless of which they are.
        print(f"Not applied: {exc}", file=sys.stderr)
        return EXIT_VALIDATION

    print(f"Applied theme {result['theme']!r}")
    for warning in result["warnings"]:
        print(f"\n    WARNING: {warning}")
    # Both stores, always. A theme that has no opinion about the option store
    # should say so rather than go unmentioned, and one line per store is
    # parallel to the icons and ui.tox lines rather than a change report.
    for store in T.STORE_FILES:
        changes = result["changes"].get(store)
        if changes is None:
            print(f"    {store}: written (no previous file to compare against)")
        else:
            print(f"    {_store_line(store, changes)}")
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
        # The two cases install different dialog geometry, so one line must not
        # read the same for both. The fallback is named as a fallback, not as a
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
    if result["backup"] is not None:
        print(f"\n    backup: {result['backup']}")
    if not result["td_running"]:
        print("    TouchDesigner is closed; changes are live on next launch.")
    return EXIT_OK


def cmd_reset(args) -> int:
    """`reset` is `apply default`, for when the theme name is not the point.

    The name is fixed here and everything else forwarded, so the two commands
    cannot drift apart: one implementation, and the flags mean what the same
    flags mean on `apply`.
    """
    args.name = "default"
    return cmd_apply(args)


def _git(args, cwd):
    """Run git, returning (returncode, stdout, stderr). Never raises."""
    try:
        done = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                              text=True)
    except OSError as exc:
        return 127, "", str(exc)
    return done.returncode, done.stdout.strip(), done.stderr.strip()


def _installed_links():
    """The symlinks on PATH that point into this checkout.

    Found by reading PATH rather than by trusting a recorded list, so it
    reports what is actually there now. A stale record would remove links that
    are already gone and miss ones made by hand.
    """
    here = str(T.root)
    found = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        for name in ("tdtheme", "tdthememaker", "check-td-writes"):
            path = Path(entry) / name
            try:
                if not path.is_symlink():
                    continue
                target = os.readlink(str(path))
            except OSError:
                continue
            resolved = target if os.path.isabs(target) else str(Path(entry) / target)
            try:
                if os.path.realpath(resolved).startswith(here + os.sep):
                    found.append(path)
            except OSError:
                continue
    return found


def cmd_uninstall(args) -> int:
    """Put TouchDesigner back to stock and remove the commands from PATH.

    The install is restored first, because that is the only part of this that
    is not reversible by re-running the tool. Once the links are gone the
    user has no way to undo a theme but by hand-editing four undocumented
    files, so the order is load-bearing and not a preference.

    The checkout itself is never deleted. It holds the themes, and a user
    maintaining their own would lose them to a command whose name reads like
    "remove this program". The directory is printed with the command to remove
    it, so the decision stays with whoever wrote the theme.
    """
    # 1. Restore stock. This is the irreversible part, so it comes first while
    #    the code that does it is still here to run.
    print("Restoring the stock TouchDesigner UI...")
    if args.keep_files:
        print("  --keep-files, so the install is left as it is")
    else:
        args.name = "default"
        args.force = False
        args.no_icons = False
        args.backup = False
        args.allow_unknown = True   # a reset is the one case where the refusal
                                    # would strand the user: they are trying to
                                    # leave, not to install
        code = cmd_apply(args)
        if code != EXIT_OK:
            print("\nuninstall: the install was not restored, so the links are "
                  "being left alone. Fix the error above and re-run.", file=sys.stderr)
            return code

    # 2. Remove the links. Only ones that point at *this* checkout - a link to
    #    somewhere else is not this tool's to delete.
    links = _installed_links()
    if not links:
        print("\nNo links to this checkout on PATH.")
    for path in links:
        try:
            path.unlink()
            print(f"  removed {path}")
        except OSError as exc:
            bad = f"could not remove {path}: {exc}"
            print(f"  PROBLEM  {bad}", file=sys.stderr)

    # 3. The state file, so a re-clone does not inherit a phantom theme.
    applied = T._applied_path()
    if applied.exists():
        try:
            applied.unlink()
            print(f"  removed {applied.name}")
        except OSError as exc:
            print(f"  PROBLEM  could not remove {applied.name}: {exc}", file=sys.stderr)

    print(f"\nDone. TouchDesigner is back to stock. The checkout is still here:\n"
          f"  {T.root}\n"
          f"It holds your themes, so it is not deleted. To remove it and this "
          f"machine's\nprivate baseline as well:\n"
          f"  rm -rf {T.root}")
    return EXIT_OK


def cmd_update(args) -> int:
    """`git pull` the checkout, refusing when the working tree is not clean.

    A fast-forward only, deliberately. This checkout holds themes that may be
    hand-edited, and a merge commit on someone's behalf is a merge conflict
    they then have to resolve with no memory of what they wanted. `--ff-only`
    means this either updates cleanly or stops and says why.

    The dirty check is a refusal, not a warning, for the same reason `apply`
    refuses unknown keys: a pull that overwrites a local edit destroys work
    that git never saw, because the whole point is that it was uncommitted.
    """
    if not (T.root / ".git").exists():
        print(f"error: {T.root} is not a git checkout, so there is nothing to "
              f"update.\n  Re-clone it instead:\n"
              f"  git clone https://github.com/jj-disaster/tdtheme.git",
              file=sys.stderr)
        return EXIT_ERROR

    code, _, err = _git(["rev-parse", "--is-inside-work-tree"], T.root)
    if code != 0:
        print(f"error: {T.root} is not a git checkout ({err})", file=sys.stderr)
        return EXIT_ERROR

    before, out, err = _git(["rev-parse", "--short", "HEAD"], T.root)
    if before:
        before = out

    status, out, _ = _git(["status", "--porcelain"], T.root)
    dirty = [l for l in out.splitlines() if l.strip()]
    if dirty:
        print("Refusing: this checkout has uncommitted changes, and a pull can "
              "overwrite them.\n",
              file=sys.stderr)
        for line in dirty[:10]:
            print(f"  {line}", file=sys.stderr)
        if len(dirty) > 10:
            print(f"  ... and {len(dirty) - 10} more", file=sys.stderr)
        print("\nCommit them, stash them, or look at them first:\n"
              "  git status          what is changed\n"
              "  git diff            what the changes are\n"
              "  git stash           set them aside, then re-run this",
              file=sys.stderr)
        return EXIT_ERROR

    code, out, err = _git(["pull", "--ff-only"], T.root)
    if code != 0:
        print(f"error: git pull failed\n{err}", file=sys.stderr)
        if "diverging" in err or "divergent" in err:
            print("\n  Your branch and the remote have both moved on, so there "
                  "is no\n  fast-forward. This is deliberately not resolved for "
                  "you:\n    git log --oneline --left-right HEAD...origin/main"
                  "\n  shows both sides. Then either:\n"
                  "    git merge origin/main    and resolve, or\n"
                  "    git rebase origin/main   to replay your commits on top",
                  file=sys.stderr)
        return EXIT_ERROR

    after, _, _ = _git(["rev-parse", "--short", "HEAD"], T.root)
    if before and after and before != after:
        print(f"Updated {before} -> {after}")
        # The install is merge(baseline, theme), so a pull that changes either
        # one means the files on disk no longer match what this checkout
        # describes. Naming the re-apply is the whole point of reporting it.
        print("\n  A new upstream theme is a new theme: it is not installed "
              "until you\n  apply it. Your currently applied theme is unchanged "
              "on disk.")
    else:
        print("Already up to date.")

    print("\n  If you changed themes, re-apply to pick up changes to it:\n"
          "    tdtheme reset        back to stock\n"
          "    tdtheme apply NAME   a specific theme")
    return EXIT_OK


def cmd_status(args) -> int:
    state = T.status()
    print(f"TouchDesigner     {state.td_version or 'not found'}")
    if state.td_running:
        print(f"                  RUNNING (pid {', '.join(map(str, state.td_running))})")
    print(f"config dir        {state.config_dir}")
    print(f"baseline          {'captured' if state.baseline_present else 'MISSING - run capture'}")
    if T.baseline_is_shadowed():
        # Which baseline is live is otherwise invisible: every command resolves
        # the same path and a tester cannot tell they are on a private one.
        print(f"                  {T.baseline_shadow_dir.name}/ - private copy, "
              f"shadowing the committed {T.baseline_shipped_dir.name}/")
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
        # The install's own icons. This one is meaningful at byte level: 0 means
        # the shipped files are exactly as TouchDesigner left them.
        print(f"  {T.ICONS_DIRNAME:<14} {state.icon_count} file(s) in {state.icons_dir}")
        for _key, count in state.icon_drift.items():
            word = ("matches baseline" if count == 0
                    else f"differs from baseline ({count} of {state.icon_count} file(s))")
            print(f"  {'':<14} {word}")
    if state.icon_theme_drift:
        # Per-theme, byte level, worded as bytes. Every regenerated icon differs
        # from the shipped file no matter what the recipe did, so for `mono` this
        # reads 97 while only 39 icons are repainted. Decoding all five sets to
        # say otherwise costs about 2s, the wrong trade for a status line;
        # `tdtheme icons diff <theme>` gives one theme's pixel count.
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
    # `icons` uses subparsers(required=True), so a branch above always returns:
    # a guard against adding a subcommand without one.


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
    # `name` is a required positional, so always set. A theme with no Icons/ is a
    # real state, indistinguishable from a typo unless the name is checked first.
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

    # Bytes and pixels disagree here, and only pixels answer the question anyone
    # is really asking. An authored icon always differs from the shipped file -
    # single-strip, re-compressed, with the ~5 KB of Photoshop metadata stripped
    # - so the byte count is 97 for every themed set, including `mono`, where a
    # grayscale recipe leaves 58 of the 97 pixel-for-pixel identical. Reporting
    # only bytes would make every recipe look equally aggressive.
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
    p.add_argument("--local", action="store_true",
                   help="capture into baseline.local/ (gitignored) instead of the "
                        "committed baseline/, and use it from then on - the right "
                        "choice on a TouchDesigner build other than the shipped one")
    p.add_argument("--i-know-this-is-shared", action="store_true",
                   help="with --force, confirm the committed baseline/ should be "
                        "replaced, so every other user of this checkout inherits "
                        "your build's capture")
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
    p.add_argument("--backup", action="store_true",
                   help="copy the outgoing stores and icons into backups/ first; "
                        "off by default because re-applying a theme restores them")
    p.add_argument("--allow-unknown", action="store_true",
                   help="write even though the install holds keys this baseline "
                        "has never seen, which drops them")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("reset", help="restore the stock look (an alias for 'apply default')")
    p.add_argument("--force", action="store_true",
                   help="write even if validation errors are present")
    p.add_argument("--no-icons", action="store_true",
                   help="reset colours and options only, leaving the icon set alone")
    p.add_argument("--backup", action="store_true",
                   help="copy the outgoing stores and icons into backups/ first; "
                        "off by default because re-applying a theme restores them")
    p.add_argument("--allow-unknown", action="store_true",
                   help="write even though the install holds keys this baseline "
                        "has never seen, which drops them")
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("uninstall",
                       help="restore the stock UI and remove the commands from PATH; "
                            "the checkout itself is kept")
    p.add_argument("--keep-files", action="store_true",
                   help="remove the commands but leave TouchDesigner's files "
                        "themed as they are now")
    p.set_defaults(func=cmd_uninstall)

    p = sub.add_parser("update",
                       help="pull changes to this checkout from its git remote")
    p.set_defaults(func=cmd_update)

    icons = sub.add_parser("icons", help="inspect icon sets")
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
