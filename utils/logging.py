# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Logging factory for the EXMeditor.

Every module in ``core``, ``formats``, and ``blender_io`` should log
through :func:`get_logger` instead of calling ``print`` or the
:mod:`logging` module directly. Centralizing this here means the log
format, verbosity, and destination can be changed in one place later
(e.g. write-to-file for bug reports, verbosity from
``addon/preferences.py``) without touching every module that logs.

Design notes
------------
- This module does **not** import ``bpy``. It is used by ``core`` and
  ``formats`` code, which must stay importable outside Blender (see the
  architecture doc's "no bpy in core/formats" rule). Instead, it exposes
  a small handler class that ``addon/operators.py`` can attach for the
  duration of an operator run, forwarding warnings/errors to
  ``operator.report(...)``. That keeps the bpy dependency confined to
  the one place that actually has an operator instance.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

# Root logger name for the whole add-on. Every module logger is a child
# of this one (e.g. "exmeditor.formats.exm.terrain"), so a single
# `logging.getLogger("exmeditor").setLevel(...)` call controls
# verbosity for the entire SDK.
_ROOT_LOGGER_NAME = "exmeditor"

_DEFAULT_FORMAT = "[%(name)s] %(levelname)s: %(message)s"

_configured = False


def _ensure_configured() -> None:
    """Attach a console handler to the root SDK logger exactly once.

    Blender's console already captures stdout, so a plain
    ``StreamHandler`` is enough to make SDK log output visible there
    without any Blender-specific code in this module.
    """
    global _configured
    if _configured:
        return

    root = logging.getLogger(_ROOT_LOGGER_NAME)
    root.setLevel(logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
    root.addHandler(handler)

    # This SDK manages its own handler; don't also duplicate output
    # through Python's default root logger.
    root.propagate = False

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger for ``name``, nested under the SDK's root logger.

    Parameters
    ----------
    name:
        Typically ``__name__`` of the calling module, e.g.
        ``"formats.exm.terrain"``. The SDK root prefix is added
        automatically, so callers never need to know it.
    """
    _ensure_configured()
    if name.startswith(_ROOT_LOGGER_NAME):
        full_name = name
    else:
        full_name = f"{_ROOT_LOGGER_NAME}.{name}"
    return logging.getLogger(full_name)


def set_verbosity(level: int) -> None:
    """Set the log level for the entire SDK at once.

    Intended to be called from ``addon/preferences.py`` when the user
    changes a "log level" preference. Accepts standard :mod:`logging`
    level constants (``logging.DEBUG``, ``logging.INFO``, ...).
    """
    _ensure_configured()
    logging.getLogger(_ROOT_LOGGER_NAME).setLevel(level)


class OperatorReportHandler(logging.Handler):
    """A :mod:`logging` handler that forwards records to a Blender operator.

    This is the *only* bridge between this module and Blender, and it
    takes the report function as a plain callable rather than importing
    ``bpy`` itself — so ``utils/logging.py`` stays usable outside
    Blender, while ``addon/operators.py`` gets SDK log messages surfaced
    in the Blender UI for free.

    Intended usage from an operator::

        logger = get_logger("formats.exm.terrain")
        handler = OperatorReportHandler(self.report)
        logger.addHandler(handler)
        try:
            ...  # do the import/export
        finally:
            logger.removeHandler(handler)

    Parameters
    ----------
    report_fn:
        A callable matching ``bpy.types.Operator.report``'s signature:
        ``report_fn(level_set: set[str], message: str) -> None``.
    min_level:
        Only records at or above this :mod:`logging` level are
        forwarded, so e.g. ``DEBUG``-level parsing chatter doesn't spam
        the Blender status bar.
    """

    # Maps Python logging levels to Blender's report() level strings.
    _LEVEL_MAP: dict[int, str] = {
        logging.DEBUG: "INFO",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "ERROR",
    }

    def __init__(
        self,
        report_fn: Callable[[set[str], str], None],
        min_level: int = logging.WARNING,
    ) -> None:
        super().__init__(level=min_level)
        self._report_fn = report_fn

    def emit(self, record: logging.LogRecord) -> None:
        # logging.Handler.emit() must never let an exception escape, or
        # it can break the logging call site it was attached to import
        # from — swallow failures here and fall back to the base
        # class's own error handling instead of re-raising.
        try:
            blender_level = self._LEVEL_MAP.get(record.levelno, "INFO")
            self._report_fn({blender_level}, self.format(record))
        except Exception:  # noqa: BLE001 - logging handlers must not raise
            self.handleError(record)
