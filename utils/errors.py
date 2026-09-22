# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Error hierarchy for the EXMeditor.

Every exception raised by ``core``, ``formats``, or ``blender_io`` should
be one of the types defined here (or a subclass added later). This keeps
error handling in ``addon/operators.py`` uniform: catch
``EXMeditorError``, read its structured context, and report it to the
user without needing to know which subsystem raised it.

Design notes
------------
- Context is a typed ``ErrorContext`` dataclass, not a bare ``dict``, so
  IDEs/type checkers can help at call sites and the common fields (which
  file, which byte offset, which XML node, which plugin, which asset)
  don't need to be re-invented as string keys in every call site. An
  ``extra`` dict remains for anything that doesn't fit a named field, so
  callers are never blocked from attaching something ad hoc.
- ``error_code`` is optional and machine-readable (e.g. ``"EXM101"``),
  meant for support/debugging shorthand ("what does EXM104 mean?") once
  the SDK has enough call sites for codes to be worth assigning. It is
  intentionally *not* required — most call sites won't have a code yet,
  and a required field would force premature numbering.
- Exception chaining: every place in the SDK that catches a lower-level
  exception (``struct.error``, ``xml.etree.ElementTree.ParseError``, ...)
  and re-raises one of these should use ``raise ParsingError(...) from e``
  so the original traceback is preserved as ``__cause__``. That's a
  standard Python mechanism and needs no special support from this
  module — the convention is documented here since this is the module
  every such call site imports from.
- This module has zero dependencies (not even on the rest of the SDK),
  so it can be imported from anywhere, including ``bpy``-free contexts
  such as unit tests or a future CLI tool.

