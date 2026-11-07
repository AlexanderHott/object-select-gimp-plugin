import numpy as np
import pytest

from object_select.refine import (
    drop_fragments,
    focus_crop,
    fuse,
    lock_edges,
    longest_side,
    rank_candidates,
    tidy,
)


def disc(shape, center, radius):
    ys, xs = np.ogrid[: shape[0], : shape[1]]
    return (xs - center[0]) ** 2 + (ys - center[1]) ** 2 <= radius**2


def logits_for(mask):
    return np.where(mask, 4.0, -4.0).astype(np.float32)


def test_ranking_prefers_the_whole_object_over_higher_scoring_parts():
    part = disc((100, 100), (50, 50), 8)
    whole = disc((100, 100), (50, 50), 30)
    assert rank_candidates([part, whole], (50, 50))[0] == 1
    assert rank_candidates([part, whole], (50, 50), smaller=True)[0] == 0


def test_ranking_pushes_background_and_masks_missing_the_click_last():
    target = disc((100, 100), (50, 50), 20)
    background = np.ones((100, 100), dtype=bool)
    background[45:55, 45:55] = False
    bigger_elsewhere = disc((100, 100), (20, 20), 25)
    background_with_click = np.ones((100, 100), dtype=bool)
    assert rank_candidates([background_with_click, target], (50, 50))[0] == 1
    assert rank_candidates([bigger_elsewhere, target], (50, 50))[0] == 1
    assert rank_candidates([background, target], (50, 50))[0] == 1


def test_ranking_distrusts_large_masks_only_when_sam_doubts_them():
    target = disc((100, 100), (50, 50), 15)
    table = np.zeros((100, 100), dtype=bool)
    table[45:, :] = True
    assert rank_candidates([table, target], (50, 50), scores=[0.1, 0.9])[0] == 1
    assert rank_candidates([table, target], (50, 50), scores=[0.9, 0.5])[0] == 0
    # A low score alone must not hide a mid-sized object, which SAM often underrates.
    whole = disc((100, 100), (50, 50), 30)
    assert rank_candidates([whole, target], (50, 50), scores=[0.01, 0.95])[0] == 0


def test_ranking_leaves_out_empty_masks():
    target = disc((40, 40), (20, 20), 5)
    assert rank_candidates([np.zeros((40, 40), dtype=bool), target]) == [1]


def test_focus_crop_keeps_context_and_stays_in_frame():
    mask = np.zeros((400, 600), dtype=bool)
    mask[100:200, 200:300] = True
    left, top, right, bottom = focus_crop(mask)
    assert left < 200 and top < 100 and right > 300 and bottom > 200
    assert right - left == bottom - top == 140
    mask = np.zeros((400, 600), dtype=bool)
    mask[0:10, 590:600] = True
    left, top, right, bottom = focus_crop(mask)
    assert 0 <= left and 0 <= top and right <= 600 and bottom <= 400
    assert mask[top:bottom, left:right].sum() == mask.sum()
    with pytest.raises(ValueError):
        focus_crop(np.zeros((4, 4), dtype=bool))


def test_same_object_uses_the_edge_models_contour_and_real_holes():
    """A cutout SAM missed must survive when both models see the same object."""
    sam = disc((200, 200), (100, 100), 60)
    edge = disc((200, 200), (100, 100), 61).astype(np.float32)
    edge[90:110, 90:110] = 0
    alpha = fuse(logits_for(sam), edge)
    assert alpha[100, 100] == 0, "A real cutout must stay cut out"
    assert alpha[100, 60] == 1


def test_a_neighbor_stays_out_when_the_edge_model_merges_it():
    """Edge models segment what stands out, so identity must come from SAM."""
    sam = disc((200, 300), (80, 100), 50)
    neighbor = disc((200, 300), (200, 100), 70)
    edge = (sam | neighbor).astype(np.float32)
    alpha = fuse(logits_for(sam), edge)
    assert alpha[100, 200] == 0, "The neighboring object must not be selected"
    assert alpha[100, 80] == 1


def test_a_different_salient_object_cannot_replace_the_pick():
    sam = disc((200, 300), (70, 100), 40)
    edge = disc((200, 300), (220, 100), 60).astype(np.float32)
    alpha = fuse(logits_for(sam), edge)
    assert alpha[100, 70] == 1, "The picked object must survive"
    assert alpha[100, 220] == 0, "The other object must stay out"


def test_edge_is_only_adopted_near_contour_where_the_edge_model_has_one():
    sam = disc((300, 300), (150, 150), 80)
    # The edge model's contour sits close to SAM's on the right half only.
    edge = disc((300, 300), (150, 150), 84)
    edge[:, :150] = False
    result = lock_edges(sam, edge.astype(np.float32))
    assert result[150, 232] == 1, "The right side follows the edge model outward"
    assert result[150, 75] == 1, "Losing a side in the edge map must not lose the object"
    assert result[150, 66] == 0, "The left side must not grow past SAM's contour"


