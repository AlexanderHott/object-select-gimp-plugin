import io
import json
import threading
import time
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from comparison import server
from comparison.imaging import crop_bounds, make_trimap, mask_image, read_image
from comparison.models import Prediction


def png(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_orientation_and_transparency():
    source = Image.new("RGBA", (20, 10), (255, 0, 0, 0))
    source.putpixel((2, 3), (0, 0, 0, 255))
    result = read_image(png(source))
    assert result.mode == "RGB"
    assert result.getpixel((0, 0)) == (255, 255, 255)
    assert result.getpixel((2, 3)) == (0, 0, 0)
    source = Image.new("RGB", (20, 10))
    exif = source.getexif()
    exif[274] = 6
    buffer = io.BytesIO()
    source.save(buffer, format="JPEG", exif=exif)
    assert read_image(buffer.getvalue()).size == (10, 20)


def test_crop_keeps_last_pixel_and_context():
    mask = np.zeros((30, 50))
    mask[10:30, 25:50] = 1
    assert crop_bounds(mask, 0) == (25, 10, 50, 30)
    assert crop_bounds(mask, 0.2) == (20, 5, 50, 30)
    with pytest.raises(ValueError, match="empty"):
        crop_bounds(np.zeros((20, 20)), 0.1)


def test_trimap_has_foreground_background_and_unknown_boundary():
    mask = np.zeros((40, 40))
    mask[10:30, 10:30] = 1
    trimap = make_trimap(mask, 3)
    assert trimap[20, 20] == 255
    assert trimap[0, 0] == 0
    assert trimap[10, 15] == trimap[9, 15] == 128
    with pytest.raises(ValueError, match="Reduce"):
        make_trimap(mask, 30)


def test_soft_export_preserves_fractional_values():
    assert np.asarray(mask_image(np.array([[0, 0.5, 1]]))).tolist() == [[0, 128, 255]]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA", tmp_path)
    with TestClient(server.app) as client:
        yield client


def upload(client):
    response = client.post(
        "/api/images",
        files={"file": ("test.png", png(Image.new("RGB", (40, 30), "red")), "image/png")},
    )
    assert response.status_code == 200
    return response.json()["id"]


def wait_job(client, job_id):
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] not in ("queued", "running"):
            return job
        time.sleep(0.01)
    pytest.fail("Worker did not complete")


def test_run_exports_and_refinement_use_exact_candidate(client, monkeypatch):
    calls = []

    class FakeRunner:
        def predict(self, request, image, parent, progress):
            calls.append((request, parent))
            progress("Predicting")
            mask = np.zeros((image.height, image.width), dtype=np.float32)
            mask[5:25, 10:30] = 0.75
            return Prediction([mask, mask * 0], [0.9, 0.1], {"device": "test"})

    monkeypatch.setattr(server, "runner", FakeRunner())
    image_id = upload(client)
    payload = {
        "image_id": image_id,
        "model": "sam2-tiny",
        "points": [{"x": 20, "y": 15, "label": 1}],
    }
    response = client.post("/api/runs", json=payload)
    assert response.status_code == 202
    job = wait_job(client, response.json()["id"])
    assert job["state"] == "done"
    result_id = job["result"]["id"]
    base = f"/api/images/{image_id}/results/{result_id}"
    mask = Image.open(io.BytesIO(client.get(f"{base}/mask-0.png").content))
    cutout = Image.open(io.BytesIO(client.get(f"{base}/cutout/0.png").content))
    assert mask.size == cutout.size == (40, 30)
    assert cutout.getpixel((20, 15)) == (255, 0, 0, 191)
    assert client.get(f"{base}/mask-8.png").status_code == 404
    assert client.get(f"{base}/cutout/-1.png").status_code == 404
    assert client.patch(base, json={"note": "Keep the thin edge"}).status_code == 200
    second = client.post(
        "/api/runs",
        json={"image_id": image_id, "model": "vitmatte", "parent_id": result_id, "candidate": 1},
    )
    assert wait_job(client, second.json()["id"])["state"] == "done"
    assert not calls[-1][1].any()
    experiment = zipfile.ZipFile(io.BytesIO(client.get(f"/api/images/{image_id}/export").content))
    saved = json.loads(experiment.read(f"{result_id}/result.json"))
    assert saved["request"]["points"] == payload["points"]
    assert saved["note"] == "Keep the thin edge"
    raw = np.load(io.BytesIO(experiment.read(f"{result_id}/mask-0.npy")))
    assert raw[15, 20] == 0.75
    assert len(client.get(f"/api/images/{image_id}/results").json()) == 2
    other_image = upload(client)
    assert (
        client.post(
            "/api/runs", json={"image_id": other_image, "model": "vitmatte", "parent_id": result_id}
        ).status_code
        == 404
    )


def test_invalid_prompts_and_files(client):
    image_id = upload(client)
    base = {"image_id": image_id, "model": "sam2-tiny"}
    assert client.post("/api/runs", json=base).status_code == 422
    assert (
        client.post(
            "/api/runs", json={**base, "points": [{"x": 40, "y": 2, "label": 1}]}
        ).status_code
        == 422
    )
    assert client.post("/api/runs", json={**base, "box": [20, 10, 10, 20]}).status_code == 422
    assert client.post("/api/runs", json={**base, "model": "vitmatte"}).status_code == 422
    assert (
        client.post("/api/images", files={"file": ("bad.png", b"not an image")}).status_code == 400
    )


def test_inference_failure_reaches_client(client, monkeypatch):
    class BrokenRunner:
        def predict(self, *args):
            raise RuntimeError("Cannot access gated repo")

    monkeypatch.setattr(server, "runner", BrokenRunner())
    image_id = upload(client)
    response = client.post(
        "/api/runs", json={"image_id": image_id, "model": "sam3", "box": [1, 1, 20, 20]}
    )
    job = wait_job(client, response.json()["id"])
    assert job["state"] == "error"
    assert "hf auth login" in job["error"]
    assert client.get(f"/api/images/{image_id}/results").json() == []


def test_cancel_discards_result_after_active_operation(client, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    class SlowRunner:
        def predict(self, request, image, parent, progress):
            entered.set()
            assert release.wait(timeout=3)
            progress("Saving")
            pytest.fail("Cancelled inference must stop at the next stage")

    monkeypatch.setattr(server, "runner", SlowRunner())
    image_id = upload(client)
    response = client.post("/api/runs", json={"image_id": image_id, "model": "birefnet"})
    job_id = response.json()["id"]
    try:
        assert entered.wait(timeout=3)
        assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    finally:
        release.set()
    assert wait_job(client, job_id)["state"] == "cancelled"
    assert client.get(f"/api/images/{image_id}/results").json() == []
