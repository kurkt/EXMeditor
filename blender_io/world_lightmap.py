# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tint roads and grass with the lightmap, the way the game does.

Not a lighting question. The game's own shader sources say, in
``data/shaders/road.fx``::

    Out.Tex1 = mul( float4( In.Pos, 1 ), mWorld ).xz * lightmapScale;
    return tex2D( DiffSampler, In.Tex0 ) * tex2D( LightmapSampler, In.Tex1 ) * 2;

and in ``grasstest_vs11.vs`` / ``grasstest_ps11.ps``::

    o.Tex1 = worldPos.xz * lightmapScale;
    lightmap = 2 * tex2D( lightmapTex, i.Tex1 );
    diff.xyz *= lightmap.xyz;

Road colour is the road texture times **the terrain's lightmap, sampled
at the road's world XZ**, times two. Grass is the same. ``g_Ambient``
and ``g_Diffuse`` are declared in ``road.fx`` and never used: the
manifest's ``MODEL_AMBIENT`` / ``LS_COLOR`` do not reach a road at all,
which is why adjusting them changed nothing — "under the same lighting
they look completely different in the game" was exactly right.

The terrain already samples that lightmap through its ``UVMap`` grid,
so a road or a tuft only has to look the lightmap up by **where it
stands**: world position, mapped onto the same 0..1 grid the terrain
uses. That is one node chain, inserted in front of whatever fed the
material's Base Color.

Materials are COPIED before being touched. A road model's material is
built by the ordinary model path, and a material that a building also
uses must not learn to sample the lightmap — buildings are lit by the
ILLUMINATION section, not by this.
"""

from __future__ import annotations

import dataclasses

import bpy

from blender_io.terrain_bridge import GRID_WIDTH_PROP, TERRAIN_UV_LAYER, _modulate2x
from utils.logging import get_logger

logger = get_logger("blender_io.world_lightmap")

#: Node names, so a material is recognisable as already tinted.
NODE_POSITION = "ExM_WorldPosition"
NODE_MAPPING = "ExM_WorldToLightmap"
NODE_LIGHTMAP = "ExM_WorldLightmap"
NODE_MIX = "ExM_WorldLightMix"

#: Suffix on the copied material.
LIT_SUFFIX = "_lit"


@dataclasses.dataclass(frozen=True)
class WorldLightmap:
    """How to turn a world position into a lightmap coordinate."""

    image: object
    #: World XY of the terrain's UV origin (vertex 0).
    origin: tuple[float, float]
    #: World extent of the terrain along X and Y — the UV grid spans
    #: 0..1 across exactly this.
    extent: tuple[float, float]

    def uv_at(self, x: float, y: float) -> tuple[float, float]:
        return (
            (x - self.origin[0]) / self.extent[0],
            (y - self.origin[1]) / self.extent[1],
        )


def from_terrain(terrain_obj) -> WorldLightmap | None:
    """Read the sampler off the terrain: its lightmap image and where
    its UV grid sits in the world.

    The grid is ``u = column / (width-1)``, ``v = row / (width-1)`` —
    ``terrain_bridge._apply_uv_grid`` — so vertex 0 is (0, 0) and the
    far corners are at 1. Their WORLD positions are taken from the
    object, not recomputed, so centring, scale and anything else the
    import did to the terrain are included by construction.
    """
    if terrain_obj is None:
        return None
    image = _lightmap_image(terrain_obj)
    if image is None:
        return None

    mesh = getattr(terrain_obj, "data", None)
    width = int(terrain_obj.get(GRID_WIDTH_PROP) or 0)
    vertices = getattr(mesh, "vertices", None)
    if width < 2 or vertices is None or len(vertices) < width * width:
        return None
    try:
        matrix = terrain_obj.matrix_world
        first = matrix @ vertices[0].co
        along_x = matrix @ vertices[width - 1].co
        along_y = matrix @ vertices[width * (width - 1)].co
    except (AttributeError, IndexError, TypeError) as exc:
        logger.debug("could not read the terrain's corners: %s", exc)
        return None

    extent = (along_x.x - first.x, along_y.y - first.y)
    if not extent[0] or not extent[1]:
        return None
    return WorldLightmap(image=image, origin=(first.x, first.y), extent=extent)


def _lightmap_image(terrain_obj):
    """The image the terrain's own Lightmap node samples, if any."""
    mesh = getattr(terrain_obj, "data", None)
    for material in getattr(mesh, "materials", None) or []:
        tree = getattr(material, "node_tree", None)
        node = getattr(tree, "nodes", {}).get("Lightmap") if tree is not None else None
        image = getattr(node, "image", None)
        if image is not None:
            return image
    return None


