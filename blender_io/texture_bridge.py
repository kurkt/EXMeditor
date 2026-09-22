# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Model materials -> Blender materials with textures.

Models name their textures as bare filenames (``rock_clif2.dds``) with
no path, so locating them means searching the game's texture folders.
The search result is cached: a map places a thousand objects drawing on
a hundred models, and walking the texture tree once per material would
dominate import time.

Texture nodes are created with the names HTAToolchain looks for —
``Diffuse``, ``Bump`` and so on. That is not cosmetic: the exporter
matches textures by node NAME rather than by type, so a material built
this way survives a round trip, while one built with Blender's default
node names exports with no texture at all.

Nodes are named from the texture's **slot number**, not from where it
happens to sit in the list. A material can carry slot 1 without slot 0,
and naming by position then labels a bump map ``Diffuse`` and wires it
into base colour. Slot 0 is the diffuse map and slot 1 the bump map,
measured on a shipped ``bump`` model and on toolchain output
independently.

Transparency
------------

Foliage is transparent through its texture's alpha, and the material
has to say so or the whole quad draws solid. ``kustarnik_1.dds`` is
DXT5, and its alpha is not a cutout: 46.3% of texels are fully clear,
24.3% fully opaque, and **29.4% in between** — of the clearly
intermediate ones, 4043 are not adjacent to any clear or opaque texel
at all, so this is real semi-transparency rather than an antialiased
edge. ``CLIP`` would throw that away; ``HASHED`` keeps it and sorts
correctly without the ordering artefacts ``BLEND`` brings.

