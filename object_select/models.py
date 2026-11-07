"""Real model adapters. Only the serial inference worker calls this module."""

import gc
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PIL import Image

from object_select.imaging import make_trimap
from object_select.refine import drop_fragments, focus_crop, fuse, paste, rank_candidates

# The ONNX export needs no remote model code. Its masks match the original PyTorch release.
EDGE_MODEL = {
    "repo": "onnx-community/BiRefNet_lite-ONNX",
    "revision": "de15b22ba131",
    "file": "onnx/model.onnx",
    "size": 1024,
}
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
# Below this crop-to-frame ratio, re-encoding the crop gives SAM more pixels to work with.
REFOCUS_SHARE = 0.9
# A mask prompt is fed back this many times; each pass lets SAM sharpen its own contour.
REFOCUS_PASSES = 2
LOGIT_LIMIT = 32.0
# Hover masks only need to look right on screen, and full-size ones made hovering laggy.
PREVIEW_SIDE = 1024

CATALOG = {
    "sam3": {"label": "SAM 3", "repo": "facebook/sam3", "kind": "sam", "gated": True},
    "sam2-tiny": {"label": "SAM 2.1 · tiny", "repo": "facebook/sam2.1-hiera-tiny", "kind": "sam"},
    "sam2-base": {
        "label": "SAM 2.1 · base+",
        "repo": "facebook/sam2.1-hiera-base-plus",
        "kind": "sam",
    },
    "sam2-large": {
        "label": "SAM 2.1 · large",
        "repo": "facebook/sam2.1-hiera-large",
        "kind": "sam",
    },
    "vitmatte": {
        "label": "ViTMatte · selected mask",
        "repo": "hustvl/vitmatte-small-composition-1k",
        "kind": "refine",
    },
}


@dataclass
class Prediction:
    masks: list[np.ndarray]
    scores: list[float | None]
    metadata: dict
    trimap: np.ndarray | None = None


def preview_size(size):
    ratio = min(1, PREVIEW_SIDE / max(size))
    return max(1, round(size[0] * ratio)), max(1, round(size[1] * ratio))


def thread_count():
    return int(os.environ.get("OBJECT_SELECT_THREADS", min(8, os.cpu_count() or 4)))


