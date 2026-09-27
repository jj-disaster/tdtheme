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

Both files are CRLF-terminated, tab-separated, ASCII, with no comments and no
duplicate keys. Both are read by TouchDesigner **at startup only**.

| File | Shape | Keys | Owns |
|---|---|---|---|
| `TouchColors` | `key <TAB> r <TAB> g <TAB> b` | 626 | every UI colour |
| `TouchOptions` | `key <TAB> value` | 183 | numeric options, alpha, sizes |

The two files share **zero keys** — disjoint namespaces. Which file owns a key
is decided by which store reads it: `getOption*(name)` reads `TouchOptions`,
`getColor<UT_Color>(name)` reads `TouchColors`. **[O]**

### Format quirks that will corrupt a file if ignored

- **Colour is the LAST THREE fields, not fields 2-4.** Two shipped keys carry a
  stray empty second field **[V]**:
  ```
  dialog.commenthint        <TAB> <TAB> 0.2 <TAB> 0.2 <TAB> 0.2
  dialog.commenthint.comp   <TAB> <TAB> 0.5 <TAB> 0.5 <TAB> 0.5
  ```
  A writer that normalises these to four fields silently changes the file.
- **Some option values are legitimately empty [V]**: `font.default.face` and
  `font.mono.face` both parse to `[""]`. An empty value is not a missing key.
- **Colour channels are NOT clamped to 1.0 [V]**. `POP.hilite` ships as
  `0.56 0.6 1.2`. Values above 1.0 are intentional and render as brighter.
  Do not "fix" them.
- **A blank line would be silently dropped** by a parser that skips empties.
  Neither shipped file has one, but a dropped line is an unrequested change.
  **[S]**

`tdtheme` round-trips both files byte-for-byte; that invariant is enforced by
`tests/test_roundtrip.py` and is the acceptance gate for any change to the
parser.

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

Measured, not assumed. Checksums taken before launching TouchDesigner and
again after quitting were identical, and mtimes never moved from the last
`tdtheme apply` across a full session.

- TouchDesigner **reads** both stores at startup.
- TouchDesigner **never writes** them.
- Therefore editing while it runs is **safe**. A restart is needed only for a
  change to become **visible** — a display refresh, not a durability concern.

Re-verify after any TouchDesigner update with `./check-td-writes`.

A TouchDesigner update also **wipes both files**. `tdtheme status` compares the
live build against the one recorded in the baseline and warns on a mismatch;
`tdtheme capture --force` refreshes it.

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
`TouchOptions` under a similar name. Examples: `tile.clone.bg` (colour) +
`tile.clone.alpha` (opacity); `worksheet.grid` (colour) +
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
OP  CHOP  COMP  DAT  MAT  POP  SOP  TOP
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

From the shipped dylibs (`TouchDesigner.app/Contents/Frameworks/*.dylib`):

| Library | Owns |
|---|---|
| `libOPUI.dylib` | network editor — all `tile.*`, `worksheet.*`. Has **zero** `graph.*` |
| `libJIVE.dylib` | CHOP Channel Editor — all `jive.*`, and `graph.line.*` |
| `libCHILI.dylib` | CHOP track graph (mini anim graph on nodes) — `graph.linewidth`, `graph.linewidth.dots`, `graph.pixelsperdot`, `graph.separator` |

This is why `graph.*` is dangerous to reason about from the name alone: see
§7.2.

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

Things confirmed *not* to work, recorded so they are not re-litigated.

- **No built-in theme switcher [V].** No theme/colourscheme UI strings in
  `libOPUI` or the main binary. No third-party tool found.
- **No live-reload or scripting API for these files [V].** Port 8888 is
  Jupyter, not TouchDesigner. Hence apply-then-restart.
- **Legacy `resources*` files are dead [O].** `resources`, `resources.grey`,
  `resources.tan`, `resources.lava`, `resources.new`, `resources.oldtan`,
  `resources.small` are a legacy parallel system using `Key:<TAB>value` with
  X11 colour names. The filenames and several of their keys
  (`SelectOnColor`, `OpTileSmallFontSize`) appear nowhere in the binaries.
- **TouchDesigner does not write these files [V]** — see §4.

---

## 8. Namespaces NOT covered by tdtheme

Three further colour systems live in the same config directory in different
formats. `tdtheme` does not read or write them, and a theme will not affect
them. Each would be one more parser behind the same interface.

| File(s) | Format | Controls |
|---|---|---|
| `colorPalette.def`, `opColorPalette.def` | count header + rows | Palette browser colour wheels |
| `3DSceneColors` (`.grey`/`.bw`/`.wb` variants) | `Key:<TAB>r g b<SPACE># comment` | 3D viewport |
| `MiscColors` | same | locks, pending changes, keyframes |

Note these use **space-separated** RGB in a single field plus an optional
`#` comment — a different format from `TouchColors`. Do not copy parsing
logic between them.

---

## 9. Known gaps in this reference and in the tooling

Stated plainly so a future author does not mistake absence for safety.

### 9.1 Most individual keys are unverified

Of 626 `TouchColors` keys and 183 `TouchOptions` keys, only the small set in
§6 has confirmed behaviour. The inventories in §10 give the shipped value for
every key — that is **factual** — but the per-key role annotations are
**[S]** convention inference or **[?]** unknown. `georender.*` (51 keys),
`parms.*` (167 keys), and the `default.*` tiers in particular have had no
individual probing at all.

### 9.2 The `blendtype` enum is undecoded — see §5.2

### 9.3 Colour field arity — was a real gap, now closed

Verified while writing this document. `tdtheme status` reported a
`TouchColors` value of `worksheet.grid` as **two** fields where the shipped
file has three:

```
shipped:  worksheet.grid <TAB> 0.1 <TAB> 0.1 <TAB> 0.15
live:     worksheet.grid <TAB> "0.317 0.189" <TAB> 0.15
```

The first colour channel had been replaced by two numbers separated by a
**space** instead of a tab — a hand-editing slip, not a valid encoding.
`tdtheme export` faithfully captured the malformed value, and at the time
`tdtheme validate` returned **zero findings** for it, because validation
skipped `TouchColors` entirely and no arity rule existed. `apply` would have
written the corruption straight back into the install.

**Now fixed.** `_check_color_fields` (`tdtheme.py`) rejects, as a hard **error**
that blocks `apply`:

- a field count that differs from the baseline's count for that key
- any field containing internal whitespace
- a non-numeric field, where the baseline's corresponding field was numeric

Every rule is stated *relative to the baseline* rather than absolutely, so the
two `dialog.commenthint*` keys keep their 4-field shape without tripping it,
and a future build that changes a key's shape will not cause mass false
positives. All 626 pristine baseline keys validate clean.

**Author guidance: every `TouchColors` value must have exactly three
space-free numeric fields**, except the two documented `dialog.commenthint*`
keys which have four with an empty second. A space inside a field is always an
error. The tool now enforces this, but the error blocks the write — fix the
YAML rather than reaching for `--force`.

### 9.4 `validate` cannot see semantic mistakes

It checks three things only: tile geometry at zero, size keys against the
baseline, and unknown keys. It cannot tell that a colour is illegible against
its background, that two keys should have been changed together (§5.4), or
that a value is a typo for another key.

---

## 10. Complete key inventory

Generated from the captured baseline — every key with its shipped value. The
**value** column is factual. It is not a statement about what the key does;
for that, see the grammar (§5) and the verified findings (§6).

Format: `TouchColors` = three fields, except `dialog.commenthint*` = four.
`TouchOptions` = one field, except two empty-valued font faces.

### 10.1 `TouchColors` — 626 keys

Three fields, space-separated, in the table below. The two `dialog.commenthint*`
keys have a **fourth, empty** leading field, shown as `(empty)`.

Shipped values are the **pristine** captured baseline, not the current install.

#### `parms.*` — 167 key(s)

[S] **Parameter dialog widgets** — buttons, fields, menus, toggles, expression-mode variants. Largest namespace (167 keys) and the highest-value target for a dark theme: it is what the user stares at while building. `.expr.*` sets run parallel to base `.*` sets.

