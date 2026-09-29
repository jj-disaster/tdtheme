# TouchDesigner UI parameter reference

What each key in `TouchColors` and `TouchOptions` is understood to do, for
authors (human or agent) building themes with `tdtheme`.

**Read section 1 before using this document.** Derivative documents none of
this. The source of truth is a black box, and the honest summary of the
knowledge below is: *the format is fully understood, a small number of keys
have been empirically verified, and the majority of individual keys are
understood only through naming convention.* Sections 5 and 6 are the parts
you can rely on. Section 9 is the part you must not trust blindly.

---

## 1. Epistemic contract

Every claim in this document carries a marker. **Do not drop them when
quoting this file.**

| Marker | Meaning | Safe to rely on? |
|---|---|---|
| **[V]** | **Verified empirically.** A value was written, TouchDesigner was restarted, and the result was observed. | Yes |
| **[O]** | **Ownership established.** Which library reads the namespace, via symbol/string-table analysis of the shipped dylibs. | Yes |
| **[S]** | **Structural.** Inferred from a naming convention that holds consistently across dozens of keys, corroborated by value patterns. Not individually probed. | Yes, for the convention. No, for any single key |
| **[?]** | **Unknown.** Name only. No evidence of behaviour beyond the name itself. | **No** |

The distinction between **[S]** and **[?]** matters most. `tile.clone.bg.disabled`
is **[S]** — the `.bg.disabled` suffix appears on many keys and the value
corroborates it. `tile.commented` is **[?]** — nothing tells us what
"commented" renders, only that the name suggests a comment marker.

**Absence of evidence is not evidence of absence.** A key being undocumented
here does not mean it is unused, and a key appearing in a dylib's string table
does not prove it is read. The reverse-engineering notes record cases of keys
built at runtime by string concatenation (`.hilite` appended to `.line`), where
static analysis sees no string at all.

---

## 2. File formats — **[V]**

Stated in full in
[reverse-engineering.md §1](reverse-engineering.md) ("The two stores"), which is
the longer account and the one to read. It carries the per-file shape and key
counts; the `UIgetOptions()->getOption*` and `UIgetColors()->getColor<UT_Color>`
call sites that decide which store owns a key, and the finding that the two
stores share **zero** keys; and every format quirk —

- the two `dialog.commenthint*` keys carrying a stray empty second field, and
  therefore that **colour is the LAST THREE fields, not fields 2-4**. A parser
  reading fields 2-4 gets the wrong value; a serializer writing back only RGB
  silently drops the empty field. This is why `tdtheme.py` keeps a file's *full
  field list* after the key rather than a decoded triple.
- the two `font.*.face` option values that are **legitimately empty** — an empty
  value is not a missing key.
- **colour channels are not clamped to 1.0** — `POP.hilite` ships as
  `0.56 0.6 1.2` on purpose. Do not "fix" it.
- blank-line preservation, and the no-comments / no-duplicate-keys / pure-ASCII
  / trailing-newline guarantees.

and the byte-exact round-trip through `tdtheme`, which is the load-bearing
assumption of the whole tool and is gated by `tests/test_roundtrip.py`.

---

## 3. Hard safety rules — **[V]**

Violating these does not produce an error from TouchDesigner. It produces a
wrong-looking UI or a broken layout, silently.

1. **`tile.*.size` and `tile.*.origsize` must be > 0.** Zeroing
   `tile.inout.origsize` collapses both the input/output connectors *and* the
   node's top border to thin slivers. TouchDesigner reports no error. This is
   the one class `tdtheme` hard-errors on.
2. **Do not zero other size keys either** — but understand why the shipped
   `font.relative.size 0` is legal: it is a *delta* from the base font size,
   not an absolute size. `tdtheme` judges non-tile size keys relative to the
   baseline for exactly this reason. An unconditional `> 0` rule would
   false-positive on the untouched shipped file and fail every apply.
3. **Set either `X` or `default.X`, not both [?].** The precedence between
   tiers is **untested**. `tdtheme` warns rather than guessing.
4. **Never introduce a new key.** A TouchDesigner update can add keys; an
   unknown key in a theme is almost always a typo. `tdtheme` warns.

---

## 4. How TouchDesigner treats these files — **[V]**