The bush also explains a symptom that looked like a texture problem
and was not: the image is uniformly dark, mean luminance 72 out of 255
under the opaque texels and 73 under the clear ones. With alpha
unconnected the material is opaque and you see the whole square —
a dark quad with the brighter leaves showing through. Nothing is wrong
with the texture.
"""

from __future__ import annotations

import os
import struct

import bpy

from core import dds
from core.mesh import Material
from utils.logging import get_logger

logger = get_logger("blender_io.texture_bridge")

#: Node names the engine's toolchain recognises, indexed by slot
#: number. Slots 0 and 1 are measured; 2 and above have never appeared
#: in any model examined and are named here only so a file that does
#: carry one round-trips instead of losing it.
TEXTURE_SLOT_NAMES = ("Diffuse", "Bump", "Lightmap", "Cube", "Detail")

#: How a material with transparency is blended. See the module
#: docstring for why this is not ``CLIP``.
TRANSPARENT_BLEND_METHOD = "HASHED"

#: Name of the colour attribute the importer writes. Must match
#: ``mesh_bridge.COLOR_LAYER``.
COLOR_LAYER = "Col"

#: The second UV set, which lightmaps are unwrapped into. Must match
#: ``mesh_bridge.UV2_LAYER``.
UV2_LAYER = "UVMap2"

#: Texture slots. 0 and 1 were measured earlier; slot 2 first appeared
#: on TheTown, where all six materials carry ``MininAO.dds`` there under
#: the ``DiffuseAO`` and ``SpecularAO`` shaders — an ambient occlusion
#: map.
SLOT_DIFFUSE = 0
SLOT_BUMP = 1
SLOT_LIGHTMAP = 2
SLOT_CUBE = 3
SLOT_DETAIL = 4

#: Shaders whose name says they read the vertex colour. The ``_vc``
#: suffix is the game's own convention and the split it marks is exact
#: across every model measured::
#:
#:     diffuse_vc, specular_vc   house2, civilhouse1   vertex colours
#:     diffuse, bump             kustarnik1, factory_box, bridge_concrete,
#:                               big_flag01             none at all
#:
#: The models on the first row are the ones that look wrong in Blender
#: and the ones on the second are the ones that look right, which is
#: what makes this the fault rather than a detail.
VERTEX_COLOR_SUFFIX = "_vc"


def uses_vertex_color(shader: str) -> bool:
    """Whether a shader name says it reads the vertex colour.

    Case-insensitive, because the game's own data is not consistent
    about it. A census of 1388 models turns up ``diffuse_vc`` 444 times
    and ``Diffuse_VC`` 4 times, ``bumpdiffuse_envalphagloss_spec`` 372
    times and ``BumpDiffuse_EnvAlphaGloss_Spec`` 1224 times,
    ``DiffuseAO``, ``diffuseAO``, ``bump`` and ``Bump``. Matching
    exactly would have silently dropped the vertex colour on every
    capitalised variant.
    """
    return bool(shader) and shader.lower().endswith(VERTEX_COLOR_SUFFIX)

#: Folders under the game root that hold textures, most likely first.
_TEXTURE_DIRECTORIES = (
    os.path.join("data", "models", "textures"),
    os.path.join("data", "textures"),
    # Terrain ground textures, named by level.tile.
    os.path.join("data", "tiles"),
    os.path.join("data", "models"),
)

#: What the registry says, when the install has one. Consulted before
#: the folder walk: the engine does not search at all, it looks the
#: name up in ``data/models/ModelTextures.xml``, and a name the
#: registry places somewhere unexpected is placed there for a reason.
_registry: dict = {}

#: filename (lowercased) -> full path, or None when the search failed.
_texture_index: dict[str, str | None] = {}
_indexed_root: str | None = None

#: Textures asked for but not found, so an import can report the scale
#: of the problem instead of leaving it to be noticed model by model.
_missing_textures: set[str] = set()

#: Textures that WERE found and still produced no pixels. A different
#: failure from a missing file and a different fix, so counting them
#: together hid it: the file is there, the path is right, and the model
#: is grey anyway.
_unreadable_textures: set[str] = set()

#: Names already reported as empty this import. The recovery runs once
#: per material that names the texture, and a map names some of them
#: two hundred times — the log drowned the one line that mattered.
_reported_empty: set[str] = set()


def missing_textures() -> set[str]:
    """Texture names requested during this session that were not found."""
    return set(_missing_textures)


def unreadable_textures() -> set[str]:
    """Texture files that were found and yielded no pixels."""
    return set(_unreadable_textures)


def reset_missing_textures() -> None:
    _missing_textures.clear()
    _unreadable_textures.clear()
    _reported_empty.clear()


def build_texture_index(game_root: str, *, refresh: bool = False) -> int:
    """Index every image file under the game's texture folders.

    One walk instead of a search per texture. A full install holds
    thousands of files and a map needs a few hundred lookups, so the
    walk pays for itself immediately and the index is reused for the
    whole session.
    """
    global _indexed_root

    if not refresh and _indexed_root == game_root and _texture_index:
        return len(_texture_index)

    _texture_index.clear()
    _indexed_root = game_root

    global _registry
    from formats.exm.texture_registry import find_texture_registry

    _registry = find_texture_registry(game_root)

    for relative in _TEXTURE_DIRECTORIES:
        directory = os.path.join(game_root, relative)
        if not os.path.isdir(directory):
            continue
        for current, _dirs, files in os.walk(directory):
            for name in files:
                if not name.lower().endswith((".dds", ".tga", ".bmp", ".png")):
                    continue
                key = texture_key(name)
                # First match wins: the directories are listed
                # most-specific first, so a texture in the models tree
                # is preferred over a same-named one elsewhere.
                _texture_index.setdefault(key, os.path.join(current, name))

    logger.info("Texture index: %d image file(s) under %s", len(_texture_index), game_root)
    return len(_texture_index)


def texture_key(filename: str) -> str:
    """The lookup key for a texture name.

    Splits on both separators explicitly. ``os.path.basename`` only
    understands the host's separator, so on Linux a Windows-style name
    like ``some\\path\\rock.dds`` would be treated as one long
    filename and never match — a failure that would never appear on the
    developer's Windows machine.
    """
    normalised = filename.replace("\\", "/")
    return normalised.rsplit("/", 1)[-1].lower()


def resolve_texture(filename: str, game_root: str) -> str | None:
    """Full path of a texture named by a model, or ``None``.

    The registry first. ``data/models/ModelTextures.xml`` is what the
    engine itself consults — it performs no search — so where it places
    a name is where that name belongs, whatever a walk of the folders
    would have turned up first.

    The walk stays as the fallback, for installs without a registry and
    for names it does not list.
    """
    if not filename:
        return None
    build_texture_index(game_root)

    key = texture_key(filename)
    reference = _registry.get(key)
    if reference:
        path = os.path.join(game_root, *reference.replace("\\", "/").split("/"))
        if os.path.isfile(path):
            return path
        # Registered and not on disk: the entry still tells us the name
        # is real, so fall through rather than treating it as unknown.
        logger.debug("registry names %s at %s, which is not there", filename, path)

    return _texture_index.get(key)


def build_material(
    material: Material,
    game_root: str | None,
    *,
    name_prefix: str = "ExM",
    uv2_available: bool = False,
    vertex_color_available: bool = True,
) -> bpy.types.Material:
    """Create a Blender material for one model material.

    Identity is the shader AND its textures together, not the shader
    alone. Reusing by shader name was wrong and visibly so: ``bump`` is
    used by 16 unrelated materials across seven models and
    ``diffuse_detail_vc`` by many more, so the first model loaded
    claimed the name and every later one inherited its textures — an
    oil rig arrived wearing a bush's texture.

    Reuse still happens where it should: two materials with the same
    shader and the same textures really are the same material, and a
    map shares those heavily.
    """
    material_name = _material_identity(
        material, name_prefix, vertex_color_available
    )
    existing = bpy.data.materials.get(material_name)
    if existing is not None:
        if _is_complete(existing, material):
            return existing
        if not _diffuse_is_available(material, game_root):
            # Incomplete only because the texture cannot be found. There
            # is nothing better to build, and tearing this one down
            # would strip its nodes, drop the last user of its image and
            # let Blender purge the image from the file entirely — an
            # unfindable texture turning into a destroyed material.
            logger.info(
                "%s names a texture that cannot be found; left as it is",
                material_name,
            )
            return existing
        # A material with the right name and the wrong contents. These
        # persist in the .blend: once an import produced a material
        # whose texture could not be loaded — or that an older version
        # of this add-on wired differently — every later import reuses
        # it by name and the model is flat-coloured forever. Reinstalling
        # the add-on does not help, because the material is saved in the
        # file rather than in the add-on.
        #
        # Rebuilding in place rather than making a second datablock, so
        # objects already pointing at this material get the fix too.
        logger.info("rebuilding %s: it carries no usable texture", material_name)
        _clear_image_nodes(existing)
        blender_material = existing
    else:
        blender_material = bpy.data.materials.new(material_name)

    blender_material.use_nodes = True
    blender_material["exm_shader"] = material.shader
    if material.textures:
        blender_material["exm_textures"] = ", ".join(material.textures)

    tree = getattr(blender_material, "node_tree", None)
    if tree is None:
        return blender_material

    principled = _find_principled(tree)
    transparent = False

    nodes_by_slot: dict[int, object] = {}
    for position, texture_name in enumerate(material.textures):
        slot = _slot_of(material, position)
        node = tree.nodes.new("ShaderNodeTexImage")
        # The name the toolchain matches on — see the module docstring.
        node.name = _slot_node_name(slot)
        node.label = texture_name
        node.location = (-900, -300 * position)

        image = _load_image(texture_name, game_root)
        if image is not None:
            node.image = image
            nodes_by_slot[slot] = node
        else:
            # Kept anyway: the node records which file the model wants,
            # so a missing texture is visible in the material rather
            # than being an absence with no explanation.
            node.label = f"{texture_name} (not found)"

    diffuse_node = nodes_by_slot.get(SLOT_DIFFUSE)
    if principled is None or diffuse_node is None:
        # No image to wire. The nodes still exist, named for their
        # slots, so the material can be repaired by hand and survives a
        # round trip — which is more than a material with nothing in it
        # at all can do.
        return blender_material

    lightmap_uv_available = bool(uv2_available)

    # The base colour is a chain, not a single link. The game modulates
    # the diffuse map by whatever else the shader declares, and leaving
    # any of it out shows a texture at full strength where the game
    # shows it shaded — the commonest reason a model looks wrong while
    # plainly carrying the right images.
    color_socket = diffuse_node.outputs["Color"]
    stage = 0

    lightmap = nodes_by_slot.get(SLOT_LIGHTMAP)
    if lightmap is not None and lightmap_uv_available:
        # Slot 2 on the ``DiffuseAO`` and ``SpecularAO`` shaders is an
        # ambient occlusion map — ``MininAO.dds`` on every material of
        # TheTown.
        #
        # Linked ONLY when the mesh has a second UV set to sample it
        # with. A lightmap is unwrapped independently of the diffuse
        # map — that is what makes it a lightmap — so sampling it with
        # the diffuse UVs lays a second, unrelated image over the first.
        # Tried the other way round and the result was visibly worse
        # than leaving it out, which is evidence and is why the
        # condition is here.
        #
        # TheTown carries no second UV set, so its AO map stays loaded
        # and unlinked. Where the game gets the coordinates from is
        # UNKNOWN and is the thing to find out next.
        # Point it at the second UV set explicitly. An image node with
        # nothing in its Vector input samples the ACTIVE UV map, which
        # is the diffuse one — so linking the lightmap without this
        # lays it over the diffuse map in the diffuse map's own
        # coordinates, which is the "two textures on top of each other"
        # this was meant to fix.
        _sample_with_uv_map(tree, lightmap, UV2_LAYER)

        mixed = _mix_multiply(
            tree, color_socket, lightmap.outputs["Color"],
            "ExM_LightmapMix", (-600, 100),
        )
        if mixed is not None:
            color_socket = mixed
            stage += 1

    detail = nodes_by_slot.get(SLOT_DETAIL)
    if detail is not None:
        # ``diffuse_detail.fx``: ``Diffuse.rgb * Details.rgb * 2.0``.
        # Modulate2x, so a mid-grey detail map leaves the base where it
        # was and only its variation comes through — the same
        # convention as the colour map and the lightmap.
        #
        # It samples the same coordinates as the diffuse: a detail map
        # is meant to tile far more often than the diffuse, and the
        # scale for that lives in the shader rather than in a UV set of
        # its own.
        mixed = _mix_multiply(
            tree, color_socket, detail.outputs["Color"],
            "ExM_DetailMix", (-450, 200),
        )
        if mixed is not None:
            doubled = _mix_multiply(
                tree, mixed, None, "ExM_DetailMix_2x", (-350, 200)
            )
            color_socket = doubled if doubled is not None else mixed

    if uses_vertex_color(material.shader) and not vertex_color_available:
        # The shader reads a vertex colour the mesh does not carry.
        # Blender's Color Attribute node answers a layer that is not
        # there with BLACK, and multiplying by black is how foliage
        # came out correctly cut out and completely colourless — the
        # alpha comes straight from the image and never noticed.
        #
        # Not multiplying is also the right answer: where the format
        # stores no vertex colour there is nothing to modulate by.
        logger.debug(
            "%s reads a vertex colour and its mesh has none; left unmixed",
            material_name,
        )
    elif uses_vertex_color(material.shader):
        vertex_color = _vertex_color_node(tree)
        if vertex_color is not None:
            mixed = _mix_multiply(
                tree, color_socket, vertex_color.outputs["Color"],
                "ExM_VertexColorMix", (-300, -100),
            )
            if mixed is not None:
                color_socket = mixed
                stage += 1

    try:
        tree.links.new(color_socket, principled.inputs["Base Color"])
    except (KeyError, RuntimeError, AttributeError) as exc:
        logger.debug("could not wire base colour: %s", exc)

    image = getattr(diffuse_node, "image", None)
    if image is not None and _image_has_alpha(
        image, diffuse_node.label, game_root, material.shader
    ):
        transparent = _connect_alpha(tree, diffuse_node, principled)
    elif image is not None and _alpha_is_gloss(
        diffuse_node.label, game_root, material.shader
    ):
        _connect_gloss(tree, diffuse_node, principled)

    if transparent:
        _make_transparent(blender_material)

    return blender_material


def _slot_of(material: Material, position: int) -> int:
    """The slot a texture occupies, falling back to its position.

    ``slots`` is empty when the skin chunk had to be read by string
    scan, which recovers names and not slot numbers. Position is the
    only thing left in that case, and it is right often enough to be
    better than nothing — but it is a fallback, not the rule.
    """
    slots = getattr(material, "slots", None) or []
    if position < len(slots):
        return slots[position]
    return position


def _slot_node_name(slot: int) -> str:
    if 0 <= slot < len(TEXTURE_SLOT_NAMES):
        return TEXTURE_SLOT_NAMES[slot]
    # An unmeasured slot. Keep the number rather than inventing a name
    # the exporter would then fail to match.
    return f"Slot{slot}"


#: Shaders whose diffuse alpha is a gloss or specular mask rather than
#: transparency. The Direct3D 8/9 convention of this era puts the gloss
#: mask in the diffuse map's alpha, and this game does it — but it says
#: so in the shader it picks, and the shader is authored data rather
#: than something inferred.
_GLOSS_ALPHA_MARKERS = ("specular", "gloss", "bump")


def alpha_is_gloss_shader(shader: str) -> bool:
    """Whether a shader's name says its diffuse alpha is a mask."""
    name = (shader or "").lower()
    return any(marker in name for marker in _GLOSS_ALPHA_MARKERS)


