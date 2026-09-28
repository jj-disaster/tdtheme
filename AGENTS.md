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

## Backups are opt-in, and re-applying is the recovery path

`apply` copies the outgoing stores and icons to `backups/<timestamp>/` only when
given `--backup`. This is settled. Do not "restore" the old always-on default,
and do not prune or add retention logic to `backups/` — there is nothing to
prune once the default is off, and the flag is the whole mechanism.

The install is a pure function of two committed inputs: the two stores hold
`merge(baseline/, themes/<name>/)`, and the icon set holds the theme's own icons
completed from `baseline/Icons/`. Both are in git, so `apply <the previous
theme>` restores the outgoing bytes exactly, and `tdtheme status` names the
theme to re-apply. Only `apply` writes those files (see `check-td-writes`),
which is what makes "nothing else can have changed them" true rather than
hopeful.

That was measured, not assumed: a scratch script rebuilt a themed install from
`baseline/` plus `themes/` and compared it against the real one — both stores
and all 97 icons byte-identical. `tests/test_icons.py` keeps the weaker but
permanent version of that fact.

What a backup buys is the one input git does not have: a theme edited on disk
and not yet committed. Keep `--backup` for that. Two costs that decided the
default: a set runs 36 KB to 464 KB depending on how much the outgoing theme
had changed, and `backups/` sat inside the checkout on the same disk as the
files it duplicated, so it protected against a wrong `apply` and nothing else.

`backups/` is gitignored (`.gitignore`). It is local scratch and is never
committed.

`result["backup"]` is `None` when the flag is absent, and `tdtheme apply` prints
**nothing about backups at all** in that case. It used to print
`backup: none (...)` plus the recovery command, on the reasoning that silence
reads as "no backup was needed". That was a deliberate change of mind: the
flag is in the command's own `--help`, the recovery path is "re-apply", and
`tdtheme status` names the theme to re-apply, so a line on every run to
announce that nothing happened is noise. Do not add it back as a "helpful"
fix; the check that pins the absence is in `tests/test_tdtheme.py`, and
reversing this is a change to that check, not an addition to `cli.py`.

The line still prints when `--backup` is given, and it names the set it wrote.
`tests/test_tdtheme.py` pins the `None`, the absence of any new set under
`backups/`, and both halves of the output.

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
- **A theme ships all 97 icons, and `apply` fills the rest from the baseline.**
  Both halves are load-bearing, and the second looks redundant next to the
  first, so it is the first thing to get "simplified" away. `copy_icons` takes
  `fill_from=baseline_icons_dir()`; without it `apply` was only a total
  overwrite while every set happened to be complete, and a partial set left the
  omitted icons as the *previous* theme had left them. That is the exact state
  leak the complete-set design exists to prevent, and the case where it bites -
  an interrupted copy - is the one nobody would notice. `fill_from` only ever
  adds names: a name the theme ships wins, including one this build's baseline
  lacks, and nothing is ever deleted, because pruning an icon the baseline does
  not know about would break a newer TouchDesigner build.

## `ui.tox` is a copy, and the three decisions around it are settled

`Config/System/ui.tox` holds the UI layout — dialog and window geometry, open
panes, column widths. It is a `.tox`, TouchDesigner's own binary project format,
so **no value in it can be edited by this tool**; the only thing that can be
done is shipping a whole file and copying it over the top. A theme may carry
`themes/<name>/ui.tox`, and `apply` writes it.

Three choices here look like omissions and are not. Do not "fix" them.

- **It is not backed up, not even by `--backup`.** It is 1.1 MB and the only
  thing that ever writes it is `apply` itself, so a backup would hold a copy of
  whichever theme was applied last - bytes that are already in git, in that
  theme's own folder - at 1.1 MB per apply. This used to read "unlike the two
  stores and the icon set", and it stopped being true when backups became
  opt-in: those two are now copied only on request, and this one is excluded
  from the request. The *reason* is the same one that made backups opt-in in
  the first place, so the reasoning now runs in one direction rather than two.
  The size is what makes this the strongest case rather than a merely
  consistent one - a store pair is 32 KB - so `--backup` is a cheap request
  for them and a ruinous one here.
  `tests/test_tdtheme.py` pins the absence **and asserts the precondition that
  the apply really did get a backup set**, because with backups off by default
  the absence alone would pass on an empty `backups/` and prove nothing.
- **A theme with no `ui.tox` falls back to `default`'s**, rather than writing
  nothing. Writing nothing leaves the *previous* theme's dialogs in place while
  `list` reports the new theme, which is the leak `default` prevents. The
  fallback is a total-overwrite property, same as the icon sets.
- **Nothing validates it.** There is no parser and no way to ask TouchDesigner
  whether it liked the bytes, so a check could only confirm the copy landed,
  which `write_file` already does. `apply` reports which file it wrote and
  stops there.

`--no-icons` does **not** skip it; that flag is about the icon set.

TouchDesigner **never writes** this file. It reads it, and the only way a new
`ui.tox` comes into existence is a manual export, which lands wherever the user
saved it rather than over the one in the install folder. So unlike the stores
and the icons, `ui.tox` is absent from `check-td-writes` on purpose, and `apply`
does *not* need re-running after a session: nothing in the install can have
moved underneath it. A hand-arranged UI is the one thing here that is **not**
recoverable — it exists only where the user put it. Copy it into a theme folder,
not into `backups/`.

## Traps in this codebase

These cost time. They are properties of the code, not opinions.

- **`diff` only looks in `other`.** `tdtheme.diff(base, other)` reports keys
  *in `other`* that are added or changed, and cannot report a key that `other`
  does not have. That is correct for `tdtheme diff`, whose question is what a
  theme *adds to* the baseline. It is not sufficient for "what did this apply
  change", because an overlay may add a key the baseline lacks and the next
  theme will drop it — a key that is absent from the comparison and therefore
  invisible. `store_changes` wraps `diff` and adds the dropped keys, and the
  `apply` summary line prints them as `N removed`. Do not build a
  change-report on bare `diff` and conclude removals are impossible; they are
  merely unreported.
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
