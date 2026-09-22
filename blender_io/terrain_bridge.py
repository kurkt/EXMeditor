# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""HeightmapData <-> Blender Mesh bridge.

This is the one place that both imports ``bpy`` and knows about
``core.terrain.HeightmapData`` — see the architecture doc's
``blender_io`` package note for why this conversion doesn't live in
``formats/`` (which must stay importable without Blender) or in
``addon/operators.py`` (which should stay thin).

Both directions live here — ``build_mesh`` (import) and
``extract_heightmap`` (export) — because they are exact inverses of
each other and must be kept in sync.

Architecture note — all spatial conversion goes through the SHARED
``core.coordinates.CoordinateTransform``, which every other spatial
format also uses. This module does not implement its own axis or scale
handling. That rule exists because it was once broken: terrain placed
height on Blender Z while ``world.xml`` objects placed it on Blender Y,
and terrain used a placeholder cell size 8x smaller than the real one,
so the two ended up on entirely different planes. Both bridges looked
correct in isolation; nothing compared them. One shared transform makes
that class of bug impossible.

Grid indexing (row/col) is NEVER derived from vertex X/Y coordinates —
see ``EXM_ROW_ATTR``/``EXM_COL_ATTR`` below and ``validate_terrain_mesh``'s
docstring for why and exactly which Blender editing operations this
remains valid across.
"""

from __future__ import annotations

import array
import os
from math import isqrt

import bmesh
import bpy

from core.terrain import HeightmapData
from core.tile_blend import (
    MASK_ATLAS_FILE,
    atlas_uv,
    blend_statistics,
    combination_of,
    offset_in_blend_cell,
    pass_alphas,
    plan_quad,
)
from utils.errors import ErrorContext, ValidationError
from utils.logging import get_logger

logger = get_logger("blender_io.terrain_bridge")

# Custom properties (object-level) stashed on the built Object. Grid
# shape is inherent to the HeightmapData this module receives, so
# build_mesh() sets the grid-shape ones; the scale values are set by the
# caller (addon/operators.py), purely as a round-tripping convenience
# for its own UI.
CELL_SIZE_PROP = "exm_cell_size"
GRID_WIDTH_PROP = "exm_grid_width"
GRID_HEIGHT_PROP = "exm_grid_height"
XY_SCALE_PROP = "exm_xy_scale"
HEIGHT_SCALE_PROP = "exm_height_scale"

# Per-vertex mesh attributes (Mesh.attributes, domain='POINT') storing
# each vertex's original grid position. This — not vertex index order,
# and never X/Y position — is the sole source of truth for row/col at
# export time. See the module docstring.
EXM_ROW_ATTR = "exm_row"
EXM_COL_ATTR = "exm_col"


def build_mesh(
    heightmap: HeightmapData,
    name: str = "ExM_Terrain",
    *,
    transform: "CoordinateTransform | None" = None,
) -> bpy.types.Object:
    """Build a Blender grid mesh from ``heightmap``, preserving heights.

    Purely mechanical: ``heightmap`` is assumed to already be in
    whatever units/space it should visually appear in — this function
    applies no scale, offset, or unit conversion of its own::

        vertex.x = column * heightmap.cell_size
        vertex.y = row    * heightmap.cell_size
        vertex.z = sample

    ``transform`` is the shared ``CoordinateTransform`` — the same one
    passed to every other spatial bridge, so terrain and objects land
    in one consistent space.

    Every vertex's ``(row, column)`` is additionally written to a
    persistent per-vertex mesh attribute (see ``EXM_ROW_ATTR``/
    ``EXM_COL_ATTR``) — this, not X/Y position and not vertex order, is
    what ``extract_heightmap`` reads back on export.

    No axis reordering (transpose/flip) is applied here either — that's
    ``core.terrain_transform``'s job, applied by the caller if ever
    needed, not something ``build_mesh`` decides on its own.

    Returns a new, unlinked ``bpy.types.Object`` — the caller (see
    ``blender_io/scene_bridge.py``) is responsible for linking it into
    a collection.
    """
    from core.coordinates import CoordinateTransform  # local: avoids a cycle at import time

    transform = transform if transform is not None else CoordinateTransform()

    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()

    width, grid_height = heightmap.width, heightmap.height
    # Grid spacing and height both go through the shared transform, so
    # terrain lands in exactly the same Blender space as every other
    # spatial format (see core/coordinates.py).
    step = transform.terrain_grid_step(heightmap.cell_size)

    # Build all verts first, indexed by grid position, so faces can
    # reference their four corners directly without a second lookup
    # pass. Creation order here is what fixes the mapping used just
    # below to populate EXM_ROW_ATTR/EXM_COL_ATTR: vertex index
    # `y * width + x` is created for grid cell (x, y) — the *only*
    # place that correspondence is defined; nothing downstream may
    # assume it still holds without reading the attribute.
    # Terrain is placed by grid index, so the origin offset (which the
    # transform applies to game-space positions) has to be applied here
    # explicitly — otherwise the terrain stays put while every object,
    # road and collision box shifts, tearing the scene apart.
    offset_x = transform.origin_offset.x * transform.xy_scale
    offset_y = transform.origin_offset.z * transform.xy_scale

    vert_grid: list[list[object]] = [[None] * width for _ in range(grid_height)]
    for y in range(grid_height):
        for x in range(width):
            sample = heightmap.get_height(x, y)
            vert_grid[y][x] = bm.verts.new((
                x * step - offset_x,
                y * step - offset_y,
                transform.game_height_to_blender(sample),
            ))

    bm.verts.ensure_lookup_table()

    for y in range(grid_height - 1):
        for x in range(width - 1):
            v0 = vert_grid[y][x]
            v1 = vert_grid[y][x + 1]
            v2 = vert_grid[y + 1][x + 1]
            v3 = vert_grid[y + 1][x]
            bm.faces.new((v0, v1, v2, v3))

    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    # Persist (row, col) per vertex now that mesh.vertices exists and
    # is sized/ordered exactly as built above.
    row_attr = mesh.attributes.new(EXM_ROW_ATTR, type="INT", domain="POINT")
    col_attr = mesh.attributes.new(EXM_COL_ATTR, type="INT", domain="POINT")
    for y in range(grid_height):
        for x in range(width):
            index = y * width + x
            row_attr.data[index].value = y
            col_attr.data[index].value = x

    obj = bpy.data.objects.new(name, mesh)
    obj[CELL_SIZE_PROP] = heightmap.cell_size
    obj[GRID_WIDTH_PROP] = heightmap.width
    obj[GRID_HEIGHT_PROP] = heightmap.height
    return obj


def log_terrain_summary(
    heightmap: HeightmapData,
    *,
    xy_scale: float,
    height_scale: float,
    output_path: str | None = None,
) -> None:
    """Log the grid/size/height-range calibration summary.

    ``heightmap`` is expected to already be in Blender world units
    (i.e. whatever ``build_mesh``/``extract_heightmap`` actually built
    from or read) — this function does no scaling of its own; the
    ``xy_scale``/``height_scale`` parameters are for the log's
    informational lines only (echoing which ``CoordinateTransform`` was
    used), not part of any computation here. Used by both the import
    and export operators, with the same formula both directions, so
    the two logs are directly comparable — the number a person compares
    against a map's known in-game size to calibrate the real
    ``xy_scale``/``height_scale``. ``output_path``, when given, adds
    the requested "Output:" line (export only).
    """
    terrain_width = (heightmap.width - 1) * heightmap.cell_size
    terrain_height = (heightmap.height - 1) * heightmap.cell_size
    min_height = min(heightmap.values)
    max_height = max(heightmap.values)

    logger.info("Grid: %d x %d", heightmap.width, heightmap.height)
    logger.info("Terrain size: %.1f x %.1f Blender units", terrain_width, terrain_height)
    logger.info("Height range: %.1f .. %.1f", min_height, max_height)
    logger.info("XY scale: %s", xy_scale)
    logger.info("Height scale: %s", height_scale)
    if output_path is not None:
        logger.info("Output: %s", output_path)


def validate_terrain_mesh(obj: bpy.types.Object) -> int:
    """Validate that ``obj`` is a well-formed square terrain grid mesh.

    Returns the grid side length (N) on success. Raises
    ``ValidationError`` with a specific, human-readable message on any
    failure — never crashes with a bare Python exception.

    Checks performed:
    - ``obj`` exists and is a Mesh object.
    - vertex count is a perfect square (an NxN grid).
    - every face is a quad — no triangles, no n-gons.
    - face count is exactly ``(N-1)^2`` (no holes, no extra geometry).
    - no duplicate/coincident vertex *positions* (a degenerate-geometry
      check — NOT how row/col is determined; see below).
    - ``EXM_ROW_ATTR``/``EXM_COL_ATTR`` are present on the mesh and, for
      every vertex, together form a complete bijection onto
      ``{0..N-1} x {0..N-1}`` — every grid cell covered exactly once.
    - every *interior* vertex (per its stored row/col, not its
      position) has exactly 4 connected edges — catches holes, missing
      edges, or extra diagonal/triangulated edges.

    What this validates *against*: a mesh built by ``build_mesh()`` and
    then edited using operations that only reposition existing
    vertices — Sculpt, Grab/Translate, Smooth (as a vertex operator),
    Scale/Rotate in Edit Mode, or any Object-mode transform (which
    doesn't touch mesh data at all). Those preserve both the vertex
    count and the ``EXM_ROW_ATTR``/``EXM_COL_ATTR`` attributes
    unchanged, so validation (and export) succeeds regardless of
    whether vertex *order* also happened to survive.

    What this correctly REJECTS: Subdivide, Remesh, or Decimate — each
    changes the mesh's topology/resolution outright, which is a
    fundamentally different mesh, not an edited version of the same
    grid. That's a resampling problem, not something this exporter
    supports — see the architecture doc changelog for the full analysis
    of which Blender operations this covers.
    """
    if obj is None:
        raise ValidationError("no object provided for terrain export")
    if obj.type != "MESH":
        raise ValidationError(
            f"object {obj.name!r} is not a Mesh (type={obj.type!r})",
            context=ErrorContext(extra={"object_name": obj.name, "object_type": obj.type}),
        )

    mesh = obj.data
    vertex_count = len(mesh.vertices)
    side = isqrt(vertex_count)
    if side * side != vertex_count:
        raise ValidationError(
            "terrain mesh vertex count is not a perfect square (not an NxN grid)",
            context=ErrorContext(extra={"object_name": obj.name, "vertex_count": vertex_count}),
        )
    if side < 2:
        raise ValidationError(
            "terrain mesh is too small to be a valid grid (need at least 2x2 vertices)",
            context=ErrorContext(extra={"object_name": obj.name, "side": side}),
        )

    expected_face_count = (side - 1) * (side - 1)
    actual_face_count = len(mesh.polygons)
    if actual_face_count != expected_face_count:
        raise ValidationError(
            "terrain mesh face count does not match a complete NxN grid "
            "(the surface may have holes, or extra/missing geometry — or "
            "the mesh was Subdivided/Remeshed/Decimated, which this "
            "exporter does not support)",
            context=ErrorContext(
                extra={
                    "object_name": obj.name,
                    "expected_face_count": expected_face_count,
                    "actual_face_count": actual_face_count,
                }
            ),
        )

    for polygon in mesh.polygons:
        if len(polygon.vertices) != 4:
            kind = "triangle" if len(polygon.vertices) == 3 else "n-gon"
            raise ValidationError(
                f"terrain mesh contains a non-quad face ({kind}) at polygon "
                f"index {polygon.index} — only quads are allowed",
                context=ErrorContext(
                    extra={
                        "object_name": obj.name,
                        "polygon_index": polygon.index,
                        "vertex_count": len(polygon.vertices),
                    }
                ),
            )

    # Degenerate-geometry check (duplicate/coincident vertex
    # POSITIONS). This is NOT how row/col is determined — it exists
    # purely to catch accidentally-welded/duplicated geometry, which is
    # a legitimate mesh-integrity problem independent of the row/col
    # attribute mechanism below.
    seen_positions: dict[tuple[float, float, float], int] = {}
    for v in mesh.vertices:
        key = (round(v.co.x, 6), round(v.co.y, 6), round(v.co.z, 6))
        if key in seen_positions:
            raise ValidationError(
                "terrain mesh contains duplicate/coincident vertices",
                context=ErrorContext(
                    extra={
                        "object_name": obj.name,
                        "vertex_index_a": seen_positions[key],
                        "vertex_index_b": v.index,
                        "position": key,
                    }
                ),
            )
        seen_positions[key] = v.index

    # --- Row/col: read from the persistent attribute, never from X/Y. ---
    if EXM_ROW_ATTR not in mesh.attributes or EXM_COL_ATTR not in mesh.attributes:
        raise ValidationError(
            f"terrain mesh is missing the {EXM_ROW_ATTR!r}/{EXM_COL_ATTR!r} "
            "grid-position attributes. This mesh was not built by "
            "build_mesh() (or those attributes were lost by an operation "
            "that regenerates mesh data, such as Remesh or Decimate) — "
            "re-import the terrain instead of exporting this object",
            context=ErrorContext(extra={"object_name": obj.name}),
        )
    row_attr = mesh.attributes[EXM_ROW_ATTR]
    col_attr = mesh.attributes[EXM_COL_ATTR]

    grid_position_of: dict[int, tuple[int, int]] = {}
    occupied: set[tuple[int, int]] = set()
    for v in mesh.vertices:
        row = row_attr.data[v.index].value
        col = col_attr.data[v.index].value
        if not (0 <= row < side and 0 <= col < side):
            raise ValidationError(
                "terrain mesh vertex has an out-of-range stored grid "
                "position — the mesh's topology no longer matches its "
                "own grid-position attributes",
                context=ErrorContext(
                    extra={"object_name": obj.name, "vertex_index": v.index, "row": row, "col": col, "side": side}
                ),
            )
        if (row, col) in occupied:
            raise ValidationError(
                "terrain mesh has more than one vertex with the same "
                "stored grid position — the surface may have been "
                "Subdivided/Remeshed/Decimated, or the attributes are "
                "corrupted",
                context=ErrorContext(extra={"object_name": obj.name, "row": row, "col": col}),
            )
        occupied.add((row, col))
        grid_position_of[v.index] = (row, col)

    if len(occupied) != side * side:
        raise ValidationError(
            "terrain mesh's stored grid positions do not cover the full "
            "NxN grid exactly once",
            context=ErrorContext(
                extra={"object_name": obj.name, "covered_cells": len(occupied), "expected_cells": side * side}
            ),
        )

    # Interior-vertex degree check, using the stored grid position (not
    # a position-derived one) to classify interior vs. boundary.
    neighbors: dict[int, set[int]] = {v.index: set() for v in mesh.vertices}
    for edge in mesh.edges:
        a, b = edge.vertices
        neighbors[a].add(b)
        neighbors[b].add(a)

    for v in mesh.vertices:
        row, col = grid_position_of[v.index]
        is_interior = 0 < row < side - 1 and 0 < col < side - 1
        if is_interior and len(neighbors[v.index]) != 4:
            raise ValidationError(
                "terrain mesh has broken topology: an interior vertex does not "
                "have exactly 4 connected edges (hole, missing edge, or extra "
                "diagonal/triangulated edge)",
                context=ErrorContext(
                    extra={
                        "object_name": obj.name,
                        "grid_row": row,
                        "grid_col": col,
                        "degree": len(neighbors[v.index]),
                    }
                ),
            )

    return side


def extract_heightmap(
    obj: bpy.types.Object,
    *,
    cell_size: float | None = None,
    transform: "CoordinateTransform | None" = None,
) -> HeightmapData:
    """Read a ``HeightmapData`` back out of a terrain mesh built by ``build_mesh()``.

    Purely mechanical, the exact inverse of ``build_mesh``: grid
    indexing comes ENTIRELY from the ``EXM_ROW_ATTR``/``EXM_COL_ATTR``
    mesh attributes (see ``validate_terrain_mesh``) — never from X/Y
    position, and never from vertex order — and ``vertex.co.z`` is
    copied as-is, with NO unit conversion of any kind::

        row, col = vertex's stored EXM_ROW_ATTR / EXM_COL_ATTR
        height   = vertex.co.z

    Heights are converted back to game units via the shared
    ``transform``; the result is game-space data ready for the codec.

    ``cell_size`` defaults to whatever ``build_mesh`` stored as a custom
    property on ``obj`` if not given explicitly, so the result's
    ``HeightmapData.cell_size`` round-trips automatically; it plays no
    role in reconstructing row/col (that's the attributes' job).

    Raises
    ------
    ValidationError
        If ``validate_terrain_mesh`` fails.
    """
    from core.coordinates import CoordinateTransform  # local: avoids a cycle at import time

    transform = transform if transform is not None else CoordinateTransform()
    side = validate_terrain_mesh(obj)
    mesh = obj.data

    if cell_size is None:
        cell_size = obj.get(CELL_SIZE_PROP, 1.0)

    row_attr = mesh.attributes[EXM_ROW_ATTR]
    col_attr = mesh.attributes[EXM_COL_ATTR]

    values = array.array("f", [0.0]) * (side * side)
    # Heights are unaffected by the horizontal origin offset, so nothing
    # to undo here — see build_mesh.
    for v in mesh.vertices:
        row = row_attr.data[v.index].value
        col = col_attr.data[v.index].value
        values[row * side + col] = transform.blender_height_to_game(v.co.z)

    return HeightmapData(width=side, height=side, cell_size=cell_size, values=values)


# --- terrain colour ---------------------------------------------------

#: Colour attribute the colormap lands in. Named like the row/column
#: attributes so everything this add-on writes to a terrain mesh is
#: recognisable as ours.
EXM_COLOR_ATTR = "exm_colormap"


#: Corner attribute carrying the blend alphas of a patch's second,
#: third and fourth render passes. The first pass is the opaque base
#: and needs no alpha, which is why three channels are enough for the
#: four tiles that can meet at one patch.
TILE_BLEND_ATTR = "ExM_TileBlend"

#: One UV layer per blended pass, holding where in ``mask.dds`` that
#: pass reads its alpha. Three, because four tiles can meet at a patch
#: and the first of them is the opaque base.
MASK_UV_LAYERS = ("ExM_MaskUV1", "ExM_MaskUV2", "ExM_MaskUV3")

#: Above this many blended materials, say so. MEASURED, because the
#: first guess at this number was wrong in the interesting direction::
#:
#:     r1m1   side 128, 20 tiles    410 combinations   64% of patches blend
#:     r1m2   side 256, 28 tiles   1137 combinations   64% of patches blend
#:
#: The assumption behind the old limit of 96 — that a map laid out in
#: regions is overwhelmingly one tile per patch — is simply false: of
#: r1m2's 1137 combinations only 23 are a single tile. Blending is the
#: normal case, not the exception, and a material per combination is
#: what that costs.
#:
#: So this is a sanity bound, not a budget: four tiles can meet at a
#: patch and a map names at most a few dozen, so a count in the
#: thousands still fits, and one far past that means the grid is being
#: read wrongly — the same thing that made it look like noise at 256
#: cells.
MAX_BLEND_MATERIALS = 4000


def apply_colormap(obj: "bpy.types.Object", layer) -> int:
    """Paint ``colormap.raw`` onto a terrain mesh as vertex colours.

    ``colormap.raw`` is 512x512 packed colours and ``displace.bin`` is
    512x512 float heights — the same grid, one colour per heightfield
    vertex. So this is a direct index-for-index assignment with no
    interpolation and no resampling, and the correspondence is checked
    rather than assumed: a layer whose sample count does not match the
    mesh gets refused, because a silently misaligned colour map looks
    like a plausible terrain rather than like an error.

    The attribute is on the POINT domain, matching how the data is
    stored (per vertex, not per corner). Returns how many vertices were
    coloured.

    Colours go through ``core.color``. The file holds packed ARGB, so
    its bytes run B, G, R, A; reading them positionally would give a
    terrain that is blue where the game is brown, which is exactly the
    kind of wrongness that gets blamed on lighting.
    """
    from core import color as color_codec

    mesh = obj.data
    vertex_count = len(mesh.vertices)

    if layer.sample_count != vertex_count:
        raise ValidationError(
            "colormap does not match the terrain grid",
            context=ErrorContext(
                extra={
                    "colormap_samples": layer.sample_count,
                    "colormap_grid": f"{layer.width}x{layer.height}",
                    "terrain_vertices": vertex_count,
                }
            ),
        )

    if layer.is_empty():
        logger.info("colormap is entirely zero; not building a colour layer")
        return 0

    existing = mesh.color_attributes.get(EXM_COLOR_ATTR)
    if existing is not None:
        mesh.color_attributes.remove(existing)
    attribute = mesh.color_attributes.new(
        name=EXM_COLOR_ATTR, type="FLOAT_COLOR", domain="POINT"
    )

    # Vertex index y * width + x is the cell (x, y) that build_mesh
    # created, and the colormap is stored row-major in the same order,
    # so the two run in step without a per-vertex lookup.
    for index, sample in enumerate(layer.colors()):
        attribute.data[index].color = color_codec.to_float(sample)

    # Reported, not just applied. The terrain's colour comes from three
    # measurable things and a wrong one is invisible in the import log
    # until it is on screen — so the numbers go in the log, where they
    # can be compared against what the file holds.
    samples = layer.colors()
    channels = [
        sum(sample[c] for sample in samples) / len(samples) for c in range(3)
    ]
    logger.info(
        "terrain colormap applied to %s vertices (%sx%s): mean R %.0f G %.0f B %.0f",
        vertex_count, layer.width, layer.height, *channels,
    )
    if channels[1] > channels[0] + 10 and channels[1] > channels[2] + 10:
        logger.warning(
            "  that colour map is green-dominant, which no shipped one is. "
            "If the terrain looks green, this is where it comes from — most "
            "likely vertex paint saved in the .blend rather than the file."
        )
    return vertex_count


def extract_colormap(obj: "bpy.types.Object", width: int, height: int) -> bytes:
    """Read the colour layer back out, ready to write to ``colormap.raw``.

    The inverse of :func:`apply_colormap`, so a map imported and
    exported without being touched produces the same bytes. A mesh with
    no colour layer yields opaque mid-grey — the value the sample map
    carries across 77% of its area — rather than black, which would
    turn an untouched terrain into a shadow on export.
    """
    from core import color as color_codec

    mesh = obj.data
    expected = width * height
    if len(mesh.vertices) != expected:
        raise ValidationError(
            "terrain mesh does not match the requested colormap grid",
            context=ErrorContext(
                extra={
                    "terrain_vertices": len(mesh.vertices),
                    "requested": f"{width}x{height}",
                }
            ),
        )

    attribute = mesh.color_attributes.get(EXM_COLOR_ATTR)
    if attribute is None:
        return color_codec.pack_many([(127, 127, 127, 255)] * expected)

    return color_codec.pack_many(
        color_codec.from_float(tuple(attribute.data[i].color))
        for i in range(expected)
    )


# --- terrain ground textures ------------------------------------------


def apply_tilemap(
    obj: "bpy.types.Object", tilemap, game_root: str | None, blend: bool = False
) -> int:
    """Give the terrain its ground textures.

    Each quad takes the material of the tile cell it falls in. The cell
    is ``(x // QUADS_PER_TILE_CELL, y // QUADS_PER_TILE_CELL)`` — the
    ratio of the two grids, which nothing has confirmed. If the terrain
    comes out with its textures at half or double the right scale, that
    ratio is the thing to question first.

    Only tiles the map actually uses get a material. Returns how many
    ground textures were used, either way.

    With ``blend`` off this draws each cell in one texture, hard-edged.
    The engine does not: it blends between neighbouring tiles across
    patches centred on the tile grid's vertices, and twenty textures on
    hard cell edges look like a chessboard where the game has
    gradients. With ``blend`` on, ``core/tile_blend.py`` reproduces
    that — see its docstring for what in it is measured and what is a
    declared approximation.
    """
    mesh = obj.data
    used = tilemap.used_tiles()
    if not used:
        return 0

    if blend:
        quads_per_cell = _quads_per_tile_cell(obj, tilemap)
        _apply_tile_uvs(obj, quads_per_cell)
        _apply_blended_tiles(obj, tilemap, game_root, quads_per_cell, used)
        return len(used)

    slot_of_tile: dict[int, int] = {}
    missing: list[str] = []
    for tile_index in used:
        # Loaded by its FULL path under the game root, not by basename.
        # ``level.tile`` names ``region1\ground_fall.dds`` and the model
        # texture tree holds its own ``ground.dds`` and friends, so
        # resolving a tile by filename alone can pick a model's texture
        # instead — a square of ground wearing a wall's material.
        relative = tilemap.path_for(tile_index).replace("\\", "/")
        absolute = os.path.join(game_root, *relative.split("/")) if game_root else ""

        material, found = _tile_material(
            f"ExM_Tile_{tile_index}_{os.path.basename(relative)}", absolute
        )
        if not found:
            missing.append(relative)

        slot_of_tile[tile_index] = len(mesh.materials)
        mesh.materials.append(material)

    if missing:
        tiles_root = os.path.join(game_root, "data", "tiles") if game_root else ""
        logger.warning(
            "%s of %s ground texture(s) not found — the terrain will be grey "
            "wherever they are missing", len(missing), len(used),
        )
        logger.warning("  looked under: %s", tiles_root or "(no game folder set)")
        if tiles_root and not os.path.isdir(tiles_root):
            logger.warning(
                "  THAT FOLDER DOES NOT EXIST. level.tile names its textures "
                "relative to it, so none of them can resolve."
            )
        for name in missing[:8]:
            logger.warning("  missing: %s", name)

    quads_per_cell = _quads_per_tile_cell(obj, tilemap)
    grid_width = obj.get(GRID_WIDTH_PROP) or 0
    quads_across = max(1, grid_width - 1)

    _apply_tile_uvs(obj, quads_per_cell)

    for index, polygon in enumerate(mesh.polygons):
        quad_x = index % quads_across
        quad_y = index // quads_across
        cell_x = min(tilemap.side - 1, quad_x // quads_per_cell)
        cell_y = min(tilemap.side - 1, quad_y // quads_per_cell)
        tile = tilemap.indices[cell_y * tilemap.side + cell_x]
        polygon.material_index = slot_of_tile.get(tile, 0)

    logger.info(
        "terrain tiles applied: %s material(s) over %s polygons",
        len(used), len(mesh.polygons),
    )
    return len(used)


#: UV layer the ground textures sample.
TERRAIN_UV_LAYER = "UVMap"


def _tile_material(name: str, image_path: str):
    """One ground material: its texture, modulated by the colour map.

    Built here rather than through ``texture_bridge.build_material``
    because a tile is addressed differently from a model texture — by
    full path under ``data/tiles`` rather than by bare filename — and
    because it samples the terrain's own colour attribute, which no
    model material has.

    Returns ``(material, whether the image loaded)``.
    """
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing, True

    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree

    principled = None
    for node in tree.nodes:
        if getattr(node, "type", "") == "BSDF_PRINCIPLED":
            principled = node
            break
    if principled is None:
        return material, False

    try:
        principled.inputs["Roughness"].default_value = 0.9
    except (AttributeError, KeyError, TypeError):
        pass

    image = None
    if image_path and os.path.isfile(image_path):
        try:
            image = bpy.data.images.load(image_path, check_existing=True)
        except RuntimeError as exc:
            logger.debug("could not load %s: %s", image_path, exc)

    if image is None:
        return material, False

    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.name = "Diffuse"
    texture.label = os.path.basename(image_path)
    texture.image = image
    texture.location = (-800, 0)

    colour = _colormap_node(tree)
    source = texture.outputs["Color"]

    if colour is not None:
        # The colour map is what makes one ground texture read as sand
        # in one place and mud in another. It is stored per terrain
        # vertex, so it modulates rather than replaces.
        try:
            mix = tree.nodes.new("ShaderNodeMixRGB")
            mix.name = "ExM_ColormapMix"
            mix.blend_type = "MULTIPLY"
            mix.location = (-400, 0)
            mix.inputs["Fac"].default_value = 1.0
            tree.links.new(source, mix.inputs["Color1"])
            tree.links.new(colour.outputs["Color"], mix.inputs["Color2"])
            source = mix.outputs["Color"]
        except (AttributeError, KeyError, RuntimeError, TypeError) as exc:
            logger.debug("could not wire the colour map: %s", exc)

    try:
        tree.links.new(source, principled.inputs["Base Color"])
    except (AttributeError, KeyError, RuntimeError, TypeError) as exc:
        logger.debug("could not wire %s: %s", name, exc)
        return material, False

    return material, True


def _colormap_node(tree):
    """A node reading the terrain's colour attribute, or None."""
    try:
        node = tree.nodes.new("ShaderNodeVertexColor")
        node.name = "ExM_Colormap"
        node.layer_name = EXM_COLOR_ATTR
        node.location = (-800, -300)
        return node
    except (AttributeError, RuntimeError, TypeError) as exc:
        logger.debug("could not create a colour map node: %s", exc)
        return None


def _apply_tile_uvs(obj: "bpy.types.Object", quads_per_cell: int) -> None:
    """Give the terrain a UV layer, so its textures have somewhere to sit.

    The terrain was built without one. Every face then sampled the same
    texel and the ground came out a flat colour — indistinguishable
    from having no texture at all, which is what it looked like.

    One tile texture covers one tile cell, so the coordinate is the
    grid position divided by the quads in a cell. Values run well past
    1 and are meant to: image nodes repeat by default, and that
    repetition is what puts a copy of the texture in each cell.

    UNCONFIRMED as the game's own mapping. It is the reading that
    follows from one texture per cell, and it is the thing to question
    first if the ground comes out at the wrong scale.
    """
    mesh = obj.data
    width = obj.get(GRID_WIDTH_PROP) or 0
    if width < 2:
        return

    existing = mesh.uv_layers.get(TERRAIN_UV_LAYER)
    layer = existing if existing is not None else mesh.uv_layers.new(
        name=TERRAIN_UV_LAYER
    )

    step = 1.0 / float(max(1, quads_per_cell))
    data = layer.data
    for index, loop in enumerate(mesh.loops):
        if index >= len(data):
            break
        vertex = loop.vertex_index
        data[index].uv = ((vertex % width) * step, (vertex // width) * step)

    try:
        layer.active_render = True
        mesh.uv_layers.active_index = list(mesh.uv_layers).index(layer)
    except (AttributeError, TypeError, ValueError):
        pass

    logger.info(
        "terrain UVs: one tile texture per %s x %s quads",
        quads_per_cell, quads_per_cell,
    )


def _quads_per_tile_cell(obj: "bpy.types.Object", tilemap) -> int:
    """How many heightfield quads one tile cell covers, per axis.

    Derived from the two grids rather than hard-coded, so a map with a
    different heightfield size lands on the right ratio instead of the
    one that happened to be true for the sample. Never below 1.
    """
    grid_width = obj.get(GRID_WIDTH_PROP) or 0
    quads_across = max(1, grid_width - 1)
    return max(1, round(quads_across / tilemap.side))


# --- blending one ground tile into the next ---------------------------


def _tile_stem(tilemap, index: int) -> str:
    """A tile's bare name, for a material to be recognisable by.

    Through "/" explicitly: the names in ``level.tile`` are Windows
    paths, and ``os.path.basename`` leaves a backslash alone on Linux —
    which would name every material after its whole path.
    """
    return os.path.splitext(tilemap.tiles[index].replace("\\", "/").split("/")[-1])[0]


def _tile_images(tilemap, used, game_root):
    """Load one image per tile the map uses.

    Returns ``(image by tile index, the ones that did not resolve)``.
    A tile whose texture is missing still gets an entry, holding None:
    dropping it would shift every later tile onto the wrong texture.
    """
    images = {}
    missing = []

    for tile_index in used:
        # By FULL path under the game root, not by basename — see
        # ``apply_tilemap``. A tile resolved by filename alone can pick
        # up a model's texture of the same name.
        relative = tilemap.path_for(tile_index).replace("\\", "/")
        absolute = os.path.join(game_root, *relative.split("/")) if game_root else ""

        image = None
        if absolute and os.path.isfile(absolute):
            try:
                image = bpy.data.images.load(absolute, check_existing=True)
            except RuntimeError as exc:
                logger.debug("could not load %s: %s", absolute, exc)
        if image is None:
            missing.append(relative)
        images[tile_index] = image

    return images, missing


def _restore_active_uv(mesh, layer) -> None:
    """Put the active UV layer back where it was."""
    if layer is None:
        return
    try:
        mesh.uv_layers.active = layer
        mesh.uv_layers.active_index = list(mesh.uv_layers).index(layer)
    except (AttributeError, TypeError, ValueError) as exc:
        logger.debug("could not restore the active UV layer: %s", exc)


def _mask_atlas(game_root, tilemap=None):
    """The shared alpha atlas, or None when it is not on disk.

    Every tile type blends through this one image — ``tileinfo.xml``
    declares a single alpha set and every land type names it — so it is
    loaded once for the whole terrain rather than per material.
    """
    if not game_root:
        return None

    candidates = [os.path.join(game_root, *MASK_ATLAS_FILE.split("/"))]
    root = getattr(tilemap, "root", "") or ""
    if root:
        candidates.append(
            os.path.join(game_root, *root.replace("\\", "/").split("/"), "mask.dds")
        )

    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            image = bpy.data.images.load(path, check_existing=True)
        except RuntimeError as exc:
            logger.debug("could not load the mask atlas %s: %s", path, exc)
            continue
        try:
            # It is an alpha lookup, not a picture: a colour transform
            # applied to it would bend the blend curve.
            image.colorspace_settings.name = "Non-Color"
        except (AttributeError, TypeError):
            pass
        return image

    logger.info(
        "no %s under the game folder — ground tiles blend on a computed "
        "ramp instead of the game's own mask shapes", MASK_ATLAS_FILE,
    )
    return None


def _write_tile_blend(obj: "bpy.types.Object", tilemap, quads_per_cell: int):
    """Write each face corner's blend alphas, and group the faces.

    Two things in one walk, because they come from the same plan: the
    corner attribute the shader reads, and which faces share a set of
    tiles and can therefore share one material.

    The alphas are those of the second, third and fourth passes, in R,
    G and B. The first pass is the opaque base and has no alpha of its
    own — see ``core/tile_blend.py``.

    Returns ``{tile tuple: [polygon indices]}``.
    """
    mesh = obj.data
    width = obj.get(GRID_WIDTH_PROP) or 0
    if width < 2:
        return {}
    quads_across = max(1, width - 1)

    # EVERY layer is created first and looked up afterwards. Adding a
    # layer to a mesh re-lays its custom data, and a reference taken
    # before that points at the old allocation: ``len(layer.data)`` on
    # it reads 0, every write is skipped, and the layer keeps its
    # default value — which is white, so the ground came out with no
    # blend at all while the layer plainly existed and had the right
    # length when anything looked at it later.
    #
    # ``apply_colormap`` never hit this because it writes immediately
    # after creating its attribute, with nothing in between.
    if mesh.color_attributes.get(TILE_BLEND_ATTR) is None:
        mesh.color_attributes.new(
            name=TILE_BLEND_ATTR, type="FLOAT_COLOR", domain="CORNER"
        )

    # Creating a UV layer also makes it the ACTIVE one, and the active
    # layer is what Texture Paint and the viewport sample with. Three
    # new layers here would hand the ground's display to a mask lookup
    # — which is exactly how a lightmap unwrap once became what the
    # viewport painted through.
    previously_active = getattr(mesh.uv_layers, "active", None)
    for name in MASK_UV_LAYERS:
        if mesh.uv_layers.get(name) is None:
            mesh.uv_layers.new(name=name)
    _restore_active_uv(mesh, previously_active)

    # Now, and only now, take the references that will be written to.
    layer = mesh.color_attributes.get(TILE_BLEND_ATTR)
    data = layer.data if layer is not None else []
    mask_uvs = [mesh.uv_layers.get(name) for name in MASK_UV_LAYERS]
    mask_uvs = [uv for uv in mask_uvs if uv is not None]

    if not len(data):
        logger.warning(
            "the blend attribute %r came back empty on a mesh with %s face "
            "corner(s); nothing can be written to it",
            TILE_BLEND_ATTR, sum(len(p.vertices) for p in mesh.polygons),
        )
        return {}

    combinations = {}
    written = 0
    totals = [0.0, 0.0, 0.0]
    for polygon in mesh.polygons:
        quad_x = polygon.index % quads_across
        quad_y = polygon.index // quads_across
        (blend_x, blend_y), passes = plan_quad(
            quad_x, quad_y, tilemap.indices, tilemap.side, quads_per_cell
        )
        combinations.setdefault(combination_of(passes), []).append(polygon.index)

        start = getattr(polygon, "loop_start", 0)
        for loop_index in range(start, start + len(polygon.vertices)):
            if loop_index >= len(data):
                break
            # A patch of one tile has nothing to blend, and that is the
            # great majority of a map — worth not computing.
            if len(passes) == 1:
                data[loop_index].color = (0.0, 0.0, 0.0, 1.0)
                for uv_layer in mask_uvs:
                    if loop_index < len(uv_layer.data):
                        uv_layer.data[loop_index].uv = (0.0, 0.0)
                continue

            vertex = mesh.loops[loop_index].vertex_index
            u = offset_in_blend_cell(vertex % width, blend_x, quads_per_cell)
            v = offset_in_blend_cell(vertex // width, blend_y, quads_per_cell)

            # Where each pass reads its alpha out of the shared atlas.
            for slot, uv_layer in enumerate(mask_uvs):
                if loop_index >= len(uv_layer.data):
                    continue
                if slot + 1 < len(passes):
                    uv_layer.data[loop_index].uv = atlas_uv(
                        passes[slot + 1].mask, u, v
                    )
                else:
                    uv_layer.data[loop_index].uv = (0.0, 0.0)

            # And the computed ramp alongside it, unchanged: it is what
            # the material falls back to when the atlas is not on disk.
            alphas = pass_alphas(passes, u, v)[1:]
            while len(alphas) < 3:
                alphas.append(0.0)
            data[loop_index].color = (alphas[0], alphas[1], alphas[2], 1.0)
            written += 1
            for channel in range(3):
                totals[channel] += alphas[channel]

    # What was written, from the writing itself rather than from a
    # sample of it afterwards. When the ground comes out unblended the
    # first question is whether this ran at all, and a line that reads
    # the numbers back out of the loop that produced them answers it
    # without depending on anything downstream being right.
    logger.info(
        "tile blend: %s of %s face corner(s) blended, mean alpha "
        "R %.3f G %.3f B %.3f over those",
        written, len(data),
        *[t / written if written else 0.0 for t in totals],
    )
    if not written:
        logger.warning(
            "no face corner on this terrain blends. Either every patch is "
            "one tile, or the grid is being read wrongly."
        )

    return combinations


def _blend_tiles(tree, texture_nodes, mask_image=None):
    """Composite pass textures the way the engine draws them.

    Each pass over the one before it, by its own alpha. With the atlas
    on disk that alpha is the game's own: ``mask.dds`` sampled through
    a UV set per pass, which carries the cell and the rotation the
    pass's corner mask calls for. Without it the alpha comes from the
    computed ramp in the corner attribute — right at the cell centres,
    a plain bilinear slope in between.

    Returns the socket carrying the result. A single pass returns its
    texture untouched, so the common case builds exactly the node graph
    it did before blending existed.
    """
    source = texture_nodes[0].outputs["Color"]
    if len(texture_nodes) < 2:
        return source

    try:
        alphas = (
            _mask_alpha_sockets(tree, mask_image, len(texture_nodes) - 1)
            if mask_image is not None
            else _ramp_alpha_sockets(tree, len(texture_nodes) - 1)
        )
        for index, node in enumerate(texture_nodes[1:]):
            mix = tree.nodes.new("ShaderNodeMixRGB")
            mix.name = "ExM_TileBlend%s" % (index + 1)
            mix.blend_type = "MIX"
            mix.location = (-750 + index * 180, -450)
            tree.links.new(alphas[index], mix.inputs["Fac"])
            tree.links.new(source, mix.inputs["Color1"])
            tree.links.new(node.outputs["Color"], mix.inputs["Color2"])
            source = mix.outputs["Color"]
    except (AttributeError, IndexError, KeyError, RuntimeError, TypeError) as exc:
        logger.debug("could not wire the tile blend: %s", exc)

    return source


def _mask_alpha_sockets(tree, mask_image, count: int):
    """One atlas lookup per blended pass, giving its alpha socket.

    All of them read the same image and differ only in the UV set,
    which is what carries the corner mask and its rotation. The alpha
    channel is the mask; the colour channels are decoration.
    """
    sockets = []
    for index in range(count):
        node = tree.nodes.new("ShaderNodeTexImage")
        node.name = "ExM_TileMask%s" % (index + 1)
        node.label = os.path.basename(MASK_ATLAS_FILE)
        node.image = mask_image
        node.location = (-1200, -450 - index * 280)
        try:
            # Clip, not repeat: the cells sit edge to edge and a
            # coordinate that strayed outside one would otherwise wrap
            # to the far side of the atlas.
            node.extension = "EXTEND"
        except (AttributeError, TypeError):
            pass
        _sample_with(tree, node, MASK_UV_LAYERS[index])
        sockets.append(node.outputs["Alpha"])
    return sockets


def _ramp_alpha_sockets(tree, count: int):
    """The fallback: the computed ramp, out of the corner attribute."""
    weights = tree.nodes.new("ShaderNodeVertexColor")
    weights.name = "ExM_TileBlendAlpha"
    weights.layer_name = TILE_BLEND_ATTR
    weights.location = (-1200, -900)

    split = tree.nodes.new("ShaderNodeSeparateColor")
    split.name = "ExM_TileBlendSplit"
    split.location = (-1000, -900)
    tree.links.new(weights.outputs["Color"], split.inputs["Color"])

    channels = ("Red", "Green", "Blue")
    return [split.outputs[channels[index]] for index in range(count)]


def _blended_tile_material(name: str, images, labels, mask_image=None):
    """A ground material for one set of tiles, blended between them."""
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing

    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree

    principled = None
    for node in tree.nodes:
        if getattr(node, "type", "") == "BSDF_PRINCIPLED":
            principled = node
            break
    if principled is None:
        return material

    try:
        principled.inputs["Roughness"].default_value = 0.9
    except (AttributeError, KeyError, TypeError):
        pass

    texture_nodes = []
    for index, image in enumerate(images):
        node = tree.nodes.new("ShaderNodeTexImage")
        # The first keeps the name the unblended path uses, so anything
        # looking for the ground's diffuse still finds it.
        node.name = "Diffuse" if index == 0 else "Diffuse%s" % index
        node.label = labels[index]
        node.image = image
        node.location = (-1500, -index * 300)
        _sample_with(tree, node, TERRAIN_UV_LAYER)
        texture_nodes.append(node)

    source = _blend_tiles(tree, texture_nodes, mask_image)

    colour = _colormap_node(tree)
    if colour is not None:
        source = (
            _multiply(tree, source, colour.outputs["Color"], "ExM_ColormapMix")
            or source
        )

    try:
        tree.links.new(source, principled.inputs["Base Color"])
    except (AttributeError, KeyError, RuntimeError, TypeError) as exc:
        logger.debug("could not wire %s: %s", name, exc)

    return material


def _apply_blended_tiles(obj, tilemap, game_root, quads_per_cell: int, used) -> None:
    """The blended half of :func:`apply_tilemap`.

    One material per set of tiles that meet, rather than one per tile:
    the set is what a patch needs to draw. That is several hundred
    materials on a real map — 410 on r1m1, 1137 on r1m2 — because
    blending is the normal case there rather than the exception, and
    a patch of three or four tiles genuinely needs three or four
    textures at once.
    """
    mesh = obj.data
    images, missing = _tile_images(tilemap, used, game_root)
    stems = {index: _tile_stem(tilemap, index) for index in used}

    combinations = _write_tile_blend(obj, tilemap, quads_per_cell)
    if not combinations:
        return

    mask_image = _mask_atlas(game_root, tilemap)

    if len(combinations) > MAX_BLEND_MATERIALS:
        logger.warning(
            "%s tile combinations over this terrain. Four tiles can meet at "
            "a patch and this map names %s, so a count this far past that "
            "means the tile grid is being read wrongly — suspect that before "
            "the shader.", len(combinations), len(used),
        )

    slot_of = {}
    for key in sorted(combinations):
        # Whether the atlas was found is part of the name. Materials are
        # cached by it, so without this an import done before the game
        # folder was set keeps its computed-ramp graph for ever, and
        # setting the folder afterwards appears to do nothing.
        name = "ExM_TileBlend_" + "_".join(
            "%s-%s" % (tile, stems.get(tile, tile)) for tile in key
        ) + ("" if mask_image is not None or len(key) < 2 else "_ramp")
        slot_of[key] = len(mesh.materials)
        mesh.materials.append(
            _blended_tile_material(
                name,
                [images.get(tile) for tile in key],
                ["%s.dds" % stems.get(tile, tile) for tile in key],
                mask_image,
            )
        )

    for key, polygons in combinations.items():
        slot = slot_of[key]
        for index in polygons:
            mesh.polygons[index].material_index = slot

    if missing:
        _report_missing_tiles(missing, used, game_root or "")

    stats = blend_statistics(tilemap.indices, tilemap.side)
    blended = stats["patches"] - stats["single"]
    logger.info(
        "terrain tiles blended: %s texture(s) in %s combination(s); "
        "%s of %s patches blend (%s two-tile, %s three, %s four)",
        len(used), len(combinations), blended, stats["patches"],
        stats["two"], stats["three"], stats["four"],
    )
    if blended == 0:
        logger.warning(
            "no patch on this map has more than one tile at its corners, so "
            "blending changes nothing. Either the map really is uniform or "
            "the tile grid is being read wrongly."
        )
    _report_tile_cell_words(tilemap)


def _report_tile_cell_words(tilemap) -> None:
    """Say what the bytes per cell that are NOT the tile index hold.

    ``level.tile`` stores four bytes a cell and only the low one is
    known to be the tile. The high half has been described as a mask
    set 0..4 — which, if it is, is authored blending data and would
    replace the masks derived here entirely. НЕИЗВЕСТНО: nothing
    interprets it. Reporting the distribution costs one pass and is
    the measurement that would settle it.
    """
    raw = getattr(tilemap, "raw_cells", None)
    if not raw:
        return
    counts = {}
    for base in range(0, len(raw) - 3, 4):
        word = raw[base + 2] | (raw[base + 3] << 8)
        counts[word] = counts.get(word, 0) + 1
    if len(counts) == 1 and 0 in counts:
        logger.info("level.tile: the high word of every cell is 0")
        return
    top = sorted(counts.items(), key=lambda item: -item[1])[:8]
    logger.info(
        "level.tile: %s distinct high word(s) per cell — %s",
        len(counts),
        ", ".join("%s x%s" % (word, count) for word, count in top),
    )


# --- water ------------------------------------------------------------

#: Name of the water plane object.
WATER_OBJECT_NAME = "ExM_Water"

#: Absorption keys give the water its tint. They are absorption
#: coefficients, so a high value means that channel is absorbed and the
#: water looks LESS of it — the colour shown is one minus the
#: coefficient, normalised. UNCONFIRMED as an exact reproduction of what
#: the engine draws; it gets the hue right and nothing depends on it.
WATER_ABSORPTION_KEYS = ("WATERABSRED", "WATERABSGREEN", "WATERABSBLUE")


def build_water(
    heightmap: HeightmapData,
    water_level: float,
    name: str = WATER_OBJECT_NAME,
    *,
    transform=None,
    absorption: tuple[float, float, float] | None = None,
    water_mask=None,
) -> "bpy.types.Object":
    """Build a flat plane at the map's water level.

    Water is not a mesh in the map data. The manifest states a height
    and two textures and the engine draws a plane across the whole
    level::

        WATERLEVEL       286.440
        BASEWATERLEVEL   286.560
        WATERSMALLTEX    WaterSm.tga
        WATERBIGTEX      Water.tga
        WATERABSRED/GREEN/BLUE

    ``WATERLEVEL`` is what this uses. What ``BASEWATERLEVEL`` means is
    UNKNOWN — it sits 0.12 above, which is too small to be a second body
    of water and too large to be rounding; a tide, a shoreline
    reference, or the level before a scripted change are all consistent
    with one sample.

    The height is a terrain height, in the same units as
    ``displace.bin``, and it lands inside that file's range: the sample
    map runs 219.78 to 540.66 and 6.8% of its vertices sit below the
    water line. So the plane goes through the same transform as the
    terrain and needs no scaling of its own.

    The plane spans the terrain exactly, corner to corner, built from
    the same expressions ``build_mesh`` uses for its own corner
    vertices — not from the terrain object's bounding box, which would
    drift if the terrain were ever moved.
    """
    from core.coordinates import CoordinateTransform

    transform = transform if transform is not None else CoordinateTransform()

    step = transform.terrain_grid_step(heightmap.cell_size)
    offset_x = transform.origin_offset.x * transform.xy_scale
    offset_y = transform.origin_offset.z * transform.xy_scale
    z = transform.game_height_to_blender(water_level)

    # Only over ground that is actually below the line. A single plane
    # across the whole level put water in places the original editor
    # shows dry, so the surface is built cell by cell: a quad exists
    # where any of its four corners is submerged.
    #
    # This is a stand-in, and a static read of the editor says for what.
    # ``Landscape::Load()`` opens a **watermap** and, failing to,
    # reports "Cannot open watermap: ... using empty waterfield" — so
    # where water goes is a stored mask, not a consequence of height.
    # ``landscapeWater.cpp`` then flood-fills the connected water tiles
    # to find the shore. The manifest's ``water.raw`` is the obvious
    # candidate for that mask.
    #
    # Until it is read, a basin the map keeps dry is filled here anyway.
    # ``water_mask`` takes it once there is one.
    width, height = heightmap.width, heightmap.height
    corner_index: dict[tuple[int, int], int] = {}
    points: list[tuple[float, float, float]] = []
    quads: list[tuple[int, int, int, int]] = []

    def corner(x: int, y: int) -> int:
        key = (x, y)
        existing = corner_index.get(key)
        if existing is not None:
            return existing
        corner_index[key] = len(points)
        points.append((x * step - offset_x, y * step - offset_y, z))
        return corner_index[key]

    for y in range(height - 1):
        for x in range(width - 1):
            corners = ((x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1))
            if water_mask is not None:
                if not any(
                    _masked(water_mask, cx, cy, width, height) for cx, cy in corners
                ):
                    continue
            elif not any(
                heightmap.get_height(cx, cy) < water_level for cx, cy in corners
            ):
                continue
            quads.append(
                (
                    corner(x, y),
                    corner(x + 1, y),
                    corner(x + 1, y + 1),
                    corner(x, y + 1),
                )
            )

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(points, [], quads)
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    obj[WATER_LEVEL_PROP] = water_level

    material = _water_material(absorption)
    if material is not None:
        mesh.materials.append(material)

    logger.info(
        "water at %.3f: %s quad(s) over %s of terrain vertices",
        water_level, len(quads), _share_below(heightmap, water_level),
    )
    return obj


#: Custom property carrying the level back out on export.
WATER_LEVEL_PROP = "exm_water_level"


def _masked(mask, x: int, y: int, width: int, height: int) -> bool:
    """Whether the watermap marks this terrain vertex as water.

    The map is stored at its own, coarser resolution — 128 x 128 on the
    sample level against 512 x 512 terrain vertices — so the terrain
    position is scaled into it. A cell is water when it is non-zero;
    zero is dry. That is the same test the engine's own flood fill
    makes on this field.
    """
    try:
        mx = min(mask.width - 1, x * mask.width // max(1, width))
        my = min(mask.height - 1, y * mask.height // max(1, height))
        if mask.bytes_per_sample == 2:
            return mask.sample_u16(mx, my) != 0
        # The older one-byte form flags water with 0xFF.
        return mask.sample_u8(mx, my) != 0
    except (AttributeError, IndexError, ZeroDivisionError, ValueError):
        return False


def _share_below(heightmap: HeightmapData, water_level: float) -> str:
    values = heightmap.values
    if not values:
        return "0%"
    below = sum(1 for v in values if v < water_level)
    return f"{100.0 * below / len(values):.1f}%"


def _water_material(absorption):
    """A translucent material tinted by the absorption coefficients.

    Deliberately simple. This stands in for what the engine draws with
    two scrolling textures and refraction, and pretending otherwise
    would put invented detail in front of the user. What it has to get
    right is where the waterline falls, which is geometry, not shading.
    """
    try:
        material = bpy.data.materials.get(WATER_OBJECT_NAME)
        if material is None:
            material = bpy.data.materials.new(WATER_OBJECT_NAME)
        material.use_nodes = True

        tree = material.node_tree
        principled = None
        for node in tree.nodes:
            if getattr(node, "type", "") == "BSDF_PRINCIPLED":
                principled = node
                break
        if principled is None:
            return material

        if absorption is not None:
            tint = tuple(max(0.0, min(1.0, 1.0 - a * 8.0)) for a in absorption)
        else:
            tint = (0.15, 0.35, 0.45)

        principled.inputs["Base Color"].default_value = (*tint, 1.0)
        for name, value in (("Roughness", 0.05), ("Alpha", 0.6)):
            if name in principled.inputs:
                principled.inputs[name].default_value = value

        for attribute in ("blend_method", "shadow_method"):
            try:
                setattr(material, attribute, "BLEND" if attribute == "blend_method" else "NONE")
            except (AttributeError, TypeError):
                pass

        return material
    except (AttributeError, KeyError, RuntimeError, TypeError) as exc:
        logger.debug("could not build the water material: %s", exc)
        return None


# --- the baked terrain texture ----------------------------------------

#: The map's own diffuse, sitting in the map folder under this name. No
#: manifest key points at it; it is found by convention.
LANDSCAPE_FILE = "landscape.dds"

#: Name of the terrain material built from it.
LANDSCAPE_MATERIAL = "ExM_Landscape"


#: The map's lighting, one image over the whole level per time of day,
#: named ``lightmap_<time>_<level>.dds``. Measured on the sample map::
#:
#:     daytime      A8R8G8B8 1024x1024, 11 mips   median 128, max 214
#:     sunrisetime  DXT1     1024x1024, 11 mips
#:     sunsettime   DXT1     1024x1024, 11 mips
#:     nighttime    DXT1     1024x1024, 11 mips   median  57, max 132
#:
#: Daytime is the one kept uncompressed, which is where the quality
#: budget went. Alpha is 255 throughout all four and carries nothing.
LIGHTMAP_PREFIX = "lightmap_"
LIGHTMAP_TIMES = ("daytime", "sunrisetime", "sunsettime", "nighttime")
LIGHTMAP_TIME = "daytime"


def apply_landscape(
    obj: "bpy.types.Object",
    map_folder: str,
    tilemap=None,
    game_root: str | None = None,
    time_of_day: str = LIGHTMAP_TIME,
    blend: bool = False,
) -> bool:
    """Texture the terrain with the map's baked diffuse.

    ``landscape.dds`` is one image covering the whole level: 1024x1024,
    DXT1, and pointedly **no mipmaps**, which is what a texture drawn
    at one fixed scale looks like. 98.5% of its texels are sand tones
    with olive-green patches — the same picture the original editor
    shows, soft-edged and blended.

    That is the difference between the two views this was compared
    against. The editor draws this; the tile map is the close-up detail
    layer the engine blends in on top, and drawing tiles alone produces
    hard-edged squares of a single texture where the reference has
    gradients.

    Mapped 0..1 across the terrain, corner to corner. Returns whether
    it was found.

    ``blend`` puts the detail tiles on through ``core/tile_blend.py``
    rather than one flat tile per cell — which is what the engine does.
    """
    path = _find_landscape(map_folder)
    if path is None:
        logger.info("no %s in %s", LANDSCAPE_FILE, map_folder or "(no folder)")
        return False

    try:
        image = bpy.data.images.load(path, check_existing=True)
    except RuntimeError as exc:
        logger.warning("could not load %s: %s", path, exc)
        return False

    _apply_uv_grid(obj, TERRAIN_UV_LAYER, span=1.0)

    lightmap = _load_lightmap(map_folder, time_of_day)
    scope = os.path.basename(os.path.normpath(map_folder)) if map_folder else ""
    if lightmap is not None:
        scope = f"{scope}_{time_of_day}" if scope else time_of_day
    mesh = obj.data
    while len(mesh.materials):
        mesh.materials.pop()

    # With no tile map there is nothing to add detail with, so the baked
    # image carries the terrain on its own.
    if tilemap is None or game_root is None:
        mesh.materials.append(
            _landscape_material(image, lightmap, None, "", scope)
        )
        for polygon in mesh.polygons:
            polygon.material_index = 0
        # Which of the two, by name. "textured with landscape.dds" on
        # its own reads like success, and the close-up ground silently
        # missing is exactly what it is not.
        reason = (
            "no game folder is set"
            if game_root is None
            else "there is no tile map (Ground Detail Tiles off, or none "
            "in the map folder)"
        )
        logger.info(
            "terrain textured with %s alone — no close-up ground, because %s",
            LANDSCAPE_FILE, reason,
        )
        _report_terrain_inputs(obj, image, lightmap)
        return True

    # One image across 4088 units is roughly four units per texel, which
    # is as blurred as it sounds. The tiles are the close-up detail the
    # engine blends over it, so each tile gets a material of its own
    # that multiplies its texture into the baked colour.
    _apply_uv_grid(obj, TILE_UV_LAYER, span=float(tilemap.side), make_active=False)

    used = tilemap.used_tiles()
    images, missing = _tile_images(tilemap, used, game_root)
    stems = {index: _tile_stem(tilemap, index) for index in used}

    # Blended: one material per set of tiles that meet at a patch, and
    # the detail layer inside it is those tiles composited the way the
    # engine draws them. Unblended: one material per tile, hard-edged.
    combinations = (
        _write_tile_blend(obj, tilemap, _quads_per_tile_cell(obj, tilemap))
        if blend
        else {}
    )
    mask_image = _mask_atlas(game_root, tilemap) if combinations else None

    if combinations:
        for key in sorted(combinations):
            slot = len(mesh.materials)
            mesh.materials.append(
                _landscape_material(
                    image, lightmap, [images.get(tile) for tile in key],
                    "_" + "_".join(f"{tile}-{stems.get(tile, tile)}" for tile in key)
                    + ("" if mask_image is not None or len(key) < 2 else "_ramp"),
                    scope, mask_image,
                )
            )
            for index in combinations[key]:
                mesh.polygons[index].material_index = slot
        _report_tile_cell_words(tilemap)
    else:
        slot_of_tile: dict[int, int] = {}
        for tile_index in used:
            slot_of_tile[tile_index] = len(mesh.materials)
            mesh.materials.append(
                _landscape_material(
                    image, lightmap, images.get(tile_index),
                    f"_{tile_index}_{stems.get(tile_index, tile_index)}",
                    scope,
                )
            )
        _assign_tile_faces(obj, tilemap, slot_of_tile)

    # Blender hands ``active`` to the newest layer, and that is the one
    # Texture Paint and the viewport's texture display read. Put it
    # back on the primary layer so painting the ground lands where the
    # baked image is, not on the tile grid.
    _make_layer_active(obj, TERRAIN_UV_LAYER)

    if missing:
        _report_missing_tiles(missing, used, game_root)

    logger.info(
        "terrain: %s + %s tile detail texture(s)%s",
        LANDSCAPE_FILE, len(used) - len(missing),
        " + lighting" if lightmap is not None else "",
    )
    _report_terrain_inputs(obj, image, lightmap)
    return True


#: The UV layer the tile detail samples.
TILE_UV_LAYER = "UVMap2"


def _make_layer_active(obj: "bpy.types.Object", layer_name: str) -> None:
    layers = getattr(obj.data, "uv_layers", None)
    if not layers:
        return
    for index, layer in enumerate(layers):
        if getattr(layer, "name", "") != layer_name:
            continue
        try:
            layers.active_index = index
            layers.active = layer
        except (AttributeError, TypeError):
            pass
        return


def _load_lightmap(map_folder: str, time_of_day: str = LIGHTMAP_TIME):
    """The map's lighting for one time of day, if it ships one.

    ``none`` skips it. The baked lighting carries real shadows — the
    daytime map runs down to a fifth power at its darkest — and the
    original editor draws the ground without them, so a view meant for
    comparison against the editor wants this off.
    """
    if time_of_day == "none":
        return None
    if not map_folder or not os.path.isdir(map_folder):
        return None

    wanted = f"{LIGHTMAP_PREFIX}{time_of_day}"
    for name in sorted(os.listdir(map_folder)):
        lowered = name.lower()
        if not lowered.startswith(wanted) or not lowered.endswith(".dds"):
            continue
        try:
            return bpy.data.images.load(
                os.path.join(map_folder, name), check_existing=True
            )
        except RuntimeError as exc:
            logger.debug("could not load %s: %s", name, exc)
            return None
    return None


def _assign_tile_faces(obj, tilemap, slot_of_tile: dict[int, int]) -> None:
    """Point each quad at the material for the tile cell it falls in."""
    mesh = obj.data
    grid_width = obj.get(GRID_WIDTH_PROP) or 0
    quads_across = max(1, grid_width - 1)
    per_cell = max(1, round(quads_across / tilemap.side))

    for index, polygon in enumerate(mesh.polygons):
        cell_x = min(tilemap.side - 1, (index % quads_across) // per_cell)
        cell_y = min(tilemap.side - 1, (index // quads_across) // per_cell)
        tile = tilemap.indices[cell_y * tilemap.side + cell_x]
        polygon.material_index = slot_of_tile.get(tile, 0)


def _report_missing_tiles(missing: list[str], used: list[int], game_root: str) -> None:
    tiles_root = os.path.join(game_root, "data", "tiles")
    logger.warning(
        "%s of %s ground detail texture(s) not found", len(missing), len(used)
    )
    logger.warning("  looked under: %s", tiles_root)
    if not os.path.isdir(tiles_root):
        logger.warning(
            "  THAT FOLDER DOES NOT EXIST. level.tile names its textures "
            "relative to it, so none of them can resolve."
        )
    for name in missing[:8]:
        logger.warning("  missing: %s", name)


def _report_terrain_inputs(obj, image, lightmap) -> None:
    """Log every input to the terrain's colour, with its own numbers.

    Three textures and one attribute decide what the ground looks like,
    and when it comes out wrong the question is always which of them.
    Naming them with their sizes, and the colour layers present on the
    mesh, answers that from the import log rather than from a screenshot.
    """
    for label, texture in (("base", image), ("lighting", lightmap)):
        if texture is None:
            continue
        logger.info(
            "  %-9s %s %s",
            label + ":", getattr(texture, "name", "?"),
            tuple(getattr(texture, "size", ())),
        )
        if getattr(texture, "is_dirty", False):
            # Paint lives in the image datablock until it is written
            # out, so a texture edited in Blender looks nothing like
            # the file on disk while every measurement of that file
            # says it is fine. That gap is hard to see from either
            # side, and this is the line that closes it.
            logger.warning(
                "    %s HAS UNSAVED EDITS — what you see is the painted "
                "version, not the file. Image > Reload discards them; "
                "Save Texture As DDS keeps them.",
                getattr(texture, "name", "?"),
            )

    layers = list(getattr(obj.data, "color_attributes", []))
    if not layers:
        logger.info("  colour:   none on the mesh")
        return
    for layer in layers:
        data = getattr(layer, "data", [])
        if not len(data):
            logger.info("  colour:   %r is empty", layer.name)
            continue
        # Across the whole layer, by stride. Reading the first 4096
        # entries of a million-entry layer samples the top edge of the
        # map and reports it as the map — which it did, and the number
        # it gave sent a search for a bug that was not there.
        step = max(1, len(data) // 4096)
        indices = range(0, len(data), step)
        count = 0
        totals = [0.0, 0.0, 0.0]
        peak = 0.0
        for index in indices:
            colour = tuple(data[index].color)
            for channel in range(3):
                totals[channel] += colour[channel]
                peak = max(peak, colour[channel])
            count += 1
        logger.info(
            "  colour:   %r mean R %.2f G %.2f B %.2f, max %.2f "
            "(%s of %s sampled)",
            layer.name, *[t / count for t in totals], peak, count, len(data),
        )


def _find_landscape(map_folder: str) -> str | None:
    if not map_folder or not os.path.isdir(map_folder):
        return None
    for name in os.listdir(map_folder):
        if name.lower() == LANDSCAPE_FILE:
            return os.path.join(map_folder, name)
    return None


def _blend_detail(tree, base, detail_colour):
    """Fold the ground detail into the baked colour.

    ``detail_colour`` is a socket, not a node: with blending on it is
    the output of several tile textures composited together, and there
    is no single node to take it from.

    ``base x detail x 2``, which is what ``diffuse_detail.fx`` states
    and nothing else. There used to be a strength dial in front of this,
    because without the corner blend every tile covered its whole cell
    and a grass tile turned a sandy map olive — the dial was the honest
    middle while the real thing was missing. The real thing is here now
    (``core/tile_blend.py``), so the dial had nothing left to
    compensate for and is gone.

    Measured, so the trade is visible: the baked colour alone averages
    R177 G161 B128, and through the lightmap and colour map it reaches
    R180 G136 B58 — the sand the original editor shows.
    """
    try:
        doubled = tree.nodes.new("ShaderNodeMixRGB")
        doubled.name = "ExM_DetailDouble"
        doubled.blend_type = "MULTIPLY"
        doubled.location = (-650, -300)
        doubled.inputs["Fac"].default_value = 1.0
        tree.links.new(detail_colour, doubled.inputs["Color1"])
        doubled.inputs["Color2"].default_value = (2.0, 2.0, 2.0, 1.0)

        mix = tree.nodes.new("ShaderNodeMixRGB")
        mix.name = "ExM_DetailMix"
        mix.blend_type = "MULTIPLY"
        mix.location = (-450, -150)
        mix.inputs["Fac"].default_value = 1.0
        tree.links.new(base, mix.inputs["Color1"])
        tree.links.new(doubled.outputs["Color"], mix.inputs["Color2"])
        return mix.outputs["Color"]
    except (AttributeError, KeyError, RuntimeError, TypeError) as exc:
        logger.debug("could not blend the ground detail: %s", exc)
        return None


def _landscape_material(image, lightmap=None, detail=None, suffix: str = "", scope: str = "", mask_image=None):
    """The terrain material: baked colour, detail, lighting, colour map.

    Built as a chain, each link only when the map ships the thing it
    needs::

        landscape          the baked colour, one image over the level
          x 2 x detail     the tile texture, for anything seen close up
          x lightmap       the level's baked lighting
          x 2 x colormap   the per-vertex tint

    The doubling before the detail texture is the usual detail-map
    arrangement: a mid-grey detail leaves the base where it was and
    only its variation comes through. Without it, multiplying two
    textures halves the terrain.
    """
    # Scoped to the map. Every level ships its own landscape.dds and
    # its own lighting under the same filenames, so a material cached
    # by name alone would dress the second map imported in the first
    # one's ground.
    name = f"{LANDSCAPE_MATERIAL}_{scope}{suffix}" if scope else f"{LANDSCAPE_MATERIAL}{suffix}"
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing

    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree

    principled = None
    for node in tree.nodes:
        if getattr(node, "type", "") == "BSDF_PRINCIPLED":
            principled = node
            break
    if principled is None:
        return material

    try:
        principled.inputs["Roughness"].default_value = 0.9
    except (AttributeError, KeyError, TypeError):
        pass

    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.name = "Diffuse"
    texture.label = LANDSCAPE_FILE
    texture.image = image
    texture.location = (-900, 0)
    _sample_with(tree, texture, TERRAIN_UV_LAYER)

    source = texture.outputs["Color"]

    if detail is not None:
        # One image, or the set of tiles that meet at a blend patch.
        # The first keeps the name "Detail" either way, so everything
        # that looks for the detail texture still finds it.
        details = list(detail) if isinstance(detail, (list, tuple)) else [detail]
        detail_nodes = []
        for index, one in enumerate(details):
            detail_node = tree.nodes.new("ShaderNodeTexImage")
            detail_node.name = "Detail" if index == 0 else f"Detail{index}"
            detail_node.label = getattr(one, "name", "detail")
            detail_node.image = one
            detail_node.location = (-900, -300 - index * 250)
            _sample_with(tree, detail_node, TILE_UV_LAYER)
            detail_nodes.append(detail_node)
        source = (
            _blend_detail(tree, source, _blend_tiles(tree, detail_nodes, mask_image))
            or source
        )

    if lightmap is not None:
        light_node = tree.nodes.new("ShaderNodeTexImage")
        light_node.name = "Lightmap"
        light_node.label = getattr(lightmap, "name", "lightmap")
        light_node.image = lightmap
        light_node.location = (-900, -600)
        _sample_with(tree, light_node, TERRAIN_UV_LAYER)
        # MODULATE2X, like the colour map and for the same measured
        # reason: the daytime lightmap's median luminance is exactly
        # 128 and its maximum is 214. It never reaches white, so a
        # plain multiply would darken the whole terrain and could not
        # brighten anything — while 128 doubled is neutral and 214
        # doubled lifts the lit ground to 1.68x. The night map centres
        # on 57, which doubled is 0.45: night.
        source = (
            _modulate2x(tree, source, light_node.outputs["Color"], "ExM_LightMix")
            or source
        )

    colour = _colormap_node(tree)
    if colour is not None:
        source = (
            _modulate2x(tree, source, colour.outputs["Color"], "ExM_ColormapMix")
            or source
        )

    try:
        tree.links.new(source, principled.inputs["Base Color"])
    except (AttributeError, KeyError, RuntimeError, TypeError) as exc:
        logger.debug("could not wire the landscape material: %s", exc)

    return material


def _sample_with(tree, texture_node, layer_name: str) -> None:
    """Point a texture node at a named UV layer.

    Every terrain texture says which layer it reads, rather than
    relying on whichever one is active. Leaving that implicit is what
    let the tile layer capture the baked landscape.
    """
    try:
        uv_map = tree.nodes.new("ShaderNodeUVMap")
        uv_map.name = f"ExM_UV_{layer_name}_{texture_node.name}"
        uv_map.uv_map = layer_name
        uv_map.location = (-1200, -300)
        tree.links.new(uv_map.outputs["UV"], texture_node.inputs["Vector"])
    except (AttributeError, KeyError, RuntimeError, TypeError) as exc:
        logger.debug("could not point %s at %s: %s", texture_node.name, layer_name, exc)


def _multiply(tree, first, second, name: str):
    """``a x b``, for a map whose neutral is white."""
    try:
        mix = tree.nodes.new("ShaderNodeMixRGB")
        mix.name = name
        mix.blend_type = "MULTIPLY"
        mix.location = (-500, -300)
        mix.inputs["Fac"].default_value = 1.0
        tree.links.new(first, mix.inputs["Color1"])
        tree.links.new(second, mix.inputs["Color2"])
        return mix.outputs["Color"]
    except (AttributeError, KeyError, RuntimeError, TypeError) as exc:
        logger.debug("could not build %s: %s", name, exc)
        return None


def _modulate2x(tree, first, second, name: str = "ExM_ColormapMix"):
    """``2 x a x b`` — the colour map's own convention.

    Its neutral value is 127, not 255: three quarters of the sample
    map is exactly mid-grey. A plain multiply would therefore halve the
    brightness of most of the terrain, which is the giveaway that this
    is Direct3D's MODULATE2X rather than MODULATE. Doubling afterwards
    puts 127 back at neutral and leaves the rest as a tint either way.
    """
    try:
        mix = tree.nodes.new("ShaderNodeMixRGB")
        mix.name = name
        mix.blend_type = "MULTIPLY"
        mix.location = (-500, 0)
        mix.inputs["Fac"].default_value = 1.0
        tree.links.new(first, mix.inputs["Color1"])
        tree.links.new(second, mix.inputs["Color2"])

        double = tree.nodes.new("ShaderNodeMixRGB")
        double.name = f"{name}_2x"
        double.blend_type = "MULTIPLY"
        double.location = (-250, 0)
        double.inputs["Fac"].default_value = 1.0
        double.inputs["Color2"].default_value = (2.0, 2.0, 2.0, 1.0)
        tree.links.new(mix.outputs["Color"], double.inputs["Color1"])
        return double.outputs["Color"]
    except (AttributeError, KeyError, RuntimeError, TypeError) as exc:
        logger.debug("could not build the modulate2x chain: %s", exc)
        return None


def _apply_uv_grid(
    obj: "bpy.types.Object", layer_name: str, span: float, make_active: bool = True
) -> None:
    """Lay a UV grid over the terrain, ``span`` across the whole map.

    ``make_active`` decides whether this layer becomes the one Blender
    renders and paints with. Only the primary layer may claim it: a
    second layer created afterwards takes the flag by default, and
    every texture node without an explicit UV input then samples
    through it. That is what turned the baked landscape into 256
    copies of itself — the tile layer had quietly become the one it was
    reading.
    """
    mesh = obj.data
    width = obj.get(GRID_WIDTH_PROP) or 0
    if width < 2:
        return

    existing = mesh.uv_layers.get(layer_name)
    layer = existing if existing is not None else mesh.uv_layers.new(name=layer_name)

    step = span / float(width - 1)
    data = layer.data
    for index, loop in enumerate(mesh.loops):
        if index >= len(data):
            break
        vertex = loop.vertex_index
        data[index].uv = ((vertex % width) * step, (vertex // width) * step)

    if make_active:
        try:
            layer.active_render = True
            mesh.uv_layers.active_index = list(mesh.uv_layers).index(layer)
        except (AttributeError, TypeError, ValueError):
            pass
