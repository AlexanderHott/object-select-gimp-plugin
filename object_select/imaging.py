"""Mask encoding and trimap generation for the model worker."""

import numpy as np
from PIL import Image
from scipy import ndimage


def mask_image(mask: np.ndarray) -> Image.Image:
    return Image.fromarray(np.rint(np.clip(mask, 0, 1) * 255).astype(np.uint8))


def make_trimap(mask: np.ndarray, radius: int) -> np.ndarray:
    foreground = mask >= 0.5
    if not foreground.any() or foreground.all():
        raise ValueError("Matting needs a mask containing both foreground and background.")
    inside = ndimage.distance_transform_edt(np.pad(foreground, 1))[1:-1, 1:-1]
    outside = ndimage.distance_transform_edt(~foreground)
    trimap = np.full(mask.shape, 128, dtype=np.uint8)
    trimap[outside > radius] = 0
    trimap[inside > radius] = 255
    if not (trimap == 255).any():
        raise ValueError("The unknown band covers the whole object. Reduce the matting band width.")
    return trimap
