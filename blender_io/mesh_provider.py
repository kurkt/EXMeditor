# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Turns an object ``id`` into a Blender mesh, with caching.

A map places the same model many times — one reference map has 1734
objects drawing on ~103 distinct models. Loading and building each
``.gam`` once and sharing the resulting mesh datablock across every
instance keeps import time and memory proportional to the number of
*models*, not the number of *objects*.

A failed lookup is cached too (as ``None``), so an id whose model is
missing doesn't retry the filesystem 500 times.
"""

from __future__ import annotations

import bpy

from blender_io.mesh_bridge import build_model_mesh

#: Custom property on a built mesh, holding the ``.gam`` it was read
#: from.
MODEL_PATH_PROP = "exm_model_path"
from core.coordinates import CoordinateTransform
from formats.exm.gam import read_model
from formats.exm.model_catalog import ModelCatalog, resolve_model_file
from utils.errors import EXMeditorError
from utils.logging import get_logger

logger = get_logger("blender_io.mesh_provider")


class MeshProvider:
    """Resolves object ids to Blender meshes, building each model once."""

    def __init__(
        self,
        catalog: ModelCatalog,
        game_root: str,
        *,
        transform: CoordinateTransform | None = None,
    ) -> None:
        self._catalog = catalog
        self._game_root = game_root
        #: model id -> {LOCATOR NAME: ModelNode}; read once per model.
        self._locators: dict[str, dict] = {}
        self._transform = transform if transform is not None else CoordinateTransform()
        self._cache: dict[str, bpy.types.Mesh | None] = {}
        self.missing_ids: set[str] = set()
        self.failed_ids: set[str] = set()

    @property
    def catalog(self):
        """The model catalogue this provider resolves against.

        Exposed so other layers (the dynamic-object resolver) can look
        up ids against exactly the same catalogue, rather than loading
        a second copy that could disagree.
        """
        return self._catalog

    def get_mesh(self, model_id: str) -> bpy.types.Mesh | None:
        """Return the mesh for ``model_id``, or ``None`` if unavailable.

        ``None`` is a normal outcome, not an error: the catalogue may
        not list the id, the ``.gam`` may not be installed, or it may
        use a vertex format this SDK doesn't parse. The caller falls
        back to an Empty so the object still appears in the right
        place.
        """
        if model_id in self._cache:
            return self._cache[model_id]

        mesh = self._build(model_id)
        self._cache[model_id] = mesh
        return mesh

    def locator(self, model_id: str, name: str):
        """A named ``LP_`` node of a model, or None. Read once, kept."""
        from formats.exm.gam import read_locators

        if model_id not in self._locators:
            found: dict = {}
            entry = self._catalog.get(model_id)
            path = resolve_model_file(entry, self._game_root) if entry else None
            if path:
                try:
                    found = read_locators(path)
                except Exception as exc:  # noqa: BLE001 - a locator is optional
                    logger.debug("no locators read from %s: %s", path, exc)
            self._locators[model_id] = found
        return self._locators[model_id].get(name.upper())

    def _build(self, model_id: str) -> bpy.types.Mesh | None:
        entry = self._catalog.get(model_id)
        if entry is None:
            self.missing_ids.add(model_id)
            return None

        path = resolve_model_file(entry, self._game_root)
        if path is None:
            self.missing_ids.add(model_id)
            return None

        try:
            model = read_model(path)
        except EXMeditorError as exc:
            logger.warning("could not read model %s (%s): %s", model_id, path, exc)
            self.failed_ids.add(model_id)
            return None

        mesh = build_model_mesh(
            model,
            f"ExM_Model_{model_id}",
            transform=self._transform,
            game_root=self._game_root,
        )

        # Remember where it came from. Anything that wants to edit the
        # model later — re-pointing a texture, say — otherwise has to
        # find the map manifest and walk the catalogue again from a
        # context that does not have either.
        if mesh is not None:
            mesh[MODEL_PATH_PROP] = path

        return mesh

    @property
    def loaded_count(self) -> int:
        """How many distinct models were successfully built."""
        return sum(1 for mesh in self._cache.values() if mesh is not None)

    def summary(self) -> str:
        """One-line report for the import operator."""
        parts = [f"{self.loaded_count} models loaded"]
        if self.missing_ids:
            parts.append(f"{len(self.missing_ids)} not found")
        if self.failed_ids:
            parts.append(f"{len(self.failed_ids)} failed to parse")
        return ", ".join(parts)
