"""Run using GIMP's Python: regression checks for hovering without keyboard focus."""

import sys
from pathlib import Path
from types import SimpleNamespace

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gimp", "3.0")
from gi.repository import Gimp

sys.path.append(Gimp.directory() + "/plug-ins/object-select/vendor")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gimp-plugin" / "object-select"))
from gi.repository import Gdk, Gtk
from ui import HoverDialog

steps = []
controller = SimpleNamespace(
    warming=False,
    area=SimpleNamespace(has_focus=lambda: False),
    pointer_inside=True,
    candidates=[object(), object(), object()],
    next_candidate=lambda step=1: steps.append(step),
    set_modifiers=lambda state: None,
    shortcuts=SimpleNamespace(get_visible=lambda: False),
)
event = SimpleNamespace(keyval=Gdk.KEY_Tab, state=0)
assert HoverDialog.key(controller, None, event), "Tab must cycle on hover before the first click"
assert steps == [1], "Tab did not advance the candidate"
print("PASS: Tab cycles candidates while hovered without keyboard focus")

event = SimpleNamespace(keyval=Gdk.KEY_ISO_Left_Tab, state=Gdk.ModifierType.SHIFT_MASK)
assert HoverDialog.key(controller, None, event)
assert steps[-1] == -1
print("PASS: Shift+Tab goes to the previous candidate")

controller.location = lambda _: (10, 10)
controller.locked = False
controller.drag = None
controller.scroll_delta = 0.0
assert HoverDialog.scroll_candidates(
    controller, None, SimpleNamespace(direction=Gdk.ScrollDirection.DOWN, state=0)
)
assert steps[-1] == 1
assert HoverDialog.scroll_candidates(
    controller, None, SimpleNamespace(direction=Gdk.ScrollDirection.UP, state=0)
)
assert steps[-1] == -1
event = SimpleNamespace(
    direction=Gdk.ScrollDirection.SMOOTH, get_scroll_deltas=lambda: (True, 0, 0.4), state=0
)
before = len(steps)
HoverDialog.scroll_candidates(controller, None, event)
HoverDialog.scroll_candidates(controller, None, event)
assert len(steps) == before, "Small touchpad deltas should accumulate"
HoverDialog.scroll_candidates(controller, None, event)
assert len(steps) == before + 1
controller.location = lambda _: None
assert not HoverDialog.scroll_candidates(controller, None, event)
print("PASS: mouse wheel and touchpad cycle within the image only")

zooms = []
controller.location = lambda _: (10, 10)
controller.zoom_step = lambda direction, anchor=None: zooms.append((direction, anchor))
controller.scroll_zoom = lambda event: HoverDialog.scroll_zoom(controller, event)
event = SimpleNamespace(
    direction=Gdk.ScrollDirection.UP, state=Gdk.ModifierType.CONTROL_MASK, x=4, y=6
)
before = len(steps)
assert HoverDialog.scroll_candidates(controller, None, event)
assert zooms == [(1, (4, 6))] and len(steps) == before
print("PASS: Ctrl+scroll zooms around the pointer instead of cycling")

tools = []
controller.tool = SimpleNamespace(set_active_id=tools.append)
controller.typing = lambda: False
controller.plain_key = lambda keyval, over: HoverDialog.plain_key(controller, keyval, over)
controller.warming = False
controller.pending = controller.hover_timer = controller.candidate = controller.drag = None
assert HoverDialog.key(controller, None, SimpleNamespace(keyval=Gdk.KEY_b, state=0))
assert tools == ["box"]
controller.typing = lambda: True
assert not HoverDialog.key(controller, None, SimpleNamespace(keyval=Gdk.KEY_e, state=0))
assert tools == ["box"], "Typing in a spin button must not switch tools"
print("PASS: tool shortcuts work unless a text field has focus")

closed = []
controller.response = closed.append
controller.status = SimpleNamespace(set_text=lambda text: None)
controller.update = lambda: None
controller.invalidate = lambda: closed.append("cancelled")
controller.escape = lambda: HoverDialog.escape(controller)
controller.has_selection = True
esc = SimpleNamespace(keyval=Gdk.KEY_Escape, state=0)
assert HoverDialog.key(controller, None, esc) and closed == [], "Esc must not discard picks"
controller.pending = True
assert HoverDialog.key(controller, None, esc) and closed == ["cancelled"]
controller.pending = False
controller.has_selection = False
closed.clear()
assert HoverDialog.key(controller, None, esc)
assert closed == [Gtk.ResponseType.CANCEL], "With no picks, Esc closes the dialog"
print("PASS: Esc cancels requests first and only closes when no picks would be lost")
