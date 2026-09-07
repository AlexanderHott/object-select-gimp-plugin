import importlib.util
from pathlib import Path

import numpy as np
from PIL import Image

from comparison.models import Prediction
from object_select.masks import clean_mask
from object_select.worker import Session, decode, encode

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "hover_selection", ROOT / "gimp-plugin" / "object-select" / "selection.py"
)
selection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selection)


def test_mask_combination_and_undo_preserve_soft_coverage():
    state = selection.Selection((3, 1))
    first = Image.fromarray(np.array([[0, 128, 255]], dtype=np.uint8))
    second = Image.fromarray(np.array([[255, 64, 128]], dtype=np.uint8))
    state.accept(first)
    state.accept(second, "add")
    assert np.asarray(state.mask).ravel().tolist() == [255, 128, 255]
    state.accept(second, "subtract")
    assert np.asarray(state.mask).ravel().tolist() == [0, 64, 127]
    state.undo()
    assert np.asarray(state.mask).ravel().tolist() == [255, 128, 255]
    state.accept(second, "intersect")
    assert np.asarray(state.mask).ravel().tolist() == [255, 64, 128]
    state.clear()
    assert state.mask.getbbox() is None
    state.undo()
    assert np.asarray(state.mask).ravel().tolist() == [255, 64, 128]


def test_letterboxed_pointer_coordinates_and_outside_rejection():
    assert selection.image_coordinates(200, 100, (400, 400), (200, 100)) == (100, 0)
    assert selection.image_coordinates(399, 299, (400, 400), (200, 100)) == (199, 99)
    assert selection.image_coordinates(100, 99, (400, 400), (200, 100)) is None
    assert selection.image_coordinates(400, 200, (400, 400), (200, 100)) is None


def test_smaller_candidate_must_contain_the_hover_point():
    whole = Image.new("L", (10, 10), 255)
    unrelated = Image.new("L", (10, 10))
    unrelated.putpixel((0, 0), 255)
    part = Image.new("L", (10, 10))
    part.putpixel((5, 5), 255)
    part.putpixel((5, 6), 255)
    masks = [whole, unrelated, part]
    assert selection.pick_candidate(masks, (5, 5)) is whole
    assert selection.pick_candidate(masks, (5, 5), smaller=True) is part


def test_worker_preserves_layer_alpha_and_passes_correction_prompts():
    class Runner:
        def predict(self, request, image, parent, progress):
            assert request["points"] == [{"x": 1, "y": 0, "label": 1}, {"x": 2, "y": 0, "label": 0}]
            assert image.mode == "RGB"
            return Prediction([np.ones((1, 3))], [0.9], {"predict_seconds": 0.01})

    rgba = Image.new("RGBA", (3, 1), (255, 0, 0, 255))
    rgba.putalpha(Image.fromarray(np.array([[0, 128, 255]], dtype=np.uint8)))
    session = Session(Runner())
    session.handle({"op": "image", "image": encode(rgba)}, lambda _: None)
    result = session.handle(
        {"op": "predict", "points": [{"x": 1, "y": 0, "label": 1}, {"x": 2, "y": 0, "label": 0}]},
        lambda _: None,
    )
    assert np.asarray(decode(result["masks"][0])).ravel().tolist() == [0, 128, 255]


def test_candidate_cleanup_fills_pinhole_but_keeps_large_opening():
    mask = np.zeros((64, 64), dtype=np.float32)
    mask[8:56, 8:56] = 1
    mask[20, 20] = 0
    mask[30:42, 30:42] = 0

    class Runner:
        def predict(self, request, image, parent, progress):
            return Prediction([mask], [0.9], {"predict_seconds": 0.01})

    session = Session(Runner())
    session.handle(
        {"op": "image", "image": encode(Image.new("RGBA", (64, 64), "white"))}, lambda _: None
    )
    result = session.handle(
        {"op": "predict", "points": [], "cleanup": {"hole_area": 16, "smooth_radius": 0}},
        lambda _: None,
    )
    cleaned = np.asarray(decode(result["masks"][0]))
    assert cleaned[20, 20] == 255, "Tiny interior holes must be filled"
    assert cleaned[35, 35] == 0, "Large openings such as rocks must remain unselected"
    assert cleaned[0, 0] == 0, "Exterior background must stay unselected"