def resize_logits(logits: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Upsample SAM's logits to (height, width). They stay continuous, so contours stay smooth."""
    import torch
    from torch.nn.functional import interpolate

    tensor = torch.from_numpy(logits)[None, None]
    return interpolate(tensor, size=size, mode="bilinear", align_corners=False)[0, 0].numpy()


def preview_mask(logits: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    return np.clip(resize_logits(logits, size) / 2 + 0.5, 0, 1)


class EdgeModel:
    """BiRefNet lite, run through ONNX Runtime on one crop at a time."""

    def __init__(self):
        self.session = None

    def load(self, progress: Callable):
        if self.session is not None:
            return
        import onnxruntime
        from huggingface_hub import hf_hub_download

        progress("Loading edge model · first use downloads 224 MB")
        path = hf_hub_download(
            EDGE_MODEL["repo"], EDGE_MODEL["file"], revision=EDGE_MODEL["revision"]
        )
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = thread_count()
        # The arena keeps every peak allocation, which held 12 GB between picks.
        options.enable_cpu_mem_arena = False
        self.session = onnxruntime.InferenceSession(
            path, options, providers=["CPUExecutionProvider"]
        )

    def predict(self, image: Image.Image) -> np.ndarray:
        """Return foreground probability for each pixel of the image."""
        side = EDGE_MODEL["size"]
        pixels = np.asarray(image.resize((side, side), Image.Resampling.BILINEAR), np.float32) / 255
        pixels = ((pixels - IMAGENET_MEAN) / IMAGENET_STD).transpose(2, 0, 1)[None]
        name = self.session.get_inputs()[0].name
        logits = self.session.run(None, {name: pixels})[0][0, 0]
        probability = 1 / (1 + np.exp(-np.clip(logits, -30, 30)))
        resized = Image.fromarray(probability.astype(np.float32)).resize(
            image.size, Image.Resampling.BILINEAR
        )
        return np.clip(np.asarray(resized), 0, 1)


class ModelRunner:
    def __init__(self):
        self.model = None
        self.processor = None
        self.loaded_repo = None
        self.revision = None
        self.embedding_key = None
        self.embeddings = None
        self.input_side = 1024
        self.edge_model = EdgeModel()
        # Low-resolution logits from the last pick, for the next correction click.
        self.pick_logits = None

    def _device(self):
        import torch

        requested = os.environ.get("OBJECT_SELECT_DEVICE", "auto")
        if requested != "auto":
            return requested
        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def _sync(self):
        import torch

        if self.device.startswith("cuda"):
            torch.cuda.synchronize()
        elif self.device == "mps":
            torch.mps.synchronize()

    def _load(self, model_id: str, progress: Callable):
        import torch
        from huggingface_hub import HfApi, try_to_load_from_cache
        from transformers import (
            Sam2Model,
            Sam2Processor,
            Sam3TrackerModel,
            Sam3TrackerProcessor,
            VitMatteForImageMatting,
            VitMatteImageProcessor,
        )

        repo = CATALOG[model_id]["repo"]
        if repo == self.loaded_repo:
            return 0.0
        progress("Loading model · first use may download weights")
        started = time.perf_counter()
        self.model = self.processor = self.embeddings = None
        self.embedding_key = self.loaded_repo = None
        # One model's logits mean nothing to another, so corrections start over.
        self.pick_logits = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        self.device = self._device()
        torch.set_num_threads(thread_count())
        cached = try_to_load_from_cache(repo, "config.json")
        if isinstance(cached, str):
            from pathlib import Path

            revision = Path(cached).parent.name
        else:
            revision = HfApi().model_info(repo, timeout=30).sha
        self.revision = revision
        options = {"revision": revision}
        if model_id.startswith("sam"):
            model_cls, processor_cls = (
                (Sam3TrackerModel, Sam3TrackerProcessor)
                if model_id == "sam3"
                else (Sam2Model, Sam2Processor)
            )
            self.processor = processor_cls.from_pretrained(repo, **options)
            self.model = model_cls.from_pretrained(repo, **options).to(self.device).eval()
        else:
            self.processor = VitMatteImageProcessor.from_pretrained(repo, **options)
            self.model = (
                VitMatteForImageMatting.from_pretrained(repo, **options).to(self.device).eval()
            )
        self.loaded_repo = repo
        self._sync()
        return time.perf_counter() - started

    def _encode(self, image):
        pixels = self.processor(images=image, return_tensors="pt")["pixel_values"]
        self.input_side = pixels.shape[-1]
        return self.model.get_image_embeddings(pixels.to(self.device))

    def _encode_image(self, image_id, image, progress):
        key = (image_id, self.loaded_repo)
        if self.embedding_key == key:
            return 0.0
        progress("Encoding image")
        started = time.perf_counter()
        self.embeddings = self._encode(image)
        self._sync()
        self.embedding_key = key
        return time.perf_counter() - started

    def _decode(self, embeddings, size, points, box=None, mask=None, multimask=True):
        """Run SAM's fast decoder. Prompts use the pixel coordinates of an image of this size."""
        import torch

        scale = torch.tensor([self.input_side / size[0], self.input_side / size[1]])
        prompts = {}
        if points:
            coordinates = torch.tensor([[p["x"], p["y"]] for p in points], dtype=torch.float32)
            prompts["input_points"] = (coordinates * scale)[None, None].to(self.device)
            labels = torch.tensor([p["label"] for p in points], dtype=torch.int64)
            prompts["input_labels"] = labels[None, None].to(self.device)
        if box is not None:
            corners = torch.tensor(box, dtype=torch.float32) * scale.repeat(2)
            prompts["input_boxes"] = corners[None, None].to(self.device)
        if mask is not None:
            prompts["input_masks"] = torch.from_numpy(mask)[None, None].to(self.device)
        output = self.model(image_embeddings=embeddings, multimask_output=multimask, **prompts)
        logits = output.pred_masks[0, 0].float().cpu().numpy()
        return logits, output.iou_scores[0, 0].float().cpu().numpy()

    def _candidates(self, image_id, image, request, progress):
        """Decode one prompt and rank its masks. Hover and pick share this, so indices match."""
        self._encode_image(image_id, image, progress)
        points = request["points"]
        previous = self.pick_logits if request.get("continue") else None
        # SAM's authors advise several candidates only for an ambiguous single click.
        multimask = previous is None and len(points) == 1 and not request.get("box")
        logits, scores = self._decode(
            self.embeddings, image.size, points, request.get("box"), previous, multimask
        )
        # Always rank at one size, so a pick's candidate index means what the hover showed.
        width, height = preview_size(image.size)
        ranking = [preview_mask(item, (height, width)) >= 0.5 for item in logits]
        click = None
        if len(points) == 1 and points[0]["label"] == 1:
            click = (
                min(width - 1, int(points[0]["x"] * width / image.width)),
                min(height - 1, int(points[0]["y"] * height / image.height)),
            )
        # A hovered candidate has an index already; only a direct click can ask for a part.
        smaller = request.get("smaller", False) and request.get("candidate") is None
        order = rank_candidates(ranking, click, smaller, scores)
        return logits[order], scores[order]

    def warmup(self, model_id, image_id, image, progress):
        """Load SAM and encode the image without predicting any masks."""
        import torch

        if not model_id.startswith("sam"):
            raise ValueError("Image encoding requires a SAM model.")
        load_seconds = self._load(model_id, progress)
        encode_seconds = 0.0
        with torch.inference_mode():
            encode_seconds = self._encode_image(image_id, image, progress)
        self.pick_logits = None
        return {"load_seconds": load_seconds, "encode_seconds": encode_seconds}

    def pick(self, request: dict, image: Image.Image, progress: Callable):
        """Produce the final full-resolution alpha for one chosen object."""
        import torch

        self._load(request["model"], progress)
        with torch.inference_mode():
            logits, _ = self._candidates(request["image_id"], image, request, progress)
            if not len(logits):
                return None
            index = request.get("candidate")
            if index is None or not 0 <= index < len(logits):
                index = 0
            chosen = logits[index]
            self.pick_logits = np.clip(chosen, -LOGIT_LIMIT, LOGIT_LIMIT)
            coarse = preview_mask(chosen, image.size[::-1])
            if not request.get("refine", True):
                return coarse
            # Stray fragments would otherwise stretch the crop and shrink the object within it.
            clicks = [(p["x"], p["y"]) for p in request["points"] if p["label"] == 1]
            mask = drop_fragments(coarse >= 0.5, clicks)
            progress("Refining the object's edges")
            box = request.get("box")
            if box is not None:
                # A drawn box is the user's own statement of where the object is.
                mask = np.zeros(mask.shape, dtype=bool)
                mask[int(box[1]) : int(box[3]), int(box[0]) : int(box[2])] = True
            crop = focus_crop(mask)
            focused = self._refocus(request, image, crop, chosen)
        self.edge_model.load(progress)
        region = image.crop(crop)
        edge = self.edge_model.predict(region)
        left, top = crop[:2]
        inside = [
            (p["x"] - left, p["y"] - top, p["label"])
            for p in request["points"]
            if left <= p["x"] < crop[2] and top <= p["y"] < crop[3]
        ]
        alpha = fuse(
            focused,
            edge,
            include=[(int(x), int(y)) for x, y, label in inside if label == 1],
            exclude=[(int(x), int(y)) for x, y, label in inside if label == 0],
            box=None if box is None else [box[0] - left, box[1] - top, box[2] - left, box[3] - top],
        )
        return paste(alpha, crop, mask.shape)

    def _refocus(self, request, image, crop, logits):
        """Run SAM again with the object filling its input, seeded with its own mask.

        Stray islands and false holes mostly come from the object occupying too few of SAM's
        pixels. The result is logits at the crop's full resolution.
        """
        left, top, right, bottom = crop
        width, height = right - left, bottom - top
        if width >= REFOCUS_SHARE * image.width and height >= REFOCUS_SHARE * image.height:
            embeddings = self.embeddings
            encoded_size = image.size
            left = top = 0
        else:
            embeddings = self._encode(image.crop(crop))
            encoded_size = (width, height)
        full = resize_logits(logits, image.size[::-1])
        mask = full[top : top + encoded_size[1], left : left + encoded_size[0]]
        mask = resize_logits(np.ascontiguousarray(mask), logits.shape)
        ys, xs = np.nonzero(mask > 0)
        scale_x, scale_y = encoded_size[0] / logits.shape[1], encoded_size[1] / logits.shape[0]
        box = None
        if request.get("box") is not None:
            drawn = request["box"]
            box = [drawn[0] - left, drawn[1] - top, drawn[2] - left, drawn[3] - top]
        elif len(ys):
            box = [
                float(xs.min() * scale_x),
                float(ys.min() * scale_y),
                float((xs.max() + 1) * scale_x),
                float((ys.max() + 1) * scale_y),
            ]
        points = [
            {"x": p["x"] - left, "y": p["y"] - top, "label": p["label"]}
            for p in request["points"]
            if 0 <= p["x"] - left < encoded_size[0] and 0 <= p["y"] - top < encoded_size[1]
        ]
        for _ in range(REFOCUS_PASSES):
            mask = np.clip(mask, -LOGIT_LIMIT, LOGIT_LIMIT)
            output, _ = self._decode(embeddings, encoded_size, points, box, mask, multimask=False)
            mask = output[0]
        self._sync()
        result = resize_logits(mask, encoded_size[::-1])
        # A skipped re-encode decoded the whole frame, so cut the crop out afterwards.
        return np.ascontiguousarray(
            result[crop[1] - top : crop[3] - top, crop[0] - left : crop[2] - left]
        )

    def predict(
        self, request: dict, image: Image.Image, parent: np.ndarray | None, progress: Callable
    ):
        import torch

        model_id = request["model"]
        load_seconds = self._load(model_id, progress)
        progress("Preparing image")
        started = time.perf_counter()
        encode_seconds = 0.0
        cached_embeddings = False
        trimap = None
        details = {}
        with torch.inference_mode():
            if model_id.startswith("sam"):
                cached_embeddings = self.embedding_key == (request["image_id"], self.loaded_repo)
                encode_seconds = self._encode_image(request["image_id"], image, progress)
                progress("Predicting candidate masks")
                logits, scores = self._candidates(request["image_id"], image, request, progress)
                width, height = request.get("output_size") or image.size
                masks = [preview_mask(item, (height, width)) for item in logits]
                scores = [float(score) for score in scores]
                details["output_kind"] = "binary segmentation"
            else:
                trimap = make_trimap(parent, request["matte_radius"])
                # Only the object's own pixels matter, so they shouldn't share the resolution
                # budget with the rest of the frame.
                # The crop follows the trimap, so a wide band never runs past its edge.
                crop = focus_crop(trimap > 0, margin=0.02)
                left, top, right, bottom = crop
                region = image.crop(crop)
                size = region.size
                limit = request["matte_max_side"]
                if limit and max(size) > limit:
                    ratio = limit / max(size)
                    size = (max(1, round(size[0] * ratio)), max(1, round(size[1] * ratio)))
                source = region.resize(size, Image.Resampling.LANCZOS)
                guide = Image.fromarray(trimap[top:bottom, left:right]).resize(
                    size, Image.Resampling.NEAREST
                )
                inputs = self.processor(images=source, trimaps=guide, return_tensors="pt").to(
                    self.device
                )
                progress(f"Refining edges at {size[0]} × {size[1]}")
                alpha = (
                    self.model(**inputs).alphas[0, 0, : size[1], : size[0]].float().cpu().numpy()
                )
                resized = np.asarray(
                    Image.fromarray(alpha).resize(region.size, Image.Resampling.BILINEAR)
                )
                alpha = (trimap == 255).astype(np.float32)
                alpha[top:bottom, left:right] = np.clip(resized, 0, 1)
                alpha[trimap == 0] = 0
                alpha[trimap == 255] = 1
                masks, scores = [alpha], [None]
                details.update(model_input=list(size), output_kind="alpha matte")
        self._sync()
        return Prediction(
            masks,
            scores,
            {
                "repo": self.loaded_repo,
                "revision": self.revision,
                "device": self.device,
                "dtype": "float32",
                "load_seconds": load_seconds,
                "encode_seconds": encode_seconds,
                "cached_embeddings": cached_embeddings,
                "predict_seconds": time.perf_counter() - started,
                **details,
            },
            trimap,
        )
