"""Image-space operations shared by inference, exports, and tests."""

import io
import math

import numpy as np
from PIL import Image, ImageCms, ImageOps
from scipy import ndimage

MAX_PIXELS = 24_000_000


def read_image(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    if image.width * image.height > MAX_PIXELS:
        raise ValueError("Use an image with at most 24 megapixels.")
    image = ImageOps.exif_transpose(image)
    alpha = image.getchannel("A") if "A" in image.getbands() else None
    profile = image.info.get("icc_profile")
    if profile:
        image = ImageCms.profileToProfile(
            image,
            ImageCms.ImageCmsProfile(io.BytesIO(profile)),
            ImageCms.createProfile("sRGB"),
            outputMode="RGB",
        )
    else:
        image = image.convert("RGBA")
        alpha = image.getchannel("A")
        image = image.convert("RGB")
    if alpha is not None:
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=alpha)
        image = background
    return image.convert("RGB")


def mask_image(mask: np.ndarray) -> Image.Image:
    return Image.fromarray(np.rint(np.clip(mask, 0, 1) * 255).astype(np.uint8))


def crop_bounds(mask: np.ndarray, padding: float) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask >= 0.5)
    if not len(xs):
        raise ValueError("This mask is empty. Choose another candidate before refining.")
    left, top, right, bottom = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    margin = math.ceil(max(right - left, bottom - top) * padding)
    height, width = mask.shape
    return (
        max(0, left - margin),
        max(0, top - margin),
        min(width, right + margin),
        min(height, bottom + margin),
    )


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
