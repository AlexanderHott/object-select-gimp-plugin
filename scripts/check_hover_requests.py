"""Run with GIMP's Python to check on-demand hover and stale-result handling."""

import sys
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import patch

import gi

gi.require_version("Gimp", "3.0")
from gi.repository import Gimp

sys.path.append(Gimp.directory() + "/plug-ins/object-select/vendor")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gimp-plugin" / "object-select"))
from PIL import Image
from selection import Selection
from ui import HoverDialog


class Bridge:
    def __init__(self):
        self.requests = []

    def submit(self, request, callback):
        self.requests.append((request, callback))
        return len(self.requests)

    def discard_pending(self):
        pass


def controller():
    state = SimpleNamespace(
        epoch=0,
        hover_timer=None,
        closed=False,
        bridge=Bridge(),
        scope=SimpleNamespace(get_active_id=lambda: "object"),
        model=SimpleNamespace(
            get_active_id=lambda: "sam2-large", get_active_text=lambda: "SAM 2.1 large"
        ),
        mode=SimpleNamespace(get_active_id=lambda: "new"),
        busy_detail=SimpleNamespace(set_text=lambda text: None),
        show_error=lambda message, retry=False: None,
        hide_error=lambda: None,
        set_modifiers=lambda state: None,
        modifiers=0,
        pan=None,
        ghost=None,
        hover_memory=None,
        busy_title=None,
        load_failed=False,
        worker_dead=False,
        tool=SimpleNamespace(get_active_id=lambda: "hover"),
        auto_refine=SimpleNamespace(get_active=lambda: False),
        status=SimpleNamespace(set_text=lambda text: None),
        update=lambda: None,
        candidate=None,
        candidates=[],
        candidate_index=0,
        selection=Selection((20, 20)),
        drag=None,
        pointer=None,
        locked=False,
        ready=True,
        image_ready=False,
        warming=False,
        location=lambda event: event.point,
    )
    for name in (
        "worker_ready",
        "warm_model",
        "controls_changed",
        "predict",
        "invalidate",
        "cancel_requests",
        "next_candidate",
        "accept",
        "leave",
        "failed",
        "motion",
        "scope_changed",
        "pick_mode",
        "restore_hover",
        "candidate_status",
        "refine_all",
    ):
        setattr(state, name, MethodType(getattr(HoverDialog, name), state))
    return state


large = Image.new("L", (20, 20), 255)
small = Image.new("L", (20, 20))
small.paste(255, (6, 6, 11, 11))


def hover(x, y):
    return SimpleNamespace(point=(x, y), state=0)


def reply(callback):
    callback({"masks": [large, small], "seconds": 0.02})


