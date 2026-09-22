# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Registry of available ``GamePlugin`` implementations.

``addon/`` code calls ``default_registry.get_active()`` and never
imports ``formats.exm`` (or any other game package) directly. The
active plugin is registered explicitly by the add-on's top-level
``register()`` (see ``EXMeditor/__init__.py``), not as an import-time
side effect, so registration order/timing stays predictable and this
module stays trivially testable outside Blender.
"""

from __future__ import annotations

from typing import Iterator

from formats.plugin import GamePlugin
from utils.errors import ErrorContext, PluginError


class PluginRegistry:
    """Holds registered ``GamePlugin`` instances and tracks the active one."""

    def __init__(self) -> None:
        self._plugins: dict[str, GamePlugin] = {}
        self._active_name: str | None = None

    def register(self, plugin: GamePlugin) -> None:
        """Register ``plugin``. The first plugin registered becomes active
        automatically; call ``set_active()`` explicitly to change that."""
        self._plugins[plugin.name] = plugin
        if self._active_name is None:
            self._active_name = plugin.name

    def get(self, name: str) -> GamePlugin:
        try:
            return self._plugins[name]
        except KeyError as exc:
            raise PluginError(
                f"no plugin registered under {name!r}",
                plugin=name,
                context=ErrorContext(extra={"known_plugins": list(self._plugins)}),
            ) from exc

    def get_active(self) -> GamePlugin:
        if self._active_name is None:
            raise PluginError("no active plugin set (none registered yet)")
        return self.get(self._active_name)

    def set_active(self, name: str) -> None:
        if name not in self._plugins:
            raise PluginError(
                f"cannot activate unknown plugin {name!r}",
                plugin=name,
                context=ErrorContext(extra={"known_plugins": list(self._plugins)}),
            )
        self._active_name = name

    def __iter__(self) -> Iterator[GamePlugin]:
        return iter(self._plugins.values())

    def __len__(self) -> int:
        return len(self._plugins)


# Module-level singleton used by the add-on. A plain global is
# acceptable here because Blender loads one instance of this add-on
# per session; the top-level `EXMeditor/__init__.py` populates it
# in `register()` (not at import time — see module docstring).
default_registry = PluginRegistry()
