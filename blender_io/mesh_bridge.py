# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""MeshData -> Blender mesh.

Builds real geometry from a parsed ``.gam`` model, applying the same
shared coordinate transform every other spatial format uses so models
sit in the same space as terrain, objects and roads.

Import only: writing geometry back into a ``.gam`` would require
regenerating the material, shadow and tool-metadata chunks, which
aren't understood. Objects can still be moved, rotated and deleted —
that's ``world.xml``'s job, and it round-trips fully.
"""

from __future__ import annotations

import bpy

from core import color as color_codec
from core.coordinates import CoordinateTransform
from core.mesh import MeshData, Model
from utils.logging import get_logger

logger = get_logger("blender_io.mesh_bridge")

#: Name of the UV layer created for the primary UV set.
UV_LAYER = "UVMap"
#: Second UV set — present in every mesh seen; typically a detail or
#: lightmap layer.
UV2_LAYER = "UVMap2"
#: Vertex colour layer. The values are greyscale with alpha always 255
#: on the models examined, consistent with baked ambient occlusion.
COLOR_LAYER = "Col"

MESH_SOURCE_PROP = "exm_mesh_source"


def build_model_mesh(
    model: Model,
    name: str,
    *,
    transform: CoordinateTransform | None = None,
    game_root: str | None = None,
) -> bpy.types.Mesh:
    """Build one Blender mesh from a model's geometry.

    A ``.gam`` file may hold several mesh chunks; they're merged into
    one Blender mesh, with index offsets adjusted, since they're parts
    of a single model rather than independent objects.
    """
    transform = transform if transform is not None else CoordinateTransform()

    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    normals: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    uv2s: list[tuple[float, float]] = []
    colors: list[tuple[float, float, float, float]] = []

    vertex_materials: list[int] = []
    # Padding keeps these lists indexable by vertex, but padding alone
    # must not conjure a layer out of nothing: a model with no second UV
    # set would otherwise get one full of zeros. See below.
    any_uv2 = False
    any_color = False

    for mesh_data in model.meshes:
        offset = len(vertices)
        for position in mesh_data.positions:
            # Deliberately the OFFSET conversion, not the position one:
            # these coordinates are relative to the model's own origin,
            # and the object is placed separately. Running them through
            # the position conversion would subtract the map-centring
            # offset from every vertex, tearing each model away from the
            # object that carries it.
            converted = transform.game_to_blender_offset(position)
            vertices.append(converted.as_tuple())
        for normal in mesh_data.normals:
            # Normals are directions: they get the axis swap but must
            # NOT be scaled, or non-uniform scale factors would skew
            # them away from unit length.
            normals.append((normal.x, normal.z, normal.y))
        # Padded to the vertex count, not merely extended. These lists
        # are indexed BY vertex index later, so a mesh that carries no
        # UVs (or no colours) would otherwise shift every later mesh's
        # attributes by its own vertex count — the textures would slide
        # and nothing would say why.
        _extend_aligned(uvs, mesh_data.uvs, len(mesh_data.positions), (0.0, 0.0))
        _extend_aligned(uv2s, mesh_data.uv2s, len(mesh_data.positions), (0.0, 0.0))
        _extend_aligned(
            colors,
            [color_codec.to_float(c) for c in mesh_data.colors],
            len(mesh_data.positions),
            (1.0, 1.0, 1.0, 1.0),
        )
        any_uv2 = any_uv2 or bool(mesh_data.uv2s)
        any_color = any_color or bool(mesh_data.colors)
        faces.extend(
            (a + offset, b + offset, c + offset) for a, b, c in mesh_data.triangles
        )
        # Per VERTEX, not per face. Blender may not keep every face it
        # is given, so a face-indexed list cannot be relied on after
        # construction — see _assign_face_materials. Each source mesh
        # owns a contiguous vertex range, so the vertex answers the
        # question exactly.
        vertex_materials.extend(
            [mesh_data.material_index] * len(mesh_data.positions)
        )

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()

    # Driven by the mesh Blender actually built, not by the face list it
    # was handed. Blender does not keep every face it is given —
    # duplicates are the common case, and models carry plenty:
    # bridge_concrete has 386 of 4345, big_flag01 56 of 346. Walking the
    # input list to number the loops then runs one loop ahead of reality
    # from the first dropped face onward, and every UV after it lands on
    # the wrong vertex. The result is a model whose UVs collapse toward
    # a point, which renders as a flat wash of the texture's average
    # colour — a material that plainly holds the right texture and shows
    # none of it.
    #
    # Reading the topology back makes all of that irrelevant.
    _apply_uv_layer(mesh, UV_LAYER, uvs, active_render=True)

    # Only when the model actually carries a second UV set. Padding made
    # ``uv2s`` a full-length list of (0, 0) for every model, which built
    # a second UV layer of zeros on all of them — and, being created
    # second, it took the active_render flag and became the layer
    # Blender renders with. Every UV collapsed to a single texel, and
    # each texture rendered as a flat wash of its own average colour:
    # a material visibly holding the right image and showing none of it.
    #
    # Only type 15 carries a second UV set at all; types 7 and 8, which
    # are most of the game, do not.
    if any_uv2:
        _apply_uv_layer(mesh, UV2_LAYER, uv2s, active_render=False)
    if any_color:
        _apply_vertex_colors(mesh, colors)

    _make_primary_uv_active(mesh)

    if normals and len(normals) == len(vertices):
        # Custom split normals preserve the model's own shading rather
        # than letting Blender recompute it, which would flatten the
        # authored hard/soft edge distinction.
        try:
            mesh.normals_split_custom_set_from_vertices(normals)
        except (AttributeError, RuntimeError):
            # Not fatal: the mesh is still correct geometry, just with
            # Blender's own normals.
            pass

    if game_root is not None and model.materials:
        from blender_io.texture_bridge import apply_materials

        attached = apply_materials(mesh, model, game_root)
        _assign_face_materials(mesh, vertex_materials, attached)

    return mesh


def _make_primary_uv_active(mesh: bpy.types.Mesh) -> None:
    """Point the ACTIVE UV layer at the diffuse one.

    ``active_render`` and ``active`` are different flags for different
    jobs. Renders follow ``active_render``; Texture Paint and the
    viewport's texture display follow ``active``, and a layer created
    second takes that one by default.

    So a model with a lightmap unwrap ends up painting and previewing
    through the lightmap's coordinates — every face sampling a small
    unrelated patch of the diffuse map. On a wall of a few hundred
    faces that reads as coloured noise, while the image itself is
    perfectly intact in the Image Editor. Setting both flags is what
    keeps the two views agreeing.
    """
    layers = getattr(mesh, "uv_layers", None)
    if not layers:
        return

    for index, layer in enumerate(layers):
        if getattr(layer, "name", "") != UV_LAYER:
            continue
        try:
            layers.active_index = index
        except (AttributeError, TypeError):
            try:
                layers.active = layer
            except (AttributeError, TypeError) as exc:
                logger.debug("could not make %s the active UV layer: %s", UV_LAYER, exc)
        return


def _extend_aligned(target: list, values: list, count: int, filler) -> None:
    """Append ``values``, padded or trimmed to exactly ``count`` entries.

    Keeps every per-vertex list the same length as the vertex list, so
    ``list[vertex_index]`` stays meaningful once several meshes have
    been merged.
    """
    if len(values) == count:
        target.extend(values)
        return
    target.extend(values[:count])
    target.extend([filler] * max(0, count - len(values)))


def _assign_face_materials(
    mesh: bpy.types.Mesh, vertex_materials: list[int], slot_count: int
) -> None:
    """Point each polygon at the material its source mesh named.

    Looked up through the polygon's own first vertex. Source meshes own
    disjoint vertex ranges, so any vertex of a face identifies which
    mesh the face came from — and unlike a position in the input face
    list, that stays true however many faces Blender declined to keep.

    An index outside the attached slots is clamped rather than raising:
    a model naming a material the skin chunk does not contain is a
    finding about that file, not a reason to lose its geometry.
    """
    if not vertex_materials or slot_count <= 0:
        return

    clamped = 0
    for polygon in mesh.polygons:
        vertices = tuple(polygon.vertices)
        if not vertices or vertices[0] >= len(vertex_materials):
            continue
        index = vertex_materials[vertices[0]]
        if not 0 <= index < slot_count:
            clamped += 1
            index = 0
        polygon.material_index = index

    if clamped:
        logger.warning(
            "%s polygon(s) named a material outside the %s the model declares",
            clamped, slot_count,
        )


def _apply_uv_layer(
    mesh: bpy.types.Mesh,
    layer_name: str,
    uvs: list[tuple[float, float]],
    active_render: bool = True,
) -> None:
    """Write per-vertex UVs into a per-loop UV layer.

    ``.gam`` stores UVs per vertex; Blender stores them per face-corner
    (loop). Each loop takes its vertex's UV — correct here because the
    source format has one UV per vertex by construction, so there are
    no seams to split.
    """
    if not uvs:
        return
    uv_layer = mesh.uv_layers.new(name=layer_name)
    # Flagged for render as it is created. A mesh carrying exactly one
    # UV map has no other sensible render layer, and leaving the flag
    # unset made every imported model unexportable: HTAToolchain calls
    # calc_tangents(), which resolves the render layer and aborts the
    # whole export with `UV Map "(null)" not found` on the first mesh
    # that has none — a message that names neither the mesh nor the
    # flag, on a model the user never touched.
    # Set on the primary layer only. Blender renders with whichever
    # layer holds this flag, and the last one to claim it wins — so a
    # secondary layer flagged for render silently replaces the real one.
    try:
        uv_layer.active_render = active_render
    except AttributeError:
        pass
    for loop_index, loop in enumerate(mesh.loops):
        vertex_index = loop.vertex_index
        if vertex_index >= len(uvs):
            continue
        u, v = uvs[vertex_index]
        # Blender's V axis points the opposite way to most game/DCC
        # conventions.
        uv_layer.data[loop_index].uv = (u, 1.0 - v)


def _apply_vertex_colors(
    mesh: bpy.types.Mesh,
    colors: list[tuple[float, float, float, float]],
) -> None:
    if not colors:
        return
    try:
        color_layer = mesh.color_attributes.new(
            name=COLOR_LAYER, type="FLOAT_COLOR", domain="CORNER",
        )
    except (AttributeError, RuntimeError):
        return  # colour attributes unavailable; geometry is still fine

    for loop_index, loop in enumerate(mesh.loops):
        vertex_index = loop.vertex_index
        if vertex_index < len(colors):
            color_layer.data[loop_index].color = colors[vertex_index]
