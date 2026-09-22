# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Python depsgraph handlers must never run on the preview render thread.

Five crash logs, one shape: a preview job's worker thread evaluates the
depsgraph, a Python handler runs there, sets an RNA property, and the
notifier queue is corrupted. Suspending the handler around the SDK's
own build was not enough because the Asset Browser starts preview
jobs of its own — the fifth crash came after a drag from the browser,
long after the build. So the guard is permanent.
"""

from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fake_bpy  # noqa: E402

fake_bpy.install()

import bpy  # noqa: E402

from blender_io import handler_guard  # noqa: E402


def _reset():
    bpy.app.handlers.depsgraph_update_pre[:] = []
    bpy.app.handlers.depsgraph_update_post[:] = []
    bpy.app.running_jobs.clear()
    handler_guard.skipped.update({"off_main_thread": 0, "during_preview": 0})


def _goo_like():
    """A stand-in for goo_engine_light_groups.sync_dg_handler."""
    calls = []

    def sync_dg_handler(scene, depsgraph=None):
        calls.append(threading.current_thread().name)

    sync_dg_handler.__module__ = "goo_engine_light_groups"
    sync_dg_handler._bpy_persistent = True
    return sync_dg_handler, calls


def test_a_handler_is_skipped_on_a_worker_thread() -> None:
    _reset()
    handler, calls = _goo_like()
    bpy.app.handlers.depsgraph_update_post.append(handler)
    assert handler_guard.install() == 1
    guarded = bpy.app.handlers.depsgraph_update_post[0]
    assert guarded is not handler

    guarded(None)                       # main thread: runs
    worker = threading.Thread(target=guarded, args=(None,), name="preview-job")
    worker.start()
    worker.join()

    assert calls == ["MainThread"]
    assert handler_guard.skipped["off_main_thread"] == 1


def test_the_storm_handler_is_also_skipped_during_a_preview_job() -> None:
    """Goo's handler rewrites every material in the file on any
    material update; a preview job updates materials constantly. On
    the main thread too, while a preview job runs, it stays quiet."""
    _reset()
    handler, calls = _goo_like()
    bpy.app.handlers.depsgraph_update_post.append(handler)
    handler_guard.install()
    guarded = bpy.app.handlers.depsgraph_update_post[0]

    bpy.app.running_jobs.add("RENDER_PREVIEW")
    guarded(None)
    bpy.app.running_jobs.discard("RENDER_PREVIEW")
    guarded(None)

    assert calls == ["MainThread"]
    assert handler_guard.skipped["during_preview"] == 1


def test_an_ordinary_handler_only_gets_the_thread_guard() -> None:
    """Another add-on's handler is not the storm; on the main thread it
    runs whether or not a preview is rendering."""
    _reset()
    calls = []

    def other(scene, depsgraph=None):
        calls.append(1)

    other.__module__ = "some_other_addon"
    bpy.app.handlers.depsgraph_update_pre.append(other)
    handler_guard.install()
    guarded = bpy.app.handlers.depsgraph_update_pre[0]

    bpy.app.running_jobs.add("RENDER_PREVIEW")
    guarded(None)
    assert calls == [1]


def test_install_is_idempotent_and_keeps_persistence() -> None:
    _reset()
    handler, _calls = _goo_like()
    bpy.app.handlers.depsgraph_update_post.append(handler)
    assert handler_guard.install() == 1
    assert handler_guard.install() == 0
    guarded = bpy.app.handlers.depsgraph_update_post[0]
    assert guarded._bpy_persistent is True
    assert guarded.__name__ == "sync_dg_handler"
    assert guarded.__module__ == "goo_engine_light_groups"


def test_uninstall_puts_the_originals_back() -> None:
    _reset()
    handler, _calls = _goo_like()
    bpy.app.handlers.depsgraph_update_post.append(handler)
    handler_guard.install()
    assert handler_guard.uninstall() == 1
    assert bpy.app.handlers.depsgraph_update_post == [handler]


def test_the_guard_is_installed_at_register_and_before_every_build() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "__init__.py"), encoding="utf-8") as fh:
        init = fh.read()
    assert "handler_guard.install()" in init
    assert "handler_guard.uninstall()" in init
    with open(os.path.join(root, "blender_io", "asset_library.py"), encoding="utf-8") as fh:
        library = fh.read()
    assert "handler_guard.install()" in library


def test_suspension_and_guard_compose() -> None:
    """The build still takes handlers off and puts them back; what it
    puts back is the guarded wrapper, by identity."""
    from blender_io.asset_library import (
        _resume_depsgraph_handlers,
        _suspend_depsgraph_handlers,
    )

    _reset()
    handler, _calls = _goo_like()
    bpy.app.handlers.depsgraph_update_post.append(handler)
    handler_guard.install()
    guarded = bpy.app.handlers.depsgraph_update_post[0]

    assert _suspend_depsgraph_handlers() == 1
    assert bpy.app.handlers.depsgraph_update_post == []
    _resume_depsgraph_handlers()
    assert bpy.app.handlers.depsgraph_update_post == [guarded]


def test_previews_are_rendered_synchronously_when_blender_is_there() -> None:
    """The icon-preview JOB is in every one of seven crash logs, or its
    aftermath. _render_preview_now renders through render.render in a
    scene of its own and returns before the operator does; the job is
    the fallback only, for an object it cannot frame."""
    from blender_io import asset_library

    source = open(asset_library.__file__, encoding="utf-8").read()
    build = source.index("def build_asset_previews(")
    loop = source.index("if _render_preview_now(obj):", build)
    fallback = source.index("elif _generate_preview(obj):", loop)
    assert loop < fallback, "the job must be the fallback, not the first choice"
    # With nothing queued, the tidy-up is immediate — no timer, no wait.
    assert "_resume_depsgraph_handlers()" in source[fallback:fallback + 1200]
    assert "_set_excluded(ASSET_COLLECTION, True)" in source[fallback:fallback + 1200]


def test_the_synchronous_render_declines_outside_blender() -> None:
    """The test double has no mathutils and no renderer; the function
    must say no rather than raise, so the fallback runs."""
    from blender_io.asset_library import _render_preview_now

    class _Obj:
        name = "x"
        data = object()
        bound_box = [(0, 0, 0)] * 8

    assert _render_preview_now(_Obj()) is False


def test_render_init_is_suspended_during_the_build_too() -> None:
    """Goo's sync_handler sits on render_init and does the full
    light-group sync per render: MEASURED 25 syncs, 14 750 material
    writes, for 24 synchronous thumbnails."""
    from blender_io.asset_library import _DEPSGRAPH_HANDLERS

    assert "render_init" in _DEPSGRAPH_HANDLERS
