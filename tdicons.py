"""Reading, validating and installing TouchDesigner's icon sets.

This module is deliberately half a codec. `tdtheme` loads themes other people
made, so it needs to *read* icons - to validate them and to tell whether a
theme actually repainted anything - and to *copy* finished sets into place. It
does not need to create them. Generating an icon set from a recipe lives in
`tdthememaker/`, the authoring tool in this repository, and nothing here writes a
TIFF.

The one thing that makes reading non-obvious is alpha: the `ExtraSamples` tag
lies about 23 of the 97 shipped icons, so the reader decides from the samples
instead. `../docs/reverse-engineering.md` §5 is the canonical account; the
codec in `../tdtiff.py` carries the reasoning next to the code that acts on it.

What this module does
---------------------

- **Read and describe** - `read_tiff`, `describe_tiff`, `lzw_decode`.
- **Validate** - `icon_manifest` walks a set and reports each file's geometry,
  compression and detected alpha convention, which is what `validate_icons`
  turns into findings before anything is written.
- **Compare** - `diff_icons` (bytes) and `pixel_diff` (pixels). A regenerated
  icon always differs in bytes from the shipped one, so a byte diff overstates
  what a theme changed and the pixel diff is the honest number.
- **Install** - `capture_icons` takes a baseline from the running app,
  `copy_icons` installs a theme's set, `icon_diff` reports what changed.
- **Preview** - `contact_sheet` and `png_bytes` render a set to one PNG.

What a theme ships
------------------

A theme directory holds **all 97 icons**, not a sparse diff. That is
deliberate, and it is why the two tools are split. A complete set means:

- a theme folder is self-contained and can be sent to someone else as-is;
- an icon can be lifted from one theme into another with a plain file copy;
- `apply` can be a dumb, total, order-independent overwrite of a known file
  list, with no patch logic that could half-apply.

The cost is ~780 KB per theme, which is irrelevant next to a 764 KB install.

The overlay behaviour the colour files rely on is preserved where it matters:
applying a theme writes the icons the theme contains, so an icon a future
TouchDesigner adds, and that no theme has ever seen, is left exactly as
shipped instead of being deleted. A theme that ships a partial set is rejected
by `validate_icons` rather than quietly applied, because a partial install
leaks the previously applied theme's icons for the missing names.

Why a hand-written codec rather than a library
----------------------------------------------

The project has a hard rule: no third-party dependencies (`tdtheme.py` even
carries a fallback YAML loader for when PyYAML is absent). Neither Pillow nor
tifffile is installed here, and asking for them would break that rule. The
subset needed to read is small and completely known: 8bpc, RGB/RGBA,
photometric 2, single strip, LZW or no compression. That is a few hundred lines
of `struct` arithmetic, and the round-trip is testable - `tests/test_icons.py`
decodes all 97 shipped files and asserts the pixels are sane. Where an
independent check is wanted, `sips` (macOS ImageIO, i.e. a different libtiff)
is asked to read the result.
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


# ==========================================================================
# TIFF constants
# ==========================================================================


# ==========================================================================
# LZW
# ==========================================================================


# ==========================================================================
# TIFF container
# ==========================================================================


# ==========================================================================
# PNG (stdlib zlib only) - for previews, never for the install
# ==========================================================================


# ==========================================================================
# Icon sets on disk
# ==========================================================================


def copy_icons(source, destination, *, backup=None,               only_changed_against=None) -> "dict[str, str]":
    """Install an icon set, optionally backing up what it replaces.

    `only_changed_against` is a directory to diff against: a file whose bytes
    already match is not rewritten. That is not an optimisation for its own
    sake - TouchDesigner has these open, and touching only what actually
    differs keeps the install's mtimes meaningful as evidence of what a theme
    changed.
    """
    source = Path(source)
    destination = Path(destination)
    names = icon_names(source)
    if not names:
        raise IconError(f"{source} holds no icons; refusing to write an empty set")

    baseline_hashes = None
    if only_changed_against is not None:
        baseline_hashes = {
            name: hashlib.sha256((Path(only_changed_against) / name).read_bytes()).hexdigest()
            for name in icon_names(only_changed_against)
        }

    result = {"written": [], "unchanged": [], "backed_up": 0}
    for name in names:
        raw = (source / name).read_bytes()
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
