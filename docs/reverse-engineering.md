# Reverse-engineering notes

How `tdtheme` came to exist, and what is actually known about TouchDesigner's
undocumented UI configuration. Kept as a durable record because the source of
truth here is a black box — Derivative documents none of this.

Derived by reading the symbol and string tables of
`TouchDesigner.app/Contents/Frameworks/*.dylib` and disassembling call sites.
Verified empirically wherever possible; unverified claims are marked as such.

Verified against **TouchDesigner 2025.33230** on macOS.

---

## 1. The two stores

Undocumented by Derivative — not in the wiki, no useful forum hits. Two
separate files, both tab-separated, both CRLF, both read at startup:

| File | Shape | Keys | Owns |
|---|---|---|---|
| `TouchColors` | `key<TAB>r<TAB>g<TAB>b` | 626 | every UI colour |
| `TouchOptions` | `key<TAB>value` | 183 | numeric UI options |

Which file owns a key is decided by which store reads it:

- `UIgetOptions()->getOption*(name)` → `TouchOptions`
- `UIgetColors()->getColor<UT_Color>(name)` → `TouchColors`

**The two files share zero keys.** Disjoint namespaces, so they can be merged
and validated independently.

### Format quirks

These matter, because the naive reading of each is wrong:

- **Two `TouchColors` keys have a stray empty second field.**
  `dialog.commenthint` and `dialog.commenthint.comp` are
  `key<TAB><TAB>r<TAB>g<TAB>b` — five fields where every other line has four.
  The colour is therefore the **last three** fields, not fields 2-4. A parser
  that reads fields 2-4 gets the wrong value; a serializer that writes back
  only RGB silently drops the empty field. This is the single reason
  `tdtheme.py` stores the *full field list* after the key rather than a
  decoded triple.
- **Some option values are legitimately empty**: `font.default.face` and
  `font.mono.face` both parse to `[""]`. An empty value is not a missing key.
- **Colour channels are not clamped to 1.0.** `POP.hilite` ships as
  `0.56 0.6 1.2`. Values above 1.0 are intentional.
- **A blank line would be silently dropped** by a parser that skips empties.
  Neither shipped file has one, but a dropped line is an unrequested change,
  so `tdtheme` preserves them.
- No comments, no duplicate keys, pure ASCII, trailing newline present.

Both files round-trip byte-for-byte through `tdtheme`. That invariant is the
load-bearing assumption of the whole tool and is enforced by
`tests/test_roundtrip.py`.

---

## 2. Library ownership

Each dylib reads a disjoint option namespace:

- **`libOPUI.dylib`** — network editor. Owns `tile.*`, `worksheet.*`. Has
  **zero** `graph.*`.
- **`libJIVE.dylib`** — CHOP Channel Editor (keyframes). Owns `jive.*` and
  `graph.line.*` (`alpha`, `select.alpha`, `select.multsat`, `select.multval`,
  `linewidth`, `linewidth.select`). Proven by string adjacency to
  `jive.segment.bg` plus RTTI `SI_Segment` / `SI_Slope` / `SI_Accel`.
- **`libCHILI.dylib`** — CHOP track graph, the mini anim graph on nodes. Owns
  `graph.linewidth`, `graph.linewidth.dots`, `graph.pixelsperdot`,
  `graph.separator`.

---

## 3. Findings

### `graph.line.*` is not about network wires

It is the keyframe spline in the CHOP Channel Editor. `multsat`/`multval` are
a pair modulating the curve's colour on selection (saturation + value), not
separate curves.

### `graph.linewidth` is a name collision

The same flat key is read by both `libJIVE` and `libCHILI`, so one value hits
two unrelated renderers.

### There is no network-wire width knob anywhere

Only three `drawLine` call sites in `libOPUI`, signature
`drawLine(x0, y0, x1, y1, r, g, b, alpha)` — no width argument. Stroke
thickness is implicit in `GX_BufferedDrawer`. **Wire appearance is
colour-only.**

### Wire colours

All in `TouchColors`:

- `tile.connection.hilite1` / `tile.connection.hilite2` — the hover highlight,
  a pair cached 16 bytes apart. **Confirmed by probe**: setting them turns
  wires red on hover.
- `tile.dockedconnection.line`, `tile.replicatorconnection.line`,
  `tile.hiddenconnection.line`, `tile.extraconnection.line` (+`.hilite`),
  `tile.connector.bg` / `fg` — the rest of the wire palette.
- **Unresolved:** which key, if any, sets the *resting* wire colour.
  `tile.dockedconnection.line` and `default.tile.line` were each probed alone
  with no visible effect on non-hovered wires.

### There is no `hover` key

