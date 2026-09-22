# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Which models to put in the asset browser, and in what order.

The catalogue lists over a thousand models. Reading every one of them
to make a thumbnail takes minutes, so the choice of which to build —
and which to build FIRST when a limit bites — is a decision worth
making deliberately rather than taking whatever the catalogue happens
to iterate.

Ordering is by how often the open map already uses a model. A map's
own vocabulary is small: on ``r1m1`` 1735 placements draw on a few
hundred distinct models out of 1072 catalogued. Building those first
means a capped run produces the models the user is actually working
with, not the alphabetically early ones.

Pure: this decides, and ``blender_io/asset_library.py`` carries it out.
"""

from __future__ import annotations

#: Build at most this many by default.
#:
#: Was 200, and 200 froze Blender: ``asset_generate_preview()`` queues
#: a render job per object and the queue is worked before the UI comes
#: back, so the wait grows with the count and nothing on screen says
#: why. Forty is a screenful of thumbnails and comes back promptly;
#: raising it is a choice the user makes with the number in front of
#: them, not a default they discover by waiting.
#:
#: Zero means no limit, and on a full catalogue means over a thousand.
DEFAULT_LIMIT = 24


def plan_previews(
    library,
    *,
    category: str | None = None,
    asset_ids=None,
    limit: int = DEFAULT_LIMIT,
) -> list:
    """The assets to build previews for, most-used first.

    ``category`` narrows to one category; ``asset_ids`` narrows to an
    explicit set (what the open map uses, say). Both may be combined.

    Assets whose model file did not resolve are dropped: a thumbnail
    cannot be made from a file that is not there, and carrying them
    into the build only to fail one by one turns one clear message
    into a hundred.
    """
    wanted = set(asset_ids) if asset_ids is not None else None

    chosen = []
    for asset in library.all():
        if not asset.resolved_path:
            continue
        if category and asset.category != category:
            continue
        if wanted is not None and asset.asset_id not in wanted:
            continue
        chosen.append(asset)

    chosen.sort(key=lambda a: (-a.usage_count, a.display_name or a.asset_id))
    if limit and limit > 0:
        return chosen[:limit]
    return chosen


def unresolved(library, *, category: str | None = None) -> list:
    """Assets the catalogue names and the disk does not have.

    Reported once, as a count and a sample, rather than as one failure
    per model — see :func:`plan_previews`.
    """
    return [
        asset
        for asset in library.all()
        if not asset.resolved_path
        and (not category or asset.category == category)
    ]


def preview_tags(asset) -> list[str]:
    """The tags a built asset carries, for filtering in the browser.

    Its category and subcategory, and ``used`` when the open map
    already places it — which is the filter that turns a thousand
    models into the few dozen this map is made of.
    """
    tags = []
    if asset.category:
        tags.append(asset.category)
    if asset.subcategory and asset.subcategory != asset.category:
        tags.append(asset.subcategory)
    if asset.usage_count:
        tags.append("used")
    return tags
