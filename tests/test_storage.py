"""Tests for persistence and the aggregation that drives the priority queue.

``group_readings`` / ``trend_of`` decide what a supervisor sees first, so they are
tested directly rather than through the HTTP layer. No boto3 import is triggered here —
``AwsStorage`` imports it lazily inside ``__init__``.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from drainwatch.api.storage import (
    UNLOCATED,
    LocalStorage,
    _from_dynamo,
    _to_dynamo,
    group_readings,
    trend_of,
)


def reading(drain_id, captured_at, choke_index, reading_id="r1"):
    return {
        "reading_id": reading_id,
        "drain_id": drain_id,
        "captured_at": captured_at,
        "choke_index": choke_index,
    }


# --------------------------------------------------------------------------- grouping


def test_group_readings_sorts_chronologically_regardless_of_insertion_order():
    readings = [
        reading("d_1", "2026-03-10T00:00:00", 0.7, "c"),
        reading("d_1", "2026-01-10T00:00:00", 0.2, "a"),
        reading("d_1", "2026-02-10T00:00:00", 0.5, "b"),
    ]
    grouped = group_readings(readings)

    assert [r["reading_id"] for r in grouped["d_1"]] == ["a", "b", "c"]


def test_group_readings_separates_drains():
    grouped = group_readings(
        [reading("d_1", "2026-01-01T00:00:00", 0.1), reading("d_2", "2026-01-01T00:00:00", 0.9)]
    )
    assert set(grouped) == {"d_1", "d_2"}


def test_unlocated_readings_are_excluded():
    """A reading with no coordinates cannot be a map pin or join a history.

    Including it would create a phantom drain named "unlocated" that accumulates
    unrelated readings from across the city into one meaningless trend line.
    """
    grouped = group_readings(
        [
            reading(None, "2026-01-01T00:00:00", 0.5),
            reading(UNLOCATED, "2026-01-01T00:00:00", 0.5),
            reading("d_1", "2026-01-01T00:00:00", 0.5),
        ]
    )
    assert set(grouped) == {"d_1"}


def test_missing_captured_at_sorts_first_without_raising():
    """Sorting must tolerate a null timestamp rather than crash the dashboard."""
    readings = [
        reading("d_1", "2026-01-02T00:00:00", 0.4, "dated"),
        reading("d_1", None, 0.1, "undated"),
    ]
    grouped = group_readings(readings)
    assert [r["reading_id"] for r in grouped["d_1"]] == ["undated", "dated"]


# ------------------------------------------------------------------------------ trend


def test_trend_is_latest_minus_first():
    history = [
        reading("d_1", "2026-01-01T00:00:00", 0.21),
        reading("d_1", "2026-01-08T00:00:00", 0.48),
        reading("d_1", "2026-01-15T00:00:00", 0.71),
    ]
    assert trend_of(history) == pytest.approx(0.50)


def test_trend_is_none_for_a_single_reading():
    """One point is not a trend.

    Returning 0.0 would claim "stable" on no evidence, and the dashboard sorts by
    trend — a fabricated zero would rank a brand-new drain against real histories.
    """
    assert trend_of([reading("d_1", "2026-01-01T00:00:00", 0.4)]) is None


def test_trend_ignores_invalid_readings():
    history = [
        reading("d_1", "2026-01-01T00:00:00", None),  # INVALID — no drain in frame
        reading("d_1", "2026-01-08T00:00:00", 0.30),
        reading("d_1", "2026-01-15T00:00:00", 0.60),
    ]
    assert trend_of(history) == pytest.approx(0.30)


def test_trend_is_none_when_only_one_reading_scored():
    history = [
        reading("d_1", "2026-01-01T00:00:00", None),
        reading("d_1", "2026-01-08T00:00:00", 0.42),
    ]
    assert trend_of(history) is None


def test_trend_can_be_negative():
    """A drain that was cleared should rank below one that is filling up."""
    history = [
        reading("d_1", "2026-01-01T00:00:00", 0.80),
        reading("d_1", "2026-01-08T00:00:00", 0.10),
    ]
    assert trend_of(history) == pytest.approx(-0.70)


# ---------------------------------------------------------------- dynamo type mapping


def test_floats_become_decimals_recursively():
    """boto3 raises on a bare float, and the choke index is a float."""
    converted = _to_dynamo({"ci": 0.7, "nested": {"c": 0.55}, "list": [0.1, 2]})

    assert isinstance(converted["ci"], Decimal)
    assert isinstance(converted["nested"]["c"], Decimal)
    assert isinstance(converted["list"][0], Decimal)
    assert converted["list"][1] == 2 and not isinstance(converted["list"][1], Decimal)


def test_decimal_conversion_avoids_binary_float_artefacts():
    # Decimal(0.55) would be 0.5500000000000000444...; Decimal("0.55") is exact.
    assert _to_dynamo(0.55) == Decimal("0.55")


def test_round_trip_preserves_values_and_int_ness():
    original = {"ci": 0.7, "waste_px": 1200, "conf": 0.925}
    restored = _from_dynamo(_to_dynamo(original))

    assert restored["ci"] == pytest.approx(0.7)
    assert restored["waste_px"] == 1200
    assert isinstance(restored["waste_px"], int)
    assert isinstance(restored["ci"], float)


def test_none_passes_through_unchanged():
    """An INVALID reading carries choke_index=None and must survive the round trip."""
    assert _from_dynamo(_to_dynamo({"choke_index": None}))["choke_index"] is None


# ---------------------------------------------------------------------- local backend


def test_local_storage_round_trips_images_and_readings(tmp_path):
    storage = LocalStorage(tmp_path)

    url = storage.put_image("readings/abc/source.jpg", b"\xff\xd8\xff")
    assert url == "/media/readings/abc/source.jpg"
    assert (tmp_path / "media" / "readings" / "abc" / "source.jpg").read_bytes() == b"\xff\xd8\xff"

    storage.put_reading(reading("d_1", "2026-01-01T00:00:00", 0.4, "a"))
    storage.put_reading(reading("d_2", "2026-01-02T00:00:00", 0.8, "b"))

    assert len(storage.all_readings()) == 2
    assert [r["reading_id"] for r in storage.drain_history("d_1")] == ["a"]


def test_local_storage_history_is_chronological(tmp_path):
    storage = LocalStorage(tmp_path)
    storage.put_reading(reading("d_1", "2026-03-01T00:00:00", 0.7, "late"))
    storage.put_reading(reading("d_1", "2026-01-01T00:00:00", 0.2, "early"))

    assert [r["reading_id"] for r in storage.drain_history("d_1")] == ["early", "late"]


def test_local_storage_reads_empty_before_first_write(tmp_path):
    assert LocalStorage(tmp_path).all_readings() == []


def test_local_storage_tolerates_blank_lines(tmp_path):
    """A JSONL file with a trailing newline must not raise on read."""
    storage = LocalStorage(tmp_path)
    storage.readings_path.write_text(
        json.dumps(reading("d_1", "2026-01-01T00:00:00", 0.4)) + "\n\n"
    )
    assert len(storage.all_readings()) == 1


def test_unknown_drain_history_is_empty_not_an_error(tmp_path):
    assert LocalStorage(tmp_path).drain_history("d_nope") == []
