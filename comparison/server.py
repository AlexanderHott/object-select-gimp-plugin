"""Local HTTP API with one inference worker and persistent experiment files."""

import io
import json
import logging
import os
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

import numpy as np
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from comparison.imaging import mask_image, read_image
from comparison.models import CATALOG, ModelRunner

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("COMPARISON_DATA", ROOT / ".comparison-data"))
app = FastAPI(title="Selection Lab")
app.mount("/static", StaticFiles(directory=ROOT / "comparison" / "static"), name="static")
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inference")
runner = ModelRunner()
logger = logging.getLogger(__name__)
jobs: dict[str, dict] = {}
lock = threading.Lock()


class Point(BaseModel):
    x: float = Field(ge=0, allow_inf_nan=False)
    y: float = Field(ge=0, allow_inf_nan=False)
    label: Literal[0, 1]


class RunRequest(BaseModel):
    image_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    model: str
    points: list[Point] = Field(default_factory=list, max_length=100)
    box: list[float] | None = Field(default=None, min_length=4, max_length=4)
    parent_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    candidate: int = Field(default=0, ge=0, le=2)
    crop_padding: float = Field(default=0.15, ge=0, le=1, allow_inf_nan=False)
    matte_radius: int = Field(default=10, ge=1, le=200)
    matte_max_side: Literal[0, 1024, 2048] = 1024


class Note(BaseModel):
    note: str = Field(max_length=5000)


def image_dir(image_id: str) -> Path:
    if len(image_id) != 32 or any(c not in "0123456789abcdef" for c in image_id):
        raise HTTPException(404, "Image not found")
    directory = DATA / image_id
    if not (directory / "image.json").is_file():
        raise HTTPException(404, "Image not found")
    return directory


def result_dir(image_id: str, result_id: str) -> Path:
    if len(result_id) != 32 or any(c not in "0123456789abcdef" for c in result_id):
        raise HTTPException(404, "Result not found")
    directory = image_dir(image_id) / result_id
    if not (directory / "result.json").is_file():
        raise HTTPException(404, "Result not found")
    return directory


def write_json(path: Path, data: dict):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False))
    temporary.replace(path)


@app.get("/")
def index():
    return FileResponse(ROOT / "comparison" / "static" / "index.html")


@app.get("/api/status")
def status():
    import torch
    from huggingface_hub import get_token

    return {
        "device": runner._device(),
        "torch": torch.__version__,
        "authenticated": bool(get_token()),
        "models": [{"id": key, **value} for key, value in CATALOG.items()],
    }


@app.post("/api/images")
async def upload(file: UploadFile):
    data = await file.read(40 * 1024 * 1024 + 1)
    await file.close()
    if len(data) > 40 * 1024 * 1024:
        raise HTTPException(413, "Use an image smaller than 40 MB.")
    try:
        image = read_image(data)
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise HTTPException(400, f"Cannot read image: {error}") from error
    image_id = uuid.uuid4().hex
    directory = DATA / image_id
    directory.mkdir(parents=True)
    image.save(directory / "source.png")
    info = {
        "id": image_id,
        "name": Path(file.filename or "image").name,
        "width": image.width,
        "height": image.height,
        "created": time.time(),
        "normalization": "EXIF orientation applied; sRGB RGB; transparency composited on white",
    }
    write_json(directory / "image.json", info)
    return info


@app.get("/api/images")
def images():
    return sorted(
        [json.loads(p.read_text()) for p in DATA.glob("*/image.json")],
        key=lambda item: item["created"],
        reverse=True,
    )


@app.get("/api/images/{image_id}/source.png")
def source(image_id: str):
    return FileResponse(image_dir(image_id) / "source.png", media_type="image/png")


@app.get("/api/images/{image_id}/results")
def results(image_id: str):
    return sorted(
        [json.loads(p.read_text()) for p in image_dir(image_id).glob("*/result.json")],
        key=lambda item: item["created"],
    )


@app.post("/api/runs", status_code=202)
def run(request: RunRequest):
    directory = image_dir(request.image_id)
    if request.model not in CATALOG:
        raise HTTPException(422, "Unknown model")
    info = json.loads((directory / "image.json").read_text())
    if any(p.x >= info["width"] or p.y >= info["height"] for p in request.points):
        raise HTTPException(422, "A prompt is outside the image")
    if request.box:
        x0, y0, x1, y1 = request.box
        if not (0 <= x0 < x1 <= info["width"] and 0 <= y0 < y1 <= info["height"]):
            raise HTTPException(422, "Draw a non-empty box inside the image")
    kind = CATALOG[request.model]["kind"]
    if kind == "sam" and not (any(p.label == 1 for p in request.points) or request.box):
        raise HTTPException(422, "Add a foreground point or draw a box first")
    if kind == "refine":
        if not request.parent_id:
            raise HTTPException(422, "Select a result to refine")
        parent = result_dir(request.image_id, request.parent_id)
        if not (parent / f"mask-{request.candidate}.npy").is_file():
            raise HTTPException(422, "Candidate not found")
    else:
        request.parent_id = None
    job_id = uuid.uuid4().hex
    with lock:
        if sum(j["state"] in ("queued", "running") for j in jobs.values()) >= 12:
            raise HTTPException(429, "Wait for the current comparisons to finish")
        jobs[job_id] = {
            "id": job_id,
            "state": "queued",
            "stage": "Queued",
            "request": request.model_dump(),
            "cancelled": False,
        }
    executor.submit(execute, job_id)
    return {"id": job_id}


