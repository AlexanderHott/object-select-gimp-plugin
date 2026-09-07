"""Run using GIMP's Python: regression checks for hovering without keyboard focus."""

import sys
from pathlib import Path
from types import SimpleNamespace

import gi

gi.require_version("Gdk", "3.0")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gimp-plugin" / "object-select"))
from gi.repository import Gdk
from ui import HoverDialog

steps = []
controller = SimpleNamespace(
    warming=False,
    area=SimpleNamespace(has_focus=lambda: False),
    pointer_inside=True,
    candidates=[object(), object(), object()],
    next_candidate=lambda step=1: steps.append(step),
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
    controller, None, SimpleNamespace(direction=Gdk.ScrollDirection.DOWN)
)
assert steps[-1] == 1
assert HoverDialog.scroll_candidates(
    controller, None, SimpleNamespace(direction=Gdk.ScrollDirection.UP)
)
assert steps[-1] == -1
event = SimpleNamespace(
    direction=Gdk.ScrollDirection.SMOOTH, get_scroll_deltas=lambda: (True, 0, 0.4)
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
