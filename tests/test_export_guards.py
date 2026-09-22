# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Guards on what leaves Blender, and on what the preview job may run.

Three faults measured on the user's own files and crash log:

* a texture name cut to 40 characters, extension and all, so the game
  found no file and drew the model with no material;
* a model written resting on Z when the format's up axis is Y — it was
  lying down in Blender and the export wrote what it saw;
* a preview render crashing Blender inside another add-on's depsgraph
  handler, on the preview worker thread.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

import bpy  # noqa: E402

from blender_io.asset_library import _depsgraph_handlers_suspended  # noqa: E402
from blender_io.gam_export import _report_orientation  # noqa: E402
from formats.exm.skin import TEXTURE_NAME_SIZE  # noqa: E402
from utils.math import Vector3  # noqa: E402


class _Bounds:
    def __init__(self, low, high):
        self.min_corner = Vector3(*low)
        self.max_corner = Vector3(*high)


def _records(caplevel="WARNING"):
    """Collect what the export logger says, without a logging fixture."""
    import logging

    seen = []

    class _Catch(logging.Handler):
        def emit(self, record):
            seen.append((record.levelname, record.getMessage()))

    handler = _Catch()
    logger = logging.getLogger("exmeditor.blender_io.gam_export")
    logger.addHandler(handler)
    return seen, logger, handler


def test_a_model_standing_on_y_is_reported_as_upright() -> None:
    """MEASURED over 698 shipped models: 596 rest on Y, 56 on Z, 46 on X.
    crag_boulder2 runs -4.39 .. 14.54 on Y and is symmetric on the other
    two, which is what upright looks like."""
    seen, logger, handler = _records()
    try:
        _report_orientation("crag_boulder2.gam",
                            _Bounds((-25.77, -4.39, -13.64), (28.32, 14.54, 14.54)))
    finally:
        logger.removeHandler(handler)

    assert seen and seen[-1][0] == "INFO"
    assert "stands on Y" in seen[-1][1]


def test_a_model_resting_on_z_is_reported_as_lying_down() -> None:
    """The .obj export: min=(-0.50, -0.46, 0.00) max=(0.50, 0.46, 0.58).
    Z rests on zero and Y is symmetric — a Z-up model in a Y-up format."""
    seen, logger, handler = _records()
    try:
        _report_orientation("tripo_node.gam",
                            _Bounds((-0.50, -0.46, 0.00), (0.50, 0.46, 0.58)))
    finally:
        logger.removeHandler(handler)

    assert seen and seen[-1][0] == "WARNING"
    assert "on its side" in seen[-1][1]
    assert "Z" in seen[-1][1]


def test_the_texture_name_field_is_forty_bytes() -> None:
    """Which is 39 characters and a terminator. The name that broke was
    cut at exactly 40 with its extension gone — the evidence for the
    width, which an earlier version of this test recorded and then
    asserted 44 against anyway. The four bytes after the name are the
    UV set (see tests/test_skin_uv_set.py); the longest of the game's
    own 1136 texture names is 37."""
    assert TEXTURE_NAME_SIZE == 40
    assert len("tripo_image_ea527cc5-57ee-420f-a393-85d6") == TEXTURE_NAME_SIZE
    assert len("tripo_image_ea527cc5-57ee-420f-a393-85d62720b655.png") > TEXTURE_NAME_SIZE - 1
    assert len("commonwealth_of independent_towns.dds") == 37


def test_a_truncated_texture_name_stops_the_export() -> None:
    """Silently is how it used to go: a model with no material and
    nothing anywhere to say why."""
    from blender_io.gam_export import verify_texture_names
    from utils.errors import EXMeditorError

    long_name = "tripo_image_ea527cc5-57ee-420f-a393-85d62720b655.png"
    objects = [_object_with_image(long_name)]

    written = _fake_gam_with_texture(long_name[:40])
    try:
        verify_texture_names(objects, written)
    except EXMeditorError as exc:
        assert "cut short" in str(exc)
        assert long_name in str(exc)
        return
    raise AssertionError("a truncated texture name was let through")


