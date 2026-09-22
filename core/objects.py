# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Object-instance domain model for ``world.xml`` (Ex Machina's scene graph).

Confirmed against real map data — see the architecture doc's
``WorldXML_Format_Spec.md`` for the full investigation this module is
built from: node hierarchy, the ``orgRel`` coordinate system, rotation
encoding, and the ``servers.xml``/``animmodels.xml`` id-resolution
chain are all documented there. This module only holds enough to
losslessly round-trip ``world.xml``'s own data — it deliberately does
NOT resolve ``asset_id`` into an actual model/mesh.

Correction from an earlier version of this module: it previously
defined ``AssetDefinition``/``AssetRegistry`` around the unverified
guess that ``object_names.xml`` maps asset ids to model paths. Real
data disproved this — ``object_names.xml`` is a localization table for
named quest/NPC entities (see the architecture doc), unrelated to
``world.xml``'s ``id`` attribute. The real catalog chain
(``servers.xml`` → ``animmodels.xml`` → ``.gam``) is a separate,
not-yet-implemented concern (see ``WorldXML_Format_Spec.md`` §8) — this
module removed the wrong types rather than leave a confirmed-incorrect
model in place.
"""

from __future__ import annotations

import dataclasses

from utils.math import Vector3

#: The exhaustive set of `class=` values observed on real map data —
#: see WorldXML_Format_Spec.md §2. A node with any other class is not
#: a format variant to silently accept; formats/exm/world.py must
#: raise rather than guess (see that module for the actual check).
KNOWN_NODE_CLASSES = frozenset({
    "SgNode",
    "SgAnimatedModelNode",
    "SgSoundSourceNode",
    "SgGameUnitNode",
})


@dataclasses.dataclass
class ObjectInstance:
    """A single ``<Node>`` from ``world.xml``, with its children (if any).

    Field-by-field justification lives in ``WorldXML_Format_Spec.md``
    (§3 for the attribute schema, §5 for ``org``/``org_rel``, §6 for
    ``raw_rotation``, §7 for ``scale``, §8 for ``asset_id``). Fields
    are ``None`` when the source attribute was absent — never defaulted
    to a concrete value here, so a lossless writer can tell "absent in
    the original" apart from "present with the default value" and
    reproduce the original exactly (see the spec's §11 write-back
    algorithm).

    ``raw_rotation`` stays an opaque 4-float tuple, not a
    ``utils.math.Quaternion`` — the spec's §6 rates the (x, y, z, w)
    component order as "Вероятно" (strong evidence, not confirmed
    against engine source), so interpreting it is deferred to the
    point where it's actually needed (``blender_io``), not baked into
    this dataclass.
    """

    name: str
    node_class: str  # one of KNOWN_NODE_CLASSES; validated by the reader, not here
    org: Vector3 | None = None
    org_rel: bool | None = None  # True = world-space (top-level only), False = parent-local
    raw_rotation: tuple[float, ...] | None = None  # 4 floats if present; None = identity (absent in source)
    scale: Vector3 | None = None  # None = absent in source (implies 1,1,1 — see spec §7)
    asset_id: str | None = None  # the `id=` attribute; required for the 3 leaf classes, absent on plain SgNode containers
    skin: int | None = None
    cast_shadow: bool | None = None  # None = attribute absent (default presumed "yes", per spec §3)
    ndm_action: str | None = None
    children: list["ObjectInstance"] = dataclasses.field(default_factory=list)
    raw_attrs: dict[str, str] = dataclasses.field(default_factory=dict)  # any attribute not modeled above

    def walk(self):
        """Yield this instance and every descendant, depth-first.

        Convenience for callers that need a flat view (e.g.
        ``MapScene.bounding_box()``) without re-implementing the
        recursion at every call site.
        """
        yield self
        for child in self.children:
            yield from child.walk()
