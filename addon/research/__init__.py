# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""Research tools: the measurement operators this editor was built with.

Censuses, forensics, coverage and resolution reports — they describe
the game's files rather than edit a map, and print long reports to
the system console. They live in the source repository and are **not
part of the install archive**: ``build_release.py`` leaves this package
and the ``core``/``blender_io`` modules only it uses out, and the
add-on's ``__init__`` registers them only when the package is present.
Running from a source checkout gives the *Research* sub-panel and the
*Show Research Tools* preference; an installed release has neither.
"""

from __future__ import annotations

from addon.research import (
    audit_operator,
    census_operator,
    coverage_operator,
    diagnostics_operator,
    forensics_operator,
    format_census_operator,
    inspector_operator,
    panel,
    registration_operator,
)

#: Registration order; unregistered in reverse.
_MODULES = (
    diagnostics_operator,
    forensics_operator,
    inspector_operator,
    format_census_operator,
    registration_operator,
    audit_operator,
    coverage_operator,
    census_operator,
    panel,
)


def register() -> None:
    for module in _MODULES:
        module.register()


def unregister() -> None:
    for module in reversed(_MODULES):
        module.unregister()
