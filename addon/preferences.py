# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Add-on preferences.

Holds settings that belong to the installation rather than to a
single import: the game's location, what an import builds, and
whether the research panel is shown. The game root in particular is a
property of the machine, not of the map being opened — re-picking it
on every import was busywork.
"""

from __future__ import annotations

import logging

import bpy

from utils.logging import set_verbosity

# NOTE: this must match the add-on's top-level package/module name —
# i.e. the folder name Blender loads this add-on from (see bl_info in
# the top-level __init__.py). Hardcoded rather than derived from
# __name__/__package__ because this module is imported as a flat
# top-level module (see the sys.path setup in the top-level
# __init__.py), so __package__ here is "addon", not "EXMeditor.addon".
ADDON_PACKAGE_NAME = "EXMeditor"

_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def _on_log_level_update(self, context) -> None:
    set_verbosity(_LOG_LEVELS[self.log_level])


def terrain_detail(context) -> bool:
    """Whether to lay the tile textures over the baked terrain.

    On. ``landscape.dds`` is one 1024x1024 image over 4088 units —
    four units to a texel — so on its own the ground is a blur, and the
    fine surface in the original editor comes from these.

    This was off for a while because the tile map looked undecoded. It
    was: read at 256 cells the arrangement sits at chance. At 128 it
    agrees with its neighbours twice as often as chance, which is what
    ground laid out in regions looks like.
    """
    try:
        addons = context.preferences.addons
        return bool(addons[ADDON_PACKAGE_NAME].preferences.terrain_detail)
    except (AttributeError, KeyError, TypeError):
        return True


def blend_tiles(context) -> bool:
    """Whether to blend one ground tile into the next.

    On. The engine does it, and without it the ground is squares of a
    single texture where the game has gradients. What reproduces it is
    ``core/tile_blend.py``; the ramp between tiles is an approximation
    of the mask atlas and exact at the cell centres.

    Off draws each cell in one texture, which is worth having when the
    question is which tile a cell actually holds.
    """
    try:
        addons = context.preferences.addons
        return bool(addons[ADDON_PACKAGE_NAME].preferences.blend_tiles)
    except (AttributeError, KeyError, TypeError):
        return True


def import_grass(context) -> bool:
    """Whether to place the map's grass at all.

    On. A map carries tens of thousands of tufts — 72529 on r1m1 — and
    that is the map. There used to be a cap here instead, and it was
    the wrong shape of control: a partial field is not a lighter
    version of the map, it is the map with holes wherever the stride
    happened to skip. Either the grass is wanted or it is not.
    """
    try:
        addons = context.preferences.addons
        return bool(addons[ADDON_PACKAGE_NAME].preferences.import_grass)
    except (AttributeError, KeyError, TypeError):
        return True


def import_lighting(context) -> bool:
    """Whether to build the map's own sun and ambient."""
    try:
        addons = context.preferences.addons
        return bool(addons[ADDON_PACKAGE_NAME].preferences.import_lighting)
    except (AttributeError, KeyError, TypeError):
        return True


def road_models(context) -> bool:
    """Whether to place the road's own models instead of a ribbon.

    Off. Placing them is what the engine does and is the only way a
    road will ever be exactly right — but a segment per node overlaps
    where the pieces are meant to span node to node, and how the model
    is meant to be oriented and scaled has not been established. Until
    it is, the ribbon is the more predictable of the two.
    """
    try:
        addons = context.preferences.addons
        return bool(addons[ADDON_PACKAGE_NAME].preferences.road_models)
    except (AttributeError, KeyError, TypeError):
        return False


def standard_view_transform(context) -> bool:
    """Whether to switch the scene to the Standard view transform."""
    try:
        addons = context.preferences.addons
        return bool(addons[ADDON_PACKAGE_NAME].preferences.standard_view_transform)
    except (AttributeError, KeyError, TypeError):
        return True


def textured_roads(context) -> bool:
    """Whether to build roads as textured ribbons rather than splines."""
    try:
        addons = context.preferences.addons
        return bool(addons[ADDON_PACKAGE_NAME].preferences.textured_roads)
    except (AttributeError, KeyError, TypeError):
        return True