`dragdrop.hover.delay.msecs` in `TouchOptions` is a *timing* value. The
network editor has zero "hover" strings; the hover highlight is the `.hilite`
keys.

### The `default.<key>` tier

`TouchColors` contains 46 keys in a `default.` tier, e.g. `default.tile.line`,
`default.bg`, `default.fg`, `default.button`, `default.field.bg`.

- **None of the 46 has a specific twin.** `default.tile.line` exists;
  `tile.line` does not. For these keys the `default.` form is the only form.
- Which tier wins if both are set is **untested**. `tdtheme` warns on that
  combination rather than encoding a guess.
- Worth settling with a probe-and-restart experiment; see open questions.

### `tile.inout.origsize` is a scale baseline, not just a connector size

Setting it to `0` collapses **both** the input/output nubs **and** the node's
top border to thin slivers, with no error from TouchDesigner. Confirmed by
isolation test after the fact. Shipped value `10`; restored.

Sibling baselines: `tile.flagh.origsize 18`, `tile.flagv.origsize 24`.

### `font.relative.size` is legitimately `0`

Shipped at `0` in `TouchOptions` because it is a *delta* from the base font
size, not an absolute size. An unconditional "size must be > 0" validation
rule false-positives here, and on the untouched shipped file — which would
make **every** `apply` fail. Caught by test, not by inspection. `tdtheme`
therefore hard-errors only on `tile.*.size` / `tile.*.origsize` and judges
other size keys relative to the baseline.

---

## 4. What TouchDesigner does with these files

**Measured, not assumed.** Checksums taken before launching TouchDesigner and
again after quitting it were identical, and file mtimes never moved from the
last `tdtheme apply` across a full session.

- TouchDesigner **reads** both stores at startup.
- TouchDesigner **never writes** them.
- Therefore editing while it runs is safe; a restart is needed only for a
  change to become **visible** — a display refresh, not a durability concern.

This corrects an earlier, wrong claim recorded in this project's own notes:
that TouchDesigner rewrites `TouchOptions` on exit and that edits made while
it was running would be lost. That came from attributing a post-session file
change to TouchDesigner when the edit was made in the same session by hand.
The apparent supporting evidence — "it reset `graph.linewidth` but left
`graph.line.alpha` modified" — was simply two different edits at two
different times.

Re-verify after any TouchDesigner update with `./check-td-writes`.

---

## 5. Ruled out

- **`resources`, `resources.grey`, `resources.tan`, `resources.lava`,
  `resources.new`, `resources.oldtan`, `resources.small`** — a legacy parallel
  system, `Key:<TAB>value` with X11 colour names. The filenames and several
  of their keys (`SelectOnColor`, `OpTileSmallFontSize`) appear nowhere in
  the binaries. `ForegroundColor` and `LineColor` do appear, but not in a way
  that ties them to these files. Treated as dead.
- **A built-in theme switcher.** No theme/colourscheme UI strings in `libOPUI`
  or the main binary. No third-party tool found either.
- **Live reload / scripting API for these files.** Port 8888 is Jupyter, not
  TouchDesigner. No usable API found — hence apply-then-restart.

### Not covered (three more theming systems)

Present in the same directory, different formats, untouched by `tdtheme`:

| File(s) | Format | Controls |
|---|---|---|
| `colorPalette.def`, `opColorPalette.def` | count header + rows | Palette browser colour wheels |
| `3DSceneColors` (+`.grey`/`.bw`/`.wb`) | `Key:<TAB>r g b<SPACE># comment` | 3D viewport |
| `MiscColors` | same | locks, pending changes, keyframes |

Each would be one more parser behind the existing interface.

---

## 6. Open questions

1. **Which key sets the resting wire colour?** Hover is solved; resting is not.
2. **Which `default.*` tier wins** when both forms are set. Needs a
   probe-and-restart experiment.
3. **Does anything rewrite these files** on a build other than 2025.33230?

---

## 7. Method notes

- `codesign` and `otool` work fine on these dylibs; `libOPUI` retains 1587
  symbols (not stripped).
- `__cstring` has `vmaddr == file offset`, so string offsets map directly to
  file offsets.
- **Presence of a key in a dylib is not proof it is used.** Some keys are
  built at runtime by string concatenation — e.g. `.hilite` appended to
  `.line`. Absence of a string is likewise weak evidence.
- Editing files inside the app bundle breaks the code signature, but the seal
  was **already** invalid on this machine
  (`missing/invalid sealed resource in Python.framework`) before any edit.
- **The main methodological lesson of this project:** do not attribute an
  observed change to a third party without controlling for your own edits in
  the same window. Doing so produced a confident, wrong, load-bearing claim
  that the tool then warned about for hours.
