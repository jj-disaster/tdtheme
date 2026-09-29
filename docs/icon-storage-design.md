# Icon storage: a design note

**Status: step 1 shipped; steps 2–5 still open.** This is no longer wholly
unimplemented. §5 step 1 — a theme may hold a *subset* of the icon set, with
the baseline supplying the rest — is built and in daily use. It landed as four
commits: `68be6d4` (`tdicons.copy_icons` grew `fill_from`, and
`tdtheme._apply_icon_set` passes `fill_from=baseline_icons_dir()`), `179a3b0`
(`tests/test_icons.py` asserts a *subset* rather than equality, so a partial set
is legal), `8e68276` (the shortfall finding shortened to two counts and a
source), and `bc0ab02` (`defaultnowarn` cut down to a single icon). Steps 2–5 —
materialise on demand, untrack the committed TIFFs, a recipe-writing importer,
and close the two `match`/recolour footguns — remain a proposal.

The worked example is `themes/defaultnowarn`: it ships **one** icon,
`WarnFace.tiff` at 1,354 B, and depends on the fill for the other 96. It is
committed, so the behaviour is exercised by a real theme and not only by a
test.

**One deliberate deviation from the proposal.** §5 step 1 was written as "stop
treating a missing icon as a warning", and that did not happen. The direction
of travel is the opposite: a missing icon is a **warning**, aggregated into a
single finding, and the set is **filled from the baseline**. It did not go from
warn to silent. `apply` gates on errors only, so the warning is informational
and the fill is what makes a partial set safe — `tests/test_icons.py:369-372`
pins exactly that.

Everything else in this document is still the unimplemented proposal. It
records the measurements behind it and the decisions already taken, so the next
person does not have to re-derive them. Where this document and the code
disagree, the code is the current behaviour and this document is a stale idea.

The question was: themes currently store all 97 icons, and it would be nicer
if icons were stored the way `TouchColors` and `TouchOptions` are — sparsely,
holding only what differs from the baseline.

**Update after the tools were split.** The recipe engine and the TIFF writer
moved to `tdthememaker/`, the authoring tool in this repository, and `tdtheme` now
only reads, validates and installs icon sets. That changes where steps 4 and 5
below belong: both are authoring concerns and both now live in `tdthememaker`.
Steps 1–3 are still about `tdtheme`'s storage model and stay here. Recipe paths
in this document are written as `tdthememaker/recipes/<name>.recipe.json`.

## 1. The analogy holds, but only halfway

The two stores really are sparse overlays: a theme authors only the keys it
changes, everything else falls through to the captured baseline, and an absent
key means *reset to baseline* because `merge()` starts from the baseline rather
than from the install. `midnight` authors 20 of 626 `TouchColors` keys,
`sunset` 17, `mono` 460, `bnw` 419.

