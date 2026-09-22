# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Names for Blender objects that carry their map identity.

Blender's own names (``Empty.001``, ``Cube.003``) say nothing about
which map node an object is, so working in the viewport meant
constantly cross-referencing the outliner against custom properties.
A name that shows the node id and the model it draws removes that
step: an object called ``4762_house1`` is immediately identifiable,
and swapping its model to ``house3`` leaves the id untouched.

The name is for the user, not for the format. Export always writes the
node's real name from the ``exm_original_name`` property — Blender
appends ``.001`` to duplicates and users rename things, so a name in
the viewport can never be the authoritative identifier.

No ``bpy`` import.
"""

from __future__ import annotations

import re

#: How much detail to put in the name.
STYLE_ID_ONLY = "ID_ONLY"                 # 4762
STYLE_ID_MODEL = "ID_MODEL"               # 4762_house1
STYLE_ID_CLASS_MODEL = "ID_CLASS_MODEL"   # 4762_Model_house1
STYLE_ORIGINAL = "ORIGINAL"               # Object4762

#: Strips the editor's prefix so the bare number can be shown. Nodes
#: with hand-typed names (``Vill_1760``) don't match and are used as-is.
_OBJECT_NUMBER = re.compile(r"^Object(\d+)$")

#: Class names shortened for readability — the ``Sg`` prefix and
#: ``Node`` suffix are on every one of them and carry no information.
_CLASS_SHORT = {
    "SgAnimatedModelNode": "Model",
    "SgGameUnitNode": "Unit",
    "SgNode": "Group",
    "SgSoundSourceNode": "Sound",
}


def node_id(node_name: str) -> str:
    """The bare id from a node name: ``Object4762`` -> ``4762``.

    Names that don't follow the editor's pattern are returned unchanged
    — they are meaningful labels a level designer typed, and turning
    them into something else would lose that.
    """
    match = _OBJECT_NUMBER.match(node_name)
    return match.group(1) if match else node_name


def short_class(node_class: str | None) -> str:
    if not node_class:
        return ""
    return _CLASS_SHORT.get(node_class, node_class)


def object_name(
    node_name: str,
    node_class: str | None = None,
    asset_id: str | None = None,
    style: str = STYLE_ID_MODEL,
) -> str:
    """Build the Blender object name for a map node."""
    if style == STYLE_ORIGINAL:
        return node_name

    identifier = node_id(node_name)
    if style == STYLE_ID_ONLY:
        return identifier

    parts = [identifier]
    if style == STYLE_ID_CLASS_MODEL:
        short = short_class(node_class)
        if short:
            parts.append(short)
    if asset_id:
        parts.append(asset_id)

    return "_".join(parts)
