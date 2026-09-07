"""Private JSON-lines worker launched by the GIMP plugin using the project Python."""

import base64
import contextlib
import io
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PIL import Image

from comparison.imaging import mask_image
from comparison.models import ModelRunner
from object_select.masks import clean_mask


def encode(image):
    output = io.BytesIO()
    image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def decode(value):
    image = Image.open(io.BytesIO(base64.b64decode(value, validate=True)))
    if image.width * image.height > 24_000_000:
        raise ValueError("Images are limited to 24 megapixels.")
    image.load()
    return image


class Session:
    def __init__(self, runner):
        self.runner = runner
        self.image = None
        self.alpha = None

    def handle(self, command, progress):
        if command["op"] == "image":
            rgba = decode(command["image"]).convert("RGBA")
            self.image_id = uuid.uuid4().hex
            self.alpha = np.asarray(rgba.getchannel("A"), dtype=np.float32) / 255
            background = Image.new("RGBA", rgba.size, "white")
            self.image = Image.alpha_composite(background, rgba).convert("RGB")
            return {"ready": True, "size": list(rgba.size)}
        if self.image is None:
            raise ValueError("Send an image before requesting a selection.")
        if command["op"] == "warmup":
            model = command.get("model", "sam2-large")
            if model not in {"sam2-tiny", "sam2-base", "sam2-large", "sam3"}:
                raise ValueError("Choose a SAM model to load for picking.")
            timings = self.runner.warmup(model, self.image_id, self.image, progress)
            return {"ready": True, **timings}
        if command["op"] not in {"predict", "cleanup"}:
            raise ValueError("Unsupported operation. Use warmup, predict, or cleanup.")
        if command["op"] == "cleanup":
            mask = np.asarray(decode(command["mask"]).convert("L"), dtype=np.float32) / 255
            if mask.shape != self.alpha.shape:
                raise ValueError("Cleanup mask dimensions do not match the layer.")
            progress("Filling small holes and smoothing the boundary")
            mask = clean_mask(mask, **command.get("cleanup", {}))
            return {"masks": [encode(mask_image(np.minimum(mask, self.alpha)))]}
        model = command.get("model", "sam2-large")
        if model not in {"sam2-tiny", "sam2-base", "sam2-large", "sam3", "vitmatte"}:
            raise ValueError("Unsupported selection model.")
        parent = None
        if model == "vitmatte":
            matte_output = command.get("matte_output", "soft")
            if matte_output not in {"crisp", "soft"}:
                raise ValueError("Choose Crisp selection or Soft matte for refinement.")
            threshold = command.get("matte_threshold", 0.5)
            if matte_output == "crisp" and not (
                isinstance(threshold, (int, float)) and 0 < threshold < 1
            ):
                raise ValueError("Refinement opacity cutoff must be between 0 and 100 percent.")
            parent = np.asarray(decode(command["mask"]).convert("L"), dtype=np.float32) / 255
            if parent.shape != self.alpha.shape:
                raise ValueError("Refinement mask dimensions do not match the layer.")
        points = command.get("points", [])
        for point in points:
            if not (0 <= point["x"] < self.image.width and 0 <= point["y"] < self.image.height):
                raise ValueError("Point outside the layer.")
        request = {
            "model": model,
            "image_id": self.image_id,
            "points": points,
            "box": command.get("box"),
            "matte_radius": command.get("radius", 10),
            "matte_max_side": 1024,
        }
        prediction = self.runner.predict(request, self.image, parent, progress)
        masks = prediction.masks
        if model == "vitmatte" and matte_output == "crisp":
            # Threshold the resized matte before PNG quantization and layer-alpha clipping.
            masks = [(mask >= threshold).astype(np.float32) for mask in masks]
        if model != "vitmatte" and command.get("cleanup") is not None:
            excluded = [
                (int(point["x"]), int(point["y"])) for point in points if point["label"] == 0
            ]
            included = [
                (int(point["x"]), int(point["y"])) for point in points if point["label"] == 1
            ]
            masks = [
                clean_mask(
                    mask, **command["cleanup"], exclude_points=excluded, include_points=included
                )
                for mask in masks
            ]
        masks = [np.minimum(np.clip(mask, 0, 1), self.alpha) for mask in masks]
        return {
            "masks": [encode(mask_image(mask)) for mask in masks],
            "scores": prediction.scores,
            "seconds": prediction.metadata["predict_seconds"],
            "cached": prediction.metadata.get("cached_embeddings", False),
        }


def main():
    output = sys.stdout

    def emit(message):
        output.write(json.dumps(message) + "\n")
        output.flush()

    # Model libraries may print diagnostics. Reserve stdout for the protocol.
    with contextlib.redirect_stdout(sys.stderr):
        session = Session(ModelRunner())
        for line in sys.stdin:
            command = {}
            try:
                command = json.loads(line)
                result = session.handle(
                    command,
                    lambda message, request_id=command["id"]: emit(
                        {"id": request_id, "progress": message}
                    ),
                )
                emit({"id": command["id"], **result})
            except Exception as error:  # noqa: BLE001 - report failures at the process/UI boundary
                emit({"id": command.get("id"), "error": str(error)})


if __name__ == "__main__":
    main()