def _image_has_alpha(
    image, texture_name: str, game_root: str | None, shader: str = ""
) -> bool:
    """Whether this texture's alpha means transparency.

    Having an alpha channel is not the same as being see-through: the
    gloss mask lives in the diffuse map's alpha on the shaders that
    declare it, and ``metal_elements_roof`` read as transparency
    produced a roof you could see through.

    **Decided by the shader.** This used to be decided by counting
    texels — a cutout was supposed to have a solid share above 2% —
    and a census of the whole corpus says that test earns nothing and
    costs a great deal::

        265 textures the texel test calls "not a cutout"
        175 of them are used ONLY with gloss/specular/bump shaders
             — which the shader name already catches, including the
               metal_elements_roof case the test was written for
         90 of them are used with diffuse, diffuse_vc or road
             — d_grass, d_fgrass, antens_alfa, resetka, the faction
               emblems: things that are plainly see-through, denied
               their alpha and drawn as solid rectangles

    Soft-edged foliage is what it got wrong: ``tomato.dds`` is 0.2%
    solid, ``dry_grass_1.dds`` 1.1%, because antialiased edges through
    DXT5 interpolation rarely land on exactly 255. Neither is less
    transparent for it.

    Nor does the shape of the alpha separate them. Measured over 484
    shader/texture pairs, the share of mid-range alpha — the "a ramp
    never commits to either end" test — has the gloss family down at
    5.5% at its tenth percentile and the foliage family up at 19.7% at
    its ninetieth, with ``tomato.dds`` at 33.5% sitting inside the
    gloss range. There is no threshold there to find, which is why
    this now asks the shader instead of the pixels.

    A plain ``diffuse`` shader has nowhere to put a gloss mask — the
    game has ``specular``, ``bump`` and ``bumpdiffuse_envalphagloss_spec``
    for that — so on those shaders alpha can only mean transparency.
    """
    if alpha_is_gloss_shader(shader):
        return False

    path = resolve_texture(texture_name, game_root) if game_root else None
    if path and path.lower().endswith(".dds"):
        try:
            with open(path, "rb") as handle:
                return dds.has_alpha(handle.read())
        except (OSError, dds.DDSError, struct.error, IndexError) as exc:
            # struct.error is what a truncated block table raises. A
            # texture we cannot inspect is not a reason to abandon the
            # material — treat it as opaque and carry on.
            logger.debug("could not inspect alpha of %s: %s", path, exc)

    return getattr(image, "depth", 0) == 32


