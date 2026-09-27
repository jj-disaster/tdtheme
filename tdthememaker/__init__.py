"""Author TouchDesigner themes; `tdtheme` installs them.

Two halves, both shipped here:

- `icons` - the pixel side. A TIFF codec, the recolour and adjustment ops, and
  the recipe engine that turns a short list of instructions into a complete
  97-icon directory.
- `theme` - the store side. Records the current install as a new theme, with
  the colour stores as a sparse overlay and the icons copied byte for byte.

`theme` is the one part that is not standalone: it imports `tdtheme` rather than
reimplementing the restricted-YAML format it writes, because a second writer
would mean themes this tool produces that `tdtheme apply` cannot read.

Command line, from the repository root:

    python3 -m tdthememaker.cli list
    python3 -m tdthememaker.cli build midnight
    python3 -m tdthememaker.cli export mytheme
"""

from __future__ import annotations

from . import icons, theme

__all__ = ["icons", "theme"]
