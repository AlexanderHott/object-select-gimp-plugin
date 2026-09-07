"""Real model adapters. Only the serial inference worker calls this module."""

import gc
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PIL import Image

from object_select.imaging import make_trimap

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


class ModelRunner:
    def __init__(self):
        self.model = None
        self.processor = None
        self.loaded_repo = None
        self.revision = None
        self.embedding_key = None
        self.embeddings = None

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
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        self.device = self._device()
        torch.set_num_threads(
            int(os.environ.get("OBJECT_SELECT_THREADS", min(8, os.cpu_count() or 4)))
        )
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

    def _encode_image(self, image_id, inputs, progress):
        key = (image_id, self.loaded_repo)
        if self.embedding_key == key:
            return 0.0
        progress("Encoding image")
        started = time.perf_counter()
        self.embeddings = self.model.get_image_embeddings(inputs["pixel_values"])
        self._sync()
        self.embedding_key = key
        return time.perf_counter() - started

    def warmup(self, model_id, image_id, image, progress):
        """Load SAM and encode the image without predicting any masks."""
        import torch

        if not model_id.startswith("sam"):
            raise ValueError("Image encoding requires a SAM model.")
        load_seconds = self._load(model_id, progress)
        encode_seconds = 0.0
        with torch.inference_mode():
            if self.embedding_key != (image_id, self.loaded_repo):
                inputs = self.processor(images=image, return_tensors="pt").to(self.device)
                encode_seconds = self._encode_image(image_id, inputs, progress)
        return {"load_seconds": load_seconds, "encode_seconds": encode_seconds}

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
                prompts = {}
                if request["points"]:
                    prompts["input_points"] = [[[[p["x"], p["y"]] for p in request["points"]]]]
                    prompts["input_labels"] = [[[p["label"] for p in request["points"]]]]
                if request.get("box"):
                    prompts["input_boxes"] = [[request["box"]]]
                inputs = self.processor(images=image, **prompts, return_tensors="pt").to(
                    self.device
                )
                cached_embeddings = self.embedding_key == (request["image_id"], self.loaded_repo)
                encode_seconds = self._encode_image(request["image_id"], inputs, progress)
                progress("Predicting candidate masks")
                output = self.model(
                    image_embeddings=self.embeddings,
                    input_points=inputs.get("input_points"),
                    input_labels=inputs.get("input_labels"),
                    input_boxes=inputs.get("input_boxes"),
                    multimask_output=True,
                )
                masks_tensor = self.processor.post_process_masks(
                    output.pred_masks.cpu(),
                    inputs["original_sizes"].cpu(),
                    binarize=True,
                )[0][0]
                scores_array = output.iou_scores[0, 0].float().cpu().numpy()
                order = np.argsort(-scores_array)
                masks = [masks_tensor[i].numpy().astype(np.float32) for i in order]
                scores = [float(scores_array[i]) for i in order]
                details["output_kind"] = "binary segmentation"
            else:
                trimap = make_trimap(parent, request["matte_radius"])
                size = image.size
                limit = request["matte_max_side"]
                if limit and max(size) > limit:
                    ratio = limit / max(size)
                    size = (max(1, round(size[0] * ratio)), max(1, round(size[1] * ratio)))
                source = image.resize(size, Image.Resampling.LANCZOS)
                guide = Image.fromarray(trimap).resize(size, Image.Resampling.NEAREST)
                inputs = self.processor(images=source, trimaps=guide, return_tensors="pt").to(
                    self.device
                )
                progress(f"Refining edges at {size[0]} × {size[1]}")
                alpha = (
                    self.model(**inputs).alphas[0, 0, : size[1], : size[0]].float().cpu().numpy()
                )
                alpha = np.asarray(
                    Image.fromarray(alpha).resize(image.size, Image.Resampling.BILINEAR)
                )
                alpha = np.clip(alpha, 0, 1).copy()
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