def _sample_with_uv_map(tree, texture_node, layer_name: str) -> bool:
    """Make a texture node sample a named UV layer rather than the active one."""
    try:
        uv_map = tree.nodes.new("ShaderNodeUVMap")
        uv_map.name = f"ExM_UV_{layer_name}"
        uv_map.uv_map = layer_name
        uv_map.location = (-1200, 100)
        tree.links.new(uv_map.outputs["UV"], texture_node.inputs["Vector"])
        return True
    except (AttributeError, KeyError, RuntimeError, TypeError) as exc:
        logger.debug("could not point %s at %s: %s", texture_node.name, layer_name, exc)
        return False


def _vertex_color_node(tree):
    """A node reading the mesh's vertex colour layer, or None."""
    try:
        node = tree.nodes.new("ShaderNodeVertexColor")
        node.name = "ExM_VertexColor"
        node.layer_name = COLOR_LAYER
        node.location = (-600, -400)
        return node
    except (AttributeError, RuntimeError, TypeError) as exc:
        logger.debug("could not create a vertex colour node: %s", exc)
        return None


def _mix_multiply(tree, first, second, name: str, location):
    """Multiply two colour sockets. ``second`` of None doubles instead.

    Doubling is how every modulate2x in this format finishes: the two
    maps are multiplied and the result scaled by two, so a mid-grey
    second map is neutral. ``diffuse_detail.fx`` states it outright as
    ``Diffuse.rgb * Details.rgb * 2.0``.
    """
    """Multiply two colour sockets together. Returns the output socket.

    MULTIPLY is the assumption throughout, not a measurement. It is
    what an AO map and a ``_vc`` vertex colour mean in every engine of
    this era, and it matches the data — both are greyscale, which is a
    shading mask and useless for anything else. A model that comes out
    too dark rather than merely different is the sign to question it.
    """
    try:
        mix = tree.nodes.new("ShaderNodeMixRGB")
        mix.name = name
        mix.blend_type = "MULTIPLY"
        mix.location = location
        mix.inputs["Fac"].default_value = 1.0
        tree.links.new(first, mix.inputs["Color1"])
        if second is None:
            mix.inputs["Color2"].default_value = (2.0, 2.0, 2.0, 1.0)
        else:
            tree.links.new(second, mix.inputs["Color2"])
        return mix.outputs["Color"]
    except (AttributeError, KeyError, RuntimeError, TypeError) as exc:
        # Blender 4.x replaced MixRGB with a generic Mix node. Losing the
        # modulation is a shading difference; losing the import over it
        # would be worse.
        logger.debug("could not create a multiply node: %s", exc)
        return None


