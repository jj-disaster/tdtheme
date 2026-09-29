#!/usr/bin/env python3
"""Generate the namespace index that parameter-reference.md §10.1 and §10.2 carry.

The index is one row per namespace: how many keys, and what that namespace is
believed to be. The shipped value of every individual key is *not* here — those
live in `baseline/TouchColors` and `baseline/TouchOptions`, are already in git,
and are the input to every `tdtheme apply`. Re-typing 809 values into markdown
was a second copy of those two files that could disagree with the first, and it
occupied 81% of §10.

What is here is the part that is not derivable: the annotation column. Those are
epistemic notes — [V] verified by probe, [O] ownership from a dylib string table,
[S] inferred from a naming convention, [?] name only — and they are knowledge
about a black box, not a property of the data. They live in the table below, so
this file is the single place either the counts or the notes are edited, and the
counts are checked against the baseline rather than trusted.

Usage:
    python3 tools/namespace_index.py            # print the index
    python3 tools/namespace_index.py --check    # exit 1 if the doc has drifted
    python3 tools/namespace_index.py --write    # splice it into the doc

`--check` is the whole staleness story: there is no CI here, so the question a
reader has is "is this index current?", and the answer is one command.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tdtheme as T  # noqa: E402

DOC = T.root / "docs" / "parameter-reference.md"

BEGIN = "<!-- BEGIN GENERATED: tools/namespace_index.py -->"
END = "<!-- END GENERATED: tools/namespace_index.py -->"

#: The seven `TouchColors` keys with no dot at all. They are not a namespace and
#: the naming grammar has nothing to say about them, so they get their own row
#: rather than being folded into a fabricated `CHOP.*`-style group.
BARE = "(bare)"

#: Repeated verbatim for every namespace nobody has looked at. Named once because
#: it is a single epistemic claim about a single kind of namespace; nine copies
#: of the sentence were nine chances to spell it differently.
UNCATALOGUED = "Uncatalogued namespace — name only, no evidence of behaviour."


# Annotations, keyed by (store, namespace). A namespace absent from this table
# has no note, which is itself information: the notes are the catalogue, and a
# namespace with no entry is one nobody has described. Every marker here is
# load-bearing — do not drop [V]/[O]/[S]/[?] when quoting this file.
# See parameter-reference.md §1 for what the markers mean.
NOTES: "OrderedDict[tuple[str, str], str]" = OrderedDict([
    # ---- TouchColors -------------------------------------------------------
    (("TouchColors", BARE),
     "**[S]** The seven base hues node tile shades are derived from, via the "
     "`OP.*.sat`/`.val` and `NODE.*.sat`/`.val` multipliers in `TouchOptions`. "
     "See §5.4 — **retint all seven together.**"),
    (("TouchColors", "parms"),
     "[S] **Parameter dialog widgets** — buttons, fields, menus, toggles, "
     "expression-mode variants. Largest namespace and the highest-value target "
     "for a dark theme: it is what the user stares at while building. `.expr.*` "
     "sets run parallel to base `.*` sets."),
    (("TouchColors", "tile"),
     "[V]/[O] **Network editor node tiles** — read by `libOPUI`. Covers "
     "backgrounds, borders, connectors, flags, icons, selection and error "
     "states. The best-documented namespace: see §6.1-6.4."),
    (("TouchColors", "georender"),
     "[?] **3D viewport / geometry rendering** — axes, grid, handles, guides, "
     "clipping planes. No individual probing. Name-based reading only; "
     "`georender.handle.*` and `georender.geo.*` are the two visible families."),
    (("TouchColors", "dat"),
     "[S] **Text editor.** Also the syntax-highlighting host: `dat.<lang>.<token>` "
     "— see §5.5. **Six** languages ship token sets, not the four §5.5 lists: "
     "`tscript` (5 keys) and `yaml` (6 keys) are missing from that table and use "
     "the same token vocabulary. Language-agnostic keys cover backgrounds, line "
     "numbers, comments, selection."),
    (("TouchColors", "default"),
     "[?] **Fallback tier** — none of these 46 has a specific twin. Precedence "
     "untested; see §6.8. Safe to set only where no specific key exists, which "
     "is all of them."),
    (("TouchColors", "jive"),
     "[V]/[O] **CHOP Channel Editor** (keyframes/curves) — read by `libJIVE`. "
     "`.bg`/`.fg` pairs on handles, slices, segments, slopes; `.plot.aux1-4` are "
     "curve families with `.mark` variants; `.timeline`/`.timemark`/"
     "`.currenttime` are the time ruler."),
    (("TouchColors", "chop"),
     "[?] **CHOP node track graph** — the mini animation graph drawn on CHOP "
     "nodes (distinct from the CHOP Channel Editor). Unprobed."),
    (("TouchColors", "oplist"),
     "[?] **Operator (OP) list** — the flat list of all operators in a network."),
    (("TouchColors", "preflist"),
     "[?] **Parameter list / preferences list.** Unprobed."),
    (("TouchColors", "textport"),
     "[?] **Textport** — the built-in Python console/log viewer. Tinting this "
     "changes console readability, a common dark-theme win. Unprobed."),
    (("TouchColors", "ramp"), UNCATALOGUED),
    (("TouchColors", "xcfladder"),
     "[?] **Expression/CF function ladder** — the value ladder beside fields. "
     "Colours plus the `xcfladder.*` options (box size, rechoose delay, steps "
     "per rotation)."),
    (("TouchColors", "worksheet"),
     "[V]/[O] **Network editor background** — read by `libOPUI`. Pairs with the "
     "`worksheet.*` options covering zoom, scroll, the wheel crossfade and "
     "autoscroll. Highest-impact key for overall feel: `worksheet.bg`."),
    (("TouchColors", "dialog"),
     "[V] **Dialogs and modal windows.** Contains the two keys with the "
     "empty-second-field quirk (see reverse-engineering.md §1)."),
    (("TouchColors", "textsheet"),
     "[?] **Text sheet** — the DAT spreadsheet/text editor view. Unprobed."),
    (("TouchColors", "top"),
     "[?] **TOP node / image viewer** colour keys. Unprobed."),
    (("TouchColors", "graph"),
     "[V] **Split ownership — read carefully.** The CHOP Channel Editor "
     "keyframe spline is `graph.line.*`, and it is in `TouchOptions`, not here. "
     "These five are `graph.grid.axes`, `graph.grid.axes.main`, `graph.grid.label`, "
     "`graph.grid.label.selected.bg` and `graph.separator`; `graph.separator` is "
     "one of the four keys `libCHILI` owns, and the `graph.grid.*` set is not "
     "attributed to any library. **Do not reason about `graph.*` from the name "
     "alone** — see §6.5-6.6 and §5.6."),
    (("TouchColors", "performance"),
     "[?] **Performance monitor** display. Unprobed."),
    (("TouchColors", "geodetail"),
     "[?] **Geometry detail / info panel.** Unprobed."),
    (("TouchColors", "icon"),
     "[?] **Icon rendering** colour keys, plus an `icon.blendtype` option."),
    (("TouchColors", "knob"),
     "[?] **Knob (rotary control) rendering.** Unprobed."),
    (("TouchColors", "mididevice"),
     "[?] **MIDI device / control surface UI.** Unprobed."),
    (("TouchColors", "overlap"),
     "[?] **Tile overlap indicator.** Unprobed."),
    (("TouchColors", "playbar"),
     "[?] **Playback / timeline bar.** Unprobed. Pairs with `playback.*`."),
    (("TouchColors", "range"),
     "[?] **Range / value slider component.** Unprobed."),
    (("TouchColors", "statusbar"),
     "[?] **Status bar** at the bottom of the network editor. Unprobed."),
    (("TouchColors", "MAT"), UNCATALOGUED),
    (("TouchColors", "SOP"), UNCATALOGUED),
    (("TouchColors", "channelexport"),
     "[?] **CHOP channel export** display. Unprobed."),
    (("TouchColors", "inputfield"),
     "[?] **Input field** component colours. Unprobed. Pairs with the `field.*` "
     "options."),
    (("TouchColors", "panel"),
     "[?] **Panel / palette component.** Unprobed."),
    (("TouchColors", "startup"),
     "[?] **Startup / splash screen.** Unprobed."),
    (("TouchColors", "tooltip"),
     "[?] **Tooltip** rendering. Unprobed."),
    (("TouchColors", "COMP"), UNCATALOGUED),
    (("TouchColors", "POP"), UNCATALOGUED),
    (("TouchColors", "circle"),
     "[?] **Circle / radial picker component.** Unprobed."),
    (("TouchColors", "colorbutton"),
     "[?] **Colour swatch button** component. Unprobed."),
    (("TouchColors", "desktop"),
     "[?] **Desktop / background root.** Unprobed. Candidate for the global base "
     "tone."),
    (("TouchColors", "extendedhelp"),
     "[?] **Extended help / doc viewer.** Unprobed."),
    (("TouchColors", "frameindicator"),
     "[?] **Frame / playback position indicator.** Unprobed."),
    (("TouchColors", "gadget"),
     "[?] **Generic widget base** colours. Unprobed. Note the singular spelling."),
    (("TouchColors", "grouplist"),
     "[?] **Group list / grouping UI.** Unprobed."),
    (("TouchColors", "lasso"),
     "[?] **Lasso / freeform select tool.** Unprobed."),
    (("TouchColors", "netoverview"),
     "[?] **Network overview (minimap).** Unprobed."),
    (("TouchColors", "opinfo"),
     "[?] **Operator info / tooltip panel.** Unprobed."),
    (("TouchColors", "slider"),
     "[?] **Slider component.** Unprobed."),
    (("TouchColors", "splitpane"),
     "[?] **Split-pane divider.** Unprobed."),
    (("TouchColors", "swatch"),
     "[?] **Colour swatch rendering.** Unprobed."),
    (("TouchColors", "OP"),
     UNCATALOGUED + " §5.4 says `OP` has no `TouchColors` entry; read that as "
     "the *bare* key — `OP.default` does ship here, at `0.67 0.67 0.67`."),
    (("TouchColors", "addoperator"),
     "[?] **\"Add Operator\" dialog.** Unprobed."),
    (("TouchColors", "image"),
     "[?] **Image / thumbnail rendering.** Unprobed."),
    (("TouchColors", "nodechooser"),
     "[?] **Node chooser / type picker.** Unprobed."),
    (("TouchColors", "playback"),
     "[?] **Playback controls.** Unprobed. Pairs with `playbar.*`."),
    (("TouchColors", "rubberbox"),
     "[?] **Rubber-band selection box** in the network editor. Unprobed."),

    # ---- TouchOptions ------------------------------------------------------
    (("TouchOptions", "tile"),
     "[V]/[O] **Network editor node tiles** — read by `libOPUI`. Sizes, "
     "alphas, blend types, timings, preview geometry. **Danger: `tile.*.size` "
     "and `tile.*.origsize` at 0 silently destroys layout (§3).**"),
    (("TouchOptions", "worksheet"),
     "[V]/[O] **Network editor background** — read by `libOPUI`. The options "
     "half of the pairing named above: zoom, scroll, the wheel crossfade, and "
     "autoscroll."),
    (("TouchOptions", "parms"),
     "[S] **Parameter dialog widgets** — sizes, margins and a label width, "
     "pairing with the 167 `parms.*` colour keys; see §5.1 for why so many of "
     "these need changing together."),
    (("TouchOptions", "font"),
     "[V] **Fonts.** Base sizes, xkerning, the operator-switch bitmap and delay, "
     "and two **legitimately empty** face names (see reverse-engineering.md §1). "
     "`font.relative.size 0` is a legal delta, not a bug — §3 rule 2. This is the "
     "namespace to change for overall text sizing."),
    (("TouchOptions", "graph"),
     "[V] **Split ownership — read carefully.** `graph.line.*` here is the CHOP "
     "Channel Editor keyframe spline: `alpha`, `select.alpha`, and the "
     "`select.multsat`/`select.multval` pair that modulate the curve's colour on "
     "selection. `graph.linewidth`, `graph.linewidth.dots`, `graph.linewidth.select` "
     "and `graph.pixelsperdot` belong to the CHOP track graph instead, and "
     "`graph.linewidth` is read by two libraries at once. See §6.5-6.6."),
    (("TouchOptions", "jive"),
     "[V]/[O] **CHOP Channel Editor** (keyframes/curves) — read by `libJIVE`. "
     "Handle widths, acceleration limits and a zoom divisor; the options half of "
     "the 39-key colour namespace above."),
    (("TouchOptions", "default"),
     "[?] **Fallback tier** — none of these has a specific twin. Precedence "
     "untested; see §6.8."),
    (("TouchOptions", "dat"),
     "[?] **Text editor behaviour** — comment highlight, line-number size, "
     "scroll width, table retention, word wrap. Not colour, and none of these is "
     "a `dat.<lang>.<token>` key; that set is in `TouchColors`. Unprobed."),
    (("TouchOptions", "viewer"),
     "[?] **Viewer panel** default size. Layout, not colour."),
    (("TouchOptions", "help"),
     "[?] **Help tooltip text and delay.** Options control the initial/recent "
     "delay and text length — behaviour, not just colour. Unprobed."),
    (("TouchOptions", "xcfladder"),
     "[?] **Expression/CF function ladder** — box size, rechoose delay, steps "
     "per rotation."),
    (("TouchOptions", "field"),
     "[?] **Input field** behaviour — cursor blink period and value-ladder "
     "delay. Not colour."),
    (("TouchOptions", "mouse"),
     "[?] **Mouse wheel** behaviour — boost and use-msec timing. Not colour."),
    (("TouchOptions", "chop"),
     "[?] **CHOP node track graph** — the mini animation graph drawn on CHOP "
     "nodes. Unprobed."),
    (("TouchOptions", "dragdrop"),
     "[V] **Drag-and-drop** hover delay in ms — a *timing* value despite the "
     "name. See §6.7."),
    (("TouchOptions", "file"),
     "[?] **File browser** — one key, branched-file timing. Not colour."),
    (("TouchOptions", "geo"),
     "[?] **Geometry viewer** — one key, orthographic floor grid size. Layout."),
    (("TouchOptions", "icon"),
     "[?] **Icon rendering** — the `blendtype` companion to the `icon.*` colour "
     "keys."),
    (("TouchOptions", "list"),
     "[?] **Generic list row** — one key, row height. Layout, not colour."),
    (("TouchOptions", "oplist"),
     "[?] **Operator (OP) list** — the flat list of all operators in a network."),
    (("TouchOptions", "osx"),
     "[?] **macOS platform integration** — one key (`osx.trackpad.zoom`). "
     "Behaviour, unprobed."),
    (("TouchOptions", "touch"),
     "[?] **Touch/multi-touch** — one key, click radius. Not colour."),
    (("TouchOptions", "OP"),
     "[S] **`OP.*.sat`/`.val`** — the saturation and value multipliers applied "
     "to the `OP` base hue: `hi`, `lo`, `lo2`, `editbg`. The sat/val pairing is "
     "unambiguous, but the exact compositing formula is **[?]** — it is not "
     "confirmed whether the base colour is converted to HSV, multiplied, and "
     "converted back, or whether these are lerps toward white/black. See §5.4."),
    (("TouchOptions", "NODE"),
     "[S] **`NODE.*.sat`/`.val`** — the same multipliers for node icon and "
     "border shades: `icon`, `innerborder`, `outerborder`. See §5.4."),
    (("TouchOptions", "CHOP"),
     "[S] **Default node size per operator type** — `CHOP.height` 90, "
     "`CHOP.width` 130. Unprobed."),
    (("TouchOptions", "COMP"),
     "[S] **Default node size per operator type** — `COMP.height` 130, "
     "`COMP.width` 160. Unprobed."),
    (("TouchOptions", "DAT"),
     "[S] **Default node size per operator type** — `DAT.height` 90, "
     "`DAT.width` 130. Unprobed."),
    (("TouchOptions", "MAT"),
     "[S] **Default node size per operator type** — `MAT.height` 90, "
     "`MAT.width` 130. Unprobed."),
    (("TouchOptions", "POP"),
     "[S] **Default node size per operator type** — `POP.height` 90, "
     "`POP.width` 130. Unprobed."),
    (("TouchOptions", "SOP"),
     "[S] **Default node size per operator type** — `SOP.height` 90, "
     "`SOP.width` 130. Unprobed."),
    (("TouchOptions", "TOP"),
     "[S] **Default node size per operator type** — `TOP.height` 90, "
     "`TOP.width` 130. Unprobed."),
])


HEADER = (
    "<!-- Generated by `tools/namespace_index.py`. Do not edit by hand; run\n"
    "     `python3 tools/namespace_index.py --write`, or `--check` to find out\n"
    "     whether this is stale. -->\n"
    "<!--\n"
    "One row per namespace: the key count, and what that namespace is believed\n"
    "to be. The markers are epistemic and are defined in §1.\n"
    "\n"
    "The shipped value of every individual key is NOT in this index. It lives in\n"
    "`baseline/TouchColors` and `baseline/TouchOptions` — one tab-separated line\n"
    "per key, already in git, and the input every `tdtheme apply` merges from.\n"
    "The counts here are checked against those files rather than trusted:\n"
    "`--check` fails if a key was added, removed or renamed.\n"
    "-->"
)


def group(data: T.TdFile) -> "OrderedDict[str, list[str]]":
    """Keys by the first dotted component, in file order.

    A key with no dot is not in any namespace. The seven bare operator-type
    names in `TouchColors` are the whole of that set, and pretending they were
    `CHOP.*` etc. is how an earlier version of this table came to list all
    seven twice - once as a base colour and once as an uncatalogued namespace.
    """
    groups: "OrderedDict[str, list[str]]" = OrderedDict()
    for key in data.keys():
        groups.setdefault(key.split(".")[0] if "." in key else BARE, []).append(key)
    return groups


def rows(store: str, path: Path) -> "list[tuple[str, int, str]]":
    """(label, key count, annotation) for one store, biggest namespace first.

    Sorted by count then name so the biggest target is the first thing read, and
    so the output does not move when a key is added to a small namespace.
    """
    parsed = T.load_file(path, store)
    groups = group(parsed)
    ordered = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    return [(name, len(keys), NOTES.get((store, name), ""))
            for name, keys in ordered]


def table(entries: "list[tuple[str, int, str]]") -> "list[str]":
    out = ["| Namespace | Keys | What it is |", "|---|---:|---|"]
    for name, count, note in entries:
        label = "no dot in the key" if name == BARE else f"`{name}.*`"
        out.append(f"| {label} | {count} | {note} |")
    return out


def section(entries: "list[tuple[str, int, str]]", *, lead: bool) -> "list[str]":
    lines = [BEGIN]
    if lead:
        lines += HEADER.split("\n")
    lines += table(entries)
    lines += [END]
    return lines


def generate() -> "list[list[str]]":
    """The marked regions, one list of lines per section, in document order."""
    colors = rows(T.TOUCHCOLORS, T.baseline_dir / T.TOUCHCOLORS)
    options = rows(T.TOUCHOPTIONS, T.baseline_dir / T.TOUCHOPTIONS)
    return [section(colors, lead=True), section(options, lead=False)]


def splice(existing: str, blocks: "list[list[str]]") -> str:
    """Replace each marked region in `existing` with the next generated block.

    Searches forward from the end of the previous region, because a generated
    block carries its own markers and would otherwise be found again as the next
    region. Raises if the document does not have exactly one region per block,
    rather than quietly appending: a doc that lost its markers should fail the
    check, not be rewritten into a shape nobody chose.
    """
    text = existing
    cursor = 0
    for number, block in enumerate(blocks, 1):
        start = text.find(BEGIN, cursor)
        end = text.find(END, start + 1) if start >= 0 else -1
        if start < 0 or end < 0:
            raise SystemExit(
                f"error: {DOC.name} has no complete generated region to fill "
                f"(block {number} of {len(blocks)}). Re-add the {BEGIN!r} / "
                f"{END!r} markers around the old table and retry."
            )
        cursor = end + len(END)
        text = text[:start] + "\n".join(block) + text[cursor:]
    if BEGIN in text[cursor:]:
        raise SystemExit(
            f"error: {DOC.name} has more generated regions than the "
            f"{len(blocks)} this script produces. Remove the extra markers."
        )
    return text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="namespace_index",
        description="Generate the per-namespace key index in "
                    "docs/parameter-reference.md §10.1 and §10.2. The counts come "
                    "from baseline/ on every run; the annotations are the "
                    "epistemic notes, held in this file.",
    )
    group_args = parser.add_mutually_exclusive_group()
    group_args.add_argument("--check", action="store_true",
                            help="exit non-zero if the document differs from "
                                 "what would be generated")
    group_args.add_argument("--write", action="store_true",
                            help="splice the index into the document")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    blocks = generate()

    if not args.write and not args.check:
        for block in blocks:
            print("\n".join(block))
        return 0

    current = DOC.read_text(encoding="utf-8")
    generated = splice(current, blocks)

    if args.write:
        if current == generated:
            print(f"{DOC.name}: already current")
        else:
            DOC.write_text(generated, encoding="utf-8")
            print(f"{DOC.name}: index written")
        return 0

    if current == generated:
        print(f"{DOC.name}: index is current")
        return 0
    print(f"error: {DOC.name} §10 does not match what "
          f"tools/namespace_index.py generates.", file=sys.stderr)
    for line in difflib.unified_diff(
        current.split("\n"), generated.split("\n"),
        f"{DOC.name} (on disk)", "namespace_index.py (generated)",
        lineterm="", n=1,
    ):
        print(line, file=sys.stderr)
    print("\nRun: python3 tools/namespace_index.py --write", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
