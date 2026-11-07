"""Turn a coarse SAM mask and an edge model's output into one object's final alpha.

SAM decides which object was picked but draws its contour on a coarse grid. BiRefNet draws
precise contours but segments whatever stands out, which may be a different object. So SAM keeps
identity, and BiRefNet may only move the edge where the two agree.

Nothing here smooths or erodes a contour: that is what rounds off points and fills cutouts.
Cleanup goes by connected area only, so tips, thin parts, and real holes survive.

The thresholds were tuned against DIS5K, ThinObject-5K, AIM-500, and crowded COCO scenes.
"""

import numpy as np
from scipy import ndimage

# Candidates covering more of the frame than this are usually the background.
BACKGROUND_SHARE = 0.6
# Below that, large masks are still background, like the table under an object, if SAM doubts them.
DOUBTFUL_SHARE = 0.5
DOUBTFUL_SCORE = 0.5
# Above this overlap, the edge model has found the same object and its whole result is trusted.
SAME_OBJECT_IOU = 0.9
# Below this, it has found another object, so nothing of its result applies.
OTHER_OBJECT_IOU = 0.1
# Edge adoption band around SAM's contour, as a share of the object's longest side.
BAND_SHARE = 0.03
# SAM can select what surrounds an object that nearly fills a drawn box. Its mask then has low
# overlap with the edge model's and holds much more of the box's outer rim.
BOX_MISREAD_IOU = 0.5
BOX_MISREAD_RIM = 0.2
BOX_RIM_SHARE = 0.03
# Pockets inside the object only become holes when the edge model is this sure of them.
HOLE_CONFIDENCE = 0.25
# Larger pockets are more likely the edge model losing part of the object than a real hole.
MAX_HOLE_SHARE = 0.02
# Parts thinner than this may extend past the band. Neighboring objects are rarely this thin.
THIN_SHARE = 0.012
# SAM fragments smaller than this share of the object are noise.
MIN_FRAGMENT_SHARE = 0.01
# Specks and pinholes below this share of the object are removed.
SPECK_SHARE = 1e-4
MIN_SPECK_AREA = 9

EIGHT_CONNECTED = np.ones((3, 3))


def rank_candidates(masks, point=None, smaller=False, scores=None):
    """Return candidate indices with the most likely intended object first.

    SAM's predicted quality favors small parts, so on single clicks it ranks the whole object
    first less than half the time. Largest-first does so about twice as often, as long as
    background-sized masks and masks that miss the click are pushed to the end. Smaller reverses
    the size preference, for when the user asked for a part.
    """

    def is_background(index, share):
        if share >= BACKGROUND_SHARE:
            return True
        return scores is not None and share >= DOUBTFUL_SHARE and scores[index] < DOUBTFUL_SCORE

    def key(index):
        mask = masks[index]
        share = float(np.mean(mask))
        misses = point is not None and not mask[point[1], point[0]]
        return (bool(misses), is_background(index, share), share if smaller else -share)

    nonempty = [index for index, mask in enumerate(masks) if mask.any()]
    return sorted(nonempty, key=key)


def focus_crop(mask, margin=0.2, min_side=64):
    """Return a near-square (left, top, right, bottom) around a mask, with context."""
    ys, xs = np.nonzero(mask)
    if not len(ys):
        raise ValueError("The mask is empty.")
    height, width = mask.shape
    left, right, top, bottom = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    # SAM stretches its input to a square, so a square crop keeps object proportions.
    side = max(right - left, bottom - top) * (1 + 2 * margin)
    side = max(side, min_side)
    crop_width, crop_height = min(side, width), min(side, height)
    x = int(np.clip((left + right) / 2 - crop_width / 2, 0, width - crop_width))
    y = int(np.clip((top + bottom) / 2 - crop_height / 2, 0, height - crop_height))
    return x, y, int(x + crop_width), int(y + crop_height)


def longest_side(mask):
    ys, xs = np.nonzero(mask)
    return int(max(xs.max() - xs.min(), ys.max() - ys.min())) + 1


def drop_fragments(mask, keep_points=()):
    """Remove SAM's tiny disconnected pieces, keeping any piece the user clicked."""
    labels, count = ndimage.label(mask, structure=EIGHT_CONNECTED)
    if count <= 1:
        return mask
    sizes = np.bincount(labels.ravel(), minlength=count + 1)
    sizes[0] = 0
    keep = sizes >= MIN_FRAGMENT_SHARE * sizes.max()
    keep[0] = False
    for x, y in keep_points:
        if labels[y, x]:
            keep[labels[y, x]] = True
    return keep[labels]