def _multiply_by_vertex_color(tree, texture_node, principled) -> bool:
    """Modulate the texture by the mesh's vertex colour.

    The ``_vc`` shaders take the baked vertex colour and the diffuse map
    together. Left unconnected, the texture renders at full strength
    everywhere while the game shows it darkened wherever the artist
    baked shading in — ``house2`` carries 774 vertex colours running
    from 255 down to 0, a third of them below white. A bright clean
    brick wall in Blender against a grimy shaded one in the game is
    that difference and nothing else.

    MULTIPLY is the assumption here, not a measurement. It is what
    ``_vc`` means in every engine of this era and it matches the data —
    the colours are pure greyscale, which is a shading mask and useless
    for anything but modulation. If a model ever looks too dark rather
    than merely different, this is the line to question.

    Returns whether the connection was made.
    """
    try:
        vertex_color = tree.nodes.new("ShaderNodeVertexColor")
        vertex_color.name = "ExM_VertexColor"
        vertex_color.layer_name = COLOR_LAYER
        vertex_color.location = (-700, -200)

        mix = tree.nodes.new("ShaderNodeMixRGB")
        mix.name = "ExM_VertexColorMix"
        mix.blend_type = "MULTIPLY"
        mix.location = (-200, 0)
        mix.inputs["Fac"].default_value = 1.0

        tree.links.new(texture_node.outputs["Color"], mix.inputs["Color1"])
        tree.links.new(vertex_color.outputs["Color"], mix.inputs["Color2"])
        tree.links.new(mix.outputs["Color"], principled.inputs["Base Color"])
        return True
    except (AttributeError, KeyError, RuntimeError, TypeError) as exc:
        # Blender 4.x replaced MixRGB with a generic Mix node. Losing the
        # modulation is a shading difference; losing the import over it
        # would be worse, so the texture stays wired straight through.
        logger.debug("could not wire vertex colour: %s", exc)
        return False


def _alpha_is_gloss(texture_name: str, game_root: str | None, shader: str) -> bool:
    """Whether this texture's alpha is a specular mask.

    The other side of :func:`_image_has_alpha`, and decided the same
    way: by the shader. ``metal_elements_roof.dds`` is a continuous
    mask and its shader is ``specular_vc``; the Direct3D 8/9 convention
    of the era puts gloss in the diffuse map's alpha and the shader is
    where the game says so.

    This used to re-run the texel test, which had it disagree with
    :func:`_image_has_alpha` whenever a gloss texture happened to look
    like a cutout — the alpha then went nowhere at all.
    """
    if not alpha_is_gloss_shader(shader):
        return False
    if not game_root:
        return False
    path = resolve_texture(texture_name, game_root)
    if not path or not path.lower().endswith(".dds"):
        return False
    try:
        with open(path, "rb") as handle:
            return dds.has_alpha(handle.read())
    except (OSError, dds.DDSError, struct.error, IndexError):
        return False


