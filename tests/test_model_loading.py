"""Tests for model loading, checkpoint validation, and inference safety.

These verify that the application cannot silently serve predictions from an
untrained segmentation head, and that missing/invalid checkpoints produce
clear, machine-readable failures.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("fastapi", reason="api extras not installed")
pytest.importorskip("httpx", reason="fastapi TestClient needs httpx")
pytest.importorskip("torch", reason="segformer module imports torch")

from fastapi.testclient import TestClient  # noqa: E402

from drainwatch.config import NUM_CLASSES, Class  # noqa: E402


def _make_client(api_module):
    """Create a TestClient from the api module."""
    return TestClient(api_module.app)


@pytest.fixture
def api(tmp_path, monkeypatch):
    """API module with storage pointed at tmp_path and model loading disabled."""
    from drainwatch.api import main as module
    from drainwatch.api.storage import LocalStorage

    monkeypatch.setattr(module, "_load_model_quietly", lambda: None)
    monkeypatch.setattr(module, "get_storage", lambda: LocalStorage(tmp_path / "store"))
    monkeypatch.setitem(module.STATE, "model", None)
    monkeypatch.setitem(module.STATE, "error", None)
    monkeypatch.setitem(module.STATE, "device", "none")
    monkeypatch.setitem(module.STATE, "model_trained", False)
    monkeypatch.setitem(module.STATE, "checkpoint", "none")
    return module


@pytest.fixture
def client(api):
    with TestClient(api.app) as tc:
        yield tc


# --- Missing checkpoint behavior ---


def test_missing_checkpoint_refuses_inference(client):
    """When no trained checkpoint is loaded, /analyze must return 503, not fake results."""
    import io
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (90, 90, 90)).save(buf, format="JPEG")
    buf.seek(0)

    response = client.post(
        "/analyze",
        files={"file": ("test.jpg", buf.getvalue(), "image/jpeg")},
        data={"lat": "28.75", "lon": "77.1"},
    )
    assert response.status_code == 503
    assert "model" in response.json()["detail"].lower()


def test_health_reports_model_not_loaded_when_no_checkpoint(client):
    """Health must clearly report that the model is not loaded."""
    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert health["model_loaded"] is False
    assert health["model_trained"] is False


def test_health_reports_model_trained_when_checkpoint_loaded(api, monkeypatch):
    """When a real checkpoint is loaded, health must report model_trained=True."""
    monkeypatch.setitem(api.STATE, "model", object())  # sentinel
    monkeypatch.setitem(api.STATE, "model_trained", True)
    monkeypatch.setitem(api.STATE, "checkpoint", "/path/to/checkpoint")
    monkeypatch.setitem(api.STATE, "device", "cpu")

    with TestClient(api.app) as tc:
        health = tc.get("/health").json()
    assert health["model_loaded"] is True
    assert health["model_trained"] is True
    assert health["checkpoint"] == "/path/to/checkpoint"


# --- Checkpoint validation ---


def test_load_model_quietly_missing_checkpoint(tmp_path, monkeypatch):
    """_load_model_quietly must not load a model when checkpoint doesn't exist."""
    from drainwatch.api import main as module

    monkeypatch.setenv("DRAINWATCH_CHECKPOINT", str(tmp_path / "nonexistent"))
    # Reset state
    module.STATE["model"] = None
    module.STATE["error"] = None
    module.STATE["model_trained"] = False
    module.STATE["checkpoint"] = "none"

    module._load_model_quietly()

    assert module.STATE["model"] is None
    assert module.STATE["model_trained"] is False
    assert module.STATE["checkpoint"] == "missing"
    assert "not found" in module.STATE["error"]


def test_load_model_quietly_missing_config(tmp_path, monkeypatch):
    """A checkpoint dir without config.json must be treated as invalid."""
    from drainwatch.api import main as module

    ckpt_dir = tmp_path / "bad_checkpoint"
    ckpt_dir.mkdir()
    (ckpt_dir / "model.safetensors").write_bytes(b"fake")

    monkeypatch.setenv("DRAINWATCH_CHECKPOINT", str(ckpt_dir))
    module.STATE["model"] = None
    module.STATE["error"] = None
    module.STATE["model_trained"] = False
    module.STATE["checkpoint"] = "none"

    module._load_model_quietly()

    assert module.STATE["model"] is None
    assert module.STATE["checkpoint"] == "invalid"
    assert "config.json" in module.STATE["error"]


def test_load_model_quietly_incompatible_classes(tmp_path, monkeypatch):
    """A checkpoint with wrong number of classes must be rejected."""
    from drainwatch.api import main as module

    ckpt_dir = tmp_path / "wrong_classes"
    ckpt_dir.mkdir()
    config = {
        "model_type": "segformer",
        "num_labels": 150,  # ADE20K classes, not our 3
        "id2label": {str(i): f"class_{i}" for i in range(150)},
    }
    (ckpt_dir / "config.json").write_text(json.dumps(config))

    monkeypatch.setenv("DRAINWATCH_CHECKPOINT", str(ckpt_dir))
    module.STATE["model"] = None
    module.STATE["error"] = None
    module.STATE["model_trained"] = False
    module.STATE["checkpoint"] = "none"

    module._load_model_quietly()

    assert module.STATE["model"] is None
    assert module.STATE["checkpoint"] == "incompatible"
    assert "150" in module.STATE["error"]
    assert str(NUM_CLASSES) in module.STATE["error"]


# --- Inference output structure ---


def test_inference_returns_correct_structure(api, monkeypatch):
    """A successful inference must return the expected fields."""
    import io
    from PIL import Image

    def fake_predict(model, image, **kwargs):
        w, h = image.size
        mask = np.full((h, w), int(Class.WATER), dtype=np.uint8)
        mask[: h // 2] = int(Class.WASTE)
        return mask, np.full((h, w), 0.9, dtype=np.float32)

    monkeypatch.setattr("drainwatch.model.segformer.predict", fake_predict)
    monkeypatch.setitem(api.STATE, "model", object())
    monkeypatch.setitem(api.STATE, "model_trained", True)

    with TestClient(api.app) as tc:
        buf = io.BytesIO()
        Image.new("RGB", (64, 48)).save(buf, format="JPEG")
        response = tc.post(
            "/analyze",
            files={"file": ("test.jpg", buf.getvalue(), "image/jpeg")},
            data={"lat": "28.75", "lon": "77.1"},
        )

    assert response.status_code == 200
    body = response.json()
    reading = body["reading"]
    assert "choke_index" in reading
    assert "severity" in reading
    assert reading["choke_index"] is not None
    assert reading["severity"] in ("ok", "watch", "critical", "needs_review", "invalid")


# --- Class mapping ---


def test_class_mapping_is_consistent():
    """Class enum values must match the config mapping."""
    from drainwatch.config import CLASS_NAMES

    assert CLASS_NAMES[Class.STRUCTURE] == "structure"
    assert CLASS_NAMES[Class.WATER] == "water"
    assert CLASS_NAMES[Class.WASTE] == "waste"
    assert Class.STRUCTURE == 0
    assert Class.WATER == 1
    assert Class.WASTE == 2
    assert NUM_CLASSES == 3
