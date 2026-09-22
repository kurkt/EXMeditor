# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fills in the scene half of a coverage report.

``core/coverage.py`` counts what the map asks for; this counts what the
scene delivered, and classifies every mesh object so the totals can be
read without arithmetic. A scene holding 1580 meshes for 1579 expected
models is correct — the extra is terrain — but that only becomes
obvious when the breakdown is stated rather than implied.

Objects are matched to model ids by the ``exm_id`` custom property the
importer writes, not by name: names are per-node and say nothing about
which model was used.
"""

from __future__ import annotations

import bpy

from core.coverage import CoverageReport, ModelCoverage

#: Custom properties written by the importer.
ASSET_ID_PROP = "exm_id"
CLASS_PROP = "exm_class"
TERRAIN_PROP = "exm_cell_size"
MARKER_PROP = "exm_diag_score"


def annotate_with_scene(report: CoverageReport, collection: bpy.types.Collection) -> None:
    """Count imported instances and classify the scene's meshes.

    Only objects carrying real geometry count as imported models. An
    Empty means the model could not be loaded — the resolution
    diagnostics explains why — and counting it here would report full
    coverage for a map whose models are all missing.
    """
    for obj, _owner in _walk(collection):
        if _is_terrain(obj):
            report.composition.terrain += 1
            continue
        if MARKER_PROP in obj:
            report.composition.diagnostic_markers += 1
            continue

        has_geometry = _has_geometry(obj)
        asset_id = obj.get(ASSET_ID_PROP)

        if asset_id and has_geometry:
            coverage = report.models.setdefault(
                asset_id, ModelCoverage(model_id=asset_id),
            )
            coverage.imported += 1
            report.composition.imported_models += 1

            # Credit the visual to the node's own class, so the report
            # can say which classes actually produce geometry rather
            # than leaving it to be inferred from totals.
            node_class = obj.get(CLASS_PROP)
            if node_class and node_class in report.by_class:
                report.by_class[node_class].visualised += 1
        elif has_geometry:
            # Geometry the SDK didn't import: user-created, or a model
            # whose provenance was lost. Worth counting separately
            # rather than silently inflating the model total.
            report.composition.other += 1


def _walk(collection: bpy.types.Collection):
    for obj in collection.objects:
        yield obj, collection
    for child in collection.children:
        yield from _walk(child)


def _is_terrain(obj: bpy.types.Object) -> bool:
    return TERRAIN_PROP in obj


def _has_geometry(obj: bpy.types.Object) -> bool:
    data = getattr(obj, "data", None)
    if data is None:
        return False
    vertices = getattr(data, "vertices", None)
    return vertices is not None and len(vertices) > 0