def lightmap_time(context) -> str:
    """Which of the map's four lightmaps to light the terrain with.

    Every map ships daytime, sunrise, sunset and night. Daytime is the
    one kept uncompressed and the one the editor shows, so it is the
    default; the others are there for judging how a change reads at
    another hour.
    """
    try:
        addons = context.preferences.addons
        return str(addons[ADDON_PACKAGE_NAME].preferences.lightmap_time)
    except (AttributeError, KeyError, TypeError):
        return "daytime"


def import_water(context) -> bool:
    """Whether to build the water surface on map import.

    On by default and easy to turn off, because where the engine draws
    water is not fully understood. This add-on floods every basin below
    ``WATERLEVEL``, and the original editor shows water in fewer places
    than that — so on some maps it will appear where it should not, and
    a switch is better than a wrong surface in the way.
    """
    try:
        addons = context.preferences.addons
        return bool(addons[ADDON_PACKAGE_NAME].preferences.import_water)
    except (AttributeError, KeyError, TypeError):
        return True


def research_tools_installed() -> bool:
    """Whether this copy has the ``addon.research`` package at all.

    The source repository does; the install archive does not
    (``build_release.py`` leaves it out). Decided by presence on disk,
    so a broken research package is not mistaken for an absent one.
    """
    import importlib.util

    try:
        return importlib.util.find_spec("addon.research") is not None
    except (ImportError, ValueError):
        return False


def show_research_tools(context) -> bool:
    """Whether the Research panel — censuses, forensics, coverage
    reports — is shown in the sidebar. Off: they measure the format
    rather than edit a map, and their reports go to the console. Never
    on in a copy that does not carry the tools."""
    if not research_tools_installed():
        return False
    try:
        addons = context.preferences.addons
        return bool(addons[ADDON_PACKAGE_NAME].preferences.show_research_tools)
    except (AttributeError, KeyError, TypeError):
        return False


def get_naming_style(context) -> str:
    """The configured naming style, or the default if unavailable."""
    try:
        addons = context.preferences.addons
        return addons[ADDON_PACKAGE_NAME].preferences.naming_style
    except (AttributeError, KeyError):
        return "ID_MODEL"


def get_game_root(context) -> str:
    """The configured game folder, or an empty string if unset.

    Reads from add-on preferences, so callers don't need to know where
    the setting lives or handle a missing preferences block (which can
    happen if the add-on is used without being registered normally,
    e.g. from a script).
    """
    try:
        addons = context.preferences.addons
        return addons[ADDON_PACKAGE_NAME].preferences.game_root
    except (AttributeError, KeyError):
        return ""


