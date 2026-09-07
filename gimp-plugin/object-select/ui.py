"""GTK hover-and-pick interface. All GIMP calls stay on the main thread."""

import json
import math
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from bridge import Bridge, encode
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk
from gimp_io import apply, snapshot
from PIL import Image, ImageChops, ImageDraw, ImageFilter
from selection import Selection, image_coordinates, pick_candidate


class HoverDialog(Gtk.Dialog):
    def __init__(self, image, layer):
        super().__init__(title="Object Select", modal=True)
        self.set_default_size(1060, 730)
        self.image, self.layer = image, layer
        self.source = snapshot(layer)
        self.selection = Selection(self.source.size)
        self.origin = layer.get_offsets()[-2:]
        self.epoch = 0
        self.hover_timer = None
        self.candidate = None
        self.candidates = []
        self.candidate_index = 0
        self.pointer = None
        self.pointer_inside = False
        self.scroll_delta = 0.0
        self.hover_point = None
        self.drag = None
        self.locked = False
        self.pending = False
        self.ready = False
        self.image_ready = False
        self.warming = False
        self.closed = False
        self.revision = 0
        self.render_key = None
        self.current_request = 0
        self.last_seed = None
        self.build()
        settings = json.loads((Path(__file__).parent / "launcher.json").read_text())
        self.bridge = Bridge(settings, self.source, self.worker_ready, self.progress, self.failed)
        self.connect("key-press-event", self.key)
        self.connect("destroy", self.shutdown)

    def combo(self, parent, label, options):
        widget = Gtk.ComboBoxText()
        for key, text in options:
            widget.append(key, text)
        widget.set_active(0)
        parent.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
        parent.pack_start(widget, False, False, 0)
        return widget

    def button(self, parent, label, callback):
        widget = Gtk.Button(label=label)
        widget.connect("clicked", lambda _: callback())
        parent.pack_start(widget, False, False, 0)
        return widget

    def build(self):
        content = self.get_content_area()
        content.set_spacing(10)
        content.set_border_width(12)
        intro = Gtk.Label(
            label=f"{self.layer.get_name()} · Hover to preview, click to pick. "
            "Apply transfers your selection to GIMP.",
            xalign=0,
        )
        intro.set_line_wrap(True)
        content.pack_start(intro, False, False, 0)
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        content.pack_start(body, True, True, 0)
        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        controls.set_size_request(225, -1)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_width(250)
        scroll.add(controls)
        body.pack_start(scroll, False, False, 0)

        def section(title):
            frame = Gtk.Frame(label=title)
            group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            group.set_border_width(8)
            frame.add(group)
            controls.pack_start(frame, False, False, 3)
            return group

        def details(parent, title):
            expander = Gtk.Expander(label=title)
            group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            group.set_border_width(6)
            expander.add(group)
            parent.pack_start(expander, False, False, 0)
            return group

        setup = section("1. Set up")
        self.model = self.combo(
            setup,
            "Model",
            [
                ("sam2-large", "SAM 2.1 large"),
                ("sam2-base", "SAM 2.1 base+"),
                ("sam2-tiny", "SAM 2.1 tiny"),
                ("sam3", "SAM 3 · access required"),
            ],
        )
        picking = section("2. Pick objects")
        self.tool = self.combo(
            picking,
            "Tool",
            [
                ("hover", "Hover & pick"),
                ("box", "Draw a box"),
                ("include", "Include object / part"),
                ("exclude", "Exclude with a point"),
            ],
        )
        self.mode = self.combo(
            picking,
            "Selection mode",
            [("new", "New"), ("add", "Add"), ("subtract", "Subtract"), ("intersect", "Intersect")],
        )
        self.scope = self.combo(
            picking,
            "Candidate preference",
            [("object", "Best candidate"), ("part", "Smaller candidate")],
        )
        self.next_button = self.button(
            picking, "Next candidate · Tab / scroll", self.next_candidate
        )
        self.next_button.set_tooltip_text(
            "Hover over the preview and use Tab or scroll down. Shift+Tab or scroll up goes back."
        )
        actions = Gtk.Box(spacing=6)
        picking.pack_start(actions, False, False, 2)
        self.undo_button = self.button(actions, "Undo", self.undo)
        self.clear_button = self.button(actions, "Clear", self.clear)

        inspection = section("3. Inspect & clean up")
        self.view = self.combo(
            inspection,
            "Preview",
            [
                ("overlay", "Selection overlay"),
                ("cutout", "On transparency"),
                ("black", "On black"),
                ("white", "On white"),
                ("mask", "Black & white mask"),
            ],
        )
        self.zoom = self.combo(
            inspection,
            "Preview zoom",
            [("fit", "Fit image"), ("1", "100%"), ("2", "200%"), ("4", "400%")],
        )
        self.auto_cleanup = Gtk.CheckButton(label="Clean new candidates")
        self.auto_cleanup.set_active(True)
        inspection.pack_start(self.auto_cleanup, False, False, 0)
        cleanup = details(inspection, "Contour cleanup settings")
        cleanup.pack_start(
            Gtk.Label(label="Fill holes up to · square pixels", xalign=0), False, False, 0
        )
        self.hole_area = Gtk.SpinButton.new_with_range(0, 10000, 16)
        self.hole_area.set_value(64)
        self.hole_area.set_tooltip_text(
            "Fill small enclosed holes. Larger openings stay unselected."
        )
        cleanup.pack_start(self.hole_area, False, False, 0)
        cleanup.pack_start(
            Gtk.Label(label="Smooth contour · layer pixels", xalign=0), False, False, 0
        )
        self.smoothing = Gtk.SpinButton.new_with_range(0, 5, 1)
        self.smoothing.set_value(1)
        cleanup.pack_start(self.smoothing, False, False, 0)
        cleanup.pack_start(
            Gtk.Label(label="Remove islands up to · square pixels", xalign=0), False, False, 0
        )
        self.island_area = Gtk.SpinButton.new_with_range(0, 1_000_000, 16)
        self.island_area.set_value(64)
        self.island_area.set_tooltip_text(
            "Remove small detached fragments; retain the main component and explicitly picked parts."
        )
        cleanup.pack_start(self.island_area, False, False, 0)
        self.cleanup_button = self.button(inspection, "Clean selected mask", self.cleanup)
        matting = details(inspection, "Refine boundary · ViTMatte")
        self.radius = Gtk.SpinButton.new_with_range(1, 80, 1)
        self.radius.set_value(10)
        matting.pack_start(
            Gtk.Label(label="Refinement band · layer pixels", xalign=0), False, False, 0
        )
        matting.pack_start(self.radius, False, False, 0)
        self.matte_output = self.combo(
            matting,
            "Refinement output",
            [("crisp", "Crisp selection"), ("soft", "Soft matte")],
        )
        self.matte_output.set_tooltip_text(
            "Crisp selection thresholds the refined boundary without smoothing. "
            "Soft matte retains partial coverage for hair and transparency."
        )
        cutoff = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        cutoff.pack_start(Gtk.Label(label="Opacity cutoff · %", xalign=0), False, False, 0)
        self.matte_threshold = Gtk.SpinButton.new_with_range(1, 99, 1)
        self.matte_threshold.set_value(50)
        self.matte_threshold.set_tooltip_text(
            "Higher values keep less of the refined boundary; lower values keep more. "
            "Run refinement to apply the cutoff."
        )
        cutoff.pack_start(self.matte_threshold, False, False, 0)
        matting.pack_start(cutoff, False, False, 0)
        self.matte_output.connect(
            "changed", lambda _: cutoff.set_sensitive(self.matte_output.get_active_id() == "crisp")
        )
        self.refine_button = self.button(matting, "Refine edges with ViTMatte", self.refine)

        output = section("4. Apply to GIMP")
        self.output = self.combo(
            output, "Output", [("selection", "GIMP selection"), ("mask", "New layer mask")]
        )
        self.combine = self.combo(
            output,
            "Combine with existing GIMP selection",
            [
                ("new", "Replace"),
                ("add", "Add"),
                ("subtract", "Subtract"),
                ("intersect", "Intersect"),
            ],
        )
        self.area = Gtk.DrawingArea()
        self.area.set_size_request(400, 350)
        self.area.set_can_focus(True)
        self.area.add_events(
            Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
        )
        self.area.connect("draw", self.draw)
        self.area.connect("motion-notify-event", self.motion)
        self.area.connect("button-press-event", self.press)
        self.area.connect("button-release-event", self.release)
        self.area.connect("leave-notify-event", self.leave)
        self.area.connect("scroll-event", self.scroll_candidates)
        self.preview_scroll = Gtk.ScrolledWindow()
        self.preview_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.preview_scroll.set_min_content_width(400)
        self.preview_scroll.set_min_content_height(350)
        self.preview_scroll.add(self.area)
        self.preview_scroll.get_hadjustment().connect(
            "value-changed", lambda _: self.area.queue_draw()
        )
        self.preview_scroll.get_vadjustment().connect(
            "value-changed", lambda _: self.area.queue_draw()
        )
        body.pack_start(self.preview_scroll, True, True, 0)
        self.status = Gtk.Label(label="Starting local model process…", xalign=0)
        self.status.set_line_wrap(True)
        self.status.set_max_width_chars(100)
        content.pack_start(self.status, False, False, 0)
        tip = Gtk.Label(
            label="Hover: Tab / scroll cycles candidates · Shift+Tab goes back\n"
            "Shift adds · Alt subtracts · Right-click excludes · "
            "Ctrl+Z undoes · Escape cancels a pending request",
            xalign=0,
        )
        tip.set_line_wrap(True)
        content.pack_start(tip, False, False, 0)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.apply_button = self.add_button("Apply", Gtk.ResponseType.OK)
        self.apply_button.set_sensitive(False)
        for widget in (self.tool, self.mode, self.model):
            widget.connect("changed", self.controls_changed)
        self.scope.connect("changed", self.scope_changed)
        self.auto_cleanup.connect("toggled", self.controls_changed)
        self.hole_area.connect("value-changed", self.controls_changed)
        self.smoothing.connect("value-changed", self.controls_changed)
        self.island_area.connect("value-changed", self.controls_changed)
        self.view.connect("changed", lambda _: self.update())
        self.zoom.connect("changed", self.zoom_changed)
        self.output.connect(
            "changed",
            lambda _: self.combine.set_sensitive(self.output.get_active_id() == "selection"),
        )
        self.update()

    def cancel_requests(self):
        self.epoch += 1
        if self.hover_timer is not None:
            GLib.source_remove(self.hover_timer)
            self.hover_timer = None
        if hasattr(self, "bridge"):
            self.bridge.discard_pending()
        self.locked = False
        self.pending = False
        self.current_request = -1

    def invalidate(self):
        self.cancel_requests()
        self.candidate = None
        self.candidates = []
        self.hover_point = None

    def controls_changed(self, widget):
        if widget is self.model and self.image_ready:
            self.warm_model()
            return
        if self.warming:
            return
        self.invalidate()
        self.pointer = None
        self.status.set_text("Hover over an object or click to request a selection.")
        self.update()

    def scope_changed(self, _):
        if self.warming:
            return
        if self.pending:
            self.invalidate()
            self.pointer = None
        if self.candidates:
            self.candidate = pick_candidate(
                self.candidates, self.hover_point, self.scope.get_active_id() == "part"
            )
            if self.candidate.getbbox() is not None:
                self.candidate_index = next(
                    i for i, mask in enumerate(self.candidates) if mask is self.candidate
                )
            else:
                self.candidate = None
            self.status.set_text("Candidate preference updated. Tab / scroll to cycle.")
        self.update()

    def worker_ready(self):
        self.image_ready = True
        self.warm_model()

    def warm_model(self):
        self.invalidate()
        epoch = self.epoch
        self.ready = False
        self.warming = self.locked = self.pending = True
        self.status.set_text("Loading model and encoding image…")
        self.update()

        def finished(result):
            if epoch != self.epoch or self.closed:
                return
            self.warming = self.locked = self.pending = False
            if "error" in result:
                self.failed(result["error"])
                return
            self.ready = True
            self.pointer = None
            self.status.set_text("Model and image ready. Hover and pause, then click to pick.")
            self.update()

        self.current_request = self.bridge.submit(
            {"op": "warmup", "model": self.model.get_active_id()}, finished
        )

    def progress(self, request_id, message):
        if request_id in (0, self.current_request):
            self.status.set_text(message)

    def failed(self, message):
        self.warming = False
        self.locked = False
        self.pending = False
        self.status.set_text(message)
        self.update()

    def update(self):
        self.revision += 1
        self.area.queue_draw()
        has_selection = self.selection.mask.getbbox() is not None
        self.apply_button.set_sensitive(has_selection and not self.locked)
        self.refine_button.set_sensitive(self.ready and has_selection and not self.locked)
        self.cleanup_button.set_sensitive(self.ready and has_selection and not self.locked)
        self.clear_button.set_sensitive(has_selection and not self.warming)
        self.undo_button.set_sensitive(bool(self.selection.history) and not self.warming)
        self.next_button.set_sensitive(len(self.candidates) > 1)

    def location(self, event):
        return image_coordinates(
            event.x,
            event.y,
            (self.area.get_allocated_width(), self.area.get_allocated_height()),
            self.source.size,
        )

    def zoom_changed(self, _):
        value = self.zoom.get_active_id()
        if value == "fit":
            self.area.set_size_request(400, 350)
        else:
            self.area.set_size_request(
                round(self.source.width * float(value)), round(self.source.height * float(value))
            )
        self.pointer = None
        self.update()

    def motion(self, _, event):
        point = self.location(event)
        self.pointer_inside = point is not None
        if self.drag:
            if point:
                self.drag[1] = point
            self.area.queue_draw()
            return True
        if self.locked or not self.ready or self.tool.get_active_id() != "hover":
            return False
        if point is None:
            self.leave(None, None)
            return False
        if point == self.pointer:
            return False
        self.invalidate()
        self.pointer = point
        self.update()

        epoch = self.epoch

        def request():
            if epoch != self.epoch or self.closed:
                return False
            self.hover_timer = None
            self.predict(point)
            return False

        self.hover_timer = GLib.timeout_add(180, request)
        return True

    def leave(self, *_):
        self.pointer_inside = False
        self.scroll_delta = 0.0
        if not self.locked and not self.drag:
            self.cancel_requests()
            if self.candidate is None:
                self.pointer = None
            self.update()
        return False

    def press(self, _, event):
        if event.button not in (1, 3) or not self.ready or self.locked:
            return False
        point = self.location(event)
        if point is None:
            return False
        self.area.grab_focus()
        mode = self.mode.get_active_id()
        if event.state & Gdk.ModifierType.SHIFT_MASK:
            mode = "add"
        elif event.state & Gdk.ModifierType.MOD1_MASK:
            mode = "subtract"
        tool = self.tool.get_active_id()
        if event.button == 3 or tool == "exclude":
            if self.last_seed is None:
                self.status.set_text("Pick an object first, then exclude an unwanted area.")
            else:
                self.predict(point, commit=True, mode="correction")
        elif tool == "box":
            self.invalidate()
            self.drag = [point, point, mode]
        elif tool == "include":
            self.predict(point, commit=True, mode="add")
        elif self.candidate is not None and point == self.hover_point:
            self.accept(self.candidate, mode, self.hover_point)
        else:
            self.predict(point, commit=True, mode=mode)
        return True

    def release(self, _, event):
        if not self.drag:
            return False
        start, end, mode = self.drag
        self.drag = None
        end = self.location(event) or end
        if abs(start[0] - end[0]) < 3 or abs(start[1] - end[1]) < 3:
            self.update()
            return True
        box = [
            min(start[0], end[0]),
            min(start[1], end[1]),
            max(start[0], end[0]),
            max(start[1], end[1]),
        ]
        self.predict(None, commit=True, mode=mode, box=box)
        return True

    def predict(self, point, commit=False, mode="new", box=None):
        self.invalidate()
        epoch = self.epoch
        self.locked = commit
        self.pending = True
        points = [] if point is None else [{"x": point[0], "y": point[1], "label": 1}]
        if mode == "correction":
            points = self.last_points + [{"x": point[0], "y": point[1], "label": 0}]
        smaller = self.scope.get_active_id() == "part"
        previous_base = self.last_base.copy() if mode == "correction" else None
        self.status.set_text("Finding object…" if not commit else "Selecting object…")
        self.update()

        def finished(result):
            if epoch != self.epoch or self.closed:
                return
            self.locked = False
            self.pending = False
            if "error" in result:
                self.failed(result["error"])
                return
            masks = result["masks"]
            if not masks:
                self.status.set_text("No candidate found. Try Include or a box.")
                self.update()
                return
            mask = pick_candidate(masks, self.last_seed if mode == "correction" else point, smaller)
            if mask.getbbox() is None:
                self.status.set_text("No candidate contains the pointer. Try Include or a box.")
                self.update()
                return
            if commit:
                if mode == "correction":
                    # Replace only the most recently picked object, preserving other picks.
                    self.accept(ImageChops.lighter(previous_base, mask), "new", self.last_seed)
                    self.last_base = previous_base
                    self.last_points = points
                else:
                    self.accept(mask, mode, point or self.mask_seed(mask))
            else:
                self.candidates = masks
                self.candidate = mask
                self.candidate_index = next(i for i, item in enumerate(masks) if item is mask)
                self.hover_point = point
                self.status.set_text(
                    f"Click to select · candidate {self.candidate_index + 1} "
                    f"of {len(masks)} · Tab / scroll to cycle · "
                    f"{result['seconds']:.2f} s"
                )
                self.update()

        request = {
            "op": "predict",
            "model": self.model.get_active_id(),
            "points": points,
            "box": box,
            "cleanup": self.cleanup_settings() if self.auto_cleanup.get_active() else None,
        }
        self.current_request = self.bridge.submit(request, finished)

    def mask_seed(self, mask):
        bounds = mask.getbbox()
        if not bounds:
            return None
        # Locate an actual foreground pixel, even for hollow or disconnected masks.
        for y in range(bounds[1], bounds[3]):
            for x in range(bounds[0], bounds[2]):
                if mask.getpixel((x, y)) > 127:
                    return x, y
        return None

    def accept(self, mask, mode, seed):
        self.last_base = self.selection.mask.copy() if mode == "add" else Image.new("L", mask.size)
        self.selection.accept(mask, mode)
        self.last_seed = seed if mode in ("new", "add") else None
        self.last_points = [{"x": seed[0], "y": seed[1], "label": 1}] if seed else []
        self.invalidate()
        self.pointer = None
        self.status.set_text("Selection updated. Keep picking, refine the edge, or Apply.")
        self.update()

    def next_candidate(self, step=1):
        if self.candidates:
            self.cancel_requests()
            self.candidate_index = (self.candidate_index + step) % len(self.candidates)
            self.candidate = self.candidates[self.candidate_index]
            self.status.set_text(
                f"Candidate {self.candidate_index + 1} of {len(self.candidates)}. "
                "Click to accept · Tab / scroll to cycle."
            )
            self.update()

    def scroll_candidates(self, _, event):
        if self.location(event) is None or not self.candidates or self.locked or self.drag:
            return False
        if event.direction == Gdk.ScrollDirection.DOWN:
            self.next_candidate()
        elif event.direction == Gdk.ScrollDirection.UP:
            self.next_candidate(-1)
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            valid, _, dy = event.get_scroll_deltas()
            if valid:
                self.scroll_delta += dy
                if abs(self.scroll_delta) >= 1:
                    self.next_candidate(1 if self.scroll_delta > 0 else -1)
                    self.scroll_delta = 0.0
        else:
            return False
        return True

    def cleanup_settings(self):
        return {
            "hole_area": self.hole_area.get_value_as_int(),
            "smooth_radius": self.smoothing.get_value(),
            "island_area": self.island_area.get_value_as_int(),
        }

    def cleanup(self):
        self.invalidate()
        epoch = self.epoch
        self.locked = self.pending = True
        self.status.set_text("Cleaning the selected mask…")
        self.update()

        def finished(result):
            if epoch != self.epoch or self.closed:
                return
            self.locked = self.pending = False
            if "error" in result:
                self.failed(result["error"])
                return
            self.accept(result["masks"][0], "new", None)
            self.status.set_text(
                "Holes and small islands cleaned; contour smoothed. Undo restores the mask."
            )

        self.current_request = self.bridge.submit(
            {
                "op": "cleanup",
                "mask": encode(self.selection.mask),
                "cleanup": self.cleanup_settings(),
            },
            finished,
        )

    def undo(self):
        self.invalidate()
        self.selection.undo()
        self.last_seed = None
        self.status.set_text("Undid the last selection change.")
        self.update()

    def clear(self):
        self.invalidate()
        self.selection.clear()
        self.last_seed = None
        self.update()

    def refine(self):
        self.invalidate()
        epoch = self.epoch
        self.locked = True
        self.pending = True
        matte_output = self.matte_output.get_active_id()
        self.status.set_text("Refining edges with ViTMatte…")
        self.update()

        def finished(result):
            if epoch != self.epoch or self.closed:
                return
            self.locked = False
            self.pending = False
            if "error" in result:
                self.failed(result["error"])
                return
            self.accept(result["masks"][0], "new", None)
            self.view.set_active_id("cutout")
            self.status.set_text(
                ("Crisp boundary refined." if matte_output == "crisp" else "Soft matte refined.")
                + " Undo restores the previous mask."
            )

        self.current_request = self.bridge.submit(
            {
                "op": "predict",
                "model": "vitmatte",
                "mask": encode(self.selection.mask),
                "radius": self.radius.get_value_as_int(),
                "matte_output": matte_output,
                "matte_threshold": self.matte_threshold.get_value() / 100,
            },
            finished,
        )

    def draw(self, widget, cr):
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        scale = min(width / self.source.width, height / self.source.height)
        left = (width - self.source.width * scale) / 2
        top = (height - self.source.height * scale) / 2
        x1, y1, x2, y2 = cr.clip_extents()
        horizontal = self.preview_scroll.get_hadjustment()
        vertical = self.preview_scroll.get_vadjustment()
        if horizontal.get_page_size() and vertical.get_page_size():
            x1 = max(x1, horizontal.get_value())
            y1 = max(y1, vertical.get_value())
            x2 = min(x2, horizontal.get_value() + horizontal.get_page_size())
            y2 = min(y2, vertical.get_value() + vertical.get_page_size())
        crop = (
            max(0, math.floor((x1 - left) / scale)),
            max(0, math.floor((y1 - top) / scale)),
            min(self.source.width, math.ceil((x2 - left) / scale)),
            min(self.source.height, math.ceil((y2 - top) / scale)),
        )
        cr.set_source_rgb(0.12, 0.13, 0.13)
        cr.paint()
        if crop[2] <= crop[0] or crop[3] <= crop[1]:
            return False
        size = (
            max(1, round((crop[2] - crop[0]) * scale)),
            max(1, round((crop[3] - crop[1]) * scale)),
        )
        key = (crop, size, self.revision)
        if key != self.render_key:
            source = self.source.crop(crop).resize(size, Image.Resampling.BILINEAR)
            selected = self.selection.mask.crop(crop).resize(size, Image.Resampling.BILINEAR)
            view = self.view.get_active_id()
            background = Image.new("RGB", size, "#333333" if view == "black" else "white")
            if view in ("overlay", "cutout"):
                painter = ImageDraw.Draw(background)
                for y in range(0, size[1], 12):
                    for x in range(0, size[0], 12):
                        if (x // 12 + y // 12) % 2:
                            painter.rectangle((x, y, x + 11, y + 11), fill="#cccccc")
            if view == "mask":
                result = selected.convert("RGB")
            else:
                alpha = source.getchannel("A") if view == "overlay" else selected
                result = Image.composite(source.convert("RGB"), background, alpha)
                if view == "overlay":
                    result = Image.composite(
                        Image.new("RGB", size, "#62d397"),
                        result,
                        selected.point(lambda p: round(p * 0.25)),
                    )
                    edge = selected.filter(ImageFilter.FIND_EDGES)
                    result = Image.composite(Image.new("RGB", size, "white"), result, edge)
            if self.candidate is not None:
                hover = self.candidate.crop(crop).resize(size, Image.Resampling.BILINEAR)
                color = "#ed6c76" if self.mode.get_active_id() == "subtract" else "#64aaff"
                result = Image.composite(
                    Image.new("RGB", size, color), result, hover.point(lambda p: round(p * 0.4))
                )
            self.pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
                GLib.Bytes.new(result.tobytes()),
                GdkPixbuf.Colorspace.RGB,
                False,
                8,
                *size,
                size[0] * 3,
            )
            self.render_key = key
        cr.set_source_rgb(0.12, 0.13, 0.13)
        cr.paint()
        Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, left + crop[0] * scale, top + crop[1] * scale)
        cr.paint()
        if self.drag:
            start, end, _ = self.drag
            cr.set_source_rgb(0.5, 0.75, 1)
            cr.set_line_width(1.5)
            cr.set_dash([5, 4])
            cr.rectangle(
                left + min(start[0], end[0]) * scale,
                top + min(start[1], end[1]) * scale,
                abs(end[0] - start[0]) * scale,
                abs(end[1] - start[1]) * scale,
            )
            cr.stroke()
        return False

    def key(self, _, event):
        if self.warming:
            if event.keyval == Gdk.KEY_Escape:
                self.response(Gtk.ResponseType.CANCEL)
                return True
            if (
                event.keyval in (Gdk.KEY_z, Gdk.KEY_Z)
                and event.state & Gdk.ModifierType.CONTROL_MASK
            ):
                return True
        if (
            (self.pointer_inside or self.area.has_focus())
            and self.candidates
            and event.keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab)
        ):
            reverse = (
                event.keyval == Gdk.KEY_ISO_Left_Tab or event.state & Gdk.ModifierType.SHIFT_MASK
            )
            self.next_candidate(-1 if reverse else 1)
            return True
        if event.keyval == Gdk.KEY_Escape and (
            self.pending or self.hover_timer or self.candidate or self.drag
        ):
            self.invalidate()
            self.drag = None
            self.status.set_text("Pending selection cancelled.")
            self.update()
            return True
        if event.keyval in (Gdk.KEY_z, Gdk.KEY_Z) and event.state & Gdk.ModifierType.CONTROL_MASK:
            self.undo()
            return True
        return False

    def run_selection(self):
        self.show_all()
        try:
            while self.run() == Gtk.ResponseType.OK:
                try:
                    if self.layer.get_offsets()[-2:] != self.origin:
                        raise ValueError("The layer moved. Reopen Object Select.")
                    apply(
                        self.image,
                        self.layer,
                        self.selection.mask,
                        self.output.get_active_id(),
                        self.combine.get_active_id(),
                    )
                    return True
                except Exception as error:  # noqa: BLE001 - report failures at the process/UI boundary
                    self.failed(str(error))
            return False
        finally:
            self.destroy()

    def shutdown(self, *_):
        self.closed = True
        self.invalidate()
        if hasattr(self, "bridge"):
            self.bridge.close()
