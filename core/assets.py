# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The asset library: models with the metadata an editor needs.

``formats/exm/model_catalog.py`` answers one question — where is the
file for this id. That is enough to import a map, and not enough to
edit one: choosing a model to place needs categories to browse,
dimensions to judge scale by, and usage counts to tell a common prop
from a one-off. Replacing a model needs the same. So does validating
that a model id exists.

Three features (an asset browser, model replacement, map validation)
therefore share one dependency, which is why this exists as its own
layer rather than as three partial catalogues that would later have to
be merged.

Extension points, deliberately left as absences rather than empty
fields
----------------------------------------------------------------
Previews, materials and textures belong to a later stage. Their place
in the design is ``AssetInfo.geometry`` and ``AssetInfo.presentation``:
optional sub-objects that are ``None`` until something fills them.
Adding empty ``preview=None, icon=None, materials=[]`` fields now would
be worse than leaving them out — a consumer cannot tell "this asset has
no preview" from "previews are not implemented", and a validator built
on that ambiguity reports nonsense.

No ``bpy`` import.
"""

from __future__ import annotations

import dataclasses
import os
import re

from utils.math import AABB

#: Category names derived from where models live on disk. Real data
#: puts them under data/models/<group>/<region>/<kind>, so the
#: structure the developers used is a better source of categories than
#: a list invented here — it stays correct when a mod adds folders.
_CATEGORY_RULES: tuple[tuple[str, str], ...] = (
    ("buildings", "Buildings"),
    ("nature", "Nature"),
    ("vehicles", "Vehicles"),
    ("monsters", "Characters"),
    ("masks", "Characters"),
    ("guns", "Weapons"),
    ("ammo", "Weapons"),
    ("questitems", "Quest Items"),
    ("dynamic", "Dynamic Objects"),
    ("objects", "Props"),
    ("gadgets", "Props"),
    ("roads", "Roads"),
    ("cliffs", "Terrain Features"),
    ("grass", "Terrain Features"),
    ("prefabs", "Prefabs"),
)

#: Sub-category from the deepest meaningful folder, when there is one.
_SUBCATEGORY_SKIP = re.compile(r"^region\d+$", re.IGNORECASE)

UNCATEGORISED = "Uncategorised"

#: Assets whose catalogue path is absolute rather than game-relative.
#: Real catalogues contain these — models added by hand during modding,
#: pointing at a working folder. They are perfectly usable, so they get
#: their own category instead of being filed under a drive letter.
EXTERNAL_CATEGORY = "External"


@dataclasses.dataclass
class AssetGeometry:
    """Measured facts about an asset's mesh.

    Populated only when the model has actually been read — a library
    can be built without touching the ``.gam`` files, which matters
    because a full game has a thousand of them and an editor should
    open before they are all parsed.
    """

    mesh_count: int = 0
    vertex_count: int = 0
    triangle_count: int = 0
    bounds: AABB | None = None
    has_uvs: bool = False
    has_normals: bool = False

    @property
    def dimensions(self):
        if self.bounds is None:
            return None
        return self.bounds.size()


@dataclasses.dataclass
class AssetInfo:
    """One placeable model, with everything an editor needs to offer it."""

    asset_id: str
    #: Game-root-relative path from the model catalogue.
    file_path: str
    category: str = UNCATEGORISED
    subcategory: str = ""
    display_name: str = ""

    #: Resolved location on disk, when the game folder is known.
    resolved_path: str | None = None

    #: Flags carried by the model catalogue. Kept because they affect
    #: how an asset behaves in-game (impostors are LOD billboards,
    #: passable objects have no collision), and a browser that shows
    #: them saves opening the file to find out.
    shadow: str | None = None
    passable: str | None = None
    use_impostors: str | None = None

    #: Measured from the .gam, or None if it has not been read.
    geometry: AssetGeometry | None = None

    #: How many times the current map places this asset. Zero means
    #: "unused here", not "unusable" — an empty library entry is still
    #: a valid thing to place.
    usage_count: int = 0

    raw_attrs: dict[str, str] = dataclasses.field(default_factory=dict)

    @property
    def is_measured(self) -> bool:
        return self.geometry is not None

    @property
    def full_category(self) -> str:
        return f"{self.category}/{self.subcategory}" if self.subcategory else self.category

    def matches(self, query: str) -> bool:
        """True if ``query`` appears in the id, name or category.

        Case-insensitive substring matching: model ids are terse and
        inconsistent (``r1_Dub_Kust1``, ``big_crag_11``), so anything
        stricter would miss what the user meant.
        """
        needle = query.lower().strip()
        if not needle:
            return True
        return (
            needle in self.asset_id.lower()
            or needle in self.display_name.lower()
            or needle in self.full_category.lower()
        )


class AssetLibrary:
    """Every placeable asset, browsable by category and searchable."""

    def __init__(self) -> None:
        self._assets: dict[str, AssetInfo] = {}

    # --- population ---

    def add(self, asset: AssetInfo) -> None:
        self._assets[asset.asset_id] = asset

    def get(self, asset_id: str) -> AssetInfo | None:
        return self._assets.get(asset_id)

    def __contains__(self, asset_id: str) -> bool:
        return asset_id in self._assets

    def __len__(self) -> int:
        return len(self._assets)

    def all(self) -> list[AssetInfo]:
        return list(self._assets.values())

    # --- browsing ---

    def categories(self) -> dict[str, int]:
        """Category name -> number of assets, most populated first."""
        counts: dict[str, int] = {}
        for asset in self._assets.values():
            counts[asset.category] = counts.get(asset.category, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def subcategories(self, category: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for asset in self._assets.values():
            if asset.category != category or not asset.subcategory:
                continue
            counts[asset.subcategory] = counts.get(asset.subcategory, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def in_category(self, category: str, subcategory: str | None = None) -> list[AssetInfo]:
        results = [a for a in self._assets.values() if a.category == category]
        if subcategory is not None:
            results = [a for a in results if a.subcategory == subcategory]
        return sorted(results, key=lambda a: a.asset_id)

    def search(self, query: str, limit: int | None = None) -> list[AssetInfo]:
        """Assets matching ``query``, most-used first.

        Usage order matters more than alphabetical here: a search for
        "stone" on a real map returns dozens of results, and the one
        placed 132 times is far likelier to be wanted than the one
        placed once.
        """
        results = [a for a in self._assets.values() if a.matches(query)]
        results.sort(key=lambda a: (-a.usage_count, a.asset_id))
        return results[:limit] if limit else results

    def most_used(self, limit: int = 20) -> list[AssetInfo]:
        used = [a for a in self._assets.values() if a.usage_count > 0]
        used.sort(key=lambda a: (-a.usage_count, a.asset_id))
        return used[:limit]

    def unmeasured(self) -> list[AssetInfo]:
        """Assets whose geometry has not been read yet."""
        return [a for a in self._assets.values() if not a.is_measured]


def categorise(file_path: str) -> tuple[str, str]:
    """Derive ``(category, subcategory)`` from a model's path.

    Uses the directory structure the game itself uses rather than a
    fixed list, so a mod that adds folders is categorised sensibly
    instead of landing in a catch-all.
    """
    normalised = file_path.replace("\\", "/").lower()

    # An absolute path (C:/..., /home/...) is outside the game's model
    # tree, so its folders describe someone's machine rather than the
    # asset. Categorising by them produced categories called "C:".
    if re.match(r"^[a-z]:/", normalised) or normalised.startswith("/"):
        return EXTERNAL_CATEGORY, ""

    parts = [p for p in normalised.split("/") if p]

    # Drop the leading data/models, and the filename.
    if len(parts) >= 2 and parts[0] == "data" and parts[1] == "models":
        parts = parts[2:]
    if parts:
        parts = parts[:-1]

    if not parts:
        return UNCATEGORISED, ""

    category = UNCATEGORISED
    for keyword, name in _CATEGORY_RULES:
        if parts[0] == keyword:
            category = name
            break
    else:
        # Unknown top-level folder: use it as the category rather than
        # discarding information into "Uncategorised".
        category = parts[0].replace("_", " ").title()

    # The deepest folder that isn't a region marker makes the best
    # sub-category: "trees" and "stones" are useful, "region1" is not.
    subcategory = ""
    for part in reversed(parts[1:]):
        if not _SUBCATEGORY_SKIP.match(part):
            subcategory = part.replace("_", " ").title()
            break

    return category, subcategory


def display_name_for(asset_id: str) -> str:
    """A readable label for an id like ``r1_Dub_Kust1``."""
    text = re.sub(r"^r\d+_", "", asset_id)      # strip a region prefix
    text = text.replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else asset_id


def build_library(
    model_catalog,
    *,
    game_root: str | None = None,
    usage_counts: dict[str, int] | None = None,
) -> AssetLibrary:
    """Build a library from a model catalogue.

    Geometry is NOT read here. A full game has over a thousand models,
    and parsing them all would make opening the browser slow for a
    benefit most users never need — measurements are filled in on
    demand by :func:`measure_asset`.
    """
    from formats.exm.model_catalog import resolve_model_file

    library = AssetLibrary()
    counts = usage_counts or {}

    for entry in model_catalog.entries():
        category, subcategory = categorise(entry.file_path)
        asset = AssetInfo(
            asset_id=entry.model_id,
            file_path=entry.file_path,
            category=category,
            subcategory=subcategory,
            display_name=display_name_for(entry.model_id),
            shadow=entry.shadow,
            passable=entry.passable,
            use_impostors=entry.use_impostors,
            usage_count=counts.get(entry.model_id, 0),
            raw_attrs=dict(entry.raw_attrs),
        )
        if game_root:
            asset.resolved_path = resolve_model_file(entry, game_root)
        library.add(asset)

    return library


def measure_asset(asset: AssetInfo) -> bool:
    """Read an asset's ``.gam`` and record its geometry.

    Returns ``True`` when measurement succeeded. A failure is not an
    error here — a model may be missing or in a vertex format this SDK
    doesn't parse — and leaves ``geometry`` as ``None``, which the
    browser shows as "not measured" rather than as zero.
    """
    if asset.resolved_path is None or not os.path.isfile(asset.resolved_path):
        return False

    from formats.exm.gam import read_model
    from utils.errors import EXMeditorError

    try:
        model = read_model(asset.resolved_path)
    except EXMeditorError:
        return False

    bounds = None
    for mesh in model.meshes:
        if mesh.stored_bounds is not None:
            bounds = mesh.stored_bounds
            break
    if bounds is None:
        bounds = AABB.from_points(p for mesh in model.meshes for p in mesh.positions)

    asset.geometry = AssetGeometry(
        mesh_count=len(model.meshes),
        vertex_count=model.total_vertices(),
        triangle_count=model.total_triangles(),
        bounds=bounds,
        has_uvs=any(mesh.uvs for mesh in model.meshes),
        has_normals=any(mesh.normals for mesh in model.meshes),
    )
    return True