def _connect_gloss(tree, node, principled) -> bool:
    """Drive roughness from the diffuse map's alpha.

    Inverted: a high alpha means shiny, and roughness runs the other
    way. This is what puts the sheen back on slate and metal, which in
    the game is much of what makes their surface detail read at all.

    The inversion is the assumption; that the alpha is a gloss mask is
    the measurement. If a surface comes out uniformly matt or uniformly
    mirrored, this is the line to question.
    """
    try:
        invert = tree.nodes.new("ShaderNodeInvert")
        invert.name = "ExM_GlossToRoughness"
        invert.location = (-300, -500)
        tree.links.new(node.outputs["Alpha"], invert.inputs["Color"])
        tree.links.new(invert.outputs["Color"], principled.inputs["Roughness"])
        return True
    except (AttributeError, KeyError, RuntimeError, TypeError) as exc:
        logger.debug("could not wire gloss: %s", exc)
        return False


def _connect_alpha(tree, node, principled) -> bool:
    """Link the texture's alpha into the shader. True if it took."""
    try:
        tree.links.new(node.outputs["Alpha"], principled.inputs["Alpha"])
        return True
    except (KeyError, RuntimeError, AttributeError) as exc:
        logger.debug("could not connect alpha: %s", exc)
        return False


def _make_transparent(blender_material) -> None:
    """Put the material into a mode that actually shows the alpha.

    Both settings, not just the first: a material that blends but casts
    an opaque shadow gives every bush a solid black silhouette on the
    ground, which reads as a lighting bug rather than a material one.
    """
    for attribute in ("blend_method", "shadow_method"):
        try:
            setattr(blender_material, attribute, TRANSPARENT_BLEND_METHOD)
        except (AttributeError, TypeError) as exc:
            # Blender 4.2 dropped these in favour of EEVEE Next's own
            # settings. The add-on targets 3.6, but failing to import a
            # model over a viewport setting would be a poor trade.
            logger.debug("could not set %s: %s", attribute, exc)


def _material_identity(
    material: Material, prefix: str, vertex_color_available: bool = True
) -> str:
    """A name unique to this shader-and-texture combination.

    The texture list is part of the identity; a hash keeps the name
    readable when a vehicle material carries nine of them, while the
    shader and first texture stay visible for anyone reading the
    outliner.

    Whether the mesh carries a vertex colour layer is part of it too,
    for shaders that read one. The same shader on a mesh that has the
    layer and on a mesh that does not needs two different node graphs,
    and a cache keyed without it hands the second mesh the first one's
    material — which is the black-foliage bug arriving by another road.
    """
    import hashlib

    shader = material.shader or material.name or "material"
    suffix = "" if vertex_color_available or not uses_vertex_color(shader) else "_novc"
    if not material.textures:
        return f"{prefix}_{shader}{suffix}"

    primary = os.path.splitext(material.textures[0])[0]
    if len(material.textures) == 1:
        return f"{prefix}_{shader}_{primary}{suffix}"

    digest = hashlib.md5(
        "|".join(material.textures).encode("utf-8"), usedforsecurity=False,
    ).hexdigest()[:6]
    return f"{prefix}_{shader}_{primary}_{digest}{suffix}"


def _find_principled(tree):
    for node in tree.nodes:
        if getattr(node, "type", "") == "BSDF_PRINCIPLED":
            return node
    return None


def _diffuse_is_available(material: Material, game_root: str | None) -> bool:
    """Whether the diffuse texture can actually be loaded right now.

    Used before tearing a material down. Rebuilding one whose texture
    is missing produces the same empty material and destroys whatever
    was there in the meantime.
    """
    if not material.textures:
        return True

    for position, texture_name in enumerate(material.textures):
        if _slot_of(material, position) != SLOT_DIFFUSE:
            continue
        if bpy.data.images.get(texture_name) is not None:
            return True
        if not game_root:
            return False
        return resolve_texture(texture_name, game_root) is not None

    return True


def _is_complete(blender_material, material: Material) -> bool:
    """Whether a cached material is actually usable as it stands.

    A material that names textures must have an image node carrying an
    image with pixels, and that image must reach Base Color. Anything
    less renders as a flat colour, which is exactly what a missing
    texture looks like — so the name alone is not evidence that the
    material is finished.

    A material that names no textures has nothing to check and is
    accepted as it is.
    """
    if not material.textures:
        return True

    tree = getattr(blender_material, "node_tree", None)
    if tree is None:
        return False

    principled = _find_principled(tree)
    if principled is None:
        return False

    for node in tree.nodes:
        if getattr(node, "type", "") != "TEX_IMAGE":
            continue
        image = getattr(node, "image", None)
        if image is None or not _has_pixels(image):
            continue
        if _reaches_base_color(tree, node, principled):
            return True

    return False


def _outgoing(tree, node_name: str, socket_name: str):
    """Nodes reached from one output socket, as (node_name, socket_name)."""
    for link in getattr(tree, "links", ()):
        if isinstance(link, tuple):
            continue
        if getattr(getattr(link, "from_node", None), "name", None) != node_name:
            continue
        if getattr(getattr(link, "from_socket", None), "name", None) != socket_name:
            continue
        yield (
            getattr(getattr(link, "to_node", None), "name", None),
            getattr(getattr(link, "to_socket", None), "name", None),
        )


