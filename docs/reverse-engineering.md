# Reverse-engineering notes

How `tdtheme` came to exist, and what is actually known about TouchDesigner's
undocumented UI configuration. Kept as a durable record because the source of
truth here is a black box — Derivative documents none of this.

Derived by reading the symbol and string tables of
`TouchDesigner.app/Contents/Frameworks/*.dylib` and disassembling call sites.
Verified empirically wherever possible; unverified claims are marked as such.

Verified against **TouchDesigner 2025.33230** on macOS.

Covers three artefacts, not two: the two tab-separated colour/option stores
(§1–§4) and the TIFF icon directory (§5), which turned out to need a
different approach in almost every respect.

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
- The icons are read **lazily on first use, then cached for the process** —
  see §5.
- Therefore editing while it runs is safe; a restart is needed only for a
  change to become **visible** — a display refresh, not a durability concern.
  For icons a restart is needed even to *look*, because the cached bitmap wins
  over a later edit of the file.

This corrects an earlier, wrong claim recorded in this project's own notes:
that TouchDesigner rewrites `TouchOptions` on exit and that edits made while
it was running would be lost. That came from attributing a post-session file
change to TouchDesigner when the edit was made in the same session by hand.
The apparent supporting evidence — "it reset `graph.linewidth` but left
`graph.line.alpha` modified" — was simply two different edits at two
different times.

Re-verify after any TouchDesigner update with `./check-td-writes`, which now
covers the `Icons` directory as well as both stores. It was extended to the
icons when icon theming landed, so the read-only claim is tested against the
third artefact too rather than being carried over on the strength of the first
two.

---

## 5. The icon directory

### Where they are, and how they are found

`libUI.dylib` resolves every glyph by **name**, not by a manifest. The strings
in the binary are:

```
UI_Icon
/Icons/
.tiff
Couldn't find icon: %s
```

and the call site builds `<ConfigDir>/Icons/<name>.tiff`. So a glyph is found by
string concatenation, which is the same pattern that makes some `TouchColors`
keys invisible to a plain string search (see §3, `graph.line.*`).

Five icon-named directories exist in the install. Only the first is reached
through `UI_Icon`:

| Directory | Contents | Themed? |
|---|---|---|
| `Config/Icons` | 97 `.tiff` UI glyphs | yes |
| `Config/IconsApp` | `toe.ico`, `tox.ico` - dock and file-type icons | no |
| `Samples/Map/Icons` | sample project assets | no |
| `Samples/ProjectPackager/Icons` | sample project assets | no |
| `Frameworks/Python.framework/.../idlelib/Icons` | IDLE's own icons | no |

### They are cached, so a restart is not optional

Each icon is read on first use and memoised for the process. A window opened
later in the session gets the cached bitmap, not the file. So unlike the colour
stores — which are read once at startup and only need a restart to be
*visible* — an icon swap is not picked up at all until the app restarts. Editing
the files while TouchDesigner runs is still safe; nothing is ever written back.

### The format, and the three things that will corrupt it

The 97 files are classic little-endian TIFF, 8 bits per sample, photometric
RGB. Measured across the set:

| Property | Count | Note |
|---|---|---|
| LZW compression | 89 | |
| uncompressed | 8 | |
| multi-strip | 5 | the four `*FaceOverlay` files plus `BypassOverlay` |
| RGBA | 96 | |
| RGB (no alpha) | 1 | `BookmarkButton` |
| **premultiplied alpha** | **95** | see below |

Three separate things had to be right, and each fails *silently* rather than
loudly:

**1. Multi-strip.** A TIFF may split its pixel data across several strips, and
five of these do (16 strips each, for the 256×256 overlays). The obvious
implementation — concatenate the strips and decode once — desynchronises,
because LZW resets its code table at the head of each strip, and yields
plausible-looking garbage. They must be decoded strip by strip.

**2. LZW code-width transition.** The decoder must widen from 12-bit to 13-bit
codes when the table reaches 4094 entries, not at 4096. Getting this off by one
means codes written at 12 bits are read back at 13.

