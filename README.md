# tdtheme

A theme manager for TouchDesigner on macOS. It edits the undocumented config
files and icon directory that control the whole UI - every colour, every size,
every glyph - so you can build and switch themes instead of hand-editing them.

```
./tdtheme status
./tdtheme list
./tdtheme apply midnight
./tdtheme apply default      # back to the stock look
```

## What it actually edits

Two files and one directory inside the app bundle:

| Path | Shape | Controls |
|---|---|---|
| `TouchColors` | `key <TAB> r <TAB> g <TAB> b` | every UI colour, incl. all network-editor keys |
| `TouchOptions` | `key <TAB> value` | numeric UI options - sizing, spacing, alpha |
| `Icons/*.tiff` | classic TIFF, 8-bit RGB(A) | the 97 UI glyphs: flags, badges, overlays |

The two stores are read at startup. The icons are not - they are read **lazily
on first use and then cached for the life of the process**, so an icon change
needs a restart even to be looked at. See [Icons](#icons).

Format details that matter, all verified rather than assumed:

- `TouchColors` has 626 keys. Two of them (`dialog.commenthint`,
  `dialog.commenthint.comp`) carry an **extra empty second field**, so the
  colour is the *last three* fields, not fields 2-4.
- `TouchOptions` has 183 keys. Some values are legitimately empty
  (`font.default.face`, `font.mono.face`).
- The two files share **zero** keys - disjoint namespaces.
- The 97 icons are 69 pure-white-with-alpha glyphs, 3 pure-black, and 25
  genuinely coloured. 89 are LZW-compressed, 8 are not, and 5 are split across
  multiple strips. See [Icons](#icons) below.
- The stores round-trip byte-for-byte through this tool. That invariant is
  enforced by `tests/test_roundtrip.py`, which is the acceptance gate for
  every other change.

## Commands

| Command | What it does |
|---|---|
| `capture` | snapshot the installed files and icons as the baseline (refuses to clobber; `--force`) |
| `list` | list themes; `*` marks the last one applied |
| `status` | TouchDesigner build, baseline, whether TD is running, per-file drift |
| `export NAME` | save the currently installed state as a new theme |
| `diff NAME` | show exactly what a theme changes, old value vs new |
| `apply NAME` | merge, validate, back up, write (`--no-icons` to skip the icon set) |
| `icons list [NAME]` | the icon set, with size and digest per file (NAME omitted = baseline) |
| `icons build NAME` | regenerate a theme's icons from its recipe |
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

To build one by hand, copy `themes/midnight/` and edit the YAML. To capture
what you have currently set in TouchDesigner, `export` it.

## Icons

The 97 glyphs in `Config/Icons/` are themed too, and they work differently from
the two stores.

**They are not a sparse overlay.** Every theme ships a *complete* icon set, and
that is deliberate. A theme with no `Icons/` directory would write nothing, so
applying `sunset` and then `default` would leave sunset's icons in the install
while claiming to be stock. A total overwrite is the only model where switching
themes cannot leak state. The cost is disk - 1.2 MB across the five shipped
sets, against a few KB for a delta scheme - and that was an accepted trade.

**Each set is generated from a recipe**, `themes/<name>/icons.recipe.json`, so
the binary blobs in git are reproducible artefacts rather than the source of
truth:

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
byte for byte* rather than decode and re-encode. That is what makes
`apply default` a lossless reset.

```
./tdtheme icons build midnight      # regenerate one theme's icons
./tdtheme icons diff mono           # by pixel - which icons are actually repainted
./tdtheme icons preview             # PNG contact sheet of the baseline
```

### What the recipes can and cannot reach

Worth knowing before writing one, because it is a property of the shipped files
rather than of any theme:

- **69 of the 97 are pure white with alpha.** A hue op moves all of them; a
  desaturating op moves none of them.
- **3 are pure black with alpha** - `Cook`, `Grid`, `CommentOffSmall`. There is
  no hue in a black pixel, so `tint` leaves them alone. That is the right
  outcome: they are dark ink drawn on a light tile, and recolouring them would
  break the field they sit on.
- **25 are genuinely coloured** - the error and warning faces, the yellow star.
  Only these move under `grayscale`, which is why `mono` repaints 18 of 97 and
  `bnw` 25 of 97 while looking like a complete theme.

### Bytes are not pixels

A regenerated icon is *always* a different file from the shipped one: it is
single-strip, explicitly straight-alpha, and has ~5 KB of Photoshop metadata
stripped out. So a byte comparison reports all 97 files as changed even when the
recipe did nothing to them.

- `tdtheme icons diff` decodes and compares **pixels**, which is the honest
  number. `--bytes` skips the decode.
- `tdtheme list` and `tdtheme status` stay byte-level: decoding all five theme
  sets costs 1.5s, and those two are meant to be glanced at. They say "differ
  in bytes" for that reason.
- `tdtheme icons build` reports the pixel count for the set it just wrote.

### The alpha trap

**95 of the 97 shipped icons store premultiplied alpha.** TIFF leaves the alpha
convention undefined when `SamplesPerPixel` is 4 and no `ExtraSamples` tag is
present, and in practice decoders assume *premultiplied*. This tool un-premultiplies
on read, transforms in straight alpha, and therefore **must** write
`ExtraSamples = 2` (unassociated) explicitly.

Getting this wrong is silent. The file is a perfectly valid TIFF, it decodes
without complaint, and it renders too dark with its thinnest strokes gone.
`tests/test_icons.py` pins it down by converting generated files with `sips` -
an independent decoder - and requiring a **max channel difference of 0** against
our own reading, plus a counterfactual proving the check can fail.

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
  silently, so without this rule the bad value survives `export` and `apply`
  writes it into the install. The rules are relative to the baseline, so the
  two shipped `dialog.commenthint*` keys keep their 4-field shape and all 626
  pristine keys validate clean.
- Colour channels are **never clamped**. The shipped `POP.hilite` contains
  `1.2`; values above 1.0 are intentional.
- Unknown keys warn - usually a typo, or a key a newer TouchDesigner added.
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

- **Restart TouchDesigner to see a change.** It reads all three at startup, so a
  running instance keeps showing the old appearance until you restart. That is a
  display lag, not data loss — the files themselves are safe to edit while
  TouchDesigner is running. `apply` says so when it detects a running process.
  Icons are additionally cached lazily on first use, so they are not re-read
  even for a window that opens later in the session.
- **A TouchDesigner update wipes all three.** `status` compares the live build
  against the one recorded in the baseline and warns on a mismatch;
  `capture --force` refreshes it. A new build may also ship new or resized
  icons, which `validate_icons` reports per theme.
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
applying a theme. Re-run `./check-td-writes` after any future TouchDesigner
update to confirm this still holds for that build.

## Known limitations

- **Icon storage is wasteful by design.** Five complete sets come to 1.2 MB
  where a delta scheme would be a few KB. A complete set per theme is what makes
  `apply` a total overwrite and `default` a lossless reset; sharing or
  deduplicating the unchanged icons would reintroduce the leak that design
  avoids. The four regenerated sets are 97-101 KB each rather than the stock
  764 KB, because re-encoding drops the Photoshop metadata and re-applies LZW -
  the bulk of the 1.2 MB is `default`, which copies the baseline verbatim.
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
- Themes cover `TouchColors`, `TouchOptions` and `Icons`. Three other colour
  systems exist in the same directory and are untouched:
  `colorPalette.def` / `opColorPalette.def` (Palette browser wheels) and
  `3DSceneColors` / `MiscColors` (3D viewport and lock/keyframe colours).
  Each would be one more parser behind the same interface.
- The other icon-named directories in the install are **not** themed:
  `Config/IconsApp` (`toe.ico`, `tox.ico`, the dock and file-type icons),
  `Samples/Map/Icons`, `Samples/ProjectPackager/Icons`, and IDLE's
  `idlelib/Icons`. None of them are looked up through the same `UI_Icon` path.

## Layout

```
tdtheme.py              core library - no argparse, no print, so a GUI can reuse it
tdicons.py              TIFF/LZW codec, icon transforms, recipes, PNG previews
cli.py                  argument parsing and output
tdtheme                 shell wrapper
check-td-writes         settles whether TouchDesigner writes these files
baseline/               captured pristine files + Icons/ + version.json
themes/<name>/          TouchColors.yaml, TouchOptions.yaml,
                        icons.recipe.json, Icons/ (generated)
backups/<timestamp>/    automatic, before every apply
testiconsforagents/     PNG contact sheets written by `icons preview`
tests/                  round-trip gate + library tests + icon tests
```

`tdtheme.py` deliberately contains no CLI concerns and returns data rather
than printing it, so a GUI front-end can be added without touching the core.
`tdicons.py` is likewise standalone - pure standard library, no third-party
imaging dependency - so the icon work did not compromise that rule.

## Tests

```
python3 tests/test_roundtrip.py    # byte-exact gate
python3 tests/test_tdtheme.py     # merge/diff/validate/capture/apply/export
python3 tests/test_icons.py       # TIFF codec, recipes, icon apply
```

All three run against a throwaway copy of the install selected by the
`TDTHEME_CONFIG` environment variable, and never write to the real
TouchDesigner config. `test_icons.py` additionally cross-checks the codec
against `sips`, so a systematic misreading of the TIFF format cannot pass by
agreeing with itself.

### Re-checking the read-only assumption

The claim that TouchDesigner only ever reads these files was originally
measured by hand, and the evidence is in "Confirmed" above. `./check-td-writes`
is the helper for redoing it on a new build:

```sh
./check-td-writes record   # before launching TouchDesigner
# ... launch TD, use it, quit it ...
./check-td-writes check    # reports whether any of the three changed
```

It tracks all three artefacts; the `Icons` directory is hashed as one blob
rather than listed file by file, since the only question is whether the set
changed. `tdtheme` assumes TouchDesigner never writes these files and does not
warn about data loss. That assumption was measured, not guessed - but it is
per-build, so re-run this after a TouchDesigner update. The tool itself
re-checks the baseline build number on every `status`.

The tool has **no third-party dependencies**. It uses PyYAML when
importable (TouchDesigner bundles 6.0.3) and otherwise falls back to a
loader covering the exact YAML subset it emits. The icon work kept that rule:
`tdicons.py` is standard library only, including its own TIFF/LZW codec and PNG
writer.
