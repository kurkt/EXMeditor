# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The plugin contract every game-format backend implements.

``addon/`` talks only to ``formats.registry.default_registry`` /
``get_active()``, never to ``formats.exm.*`` directly — this is what
lets a future ``formats/cnc3/`` (or any other game) plug in without
touching ``addon/operators.py``. See the architecture doc's plugin
system decision.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.scene import MapScene


class GamePlugin(ABC):
    """Common interface for a single game's file-format backend.

    Attributes
    ----------
    name:
        Human-readable, unique plugin name (e.g. ``"Ex Machina"``),
        used as the key in ``PluginRegistry``.
    format_version:
        The file-format version this plugin targets, e.g. an eventual
        ``"EXM_103"``. ``"UNKNOWN"`` until format versioning is
        actually confirmed to matter (see the architecture doc's
        deferred-roadmap note on format versioning) — present as a
        field now so a version check can be added later without a
        signature change.
    """

    name: str
    format_version: str

    @abstractmethod
    def scan(self, folder: str) -> dict[str, bool]:
        """Report which of this game's recognized files exist in ``folder``.

        Must not raise for a folder that simply doesn't contain some
        (or any) of the recognized files — an empty/partial result is
        valid; ``load()`` is what turns missing files into warnings.
        """

    @abstractmethod
    def load(self, folder: str) -> MapScene:
        """Read every recognized, *supported* file in ``folder`` into one ``MapScene``.

        Missing or not-yet-supported components are recorded in the
        returned ``MapScene.warnings`` rather than raised, so a
        partially-supported map (e.g. terrain confirmed, objects not
        yet parseable) still produces a usable scene.
        """

    @abstractmethod
    def save(self, scene: MapScene, folder: str) -> None:
        """Write ``scene`` back out in this game's format(s).

        May raise ``NotImplementedError`` for components this plugin
        doesn't support writing yet — callers should call ``validate()``
        first to get a clean list of what would fail, rather than
        relying on catching this.
        """

    @abstractmethod
    def validate(self, scene: MapScene) -> list[str]:
        """Return human-readable problems that would prevent ``save()``.

        Returns an empty list if ``scene`` is writable as-is.
        """
