#!/usr/bin/env python3
"""GIMP entry point for hover-and-pick object selection."""

import sys

import gi

gi.require_version("Gimp", "3.0")
gi.require_version("GimpUi", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gegl, Gimp, GimpUi, GLib

PROCEDURE = "plug-in-object-select-hover"


def run(procedure, run_mode, image, drawables, config, run_data):
    try:
        if run_mode != Gimp.RunMode.INTERACTIVE:
            raise ValueError("Object Select requires an interactive GIMP session.")
        if len(drawables) != 1 or not isinstance(drawables[0], Gimp.Layer):
            raise ValueError("Select exactly one pixel layer first.")
        layer = drawables[0]
        if layer.is_group():
            raise ValueError("Select a pixel layer rather than a layer group.")
        Gegl.init(None)
        GimpUi.init("object-select")
        from ui import HoverDialog

        applied = HoverDialog(image, layer).run_selection()
        status = Gimp.PDBStatusType.SUCCESS if applied else Gimp.PDBStatusType.CANCEL
        return procedure.new_return_values(status, None)
    except Exception as error:  # noqa: BLE001 - report failures at the process/UI boundary
        return procedure.new_return_values(
            Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error(str(error))
        )


class ObjectSelect(Gimp.PlugIn):
    def do_set_i18n(self, procedure_name):
        return False, None, None

    def do_query_procedures(self):
        return [PROCEDURE]

    def do_create_procedure(self, name):
        if name != PROCEDURE:
            return None
        procedure = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN, run, None)
        procedure.set_image_types("RGB*, GRAY*")
        procedure.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.DRAWABLE)
        procedure.set_menu_label("_Object Select…")
        procedure.add_menu_path("<Image>/Select")
        procedure.set_documentation(
            "Hover over objects and click to select them",
            "Select objects from the active layer in an interactive preview using local SAM models.",
            name,
        )
        procedure.set_attribution(
            "Object Select contributors", "Object Select contributors", "2026"
        )
        return procedure


Gimp.main(ObjectSelect.__gtype__, sys.argv)
