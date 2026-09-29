# tdtheme

A theme manager for TouchDesigner on macOS. It edits the undocumented config
files and icon directory that control the whole UI - every colour, every size,
every glyph - so you can build and switch themes instead of hand-editing them.

```
./tdtheme status
./tdtheme list
./tdtheme apply midnight
./tdtheme reset              # back to the stock look
```

## Installation

Requires macOS and TouchDesigner. Nothing else — no pip, no virtualenv, no
build step. It is a few Python files that read and write four files inside your
TouchDesigner install.

```sh
git clone https://github.com/jj-disaster/tdtheme.git
cd tdtheme
./tdtheme status
```

That last line is the real test. If it prints your TouchDesigner build and a
list of themes, you are installed and can stop reading.

### Put it on your PATH (optional)

The wrapper resolves its own location, so `cd`-ing into the checkout always
works. To drop the `./` and run it from anywhere, use the setup script — it
picks a directory it can actually write to and tells you what it chose:

```sh
./setup
```

It links `tdtheme`, `tdthememaker` and `check-td-writes` together, because the
second two are the companions you want in the same place anyway and both hit
the same `permission denied` problems. Other flags:

```sh
./setup --check              # report what it would do, change nothing
./setup --uninstall          # remove the links it made
./setup "$HOME/.local/bin"   # put them somewhere specific
SETUP_DEBUG=1 ./setup        # trace every probe it makes
```

If it picks a directory that is not on your `PATH`, it prints the `export` line
to add. That is not a failure — the links are correct, they just need one line
in your shell rc before the bare name resolves.

**A symlink, not a copy.** `tdtheme` has no dependencies to resolve, and it reads
`themes/` and `baseline/` from the repository it lives in. A symlink keeps one
copy of your theme data and makes the command always reflect the current
checkout; an installed copy would be a second, silently-drifting copy of the
same themes, and `apply` would install from whichever one it found first.

Then it works from anywhere:

```sh
cd ~
tdtheme apply midnight
```

`tdthememaker` needs a different installed filename than its repo directory
(`tdthememaker-cli`), because the package directory already owns the name
`tdthememaker`. `setup` handles that rename for you.

### If `setup` reports `permission denied` for a link you did not create

It refuses rather than guessing, and tells you which of the two causes it found.
Both produce the identical `zsh: permission denied` from a link that looks
perfectly well formed, which is why the script checks for both:

- **The file behind the link is not executable.** Git records the exec bit so a
  clone keeps it, but exFAT, cloud sync and zip all drop it. `setup` restores it
  and says so.
- **Something is a directory where the link goes.** `ln -s` does not fail when
  that happens; it nests the link *inside* the directory, and `PATH` then finds
  a directory where a command should be. Executing a directory is `EACCES` — the
  same error, a completely different cause. `setup` refuses, and if the
  directory is empty it gives you the `rm -rf` to fix it. If it is **not**
  empty it will not touch it.

### If it says `no python3 found`

You do not need to install Python. TouchDesigner already ships a full CPython
3.11, and the wrapper falls back to it automatically — it only complains if
*neither* a working `python3` on your `PATH` *nor* TouchDesigner can be found.
`setup` checks this before it creates any links, so it will not leave you with
three commands that cannot run.

The case that trips people up is a Mac without the Command Line Tools, where
`/usr/bin/python3` exists but is only a 118 KB stub that opens a GUI installer
instead of running. The fallback covers the case where no `python3` is on your
`PATH` at all; a stub that is *present but unrunnable* still fails, because
`PATH` lookup stops at the first match. If you hit that, either install the real
thing or move TouchDesigner so the script can find it:

```sh
xcode-select --install
```

### If it cannot write to TouchDesigner

`apply` needs write access to
`/Applications/TouchDesigner.app/Contents/Resources/tfs/Config`. If the files are
owned by root, you will get a permission error.

```sh
sudo chown -R "$USER" /Applications/TouchDesigner.app/Contents/Resources/tfs/Config
```