**Icons do not have that property.** The per-theme measurements are in the
[Icons section of the README](../README.md#icons): 18 150 B of recipes against
1 368 702 B of committed TIFFs, and every theme with a real recipe repainting
39-94 of the 97 glyphs. The conclusion the measurements force is the one that
still matters here:

**The premise of the request does not survive contact with the data.** Icon
theming is wholesale recolouring, not sparse key patching. That is not an
accident of how these four themes happen to be written; it is what theming a
monochrome glyph set *is*. `midnight` tints every neutral glyph and then
overrides 19 semantic ones by name, and the other 78 genuinely need changing.

## 2. A sparse description already exists

`tdthememaker/recipes/<name>.recipe.json` is the icon equivalent of the sparse
overlay, and it is already the authored source of truth:

```json
{"version": 1, "ops": [{"match": "*", "op": "tint", "color": [140, 172, 255]}]}
```

It is sparse in the same way the key overlays are — `match` globs rather than 97
literal names — it carries a long `_comment` explaining the design, and
`"ops": []` gives the reset identity for free, exactly as
`merge(baseline, {}) == baseline` does for the stores. `default`'s reset is
already correct and already byte-exact. An icon that matches no op is copied
from the baseline verbatim, so the recipe already has the "absent means
baseline" semantics too. Measured: a recipe whose only op is
`{"match": "CommentOff", "op": "tint", …}` reports 1 transformed and
96 pixel-identical; an empty recipe reports 0 and 97. The only thing missing is
the *other* representation.

### Two authoring footguns

**`match` is applied to the stem, not the filename** (`tdthememaker/icons.py`
strips the extension and matches the stem, with no rejection of a selector that
carries one), and **a recolour op on a black-ink glyph is a silent no-op**
(`apply_recipe` reports `transformed`/`pixel_identical` without a warning that
an op changed nothing). Both are still true, both are still unfixed, and both
are written up for the recipe author in
[`tdthememaker/README.md`](../tdthememaker/README.md#recipes) — "Two things to
know before writing one", under **Recipes**. That is where they belong now that
the recipe is the source of truth; §5 step 5 is the reminder that they are
still open.

This is the same split the key stores already have: `capture` writes the
resolved full form to `baseline/`, and the diff describes the theme as an
overlay. Both representations are deliberate. (The direction that produces a
theme from an install - `export` - now lives in `tdthememaker`, but it writes
the same overlay, so the parallel holds.)

## 3. The constraint that must survive

The constraint is about **materialisation, not storage**, and it is stated in
[reverse-engineering.md §5](reverse-engineering.md), under "Why every theme
ships all 97 files": because `UI_Icon` looks icons up by name, a theme with a
*partial* set would leave every missing glyph rendering with whatever the
previously applied theme left in the install, there is no fallback to the stock
file, and a complete overwrite is the only model in which switching themes
cannot leak state.

That is load-bearing and unaffected by where the bytes live. It says the
*install* must end up with a decision for all 97 names every time. It does not
say the *theme* has to carry all 97 files. Sparse storage and total
materialisation are compatible — every name is written, from the theme if it
has it and from the baseline otherwise — and the only difference is where the
bytes were read from. That is now implemented, as `tdicons.copy_icons` with
`fill_from=`; the pseudocode this section used to carry is that function.

Two further facts constrain any change here:

- **Icons are cached for the process lifetime.** Each is read on first use and
  memoised, so an icon swap is not visible until TouchDesigner restarts. This is
  already true today and is not affected by storage layout, but it means "did my
  change work" always requires a restart.
- **Byte diffing regenerated icons is meaningless.** A regenerated icon is
  never byte-identical to the shipped one, which is why `icons diff` compares
  pixels and why the sparse/dense question cannot be settled by looking at file
  sizes in git.

## 4. What the codec determinism costs

The current design was chosen deliberately, and the module docstring says so:
a complete copy makes a theme directory self-contained, lets an icon be lifted
between themes with a plain file copy, and keeps `apply` a dumb total overwrite
with no patch logic that could half-apply.

Generating at apply time gives up the first two. It also introduces one problem
that is easy to miss. The baseline is captured verbatim precisely so that a
drift report can distinguish "the install changed" from "we changed the
encoder":

> Re-encoding them would make a future drift report unable to distinguish "the
> install changed" from "we changed the encoder."

Generating icon bytes on every apply puts the codec back on that path. Today a
codec change shows up as a diff in the committed files, reviewed as part of a
commit. Under generation it would show up as a silent, unversioned change in
what `apply` writes — and `apply` runs against a live install. The encoder is
deterministic, so this is not a correctness problem, it is a *reviewability*
problem, and it is the strongest argument for keeping generated output
committed.

If generation is adopted anyway, the mitigation is to record a digest of the
expected output per theme and warn on mismatch, so a codec change is visible
rather than silent.

## 5. Proposal, in the order I would do it

Each step is independently useful and independently revertible. None requires
step 2, and step 1 is the one that actually saves space.

1. **Stop treating a missing icon as a warning, and stop requiring a complete
   set.** — **SHIPPED, but not in the direction proposed.** `validate_icons`
   warns when a theme lacks a baseline icon, and `tests/test_icons.py` asserts
   a *subset* rather than all 97 names, so a theme may hold a subset with the
   baseline supplying the rest. The warning was **kept**, not removed: the fill
   is what makes a partial set safe, and `apply` gates on errors only, so what
   shipped is warn-and-fill rather than silence. The commits and the worked
   example — `themes/defaultnowarn`, one icon — are in the header.
2. **Add a `icons materialise` / on-demand build path** so `apply` can populate
   the install from `baseline ∪ theme` without the theme carrying every file.
   `default` needs nothing: its absent icons all come from the baseline, which
   is already its byte-exact reset.
3. **Stop tracking `themes/*/Icons/` in git** and let step 2 rebuild on demand.
   This is the commit that removes ~2 MB from the working tree, and the point at
   which the codec-determinism trade-off in §4 becomes real rather than
   theoretical.
4. **Make `tdthememaker` import a hand-touched icon set and write a recipe** for
   it, so authoring from an existing set produces something reviewable. This is
   the largest piece of new work and should not be bundled with 1–3. Note that
   a reverse conversion cannot be exact in general: a recipe is a lossy
   description of a 97-image edit, so it would be offered as a starting point
   rather than a guarantee.
5. **Fix the two footguns in §2** — reject a `match` that carries an extension,
   and warn when a recolour op leaves a glyph unchanged. Both are in
   `tdthememaker` now, and both are cheap.

Steps 1–2 give the storage model the user asked for. Step 3 is where the space
saving lands. Steps 4–5 are what make the model maintainable over time, and
both now belong to the authoring tool rather than this one.

## 6. Decisions already taken

- **Unknown icons: keep today's behaviour.** Applying a theme writes only the
  icons it contains, so an icon that a future TouchDesigner adds, and that no
  theme has ever seen, is left exactly as shipped rather than deleted. Do not
  change this to match the key stores, which reset wholesale from the baseline
  and would silently drop a key the baseline does not know about.
  - Worth noting the two directions are opposites, and both are defensible: the
    stores protect against a theme freezing a stale value, the icon set protects
    against a build's new artwork being deleted. The icon set's choice is the
    safer one, because deleting a file is not recoverable by re-running a
    command.
  - This decision constrains §5: materialisation must iterate over the union of
    baseline and theme names, never over the theme's names alone.
- **No per-icon `Icons.yaml`.** Converting the recipe into a literal per-icon
  key/value file would match the key stores' *form* while storing strictly more
  than the recipe does, and it would need a catch-all default that the key
  overlays have no analogue for. Recorded here so it is not re-proposed.

## 7. Open questions

- Does the 2 MB of committed TIFFs actually matter? The repo is 1.0 MB of git
  history; the working tree cost is the larger number. If the answer is no,
  steps 1–2 are worth doing for the conceptual consistency and step 3 is not
  worth its trade-offs at all.
- Should `mono` and `bnw` be sparse by construction? They are derived by a
  uniform rule and change all 97 glyphs, so a recipe *is* the compact
  representation for them. That is already true and needs no work.
- Is a per-icon override ever needed that a recipe cannot express — for example
  hand-editing one glyph of `midnight`? Today the answer is "edit the TIFF", and
  that is worth keeping in mind: a sparse *storage* model makes that harder,
  not easier, unless a per-icon override list is added back.
