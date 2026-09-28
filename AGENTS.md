# AGENTS.md

Working notes for agents and humans changing this repository. These are
conclusions that cost real investigation to reach. If something here contradicts
what you measure, measure it and fix this file.

## PyYAML: its absence is not a problem. Move on.

**Do not treat "PyYAML is not installed" as a defect, and do not propose adding
it as a dependency or deleting the fallback loader.** This is settled.

The tool is standard-library-only by design. `tdtheme.py` has two overlay
loaders:

- `_load_overlay_fallback` — the default. Handles the documented subset.
- `load_overlay` — uses PyYAML when importable, else delegates to the fallback.

`./tdtheme` runs `python3`, which on a clean machine has no PyYAML, so **the
fallback is the primary path and always will be.** TouchDesigner bundles
PyYAML 6.0.3, so the PyYAML path is reachable only if someone deliberately runs
TouchDesigner's interpreter — that is a nice-to-have, not the contract.

A test asserted `_have_yaml() is False` and failed on any correctly provisioned
host. That was a bug, not a feature. **Record which loader ran as a printed note;
never assert it.** All four suites must pass under both interpreters; there is no
CI config in this repo, so "run them under both" is the whole verification
procedure.

### What still matters, and is not the same problem

The two loaders must return **the same data for the same file**. They agree by
construction on anything `json.loads` accepts, because JSON is a subset of YAML.
They used to disagree on single-quoted scalars: the fallback is
`json.loads(rest)` and otherwise keeps the raw string, so `origsize: '11'`
loaded as the three characters `'11'` under the fallback and as `11` under
PyYAML. Same file, same command, different install depending on interpreter.

The fallback now refuses single-quoted scalars and lists outright, and
`validate` treats a non-numeric `.size` as an **error** rather than a warning,
because `apply` gates on `severity == "error"` only. So the divergence fails
loudly instead of writing quotes into a live install.

`tests/test_tdtheme.py` covers this three ways. Note that the aggregate check
("both loaders agree on all committed overlays") passes even against the
unfixed code, because no committed overlay uses single quotes — the targeted
probes are what actually catch it. Keep both kinds.

## Other things that are settled

- **`tdtiff.py` is the shared TIFF layer.** It is the leaf of the import graph
  and imports nothing from this project. `tdicons.py` and
  `tdthememaker/icons.py` both re-export from it; `tdicons.read_tiff` and
  `tdtiff.read_tiff` are the same object. Do not re-duplicate codec code.
- **Writing lives only in `tdthememaker`.** `write_tiff` and `lzw_encode` are
  authoring-only. Applying a theme only ever needs to read.
- **The four test scripts are plain scripts**, not a framework. Run them
  directly; each exits non-zero on failure. Run all four after any change, and
  run `tests/test_tdtheme.py` under TouchDesigner's interpreter too.
- **Every name in an `__all__` must resolve.** After deleting a symbol, a stale
  `__all__` entry makes `from tdicons import *` raise `AttributeError`, which no
  other test catches. Verify by importing each module and resolving `__all__`.
- **`docs/icon-storage-design.md` is an unimplemented proposal**, deliberately.
  It is not a description of current behaviour. Do not "fix" code to match it.

## `ui.tox` is a copy, and the three decisions around it are settled

`Config/System/ui.tox` holds the UI layout — dialog and window geometry, open
panes, column widths. It is a `.tox`, TouchDesigner's own binary project format,
so **no value in it can be edited by this tool**; the only thing that can be
done is shipping a whole file and copying it over the top. A theme may carry
`themes/<name>/ui.tox`, and `apply` writes it.

Three choices here look like omissions and are not. Do not "fix" them.

- **It is not backed up**, unlike the two stores and the icon set. It is 1.1 MB
  and TouchDesigner rewrites it on any layout change, so an apply-time copy is a
  snapshot of the last session, at one copy per apply. `tests/test_tdtheme.py`
  pins the absence, because it reads as an oversight.
- **A theme with no `ui.tox` falls back to `default`'s**, rather than writing
  nothing. Writing nothing leaves the *previous* theme's dialogs in place while
  `list` reports the new theme, which is the leak `default` prevents. The
  fallback is a total-overwrite property, same as the icon sets.
- **Nothing validates it.** There is no parser and no way to ask TouchDesigner
  whether it liked the bytes, so a check could only confirm the copy landed,
  which `write_file` already does. `apply` reports which file it wrote and
  stops there.

`--no-icons` does **not** skip it; that flag is about the icon set.

A `.tox` is written *by TouchDesigner* — a project file, saved as state. So
`apply` may need re-running after a session that changed the layout, and unlike
the stores and the icons, `ui.tox` is absent from `check-td-writes` on purpose.
A hand-arranged UI is the one thing here that is **not** recoverable: it exists
only where the user put it. Copy it into a theme folder, not into `backups/`.

## Traps in this codebase

These cost time. They are properties of the code, not opinions.

- **Implicit protocol calls defeat grep.** `len(x)` and `x in y` reach
  `__len__` and `__contains__` without either name appearing at the call site.
  `TdFile.__len__` and `TdFile.__contains__` look unused to a grep and are used
  by the test suite. To decide whether a dunder is dead, walk the AST **per
  function scope** — and bind locals per scope, or a local named `after` in one
  function will convince you a same-named local in another is live.
- **A decorator is not part of `node.lineno`.** Slicing source lines from
  `n.lineno` to `n.end_lineno` silently drops `@dataclass`, which removes
  `__init__`/`__eq__`/`__repr__` with no error until something constructs the
  class. Include `n.decorator_list` explicitly.
- **The 2-space indent is real.** Several files use 2-space indents, not 4.
  Check before inserting a block, or the file will not parse.
- **pyflakes does not honour `# noqa`.** It is flake8 that reads those comments.
  A `# noqa: F401` re-export will still be reported by pyflakes. That is
  expected, not a bug to fix.
- **Never delete a symbol on the strength of one grep.** Verify with a
  scope-aware AST pass, then confirm every `__all__` still resolves, then run the
  suites. Removing an `@dataclass` from a deleted class leaves the decorator
  attached to whatever function comes next — a `TypeError` at import time, not a
  clean failure.
