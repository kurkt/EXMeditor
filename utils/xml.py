# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Safe XML read/write helpers for the EXMeditor.

Wraps :mod:`xml.etree.ElementTree` with typed attribute accessors that
raise the SDK's own :class:`~utils.errors.ParsingError` (with useful
context: which file, which element, which attribute) instead of letting
a bare ``KeyError``/``ValueError`` escape from deep inside a format
parser. Every format codec under ``formats/`` should read/write XML
through this module rather than calling :mod:`xml.etree.ElementTree`
directly, so parsing failures are reported consistently.

This module has no ``bpy`` dependency and no dependency on
``CoordinateSystem``/``Transform`` — it only knows about XML, not about
any particular file's schema.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from utils.errors import ErrorContext, ParsingError

Element = ET.Element


def parse_file(path: str) -> Element:
    """Parse an XML file and return its root element.

    Raises
    ------
    ParsingError
        If the file cannot be read, or its content is not well-formed
        XML. The original :class:`xml.etree.ElementTree.ParseError` (or
        :class:`OSError`) is preserved as ``__cause__``.
    """
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise ParsingError(
            "file is not well-formed XML",
            context=ErrorContext(source_file=path, extra={"parse_error": str(exc)}),
        ) from exc
    except OSError as exc:
        raise ParsingError(
            "could not read XML file",
            context=ErrorContext(source_file=path, extra={"os_error": str(exc)}),
        ) from exc
    return tree.getroot()


def parse_string(data: str, *, source_file: str | None = None) -> Element:
    """Parse an XML document already in memory and return its root element.

    ``source_file`` is optional context for error messages (e.g. when
    the string was read from a file by the caller for another reason).
    """
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ParsingError(
            "content is not well-formed XML",
            context=ErrorContext(source_file=source_file, extra={"parse_error": str(exc)}),
        ) from exc


def get_attr(
    element: Element,
    name: str,
    *,
    required: bool = True,
    default: str | None = None,
    source_file: str | None = None,
) -> str | None:
    """Return ``element``'s ``name`` attribute as a string.

    Parameters
    ----------
    required:
        If ``True`` (default) and the attribute is missing, raises
        ``ParsingError``. If ``False``, returns ``default`` instead.
    """
    value = element.get(name)
    if value is not None:
        return value
    if required:
        raise ParsingError(
            f"missing required XML attribute {name!r}",
            context=ErrorContext(source_file=source_file, field=name, xml_node=element.tag),
        )
    return default


def get_attr_float(
    element: Element,
    name: str,
    *,
    required: bool = True,
    default: float | None = None,
    source_file: str | None = None,
) -> float | None:
    """Return ``element``'s ``name`` attribute parsed as a float.

    Raises ``ParsingError`` (chained from the original ``ValueError``)
    if the attribute is present but not a valid float.
    """
    raw = get_attr(element, name, required=required, default=None, source_file=source_file)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ParsingError(
            f"XML attribute {name!r} is not a valid float: {raw!r}",
            context=ErrorContext(source_file=source_file, field=name, xml_node=element.tag),
        ) from exc


def get_attr_int(
    element: Element,
    name: str,
    *,
    required: bool = True,
    default: int | None = None,
    source_file: str | None = None,
) -> int | None:
    """Return ``element``'s ``name`` attribute parsed as an int.

    Raises ``ParsingError`` (chained from the original ``ValueError``)
    if the attribute is present but not a valid integer.
    """
    raw = get_attr(element, name, required=required, default=None, source_file=source_file)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ParsingError(
            f"XML attribute {name!r} is not a valid int: {raw!r}",
            context=ErrorContext(source_file=source_file, field=name, xml_node=element.tag),
        ) from exc


def iter_children(element: Element, tag: str) -> list[Element]:
    """Return ``element``'s direct children matching ``tag``, as a list.

    A plain list (not a lazy iterator) so callers can safely check
    ``len(...)`` or iterate more than once, which is the common case
    when validating a format ("must have at least one <node> child").
    """
    return element.findall(tag)


def write_file(path: str, root: Element, *, pretty: bool = True, encoding: str = "utf-8") -> None:
    """Write ``root`` (and its tree) to ``path`` as an XML document.

    Parameters
    ----------
    pretty:
        If ``True`` (default), indent the output for human readability
        using :func:`xml.etree.ElementTree.indent`. Set to ``False`` to
        write the most compact form, e.g. for round-trip byte-diffing
        against a source file during format verification.
    """
    if pretty:
        ET.indent(root)
    tree = ET.ElementTree(root)
    directory = Path(path).parent
    if str(directory) and not directory.exists():
        raise ParsingError(
            "output directory does not exist",
            context=ErrorContext(source_file=path, extra={"directory": str(directory)}),
        )
    try:
        tree.write(path, encoding=encoding, xml_declaration=True)
    except OSError as exc:
        raise ParsingError(
            "could not write XML file",
            context=ErrorContext(source_file=path, extra={"os_error": str(exc)}),
        ) from exc
