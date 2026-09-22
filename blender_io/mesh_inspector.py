# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Report what an imported object actually contains, in the user's Blender.

Why this exists
---------------

Every texture fault in this project has been diagnosed by reasoning
about what Blender probably does, and the reasoning has been wrong more
often than right. Faces were assumed to be dropped by ``from_pydata``;
that was never checked. Sockets were compared by identity, which works
in the test double and never in bpy. Both looked correct until a real
import disagreed.

The Model Doctor reads files. This reads the *result* — the mesh
Blender built, the UVs on its loops, the images on its nodes — and
compares it against the source file. Where they disagree is the fault,
and no step of it depends on knowing in advance how Blender behaves.

Run it on an imported object and it answers, in order:

* did every face survive, or did Blender discard some
* do the loop UVs match the file's UVs for the vertex each loop uses
* what range do the UVs actually cover once written
* which material does each face use, and does that material carry a
  usable image linked to Base Color

Nothing here modifies anything.
"""

from __future__ import annotations

import bpy

from formats.exm.gam import read_model

#: UVs beyond this are certainly not tiling but corruption. Real models
#: tile: ``factory_box`` runs its V to 4.0 across a fence, and
#: ``bridge_concrete`` to 2.0. Ten is far past anything authored.
_ABSURD_UV = 10.0


def inspect_object(obj, model_path: str | None = None) -> list[str]:
    """Describe an imported object, optionally against its source file."""
    lines = ["", "=" * 68, f"Mesh Inspector: {getattr(obj, 'name', '?')}", "=" * 68]

    mesh = getattr(obj, "data", None)
    if mesh is None or not hasattr(mesh, "polygons"):
        lines.append("This object has no mesh data.")
        return lines

    # The datablock name, because it says which model this geometry was
    # actually built from. When the counts below disagree with the file,
    # the first thing to rule out is that the two are different models.
    datablock = getattr(mesh, "name", "?")
    lines.append(f"mesh datablock: {datablock}")

    siblings = _same_base_name(datablock)
    if len(siblings) > 1:
        lines.append(
            f"  {len(siblings)} datablocks share this base name: "
            f"{', '.join(siblings[:4])}"
        )
        lines.append(
            "    Blender never overwrites a datablock — a second import "
            "makes .001 and leaves the first in place. An object still "
            "pointing at the older one carries the geometry that import "
            "produced, however many times the add-on has been updated "
            "since. Materials, matched by name, ARE rebuilt on it, so an "
            "old mesh ends up wearing new materials and looks worse than "
            "either. Import into a fresh file to compare."
        )

    vertices = len(mesh.vertices)
    polygons = len(mesh.polygons)
    loops = len(mesh.loops)
    lines.append(f"vertices {vertices}   polygons {polygons}   loops {loops}")

    model = None
    if model_path:
        try:
            model = read_model(model_path)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            lines.append(f"could not read {model_path}: {exc}")

    if model is not None:
        lines.extend(_compare_with_source(mesh, model))

    lines.extend(_describe_colors(mesh))
    lines.extend(_describe_uvs(mesh, model))
    lines.extend(_describe_materials(obj, mesh))
    return lines


def _same_base_name(name: str) -> list[str]:
    """Datablocks whose name differs only by Blender's .NNN suffix."""
    base = name.rsplit(".", 1)[0]
    try:
        meshes = list(bpy.data.meshes)
    except TypeError:
        # A collection that does not iterate. Nothing to compare, and a
        # diagnostic must not be the thing that raises.
        return [name]

    found = []
    for other in meshes:
        other_name = getattr(other, "name", "")
        if other_name == base or other_name.rsplit(".", 1)[0] == base:
            found.append(other_name)
    return sorted(found) or [name]