def test_points_and_thin_parts_survive_where_smoothing_would_remove_them():
    """Spikes SAM misses are recovered, and no morphology rounds their tips off."""
    sam = disc((300, 300), (150, 200), 60)
    edge = sam.copy()
    edge[20:150, 149:152] = True  # An antenna rising from the top of the object.
    result = fuse(logits_for(sam), edge.astype(np.float32))
    assert result[60, 150] == 1, "The thin part must connect through"
    assert result[21, 150] == 1, "The very tip must be kept"


def test_large_loss_in_the_edge_map_does_not_punch_a_hole():
    sam = disc((200, 200), (100, 100), 70)
    edge = sam.astype(np.float32).copy()
    edge[60:140, 60:140] = 0  # Far above the size of a plausible real hole.
    alpha = fuse(logits_for(sam), edge)
    assert alpha[100, 100] == 1


def test_user_clicks_outrank_both_models():
    sam = disc((200, 200), (100, 100), 50)
    edge = disc((200, 200), (100, 100), 60).astype(np.float32)
    assert fuse(logits_for(sam), edge, exclude=[(100, 45)])[45, 100] == 0
    edge = sam.astype(np.float32).copy()
    edge[95:105, 95:105] = 0
    assert fuse(logits_for(sam), edge, include=[(100, 100)])[100, 100] == 1


def test_a_drawn_box_settles_total_disagreement_and_clips_the_result():
    """SAM can return the surroundings of an object that nearly fills the box."""
    shape = (200, 200)
    obj = disc(shape, (100, 100), 70)
    surroundings = ~obj
    alpha = fuse(logits_for(surroundings), obj.astype(np.float32), box=[10, 10, 190, 190])
    assert alpha[100, 100] == 1 and alpha[5, 5] == 0
    wide = disc(shape, (100, 100), 90)
    alpha = fuse(logits_for(wide), wide.astype(np.float32), box=[50, 50, 150, 150])
    assert alpha[100, 100] == 1
    assert alpha[100, 40] == 0, "Nothing may leave a drawn box"


def test_a_box_does_not_hand_the_pick_to_another_object_inside_it():
    """A box around a car can contain a dog, and the edge model may prefer the dog."""
    shape = (200, 300)
    car = disc(shape, (90, 100), 60)
    dog = disc(shape, (220, 100), 30)
    alpha = fuse(logits_for(car), dog.astype(np.float32), box=[20, 30, 260, 170])
    assert alpha[100, 90] == 1, "The picked object must survive"
    assert alpha[100, 220] == 0, "The other object must stay out"


def test_a_box_does_not_override_partial_agreement_with_a_neighbor():
    shape = (200, 300)
    target = disc(shape, (100, 100), 60)
    neighbor = disc(shape, (190, 100), 60)
    alpha = fuse(logits_for(target), (target | neighbor).astype(np.float32), box=[30, 30, 270, 170])
    assert alpha[100, 100] == 1
    assert alpha[100, 230] == 0, "SAM's identity still holds when the models partly agree"


def test_fragments_are_dropped_unless_clicked():
    mask = disc((200, 200), (100, 100), 60)
    mask[5:7, 5:7] = True
    assert not drop_fragments(mask)[5, 5]
    assert drop_fragments(mask, [(5, 5)])[5, 5]


def test_tidy_removes_specks_and_pinholes_without_touching_soft_coverage():
    alpha = disc((200, 200), (100, 100), 60).astype(np.float32)
    alpha[5, 5] = 1
    alpha[100, 100] = 0
    alpha[100, 40] = 0.4
    result = tidy(alpha)
    assert result[5, 5] == 0, "A speck must go"
    assert result[100, 100] == 1, "A pinhole must close"
    assert result[100, 40] == pytest.approx(0.4), "Soft coverage must stay as it was"


def test_tidy_keeps_real_holes_and_explicit_points():
    alpha = disc((200, 200), (100, 100), 60).astype(np.float32)
    alpha[90:110, 90:110] = 0
    alpha[60, 100] = 0
    assert tidy(alpha)[100, 100] == 0, "A real hole must stay"
    assert tidy(alpha, hole_points=[(100, 60)])[60, 100] == 0, "An excluded pinhole must stay"


def test_empty_masks_pass_through():
    empty = np.full((20, 20), -4.0, dtype=np.float32)
    assert not fuse(empty, np.ones((20, 20), dtype=np.float32)).any()
    assert fuse(empty, np.ones((20, 20), dtype=np.float32), box=[0, 0, 20, 20]).all()
    assert longest_side(disc((50, 50), (25, 25), 5)) == 11