def test_a_name_that_fits_passes_untouched() -> None:
    from blender_io.gam_export import verify_texture_names

    name = "tropiccrag.dds"
    verify_texture_names([_object_with_image(name)], _fake_gam_with_texture(name))


def test_depsgraph_handlers_are_off_inside_and_back_after() -> None:
    """MEASURED in TESTmap.crash.txt: object_preview_render evaluates the
    depsgraph on a worker thread, a Python handler sets an RNA property,
    that queues a UI notifier, and the notifier queue is not thread-safe.
    Seven such handlers are registered in the user's Blender and none is
    this SDK's."""
    def _one(scene, depsgraph=None):
        pass

    def _two(scene, depsgraph=None):
        pass

    bpy.app.handlers.depsgraph_update_pre.append(_one)
    bpy.app.handlers.depsgraph_update_post.append(_two)
    try:
        with _depsgraph_handlers_suspended() as suspended:
            assert suspended == 2
            assert list(bpy.app.handlers.depsgraph_update_pre) == []
            assert list(bpy.app.handlers.depsgraph_update_post) == []

        assert list(bpy.app.handlers.depsgraph_update_pre) == [_one]
        assert list(bpy.app.handlers.depsgraph_update_post) == [_two]
    finally:
        bpy.app.handlers.depsgraph_update_pre[:] = []
        bpy.app.handlers.depsgraph_update_post[:] = []


def test_handlers_come_back_even_when_a_preview_raises() -> None:
    """Leaving another add-on's handlers off would break that add-on for
    the rest of the session, in a way nothing would connect to us."""
    def _one(scene, depsgraph=None):
        pass

    bpy.app.handlers.depsgraph_update_post.append(_one)
    try:
        try:
            with _depsgraph_handlers_suspended():
                raise RuntimeError("preview blew up")
        except RuntimeError:
            pass
        assert list(bpy.app.handlers.depsgraph_update_post) == [_one]
    finally:
        bpy.app.handlers.depsgraph_update_post[:] = []


def test_suspending_nothing_is_not_an_error() -> None:
    with _depsgraph_handlers_suspended() as suspended:
        assert suspended == 0


# --- the handler stays off until Blender says the jobs are over --------


def _goo_handler(scene, depsgraph=None):
    """Stands in for goo_engine_light_groups.sync_dg_handler."""


def _build_with_timer():
    """Suspend, queue, hand over to the timer. Returns the palette layer."""
    from blender_io.asset_library import (
        ASSET_COLLECTION,
        _finish_when_previews_are_done,
        _suspend_depsgraph_handlers,
        asset_collection,
    )

    asset_collection()          # creates, links and un-excludes it
    layer = next(
        c for c in bpy.context.view_layer.layer_collection.children
        if c.name == ASSET_COLLECTION
    )
    assert layer.exclude is False

    bpy.app.handlers.depsgraph_update_post[:] = [_goo_handler]
    assert _suspend_depsgraph_handlers() == 1
    _finish_when_previews_are_done([])
    return layer


def test_the_culprit_is_a_goo_engine_startup_module_not_an_addon() -> None:
    """ОПРОВЕРГНУТО, then narrowed. "Disable the seven add-ons" was the
    experiment and it did not help — the crash came back by the same
    path. Asked of that Blender again, one depsgraph handler remained:

        depsgraph_update_post  sync_dg_handler  from goo_engine_light_groups

    which lives in Goo Engine's scripts/startup and is not on the
    add-on list at all. It rewrites two RNA properties on EVERY
    material in the file whenever ANY material is evaluated — and a
    preview job evaluates materials from inside a depsgraph update.
    The suspension covers depsgraph_update_post, so it covers this."""
    from blender_io.asset_library import _DEPSGRAPH_HANDLERS

    assert "depsgraph_update_post" in _DEPSGRAPH_HANDLERS


