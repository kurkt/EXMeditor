# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared, lightweight domain type aliases for ``core/``.

Every ``core`` module (``terrain``, ``objects``, ``roads``, ``scene``)
tends to reference the same handful of identifier types. Defining them
here — a module with no dependencies on the rest of ``core`` — means
``core/terrain.py`` and `core/objects.py`` can both use, say, ``AssetID``
without importing from each other, which is what actually causes
circular imports in a growing project (``terrain`` importing from
``scene`` importing from ``terrain``, etc.).

This module intentionally holds *only* type aliases, not real classes
with behavior — those belong in the module that owns the concept
(``HeightmapData`` in ``core/terrain.py``, ``RoadNetwork`` in
``core/roads.py``, ...). If a shared *class* (not just an alias) is
ever needed by multiple ``core`` modules, it goes here too, but nothing
like that exists yet.
"""

from __future__ import annotations

from typing import NewType

# The game's numeric asset identifier, as used in both `world.xml`
# (which object instance references which asset) and
# `object_names.xml` (which asset id maps to which model path).
AssetID = NewType("AssetID", int)

# The game's numeric road-node identifier, as used within `roads.xml`'s
# node list and edge references.
RoadID = NewType("RoadID", int)

# A stable, SDK-internal identifier for a single ObjectInstance within
# a MapScene, independent of its position in `MapScene.objects` and of
# whatever Blender names the corresponding Empty.
#
# NOT YET WIRED IN: this alias exists so the type has a name to import,
# but `ObjectInstance` does not have an `id: ObjectID` field yet — that
# was raised as a good idea and explicitly deferred (see the
# architecture doc's roadmap) until MapScene.remove_object() /
# .get_object() actually need it, rather than adding an unused field
# now. Declaring the alias here means adding it later is a one-line
# change to `core/objects.py`, not a new import to wire through.
ObjectID = NewType("ObjectID", int)
