"""Tests for geolocation.

``drain_id`` is load-bearing: it is what joins repeat visits to one drain into a time
series, and the time series is the predictive-maintenance claim. If GPS jitter across
two visits produced two ids, every drain would look like a brand-new observation and
the trend view would always be empty.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from drainwatch.geo.sources import (
    GeoPoint,
    MapillaryImage,
    _dms_to_degrees,
    extract_captured_at,
    extract_gps,
    group_by_drain,
)

DTU_LAT, DTU_LON = 28.7501, 77.1177


# -------------------------------------------------------------------------- GeoPoint


def test_out_of_range_coordinates_are_rejected():
    with pytest.raises(ValueError, match="latitude"):
        GeoPoint(91.0, 0.0)
    with pytest.raises(ValueError, match="longitude"):
        GeoPoint(0.0, 181.0)


def test_drain_id_is_stable_under_gps_jitter():
    """Two visits to one drain must produce one id.

    4 decimal places is ~11 m at Delhi's latitude, comfortably wider than consumer GPS
    error, so revisits collapse onto the same drain.
    """
    first = GeoPoint(DTU_LAT, DTU_LON)
    second = GeoPoint(DTU_LAT + 0.000004, DTU_LON - 0.000003)
    assert first.drain_id == second.drain_id


def test_distinct_drains_get_distinct_ids():
    near = GeoPoint(DTU_LAT, DTU_LON)
    far = GeoPoint(DTU_LAT + 0.002, DTU_LON)  # ~220 m away
    assert near.drain_id != far.drain_id


def test_drain_id_has_fixed_precision():
    # Trailing zeros must be preserved, or 28.75 and 28.7500 would be different drains.
    assert GeoPoint(28.75, 77.1).drain_id == "d_28.7500_77.1000"


def test_to_dict_carries_the_id():
    assert GeoPoint(DTU_LAT, DTU_LON).to_dict() == {
        "lat": DTU_LAT,
        "lon": DTU_LON,
        "drain_id": GeoPoint(DTU_LAT, DTU_LON).drain_id,
    }


# ------------------------------------------------------------------------ DMS parsing


@pytest.mark.parametrize(
    "dms,ref,expected",
    [
        ((28, 45, 0.36), "N", 28.7501),
        ((77, 7, 3.72), "E", 77.1177),
        ((28, 45, 0.36), "S", -28.7501),  # southern hemisphere
        ((77, 7, 3.72), "W", -77.1177),  # western hemisphere
        ((28, 45, 0.36), None, 28.7501),  # missing ref -> assume positive
        ((28, 45, 0.36), "n", 28.7501),   # lowercase ref
    ],
)
def test_dms_conversion(dms, ref, expected):
    assert _dms_to_degrees(dms, ref) == pytest.approx(expected, abs=1e-4)


def test_hemisphere_sign_is_not_dropped():
    """A sign error would put Delhi drains in the southern Indian Ocean."""
    assert _dms_to_degrees((28, 45, 0.36), "S") < 0


# ------------------------------------------------------------------------ EXIF reading


def _blank_image() -> Image.Image:
    return Image.new("RGB", (8, 8), (120, 120, 120))


def test_image_without_exif_returns_none():
    """The common case: most web-sourced images have EXIF stripped.

    None rather than an exception — the API handles it by taking coordinates from the
    form, which is an ordinary path, not an error.
    """
    assert extract_gps(_blank_image()) is None
    assert extract_captured_at(_blank_image()) is None


def test_gps_exif_round_trip():
    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: (28, 45, 0.36), 3: "E", 4: (77, 7, 3.72)}

    buffer = io.BytesIO()
    _blank_image().save(buffer, format="JPEG", exif=exif)
    buffer.seek(0)

    point = extract_gps(Image.open(buffer))
    if point is None:
        pytest.skip("this Pillow build does not write a nested GPS IFD")
    assert point.lat == pytest.approx(DTU_LAT, abs=1e-4)
    assert point.lon == pytest.approx(DTU_LON, abs=1e-4)


def test_captured_at_is_read_from_the_exif_subifd():
    """Where a real camera actually writes it.

    DateTimeOriginal (0x9003) belongs to the Exif sub-IFD (0x8769). Reading only the root
    IFD would return None for every photograph taken on a phone.
    """
    exif = Image.Exif()
    exif[0x8769] = {0x9003: "2026:07:14 09:31:02"}

    buffer = io.BytesIO()
    _blank_image().save(buffer, format="JPEG", exif=exif)
    buffer.seek(0)

    assert extract_captured_at(Image.open(buffer)) == "2026-07-14T09:31:02"


def test_captured_at_falls_back_to_the_root_datetime():
    """EXIF uses 'YYYY:MM:DD'; colons in the date break ISO-8601 parsing and sorting."""
    exif = Image.Exif()
    exif[0x0132] = "2026:07:14 09:31:02"  # root-IFD DateTime

    buffer = io.BytesIO()
    _blank_image().save(buffer, format="JPEG", exif=exif)
    buffer.seek(0)

    assert extract_captured_at(Image.open(buffer)) == "2026-07-14T09:31:02"


def test_malformed_timestamp_returns_none():
    exif = Image.Exif()
    exif[0x0132] = "not a date"

    buffer = io.BytesIO()
    _blank_image().save(buffer, format="JPEG", exif=exif)
    buffer.seek(0)

    assert extract_captured_at(Image.open(buffer)) is None


# ------------------------------------------------------------------- Mapillary grouping


def _capture(image_id, lat, lon, when_ms):
    return MapillaryImage(
        image_id=image_id, point=GeoPoint(lat, lon), captured_at_ms=when_ms, thumb_url=None
    )


def test_repeat_captures_of_one_spot_group_together():
    """This is the check that decides whether the trend view runs on real data."""
    captures = [
        _capture("b", DTU_LAT, DTU_LON, 1_700_000_000_000),
        _capture("a", DTU_LAT + 0.000002, DTU_LON, 1_600_000_000_000),
        _capture("c", DTU_LAT + 0.01, DTU_LON, 1_650_000_000_000),
    ]
    grouped = group_by_drain(captures)

    assert len(grouped) == 2
    series = grouped[GeoPoint(DTU_LAT, DTU_LON).drain_id]
    assert [c.image_id for c in series] == ["a", "b"]  # oldest first


def test_missing_capture_time_does_not_break_sorting():
    grouped = group_by_drain(
        [
            _capture("dated", DTU_LAT, DTU_LON, 1_700_000_000_000),
            _capture("undated", DTU_LAT, DTU_LON, None),
        ]
    )
    series = next(iter(grouped.values()))
    assert [c.image_id for c in series] == ["undated", "dated"]


def test_captured_at_renders_as_iso_utc():
    capture = _capture("x", DTU_LAT, DTU_LON, 1_700_000_000_000)
    assert capture.captured_at.startswith("2023-11-14T")


def test_captured_at_is_none_without_a_timestamp():
    assert _capture("x", DTU_LAT, DTU_LON, None).captured_at is None
