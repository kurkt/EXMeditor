# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""``grass.xml`` — where the grass grows.

Not XML. It is the same ``ecbnt,t`` container as ``.gam`` and
``level.tile``, and the extension is a lie the shipped data tells in
several places.

The layout, walked byte-exact on the sample map — 1820696 bytes
consumed of 1820696::

    chunk 1   uint32 typeCount        13
              uint32 patchCount     3674

    chunk 2   typeCount paths, NUL-terminated

    chunk 3   per patch, patchCount of them:
                  uint32 patchId
                  uint32 listCount
                  uint32 0
                  per list:
                      uint32 typeIndex      into chunk 2
                      uint32 recordCount
                      per record, 24 bytes:
                          float3 position   x, world Y, z
                          float  scale
                          float2 heading    unit vector

``patchCount`` in chunk 1 and the number of patches walked agree
exactly, which is the check that the walk is right rather than merely
plausible.

Two things this cost before they were understood. A patch may hold
**several lists**, one per grass type — most hold one, and reading the
header as a fixed five words works for sixty-odd patches and then
desynchronises. And the fourth header word is the type index, not a
record size: taking it as a size made a 4-record list consume 112 bytes
instead of 96.

The heights are absolute world Y. Checked against ``displace.bin`` for
821 records with ``H[z * N + x]`` at 8 units a sample: **median error
0.004**.

Where the models are
--------------------

The paths name ``.sam`` files. Those do not exist — in a 1355-model
build there is no grass ``.sam`` at all, and the same folders hold
``.gam`` of the same names. So the reader rewrites the extension, and
the models load through the ordinary ``.gam`` reader. No second format
is needed for grass.
"""

from __future__ import annotations

import dataclasses
import os
import re
import struct

from formats.exm.gam import read_container
from utils.errors import ErrorContext, ParsingError
from utils.logging import get_logger

logger = get_logger("formats.exm.grass")

#: Chunk holding the counts.
COUNTS_CHUNK_ID = 1
#: Chunk holding the model paths.
PATHS_CHUNK_ID = 2
#: Chunk holding the placements.
PLACEMENT_CHUNK_ID = 3

#: One placement: three floats of position, a scale, and a heading.
RECORD_SIZE = 24

_NAME_PATTERN = re.compile(rb"[\x20-\x7e]{6,}")


@dataclasses.dataclass
class GrassPlacement:
    """One tuft."""

    x: float
    y: float
    z: float
    scale: float
    heading: tuple


@dataclasses.dataclass
class GrassField:
    """Every placement on a map, and the models they use."""

    models: list = dataclasses.field(default_factory=list)
    #: type index -> placements
    by_type: dict = dataclasses.field(default_factory=dict)
    patches: int = 0

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.by_type.values())

    def model_for(self, type_index: int) -> str:
        """Game-relative path of a type's model, as a ``.gam``."""
        if not 0 <= type_index < len(self.models):
            return ""
        path = self.models[type_index]
        stem, extension = os.path.splitext(path)
        return f"{stem}.gam" if extension.lower() == ".sam" else path


def read_grass(path: str) -> GrassField:
    """Read a map's grass.

    Raises if the walk does not consume the placement chunk exactly.
    A partial walk means the layout is not what was measured, and
    carrying on would place tufts from misread bytes.
    """
    _subtype, chunks = read_container(path)
    by_id = {c.chunk_id: c.data for c in chunks}

    field = GrassField()

    counts = by_id.get(COUNTS_CHUNK_ID, b"")
    expected_patches = 0
    if len(counts) >= 8:
        _types, expected_patches = struct.unpack_from("<2I", counts, 0)

    field.models = [
        m.group().decode("cp1251", errors="replace")
        for m in _NAME_PATTERN.finditer(by_id.get(PATHS_CHUNK_ID, b""))
    ]

    block = by_id.get(PLACEMENT_CHUNK_ID)
    if block is None:
        raise ParsingError(
            "grass file has no placement chunk",
            context=ErrorContext(source_file=path),
        )

    position = 0
    while position + 12 <= len(block):
        _patch_id, list_count, zero = struct.unpack_from("<3I", block, position)
        position += 12

        if zero != 0 or not 0 < list_count <= 64:
            raise ParsingError(
                "grass placement header is not what was measured",
                context=ErrorContext(
                    source_file=path,
                    extra={"at": position - 12, "lists": list_count, "third": zero},
                ),
            )

        for _ in range(list_count):
            type_index, count = struct.unpack_from("<2I", block, position)
            position += 8
            if position + count * RECORD_SIZE > len(block):
                raise ParsingError(
                    "grass list runs past the end of the chunk",
                    context=ErrorContext(
                        source_file=path,
                        extra={"at": position, "count": count},
                    ),
                )

            placements = field.by_type.setdefault(type_index, [])
            for index in range(count):
                x, y, z, scale, cos, sin = struct.unpack_from(
                    "<6f", block, position + index * RECORD_SIZE
                )
                placements.append(
                    GrassPlacement(x=x, y=y, z=z, scale=scale, heading=(cos, sin))
                )
            position += count * RECORD_SIZE

        field.patches += 1

    if position != len(block):
        raise ParsingError(
            "grass placements do not end at the chunk boundary",
            context=ErrorContext(
                source_file=path,
                extra={"ends_at": position, "chunk_size": len(block)},
            ),
        )

    if expected_patches and field.patches != expected_patches:
        logger.warning(
            "%s: walked %s patches, the header says %s",
            os.path.basename(path), field.patches, expected_patches,
        )

    logger.info(
        "%s: %s patch(es), %s tuft(s) across %s type(s)",
        os.path.basename(path), field.patches, field.total, len(field.by_type),
    )
    return field


def find_grass(map_folder: str) -> str | None:
    """The map's grass file, whatever it is called."""
    if not map_folder or not os.path.isdir(map_folder):
        return None
    for name in sorted(os.listdir(map_folder)):
        if name.lower() in ("grass.xml", "grass.bin"):
            return os.path.join(map_folder, name)
    return None