def _compare_with_source(mesh, model) -> list[str]:
    """Did Blender keep the geometry it was given."""
    source_vertices = sum(len(m.positions) for m in model.meshes)
    source_faces = sum(len(m.triangles) for m in model.meshes)

    lines = [
        "",
        f"source file: {source_vertices} vertices, {source_faces} triangles",
    ]

    if len(mesh.vertices) != source_vertices:
        lines.append(
            f"  VERTEX COUNT DIFFERS: Blender has {len(mesh.vertices)}."
        )
        lines.append(
            "    Blender does not discard vertices on import, so this is "
            "not something the import did to this file. Either the object "
            "and the .gam are different models — check the datablock name "
            "above, which carries the model id — or the object predates "
            "the current add-on and still points at the datablock an "
            "earlier import built. Every UV comparison below is "
            "meaningless while the counts disagree."
        )
    else:
        lines.append("  vertex count matches")

    dropped = source_faces - len(mesh.polygons)
    if dropped > 0:
        lines.append(
            f"  BLENDER DROPPED {dropped} FACE(S). Anything written by walking "
            "the source face list runs ahead of the real loops from the first "
            "dropped face onward."
        )
    elif dropped < 0:
        lines.append(f"  Blender has {-dropped} MORE faces than the file. Unexpected.")
    else:
        lines.append("  every face survived — no face was discarded")

    return lines


def _describe_colors(mesh) -> list[str]:
    """The vertex colour layer, which the ``_vc`` shaders read."""
    layers = list(getattr(mesh, "color_attributes", []))
    if not layers:
        return []

    lines = [""]
    for layer in layers:
        data = layer.data
        if not len(data):
            lines.append(f"colour layer {layer.name!r}: empty")
            continue
        lum = [
            sum(tuple(data[i].color)[:3]) / 3.0 for i in range(min(len(data), 5000))
        ]
        lines.append(
            f"colour layer {layer.name!r}: {len(data)} entries, "
            f"mean {sum(lum) / len(lum):.2f}, min {min(lum):.2f}, max {max(lum):.2f}"
        )
    return lines


def _describe_uvs(mesh, model) -> list[str]:
    """What is actually on the UV layers, and does it match the file."""
    lines = [""]
    layers = list(getattr(mesh, "uv_layers", []))
    if not layers:
        lines.append("NO UV LAYER. The model cannot be textured or exported.")
        return lines

    source_uvs: list[tuple[float, float]] = []
    if model is not None:
        for mesh_data in model.meshes:
            source_uvs.extend(mesh_data.uvs)

    for layer in layers:
        data = layer.data
        us = [data[i].uv[0] for i in range(len(data))]
        vs = [data[i].uv[1] for i in range(len(data))]
        if not us:
            lines.append(f"UV layer {layer.name!r}: empty")
            continue

        render = getattr(layer, "active_render", None)
        lines.append(
            f"UV layer {layer.name!r}: {len(data)} entries, "
            f"u [{min(us):.3f}, {max(us):.3f}]  v [{min(vs):.3f}, {max(vs):.3f}]"
            f"   active_render={render}"
        )

        spread = max(max(us) - min(us), max(vs) - min(vs))
        if spread < 0.001:
            lines.append(
                "  UVS COLLAPSED TO A POINT. Blender samples the smallest mip, "
                "so the texture renders as a flat wash of its average colour — "
                "a material that plainly holds the right image and shows none of it."
            )
        if max(abs(min(us)), abs(max(us)), abs(min(vs)), abs(max(vs))) > _ABSURD_UV:
            lines.append(
                "  UVs far outside any plausible tiling. These are not texture "
                "coordinates; some other field is being read as UV."
            )

        if source_uvs and len(layers) == 1:
            lines.extend(_compare_uvs_against_source(mesh, data, source_uvs))

    return lines


