"""GTK hover-and-pick interface. All GIMP calls stay on the main thread."""

import json
import math
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Gimp", "3.0")
gi.require_version("GimpUi", "3.0")
from bridge import Bridge, encode
from gi.repository import Gdk, GdkPixbuf, Gimp, GimpUi, GLib, Gtk, Pango
from gimp_io import apply, snapshot
from PIL import Image, ImageChops
from preview import render_base, render_candidate
from selection import (
    Pick,
    Selection,
    coverage,
    effective_mode,
    image_coordinates,
    pack,
    pick_candidate,
    refine_plan,
    replay,
)
from widgets import (
    Choice,
    Option,
    Spinner,
    combo,
    details,
    icon_button,
    install_css,
    scrolls_past,
    section,
    setting,
    spin,
    text_button,
)

ZOOM_LEVELS = (0.1, 0.25, 0.5, 1, 2, 4, 8)
# GDK windows stop working reliably well before X11's 32767 pixel limit.
MAX_CANVAS_SIDE = 16000
MODIFIERS = Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK
MODIFIER_KEYS = {
    Gdk.KEY_Shift_L: Gdk.ModifierType.SHIFT_MASK,
    Gdk.KEY_Shift_R: Gdk.ModifierType.SHIFT_MASK,
    Gdk.KEY_Control_L: Gdk.ModifierType.CONTROL_MASK,
    Gdk.KEY_Control_R: Gdk.ModifierType.CONTROL_MASK,
    Gdk.KEY_Alt_L: Gdk.ModifierType.MOD1_MASK,
    Gdk.KEY_Alt_R: Gdk.ModifierType.MOD1_MASK,
}
TOOL_KEYS = {Gdk.KEY_h: "hover", Gdk.KEY_b: "box", Gdk.KEY_i: "include", Gdk.KEY_e: "exclude"}
TOOL_HINTS = {
    "hover": "Hover over an object, then click to select it.",
    "box": "Drag a box around the object you want.",
    "include": "Click part of an object to add it to the selection.",
    "exclude": "Click an area to remove it from your last pick.",
}
MODE_HINTS = {
    "new": "Each pick replaces earlier picks.",
    "add": "Each pick adds to your picks.",
    "subtract": "Each pick subtracts from your picks.",
    "intersect": "Each pick keeps only the overlap.",
}
FIXED_MODE_HINTS = {"include": "Include always adds.", "exclude": "Exclude always removes."}
PICK_ACTIONS = {"new": "Select", "add": "Add", "subtract": "Subtract", "intersect": "Intersect"}
SHORTCUTS = (
    ("Click or Space", "Accept the candidate and refine its edges"),
    ("Tab or scroll", "Next candidate"),
    ("Shift+Tab", "Previous candidate"),
    ("Shift+click", "Add to your picks"),
    ("Ctrl+click or Alt+click", "Subtract from your picks"),
    ("Shift+Ctrl+click", "Intersect with your picks"),
    ("Right-click", "Exclude from the last pick"),
    ("H  B  I  E", "Hover, Box, Include, Exclude"),
    ("Ctrl+scroll or +  −", "Zoom"),
    ("0 and 1", "Fit and 100%"),
    ("Middle-drag", "Pan"),
    ("Ctrl+Z and Ctrl+Y", "Undo and Redo"),
    ("Esc", "Cancel a request or candidate; close if nothing is picked"),
    ("Enter", "Apply"),
)


