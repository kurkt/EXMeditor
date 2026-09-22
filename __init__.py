# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""EXMeditor — a Blender 3.6 add-on for editing Ex Machina / Hard Truck
Apocalypse maps.

Imports a map folder — terrain, world.xml nodes, the dynamic layer,
roads, collision, models and textures — for editing in Blender, and
exports it back, writing only what changed and preserving every byte
it does not understand. See README.md for the workflow and
ARCHITECTURE.md for how the packages fit together.
"""

bl_info = {
    "name": "EXMeditor",
    "author": "Kurkt",
    "version": (0, 52, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > EXMeditor",
    "description": "Import, edit and export Ex Machina / Hard Truck Apocalypse maps and models",
    "category": "Import-Export",
}

import os
import sys

# --- Make this add-on's internal packages importable as flat top-level
# modules (`core`, `utils`, `formats`, `blender_io`, `addon`) instead of
# requiring relative imports everywhere. Blender imports an add-on as a
# single package named after its folder (e.g. "EXMeditor"), so
# `import core` would otherwise fail — this inserts the add-on's own
# directory onto sys.path so those bare imports resolve.
#
# KNOWN TRADE-OFF: this is a common Blender add-on pattern, but it does
# put generically-named packages ("utils", "core") onto the shared
# Python path, which could collide with another installed add-on doing
# the same thing. Flagged here as technical debt to revisit (e.g.
# renaming to more unique package names, or switching every internal
# import to explicit relative imports) if it ever collides in practice.
_ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
if _ADDON_DIR not in sys.path:
    sys.path.insert(0, _ADDON_DIR)

_FLAT_PACKAGES = ("addon", "blender_io", "core", "formats", "utils")
#: The name this add-on was installed under before 0.52.0. An old copy
#: left enabled registers the same operators and, worse, has already
#: put ITS `core`/`addon`/… into sys.modules under the flat names, so
#: the imports below would silently pick up its code instead of this.
_PREVIOUS_NAME = "ExMachinaSDK"


def _evict_foreign_flat_modules() -> None:
    """Drop flat-named modules that belong to another copy of this
    add-on (an older install under another name), so the imports
    below resolve to this directory."""
    for name in list(sys.modules):
        root = name.split(".")[0]
        if root not in _FLAT_PACKAGES:
            continue
        module = sys.modules[name]
        origin = getattr(module, "__file__", None) or ""
        if origin and not os.path.abspath(origin).startswith(_ADDON_DIR):
            del sys.modules[name]


_evict_foreign_flat_modules()

from addon import (  # noqa: E402
    asset_operator,
    asset_preview_operator,
    assign_operator,
    create_map_operator,
    create_model_operator,
    doctor_operator,
    edit_texture_operator,
    fork_texture_operator,
    save_all_textures_operator,
    save_texture_operator,
    operators,
    panels,
    preferences,
)
from addon.preferences import research_tools_installed  # noqa: E402
from blender_io import handler_guard  # noqa: E402
from formats.exm.plugin import ExMachinaPlugin  # noqa: E402
from formats.registry import default_registry  # noqa: E402

# The research tools (censuses, forensics, coverage reports) are in the
# source repository only; build_release.py leaves addon/research out of
# the install archive. Imported by presence, not by catching
# ImportError, so a broken research package still fails loudly.
if research_tools_installed():
    from addon import research  # noqa: E402
else:
    research = None


def _retire_previous_install() -> None:
    """Disable an older copy still enabled under the previous name.

    Both would register ``exmachina.*`` operators, and Blender refuses
    the second. Disabling is reversible and leaves the files alone;
    the console says what happened.
    """
    try:
        import addon_utils
        import bpy

        if _PREVIOUS_NAME in bpy.context.preferences.addons:
            addon_utils.disable(_PREVIOUS_NAME, default_set=True)
            print(
                f"EXMeditor: disabled the earlier '{_PREVIOUS_NAME}' add-on — "
                "it is the same tool under its old name. Remove it from "
                "Preferences > Add-ons when convenient."
            )
    except Exception as exc:  # noqa: BLE001 - never block registration on this
        print(f"EXMeditor: could not check for a previous install: {exc}")


def register() -> None:
    _retire_previous_install()
    preferences.register()
    operators.register()
    doctor_operator.register()
    edit_texture_operator.register()
    save_texture_operator.register()
    save_all_textures_operator.register()
    fork_texture_operator.register()
    assign_operator.register()
    asset_operator.register()
    asset_preview_operator.register()
    create_map_operator.register()
    create_model_operator.register()
    panels.register()
    if research is not None:
        research.register()  # after panels: its panel is a child of the main one
    default_registry.register(ExMachinaPlugin())
    # Other add-ons' depsgraph handlers off the preview render thread,
    # for the whole session: see blender_io/handler_guard.py.
    handler_guard.install()


def unregister() -> None:
    handler_guard.uninstall()
    if research is not None:
        research.unregister()
    panels.unregister()
    create_model_operator.unregister()
    create_map_operator.unregister()
    asset_preview_operator.unregister()
    asset_operator.unregister()
    assign_operator.unregister()
    fork_texture_operator.unregister()
    save_all_textures_operator.unregister()
    save_texture_operator.unregister()
    edit_texture_operator.unregister()
    doctor_operator.unregister()
    operators.unregister()
    preferences.unregister()
