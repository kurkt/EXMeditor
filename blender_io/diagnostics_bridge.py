# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fills in the Blender-side end of the resolution chain.

The last three stages — object created, linked into a collection,
actually visible — can only be checked against a real scene, so they
live here rather than in ``formats``. Everything before them is
Blender-free and lives in ``formats/exm/model_trace.py``.

The distinction these stages draw is the one that was previously
impossible to make from the outside: an object that was never created,
one that exists but sits outside every collection, and one that is
linked but hidden all look identical in the viewport — and need
completely different fixes.
"""

from __future__ import annotations

import bpy

from core.diagnostics import DiagnosticsReport, Stage
from blender_io.world_bridge import CLASS_PROP, ORIGINAL_NAME_PROP


def annotate_with_scene(
    report: DiagnosticsReport,
    collection: bpy.types.Collection,
) -> None:
    """Advance each record through the Blender stages, in place.

    Records that already failed earlier in the chain are left alone —
    reporting "not visible" for a model whose file was never found
    would hide the real cause.
    """
    objects_by_name = _index_scene_objects(collection)

    for record in report.records:
        if record.expected_no_model:
            continue
        # Only nodes that got all the way through the file stages can
        # meaningfully be asked about their Blender object.
        if record.reached < Stage.HAS_GEOMETRY:
            continue

        entry = objects_by_name.get(record.node_name)
        if entry is None:
            record.fail(
                Stage.OBJECT_CREATED,
                "no Blender object with this node name exists in the scene",
            )
            continue

        obj, owning_collection = entry
        record.blender_object = obj.name
        record.advance(Stage.OBJECT_CREATED)

        if owning_collection is None:
            record.fail(
                Stage.LINKED,
                "object exists but is not linked into any collection",
            )
            continue
        record.collection = owning_collection
        record.advance(Stage.LINKED)

        hidden_reason = _hidden_reason(obj, entry)
        if hidden_reason is not None:
            record.fail(Stage.VISIBLE, hidden_reason)
            continue
        record.advance(Stage.VISIBLE)


def _index_scene_objects(
    collection: bpy.types.Collection,
) -> dict[str, tuple[bpy.types.Object, str | None]]:
    """Map NODE name -> (object, name of the collection holding it).

    Keyed on the node name the object was imported from, which the
    importer stores in ORIGINAL_NAME_PROP, not on ``obj.name``.

    Indexing by ``obj.name`` is why this diagnostic reported that not
    one of 1562 models had a Blender object, in the same scene where
    the coverage report counted 5888 imported and all of them
    visualised. The importer deliberately builds a readable display
    name out of the node id and the model it draws, and says so where
    it does it — so looking up the bare node name could never match,
    and the tool answered "nothing imported" about a scene that was
    fully imported.

    Two tools disagreeing about one scene meant one of them was
    measuring something other than what it claimed, and it was this
    one. The display name is kept as a fallback key for objects the
    user created by hand, which carry no such property.
    """
    found: dict[str, tuple[bpy.types.Object, str | None]] = {}

    def visit(current: bpy.types.Collection) -> None:
        for obj in current.objects:
            entry = (obj, current.name)
            found.setdefault(obj.name, entry)
            node_name = obj.get(ORIGINAL_NAME_PROP)
            if node_name:
                # Overwrites the display-name key deliberately: an
                # object that knows which node it came from is a better
                # answer than one that merely happens to be called that.
                found[node_name] = entry
        for child in current.children:
            visit(child)

    visit(collection)
    return found


def _hidden_reason(obj: bpy.types.Object, entry) -> str | None:
    """Why ``obj`` would not be seen, or ``None`` if it is visible.

    Checks the several independent ways Blender can hide something —
    they are genuinely different settings and a report that just said
    "hidden" would leave the user hunting for which one.
    """
    if getattr(obj, "hide_viewport", False):
        return "hidden in the viewport (monitor icon)"
    if getattr(obj, "hide_get", None) is not None:
        try:
            if obj.hide_get():
                return "hidden in the outliner (eye icon)"
        except (RuntimeError, TypeError):
            pass  # not linked to a view layer; the LINKED stage covers that
    if getattr(obj, "hide_render", False):
        # Not a viewport problem, but worth surfacing rather than
        # silently reporting the object as fine.
        return None

    data = getattr(obj, "data", None)
    if data is not None:
        vertices = getattr(data, "vertices", None)
        if vertices is not None and len(vertices) == 0:
            return "object has mesh data with no vertices"
    return None