| Key | Shipped (r g b) |
|---|---|
| `parms.bind.bg` | `0.192 0.19 0.2` |
| `parms.bind.bg.disabled` | `0.416 0.36 0.6` |
| `parms.bind.bg.enabled` | `0.77 0.7 1` |
| `parms.bind.fg` | `0.77 0.7 1` |
| `parms.bind.hilite` | `0.308 0.28 0.4` |
| `parms.bind.indicator` | `0.77 0.7 1` |
| `parms.button.bg` | `0.36 0.36 0.36` |
| `parms.button.bg.disabled` | `0.3 0.3 0.3` |
| `parms.button.bg.loc` | `0.42 0.42 0.42` |
| `parms.button.bg.sel` | `0.36 0.36 0.36` |
| `parms.button.border` | `0.2 0.2 0.2` |
| `parms.button.border.disabled` | `0.2 0.2 0.2` |
| `parms.button.border.inner` | `0.25 0.25 0.25` |
| `parms.button.border.top` | `0.45 0.45 0.45` |
| `parms.button.expr.bg` | `0.2 0.2 0.2` |
| `parms.button.expr.bg.loc` | `0.22 0.22 0.22` |
| `parms.button.expr.bg.sel` | `0.18 0.18 0.18` |
| `parms.button.expr.border` | `0.15 0.15 0.15` |
| `parms.button.expr.border.inner` | `0.15 0.15 0.15` |
| `parms.button.expr.border.top` | `0.22 0.22 0.22` |
| `parms.button.expr.fg` | `0.645 0.9 0.9` |
| `parms.button.fg` | `0.72 0.72 0.72` |
| `parms.button.fg.disabled` | `0.4 0.4 0.4` |
| `parms.button.override.bg` | `0.35 0.5 0.25` |
| `parms.button.override.bg.loc` | `0.4 0.55 0.3` |
| `parms.button.override.bg.sel` | `0.35 0.5 0.25` |
| `parms.button.override.border` | `0.2 0.2 0.2` |
| `parms.button.override.border.inner` | `0.21 0.3 0.15` |
| `parms.button.override.border.top` | `0.385 0.55 0.275` |
| `parms.button.override.fg` | `0.645 0.9 0.6` |
| `parms.comment.disabled.fg` | `0.6 0.6 0.6` |
| `parms.const.bg` | `0.425 0.425 0.425` |
| `parms.dialog.bg` | `0.25 0.25 0.25` |
| `parms.dialog.fg` | `0.9 0.9 0.9` |
| `parms.disabled.bg` | `0.25 0.25 0.25` |
| `parms.disabled.err.bg` | `0.5 0 0` |
| `parms.disabled.err.fg` | `0.4 0.4 0.4` |
| `parms.disabled.fg` | `0.4 0.4 0.4` |
| `parms.err.bg` | `1 0 0` |
| `parms.err.fg` | `0.8 0.8 0.8` |
| `parms.expand.bg` | `0.25 0.25 0.25` |
| `parms.expr.bg` | `0.5 0.7 0.7` |
| `parms.expr.bg.disabled` | `0.25 0.35 0.35` |
| `parms.expr.bg.off` | `0.55 0.65 0.65` |
| `parms.expr.bg.off.selected` | `0.645 0.9 0.9` |
| `parms.expr.bg.on` | `0.498 0.686 0.686` |
| `parms.expr.bg.on.selected` | `0.71 0.99 0.99` |
| `parms.expr.bg.selected` | `0.656 0.9 0.9` |
| `parms.expr.fg` | `0.645 0.9 0.9` |
| `parms.expr.fg.off` | `0.1 0.1 0.1` |
| `parms.expr.fg.on` | `0.1 0.1 0.1` |
| `parms.expr.field.bg` | `0.2 0.2 0.2` |
| `parms.expr.field.bg.loc` | `0.17 0.17 0.17` |
| `parms.expr.field.bg.selected` | `0.15 0.15 0.15` |
| `parms.expr.field.bg.selected.loc` | `0.12 0.12 0.12` |
| `parms.expr.field.fg` | `0.645 0.9 0.9` |
| `parms.expr.field.hilite` | `0.32 0.45 0.45` |
| `parms.expr.hilite` | `0.1 0.3 0.55` |
| `parms.expr.menubar.bg` | `0.5 0.7 0.7` |
| `parms.expr.menubar.bg.loc` | `0.5 0.7 0.7` |
| `parms.expr.menubar.bg.sel` | `0.5 0.7 0.7` |
| `parms.expr.menubar.fg` | `0.1 0.1 0.1` |
| `parms.expr.menubar.fg.loc` | `0.1 0.1 0.1` |
| `parms.expr.menubar.fg.sel` | `0.1 0.1 0.1` |
| `parms.field.numeric.bg` | `0.325 0.325 0.325` |
| `parms.field.numeric.bg.loc` | `0.4 0.4 0.4` |
| `parms.field.numeric.bg.selected` | `0.45 0.45 0.45` |
| `parms.field.numeric.bg.selected.loc` | `0.375 0.375 0.375` |
| `parms.field.numeric.fg` | `0.75 0.75 0.75` |
| `parms.field.numeric.hilite` | `0.3 0.3 0.3` |
| `parms.field.string.bg` | `0.7 0.7 0.7` |
| `parms.field.string.bg.loc` | `0.75 0.75 0.75` |
| `parms.field.string.bg.selected` | `0.9 0.9 0.9` |
| `parms.field.string.bg.selected.loc` | `0.95 0.95 0.95` |
| `parms.field.string.fg` | `0.1 0.1 0.1` |
| `parms.field.string.hilite` | `0.4 0.4 0.4` |
| `parms.foldertab.fg` | `0.75 0.75 0.75` |
| `parms.foldertab.loc` | `0.85 0.85 0.85` |
| `parms.foldertab.underline` | `0.85 0.85 0.85` |
| `parms.label.fg` | `0.72 0.72 0.72` |
| `parms.label.fg.disabled` | `0.4 0.4 0.4` |
| `parms.label.fg.loc` | `0.85 0.85 0.85` |
| `parms.menubar.bg` | `0.36 0.36 0.36` |
| `parms.menubar.bg.loc` | `0.42 0.42 0.42` |
| `parms.menubar.bg.sel` | `0.36 0.36 0.36` |
| `parms.menubar.disabled.bg` | `0.35 0.35 0.35` |
| `parms.menubar.disabled.fg` | `0.5 0.5 0.5` |
| `parms.menubar.fg` | `0.72 0.72 0.72` |
| `parms.menubar.fg.loc` | `0.7 0.7 0.7` |
| `parms.menubar.fg.sel` | `0.7 0.7 0.7` |
| `parms.menubar.outline` | `0.2 0.2 0.2` |
| `parms.menubar.outline.loc` | `0.2 0.2 0.2` |
| `parms.menuentry.bg` | `0.36 0.36 0.36` |
| `parms.menuentry.bg.loc` | `0.42 0.42 0.42` |
| `parms.menuentry.bg.sel` | `0.5 0.5 0.5` |
| `parms.menuentry.fg` | `0.72 0.72 0.72` |
| `parms.menuentry.fg.loc` | `0.7 0.7 0.7` |
| `parms.menuentry.fg.sel` | `0.1 0.1 0.1` |
| `parms.menuentry.outline` | `0.2 0.2 0.2` |
| `parms.multi.input.bg` | `0.3 0.3 0.3` |
| `parms.multi.input.bg.loc` | `0.34 0.34 0.34` |
| `parms.multi.input.border` | `0.42 0.42 0.42` |
| `parms.multi.input.delete` | `0.8 0.1 0.25` |
| `parms.multi.input.entry.bg` | `0.3 0.3 0.3` |
| `parms.multi.input.entry.fg` | `0.72 0.72 0.72` |
| `parms.multi.input.title.bg` | `0.35 0.35 0.35` |
| `parms.multi.input.title.fg` | `0.7 0.7 0.7` |
| `parms.multi.input.up` | `0.25 0.55 0.55` |
| `parms.override.bg` | `0.35 0.5 0.25` |
| `parms.override.bg.disabled` | `0.14 0.2 0.1` |
| `parms.override.bg.off` | `0.517 0.603 0.498` |
| `parms.override.bg.off.selected` | `0.62 0.724 0.598` |
| `parms.override.bg.on` | `0.35 0.5 0.25` |
| `parms.override.bg.on.selected` | `0.4 0.6 0.35` |
| `parms.override.bg.selected` | `0.32 0.5 0.2` |
| `parms.override.fg` | `0.645 0.9 0.6` |
| `parms.override.fg.off` | `0.1 0.1 0.1` |
| `parms.override.fg.on` | `0.1 0.1 0.1` |
| `parms.override.field.bg` | `0.35 0.5 0.25` |
| `parms.override.field.bg.loc` | `0.4 0.55 0.3` |
| `parms.override.field.bg.selected` | `0.32 0.5 0.2` |
| `parms.override.field.bg.selected.loc` | `0.37 0.55 0.25` |
| `parms.override.field.fg` | `0.645 0.9 0.6` |
| `parms.override.field.hilite` | `0.17 0.25 0.16` |
| `parms.override.hilite` | `0.1 0.35 0.1` |
| `parms.override.menubar.bg` | `0.35 0.5 0.25` |
| `parms.override.menubar.bg.loc` | `0.35 0.5 0.25` |
| `parms.override.menubar.bg.sel` | `0.35 0.5 0.25` |
| `parms.override.menubar.fg` | `0.645 0.9 0.6` |
| `parms.override.menubar.fg.loc` | `0.645 0.9 0.6` |
| `parms.override.menubar.fg.sel` | `0.645 0.9 0.6` |
| `parms.readonly.bg` | `0.25 0.25 0.25` |
| `parms.readonly.fg` | `0.75 0.75 0.3` |
| `parms.readonly.hilite` | `0.35 0.35 0.2` |
| `parms.slider.bg` | `0.175 0.175 0.175` |
| `parms.slider.disabled.bg` | `0.3 0.3 0.3` |
| `parms.slider.disabled.fg` | `0.35 0.35 0.35` |
| `parms.slider.fg` | `0.05 0.05 0.05` |
| `parms.slider.thumb` | `0.4 0.4 0.4` |
| `parms.slider.thumb.disabled` | `0.3 0.3 0.3` |
| `parms.slider.thumb.disabled.outline` | `0.3 0.3 0.3` |
| `parms.slider.thumb.loc` | `0.5 0.5 0.5` |
| `parms.slider.thumb.off` | `0.5 0.5 0.5` |
| `parms.slider.thumb.on` | `0.5 0.5 0.5` |
| `parms.slider.thumb.outline` | `0.225 0.225 0.225` |
| `parms.slider.thumb.outline.loc` | `0.225 0.225 0.225` |
| `parms.text.hint` | `0.3 0.3 0.3` |
| `parms.text.hint.dark` | `0.4 0.4 0.4` |
| `parms.text.hint.light` | `0.6 0.6 0.6` |
| `parms.textbox.expr.bg` | `0.56 0.64 0.69` |
| `parms.textbox.override.bg` | `0.493 0.701 0.797` |
| `parms.toggle.bg` | `0.15 0.15 0.15` |
| `parms.toggle.bg.off` | `0.325 0.325 0.35` |
| `parms.toggle.bg.off.loc` | `0.275 0.275 0.275` |
| `parms.toggle.bg.on` | `0.55 0.55 0.55` |
| `parms.toggle.bg.on.loc` | `0.65 0.65 0.65` |
| `parms.toggle.disabled.bg` | `0.2 0.2 0.2` |
| `parms.toggle.disabled.fg` | `0.25 0.25 0.25` |
| `parms.toggle.fg` | `0.5 0.5 0.5` |
| `parms.toggle.fg.off` | `0.7 0.7 0.7` |
| `parms.toggle.fg.on` | `0.1 0.1 0.1` |
| `parms.toggle.outline` | `0.2 0.2 0.2` |
| `parms.toggle.thumb.disabled` | `0.4 0.4 0.4` |
| `parms.toggle.thumb.off` | `0.2 0.2 0.2` |
| `parms.toggle.thumb.off.loc` | `0.17 0.17 0.17` |
| `parms.toggle.thumb.on` | `0.7 0.7 0.7` |
| `parms.toggle.thumb.on.loc` | `0.85 0.85 0.85` |