with patch("ui.GLib.timeout_add") as timer, patch("ui.GLib.source_remove"):
    timer.return_value = 123
    state = controller()
    state.worker_ready()
    assert state.bridge.requests[0][0] == {"op": "warmup", "model": "sam2-large"}
    assert state.warming and not state.ready
    state.motion(None, hover(8, 8))
    assert len(state.bridge.requests) == 1
    timer.assert_not_called()
    epoch = state.epoch
    state.controls_changed(state.tool)
    state.scope_changed(None)
    assert state.epoch == epoch, "Picking controls must not interrupt model loading"
    old_finished = state.bridge.requests[0][1]
    state.controls_changed(state.model)
    old_finished({"ready": True})
    assert not state.ready, "A stale model load must not enable picking"
    state.bridge.requests[-1][1]({"ready": True})
    assert state.ready and not state.warming and not state.locked
    assert all(request["op"] == "warmup" for request, _ in state.bridge.requests)
    state.bridge.requests.clear()
    print("PASS: startup and model changes encode without regions; loading blocks picks safely")

    state.motion(None, hover(8, 8))
    delay, callback = timer.call_args.args
    assert delay == 180 and state.bridge.requests == []
    callback()
    request, finished = state.bridge.requests[-1]
    assert request["op"] == "predict" and "remember" not in request
    assert request["points"] == [{"x": 8, "y": 8, "label": 1}]
    timer.reset_mock()
    reply(finished)
    assert state.candidate is large and not state.selection.mask.getbbox()
    timer.assert_not_called()
    state.motion(None, hover(8, 8))
    assert len(state.bridge.requests) == 1
    timer.assert_not_called()
    print("PASS: one exact-point prediction after dwell; stationary hover never replaces it")

    state.motion(None, hover(9, 8))
    assert state.candidate is None
    assert state.ghost is large, "Hovering within the object keeps a faint preview"
    assert timer.called, "A one-pixel move must be available for narrow regions"
    old_timer = timer.call_args.args[1]
    state.motion(None, hover(10, 8))
    old_timer()
    assert len(state.bridge.requests) == 1, "Obsolete dwell callbacks must not start predictions"
    timer.call_args.args[1]()
    assert state.bridge.requests[-1][0]["points"][0]["x"] == 10
    print("PASS: exact pixel targeting and stale dwell cancellation")

    for action in ("cycle", "accept", "move", "leave"):
        state = controller()
        state.predict((8, 8))
        old_finished = state.bridge.requests[-1][1]
        reply(old_finished)
        if action == "cycle":
            state.next_candidate()
        elif action == "accept":
            state.accept(large, "new", (8, 8))
        elif action == "move":
            state.predict((12, 12))
        else:
            state.leave()
        kept = state.candidate
        old_finished({"masks": [small.copy()], "seconds": 0.02})
        assert state.candidate is kept, action
    print("PASS: cycling, acceptance, movement and leaving reject stale results")

    state = controller()
    state.predict((8, 8))
    reply(state.bridge.requests[-1][1])
    state.motion(None, hover(9, 8))
    assert state.candidate is None
    state.leave()
    assert state.candidate is large and state.hover_point == (8, 8)
    print("PASS: leaving the image restores the candidate so sidebar buttons can use it")

    state = controller()
    state.predict((8, 8), commit=True)
    reply(state.bridge.requests[-1][1])
    assert state.selection.mask.getbbox() == large.getbbox()
    state.predict((8, 8))
    state.bridge.requests[-1][1]({"masks": [], "seconds": 0.02})
    assert state.candidate is None
    assert state.selection.mask.getbbox() == large.getbbox()
    print("PASS: direct click selects; an empty result preserves the accepted selection")

    state = controller()
    state.predict((8, 8), commit=True)
    assert state.bridge.requests[-1][0]["refine"] is False
    reply(state.bridge.requests[-1][1])
    state.predict((8, 8), commit=True, mode="subtract")
    state.bridge.requests[-1][1]({"masks": [small]})
    assert state.selection.unrefined() == 2
    coarse = state.selection.mask.tobytes()
    state.bridge.requests.clear()
    state.refine_all()
    assert state.locked and state.busy_title.startswith("Refining pick 1 of 2")
    assert state.selection.mask.tobytes() == coarse, "Only a finished replay may change picks"
    request, finished = state.bridge.requests[-1]
    assert request["op"] == "pick" and request["refine"] is True and request["points"][0]["x"] == 8
    finished({"masks": [large]})
    assert state.busy_title.startswith("Refining pick 2 of 2")
    state.bridge.requests[-1][1]({"masks": [small]})
    assert not state.locked and state.selection.unrefined() == 0
    assert (
        state.selection.mask.getpixel((8, 8)) == 0 and state.selection.mask.getpixel((1, 1)) == 255
    )
    state.selection.undo()
    assert state.selection.mask.tobytes() == coarse and state.selection.unrefined() == 2
    print("PASS: Refine all replays every pick in order as one undo step")

    state.refine_all()
    finished = state.bridge.requests[-1][1]
    state.invalidate()
    finished({"masks": [large]})
    assert state.selection.mask.tobytes() == coarse and len(state.selection.picks) == 2
    state.refine_all()
    state.bridge.requests[-1][1]({"error": "boom"})
    assert not state.locked and state.selection.mask.tobytes() == coarse
    print("PASS: cancelling or failing Refine all leaves the picks untouched")