def test_candidate_cleanup_smooths_shape_without_feathering():
    mask = np.zeros((64, 64), dtype=np.float32)
    for y in range(10, 54):
        mask[y, 10:y] = 1

    class Runner:
        def predict(self, request, image, parent, progress):
            return Prediction([mask], [0.9], {"predict_seconds": 0.01})

    session = Session(Runner())
    session.handle(
        {"op": "image", "image": encode(Image.new("RGBA", (64, 64), "white"))}, lambda _: None
    )
    result = session.handle(
        {"op": "predict", "points": [], "cleanup": {"hole_area": 0, "smooth_radius": 1}},
        lambda _: None,
    )
    cleaned = np.asarray(decode(result["masks"][0]))
    assert set(np.unique(cleaned)) <= {0, 255}, "Shape smoothing must keep a crisp mask"
    assert cleaned[40, 20] == 255
    assert cleaned[0, 0] == 0


def test_cleanup_removes_small_external_islands_but_keeps_two_large_objects():
    mask = np.zeros((80, 100), dtype=np.float32)
    mask[10:40, 10:40] = 1
    mask[45:75, 60:90] = 1
    mask[3:5, 80:82] = 1
    result = clean_mask(mask, hole_area=0, smooth_radius=0, island_area=16)
    assert result[3, 80] == 0
    assert result[20, 20] == result[55, 70] == 1


def test_smoothing_removes_small_teeth_and_preserves_a_crisp_boundary():
    mask = np.zeros((60, 60), dtype=np.float32)
    mask[10:50, 10:50] = 1
    mask[9, 15:45:2] = 1
    result = clean_mask(mask, hole_area=0, smooth_radius=1)
    assert set(np.unique(result)) <= {0, 1}
    assert result[9].sum() < mask[9].sum()
    assert result[30, 30] == 1


def test_pointer_in_a_gap_never_falls_back_to_an_unrelated_object():
    left = Image.new("L", (50, 30))
    left.paste(255, (0, 0, 24, 30))
    right = Image.new("L", (50, 30))
    right.paste(255, (26, 0, 50, 30))
    result = selection.pick_candidate([left, right], (25, 15), smaller=True)
    assert result.getbbox() is None, "An empty gap must not select the neighboring object"


def test_cleanup_preserves_exclusion_points_and_border_connected_gaps():
    mask = np.ones((40, 40), dtype=np.float32)
    mask[15, 15] = 0
    mask[25, 25] = 0
    mask[0:3, 8] = 0
    cleaned = clean_mask(mask, hole_area=8, smooth_radius=0, exclude_points=[(15, 15)])
    assert cleaned[15, 15] == 0
    assert cleaned[25, 25] == 1
    assert cleaned[1, 8] == 0
    smoothed = clean_mask(mask, hole_area=8, smooth_radius=1, exclude_points=[(15, 15)])
    assert smoothed[15, 15] == 0, "Smoothing must not undo an explicit exclusion"


def test_cleanup_only_command_preserves_transparency_without_loading_model():
    class Runner:
        def predict(self, *args):
            raise AssertionError("Cleanup should not run inference")

    rgba = Image.new("RGBA", (40, 40), "white")
    rgba.putpixel((20, 20), (0, 0, 0, 0))
    mask = Image.new("L", (40, 40), 255)
    mask.putpixel((10, 10), 0)
    mask.putpixel((20, 20), 0)
    session = Session(Runner())
    session.handle({"op": "image", "image": encode(rgba)}, lambda _: None)
    result = session.handle(
        {"op": "cleanup", "mask": encode(mask), "cleanup": {"hole_area": 8, "smooth_radius": 0}},
        lambda _: None,
    )
    cleaned = decode(result["masks"][0])
    assert cleaned.getpixel((10, 10)) == 255
    assert cleaned.getpixel((20, 20)) == 0


def test_cleanup_can_be_disabled_without_changing_soft_coverage():
    mask = np.array([[0, 0.2, 0.7, 1]], dtype=np.float32)
    np.testing.assert_array_equal(clean_mask(mask, hole_area=0, smooth_radius=0), mask)