Note that the app bundle's code signature was already invalid before this tool
existed (a sealed resource is missing from `Python.framework`), so editing files
inside the bundle does not make that worse. See
[Things that will bite you](#things-that-will-bite-you).

### If your TouchDesigner is newer than the baseline

Run this once, before your first `apply`:

```sh
tdtheme capture --local
```

The committed baseline was captured from one specific build (2025.33230). A newer
build may have added keys, and `apply` **refuses to write** rather than delete
them — see [If your TouchDesigner is newer than the
baseline](#if-your-touchdesigner-is-newer-than-the-baseline) for what that
message means. `capture --local` writes a gitignored, per-machine copy that every
later command prefers, so your install is diffed against the build you actually
run. It is safe to delete the directory to go back.

## Using it

The whole workflow is four commands. The rest of this README is detail on any
of them.

### 1. Check it can see your install

```sh
tdtheme status
```

```
TouchDesigner     2025.33230
config dir        /Applications/TouchDesigner.app/Contents/Resources/tfs/Config
baseline          captured
                  baseline.local/ - private copy, shadowing the committed baseline/
                  build 2025.33230 matches
themes            bnw, default, defaultnowarn, midnight, mono, pink, sunset
                  last applied: pink
```

Three things worth reading here. The **build** tells you which TouchDesigner this
is, and whether the baseline was captured from the same one. **Drift** — the
per-file lines further down — tells you whether the files on disk still match
what `tdtheme` last wrote, which is how you find out that something else changed
them. And `last applied` is the theme that is currently installed.

If your build is not the one the committed baseline came from, do step 1b.

### 1b. On a different build, capture your own baseline

```sh
tdtheme capture --local
```

Skip this on a matching build. It writes a private `baseline.local/` that every
later command prefers over the committed `baseline/`, so your install is diffed
against the build you actually run. See
[If your TouchDesigner is newer than the baseline](#if-your-touchdesigner-is-newer-than-the-baseline).

### 2. See what is on offer

```sh
tdtheme list
```

```
    bnw                  TouchColors 419, TouchOptions 0 icons 97 (all differ in bytes)
    default              TouchColors 0, TouchOptions 0 icons 97 (all differ in bytes)
    defaultnowarn        TouchColors 0, TouchOptions 0 icons 1 (96 absent, 1 changed)
    midnight             TouchColors 20, TouchOptions 0 icons 97 (all differ in bytes)
    mono                 TouchColors 460, TouchOptions 0 icons 97 (all differ in bytes)
  * pink                 TouchColors 4, TouchOptions 0 icons 97 (stock bytes)
    sunset               TouchColors 17, TouchOptions 1 icons 97 (all differ in bytes)

* = last applied by tdtheme (pink).
```

`mono` changes 460 keys, `midnight` changes 20. The number is the size of the
change, so it is the first thing to look at when picking one. The `icons` column
counts bytes rather than pixels, so that `list` stays fast enough to read at a
glance — `tdtheme icons diff` is the pixel-accurate version.

To read the individual changes before applying anything:

```sh
tdtheme diff midnight
```

That lists every key with its old and new value. Nothing is written.

### 3. Apply one

```sh
tdtheme apply midnight
```

Then **restart TouchDesigner**. It reads the two stores at startup, and each
icon is cached the first time it is drawn — so an icon change is not even looked
at until a restart, let alone a new process.

```
Applied theme 'midnight'
    TouchColors: 20 changed
    TouchOptions: unchanged
    icons: 97 written, 0 already matched the baseline
    ui.tox: written from default (this theme has no ui.tox)
    TouchDesigner is closed; changes are live on next launch.
```

Both stores always print, because `unchanged` is a real answer and silence
would read as a bug. These are counts, not a change report — the largest
shipped theme changes 460 keys, and a line naming them is a line nobody reads.
For the key-by-key listing, `tdtheme diff` is the tool.

Note the last line: if TouchDesigner is open when you apply, the change lands on
disk but you will not see it until a restart, and the message tells you so
rather than letting you think it failed.

### 4. Go back

```sh
tdtheme reset
```

An alias for `apply default`. There is no undo stack: the install is a pure
function of the baseline and the theme, both in git, so re-applying the previous
theme restores the exact bytes. See
[Undo is re-applying, not restoring](#undo-is-re-applying-not-restoring).

### Keeping it up to date

```sh
tdtheme update
```

A `git pull` of this checkout, and it **refuses** in two cases rather than
guessing:

- **Uncommitted changes.** A theme you are editing on disk is invisible to git,
  so a pull that overwrites it loses work no `reflog` can bring back. It lists
  what is modified and stops. `git stash` sets it aside.
- **A diverged branch.** If your commits and upstream's have both moved on there
  is no fast-forward, and this will not invent a merge commit for you. It prints
  the `git log --left-right` you want and stops.

It only ever fast-forwards, so it never creates a commit you did not ask for.

An update does not change what is installed. A new upstream theme is a new
theme: it is not on your machine until you `apply` it.

### Removing it

```sh
tdtheme uninstall
```

Restores the stock UI **first**, then removes the three commands from `PATH`. The
order is the point: once the links are gone there is no way to undo a theme but
by hand-editing four undocumented files.

**The checkout is not deleted.** It holds your themes, and a command whose name
reads like "remove this program" should not be the thing that deletes them. It
prints the directory and the `rm -rf` for it, and leaves that decision to you —
including whether to keep `baseline.local/`.

`--keep-files` removes the commands but leaves TouchDesigner themed as it is now.

### Looking at the icons

The icon set is the part you cannot judge from a number, so it gets its own
commands:

```sh
tdtheme icons preview midnight   # a PNG contact sheet of all 97 glyphs
tdtheme icons diff midnight      # which icons this theme actually repaints
tdtheme icons list               # size and digest per file
```

`preview` writes `testiconsforagents/<theme>-icons.png` — a gitignored scratch
directory, so it will not dirty your checkout. It is the fastest way to see what
a theme does, and worth running before any `apply`. Open the PNG in Preview.

### Making your own

Authoring is a separate command, `tdthememaker`, because it is a different job —
it generates things, `tdtheme` only merges and installs them.

```sh
tdthememaker list                   # the available recipes
tdthememaker build midnight         # generate that icon set from its recipe
tdthememaker preview midnight       # render it to a PNG
tdtheme apply midnight              # install the finished theme
```

`build` takes the *name* of a recipe, and writes into that theme's `Icons/`
directory. Two flags matter: `--check` reports what it would do without failing
on an existing set, and `--force` overwrites one. Note that `build midnight`
overwrites the *shipped* `midnight` recipe's output — which is fine to try and
worth reverting with `git checkout themes/` if you want the committed set back.

`tdthememaker export` does the reverse: it records your current install as a
new theme, which is the easy way to start one from your own colours. See
[Writing themes](#writing-themes).

## What it actually edits

Two files, one directory and a third file, inside the app bundle:

| Path | Shape | Controls |
|---|---|---|
| `TouchColors` | `key <TAB> r <TAB> g <TAB> b` | every UI colour, incl. all network-editor keys |
| `TouchOptions` | `key <TAB> value` | numeric UI options - sizing, spacing, alpha |
| `Icons/*.tiff` | classic TIFF, 8-bit RGB(A) | the 97 UI glyphs: flags, badges, overlays |
| `System/ui.tox` | TouchDesigner's own `.tox` | the UI itself: dialog and window geometry, layout |

The two stores are read at startup. The icons are not - they are read **lazily
on first use and then cached for the life of the process**, so an icon change
needs a restart even to be looked at. See [Icons](#icons). `ui.tox` is a
different case again: it cannot be edited at all, only installed whole. See
[ui.tox](#uitox).

Format details that matter, all verified rather than assumed:

- `TouchColors` has 626 keys. Two of them (`dialog.commenthint`,
  `dialog.commenthint.comp`) carry an **extra empty second field**, so the
  colour is the *last three* fields, not fields 2-4.
- `TouchOptions` has 183 keys. Some values are legitimately empty
  (`font.default.face`, `font.mono.face`).
- The two files share **zero** keys - disjoint namespaces.
- The 97 icons are 83 neutral-ink glyphs, 3 of them pure black, and 14
  genuinely coloured. 89 are LZW-compressed, 8 are not, and 5 are split across
  multiple strips. See [Icons](#icons) below.
- The stores round-trip byte-for-byte through this tool. That invariant is
  enforced by `tests/test_roundtrip.py`, which is the acceptance gate for
  every other change.

## Commands

| Command | What it does |
|---|---|
| `capture` | snapshot the installed files and icons as the baseline (refuses to clobber; `--local` for a private one, or `--force --i-know-this-is-shared` to replace the committed copy) |
| `list` | list themes; `*` marks the last one applied |
| `status` | TouchDesigner build, baseline, whether TD is running, per-file drift |
| `diff NAME` | show exactly what a theme changes, old value vs new |
| `apply NAME` | merge, validate, write (`--no-icons` to skip the icon set; `--backup` to keep a copy of the outgoing files; `--allow-unknown` to write despite keys this baseline has never seen; the `ui.tox` is always written) |
| `reset` | back to stock: an alias for `apply default`, same flags |
| `update` | `git pull` this checkout from its remote; refuses on uncommitted changes or a diverged branch |
| `uninstall` | restore the stock UI and remove the commands from `PATH` (the checkout is kept) |
| `icons list [NAME]` | the icon set, with size and digest per file (NAME omitted = baseline) |
| `icons diff NAME` | which icons a theme repaints, by pixel (`--bytes` to skip decoding) |
| `icons preview [NAME]` | write a PNG contact sheet so the icons can actually be looked at |

## How themes are stored

A theme's **two stores** are a **sparse overlay**: only the keys it actually
changes, as restricted YAML. Everything else falls through to the captured
baseline. Its **icon set is not** - see below.

```yaml
worksheet.bg: ["0.05", "0.055", "0.075"]
tile.connection.hilite1: ["1", "0.85", "0.35"]
```

Sparse rather than full-file copies because a TouchDesigner update can add
new keys. A full copy would silently drop them; an overlay inherits them
from the baseline.

### Undo is re-applying, not restoring

`apply` does **not** keep a copy of what it replaces unless you pass
`--backup`. That is a change of default, and the reasoning is worth stating
because the safety net it removes looks load-bearing until you check where the
files come from.

The install is reconstructible from what is already in this repository. The two
stores only ever hold `merge(baseline/, themes/<name>/)`, and the icon set only
ever holds a theme's own icons completed from `baseline/Icons/` — so for any
theme that is committed, the bytes on disk are a pure function of two files git
already tracks. `tdtheme apply <the previous theme>` puts them back exactly, and
`tdtheme status` tells you which theme that is. This is not a claim in a
comment; `tests/test_icons.py` rebuilds a themed install from `baseline/` plus
`themes/` and compares it byte for byte.

What a backup actually adds is cover for the one input git does not have: a
theme you edited on disk and have not committed. That is worth having when you
are in the middle of an edit, and not worth 36 KB to 464 KB on every apply for
the rest of the time — the set varies with how much of the install the outgoing
theme had changed, and nothing ever pruned `backups/`.

So:

- **`tdtheme apply midnight`** — no copy kept, nothing to clean up. Undo by
  re-applying, or `tdtheme reset` for stock.
- **`tdtheme apply midnight --backup`** — the outgoing stores and icons are
  copied to `backups/<timestamp>/` first. The `ui.tox` is still not copied; see
  [ui.tox](#uitox) for why that one is excluded even from this.
- **`backups/` is gitignored.** It is a local scratch space, never committed,
  and it sits in the checkout rather than anywhere off the disk — so it protects
  against a wrong `apply`, not against losing the machine.

### If your TouchDesigner is newer than the baseline

The baseline in this repository was captured from one build. A newer build may
have added keys to `TouchColors` or `TouchOptions`, and `apply` writes
`merge(baseline, theme)` — so a key that is in neither input is **not written
back**. It would be deleted, silently, as a side effect of changing the
colours.

So `apply` stops instead, and writes nothing:

```
Not applied: the install holds 3 key(s) this baseline has never seen, in
TouchColors (3); applying would drop them and nothing can put them back. Run
`tdtheme capture --local` to re-baseline your own build, or pass
--allow-unknown to drop them deliberately.
```

`tdtheme capture --local` re-baselines into `baseline.local/` — a gitignored,
per-machine copy that every later command prefers over the committed `baseline/`.
After that those keys are ordinary baseline keys, `tdtheme reset` handles them
correctly, and the check stops firing. Delete the directory to go back to the
shipped baseline.

Because the refusal writes nothing, **it is safe to ignore and come back to**:
whatever the apply was going to do, you can still do it after re-baselining.
`--allow-unknown` is the other way past it, for the case where the extra keys
really are junk you want gone rather than kept.

Only keys the *last applied theme* did not write are treated this way. A key one
of your own themes added is not blocked — it is in the install because this tool
put it there, so dropping it when you switch themes is the intended behaviour
and not loss. `tests/test_tdtheme.py` pins both halves, including that a theme
which adds a key is still dropped by the next one.

### The accepted value syntax

`tdtheme` is standard-library-only, so it reads overlays with its own small
parser rather than a YAML library. It is deliberately **not** general YAML, and
it is strict on purpose: a theme must install the same bytes whichever
interpreter runs the tool, so anything ambiguous is refused rather than guessed.

| form | example | |
|---|---|---|
| double-quoted string | `key: "text"` | ✅ |
| bare scalar | `key: 11` | ✅ |
| list, quoted items | `key: ["a", "b"]` | ✅ the usual way to write a colour |
| comments and blank lines | `# note` | ✅ |
| single-quoted | `key: 'text'` | ❌ refused |
| list, bare items | `key: [a, b]` | ❌ refused |
| flow mapping | `key: {a: 1}` | ❌ refused |
| anchor / alias | `a: &x 1`, `b: *x` | ❌ refused |
| block scalar | `key: \|` | ❌ refused |

Two rows earn their keep.

**Single quotes** are the one people reach for, and they are refused on purpose.
A YAML-aware reader strips them; this parser cannot, so accepting them would
write `'text'` — quotes included — into the store, and a quoted `.size` value
becomes a geometry TouchDesigner cannot parse. Write `"text"` or bare `text`.

**Bare list items** (`[a, b]`) look equivalent to `["a", "b"]` and are not:
they are valid YAML but not valid JSON, and this parser only reads lists whose
items are quoted. Quote the items.

Everything outside the table is refused with a message naming the construct,
rather than partially understood. That is the design: a refused overlay is a
five-second fix, whereas a misread one installs a theme that looks applied and
is not. A leading `-` is fine (`key: -0.5`), since a bare `-` is not a
construct without a space after it.

To build one by hand, copy `themes/midnight/` and edit the YAML. To capture
what you have currently set in TouchDesigner as a new theme, use
`tdthememaker/` - see [Writing themes](#writing-themes).

## Icons

The 97 glyphs in `Config/Icons/` are themed too, and they work differently from
the two stores.

**They are not a sparse overlay.** Every theme but `defaultnowarn` ships a
*complete* icon set, and that is deliberate. (`defaultnowarn` ships one icon,
1,354 B, and leans on the fill described below.) A theme with no `Icons/`
directory would write nothing, so applying `sunset` and then `default` would
leave sunset's icons in the install while claiming to be stock. A total
overwrite is the only model where switching
themes cannot leak state. The cost is disk - 1.31 MB across the seven shipped
sets, against a few KB for a delta scheme - and that was an accepted trade.

That 1.31 MB is 1 368 702 B, of which 782 520 B is `default` alone, because it
copies the baseline verbatim. The six regenerated sets are 101-128 KB each
rather than that 782 KB stock set, since re-encoding drops ~5 KB of Photoshop
metadata per icon and re-applies LZW - so the bulk of the total is the one
theme that changes nothing. The recipes those sets were generated from are
18 150 B in all.

A theme that is *not* complete is still safe. Anything it does not ship is
filled in from the baseline at apply time, so a partial set - an interrupted
copy, or a folder assembled by hand - gives you that theme's glyphs on top of
stock ones rather than the *previous* theme's glyphs. `apply` says how many it
filled in, and `validate` reports the shortfall as a single warning rather than
one line per missing icon. Filling in only ever adds names: a name the theme
ships always wins, including a name this build's baseline does not have.

**Each set is generated from a recipe**, which lives in `tdthememaker/`, so the
binary blobs in git are reproducible artefacts rather than the source of truth:

```json
{
  "ops": [
    { "match": "*", "op": "tint", "color": [140, 172, 255] },
    { "match": "Error*", "op": "tint", "color": [255, 92, 84] },
    { "match": "Warn*", "op": "tint", "color": [255, 196, 74] }
  ]
}
```

Ops run in order and a later op wins, so the semantic groups are painted back
over the general tint. The available ops are `grayscale`, `tint`, `hue`,
`saturate`, `brightness`, `contrast` and `solid`; `tint` keeps each pixel's own
luminance and takes only its hue, which is what makes it the right tool for
glyphs - the anti-aliasing is luminance structure, and `solid` would flatten it
away. An unknown op, a misspelled argument, or an out-of-range colour is a hard
error, because a silently skipped line means a subtly wrong icon set.

`default` uses an **empty** `ops` list, which means *copy the baseline files
byte for byte* rather than decode and re-encode. That is what makes a reset
lossless.

| theme | recipe | `Icons/` | recipe ÷ set | icons actually repainted |
|---|---:|---:|---:|---:|
| `default` | 662 B | 782 520 B | 0.1% | 0 / 97 |
| `defaultnowarn` | 1 611 B | 1 354 B | 119% | 1 / 1 shipped |
| `midnight` | 2 576 B | 127 946 B | 2.0% | 94 / 97 |
| `sunset` | 1 680 B | 127 566 B | 1.3% | 94 / 97 |
| `mono` | 1 563 B | 106 152 B | 1.5% | 39 / 97 |
| `bnw` | 2 162 B | 101 363 B | 2.1% | 46 / 97 |
| `pink` | 7 896 B | 121 801 B | 6.5% | 94 / 97 |
| **all seven** | **18 150 B** | **1 368 702 B** | **1.3%** | |

The last column is `tdtheme icons diff`, which decodes and compares pixels; the
byte columns are `du`. Every theme ships all 97 files except `defaultnowarn`,
which ships one, and an earlier version of
[the icon-storage design](docs/icon-storage-design.md) reported 97/97 for the
middle four rows by counting icons *shipped* instead of icons *changed*.

`defaultnowarn` is the one row whose recipe is bigger than the set it produces:
it is a single hand-placed icon with an empty `ops` list, so the recipe is
documentation. It is also the clearest argument that a sparse *storage* model
was worth building.

```
./tdtheme icons diff mono           # by pixel - which icons are actually repainted
./tdtheme icons preview             # PNG contact sheet of the baseline
```

This tool does not create icon sets. It installs them, checks them and tells
you what they change; the recipe that produced one lives in `tdthememaker/recipes/`,
along with the writer that generated it. `tdtheme icons build` used to exist
here and was removed, because a tool for *importing* other people's themes
should not be the tool that authors them.

### What the recipes can and cannot reach

Worth knowing before writing one, because it is a property of the shipped files
rather than of any theme:

- **83 of the 97 have neutral ink** - white or black, with alpha doing all the
  work. A hue op moves them completely; a desaturating op moves none of them.
- **3 of those are pure black with alpha** - `Cook`, `Grid`, `CommentOffSmall`.
  There is no hue in a black pixel, so `tint` leaves them alone. That is the
  right outcome: they are dark ink drawn on a light tile, and recolouring them
  would break the field they sit on. They are counted separately because they
  are the one case where a recolour op silently does nothing.
- **14 are genuinely coloured** - the error and warning faces, the `Origin`
  axes, the script/python markers. Only these move under `grayscale`, so `mono`
  repaints 39 of 97 and `bnw` 46 of 97 while looking like a complete theme.
  Both counts are higher than the 18/25 an earlier version of this file gave:
  the 23 mislabeled icons have soft grey edges that only became visible once
  they were read as straight alpha, and those edges are what a desaturating or
  contrast op can act on.

The 83/14 split is measured from each icon's alpha-weighted mean ink colour and
holds for any chromaticity threshold from 6 to 20, with a gap from 4.5 to 23.0
between the 15th and 14th values. An earlier version of this file claimed 69
white / 3 black / 25 coloured; that count was an artefact of the
mislabeled-alpha bug described under [The alpha trap](#the-alpha-trap) below,
which drove the soft edges of 23 icons to solid white.

### Bytes are not pixels

A regenerated icon is *always* a different file from the shipped one: it is
single-strip, re-compressed, and has ~5 KB of Photoshop metadata stripped out. So a byte comparison reports all 97 files as changed even when the
recipe did nothing to them.

- `tdtheme icons diff` decodes and compares **pixels**, which is the honest
  number. `--bytes` skips the decode.
- `tdtheme list` and `tdtheme status` stay byte-level: decoding all seven theme
  sets costs 1.8s, and those two are meant to be glanced at. They say "differ
  in bytes" for that reason.

### The alpha trap

Two traps, both silent, both of which this tool fell into first. The full
derivation, with the measurements behind it, is in
[docs/reverse-engineering.md §5](docs/reverse-engineering.md); this is the
summary.

**1. Do not trust the `ExtraSamples` tag.** 95 of the 97 shipped icons declare
premultiplied alpha, and for 23 of the 97 the tag and the samples disagree. The
tag is metadata about intent; the samples are the image.
That is decidable rather than a matter of taste: in genuine premultiplied data
no channel can exceed alpha, so a single pixel with `max(RGB) > alpha` refutes
the tag, and the 68 genuine cases never violate it. So `read_tiff` decides from
the samples and treats the tag as metadata about intent.

**2. Write premultiplied, because that is what TouchDesigner composites.**
This tool un-premultiplies on read so the transforms can reason in straight
alpha, and multiplies back on the way out. The temptation is to declare the
result `ExtraSamples = 2` (unassociated) since generic decoders would then
assume straight - irrelevant here, because the only decoder that matters
composites premultiplied. Writing straight data makes every antialiased edge
pixel draw at full strength; measured on a 24x24 glyph, the icon came out
**2.83x too heavy**.

Getting either wrong is silent. The file is a perfectly valid TIFF, it decodes
without complaint, and it renders wrong. `tests/test_icons.py` pins it down by
converting generated files with `sips` - an independent decoder - requiring a
**max channel difference of 0** at full opacity, and adding a counterfactual
that forges a mislabeled file to prove the check can fail.

## ui.tox

Everything about the UI that is not a colour, a number or a glyph lives in one
more file: `Config/System/ui.tox`. Dialog and window sizes, which panes are
open, column widths - the layout itself.

It is also a `.tox`, which is TouchDesigner's own binary project format, and
that is the whole difficulty. **A `.tox` can only be written by TouchDesigner.**
No editor in this repository can change a value inside one, and neither can
this tool. The way to get a different UI is to arrange it by hand in the app
and save, which is a person doing it, not a script.

What a script *can* do is the part that was missing: the file is still a file.
So a theme ships a `ui.tox`, and `apply` copies it over the top of the one in
the bundle. There is no format to parse, no merge, no diff and no validation,
because there is nothing to validate - the bytes either arrive or they do not,
and `write_file` already settles that. `apply` reports which file it wrote.

```
themes/<name>/ui.tox        ->  Config/System/ui.tox
```

To make one, rearrange the UI in TouchDesigner, save, and copy
`Config/System/ui.tox` into a theme directory. `themes/default/ui.tox` is the
stock file, taken from a fresh install.

**A theme with no `ui.tox` of its own gets `default`'s**, which is the detail
that makes this safe rather than merely convenient. The alternative - writing
nothing - means applying a theme that has no opinion about the UI leaves the
*previous* theme's dialogs on screen while `tdtheme list` reports the new one,
and nothing about that looks wrong. Falling back means a theme switch is a
total overwrite, the same property the icon sets have and for the same reason.

Three consequences worth knowing:

  - **It is not backed up, even with `--backup`.** `apply --backup` copies the
    outgoing stores and icon set into `backups/`, and deliberately leaves this
    file out. It is 1.1 MB, and the only thing that ever writes it is `apply`
    itself, so a copy taken at apply time is the previous theme's file - bytes
    that are already in git, in that theme's own folder - and at one per apply
    it would grow `backups/` by hundreds of megabytes. A store pair is 32 KB
    against that, so the flag is cheap for them and ruinous for this. The stock
    file is recoverable from
    `themes/default/ui.tox` and from `baseline/System/ui.tox`. **A hand-arranged
    UI is not**, so keep it in a theme folder, which is where it came from.
- **`--no-icons` does not skip it.** That flag is about the icon set. A theme
  switch that skipped the UI would leave the previous theme's geometry in place
  while reporting the new theme, which is the exact failure the fallback above
  exists to prevent.
- **`capture` keeps a copy**, verbatim, at `baseline/System/ui.tox`, mirroring
  the install's layout. Nothing reads it - the fallback is the theme, not the
  baseline - but it means `capture` does not quietly lose the file, and a
  hand-edited `ui.tox` in the install is distinguishable from the shipped one
  by hash.

`tests/test_tdtheme.py` covers the resolution rules and the no-leak property.
It asserts nothing about the contents, because nothing can be asserted about
1.1 MB of opaque bytes.

## Validation

`apply` refuses to write on a validation error, and warns on anything
suspicious.

- **Tile geometry at zero is a hard error.** `tile.inout.origsize 0`
  collapses both the connector and the top border, with no error from
  TouchDesigner itself - it just quietly renders wrong.
- **Other `.size` keys are judged only against the baseline.** The shipped
  `font.relative.size` is legitimately `0` (it is a delta, not an absolute
  size), so an unconditional `> 0` rule would false-positive on the
  untouched shipped file and make every apply fail.
- **A malformed colour value is a hard error.** A `TouchColors` value must
  keep the field count the baseline ships for that key, hold no whitespace
  inside a field, and be numeric. This catches the real corruption of typing
  a space where a tab belongs - `worksheet.grid 0.317 0.189 <TAB> 0.15` merges
  two channels into one field, and `parse` and `serialize` both accept it
  silently, so without this rule the bad value survives being written out and
  `apply` writes it into the install. The rules are relative to the baseline, so the
  two shipped `dialog.commenthint*` keys keep their 4-field shape and all 626
  pristine keys validate clean.
- Colour channels are **never clamped**. The shipped `POP.hilite` contains
  `1.2`; values above 1.0 are intentional.
- Unknown keys warn - a typo, or a key this build's TouchDesigner added.
- Setting both `X` and `default.X` warns that the tier precedence is
  unverified.
- **Icons are validated before anything is written into the bundle.** An icon
  that will not decode is a hard error, because TouchDesigner logs
  `Couldn't find icon` and draws nothing for it. An icon whose dimensions differ
  from the baseline is a hard error too, since TouchDesigner sizes most of these
  glyphs from the file. An icon missing from a theme is only a warning - the
  glyph still renders, it just keeps its shipped bytes - but it almost always
  means the theme was built from a stale baseline.

## Things that will bite you

- **Restart TouchDesigner to see a change.** It reads the stores and the
  `ui.tox` at startup, so a running instance keeps showing the old appearance
 until you restart. That is a
  display lag, not data loss — the files themselves are safe to edit while
  TouchDesigner is running. `apply` says so when it detects a running process.
  Icons are additionally cached lazily on first use, so they are not re-read
  even for a window that opens later in the session.
- **A TouchDesigner update wipes all of it.** `status` compares the live build
  against the one recorded in the baseline and warns on a mismatch. A new build
  may also add store keys and ship new or resized icons, which
  `validate_icons` reports per theme, and it will almost certainly ship a new
  `ui.tox` - after an update, re-copy the stock file into `themes/default/` or
  every theme inherits the new build's layout. **Run
  `tdtheme capture --local` first, before any `apply`** — a build that added
  store keys makes `apply` refuse until you have, and it will not refuse if you
  have already applied and lost them. See
  [If your TouchDesigner is newer than the baseline](#if-your-touchdesigner-is-newer-than-the-baseline).
- **The app bundle's code signature was already invalid** before this tool
  existed (a sealed resource is missing in `Python.framework`). Editing
  files inside the bundle does not make that worse, but it is why macOS
  Gatekeeper may complain about the app.

### Confirmed: TouchDesigner only reads these files

**Measured, 2026-09-25, build 2025.33230.** Checksums were taken before
launching TouchDesigner and again after quitting it:

```
file           before                                                          after
TouchColors    10da545fa18aa8483e0fe1ce896218a2                               10da545fa18aa8483e0fe1ce896218a2
TouchOptions   fc86c3e86a5aaa09e17c8573faea3b59                               fc86c3e86a5aaa09e17c8573faea3b59
```

Unchanged, with file mtimes still pointing at the last `tdtheme apply`.
TouchDesigner ran a full session and wrote neither file. The `Icons` directory
was added to `check-td-writes` when icon theming landed and is covered by the
same test.

This corrects an earlier version of this README, which claimed TouchDesigner
rewrites `TouchOptions` on exit and that changes made while it was running
would be lost. **That claim was wrong.** It came from observing a file change
after a TouchDesigner session and attributing it to TouchDesigner, without
controlling for edits made in the same session — the user was editing these
files directly at the time, and the change was theirs. The tool had been
warning about data loss that could not happen.

The correct model, now measured rather than assumed:

- TouchDesigner **reads** the two stores at startup and the icons lazily on
  first use.
- TouchDesigner **never writes** any of them, so editing while it runs is safe.
- A **restart is needed only for a change to become visible** — that is a
  display refresh, not a durability concern. For icons it is needed even to
  *look*, because the process-cached bitmap wins.

The practical consequence: there is no reason to quit TouchDesigner before
applying a theme. The measurement is per-build, so re-run
`./check-td-writes` after any future TouchDesigner update:

```sh
./check-td-writes record   # before launching TouchDesigner
# ... launch TD, use it, quit it ...
./check-td-writes check    # reports whether any of the three changed
```

It tracks all three artefacts; the `Icons` directory is hashed as one blob
rather than listed file by file, since the only question is whether the set
changed. `tdtheme` assumes TouchDesigner never writes these files and does not
warn about data loss. The tool itself re-checks the baseline build number on
every `status`.

**`ui.tox` is not among the three, and that omission is deliberate rather than
an oversight.** TouchDesigner **never writes** this file: it reads it at startup,
and the only way a new `ui.tox` comes into existence is a manual export, which
lands wherever the user saved it rather than over the one in the install folder.
So `apply` does not need re-running after a session — nothing in the install
can have moved underneath it — and a theme's `ui.tox` is the layout you get at
launch, full stop. It is absent here because there is nothing to check, not
because the answer is unknown: the other three were measured to be read-only
and this one is read-only by construction.

## Known limitations

- **Icon storage is wasteful by design.** Seven shipped sets come to 1.31 MB
  where a delta scheme would be a few KB — a storage trade, not a correctness
  one, and the full accounting is under [Icons](#icons).
- **The TIFF codec handles only what TouchDesigner ships**: little-endian
  classic TIFF, single page, 8 bits per sample, photometric RGB, LZW or
  uncompressed, 3 or 4 samples. Big-endian, planar, 16-bit and palette images
  all raise rather than guess - a silently mis-decoded icon is worse than a loud
  failure.
- **Only the premultiplied-alpha case is undone.** If a future build ships
  associated alpha with a value of 0 the round-trip is lossy, because colour
  cannot be recovered from a fully transparent premultiplied pixel.
- **The `default.*` precedence question is unverified.** 46 keys live in a
  `default.` tier. None of them currently has a specific twin, so no theme
  hits the collision in practice, but which tier wins is untested. Settling
  it needs a probe-and-restart experiment.
- **Key deletion is not supported** in v1. A key with no value is rejected
  with a message saying so.
- Themes cover `TouchColors`, `TouchOptions`, `Icons` and `System/ui.tox`. Three
  other colour systems exist in the same directory and are untouched:
  `colorPalette.def` / `opColorPalette.def` (Palette browser wheels) and
  `3DSceneColors` / `MiscColors` (3D viewport and lock/keyframe colours).
  Each would be one more parser behind the same interface.
- **`ui.tox` is installed whole and never inspected.** There is no way to tell
  from outside TouchDesigner whether a given `ui.tox` is one it will accept, so
  a wrong one shows up as a wrong-looking UI after a restart and nothing else.
  The 22 other `.tox` files in `Config/System` (the per-dialog ones, `keymanager`,
  `menu_op`, `maps`, `midi`) are **not** themed, and could not be by the same
  mechanism without one copy per theme per file - the copy is 1.1 MB each.
- The other icon-named directories in the install are **not** themed:
  `Config/IconsApp` (`toe.ico`, `tox.ico`, the dock and file-type icons),
  `Samples/Map/Icons`, `Samples/ProjectPackager/Icons`, and IDLE's
  `idlelib/Icons`. None of them are looked up through the same `UI_Icon` path.

## Layout

```
tdtheme.py              core library - no argparse, no print, so a GUI can reuse it
tdtiff.py               the TIFF reader, shared by tdicons.py and tdthememaker
tdicons.py              icon-set validation and install, PNG previews
cli.py                  argument parsing and output
tdtheme                 shell wrapper
check-td-writes         settles whether TouchDesigner writes these files
docs/                   background reading; see Documentation below
baseline/               captured pristine files + Icons/ + System/ui.tox
                        + version.json
baseline.local/         optional, gitignored; `capture --local` writes here
                        and it shadows baseline/ when it exists
themes/<name>/          TouchColors.yaml, TouchOptions.yaml, Icons/ (a full
                        set), and optionally ui.tox
  backups/<timestamp>/    only with `apply --backup`; off by default, see below
testiconsforagents/     PNG contact sheets written by `icons preview`
tests/                  round-trip gate + library tests + icon tests
tdthememaker/           the authoring tool: recipes, icon generation, export
  icons.py              TIFF/LZW writer, ops, recipe engine, contact sheets
  theme.py              export: sparse colour overlay + verbatim icon copy
  cli.py                list / build / diff / export / preview
  recipes/              one <name>.recipe.json per theme
  tests/                the generation and export suite
```

`tdtheme.py` deliberately contains no CLI concerns and returns data rather
than printing it, so a GUI front-end can be added without touching the core.
`tdicons.py` is likewise standalone - pure standard library, no third-party
imaging dependency - so the icon work did not compromise that rule.

`tdicons.py` also no longer writes a TIFF. It reads them, to validate and to
compare, and copies them, to install. Writing lives in `tdthememaker/`, and the
reader both of them use lives in `tdtiff.py`, which is the leaf of the import
graph: `tdicons.read_tiff` and `tdtiff.read_tiff` are the same function, reached
under two names. So there is one reader to get right and one writer, rather than
two of each.

## Writing themes

Authoring a theme is the other direction, and it is a separate command. It
lives in `tdthememaker/`, in this repository and sharing `tdtiff.py`, but under
its own entry point: generation is there, and so is writing a theme out of the
live install:

```
python3 -m tdthememaker.cli build mytheme     # generate the icon set
python3 -m tdthememaker.cli export mytheme    # record the install as a theme
```

`tdthememaker` imports this package's store layer - the restricted YAML parser,
the sparse differ, the field-integrity rules - rather than copying it, because
it is the side that *writes* that format. A second implementation of the writer
would mean themes this tool produces that `tdtheme apply` cannot read. `export`
is therefore the one part of `tdthememaker` that needs `tdtheme` present;
everything else runs without it.

## Tests

```
python3 tests/test_roundtrip.py           # byte-exact gate
python3 tests/test_tdtheme.py             # merge/diff/validate/capture/apply
python3 tests/test_icons.py               # read, validate, diff, apply, preview
python3 tdthememaker/tests/test_thememaker.py   # generation + export
```

The first three run against a throwaway copy of the install selected by the
`TDTHEME_CONFIG` environment variable, and never write to the real
TouchDesigner config. `test_icons.py` additionally cross-checks the codec
against `sips`, so a systematic misreading of the TIFF format cannot pass by
agreeing with itself.

The tool has **no third-party dependencies**. It uses PyYAML when
importable (TouchDesigner bundles 6.0.3) and otherwise falls back to a
loader covering the exact YAML subset it emits. The icon work kept that rule:
`tdtiff.py` is standard library only, including the TIFF/LZW reader and the
PNG writer.

## Documentation

`docs/` is background. Nothing there is needed to use the tool, and the README
above is the thing to read first.

- **[docs/parameter-reference.md](docs/parameter-reference.md)** — what each
  key in `TouchColors` and `TouchOptions` is understood to do. It marks which
  keys were verified empirically and which are only understood by naming
  convention, so read the confidence notes before trusting a value. Derivative
  documents none of this; the format is a black box.
- **[docs/reverse-engineering.md](docs/reverse-engineering.md)** — how the
  format and the icon path were recovered from the `dylib` symbol tables, and
  what is actually known about them. This is also the canonical account of the
  TIFF alpha convention (`ExtraSamples` and premultiplication), which the
  README summarises and the code implements.
- **[docs/icon-storage-design.md](docs/icon-storage-design.md)** — **a design
  note, partly implemented.** It proposes storing icon sets sparsely instead of
  shipping all 97 files per theme, and records the measurements behind the idea.
  Step 1 is built: a theme may ship a subset, and `tdicons.copy_icons` fills
  the rest from the baseline. Steps 2–5 are still a proposal. Where it
  disagrees with the code, the code is the current behaviour.
