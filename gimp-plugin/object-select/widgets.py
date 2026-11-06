"""Small GTK building blocks for the Object Select dialog."""

from __future__ import annotations

import math
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GObject, Gtk, Pango

CSS = b"""
frame.object-select-busy {
    background-color: rgba(24, 25, 26, 0.92);
    color: white;
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 8px;
}
frame.object-select-busy > border { border: none; }
frame.object-select-busy * { background: none; color: white; }
infobar.object-select-error,
infobar.object-select-error > revealer > box {
    background-color: #6b1f24;
    background-image: none;
    color: white;
}
label.object-select-error-text { color: white; }
"""


@dataclass(frozen=True)
class Option:
    key: str
    label: str
    icon: str | None = None
    shortcut: str | None = None
    tooltip: str | None = None
    icon_only: bool = False


def install_css():
    """Give floating and error surfaces fixed colors that stay readable in any GIMP theme."""
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


def has_icon(name):
    return bool(name) and Gtk.IconTheme.get_default().has_icon(name)


class Choice(Gtk.Box):
    """A linked row of radio buttons with the id API of Gtk.ComboBoxText."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}  # noqa: RUF012

    def __init__(self, options, orientation=Gtk.Orientation.HORIZONTAL):
        super().__init__(orientation=orientation, homogeneous=True)
        self.get_style_context().add_class("linked")
        self.buttons = {}
        self.tooltips = {}
        self.highlighting = False
        self.active = options[0].key
        group = None
        for option in options:
            button = Gtk.RadioButton.new_from_widget(group)
            group = group or button
            button.set_mode(False)
            button.add(self.content(option))
            tooltip = option.tooltip or (option.label if option.icon_only else None)
            button.set_tooltip_text(tooltip)
            button.get_accessible().set_name(option.label)
            button.connect("toggled", self.toggled, option.key)
            self.pack_start(button, True, True, 0)
            self.buttons[option.key] = button
            self.tooltips[option.key] = tooltip

    @staticmethod
    def content(option):
        box = Gtk.Box(spacing=8)
        show_icon = has_icon(option.icon)
        if show_icon:
            box.pack_start(Gtk.Image.new_from_icon_name(option.icon, Gtk.IconSize.BUTTON), 0, 0, 0)
        if not (option.icon_only and show_icon):
            label = Gtk.Label(label=option.label, xalign=0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            box.pack_start(label, True, True, 0)
        if option.shortcut:
            hint = Gtk.Label(label=option.shortcut)
            hint.get_style_context().add_class("dim-label")
            box.pack_end(hint, False, False, 0)
        if option.icon_only and show_icon:
            box.set_halign(Gtk.Align.CENTER)
        return box

    def toggled(self, button, key):
        if button.get_active() and not self.highlighting:
            self.active = key
            self.emit("changed")

    def get_active_id(self):
        return self.active

    def highlight(self, key):
        """Light up an option without choosing it, as GIMP does while a modifier is held."""
        button = self.buttons[key]
        if not button.get_active():
            self.highlighting = True
            button.set_active(True)
            self.highlighting = False

    def set_active_id(self, key):
        self.buttons[key].set_active(True)

    def set_option_sensitive(self, key, sensitive, reason=None):
        button = self.buttons[key]
        button.set_sensitive(sensitive)
        button.set_tooltip_text(self.tooltips[key] if sensitive else reason)


def section(parent, title):
    """Pack a titled group, styled like GIMP's own tool option frames."""
    frame = Gtk.Frame(label=title)
    frame.set_shadow_type(Gtk.ShadowType.NONE)
    frame.get_label_widget().set_markup(f"<b>{title}</b>")
    group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    group.set_margin_top(6)
    frame.add(group)
    parent.pack_start(frame, False, False, 0)
    return group


def details(parent, title):
    """Pack a collapsed group for settings most picks never need."""
    expander = Gtk.Expander()
    heading = Gtk.Label(xalign=0)
    heading.set_markup(f"<b>{title}</b>")
    heading.set_margin_start(4)
    expander.set_label_widget(heading)
    group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    group.set_margin_top(6)
    expander.add(group)
    parent.pack_start(expander, False, False, 0)
    return group


def setting(parent, label, widget):
    """Pack a labelled control, with the label also naming it for assistive tools."""
    row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    caption = Gtk.Label(label=label, xalign=0)
    caption.set_mnemonic_widget(widget)
    row.pack_start(caption, False, False, 0)
    row.pack_start(widget, False, False, 0)
    parent.pack_start(row, False, False, 0)
    return row


def scrolls_past(widget):
    """Let the wheel scroll the sidebar rather than change a value under the pointer."""

    def forward(control, _):
        control.stop_emission_by_name("scroll-event")
        return False

    widget.connect("scroll-event", forward)
    return widget


def spin(low, high, step, value, tooltip=None):
    widget = Gtk.SpinButton.new_with_range(low, high, step)
    widget.set_value(value)
    widget.set_tooltip_text(tooltip)
    return scrolls_past(widget)


class Spinner(Gtk.DrawingArea):
    """A busy arc drawn with cairo, because GIMP's theme and icons include no GTK spinner."""

    def __init__(self, size=16):
        super().__init__()
        self.size = size
        self.tick = None
        self.set_size_request(size, size)
        self.set_valign(Gtk.Align.CENTER)
        self.connect("draw", self.paint)

    def set_active(self, active):
        if active == (self.tick is not None):
            return
        if active:
            self.tick = self.add_tick_callback(lambda *_: self.queue_draw() or True)
        else:
            self.remove_tick_callback(self.tick)
            self.tick = None
        self.queue_draw()

    def paint(self, _, cr):
        if self.tick is None:
            return False
        clock = self.get_frame_clock()
        turn = (clock.get_frame_time() / 1_000_000 if clock else 0) % 1
        color = self.get_style_context().get_color(self.get_state_flags())
        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.set_line_width(max(2, self.size / 8))
        center = self.get_allocated_width() / 2, self.get_allocated_height() / 2
        start = turn * 2 * math.pi
        cr.arc(*center, self.size / 2 - 2, start, start + 1.5 * math.pi)
        cr.stroke()
        return False


def icon_button(icon, tooltip, callback, fallback):
    button = Gtk.Button()
    if has_icon(icon):
        button.set_image(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON))
    else:
        button.set_label(fallback)
    button.set_tooltip_text(tooltip)
    button.get_accessible().set_name(tooltip)
    button.connect("clicked", lambda _: callback())
    return button


def text_button(parent, label, callback, tooltip=None):
    button = Gtk.Button(label=label)
    button.set_tooltip_text(tooltip)
    button.connect("clicked", lambda _: callback())
    parent.pack_start(button, True, True, 0)
    return button


def combo(options):
    widget = Gtk.ComboBoxText()
    for key, text in options:
        widget.append(key, text)
    widget.set_active(0)
    return widget
