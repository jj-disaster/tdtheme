# tdthememaker

Generate TouchDesigner UI icon sets from recipes.

`tdtheme` installs themes other people made. This is the other half: the tool
that *makes* one. It owns the pixel side of theming — a TIFF codec, a set of
recolour and adjustment ops, and the recipe engine that turns a short list of
instructions into a complete 97-icon directory — and it can also record the
current install as a new theme.

```
tdthememaker list                      # what recipes exist
tdthememaker diff mono                 # which icons a set actually repaints
tdthememaker build midnight            # regenerate a theme's icons
tdthememaker build midnight --check    # report what would change, write nothing
tdthememaker export mytheme            # record the installed state as a theme
tdthememaker preview midnight          # contact sheet PNG
python3 tdthememaker/tests/test_thememaker.py    # generation + export
```

`tdthememaker` is a wrapper at the repository root, `tdthememaker-cli`, symlinked
onto `PATH` under the name above — the package directory already owns
`tdthememaker`, so the file is named differently and the command is not. It runs
from any directory, and unlike `cd`-ing to the repository first, it leaves the
working directory alone. Without the symlink, `python3 -m tdthememaker.cli ...`
still works from the repository root.

`export` is the one command that needs `tdtheme` to be present. Everything else
runs without it — see [Two dependencies, and why they differ](#two-dependencies-and-why-they-differ).

## The idea

TouchDesigner reads its UI icons from a fixed path inside the app bundle:

```
TouchDesigner.app/Contents/Resources/tfs/Config/Icons/*.tiff
```

97 TIFF files, no fallback, loaded once and cached for the process lifetime.
`TouchColors` and `TouchOptions` are text, so a theme can hold a sparse overlay
of them. Icons are not text, so a theme author has to produce real files. A
recipe is the reviewable form of that work:

```json
{
  "version": 1,
  "ops": [
    { "match": "*",        "op": "grayscale", "amount": 1.0 },
    { "match": "*",        "op": "tint", "color": [140, 172, 255] },
    { "match": "Error*",   "op": "tint", "color": [232, 96, 84] }
  ]
}
```

`match` is an fnmatch glob on the icon name **without its extension**. Ops apply
in order, so a later op overrides an earlier one — which is how a recipe says
"tint everything, except keep the error faces red".

The recipe is the source. The generated `Icons/` directory is the artifact, and
it is what gets committed and what gets sent to someone else.

## Ops

| op | arguments | behaviour |
|---|---|---|
| `grayscale` | `amount` | blend toward the pixel's own luminance |
| `tint` | `color`, `strength` | recolour by luminance, keeping the shading |
| `hue` | `degrees` | rotate hue |
| `saturate` | `amount` | scale chroma |
| `brightness` | `factor` | multiply |
| `contrast` | `amount`, `pivot` | push away from a pivot |
| `solid` | `color` | flatten to one colour, discarding luminance |

Ops fall into two groups, and the distinction is the whole reason a recipe can
say "a later op wins" and mean it:

- **Recolouring** (`tint`, `solid`, `hue`) are *replacements*. The last one that
  matches wins, and it is applied to the original image. Running `tint` twice is
  not "twice as much" — it reads the first result's luminance as if it were the
  artwork's own shading and multiplies by the target's luminance again. On a
  white pixel that turns the error group's `(255,92,84)` into `(171,62,56)`, 33%
  too dark.
- **Adjusting** (`grayscale`, `saturate`, `brightness`, `contrast`) *accumulate*
  in recipe order. `bnw` layers `contrast 1.5` then `1.7` on purpose.

An unknown op, a misspelled argument, a missing required argument or a
two-component colour all raise rather than being skipped. A silently ignored
line means a subtly wrong icon set.

## Recipes

`recipes/<name>.recipe.json`, one per theme. An empty `ops` list means *copy
the baseline byte for byte* — a verbatim reset, not a re-encode, which is what
makes it a lossless way back to stock.

Two things to know before writing one:

- **`match` is applied to the stem.** The extension is stripped first, so
  `{"match": "Cook.tiff"}` matches **nothing** and reports success. Spell it
  `{"match": "Cook"}`.
- **A recolour op on a black-ink glyph is a no-op.** `tint` maps a pixel to
  `colour × luma`, and black has no luminance, so tinting `Cook`, `Grid` or
  `CommentOffSmall` reports `transformed=1` and changes nothing. That is
  correct — they are dark ink on a light tile — but it is silent.

## Reproducibility

`tdthememaker/tests/test_thememaker.py` asserts that every committed theme set
is reproduced **byte for byte** from its recipe, which is what makes a recipe a
source description rather than a historical note. Five of the six do.

`defaultnowarn` does not, and cannot: its recipe is empty, so a rebuild is a
verbatim baseline copy, but its `WarnFace.tiff` is a hand-placed blanked face
whose 551 differing pixels no recipe describes. So `build` refuses to overwrite
an existing set without `--force`, and `--check` names the file that would be
lost:

```
$ python3 -m tdthememaker.cli build defaultnowarn --check
would write 97 icons from defaultnowarn.recipe.json
    ...
    1 of 97 installed icons would change
      WarnFace.tiff  (changed)
```

## Layout

```
__init__.py          the package, and the two halves it holds
icons.py             codec, ops, recipe engine, set diffing, contact sheets
theme.py             export: sparse colour overlay + verbatim icon copy
cli.py               list / build / diff / export / preview
../tdthememaker-cli  the wrapper; symlinked onto PATH as `tdthememaker`
recipes/             one <name>.recipe.json per theme
tests/               test_thememaker.py
```

## Two dependencies, and why they differ

The TIFF reader is **shared**. It lives in `../tdtiff.py` — the leaf of the
import graph, which imports nothing from this project — and both `../tdicons.py`
and `icons.py` re-export it, so `read_tiff` is one function reached under three
names rather than two implementations to keep in agreement. The *writer* stays
here, because `tdtheme` reads and copies icons but never encodes one.

The colour-store layer is **imported**. `theme.py` pulls in `tdtheme`'s
restricted YAML parser, sparse differ and field-integrity rules, because this
tool is the side that *writes* that format. A second implementation of the
writer would mean themes this tool produces that `tdtheme apply` cannot read —
the one duplication that could silently produce a theme nobody can install.
One implementation of a format, in the tool that owns it.

## Export

`export` records the live install as a theme, in the form `tdtheme apply` reads:

```
python3 -m tdthememaker.cli export mytheme
```

The two halves are recorded differently, on purpose.

**Colours** become a *sparse* overlay: only the keys that differ from the
baseline, in restricted YAML. That is what makes the result reviewable in a diff
and editable by hand. Both store files are always written, even when one has no
differences, so the directory is self-describing — a reader can tell "this theme
does not touch `TouchOptions`" from "this theme is missing `TouchOptions`".

**Icons** are copied byte for byte. Re-encoding them would make it impossible to
tell later whether the install changed or this tool's codec did, and a TIFF is
not reviewable anyway.

The consequence is that an exported theme is reproducible only as bytes. An
icon placed by hand — or by anything other than this tool — leaves no trace in
a recipe, so rebuilding from one would quietly revert it. That is the
`defaultnowarn` case above, and `build --check` is how you find out which kind
of theme you have.

## The two alpha traps

Both silent: the file is valid, it decodes, and it renders wrong.

**Reading.** 23 of the 95 icons declaring premultiplied alpha contain straight
samples. It is decidable rather than a matter of taste — in genuine
premultiplied data no channel can exceed alpha, so one pixel with
`max(RGB) > alpha` refutes the tag. Un-premultiplying on the strength of the tag
alone turns `(102,102,102,a=91)` into `(285,285,255)`, which clamps to white,
and a 16×16 glyph collapses into a blocky silhouette. The reader decides from
the samples and treats the tag as metadata about intent.

**Writing.** Output is premultiplied. TouchDesigner's compositor evaluates
`src + bg·(1−a)`, so straight-alpha data draws every antialiased edge pixel at
full strength: the gaps between strokes close and small glyphs go blocky.
Measured on a 24×24 glyph, the icon rendered **2.83× too heavy**, and whole
regenerated sets came out 1.3–1.8× heavier than the shipped ones. The obvious
reasoning — that TIFF leaves the convention undefined, so declare it explicitly
and satisfy every generic decoder — is true of libtiff, Photoshop and image
viewers, and irrelevant here.

Fidelity is therefore judged in premultiplied space, where an 8-bit file cannot
carry the colour of a nearly-transparent pixel: `(100,100,100,a=2)`
premultiplies to `(1,1,1,2)`, which un-premultiplies to `(128,128,128,2)`. So
`sips` interop requires exact agreement at full opacity and a tolerance below
it, plus a counterfactual that forges a mislabelled file to prove the check can
fail. Premultiplied 8-bit is inherently lossy, and the test asserts the real
invariant rather than an untrue one.