Error code ranges (reference only, assigned at call sites as the SDK
grows — not enforced by this module):

    EXM0xx  generic / SDK-level errors
    EXM1xx  parsing errors            (formats/*)
    EXM2xx  unsupported-format errors (formats/*)
    EXM3xx  validation errors         (core/*, before a write)
    EXM4xx  plugin-system errors      (formats.plugin / formats.registry)
"""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass
class ErrorContext:
    """Structured, extensible context attached to an :class:`EXMeditorError`.

    The named fields cover the situations that come up constantly when
    working with reverse-engineered binary/XML formats. Anything that
    doesn't fit one of them goes in ``extra`` instead of forcing a new
    field onto every error site.

    All fields are optional — construct with only the ones that apply,
    e.g. ``ErrorContext(source_file="displace.bin", offset=2048)``.

    Note: the ``field`` attribute below (meaning "which struct/XML field
    was involved") intentionally shares its name with
    ``dataclasses.field``. That's why this module imports ``dataclasses``
    itself rather than doing ``from dataclasses import field`` — the
    bare name ``field`` would be shadowed by this very attribute while
    Python evaluates the rest of the class body.
    """

    source_file: str | None = None
    offset: int | None = None
    field: str | None = None
    plugin: str | None = None
    asset: str | None = None
    xml_node: str | None = None
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return every populated field (named + extra) as a flat dict.

        Used by :meth:`EXMeditorError._format` to render context, and
        available for anything that wants to serialize an error (e.g. a
        future bug-report log sink).
        """
        result: dict[str, Any] = {}
        for name in ("source_file", "offset", "field", "plugin", "asset", "xml_node"):
            value = getattr(self, name)
            if value is not None:
                result[name] = value
        result.update(self.extra)
        return result


class EXMeditorError(Exception):
    """Base class for every error raised by the EXMeditor.

    Parameters
    ----------
    message:
        Human-readable description of what went wrong. This is what
        ends up in ``operator.report({'ERROR'}, ...)``, so it should be
        understandable without looking at the traceback.
    error_code:
        Optional machine-readable code (e.g. ``"EXM101"``), see the
        module-level "Error code ranges" reference above. Leave unset
        until a call site has earned a stable code.
    context:
        Structured :class:`ErrorContext` with the details of what was
        being processed. Subclasses below accept the common fields
        directly as keyword arguments for convenience (e.g.
        ``ParsingError(msg, source_file=..., offset=...)``) and build
        the ``ErrorContext`` internally.

    To preserve the original traceback when re-raising a lower-level
    exception, use Python's standard chaining syntax::

        try:
            struct.unpack(fmt, data)
        except struct.error as e:
            raise ParsingError(
                "heightmap payload is not a multiple of float32 size",
                source_file=path,
            ) from e
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        context: ErrorContext | None = None,
    ) -> None:
        self.message = message
        self.error_code = error_code
        self.context: ErrorContext = context if context is not None else ErrorContext()
        super().__init__(self._format())

    @property
    def source_file(self) -> str | None:
        """Convenience accessor mirroring ``self.context.source_file``."""
        return self.context.source_file

    def _format(self) -> str:
        """Build the full exception text, including context, once.

        Kept separate from ``__str__`` so subclasses that accept their
        own named keyword arguments (see ``ParsingError`` below) can
        assemble the ``ErrorContext`` *before* calling
        ``super().__init__()`` and still get a single consistent
        rendering here.
        """
        parts = [self.message]
        if self.error_code:
            parts.append(f"[{self.error_code}]")
        for key, value in self.context.as_dict().items():
            parts.append(f"{key}={value!r}")
        return " | ".join(parts)


#: The name this class had before 0.52.0, kept so that code written
#: against the old name keeps importing and catching it.
ExMachinaSDKError = EXMeditorError


class ParsingError(EXMeditorError):
    """Raised when a file's bytes/XML do not match the expected structure.

    Use this for anything that indicates the input is malformed *given
    what we currently believe the format to be* — a wrong magic number,
    an array length that doesn't match the file size, a required XML
    attribute that's missing. Use ``UnsupportedFormatError`` instead when
    the input is well-formed but describes a variant we don't (yet)
    support.
    """

    def __init__(
        self,
        message: str,
        *,
        source_file: str | None = None,
        offset: int | None = None,
        field: str | None = None,
        error_code: str | None = None,
        context: ErrorContext | None = None,
    ) -> None:
        ctx = context if context is not None else ErrorContext()
        if source_file is not None:
            ctx.source_file = source_file
        if offset is not None:
            ctx.offset = offset
        if field is not None:
            ctx.field = field
        super().__init__(message, error_code=error_code, context=ctx)


class UnsupportedFormatError(EXMeditorError):
    """Raised when the input is well-formed but not (yet) supported.

    Examples: a ``displace.bin`` whose sample count isn't a perfect
    square (it's confirmed to always be a square grid, but not a fixed
    size — see ``formats/exm/terrain.py``), a
    ``world.xml`` produced by a game/mod version this SDK hasn't been
    taught about yet, or a deliberate "not implemented yet" stub for a
    format that's still being reverse-engineered (e.g. SGO meshes, or
    rotation-encoding conversion in ``utils/math.py`` until confirmed).
    Distinguishing this from ``ParsingError`` matters for the UI message:
    "this file is corrupt" vs. "this SDK doesn't handle this variant yet"
    are very different things to tell a user.
    """

    def __init__(
        self,
        message: str,
        *,
        source_file: str | None = None,
        error_code: str | None = None,
        context: ErrorContext | None = None,
    ) -> None:
        ctx = context if context is not None else ErrorContext()
        if source_file is not None:
            ctx.source_file = source_file
        super().__init__(message, error_code=error_code, context=ctx)


class ValidationError(EXMeditorError):
    """Raised when in-memory data fails a domain rule before I/O.

    Use this for checks that happen *before* writing a file, e.g.
    "terrain grid must be square to export", or "object
    references an asset_id with no entry in the AssetRegistry". Raising
    this ahead of the binary/XML writer is what keeps a bad export from
    ever touching disk (see the terrain export data flow in the
    architecture doc).
    """


class PluginError(EXMeditorError):
    """Raised by the plugin system (`formats.plugin`, `formats.registry`).

    Covers: no plugin registered under a given name, no active plugin
    set, or a plugin's own ``load``/``save``/``scan``/``validate``
    raising something unexpected that this SDK wants to normalize into
    its own error hierarchy before it reaches ``addon/operators.py``.
    """

    def __init__(
        self,
        message: str,
        *,
        plugin: str | None = None,
        error_code: str | None = None,
        context: ErrorContext | None = None,
    ) -> None:
        ctx = context if context is not None else ErrorContext()
        if plugin is not None:
            ctx.plugin = plugin
        super().__init__(message, error_code=error_code, context=ctx)