def _reaches_base_color(tree, node, principled) -> bool:
    """Whether ``node``'s Color output is linked to Base Color.

    Compared by node and socket NAME, never by object identity.
    ``node.outputs["Color"]`` hands back a fresh Python wrapper on every
    access in bpy, so ``is`` between two of them is false even when they
    address the same socket. Written with ``is`` first, this check
    failed for every material in real Blender while passing in the test
    double — which rebuilt every material on every import and made a
    working material indistinguishable from a broken one.
    """
    node_name = getattr(node, "name", None)
    principled_name = getattr(principled, "name", None)

    # Follow the graph rather than looking for one direct link. A
    # vertex-colour material puts a Mix node between the texture and
    # the shader, and a check that only accepted a direct link called
    # every such material broken and rebuilt it on every import.
    seen = {node_name}
    frontier = [(node_name, "Color")]
    while frontier:
        from_name, from_socket = frontier.pop()
        for to_name, to_socket in _outgoing(tree, from_name, from_socket):
            if to_name == principled_name and to_socket == "Base Color":
                return True
            if to_name in seen or to_name is None:
                continue
            seen.add(to_name)
            for output in ("Color", "Result"):
                frontier.append((to_name, output))

    for link in getattr(tree, "links", ()):
        if isinstance(link, tuple):
            # The test double stores links as (from_socket, to_socket).
            from_socket, to_socket = link
            if from_socket is None or to_socket is None:
                continue
            try:
                if (
                    from_socket is node.outputs["Color"]
                    and to_socket is principled.inputs["Base Color"]
                ):
                    return True
            except (KeyError, TypeError):
                continue
            continue

        if getattr(getattr(link, "from_node", None), "name", None) != node_name:
            continue
        if getattr(getattr(link, "to_node", None), "name", None) != principled_name:
            continue
        if getattr(getattr(link, "from_socket", None), "name", None) != "Color":
            continue
        if getattr(getattr(link, "to_socket", None), "name", None) != "Base Color":
            continue
        return True

    return False


def _clear_image_nodes(blender_material) -> None:
    """Strip the texture nodes so the material can be rebuilt in place."""
    tree = getattr(blender_material, "node_tree", None)
    if tree is None:
        return
    for node in [n for n in tree.nodes if getattr(n, "type", "") == "TEX_IMAGE"]:
        try:
            tree.nodes.remove(node)
        except (AttributeError, RuntimeError, ValueError):
            pass


def _has_pixels(image) -> bool:
    """Whether an image datablock actually carries an image.

    Blender keeps a datablock for a file it could not read: right name,
    zero size, no data. It looks like a loaded texture to anything that
    only checks the name.
    """
    try:
        if not getattr(image, "has_data", True):
            # Blender fills an image's buffer on demand, so has_data is
            # also False for a perfectly good file nothing has asked
            # for yet. Asking is what tells the two apart: touching
            # pixels forces the load, after which has_data is the
            # answer it claims to be. Without this the check condemns
            # working textures, and the repair below would rebuild them
            # for ever.
            _force_load(image)
            if not getattr(image, "has_data", True):
                return False
        size = getattr(image, "size", None)
        if not size:
            return False
        return size[0] > 0 and size[1] > 0
    except (AttributeError, IndexError, TypeError):
        return False


def _force_load(image) -> None:
    """Make Blender fetch an image's buffer, without copying it.

    ``len(image.pixels)`` needs the buffer and does not transfer it —
    reading the pixels themselves would move megabytes per texture.
    """
    try:
        len(image.pixels)
    except (AttributeError, RuntimeError, TypeError) as exc:
        logger.debug("could not force %s to load: %s", getattr(image, "name", "?"), exc)


def _load_image(texture_name: str, game_root: str | None):
    """Load a texture, or return None with the reason logged.

    A missing texture is not an error: models routinely reference files
    a partial install lacks, and refusing to build the material would
    lose the geometry too.
    """
    existing = bpy.data.images.get(texture_name)
    if existing is not None and _has_pixels(existing):
        return existing

    if not game_root:
        # No game folder configured. Recorded like a missing file so the
        # import reports it, rather than silently producing materials
        # with no images and no explanation.
        _missing_textures.add(texture_name)
        logger.debug("no game folder set, cannot load %s", texture_name)
        return None

    path = resolve_texture(texture_name, game_root)
    if path is None:
        _missing_textures.add(texture_name)
        logger.debug("texture not found: %s", texture_name)
        return None

    if existing is not None:
        # An image datablock with the right name but no pixels. These
        # survive in the .blend after an import that could not read the
        # file, and reusing one by name alone means the texture never
        # appears again however many times the model is re-imported —
        # reinstalling the add-on does not help, because the empty
        # datablock is saved in the file, not in the add-on.
        if texture_name not in _reported_empty:
            _reported_empty.add(texture_name)
            logger.info(
                "%s exists in this .blend with no pixel data; reloading it "
                "from %s", texture_name, path,
            )
        try:
            existing.filepath = path
            existing.reload()
            if _has_pixels(existing):
                return existing
        except (AttributeError, RuntimeError) as exc:
            logger.debug("could not reload %s: %s", texture_name, exc)

        # The reload did not revive it, so the datablock has to give up
        # both the name and the filepath before anything else is tried.
        # This is where the recovery used to fail silently and for ever:
        # it asked for ``load(path, check_existing=True)``, which
        # matches on filepath — the filepath just assigned two lines
        # above — and Blender handed the same empty datablock straight
        # back. Every import then reported the same texture again.
        _retire(existing, texture_name)

    image = None
    try:
        # check_existing only when nothing was retired: otherwise it
        # would find what was just put aside.
        image = bpy.data.images.load(path, check_existing=existing is None)
    except RuntimeError as exc:
        logger.debug("Blender could not load %s: %s", path, exc)

    if image is not None and not _has_pixels(image):
        # Loaded without complaint and carrying nothing. Blender does
        # not raise for a DDS it cannot decode, so an exception is not
        # the only way this fails and was not the common one.
        logger.debug("%s loaded from %s with no pixels", texture_name, path)
        _discard(image)
        image = None

    if image is None:
        image = _load_dds_ourselves(path, texture_name)
        if image is None:
            logger.warning(
                "%s is on disk at %s and could not be read by Blender or by "
                "this add-on", texture_name, path,
            )
            _unreadable_textures.add(texture_name)
            return None
        return image

    try:
        image.name = texture_name
    except (AttributeError, TypeError) as exc:
        logger.debug("could not name %s: %s", texture_name, exc)
    return image


