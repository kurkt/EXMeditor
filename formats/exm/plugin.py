# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Ex Machina game plugin.

Implements ``GamePlugin`` for Ex Machina. File discovery goes through
the ``.ssl`` manifest rather than hardcoded filenames — that's what
makes the plugin work on maps whose files are named differently, and
what fixes case-sensitivity on Linux/macOS (real maps reference
``LevelRoads.xml`` while shipping ``levelroads.xml``; Windows doesn't
care, other platforms do).

Currently loaded: terrain (``displace.bin``) and objects
(``world.xml``). Other recognized files are detected and reported but
not yet parsed.
"""

from __future__ import annotations

import os

from core.manifest import FILE_REFERENCE_KEYS, FIXED_FILENAME_EXCEPTIONS, LevelManifest
from core.objects import KNOWN_NODE_CLASSES, ObjectInstance
from core.scene import MapScene
from formats.exm.ssl import read_manifest, resolve_in_folder
from formats.exm.collision import read_obstacles, write_obstacles
from formats.exm.dynamic_scene import read_dynamic_scene, write_dynamic_scene
from formats.exm.roads import read_roads, write_roads
from formats.exm.raster import read_raster
from formats.exm.tilemap import read_tilemap
from formats.exm.terrain import read_displace, write_displace
from formats.exm.world import read_world, write_world
from formats.plugin import GamePlugin
from utils.errors import EXMeditorError, ValidationError
from utils.logging import get_logger

logger = get_logger("formats.exm.plugin")

#: The manifest key naming the heightmap file.
TERRAIN_KEY = "HIGHMAP"

#: Manifest key for colormap.raw — the terrain colour layer.
COLORMAP_KEY = "COLORMAP"

#: Manifest key for level.tile — the ground texture map.
TILES_KEY = "TILES"

#: Manifest keys for water. It has no file — just a height and a tint.
WATER_LEVEL_KEY = "WATERLEVEL"
WATER_ABSORPTION_KEYS = ("WATERABSRED", "WATERABSGREEN", "WATERABSBLUE")

#: ``world.xml`` is never referenced by any manifest key — confirmed by
#: its absence from the full key list of a real ``.ssl``. It's found by
#: fixed filename instead (see ``core.manifest.FIXED_FILENAME_EXCEPTIONS``).
WORLD_FILENAME = "world.xml"

#: The manifest key naming the road layout file.
ROADS_KEY = "ROADMAP"

#: The manifest key naming the static collision file.
OBSTACLES_KEY = "STATICOBSTACLES"

#: The manifest key naming the second placement layer.
DYNAMIC_SCENE_KEY = "SERVERDYN"


def find_map_folder(manifest_path: str) -> str | None:
    """Return the folder holding a map's data, given its ``.ssl`` file.

    The inverse of :func:`find_manifest`, for when the user picks the
    manifest rather than the folder. Two layouts, matching the two that
    ``find_manifest`` handles:

    1. The real game's: a folder beside the manifest, named after it::

           data/maps/
               r1m1-1-1.ssl        <- the file picked
               r1m1-1-1/           <- the data folder

    2. Flat: the manifest sits among the data files, so its own folder
       is the map folder (this is what an exported map looks like).

    Returns ``None`` if the path isn't a file. Case-insensitive, since
    map data is inconsistently cased and only Windows forgives that.
    """
    if not os.path.isfile(manifest_path):
        return None

    parent = os.path.dirname(os.path.abspath(manifest_path))
    stem = os.path.splitext(os.path.basename(manifest_path))[0]

    # Layout 1: a sibling folder named after the manifest.
    try:
        for name in os.listdir(parent):
            if name.lower() == stem.lower():
                candidate = os.path.join(parent, name)
                if os.path.isdir(candidate):
                    return candidate
    except OSError:
        return None

    # Layout 2: the manifest lives among the data files.
    return parent


def find_manifest(folder: str) -> str | None:
    """Return the path of the map's ``.ssl`` file, or ``None``.

    Two layouts are supported, because the real game uses the second
    one and a flat export folder uses the first:

    1. Inside the map folder::

           r1m1-1-1/
               r1m1-1-1.ssl
               displace.bin

    2. **Beside** the map folder, named after it — this is the real
       game's layout::

           data/maps/
               r1m1-1-1.ssl        <- manifest
               r1m1-1-1/           <- the folder the user selects
                   displace.bin

    Case-insensitive throughout, for the same reason file lookup is
    (see ``formats/exm/ssl.py``): Windows doesn't care about casing
    and the shipped files aren't consistent, but Linux/macOS do care.
    """
    folder = os.path.normpath(folder)
    folder_name = os.path.basename(folder)

    # Layout 1: a .ssl directly inside the selected folder. Prefer one
    # named after the folder; fall back to any single .ssl present.
    #
    # The "named after the folder" preference matters: a folder holding
    # SEVERAL maps (e.g. .../data/maps/, containing r1m1-1-1.ssl next to
    # r1m1-1-1/, r1m2-1-1.ssl next to r1m2-1-1/, ...) would otherwise
    # match whichever .ssl sorted first and try to load that map's
    # manifest against the wrong directory.
    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return None
    ssl_files = [n for n in entries if n.lower().endswith(".ssl")]
    for name in ssl_files:
        if name.lower() == f"{folder_name}.ssl".lower():
            return os.path.join(folder, name)
    if len(ssl_files) == 1:
        return os.path.join(folder, ssl_files[0])
    if len(ssl_files) > 1:
        # Several maps' manifests in one folder — this is the "all maps"
        # directory, not a single map. Refuse rather than pick one.
        return None

    # Layout 2: a .ssl beside the folder, named after it.
    parent = os.path.dirname(folder)
    if not parent or not folder_name:
        return None
    expected = f"{folder_name}.ssl".lower()
    try:
        siblings = os.listdir(parent)
    except OSError:
        return None
    for name in siblings:
        if name.lower() == expected:
            return os.path.join(parent, name)

    return None



def world_fingerprint(path: str) -> str:
    """A cheap identity for a ``world.xml`` on disk.

    Size and modification time, not a hash: the file runs to megabytes
    and this is checked on every export. Either changing is enough to
    know it is not the file that was loaded.
    """
    try:
        stat = os.stat(path)
    except OSError:
        return ""
    return f"{stat.st_size}:{int(stat.st_mtime)}"


def _refuse_stale_overwrite(path: str, loaded: str | None) -> None:
    """Stop an export that would silently discard someone else's work.

    write_world() reproduces the scene, not the file — a node the
    Blender scene does not hold is a node the written file will not
    hold. That is correct when the scene is current and destructive
    when it is not, and the two are indistinguishable from inside.

    Seen in practice: a node written by the SDK, a save from the game's
    own editor that dropped it, an export from a Blender scene imported
    before that save, and the editor's node gone too. Neither tool
    merges, so whoever writes last wins, and each round of the
    investigation was measuring a file that had changed underneath it.

    A fingerprint recorded at import makes the difference visible. This
    only refuses; merging the two files is the real fix and a larger
    piece of work.
    """
    if not loaded or not os.path.isfile(path):
        return
    current = world_fingerprint(path)
    if current and current != loaded:
        raise ValidationError(
            "world.xml has changed on disk since this map was imported — "
            "something else has written it, most likely the game's own "
            "editor. Exporting now would replace its version with this "
            "scene and lose whatever it added. Re-import the map, redo the "
            "Blender-side changes, and export again."
        )


def _output_filename(folder: str, preferred: str) -> str:
    """Return the name to write ``preferred`` as, inside ``folder``.

    If a case-variant of the file already exists there, reuse that exact
    name. Writing "LevelRoads.xml" next to an existing "levelroads.xml"
    would create a SECOND file on a case-sensitive filesystem, leaving
    the game reading the old one and the edits silently ignored.
    """
    existing = resolve_in_folder(folder, preferred)
    if existing is not None:
        return os.path.basename(existing)
    return preferred


def _game_root_above(folder: str) -> str:
    """Walk up from a map folder to the one holding ``data``.

    The plugin is handed a map folder, not a game root, and the
    editor's working copy of the level sits under ``data/editor`` —
    somewhere else entirely.
    """
    current = os.path.abspath(folder) if folder else ""
    while current:
        if os.path.isdir(os.path.join(current, "data")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return ""
        current = parent
    return ""


class ExMachinaPlugin(GamePlugin):
    """``GamePlugin`` implementation for Ex Machina."""

    name = "Ex Machina"
    # Format versioning isn't confirmed to matter yet — placeholder
    # until a real version difference is observed in the wild.
    format_version = "UNKNOWN"

    # --- discovery ---

    def scan(self, folder: str) -> dict[str, bool]:
        """Report which of this map's files are present.

        Keys are manifest keys (``"HIGHMAP"``, ``"ROADMAP"``, ...) for
        manifest-referenced files, plus literal filenames for the
        fixed-name exceptions. Returns an empty dict if there's no
        ``.ssl`` at all, since without it there's no map to describe.
        """
        manifest_path = find_manifest(folder)
        if manifest_path is None:
            return {}
        try:
            manifest = read_manifest(manifest_path)
        except EXMeditorError as exc:
            logger.error("could not read manifest %s: %s", manifest_path, exc)
            return {}
        return self._scan_with_manifest(folder, manifest)

    def _scan_with_manifest(self, folder: str, manifest: LevelManifest) -> dict[str, bool]:
        found: dict[str, bool] = {}
        for key in FILE_REFERENCE_KEYS:
            raw_value = (manifest.file_ref(key) or "").strip()
            if not raw_value:
                continue  # key absent or blank — nothing to look for
            filename = os.path.basename(raw_value.replace("\\", "/"))
            found[key] = resolve_in_folder(folder, filename) is not None
        for filename in FIXED_FILENAME_EXCEPTIONS:
            found[filename] = resolve_in_folder(folder, filename) is not None
        return found

    # --- load ---

    def load(self, folder: str) -> MapScene:
        """Read every supported file in ``folder`` into one ``MapScene``.

        Missing or unsupported components become ``MapScene.warnings``
        entries rather than exceptions, so a partially-supported map
        still produces a usable scene.
        """
        scene = MapScene(source_dir=folder)

        manifest_path = find_manifest(folder)
        if manifest_path is None:
            folder_name = os.path.basename(os.path.normpath(folder)) or "<map>"
            scene.warnings.append(
                f"No .ssl manifest found. Expected either a .ssl inside the "
                f"selected folder, or '{folder_name}.ssl' beside it. Select the "
                f"map's own folder (e.g. .../maps/{folder_name}/), not the "
                f"folder that contains all maps."
            )
            return scene

        try:
            scene.manifest = read_manifest(manifest_path)
        except EXMeditorError as exc:
            scene.warnings.append(f"Could not read map manifest: {exc.message}")
            return scene

        found = self._scan_with_manifest(folder, scene.manifest)

        self._load_terrain(folder, scene, found)
        self._load_objects(folder, scene, found)
        self._load_roads(folder, scene, found)
        self._load_obstacles(folder, scene, found)
        self._load_dynamic_scene(folder, scene, found)

        # Everything else the manifest points at, detected but not yet
        # parsed. Reported once as a summary rather than one warning per
        # file, which would drown out the actionable messages above.
        unparsed = sorted(
            key for key, present in found.items()
            if present and key not in (
                TERRAIN_KEY, WORLD_FILENAME, ROADS_KEY, OBSTACLES_KEY, DYNAMIC_SCENE_KEY,
            )
        )
        if unparsed:
            scene.warnings.append(
                f"{len(unparsed)} further map files detected but not yet supported: "
                + ", ".join(unparsed)
            )

        return scene

    def _load_terrain(self, folder: str, scene: MapScene, found: dict[str, bool]) -> None:
        if not found.get(TERRAIN_KEY):
            scene.warnings.append("Map incomplete: terrain not found (no HIGHMAP file)")
            return
        assert scene.manifest is not None  # set by load() before this is called
        raw_value = scene.manifest.file_ref(TERRAIN_KEY) or ""
        path = resolve_in_folder(folder, os.path.basename(raw_value.replace("\\", "/")))
        if path is None:
            scene.warnings.append("Terrain not loaded: HIGHMAP file disappeared during load")
            return
        try:
            scene.terrain = read_displace(path)
        except EXMeditorError as exc:
            logger.error("failed to read terrain %s: %s", path, exc)
            scene.warnings.append(f"Terrain not loaded: {exc.message}")
            return

        scene.map_folder = folder
        self._load_colormap(folder, scene)
        self._load_tilemap(folder, scene)
        self._load_water(scene)

        from core.lighting import read_lighting

        scene.lighting = read_lighting(scene.manifest)

        from formats.exm.grass import find_grass, read_grass

        grass_path = find_grass(folder)
        if grass_path:
            try:
                scene.grass = read_grass(grass_path)
            except EXMeditorError as exc:
                logger.warning("failed to read %s: %s", grass_path, exc)
                scene.warnings.append(f"Grass not loaded: {exc.message}")

    def _load_colormap(self, folder: str, scene: MapScene) -> None:
        """Read colormap.raw, if the manifest names one.

        A map without it is normal, not an error — the terrain simply
        arrives uncoloured. A map with one that fails to parse is worth
        a warning, since a size mismatch means either an unseen map
        variant or the wrong file.
        """
        assert scene.manifest is not None
        raw_value = scene.manifest.file_ref(COLORMAP_KEY)
        if not raw_value:
            return

        path = resolve_in_folder(
            folder, os.path.basename(raw_value.replace("\\", "/"))
        )
        if path is None:
            return

        try:
            scene.colormap = read_raster(path, COLORMAP_KEY)
        except EXMeditorError as exc:
            logger.warning("failed to read colormap %s: %s", path, exc)
            scene.warnings.append(f"Terrain colour not loaded: {exc.message}")

    def _load_tilemap(self, folder: str, scene: MapScene) -> None:
        """Read level.tile, if the manifest names one.

        Not fatal when absent or unreadable: the terrain geometry is
        still worth having untextured, and a tile file that does not
        match what was measured is a finding to report rather than a
        reason to lose the map.
        """
        assert scene.manifest is not None
        raw_value = scene.manifest.file_ref(TILES_KEY)
        if not raw_value:
            # Said out loud. A map with no ground textures and a map
            # whose ground textures were switched off look identical
            # once the import is over, and the difference is not
            # something the user can be asked to guess.
            logger.info("the manifest names no tile map; the ground gets none")
            return

        name = os.path.basename(raw_value.replace("\\", "/"))
        path = resolve_in_folder(folder, name)
        if path is None:
            logger.warning(
                "the manifest names %s and it is not in %s; "
                "the ground will have no tile textures", name, folder,
            )
            return

        try:
            scene.tilemap = read_tilemap(path)
        except EXMeditorError as exc:
            logger.warning("failed to read tile map %s: %s", path, exc)
            scene.warnings.append(f"Ground textures not loaded: {exc.message}")

    def _load_water(self, scene: MapScene) -> None:
        """Read the water level and, if the map has one, its water map.

        Water is **not** a consequence of height. ``Landscape::Load()``
        opens a watermap and, failing to, reports "Cannot open
        watermap: ... using empty waterfield" — an empty field, not a
        flooded one. A level with no watermap has no water at all,
        however far its ground drops below ``WATERLEVEL``.

        That is the whole of "water appears where it should not": this
        map ships no watermap, so the correct amount of water on it is
        none, and every basin filled from the heightfield was invented.

        The format, from the same function::

            cells      = (tile grid size x 4) squared
            cell       = int16; 0 is dry, non-zero is water
            legacy     = 1 byte per cell, 0xFF meaning water, converted
                         on load to BASEWATERLEVEL x a scale constant

        Which also settles what ``BASEWATERLEVEL`` is for: it is the
        stand-in height used when converting that older one-byte form,
        not a tide and not a shoreline mark.
        """
        assert scene.manifest is not None

        raw_level = scene.manifest.file_ref(WATER_LEVEL_KEY)
        if not raw_level:
            return
        try:
            scene.water_level = float(raw_level)
        except ValueError:
            logger.warning("WATERLEVEL is not a number: %r", raw_level)
            return

        absorption = []
        for key in WATER_ABSORPTION_KEYS:
            raw = scene.manifest.file_ref(key)
            try:
                absorption.append(float(raw))
            except (TypeError, ValueError):
                absorption = []
                break
        if len(absorption) == 3:
            scene.water_absorption = tuple(absorption)

        scene.water_map = self._load_water_map(scene, _game_root_above(scene.map_folder))
        if scene.water_map is None:
            logger.info(
                "no watermap on this map: the engine draws an empty "
                "waterfield, so there is no water to build"
            )

    def _load_water_map(self, scene: MapScene, game_root: str = ""):
        """The map's watermap, if it ships one.

        Looked for beside the map AND under ``data/editor``, which is
        the working folder of whatever level the editor has open — it
        holds that level's ``level.tile``, ``colormap.raw``,
        ``water.raw`` and ``tilemap.xml``. Searching only the map
        folder found nothing and reported the map as having no water,
        which is how a map that plainly has water came to have none.

        The size confirms the grid on the sample map::

            LEVELSIZE = 32  ->  (32 x 4)^2 = 128 x 128 cells
            128 x 128 x 2 bytes = 32768 = the size of water.raw

        the same 128 x 128 grid ``level.tile`` uses.
        """
        folders = [scene.map_folder]
        if game_root:
            folders.append(os.path.join(game_root, "data", "editor"))

        for folder in folders:
            found = self._water_map_in(folder)
            if found is not None:
                return found
        logger.info(
            "no watermap in %s", " or ".join(f for f in folders if f) or "(nowhere)"
        )
        return None

    def _water_map_in(self, folder: str):
        if not folder or not os.path.isdir(folder):
            return None

        for name in sorted(os.listdir(folder)):
            if name.lower() not in ("water.raw", "watermap.raw", "water.bin"):
                continue
            path = os.path.join(folder, name)
            try:
                data = open(path, "rb").read()
            except OSError as exc:
                logger.warning("could not read %s: %s", path, exc)
                return None

            from core.raster import RasterLayer

            # int16 per cell in the current form, one byte in the old
            # one. Which it is follows from the size against the grid,
            # exactly as the engine decides it.
            for bytes_per in (2, 1):
                cells = len(data) // bytes_per
                side = int(cells ** 0.5)
                if side * side == cells and side > 1:
                    logger.info(
                        "watermap %s: %sx%s, %s byte(s) per cell",
                        name, side, side, bytes_per,
                    )
                    return RasterLayer(
                        width=side, height=side,
                        bytes_per_sample=bytes_per, data=data,
                    )

            logger.warning("%s is %s bytes, which is no square grid", name, len(data))
            return None

        return None

    def _load_objects(self, folder: str, scene: MapScene, found: dict[str, bool]) -> None:
        if not found.get(WORLD_FILENAME):
            scene.warnings.append("No world.xml found — map has no placed objects")
            return
        path = resolve_in_folder(folder, WORLD_FILENAME)
        if path is None:
            scene.warnings.append("Objects not loaded: world.xml disappeared during load")
            return
        try:
            objects, root_attributes = read_world(path, unknown=scene.unknown_nodes)
        except EXMeditorError as exc:
            logger.error("failed to read %s: %s", path, exc)
            scene.warnings.append(f"Objects not loaded: {exc.message}")
            return
        scene.objects = objects
        # Recorded at load, checked at export: the only way to tell a
        # scene that is current from one whose file has moved on.
        scene.world_fingerprint = world_fingerprint(path)
        scene.world_root_attributes = root_attributes
        logger.info("Loaded %d objects (%d top-level)", scene.object_count(), len(objects))

        if scene.unknown_nodes:
            report = scene.unknown_nodes
            names = ", ".join(sorted(report.classes))
            scene.warnings.append(
                f"{report.total_skipped} object(s) skipped: unsupported node "
                f"class(es) {names}. Run Diagnose Model Resolution for details."
            )

    def _load_roads(self, folder: str, scene: MapScene, found: dict[str, bool]) -> None:
        if not found.get(ROADS_KEY):
            return  # a map with no roads is perfectly normal
        assert scene.manifest is not None
        raw_value = scene.manifest.file_ref(ROADS_KEY) or ""
        path = resolve_in_folder(folder, os.path.basename(raw_value.replace("\\", "/")))
        if path is None:
            scene.warnings.append("Roads not loaded: ROADMAP file disappeared during load")
            return
        try:
            scene.roads = read_roads(path)
        except EXMeditorError as exc:
            logger.error("failed to read roads %s: %s", path, exc)
            scene.warnings.append(f"Roads not loaded: {exc.message}")
            return
        logger.info(
            "Loaded %d roads (%d nodes)", len(scene.roads.chains), scene.roads.node_count()
        )

    def _load_obstacles(self, folder: str, scene: MapScene, found: dict[str, bool]) -> None:
        if not found.get(OBSTACLES_KEY):
            return  # a map with no static collision is valid
        assert scene.manifest is not None
        raw_value = scene.manifest.file_ref(OBSTACLES_KEY) or ""
        path = resolve_in_folder(folder, os.path.basename(raw_value.replace("\\", "/")))
        if path is None:
            scene.warnings.append("Obstacles not loaded: file disappeared during load")
            return
        try:
            scene.obstacles = read_obstacles(path)
        except EXMeditorError as exc:
            logger.error("failed to read obstacles %s: %s", path, exc)
            scene.warnings.append(f"Obstacles not loaded: {exc.message}")
            return
        logger.info("Loaded %d collision boxes", len(scene.obstacles))

    def _load_dynamic_scene(self, folder: str, scene: MapScene, found: dict[str, bool]) -> None:
        if not found.get(DYNAMIC_SCENE_KEY):
            return
        assert scene.manifest is not None
        raw_value = scene.manifest.file_ref(DYNAMIC_SCENE_KEY) or ""
        path = resolve_in_folder(folder, os.path.basename(raw_value.replace("\\", "/")))
        if path is None:
            return
        try:
            scene.dynamic_scene = read_dynamic_scene(path)
        except EXMeditorError as exc:
            logger.error("failed to read dynamicscene %s: %s", path, exc)
            scene.warnings.append(f"Dynamic objects not loaded: {exc.message}")
            return
        logger.info(
            "Loaded %d dynamic objects (%d placed)",
            scene.dynamic_scene.object_count(), len(scene.dynamic_scene.placed()),
        )

    # --- save ---

    def save(self, scene: MapScene, folder: str) -> None:
        """Write ``scene`` back out into ``folder``.

        Only writes what this SDK understands; every other map file is
        expected to already be in ``folder`` (the export operator
        snapshots the source folder first — see ``core.snapshot``), so
        an unmodified file survives simply by not being touched here.

        ``scene`` must already be in game units — converting from
        Blender units is the caller's job (via
        ``core.coordinates.CoordinateTransform``), not the plugin's.
        """
        if scene.terrain is not None:
            write_displace(
                os.path.join(folder, _output_filename(folder, "displace.bin")), scene.terrain
            )

        if scene.roads is not None and scene.roads.chains:
            write_roads(
                os.path.join(folder, self._roads_filename(scene, folder)), scene.roads
            )

        if scene.obstacles is not None and scene.obstacles.boxes:
            write_obstacles(
                os.path.join(folder, self._obstacles_filename(scene, folder)), scene.obstacles
            )

        if scene.dynamic_scene is not None and scene.dynamic_scene.objects:
            write_dynamic_scene(
                os.path.join(folder, self._dynamic_scene_filename(scene, folder)),
                scene.dynamic_scene,
            )

        if scene.objects:
            if scene.world_root_attributes is None:
                raise ValidationError(
                    "cannot write world.xml: the scene has objects but no "
                    "original <World> attributes to preserve (was it loaded "
                    "from a map?)"
                )
            world_path = os.path.join(
                folder, _output_filename(folder, WORLD_FILENAME)
            )
            _refuse_stale_overwrite(world_path, scene.world_fingerprint)
            write_world(world_path, scene.objects, scene.world_root_attributes)

    def _roads_filename(self, scene: MapScene, folder: str) -> str:
        """The name to write the road file as: whatever the manifest
        calls it, resolved to the actual on-disk casing if present."""
        preferred = "LevelRoads.xml"
        if scene.manifest is not None:
            raw_value = scene.manifest.file_ref(ROADS_KEY)
            if raw_value:
                preferred = os.path.basename(raw_value.replace("\\", "/"))
        return _output_filename(folder, preferred)

    def _obstacles_filename(self, scene: MapScene, folder: str) -> str:
        preferred = "static_obstacles.xml"
        if scene.manifest is not None:
            raw_value = scene.manifest.file_ref(OBSTACLES_KEY)
            if raw_value:
                preferred = os.path.basename(raw_value.replace("\\", "/"))
        return _output_filename(folder, preferred)

    def _dynamic_scene_filename(self, scene: MapScene, folder: str) -> str:
        preferred = "DynamicScene.xml"
        if scene.manifest is not None:
            raw_value = scene.manifest.file_ref(DYNAMIC_SCENE_KEY)
            if raw_value:
                preferred = os.path.basename(raw_value.replace("\\", "/"))
        return _output_filename(folder, preferred)

    def saved_filenames(self, scene: MapScene, folder: str | None = None) -> set[str]:
        """Names of the files ``save()`` would write for this scene.

        Used by the export operator to tell ``core.snapshot`` which
        files are expected to differ from the source (everything else
        must survive byte-identical), and which to back up before an
        in-place export.

        ``folder`` resolves each name to its actual on-disk casing, so
        the names returned match the files ``save()`` will really
        touch. Omit it only when no folder is known yet.
        """
        names: set[str] = set()
        if scene.terrain is not None:
            names.add(
                _output_filename(folder, "displace.bin") if folder else "displace.bin"
            )
        if scene.objects:
            names.add(
                _output_filename(folder, WORLD_FILENAME) if folder else WORLD_FILENAME
            )
        if scene.roads is not None and scene.roads.chains:
            if folder:
                names.add(self._roads_filename(scene, folder))
            else:
                names.add("LevelRoads.xml")
        if scene.dynamic_scene is not None and scene.dynamic_scene.objects:
            if folder:
                names.add(self._dynamic_scene_filename(scene, folder))
            else:
                names.add("DynamicScene.xml")
        if scene.obstacles is not None and scene.obstacles.boxes:
            if folder:
                names.add(self._obstacles_filename(scene, folder))
            else:
                names.add("static_obstacles.xml")
        return names

    # --- validate ---

    def validate(self, scene: MapScene) -> list[str]:
        problems: list[str] = []
        if scene.terrain is not None:
            # Squareness is the one dimension constraint actually
            # confirmed — there is no fixed size.
            if scene.terrain.width != scene.terrain.height:
                problems.append(
                    "terrain grid is not square (width "
                    f"{scene.terrain.width} != height {scene.terrain.height}) "
                    "— displace.bin requires a square grid"
                )
            try:
                scene.terrain.validate()
            except EXMeditorError as exc:
                problems.append(str(exc))

        if scene.objects and scene.world_root_attributes is None:
            problems.append(
                "scene has objects but no original <World> attributes — "
                "world.xml cannot be written losslessly"
            )

        for obj in scene.walk_objects():
            if not isinstance(obj, ObjectInstance):  # defensive: wrong type in the tree
                problems.append(f"non-ObjectInstance found in object tree: {obj!r}")
                break

        problems.extend(self._check_node_integrity(scene))
        return problems

    @staticmethod
    def _check_node_integrity(scene: MapScene) -> list[str]:
        """Report nodes the game could not use.

        Deliberately narrow. An earlier version also rejected duplicate
        node names, reasoning that other files reference nodes by name —
        but a real shipped map contains 84 nodes called "noise" and
        several repeated ``ObjectN`` names, and the game loads it fine.
        Refusing to export an unmodified map that the game itself
        accepts is a worse failure than the one being guarded against,
        so only genuinely unusable nodes are reported.
        """
        problems: list[str] = []

        for instance in scene.walk_objects():
            if not instance.name:
                problems.append(
                    f"a {instance.node_class} node has no name — nodes are "
                    "referenced by name and an empty one cannot be resolved"
                )
                continue

            if instance.node_class not in KNOWN_NODE_CLASSES:
                problems.append(
                    f"node {instance.name!r} has unsupported class "
                    f"{instance.node_class!r}"
                )
            elif instance.node_class in ("SgAnimatedModelNode", "SgGameUnitNode"):
                if not instance.asset_id:
                    problems.append(
                        f"node {instance.name!r} is a {instance.node_class} but has "
                        "no model id — the game would have nothing to draw"
                    )

        return problems
