#!/usr/bin/env python3
"""Command line for authoring TouchDesigner themes.

    python3 cli.py list
    python3 cli.py build midnight
    python3 cli.py build midnight --check
    python3 cli.py diff midnight
    python3 cli.py preview midnight
    python3 cli.py export mytheme

`build` writes into the target theme's `Icons/` directory by default, which is
where `tdtheme apply` reads from. It refuses to overwrite an existing set
without `--force`, because a recipe cannot always reproduce what is already
there: `defaultnowarn` has an empty recipe and one hand-placed icon, so
rebuilding it would silently put the stock warning face back.

`export` goes the other way, recording the live install as a new theme. To
install a finished theme, use tdtheme.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import theme as G
import tdthememaker as F

HERE = Path(__file__).resolve().parent
RECIPES = HERE / "recipes"
TDTHEME = HERE.parent / "tdtheme"
BASELINE = TDTHEME / "baseline" / "Icons"
THEMES = TDTHEME / "themes"

EXIT_OK, EXIT_ERROR, EXIT_CHANGED = 0, 1, 2


def _recipe(name: str) -> Path:
    path = RECIPES / f"{name}.recipe.json"
    if not path.exists():
        known = ", ".join(sorted(p.name[:-12] for p in RECIPES.glob("*.recipe.json")))
        sys.exit(f"error: no recipe for {name!r}. Known: {known or '(none)'}")
    return path


def _themes_with_recipes() -> list[str]:
    return sorted(p.name[: -len(".recipe.json")] for p in RECIPES.glob("*.recipe.json"))


def _bar(done: int, total: int, name: str) -> None:
    # Only draw to a terminal. Carriage returns are correct on a tty and
    # unreadable in a pipe, where 97 updates become one enormous line.
    if not sys.stdout.isatty():
        return
    end = "\n" if done == total else "\r"
    print(f"  [{done:>3}/{total}] {name[:44]:<44}", end=end, flush=True)


# ------------------------------------------------------------------ list

def cmd_list(args) -> int:
    rows = []
    for name in _themes_with_recipes():
        recipe = json.loads(_recipe(name).read_text())
        ops = recipe.get("ops") or []
        target = THEMES / name / "Icons"
        shipped = len(F.icon_names(target)) if target.is_dir() else 0
        rows.append((name, len(ops), shipped))
    if not rows:
        print("no recipes")
        return EXIT_OK
    print(f"  {'recipe':<16} {'ops':>4} {'icons':>6}")
    for name, ops, shipped in rows:
        print(f"  {name:<16} {ops:>4} {shipped:>6}")
    print(f"\n  recipes in {RECIPES}")
    print(f"  baseline in {BASELINE}")
    return EXIT_OK


def _classify(delta: dict, installed: list[str]) -> tuple[list, list, list, list]:
    """Split a `pixel_diff` result into the four things a caller cares about.

    `pixel_diff` only reports files that differ in *bytes*, then says whether
    the pixels moved. So "absent from the result" means byte-identical, and
    "identical" means re-encoded but visually the same. Only `changed` and the
    structural states are real changes.
    """
    changed = [n for n, v in delta.items() if v == "changed"]
    structural = [n for n, v in delta.items() if v in ("added", "absent")]
    broken = [n for n, v in delta.items() if str(v).startswith("unreadable")]
    reencoded = [n for n, v in delta.items() if v == "identical"]
    return changed, structural, broken, reencoded


# ----------------------------------------------------------------- build

def cmd_build(args) -> int:
    recipe = json.loads(_recipe(args.name).read_text())
    compression = F.COMPRESSION_NONE if args.no_compress else F.COMPRESSION_LZW
    progress = None if args.quiet or not sys.stdout.isatty() else _bar
    existing = F.icon_names(THEMES / args.name / "Icons") if not args.out else []

    # `--check` must never write. Build to a scratch directory and report the
    # difference, because a recipe cannot always reproduce what is on disk: a
    # hand-placed icon is invisible to the recipe and would be lost silently.
    if args.check:
        import tempfile
        with tempfile.TemporaryDirectory() as scratch:
            result = F.apply_recipe(BASELINE, Path(scratch), recipe,
                                    compression=compression, progress=progress)
            print(f"would write {result['written']} icons from "
                  f"{args.name}.recipe.json")
            _summarise(result)
            target = Path(args.out) if args.out else THEMES / args.name / "Icons"
            installed = F.icon_names(target) if target.is_dir() else []
            if not installed:
                print(f"    {target} is empty or absent, so nothing would be lost")
                return EXIT_OK
            # `pixel_diff` reports only the files that differ in bytes, so an
            # installed icon the recipe reproduces exactly is simply absent
            # from it. Absence is the good case; the denominator has to be the
            # installed count, not the length of the delta.
            changed, structural, broken, reencoded = _classify(
                F.pixel_diff(target, Path(scratch)), installed)
            lost = changed + structural
            for icon in broken:
                print(f"    cannot read installed {icon}")
            print(f"    {len(lost)} of {len(installed)} installed icons would change")
            for icon in lost:
                print(f"      {icon}  ({F.pixel_diff(target, Path(scratch))[icon]})")
            if reencoded:
                print(f"    {len(reencoded)} more would be re-encoded but look "
                      "the same")
            if not lost and not broken:
                print("    the recipe reproduces the installed set exactly")
            return EXIT_CHANGED if (lost or broken) else EXIT_OK

    destination = Path(args.out) if args.out else THEMES / args.name / "Icons"
    if destination.is_dir() and F.icon_names(destination) and not args.force:
        count = len(F.icon_names(destination))
        print(f"error: {destination} already holds {count} icons.", file=sys.stderr)
        print("       a recipe cannot reproduce a hand-edited set, so "
              "overwriting is opt-in:", file=sys.stderr)
        print("         --check   report what would change, write nothing",
              file=sys.stderr)
        print("         --force   overwrite", file=sys.stderr)
        return EXIT_ERROR

    try:
        result = F.apply_recipe(BASELINE, destination, recipe,
                                compression=compression, progress=progress)
    except F.IconError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(f"{'Wrote' if result.get('verbatim') else 'Rebuilt'} "
          f"{result['written']} icons for {args.name!r} into {destination}")
    _summarise(result)
    return EXIT_OK


def _summarise(result: dict) -> None:
    print(f"    {result['bytes'] / 1024:.0f} KB, "
          f"compression={'none' if result['compression'] == 1 else 'lzw'}")
    if result.get("verbatim"):
        print("    copied byte for byte from the baseline: the recipe has no ops, "
              "so this is a lossless reset")
    else:
        changed = result["written"] - result["pixel_identical_to_baseline"]
        print(f"    {result['transformed']} transformed by an op, "
              f"{result['untransformed']} passed through")
        print(f"    {changed} of {result['written']} changed pixels; the other "
              f"{result['pixel_identical_to_baseline']} came out identical to "
              "the shipped icon")


# ------------------------------------------------------------------ diff

def cmd_diff(args) -> int:
    name = args.name
    other = Path(args.icons) if args.icons else THEMES / name / "Icons"
    if not other.is_dir():
        print(f"error: no icon directory at {other}", file=sys.stderr)
        return EXIT_ERROR

    if args.recipe:
        # What the recipe would produce, against what is installed.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            F.apply_recipe(BASELINE, Path(tmp),
                           json.loads(_recipe(name).read_text()))
            result = F.pixel_diff(Path(tmp), other)
    else:
        result = F.pixel_diff(BASELINE, other)

    installed = F.icon_names(other)
    if args.bytes:
        changed = [n for n, v in result.items() if v == "changed"]
        structural = [n for n, v in result.items() if v in ("added", "absent")]
        reencoded = []
    else:
        changed, structural, broken, reencoded = _classify(result, installed)
        for icon in broken:
            print(f"  {icon:<28} {result[icon]}")
    for icon in changed:
        print(f"  {icon:<28} repainted")
    for icon in structural:
        print(f"  {icon:<28} {result[icon]}")
    total = len(result) if args.bytes else len(installed)
    against = "the recipe" if args.recipe else "the baseline"
    print(f"\n  {len(changed)} of {total} repainted, measured against {against}")
    if reencoded:
        print(f"  {len(reencoded)} more differ in bytes but are pixel-identical")
    return EXIT_CHANGED if (changed or structural) else EXIT_OK


# --------------------------------------------------------------- preview

def cmd_preview(args) -> int:
    name = args.name
    source = Path(args.icons) if args.icons else THEMES / name / "Icons"
    if not source.is_dir():
        source = BASELINE
        label = "baseline"
    else:
        label = name
    images = [(n, F.read_tiff((source / n).read_bytes()))
              for n in F.icon_names(source)]
    if not images:
        print(f"error: no icons in {source}", file=sys.stderr)
        return EXIT_ERROR
    sheet = F.contact_sheet(images, columns=args.columns, cell=args.cell)
    out = Path(args.out) if args.out else HERE / "preview" / f"{label}-icons.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(F.png_bytes(sheet))
    print(f"wrote {out} ({len(images)} icons)")
    return EXIT_OK


# ---------------------------------------------------------------- export

def cmd_export(args) -> int:
    try:
        written = G.export_theme(args.name, force=args.force)
    except (G.ExportError, G.ThemeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(f"Exported the current TouchDesigner state as theme {args.name!r}:")
    for line in G.export_report(args.name, written):
        print(line)
    print("\nThe theme records only what differs from baseline. Edit the YAML "
          "by hand to build it further.")
    icons = [n for n in written if n not in G.STORE_FILES]
    if icons:
        print("The icons were copied as-is, so this theme is reproducible only "
              "as bytes. If they came from a recipe, keep it; if they were "
              "placed by hand, no recipe describes them. Check with:")
        print(f"    python3 cli.py build {args.name} --check")
    return EXIT_OK


# ------------------------------------------------------------------ main

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tdthememaker", description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Builds icon sets from recipes and exports the current install "
               "as a theme. To install a finished theme, use tdtheme.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="show the available recipes")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("build", help="generate an icon set from its recipe")
    p.add_argument("name")
    p.add_argument("--out", help="destination directory (default: the theme's Icons/)")
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing set")
    p.add_argument("--check", action="store_true",
                   help="build, report, and do not fail on an existing set")
    p.add_argument("--no-compress", action="store_true",
                   help="write uncompressed TIFFs (larger, but faster to read)")
    p.add_argument("--quiet", action="store_true", help="no progress bar")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("diff", help="compare an icon set against the baseline")
    p.add_argument("name")
    p.add_argument("--icons", help="set to compare (default: the theme's Icons/)")
    p.add_argument("--recipe", action="store_true",
                   help="compare what the recipe produces instead of the baseline")
    p.add_argument("--bytes", action="store_true", help="compare bytes, not pixels")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser(
        "export", help="record the installed state as a new theme")
    p.add_argument("name")
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing theme of this name")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("preview", help="render a contact sheet PNG")
    p.add_argument("name")
    p.add_argument("--icons", help="set to render (default: the theme's Icons/)")
    p.add_argument("--out", help="output PNG path")
    p.add_argument("--columns", type=int, default=10)
    p.add_argument("--cell", type=int, default=64)
    p.set_defaults(func=cmd_preview)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except F.IconError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except BrokenPipeError:
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