def _retire(image, texture_name: str) -> None:
    """Put an unreadable datablock out of the way of its replacement.

    Renaming does two things, and both are needed: the replacement can
    take the canonical name, and anything still pointing at this
    datablock keeps pointing at something that says what it is.
    """
    try:
        image.name = f"{texture_name}.unreadable"
    except (AttributeError, TypeError) as exc:
        logger.debug("could not set aside the empty %s: %s", texture_name, exc)


def _discard(image) -> None:
    """Drop a datablock that turned out to hold nothing.

    Only ever called on one this function has just created and not
    handed to anybody, so there is no user to strip it from.
    """
    try:
        bpy.data.images.remove(image)
    except (AttributeError, KeyError, RuntimeError, TypeError) as exc:
        logger.debug("could not discard an empty image: %s", exc)


def _load_dds_ourselves(path: str, texture_name: str):
    """Build a Blender image from a DDS this SDK can read and Blender can't.

    Blender's DDS support covers the common cases and not all of them,
    and a texture it refuses is indistinguishable from a missing one
    once it reaches the material: the model arrives with a shader, no
    image, and a flat colour where the texture should be. Since the
    SDK already has a decoder good enough to write these files, it may
    as well read the ones Blender declines.

    Returns None for anything that is not a DDS or that the decoder
    cannot handle either, so the caller still reports it as missing.
    """
    if not path.lower().endswith(".dds"):
        return None

    try:
        with open(path, "rb") as handle:
            data = handle.read()
        rgba, width, height = dds.decode(data)
    except (OSError, dds.DDSError, struct.error, IndexError) as exc:
        logger.debug("SDK decoder could not read %s either: %s", path, exc)
        return None

    try:
        image = bpy.data.images.new(
            texture_name, width=width, height=height, alpha=True
        )
    except (AttributeError, RuntimeError, TypeError) as exc:
        logger.debug("could not create an image for %s: %s", texture_name, exc)
        return None

    # Blender stores pixels bottom-up as floats; DDS is top-down bytes.
    row = width * 4
    flat = [0.0] * (width * height * 4)
    for y in range(height):
        source = (height - 1 - y) * row
        target = y * row
        for i in range(row):
            flat[target + i] = rgba[source + i] / 255.0

    try:
        # foreach_set moves the whole buffer in one call; assigning the
        # sequence element by element is minutes rather than seconds on
        # a 1024x1024 texture.
        image.pixels.foreach_set(flat)
    except (AttributeError, TypeError):
        image.pixels = flat

    try:
        image.pack()
    except (AttributeError, RuntimeError):
        # Packing keeps the decoded pixels with the .blend. Without it
        # the image still works for this session.
        pass

    logger.info(
        "%s was decoded by the SDK after Blender declined it (%sx%s)",
        texture_name, width, height,
    )
    return image


def apply_materials(mesh, model, game_root: str | None) -> int:
    """Attach a model's materials to a Blender mesh.

    Attached in the model's own order, so slot *i* on the Blender mesh
    is ``model.materials[i]`` and a polygon's ``material_index`` can be
    set straight from the mesh header's material index. Which mesh uses
    which material is stored at offset 44 of each mesh header — see
    ``formats/exm/gam.py``. Until that was decoded every mesh got the
    first material, which is why a model carrying several textures
    showed only one.

    Returns how many were attached.
    """
    if not model.materials:
        return 0

    # Whether a lightmap has coordinates of its own to sample with.
    uv2_available = len(getattr(mesh, "uv_layers", [])) > 1
    # And whether there is a vertex colour layer to read at all. Vertex
    # types 7, 10, 11 and 15 carry no colour, so a mesh of one of those
    # has no layer however loudly its shader name asks for one.
    vertex_color_available = any(
        getattr(layer, "name", None) == COLOR_LAYER
        for layer in getattr(mesh, "color_attributes", [])
    )

    for material in model.materials:
        mesh.materials.append(
            build_material(
                material, game_root,
                uv2_available=uv2_available,
                vertex_color_available=vertex_color_available,
            )
        )
    return len(model.materials)
