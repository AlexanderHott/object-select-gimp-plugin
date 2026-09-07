import numpy as np
import pytest
from PIL import Image

from comparison.models import Prediction
from object_select.worker import Session, decode, encode


class Runner:
    def __init__(self):
        self.requests = []
        self.warmups = []

    def warmup(self, model, image_id, image, progress):
        self.warmups.append((model, image_id, image.size))
        return {"load_seconds": 1, "encode_seconds": 2}

    def predict(self, request, image, parent, progress):
        self.requests.append(request)
        return Prediction([np.ones((30, 50), dtype=np.float32)], [0.9], {"predict_seconds": 0.01})


def test_snapshot_does_not_segment_and_each_prompt_uses_the_same_image_encoding_key():
    runner = Runner()
    session = Session(runner)
    image = encode(Image.new("RGBA", (50, 30), "white"))
    session.handle({"op": "image", "image": image}, print)
    assert runner.requests == []
    for x in (10, 11):
        result = session.handle({"op": "predict", "points": [{"x": x, "y": 15, "label": 1}]}, print)
        assert result["masks"]
    assert len(runner.requests) == 2
    assert [request["points"][0]["x"] for request in runner.requests] == [10, 11]
    assert runner.requests[0]["image_id"] == runner.requests[1]["image_id"]
    session.handle({"op": "image", "image": image}, print)
    session.handle({"op": "predict", "points": [{"x": 10, "y": 15, "label": 1}]}, print)
    assert runner.requests[2]["image_id"] != runner.requests[0]["image_id"]


def test_warmup_loads_selected_model_and_image_without_predicting_regions():
    runner = Runner()
    session = Session(runner)
    session.handle({"op": "image", "image": encode(Image.new("RGBA", (50, 30), "white"))}, print)
    result = session.handle({"op": "warmup", "model": "sam2-tiny"}, print)
    assert result["ready"] and "masks" not in result
    assert not runner.requests
    session.handle({"op": "predict", "model": "sam2-tiny", "points": []}, print)
    assert runner.warmups == [("sam2-tiny", runner.requests[0]["image_id"], (50, 30))]


@pytest.mark.parametrize("operation", ["prepare", "cached"])
def test_removed_region_operations_cannot_trigger_inference(operation):
    runner = Runner()
    session = Session(runner)
    session.handle({"op": "image", "image": encode(Image.new("RGBA", (50, 30), "white"))}, print)
    with pytest.raises(ValueError, match="Unsupported operation"):
        session.handle({"op": operation}, print)
    assert runner.requests == []


@pytest.mark.parametrize(
    ("mode", "threshold", "expected"),
    [
        ("crisp", 0.5, [0, 0, 255, 255, 255]),
        ("crisp", 0.75, [0, 0, 0, 255, 255]),
        ("soft", 0.5, [0, 125, 128, 191, 255]),
    ],
)
def test_refinement_output_mode_thresholds_without_smoothing(mode, threshold, expected):
    class MatteRunner:
        def predict(self, request, image, parent, progress):
            assert request["model"] == "vitmatte"
            assert parent.shape == (1, 5)
            matte = np.array([[0, 0.49, 0.5, 0.75, 1]], dtype=np.float32)
            return Prediction([matte], [None], {"predict_seconds": 0.01})

    session = Session(MatteRunner())
    session.handle({"op": "image", "image": encode(Image.new("RGBA", (5, 1), "white"))}, print)
    result = session.handle(
        {
            "op": "predict",
            "model": "vitmatte",
            "mask": encode(Image.new("L", (5, 1), 255)),
            "matte_output": mode,
            "matte_threshold": threshold,
            "cleanup": {"smooth_radius": 5, "hole_area": 10000, "island_area": 10000},
        },
        print,
    )
    assert list(decode(result["masks"][0]).tobytes()) == expected


def test_crisp_refinement_preserves_source_transparency():
    class MatteRunner:
        def predict(self, request, image, parent, progress):
            return Prediction([np.ones((1, 3))], [None], {"predict_seconds": 0.01})

    source = Image.new("RGBA", (3, 1), "white")
    source.putpixel((0, 0), (255, 255, 255, 0))
    source.putpixel((1, 0), (255, 255, 255, 128))
    session = Session(MatteRunner())
    session.handle({"op": "image", "image": encode(source)}, print)
    result = session.handle(
        {
            "op": "predict",
            "model": "vitmatte",
            "mask": encode(Image.new("L", (3, 1), 255)),
            "matte_output": "crisp",
        },
        print,
    )
    assert list(decode(result["masks"][0]).tobytes()) == [0, 128, 255]


@pytest.mark.parametrize(
    "settings",
    [
        {"matte_output": "unknown"},
        {"matte_threshold": 0},
        {"matte_threshold": 1},
        {"matte_threshold": float("nan")},
    ],
)
def test_invalid_refinement_options_are_rejected_before_inference(settings):
    runner = Runner()
    session = Session(runner)
    session.handle({"op": "image", "image": encode(Image.new("RGBA", (50, 30), "white"))}, print)
    with pytest.raises(ValueError):
        session.handle(
            {
                "op": "predict",
                "model": "vitmatte",
                "mask": encode(Image.new("L", (50, 30), 255)),
                "matte_output": "crisp",
                **settings,
            },
            print,
        )
    assert not runner.requests