class EXM_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_PACKAGE_NAME

    game_root: bpy.props.StringProperty(
        name="Game Folder",
        description=(
            "The game's install folder — the one CONTAINING 'data' "
            "(e.g. ...\\GC). Needed to load 3D models, since model paths in "
            "map data are relative to it. A subfolder is accepted and "
            "corrected automatically. Leave empty to import objects as "
            "Empties instead of meshes"
        ),
        subtype="DIR_PATH",
    )

    terrain_detail: bpy.props.BoolProperty(
        name="Ground Detail Tiles",
        description=(
            "Lay level.tile's textures over the terrain. The baked map "
            "alone is one image across the whole level and looks it; "
            "these are where the close-up ground surface comes from"
        ),
        default=True,
    )

    blend_tiles: bpy.props.BoolProperty(
        name="Blend Ground Tiles",
        description=(
            "Blend each ground tile into its neighbours, the way the "
            "engine does, instead of drawing every cell in one texture. "
            "Turn it off to see which tile a cell actually holds"
        ),
        default=True,
    )

    import_grass: bpy.props.BoolProperty(
        name="Import Grass",
        description=(
            "Place the map's grass. A map carries tens of thousands of "
            "tufts, so this is the heaviest single part of an import — "
            "turn it off while working on anything else"
        ),
        default=True,
    )

    import_lighting: bpy.props.BoolProperty(
        name="Import Lighting",
        description=(
            "Build a sun from the map's own SUN_AZIMUTH and ascension, "
            "with the colours it states in MODEL_DIFFUSE and "
            "MODEL_AMBIENT, instead of leaving the scene on Blender's "
            "defaults"
        ),
        default=True,
    )

    road_models: bpy.props.BoolProperty(
        name="Roads From Models (experimental)",
        description=(
            "Place each road's own .gam segments along its chain, as the "
            "engine does, instead of drawing a generated surface. How the "
            "pieces are meant to be spaced and oriented is not yet "
            "established, so this can look worse than the surface"
        ),
        default=False,
    )

    standard_view_transform: bpy.props.BoolProperty(
        name="Standard Colours",
        description=(
            "Switch the scene to the Standard view transform on import. "
            "Blender's default Filmic is built for photographic renders "
            "and washes out game textures, which are already authored for "
            "display"
        ),
        default=True,
    )

    textured_roads: bpy.props.BoolProperty(
        name="Textured Roads",
        description=(
            "Give road chains a width and a surface, instead of drawing "
            "them as bare splines"
        ),
        default=True,
    )

    lightmap_time: bpy.props.EnumProperty(
        name="Terrain Lighting",
        description="Which of the map's lightmaps to light the terrain with",
        items=(
            (
                "none",
                "None",
                "No lighting at all. The original editor draws the ground "
                "unlit, so this is the setting to compare against it",
            ),
            ("daytime", "Day", "The map's daytime lighting, uncompressed"),
            ("sunrisetime", "Sunrise", "The map's sunrise lighting"),
            ("sunsettime", "Sunset", "The map's sunset lighting"),
            ("nighttime", "Night", "The map's night lighting"),
        ),
        default="daytime",
    )

    import_water: bpy.props.BoolProperty(
        name="Import Water",
        description=(
            "Build the water surface from the map's WATERLEVEL. Turn off "
            "if water appears where the original editor shows dry ground "
            "— which basins the engine actually floods is not yet known"
        ),
        default=True,
    )

    naming_style: bpy.props.EnumProperty(
        name="Object Names",
        description=(
            "How imported objects are named in Blender. The name is for "
            "readability only — export always uses the node's real name"
        ),
        items=[
            ("ID_MODEL", "4762_house1", "Node id and model — recommended"),
            ("ID_CLASS_MODEL", "4762_Model_house1", "Node id, class and model"),
            ("ID_ONLY", "4762", "Node id only"),
            ("ORIGINAL", "Object4762", "Exactly as written in world.xml"),
        ],
        default="ID_MODEL",
    )

    show_research_tools: bpy.props.BoolProperty(
        name="Show Research Tools",
        description=(
            "Add a Research panel to the sidebar with the measurement tools "
            "this editor was built with: censuses, model forensics, coverage "
            "and resolution reports. They describe the game's files rather "
            "than edit a map, and print to the system console"
        ),
        default=False,
    )

    log_level: bpy.props.EnumProperty(
        name="Log Level",
        description="Verbosity of EXMeditor's console/status-bar messages",
        items=[
            ("DEBUG", "Debug", "Verbose — every step of every import"),
            ("INFO", "Info", "Normal operation messages"),
            ("WARNING", "Warning", "Only warnings and errors"),
            ("ERROR", "Error", "Only errors"),
        ],
        default="INFO",
        update=_on_log_level_update,
    )

    def draw(self, context) -> None:
        layout = self.layout

        box = layout.box()
        box.label(text="Game Location", icon="FILE_FOLDER")
        box.prop(self, "game_root")
        if not self.game_root:
            box.label(
                text="Not set — map objects will import as Empties, without models",
                icon="INFO",
            )

        box = layout.box()
        box.label(text="Import", icon="IMPORT")
        box.prop(self, "lightmap_time")
        box.prop(self, "terrain_detail")
        box.prop(self, "blend_tiles")
        box.prop(self, "textured_roads")
        box.prop(self, "road_models")
        box.prop(self, "import_lighting")
        box.prop(self, "import_grass")
        box.prop(self, "import_water")
        box.prop(self, "standard_view_transform")
        box.prop(self, "naming_style")

        box = layout.box()
        box.label(text="Interface", icon="WINDOW")
        if research_tools_installed():
            box.prop(self, "show_research_tools")
        box.prop(self, "log_level")


_CLASSES = (EXM_AddonPreferences,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
