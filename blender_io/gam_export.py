# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Writes a Blender mesh out as a ``.gam``.

Uses HTAToolchain's parser rather than this SDK's own. That parser
round-trips a real shipped model with only its own version stamp
differing, which means it can generate the material, shadow and
metadata chunks this SDK understands well enough to read past but not
well enough to write.

The dependency is optional and located at call time: a user who only
edits existing maps never needs it, and failing at import would make
the whole add-on refuse to load over a feature they don't use.

Geometry goes through the shared coordinate transform in reverse, so a
model exported from Blender comes back the same size and the right way
up when re-imported.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import bpy

from core.coordinates import (
    DEFAULT_HEIGHT_SCALE,
    DEFAULT_XY_SCALE,
    CoordinateTransform,
)
from utils.errors import EXMeditorError
from utils.logging import get_logger

logger = get_logger("blender_io.gam_export")

#: Add-on folder names HTAToolchain is installed under.
_TOOLCHAIN_NAMES = ("HTAToolchain", "hta_toolchain", "htatoolchain")


def find_toolchain():
    """Import HTAToolchain's parser, or raise with what to do about it.

    Imports ``htaparser`` directly rather than the package: the
    package's ``__init__`` is Blender glue that imports ``imp``, a
    module removed in Python 3.12, so importing it would fail on newer
    builds for reasons unrelated to parsing.
    """
    # Already imported, or installed as an ordinary module.
    for name in ("htaparser", *(f"{p}.htaparser" for p in _TOOLCHAIN_NAMES)):
        if name in sys.modules:
            return sys.modules[name]

    for directory in _candidate_directories():
        path = os.path.join(directory, "htaparser.py")
        if not os.path.isfile(path):
            continue
        spec = importlib.util.spec_from_file_location("htaparser", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules["htaparser"] = module
        spec.loader.exec_module(module)
        logger.info("Using HTAToolchain parser from %s", path)
        return module

    raise EXMeditorError(
        "HTAToolchain is not installed. It provides the .gam writer this SDK "
        "does not have — install it as a Blender add-on, then try again."
    )


def _candidate_directories():
    """Places HTAToolchain might be, most likely first."""
    seen: list[str] = []
    try:
        import addon_utils  # noqa: F401 - Blender only

        for path in bpy.utils.script_paths(subdir="addons"):
            for name in _TOOLCHAIN_NAMES:
                seen.append(os.path.join(path, name))
    except (ImportError, AttributeError):
        pass

    # Beside this add-on, for a manual drop-in.
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in _TOOLCHAIN_NAMES:
        seen.append(os.path.join(os.path.dirname(here), name))
    return seen


def export_meshes_to_gam(
    objects,
    path: str,
    *,
    context=None,
    transform=None,
    vertex_type: str | None = None,
) -> None:
    """Write several meshes to one ``.gam``.

    The format holds many meshes per file — a vehicle cab ships as 28 —
    so a multi-part model is one file, not several. An earlier version
    refused more than one object, which contradicted the format and
    forced users to destroy their part structure with Ctrl+J.
    """
    objects = [o for o in objects if getattr(o, "type", None) == "MESH"]
    if not objects:
        raise EXMeditorError("No mesh objects selected")

    find_toolchain()
    for obj in objects:
        _check_exportable(obj)

    operator = _find_export_operator()
    if operator is None:
        raise EXMeditorError(
            "HTAToolchain's export operator is not registered — its add-on may "
            "be installed but disabled. Enable it in Preferences > Add-ons."
        )

    _export_in_isolation(operator, objects, path, vertex_type=vertex_type)

    if not os.path.isfile(path):
        raise EXMeditorError(
            f"HTAToolchain did not produce {os.path.basename(path)}"
        )

    verify_written_model(path)
    verify_texture_names(objects, path)


def verify_texture_names(objects, path: str) -> None:
    """Check the texture names that landed in the file are the real ones.

    The skin chunk stores each filename in a ``char[40]`` (HTAToolchain
    packs ``<40s``, which cuts without saying so; the four bytes after
    it are the UV set, not name), so 39 characters is all there is
    room for with the NUL. MEASURED on a model exported from an ``.obj``::

        Blender image  tripo_image_ea527cc5-57ee-420f-a393-85d62720b655.png
        written        tripo_image_ea527cc5-57ee-420f-a393-85d6

    40 characters, and the extension gone with the rest. The game then
    looks for a file of that name, finds nothing, and draws the model
    with no material — which is exactly "the material was lost", with
    no error anywhere to say why.

    Rather than guess where the cut falls, this compares what is in the
    file against the images the materials actually name. The longest of
    the 1136 texture names the game itself uses is 37 characters, so no
    shipped material is anywhere near the limit.
    """
    from formats.exm.gam import SKIN_CHUNK_ID, read_container, read_materials

    wanted = set()
    for obj in objects:
        mesh = getattr(obj, "data", None)
        for material in getattr(mesh, "materials", None) or []:
            for image in _images_of(material):
                for candidate in (
                    os.path.basename(getattr(image, "filepath", "") or ""),
                    getattr(image, "name", "") or "",
                ):
                    if candidate:
                        wanted.add(candidate)
    if not wanted:
        return

    try:
        _subtype, chunks = read_container(path)
        written = [
            texture
            for chunk in chunks if chunk.chunk_id == SKIN_CHUNK_ID
            for material in read_materials(chunk.data)
            for texture in material.textures
        ]
    except Exception as exc:  # noqa: BLE001 - a check must not break the export
        logger.debug("could not read %s back for its textures: %s", path, exc)
        return

    for name in written:
        if name in wanted:
            continue
        source = next((w for w in wanted if w.startswith(name)), None)
        if source is None:
            logger.warning(
                "%s names texture %r, which is not one of the images the "
                "materials use (%s)",
                os.path.basename(path), name, ", ".join(sorted(wanted)),
            )
            continue
        raise EXMeditorError(
            f"{os.path.basename(path)} was written with the texture name cut "
            f"short: {name!r} ({len(name)} characters) instead of "
            f"{source!r} ({len(source)}). The format stores it in 40 bytes, "
            "and the game will look for the cut name, find nothing, and draw "
            "the model with no material. Rename the image to 39 characters "
            "or fewer (the longest name the game itself uses is 37) and "
            "export again."
        )


def _images_of(material):
    """Every image datablock a material's nodes point at."""
    tree = getattr(material, "node_tree", None)
    for node in getattr(tree, "nodes", None) or []:
        if getattr(node, "type", "") != "TEX_IMAGE":
            continue
        image = getattr(node, "image", None)
        if image is not None:
            yield image


#: Below this largest dimension a written model is probably a speck.
#: MEASURED over 1336 shipped models: median largest dimension 10.9
#: units, a terrain cell is 8, rocks and buildings run 30-120 — but
#: 386 are under 4 (ammo, suspension parts, gadgets), so this is a
#: warning, never a refusal. A default Blender cube is 2 units, which
#: exported as-is is the commonest reason a correctly created model
#: "does not appear".
MINIMUM_VISIBLE_EXTENT = 4.0


def verify_written_model(path: str) -> None:
    """Read the file back and confirm it holds usable geometry.

    Worth the extra read: a .gam that exists but is empty or malformed
    registers and assigns exactly like a good one, so the first sign of
    trouble would otherwise be an invisible object in the game — with
    nothing anywhere to say why.
    """
    from formats.exm.gam import read_model

    size = os.path.getsize(path)
    if size < 64:
        raise EXMeditorError(
            f"{os.path.basename(path)} is only {size} bytes — the exporter "
            "produced an empty file"
        )

    try:
        model = read_model(path)
    except EXMeditorError as exc:
        raise EXMeditorError(
            f"{os.path.basename(path)} was written but cannot be read back: "
            f"{exc.message}. The game will not be able to load it either."
        ) from exc

    if model.total_triangles() == 0:
        raise EXMeditorError(
            f"{os.path.basename(path)} contains {model.total_vertices()} "
            "vertices but no triangles — nothing would be drawn"
        )

    bounds = None
    for mesh in model.meshes:
        if mesh.stored_bounds is not None:
            bounds = mesh.stored_bounds
            break
    extent = 0.0
    if bounds is not None:
        size_vector = bounds.size()
        extent = max(size_vector.x, size_vector.y, size_vector.z)

    # Geometry must sit around the model's own origin. A model whose
    # vertices are hundreds of units away carries a world position
    # baked in, and the game will draw it that far from where the node
    # says it is — invisible in practice, with nothing to indicate why.
    if bounds is not None:
        centre = max(
            abs((bounds.min_corner.x + bounds.max_corner.x) * 0.5),
            abs((bounds.min_corner.y + bounds.max_corner.y) * 0.5),
            abs((bounds.min_corner.z + bounds.max_corner.z) * 0.5),
        )
        if centre > max(extent, 1.0) * 5.0:
            raise EXMeditorError(
                f"{os.path.basename(path)} has its geometry {centre:.0f} units "
                f"from its own origin while measuring only {extent:.0f} across. "
                "The object's map position was baked into the mesh; the game "
                "would draw it far from where it is placed."
            )

    logger.info(
        "Verified %s: %d mesh(es), %d vertices, %d triangles, largest "
        "dimension %.1f game units",
        os.path.basename(path),
        len(model.meshes),
        model.total_vertices(),
        model.total_triangles(),
        extent,
    )
    if bounds is not None:
        _report_orientation(os.path.basename(path), bounds)
    if 0.0 < extent < MINIMUM_VISIBLE_EXTENT:
        logger.warning(
            "  %s measures only %.1f game units across. A terrain cell is 8 "
            "and the median shipped model 10.9; a default Blender cube is 2. "
            "If this is meant to stand on a map, scale it up in Blender and "
            "export again (ammo and vehicle parts are legitimately this small).",
            os.path.basename(path), extent,
        )


def _report_orientation(name: str, bounds) -> None:
    """Say which way up the written model is, in the game's own terms.

    A model is stored **Y up** and standing on Y near zero. MEASURED
    over 698 shipped models: for 596 of them (85%) it is Y whose
    minimum lies closest to zero — the axis the model rests on —
    against 56 for Z and 46 for X, and the medians say the same
    (min Y -1.09, min Z -5.56, min X -6.29).

    So a written model resting on Z is one that was lying on its side
    in Blender. That is worth saying with numbers, at the moment the
    file is written, rather than leaving it to be discovered in the
    editor: 102 shipped models genuinely do rest on X or Z, so this
    cannot be an error — but it is never what someone means by a
    building or a rock.
    """
    lows = {
        "X": abs(bounds.min_corner.x),
        "Y": abs(bounds.min_corner.y),
        "Z": abs(bounds.min_corner.z),
    }
    resting = min(lows, key=lows.get)
    if resting == "Y":
        logger.info(
            "  %s stands on Y (%.2f .. %.2f), which is up in this format",
            name, bounds.min_corner.y, bounds.max_corner.y,
        )
        return

    logger.warning(
        "  %s rests on %s (%.2f .. %.2f) while Y — the format's up axis — "
        "runs %.2f .. %.2f through it. The model is on its side: it was "
        "lying down in Blender, and the export wrote what it saw. Stand it "
        "up in Blender and export again. (596 of 698 shipped models rest "
        "on Y.)",
        name, resting,
        getattr(bounds.min_corner, resting.lower()),
        getattr(bounds.max_corner, resting.lower()),
        bounds.min_corner.y, bounds.max_corner.y,
    )



def export_mesh_to_gam(obj, path: str, *, context=None, transform=None) -> None:
    """Write ``obj``'s geometry to ``path`` as a ``.gam``.

    Delegates to HTAToolchain's own exporter. It already converts a
    Blender mesh to this format, and reimplementing that here would
    mean this SDK guessing at the material, shadow and metadata chunks
    it can read past but not generate — the exact gap the toolchain
    fills.
    """
    find_toolchain()  # fail early with a useful message if it is absent

    operator = _find_export_operator()
    if operator is None:
        raise EXMeditorError(
            "HTAToolchain's export operator is not registered — its add-on may "
            "be installed but disabled. Enable it in Preferences > Add-ons, or "
            "export the mesh yourself with File > Export > HTA and place the "
            ".gam under data/models/custom/, then run this again to register it."
        )

    _check_exportable(obj)

    _export_in_isolation(operator, obj, path)

    if not os.path.isfile(path):
        raise EXMeditorError(
            f"HTAToolchain did not produce {os.path.basename(path)}"
        )


def _export_in_isolation(
    operator, objects, path: str, *, vertex_type: str | None = None
) -> None:
    """Run the exporter with only ``objects`` visible to it.

    HTAToolchain iterates ``bpy.data.objects`` — every object in the
    BLEND FILE, not the scene — and needs each mesh to carry a
    material. On a loaded map that means the terrain, thousands of
    dynamic objects and any markers; the first one without a material
    aborts the export with an error naming none of this.

    A temporary scene therefore does not help, which is why an earlier
    attempt at exactly that changed nothing. The isolation has to
    happen at file level: the models are written from a separate blend
    file containing copies of the selected objects and nothing else.

    Blender cannot swap the open file in-process without discarding
    the user's work, so the export runs in a **background Blender
    instance** against a temporary file. Slower, but it is the only
    way to give the exporter a clean bpy.data while the user keeps
    their map open.
    """
    if not isinstance(objects, (list, tuple)):
        objects = [objects]

    isolate = _foreign_meshes_the_exporter_rejects()
    offenders = _meshes_the_exporter_rejects()
    # WARNING level so it reaches the status bar, not only the console.
    # Which path ran, and which meshes forced the choice, is the one
    # thing that has been missing from every report so far.
    logger.warning(
        "Export path: %s | %d mesh(es) in the file | problem meshes: %s",
        "background Blender" if isolate else "in-process",
        sum(1 for o in bpy.data.objects if getattr(o, "type", None) == "MESH"),
        ", ".join(offenders[:5]) or "none",
    )

    background_error = None
    if isolate:
        try:
            _export_via_background_blender(objects, path, vertex_type=vertex_type)
            return
        except EXMeditorError as exc:
            # The background instance is an optimisation, not the only
            # way. If it cannot run — a missing binary, an add-on it
            # fails to enable — falling back in-process is better than
            # refusing: it only fails if the file also contains meshes
            # without materials, and the user finds that out with a
            # message naming the real obstacle.
            #
            # Kept, not just logged. When the fallback then fails on a
            # mesh the isolation existed to avoid, the in-process error
            # is a true statement about a path that should never have
            # run, and reporting it alone sends the investigation after
            # the wrong problem. It cost several rounds of exactly that.
            background_error = exc.message
            logger.warning("background export failed (%s); trying in-process", exc.message)

    original_scene = bpy.context.window.scene
    temporary = bpy.data.scenes.new("ExM_Export_Temp")

    # A model stores geometry around its OWN origin; the map node
    # supplies the world position. HTAToolchain bakes the object's
    # transform into the vertices, so an object sitting at (1400, 900,
    # -1200) on the map exported with those coordinates inside the
    # mesh — the game then placed the node correctly and drew the
    # geometry another 1400 units away, which is why created models
    # were written, registered, placed and still invisible.
    restore_meshes = _prepare_meshes_for_export(objects)
    restore = _move_to_origin(objects)
    restore_names = _name_texture_nodes_for_export(objects)
    restore_materials = _name_materials_for_export(objects)
    restore_images = _name_images_for_export(objects)
    restore_types = _set_static_vertex_type(objects, vertex_type)
    _ensure_render_uv(objects)
    _ensure_vertex_format_inputs(objects)

    try:
        # Link the objects themselves, not copies: a copy would need its
        # mesh, materials and modifiers duplicated too, and any of them
        # differing would export something other than what was seen.
        for obj in objects:
            temporary.collection.objects.link(obj)
        bpy.context.window.scene = temporary

        for other in temporary.collection.objects:
            other.select_set(True)
        bpy.context.view_layer.objects.active = objects[0]

        # EXEC_DEFAULT: run it directly rather than opening its file
        # browser, which would abandon the export half done.
        try:
            operator("EXEC_DEFAULT", filepath=path)
        except Exception as exc:  # noqa: BLE001 - re-raised with context
            if background_error is None:
                raise
            raise EXMeditorError(
                f"The isolated export could not start ({background_error}), so "
                f"the export ran against the whole open file and failed there: "
                f"{exc}. Fix the first problem — the second is a consequence "
                "of falling back, not the cause."
            ) from exc
    finally:
        _restore_vertex_types(restore_types)
        _restore_image_names(restore_images)
        _restore_material_names(restore_materials)
        _restore_texture_node_names(restore_names)
        _restore_transforms(restore)
        _restore_meshes(restore_meshes)
        bpy.context.window.scene = original_scene
        for obj in objects:
            try:
                temporary.collection.objects.unlink(obj)
            except (RuntimeError, ReferenceError):
                pass
        try:
            bpy.data.scenes.remove(temporary)
        except (RuntimeError, ReferenceError):
            pass


#: Vertex format used by the game's own map decorations: position,
#: normal, colour, two UV sets. Identified by comparing a created model
#: against a shipped one — the only difference in the mesh header was
#: this field, 15 (a vehicle format carrying tangents but no vertex
#: colour) against 9.
#:
#: The value is the enum identifier HTAToolchain uses, not the label:
#: its items are ('9', 'XYZNCT2', '').
STATIC_VERTEX_TYPE = "9"

#: Format the only model measured to render correctly in the game's own
#: editor actually uses: position, normal, uv, tangent (stride 48).
#:
#: MEASURED, not assumed. Every one of MSCV NOD's 36 meshes carries
#: component count 15 and stride 48. That model was made with
#: HTAToolchain and the editor draws it, so 15 is known to be
#: acceptable; 9 has never been measured on a model confirmed to work.
#: The two claims can both be true — shipped decorations may well use 9
#: while HTAToolchain's own output uses 15 — which is exactly why this
#: is now a choice instead of a constant.
TANGENT_VERTEX_TYPE = "15"


def _ensure_vertex_format_inputs(objects) -> None:
    """Give each mesh what the XYZNCT2 vertex format needs.

    That format stores position, normal, COLOUR and TWO UV sets — the
    name spells it out: N for normal, C for colour, T2 for two texture
    coordinate sets. A mesh with one UV map and no colour layer makes
    the exporter dereference None and fail with a TypeError naming
    neither the mesh nor the missing piece.

    Both additions are neutral: a second UV set copied from the first
    is what a model without a lightmap would carry anyway, and white
    vertex colour is the identity for the shading the engine multiplies
    it into.
    """
    for obj in objects:
        mesh = getattr(obj, "data", None)
        if mesh is None:
            continue

        layers = getattr(mesh, "uv_layers", None)
        if layers is not None and len(layers) == 1:
            try:
                # Blender copies the active layer's coordinates into a
                # new one, so this really duplicates the unwrap rather
                # than adding an empty set.
                layers.new(name="UVMap2")
            except (AttributeError, RuntimeError, TypeError):
                pass

        colors = getattr(mesh, "vertex_colors", None)
        if colors is not None and not len(colors):
            try:
                colors.new(name="Col")
            except (AttributeError, RuntimeError, TypeError):
                pass


def _ensure_render_uv(objects) -> None:
    """Mark a UV layer as the render layer on each mesh.

    ``calc_tangents()`` with no argument uses the layer flagged
    ``active_render``, and reports a missing one as
    ``UV Map "(null)" not found`` — which reads as "there is no UV map"
    even when there is. Smart UV Project does not always set the flag,
    so a user who unwrapped correctly still hit the same error with no
    indication of what differed.

    Not restored afterwards: the flag decides which layer renders, and
    a mesh with exactly one UV map has no other sensible value.

    The check must look at each LAYER, not at the collection. An
    earlier version asked ``uv_layers`` itself for ``active_render``;
    real Blender answers that with a layer object rather than with
    "something is flagged", so the guard saw a non-None value on every
    mesh and skipped the assignment entirely. The flag was therefore
    never set, and ``calc_tangents()`` failed with the exact message
    this function exists to prevent. It passed its test only because
    the fake collection had no such attribute — the test was checking
    the intended behaviour against a stand-in that could not exhibit
    the bug.
    """
    for obj in objects:
        mesh = getattr(obj, "data", None)
        layers = getattr(mesh, "uv_layers", None)
        if not layers:
            continue
        try:
            if any(getattr(layer, "active_render", False) for layer in layers):
                continue
        except TypeError:
            pass
        try:
            layers[0].active_render = True
            layers.active = layers[0]
        except (AttributeError, IndexError, TypeError):
            continue


def _set_static_vertex_type(objects, vertex_type: str | None = None):
    """Force a vertex format, overriding the user's HTAToolchain setting.

    Set here rather than left to the user's HTAToolchain preference,
    because that preference is read when its add-on registers: changing
    it has no effect until Blender restarts, so a user who changes it
    and exports immediately gets the old format with nothing to
    indicate why.

    Returns what is needed to put the objects back as they were.
    """
    # Resolved here, not in the signature: the constant is defined
    # below this function and a default would be evaluated too early.
    vertex_type = vertex_type or STATIC_VERTEX_TYPE
    saved = []
    for obj in objects:
        settings = getattr(obj, "htatools", None)
        if settings is None:
            continue
        try:
            previous = settings.vertex_type
        except AttributeError:
            continue
        saved.append((settings, previous))
        try:
            settings.vertex_type = vertex_type
        except (TypeError, ValueError):
            # An older toolchain may not offer this identifier; leaving
            # the object alone is better than failing the export.
            saved.pop()
    return saved


def _restore_vertex_types(saved) -> None:
    for settings, previous in saved:
        try:
            settings.vertex_type = previous
        except (AttributeError, TypeError, ValueError, ReferenceError):
            pass


#: The vertex-colour layer HTAToolchain looks for, by name and by type.
#: ``if 'color' in data.vertex_colors`` — and ``mesh.vertex_colors`` only
#: shows BYTE_COLOR corner layers, so a FLOAT_COLOR one is invisible to
#: it however it is named.
TOOLCHAIN_COLOR_LAYER = "color"


def _prepare_meshes_for_export(objects):
    """Hand the toolchain a mesh that matches what Blender is showing.

    Two things it cannot do for itself, both MEASURED by exporting one
    asymmetric box (1 x 2 x 4, upright on Z=0) four ways:

    ======================================  ==========================
    exported                                came out as
    ======================================  ==========================
    plain                                   X 0..1  Y 0..4  Z 0..2
    + object rotation 90 deg X and scale 10 X 0..1  Y 0..4  Z 0..2
    + the same baked into the mesh          X 0..10 Y 0..20 Z -40..0
    ======================================  ==========================

    **The object's rotation and scale are thrown away.** The middle row
    is identical to the first. The exporter reads ``vert.co`` — local
    mesh data — and only reaches for ``matrix_world`` when
    ``mesh.type == 4``, which is not the type a model uses. So a mesh
    the user rotated and scaled AS AN OBJECT, which is what everyone
    does after importing an ``.obj``, exports at its original size and
    its original orientation: "much smaller, and on its side".

    (The axis swap itself is right: Blender Z -> game Y. The upright box
    came out standing on game Y, which is up.)

    **A vertex-colour layer named ``color`` is required.** The SDK asks
    for vertex type XYZNCT2, whose writer unpacks ``*vertex.color``, and
    that is ``None`` unless ``'color' in data.vertex_colors``. With no
    such layer the export does not come out wrong — it raises
    ``TypeError: Value after * must be an iterable, not NoneType`` and
    writes nothing. The importer's own layer is called ``Col`` and is
    FLOAT_COLOR, and ``mesh.vertex_colors`` shows only BYTE_COLOR, so
    it is invisible on both counts.

    Everything is done on a COPY that is swapped onto the object for
    the export and swapped back after, so the user's mesh is never
    transformed and un-transformed — which would not be exact.
    """
    import mathutils  # Blender only

    saved = []
    identity = mathutils.Matrix.Identity(4)
    for obj in objects:
        original = getattr(obj, "data", None)
        if original is None or not hasattr(original, "copy"):
            continue
        try:
            copy = original.copy()
        except (AttributeError, RuntimeError, TypeError) as exc:
            logger.debug("could not copy %s's mesh: %s", obj.name, exc)
            continue

        # Rotation and scale, never translation: the map node carries
        # the position, and _move_to_origin deals with it.
        matrix = obj.matrix_world.to_3x3().to_4x4()
        if matrix != identity:
            copy.transform(matrix)
            logger.info(
                "%s: baked its rotation and scale into the geometry — the "
                "exporter reads mesh data and ignores the object's own "
                "transform", obj.name,
            )

        _ensure_toolchain_color_layer(copy, obj.name)
        obj.data = copy
        saved.append((obj, original, copy))
    return saved


def apply_baked_transform(objects) -> int:
    """Make the Blender object match the file that was just written.

    The export bakes rotation and scale into the geometry it writes
    (``_prepare_meshes_for_export``) and then puts the object back
    exactly as it was — still rotated, still scaled. The next map
    export then writes that scale onto the NODE as well, and the game
    applies both: a model scaled 258x in Blender to make a 1-unit
    ``.obj`` map-sized arrives 258 x 258 times too big, which from the
    inside looks like nothing at all.

    MEASURED in the user's own maps, every one written by this SDK::

        NewMap4      scale="258.550 258.550 258.550"   tripo_node
        r1m1         scale="345.826 345.826 161.062"   Icosphere
        r1m12        scale="1890.182 ..."               PlaneSKY
        r1m1-1-1     scale="5352.797 ..."               teeth_top

    against 0.6 .. 2.4 on every node of every shipped map.

    So after a successful write the object is given the baked mesh
    and an identity rotation and scale — Blender's own Apply
    Transform, done for the user, on a copy so a mesh shared with
    another object is not changed under it. What is on screen, what
    is in the file and what the node will say then all agree.
    Location is left alone: that is the placement, not the model.
    """
    import mathutils  # Blender only

    identity = mathutils.Matrix.Identity(4)
    applied = 0
    for obj in objects:
        original = getattr(obj, "data", None)
        if original is None or not hasattr(original, "copy"):
            continue
        try:
            matrix = obj.matrix_world.to_3x3().to_4x4()
        except (AttributeError, TypeError):
            continue
        if matrix == identity:
            continue
        try:
            baked = original.copy()
            baked.transform(matrix)
            obj.data = baked
            obj.rotation_mode = "XYZ"
            obj.rotation_euler = (0.0, 0.0, 0.0)
            obj.scale = (1.0, 1.0, 1.0)
        except (AttributeError, RuntimeError, TypeError) as exc:
            logger.warning("could not apply %s's transform: %s", obj.name, exc)
            continue
        applied += 1
        logger.info(
            "%s: rotation and scale applied to the mesh, as they were to the "
            "file — the node will carry neither", obj.name,
        )
    return applied


def _restore_meshes(saved) -> None:
    for obj, original, copy in saved:
        try:
            obj.data = original
        except (AttributeError, ReferenceError, TypeError):
            continue
        try:
            bpy.data.meshes.remove(copy)
        except (AttributeError, ReferenceError, RuntimeError, TypeError):
            pass


def _ensure_toolchain_color_layer(mesh, object_name: str) -> None:
    """Give ``mesh`` a BYTE_COLOR corner layer called ``color``.

    Filled from whatever colour attribute the mesh already has — the
    importer's ``Col`` — and white where there is none. White is the
    neutral value: the colours in shipped models are greyscale and read
    as baked ambient occlusion, so white is "nothing darkened" rather
    than an invention.
    """
    colors = getattr(mesh, "vertex_colors", None)
    if colors is None:
        return
    try:
        if TOOLCHAIN_COLOR_LAYER in colors.keys():
            return
    except (AttributeError, TypeError):
        return

    source = None
    for attribute in getattr(mesh, "color_attributes", None) or []:
        if getattr(attribute, "domain", "") == "CORNER":
            source = attribute
            break

    try:
        layer = colors.new(name=TOOLCHAIN_COLOR_LAYER)
    except (AttributeError, RuntimeError, TypeError) as exc:
        logger.warning(
            "%s: could not add the %r vertex-colour layer the exporter "
            "requires (%s); the export will fail",
            object_name, TOOLCHAIN_COLOR_LAYER, exc,
        )
        return

    if source is None:
        logger.info(
            "%s: no vertex colours, so a white %r layer was added — the "
            "exporter's XYZNCT2 writer has no value to write without one",
            object_name, TOOLCHAIN_COLOR_LAYER,
        )
        return

    try:
        for index, item in enumerate(layer.data):
            item.color = tuple(source.data[index].color)
    except (AttributeError, IndexError, TypeError) as exc:
        logger.debug("%s: could not copy %s: %s", object_name, source.name, exc)
        return
    logger.info(
        "%s: copied vertex colours from %r into the %r layer the exporter "
        "reads", object_name, source.name, TOOLCHAIN_COLOR_LAYER,
    )


def _move_to_origin(objects):
    """Temporarily place ``objects`` at the world origin.

    Returns what is needed to put them back. The first object's
    location becomes the reference point so a multi-part model keeps
    the relative arrangement of its parts — moving each to (0,0,0)
    independently would collapse them on top of each other.
    """
    if not objects:
        return []

    anchor = tuple(objects[0].location)
    saved = []
    for obj in objects:
        original = tuple(obj.location)
        # Scale and rotation are baked into the vertices by the
        # exporter AND carried on the map node, so leaving them applied
        # here means they are applied twice — a cube scaled 26x arrived
        # large enough to contain the whole scene.
        scale = tuple(obj.scale)
        # Blender's default rotation_mode is XYZ Euler, and assigning
        # rotation_quaternion on such an object changes nothing at all.
        # The reset below therefore left the rotation applied, and the
        # exporter baked it in while the node applied it again — the
        # same double-application the scale reset was written to stop.
        mode = getattr(obj, "rotation_mode", "QUATERNION")
        if mode == "QUATERNION":
            rotation = ("QUATERNION", tuple(obj.rotation_quaternion))
        elif mode == "AXIS_ANGLE":
            rotation = ("AXIS_ANGLE", tuple(obj.rotation_axis_angle))
        else:
            rotation = ("EULER", tuple(obj.rotation_euler))
        saved.append((obj, original, scale, rotation))

        obj.location = (
            original[0] - anchor[0],
            original[1] - anchor[1],
            original[2] - anchor[2],
        )
        obj.scale = (1.0, 1.0, 1.0)
        if rotation[0] == "QUATERNION":
            obj.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        elif rotation[0] == "AXIS_ANGLE":
            obj.rotation_axis_angle = (0.0, 0.0, 1.0, 0.0)
        else:
            obj.rotation_euler = (0.0, 0.0, 0.0)
    return saved


def _restore_transforms(saved) -> None:
    for obj, location, scale, rotation in saved:
        try:
            obj.location = location
            obj.scale = scale
            kind, values = rotation
            if kind == "QUATERNION":
                obj.rotation_quaternion = values
            elif kind == "AXIS_ANGLE":
                obj.rotation_axis_angle = values
            else:
                obj.rotation_euler = values
        except (AttributeError, ReferenceError, TypeError):
            pass


def _meshes_the_exporter_rejects() -> list[str]:
    """Names of meshes that would abort an in-process export.

    Returned rather than merely counted so a failing export can say
    WHICH mesh is the obstacle. Every report so far named only the
    object being exported, which was rarely the one at fault.
    """
    problems: list[str] = []
    for obj in bpy.data.objects:
        if getattr(obj, "type", None) != "MESH":
            continue
        mesh = getattr(obj, "data", None)
        slots = list(getattr(mesh, "materials", []) or [])
        reasons = []
        if not [m for m in slots if m is not None]:
            reasons.append("no material")
        if not getattr(mesh, "uv_layers", None):
            reasons.append("no UV map")
        if reasons:
            problems.append(f"{obj.name} ({', '.join(reasons)})")
    return problems


def _foreign_meshes_the_exporter_rejects() -> bool:
    """True if the file holds meshes the exporter would choke on.

    HTAToolchain iterates ``bpy.data.objects`` — every mesh in the
    file, not just the selection — and needs each one to have a
    material AND a UV map. With a map open that includes the terrain,
    which has neither and never will.

    Both conditions matter. An earlier version checked only materials,
    so once the terrain had one the in-process path was chosen again
    and the export died on the terrain's missing UV map instead —
    reporting a UV error for a cube that had been unwrapped correctly.

    A UV map that exists but is not flagged for render counts as
    missing here, because that is what ``calc_tangents()`` sees: it
    resolves the render layer, not "any layer". Every model this SDK
    imports used to arrive in exactly that state, so a file full of
    them passed a presence check and then failed the export anyway.

    Checked rather than assumed: on a clean file the in-process export
    works and is instant, and forcing everyone through a background
    Blender for a case that does not apply would be a poor trade.
    """
    for obj in bpy.data.objects:
        if getattr(obj, "type", None) != "MESH":
            continue
        mesh = getattr(obj, "data", None)

        slots = list(getattr(mesh, "materials", []) or [])
        if not [m for m in slots if m is not None]:
            return True

        layers = getattr(mesh, "uv_layers", None)
        if not layers:
            return True

        try:
            if not any(getattr(layer, "active_render", False) for layer in layers):
                return True
        except TypeError:
            continue
    return False


#: Kept so existing callers and tests keep working.
_foreign_meshes_without_materials = _foreign_meshes_the_exporter_rejects


def _export_via_background_blender(
    objects, path: str, *, vertex_type: str | None = None
) -> None:
    """Export from a clean blend file in a background Blender."""
    import subprocess
    import tempfile

    binary = bpy.app.binary_path
    if not binary or not os.path.isfile(binary):
        raise EXMeditorError(
            "Cannot locate the Blender executable to run the export in "
            "isolation. Save the model's meshes into an empty .blend and "
            "export with File > Export > HTA instead."
        )

    workdir = tempfile.mkdtemp(prefix="exm_gam_")
    scratch = os.path.join(workdir, "model.blend")

    # Same origin correction as the in-process path — the written
    # blend must contain the geometry already centred, since the
    # background Blender never sees the map.
    restore_meshes = _prepare_meshes_for_export(objects)
    restore = _move_to_origin(objects)
    restore_names = _name_texture_nodes_for_export(objects)
    restore_materials = _name_materials_for_export(objects)
    restore_images = _name_images_for_export(objects)
    restore_types = _set_static_vertex_type(objects, vertex_type)
    _ensure_render_uv(objects)
    _ensure_vertex_format_inputs(objects)
    try:
        # A file holding only these objects. bpy.data.libraries.write is
        # the only way to produce one without touching the open file.
        bpy.data.libraries.write(scratch, set(objects), fake_user=True)
    finally:
        _restore_vertex_types(restore_types)
        _restore_image_names(restore_images)
        _restore_material_names(restore_materials)
        _restore_texture_node_names(restore_names)
        _restore_transforms(restore)
        _restore_meshes(restore_meshes)

    script = os.path.join(workdir, "export.py")
    with open(script, "w", encoding="utf-8") as handle:
        handle.write(_BACKGROUND_SCRIPT)

    module = _toolchain_module_name()

    result = subprocess.run(
        [
            binary,
            "--background",
            scratch,
            # Without this the background instance loads every add-on
            # the user has installed — HumGen, Substance and the rest —
            # which takes tens of seconds per export and looks like a
            # hang. Only HTAToolchain is needed, and it is enabled
            # explicitly by the script below.
            "--factory-startup",
            "--python",
            script,
            "--",
            path,
            module or "",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    if not os.path.isfile(path):
        combined = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
        lines = [line for line in combined.splitlines() if line.strip()]
        # The last line is usually a location, not the cause; showing a
        # few gives the actual error. Guessing which path failed cost
        # several rounds of this.
        tail = " | ".join(lines[-4:]) if lines else "no output"
        raise EXMeditorError(
            f"The BACKGROUND export produced no file. Output: {tail}"
        )
    logger.info("Exported %s via a background Blender", os.path.basename(path))


def _toolchain_module_name() -> str | None:
    """The module name HTAToolchain is actually installed under.

    Taken from the running session rather than guessed. An earlier
    version tried a list of likely spellings in the background process
    and every one failed, because the folder is ``HTAToolchain`` and
    module names are case-sensitive — the background Blender then had
    no exporter and produced nothing, reporting only a
    ModuleNotFoundError for the last name tried.
    """
    try:
        import addon_utils
    except ImportError:
        return None

    for module in addon_utils.modules():
        name = getattr(module, "__name__", "")
        if "hta" in name.lower():
            return name
    return None


#: Runs inside the background Blender: enable the toolchain, link every
#: object into the scene, select them, and call the exporter.
_BACKGROUND_SCRIPT = """
import sys
import bpy

arguments = sys.argv[sys.argv.index("--") + 1:]
target = arguments[0]
module = arguments[1] if len(arguments) > 1 else ""

# --factory-startup disables every add-on, so the one actually needed is
# turned on here. The name comes from the parent process, which has it
# working — guessing spellings here failed silently.
#
# The preferences entry has to be created BEFORE the module is imported.
# HTAToolchain reads its own preferences at module level:
#
#     preferences = bpy.context.preferences.addons[__package__].preferences
#
# and addon_utils.enable() imports the module first and creates that
# entry afterwards, so under --factory-startup — where no add-on
# preferences are loaded at all — the import always died with
# KeyError: key "HTAToolchain" not found. The background Blender then
# produced no file, the caller fell back in-process, and the failure
# that surfaced was the terrain's missing UV map: a true statement
# about a path that should never have run.
import addon_utils
candidates = [module] if module else []
candidates += ["HTAToolchain", "hta_toolchain", "htatoolchain"]
enabled = False
for candidate in candidates:
    if not candidate:
        continue
    try:
        addons = bpy.context.preferences.addons
        if candidate not in addons:
            entry = addons.new()
            entry.module = candidate
        addon_utils.enable(candidate, default_set=True, persistent=False)
        # Rebind the module global: at import time the AddonPreferences
        # class was not registered yet, so the lookup above returned
        # None even though the entry existed.
        imported = sys.modules.get(candidate)
        if imported is not None and getattr(imported, "preferences", 1) is None:
            imported.preferences = bpy.context.preferences.addons[candidate].preferences
        enabled = True
        break
    except Exception as exc:
        print("EXM: could not enable %s: %s" % (candidate, exc))

if not enabled:
    print("EXM: HTAToolchain could not be enabled in the background instance")

scene = bpy.context.scene
for obj in bpy.data.objects:
    if obj.name not in scene.collection.objects:
        scene.collection.objects.link(obj)
    obj.select_set(True)

meshes = [o for o in bpy.data.objects if o.type == "MESH"]
if meshes:
    bpy.context.view_layer.objects.active = meshes[0]

operator = None
for group_name in dir(bpy.ops):
    if "hta" not in group_name.lower():
        continue
    group = getattr(bpy.ops, group_name)
    for name in dir(group):
        if "export" in name.lower():
            operator = getattr(group, name)
            break

if operator is None:
    print("EXM: HTAToolchain export operator not found")
else:
    operator("EXEC_DEFAULT", filepath=target)
"""



#: Indices in the mesh chunk are 16-bit, so a mesh cannot address more
#: vertices than this. A hard format limit, not a preference.
MAX_VERTICES_PER_MESH = 65535

#: Above this the game's editor is at risk. Measured: its index-buffer
#: pool refused an allocation and the process died with
#: `d3d: [ AddIbPoolField ] : Asked size is too big!!!` right after
#: loading a freshly exported model. Shipped models are far below —
#: civilhouse1 is 733 triangles across three meshes, big_flag01 346
#: across five — so the ceiling is somewhere between, and this is a
#: deliberately cautious guess at it rather than a measurement.
RISKY_TRIANGLE_COUNT = 10000


def _check_density(obj, mesh) -> None:
    """Refuse geometry the format cannot address, warn about the rest.

    A modern mesh — a subdivided sphere, a sculpt, anything with a
    Subdivision modifier applied — carries orders of magnitude more
    geometry than 2005 content, and the engine was built for 2005
    content.
    """
    polygons = getattr(mesh, "polygons", None) or []
    vertices = getattr(mesh, "vertices", None) or []
    # Triangles, not faces: a quad becomes two.
    triangles = sum(max(len(getattr(p, "vertices", ())) - 2, 0) for p in polygons)

    if len(vertices) > MAX_VERTICES_PER_MESH:
        raise EXMeditorError(
            f"'{obj.name}' has {len(vertices)} vertices. The format stores "
            f"triangle indices as 16-bit numbers, so a mesh cannot exceed "
            f"{MAX_VERTICES_PER_MESH}. Decimate it, or split it into several "
            "objects — a .gam holds many meshes."
        )

    if triangles > RISKY_TRIANGLE_COUNT:
        logger.warning(
            "%s has %d triangles. Shipped models run to a few hundred, and "
            "the game's editor has been seen to die outright on a dense "
            "model with 'AddIbPoolField: Asked size is too big'. Consider a "
            "Decimate modifier before exporting.",
            obj.name, triangles,
        )


def _check_exportable(obj) -> None:
    """Refuse cases HTAToolchain's exporter cannot handle.

    Checked here because it fails deep inside that add-on with an
    AttributeError on a None skin list, which says nothing about what
    to do. The requirement is real: the format stores a material index
    per mesh, so a mesh with no material has nothing to store.
    """
    mesh = getattr(obj, "data", None)
    if mesh is None:
        raise EXMeditorError(f"'{obj.name}' has no mesh data")

    _check_density(obj, mesh)

    slots = list(getattr(mesh, "materials", []) or [])
    materials = [m for m in slots if m is not None]

    if materials and len(materials) != len(slots):
        # An empty slot reaches HTAToolchain as None and fails there
        # with the same unhelpful AttributeError as having no material
        # at all — a multi-part object joined with Ctrl+J collects these
        # easily.
        raise EXMeditorError(
            f"'{obj.name}' has {len(slots) - len(materials)} empty material "
            "slot(s). Remove them in Material Properties (the minus button) — "
            "the exporter cannot write a mesh with a blank slot."
        )

    if not materials:
        raise EXMeditorError(
            f"'{obj.name}' has no material. The .gam format stores a material "
            "per mesh, so the exporter cannot write one without it — add any "
            "material in the Material Properties tab and try again."
        )

    if not getattr(mesh, "polygons", None):
        raise EXMeditorError(
            f"'{obj.name}' has no faces — a model needs geometry to draw"
        )

    if not getattr(mesh, "uv_layers", None):
        # Refused rather than warned about. The exporter calls
        # calc_tangents(), which needs a UV map and raises deep inside
        # HTAToolchain with a message that names neither the object nor
        # what to do. Every shipped model carries UVs — the format
        # stores them per vertex — so this is a real requirement, not a
        # preference.
        raise EXMeditorError(
            f"'{obj.name}' has no UV map. The exporter needs one to compute "
            "tangents, and the format stores UVs per vertex. In Edit Mode "
            "select everything (A) and unwrap with U > Smart UV Project, then "
            "try again."
        )

    # A UV map that exists but is not flagged for render fails the same
    # way, with a message that reads as though there were none at all.
    _ensure_render_uv([obj])

    _warn_about_texture_nodes(materials, obj.name)


#: Node names HTAToolchain looks for in a material. It matches by NODE
#: NAME, not by node type, so an Image Texture node called
#: "Image Texture" is invisible to it — the material exports with no
#: texture and the game falls back to something arbitrary. This is the
#: cause of "the material is not preserved and a random one appears".
TEXTURE_NODE_NAMES = ("Diffuse", "Bump", "Lightmap", "Cube", "Detail")


def _name_texture_nodes_for_export(objects) -> list:
    """Rename each material's image node to what HTAToolchain looks for.

    It matches by NODE NAME, so an Image Texture node called "Image
    Texture" — Blender's default, and what everyone gets — is invisible
    to it. The material exports with a shader but no texture file, and
    the game binds whatever happens to be around. That is the whole of
    "the textures are broken and a random one is used": measured on a
    cube whose skin chunk named the shader and no image at all, beside
    a working model whose skin chunk named two .dds files.

    Warning about it was not enough. The message went to the system
    console, which is not open by default, so the export "succeeded"
    and the fault appeared much later as a texture nobody chose.

    Only acts when there is exactly one image node and none is already
    named for the exporter: with several, which one is the diffuse map
    is the user's decision, and guessing would silently pick wrong.

    Returns what is needed to put the names back.
    """
    saved = []
    for obj in objects:
        mesh = getattr(obj, "data", None)
        for material in getattr(mesh, "materials", None) or []:
            if material is None:
                continue
            tree = getattr(material, "node_tree", None)
            nodes = getattr(tree, "nodes", None)
            if nodes is None:
                continue
            try:
                existing = set(nodes.keys())
            except (AttributeError, TypeError):
                continue
            if existing & set(TEXTURE_NODE_NAMES):
                continue

            images = [n for n in nodes if getattr(n, "type", "") == "TEX_IMAGE"]
            if len(images) != 1:
                continue

            node = images[0]
            saved.append((node, node.name))
            node.name = TEXTURE_NODE_NAMES[0]
            logger.info(
                "%s: renamed image node %r to 'Diffuse' for export so its "
                "texture is written",
                getattr(obj, "name", "?"), saved[-1][1],
            )
    return saved


def _restore_texture_node_names(saved) -> None:
    for node, name in saved:
        try:
            node.name = name
        except (AttributeError, ReferenceError, TypeError):
            pass


#: Where the importer stashed the shader the model was written with.
#: Set in ``blender_io.texture_bridge.build_material``.
SHADER_PROP = "exm_shader"


def _name_materials_for_export(objects) -> list:
    """Put the source model's shader where the exporter reads it.

    CORRECTION. This used to rename the material, on the belief that
    the shader came from the material name the way the texture comes
    from the node name. It does not::

        HTAToolchain/__init__.py:891
            mtl.shader = material.htatools.shader_name

    A ``StringProperty`` on the material, defaulting to the add-on's
    preference, whose own default is ``bumpdiffuse_envalphagloss_spec``.
    That is why 27 of the 31 models exported so far carry exactly that
    shader, and why the four that came out right were the four that had
    been through HTAToolchain's own importer — it sets the property at
    line 397.

    So the value is written to the property, and the material's name is
    left alone. Renaming it was not merely useless: the name is what
    the exporter matches a mesh's material index against.
    """
    saved = []
    for obj in objects:
        mesh = getattr(obj, "data", None)
        for material in getattr(mesh, "materials", None) or []:
            if material is None:
                continue
            try:
                shader = material.get(SHADER_PROP)
            except (AttributeError, TypeError):
                continue
            if not shader or not isinstance(shader, str):
                # Built in Blender and never had one. The toolchain's
                # default is a better answer than an invented shader.
                continue
            settings = getattr(material, "htatools", None)
            if settings is None:
                logger.warning(
                    "%s: HTAToolchain's material settings are missing, so the "
                    "shader cannot be set and %r will be written instead of "
                    "%r", getattr(material, "name", "?"),
                    "bumpdiffuse_envalphagloss_spec", shader,
                )
                continue
            try:
                current = settings.shader_name
                if current == shader:
                    continue
                settings.shader_name = shader
            except (AttributeError, TypeError) as exc:
                logger.debug("could not set the shader on %s: %s",
                             getattr(material, "name", "?"), exc)
                continue
            saved.append((settings, current))
            logger.info(
                "%s: shader set to %r for export (was %r)",
                getattr(material, "name", "?"), shader, current,
            )
    return saved


def _restore_material_names(saved) -> None:
    for settings, name in saved:
        try:
            settings.shader_name = name
        except (AttributeError, ReferenceError, TypeError):
            pass


def _name_images_for_export(objects) -> list:
    """Name each image datablock after the file it came from.

    The exporter writes the IMAGE DATABLOCK'S NAME as the texture
    filename::

        HTAToolchain/__init__.py:901
            texture.filename = pointer.image.name

    Not the filepath. So a ``.dds`` loaded from disk exports correctly
    only because Blender happens to name a loaded image after its file
    — and an image that was renamed, or generated in Blender, exports
    under whatever it is called in the outliner. MEASURED: a model
    whose texture file had been renamed to ``1`` still wrote
    ``tripo_image_ea527cc5-57ee-420f-a393-85d6``, because that is what
    the datablock was still called.

    Restored afterwards, so the user's outliner is unchanged.
    """
    saved = []
    for obj in objects:
        mesh = getattr(obj, "data", None)
        for material in getattr(mesh, "materials", None) or []:
            if material is None:
                continue
            for image in _images_of(material):
                filepath = getattr(image, "filepath", "") or ""
                wanted = os.path.basename(filepath)
                current = getattr(image, "name", "") or ""
                if not wanted or wanted == current:
                    continue
                try:
                    image.name = wanted
                except (AttributeError, TypeError) as exc:
                    logger.debug("could not rename %r: %s", current, exc)
                    continue
                saved.append((image, current))
                logger.info(
                    "image %r renamed to %r for export — the exporter writes "
                    "the datablock's NAME as the texture filename",
                    current, wanted,
                )
    return saved


def _restore_image_names(saved) -> None:
    for image, name in saved:
        try:
            image.name = name
        except (AttributeError, ReferenceError, TypeError):
            pass


def _warn_about_texture_nodes(materials, object_name: str) -> None:
    for material in materials:
        tree = getattr(material, "node_tree", None)
        nodes = getattr(tree, "nodes", None)
        if nodes is None:
            logger.warning(
                "%s: material %r has no nodes; it will export without a texture",
                object_name, getattr(material, "name", "?"),
            )
            continue
        try:
            names = set(nodes.keys())
        except (AttributeError, TypeError):
            continue
        if not names & set(TEXTURE_NODE_NAMES):
            logger.warning(
                "%s: material %r has no node named 'Diffuse', so no texture is "
                "written and the game will show a default. Rename the Image "
                "Texture node to 'Diffuse' in the Shader Editor (N panel > "
                "Item > Name).",
                object_name, getattr(material, "name", "?"),
            )


def _find_export_operator():
    """Locate the toolchain's export operator, whatever it is called.

    Its id is built from the add-on's package name
    (``f'{__package__}.modelexport'``), so it changes with the folder
    the user installed it under — ``htatoolchain.modelexport`` for the
    usual name, something else for a renamed copy.

    Checked against ``bpy.ops`` properly rather than with ``getattr``:
    ``bpy.ops.anything.at_all`` returns a stub object instead of
    raising, so a missing operator looks present until it is called.
    That is exactly how a wrong guess reached a release.
    """
    known = getattr(bpy.types, "Operator", None)
    candidates: list[str] = []

    # Ask Blender what is actually registered.
    for cls in getattr(known, "__subclasses__", lambda: [])():
        identifier = getattr(cls, "bl_idname", "")
        if not identifier or "." not in identifier:
            continue
        group, _, name = identifier.partition(".")
        if "hta" in group.lower() and "export" in name.lower():
            candidates.append(identifier)

    # Fall back to the names the shipped versions use.
    candidates.extend(
        f"{package.lower()}.modelexport" for package in _TOOLCHAIN_NAMES
    )

    for identifier in candidates:
        group_name, _, name = identifier.partition(".")
        group = getattr(bpy.ops, group_name, None)
        if group is None:
            continue
        operator = getattr(group, name, None)
        if operator is None:
            continue
        poll = getattr(operator, "poll", None)
        if poll is None:
            continue
        try:
            poll()          # raises if the operator does not exist
        except Exception:   # noqa: BLE001 - not registered
            continue
        return operator
    return None


def _unused_direct_writer(obj, path, transform):
    """Kept only to document why direct writing is not attempted.

    Building a .gam from raw geometry needs the material, shadow and
    tool-metadata chunks populated. This SDK reads past those without
    understanding them, so generating one would be guesswork — and the
    toolchain already does it correctly.
    """
    parser_module = find_toolchain()

    if transform is None:
        scene = getattr(context, "scene", None) if context else None
        transform = CoordinateTransform(
            xy_scale=(scene.get("exm_scene_xy_scale", DEFAULT_XY_SCALE)
                      if scene else DEFAULT_XY_SCALE),
            height_scale=(scene.get("exm_scene_height_scale", DEFAULT_HEIGHT_SCALE)
                          if scene else DEFAULT_HEIGHT_SCALE),
        )

    vertices, triangles = _read_mesh(obj, transform)
    if not triangles:
        raise EXMeditorError(
            f"'{obj.name}' has no faces — a model needs geometry to draw"
        )

    _write(parser_module, path, obj.name, vertices, triangles)
    logger.info(
        "Wrote %s: %d vertices, %d triangles", path, len(vertices), len(triangles),
    )


def _read_mesh(obj, transform: CoordinateTransform):
    """Vertices in game space, and triangle indices.

    Object scale and rotation are baked in: the format stores those on
    the map node, and a model carrying them too would apply them twice.
    """
    mesh = obj.data
    matrix = getattr(obj, "matrix_world", None)

    vertices = []
    for vertex in mesh.vertices:
        co = vertex.co
        if matrix is not None:
            try:
                co = matrix @ co
            except TypeError:
                pass
        # Local geometry, so the offset conversion — not the position
        # one, which would apply the map-centring offset to every
        # vertex.
        from utils.math import Vector3

        local = Vector3(co[0] - obj.location[0], co[1] - obj.location[1], co[2] - obj.location[2])
        game = transform.blender_to_game_offset(local)
        vertices.append(game.as_tuple())

    triangles = []
    for polygon in mesh.polygons:
        indices = list(polygon.vertices)
        # Fan-triangulate: the format stores triangles only.
        for i in range(1, len(indices) - 1):
            triangles.append((indices[0], indices[i], indices[i + 1]))

    return vertices, triangles


def _write(parser_module, path: str, name: str, vertices, triangles) -> None:
    """Build a parser document and dump it.

    Deliberately thin: the toolchain owns the format, and any cleverness
    here would be this SDK guessing at chunks it does not understand.
    """
    parser = parser_module.Parser()
    parser.model_name = name

    builder = getattr(parser_module, "build_simple_model", None)
    if callable(builder):
        builder(parser, name, vertices, triangles)
    else:
        raise EXMeditorError(
            "The installed HTAToolchain does not expose a way to build a model "
            "from raw geometry. Export the mesh with HTAToolchain's own "
            "exporter instead, then use 'Register Existing Model'."
        )

    with open(path, "wb") as handle:
        parser.dump(handle)