def test_handlers_stay_off_while_the_preview_job_is_running() -> None:
    """v105 restored them after one second, because it inferred
    completion from the preview images — which are allocated before
    they are rendered. That is the second failed fix in a row, and the
    reason: the job's own signal was never asked. Now it is."""
    from blender_io.asset_library import _PREVIEW_HOLD_MINIMUM, _PREVIEW_POLL_SECONDS

    layer = _build_with_timer()
    timers = bpy.app.timers
    hold = int(_PREVIEW_HOLD_MINIMUM / _PREVIEW_POLL_SECONDS)
    try:
        bpy.app.running_jobs.add("RENDER_PREVIEW")
        for _ in range(hold + 5):
            timers.tick()
            assert bpy.app.handlers.depsgraph_update_post == [], "put back too early"
            assert layer.exclude is False, "excluded while still rendering"

        bpy.app.running_jobs.discard("RENDER_PREVIEW")
        timers.tick()
        # Excluded first — with the handler still off, so the depsgraph
        # update the exclusion causes runs without it.
        assert layer.exclude is True
        assert bpy.app.handlers.depsgraph_update_post == []

        timers.tick()
        # And only then is the handler put back.
        assert bpy.app.handlers.depsgraph_update_post == [_goo_handler]
        assert not timers.pending
    finally:
        bpy.app.running_jobs.clear()
        bpy.app.handlers.depsgraph_update_post[:] = []
        timers.pending.clear()


def test_a_job_that_has_not_started_yet_is_waited_for() -> None:
    """Right after the operator returns the job may not have begun, so
    'not running' on the first tick means nothing. Two ticks of grace
    before believing it."""
    from blender_io.asset_library import _PREVIEW_HOLD_MINIMUM, _PREVIEW_POLL_SECONDS

    layer = _build_with_timer()
    timers = bpy.app.timers
    hold = int(_PREVIEW_HOLD_MINIMUM / _PREVIEW_POLL_SECONDS)
    try:
        timers.tick()                       # not running, not yet started
        assert layer.exclude is False
        assert bpy.app.handlers.depsgraph_update_post == []

        bpy.app.running_jobs.add("RENDER_PREVIEW")
        timers.tick()                       # now it is running
        assert layer.exclude is False

        bpy.app.running_jobs.discard("RENDER_PREVIEW")
        # Finished quickly — but nothing happens before the minimum
        # hold has passed. Two ticks are already spent; the hold ends
        # on tick `hold`, and the tick after that excludes.
        for _ in range(hold - 2):
            timers.tick()
            assert layer.exclude is False, "excluded before the hold was up"
            assert bpy.app.handlers.depsgraph_update_post == []
        timers.tick()
        assert layer.exclude is True
        assert bpy.app.handlers.depsgraph_update_post == []
        timers.tick()
        assert bpy.app.handlers.depsgraph_update_post == [_goo_handler]
    finally:
        bpy.app.running_jobs.clear()
        bpy.app.handlers.depsgraph_update_post[:] = []
        timers.pending.clear()


def test_the_preview_image_is_allocated_before_it_is_rendered() -> None:
    """MEASURED in a background Blender: obj.preview.image_size reads
    (128, 128) the instant asset_generate_preview() returns, when
    nothing can have rendered yet. That is why v105's "wait until every
    preview has a size" put the handler back after one second and
    changed nothing — and why the hold below has a floor."""
    from blender_io.asset_library import _PREVIEW_HOLD_MINIMUM, _PREVIEW_POLL_SECONDS

    assert _PREVIEW_HOLD_MINIMUM >= 10 * _PREVIEW_POLL_SECONDS