def _compare_uvs_against_source(mesh, data, source_uvs) -> list[str]:
    """Does each loop carry its own vertex's UV from the file.

    The importer flips V for Blender, so the file's ``(u, v)`` must
    appear as ``(u, 1 - v)``. A loop that carries some other vertex's
    UV is the desynchronisation this whole class of bug comes down to,
    and it is visible here directly rather than inferred.
    """
    mismatched = 0
    first_bad = None

    for index, loop in enumerate(mesh.loops):
        if index >= len(data):
            break
        vertex_index = loop.vertex_index
        if vertex_index >= len(source_uvs):
            continue
        u, v = source_uvs[vertex_index]
        got_u, got_v = data[index].uv[0], data[index].uv[1]
        if abs(got_u - u) > 1e-4 or abs(got_v - (1.0 - v)) > 1e-4:
            mismatched += 1
            if first_bad is None:
                first_bad = (index, vertex_index, (u, 1.0 - v), (got_u, got_v))

    if not mismatched:
        return ["  every loop carries its own vertex's UV from the file"]

    index, vertex_index, expected, got = first_bad
    return [
        f"  {mismatched} of {len(mesh.loops)} LOOPS CARRY THE WRONG UV",
        f"      first at loop {index} (vertex {vertex_index}): "
        f"expected {expected[0]:.4f},{expected[1]:.4f} got {got[0]:.4f},{got[1]:.4f}",
    ]


def _describe_materials(obj, mesh) -> list[str]:
    """Which material each face uses, and whether it can show a texture."""
    lines = [""]
    slots = list(getattr(mesh, "materials", []))
    if not slots:
        lines.append("NO MATERIAL SLOTS.")
        return lines

    counts: dict[int, int] = {}
    for polygon in mesh.polygons:
        index = polygon.material_index
        counts[index] = counts.get(index, 0) + 1

    lines.append(f"{len(slots)} material slot(s):")
    for index, material in enumerate(slots):
        faces = counts.get(index, 0)
        name = getattr(material, "name", "?") if material else "(empty slot)"
        lines.append(f"  [{index}] {name} — {faces} face(s)")
        if material is not None:
            lines.extend(f"      {line}" for line in _describe_material(material))

    unused = [i for i in counts if i >= len(slots)]
    if unused:
        lines.append(f"  FACES POINT AT SLOTS THAT DO NOT EXIST: {sorted(unused)}")

    return lines


def _uv_source(tree, node) -> str:
    """Which UV layer a texture node samples.

    An image node with nothing in its Vector input takes the active UV
    map, which is the diffuse one. A lightmap doing that lays itself
    over the diffuse map in the diffuse map's coordinates, which looks
    like two textures on top of each other and is the reason this is
    worth reporting.
    """
    for link in getattr(tree, "links", ()):
        if getattr(getattr(link, "to_node", None), "name", None) != node.name:
            continue
        if getattr(getattr(link, "to_socket", None), "name", None) != "Vector":
            continue
        source = getattr(link, "from_node", None)
        layer = getattr(source, "uv_map", None)
        return f"UV layer {layer!r}" if layer else "a custom vector input"
    return "the active UV layer"


def _describe_material(material) -> list[str]:
    """The image nodes on one material and whether they reach the shader."""
    from blender_io.texture_bridge import (
        _find_principled,
        _has_pixels,
        _reaches_base_color,
    )

    tree = getattr(material, "node_tree", None)
    if tree is None:
        return ["no node tree"]

    principled = _find_principled(tree)
    if principled is None:
        return ["NO PRINCIPLED NODE — nothing to connect a texture to"]

    image_nodes = [n for n in tree.nodes if getattr(n, "type", "") == "TEX_IMAGE"]
    if not image_nodes:
        return ["no image node at all"]

    lines = []
    for node in image_nodes:
        image = getattr(node, "image", None)
        if image is None:
            lines.append(f"node {node.name!r}: NO IMAGE ASSIGNED")
            continue

        size = tuple(getattr(image, "size", (0, 0)))
        state = "has pixels" if _has_pixels(image) else "EMPTY — no pixel data"
        linked = _reaches_base_color(tree, node, principled)
        lines.append(
            f"node {node.name!r}: {image.name} {size} {state}, "
            f"{'linked to Base Color' if linked else 'NOT LINKED to Base Color'}"
            f", samples {_uv_source(tree, node)}"
        )

    vertex_color = [
        n for n in tree.nodes if getattr(n, "type", "") == "VERTEX_COLOR"
    ]
    if vertex_color:
        layer = getattr(vertex_color[0], "layer_name", "?")
        lines.append(f"vertex colour {layer!r} multiplied into the base colour")

    blend = getattr(material, "blend_method", None)
    if blend is not None:
        lines.append(f"blend_method={blend}")

    return lines