class Cancelled(Exception):
    pass


def execute(job_id: str):
    with lock:
        job = jobs[job_id]
        request = job["request"]
        job["state"] = "running"

    def progress(stage):
        with lock:
            if job["cancelled"]:
                raise Cancelled()
            job["stage"] = stage

    started = time.perf_counter()
    try:
        progress("Starting")
        directory = image_dir(request["image_id"])
        with Image.open(directory / "source.png") as original:
            image = original.convert("RGB")
        parent = None
        if request["parent_id"]:
            parent = np.load(directory / request["parent_id"] / f"mask-{request['candidate']}.npy")
        prediction = runner.predict(request, image, parent, progress)
        progress("Saving masks")
        output = directory / job_id
        output.mkdir()
        for index, mask in enumerate(prediction.masks):
            if mask.shape != (image.height, image.width) or not np.isfinite(mask).all():
                raise ValueError("Model returned an invalid mask")
            np.save(output / f"mask-{index}.npy", mask.astype(np.float32))
            mask_image(mask).save(output / f"mask-{index}.png")
        if prediction.trimap is not None:
            Image.fromarray(prediction.trimap).save(output / "trimap.png")
        result = {
            "id": job_id,
            "image_id": request["image_id"],
            "label": CATALOG[request["model"]]["label"],
            "request": request,
            "scores": prediction.scores,
            "count": len(prediction.masks),
            "metadata": prediction.metadata,
            "created": time.time(),
            "note": "",
            "has_trimap": prediction.trimap is not None,
            "total_seconds": time.perf_counter() - started,
        }
        with lock:
            if job["cancelled"]:
                raise Cancelled()
            write_json(output / "result.json", result)
            job.update(state="done", stage="Done", result=result)
    except Cancelled:
        with lock:
            job.update(state="cancelled", stage="Cancelled")
    except Exception as error:
        logger.exception("Inference failed for %s", request["model"])
        message = str(error)
        if any(term in message.lower() for term in ("gated", "401", "403", "unauthorized")):
            message = "SAM 3 needs Hugging Face access and a local login. In this project run: uv run hf auth login. Then retry."
        with lock:
            job.update(state="error", stage="Failed", error=message[:1500])


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with lock:
        if job_id not in jobs:
            raise HTTPException(404, "Job not found; the server may have restarted")
        return dict(jobs[job_id])


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str):
    with lock:
        if job_id not in jobs:
            raise HTTPException(404, "Job not found")
        if jobs[job_id]["state"] in ("queued", "running"):
            jobs[job_id]["cancelled"] = True
            jobs[job_id]["stage"] = "Cancelling after the current model operation"
    return {"ok": True}


@app.get("/api/images/{image_id}/results/{result_id}/{filename}")
def artifact(image_id: str, result_id: str, filename: str):
    directory = result_dir(image_id, result_id)
    if filename not in {"mask-0.png", "mask-1.png", "mask-2.png", "trimap.png", "result.json"}:
        raise HTTPException(404, "Artifact not found")
    if not (directory / filename).is_file():
        raise HTTPException(404, "Artifact not found")
    return FileResponse(directory / filename)


@app.get("/api/images/{image_id}/results/{result_id}/cutout/{candidate}.png")
def cutout(image_id: str, result_id: str, candidate: int):
    directory = result_dir(image_id, result_id)
    path = directory / f"mask-{candidate}.png"
    if candidate not in range(3) or not path.is_file():
        raise HTTPException(404, "Candidate not found")
    with Image.open(image_dir(image_id) / "source.png") as source_image:
        image = source_image.convert("RGBA")
    with Image.open(path) as mask:
        image.putalpha(mask)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return Response(buffer.getvalue(), media_type="image/png")


@app.patch("/api/images/{image_id}/results/{result_id}")
def save_note(image_id: str, result_id: str, body: Note):
    path = result_dir(image_id, result_id) / "result.json"
    with lock:
        result = json.loads(path.read_text())
        result["note"] = body.note
        write_json(path, result)
    return {"ok": True}


@app.get("/api/images/{image_id}/export")
def export(image_id: str):
    directory = image_dir(image_id)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ("source.png", "image.json"):
            archive.write(directory / name, name)
        for manifest in sorted(directory.glob("*/result.json")):
            for path in manifest.parent.iterdir():
                if path.suffix in (".png", ".json", ".npy"):
                    archive.write(path, str(path.relative_to(directory)))
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="selection-comparison-{image_id[:8]}.zip"',
        },
    )