#### `tile.*` — 62 key(s)

[V]/[O] **Network editor node tiles** — read by `libOPUI`. Covers backgrounds, borders, connectors, flags, icons, selection and error states. The best-documented namespace: see §6.1-6.4.

| Key | Shipped (r g b) |
|---|---|
| `tile.active.bg` | `0.09 0.1 0.111` |
| `tile.bg` | `0 0 0` |
| `tile.block` | `0.1 0.1 0.1` |
| `tile.commented` | `0.3 0.36 0.6` |
| `tile.connection.hilite1` | `1 1 0` |
| `tile.connection.hilite2` | `0.5 0.5 0` |
| `tile.connector.bg` | `0.1 0.1 0.1` |
| `tile.connector.fg` | `0.75 0.75 0.75` |
| `tile.current` | `0 1 0` |
| `tile.default.bg` | `0.0 0.0 0.0` |
| `tile.dockedconnection.line` | `0.2 0.2 0.2` |
| `tile.drag.crosshair` | `0.4 0.4 0.4` |
| `tile.droppable` | `0 0 0` |
| `tile.error` | `1 0 0` |
| `tile.extraconnection.line` | `0.3 0.35 0.4` |
| `tile.extraconnection.line.hilite` | `0.4 0.467 0.5` |
| `tile.flag.activate` | `1 1 1` |
| `tile.flag.bg` | `0.25 0.25 0.25` |
| `tile.flag.bypass` | `0.3 0.3 0.3` |
| `tile.flag.bypass.bright` | `0.8 0.8 0.8` |
| `tile.flag.bypass.cross` | `0.8 0 0` |
| `tile.flag.bypass.nocookcross` | `0 0 0` |
| `tile.flag.bypass.noparentcookcross` | `0.32 0.32 0.32` |
| `tile.flag.bypass.noparentcookcross.bright` | `0.8 0.8 0.8` |
| `tile.flag.capture` | `0.461465 0.732 0.082716` |
| `tile.flag.clone` | `1 0.3 0.3` |
| `tile.flag.clonechild` | `0.5 0.15 0.15` |
| `tile.flag.compare` | `0.11016 0.765 0.743172` |
| `tile.flag.display` | `0.195 0.490167 1` |
| `tile.flag.export` | `0.525 0.75 0.375` |
| `tile.flag.expose` | `1 0 0` |
| `tile.flag.hardlock` | `1 1 0` |
| `tile.flag.link` | `0.78 0.729932 0.06474` |
| `tile.flag.origin` | `0.84 0.310926 0.21756` |
| `tile.flag.pickable` | `0.885 0.556812 0.09735` |
| `tile.flag.render` | `0.53057 0.402 1` |
| `tile.flag.saveviewer` | `0.7 0.7 0.7` |
| `tile.flag.template` | `0.871413 0.377316 0.892` |
| `tile.flag.viewer` | `0.8 0.8 0.8` |
| `tile.flagh.bg` | `0.17 0.17 0.17` |
| `tile.flagh.border` | `0.52 0.52 0.52` |
| `tile.flagh.icon.locate` | `1 1 1` |
| `tile.flagv.bg.hilite` | `1 1 1` |
| `tile.flagv.bypass` | `0.3 0.3 0.3` |
| `tile.flagv.cloneimmune` | `0.9 0.45 0` |
| `tile.flagv.hardlock` | `0.823529 0.705882 0.133333` |
| `tile.flagv.icon.locate` | `1 1 1` |
| `tile.flagv.mastercloneimmune` | `0.45 0.3 0` |
| `tile.ghost` | `0.3 0.3 0.3` |
| `tile.hiddenconnection.line` | `0.3 0.4 0.35` |
| `tile.icon.bg` | `0.2 0.2 0.2` |
| `tile.locate` | `0.2 0.2 0.2` |
| `tile.name.bg` | `0.2 0.2 0.2` |
| `tile.name.border` | `0.52 0.52 0.52` |
| `tile.name.fg` | `0.75 0.75 0.75` |
| `tile.name.hilite` | `0.7 0.7 0.7` |
| `tile.picked` | `0.85 0.85 0` |
| `tile.replicatorconnection.line` | `0.2 0.2 0.2` |
| `tile.resizeborder` | `0.2 0.2 0.2` |
| `tile.utility.current` | `0.7 0.7 0.7` |
| `tile.utility.picked` | `0.7 0.7 0.7` |
| `tile.warning` | `1 1 0` |

#### `georender.*` — 51 key(s)

[?] **3D viewport / geometry rendering** — axes, grid, handles, guides, clipping planes. 51 keys with **no individual probing**. Name-based reading only; `georender.handle.*` and `georender.geo.*` are the two visible families.

| Key | Shipped (r g b) |
|---|---|
| `georender.aux` | `1 0.45 0.07` |
| `georender.axes` | `1 0 0` |
| `georender.cplane` | `0 0.5 0.5` |
| `georender.cplane.selected` | `1 0.51 0.8` |
| `georender.current` | `0 1 0` |
| `georender.geo.axes` | `0 0 0` |
| `georender.geo.axes.selected` | `1 0 0` |
| `georender.ghost` | `0.3 0.4 0.6` |
| `georender.grid` | `0.22 0.22 0.22` |
| `georender.grid.snap` | `0.301961 0.301961 0.301961` |
| `georender.guide1` | `1 0.3 0.3` |
| `georender.guide2` | `1 0.7 0.3` |
| `georender.handle` | `1 0.17 0` |
| `georender.handle.axis` | `0 1 1` |
| `georender.handle.col1` | `0.8 0.8 0` |
| `georender.handle.col2` | `0.6 0.6 0` |
| `georender.handle.col3` | `0.4 0.4 0` |
| `georender.handle.col4` | `0.2 0.2 0` |
| `georender.handle.pivot` | `0 1 0` |
| `georender.handle.rotate` | `1 1 0` |
| `georender.handle.selected` | `1 0.51 0.8` |
| `georender.handle.xaxis` | `1 0 0` |
| `georender.handle.yaxis` | `0 1 0` |
| `georender.handle.zaxis` | `0 0 1` |
| `georender.heightvector` | `1 0.5 0.5` |
| `georender.label` | `0.8 0.8 0.8` |
| `georender.label.aux` | `0.8 0.8 0` |
| `georender.label.bg` | `0.2 0.2 0.2` |
| `georender.ortho.origin` | `0.15 0.15 0.15` |
| `georender.point` | `0.09 0.59 1` |
| `georender.point.closure` | `0 1 0.97` |
| `georender.point.selected` | `1 1 0` |
| `georender.point.unused` | `0.92 0.89 0.62` |
| `georender.prim.aux` | `0.7 0.4 0.4` |
| `georender.prim.aux.selected` | `0.9 0.6 0.7` |
| `georender.prim.closure` | `0.31 0.9 0.31` |
| `georender.prim.edits` | `1 1 1` |
| `georender.prim.info` | `1 0.15 0.78` |
| `georender.prim.info.selected` | `1 0.75 1` |
| `georender.prim.selected` | `1 1 0` |
| `georender.selected` | `1 1 0` |
| `georender.selected.child` | `0.5 0.5 0` |
| `georender.selected.spot` | `1 1 1` |
| `georender.simpledraw` | `1 1 1` |
| `georender.subdivide.edge.selected` | `1 0 0` |
| `georender.subdivide.edge.weighted` | `0 1 1` |
| `georender.template` | `0.4 0.4 0.4` |
| `georender.wire` | `0.8 0.8 0.73` |
| `georender.xaxis` | `0.42 0.25 0.25` |
| `georender.yaxis` | `0.25 0.42 0.25` |
| `georender.zaxis` | `0.25 0.25 0.42` |

