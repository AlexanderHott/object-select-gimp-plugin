"""Run inside GIMP's batch interpreter with the installed plugin, SAM and ViTMatte."""

import sys
import time
import traceback
from types import SimpleNamespace

import cairo
import gi

gi.require_version("Gimp", "3.0")
gi.require_version("GimpUi", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gegl", "0.4")
from gi.repository import Gegl, Gimp, GimpUi, GLib, Gtk

sys.path.append(Gimp.directory() + "/plug-ins/object-select/vendor")
from PIL import Image, ImageDraw

sys.path.insert(0, Gimp.directory() + "/plug-ins/object-select")
from ui import HoverDialog

GimpUi.init("object-select-hover-check")
image = Gimp.Image.new(256, 192, Gimp.ImageBaseType.RGB)
layer = Gimp.Layer.new(
    image, "Hover check", 256, 192, Gimp.ImageType.RGBA_IMAGE, 100, Gimp.LayerMode.NORMAL
)
image.insert_layer(layer, None, 0)
source = Image.new("RGBA", (256, 192), "#d0c9b0")
ImageDraw.Draw(source).ellipse((65, 35, 190, 165), fill="#386d67")
buffer = layer.get_buffer()
buffer.set(Gegl.Rectangle.new(0, 0, 256, 192), "R'G'B'A u8", source.tobytes())
buffer.flush()
dialog = None
errors = []
phase = "ready"
started = time.monotonic()
shown_at = None
shown_mask = None
before_refine = None
crisp_mask = None
replies = []


def walk(widget):
    yield widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield from walk(child)


def tick():
    global phase, shown_at, shown_mask, before_refine, crisp_mask
    try:
        if time.monotonic() - started > 180:
            raise AssertionError("Dialog timeout: " + dialog.status.get_text())
        if phase == "ready" and dialog.ready:
            assert dialog.bridge.serial == 1, "Startup should only load the model and image"
            assert replies[0][0]["op"] == "warmup" and "masks" not in replies[0][1]
            frames = [
                widget.get_label()
                for widget in walk(dialog)
                if isinstance(widget, Gtk.Frame) and widget.get_label()
            ]
            assert frames == ["Tool", "Candidates", "Picks", "Model"], frames
            expanders = [widget for widget in walk(dialog) if isinstance(widget, Gtk.Expander)]
            assert len(expanders) == 2 and all(not widget.get_expanded() for widget in expanders)
            assert dialog.matte_output.get_active_id() == "crisp"
            assert dialog.matte_threshold.get_value() == 50
            dialog.mode.highlight("add")
            assert dialog.mode.get_active_id() == "new", "Highlighting must not change the mode"
            dialog.mode.highlight("new")
            dialog.matte_output.set_active_id("soft")
            assert not dialog.matte_threshold.is_sensitive()
            dialog.matte_output.set_active_id("crisp")
            assert dialog.matte_threshold.is_sensitive()
            print(
                "PASS: startup loads image features without regions; sidebar follows workflow",
                flush=True,
            )
            dialog.motion(
                None,
                SimpleNamespace(
                    x=dialog.area.get_allocated_width() / 2,
                    y=dialog.area.get_allocated_height() / 2,
                    state=0,
                ),
            )
            phase = "hover"
        elif phase == "hover" and dialog.candidate is not None:
            assert dialog.bridge.serial == 2, (phase, dialog.bridge.serial, dialog.pointer)
            assert replies[-1][1]["cached"], "First hover must reuse the preloaded image features"
            assert dialog.selection.mask.getbbox() is None
            shown_at, shown_mask = time.monotonic(), dialog.candidate
            phase = "stable"
        elif phase == "stable" and time.monotonic() - shown_at > 0.5:
            assert dialog.candidate is shown_mask and dialog.bridge.serial == 2
            surface = cairo.ImageSurface(
                cairo.FORMAT_ARGB32, dialog.get_allocated_width(), dialog.get_allocated_height()
            )
            Gtk.Widget.draw(dialog, cairo.Context(surface))
            surface.write_to_png("/tmp/object-select-panel.png")
            print("PASS: real SAM preview remains stable with one request", flush=True)
            dialog.accept(dialog.candidate, "new", dialog.hover_point)
            before_refine = dialog.selection.mask.copy()
            dialog.refine()
            phase = "crisp"
        elif phase == "crisp" and not dialog.pending:
            assert "Crisp boundary refined" in dialog.status.get_text(), dialog.status.get_text()
            crisp_mask = dialog.selection.mask.copy()
            assert set(crisp_mask.tobytes()) == {0, 255}
            dialog.undo()
            assert dialog.selection.mask.tobytes() == before_refine.tobytes()
            print("PASS: real ViTMatte crisp output and Undo", flush=True)
            dialog.matte_output.set_active_id("soft")
            dialog.refine()
            phase = "soft"
        elif phase == "soft" and not dialog.pending:
            assert "Soft matte refined" in dialog.status.get_text(), dialog.status.get_text()
            assert any(0 < value < 255 for value in dialog.selection.mask.tobytes())
            dialog.undo()
            assert dialog.selection.mask.tobytes() == before_refine.tobytes()
            print("PASS: real ViTMatte soft coverage and Undo", flush=True)
            dialog.accept(crisp_mask, "new", None)
            dialog.response(Gtk.ResponseType.OK)
            return False
    except Exception as error:  # noqa: BLE001 - report callback failures after closing the dialog
        traceback.print_exc()
        errors.append(repr(error))
        dialog.response(Gtk.ResponseType.CANCEL)
        return False
    return True


try:
    dialog = HoverDialog(image, layer)
    # Drive pointer events explicitly so physical mouse movement cannot interrupt the check.
    dialog.area.handler_block_by_func(dialog.motion)
    dialog.area.handler_block_by_func(dialog.leave)
    submit = dialog.bridge.submit

    def record_submit(request, callback):
        def finished(result):
            replies.append((request, result))
            callback(result)

        return submit(request, finished)

    dialog.bridge.submit = record_submit
    GLib.timeout_add(50, tick)
    assert dialog.run_selection(), errors
    assert not Gimp.Selection.is_empty(image)
    applied = (
        image.get_selection()
        .get_buffer()
        .get(Gegl.Rectangle.new(0, 0, 256, 192), 1.0, "Y u8", Gegl.AbyssPolicy.NONE)
    )
    assert set(bytes(applied)) == {0, 255}
    print("PASS: crisp refinement applied to actual GIMP selection", flush=True)
finally:
    if dialog and not dialog.closed:
        dialog.destroy()
    image.delete()
