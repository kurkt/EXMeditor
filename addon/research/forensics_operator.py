# SPDX-FileCopyrightText: 2026 Kurkt
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Model Forensics operator.

Sits with the other diagnostics: read-only, prints to the system
console, changes nothing. It answers the question the other four
cannot — not "why is this model missing" but "why does this model,
which loads, not draw properly".

Two modes from one button. With only a subject file it dumps that
model's structure; with a reference file as well it reports only where
the two disagree. The second is the one that matters: a dump of a
broken file looks entirely plausible on its own, and it is the
comparison against a file the editor renders that turns a plausible
dump into a located difference.
"""

from __future__ import annotations

import os

import bpy
from bpy.props import StringProperty

from core.gam_forensics import (
    compare,
    deviations,
    describe,
    format_comparison,
    format_report,
    measure_population,
    survey,
)
from utils.errors import EXMeditorError
from utils.logging import get_logger

logger = get_logger("addon.forensics")  # errors only; report output goes to print()


class EXM_OT_model_forensics(bpy.types.Operator):
    """Dump a model's structure, or diff it against one that works."""

    bl_idname = "exmachina.model_forensics"
    bl_label = "Model Forensics"
    bl_description = (
        "Report a .gam file's chunk table, undecoded mesh header fields and "
        "embedded strings — and, given a second file, exactly where the two "
        "differ"
    )
    # REGISTER *and* UNDO. Blender only offers the "Adjust Last
    # Operation" panel for an operator that pushed an undo step, so
    # with REGISTER alone the properties below are unreachable: the
    # operator runs on its defaults and the fields it tells the user to
    # fill in never appear anywhere. The step is harmless here — this
    # operator reads files and changes nothing.
    bl_options = {"REGISTER", "UNDO"}

    subject: StringProperty(
        name="Model",
        description="The .gam file under investigation",
        subtype="FILE_PATH",
    )

    folder: StringProperty(
        name="Folder",
        description=(
            "A folder of .gam files, searched recursively. On its own it "
            "reports one line per model. Set Model as well and it becomes "
            "the reference population: point it at the game's models folder "
            "to measure a candidate against every model known to work"
        ),
        subtype="DIR_PATH",
    )

    reference: StringProperty(
        name="Compare With",
        description=(
            "Optional: a .gam the game's editor renders correctly. Given one, "
            "only the differences between the two files are reported"
        ),
        subtype="FILE_PATH",
    )

    #: NO ``invoke`` OVERRIDE, AND NOT BY OMISSION.
    #:
    #: An earlier version opened ``invoke_props_dialog``. Blender then
    #: crashed with EXCEPTION_ACCESS_VIOLATION in
    #: ``file_browse_exec -> RNA_property_string_set -> IDP_AddToGroup``
    #: the moment the browse button beside a FILE_PATH field was used:
    #: the file browser writes the chosen path back into the operator's
    #: properties after the popup that owns them has gone.
    #:
    #: Every other diagnostic here takes its paths through the "Adjust
    #: Last Operation" panel instead, which is a real region rather
    #: than a popup and where browsing is safe. This one does the same.

    def execute(self, context):
        folder = self._absolute(self.folder)
        subject_path = self._absolute(self.subject)
        if folder:
            if not os.path.isdir(folder):
                self.report({"ERROR"}, f"Not a folder: {folder}")
                return {"CANCELLED"}
            if subject_path and os.path.isfile(subject_path):
                self._against_population(subject_path, folder)
            else:
                self._survey(folder)
            return {"FINISHED"}

        subject = subject_path
        if not subject:
            # FINISHED, not CANCELLED. Blender only shows "Adjust Last
            # Operation" for an operator that finished, so cancelling
            # here hid the very fields this message points at — the
            # panel never appeared and the operator looked like it ran
            # itself with no way to aim it.
            self.report(
                {"INFO"},
                "Set Model (and optionally Compare With) in the 'Adjust Last "
                "Operation' panel at the bottom left, or press F9",
            )
            return {"FINISHED"}
        if not os.path.isfile(subject):
            self.report({"ERROR"}, f"No such file: {subject}")
            return {"CANCELLED"}

        reference = self._absolute(self.reference)
        if reference and not os.path.isfile(reference):
            self.report({"ERROR"}, f"No such file: {reference}")
            return {"CANCELLED"}

        try:
            if reference:
                self._compare(reference, subject)
            else:
                self._describe(subject)
        except EXMeditorError as exc:
            # A file that cannot be parsed is itself a finding, so the
            # message names which of the two failed rather than only
            # that something did.
            self.report({"ERROR"}, f"Forensics failed: {exc.message}")
            return {"CANCELLED"}

        return {"FINISHED"}

    @staticmethod
    def _absolute(path: str) -> str:
        """Resolve Blender's ``//`` relative paths, if bpy offers it."""
        if not path:
            return ""
        blender_path = getattr(bpy, "path", None)
        if blender_path is not None and hasattr(blender_path, "abspath"):
            return blender_path.abspath(path)
        return path

    def _emit(self, line: str) -> None:
        """One line to the system console.

        Only ``print`` — the logger writes to the same console inside
        Blender, so emitting to both would duplicate the whole report.
        Headline numbers go to the status bar via ``self.report``.
        """
        print(line)

    @staticmethod
    def _gam_files(folder: str) -> list[str]:
        """Recursive: the game keeps models in region and type subfolders."""
        found = []
        for root, _dirs, names in os.walk(folder):
            found += [
                os.path.join(root, name)
                for name in names
                if name.lower().endswith(".gam")
            ]
        return sorted(found)

    def _against_population(self, subject: str, folder: str) -> None:
        paths = [p for p in self._gam_files(folder) if p != subject]
        if not paths:
            self.report({"WARNING"}, f"No .gam files under {folder}")
            return

        population = measure_population(paths)
        findings = deviations(describe(subject), population)

        self._emit("")
        self._emit("=== Model Forensics: against known-good models ===")
        self._emit(
            f"Reference population: {population.count} readable model(s) "
            f"under {folder}"
        )
        if population.unreadable:
            self._emit(f"  ({len(population.unreadable)} could not be read)")
        self._emit("")
        for line in findings:
            self._emit(line)

        outliers = [f for f in findings if f.startswith("!")]
        self.report(
            {"WARNING" if outliers else "INFO"},
            f"{len(outliers)} deviation(s) from {population.count} known-good "
            "model(s) — full report in the console",
        )
        for line in outliers[:4]:
            self.report({"WARNING"}, line.lstrip("! "))

    def _survey(self, folder: str) -> None:
        paths = self._gam_files(folder)
        if not paths:
            self.report({"WARNING"}, f"No .gam files in {folder}")
            return

        self._emit("")
        self._emit("=== Model Forensics: survey ===")
        for line in survey(paths):
            self._emit(line)

        self.report(
            {"INFO"},
            f"Surveyed {len(paths)} model(s) — table in the console "
            "(Window > Toggle System Console)",
        )

    def _describe(self, path: str) -> None:
        report = describe(path)

        self._emit("")
        self._emit("=== Model Forensics ===")
        for line in format_report(report):
            self._emit(line)

        undecoded = [
            c.chunk_id for c in report.chunks if c.label == "UNDECODED"
        ]
        self._emit("")
        self._emit(
            "Run again with 'Compare With' set to a model the editor renders "
            "to see which of these fields differ."
        )

        self.report(
            {"INFO"},
            f"{len(report.meshes)} mesh(es), {len(report.chunks)} chunk(s) — "
            "full report in the console (Window > Toggle System Console)",
        )
        if undecoded:
            self.report(
                {"WARNING"},
                "undecoded chunk id(s): "
                + ", ".join(str(i) for i in sorted(set(undecoded))),
            )

    def _compare(self, reference: str, subject: str) -> None:
        result = compare(reference, subject)

        self._emit("")
        self._emit("=== Model Forensics: comparison ===")
        for line in format_comparison(result):
            self._emit(line)

        if result.identical:
            self.report(
                {"INFO"},
                "No structural difference — see the console for what that "
                "rules out",
            )
            return

        # Headline findings only. The status bar holds a few lines
        # before it scrolls, and burying "the skin chunk is missing"
        # under forty header offsets would waste the one place the user
        # is actually looking.
        headline = [line for line in result.differences if not line.startswith(" ")]
        self.report(
            {"WARNING"},
            f"{len(headline)} difference(s) — full report in the console "
            "(Window > Toggle System Console)",
        )
        for line in headline[:4]:
            self.report({"WARNING"}, line)


_CLASSES = (EXM_OT_model_forensics,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