#### `dat.*` — 50 key(s)

[S] **Text editor.** Also the syntax-highlighting host: `dat.<lang>.<token>` — see §5.5. Language-agnostic keys cover backgrounds, line numbers, comments, selection.

| Key | Shipped (r g b) |
|---|---|
| `dat.bg.editable` | `0.1 0.1 0.1` |
| `dat.bg.hilite` | `0.22 0.22 0.22` |
| `dat.bg.noteditable` | `0.165 0.165 0.165` |
| `dat.comment.fg` | `0.65 0.65 0.65` |
| `dat.glsl.comments` | `0.6 1 0.6` |
| `dat.glsl.functions` | `1 1 0.66` |
| `dat.glsl.keywords` | `0.66 0.66 1` |
| `dat.glsl.numbers` | `1 1 0.66` |
| `dat.glsl.preprocessors` | `0.66 0.66 0.66` |
| `dat.glsl.strings` | `1 0.66 0.66` |
| `dat.indicators` | `0.3 0.3 0.3` |
| `dat.json.booleans` | `0.66 0.66 1` |
| `dat.json.comments` | `0.6 1 0.6` |
| `dat.json.numbers` | `1 1 0.66` |
| `dat.json.strings` | `1 0.66 0.66` |
| `dat.json.symbols` | `0.45 0.45 0.45` |
| `dat.lineno.bg` | `0.145 0.145 0.145` |
| `dat.lineno.bg.hilite` | `0.25 0.25 0.25` |
| `dat.lineno.fg` | `0.7 0.7 0.7` |
| `dat.lineno.fg.nodata` | `0.3 0.3 0.3` |
| `dat.python.comments` | `0.6 1 0.6` |
| `dat.python.editable` | `0.65 0.95 1` |
| `dat.python.editing` | `0.5 0.7 1` |
| `dat.python.functions` | `1 1 0.66` |
| `dat.python.hilite` | `0.325 0.475 0.5` |
| `dat.python.keywords` | `0.66 0.66 1` |
| `dat.python.noteditable` | `0.4 0.7 0.8` |
| `dat.python.numbers` | `1 1 0.66` |
| `dat.python.strings` | `1 0.66 0.66` |
| `dat.scroll` | `1 1 1` |
| `dat.table.outline` | `0.25 0.25 0.25` |
| `dat.table.select.background` | `0.25 0.2625 0.3` |
| `dat.table.select.outline` | `0.45 0.45 0.45` |
| `dat.tscript.comments` | `0.38 0.38 0.38` |
| `dat.tscript.editable` | `0.85 0.74 0.88` |
| `dat.tscript.editing` | `0.8 0.49 0.73` |
| `dat.tscript.hilite` | `0.425 0.37 0.44` |
| `dat.tscript.noteditable` | `0.6 0.54 0.63` |
| `dat.xml.attributes` | `0.69 0.89 1` |
| `dat.xml.attributevalues` | `0.89 0.89 0.6` |
| `dat.xml.comments` | `0.6 1 0.6` |
| `dat.xml.entityrefs` | `1 0.66 1` |
| `dat.xml.symbols` | `0.45 0.45 0.45` |
| `dat.xml.tags` | `0.52 0.69 1` |
| `dat.yaml.booleans` | `0.86 0.86 1` |
| `dat.yaml.comments` | `0.6 1 0.6` |
| `dat.yaml.keys` | `0.56 0.56 1` |
| `dat.yaml.numbers` | `1 1 0.66` |
| `dat.yaml.strings` | `1 0.66 0.66` |
| `dat.yaml.symbols` | `0.85 0.85 0.85` |

#### `default.*` — 46 key(s)

[?] **Fallback tier** — 46 keys, none with a specific twin. Precedence untested; see §6.8. Safe to set only where no specific key exists, which is all 46.

| Key | Shipped (r g b) |
|---|---|
| `default.arrow.locate` | `1 1 1` |
| `default.arrow.off` | `0 0 0` |
| `default.arrow.on` | `1 1 1` |
| `default.bg` | `0.25 0.25 0.25` |
| `default.border.hilite` | `1 1 1` |
| `default.button` | `0.74902 0.74902 0.74902` |
| `default.button.select` | `0.4 0.4 0.4` |
| `default.buttonstrip` | `0.216 0.22 0.235` |
| `default.check` | `0.75 0.75 0.75` |
| `default.error` | `1 0 0` |
| `default.fg` | `0.65 0.65 0.65` |
| `default.field.bg` | `0.7 0.7 0.7` |
| `default.field.bg.loc` | `0.9 0.9 0.9` |
| `default.field.bg.sel` | `0.95 0.95 0.95` |
| `default.field.fg` | `0.1 0.1 0.1` |
| `default.groove.hilite` | `0.25 0.25 0.25` |
| `default.groove.lolite` | `0.216 0.22 0.235` |
| `default.input` | `0.701961 0.701961 0.701961` |
| `default.label` | `0.4 0.4 0.5` |
| `default.line` | `0.498039 0.498039 0.498039` |
| `default.listentry.bg` | `0.36 0.36 0.36` |
| `default.listentry.bg.loc` | `0.42 0.42 0.42` |
| `default.listentry.fg` | `0.65 0.65 0.65` |
| `default.listentry.outline` | `0.2 0.2 0.2` |
| `default.menu` | `0.25 0.25 0.25` |
| `default.menu.fg` | `0.65 0.65 0.65` |
| `default.menu.loc` | `0.44 0.44 0.44` |
| `default.menubar` | `0.184 0.188 0.2` |
| `default.menubar.loc` | `0.44 0.44 0.44` |
| `default.menuheading.bg` | `0.3 0.3 0.3` |
| `default.multitext.editable` | `0.65 0.9 0.5` |
| `default.multitext.editing` | `0.8 0.98 0.8` |
| `default.multitext.hilite` | `1 1 1` |
| `default.multitext.noteditable` | `0.55 0.65 0.8` |
| `default.negativespace` | `0 0 0` |
| `default.override` | `0.2515 0.405 0.46` |
| `default.paper` | `0.898039 0.898039 0.898039` |
| `default.radio` | `0.4 0.4 0.4` |
| `default.rightcheck` | `0.5 0.5 0.5` |
| `default.scrollbars.bg` | `0.3 0.3 0.3` |
| `default.separator` | `0.6 0.6 0.6` |
| `default.slider.outline` | `0.2 0.2 0.2` |
| `default.slider.thumb` | `0.35 0.35 0.35` |
| `default.slider.thumb.outline` | `0.5 0.5 0.5` |
| `default.stow.bg` | `0.3 0.3 0.3` |
| `default.tile.line` | `0.2 0.2 0.2` |

#### `jive.*` — 39 key(s)

[V]/[O] **CHOP Channel Editor** (keyframes/curves) — read by `libJIVE`. 39 keys. `.bg`/`.fg` pairs on handles, slices, segments, slopes; `.plot.aux1-4` are curve families with `.mark` variants; `.timeline`/`.timemark`/`.currenttime` are the time ruler.

| Key | Shipped (r g b) |
|---|---|
| `jive.accel.bg` | `0.498039 0.498039 0.498039` |
| `jive.accel.fg` | `0.8 0.8 0.8` |
| `jive.channel.bg` | `0.1 0.105 0.12` |
| `jive.channel.crosshairs` | `0.5 0.5 0` |
| `jive.channel.current` | `0 1 0` |
| `jive.channel.display` | `0.333 0.5 1` |
| `jive.channel.normal` | `0.6 0.6 0.6` |
| `jive.channel.picked` | `1 1 0` |
| `jive.channel.plot.aux1` | `0.75 0.25 0.75` |
| `jive.channel.plot.aux1.mark` | `0.875 0.625 0.875` |
| `jive.channel.plot.aux2` | `0.75 0.75 0.25` |
| `jive.channel.plot.aux2.mark` | `0.875 0.875 0.625` |
| `jive.channel.plot.aux3` | `0.25 0.75 0.75` |
| `jive.channel.plot.aux3.mark` | `0.625 0.875 0.875` |
| `jive.channel.plot.aux4` | `1 0 0.5` |
| `jive.channel.plot.aux4.mark` | `1 0.5 0.75` |
| `jive.channel.template` | `0.999 0.5 1` |
| `jive.currenttime.bg` | `0.701961 0.701961 0.701961` |
| `jive.currenttime.fg` | `1 1 1` |
| `jive.frame.timetext` | `0.2 0.2 0.7` |
| `jive.graph.bg` | `0 0 0` |
| `jive.handle.time` | `0.5 0.5 1` |
| `jive.inout.bg` | `0.498039 0.498039 0.498039` |
| `jive.inout.fg` | `0.8 0.8 0.8` |
| `jive.pickbox` | `1 1 1` |
| `jive.rawvalue.bg` | `0.498039 0.498039 0.498039` |
| `jive.rawvalue.fg` | `0.8 0.8 0.8` |
| `jive.scalehandle.bg` | `0.498039 0.498039 0.498039` |
| `jive.scalehandle.fg` | `0.8 0.8 0.8` |
| `jive.segment.bg` | `0.498039 0.498039 0.498039` |
| `jive.segment.fg` | `0.8 0.8 0.8` |
| `jive.slice.bar` | `1 1 0` |
| `jive.slice.box` | `0.498039 0.498039 0.498039` |
| `jive.slope.bg` | `0.498039 0.498039 0.498039` |
| `jive.slope.fg` | `0.8 0.8 0.8` |
| `jive.timeline.bg` | `0.701961 0.701961 0.701961` |
| `jive.timeline.fg` | `1 1 1` |
| `jive.timemark.bg` | `0.701961 0.701961 0.701961` |
| `jive.timemark.fg` | `1 1 1` |

