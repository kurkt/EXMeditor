# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Mesh geometry model.

Holds what a ``.gam`` mesh chunk contains: interleaved vertices
(position, normal, colour, two UV sets) plus a triangle index buffer.
Game-agnostic and ``bpy``-free — the Blender conversion lives in
``blender_io/mesh_bridge.py``.
"""

from __future__ import annotations

import dataclasses

from utils.math import AABB, Vector3


@dataclasses.dataclass
class MeshData:
    """One mesh: vertices, per-vertex attributes, and triangles.

    All per-vertex lists are the same length. ``triangles`` holds
    index triples into those lists.

    ``uv2`` is a second UV set, present in every mesh examined. It's
    kept separate rather than merged because the two sets carry
    different data (the second is typically a detail/lightmap layer),
    and collapsing them would lose one.
    """

    name: str
    #: Index into the model's ``materials`` list. Read from the mesh
    #: header, not inferred from mesh order — a model may leave some
    #: materials unused, and ``big_flag01`` does.
    material_index: int = 0
    #: Index into the model's node list (chunk 2). Not the same as the
    #: material index — ``machine_house1`` has 30 nodes and 6
    #: materials — and kept because it is what names this mesh.
    node_index: int = 0
    positions: list[Vector3] = dataclasses.field(default_factory=list)
    normals: list[Vector3] = dataclasses.field(default_factory=list)
    #: ``(red, green, blue, alpha)`` in 0..255 — channel order, not the
    #: B, G, R, A the file stores. See ``core.color``.
    colors: list[tuple[int, int, int, int]] = dataclasses.field(default_factory=list)
    uvs: list[tuple[float, float]] = dataclasses.field(default_factory=list)
    uv2s: list[tuple[float, float]] = dataclasses.field(default_factory=list)
    triangles: list[tuple[int, int, int]] = dataclasses.field(default_factory=list)
    #: Bounding box as stored in the file, when present. Verified to
    #: match the actual vertex extents on real data, so it's useful as
    #: an integrity check rather than something to recompute.
    stored_bounds: AABB | None = None

    @property
    def vertex_count(self) -> int:
        return len(self.positions)

    @property
    def triangle_count(self) -> int:
        return len(self.triangles)

    def computed_bounds(self) -> AABB | None:
        """Bounding box derived from the actual vertex positions."""
        return AABB.from_points(self.positions)


@dataclasses.dataclass
class Material:
    """One entry from a model's skin chunk.

    ``shader`` names the engine's shading program
    (``diffuse_detail_vc``, ``bumpdiffuse_envalphagloss_spec``) and
    implies how the textures are used: the first is the base colour in
    every sample examined, later ones are detail, bump or environment
    maps depending on the shader.

    Textures are bare filenames, not paths — where they live is
    resolved separately, since the model does not say.
    """

    name: str = ""
    shader: str = ""
    textures: list[str] = dataclasses.field(default_factory=list)

    #: Slot number per entry in ``textures``, parallel and same length,
    #: or empty when the skin chunk could not be read structurally.
    #: Slot 0 is the diffuse map and slot 1 the bump map, measured on a
    #: shipped ``bump`` model and on HTAToolchain output independently.
    slots: list[int] = dataclasses.field(default_factory=list)

    def texture_for_slot(self, slot: int) -> str | None:
        """The texture in ``slot``, or None if the material has none."""
        for filename, number in zip(self.textures, self.slots):
            if number == slot:
                return filename
        return None

    @property
    def diffuse(self) -> str | None:
        """The base colour texture, or None.

        Taken from slot 0 when the slots are known. Order of appearance
        is the fallback and not the same thing: a filename does not say
        what it is for, and picking the first texture is how a bump map
        ends up rendered as base colour.
        """
        by_slot = self.texture_for_slot(0)
        if by_slot is not None:
            return by_slot
        return self.textures[0] if self.textures else None

    @property
    def bump(self) -> str | None:
        """The bump map, or None."""
        return self.texture_for_slot(1)


@dataclasses.dataclass
class Model:
    """Everything geometric in one ``.gam`` file.

    A file can hold several mesh chunks; every example examined has
    exactly one, but the container format allows more, so this doesn't
    assume a single mesh.
    """

    meshes: list[MeshData] = dataclasses.field(default_factory=list)
    materials: list[Material] = dataclasses.field(default_factory=list)

    def total_vertices(self) -> int:
        return sum(m.vertex_count for m in self.meshes)

    def total_triangles(self) -> int:
        return sum(m.triangle_count for m in self.meshes)