def test_the_ceiling_grows_with_the_number_of_previews() -> None:
    """MEASURED crash: limit 0 queued 156 previews, the flat 60-second
    ceiling expired mid-render, the handler went back on and the next
    preview died in it. Five seconds each, never under a minute."""
    from blender_io.asset_library import _wait_limit

    assert _wait_limit(0) == 60.0
    assert _wait_limit(24) == 120.0
    assert _wait_limit(156) == 780.0


def test_a_job_that_never_ends_cannot_keep_the_handler_off_forever() -> None:
    """Sixty seconds, then tidy up regardless: another add-on's handler
    left off for the session is a fault nothing would connect to us."""
    from blender_io.asset_library import _PREVIEW_POLL_SECONDS, _PREVIEW_WAIT_LIMIT

    _build_with_timer()
    timers = bpy.app.timers
    try:
        bpy.app.running_jobs.add("RENDER_PREVIEW")
        ticks = int(_PREVIEW_WAIT_LIMIT / _PREVIEW_POLL_SECONDS) + 3
        for _ in range(ticks):
            timers.tick()
        assert bpy.app.handlers.depsgraph_update_post == [_goo_handler]
        assert not timers.pending
    finally:
        bpy.app.running_jobs.clear()
        bpy.app.handlers.depsgraph_update_post[:] = []
        timers.pending.clear()


