# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generic binary read/write helpers for the EXMeditor.

Reverse-engineered binary formats tend to need the same handful of
primitives over and over: fixed-width integers/floats, raw byte
extraction, alignment padding, null-terminated or length-prefixed
strings. This module provides those once, as :class:`BinaryReader` and
:class:`BinaryWriter`, so ``formats/exm/terrain.py``, a future SGO
mesh parser, and any other binary codec share one implementation and
one error-reporting style instead of each calling :mod:`struct`
directly and inventing its own bounds-checking.

This module has no ``bpy`` dependency and no knowledge of any specific
file's structure — it only knows how to move bytes in and out of a
buffer.
"""

from __future__ import annotations

import array
import struct
from pathlib import Path

from utils.errors import ErrorContext, ParsingError

# Struct format characters, all little-endian ("<") since that is the
# overwhelmingly common convention for PC game formats of this era.
# A reader/writer can still be constructed with a different byte order
# if a future format needs one.
_FORMATS: dict[str, str] = {
    "uint8": "B",
    "int8": "b",
    "uint16": "H",
    "int16": "h",
    "uint32": "I",
    "int32": "i",
    "uint64": "Q",
    "int64": "q",
    "float32": "f",
    "float64": "d",
}

_NATIVE_BYTE_ORDER = "<" if struct.pack("=I", 1) == struct.pack("<I", 1) else ">"


class BinaryReader:
    """Sequential reader over an in-memory ``bytes`` buffer.

    Parameters
    ----------
    data:
        The full file content to read from.
    source_file:
        Path used only for error messages, so a failure reports which
        file it came from without the caller needing to catch and
        re-wrap the exception.
    byte_order:
        A :mod:`struct` byte-order prefix, ``"<"`` (little-endian,
        default) or ``">"`` (big-endian).
    """

    def __init__(self, data: bytes, *, source_file: str | None = None, byte_order: str = "<") -> None:
        self._data = data
        self._pos = 0
        self._source_file = source_file
        self._byte_order = byte_order

    @classmethod
    def from_file(cls, path: str, *, byte_order: str = "<") -> "BinaryReader":
        """Read ``path`` fully into memory and return a reader over it.

        Raises ``ParsingError`` (chained from the original ``OSError``)
        if the file cannot be read.
        """
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            raise ParsingError(
                "could not read binary file",
                context=ErrorContext(source_file=path, extra={"os_error": str(exc)}),
            ) from exc
        return cls(data, source_file=path, byte_order=byte_order)

    def tell(self) -> int:
        """Current read position, in bytes from the start of the buffer."""
        return self._pos

    def remaining(self) -> int:
        """Number of bytes left to read."""
        return len(self._data) - self._pos

    def at_end(self) -> bool:
        return self._pos >= len(self._data)

    def seek(self, offset: int, whence: int = 0) -> None:
        """Move the read position. ``whence``: 0=start, 1=current, 2=end
        (matching :meth:`io.IOBase.seek` semantics)."""
        if whence == 0:
            new_pos = offset
        elif whence == 1:
            new_pos = self._pos + offset
        elif whence == 2:
            new_pos = len(self._data) + offset
        else:
            raise ValueError(f"invalid whence: {whence!r}")
        if not (0 <= new_pos <= len(self._data)):
            raise ParsingError(
                "seek target is outside the buffer",
                context=ErrorContext(
                    source_file=self._source_file,
                    offset=new_pos,
                    extra={"buffer_size": len(self._data)},
                ),
            )
        self._pos = new_pos

    def align(self, alignment: int) -> None:
        """Advance the read position to the next multiple of ``alignment``."""
        remainder = self._pos % alignment
        if remainder:
            self.seek(alignment - remainder, whence=1)

    def peek(self, size: int) -> bytes:
        """Return the next ``size`` bytes without advancing the position."""
        pos = self._pos
        data = self.read_bytes(size)
        self._pos = pos
        return data

    def read_bytes(self, size: int) -> bytes:
        """Read and return exactly ``size`` raw bytes."""
        if self._pos + size > len(self._data):
            raise ParsingError(
                f"unexpected end of data: requested {size} bytes, "
                f"only {self.remaining()} remain",
                context=ErrorContext(source_file=self._source_file, offset=self._pos),
            )
        chunk = self._data[self._pos:self._pos + size]
        self._pos += size
        return chunk

    def _read_scalar(self, type_name: str) -> int | float:
        fmt = self._byte_order + _FORMATS[type_name]
        size = struct.calcsize(fmt)
        chunk = self.read_bytes(size)
        try:
            (value,) = struct.unpack(fmt, chunk)
        except struct.error as exc:
            raise ParsingError(
                f"failed to unpack {type_name}",
                context=ErrorContext(source_file=self._source_file, offset=self._pos - size, field=type_name),
            ) from exc
        return value

    def read_uint8(self) -> int:
        return self._read_scalar("uint8")

    def read_int8(self) -> int:
        return self._read_scalar("int8")

    def read_uint16(self) -> int:
        return self._read_scalar("uint16")

    def read_int16(self) -> int:
        return self._read_scalar("int16")

    def read_uint32(self) -> int:
        return self._read_scalar("uint32")

    def read_int32(self) -> int:
        return self._read_scalar("int32")

    def read_uint64(self) -> int:
        return self._read_scalar("uint64")

    def read_int64(self) -> int:
        return self._read_scalar("int64")

    def read_float32(self) -> float:
        return self._read_scalar("float32")

    def read_float64(self) -> float:
        return self._read_scalar("float64")

    def read_float32_array(self, count: int) -> array.array:
        """Read ``count`` consecutive float32 values as an ``array.array('f')``.

        Used for bulk data like the ``displace.bin`` heightmap grid,
        where unpacking one value at a time in a Python loop would be
        both slower and noisier than a single bulk read.
        """
        size = count * struct.calcsize(self._byte_order + "f")
        chunk = self.read_bytes(size)
        values = array.array("f")
        values.frombytes(chunk)
        if self._byte_order == ">" and _NATIVE_BYTE_ORDER == "<":
            values.byteswap()
        elif self._byte_order == "<" and _NATIVE_BYTE_ORDER == ">":
            values.byteswap()
        return values

    def read_string(
        self,
        *,
        length: int | None = None,
        encoding: str = "utf-8",
        null_terminated: bool = False,
    ) -> str:
        """Read a string, either fixed-``length`` or null-terminated.

        Parameters
        ----------
        length:
            If given, read exactly this many bytes. If ``null_terminated``
            is also ``True``, the fixed-size field is read first and then
            trimmed at the first NUL byte (the common "char name[32]"
            C-struct convention).
        null_terminated:
            If ``True`` and ``length`` is ``None``, read byte-by-byte
            until a NUL terminator (exclusive) or the buffer ends.
        """
        if length is not None:
            raw = self.read_bytes(length)
            if null_terminated:
                nul = raw.find(b"\x00")
                if nul != -1:
                    raw = raw[:nul]
            return raw.decode(encoding)

        if not null_terminated:
            raise ValueError("read_string requires either `length` or `null_terminated=True`")

        start = self._pos
        nul = self._data.find(b"\x00", start)
        if nul == -1:
            raw = self.read_bytes(self.remaining())
        else:
            raw = self.read_bytes(nul - start)
            self.seek(1, whence=1)  # skip the NUL terminator itself
        return raw.decode(encoding)


class BinaryWriter:
    """Sequential writer building up an in-memory ``bytearray``.

    Mirrors :class:`BinaryReader`'s primitives so a format's exporter
    reads like the inverse of its importer.
    """

    def __init__(self, *, byte_order: str = "<") -> None:
        self._buffer = bytearray()
        self._byte_order = byte_order

    def tell(self) -> int:
        return len(self._buffer)

    def write_bytes(self, data: bytes) -> None:
        self._buffer.extend(data)

    def align(self, alignment: int, pad: bytes = b"\x00") -> None:
        """Pad with ``pad`` until the position is a multiple of ``alignment``."""
        remainder = len(self._buffer) % alignment
        if remainder:
            self._buffer.extend(pad * (alignment - remainder))

    def _write_scalar(self, type_name: str, value: int | float) -> None:
        fmt = self._byte_order + _FORMATS[type_name]
        self._buffer.extend(struct.pack(fmt, value))

    def write_uint8(self, value: int) -> None:
        self._write_scalar("uint8", value)

    def write_int8(self, value: int) -> None:
        self._write_scalar("int8", value)

    def write_uint16(self, value: int) -> None:
        self._write_scalar("uint16", value)

    def write_int16(self, value: int) -> None:
        self._write_scalar("int16", value)

    def write_uint32(self, value: int) -> None:
        self._write_scalar("uint32", value)

    def write_int32(self, value: int) -> None:
        self._write_scalar("int32", value)

    def write_uint64(self, value: int) -> None:
        self._write_scalar("uint64", value)

    def write_int64(self, value: int) -> None:
        self._write_scalar("int64", value)

    def write_float32(self, value: float) -> None:
        self._write_scalar("float32", value)

    def write_float64(self, value: float) -> None:
        self._write_scalar("float64", value)

    def write_float32_array(self, values: array.array) -> None:
        """Write an ``array.array('f')`` of float32 values in bulk."""
        data = values.tobytes()
        if self._byte_order == ">" and _NATIVE_BYTE_ORDER == "<":
            data = array.array("f", values).copy()
            data.byteswap()
            data = data.tobytes()
        self.write_bytes(data)

    def write_string(
        self,
        text: str,
        *,
        encoding: str = "utf-8",
        length: int | None = None,
        pad: bytes = b"\x00",
    ) -> None:
        """Write a string, optionally padded/truncated to a fixed ``length``.

        If ``length`` is ``None``, writes the encoded bytes with no
        terminator or padding — the caller adds one if the format needs
        it (e.g. via ``write_uint8(0)``).
        """
        raw = text.encode(encoding)
        if length is None:
            self.write_bytes(raw)
            return
        if len(raw) > length:
            raise ValueError(
                f"encoded string is {len(raw)} bytes, exceeds fixed length {length}"
            )
        self.write_bytes(raw + pad * (length - len(raw)))

    def getvalue(self) -> bytes:
        """Return the accumulated bytes."""
        return bytes(self._buffer)

    def write_to_file(self, path: str) -> None:
        """Write the accumulated bytes to ``path``.

        Raises ``ParsingError`` (chained from the original ``OSError``)
        if the file cannot be written.
        """
        try:
            Path(path).write_bytes(self.getvalue())
        except OSError as exc:
            raise ParsingError(
                "could not write binary file",
                context=ErrorContext(source_file=path, extra={"os_error": str(exc)}),
            ) from exc
