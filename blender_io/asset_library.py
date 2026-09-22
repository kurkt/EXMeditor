# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Put the game's models into Blender's own Asset Browser.

A list of names is not a way to choose a model. The browser had one,
and picking anything from it meant knowing what ``heavy_dot3`` looks
like before you looked.

Blender already has the right window for this — the Asset Browser
draws a grid of thumbnails and drags what you pick into the scene — and
it needs three things from us per model: an object in the file, that
object marked as an asset, and a preview rendered for it. This module
does those three, and nothing about the UI: the browser is Blender's.

**"Current File", not a saved library.** An asset library on disk needs
a saved ``.blend`` and a folder registered in Blender's preferences,
and it goes stale the moment the game folder changes. Assets in the
open file need neither, appear the moment they are marked, and are
rebuilt in seconds. The user sets the Asset Browser's library to
"Current File" and the models are there.

Each object keeps its ``exm_asset_id``, so one dragged into the scene
is already the model the map format names — *Assign ExMachina Node*
turns it into a placed object without asking which model it is.
"""

from __future__ import annotations

import contextlib
import dataclasses

import bpy

import os

from core.asset_catalogs import catalog_path, catalog_uuid, merge
from core.asset_previews import preview_tags
from utils.logging import get_logger

logger = get_logger("blender_io.asset_library")

#: Collection the preview objects live in. Kept out of the view layer:
#: they are a palette, not part of the map, and a thousand models
#: stacked at the origin would bury the scene.
ASSET_COLLECTION = "ExM_Assets"

#: Blender's own name for the catalogue file. It belongs in the asset
#: library's folder, which for a Current File library is the folder the
#: ``.blend`` is saved in.
CATALOG_FILE = "blender_assets.cats.txt"

#: What the tree is rooted at, for messages.
ROOT_LABEL = "Ex Machina"


@dataclasses.dataclass
class AssetPreviewResult:
    """What a build produced."""

    built: int = 0
    reused: int = 0
    failed: list = dataclasses.field(default_factory=list)
    previews: int = 0
    #: How many of those went through Blender's icon-preview JOB instead
    #: of the synchronous render — ideally none; see _render_preview_now.
    previews_by_job: int = 0
    #: How many other add-ons' depsgraph handlers had to be taken off
    #: while the previews rendered. See _depsgraph_handlers_suspended.
    suspended_handlers: int = 0
    #: Where the catalogue file was written, or None when the .blend is
    #: unsaved and Blender therefore has nowhere to keep one.
    catalog_file: str | None = None

    @property
    def total(self) -> int:
        return self.built + self.reused


def asset_collection(scene=None):
    """The palette collection, linked into the scene and VISIBLE.

    Visible on purpose while it is being filled. Excluding a layer
    collection takes its objects out of the depsgraph entirely, and a
    preview is an offscreen render OF THE EVALUATED OBJECT — so an
    excluded object has nothing to render. The palette is put out of
    the way by :func:`hide_palette` once every preview is done, not
    before.
    """
    collection = bpy.data.collections.get(ASSET_COLLECTION)
    if collection is None:
        collection = bpy.data.collections.new(ASSET_COLLECTION)

    scene = scene if scene is not None else getattr(bpy.context, "scene", None)
    root = getattr(scene, "collection", None)
    if root is not None:
        try:
            if ASSET_COLLECTION not in [c.name for c in root.children]:
                root.children.link(collection)
        except (AttributeError, RuntimeError, TypeError) as exc:
            logger.debug("could not link the asset collection: %s", exc)

    # Both, and never excluded again: a previous build hid this
    # collection by excluding it, and a file saved in that state would
    # start the next session with the palette outside the depsgraph.
    _set_excluded(ASSET_COLLECTION, False)
    _set_hidden(ASSET_COLLECTION, False)
    return collection


def hide_palette() -> None:
    """Put the palette out of sight — WITHOUT taking it out of the
    depsgraph.

    A thousand models stacked at the origin is not a scene, so it has
    to go somewhere. It used to be ``exclude``, and that is the eye
    that also removes the objects from evaluation.

    That was fatal, because **a preview is asynchronous**.
    ``asset_generate_preview()`` does not render — it queues a job and
    returns, and the job outlives the operator. Excluding the
    collection the moment the loop finished pulled the objects out from
    under jobs that were still to run.

    MEASURED, second crash in ``TESTmap.crash.txt``, on the MAIN
    thread this time — after the first crash's worker-thread path had
    been closed off::

        note_cmp_for_queue_fn
        BLI_gset_ensure_p_ex
        WM_event_add_notifier_ex
        wm_jobs_timer            <- the job timer, long after the
        wm_window_timer             operator returned
        wm_window_process_events
        WM_main

    ``wm_jobs_timer`` running at all is the proof that the jobs were
    still going. So the palette is hidden with the *viewport* eye,
    which hides it and leaves it evaluated.
    """
    _set_hidden(ASSET_COLLECTION, True)


def _layer_collection(name: str):
    view_layer = getattr(bpy.context, "view_layer", None)
    layer_root = getattr(view_layer, "layer_collection", None)
    for child in getattr(layer_root, "children", []) or []:
        if getattr(child, "name", "") == name:
            return child
    return None


def _set_hidden(name: str, hidden: bool) -> None:
    """Hide or show, keeping the objects in the depsgraph either way."""
    child = _layer_collection(name)
    if child is None:
        return
    try:
        child.hide_viewport = hidden
    except (AttributeError, TypeError) as exc:
        logger.debug("could not hide %s: %s", name, exc)


def _set_excluded(name: str, excluded: bool) -> None:
    child = _layer_collection(name)
    if child is None:
        return
    try:
        child.exclude = excluded
    except (AttributeError, TypeError) as exc:
        logger.debug("could not set exclude on %s: %s", name, exc)


def catalog_folder() -> str | None:
    """Where the catalogue file goes, or None for an unsaved file.

    A Current File asset library is the ``.blend`` itself, and Blender
    looks for the catalogue beside it. An unsaved file has no beside,
    so it can carry tags and no tree — that is Blender's arrangement,
    not a decision made here, and the operator says so rather than
    leaving the tree mysteriously empty.
    """
    path = getattr(getattr(bpy, "data", None), "filepath", "") or ""
    folder = os.path.dirname(path)
    return folder if folder and os.path.isdir(folder) else None


def write_catalogs(paths) -> str | None:
    """Add ``paths`` to the catalogue file. Returns where it was written.

    Merged, never rewritten: the file is shared with the user's own
    catalogues and with any other add-on that files assets here.
    """
    folder = catalog_folder()
    if folder is None:
        return None

    path = os.path.join(folder, CATALOG_FILE)
    try:
        existing = ""
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                existing = handle.read()
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(merge(existing, paths))
    except OSError as exc:
        logger.warning("could not write %s: %s", path, exc)
        return None
    return path


def build_asset_previews(
    assets,
    mesh_provider,
    *,
    scene=None,
    generate_previews: bool = True,
    map_name: str = "",
    asset_id_prop: str = "exm_asset_id",
) -> AssetPreviewResult:
    """Make one marked, previewed object per asset. Returns what it did.

    An asset already built is left alone rather than rebuilt: a second
    run after widening the selection should cost only the new ones.
    """
    result = AssetPreviewResult()
    collection = asset_collection(scene)
    existing = {
        obj.get(asset_id_prop): obj
        for obj in collection.objects
        if obj.get(asset_id_prop)
    }

    # Two passes. Everything is created, linked and marked first; only
    # then is anything rendered. A preview is an offscreen render of the
    # EVALUATED object, so the depsgraph has to have caught up with the
    # linking before the first one runs — rendering as we went meant
    # rendering objects the view layer had not seen yet.
    marked = []
    for asset in assets:
        held = existing.get(asset.asset_id)
        if held is not None and getattr(held, "asset_data", None) is not None:
            result.reused += 1
            continue

        mesh = mesh_provider.get_mesh(asset.asset_id)
        if mesh is None:
            result.failed.append(asset.asset_id)
            continue

        obj = held
        if obj is None:
            obj = bpy.data.objects.new(
                asset.display_name or asset.asset_id, mesh
            )
            obj[asset_id_prop] = asset.asset_id
            try:
                collection.objects.link(obj)
            except (AttributeError, RuntimeError, TypeError) as exc:
                logger.debug("could not link %s: %s", asset.asset_id, exc)
                result.failed.append(asset.asset_id)
                continue

        if _mark(obj, asset, map_name):
            result.built += 1
            marked.append(obj)
        else:
            result.failed.append(asset.asset_id)

    # The catalogue file first, then the ids that point into it: an
    # asset filed under a catalogue the file does not name shows up
    # under "Unassigned", which looks exactly like no catalogue at all.
    result.catalog_file = write_catalogs(
        {catalog_path(map_name, a.category, a.subcategory) for a in assets}
    )

    if generate_previews and marked:
        _settle_view_layer()
        # A handler registered since the add-on loaded gets its guard
        # now; the ones already guarded are left as they are.
        from blender_io import handler_guard
        handler_guard.install()
        # Suspended BEFORE the first render and put back only once the
        # jobs have finished — not at the end of this loop. The loop
        # only queues them; the crash arrives later, out of the event
        # loop, which is why suspending across the loop alone was not
        # enough. See _finish_when_previews_are_done.
        result.suspended_handlers = _suspend_depsgraph_handlers()
        queued = []
        with _progress(len(marked)) as advance:
            for obj in marked:
                if _render_preview_now(obj):
                    result.previews += 1
                elif _generate_preview(obj):
                    # The job, only for an object the synchronous render
                    # could not handle; the wait below covers it.
                    result.previews += 1
                    queued.append(obj)
                advance()
        result.previews_by_job = len(queued)
        if queued:
            _finish_when_previews_are_done(queued)
        else:
            # Nothing asynchronous happened: tidy up here and now.
            _resume_depsgraph_handlers()
            _set_excluded(ASSET_COLLECTION, True)

    hide_palette()

    logger.info(
        "asset previews: %s built, %s already there, %s preview(s) rendered, "
        "%s could not be built",
        result.built, result.reused, result.previews, len(result.failed),
    )
    if result.suspended_handlers:
        logger.info(
            "  %d depsgraph handler(s) from other add-ons were off while "
            "rendering and are back on", result.suspended_handlers,
        )
    if result.catalog_file:
        logger.info("  filed under %r in %s", ROOT_LABEL, result.catalog_file)
    else:
        logger.warning(
            "  no catalogue tree: Blender keeps it in a file beside the "
            ".blend, and this one has not been saved. Save the file and "
            "build again for the %r > map > category tree; the tags work "
            "either way.", ROOT_LABEL,
        )
    if result.failed:
        logger.warning(
            "  no model for: %s%s",
            ", ".join(result.failed[:6]),
            f" and {len(result.failed) - 6} more" if len(result.failed) > 6 else "",
        )
    return result


#: The handler lists a preview render can reach. Only the depsgraph
#: ones: rendering a thumbnail evaluates the depsgraph and nothing
#: else, so frame-change and load handlers are none of our business.
#: Plus render_init: the synchronous thumbnail is a render, and Goo's
#: ``sync_handler`` sits on render_init and does the full light-group
#: sync per render — MEASURED as 25 syncs and 14 750 material writes
#: for 24 thumbnails. A thumbnail of a palette object does not need
#: the file's light groups brought up to date first.
_DEPSGRAPH_HANDLERS = ("depsgraph_update_pre", "depsgraph_update_post", "render_init")


#: Handlers taken off, waiting to go back. Module level because the
#: timer that restores them runs long after the operator has returned.
_SUSPENDED: list = []

#: How often to look, and how long to keep them off at the outside.
#:
#: The ceiling exists so a preview that never completes cannot leave
#: another add-on switched off for the rest of the session. It was a
#: flat sixty seconds, and that was a crash: "Build Asset Previews"
#: with limit 0 queued 156 previews, the ceiling expired while they
#: were still rendering, the handler went back on, and the next
#: preview to evaluate a material died in it — worker-thread path,
#: identical to the very first crash log. MEASURED: 24 previews took
#: 8.5 s with the handler off and 23-34 s with it on, so 156 is well
#: past a minute either way. The ceiling now scales with what was
#: queued: five seconds per preview, never under a minute.
_PREVIEW_POLL_SECONDS = 1.0
_PREVIEW_WAIT_LIMIT = 60.0
_PREVIEW_SECONDS_EACH = 5.0
#: And a floor. The job signal was confirmed live on this build in the
#: foreground harness (True on the first tick after queueing, False
#: once the job ends), but a floor still guards the second or so before
#: the job registers as running. Ten seconds is longer than 24
#: previews take and short enough that nobody notices.
_PREVIEW_HOLD_MINIMUM = 10.0


def _wait_limit(count: int) -> float:
    """How long the handler may stay off for ``count`` queued previews."""
    return max(_PREVIEW_WAIT_LIMIT, _PREVIEW_SECONDS_EACH * max(count, 0))


def _suspend_depsgraph_handlers() -> int:
    """Take the handlers off. Returns how many. Idempotent."""
    if _SUSPENDED:
        return sum(len(taken) for _list, taken in _SUSPENDED)

    for name in _DEPSGRAPH_HANDLERS:
        app = getattr(bpy, "app", None)
        handlers = getattr(getattr(app, "handlers", None), name, None)
        if handlers is None:
            continue
        try:
            taken = list(handlers)
            if not taken:
                continue
            handlers[:] = []
        except (AttributeError, TypeError) as exc:
            logger.debug("could not suspend %s: %s", name, exc)
            continue
        _SUSPENDED.append((handlers, taken))

    total = sum(len(taken) for _list, taken in _SUSPENDED)
    if total:
        logger.info(
            "suspended %d depsgraph handler(s) from other add-ons while "
            "previews render: %s", total,
            ", ".join(
                getattr(h, "__module__", "?")
                for _list, taken in _SUSPENDED for h in taken
            ),
        )
    return total


def _resume_depsgraph_handlers() -> None:
    """Put them back, by identity, in the lists they came from."""
    while _SUSPENDED:
        handlers, taken = _SUSPENDED.pop()
        try:
            handlers[:] = taken
        except (AttributeError, TypeError) as exc:
            logger.warning(
                "could not put a depsgraph handler back (%s) — save your work "
                "and restart Blender", exc,
            )


#: Blender's own name for the icon / asset preview job.
_PREVIEW_JOB = "RENDER_PREVIEW"


def _preview_job_running() -> bool | None:
    """Whether Blender is still rendering previews. None if it cannot say."""
    app = getattr(bpy, "app", None)
    ask = getattr(app, "is_job_running", None)
    if ask is None:
        return None
    try:
        return bool(ask(_PREVIEW_JOB))
    except (RuntimeError, TypeError, ValueError):
        return None


def _previews_pending(objects) -> int:
    """How many of these still have no rendered thumbnail.

    The fallback signal, for a Blender without ``is_job_running``. Not
    reliable on its own: the preview image can be allocated before it
    is rendered, which read as "done" a second after the jobs were
    queued and put the handlers straight back.
    """
    pending = 0
    for obj in objects:
        preview = getattr(obj, "preview", None)
        size = getattr(preview, "image_size", None)
        try:
            if not size or not size[0]:
                pending += 1
        except (IndexError, TypeError):
            pending += 1
    return pending


def _finish_when_previews_are_done(marked) -> None:
    """Wait for the render jobs, THEN tidy up.

    ``asset_generate_preview()`` queues a job and returns. Everything
    that has to happen after the previews exist — taking the palette
    out of the depsgraph so a thousand models stop being evaluated on
    every frame, and putting the suspended handler back — has to wait
    for the jobs, not for this function.

    **Which handler, and why it has to stay off until the jobs are
    over.** With every add-on disabled the crash still came, by the
    same path, and one depsgraph handler remained::

        depsgraph_update_post  sync_dg_handler  from goo_engine_light_groups

    It is not an add-on. It ships in Goo Engine's ``scripts/startup``
    and cannot be switched off from the add-on list — which is why
    disabling the add-ons did not help. What it does
    (``goo_engine_light_groups.py``)::

        def sync_dg_handler(scn, dg):
            for update in dg.updates:
                if isinstance(uid, Material) or isinstance(uid, Light):
                    sync_light_groups()          # on ANY material update

        def sync_light_groups():
            for data in iter_light_group_owners():   # EVERY material
                map_bits(data, bit_mapping)

        def map_bits(data, mapping):
            data.light_group_bits = (0, 0, 0, 0)     # RNA write, notifier
            data.light_group_shadow_bits = ...       # RNA write, notifier

    One material evaluated by a preview job, and it rewrites two
    properties on every material in the file — hundreds, on a map with
    147 models — each write queueing a UI notifier from inside the
    depsgraph update the preview is running. That is the crash, all
    three times: ``bpy_app_generic_callback`` → Python →
    ``pyrna_struct_setattro`` → ``WM_event_add_notifier_ex`` →
    ``note_cmp_for_queue_fn``.

    Taking it off is safe: it recomputes derived bit masks that its
    own ``load_post`` and property-update hooks recompute anyway.

    **When the jobs are over** is asked of Blender directly —
    ``bpy.app.is_job_running("RENDER_PREVIEW")`` — rather than
    inferred from the preview images, which can be allocated before
    they are rendered. The palette is excluded first and the handler
    put back one tick LATER, so the depsgraph update the exclusion
    causes runs with the handler still off.

    Without ``bpy.app.timers`` (the test double, a background Blender)
    the handler goes straight back and the palette stays evaluated:
    slower, and correct.
    """
    timers = getattr(getattr(bpy, "app", None), "timers", None)
    if timers is None or not hasattr(timers, "register"):
        _resume_depsgraph_handlers()
        return

    state = {"waited": 0.0, "seen_running": False, "excluded": False,
             "limit": _wait_limit(len(marked))}

    def _tick():
        running = _preview_job_running()
        if running is None:
            # No job signal in this Blender: fall back to the images.
            running = _previews_pending(marked) > 0
        elif running:
            state["seen_running"] = True

        # The job may not have STARTED by the first tick. Give it two
        # ticks to appear before believing "not running" means "done".
        not_yet_started = (
            not running and not state["seen_running"]
            and state["waited"] < 2 * _PREVIEW_POLL_SECONDS
        )
        too_soon = state["waited"] < _PREVIEW_HOLD_MINIMUM
        if (running or not_yet_started or too_soon) and state["waited"] < state["limit"]:
            state["waited"] += _PREVIEW_POLL_SECONDS
            return _PREVIEW_POLL_SECONDS

        if running:
            logger.warning(
                "previews were still rendering after %.0f seconds (the limit "
                "for %d of them); tidying up anyway", state["waited"], len(marked),
            )

        if not state["excluded"]:
            # Exclude now; put the handler back on the NEXT tick, so the
            # depsgraph update this causes still runs without it.
            _set_excluded(ASSET_COLLECTION, True)
            state["excluded"] = True
            logger.info(
                "asset palette: previews finished after %.0f s, %d model(s) "
                "taken out of the depsgraph", state["waited"], len(marked),
            )
            return _PREVIEW_POLL_SECONDS

        _resume_depsgraph_handlers()
        return None

    try:
        timers.register(_tick, first_interval=_PREVIEW_POLL_SECONDS)
    except (AttributeError, RuntimeError, TypeError) as exc:
        logger.debug("could not register the preview timer: %s", exc)
        _resume_depsgraph_handlers()


@contextlib.contextmanager
def _depsgraph_handlers_suspended():
    """Take other add-ons' depsgraph handlers off while previews render.

    MEASURED, from ``TESTmap.crash.txt`` — Blender 3.6.1, EXCEPTION_
    ACCESS_VIOLATION::

        do_job_thread
          icon_preview_startjob_all_sizes
            object_preview_render
              scene_graph_update_tagged
                BKE_callback_exec_id_depsgraph
                  bpy_app_generic_callback      <- a PYTHON handler
                    ... pyrna_struct_setattro   <- it sets an RNA property
                      RNA_property_update
                        WM_event_add_notifier_ex
                          note_cmp_for_queue_fn <- crash

    A preview is rendered on a WORKER THREAD. Blender evaluates the
    depsgraph there, which fires every registered Python depsgraph
    handler on that thread. One of them sets an RNA property; setting
    one queues a UI notifier; the notifier queue is not thread-safe.

    Seven such handlers are registered in this Blender::

        depsgraph_update_pre   DirectUpdateNPRNodes       toonkit
        depsgraph_update_pre   on_depsgraph_update_pre    psoft_pencil4_line
        depsgraph_update_post  sync_dg_handler            goo_engine_light_groups
        depsgraph_update_post  scene_update_post          flip_fluids_addon
        depsgraph_update_post  update_material_utility    extreme_pbr
        depsgraph_update_post  ypaint_last_object_update  ucupaint
        depsgraph_update_post  sbs_depsgraph_update_post  Substance3DInBlender

    None of them is this SDK's — it registers no handlers at all. They
    exist to react to the user editing something, and rendering a
    thumbnail is not the user editing something, so taking them off for
    the duration costs nothing and removes the exact frames above.

    Restored in a ``finally`` and by identity, so a handler another
    add-on adds or removes meanwhile is not resurrected or lost.
    """
    saved = []
    for name in _DEPSGRAPH_HANDLERS:
        app = getattr(bpy, "app", None)
        handlers = getattr(getattr(app, "handlers", None), name, None)
        if handlers is None:
            continue
        try:
            taken = list(handlers)
            if not taken:
                continue
            handlers[:] = []
        except (AttributeError, TypeError) as exc:
            logger.debug("could not suspend %s: %s", name, exc)
            continue
        saved.append((handlers, taken))

    total = sum(len(taken) for _list, taken in saved)
    if total:
        logger.info(
            "suspended %d depsgraph handler(s) from other add-ons while "
            "previews render: %s", total,
            ", ".join(
                getattr(h, "__module__", "?")
                for _list, taken in saved for h in taken
            ),
        )
    try:
        yield total
    finally:
        for handlers, taken in saved:
            try:
                handlers[:] = taken
            except (AttributeError, TypeError) as exc:
                logger.warning(
                    "could not put a depsgraph handler back (%s) — save your "
                    "work and restart Blender", exc,
                )


def _settle_view_layer() -> None:
    """Let the depsgraph catch up with everything just linked."""
    view_layer = getattr(bpy.context, "view_layer", None)
    try:
        if view_layer is not None:
            view_layer.update()
    except (AttributeError, RuntimeError, TypeError) as exc:
        logger.debug("could not update the view layer: %s", exc)


def _mark(obj, asset, map_name: str = "") -> bool:
    """Mark an object as an asset, describe it and file it. True if it took."""
    try:
        obj.asset_mark()
    except (AttributeError, RuntimeError, TypeError) as exc:
        logger.debug("could not mark %s as an asset: %s", asset.asset_id, exc)
        return False

    data = getattr(obj, "asset_data", None)
    if data is None:
        return False

    try:
        # The id the map format uses, spelled out: the display name is
        # prettied up for reading and is not what world.xml writes.
        size = asset.geometry.dimensions if asset.geometry else None
        described = f"{asset.asset_id} — {asset.file_path}"
        if size is not None:
            described += f" ({size.x:.1f} x {size.y:.1f} x {size.z:.1f})"
        data.description = described
    except (AttributeError, TypeError) as exc:
        logger.debug("could not describe %s: %s", asset.asset_id, exc)

    for tag in preview_tags(asset):
        try:
            data.tags.new(tag)
        except (AttributeError, RuntimeError, TypeError):
            # A duplicate tag raises; nothing depends on it.
            pass

    # Both, as asked: the tree groups, the tags filter.
    try:
        data.catalog_id = catalog_uuid(
            catalog_path(map_name, asset.category, asset.subcategory)
        )
    except (AttributeError, TypeError) as exc:
        logger.debug("could not file %s: %s", asset.asset_id, exc)
    return True


#: Thumbnail edge, in pixels. Blender's own asset previews are 256;
#: 128 is what its icon job renders first and plenty for a grid.
PREVIEW_SIZE = 128

#: A three-quarter view from above, the convention for a thumbnail.
_PREVIEW_DIRECTION = (1.0, -1.0, 0.8)


def _render_preview_now(obj) -> bool:
    """Render the thumbnail synchronously, in a scene of its own.

    **Why not ``asset_generate_preview()``.** It queues Blender's
    icon-preview JOB and returns. Every one of six crash logs from the
    user's machine has that job in it — or its aftermath: the last
    one is Blender's own main loop, no Python on the stack, popping a
    notifier and dying on a corrupt entry in the notifier set, with
    no preview thread left alive. The job is asynchronous, it runs
    long after the operator, it evaluates the depsgraph on a worker
    thread, and it posts notifiers referring to IDs that undo, redo
    and the Asset Browser's own redraws may have replaced by the time
    they are processed. Windows of handler suspension around it were
    tried three times and each time the crash arrived by another
    door.

    So: no job. A temporary scene holding this one object and a
    camera framed on its bounding box, rendered with Workbench through
    ``render.render`` — which runs to completion on the main thread
    before returning — and the pixels pushed into ``obj.preview``.
    MEASURED: 0.7 s for the first thumbnail in a background Blender,
    the PNG framed and lit, ``is_job_running("RENDER_PREVIEW")`` False
    throughout.

    Returns False when the object has no geometry to frame or the
    render refuses; the caller may then fall back to the job.
    """
    import tempfile

    try:
        import mathutils
    except ImportError:
        # Outside Blender (the test double): nothing to render with.
        return False

    mesh = getattr(obj, "data", None)
    corners = getattr(obj, "bound_box", None)
    if mesh is None or corners is None:
        return False
    try:
        points = [mathutils.Vector(c) for c in corners]
        low = mathutils.Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
        high = mathutils.Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    except (TypeError, ValueError):
        return False
    radius = (high - low).length / 2.0
    if radius <= 0.0:
        return False
    centre = (low + high) / 2.0

    scene = camera = camera_data = image = None
    path = os.path.join(tempfile.gettempdir(), f"exm_preview_{os.getpid()}.png")
    prefs_view = getattr(getattr(bpy.context, "preferences", None), "view", None)
    display_type = getattr(prefs_view, "render_display_type", None)
    try:
        scene = bpy.data.scenes.new("ExM_PreviewScene")
        scene.collection.objects.link(obj)
        camera_data = bpy.data.cameras.new("ExM_PreviewCam")
        camera = bpy.data.objects.new("ExM_PreviewCam", camera_data)
        scene.collection.objects.link(camera)
        scene.camera = camera

        # The object sits at the palette's origin; the box is local.
        direction = mathutils.Vector(_PREVIEW_DIRECTION).normalized()
        camera.location = centre + direction * radius * 2.6
        camera.rotation_mode = "QUATERNION"
        camera.rotation_quaternion = (centre - camera.location).to_track_quat("-Z", "Y")
        camera_data.lens = 50
        camera_data.clip_end = max(1000.0, radius * 10.0)

        render = scene.render
        render.resolution_x = render.resolution_y = PREVIEW_SIZE
        render.resolution_percentage = 100
        render.engine = "BLENDER_WORKBENCH"
        render.film_transparent = True
        render.image_settings.file_format = "PNG"
        render.image_settings.color_mode = "RGBA"
        render.filepath = path
        try:
            scene.display.shading.light = "STUDIO"
            scene.display.shading.color_type = "TEXTURE"
        except (AttributeError, TypeError):
            pass
        # Keep the render from opening a window of its own.
        if display_type is not None:
            try:
                prefs_view.render_display_type = "NONE"
            except (AttributeError, TypeError):
                pass

        with bpy.context.temp_override(scene=scene):
            result = bpy.ops.render.render(write_still=True)
        if "FINISHED" not in result or not os.path.isfile(path):
            logger.debug("synchronous preview of %s: %s", obj.name, result)
            return False

        image = bpy.data.images.load(path)
        pixels = list(image.pixels)
        size = tuple(image.size)
        if not pixels or not size[0]:
            return False
        obj.preview_ensure()
        obj.preview.image_size = size
        obj.preview.image_pixels_float = pixels
        return True
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        logger.debug("synchronous preview of %s failed: %s", obj.name, exc)
        return False
    finally:
        if display_type is not None:
            try:
                prefs_view.render_display_type = display_type
            except (AttributeError, TypeError):
                pass
        for datablocks, block in ((bpy.data.images, image), (bpy.data.objects, camera),
                                  (bpy.data.cameras, camera_data)):
            if block is not None:
                try:
                    datablocks.remove(block)
                except (AttributeError, RuntimeError, TypeError):
                    pass
        if scene is not None:
            try:
                scene.collection.objects.unlink(obj)
            except (AttributeError, RuntimeError, TypeError):
                pass
            try:
                bpy.data.scenes.remove(scene)
            except (AttributeError, RuntimeError, TypeError):
                pass
        try:
            os.remove(path)
        except OSError:
            pass


@contextlib.contextmanager
def _progress(total: int):
    """Blender's cursor progress, when there is a window to show it in."""
    wm = getattr(bpy.context, "window_manager", None)
    begin = getattr(wm, "progress_begin", None)
    done = [0]
    started = False
    if begin is not None and total:
        try:
            begin(0, total)
            started = True
        except (RuntimeError, TypeError):
            started = False

    def advance():
        done[0] += 1
        if started:
            try:
                wm.progress_update(done[0])
            except (RuntimeError, TypeError):
                pass

    try:
        yield advance
    finally:
        if started:
            try:
                wm.progress_end()
            except (RuntimeError, TypeError):
                pass


def _generate_preview(obj) -> bool:
    """Render the thumbnail through Blender's icon-preview JOB. The
    fallback only: see _render_preview_now for why."""
    try:
        obj.asset_generate_preview()
        return True
    except (AttributeError, RuntimeError, TypeError) as exc:
        logger.debug("could not render a preview for %s: %s", obj.name, exc)
        return False


def clear_asset_previews(asset_id_prop: str = "exm_asset_id") -> int:
    """Unmark and remove every built preview. Returns how many.

    Wanted because the palette is derived data: pointing the add-on at
    a different game folder should not leave the previous game's models
    in the browser.
    """
    collection = bpy.data.collections.get(ASSET_COLLECTION)
    if collection is None:
        return 0

    removed = 0
    for obj in list(collection.objects):
        if not obj.get(asset_id_prop):
            continue
        try:
            obj.asset_clear()
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            bpy.data.objects.remove(obj)
            removed += 1
        except (AttributeError, RuntimeError, TypeError) as exc:
            logger.debug("could not remove %s: %s", obj.name, exc)
    logger.info("asset previews: %s cleared", removed)
    return removed