#### `chop.*` — 21 key(s)

[?] **CHOP node track graph** — the mini animation graph drawn on CHOP nodes (distinct from the CHOP Channel Editor). 21 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `chop.graph.bar.0` | `0.47 0.47 0.47` |
| `chop.graph.bar.1` | `0.54 0.54 0.54` |
| `chop.graph.bar.2` | `0.6 0.6 0.6` |
| `chop.graph.bar.3` | `0.8 0.8 0.7` |
| `chop.graph.bar.4` | `0.63 0.8 0.8` |
| `chop.graph.bar.5` | `0.7 0.7 0.9` |
| `chop.graph.bar.6` | `0.9 0.7 0.7` |
| `chop.graph.bar.7` | `0.9 0.9 0.9` |
| `chop.graph.bg` | `0 0 0` |
| `chop.graph.divider` | `0.0196078 0.0196078 0.0196078` |
| `chop.graph.label` | `0 0 0` |
| `chop.graph.pickbox` | `1 1 1` |
| `chop.track.bg` | `0 0 0` |
| `chop.track.channel` | `0.4 0.4 0.4` |
| `chop.track.extendindicator` | `1 1 1` |
| `chop.track.label` | `0 0 0` |
| `chop.track.label.located.bg` | `0.09 0.54 0` |
| `chop.track.label.selected.bg` | `0.27 0.403 0.243` |
| `chop.track.separator` | `0.498039 0.498039 0.498039` |
| `chop.track.time` | `0.2 0.2 1` |
| `chop.track.timebar` | `0.0625 0.0625 0.0625` |

#### `oplist.*` — 13 key(s)

[?] **Operator (OP) list** — the flat list of all operators in a network.

| Key | Shipped (r g b) |
|---|---|
| `oplist.bg` | `0.1 0.105 0.12` |
| `oplist.equal.fg` | `0.8 0.8 0.8` |
| `oplist.header` | `0.05 0.0525 0.06` |
| `oplist.outline` | `0.15 0.1575 0.18` |
| `oplist.select.edithilite` | `0.2 0.21 0.24` |
| `oplist.select.field.enable` | `0.85 0.85 0.85` |
| `oplist.select.field.hilite` | `0.5 0.5 0.5` |
| `oplist.select.hilite` | `0.15 0.1575 0.18` |
| `oplist.select.outline` | `0.25 0.2625 0.3` |
| `oplist.splitbar` | `0.6 0.6 0.6` |
| `oplist.treearrow.fg` | `0 0 0` |
| `oplist.treebutton.bg` | `0.498039 0.498039 0.498039` |
| `oplist.treebutton.fg` | `0 0 0` |

#### `preflist.*` — 13 key(s)

[?] **Parameter list / preferences list** — 13 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `preflist.info.child.bg` | `1 1 1` |
| `preflist.info.child.fg` | `0 0 0` |
| `preflist.info.error` | `1 0 0` |
| `preflist.info.filtered` | `0.6 0.6 0.6` |
| `preflist.info.message` | `0.8 0.8 0.8` |
| `preflist.info.normal` | `0.498039 0.498039 0.498039` |
| `preflist.info.warning` | `0 0 0` |
| `preflist.label.append.bg` | `0.6 0.6 0.6` |
| `preflist.label.hide.bg` | `0.6 0.6 0.6` |
| `preflist.text.bad.fg` | `0.74902 0.74902 0.74902` |
| `preflist.text.good.fg` | `0 0 0` |
| `preflist.text.input.bg` | `0.701961 0.701961 0.701961` |
| `preflist.text.picked.bg` | `0.6 0.6 0.6` |

#### `textport.*` — 11 key(s)

[?] **Textport** — the built-in Python console/log viewer. 11 keys, unprobed. Tinting this changes console readability, a common dark-theme win.

| Key | Shipped (r g b) |
|---|---|
| `textport.bg` | `0.05 0.05 0.05` |
| `textport.editable` | `0.5 0.9 1` |
| `textport.editing` | `0.8 0.98 0.8` |
| `textport.fg` | `0.75 0.75 0.75` |
| `textport.hilite` | `0.9 0.9 0.9` |
| `textport.line` | `0.45 0.45 0.45` |
| `textport.noteditable` | `0.55 0.65 0.8` |
| `textport.status.bg1` | `0.301961 0.301961 0.301961` |
| `textport.status.bg2` | `0.498039 0.498039 0.498039` |
| `textport.status.fg1` | `0.898039 0.898039 0.898039` |
| `textport.status.fg2` | `0 0 0` |

#### `ramp.*` — 10 key(s)

[?] Uncatalogued namespace — name only, no evidence of behaviour.

| Key | Shipped (r g b) |
|---|---|
| `ramp.bg` | `0 0 0` |
| `ramp.blue` | `0 0 1` |
| `ramp.green` | `0 1 0` |
| `ramp.marker.normal.bg` | `0 0 0` |
| `ramp.marker.normal.fg` | `1 1 1` |
| `ramp.marker.selected.bg` | `1 1 1` |
| `ramp.marker.selected.fg` | `0 0 0` |
| `ramp.red` | `1 0 0` |
| `ramp.saturation` | `1 0 0` |
| `ramp.value` | `1 1 1` |

#### `xcfladder.*` — 10 key(s)

[?] **Expression/CF function ladder** — the value ladder beside fields. Colours plus the `xcfladder.*` options (box size, rechoose delay, steps per rotation).

| Key | Shipped (r g b) |
|---|---|
| `xcfladder.active.bg` | `0.15 0.15 0.15` |
| `xcfladder.bg` | `0.1 0.1 0.1` |
| `xcfladder.circle.baseleft` | `0.5 0.15 0.15` |
| `xcfladder.circle.baseright` | `0.1 0.5 0.1` |
| `xcfladder.circle.bg` | `0.15 0.15 0.15` |
| `xcfladder.circle.line` | `0.15 0.15 0.15` |
| `xcfladder.circle.outline` | `0.15 0.15 0.15` |
| `xcfladder.outline` | `0.25 0.25 0.25` |
| `xcfladder.text` | `0.6 0.6 0.6` |
| `xcfladder.text.levels` | `0.6 0.6 0.6` |

#### `worksheet.*` — 9 key(s)

[V]/[O] **Network editor background** — read by `libOPUI`. 9 colour keys. Pairs with 29 `worksheet.*` options covering zoom, scroll, the wheel crossfade, and autoscroll. Highest-impact key for overall feel: `worksheet.bg`.

| Key | Shipped (r g b) |
|---|---|
| `worksheet.autoscroll.highlight` | `0.125 0.126 0.15` |
| `worksheet.bg` | `0.1 0.105 0.12` |
| `worksheet.external.flag` | `0.4 0.4 0.15` |
| `worksheet.grid` | `0.1 0.1 0.15` |
| `worksheet.gridxy` | `0.15 0.15 0.3` |
| `worksheet.newnode` | `0.8 0.8 0.8` |
| `worksheet.operatemode` | `0.8 0.3 0.3` |
| `worksheet.pickbox` | `0.8 0.8 0.8` |
| `worksheet.private` | `0.1 0.1 0.85` |

#### `dialog.*` — 8 key(s)

[V] **Dialogs and modal windows.** Contains the two keys with the empty-second-field quirk (§2).

| Key | Shipped (r g b) |
|---|---|
| `dialog.bg` | `0.2 0.2 0.2` |
| `dialog.commented` | `0 0 1` |
| `dialog.commented.comp` | `0 0.7 1` |
| `dialog.commenthint` | `(empty) 0.2 0.2 0.2` |
| `dialog.commenthint.comp` | `(empty) 0.5 0.5 0.5` |
| `dialog.confirm.bg` | `0.3 0.3 0.3` |
| `dialog.confirm.fg` | `0 0 0` |
| `dialog.error` | `1 0 0` |

#### `textsheet.*` — 8 key(s)

[?] **Text sheet** — the DAT spreadsheet/text editor view. 8 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `textsheet.bg` | `0.1 0.105 0.12` |
| `textsheet.editable` | `0.7 0.7 1` |
| `textsheet.noteditable` | `0.1 0.734 0.885` |
| `textsheet.row.hilite` | `1 1 0` |
| `textsheet.select.copy` | `0.4 0.4 0.6` |
| `textsheet.select.firstdrag` | `0.5 0.5 0.5` |
| `textsheet.select.outline` | `0.25 0.25 0.25` |
| `textsheet.select.stippleoutline` | `0.6 0.6 0.6` |

#### `top.*` — 6 key(s)

