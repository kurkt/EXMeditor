# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Which models name which texture, across the whole game.

Editing a texture in place changes every model that names it, so
"is this texture shared?" is the question the edit path turns on. It
used to be answered by counting the objects in the OPEN SCENE, and the
scene is not the game::

    1391 model files scanned
    1006 distinct texture names
     531 of them — 53% — are named by more than one model
     concrete.dds by 43, coverrock_detail.dds by 41, gun_8.dds by 56

A map places a fraction of those 1391 models, so a texture shared by
forty-three of them shows up as "used by one model here" as often as
not. The edit then went in place, overwrote the file, and changed the
other forty-two somewhere nobody was looking — which is the exact
damage the fork exists to prevent.

The census costs a pass over every ``.gam`` under the game folder, so
it is built once and kept.
"""

from __future__ import annotations

import os

from formats.exm.gam import SKIN_CHUNK_ID, read_container, read_materials
from utils.logging import get_logger

logger = get_logger("formats.exm.texture_usage")

#: texture name (lowercased) -> set of model filenames naming it,
#: keyed by game root. Built on demand.
_CENSUS: dict[str, dict[str, set]] = {}


def clear_cache() -> None:
    """Forget the census. Wanted when the game folder changes."""
    _CENSUS.clear()


def build_census(game_root: str, *, refresh: bool = False) -> dict:
    """Read every model once and record which textures it names.

    Failures are skipped rather than raised: a corpus with a handful of
    unreadable models should still answer the question for the rest,
    and the alternative is an edit path that stops working because of a
    file it was never going to touch.
    """
    key = os.path.normcase(os.path.abspath(game_root or ""))
    if not refresh and key in _CENSUS:
        return _CENSUS[key]

    users: dict[str, set] = {}
    models = 0
    unreadable = 0
    root = os.path.join(game_root, "data", "models")
    for folder, _dirs, files in os.walk(root):
        for name in files:
            if not name.lower().endswith(".gam"):
                continue
            path = os.path.join(folder, name)
            try:
                materials = _materials_of(path)
            except Exception:  # noqa: BLE001 - counted, never fatal
                unreadable += 1
                continue
            models += 1
            for material in materials:
                for texture in material.textures:
                    users.setdefault(texture.lower(), set()).add(
                        os.path.normcase(path)
                    )

    logger.info(
        "texture usage: %s model(s) name %s distinct texture(s); %s "
        "unreadable", models, len(users), unreadable,
    )
    _CENSUS[key] = users
    return users


def _materials_of(path: str):
    """A model's materials, without reading its geometry.

    Only the skin chunk is opened. Two reasons, and the second is the
    one that matters: it is far faster over a thousand files, and it
    answers for models whose GEOMETRY this SDK cannot parse yet — the
    98 skinned meshes among them. Reading the whole model would drop
    those from the census, and a texture only they name would then come
    back "unknown".
    """
    _subtype, chunks = read_container(path)
    for chunk in chunks:
        if chunk.chunk_id == SKIN_CHUNK_ID:
            return read_materials(chunk.data)
    return []


def models_naming(texture_name: str, game_root: str) -> set:
    """The model files that name this texture. Empty when unknown."""
    if not texture_name or not game_root:
        return set()
    census = build_census(game_root)
    return set(census.get(texture_name.lower(), ()))


def is_shared(texture_name: str, game_root: str, *, model_path: str = "") -> bool:
    """Whether editing this texture in place would reach another model.

    ``model_path`` is the model being edited; it does not count as
    somebody else. A texture the census has never heard of is treated
    as SHARED — an unknown is not evidence of safety, and the cost of
    forking unnecessarily is one extra file while the cost of the other
    mistake is silent damage to models nobody has open.
    """
    users = models_naming(texture_name, game_root)
    if not users:
        return True
    if model_path:
        users.discard(os.path.normcase(os.path.abspath(model_path)))
        users.discard(os.path.normcase(model_path))
    return len(users) > 0 if model_path else len(users) > 1
