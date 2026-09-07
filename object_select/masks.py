"""Geometric cleanup of selection masks without feathering."""

import math

import numpy as np
from scipy import ndimage


def clean_mask(
    mask, hole_area=64, smooth_radius=1.0, island_area=64, exclude_points=(), include_points=()
):
    """Fill holes, simplify the contour, and remove small disconnected islands.

    Border-connected background, large openings, and holes containing explicit
    exclusion points are kept. Alpha clipping belongs to the layer snapshot.
    """
    if not isinstance(hole_area, int) or not 0 <= hole_area <= 10000:
        raise ValueError("Hole size must be between 0 and 10000 square pixels.")
    if not math.isfinite(smooth_radius) or not 0 <= smooth_radius <= 5:
        raise ValueError("Edge smoothing must be between 0 and 5 pixels.")
    if not isinstance(island_area, int) or not 0 <= island_area <= 1_000_000:
        raise ValueError("Island size must be between 0 and 1000000 square pixels.")
    result = np.clip(mask, 0, 1).astype(np.float32, copy=True)
    protected = None
    if hole_area or exclude_points:
        labels, count = ndimage.label(result < 0.5, structure=np.ones((3, 3)))
        sizes = np.bincount(labels.ravel(), minlength=count + 1)
        fill = sizes <= hole_area
        fill[0] = False
        border = np.unique(np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1])))
        fill[border] = False
        excluded = {labels[y, x] for x, y in exclude_points if labels[y, x] != 0}
        if excluded:
            ids = list(excluded)
            fill[ids] = False
            protected = np.isin(labels, ids)
        result[fill[labels]] = 1
    if smooth_radius:
        radius = max(1, round(smooth_radius))
        # Majority voting moves the contour without creating fractional coverage.
        # Unlike feathering, the result stays entirely selected or unselected.
        votes = ndimage.uniform_filter(
            (result >= 0.5).astype(np.float32), size=2 * radius + 1, mode="nearest"
        )
        result = (votes >= 0.5).astype(np.float32)
        if protected is not None:
            result[protected] = 0
    if island_area:
        labels, count = ndimage.label(result >= 0.5, structure=np.ones((3, 3)))
        if count:
            sizes = np.bincount(labels.ravel(), minlength=count + 1)
            sizes[0] = 0
            remove = sizes <= island_area
            remove[0] = False
            remove[np.argmax(sizes)] = False
            for x, y in include_points:
                remove[labels[y, x]] = False
            result[remove[labels]] = 0
    return result