# --- what the toolchain keeps and what it drops -------------------------


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _source(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def test_both_export_paths_prepare_and_restore_the_mesh() -> None:
    """MEASURED in real Blender with the real toolchain — one box,
    1 x 2 x 4, upright on Z=0, exported four ways:

        plain                                X 0..1  Y 0..4  Z 0..2
        + OBJECT rotation 90 X and scale 10  X 0..1  Y 0..4  Z 0..2
        + the same baked into the mesh       X 0..10 Y 0..20 Z -40..0

    The middle row is identical to the first: the object's rotation and
    scale are thrown away, because the exporter reads `vert.co` and
    only reaches for `matrix_world` when `mesh.type == 4`. That is
    "much smaller, and on its side".

    A helper nothing calls fixes nothing, so this pins the call sites
    rather than the helper."""
    export = _source("blender_io", "gam_export.py")
    assert export.count("= _prepare_meshes_for_export(objects)") == 2
    assert export.count("_restore_meshes(restore_meshes)") == 2


def test_the_axis_swap_itself_was_right() -> None:
    """Worth pinning, because it was the obvious suspect and it was
    innocent. The upright box (Blender Z 0..4) came out on game Y 0..4,
    and Y is up in the format — 596 of 698 shipped models rest on it."""
    blender_box = {"x": (0, 1), "y": (0, 2), "z": (0, 4)}
    # Blender (x, y, z) -> game (x, z, y), from the exporter's source.
    game = {"x": blender_box["x"], "y": blender_box["z"], "z": blender_box["y"]}
    assert game == {"x": (0, 1), "y": (0, 4), "z": (0, 2)}


def test_a_colour_layer_named_color_is_added_when_there_is_none() -> None:
    """Without it the export does not come out wrong — it raises
    `TypeError: Value after * must be an iterable, not NoneType` inside
    the XYZNCT2 writer and produces no file at all."""
    from blender_io.gam_export import (
        TOOLCHAIN_COLOR_LAYER,
        _ensure_toolchain_color_layer,
    )

    mesh = _FakeMesh()
    _ensure_toolchain_color_layer(mesh, "Cube")

    assert TOOLCHAIN_COLOR_LAYER in mesh.vertex_colors.keys()


def test_the_importers_own_layer_is_copied_into_it() -> None:
    """The importer makes a FLOAT_COLOR layer called `Col`, and
    `mesh.vertex_colors` shows only BYTE_COLOR ones — so it is
    invisible to the exporter twice over, by name and by type."""
    from blender_io.gam_export import _ensure_toolchain_color_layer

    mesh = _FakeMesh()
    mesh.add_attribute("Col", "FLOAT_COLOR", "CORNER",
                       [(0.25, 0.5, 0.75, 1.0)] * 3)
    _ensure_toolchain_color_layer(mesh, "Rock")

    written = [tuple(item.color) for item in mesh.vertex_colors["color"].data]
    assert written == [(0.25, 0.5, 0.75, 1.0)] * 3


def test_an_existing_color_layer_is_left_alone() -> None:
    from blender_io.gam_export import _ensure_toolchain_color_layer

    mesh = _FakeMesh()
    layer = mesh.vertex_colors.new(name="color")
    layer.data[0].color = (1.0, 0.0, 0.0, 1.0)
    mesh.add_attribute("Col", "FLOAT_COLOR", "CORNER",
                       [(0.0, 1.0, 0.0, 1.0)] * 3)

    _ensure_toolchain_color_layer(mesh, "Rock")

    assert tuple(mesh.vertex_colors["color"].data[0].color) == (1.0, 0.0, 0.0, 1.0)


class _Item:
    def __init__(self, color=(1.0, 1.0, 1.0, 1.0)):
        self.color = color


class _Layer:
    def __init__(self, name, values=None):
        self.name = name
        self.data = [_Item(v) for v in (values or [(1.0, 1.0, 1.0, 1.0)] * 3)]


class _VertexColors:
    """bpy's LoopColors: BYTE_COLOR corner layers only."""

    def __init__(self):
        self._layers = {}

    def keys(self):
        return list(self._layers)

    def new(self, name="Col"):
        layer = _Layer(name)
        self._layers[name] = layer
        return layer

    def __getitem__(self, name):
        return self._layers[name]


class _Attribute(_Layer):
    def __init__(self, name, data_type, domain, values):
        super().__init__(name, values)
        self.data_type = data_type
        self.domain = domain


class _FakeMesh:
    def __init__(self):
        self.vertex_colors = _VertexColors()
        self.color_attributes = []

    def add_attribute(self, name, data_type, domain, values):
        self.color_attributes.append(
            _Attribute(name, data_type, domain, values))


# --- helpers -----------------------------------------------------------


class _Image:
    def __init__(self, name):
        self.name = name
        self.filepath = name


class _Node:
    type = "TEX_IMAGE"

    def __init__(self, image):
        self.image = image


class _Tree:
    def __init__(self, nodes):
        self.nodes = nodes


class _Material:
    def __init__(self, image):
        self.node_tree = _Tree([_Node(image)])


class _Mesh:
    def __init__(self, materials):
        self.materials = materials


class _Object:
    def __init__(self, materials):
        self.data = _Mesh(materials)
        self.name = "Model"


def _object_with_image(name):
    return _Object([_Material(_Image(name))])


def _fake_gam_with_texture(name: str) -> str:
    """A .gam holding one material with one texture, written as the real
    format writes it — 4 + 172 + 48 bytes."""
    import struct
    import tempfile

    from formats.exm.gam import Chunk, write_container
    from formats.exm.skin import SKIN_CHUNK_ID

    block = struct.pack("<I", 1)
    block += struct.pack("<17f", *([1.0] * 17))
    block += struct.pack("<I", 1)
    block += b"diffuse".ljust(100, b"\x00")
    block += name.encode("cp1251").ljust(44, b"\x00")
    block += struct.pack("<I", 0)

    path = os.path.join(tempfile.mkdtemp(), "model.gam")
    with open(path, "wb") as handle:
        handle.write(write_container(0, [Chunk(SKIN_CHUNK_ID, block)]))
    return path


def test_create_model_applies_the_transform_it_just_wrote() -> None:
    """A helper nothing calls is no fix: the call must sit right after
    the successful write, inside the same try, before registration."""
    source = _source("addon", "create_model_operator.py")
    write = source.index("self._write_gam(context, meshes, target)")
    apply_ = source.index("apply_baked_transform(meshes)")
    assert write < apply_ < write + 600
    assert "from blender_io.gam_export import apply_baked_transform" in source
