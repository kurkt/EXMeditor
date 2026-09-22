# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""RoadNetwork <-> Blender Curve objects.

Each ``RoadChain`` becomes one poly curve whose control points are the
road nodes, in order — so dragging a point in Blender moves that road
node, and adding/removing points changes the road's shape. That maps
onto the file format directly: chain order is what the
``FwdZLink``/``BackZLink`` attributes are regenerated from on write.

Positions go through the shared ``core.coordinates.CoordinateTransform``
like every other spatial format, so roads land in the same Blender
space as terrain and objects.

Height handling matches ``world.xml`` objects: road node ``org`` Y is
an offset above the terrain surface (most road nodes on the reference
map have Y exactly 0.000, i.e. sitting on the ground), so it's resolved
against the terrain on import and un-resolved on export.
"""

from __future__ import annotations

import os

import json

import bpy

from core.coordinates import CoordinateTransform
from utils.logging import get_logger
from core.roads import RoadChain, RoadNetwork, RoadNode
from core.terrain import HeightmapData
from utils.math import Vector3

logger = get_logger("blender_io.roads_bridge")

# Custom properties, `exm_` prefixed like every other bridge.
ROAD_CHAIN_PROP = "exm_road_chain"          # marks an object as an imported road
NODE_NAMES_PROP = "exm_road_node_names"      # JSON list, one name per control point
NODE_META_PROP = "exm_road_node_meta"        # JSON list of per-node attributes


def build_road_curves(
    network: RoadNetwork,
    collection: bpy.types.Collection,
    *,
    transform: CoordinateTransform | None = None,
    terrain: HeightmapData | None = None,
) -> list[bpy.types.Object]:
    """Create one Blender poly curve per road chain.

    Returns the created curve objects.
    """
    transform = transform if transform is not None else CoordinateTransform()
    created: list[bpy.types.Object] = []

    for index, chain in enumerate(network.chains):
        if not chain.nodes:
            continue
        name = f"ExM_Road_{index:03d}_{chain.roadset or 'road'}"
        created.append(_build_chain(chain, name, collection, transform, terrain))

    return created


def _build_chain(
    chain: RoadChain,
    name: str,
    collection: bpy.types.Collection,
    transform: CoordinateTransform,
    terrain: HeightmapData | None,
) -> bpy.types.Object:
    curve_data = bpy.data.curves.new(name, type="CURVE")
    curve_data.dimensions = "3D"
    try:
        # The Curve modifier orients what it deforms by this curve's own
        # frame. Blender's default twist is MINIMUM, which derives that
        # frame from the first tangent and an arbitrary reference — and
        # on a horizontal road it comes out rolled a quarter turn, so
        # every arrayed piece stands on its edge. Z_UP pins the up
        # vector to the world's, which is what a road on ground wants.
        #
        # The ribbon path has said exactly this since it was written
        # (see ``texture_road_chains``); the chains the models are
        # arrayed along never got it.
        curve_data.twist_mode = "Z_UP"
    except (AttributeError, TypeError) as exc:
        logger.debug("could not set the twist mode on %s: %s", name, exc)

    spline = curve_data.splines.new("POLY")
    # A new spline starts with one point; add the rest.
    spline.points.add(len(chain.nodes) - 1)

    for point, node in zip(spline.points, chain.nodes):
        org = node.org
        if terrain is not None:
            ground = terrain.sample_at_world(org.x, org.z)
            org = Vector3(org.x, ground + org.y, org.z)
        position = transform.game_to_blender_position(org)
        # Poly control points are 4D (x, y, z, w); w is the point weight
        # and is unused here.
        point.co = (position.x, position.y, position.z, 1.0)

    obj = bpy.data.objects.new(name, curve_data)
    obj[ROAD_CHAIN_PROP] = 1
    # The set the chain belongs to, taken from its first node. It is
    # per-node in the file and uniform along a chain in every sample,
    # and the surface needs it to find the model that says how wide
    # this road is and what it is made of.
    first = chain.nodes[0].roadset if chain.nodes else None
    if first:
        obj[ROADSET_PROP] = first
    obj[NODE_NAMES_PROP] = json.dumps([n.name for n in chain.nodes])
    obj[NODE_META_PROP] = json.dumps([
        {
            "roadset": n.roadset,
            "skin_number": n.skin_number,
            "model_num": n.model_num,
            "as_cliff": n.as_cliff,
            "node_class": n.node_class,
            "raw_attrs": n.raw_attrs,
            "ground_offset": n.org.y,
        }
        for n in chain.nodes
    ])
    collection.objects.link(obj)
    return obj


def extract_road_network(
    curve_objects: list[bpy.types.Object],
    *,
    transform: CoordinateTransform | None = None,
    terrain: HeightmapData | None = None,
) -> RoadNetwork:
    """Read road curves back into a ``RoadNetwork``.

    Objects without ``ROAD_CHAIN_PROP`` are skipped — they weren't
    imported as roads, and there is no operator yet to give a new
    curve the roadset/skin metadata the format needs.

    Control points added in Blender beyond the original node count get
    generated names and inherit the metadata of the last original node,
    which is the only defensible default: a new point on a road is
    almost certainly the same kind of road as the one it extends.
    """
    transform = transform if transform is not None else CoordinateTransform()
    network = RoadNetwork()

    for obj in curve_objects:
        if ROAD_CHAIN_PROP not in obj:
            continue
        network.chains.append(_extract_chain(obj, transform, terrain))

    return network


def _extract_chain(
    obj: bpy.types.Object,
    transform: CoordinateTransform,
    terrain: HeightmapData | None,
) -> RoadChain:
    names: list[str] = json.loads(obj.get(NODE_NAMES_PROP) or "[]")
    meta: list[dict] = json.loads(obj.get(NODE_META_PROP) or "[]")

    chain = RoadChain()
    spline = obj.data.splines[0]

    for index, point in enumerate(spline.points):
        blender_position = Vector3(point.co[0], point.co[1], point.co[2])
        org = transform.blender_to_game_position(blender_position)
        if terrain is not None:
            ground = terrain.sample_at_world(org.x, org.z)
            org = Vector3(org.x, org.y - ground, org.z)

        if index < len(names):
            name = names[index]
            node_meta = meta[index] if index < len(meta) else {}
        else:
            # A point the user added. Name it distinctly so it can't
            # collide with an existing node's name (links are resolved
            # by name, so a collision would corrupt the chain).
            name = f"{obj.name}_new_{index}"
            node_meta = meta[-1] if meta else {}

        chain.nodes.append(RoadNode(
            name=name,
            org=org,
            roadset=node_meta.get("roadset"),
            skin_number=node_meta.get("skin_number"),
            model_num=node_meta.get("model_num"),
            as_cliff=node_meta.get("as_cliff"),
            node_class=node_meta.get("node_class", "RoadNode"),
            raw_attrs=node_meta.get("raw_attrs", {}),
        ))

    return chain


# --- giving a road a surface ------------------------------------------

#: Half-width of a road ribbon, in Blender units. Roads on the sample
#: map run between fences roughly 20 units apart, and the ribbon has to
#: sit inside that. UNCONFIRMED against any width the game stores:
#: ``LevelRoads.xml`` carries none that has been found, so this is a
#: figure chosen to look right rather than one read from the data.
ROAD_HALF_WIDTH = 4.0

#: How far above the ground the ribbon floats, to keep it from fighting
#: the terrain for the same pixels.
ROAD_LIFT = 0.15

#: Textures to try for the surface, in order. The census found a
#: ``road`` shader on 115 materials and ``road_detail`` on 90, so the
#: game does texture its roads; which file it uses has not been
#: established, and these are the plausible names in the texture tree.
ROAD_TEXTURES = ("road.dds", "asfalt.dds", "beton_asfalt.dds", "ground.dds")

ROAD_MATERIAL = "ExM_Road"


def road_profile_from_model(model_path: str):
    """Width and textures of a road, read from its own model.

    Measured on ``road_country.gam``::

        1 mesh, 5 materials, all shader 'road'
        X span 19.77   the length of one piece, along the chain
        Z span 14.45   the width of the carriageway
        Y span  0.19   flat, as a road is
        UV 0..1 in both axes — one full texture per piece

    Two things follow. The width is 14.45 and not something to guess at.
    And the texture covers **one segment**, corner to corner, rather
    than repeating by distance — so a ribbon has to lay 0..1 between
    each pair of nodes, not tile by length.

    The five materials are ``country_1``, ``country_2``, ``country_3``,
    ``country_1``, ``country_1``, and ``skinNumber`` in
    ``levelroads.xml`` runs 0..3 — so **skinNumber picks the material
    inside the model**, which is where the variation along a road comes
    from. It is not a choice between models.

    Returns ``(half_width, [texture per skinNumber])``, or None.
    """
    try:
        from formats.exm.gam import read_model

        model = read_model(model_path)
    except Exception as exc:  # noqa: BLE001 - falls back to the default
        logger.debug("could not read the road model %s: %s", model_path, exc)
        return None

    xs = [p.x for mesh in model.meshes for p in mesh.positions]
    zs = [p.z for mesh in model.meshes for p in mesh.positions]
    if not xs:
        return None

    # X is the width, Z the length — not "the narrower one is the
    # width", which is what this used to assume and which had them the
    # wrong way round. Two pieces of the same set settle it::
    #
    #     road_country.gam         X 19.770   Y 0.188   Z 14.447
    #     cross_road_sml/small.gam X 19.770   Y 0.477   Z 31.919
    #
    # The X span is identical across the set and the Z span is not,
    # because X is the road grid's step — shared by every piece — while
    # Z is how far that particular piece runs.
    width = max(xs) - min(xs)
    length = max(zs) - min(zs)
    logger.info(
        "  %s: %.1f wide, %.1f long",
        os.path.basename(model_path), width, length,
    )
    # Every material in file order: skinNumber indexes straight into
    # this list.
    textures = [material.diffuse or "" for material in model.materials]

    if width <= 0.0:
        return None
    return width / 2.0, textures


def build_road_surfaces(
    objects,
    game_root: str | None,
    collection=None,
    road_sets=None,
    terrain=None,
    transform=None,
) -> int:
    """Build a ribbon mesh for each road chain.

    A static read of the editor says this is the right shape of answer.
    ``roadManager.cpp`` holds a graph of ``GRoadNode`` and builds a
    ``GeomObjectRoad`` from it, reporting the result as a count of road
    triangles — a road is **generated geometry**, not a row of placed
    models. ``roadProjector.fx`` then projects it onto the terrain,
    which is why it takes the ground's shape instead of hovering over
    it.

    So the models named in ``Roads.xml`` are what the geometry is built
    from and textured with, rather than instances to be dropped at each
    node. Placing them one per node — which this add-on tried — put
    overlapping copies along every chain.

    The curve stays exactly as it is — the export reads it, and nothing
    here touches its points. What this adds is a sibling mesh that
    carries the surface.

    Written as geometry rather than as a curve bevel because a bevel is
    a request to Blender that either works or silently does not: the
    profile has to be in the dependency graph, the curve has to be told
    to fill, and when any of that is off the road draws as a bare line
    with nothing to say why. Three rounds went to that. A mesh built
    vertex by vertex is either there or is a test failure.

    Returns how many surfaces were built.
    """
    road_sets = road_sets or {}
    profiles: dict = {}
    built = 0

    for obj in objects:
        curve = getattr(obj, "data", None)
        splines = list(getattr(curve, "splines", []) or [])
        if not splines:
            continue

        points = [tuple(p.co)[:3] for p in splines[0].points]
        if len(points) < 2:
            continue

        half_width, materials = _profile_for(
            obj, road_sets, game_root, profiles
        )
        skins = [
            int(entry.get("skin_number") or 0)
            for entry in _node_meta(obj, len(points))
        ]
        mesh = _ribbon(
            points, f"{obj.name}_surface", half_width, skins, len(materials),
            terrain, transform,
        )
        if mesh is None:
            continue
        for material in materials:
            if material is not None:
                mesh.materials.append(material)

        surface = bpy.data.objects.new(f"{obj.name}_surface", mesh)
        surface[ROAD_SURFACE_PROP] = obj.name
        try:
            surface.location = obj.location
            surface.rotation_euler = obj.rotation_euler
            surface.scale = obj.scale
        except (AttributeError, TypeError):
            pass

        if collection is not None:
            try:
                collection.objects.link(surface)
            except (AttributeError, RuntimeError, TypeError) as exc:
                logger.debug("could not link %s: %s", surface.name, exc)

        built += 1

    for name, (half_width, mats) in sorted(profiles.items()):
        logger.info(
            "road set %r: %.1f units wide, %s skin(s)",
            name or "(unnamed)", half_width * 2, len(mats),
        )
    logger.info("roads: %s surface(s) built", built)
    return built


def _ground(where, fallback: float, terrain, transform) -> float:
    """The terrain height under a ribbon vertex, plus a little.

    A chain gives heights only at its nodes, and a straight run between
    two of them cuts through a crest and floats over a dip — which is
    what road sections rising off the map are. The engine has the same
    problem and solves it by projecting: ``roadProjector.fx`` puts the
    road on the ground rather than trusting the geometry's own height.

    Sampling every vertex does the same thing. The small lift keeps the
    surface from fighting the terrain for the same pixels.
    """
    if terrain is None or transform is None:
        return fallback + ROAD_LIFT

    try:
        from utils.math import Vector3

        game = transform.blender_to_game_position(Vector3(where[0], where[1], fallback))
        height = terrain.sample_at_world(game.x, game.z)
        return transform.game_height_to_blender(height) + ROAD_LIFT
    except (AttributeError, IndexError, TypeError, ValueError) as exc:
        logger.debug("could not project a road vertex: %s", exc)
        return fallback + ROAD_LIFT


def _profile_for(obj, road_sets, game_root, cache):
    """The width and material for this chain's road set.

    Cached per set: a map holds hundreds of chains across four sets,
    and reading the same model for each of them would read it hundreds
    of times.
    """
    name = obj.get(ROADSET_PROP) or ""
    if name in cache:
        return cache[name]

    half_width = ROAD_HALF_WIDTH
    textures: list = []

    road_set = road_sets.get(name)
    if road_set is not None and game_root:
        from formats.exm.model_catalog import resolve_game_relative_path

        model_path = resolve_game_relative_path(road_set.straight(), game_root)
        if model_path:
            measured = road_profile_from_model(model_path)
            if measured is not None:
                half_width, textures = measured

    materials = [
        _road_material(game_root, texture) for texture in textures
    ] or [_road_material(game_root, "")]

    cache[name] = (half_width, materials)
    return cache[name]


#: Custom property on a road curve naming its set in ``roads.xml``.
#: Written by ``build_road_curves`` from the chain's own nodes.
ROADSET_PROP = "exm_roadset"


#: Custom property linking a surface back to the chain it came from, so
#: the export can tell generated geometry from anything the user added.
ROAD_SURFACE_PROP = "exm_road_surface_of"


def _ribbon(
    points,
    name: str,
    half_width: float = ROAD_HALF_WIDTH,
    skins=None,
    skin_count: int = 1,
    terrain=None,
    transform=None,
):
    """A flat strip of quads following ``points``.

    Each point gets two vertices, offset left and right of the road's
    direction in the horizontal plane — so the surface lies down
    however steeply the chain climbs. The UV runs 0..1 across the width
    and along the length in units of the road's own width, which makes
    the texture repeat at its natural scale instead of stretching over
    a kilometre of road.
    """
    import math

    # Cross-sections between the nodes as well as at them. A stretch is
    # 17 to 20 units on the sample map and the ground moves under it,
    # so a single straight span cuts through crests and floats over
    # dips — which is what road sections rising off the map are. One
    # extra section per road-width of length is enough to follow it.
    step = max(1.0, half_width * 2.0)
    dense: list = []
    dense_v: list = []
    dense_skin: list = []

    # skinNumber is an index INTO the model's materials, so one past the
    # end is a mismatch between the map and the model — not something to
    # wrap around quietly. It happens when a model has been re-exported
    # with fewer materials than it shipped with, and the only symptom is
    # a road wearing the wrong texture.
    if skins:
        beyond = sorted({s for s in skins if s >= max(1, skin_count)})
        if beyond:
            logger.warning(
                "%s: skinNumber %s named, but the model carries %s material(s). "
                "Those stretches are being drawn with the wrong one. The model "
                "has probably lost materials in an export.",
                name, ", ".join(str(s) for s in beyond[:6]), skin_count,
            )

    for index in range(len(points) - 1):
        here, ahead = points[index], points[index + 1]
        span = math.dist(here[:2], ahead[:2])
        cuts = max(1, int(span // step))
        skin = skins[index] % max(1, skin_count) if skins and index < len(skins) else 0
        for cut in range(cuts):
            fraction = cut / cuts
            dense.append(
                tuple(here[axis] + (ahead[axis] - here[axis]) * fraction for axis in range(3))
            )
            dense_v.append(index + fraction)
            dense_skin.append(skin)

    dense.append(points[-1])
    dense_v.append(float(len(points) - 1))
    points = dense

    vertices = []
    faces = []
    uvs = []
    face_skins: list = []
    travelled = 0.0

    for index, point in enumerate(points):
        ahead = points[min(index + 1, len(points) - 1)]
        behind = points[max(index - 1, 0)]
        dx = ahead[0] - behind[0]
        dy = ahead[1] - behind[1]
        length = math.hypot(dx, dy)
        if length < 1e-6:
            dx, dy, length = 1.0, 0.0, 1.0
        # Left of travel, in the horizontal plane.
        nx, ny = -dy / length, dx / length

        x, y, z = point
        left = (x - nx * half_width, y - ny * half_width)
        right = (x + nx * half_width, y + ny * half_width)
        vertices.append((*left, _ground(left, z, terrain, transform)))
        vertices.append((*right, _ground(right, z, terrain, transform)))

        if index:
            previous = points[index - 1]
            travelled += math.dist(previous[:2], point[:2])
        # One full texture between each pair of nodes, which is how the
        # road model is unwrapped: its own UVs run 0..1 across a single
        # piece rather than repeating by distance. Tiling by length
        # instead stretched or cut the surface at every node.
        along = dense_v[index] if index < len(dense_v) else float(index)
        uvs.append((0.0, along))
        uvs.append((1.0, along))

        if index:
            base = (index - 1) * 2
            faces.append((base, base + 1, base + 3, base + 2))
            skin = dense_skin[index - 1] if index - 1 < len(dense_skin) else 0
            face_skins.append(skin)

    if not faces:
        return None

    try:
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
    except (AttributeError, RuntimeError, TypeError) as exc:
        logger.warning("could not build the road surface %s: %s", name, exc)
        return None

    try:
        layer = mesh.uv_layers.new(name="UVMap")
        for loop_index, loop in enumerate(mesh.loops):
            if loop_index < len(layer.data) and loop.vertex_index < len(uvs):
                layer.data[loop_index].uv = uvs[loop.vertex_index]
    except (AttributeError, IndexError, RuntimeError, TypeError) as exc:
        logger.debug("could not lay UVs on %s: %s", name, exc)

    # Each stretch takes the skin its own node names, which is what
    # varies the surface along a road.
    try:
        for polygon, skin in zip(mesh.polygons, face_skins):
            polygon.material_index = skin
    except (AttributeError, TypeError) as exc:
        logger.debug("could not assign skins on %s: %s", name, exc)

    return mesh


def texture_road_chains(objects, game_root: str | None, collection=None) -> int:
    """Turn road splines into ribbons with a surface.

    A road in this game is not geometry — ``LevelRoads.xml`` holds a
    chain of points and the engine lays the surface itself. A spline
    shows where a road goes and nothing about what it looks like, which
    makes judging a map against the original editor harder than it
    needs to be.

    Extruding the curve gives it width; the taper and bevel stay at
    zero so the ribbon is flat. The curve keeps its points and its
    custom properties, so the export still writes the same chain.

    Returns how many were given a surface.
    """
    material = _road_material(game_root)
    profile = _road_profile(collection)
    done = 0

    for obj in objects:
        curve = getattr(obj, "data", None)
        if curve is None or not hasattr(curve, "splines"):
            continue

        try:
            # A flat ribbon along the spline, not a wall along it.
            # ``extrude`` pushes the curve along its own Z and turns a
            # road into a vertical strip — which is what "white
            # noodles" were. A bevel object sweeps a profile instead,
            # and a two-point horizontal profile sweeps a flat surface.
            curve.extrude = 0.0
            curve.bevel_depth = 0.0
            curve.offset = 0.0
            curve.bevel_object = profile
            curve.use_fill_caps = False
            curve.dimensions = "3D"
            # A 3D curve fills nothing unless told to. Without this the
            # sweep exists and has no surface, which looks exactly like
            # a curve that was never beveled at all.
            curve.fill_mode = "FULL"
            curve.resolution_u = 4
            # Keeps the ribbon level instead of rolling with the
            # spline's own twist as it climbs and falls.
            curve.twist_mode = "Z_UP"
        except (AttributeError, TypeError) as exc:
            logger.debug("could not widen %s: %s", getattr(obj, "name", "?"), exc)
            continue

        if material is not None:
            try:
                if not len(curve.materials):
                    curve.materials.append(material)
            except (AttributeError, TypeError) as exc:
                logger.debug("could not surface %s: %s", obj.name, exc)

        # Curves generate UVs along their own length, which is what a
        # road surface wants: the texture runs with the road rather
        # than being projected across it.
        try:
            curve.use_uv_as_generated = True
        except (AttributeError, TypeError):
            pass

        try:
            obj.location = (obj.location[0], obj.location[1], obj.location[2] + ROAD_LIFT)
        except (AttributeError, IndexError, TypeError):
            pass

        done += 1

    logger.info(
        "roads: %s chain(s) given a %s-unit surface", done, ROAD_HALF_WIDTH * 2
    )
    if done and profile is not None:
        # Stated so a road that still draws as a line can be told apart
        # from one that was never widened: this says the sweep was set
        # up, and anything after that is Blender evaluating it.
        logger.info(
            "  profile %r, %s point(s), linked: %s",
            getattr(profile, "name", "?"),
            len(profile.data.splines[0].points) if profile.data.splines else 0,
            bool(getattr(profile, "users_collection", None))
            or "unknown",
        )
    return done


#: Name of the shared profile curve the ribbons sweep.
ROAD_PROFILE = "ExM_RoadProfile"


def _road_profile(collection=None):
    """A two-point horizontal line, swept along each road.

    One profile shared by every chain, so changing the road width is
    one edit rather than one per chain.

    **Linked into the scene, and hidden.** An object that belongs to no
    collection is not in the dependency graph, and Blender evaluates a
    bevel through the graph — so an unlinked profile sweeps nothing and
    the roads stay bare lines. That is what they were doing. Hiding it
    keeps a stray eight-unit line from sitting at the origin.
    """
    existing = bpy.data.objects.get(ROAD_PROFILE)
    if existing is not None:
        # It may have survived from an earlier import — or from an
        # earlier version that never linked it. Linking again is
        # harmless and is the difference between a road with a surface
        # and a bare line.
        _link_profile(existing, collection)
        return existing

    try:
        data = bpy.data.curves.new(ROAD_PROFILE, type="CURVE")
        data.dimensions = "3D"
        spline = data.splines.new("POLY")
        spline.points.add(1)
        spline.points[0].co = (-ROAD_HALF_WIDTH, 0.0, 0.0, 1.0)
        spline.points[1].co = (ROAD_HALF_WIDTH, 0.0, 0.0, 1.0)

        obj = bpy.data.objects.new(ROAD_PROFILE, data)
    except (AttributeError, RuntimeError, TypeError) as exc:
        logger.warning("could not build the road profile: %s", exc)
        return None

    _link_profile(obj, collection)
    return obj


def _link_profile(obj, collection) -> None:
    """Put the profile in the scene and out of the way."""
    if collection is None:
        logger.warning(
            "no collection for the road profile; roads will stay bare lines"
        )
    else:
        try:
            if obj not in list(collection.objects):
                collection.objects.link(obj)
        except (AttributeError, RuntimeError, TypeError) as exc:
            logger.warning(
                "could not link the road profile: %s — roads will stay bare "
                "lines, because a bevel is evaluated through the dependency "
                "graph and an unlinked object is not in it", exc,
            )

    for attribute in ("hide_viewport", "hide_render", "hide_select"):
        try:
            setattr(obj, attribute, True)
        except (AttributeError, TypeError):
            pass


def _road_material(game_root: str | None, texture_name: str = ""):
    """A material for the road surface, from the road model's own texture.

    Alpha-blended, because ``road.fx`` says so in as many words: "simple
    diffuse shader for roads (alpha-blended)". That is why a road in the
    original editor melts into the ground at its edges instead of
    sitting on it as a slab.
    """
    name = f"{ROAD_MATERIAL}_{os.path.splitext(texture_name)[0]}" if texture_name else ROAD_MATERIAL
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
        principled.inputs["Roughness"].default_value = 0.95
    except (AttributeError, KeyError, TypeError):
        pass

    for attribute, value in (("blend_method", "BLEND"), ("shadow_method", "NONE")):
        try:
            setattr(material, attribute, value)
        except (AttributeError, TypeError):
            pass

    path = None
    chosen = ""
    if game_root:
        from blender_io.texture_bridge import resolve_texture

        for candidate in ([texture_name] if texture_name else []) + list(ROAD_TEXTURES):
            path = resolve_texture(candidate, game_root)
            if path is not None:
                chosen = candidate
                break

    if path is None:
        # No texture: a dark neutral surface still reads as a road
        # against the ground, which is the point of drawing it at all.
        try:
            principled.inputs["Base Color"].default_value = (0.22, 0.20, 0.18, 1.0)
        except (AttributeError, KeyError, TypeError):
            pass
        logger.info(
            "none of %s found; roads get a plain dark surface",
            ", ".join(ROAD_TEXTURES),
        )
        return material

    try:
        image = bpy.data.images.load(path, check_existing=True)
    except RuntimeError as exc:
        logger.debug("could not load %s: %s", path, exc)
        return material

    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.name = "Diffuse"
    texture.label = chosen
    texture.image = image
    texture.location = (-400, 0)
    try:
        tree.links.new(texture.outputs["Color"], principled.inputs["Base Color"])
    except (AttributeError, KeyError, RuntimeError, TypeError) as exc:
        logger.debug("could not wire the road material: %s", exc)

    return material


# --- roads as the models the engine lays ------------------------------

#: Custom property on a placed segment, naming the chain it belongs to.
ROAD_SEGMENT_PROP = "exm_road_segment_of"


def place_road_models(
    objects, game_root: str | None, collection=None, road_sets=None, transform=None
) -> int:
    """Place the road's own models along each chain, as the engine does.

    A generated ribbon can only ever approximate a road: it guesses the
    width, guesses how the texture sits, and joins at junctions in a
    way nothing in the data describes. The engine does none of that —
    it lays a model per node, chosen by ``ModelNum`` and ``skinNumber``
    from the set named in ``roads.xml``.

    Placing those models puts the game's own geometry in the scene, so
    a road is the shape, width and surface the game draws rather than
    an approximation of them.

    The chain curves are untouched; the export still reads their
    points. Segments are separate objects, marked so they can be told
    from anything the user has added.

    Returns how many segments were placed.
    """
    road_sets = road_sets or {}
    cache: dict = {}
    placed = 0
    missing: set = set()

    for obj in objects:
        curve = getattr(obj, "data", None)
        splines = list(getattr(curve, "splines", []) or [])
        if not splines:
            continue

        points = [tuple(p.co)[:3] for p in splines[0].points]
        meta = _node_meta(obj, len(points))
        if len(points) < 2:
            continue

        for index, point in enumerate(points):
            entry = meta[index] if index < len(meta) else {}
            set_name = entry.get("roadset") or obj.get(ROADSET_PROP) or ""
            road_set = road_sets.get(set_name)
            if road_set is None:
                missing.add(set_name)
                continue

            model_path = road_set.piece(
                int(entry.get("model_num") or 0), int(entry.get("skin_number") or 0)
            )
            mesh, long_axis = _segment_mesh(
                model_path, game_root, cache, transform
            )
            if mesh is None:
                missing.add(model_path or set_name)
                continue

            segment = bpy.data.objects.new(f"{obj.name}_seg{index}", mesh)
            segment[ROAD_SEGMENT_PROP] = obj.name
            _aim(segment, point, points[min(index + 1, len(points) - 1)], long_axis)

            if collection is not None:
                try:
                    collection.objects.link(segment)
                except (AttributeError, RuntimeError, TypeError) as exc:
                    logger.debug("could not link %s: %s", segment.name, exc)
            placed += 1

    if missing:
        logger.warning(
            "%s road piece(s) could not be resolved: %s",
            len(missing), ", ".join(sorted(str(m) for m in missing)[:4]),
        )
    logger.info("roads: %s segment(s) placed from their own models", placed)
    return placed


def _node_meta(obj, count: int) -> list:
    """Per-node attributes stored on the chain at import."""
    import json

    raw = obj.get(NODE_META_PROP)
    if not raw:
        return [{}] * count
    try:
        loaded = json.loads(raw)
    except (TypeError, ValueError):
        return [{}] * count
    return loaded if isinstance(loaded, list) else [{}] * count


def _segment_mesh(model_path: str, game_root: str | None, cache: dict, transform):
    """The mesh for one road piece, and which axis it runs along.

    Cached: a map places hundreds of segments from a handful of models,
    and every one of them would otherwise be read from disk again.
    """
    if model_path in cache:
        return cache[model_path]

    if not model_path or not game_root:
        cache[model_path] = (None, "x")
        return cache[model_path]

    from formats.exm.model_catalog import resolve_game_relative_path

    resolved = resolve_game_relative_path(model_path, game_root)
    if not resolved:
        cache[model_path] = (None, "x")
        return cache[model_path]

    try:
        from blender_io.mesh_bridge import build_model_mesh
        from formats.exm.gam import read_model

        model = read_model(resolved)
        name = f"ExM_RoadPiece_{os.path.splitext(os.path.basename(resolved))[0]}"
        mesh = build_model_mesh(
            model, name, transform=transform, game_root=game_root
        )
    except Exception as exc:  # noqa: BLE001 - counted as missing, not fatal
        logger.debug("could not build %s: %s", resolved, exc)
        cache[model_path] = (None, "x")
        return cache[model_path]

    xs = [p.x for m in model.meshes for p in m.positions]
    zs = [p.z for m in model.meshes for p in m.positions]
    long_axis = "x"
    if xs and zs and (max(zs) - min(zs)) > (max(xs) - min(xs)):
        long_axis = "z"

    logger.info(
        "  road piece %s: runs along %s",
        os.path.basename(resolved), long_axis.upper(),
    )
    cache[model_path] = (mesh, long_axis)
    return cache[model_path]


def _aim(segment, here, ahead, long_axis: str) -> None:
    """Put a segment at ``here``, turned to face ``ahead``.

    The model's own long axis is what runs along the road, so the turn
    is measured from that rather than assumed to be X. Which axis it is
    comes from the model's bounding box, not from a convention nobody
    has written down.
    """
    import math

    try:
        segment.location = here
    except (AttributeError, TypeError):
        return

    dx = ahead[0] - here[0]
    dy = ahead[1] - here[1]
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return

    heading = math.atan2(dy, dx)
    if long_axis == "z":
        # The piece runs along its own Z, which is Blender's Y here, so
        # it starts a quarter turn away from the heading.
        heading -= math.pi / 2.0

    try:
        segment.rotation_euler = (0.0, 0.0, heading)
    except (AttributeError, TypeError):
        pass


# --- roads that follow their chain ------------------------------------

#: Names of the two modifiers, so a rebuild can find and replace them.
ARRAY_MODIFIER = "ExM_RoadArray"
CURVE_MODIFIER = "ExM_RoadCurve"

#: The axis a road piece runs along **in Blender**, after the import
#: has swapped the game's Y and Z.
#:
#: Measured on ``road_country.gam``::
#:
#:     model space     X 19.770   Y  0.188   Z 14.447
#:     Blender space   X 19.770   Y 14.447   Z  0.188
#:
#: So the piece is 19.77 across, 14.45 along and flat — and it runs
#: along Y. Built without that swap it stands on edge, 14.4 units tall,
#: and an array offsets it straight up: one bug behind wrong
#: orientation, wrong position and far too many copies at once.
#:
#: X is 19.770 on ``cross_road_sml/small.gam`` too, while its Z is
#: 31.919 — the width is the road grid's step and shared by the set,
#: the length is the piece's own.
SEGMENT_LENGTH_AXIS = "Y"


def build_roads_from_models(
    objects,
    game_root: str | None,
    collection=None,
    road_sets=None,
    transform=None,
) -> int:
    """Lay each road's own model along its chain, with modifiers.

    Blender arrays a mesh along a curve and deforms it to follow — and
    the result stays live, so moving a chain point reshapes the road
    instead of leaving a mesh built at import time behind. That is the
    thing a generated ribbon could never do.

    It also settles the texture. A ribbon has to invent UVs, and
    stretching the whole of ``country_1.dds`` across the carriageway
    put the texture's dark border down the middle of the road and its
    lane markings across it. The model carries its own unwrap, its own
    five materials and its own width; using it means none of that is
    guessed.

    The chain curve is untouched — the export still reads its points.

    Returns how many roads were built.
    """
    road_sets = road_sets or {}
    meshes: dict = {}
    built = 0

    for obj in objects:
        name = obj.get(ROADSET_PROP) or ""
        mesh, length = _segment_mesh_for(
            name, road_sets, game_root, meshes, transform
        )
        if mesh is None:
            continue

        surface = bpy.data.objects.new(f"{obj.name}_road", mesh)
        surface[ROAD_SURFACE_PROP] = obj.name
        try:
            surface.location = obj.location
        except (AttributeError, TypeError):
            pass

        if collection is not None:
            try:
                collection.objects.link(surface)
            except (AttributeError, RuntimeError, TypeError) as exc:
                logger.debug("could not link %s: %s", surface.name, exc)
                continue

        if not _add_modifiers(surface, obj, length):
            continue
        if built == 0:
            _report_road_frame(surface, obj, length)
        built += 1

    if built:
        logger.info(
            "roads: %s chain(s) built from their own models, following the "
            "curve — edit a chain and the road follows", built,
        )
    return built


def _report_road_frame(surface, chain, length: float) -> None:
    """Everything that decides which way a road piece faces, once.

    Reported for the first road only. A piece standing on its edge can
    come from the mesh, from the curve's own frame, or from the
    modifier — and the three are told apart by numbers that are all
    available here and by nothing that is visible in the viewport.
    """
    try:
        mesh = surface.data
        points = [tuple(p.co)[:3] for p in chain.data.splines[0].points]
        spans = [
            max(c[axis] for c in points) - min(c[axis] for c in points)
            for axis in range(3)
        ]
        vertices = [tuple(v.co) for v in mesh.vertices]
        extents = [
            max(c[axis] for c in vertices) - min(c[axis] for c in vertices)
            for axis in range(3)
        ]
        logger.info(
            "road frame (%s): piece extents X %.2f Y %.2f Z %.2f, "
            "length axis %s = %.2f", surface.name, *extents,
            SEGMENT_LENGTH_AXIS, length,
        )
        logger.info(
            "  chain %r: %s point(s), spans X %.1f Y %.1f Z %.1f, "
            "dimensions=%s twist=%s",
            chain.name, len(points), *spans,
            getattr(chain.data, "dimensions", "?"),
            getattr(chain.data, "twist_mode", "?"),
        )
        # Where the chain actually IS, not just how big it is. The
        # first report printed spans alone and could not answer whether
        # the piece sits anywhere near the curve it is deformed along.
        logger.info(
            "  chain starts at %s; the piece sits at %s",
            tuple(round(v, 1) for v in points[0]),
            tuple(round(v, 1) for v in getattr(surface, "location", ())),
        )
        logger.info(
            "  object: location=%s rotation=%s; modifiers=%s",
            tuple(round(v, 2) for v in getattr(surface, "location", ())),
            tuple(round(v, 3) for v in getattr(surface, "rotation_euler", ())),
            [
                (m.type, getattr(m, "deform_axis", None),
                 getattr(m, "fit_type", None))
                for m in surface.modifiers
            ],
        )
    except (AttributeError, IndexError, TypeError, ValueError) as exc:
        logger.debug("could not report the road frame: %s", exc)


def _roll_piece(model) -> None:
    """Turn a road piece a quarter turn about its length, in place.

    See ``core.roads.roll_about_length`` for why. Applied to the model
    rather than to the built mesh so that positions and normals turn
    together: the normals are handed to Blender as custom split
    normals, and rotating the geometry underneath them would light the
    road as if it were still standing on its edge.

    The model here is freshly read for this road set and shared with
    nobody, so rolling it in place costs no copy.
    """
    from core.roads import roll_about_length
    from utils.math import Vector3

    # The piece's own axes at this point are the model's: X across, Y
    # up, Z along. The roll is about the LENGTH, which the transform
    # will turn into Blender's Y — so in model space that is the axis
    # this leaves alone.
    for mesh in model.meshes:
        mesh.positions = [
            Vector3(*roll_about_length(p.x, p.y, p.z)) for p in mesh.positions
        ]
        if mesh.normals:
            mesh.normals = [
                Vector3(*roll_about_length(n.x, n.y, n.z)) for n in mesh.normals
            ]


def _segment_mesh_for(set_name: str, road_sets, game_root, cache, transform=None):
    """The straight piece of a road set, and how long it is."""
    if set_name in cache:
        return cache[set_name]

    cache[set_name] = (None, 0.0)
    road_set = road_sets.get(set_name)
    if road_set is None or not game_root:
        return cache[set_name]

    from formats.exm.model_catalog import resolve_game_relative_path

    path = resolve_game_relative_path(road_set.straight(), game_root)
    if not path:
        logger.warning("road set %r: %s not found", set_name, road_set.straight())
        return cache[set_name]

    try:
        from blender_io.mesh_bridge import build_model_mesh
        from formats.exm.gam import read_model

        model = read_model(path)
        # Before the mesh is built, so the custom split normals this
        # model carries are turned with it rather than left pointing
        # the old way — a road lit as if it were still on its edge
        # looks almost right and is not.
        _roll_piece(model)
        mesh = build_model_mesh(
            model,
            f"ExM_RoadPiece_{os.path.splitext(os.path.basename(path))[0]}",
            transform=transform,
            game_root=game_root,
        )
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        logger.warning("could not build %s: %s", path, exc)
        return cache[set_name]

    # The length is the model's Z, which the transform turns into
    # Blender's Y. Measured before the swap, because that is where the
    # model's own numbers live — and the roll above leaves Z alone, so
    # it is still the length here.
    zs = [p.z for m in model.meshes for p in m.positions]
    length = (max(zs) - min(zs)) if zs else 0.0
    if length <= 0.0:
        logger.warning("%s has no length along %s", path, SEGMENT_LENGTH_AXIS)
        return cache[set_name]

    logger.info(
        "road set %r: %s, %.2f units per piece",
        set_name, os.path.basename(path), length,
    )
    cache[set_name] = (mesh, length)
    return cache[set_name]


def _add_modifiers(surface, chain, length: float) -> bool:
    """Array the piece along the chain, then bend it to follow.

    Order matters: the array has to run before the curve, or the copies
    are made from geometry that has already been bent and the road
    doubles back on itself.
    """
    try:
        array = surface.modifiers.new(ARRAY_MODIFIER, "ARRAY")
        array.fit_type = "FIT_CURVE"
        array.curve = chain
        array.use_relative_offset = True
        # One piece-length of offset along the axis the piece runs on,
        # and none across it.
        array.relative_offset_displace = [
            1.0 if SEGMENT_LENGTH_AXIS == "X" else 0.0,
            1.0 if SEGMENT_LENGTH_AXIS == "Y" else 0.0,
            1.0 if SEGMENT_LENGTH_AXIS == "Z" else 0.0,
        ]
        array.use_merge_vertices = True
        array.merge_threshold = 0.01

        deform = surface.modifiers.new(CURVE_MODIFIER, "CURVE")
        deform.object = chain
        deform.deform_axis = f"POS_{SEGMENT_LENGTH_AXIS}"
    except (AttributeError, KeyError, RuntimeError, TypeError) as exc:
        logger.warning("could not set up %s: %s", surface.name, exc)
        return False
    return True