**Measured, not assumed** — and recorded in full, including the correction of an
earlier wrong claim about data loss, in
[reverse-engineering.md §4](reverse-engineering.md) ("What TouchDesigner does
with these files"). The short form: TouchDesigner **reads** both stores at
startup and **never writes** them, so editing while it runs is safe and a restart
is needed only for a change to become *visible* — a display refresh, not a
durability concern. The icons are the exception: they are read lazily on first
use and memoised for the process, so an icon swap is not picked up even to look
at until the app restarts. `./check-td-writes` re-verifies all of this on a new
build, and covers the `Icons` directory as well as both stores.

A TouchDesigner update also **wipes both files**. `tdtheme status` compares the
live build against the one recorded in the baseline and warns on a mismatch;
`tdtheme capture --force` refreshes it. A new build may also ship new or resized
icons (`validate_icons` reports those per theme) and a new `ui.tox`.

---

## 5. The naming grammar — **[S]**

This is the highest-value section for a theme author, because the convention
generalises: you can read an undocumented key's role off its name with
reasonable confidence, even where the individual key is untested.

### 5.1 Role suffixes

| Suffix | Role | Confidence |
|---|---|---|
| `.bg` | background fill | [S] — 104 keys, by far the most common |
| `.fg` | foreground / text or glyph colour | [S] — 69 keys |
| `.border` | outline | [S] |
| `.border.inner` / `.border.top` | the two-tone bevel on a raised widget | [S] — `parms.button.border.inner` is darker than `.border.top`, consistent with a lit-from-above bevel |
| `.outline` | outer edge | [S] — 18 keys |
| `.hilite` | hover or highlight state | [S] — 29 keys. **The network editor has no `hover` key**; `.hilite` is what hover uses **[V]** |
| `.sel` / `.selected` | selected state | [S] |
| `.disabled` | disabled/greyed state | [S] |
| `.loc` | "local" variant | [?] — appears 33 times, often beside a base key, but the precise distinction is unverified |
| `.expr` | expression-mode variant of a widget | [S] — `parms.button.expr.*` is a parallel key set beside `parms.button.*` |
| `.label` | the text label of a control | [S] |
| `.current` | the currently active/live item | [S] |
| `.picked` | click-picked item | [S] — pairs with `.current`; e.g. `jive.channel.current` is green, `jive.channel.picked` is yellow |

**Modifier keys generally sit in `TouchOptions`, not `TouchColors` [V].** A
colour key in `TouchColors` is paired with an *alpha* or *blend mode* in
`TouchOptions` under a similar name. Examples: `tile.droppable` (colour) +
`tile.droppable.alpha` (opacity) + `tile.droppable.blendtype` (blend mode);
`worksheet.grid` (colour) +
`worksheet.grid.alpha` (opacity) + `worksheet.grid.blendtype` (blend mode).
This split is why many visual properties need **two** files edited.

### 5.2 `blendtype` — **[?]**

`TouchOptions` contains several `blendtype` keys taking small integers. The
shipped values in use are **1, 2, 3, and 9**. The enum is **not decoded**. Do
not guess which value means additive, multiply, or screen — writing a wrong
value will change how a tile composites and the effect is not reversible by
inspection. If you need to change a blend mode, change one key, restart, look,
and record the result here.

### 5.3 Alpha is a multiplier, not a colour

Alpha values in `TouchOptions` ship across a wide range (`0`, `0.1`, `0.2`,
`0.5`, `0.52`, `0.6`, `0.65`, `0.7`, `0.8`, `1`) and are **[S]** applied as
opacity on top of the corresponding `TouchColors` entry. Setting alpha to `0`
hides the element; this is the clean way to disable a decoration (e.g.
`worksheet.grid.alpha 0` to hide the network grid) without inventing a colour
that clashes with an unknown background.

### 5.4 Node type base colours and HSV modulation — **[S]**

Seven keys in `TouchColors` are bare operator-type names with no suffix:

```
CHOP  COMP  DAT  MAT  POP  SOP  TOP
```

These are the **base hues** for node tiles. Only seven exist; `OP` itself has
**no** entry in `TouchColors` (only `OP.*` options).

`TouchOptions` then derives several shades from each base via **saturation and
value multipliers**:

```
NODE.icon.sat        1.8    NODE.icon.val        0.27
NODE.innerborder.sat 0.9    NODE.innerborder.val 0.625
NODE.outerborder.sat 1      NODE.outerborder.val 1
OP.hi.sat            1      OP.hi.val            1.7
OP.lo.sat            1.2    OP.lo.val            0.5
OP.lo2.sat           1      OP.lo2.val           0.3
OP.editbg.sat        0.8    OP.editbg.val        1.2
```

Read together: `hi` brightens (val 1.7), `lo` darkens and saturates
(val 0.5), `lo2` darkens hard (val 0.3), `icon` desaturates hard while
brightening (sat 1.8, val 0.27). **[S]** — the sat/val pairing is
unambiguous, but the exact compositing formula is **[?]**; it has not been
confirmed whether the base colour is converted to HSV, multiplied, and
converted back, or whether these are lerps toward white/black.

**Practical upshot for theming: retint all seven base colours together.**
Changing only `CHOP` leaves its derived highlight, low, and icon shades
inconsistent, because those are computed from the base rather than stored. The
multipliers themselves are shared by all types, so they are global levers, not
per-type.

### 5.5 `dat.<lang>.<token>` is syntax highlighting — **[S]**

`dat` (50 keys) is the text editor, and four languages have token-level colour
sets. The token vocabulary is near-identical across languages, which is what
makes the convention trustworthy:

| Language | Tokens present |
|---|---|
| `dat.python` | comments, editable, editing, functions, hilite, keywords, noteditable, numbers, strings |
| `dat.glsl` | comments, functions, keywords, numbers, preprocessors, strings |
| `dat.json` | booleans, comments, numbers, strings, symbols |
| `dat.xml` | attributes, attributevalues, comments, entityrefs, symbols, tags |

Retinting these changes code-editor syntax colours only — the most visible
single win available for a dark theme, and entirely independent of the rest of
the UI. `dat.python.comments` ships `0.6 1 0.6` (green) and
`dat.python.strings` `1 0.66 0.66` (salmon), i.e. conventional.

### 5.6 Library ownership — **[O]**

The three-way table of which shipped dylib
(`TouchDesigner.app/Contents/Frameworks/*.dylib`) owns which namespace —
`libOPUI` for `tile.*` and `worksheet.*` with **zero** `graph.*`, `libJIVE` for
`jive.*` and `graph.line.*`, `libCHILI` for the CHOP track graph — is in
[reverse-engineering.md §2](reverse-engineering.md) ("Library ownership"),
together with the evidence behind the `libJIVE` half: string adjacency to
`jive.segment.bg`, and RTTI `SI_Segment` / `SI_Slope` / `SI_Accel`.

This is why `graph.*` is dangerous to reason about from the name alone: see
§6.5-6.6.

---

## 6. Verified keys — **[V]**

These are the individual keys whose behaviour was established by writing a
value, restarting TouchDesigner, and observing the result. Prefer these when
choosing what to theme.

### 6.1 `tile.connection.hilite1` / `tile.connection.hilite2` — network wire hover

The **hover** highlight colour of network-editor wires. A pair cached 16 bytes
apart. Probe result: setting them turns wires **red on hover**. This is the
only confirmed wire-colour control.

### 6.2 Resting wire colour — **[?] UNRESOLVED**

**No key has been identified that sets the resting (non-hovered) wire colour.**
`tile.dockedconnection.line` and `default.tile.line` were each probed alone
with no visible effect on non-hovered wires. Do not assume
`tile.dockedconnection.line` is it.

### 6.3 There is no wire-width knob — **[V]**

Only three `drawLine` call sites exist in `libOPUI`, all with signature
`drawLine(x0, y0, x1, y1, r, g, b, alpha)` — **no width argument**. Stroke
thickness is implicit in the buffered drawer. **Wire appearance is
colour-only**; there is nothing to widen.

### 6.4 `tile.inout.origsize` — scale baseline, not just connector size

Shipped `10`. Setting it to `0` collapses **both** the input/output nubs
**and** the node's top border. Confirmed by isolation test. Sibling baselines:
`tile.flagh.origsize 18`, `tile.flagv.origsize 24`. This is the canonical
"silent layout destruction" case — see §3.

### 6.5 `graph.line.*` is NOT about network wires — **[V]**

Despite the name, `graph.line.*` is the **keyframe spline in the CHOP Channel
Editor**. `graph.line.select.multsat` and `graph.line.select.multval` are a
*pair* modulating the curve's colour on selection (saturation and value), not
two separate curves.

### 6.6 `graph.linewidth` is a name collision — **[V]**

The same flat key is read by **both** `libJIVE` and `libCHILI`, so one value
affects two unrelated renderers at once. Changing it cannot be reasoned about
in isolation.

### 6.7 `dragdrop.hover.delay.msecs` is a timing value — **[V]**

Despite "hover", this is milliseconds of delay. The network editor has zero
"hover" strings in its binaries; highlight is expressed via `.hilite`.

### 6.8 The `default.*` tier — exists, precedence untested

`TouchColors` has 46 `default.*` keys; `TouchOptions` has 7. Examples:
`default.tile.line`, `default.bg`, `default.fg`, `default.button`,
`default.field.bg`.

- **None of the 46 has a specific twin. [V]** `default.tile.line` exists;
  `tile.line` does not. For these keys the `default.` form is the only form.
- **Which tier wins if both are set is untested. [?]** See §3 rule 3.

---

## 7. Verified negative findings

Things confirmed *not* to work, recorded so they are not re-litigated. The
canonical list is [reverse-engineering.md §6](reverse-engineering.md) ("Ruled
out"), which is where each of these was ruled out and what the evidence was:

- **No built-in theme switcher [V]** — no theme/colourscheme UI strings in
  `libOPUI` or the main binary, and no third-party tool.
- **No live-reload or scripting API for these files [V]** — port 8888 is Jupyter,
  not TouchDesigner. Hence apply-then-restart.
- **Legacy `resources*` files are dead [O]** — the seven `resources*` variants
  are a parallel `Key:<TAB>value` system with X11 colour names, and the
  filenames and several of their keys appear nowhere in the binaries. That
  section also records the one apparent counter-example (`ForegroundColor` and
  `LineColor` *do* appear, but not in a way that ties them to these files) and
  why it does not count.
- **Runtime tinting of icons** — nothing in `libUI` suggests the glyphs are
  colour-managed or modulated by a `TouchColors` key. Listed there only.

- **TouchDesigner does not write these files [V]** — see §4.

---

## 8. Namespaces NOT covered by tdtheme

The three further colour systems that live in the same config directory in
different formats, and that `tdtheme` neither reads nor writes — so a theme will
not affect them, and each would be one more parser behind the same interface —
are tabulated in
[reverse-engineering.md §6](reverse-engineering.md), under "Not covered":

| File(s) | Format | Controls |
|---|---|---|
| `colorPalette.def`, `opColorPalette.def` | count header + rows | Palette browser colour wheels |
| `3DSceneColors` (`.grey`/`.bw`/`.wb` variants) | `Key:<TAB>r g b<SPACE># comment` | 3D viewport |
| `MiscColors` | same | locks, pending changes, keyframes |

Do not copy parsing logic between them and `TouchColors`: these use
**space-separated** RGB in a single field plus an optional `#` comment, which is
a different format.

---

## 9. Known gaps in this reference and in the tooling

Stated plainly so a future author does not mistake absence for safety.

### 9.1 Most individual keys are unverified

Of 626 `TouchColors` keys and 183 `TouchOptions` keys, only the small set in
§6 has confirmed behaviour. The shipped value of every key is **factual** and
lives in `baseline/TouchColors` and `baseline/TouchOptions`; §10 indexes the
namespaces and says how big each one is, but its per-namespace annotations are
**[S]** convention inference or **[?]** unknown, not measurement.
`georender.*` (51 keys), `parms.*` (167 keys), and the `default.*` tiers in
particular have had no individual probing at all.

### 9.2 The `blendtype` enum is undecoded — see §5.2

### 9.3 Colour field arity — was a real gap, now closed

A real one: a hand-edit replaced the first channel of `worksheet.grid` with two
numbers separated by a **space** where a tab belonged, and `validate` reported
**zero findings** for it, so `apply` would have written the corruption straight
back into the install. `export` (now `tdthememaker export`) had captured it
faithfully, which is how it was found at all.

**Now fixed**, and enforced as a hard **error** that blocks `apply`.
`_check_color_fields` (`tdtheme.py`) rejects a field count that differs from the
baseline's count for that key, any field containing internal whitespace, and a
non-numeric field where the baseline's corresponding field was numeric. Every
rule is stated *relative to the baseline* rather than absolutely, so the two
`dialog.commenthint*` keys keep their 4-field shape without tripping it, and a
future build that changes a key's shape will not cause mass false positives. All
626 pristine baseline keys validate clean, and
`tests/test_tdtheme.py` pins that.

**Author guidance: every `TouchColors` value must have exactly three
space-free numeric fields**, except the two documented `dialog.commenthint*`
keys which have four with an empty second. A space inside a field is always an
error. The tool now enforces this, but the error blocks the write — fix the
YAML rather than reaching for `--force`.

### 9.4 `validate` cannot see semantic mistakes

It checks five things: tile geometry at zero, size keys against the baseline,
unknown keys, colour-channel field integrity (§9.3), and a theme setting both
`X` and `default.X` (§3 rule 3, §6.8). It cannot tell that a
colour is illegible against its background, that two keys should have been
changed together (§5.4), or that a value is a typo for another key.

### 9.5 Icon validation is a separate pass

Everything above concerns the two colour stores. Icons are validated
independently, by `validate_icons`, and a theme can have a structurally perfect
colour store and still ship a wrong or incomplete icon set. `tdtheme apply` runs
both and refuses to write if either reports an error.

`validate_icons` checks five things, and like the colour pass it cannot see
semantics:

| Check | Severity | Catches |
|---|---|---|
| the set is non-empty | error | a theme that would install no icons at all |
| every file decodes as TIFF | error | a file TouchDesigner cannot read at all — it logs "Couldn't find icon" and draws nothing |
| dimensions match the baseline | error | a resized glyph; TouchDesigner sizes most of these from the file, so it renders visibly wrong |
| a name is not in the baseline | warning | a new icon, or one from a different TouchDesigner build — it will be written, but nothing here can vouch for it |
| a baseline icon is missing from the theme | warning | harmless (the icon keeps its baseline bytes) but almost always means the set was generated from a stale baseline |

Two limits worth stating. The decode check confirms the *file* is readable, not
that it looks right: an icon whose alpha convention is wrong decodes cleanly and
renders blocky, which is why the alpha traps in `docs/reverse-engineering.md`
are checked against `sips` in the test suite rather than inferred here. And a
warning is not a failure — it is a note that the set and the baseline disagree
in a way a human should look at.

---

## 10. Namespace index

One row per namespace: how many keys it holds, and what it is believed to be.

**Per-key shipped values are not in this document.** Every one of them is in
`baseline/TouchColors` and `baseline/TouchOptions` — one tab-separated line per
key, already in git, and the input every `tdtheme apply` merges from. Both
tables below are generated by `tools/namespace_index.py`; run
`python3 tools/namespace_index.py --check` to find out whether they have drifted
from those files, and `--write` to bring them back in line. The annotations are
the epistemic notes and live in that script too, so it is the single place
either the counts or the notes are edited.

The **keys** column is the only factual one, and it says nothing about what the
keys do. For that, see the grammar (§5) and the verified findings (§6).

Format of the baseline files, for reading them directly: `TouchColors` = three
fields, except `dialog.commenthint*` = four. `TouchOptions` = one field, except
two empty-valued font faces.

### 10.1 `TouchColors` — 626 keys

<!-- BEGIN GENERATED: tools/namespace_index.py -->
<!-- Generated by `tools/namespace_index.py`. Do not edit by hand; run
     `python3 tools/namespace_index.py --write`, or `--check` to find out
     whether this is stale. -->
<!--
One row per namespace: the key count, and what that namespace is believed
to be. The markers are epistemic and are defined in §1.

The shipped value of every individual key is NOT in this index. It lives in
`baseline/TouchColors` and `baseline/TouchOptions` — one tab-separated line
per key, already in git, and the input every `tdtheme apply` merges from.
The counts here are checked against those files rather than trusted:
`--check` fails if a key was added, removed or renamed.
-->
| Namespace | Keys | What it is |
|---|---:|---|
| `parms.*` | 167 | [S] **Parameter dialog widgets** — buttons, fields, menus, toggles, expression-mode variants. Largest namespace and the highest-value target for a dark theme: it is what the user stares at while building. `.expr.*` sets run parallel to base `.*` sets. |
| `tile.*` | 62 | [V]/[O] **Network editor node tiles** — read by `libOPUI`. Covers backgrounds, borders, connectors, flags, icons, selection and error states. The best-documented namespace: see §6.1-6.4. |
| `georender.*` | 51 | [?] **3D viewport / geometry rendering** — axes, grid, handles, guides, clipping planes. No individual probing. Name-based reading only; `georender.handle.*` and `georender.geo.*` are the two visible families. |
| `dat.*` | 50 | [S] **Text editor.** Also the syntax-highlighting host: `dat.<lang>.<token>` — see §5.5. **Six** languages ship token sets, not the four §5.5 lists: `tscript` (5 keys) and `yaml` (6 keys) are missing from that table and use the same token vocabulary. Language-agnostic keys cover backgrounds, line numbers, comments, selection. |
| `default.*` | 46 | [?] **Fallback tier** — none of these 46 has a specific twin. Precedence untested; see §6.8. Safe to set only where no specific key exists, which is all of them. |
| `jive.*` | 39 | [V]/[O] **CHOP Channel Editor** (keyframes/curves) — read by `libJIVE`. `.bg`/`.fg` pairs on handles, slices, segments, slopes; `.plot.aux1-4` are curve families with `.mark` variants; `.timeline`/`.timemark`/`.currenttime` are the time ruler. |
| `chop.*` | 21 | [?] **CHOP node track graph** — the mini animation graph drawn on CHOP nodes (distinct from the CHOP Channel Editor). Unprobed. |
| `oplist.*` | 13 | [?] **Operator (OP) list** — the flat list of all operators in a network. |
| `preflist.*` | 13 | [?] **Parameter list / preferences list.** Unprobed. |
| `textport.*` | 11 | [?] **Textport** — the built-in Python console/log viewer. Tinting this changes console readability, a common dark-theme win. Unprobed. |
| `ramp.*` | 10 | Uncatalogued namespace — name only, no evidence of behaviour. |
| `xcfladder.*` | 10 | [?] **Expression/CF function ladder** — the value ladder beside fields. Colours plus the `xcfladder.*` options (box size, rechoose delay, steps per rotation). |
| `worksheet.*` | 9 | [V]/[O] **Network editor background** — read by `libOPUI`. Pairs with the `worksheet.*` options covering zoom, scroll, the wheel crossfade and autoscroll. Highest-impact key for overall feel: `worksheet.bg`. |
| `dialog.*` | 8 | [V] **Dialogs and modal windows.** Contains the two keys with the empty-second-field quirk (see reverse-engineering.md §1). |
| `textsheet.*` | 8 | [?] **Text sheet** — the DAT spreadsheet/text editor view. Unprobed. |
| no dot in the key | 7 | **[S]** The seven base hues node tile shades are derived from, via the `OP.*.sat`/`.val` and `NODE.*.sat`/`.val` multipliers in `TouchOptions`. See §5.4 — **retint all seven together.** |
| `top.*` | 6 | [?] **TOP node / image viewer** colour keys. Unprobed. |
| `graph.*` | 5 | [V] **Split ownership — read carefully.** The CHOP Channel Editor keyframe spline is `graph.line.*`, and it is in `TouchOptions`, not here. These five are `graph.grid.axes`, `graph.grid.axes.main`, `graph.grid.label`, `graph.grid.label.selected.bg` and `graph.separator`; `graph.separator` is one of the four keys `libCHILI` owns, and the `graph.grid.*` set is not attributed to any library. **Do not reason about `graph.*` from the name alone** — see §6.5-6.6 and §5.6. |
| `performance.*` | 5 | [?] **Performance monitor** display. Unprobed. |
| `geodetail.*` | 4 | [?] **Geometry detail / info panel.** Unprobed. |
| `icon.*` | 4 | [?] **Icon rendering** colour keys, plus an `icon.blendtype` option. |
| `knob.*` | 4 | [?] **Knob (rotary control) rendering.** Unprobed. |
| `mididevice.*` | 4 | [?] **MIDI device / control surface UI.** Unprobed. |
| `overlap.*` | 4 | [?] **Tile overlap indicator.** Unprobed. |
| `playbar.*` | 4 | [?] **Playback / timeline bar.** Unprobed. Pairs with `playback.*`. |
| `range.*` | 4 | [?] **Range / value slider component.** Unprobed. |
| `statusbar.*` | 4 | [?] **Status bar** at the bottom of the network editor. Unprobed. |
| `channelexport.*` | 3 | [?] **CHOP channel export** display. Unprobed. |
| `inputfield.*` | 3 | [?] **Input field** component colours. Unprobed. Pairs with the `field.*` options. |
| `panel.*` | 3 | [?] **Panel / palette component.** Unprobed. |
| `startup.*` | 3 | [?] **Startup / splash screen.** Unprobed. |
| `tooltip.*` | 3 | [?] **Tooltip** rendering. Unprobed. |
| `MAT.*` | 2 | Uncatalogued namespace — name only, no evidence of behaviour. |
| `SOP.*` | 2 | Uncatalogued namespace — name only, no evidence of behaviour. |
| `circle.*` | 2 | [?] **Circle / radial picker component.** Unprobed. |
| `colorbutton.*` | 2 | [?] **Colour swatch button** component. Unprobed. |
| `desktop.*` | 2 | [?] **Desktop / background root.** Unprobed. Candidate for the global base tone. |
| `extendedhelp.*` | 2 | [?] **Extended help / doc viewer.** Unprobed. |
| `frameindicator.*` | 2 | [?] **Frame / playback position indicator.** Unprobed. |
| `gadget.*` | 2 | [?] **Generic widget base** colours. Unprobed. Note the singular spelling. |
| `grouplist.*` | 2 | [?] **Group list / grouping UI.** Unprobed. |
| `lasso.*` | 2 | [?] **Lasso / freeform select tool.** Unprobed. |
| `netoverview.*` | 2 | [?] **Network overview (minimap).** Unprobed. |
| `opinfo.*` | 2 | [?] **Operator info / tooltip panel.** Unprobed. |
| `slider.*` | 2 | [?] **Slider component.** Unprobed. |
| `splitpane.*` | 2 | [?] **Split-pane divider.** Unprobed. |
| `swatch.*` | 2 | [?] **Colour swatch rendering.** Unprobed. |
| `COMP.*` | 1 | Uncatalogued namespace — name only, no evidence of behaviour. |
| `OP.*` | 1 | Uncatalogued namespace — name only, no evidence of behaviour. §5.4 says `OP` has no `TouchColors` entry; read that as the *bare* key — `OP.default` does ship here, at `0.67 0.67 0.67`. |
| `POP.*` | 1 | Uncatalogued namespace — name only, no evidence of behaviour. |
| `addoperator.*` | 1 | [?] **"Add Operator" dialog.** Unprobed. |
| `image.*` | 1 | [?] **Image / thumbnail rendering.** Unprobed. |
| `nodechooser.*` | 1 | [?] **Node chooser / type picker.** Unprobed. |
| `playback.*` | 1 | [?] **Playback controls.** Unprobed. Pairs with `playbar.*`. |
| `rubberbox.*` | 1 | [?] **Rubber-band selection box** in the network editor. Unprobed. |
<!-- END GENERATED: tools/namespace_index.py -->
### 10.2 `TouchOptions` — 183 keys

<!-- BEGIN GENERATED: tools/namespace_index.py -->
| Namespace | Keys | What it is |
|---|---:|---|
| `tile.*` | 55 | [V]/[O] **Network editor node tiles** — read by `libOPUI`. Sizes, alphas, blend types, timings, preview geometry. **Danger: `tile.*.size` and `tile.*.origsize` at 0 silently destroys layout (§3).** |
| `worksheet.*` | 29 | [V]/[O] **Network editor background** — read by `libOPUI`. The options half of the pairing named above: zoom, scroll, the wheel crossfade, and autoscroll. |
| `parms.*` | 10 | [S] **Parameter dialog widgets** — sizes, margins and a label width, pairing with the 167 `parms.*` colour keys; see §5.1 for why so many of these need changing together. |
| `font.*` | 9 | [V] **Fonts.** Base sizes, xkerning, the operator-switch bitmap and delay, and two **legitimately empty** face names (see reverse-engineering.md §1). `font.relative.size 0` is a legal delta, not a bug — §3 rule 2. This is the namespace to change for overall text sizing. |
| `OP.*` | 8 | [S] **`OP.*.sat`/`.val`** — the saturation and value multipliers applied to the `OP` base hue: `hi`, `lo`, `lo2`, `editbg`. The sat/val pairing is unambiguous, but the exact compositing formula is **[?]** — it is not confirmed whether the base colour is converted to HSV, multiplied, and converted back, or whether these are lerps toward white/black. See §5.4. |
| `graph.*` | 8 | [V] **Split ownership — read carefully.** `graph.line.*` here is the CHOP Channel Editor keyframe spline: `alpha`, `select.alpha`, and the `select.multsat`/`select.multval` pair that modulate the curve's colour on selection. `graph.linewidth`, `graph.linewidth.dots`, `graph.linewidth.select` and `graph.pixelsperdot` belong to the CHOP track graph instead, and `graph.linewidth` is read by two libraries at once. See §6.5-6.6. |
| `jive.*` | 8 | [V]/[O] **CHOP Channel Editor** (keyframes/curves) — read by `libJIVE`. Handle widths, acceleration limits and a zoom divisor; the options half of the 39-key colour namespace above. |
| `default.*` | 7 | [?] **Fallback tier** — none of these has a specific twin. Precedence untested; see §6.8. |
| `NODE.*` | 6 | [S] **`NODE.*.sat`/`.val`** — the same multipliers for node icon and border shades: `icon`, `innerborder`, `outerborder`. See §5.4. |
| `dat.*` | 6 | [?] **Text editor behaviour** — comment highlight, line-number size, scroll width, table retention, word wrap. Not colour, and none of these is a `dat.<lang>.<token>` key; that set is in `TouchColors`. Unprobed. |
| `viewer.*` | 4 | [?] **Viewer panel** default size. Layout, not colour. |
| `help.*` | 3 | [?] **Help tooltip text and delay.** Options control the initial/recent delay and text length — behaviour, not just colour. Unprobed. |
| `xcfladder.*` | 3 | [?] **Expression/CF function ladder** — box size, rechoose delay, steps per rotation. |
| `CHOP.*` | 2 | [S] **Default node size per operator type** — `CHOP.height` 90, `CHOP.width` 130. Unprobed. |
| `COMP.*` | 2 | [S] **Default node size per operator type** — `COMP.height` 130, `COMP.width` 160. Unprobed. |
| `DAT.*` | 2 | [S] **Default node size per operator type** — `DAT.height` 90, `DAT.width` 130. Unprobed. |
| `MAT.*` | 2 | [S] **Default node size per operator type** — `MAT.height` 90, `MAT.width` 130. Unprobed. |
| `POP.*` | 2 | [S] **Default node size per operator type** — `POP.height` 90, `POP.width` 130. Unprobed. |
| `SOP.*` | 2 | [S] **Default node size per operator type** — `SOP.height` 90, `SOP.width` 130. Unprobed. |
| `TOP.*` | 2 | [S] **Default node size per operator type** — `TOP.height` 90, `TOP.width` 130. Unprobed. |
| `field.*` | 2 | [?] **Input field** behaviour — cursor blink period and value-ladder delay. Not colour. |
| `mouse.*` | 2 | [?] **Mouse wheel** behaviour — boost and use-msec timing. Not colour. |
| `chop.*` | 1 | [?] **CHOP node track graph** — the mini animation graph drawn on CHOP nodes. Unprobed. |
| `dragdrop.*` | 1 | [V] **Drag-and-drop** hover delay in ms — a *timing* value despite the name. See §6.7. |
| `file.*` | 1 | [?] **File browser** — one key, branched-file timing. Not colour. |
| `geo.*` | 1 | [?] **Geometry viewer** — one key, orthographic floor grid size. Layout. |
| `icon.*` | 1 | [?] **Icon rendering** — the `blendtype` companion to the `icon.*` colour keys. |
| `list.*` | 1 | [?] **Generic list row** — one key, row height. Layout, not colour. |
| `oplist.*` | 1 | [?] **Operator (OP) list** — the flat list of all operators in a network. |
| `osx.*` | 1 | [?] **macOS platform integration** — one key (`osx.trackpad.zoom`). Behaviour, unprobed. |
| `touch.*` | 1 | [?] **Touch/multi-touch** — one key, click radius. Not colour. |
<!-- END GENERATED: tools/namespace_index.py -->
### 10.3 Quick index — highest-leverage keys for a dark theme

Ranked by how much of the UI they affect. **[S]** unless marked.

| Priority | Key(s) | File | Why |
|---|---|---|---|
| 1 | `worksheet.bg` | Colors | The single largest area on screen. Sets the tone everything else is judged against. |
| 2 | `tile.bg` | Colors | Every node tile. Currently pure black `0 0 0`. |
| 3 | `dat.python.*`, `dat.glsl.*`, `dat.json.*`, `dat.xml.*` | Colors | Whole syntax-highlighting sets — 26 keys, high visibility, zero coupling to the rest. |
| 4 | `parms.*` | Colors | 167 keys covering all parameter-dialog controls. |
| 5 | `CHOP` `COMP` `DAT` `MAT` `POP` `SOP` `TOP` | Colors | Node hues. **Change all seven together** (§5.4) or derived shades desync. |
| 6 | `worksheet.grid.alpha` | Options | Set `0` to hide the network grid outright — cheapest legibility win. |
| 7 | `font.default.size`, `font.mono.size` | Options | Global text scale. Ships `9.0` / `9.5`. |
| 8 | `tile.connection.hilite1` / `.hilite2` | Colors | **The only verified wire control** (§6.1) — hover only. |
| 9 | `jive.channel.bg` + `jive.graph.bg` | Colors | CHOP Channel Editor, if keyframes are used. |
| 10 | `*.blendtype` | Options | **[?] Undecoded enum (§5.2)** — change one at a time, restart, record. |