def lock_edges(object_mask, edge):
    """Adopt the edge model's contour only near SAM's contour, and only where it has one.

    Near SAM's contour, an edge map that is all foreground has merged a neighbor, and one that
    is all background has lost this part. In both cases SAM's contour stays.
    """
    edge_mask = edge >= 0.5
    area = int(object_mask.sum())
    side = longest_side(object_mask)
    radius = max(3.0, side * BAND_SHARE)
    inside = ndimage.distance_transform_edt(object_mask)
    outside = ndimage.distance_transform_edt(~object_mask)
    band = (inside <= radius) & (outside <= radius)

    window = int(2 * radius) + 1
    any_foreground = ndimage.maximum_filter(edge_mask, size=window, mode="nearest")
    all_foreground = ndimage.minimum_filter(edge_mask, size=window, mode="nearest")
    adopt = band & any_foreground & ~all_foreground
    adopt = ndimage.binary_closing(adopt, iterations=2) & band

    result = object_mask.astype(np.float32)
    result[adopt] = edge[adopt]

    pockets, count = ndimage.label((inside > radius) & (edge < HOLE_CONFIDENCE))
    if count:
        sizes = np.bincount(pockets.ravel(), minlength=count + 1)
        is_hole = sizes <= MAX_HOLE_SHARE * area
        is_hole[0] = False
        holes = is_hole[pockets]
        result[holes] = edge[holes]

    # Spokes, legs, and antennae that SAM missed, if they connect to what is already kept.
    thickness = max(2, int(side * THIN_SHARE))
    beyond = edge_mask & (outside > radius)
    if beyond.any():
        thin = beyond & ~ndimage.binary_opening(beyond, iterations=thickness)
        kept = result >= 0.5
        labels, _ = ndimage.label(thin | kept, structure=EIGHT_CONNECTED)
        connected = np.isin(labels, np.unique(labels[kept])) & thin
        result[connected] = edge[connected]
    return result


def tidy(alpha, keep_points=(), hole_points=()):
    """Remove specks and fill pinholes by area alone, leaving soft coverage untouched."""
    solid = alpha >= 0.5
    if not solid.any():
        return alpha
    limit = max(MIN_SPECK_AREA, SPECK_SHARE * solid.sum())
    result = alpha.copy()

    labels, count = ndimage.label(solid, structure=EIGHT_CONNECTED)
    sizes = np.bincount(labels.ravel(), minlength=count + 1)
    speck = sizes < limit
    speck[0] = False
    for x, y in keep_points:
        speck[labels[y, x]] = False
    result[speck[labels]] = 0

    labels, count = ndimage.label(~solid)
    sizes = np.bincount(labels.ravel(), minlength=count + 1)
    pinhole = sizes < limit
    pinhole[0] = False
    edge_labels = np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1]))
    pinhole[np.unique(edge_labels)] = False
    for x, y in hole_points:
        pinhole[labels[y, x]] = False
    result[pinhole[labels]] = 1
    return result


def clip_to_box(mask, box):
    left, top, right, bottom = [round(value) for value in box]
    clipped = np.zeros_like(mask)
    clipped[top:bottom, left:right] = mask[top:bottom, left:right]
    return clipped


def rim_coverage(mask, box):
    """Return how much of the outer rim of a box the mask holds."""
    left, top, right, bottom = [round(value) for value in box]
    inner = mask[top:bottom, left:right]
    if not inner.size:
        return 0.0
    width = max(1, int(BOX_RIM_SHARE * max(inner.shape)))
    rim = np.ones(inner.shape, dtype=bool)
    rim[width:-width, width:-width] = False
    return float(inner[rim].mean())


def misread_box(object_mask, edge_mask, overlap, box):
    """Tell whether SAM selected what surrounds the object in a drawn box."""
    if box is None or not edge_mask.any():
        return False
    if not object_mask.any():
        return True
    excess = rim_coverage(object_mask, box) - rim_coverage(edge_mask, box)
    return overlap < BOX_MISREAD_IOU and excess >= BOX_MISREAD_RIM


def fuse(logits, edge, include=(), exclude=(), box=None):
    """Combine SAM logits and an edge probability map of the same size into an alpha matte.

    Points and box use the same coordinates. The user's input outranks both models, and nothing
    leaves a drawn box.
    """
    object_mask = logits > 0
    if box is not None:
        object_mask = clip_to_box(object_mask, box)
        edge = clip_to_box(edge, box)
    object_mask = drop_fragments(object_mask, include)
    edge_mask = edge >= 0.5
    union = np.count_nonzero(object_mask | edge_mask)
    overlap = np.count_nonzero(object_mask & edge_mask) / max(1, union)
    misread = misread_box(object_mask, edge_mask, overlap, box)
    if not object_mask.any() and not misread:
        return object_mask.astype(np.float32)
    if overlap >= SAME_OBJECT_IOU or misread:
        result = edge.astype(np.float32, copy=True)
    elif overlap < OTHER_OBJECT_IOU:
        result = object_mask.astype(np.float32)
    else:
        result = lock_edges(object_mask, edge)
    clicks_ignored = any(result[y, x] >= 0.5 for x, y in exclude) or any(
        result[y, x] < 0.5 and object_mask[y, x] for x, y in include
    )
    if clicks_ignored and object_mask.any():
        result = object_mask.astype(np.float32)
    return tidy(result, include, exclude)


def paste(alpha, crop, shape):
    """Place a crop-sized alpha into an empty frame."""
    result = np.zeros(shape, dtype=np.float32)
    left, top, right, bottom = crop
    result[top:bottom, left:right] = alpha
    return result