[?] **TOP node / image viewer** colour keys. 6 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `top.viewer.bg` | `0.1 0.1 0.1` |
| `top.viewer.fieldguide.grid` | `0.4 0.4 0.4` |
| `top.viewer.fieldguide.hilite` | `1 0 0` |
| `top.viewer.fieldguide.safe` | `0.7 0.7 0.7` |
| `top.viewer.label.text` | `1 1 1` |
| `top.viewer.separator` | `0.498039 0.498039 0.498039` |

#### `graph.*` — 5 key(s)

[V] **Split ownership — read carefully.** `graph.line.*` is the CHOP Channel Editor keyframe spline; `graph.linewidth`, `graph.linewidth.dots`, `graph.pixelsperdot`, `graph.separator` belong to the CHOP track graph. `graph.linewidth` is read by two libraries at once. See §6.5-6.6.

| Key | Shipped (r g b) |
|---|---|
| `graph.grid.axes` | `0.2 0.2 0.2` |
| `graph.grid.axes.main` | `0.25 0.25 0.25` |
| `graph.grid.label` | `0.7 0.7 0.7` |
| `graph.grid.label.selected.bg` | `0 0 0` |
| `graph.separator` | `0.12 0.12 0.12` |

#### `performance.*` — 5 key(s)

[?] **Performance monitor** display. 5 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `performance.actualtime` | `0 0.5 0` |
| `performance.bg` | `0.05 0.05 0.05` |
| `performance.embeddedtime` | `0.3 0.3 0.3` |
| `performance.text` | `0.7 0.7 0.7` |
| `performance.totaltime` | `0.15 0.15 0.15` |

#### `geodetail.*` — 4 key(s)

[?] **Geometry detail / info panel.** 4 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `geodetail.bg` | `0 0 0` |
| `geodetail.compare` | `0.11016 0.765 0.743172` |
| `geodetail.fg` | `0 0 1` |
| `geodetail.template` | `0.871413 0.377316 0.892` |

#### `icon.*` — 4 key(s)

[?] **Icon rendering** colour keys, plus an `icon.blendtype` option.

| Key | Shipped (r g b) |
|---|---|
| `icon.located` | `1 1 1` |
| `icon.notlocated` | `0.8 0.8 0.8` |
| `icon.outline` | `0 0 0` |
| `icon.shadow` | `0.4 0.4 0.4` |

#### `knob.*` — 4 key(s)

[?] **Knob (rotary control) rendering.** 4 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `knob.hash` | `0 0 0` |
| `knob.inner` | `0.4 0.46 0.65` |
| `knob.notch` | `0.95 0.95 0` |
| `knob.outline` | `0 0 0` |

#### `mididevice.*` — 4 key(s)

[?] **MIDI device / control surface UI.** 4 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `mididevice.mapper.bg` | `0.701961 0.701961 0.701961` |
| `mididevice.mapper.disabled` | `0.4 0.6 0.4` |
| `mididevice.mapper.enabled` | `0.1 0.6 0.2` |
| `mididevice.mapper.enabled.outline` | `0 0 0` |

#### `overlap.*` — 4 key(s)

[?] **Tile overlap indicator.** 4 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `overlap.bg` | `0.513726 0.545098 0.513726` |
| `overlap.range1` | `0.956863 0.643137 0.376471` |
| `overlap.range2` | `0.980392 0.980392 0.823529` |
| `overlap.range3` | `0.980392 0.980392 0.823529` |

#### `playbar.*` — 4 key(s)

[?] **Playback / timeline bar.** 4 keys, unprobed. Related `playback.*` key below.

| Key | Shipped (r g b) |
|---|---|
| `playbar.bg` | `0.6 0.6 0.6` |
| `playbar.off` | `0.1 0.1 0.1` |
| `playbar.on` | `0.05 0.8 0.05` |
| `playbar.reset` | `1 1 0` |

#### `range.*` — 4 key(s)

[?] **Range / value slider component.** 4 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `range.arrow.bg` | `0.6 0.6 0.6` |
| `range.arrow.fg` | `0.701961 0.701961 0.701961` |
| `range.bg` | `0.498039 0.498039 0.498039` |
| `range.tick` | `0 0 0` |

#### `statusbar.*` — 4 key(s)

[?] **Status bar** at the bottom of the network editor. 4 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `statusbar.bg1` | `0.15 0.15 0.15` |
| `statusbar.bg2` | `0.5 0.5 0.5` |
| `statusbar.fg1` | `0.685 0.685 0.685` |
| `statusbar.fg2` | `0 0 0` |

#### `MAT.*` — 3 key(s)

[?] Uncatalogued namespace — name only, no evidence of behaviour.

| Key | Shipped (r g b) |
|---|---|
| `MAT` | `0.625 0.58 0.28` |
| `MAT.editbg` | `0.67 0.64 0.40` |
| `MAT.hilite` | `0.85 0.828 0.38` |

#### `SOP.*` — 3 key(s)

[?] Uncatalogued namespace — name only, no evidence of behaviour.

| Key | Shipped (r g b) |
|---|---|
| `SOP` | `0.29 0.5 0.7` |
| `SOP.editbg` | `0.38 0.56 0.72` |
| `SOP.hilite` | `0.495 0.765 0.9` |

#### `channelexport.*` — 3 key(s)

[?] **CHOP channel export** display. 3 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `channelexport.destination.bg` | `0.6 0.6 0.6` |
| `channelexport.node.bg` | `0.4 0.4 0.4` |
| `channelexport.node.fg` | `0.898039 0.898039 0.898039` |

#### `inputfield.*` — 3 key(s)

[?] **Input field** component colours. 3 keys, unprobed. Pairs with the `field.*` options.

| Key | Shipped (r g b) |
|---|---|
| `inputfield.disabled` | `0.4 0.4 0.4` |
| `inputfield.enabled` | `0.1 0.1 0.1` |
| `inputfield.hilite` | `0.65 0.65 0.65` |

#### `panel.*` — 3 key(s)

[?] **Panel / palette component.** 3 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `panel.bg` | `0.1 0.1 0.1` |
| `panel.edit.normal` | `1 1 1` |
| `panel.edit.picked` | `1 1 0` |

#### `startup.*` — 3 key(s)

[?] **Startup / splash screen.** 3 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `startup.error` | `1 0.35 0.35` |
| `startup.message` | `0.7 0.7 0.7` |
| `startup.warning` | `0.9 0.9 0.498` |

#### `tooltip.*` — 3 key(s)

[?] **Tooltip** rendering. 3 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `tooltip.bg` | `0.15 0.15 0.15` |
| `tooltip.fg` | `0.45 0.45 0.45` |
| `tooltip.fg.value` | `0.7 0.7 0.8` |

#### `COMP.*` — 2 key(s)

[?] Uncatalogued namespace — name only, no evidence of behaviour.

| Key | Shipped (r g b) |
|---|---|
| `COMP` | `0.19 0.19 0.19` |
| `COMP.hilite` | `0.5 0.5 0.5` |

#### `POP.*` — 2 key(s)

[?] Uncatalogued namespace — name only, no evidence of behaviour.

| Key | Shipped (r g b) |
|---|---|
| `POP` | `0.315 0.305 0.75` |
| `POP.hilite` | `0.56 0.6 1.2` |

#### `circle.*` — 2 key(s)

[?] **Circle / radial picker component.** 2 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `circle.check` | `0 1 0` |
| `circle.minus` | `1 0 0` |

#### `colorbutton.*` — 2 key(s)

[?] **Colour swatch button** component. 2 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `colorbutton.bg` | `1 1 1` |
| `colorbutton.fg` | `0 0 0` |

#### `desktop.*` — 2 key(s)

[?] **Desktop / background root.** 2 keys, unprobed. Candidate for the global base tone.

| Key | Shipped (r g b) |
|---|---|
| `desktop.exposeon` | `1 0 0` |
| `desktop.nosave` | `0 0.4 0.1` |

#### `extendedhelp.*` — 2 key(s)

[?] **Extended help / doc viewer.** 2 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `extendedhelp.bg` | `0.2 0.2 0.212` |
| `extendedhelp.fg` | `0.65 0.65 0.65` |

#### `frameindicator.*` — 2 key(s)

[?] **Frame / playback position indicator.** 2 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `frameindicator.bg` | `0.6 0.6 0.6` |
| `frameindicator.fg` | `0.701961 0.701961 0.701961` |

#### `gadget.*` — 2 key(s)

[?] **Generic widget base** colours. 2 keys, unprobed. Note the singular spelling.

| Key | Shipped (r g b) |
|---|---|
| `gadget.dualmonitor.bg` | `0 0 0` |
| `gadget.offscreenwin.bg` | `0 0 0` |

#### `grouplist.*` — 2 key(s)

[?] **Group list / grouping UI.** 2 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `grouplist.bg` | `0.1 0.105 0.12` |
| `grouplist.splitbar` | `0.6 0.6 0.6` |

#### `lasso.*` — 2 key(s)

[?] **Lasso / freeform select tool.** 2 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `lasso.bg` | `0 0 0` |
| `lasso.fg` | `1 1 1` |

#### `netoverview.*` — 2 key(s)

[?] **Network overview (minimap).** 2 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `netoverview.bg` | `0.6 0.6 0.6` |
| `netoverview.fg` | `0 0 0` |

#### `opinfo.*` — 2 key(s)