def tint_objects(objects, sampler: WorldLightmap, label: str) -> int:
    """Give every material on ``objects`` the lightmap tint, on a copy.

    Meshes are visited once each — a thousand grass tufts share a
    handful of meshes — and a material already copied is reused, so
    two roads with the same texture share one lit material.
    Returns how many materials were made.
    """
    if sampler is None:
        return 0
    copies: dict = {}
    made = 0
    seen_meshes = set()
    for obj in objects:
        mesh = getattr(obj, "data", None)
        slots = getattr(mesh, "materials", None)
        if mesh is None or slots is None or id(mesh) in seen_meshes:
            continue
        seen_meshes.add(id(mesh))
        for index in range(len(slots)):
            material = slots[index]
            if material is None:
                continue
            name = getattr(material, "name", "")
            if name.endswith(LIT_SUFFIX) or _already_tinted(material):
                continue
            lit = copies.get(name)
            if lit is None:
                lit = _lit_copy(material, sampler)
                if lit is None:
                    continue
                copies[name] = lit
                made += 1
            try:
                slots[index] = lit
            except (AttributeError, RuntimeError, TypeError) as exc:
                logger.debug("could not assign %s: %s", lit.name, exc)
    if made:
        logger.info(
            "%s: %d material(s) now sample the lightmap by world position, "
            "x2 — road.fx / grasstest_ps11.ps", label, made,
        )
    return made


def _already_tinted(material) -> bool:
    tree = getattr(material, "node_tree", None)
    nodes = getattr(tree, "nodes", None)
    try:
        return nodes is not None and NODE_MIX in nodes.keys()
    except (AttributeError, TypeError):
        return False


def _lit_copy(material, sampler: WorldLightmap):
    """A copy of ``material`` whose Base Color is multiplied by the
    lightmap at the fragment's world position, times two."""
    wanted = f"{material.name}{LIT_SUFFIX}"
    existing = bpy.data.materials.get(wanted)
    if existing is not None and _already_tinted(existing):
        return existing
    try:
        lit = material.copy()
        lit.name = wanted
    except (AttributeError, RuntimeError, TypeError) as exc:
        logger.debug("could not copy %s: %s", material.name, exc)
        return None
    if not _insert_tint(lit, sampler):
        try:
            bpy.data.materials.remove(lit)
        except (AttributeError, RuntimeError, TypeError):
            pass
        return None
    return lit


def _insert_tint(material, sampler: WorldLightmap) -> bool:
    tree = getattr(material, "node_tree", None)
    nodes = getattr(tree, "nodes", None)
    links = getattr(tree, "links", None)
    if nodes is None or links is None:
        return False

    principled = next(
        (n for n in nodes if getattr(n, "type", "") == "BSDF_PRINCIPLED"), None,
    )
    if principled is None:
        return False
    base = principled.inputs["Base Color"]
    feeding = base.links[0].from_socket if base.links else None

    try:
        position = nodes.new("ShaderNodeNewGeometry")
        position.name = NODE_POSITION
        position.location = (-1400, -500)

        mapping = nodes.new("ShaderNodeMapping")
        mapping.name = NODE_MAPPING
        mapping.location = (-1150, -500)
        # uv = (P - origin) / extent, as a Mapping node does it:
        # (P * scale) + location. The Z of world position is ignored
        # by a 2D image lookup.
        sx, sy = 1.0 / sampler.extent[0], 1.0 / sampler.extent[1]
        mapping.inputs["Scale"].default_value = (sx, sy, 1.0)
        mapping.inputs["Location"].default_value = (
            -sampler.origin[0] * sx, -sampler.origin[1] * sy, 0.0,
        )
        links.new(position.outputs["Position"], mapping.inputs["Vector"])

        lightmap = nodes.new("ShaderNodeTexImage")
        lightmap.name = NODE_LIGHTMAP
        lightmap.label = getattr(sampler.image, "name", "lightmap")
        lightmap.image = sampler.image
        lightmap.location = (-900, -500)
        # The terrain's own lightmap node is CLAMPed too: a road at the
        # map edge must not wrap round to the far side's lighting.
        try:
            lightmap.extension = "EXTEND"
        except (AttributeError, TypeError):
            pass
        links.new(mapping.outputs["Vector"], lightmap.inputs["Vector"])

        if feeding is None:
            # A plain colour: feed the constant through an RGB node so
            # there is something to multiply.
            rgb = nodes.new("ShaderNodeRGB")
            rgb.name = "ExM_BaseColour"
            rgb.outputs["Color"].default_value = tuple(base.default_value)
            rgb.location = (-900, -200)
            feeding = rgb.outputs["Color"]

        tinted = _modulate2x(tree, feeding, lightmap.outputs["Color"], NODE_MIX)
        if tinted is None:
            return False
        links.new(tinted, base)
    except (AttributeError, KeyError, RuntimeError, TypeError) as exc:
        logger.debug("could not insert the lightmap tint into %s: %s", material.name, exc)
        return False
    return True


# Kept for the verifier: which UV layer the terrain samples its
# lightmap through, so a check can compare a road's computed UV with
# the terrain's stored one at the same spot.
TERRAIN_LIGHTMAP_UV = TERRAIN_UV_LAYER
