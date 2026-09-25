# tdtheme

A theme manager for TouchDesigner on macOS. It edits the two undocumented
config files that control the whole UI, so you can build and switch themes
instead of hand-editing them.

```
./tdtheme status
./tdtheme list
./tdtheme apply midnight
./tdtheme apply default      # back to the stock look
```

## What it actually edits

Two files inside the app bundle:

| File | Shape | Controls |
|---|---|---|
| `TouchColors` | `key <TAB> r <TAB> g <TAB> b` | every UI colour, incl. all network-editor keys |
| `TouchOptions` | `key <TAB> value` | numeric UI options - sizing, spacing, alpha |

Both are CRLF, ASCII, no comments, and both are read at startup.

Format details that matter, all verified rather than assumed:

- `TouchColors` has 626 keys. Two of them (`dialog.commenthint`,
  `dialog.commenthint.comp`) carry an **extra empty second field**, so the
  colour is the *last three* fields, not fields 2-4.
- `TouchOptions` has 183 keys. Some values are legitimately empty
  (`font.default.face`, `font.mono.face`).
- The two files share **zero** keys - disjoint namespaces.
- The stores round-trip byte-for-byte through this tool. That invariant is
  enforced by `tests/test_roundtrip.py`, which is the acceptance gate for
  every other change.

## Commands

| Command | What it does |
|---|---|
| `capture` | snapshot the installed files as the baseline (refuses to clobber; `--force`) |
| `list` | list themes; `*` marks the last one applied |
| `status` | TouchDesigner build, baseline, whether TD is running, per-file drift |
| `export NAME` | save the currently installed state as a new theme |
| `diff NAME` | show exactly what a theme changes, old value vs new |
| `apply NAME` | merge, validate, back up, write |

## How themes are stored

A theme is a **sparse overlay**: only the keys it actually changes, as
restricted YAML. Everything else falls through to the captured baseline.

```yaml
worksheet.bg: ["0.05", "0.055", "0.075"]
tile.connection.hilite1: ["1", "0.85", "0.35"]
```

Sparse rather than full-file copies because a TouchDesigner update can add
new keys. A full copy would silently drop them; an overlay inherits them
from the baseline.

To build one by hand, copy `themes/midnight/` and edit the YAML. To capture
what you have currently set in TouchDesigner, `export` it.

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
- Colour channels are **never clamped**. The shipped `POP.hilite` contains
  `1.2`; values above 1.0 are intentional.
- Unknown keys warn - usually a typo, or a key a newer TouchDesigner added.
- Setting both `X` and `default.X` warns that the tier precedence is
  unverified.

## Things that will bite you

- **Restart TouchDesigner to see a change.** It reads both files at startup,
  so a running instance keeps showing the old appearance until you restart.
  That is a display lag, not data loss — the files themselves are safe to
  edit while TouchDesigner is running. `apply` says so when it detects a
  running process.
- **A TouchDesigner update wipes both files.** `status` compares the live
  build against the one recorded in the baseline and warns on a mismatch;
  `capture --force` refreshes it.
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
TouchDesigner ran a full session and wrote neither file.

This corrects an earlier version of this README, which claimed TouchDesigner
rewrites `TouchOptions` on exit and that changes made while it was running
would be lost. **That claim was wrong.** It came from observing a file change
after a TouchDesigner session and attributing it to TouchDesigner, without
controlling for edits made in the same session — the user was editing these
files directly at the time, and the change was theirs. The tool had been
warning about data loss that could not happen.

The correct model, now measured rather than assumed:

- TouchDesigner **reads** both files at startup.
- TouchDesigner **never writes** them, so editing while it runs is safe.
- A **restart is needed only for a change to become visible** — that is a
  display refresh, not a durability concern.

The practical consequence: there is no reason to quit TouchDesigner before
applying a theme. Re-run `./check-td-writes` after any future TouchDesigner
update to confirm this still holds for that build.

## Known limitations

- **The `default.*` precedence question is unverified.** 46 keys live in a
  `default.` tier. None of them currently has a specific twin, so no theme
  hits the collision in practice, but which tier wins is untested. Settling
  it needs a probe-and-restart experiment.
- **Key deletion is not supported** in v1. A key with no value is rejected
  with a message saying so.
- Themes cover `TouchColors` and `TouchOptions` only. Three other colour
  systems exist in the same directory and are untouched:
  `colorPalette.def` / `opColorPalette.def` (Palette browser wheels) and
  `3DSceneColors` / `MiscColors` (3D viewport and lock/keyframe colours).
  Each would be one more parser behind the same interface.

## Layout

```
tdtheme.py              core library - no argparse, no print, so a GUI can reuse it
cli.py                  argument parsing and output
tdtheme                 shell wrapper
check-td-writes         settles whether TouchDesigner writes these files
baseline/               captured pristine files + version.json
themes/<name>/          TouchColors.yaml, TouchOptions.yaml
backups/<timestamp>/    automatic, before every apply
tests/                  round-trip gate + library tests
```

`tdtheme.py` deliberately contains no CLI concerns and returns data rather
than printing it, so a GUI front-end can be added without touching the core.

## Tests

```
python3 tests/test_roundtrip.py    # byte-exact gate
python3 tests/test_tdtheme.py     # merge/diff/validate/capture/apply/export
```

Both run against a throwaway copy of the install selected by the
`TDTHEME_CONFIG` environment variable, and never write to the real
TouchDesigner config.

The one experiment this project still owes — does TouchDesigner *write*
these files, or only read them? — has a helper:

```sh
./check-td-writes record   # before launching TouchDesigner
# ... launch TD, use it, quit it ...
./check-td-writes check    # reports whether either file changed
```

The tool assumes TouchDesigner only reads these files, and does not warn
about data loss. That assumption was measured, not guessed — see "Confirmed"
under "Things that will bite you". Re-run `./check-td-writes` after a
TouchDesigner update to re-confirm it for the new build.

The tool has **no third-party dependencies**. It uses PyYAML when
importable (TouchDesigner bundles 6.0.3) and otherwise falls back to a
loader covering the exact YAML subset it emits.
