# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Keep Python depsgraph handlers off the preview render thread.

Five crash logs, one shape::

    do_job_thread                       <- a preview render WORKER THREAD
      icon_preview_startjob_all_sizes
        object_preview_render
          scene_graph_update_tagged
            BKE_callback_exec_id_depsgraph
              bpy_app_generic_callback  <- a Python depsgraph handler
                pyrna_struct_setattro   <- it sets an RNA property
                  WM_event_add_notifier_ex
                    note_cmp_for_queue_fn   <- EXCEPTION_ACCESS_VIOLATION

Blender renders an asset preview on a worker thread and evaluates the
depsgraph there, and that fires every registered Python depsgraph
handler ON THAT THREAD. A handler that sets an RNA property queues a
UI notifier from a thread that must not touch the notifier queue.

The handler in question is Goo Engine's own
``goo_engine_light_groups.sync_dg_handler`` — not an add-on, it ships
in ``scripts/startup`` — which rewrites two properties on every
material in the file whenever any material is evaluated.

Suspending it around the SDK's own preview build (``asset_library``)
was not enough, and the reason is that **the Asset Browser starts
preview jobs of its own**, whenever it draws an asset whose thumbnail
it wants — after the build, after the SDK has put the handler back,
on a drag, on a redraw. The fifth crash came on exactly such a job:
``object.add_named`` from the browser, then the worker-thread path.

So the guard is permanent, not a window: each depsgraph handler is
wrapped so that it is **skipped on any thread but the main one**, and
Goo's is additionally skipped while a preview job is running at all.
A depsgraph update caused by a thumbnail render is not the user
editing anything, which is the only thing those handlers exist for.

Installed when the add-on registers and again before every preview
build (a handler registered in between is caught then); removed, with
the originals put back, when the add-on unregisters.
"""

from __future__ import annotations

import functools
import threading

import bpy

from utils.logging import get_logger

logger = get_logger("blender_io.handler_guard")

#: The handler lists a preview render can reach.
HANDLER_LISTS = ("depsgraph_update_pre", "depsgraph_update_post")

#: Handlers that are skipped during a preview job even on the main
#: thread: the ones measured to rewrite the whole file's materials.
STORM_MODULES = ("goo_engine_light_groups",)

#: Marker on a wrapper, holding the function it wraps.
WRAPPED_ATTR = "_exm_wraps"

#: What was skipped, for the diagnostics harness and the log.
skipped = {"off_main_thread": 0, "during_preview": 0}


def _preview_job_running() -> bool:
    app = getattr(bpy, "app", None)
    ask = getattr(app, "is_job_running", None)
    if ask is None:
        return False
    try:
        return bool(ask("RENDER_PREVIEW"))
    except (RuntimeError, TypeError, ValueError):
        return False


def _guard(func):
    """Wrap one handler. Idempotent: a wrapper is returned as it is."""
    if getattr(func, WRAPPED_ATTR, None) is not None:
        return func
    storm = getattr(func, "__module__", "") in STORM_MODULES

    @functools.wraps(func)
    def guarded(*args, **kwargs):
        if threading.current_thread() is not threading.main_thread():
            skipped["off_main_thread"] += 1
            return None
        if storm and _preview_job_running():
            skipped["during_preview"] += 1
            return None
        return func(*args, **kwargs)

    setattr(guarded, WRAPPED_ATTR, func)
    # Blender drops handlers on file load unless they carry this; keep
    # whatever the original declared.
    if hasattr(func, "_bpy_persistent"):
        guarded._bpy_persistent = func._bpy_persistent
    return guarded


def install() -> int:
    """Wrap every depsgraph handler not yet wrapped. Returns how many."""
    handlers = getattr(getattr(bpy, "app", None), "handlers", None)
    if handlers is None:
        return 0
    wrapped = 0
    names = []
    for list_name in HANDLER_LISTS:
        entries = getattr(handlers, list_name, None)
        if entries is None:
            continue
        try:
            for index, func in enumerate(list(entries)):
                guarded = _guard(func)
                if guarded is not func:
                    entries[index] = guarded
                    wrapped += 1
                    names.append(f"{getattr(func, '__module__', '?')}.{getattr(func, '__name__', '?')}")
        except (AttributeError, TypeError) as exc:
            logger.debug("could not guard %s: %s", list_name, exc)
    if wrapped:
        logger.info(
            "%d depsgraph handler(s) guarded against the preview render "
            "thread: %s", wrapped, ", ".join(names),
        )
    return wrapped


def uninstall() -> int:
    """Put the original handlers back. Returns how many."""
    handlers = getattr(getattr(bpy, "app", None), "handlers", None)
    if handlers is None:
        return 0
    restored = 0
    for list_name in HANDLER_LISTS:
        entries = getattr(handlers, list_name, None)
        if entries is None:
            continue
        try:
            for index, func in enumerate(list(entries)):
                original = getattr(func, WRAPPED_ATTR, None)
                if original is not None:
                    entries[index] = original
                    restored += 1
        except (AttributeError, TypeError) as exc:
            logger.debug("could not unguard %s: %s", list_name, exc)
    return restored
