# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Whether editing a texture in place would reach another model.

Asked of the GAME, not of the open scene. It used to be asked of the
scene, and the scene is not the game::

    1391 model files, 1006 distinct texture names
    531 of them — 53% — are named by more than one model
    concrete.dds by 43, coverrock_detail.dds by 41, gun_8.dds by 56

A map places a fraction of those models, so a texture shared
forty-three ways reads as "used by one model here" as often as not.
The edit then went in place, overwrote the file, and changed the other
forty-two somewhere nobody was looking.
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from formats.exm.gam import Chunk, MESH_CHUNK_ID, SKIN_CHUNK_ID, write_container  # noqa: E402
from formats.exm.texture_usage import (  # noqa: E402
    build_census,
    clear_cache,
    is_shared,
    models_naming,
)


def _skin(textures) -> bytes:
    """A skin chunk naming one texture per material."""
    block = struct.pack("<I", len(textures))
    for name in textures:
        block += struct.pack("<17f", *([0.0] * 17))
        block += struct.pack("<I", 1)
        block += b"diffuse".ljust(100, b"\x00")
        block += name.encode("ascii").ljust(44, b"\x00")
        block += struct.pack("<I", 0)
    return block


def _model(folder: str, name: str, textures) -> str:
    path = os.path.join(folder, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(write_container(0, [
            Chunk(MESH_CHUNK_ID, b""),
            Chunk(SKIN_CHUNK_ID, _skin(textures)),
        ]))
    return path


def _game(layout) -> str:
    """A game folder whose models name the textures given."""
    clear_cache()
    root = tempfile.mkdtemp()
    models = os.path.join(root, "data", "models")
    for name, textures in layout.items():
        _model(models, name, textures)
    return root


def test_a_texture_named_by_one_model_is_not_shared() -> None:
    root = _game({
        "lonely.gam": ["lonely.dds"],
        "other.gam": ["concrete.dds"],
    })

    assert is_shared("lonely.dds", root) is False
    assert len(models_naming("lonely.dds", root)) == 1


def test_a_texture_named_by_two_models_is_shared() -> None:
    """The case the scene count missed whenever only one of the two was
    placed on the open map."""
    root = _game({
        "wall.gam": ["concrete.dds"],
        "shed.gam": ["concrete.dds"],
    })

    assert is_shared("concrete.dds", root) is True
    assert len(models_naming("concrete.dds", root)) == 2


def test_the_model_being_edited_does_not_count_as_somebody_else() -> None:
    root = _game({"wall.gam": ["own.dds"]})
    mine = os.path.join(root, "data", "models", "wall.gam")

    assert is_shared("own.dds", root, model_path=mine) is False


def test_a_texture_nobody_names_is_treated_as_shared() -> None:
    """An unknown is not evidence of safety.

    Forking unnecessarily costs one file. The other mistake overwrites
    a texture other models draw with.
    """
    root = _game({"wall.gam": ["concrete.dds"]})

    assert is_shared("never_heard_of_it.dds", root) is True


def test_an_unreadable_model_does_not_stop_the_census() -> None:
    """A corpus with a few broken files should still answer for the
    rest — the alternative is an edit path that stops working because
    of a file it was never going to touch."""
    root = _game({"good.gam": ["a.dds"]})
    with open(os.path.join(root, "data", "models", "broken.gam"), "wb") as handle:
        handle.write(b"not a container at all")

    clear_cache()
    census = build_census(root)
    assert "a.dds" in census


def test_the_census_is_built_once() -> None:
    """It is a pass over every model in the game; twice is a wait."""
    root = _game({"a.gam": ["x.dds"]})

    first = build_census(root)
    assert build_census(root) is first
    assert build_census(root, refresh=True) is not first


def test_no_game_folder_answers_nothing_rather_than_wrongly() -> None:
    assert models_naming("concrete.dds", "") == set()
    assert is_shared("concrete.dds", "") is True
