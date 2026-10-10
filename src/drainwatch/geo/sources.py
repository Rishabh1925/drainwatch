"""Geolocation: EXIF GPS extraction and Mapillary imagery pull.

Two jobs:

1. **Where was this photo taken.** Without a location a reading cannot go on a map or
   join a drain's history, so a reading with no coordinates is explicitly marked rather
   than silently dropped at a lower layer.
2. **Where do photos come from at all.** With no local photo collection possible,
   Mapillary is the source of geotagged street-level Delhi imagery (CC-BY-SA), and its
   repeat captures of the same location are what make the *trend* view real rather than
   simulated.

> The Mapillary Graph API shape below was written without network access.
> **Verify before relying on it** — run ``drainwatch geo check`` with a token.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

MAPILLARY_GRAPH = "https://graph.mapillary.com/images"

# EXIF tag numbers (see Exif 2.3 §4.6.6). Used directly to avoid depending on
# PIL.ExifTags name tables, which have shifted between Pillow versions.
_GPS_IFD = 0x8825
_EXIF_IFD = 0x8769
_DATETIME_ORIGINAL = 0x9003  # lives in the Exif sub-IFD on real cameras
_DATETIME = 0x0132  # root-IFD file timestamp; the fallback
_GPS_LAT_REF, _GPS_LAT, _GPS_LON_REF, _GPS_LON = 1, 2, 3, 4

# Approximate bounding boxes (west, south, east, north) for the pilot area.
# DTU Delhi sits at roughly 28.750 N, 77.118 E with Bawana Road running north.
DTU_BBOX = (77.100, 28.735, 77.140, 28.775)
DELHI_BBOX = (76.84, 28.40, 77.35, 28.88)

# Grid used to collapse repeat captures of one spot into one drain. 4 decimal places is
# ~11 m at Delhi's latitude — tight enough to separate adjacent drains, loose enough
# that GPS jitter across visits still lands on the same id.
DRAIN_ID_DECIMALS = 4


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lon: float

    def __post_init__(self) -> None:
        if not -90.0 <= self.lat <= 90.0:
            raise ValueError(f"latitude out of range: {self.lat}")
        if not -180.0 <= self.lon <= 180.0:
            raise ValueError(f"longitude out of range: {self.lon}")

    @property
    def drain_id(self) -> str:
        """Stable id for the drain at this location.

        Derived from coordinates rather than assigned, so two visits to the same drain
        on different days join into one time series without any registry lookup.
        """
        return f"d_{self.lat:.{DRAIN_ID_DECIMALS}f}_{self.lon:.{DRAIN_ID_DECIMALS}f}"

    def to_dict(self) -> dict:
        return {"lat": self.lat, "lon": self.lon, "drain_id": self.drain_id}


def _rational_to_float(value) -> float:
    """Pillow returns EXIF rationals as IFDRational, which casts cleanly to float."""
    return float(value)


def _dms_to_degrees(dms, ref: str | None) -> float:
    """Convert EXIF degrees/minutes/seconds plus a N/S/E/W reference to signed degrees."""
    degrees, minutes, seconds = (_rational_to_float(v) for v in dms)
    value = degrees + minutes / 60.0 + seconds / 3600.0
    if ref and ref.upper() in {"S", "W"}:
        value = -value
    return value


def extract_gps(source: Path | Image.Image) -> GeoPoint | None:
    """Read GPS coordinates from EXIF. Returns None when the image carries none.

    Returning None rather than raising is deliberate: most images from the web have had
    EXIF stripped, and that is an ordinary case the caller handles by asking the user
    for a location, not an error.
    """
    image = Image.open(source) if isinstance(source, (str, Path)) else source
    try:
        exif = image.getexif()
    except Exception:  # pragma: no cover - malformed EXIF blocks vary wildly
        return None
    if not exif:
        return None

    gps = exif.get_ifd(_GPS_IFD)
    if not gps or _GPS_LAT not in gps or _GPS_LON not in gps:
        return None

    try:
        lat = _dms_to_degrees(gps[_GPS_LAT], gps.get(_GPS_LAT_REF))
        lon = _dms_to_degrees(gps[_GPS_LON], gps.get(_GPS_LON_REF))
        return GeoPoint(lat, lon)
    except (TypeError, ValueError, ZeroDivisionError):
        return None  # present but unparseable — same handling as absent


def extract_captured_at(source: Path | Image.Image) -> str | None:
    """EXIF DateTimeOriginal as an ISO-8601 string, or None.

    ``DateTimeOriginal`` lives in the Exif *sub*-IFD, not the root IFD — reading only the
    root would miss the timestamp on every photograph a real camera or phone produced.
    The root ``DateTime`` is the fallback, and it is a weaker signal: editors rewrite it.

    EXIF stores ``YYYY:MM:DD HH:MM:SS``; the colons in the date are swapped for dashes so
    the value sorts correctly and parses as ISO-8601 downstream.
    """
    image = Image.open(source) if isinstance(source, (str, Path)) else source
    try:
        exif = image.getexif()
    except Exception:  # pragma: no cover
        return None
    if not exif:
        return None

    try:
        raw = exif.get_ifd(_EXIF_IFD).get(_DATETIME_ORIGINAL)
    except Exception:  # pragma: no cover - malformed sub-IFDs vary wildly
        raw = None
    if not raw:
        raw = exif.get(_DATETIME_ORIGINAL) or exif.get(_DATETIME)

    if not isinstance(raw, str) or len(raw) < 19:
        return None
    date, _, clock = raw.partition(" ")
    return f"{date.replace(':', '-')}T{clock}"


@dataclass(frozen=True)
class MapillaryImage:
    """One Mapillary capture: a geotagged, dated, downloadable frame."""

    image_id: str
    point: GeoPoint
    captured_at_ms: int | None
    thumb_url: str | None

    @property
    def captured_at(self) -> str | None:
        if self.captured_at_ms is None:
            return None
        from datetime import datetime, timezone

        return datetime.fromtimestamp(self.captured_at_ms / 1000, tz=timezone.utc).isoformat()


def _token(explicit: str | None = None) -> str:
    token = explicit or os.environ.get("MAPILLARY_TOKEN")
    if not token:
        raise RuntimeError(
            "No Mapillary token. Create a client token at "
            "https://www.mapillary.com/dashboard/developers and export it:\n"
            "    export MAPILLARY_TOKEN=MLY|...")
    return token


def search_mapillary(
    bbox: tuple[float, float, float, float] = DTU_BBOX,
    *,
    token: str | None = None,
    limit: int = 200,
    thumb_field: str = "thumb_1024_url",
) -> list[MapillaryImage]:
    """List captures inside a bounding box, newest-capture metadata included.

    Args:
        bbox: ``(west, south, east, north)`` in degrees.
        limit: API page size; Mapillary caps this (2000 at time of writing).
        thumb_field: ``thumb_256_url`` … ``thumb_2048_url``. 1024 is enough for
            SegFormer at 512 and keeps the pull fast.
    """
    import requests

    params = {
        "access_token": _token(token),
        "fields": f"id,geometry,captured_at,{thumb_field}",
        "bbox": ",".join(str(v) for v in bbox),
        "limit": limit,
    }
    response = requests.get(MAPILLARY_GRAPH, params=params, timeout=60)
    response.raise_for_status()

    results: list[MapillaryImage] = []
    for item in response.json().get("data", []):
        coords = (item.get("geometry") or {}).get("coordinates")
        if not coords or len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])  # GeoJSON is lon, lat
        try:
            point = GeoPoint(lat, lon)
        except ValueError:
            continue
        results.append(
            MapillaryImage(
                image_id=str(item["id"]),
                point=point,
                captured_at_ms=item.get("captured_at"),
                thumb_url=item.get(thumb_field),
            )
        )
    return results


def download_mapillary(
    images: list[MapillaryImage], out_dir: Path, *, overwrite: bool = False
) -> list[Path]:
    """Download capture thumbnails into ``out_dir``, named by image id.

    Failures are skipped rather than fatal — one dead URL in a 200-image pull should not
    cost the whole batch.
    """
    import requests

    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for image in images:
        if not image.thumb_url:
            continue
        destination = out_dir / f"mly_{image.image_id}.jpg"
        if destination.exists() and not overwrite:
            paths.append(destination)
            continue
        try:
            response = requests.get(image.thumb_url, timeout=60)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"  skip {image.image_id}: {exc}")
            continue
        part = destination.with_suffix(".part")
        part.write_bytes(response.content)
        part.rename(destination)
        paths.append(destination)

    return paths


def group_by_drain(images: list[MapillaryImage]) -> dict[str, list[MapillaryImage]]:
    """Group captures by drain id, each list sorted oldest-first.

    This is the check for whether the trend story can run on real data: any group with
    two or more entries on different dates is a genuine longitudinal observation of one
    location. If every group has length 1, the demo history has to be simulated — and
    the video must say so.
    """
    grouped: dict[str, list[MapillaryImage]] = {}
    for image in images:
        grouped.setdefault(image.point.drain_id, []).append(image)
    for group in grouped.values():
        group.sort(key=lambda i: i.captured_at_ms or 0)
    return grouped