[?] **Operator info / tooltip panel.** 2 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `opinfo.bg` | `1 1 1` |
| `opinfo.fg` | `0 0 0` |

#### `slider.*` — 2 key(s)

[?] **Slider component.** 2 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `slider.linethumb.bg` | `0 0 0` |
| `slider.linethumb.fg` | `1 1 1` |

#### `splitpane.*` — 2 key(s)

[?] **Split-pane divider.** 2 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `splitpane.bar` | `0.3 0.3 0.4` |
| `splitpane.bar.dragging` | `0.4 0.4 0.4` |

#### `swatch.*` — 2 key(s)

[?] **Colour swatch rendering.** 2 keys, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `swatch.none.bg` | `0.08 0.08 0.08` |
| `swatch.none.text` | `0.63 0.67 0.75` |

#### `CHOP.*` — 1 key(s)

[?] Uncatalogued namespace — name only, no evidence of behaviour.

| Key | Shipped (r g b) |
|---|---|
| `CHOP` | `0.385 0.55 0.275` |

#### `DAT.*` — 1 key(s)

[?] Uncatalogued namespace — name only, no evidence of behaviour.

| Key | Shipped (r g b) |
|---|---|
| `DAT` | `0.575 0.36 0.50` |

#### `OP.*` — 1 key(s)

[?] Uncatalogued namespace — name only, no evidence of behaviour.

| Key | Shipped (r g b) |
|---|---|
| `OP.default` | `0.67 0.67 0.67` |

#### `TOP.*` — 1 key(s)

[?] Uncatalogued namespace — name only, no evidence of behaviour.

| Key | Shipped (r g b) |
|---|---|
| `TOP` | `0.41 0.36 0.575` |

#### `addoperator.*` — 1 key(s)

[?] **"Add Operator" dialog.** 1 key, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `addoperator.fg` | `0 0 0` |

#### `image.*` — 1 key(s)

[?] **Image / thumbnail rendering.** 1 key, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `image.disable.overlay` | `0 0 0` |

#### `nodechooser.*` — 1 key(s)

[?] **Node chooser / type picker.** 1 key, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `nodechooser.down.hilite` | `0.4 0.6 0.4` |

#### `playback.*` — 1 key(s)

[?] **Playback controls.** 1 key, unprobed. Pairs with `playbar.*`.

| Key | Shipped (r g b) |
|---|---|
| `playback.warning` | `1 1 0` |

#### `rubberbox.*` — 1 key(s)

[?] **Rubber-band selection box** in the network editor. 1 key, unprobed.

| Key | Shipped (r g b) |
|---|---|
| `rubberbox.fg` | `1 1 1` |

#### Bare operator-type base colours — 7 key(s)

**[S]** These seven are the base hues from which node tile shades are derived
via the `OP.*.sat`/`.val` and `NODE.*.sat`/`.val` multipliers. See §5.4 —
**retint all of them together.**

| Key | Shipped (r g b) |
|---|---|
| `CHOP` | `0.385 0.55 0.275` |
| `COMP` | `0.19 0.19 0.19` |
| `DAT` | `0.575 0.36 0.50` |
| `MAT` | `0.625 0.58 0.28` |
| `POP` | `0.315 0.305 0.75` |
| `SOP` | `0.29 0.5 0.7` |
| `TOP` | `0.41 0.36 0.575` |

Note: `OP` has **no** `TouchColors` entry — only `OP.*` options exist.

### 10.2 `TouchOptions` — 183 keys

One field. Not colours except where noted: these are sizes, alphas, timings,
and behaviour switches. **Read §5.1** — many of these pair with a `TouchColors`
key of a similar name and both usually need changing.

**Danger: `tile.*.size` and `tile.*.origsize` at 0 silently destroys layout (§3).**

#### `tile.*` — 55 key(s)

[V]/[O] **Network editor node tiles** — read by `libOPUI`. Covers backgrounds, borders, connectors, flags, icons, selection and error states. The best-documented namespace: see §6.1-6.4.

| Key | Shipped value |
|---|---|
| `tile.active.alpha` | `1` |
| `tile.block.alpha` | `0` |
| `tile.block.max` | `10` |
| `tile.border.alpha` | `0.8` |
| `tile.border.size` | `5` |
| `tile.clone.alpha` | `0.65` |
| `tile.clone.blendtype` | `1` |
| `tile.drag.crosshair.size` | `5` |
| `tile.droppable.alpha` | `0.3` |
| `tile.droppable.blendtype` | `1` |
| `tile.flagh.alpha` | `0.8` |
| `tile.flagh.icon.blendtype` | `1` |
| `tile.flagh.icon.locate.alpha` | `0.1` |
| `tile.flagh.origsize` | `18` |
| `tile.flagv.alpha` | `0.8` |
| `tile.flagv.icon.alpha` | `1` |
| `tile.flagv.icon.blendtype` | `1` |
| `tile.flagv.icon.locate.alpha` | `0.2` |
| `tile.flagv.margin` | `0` |
| `tile.flagv.origsize` | `24` |
| `tile.ghost.alpha` | `0.5` |
| `tile.ghost.blendtype` | `9` |
| `tile.icon.bg.alpha` | `0.7` |
| `tile.icon.bg.blendtype` | `1` |
| `tile.icon.border.alpha` | `0.8` |
| `tile.icon.border.type` | `1` |
| `tile.icon.resolution.max` | `128` |
| `tile.in.alpha` | `0.8` |
| `tile.in.border.blendtype` | `2` |
| `tile.inout.dist` | `50` |
| `tile.inout.origsize` | `10` |
| `tile.jumpdown.doubleclick.msec` | `333` |
| `tile.locate.alpha` | `0.2` |
| `tile.locate.offset` | `1` |
| `tile.name.bg.alpha` | `0.6` |
| `tile.out.alpha` | `0.8` |
| `tile.out.border.blendtype` | `2` |
| `tile.out.preview.dist` | `75` |
| `tile.out.preview.h` | `48` |
| `tile.out.preview.w` | `48` |
| `tile.out.preview.w.CHOP` | `64` |
| `tile.out.preview.w.DAT` | `64` |
| `tile.out.preview.w.MAT` | `64` |
| `tile.out.preview.w.POP` | `64` |
| `tile.out.preview.w.SOP` | `64` |
| `tile.out.preview.w.TOP` | `48` |
| `tile.picked.offset` | `7` |
| `tile.resizeborder.alpha` | `1` |
| `tile.resizeborder.size` | `8` |
| `tile.resizecorner.maxratio` | `0.25` |
| `tile.resizecorner.maxsize` | `40` |
| `tile.resizecurrent.alpha` | `0.52` |
| `tile.spaceh.size` | `1` |
| `tile.spacev.size` | `1` |
| `tile.status.maxsize` | `50` |

#### `worksheet.*` — 29 key(s)

[V]/[O] **Network editor background** — read by `libOPUI`. 9 colour keys. Pairs with 29 `worksheet.*` options covering zoom, scroll, the wheel crossfade, and autoscroll. Highest-impact key for overall feel: `worksheet.bg`.

| Key | Shipped value |
|---|---|
| `worksheet.autoscroll.alpha` | `0.5` |
| `worksheet.autoscroll.inc.msecs` | `4` |
| `worksheet.autoscroll.triggerwidth` | `30` |
| `worksheet.comment.alpha` | `0.2` |
| `worksheet.comment.valmultiplier` | `0.8` |
| `worksheet.grid.alpha` | `0.2` |
| `worksheet.grid.blendtype` | `3` |
| `worksheet.grid.drag` | `0.01` |
| `worksheet.grid.snaptocenter` | `1` |
| `worksheet.min.scale.name` | `1` |
| `worksheet.min.zoom` | `0.001` |
| `worksheet.min.zoom.maxfill` | `0.9` |
| `worksheet.min.zoom.minfill` | `0.7` |
| `worksheet.min.zoom.numtiles` | `5` |
| `worksheet.scroll.pixels` | `10` |
| `worksheet.shownetworktext` | `1` |
| `worksheet.shownetworkzoomlevel` | `0` |
| `worksheet.wheel.crossfade.inside.secs` | `0.35` |
| `worksheet.wheel.crossfade.jumpdown.captures` | `10` |
| `worksheet.wheel.crossfade.jumpup.inside` | `0.5` |
| `worksheet.wheel.crossfade.outside.secs` | `0.35` |
| `worksheet.wheel.crossfade.shift.enabled` | `0` |
| `worksheet.wheel.crossfade.total.secs` | `0.35` |
| `worksheet.wheel.jumpdown.home` | `0.7` |
| `worksheet.wheel.jumpdown.slide` | `0.5` |
| `worksheet.wheel.jumpup.home` | `0.7` |
| `worksheet.wheel.maxnoderatio` | `5` |
| `worksheet.wheel.maxnoderatiopanel` | `20` |
| `worksheet.zoom.inc` | `0.1` |

#### `parms.*` — 10 key(s)

[S] **Parameter dialog widgets** — buttons, fields, menus, toggles, expression-mode variants. Largest namespace (167 keys) and the highest-value target for a dark theme: it is what the user stares at while building. `.expr.*` sets run parallel to base `.*` sets.