**3. Premultiplied alpha — the expensive one.** 95 of 97 icons store
*premultiplied* (associated) alpha. TIFF 6.0 leaves the convention **undefined**
when `SamplesPerPixel` is 4 and no `ExtraSamples` tag is present, and in
practice decoders assume *premultiplied*: libtiff, Photoshop and macOS `sips`
included. A reader that treats them as straight alpha renders every glyph too
light; a writer that emits straight-alpha data without saying so has every
reader multiply alpha in a second time, and the icon renders too dark with its
thinnest anti-aliased strokes gone. Nothing errors — the file is a valid TIFF,
it just looks wrong. The fix is to emit `ExtraSamples = 2` (unassociated)
explicitly.

This is the one claim in the project that is verified against a decoder we did
not write: `tests/test_icons.py` converts generated files with `sips`, reads
the PNG back, and requires a max channel difference of **0** against our own
reading — plus a counterfactual that declares the data premultiplied and
confirms the check can fail.

### Byte-level diffing is meaningless here

A regenerated icon is always a different file from the shipped one. It is
single-strip where five shipped files are not, explicitly straight-alpha where
95 are premultiplied, and about 5 KB smaller because the Photoshop XMP and IPTC
blobs (tag 34377 and friends) are dropped. So a sha256 comparison reports all
97 files as changed even when the recipe altered nothing.

Pixel comparison is the only meaningful measure, and the two disagree sharply:
`mono` is a pure `grayscale` recipe, and it leaves **79 of 97** icons
pixel-for-pixel identical. The remaining 18 are the genuinely coloured ones.
`tdtheme icons diff` decodes to report this; `list` and `status` stay
byte-level for speed and say so in their wording.

### Why every theme ships all 97 files

Because `UI_Icon` looks icons up by name, a theme with a *partial* set would
leave every missing glyph rendering with whatever the previously applied theme
left in the install. There is no fallback to the stock file. A complete
overwrite is the only model in which switching themes cannot leak state, and it
is what makes `apply default` a true reset — which is why `default`'s recipe is
an empty `ops` list meaning *copy the baseline byte for byte*, not re-encode.

---

## 6. Ruled out

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
- **Runtime tinting of icons.** Nothing in `libUI` suggests the glyphs are
  colour-managed or modulated by a `TouchColors` key. The 69 pure-white icons
  are white in the file, and the themes reach them by rewriting the file.

### Not covered (three more theming systems)

Present in the same directory, different formats, untouched by `tdtheme`:

| File(s) | Format | Controls |
|---|---|---|
| `colorPalette.def`, `opColorPalette.def` | count header + rows | Palette browser colour wheels |
| `3DSceneColors` (+`.grey`/`.bw`/`.wb`) | `Key:<TAB>r g b<SPACE># comment` | 3D viewport |
| `MiscColors` | same | locks, pending changes, keyframes |

Each would be one more parser behind the existing interface.

---

## 7. Open questions

1. **Which key sets the resting wire colour?** Hover is solved; resting is not.
2. **Which `default.*` tier wins** when both forms are set. Needs a
   probe-and-restart experiment.
3. **Does anything rewrite these files** on a build other than 2025.33230?
4. **Do the icons survive a TouchDesigner update intact?** `validate_icons`
   will report resized or new glyphs per theme after `capture --force`, but
   whether the vendor re-exports them with different premultiplication is
   unknown.
5. **Are the black-ink glyphs (`Cook`, `Grid`, `CommentOffSmall`) ever drawn on
   a dark surface?** They are invisible there, and no theme can fix that by
   tinting. Only a look inside a running TouchDesigner would say.

---

## 8. Method notes

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
- **The second lesson, learned the same way:** a claim proven for one artefact
  does not transfer to a neighbour just because they are adjacent and share a
  directory. The read-only finding was established for `TouchColors` and
  `TouchOptions`, and it was tempting to extend it to `Icons` implicitly. It
  happens to hold, but only because `check-td-writes` was extended to cover
  the icon set and the claim was re-tested rather than assumed.
- **Verify format assumptions with a decoder you did not write.** The
  premultiplied-alpha finding (§5) was the one place where being confidently
  wrong was invisible in every local check: our encode and our decode agreed
  perfectly and were both wrong. The disagreement had to come from outside —
  `sips`, i.e. macOS ImageIO's libtiff — and it immediately pinned the value
  of `ExtraSamples`. A self-consistent test suite cannot catch a shared
  misreading of a specification.