class HoverDialog(Gtk.Dialog):
    def __init__(self, image, layer):
        super().__init__(title=f"Object Select – {layer.get_name()}", modal=True)
        self.set_default_size(1060, 860)
        self.image, self.layer = image, layer
        self.source = snapshot(layer)
        self.selection = Selection(self.source.size)
        self.origin = layer.get_offsets()[-2:]
        self.epoch = 0
        self.hover_timer = None
        self.candidate = None
        self.candidates = []
        self.candidate_index = 0
        self.ghost = None
        self.hover_memory = None
        self.pointer = None
        self.pointer_inside = False
        self.scroll_delta = 0.0
        self.hover_point = None
        self.drag = None
        self.pan = None
        self.modifiers = 0
        self.locked = False
        self.pending = False
        self.ready = False
        self.image_ready = False
        self.warming = False
        self.load_failed = False
        self.worker_dead = False
        self.busy_title = None
        self.loading_title = "Starting…"
        self.closed = False
        self.revision = 0
        self.render_key = None
        self.base_key = None
        self.base_image = None
        self.zoom_factor = None
        self.zoom_anchor = None
        self.cursor_name = None
        self.current_request = 0
        self.last_seed = None
        self.summary_revision = None
        self.has_selection = False
        self.build()
        settings = json.loads((Path(__file__).parent / "launcher.json").read_text())
        self.bridge = Bridge(
            settings, self.source, self.worker_ready, self.progress, self.worker_died
        )
        self.connect("key-press-event", self.key)
        self.connect("key-release-event", self.key_release)
        self.connect("focus-in-event", lambda *_: self.refresh_output())
        self.connect("focus-out-event", lambda *_: self.set_modifiers(0))
        self.connect("destroy", self.shutdown)

    def build(self):
        install_css()
        content = self.get_content_area()
        content.set_spacing(10)
        content.set_border_width(12)
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        content.pack_start(body, True, True, 0)
        body.pack_start(self.build_sidebar(), False, False, 0)
        body.pack_start(self.build_canvas(), True, True, 0)
        content.pack_start(self.build_output(), False, False, 0)
        self.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        self.apply_button = self.add_button("_Apply", Gtk.ResponseType.OK)
        self.apply_button.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.OK)
        self.refresh_output()
        self.update()

    def build_sidebar(self):
        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        # A fixed width keeps wrapping hints and a scrollbar from shifting the layout.
        controls.set_size_request(238, -1)
        controls.set_margin_end(12)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(controls)

        self.tool = Choice(
            [
                Option("hover", "Hover & pick", "gimp-tool-fuzzy-select", "H"),
                Option("box", "Draw a box", "gimp-tool-rect-select", "B"),
                Option("include", "Include a part", "list-add-symbolic", "I"),
                Option("exclude", "Exclude an area", "list-remove-symbolic", "E"),
            ],
            Gtk.Orientation.VERTICAL,
        )
        self.tool.connect("changed", self.controls_changed)
        section(controls, "Tool").pack_start(self.tool, False, False, 0)

        picking = section(controls, "Candidates")
        row = Gtk.Box(spacing=6)
        stepper = Gtk.Box()
        stepper.get_style_context().add_class("linked")
        self.previous_button = icon_button(
            "go-previous-symbolic",
            "Previous candidate (Shift+Tab)",
            lambda: self.next_candidate(-1),
            "‹",
        )
        self.next_button = icon_button(
            "go-next-symbolic", "Next candidate (Tab or scroll)", self.next_candidate, "›"
        )
        stepper.pack_start(self.previous_button, False, False, 0)
        stepper.pack_start(self.next_button, False, False, 0)
        row.pack_start(stepper, False, False, 0)
        self.candidate_label = Gtk.Label(label="None yet")
        self.candidate_label.get_style_context().add_class("dim-label")
        row.pack_start(self.candidate_label, True, True, 0)
        self.accept_button = Gtk.Button(label="Select")
        # Labels change with held modifiers, so a fixed width stops the row from shifting.
        self.accept_button.set_size_request(80, -1)
        self.accept_button.set_tooltip_text("Use this candidate (Space)")
        self.accept_button.connect("clicked", lambda _: self.accept_candidate())
        row.pack_start(self.accept_button, False, False, 0)
        picking.pack_start(row, False, False, 0)
        self.scope = Choice(
            [
                Option("object", "Whole object", tooltip="Start on the largest candidate."),
                Option("part", "Smaller part", tooltip="Start on the smallest candidate."),
            ]
        )
        self.scope.connect("changed", self.scope_changed)
        picking.pack_start(self.scope, False, False, 0)

        selected = section(controls, "Picks")
        self.mode = Choice(
            [
                Option("new", "Replace", "gimp-selection-replace", icon_only=True),
                Option("add", "Add (hold Shift)", "gimp-selection-add", icon_only=True),
                Option(
                    "subtract", "Subtract (hold Ctrl)", "gimp-selection-subtract", icon_only=True
                ),
                Option(
                    "intersect",
                    "Intersect (hold Shift+Ctrl)",
                    "gimp-selection-intersect",
                    icon_only=True,
                ),
            ]
        )
        self.mode.connect("changed", lambda _: self.pick_feedback())
        selected.pack_start(self.mode, False, False, 0)
        self.mode_hint = Gtk.Label(xalign=0)
        self.mode_hint.set_line_wrap(True)
        self.mode_hint.set_max_width_chars(1)
        selected.pack_start(self.mode_hint, False, False, 0)
        self.summary = Gtk.Label(xalign=0)
        selected.pack_start(self.summary, False, False, 0)
        actions = Gtk.Box(spacing=6, homogeneous=True)
        self.undo_button = text_button(actions, "Undo", self.undo, "Ctrl+Z")
        self.redo_button = text_button(actions, "Redo", self.redo, "Ctrl+Y")
        self.clear_button = text_button(
            actions, "Clear", self.clear, "Clear all picks. GIMP's selection is unchanged."
        )
        selected.pack_start(actions, False, False, 0)

        self.auto_refine = Gtk.CheckButton(label="Refine edges of each pick")
        self.auto_refine.set_active(True)
        self.auto_refine.set_tooltip_text(
            "Slower, with much cleaner contours. Turn off to keep the model's coarse mask."
        )
        edges = section(controls, "Edges")
        edges.pack_start(self.auto_refine, False, False, 0)
        self.refine_all_button = text_button(edges, "Refine all picks", self.refine_all)

        cleanup = details(controls, "Clean up")
        self.hole_area = spin(
            0, 10000, 16, 64, "Fill small enclosed holes. Larger openings stay unselected."
        )
        self.smoothing = spin(
            0, 5, 1, 0, "Round off the contour. This also rounds points and makes edges hard."
        )
        self.island_area = spin(
            0,
            1_000_000,
            16,
            64,
            "Remove small detached fragments; keep the main shape and picked parts.",
        )
        setting(cleanup, "Largest hole to fill (px²)", self.hole_area)
        setting(cleanup, "Smooth contour (px)", self.smoothing)
        setting(cleanup, "Largest island to remove (px²)", self.island_area)
        for widget in (self.hole_area, self.smoothing, self.island_area):
            widget.connect("value-changed", self.controls_changed)
        self.cleanup_button = text_button(cleanup, "Clean selected mask", self.cleanup)

        matting = details(controls, "Hair and fur")
        self.matte_threshold = spin(
            1,
            99,
            1,
            50,
            "Higher values keep less of the refined boundary; lower values keep more. "
            "Run refinement to apply the cutoff.",
        )
        self.matte_output = Choice(
            [
                Option(
                    "soft",
                    "Soft matte",
                    tooltip="Keep partial coverage for hair and transparency.",
                ),
                Option(
                    "crisp",
                    "Crisp",
                    tooltip="Threshold the refined boundary without smoothing.",
                ),
            ]
        )

        matting.pack_start(self.matte_output, False, False, 0)
        self.radius = spin(1, 80, 1, 10, "How far from the edge ViTMatte may change the mask.")
        setting(matting, "Edge band (px)", self.radius)
        cutoff = setting(matting, "Opacity cutoff (%)", self.matte_threshold)
        threshold_tooltip = self.matte_threshold.get_tooltip_text()

        def output_changed(_):
            crisp = self.matte_output.get_active_id() == "crisp"
            cutoff.set_sensitive(crisp)
            self.matte_threshold.set_tooltip_text(
                threshold_tooltip if crisp else "Only used by Crisp."
            )

        self.matte_output.connect("changed", output_changed)
        output_changed(None)
        self.refine_button = text_button(matting, "Refine hair and fur", self.refine)

        self.model = combo(
            [
                ("sam2-large", "SAM 2.1 large · recommended"),
                ("sam2-base", "SAM 2.1 base+ · balanced"),
                ("sam2-tiny", "SAM 2.1 tiny · fastest"),
                ("sam3", "SAM 3 · much slower, access required"),
            ]
        )
        self.model.connect("changed", self.controls_changed)
        scrolls_past(self.model)
        self.model.get_accessible().set_name("Model")
        section(controls, "Model").pack_start(self.model, False, False, 0)
        return scroll

    def build_toolbar(self):
        toolbar = Gtk.Box(spacing=8)
        view_label = Gtk.Label.new_with_mnemonic("_View")
        toolbar.pack_start(view_label, False, False, 0)
        self.view = combo(
            [
                ("overlay", "Selection overlay"),
                ("cutout", "On transparency"),
                ("black", "On black"),
                ("white", "On white"),
                ("mask", "Black & white mask"),
            ]
        )
        view_label.set_mnemonic_widget(self.view)
        self.view.connect("changed", lambda _: self.update())
        toolbar.pack_start(self.view, False, False, 0)
        zoom = Gtk.Box()
        zoom.get_style_context().add_class("linked")
        for button in (
            icon_button("zoom-out-symbolic", "Zoom out (−)", lambda: self.zoom_step(-1), "−"),
            icon_button("zoom-in-symbolic", "Zoom in (+)", lambda: self.zoom_step(1), "+"),
            icon_button("zoom-original-symbolic", "100% (1)", lambda: self.set_zoom(1), "1:1"),
            icon_button(
                "zoom-fit-best-symbolic", "Fit image (0)", lambda: self.set_zoom(None), "Fit"
            ),
        ):
            zoom.pack_start(button, False, False, 0)
        toolbar.pack_end(zoom, False, False, 0)
        self.zoom_label = Gtk.Label(label="100%")
        self.zoom_label.set_width_chars(5)
        self.zoom_label.set_xalign(1)
        self.zoom_label.get_style_context().add_class("dim-label")
        toolbar.pack_end(self.zoom_label, False, False, 0)
        return toolbar

    def build_canvas(self):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        column.pack_start(self.build_toolbar(), False, False, 0)
        self.error_bar = Gtk.InfoBar()
        self.error_bar.set_message_type(Gtk.MessageType.ERROR)
        self.error_bar.set_show_close_button(True)
        self.error_bar.get_style_context().add_class("object-select-error")
        self.error_bar.set_valign(Gtk.Align.START)
        self.error_label = Gtk.Label(xalign=0)
        self.error_label.set_line_wrap(True)
        self.error_label.set_selectable(True)
        self.error_label.set_lines(4)
        self.error_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.error_label.set_max_width_chars(1)
        self.error_label.get_style_context().add_class("object-select-error-text")
        self.error_bar.get_content_area().pack_start(self.error_label, True, True, 0)
        self.retry_button = self.error_bar.add_button("_Retry", Gtk.ResponseType.ACCEPT)
        self.error_bar.connect("response", self.error_response)
        self.error_bar.show_all()
        self.error_bar.set_no_show_all(True)
        self.error_bar.hide()

        self.area = Gtk.DrawingArea()
        self.area.set_size_request(400, 350)
        self.area.set_can_focus(True)
        self.area.get_accessible().set_name("Image preview")
        self.area.add_events(
            Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
        )
        self.area.connect("draw", self.draw)
        self.area.connect("motion-notify-event", self.motion)
        self.area.connect("button-press-event", self.press)
        self.area.connect("button-release-event", self.release)
        self.area.connect("enter-notify-event", self.enter)
        self.area.connect("leave-notify-event", self.leave)
        self.area.connect("scroll-event", self.scroll_candidates)
        self.area.connect("size-allocate", self.area_allocated)
        self.area.connect("realize", lambda _: self.sync_cursor())
        self.preview_scroll = Gtk.ScrolledWindow()
        self.preview_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.preview_scroll.set_min_content_width(400)
        self.preview_scroll.set_min_content_height(350)
        self.preview_scroll.add(self.area)
        # GtkViewport otherwise scrolls a zoomed canvas to its origin when it takes focus.
        viewport = self.preview_scroll.get_child()
        viewport.set_focus_hadjustment(Gtk.Adjustment())
        viewport.set_focus_vadjustment(Gtk.Adjustment())
        self.preview_scroll.get_hadjustment().connect(
            "value-changed", lambda _: self.area.queue_draw()
        )
        self.preview_scroll.get_vadjustment().connect(
            "value-changed", lambda _: self.area.queue_draw()
        )
        overlay = Gtk.Overlay()
        overlay.add(self.preview_scroll)
        overlay.add_overlay(self.build_busy_card())
        overlay.set_overlay_pass_through(self.busy_card, True)
        # Floating errors keep the image still under the pointer.
        overlay.add_overlay(self.error_bar)
        overlay.connect("get-child-position", self.overlay_child_position)
        column.pack_start(overlay, True, True, 0)

        footer = Gtk.Box(spacing=8)
        self.spinner = Spinner()
        self.status = Gtk.Label(label="Picking starts when the model is ready.", xalign=0)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.status.connect(
            "notify::label", lambda label, _: label.set_tooltip_text(label.get_text())
        )
        footer.pack_start(self.status, True, True, 0)
        footer.pack_start(self.spinner, False, False, 0)
        footer.pack_start(self.build_shortcuts(), False, False, 0)
        column.pack_start(footer, False, False, 0)
        return column

    def build_busy_card(self):
        self.busy_card = Gtk.Frame()
        self.busy_card.set_shadow_type(Gtk.ShadowType.NONE)
        self.busy_card.get_style_context().add_class("object-select-busy")
        self.busy_card.set_halign(Gtk.Align.CENTER)
        self.busy_card.set_valign(Gtk.Align.CENTER)
        row = Gtk.Box(spacing=14)
        row.set_border_width(16)
        self.busy_spinner = Spinner(24)
        row.pack_start(self.busy_spinner, False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.busy_title_label = Gtk.Label(xalign=0)
        self.busy_detail = Gtk.Label(xalign=0)
        self.busy_detail.set_line_wrap(True)
        self.busy_detail.set_max_width_chars(48)
        self.busy_detail.get_style_context().add_class("dim-label")
        text.pack_start(self.busy_title_label, False, False, 0)
        text.pack_start(self.busy_detail, False, False, 0)
        row.pack_start(text, False, False, 0)
        self.busy_card.add(row)
        self.busy_card.show_all()
        self.busy_card.set_no_show_all(True)
        return self.busy_card

    def build_shortcuts(self):
        grid = Gtk.Grid(row_spacing=4, column_spacing=16)
        grid.set_border_width(10)
        for row, (keys, action) in enumerate(SHORTCUTS):
            grid.attach(Gtk.Label(label=keys, xalign=0), 0, row, 1, 1)
            grid.attach(Gtk.Label(label=action, xalign=0), 1, row, 1, 1)
        grid.show_all()
        popover = Gtk.Popover()
        popover.add(grid)
        self.shortcuts = popover
        button = Gtk.MenuButton(label="Shortcuts")
        button.set_popover(popover)
        return button

    def build_output(self):
        row = Gtk.Box(spacing=8)
        row.pack_start(Gtk.Label(label="Apply as"), False, False, 0)
        self.output = Choice(
            [
                Option("selection", "GIMP selection"),
                Option("mask", "New layer mask"),
            ]
        )
        self.output.connect("changed", lambda _: self.refresh_output())
        row.pack_start(self.output, False, False, 0)
        self.combine_row = Gtk.Box(spacing=8)
        self.combine_row.set_margin_start(16)
        self.combine_row.pack_start(Gtk.Label(label="GIMP's current selection"), False, False, 0)
        self.combine = combo(
            [
                ("new", "Replace it"),
                ("add", "Add to it"),
                ("subtract", "Subtract from it"),
                ("intersect", "Intersect with it"),
            ]
        )
        self.combine_row.pack_start(self.combine, False, False, 0)
        self.combine_row.show_all()
        self.combine_row.set_no_show_all(True)
        row.pack_start(self.combine_row, False, False, 0)
        return row

    def refresh_output(self):
        """Match output options to the layer and GIMP selection, which can change meanwhile."""
        if not (self.image.is_valid() and self.layer.is_valid()):
            return False
        has_mask = self.layer.get_mask() is not None
        self.output.set_option_sensitive(
            "mask", not has_mask, "This layer already has a mask. Remove it in GIMP first."
        )
        if has_mask and self.output.get_active_id() == "mask":
            self.output.set_active_id("selection")
        to_selection = self.output.get_active_id() == "selection"
        has_selection = not Gimp.Selection.is_empty(self.image)
        self.combine_row.set_visible(to_selection and has_selection)
        if not has_selection:
            self.combine.set_active_id("new")
        self.apply_button.set_label("Apply _Selection" if to_selection else "Add Layer _Mask")
        self.apply_button.set_use_underline(True)
        return False

    def cancel_requests(self):
        self.epoch += 1
        if self.hover_timer is not None:
            GLib.source_remove(self.hover_timer)
            self.hover_timer = None
        if hasattr(self, "bridge"):
            self.bridge.discard_pending()
        self.locked = False
        self.pending = False
        self.busy_title = None
        self.current_request = -1

    def invalidate(self):
        self.cancel_requests()
        self.candidate = None
        self.candidates = []
        self.hover_point = None
        self.ghost = None
        self.hover_memory = None

    def controls_changed(self, widget):
        if widget is self.model and self.image_ready:
            self.warm_model()
            return
        # A running request survives setting changes; they apply to the next pick.
        if self.locked or self.worker_dead:
            return
        self.invalidate()
        self.pointer = None
        if self.ready:
            self.status.set_text(TOOL_HINTS[self.tool.get_active_id()])
        self.update()

    def scope_changed(self, _):
        if self.locked:
            return
        if self.pending:
            self.invalidate()
            self.pointer = None
            self.status.set_text(TOOL_HINTS[self.tool.get_active_id()])
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
            self.status.set_text(self.candidate_status())
        self.update()

    def worker_ready(self):
        self.image_ready = True
        self.warm_model()

    def warm_model(self):
        self.invalidate()
        epoch = self.epoch
        self.ready = False
        self.load_failed = False
        self.hide_error()
        self.warming = self.locked = self.pending = True
        self.loading_title = f"Loading {self.model.get_active_text().split(' · ')[0]}"
        self.busy_detail.set_text("Loading model · first use may download weights")
        self.status.set_text("Picking starts when the model is ready.")
        self.update()

        def finished(result):
            if epoch != self.epoch or self.closed:
                return
            self.warming = self.locked = self.pending = False
            if "error" in result:
                self.load_failed = True
                self.failed(result["error"], retry=True)
                self.status.set_text("No model is loaded. Retry or choose another model.")
                return
            self.ready = True
            self.pointer = None
            self.status.set_text(f"Ready. {TOOL_HINTS[self.tool.get_active_id()]}")
            self.update()

        self.current_request = self.bridge.submit(
            {"op": "warmup", "model": self.model.get_active_id()}, finished
        )

    def progress(self, request_id, message):
        if request_id in (0, self.current_request):
            if self.showing_busy_card():
                self.busy_detail.set_text(message)
                self.busy_detail.show()
            else:
                self.status.set_text(message)

    def failed(self, message, retry=False):
        self.warming = False
        self.locked = False
        self.pending = False
        self.busy_title = None
        self.ghost = None
        self.show_error(message, retry)
        self.status.set_text("Your picks were not changed.")
        self.update()

    def worker_died(self, message):
        self.invalidate()
        self.ready = self.image_ready = False
        self.worker_dead = self.load_failed = True
        self.model.set_sensitive(False)
        self.failed(message)
        self.status.set_text("Picking stopped. You can still apply your picks.")
        self.update()

    def show_error(self, message, retry=False):
        self.error_label.set_text(message)
        self.retry_button.set_visible(retry)
        # Retry is the only way to reload the same model, so it can't be dismissed.
        self.error_bar.set_show_close_button(not retry)
        self.error_bar.show()

    def hide_error(self):
        self.error_bar.hide()

    def error_response(self, _, response):
        self.hide_error()
        if response == Gtk.ResponseType.ACCEPT:
            self.warm_model()

    def overlay_child_position(self, overlay, child, allocation):
        """Size the wrapping error bar for the overlay's width, unlike GTK's default."""
        if child is not self.error_bar:
            return False
        width = overlay.get_allocated_width()
        allocation.x = allocation.y = 0
        allocation.width = width
        allocation.height = child.get_preferred_height_for_width(width).natural_height
        return True

    def showing_busy_card(self):
        return bool(self.busy_title) or (not self.ready and not self.load_failed)

    def pick_mode(self):
        return effective_mode(
            self.mode.get_active_id(),
            shift=bool(self.modifiers & Gdk.ModifierType.SHIFT_MASK),
            ctrl=bool(self.modifiers & Gdk.ModifierType.CONTROL_MASK),
            alt=bool(self.modifiers & Gdk.ModifierType.MOD1_MASK),
        )

    def set_modifiers(self, state):
        modifiers = state & MODIFIERS
        if modifiers != self.modifiers:
            self.modifiers = modifiers
            self.pick_feedback()
        return False

    def pick_feedback(self):
        """Show what a click would do now, without touching the model."""
        if self.candidate is not None and not self.locked:
            self.status.set_text(self.candidate_status())
        self.update()

    def candidate_status(self):
        text = f"Click to {PICK_ACTIONS[self.pick_mode()].lower()}"
        if len(self.candidates) > 1:
            text += f" · scroll for more ({self.candidate_index + 1} of {len(self.candidates)})"
        return text

    def update(self):
        self.revision += 1
        self.area.queue_draw()
        # update() runs on every hover move, and scanning a large mask is too slow for that.
        if self.summary_revision != self.selection.revision:
            self.summary_revision = self.selection.revision
            self.has_selection = self.selection.mask.getbbox() is not None
            self.summary.set_text(
                f"{coverage(self.selection.mask):.1%} of the layer picked"
                if self.has_selection
                else "Nothing picked yet"
            )
        has_selection = self.has_selection
        self.apply_button.set_sensitive(has_selection and not self.locked)
        if not has_selection:
            blocked = "Nothing picked yet."
        elif self.locked:
            blocked = "Waiting for the current request."
        else:
            blocked = None
        self.apply_button.set_tooltip_text(blocked)
        can_edit = self.ready and has_selection and not self.locked
        no_model = None if self.ready else "No model is ready."
        for button, label in ((self.refine_button, "ViTMatte"), (self.cleanup_button, None)):
            button.set_sensitive(can_edit)
            button.set_tooltip_text(
                blocked or no_model or (f"Runs {label} on the picked edges." if label else None)
            )
        coarse = self.selection.unrefined()
        self.refine_all_button.set_sensitive(self.ready and coarse > 0 and not self.locked)
        self.refine_all_button.set_tooltip_text(
            f"Refine the coarse edges of {coarse} pick{'' if coarse == 1 else 's'}."
            if coarse
            else "Every pick already has refined edges."
        )
        self.clear_button.set_sensitive(has_selection and not self.locked)
        self.undo_button.set_sensitive(bool(self.selection.history) and not self.locked)
        self.redo_button.set_sensitive(bool(self.selection.future) and not self.locked)
        many = len(self.candidates) > 1
        self.previous_button.set_sensitive(many)
        self.next_button.set_sensitive(many)
        self.candidate_label.set_text(
            f"{self.candidate_index + 1} of {len(self.candidates)}"
            if self.candidates
            else "None yet"
        )
        mode = self.pick_mode()
        tool = self.tool.get_active_id()
        self.mode.highlight(mode)
        self.mode.set_sensitive(tool not in FIXED_MODE_HINTS)
        hint = FIXED_MODE_HINTS.get(tool) or MODE_HINTS[mode]
        if hint != self.mode_hint.get_text():
            self.mode_hint.set_text(hint)
        action = PICK_ACTIONS[mode]
        if action != self.accept_button.get_label():
            self.accept_button.set_label(action)
        self.accept_button.set_sensitive(self.candidate is not None and not self.locked)
        busy = self.showing_busy_card()
        self.busy_title_label.set_text(self.busy_title or self.loading_title)
        self.busy_detail.set_visible(bool(self.busy_detail.get_text()))
        self.busy_card.set_visible(busy)
        self.busy_spinner.set_active(busy)
        self.spinner.set_active(self.pending and not busy)
        self.sync_cursor()

    def sync_cursor(self):
        window = self.area.get_window()
        if window is None:
            return
        if self.pan:
            name = "grabbing"
        elif self.locked or (not self.ready and not self.load_failed):
            name = "progress"
        elif not self.ready:
            name = "not-allowed"
        else:
            name = "crosshair"
        if name != self.cursor_name:
            self.cursor_name = name
            window.set_cursor(Gdk.Cursor.new_from_name(window.get_display(), name))

    def location(self, event):
        return image_coordinates(
            event.x,
            event.y,
            (self.area.get_allocated_width(), self.area.get_allocated_height()),
            self.source.size,
        )

    def geometry(self):
        width, height = self.area.get_allocated_width(), self.area.get_allocated_height()
        scale = min(width / self.source.width, height / self.source.height)
        return (
            scale,
            (width - self.source.width * scale) / 2,
            (height - self.source.height * scale) / 2,
        )

    def set_zoom(self, factor, anchor=None):
        """Zoom to a factor, or fit when None, keeping the anchor's image point in place."""
        if factor is not None:
            factor = min(factor, MAX_CANVAS_SIDE / max(self.source.size))
        # No allocation follows an unchanged size, so an anchor would linger until a later one.
        if factor == self.zoom_factor:
            return
        horizontal = self.preview_scroll.get_hadjustment()
        vertical = self.preview_scroll.get_vadjustment()
        allocation = self.area.get_allocation()
        if anchor is None:
            anchor = (
                horizontal.get_value() + horizontal.get_page_size() / 2 - allocation.x,
                vertical.get_value() + vertical.get_page_size() / 2 - allocation.y,
            )
        scale, left, top = self.geometry()
        self.zoom_anchor = (
            (anchor[0] - left) / scale,
            (anchor[1] - top) / scale,
            anchor[0] + allocation.x - horizontal.get_value(),
            anchor[1] + allocation.y - vertical.get_value(),
        )
        self.zoom_factor = factor
        if factor is None:
            self.area.set_size_request(400, 350)
            align = Gtk.Align.FILL
        else:
            # Centering keeps the allocation at the exact zoom when the image fits the viewport.
            self.area.set_size_request(
                max(1, round(self.source.width * factor)),
                max(1, round(self.source.height * factor)),
            )
            align = Gtk.Align.CENTER
        self.area.set_halign(align)
        self.area.set_valign(align)
        self.pointer = None
        self.update()

    def zoom_step(self, direction, anchor=None):
        scale, _, _ = self.geometry()
        horizontal = self.preview_scroll.get_hadjustment()
        vertical = self.preview_scroll.get_vadjustment()
        fit = min(
            horizontal.get_page_size() / self.source.width,
            vertical.get_page_size() / self.source.height,
        )
        if direction > 0:
            target = next((z for z in ZOOM_LEVELS if z > scale * 1.01), ZOOM_LEVELS[-1])
        else:
            target = next((z for z in reversed(ZOOM_LEVELS) if z < scale * 0.99), ZOOM_LEVELS[0])
        # Stop at fit when zooming out past it, but never let "zoom out" enlarge a small image.
        self.set_zoom(None if target <= fit < scale * 0.99 else target, anchor)

    def area_allocated(self, _, allocation):
        scale, left, top = self.geometry()
        self.zoom_label.set_text(f"{scale:.0%}")
        if self.zoom_anchor is None:
            return
        image_x, image_y, view_x, view_y = self.zoom_anchor
        self.zoom_anchor = None
        self.preview_scroll.get_hadjustment().set_value(
            allocation.x + left + image_x * scale - view_x
        )
        self.preview_scroll.get_vadjustment().set_value(
            allocation.y + top + image_y * scale - view_y
        )

    def motion(self, _, event):
        self.set_modifiers(event.state)
        if self.pan and not event.state & Gdk.ModifierType.BUTTON2_MASK:
            # The window manager can break the grab, so the release never arrives.
            self.pan = None
            self.sync_cursor()
        if self.pan:
            start_x, start_y, start_h, start_v = self.pan
            self.preview_scroll.get_hadjustment().set_value(start_h + start_x - event.x_root)
            self.preview_scroll.get_vadjustment().set_value(start_v + start_y - event.y_root)
            return True
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
        # Keep a faint preview while the pointer stays on the object, so hovering doesn't flicker.
        previous = self.candidate or self.ghost
        memory = (
            (self.candidates, self.candidate_index, self.hover_point)
            if self.candidates
            else self.hover_memory
        )
        self.invalidate()
        self.ghost = previous if previous and previous.getpixel(point) > 127 else None
        self.hover_memory = memory
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

    def enter(self, _, event):
        # Canvas shortcuts must reach the canvas after using a spin button in the sidebar.
        if event.mode == Gdk.CrossingMode.NORMAL:
            self.area.grab_focus()
        return False

    def leave(self, *_):
        self.pointer_inside = False
        self.scroll_delta = 0.0
        if not self.locked and not self.drag:
            if self.pending and self.ready:
                self.status.set_text(TOOL_HINTS[self.tool.get_active_id()])
            self.cancel_requests()
            self.ghost = None
            self.restore_hover()
            if self.candidate is None:
                self.pointer = None
            self.update()
        return False

    def restore_hover(self):
        """Bring back the last candidate, since reaching the sidebar crosses other pixels."""
        if self.hover_memory and self.candidate is None:
            self.candidates, self.candidate_index, self.hover_point = self.hover_memory
            self.candidate = self.candidates[self.candidate_index]
            self.status.set_text(self.candidate_status())
        self.hover_memory = None

    def press(self, _, event):
        if event.button == 2:
            self.pan = (
                event.x_root,
                event.y_root,
                self.preview_scroll.get_hadjustment().get_value(),
                self.preview_scroll.get_vadjustment().get_value(),
            )
            self.sync_cursor()
            return True
        if event.button not in (1, 3) or not self.ready or self.locked:
            return False
        point = self.location(event)
        if point is None:
            return False
        self.area.grab_focus()
        self.set_modifiers(event.state)
        mode = self.pick_mode()
        tool = self.tool.get_active_id()
        if event.button == 3 or tool == "exclude":
            if self.last_seed is None:
                self.status.set_text("Exclude only changes your last pick. Pick an object first.")
            else:
                self.predict(point, commit=True, mode="correction")
        elif tool == "box":
            self.invalidate()
            self.drag = [point, point, mode]
        elif tool == "include":
            self.predict(point, commit=True, mode="add")
        elif self.candidate is not None and point == self.hover_point:
            self.commit_candidate(mode)
        else:
            self.predict(point, commit=True, mode=mode)
        return True

    def release(self, _, event):
        if event.button == 2 and self.pan:
            self.pan = None
            self.sync_cursor()
            return True
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

    def predict(self, point, commit=False, mode="new", box=None, candidate=None):
        if not self.ready:
            return
        chosen, ghost, memory = self.candidate, self.ghost, self.hover_memory
        self.invalidate()
        if commit:
            self.hide_error()
            # Keep showing what was clicked while its edges are refined.
            self.ghost = chosen
        else:
            self.ghost, self.hover_memory = ghost, memory
        epoch = self.epoch
        self.locked = commit
        self.pending = True
        points = [] if point is None else [{"x": point[0], "y": point[1], "label": 1}]
        if mode == "correction":
            points = self.last_points + [{"x": point[0], "y": point[1], "label": 0}]
        smaller = self.scope.get_active_id() == "part"
        previous_base = self.last_base.copy() if mode == "correction" else None
        refining = commit and self.auto_refine.get_active()
        if refining:
            self.busy_title = "Refining edges (Esc cancels)"
            self.busy_detail.set_text("")
        self.status.set_text("Finding object…" if not commit else "Selecting object…")
        self.update()

        def finished(result):
            if epoch != self.epoch or self.closed:
                return
            self.locked = False
            self.pending = False
            self.busy_title = None
            self.ghost = None
            self.hover_memory = None
            if "error" in result:
                self.failed(result["error"])
                return
            masks = result["masks"]
            if not masks:
                self.status.set_text("No candidate found. Try Include or a box.")
                self.update()
                return
            if commit:
                mask = masks[0]
                pick = Pick(request, mode, pack(mask) if request["refine"] else None)
                if mask.getbbox() is None:
                    self.status.set_text("Nothing was selected there. Try Include or a box.")
                    self.update()
                elif mode == "correction":
                    # Replace only the most recently picked object, preserving other picks.
                    merged = ImageChops.lighter(previous_base, mask)
                    self.accept(merged, "new", self.last_seed, pick)
                    self.last_base = previous_base
                    self.last_points = points
                else:
                    self.accept(mask, mode, point or self.mask_seed(mask), pick)
                return
            mask = pick_candidate(masks, point, smaller)
            if mask.getbbox() is None:
                self.status.set_text("No candidate contains the pointer. Try Include or a box.")
                self.update()
                return
            self.candidates = masks
            self.candidate = mask
            self.candidate_index = next(i for i, item in enumerate(masks) if item is mask)
            self.hover_point = point
            self.hide_error()
            self.status.set_text(self.candidate_status())
            self.update()

        model = self.model.get_active_id()
        if commit:
            request = {
                "op": "pick",
                "model": model,
                "points": points,
                "box": box,
                "candidate": candidate,
                "smaller": smaller,
                "continue": mode == "correction",
                "refine": self.auto_refine.get_active(),
            }
        else:
            request = {"op": "predict", "model": model, "points": points, "preview": True}
        self.current_request = self.bridge.submit(request, finished)

    def commit_candidate(self, mode):
        """Refine the candidate on screen. Its index still means the same mask to the worker."""
        self.predict(self.hover_point, commit=True, mode=mode, candidate=self.candidate_index)

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

    def accept(self, mask, mode, seed, pick=None):
        self.last_base = self.selection.mask.copy() if mode == "add" else Image.new("L", mask.size)
        self.selection.accept(mask, mode, pick)
        self.last_seed = seed if mode in ("new", "add") else None
        self.last_points = [{"x": seed[0], "y": seed[1], "label": 1}] if seed else []
        self.invalidate()
        self.hide_error()
        self.pointer = None
        self.status.set_text("Picked. Keep picking or apply.")
        self.update()

    def accept_candidate(self):
        if self.candidate is not None and not self.locked:
            self.commit_candidate(self.pick_mode())

    def next_candidate(self, step=1):
        if self.candidates:
            self.cancel_requests()
            self.candidate_index = (self.candidate_index + step) % len(self.candidates)
            self.candidate = self.candidates[self.candidate_index]
            self.status.set_text(self.candidate_status())
            self.update()

    def scroll_candidates(self, _, event):
        if event.state & Gdk.ModifierType.CONTROL_MASK:
            return self.scroll_zoom(event)
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

    def scroll_zoom(self, event):
        anchor = (event.x, event.y)
        if event.direction == Gdk.ScrollDirection.UP:
            self.zoom_step(1, anchor)
        elif event.direction == Gdk.ScrollDirection.DOWN:
            self.zoom_step(-1, anchor)
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            valid, _, dy = event.get_scroll_deltas()
            if valid:
                self.scroll_delta += dy
                if abs(self.scroll_delta) >= 1:
                    self.zoom_step(-1 if self.scroll_delta > 0 else 1, anchor)
                    self.scroll_delta = 0.0
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
            self.status.set_text("Mask cleaned. Undo restores the previous mask.")

        self.current_request = self.bridge.submit(
            {
                "op": "cleanup",
                "mask": encode(self.selection.mask),
                "cleanup": self.cleanup_settings(),
            },
            finished,
        )

    def undo(self):
        if self.locked or not self.selection.history:
            return
        self.invalidate()
        self.selection.undo()
        self.last_seed = None
        self.status.set_text("Undid the last change.")
        self.update()

    def redo(self):
        if self.locked or not self.selection.future:
            return
        self.invalidate()
        self.selection.redo()
        self.last_seed = None
        self.status.set_text("Restored the change.")
        self.update()

    def clear(self):
        self.invalidate()
        self.selection.clear()
        self.last_seed = None
        self.status.set_text("Picks cleared. Undo restores them.")
        self.update()

    def refine_all(self):
        """Refine every coarse pick in order, then show the result as one undo step."""
        if self.locked or not self.selection.unrefined():
            return
        self.invalidate()
        epoch = self.epoch
        origin, picks = self.selection.origin, self.selection.picks
        plan = refine_plan(picks)
        total = self.selection.unrefined()
        results = {}
        self.locked = self.pending = True
        self.hide_error()

        def finish():
            self.locked = self.pending = False
            self.busy_title = None
            refined = [
                Pick(pick.request, pick.mode, pack(results[index]))
                if index in results and pick.mask is None
                else pick
                for index, pick in enumerate(picks)
            ]
            mask, base = replay(origin, refined, results)
            self.selection.replace(mask, refined)
            self.last_base = base
            self.pointer = None
            self.status.set_text("Picks refined. Undo restores the coarse edges.")
            self.update()

        def run(step):
            if step == len(plan):
                finish()
                return
            index, refining = plan[step]
            if refining:
                done = sum(1 for item, flag in plan[:step] if flag)
                self.busy_title = f"Refining pick {done + 1} of {total} (Esc cancels)"
                self.busy_detail.set_text("")
                self.status.set_text("Refining all picks…")
                self.update()

            def finished(result):
                if epoch != self.epoch or self.closed:
                    return
                if "error" in result:
                    self.failed(result["error"])
                    return
                if refining:
                    results[index] = (
                        result["masks"][0] if result["masks"] else Image.new("L", origin.size)
                    )
                run(step + 1)

            self.current_request = self.bridge.submit(
                {**picks[index].request, "refine": refining}, finished
            )

        run(0)

    def refine(self):
        self.invalidate()
        epoch = self.epoch
        self.locked = True
        self.pending = True
        self.busy_title = "Refining edges (Esc cancels)"
        self.busy_detail.set_text("")
        self.hide_error()
        matte_output = self.matte_output.get_active_id()
        self.status.set_text("Refining edges…")
        self.update()

        def finished(result):
            if epoch != self.epoch or self.closed:
                return
            self.locked = False
            self.pending = False
            self.busy_title = None
            if "error" in result:
                self.failed(result["error"])
                return
            self.accept(result["masks"][0], "new", None)
            self.view.set_active_id("cutout")
            self.status.set_text(
                ("Crisp boundary refined." if matte_output == "crisp" else "Soft matte refined.")
                + " Showing it on transparency. Undo restores the previous mask."
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

    def draw(self, _, cr):
        scale, left, top = self.geometry()
        x1, y1, x2, y2 = cr.clip_extents()
        horizontal = self.preview_scroll.get_hadjustment()
        vertical = self.preview_scroll.get_vadjustment()
        if horizontal.get_page_size() and vertical.get_page_size():
            allocation = self.area.get_allocation()
            x1 = max(x1, horizontal.get_value() - allocation.x)
            y1 = max(y1, vertical.get_value() - allocation.y)
            x2 = min(x2, horizontal.get_value() + horizontal.get_page_size() - allocation.x)
            y2 = min(y2, vertical.get_value() + vertical.get_page_size() - allocation.y)
        crop = (
            max(0, math.floor((x1 - left) / scale)),
            max(0, math.floor((y1 - top) / scale)),
            min(self.source.width, math.ceil((x2 - left) / scale)),
            min(self.source.height, math.ceil((y2 - top) / scale)),
        )
        # Match the theme, as the area outside a zoomed image does.
        Gtk.render_background(
            self.get_style_context(),
            cr,
            0,
            0,
            self.area.get_allocated_width(),
            self.area.get_allocated_height(),
        )
        if crop[2] <= crop[0] or crop[3] <= crop[1]:
            return False
        size = (
            max(1, round((crop[2] - crop[0]) * scale)),
            max(1, round((crop[3] - crop[1]) * scale)),
        )
        # Show real pixels when inspecting edges up close.
        resample = Image.Resampling.NEAREST if scale >= 2 else Image.Resampling.BILINEAR
        key = (crop, size, self.revision)
        if key != self.render_key:
            # Cutout views of an empty selection would leave nothing visible to hover over.
            view = self.view.get_active_id() if self.has_selection else "overlay"
            base_key = (crop, size, view, self.selection.revision)
            if base_key != self.base_key:
                self.base_image = render_base(
                    self.source.crop(crop).resize(size, resample),
                    self.selection.mask.crop(crop).resize(size, resample),
                    view,
                    (round(crop[0] * scale), round(crop[1] * scale)),
                )
                self.base_key = base_key
            result = self.base_image
            hover = self.candidate or self.ghost
            if hover is not None:
                result = render_candidate(
                    result,
                    hover.crop(crop).resize(size, resample),
                    self.pick_mode(),
                    faint=self.candidate is None,
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
        Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, left + crop[0] * scale, top + crop[1] * scale)
        cr.paint()
        if self.drag:
            self.draw_box(cr, scale, left, top)
        return False

    def draw_box(self, cr, scale, left, top):
        start, end, _ = self.drag
        cr.rectangle(
            left + min(start[0], end[0]) * scale,
            top + min(start[1], end[1]) * scale,
            abs(end[0] - start[0]) * scale,
            abs(end[1] - start[1]) * scale,
        )
        cr.set_source_rgba(0.39, 0.67, 1, 0.15)
        cr.fill_preserve()
        cr.set_line_width(1.5)
        cr.set_source_rgb(0, 0, 0)
        cr.stroke_preserve()
        cr.set_dash([5, 5])
        cr.set_source_rgb(1, 1, 1)
        cr.stroke()
        cr.set_dash([])

    def typing(self):
        return isinstance(self.get_focus(), Gtk.Editable)

    def key_release(self, _, event):
        self.set_modifiers(event.state & ~MODIFIER_KEYS.get(event.keyval, 0))
        return False

    def key(self, _, event):
        self.set_modifiers(event.state | MODIFIER_KEYS.get(event.keyval, 0))
        if self.shortcuts.get_visible():
            return False
        over_canvas = self.pointer_inside or self.area.has_focus()
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        if over_canvas and self.candidates and event.keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            reverse = (
                event.keyval == Gdk.KEY_ISO_Left_Tab or event.state & Gdk.ModifierType.SHIFT_MASK
            )
            self.next_candidate(-1 if reverse else 1)
            return True
        if event.keyval == Gdk.KEY_Escape:
            return self.escape()
        if self.typing():
            return False
        if ctrl:
            lower = Gdk.keyval_to_lower(event.keyval)
            shift = event.state & Gdk.ModifierType.SHIFT_MASK
            if lower == Gdk.KEY_y or (lower == Gdk.KEY_z and shift):
                self.redo()
                return True
            if lower == Gdk.KEY_z:
                self.undo()
                return True
            return False
        if event.state & Gdk.ModifierType.MOD1_MASK:
            return False
        return self.plain_key(event.keyval, over_canvas)

    def escape(self):
        """Cancel work first; Esc never discards picks, since it's pressed to stop a request."""
        if not self.warming and (self.pending or self.hover_timer or self.candidate or self.drag):
            self.invalidate()
            self.drag = None
            self.status.set_text("Cancelled.")
            self.update()
        elif self.has_selection:
            self.status.set_text("Your picks are kept. Use Cancel to close without applying.")
        else:
            self.response(Gtk.ResponseType.CANCEL)
        return True

    def plain_key(self, keyval, over_canvas):
        lower = Gdk.keyval_to_lower(keyval)
        if lower in TOOL_KEYS:
            self.tool.set_active_id(TOOL_KEYS[lower])
        elif keyval == Gdk.KEY_space and over_canvas and self.candidate is not None:
            self.accept_candidate()
        elif keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self.zoom_step(1)
        elif keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            self.zoom_step(-1)
        elif keyval in (Gdk.KEY_0, Gdk.KEY_KP_0):
            self.set_zoom(None)
        elif keyval in (Gdk.KEY_1, Gdk.KEY_KP_1):
            self.set_zoom(1)
        else:
            return False
        return True

    def run_selection(self):
        GimpUi.window_set_transient(self)
        self.show_all()
        self.area.grab_focus()
        try:
            while self.run() == Gtk.ResponseType.OK:
                try:
                    if self.layer.is_valid() and self.layer.get_offsets()[-2:] != self.origin:
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
