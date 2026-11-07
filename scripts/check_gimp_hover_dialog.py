"""Run inside GIMP's batch interpreter with the installed plugin, SAM and ViTMatte."""

import os
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

PANEL = os.environ.get("OBJECT_SELECT_PANEL", "/tmp/object-select-panel.png")
# Point this at a photo to see the real workflow. Its center pixel is hovered and clicked.
photo = os.environ.get("OBJECT_SELECT_CHECK_IMAGE")
if photo:
    source = Image.open(photo).convert("RGBA")
else:
    source = Image.new("RGBA", (256, 192), "#d0c9b0")
    ImageDraw.Draw(source).ellipse((65, 35, 190, 165), fill="#386d67")
WIDTH, HEIGHT = source.size
GimpUi.init("object-select-hover-check")
image = Gimp.Image.new(WIDTH, HEIGHT, Gimp.ImageBaseType.RGB)
layer = Gimp.Layer.new(
    image, "Hover check", WIDTH, HEIGHT, Gimp.ImageType.RGBA_IMAGE, 100, Gimp.LayerMode.NORMAL
)
image.insert_layer(layer, None, 0)
buffer = layer.get_buffer()
buffer.set(Gegl.Rectangle.new(0, 0, WIDTH, HEIGHT), "R'G'B'A u8", source.tobytes())
buffer.flush()
dialog = None
errors = []
phase = "ready"
started = time.monotonic()
shown_at = None
shown_mask = None
before_refine = None
crisp_mask = None
coarse_mask = None
picked_at = None
picked_index = None
replies = []


def walk(widget):
    yield widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield from walk(child)


def tick():
    global phase, shown_at, shown_mask, before_refine, crisp_mask
    global coarse_mask, picked_at, picked_index
    try:
        if time.monotonic() - started > 240:
            raise AssertionError("Dialog timeout: " + dialog.status.get_text())
        if phase == "ready" and dialog.ready:
            assert dialog.bridge.serial == 1, "Startup should only load the model and image"
            assert replies[0][0]["op"] == "warmup" and "masks" not in replies[0][1]
            frames = [
                widget.get_label()
                for widget in walk(dialog)
                if isinstance(widget, Gtk.Frame) and widget.get_label()
            ]
            assert frames == ["Tool", "Candidates", "Picks", "Edges", "Model"], frames
            expanders = [widget for widget in walk(dialog) if isinstance(widget, Gtk.Expander)]
            assert len(expanders) == 2 and all(not widget.get_expanded() for widget in expanders)
            assert dialog.auto_refine.get_active(), "Picks must refine their edges by default"
            assert dialog.matte_output.get_active_id() == "soft"
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
            assert replies[-1][1]["preview"], "Hover must not transfer full-size masks"
            assert dialog.candidate.size == dialog.source.size
            assert dialog.selection.mask.getbbox() is None
            shown_at, shown_mask = time.monotonic(), dialog.candidate
            phase = "stable"
        elif phase == "stable" and time.monotonic() - shown_at > 0.5:
            assert dialog.candidate is shown_mask and dialog.bridge.serial == 2
            surface = cairo.ImageSurface(
                cairo.FORMAT_ARGB32, dialog.get_allocated_width(), dialog.get_allocated_height()
            )
            Gtk.Widget.draw(dialog, cairo.Context(surface))
            surface.write_to_png(PANEL)
            print("PASS: real SAM preview remains stable with one request", flush=True)
            picked_at, picked_index = dialog.hover_point, dialog.candidate_index
            dialog.commit_candidate("new")
            assert dialog.locked and dialog.ghost is shown_mask, "Show the pick while it refines"
            phase = "picked"
        elif phase == "picked" and not dialog.pending:
            assert replies[-1][0]["op"] == "pick" and "masks" in replies[-1][1], replies[-1][0]
            before_refine = dialog.selection.mask.copy()
            assert before_refine.size == dialog.source.size and before_refine.getbbox()
            selected = sum(before_refine.histogram()[128:])
            assert selected > 0.05 * before_refine.width * before_refine.height, selected
            surface = cairo.ImageSurface(
                cairo.FORMAT_ARGB32, dialog.get_allocated_width(), dialog.get_allocated_height()
            )
            dialog.pointer = None
            Gtk.Widget.draw(dialog, cairo.Context(surface))
            surface.write_to_png(PANEL.replace(".png", "-picked.png"))
            print(f"PASS: real click refined into a selection of {selected} pixels", flush=True)
            dialog.matte_output.set_active_id("crisp")
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
            dialog.auto_refine.set_active(False)
            dialog.predict(picked_at, commit=True, mode="new", candidate=picked_index)
            phase = "coarse"
        elif phase == "coarse" and not dialog.pending:
            assert dialog.selection.unrefined() == 1
            assert dialog.refine_all_button.get_sensitive()
            coarse_mask = dialog.selection.mask.copy()
            dialog.refine_all()
            assert dialog.locked and not dialog.refine_all_button.get_sensitive()
            phase = "all"
        elif phase == "all" and not dialog.pending:
            assert dialog.selection.unrefined() == 0, dialog.status.get_text()
            assert not dialog.refine_all_button.get_sensitive()
            assert dialog.selection.mask.tobytes() == before_refine.tobytes(), (
                "Refining later must match refining at the click"
            )
            dialog.undo()
            assert dialog.selection.mask.tobytes() == coarse_mask.tobytes()
            print("PASS: Refine all matches refining at the click, and Undo restores", flush=True)
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
        .get(Gegl.Rectangle.new(0, 0, WIDTH, HEIGHT), 1.0, "Y u8", Gegl.AbyssPolicy.NONE)
    )
    assert set(bytes(applied)) == {0, 255}
    print("PASS: crisp refinement applied to actual GIMP selection", flush=True)
finally:
    if dialog and not dialog.closed:
        dialog.destroy()
    image.delete()