| Key | Shipped value |
|---|---|
| `parms.button.size` | `50` |
| `parms.expand.label` | `12` |
| `parms.field.height` | `20` |
| `parms.field.size` | `50` |
| `parms.mode.size` | `12` |
| `parms.mode.spacing` | `2` |
| `parms.pagetab.margin` | `6` |
| `parms.slider.thumb.size` | `10` |
| `parms.toggle.size` | `50` |
| `parms.toggle.thumb.size` | `25` |

#### `font.*` — 9 key(s)

[V] **Fonts.** 9 keys: base sizes, xkerning, and two **legitimately empty** face names (§2). `font.relative.size 0` is a legal delta, not a bug — §3 rule 2. This is the namespace to change for overall text sizing.

| Key | Shipped value |
|---|---|
| `font.default.face` | *(empty)* |
| `font.default.size` | `9.0` |
| `font.default.xkerning` | `0` |
| `font.mono.face` | *(empty)* |
| `font.mono.size` | `9.5` |
| `font.mono.xkerning` | `0` |
| `font.ops.switchbitmap.size` | `19` |
| `font.ops.switchdelay.msecs` | `222` |
| `font.relative.size` | `0` |

#### `OP.*` — 8 key(s)

| Key | Shipped value |
|---|---|
| `OP.editbg.sat` | `0.8` |
| `OP.editbg.val` | `1.2` |
| `OP.hi.sat` | `1` |
| `OP.hi.val` | `1.7` |
| `OP.lo.sat` | `1.2` |
| `OP.lo.val` | `0.5` |
| `OP.lo2.sat` | `1` |
| `OP.lo2.val` | `0.3` |

#### `graph.*` — 8 key(s)

[V] **Split ownership — read carefully.** `graph.line.*` is the CHOP Channel Editor keyframe spline; `graph.linewidth`, `graph.linewidth.dots`, `graph.pixelsperdot`, `graph.separator` belong to the CHOP track graph. `graph.linewidth` is read by two libraries at once. See §6.5-6.6.

| Key | Shipped value |
|---|---|
| `graph.line.alpha` | `0.5` |
| `graph.line.select.alpha` | `1` |
| `graph.line.select.multsat` | `1.5` |
| `graph.line.select.multval` | `1.5` |
| `graph.linewidth` | `1` |
| `graph.linewidth.dots` | `3` |
| `graph.linewidth.select` | `3` |
| `graph.pixelsperdot` | `5` |

#### `jive.*` — 8 key(s)

[V]/[O] **CHOP Channel Editor** (keyframes/curves) — read by `libJIVE`. 39 keys. `.bg`/`.fg` pairs on handles, slices, segments, slopes; `.plot.aux1-4` are curve families with `.mark` variants; `.timeline`/`.timemark`/`.currenttime` are the time ruler.

| Key | Shipped value |
|---|---|
| `jive.channel.accel.log10.max` | `0.5` |
| `jive.channel.handle.accel.max.w` | `100` |
| `jive.channel.handle.accel.min.w` | `15` |
| `jive.channel.handle.accel.none.w` | `25` |
| `jive.channel.handle.inout.r` | `4` |
| `jive.channel.handle.inout.selr` | `5` |
| `jive.channel.selected.w` | `1` |
| `jive.channel.zoom.div` | `5` |

#### `default.*` — 7 key(s)

[?] **Fallback tier** — 46 keys, none with a specific twin. Precedence untested; see §6.8. Safe to set only where no specific key exists, which is all 46.

| Key | Shipped value |
|---|---|
| `default.blendtype` | `2` |
| `default.foldertab.margin` | `2` |
| `default.foldertab.thickness` | `4` |
| `default.label.margin.horz` | `4` |
| `default.roundrect` | `2` |
| `default.scrollbar.stepdelay.msecs` | `200` |
| `default.scrollbar.thickness` | `12` |

#### `NODE.*` — 6 key(s)

| Key | Shipped value |
|---|---|
| `NODE.icon.sat` | `1.8` |
| `NODE.icon.val` | `0.27` |
| `NODE.innerborder.sat` | `0.9` |
| `NODE.innerborder.val` | `0.625` |
| `NODE.outerborder.sat` | `1` |
| `NODE.outerborder.val` | `1` |

#### `dat.*` — 6 key(s)

[S] **Text editor.** Also the syntax-highlighting host: `dat.<lang>.<token>` — see §5.5. Language-agnostic keys cover backgrounds, line numbers, comments, selection.

| Key | Shipped value |
|---|---|
| `dat.comment.hilite` | `1` |
| `dat.lineno.relsize` | `0.8` |
| `dat.scroll.minwidth` | `50` |
| `dat.table.keepmax.adjust` | `5` |
| `dat.table.keepmax.secs` | `0.2` |
| `dat.wordwrap.chars` | `80` |

#### `viewer.*` — 4 key(s)

[?] **Viewer panel** default size. Layout, not colour.

| Key | Shipped value |
|---|---|
| `viewer.dat.height` | `300` |
| `viewer.dat.width` | `400` |
| `viewer.default.height` | `400` |
| `viewer.default.width` | `600` |

#### `help.*` — 3 key(s)

[?] **Help tooltip text and delay.** 3 keys, unprobed. Options control the initial/recent delay and text length — behaviour, not just colour.

| Key | Shipped value |
|---|---|
| `help.initialdelay` | `0.5` |
| `help.recentdelay` | `0.2` |
| `help.textlength` | `500` |

#### `xcfladder.*` — 3 key(s)

[?] **Expression/CF function ladder** — the value ladder beside fields. Colours plus the `xcfladder.*` options (box size, rechoose delay, steps per rotation).

| Key | Shipped value |
|---|---|
| `xcfladder.box.size` | `37` |
| `xcfladder.rechoose.delay` | `0.375` |
| `xcfladder.stepsperrotation` | `20` |

#### `CHOP.*` — 2 key(s)

| Key | Shipped value |
|---|---|
| `CHOP.height` | `90` |
| `CHOP.width` | `130` |

#### `COMP.*` — 2 key(s)

| Key | Shipped value |
|---|---|
| `COMP.height` | `130` |
| `COMP.width` | `160` |

#### `DAT.*` — 2 key(s)

| Key | Shipped value |
|---|---|
| `DAT.height` | `90` |
| `DAT.width` | `130` |

#### `MAT.*` — 2 key(s)

| Key | Shipped value |
|---|---|
| `MAT.height` | `90` |
| `MAT.width` | `130` |

#### `POP.*` — 2 key(s)

| Key | Shipped value |
|---|---|
| `POP.height` | `90` |
| `POP.width` | `130` |

#### `SOP.*` — 2 key(s)

| Key | Shipped value |
|---|---|
| `SOP.height` | `90` |
| `SOP.width` | `130` |

#### `TOP.*` — 2 key(s)

| Key | Shipped value |
|---|---|
| `TOP.height` | `90` |
| `TOP.width` | `130` |

#### `field.*` — 2 key(s)

[?] **Input field** behaviour — cursor blink period and value-ladder delay. Not colour.

| Key | Shipped value |
|---|---|
| `field.cursor.msec` | `500` |
| `field.valueladderdelay.msec` | `600` |

#### `mouse.*` — 2 key(s)

[?] **Mouse wheel** behaviour — boost and use-msec timing. Not colour.

| Key | Shipped value |
|---|---|
| `mouse.wheel.boost` | `1` |
| `mouse.wheel.use.msec` | `200` |

#### `chop.*` — 1 key(s)

[?] **CHOP node track graph** — the mini animation graph drawn on CHOP nodes (distinct from the CHOP Channel Editor). 21 keys, unprobed.

| Key | Shipped value |
|---|---|
| `chop.track.label.overlay.alpha` | `0.7` |

#### `dragdrop.*` — 1 key(s)

[V] **Drag-and-drop** hover delay in ms — a *timing* value despite the name. See §6.7.

| Key | Shipped value |
|---|---|
| `dragdrop.hover.delay.msecs` | `500` |

#### `file.*` — 1 key(s)

[?] **File browser** — one key, branched-file timing. Not colour.

| Key | Shipped value |
|---|---|
| `file.branched.secs` | `1` |

#### `geo.*` — 1 key(s)

[?] **Geometry viewer** — one key, orthographic floor grid size. Layout.

| Key | Shipped value |
|---|---|
| `geo.orthofloor.gridsize` | `1` |

#### `icon.*` — 1 key(s)

[?] **Icon rendering** colour keys, plus an `icon.blendtype` option.

| Key | Shipped value |
|---|---|
| `icon.blendtype` | `2` |

#### `list.*` — 1 key(s)

[?] **Generic list row** — one key, row height. Layout, not colour.

| Key | Shipped value |
|---|---|
| `list.rowheight` | `20` |

#### `oplist.*` — 1 key(s)

[?] **Operator (OP) list** — the flat list of all operators in a network.

| Key | Shipped value |
|---|---|
| `oplist.flag.alpha` | `0.2` |

#### `osx.*` — 1 key(s)

[?] **macOS platform integration** — one key (`osx.trackpad.zoom`). Behaviour, unprobed.

| Key | Shipped value |
|---|---|
| `osx.trackpad.zoom` | `0.1` |

#### `touch.*` — 1 key(s)

[?] **Touch/multi-touch** — one key, click radius. Not colour.

| Key | Shipped value |
|---|---|
| `touch.click.radius` | `5` |

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


