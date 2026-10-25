"""Tests for the FastAPI service, with storage and inference stubbed.

These cover the seam that the Oct 7 deploy gate depends on: an upload goes in, a scored
reading comes back, and it lands in storage in a shape the dashboard can read. Nothing
here touches AWS or a real checkpoint — inference is replaced by a deterministic fake so
the assertions are about *plumbing*, which is what breaks on deploy day.

One behaviour is deliberately pinned down because it looks like a bug and is not: the
service boots and answers ``/health`` with **no model loaded**. A container that refused
to start without a checkpoint would convert a model problem into a deploy problem, which
is the one trade this project cannot afford.
"""

from __future__ import annotations

import io

import numpy as np
import pytest

pytest.importorskip("fastapi", reason="api extras not installed")
pytest.importorskip("httpx", reason="fastapi TestClient needs httpx")
pytest.importorskip("torch", reason="segformer module imports torch")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from drainwatch.config import Class  # noqa: E402

CI_HALF = 0.5  # the fake segmenter's choke index: half waste, half water


def jpeg_bytes(size=(64, 48), colour=(90, 90, 90)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def upload(client, *, lat=None, lon=None, captured_at=None, data=None):
    form = {}
    if lat is not None:
        form["lat"] = str(lat)
    if lon is not None:
        form["lon"] = str(lon)
    if captured_at is not None:
        form["captured_at"] = captured_at
    return client.post(
        "/analyze",
        files={"file": ("drain.jpg", data if data is not None else jpeg_bytes(), "image/jpeg")},
        data=form,
    )


@pytest.fixture
def api(tmp_path, monkeypatch):
    """The app module with storage pointed at tmp_path and model loading disabled.

    ``_load_model_quietly`` is stubbed out because the real one falls back to downloading
    the pretrained base when no checkpoint exists — a network call, in a unit test.
    """
    from drainwatch.api import main as module
    from drainwatch.api.storage import LocalStorage

    monkeypatch.setattr(module, "_load_model_quietly", lambda: None)
    monkeypatch.setattr(module, "get_storage", lambda: LocalStorage(tmp_path / "store"))
    monkeypatch.setitem(module.STATE, "model", None)
    monkeypatch.setitem(module.STATE, "error", None)
    monkeypatch.setitem(module.STATE, "device", "none")
    return module


@pytest.fixture
def client(api):
    with TestClient(api.app) as test_client:
        yield test_client


@pytest.fixture
def segmenter(api, monkeypatch):
    """Install a deterministic fake segmenter: top half waste, bottom half water."""

    def fake_predict(model, image, **kwargs):
        width, height = image.size
        mask = np.full((height, width), int(Class.WATER), dtype=np.uint8)
        mask[: height // 2] = int(Class.WASTE)
        return mask, np.full((height, width), 0.9, dtype=np.float32)

    monkeypatch.setattr("drainwatch.model.segformer.predict", fake_predict)
    monkeypatch.setitem(api.STATE, "model", object())  # any non-None sentinel
    monkeypatch.setitem(api.STATE, "model_trained", True)
    return fake_predict


# --- booting without a model ----------------------------------------------------


def test_health_is_ok_even_with_no_model(client):
    """The deploy gate: an HTTPS URL that answers is worth more than one that refuses."""
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is False
    assert body["storage"] == "local"


def test_health_reports_the_thresholds_it_is_scoring_with(client):
    """A reviewer must be able to see the cutoffs without reading the source."""
    thresholds = client.get("/health").json()["thresholds"]

    assert thresholds["watch"] == 0.40
    assert thresholds["critical"] == 0.70


def test_analyze_without_a_model_is_503_not_500(client):
    """Unavailable, not broken — and the reason has to travel with the status."""
    response = upload(client, lat=28.75, lon=77.1)

    assert response.status_code == 503
    assert "model" in response.json()["detail"].lower()


# --- the analyze path -----------------------------------------------------------


def test_analyze_scores_stores_and_returns_a_reading(client, segmenter):
    response = upload(client, lat=28.7604, lon=77.1035)

    assert response.status_code == 200, response.text
    reading = response.json()["reading"]
    assert reading["choke_index"] == pytest.approx(CI_HALF)
    assert reading["severity"] == "watch"
    assert reading["drain_id"] == "d_28.7604_77.1035"
    assert reading["image_url"] and reading["overlay_url"]
    assert reading["simulated"] is False  # a real measurement, not seeded


def test_analyze_writes_both_images_to_storage(client, segmenter, tmp_path):
    """The overlay is what the demo video shows; if it is not persisted there is nothing
    to show."""
    response = upload(client, lat=28.75, lon=77.1)

    prefix = response.json()["reading"]["reading_id"]
    media = tmp_path / "store" / "media" / "readings" / prefix
    assert (media / "source.jpg").exists()
    assert (media / "overlay.png").exists()
    # A real PNG, not an empty file.
    with Image.open(media / "overlay.png") as overlay:
        assert overlay.size == (64, 48)


def test_analyze_returns_the_drain_aggregate_alongside_the_reading(client, segmenter):
    """The dashboard updates the map from this, so one round trip has to carry both."""
    drain = upload(client, lat=28.75, lon=77.1).json()["drain"]

    assert drain["drain_id"] == "d_28.7500_77.1000"
    assert drain["reading_count"] == 1
    assert drain["trend"] is None  # one point is not a trend
    assert drain["history"] == [pytest.approx(CI_HALF)]


def test_repeat_visits_to_one_location_build_a_time_series(client, segmenter):
    """The predictive-maintenance claim in one test.

    Two uploads at coordinates that differ below the drain_id rounding (4 dp, ~11 m) must
    join a single drain's history rather than create two drains.
    """
    upload(client, lat=28.75, lon=77.1, captured_at="2026-09-01T10:00:00+00:00")
    second = upload(client, lat=28.750004, lon=77.100003, captured_at="2026-09-08T10:00:00+00:00")

    drain = second.json()["drain"]
    assert drain["reading_count"] == 2
    assert drain["trend"] == pytest.approx(0.0)  # same fake mask twice: flat, not None


def test_analyze_without_coordinates_still_scores_but_has_no_drain(client, segmenter):
    """Most web-sourced images have EXIF stripped. An unlocatable reading is still a
    reading; it just cannot go on the map."""
    response = upload(client)  # no lat/lon, JPEG carries no GPS

    assert response.status_code == 200
    assert response.json()["reading"]["choke_index"] == pytest.approx(CI_HALF)
    assert response.json()["drain"] is None


@pytest.mark.parametrize("lat,lon", [(91.0, 77.1), (28.75, 181.0), (-91.0, 0.0)])
def test_out_of_range_coordinates_are_400(client, segmenter, lat, lon):
    assert upload(client, lat=lat, lon=lon).status_code == 400


def test_empty_upload_is_400(client, segmenter):
    assert upload(client, data=b"").status_code == 400


def test_non_image_upload_is_400_not_500(client, segmenter):
    response = upload(client, data=b"this is not a JPEG")

    assert response.status_code == 400
    assert "image" in response.json()["detail"].lower()


# --- the priority queue ---------------------------------------------------------


@pytest.fixture
def three_drains(api, tmp_path):
    """Three drains written straight to storage, bypassing inference."""
    storage = api.STATE["storage"]
    rows = [
        ("d_28.1000_77.1000", 0.82, "critical", "2026-09-01T00:00:00+00:00", 0.82),
        ("d_28.2000_77.2000", 0.10, "ok", "2026-09-01T00:00:00+00:00", 0.10),
        ("d_28.3000_77.3000", 0.45, "watch", "2026-09-01T00:00:00+00:00", 0.45),
    ]
    for drain_id, ci, severity, when, _ in rows:
        lat, lon = (float(p) for p in drain_id.removeprefix("d_").split("_"))
        storage.put_reading(
            {
                "reading_id": f"r-{drain_id}",
                "drain_id": drain_id,
                "captured_at": when,
                "lat": lat,
                "lon": lon,
                "choke_index": ci,
                "severity": severity,
                "confidence": 0.9,
            }
        )
    return storage


def test_drains_are_ordered_worst_first(client, three_drains):
    """The dashboard's whole job is answering "which drain do I clear first"."""
    drains = client.get("/drains").json()

    assert [d["drain_id"] for d in drains] == [
        "d_28.1000_77.1000",
        "d_28.3000_77.3000",
        "d_28.2000_77.2000",
    ]


def test_drains_can_be_sorted_by_trend_instead(client, three_drains):
    response = client.get("/drains", params={"sort": "trend"})

    assert response.status_code == 200
    assert len(response.json()) == 3


def test_drain_detail_and_history_round_trip(client, three_drains):
    detail = client.get("/drains/d_28.1000_77.1000")
    history = client.get("/drains/d_28.1000_77.1000/history")

    assert detail.status_code == 200
    assert detail.json()["latest"]["severity"] == "critical"
    assert history.status_code == 200
    assert len(history.json()) == 1


def test_unknown_drain_is_404(client, three_drains):
    assert client.get("/drains/d_00.0000_00.0000").status_code == 404
    assert client.get("/drains/d_00.0000_00.0000/history").status_code == 404


def test_drains_is_empty_not_an_error_before_any_reading(client):
    """A freshly deployed, empty service must render an empty dashboard, not a 500."""
    response = client.get("/drains")

    assert response.status_code == 200
    assert response.json() == []


def test_one_malformed_row_does_not_blank_the_whole_dashboard(client, three_drains, capsys):
    """Per-row degradation, which is the difference between a bad row and a dead demo.

    ``/drains`` is the single request the map *and* the priority queue are built from, so
    a validation error anywhere in it used to mean an empty screen. A row with no
    ``severity`` is unrenderable — there is no honest colour for it — but the other two
    drains are fine and must still arrive.
    """
    three_drains.put_reading(
        {
            "reading_id": "r-broken",
            "drain_id": "d_28.9000_77.9000",
            "captured_at": "2026-09-01T00:00:00+00:00",
            "choke_index": 0.99,  # would sort first, so its absence is unmissable
            # no "severity" — required, and deliberately absent
        }
    )

    response = client.get("/drains")

    assert response.status_code == 200
    ids = [d["drain_id"] for d in response.json()]
    assert "d_28.9000_77.9000" not in ids
    assert len(ids) == 3, "the three good drains must survive the one bad one"
    assert "skipping drain d_28.9000_77.9000" in capsys.readouterr().out


def test_a_reading_without_pixel_counts_is_still_served(client, three_drains):
    """The pixel counts are diagnostic, not the product.

    They are optional in ``ReadingOut`` for this reason: a row carrying a score and a
    severity is actionable — "clear this drain" — whether or not the pixel tallies that
    produced it survived the round trip. Requiring them would make the dashboard hostage
    to its own debug fields.
    """
    reading = client.get("/drains").json()[0]["latest"]

    assert reading["choke_index"] == pytest.approx(0.82)
    assert reading["severity"] == "critical"
    assert reading["waste_px"] is None
