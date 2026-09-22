# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Traces model resolution from ``world.xml`` to a parsed ``.gam``.

Covers every stage that doesn't need Blender, so the same trace can run
from a test or a CLI. The Blender-side stages (object created, linked,
visible) are filled in afterwards by ``blender_io``.

Deliberately does no fixing and no fallback: the point is to report
where the chain breaks, exactly as the real import would experience it.
If this module started compensating for problems, it would stop being
able to describe them.
"""

from __future__ import annotations

import os

from core.diagnostics import DiagnosticsReport, ResolutionRecord, Stage
from core.objects import ObjectInstance
from formats.exm.gam import read_model
from formats.exm.model_catalog import (
    ModelCatalog,
    ModelEntry,
    read_catalogs_from_servers,
    resolve_game_relative_path,
    resolve_model_file,
)
from utils.errors import EXMeditorError
from utils.logging import get_logger

logger = get_logger("formats.exm.model_trace")

#: Node classes that are expected to carry visible geometry. Sound
#: emitters and plain grouping nodes legitimately have no model, and
#: counting them as failures would bury the real ones.
GEOMETRY_CLASSES = frozenset({"SgAnimatedModelNode", "SgGameUnitNode"})


def build_catalogue_with_sources(
    manifest,
    map_folder: str,
    game_root: str,
) -> tuple[ModelCatalog, dict[str, str], list[str]]:
    """Load every model catalogue the manifest points at.

    Returns ``(catalogue, id_to_source_file, problems)`` — the mapping
    from model id to the catalogue that supplied it is what lets a
    report say *which* file an entry came from, or that no catalogue
    provided it at all.
    """
    catalogue = ModelCatalog()
    sources: dict[str, str] = {}
    problems: list[str] = []

    for key in ("SERVERS", "STATICSERVERS"):
        reference = manifest.file_ref(key) if manifest else None
        if not reference:
            problems.append(f"{key}: not listed in the map manifest")
            continue

        index_path = resolve_game_relative_path(reference, game_root)
        if index_path is None:
            basename = os.path.basename(reference.replace("\\", "/"))
            from formats.exm.ssl import resolve_in_folder

            index_path = resolve_in_folder(map_folder, basename)
        if index_path is None:
            problems.append(f"{key}: file not found ({reference})")
            continue

        inner: list[str] = []
        try:
            found = read_catalogs_from_servers(index_path, game_root, problems=inner)
        except EXMeditorError as exc:
            problems.append(f"{key}: could not be read — {exc.message}")
            continue
        # A catalogue index that resolves but whose catalogues are all
        # missing yields zero entries with no visible reason — exactly
        # the silence this tool exists to remove.
        for detail in inner:
            problems.append(f"{key}: {detail}")
        if not found.entries():
            problems.append(
                f"{key}: {os.path.basename(index_path)} lists no model entries"
            )

        source_name = os.path.basename(index_path)
        for entry in found.entries():
            catalogue.add(entry)
            sources[entry.model_id] = source_name

    return catalogue, sources, problems


def trace_objects(
    objects: list[ObjectInstance],
    catalogue: ModelCatalog,
    sources: dict[str, str],
    game_root: str,
    *,
    parse_files: bool = True,
) -> DiagnosticsReport:
    """Trace every object through the non-Blender part of the chain.

    ``parse_files`` can be turned off for a fast structural check that
    skips actually reading the ``.gam`` files.

    Parsed models are cached per id, matching what the real import
    does — otherwise a model used 400 times would be read 400 times and
    the timing would be meaningless.
    """
    report = DiagnosticsReport()
    parse_cache: dict[str, tuple[int, int, int] | str] = {}

    for instance in _walk(objects):
        record = ResolutionRecord(
            node_name=instance.name,
            asset_id=instance.asset_id,
            node_class=instance.node_class,
        )
        report.records.append(record)

        if instance.node_class not in GEOMETRY_CLASSES:
            record.not_applicable(f"{instance.node_class} carries no model")
            continue

        if not instance.asset_id:
            record.fail(Stage.HAS_ID, "node has no id attribute")
            continue
        record.advance(Stage.HAS_ID)

        entry = catalogue.get(instance.asset_id)
        if entry is None:
            record.fail(
                Stage.CATALOGUE_ENTRY,
                "id is not listed in any loaded catalogue",
            )
            continue
        record.catalogue_source = sources.get(instance.asset_id)
        record.advance(Stage.CATALOGUE_ENTRY)

        if not entry.file_path:
            record.fail(Stage.FILE_PATH, "catalogue entry has no file path")
            continue
        record.game_path = entry.file_path
        record.advance(Stage.FILE_PATH)

        resolved = resolve_model_file(entry, game_root)
        if resolved is None:
            record.fail(
                Stage.FILE_FOUND,
                f"no file at {entry.file_path} under the game folder",
            )
            continue
        record.resolved_path = resolved
        record.advance(Stage.FILE_FOUND)

        if not parse_files:
            continue

        cached = parse_cache.get(instance.asset_id)
        if cached is None:
            try:
                model = read_model(resolved)
                cached = (
                    len(model.meshes), model.total_vertices(), model.total_triangles(),
                )
            except EXMeditorError as exc:
                cached = exc.message
            parse_cache[instance.asset_id] = cached

        if isinstance(cached, str):
            record.fail(Stage.PARSED, cached)
            continue
        record.advance(Stage.PARSED)

        record.mesh_count, record.vertex_count, record.triangle_count = cached
        if record.triangle_count == 0:
            record.fail(Stage.HAS_GEOMETRY, "parsed but contains no triangles")
            continue
        record.advance(Stage.HAS_GEOMETRY)

    return report


def _walk(objects: list[ObjectInstance]):
    for instance in objects:
        yield instance
        yield from _walk(instance.children)
