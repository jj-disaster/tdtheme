"""Reading, validating and installing TouchDesigner's icon sets.

Deliberately half a codec. `tdtheme` loads themes other people made, so it needs
to *read* icons - to validate them and to tell whether a theme actually repainted
anything - and to *copy* finished sets into place, but not to create them:
generating a set from a recipe lives in `tdthememaker/`, and nothing here writes a
TIFF. `read_tiff`, `describe_tiff` and `lzw_decode` read and describe;
`icon_manifest` reports each file's geometry, compression and detected alpha,
which is what `validate_icons` turns into findings before anything is written;
`diff_icons`/`pixel_diff` compare by byte and by pixel; `capture_icons` takes a
baseline from the running app, `copy_icons` installs a theme's set, `icon_diff`
reports what changed; `contact_sheet` and `png_bytes` render a set to one PNG.

The one thing that makes reading non-obvious is alpha: the `ExtraSamples` tag lies
about 23 of the 97 shipped icons, so the reader decides from the samples instead.
`../docs/reverse-engineering.md` §5 is the canonical account; the codec in
`../tdtiff.py` carries the reasoning next to the code that acts on it.

What a theme ships: **all 97 icons**, not a sparse diff. Deliberate, and why the
two tools are split: a theme folder is self-contained and sendable as-is, an icon
can be lifted from one theme into another with a plain file copy, and `apply` can
be a dumb, total, order-independent overwrite of a known file list, with no patch
logic that could half-apply. The cost is ~780 KB per theme for a set that copies
the baseline verbatim, and 101-128 KB for a regenerated one, either way
irrelevant next to a 764 KB install.

A set that is *not* complete is still applied safely, and this is the one place
where the completeness above is a convention rather than a requirement. Anything
the theme does not ship is filled in from the baseline rather than left as the
previously applied theme left it, so a partial set cannot leave the install
holding a mixture of two themes. Without that, `apply` was only a total overwrite
when every set happened to be complete - and an interrupted or hand-assembled set
is exactly the case where nobody would notice. The same reasoning is why an icon a
future TouchDesigner adds, and that no theme has ever seen, is left exactly as
shipped instead of being deleted. A theme that ships a partial set is not
rejected: `validate_icons` reports the shortfall as a single aggregated warning,
and `copy_icons` completes the set from the baseline through its `fill_from`
argument. The warning is informational - `apply` gates on errors only - so the
fill above, not the gate, is what stops a partial set leaking the previously
applied theme's icons for the missing names.

Why a hand-written codec rather than a library: the project has a hard rule of no
third-party dependencies (`tdtheme.py` even carries a fallback YAML loader for
when PyYAML is absent). Neither Pillow nor tifffile is installed here, and asking
for them would break that rule. The subset needed to read is small and completely
known: 8bpc, RGB/RGBA, photometric 2, single strip, LZW or no compression. That
is a few hundred lines of `struct` arithmetic, and the round-trip is testable -
`tests/test_icons.py` decodes all 97 shipped files and asserts the pixels are
sane. Where an independent check is wanted, `sips` (macOS ImageIO, i.e. a
different libtiff) is asked to read the result.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

# The codec lives in tdtiff; re-exported so tdicons.read_tiff keeps working.
from tdtiff import (
    IconError,
    TiffImage,
    read_tiff,
    describe_tiff,
    lzw_decode,
    png_bytes,
    contact_sheet,
    icon_names,
    icon_manifest,
    capture_icons,
    diff_icons,
    pixel_diff,
    _atomic_write,
)


__all__ = [
    "IconError", "TiffImage",     "read_tiff", "describe_tiff", "lzw_decode",
    "png_bytes", "contact_sheet",
    "icon_names", "icon_manifest", "capture_icons",
    "diff_icons", "pixel_diff", "copy_icons",
]


# Icon sets on disk ========================================================


def copy_icons(source, destination, *, backup=None, only_changed_against=None,
               fill_from=None) -> "dict":
    """Install an icon set, optionally backing up what it replaces.

    `only_changed_against` is a directory to diff against: a file whose bytes
    already match is not rewritten. Not an optimisation for its own sake -
    TouchDesigner has these open, and touching only what actually differs keeps
    the install's mtimes meaningful as evidence of what a theme changed.

    `fill_from` completes a partial set. Every name it holds that `source` lacks
    is installed too, taken from `fill_from`, and `source` wins any name both
    hold. The result is a whole icon set rather than a patch over whatever the
    install happened to contain: a theme switch that leaves the previous theme's
    glyphs in place leaks state, and a leaked glyph is a missing-looking icon
    that nothing reports. Every shipped theme ships all 97, so in practice the
    union equals `source` - this is what makes a hand-assembled or interrupted set
    safe rather than silently partial.
    """
    source = Path(source)
    destination = Path(destination)
    names = icon_names(source)
    filled = {}
    if fill_from is not None:
        fill_from = Path(fill_from)
        present = set(names)
        filled = {name: fill_from / name for name in icon_names(fill_from)
                  if name not in present}
        names = names + sorted(filled)
    if not names:
        raise IconError(f"{source} holds no icons; refusing to write an empty set")

    baseline_hashes = None
    if only_changed_against is not None:
        baseline_hashes = {
            name: hashlib.sha256((Path(only_changed_against) / name).read_bytes()).hexdigest()
            for name in icon_names(only_changed_against)
        }

    result = {"written": [], "unchanged": [], "filled": sorted(filled), "backed_up": 0}
    for name in names:
        raw = (filled[name] if name in filled else source / name).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        target = destination / name
        if (baseline_hashes is not None and baseline_hashes.get(name) == digest
                and target.exists()
                and hashlib.sha256(target.read_bytes()).hexdigest() == digest):
            result["unchanged"].append(name)
            continue
        if backup is not None and target.exists():
            backup = Path(backup)
            backup.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup / name)
            result["backed_up"] += 1
        _atomic_write(target, raw)
        result["written"].append(name)
    return result
