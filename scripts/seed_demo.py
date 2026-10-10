#!/usr/bin/env python3
"""Seed the dashboard with a demo history.

The product claim is *predictive* maintenance: a drain climbing 0.18 -> 0.79 over four
weeks is worth dispatching a crew to **before** the street floods. Demonstrating that
needs several readings of the same drain weeks apart, and there is no way to obtain real
weekly captures of a Delhi drain before the deadline.

So the readings here are synthetic, and the script is built so they can never be
mistaken for measurements:

  * every record carries ``simulated: true``, which the API returns and the UI badges;
  * every ``reading_id`` begins with ``seed-``;
  * the masks are obviously synthetic to look at, not doctored photographs.

Crucially the *scores* are not hard-coded. Each synthetic mask is passed through the
real :func:`compute_choke_index`, so what the dashboard shows is the production scoring
path operating on a known input — which also makes this a useful end-to-end check of
scoring, overlay rendering and storage without torch installed.

Usage::

    python scripts/seed_demo.py                 # local store
    python scripts/seed_demo.py --clear         # wipe the local store first
    DRAINWATCH_BUCKET=... DRAINWATCH_TABLE=... python scripts/seed_demo.py   # AWS
"""

from __future__ import annotations

import argparse
import io
import shutil
import sys
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from drainwatch.api.storage import LocalStorage, get_storage  # noqa: E402
from drainwatch.choke.index import compute_choke_index  # noqa: E402
from drainwatch.choke.overlay import render_overlay  # noqa: E402
from drainwatch.config import Class  # noqa: E402
from drainwatch.geo.sources import GeoPoint  # noqa: E402

FRAME = (512, 384)  # width, height


@dataclass(frozen=True)
class Scenario:
    """One drain and the story its history tells."""

    slug: str
    name: str
    lat: float
    lon: float
    series: tuple[float, ...]  # target choke index per reading, oldest first
    note: str
    confidence: float = 0.88


# Coordinates are real locations around DTU / Bawana Road; the readings are not.
SCENARIOS = (
    Scenario(
        slug="bawana-culvert",
        name="Bawana Road culvert",
        lat=28.7604,
        lon=77.1035,
        series=(0.18, 0.37, 0.58, 0.79),
        note="Silting steadily since the first pre-monsoon shower — the headline case.",
    ),
    Scenario(
        slug="shahbad-dairy",
        name="Shahbad Dairy nala",
        lat=28.7312,
        lon=77.1246,
        series=(0.42, 0.49, 0.55),
        note="In the watch band and climbing; will cross 0.70 within a month.",
    ),
    Scenario(
        slug="rohini-17",
        name="Rohini Sector 17 drain",
        lat=28.7389,
        lon=77.1012,
        series=(0.80, 0.83, 0.81),
        note="Chronically blocked. High and flat — a maintenance failure, not an event.",
    ),
    Scenario(
        slug="narela-industrial",
        name="Narela industrial drain",
        lat=28.7718,
        lon=77.0941,
        series=(0.74, 0.69, 0.11),
        note="Cleared between the second and third readings — a falling trend.",
    ),
    Scenario(
        slug="dtu-main-gate",
        name="DTU main gate",
        lat=28.7496,
        lon=77.1181,
        series=(0.07, 0.10, 0.08),
        note="Healthy drain. Present so the map is not uniformly alarming.",
    ),
    Scenario(
        slug="dtu-hostel-road",
        name="DTU hostel road",
        lat=28.7523,
        lon=77.1143,
        series=(0.33,),
        note="One reading only — exercises the no-trend-yet state.",
    ),
    Scenario(
        slug="bawana-industrial",
        name="Bawana industrial area",
        lat=28.7955,
        lon=77.0469,
        series=(0.61,),
        note="Hazy dusk capture: scored, but low confidence routes it to human review.",
        confidence=0.46,
    ),
)


def synthesise_mask(target_ci: float, *, seed: int, frame: tuple[int, int] = FRAME) -> np.ndarray:
    """Build a 3-class mask whose choke index is approximately ``target_ci``.

    The channel is a trapezoid widening toward the camera, and waste accumulates from the
    downstream end — which is where it collects in a real drain, against the grate.
    """
    width, height = frame
    rng = np.random.default_rng(seed)
    mask = np.zeros((height, width), dtype=np.uint8)  # STRUCTURE

    centre = height * 0.66
    for x in range(width):
        t = x / (width - 1)
        thickness = (0.07 + 0.11 * t) * height  # perspective
        cy = centre + 0.035 * height * np.sin(t * 2.4)  # a gentle bend
        lo = max(int(round(cy - thickness / 2)), 0)
        hi = min(int(round(cy + thickness / 2)), height)
        mask[lo:hi, x] = Class.WATER

    channel = mask == Class.WATER
    want = int(round(target_ci * int(channel.sum())))

    taken = 0
    for x in range(width - 1, -1, -1):  # downstream (right) first
        if taken >= want:
            break
        rows = np.flatnonzero(channel[:, x])
        if rows.size == 0:
            continue
        need = want - taken
        rows = rows if rows.size <= need else rows[:need]
        mask[rows, x] = Class.WASTE
        taken += rows.size

    # Ragged the boundary a little: a real segmentation never produces a clean edge, and
    # a razor-straight one looks synthesised in the video. Symmetric, so the index barely
    # moves — and whatever it does move to is what gets reported.
    flips = max(int(0.02 * channel.sum()), 1)
    idx = np.flatnonzero(channel.ravel())
    flat = mask.ravel()
    water_idx = idx[flat[idx] == Class.WATER]
    waste_idx = idx[flat[idx] == Class.WASTE]
    if water_idx.size:
        flat[rng.choice(water_idx, size=min(flips, water_idx.size), replace=False)] = Class.WASTE
    if waste_idx.size:
        flat[rng.choice(waste_idx, size=min(flips, waste_idx.size), replace=False)] = Class.WATER

    return mask


def synthesise_image(mask: np.ndarray, *, seed: int) -> Image.Image:
    """A plain synthetic scene for the mask to sit on. Deliberately not photoreal."""
    rng = np.random.default_rng(seed + 991)
    height, width = mask.shape

    canvas = rng.normal(138, 8, size=(height, width, 3))
    canvas[: int(height * 0.42)] += 34  # brighter above the horizon
    canvas[mask != Class.STRUCTURE] -= 44  # the channel sits in shadow
    return Image.fromarray(canvas.clip(0, 255).astype(np.uint8), mode="RGB")


def _jpeg(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def seed(storage, *, days_apart: int = 7, only: set[str] | None = None) -> list[dict]:
    """Write one reading per entry in each scenario's series. Returns the records."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    records: list[dict] = []

    for scenario in SCENARIOS:
        if only and scenario.slug not in only:
            continue
        point = GeoPoint(scenario.lat, scenario.lon)
        count = len(scenario.series)

        for i, target in enumerate(scenario.series):
            # crc32, not hash(): Python salts string hashing per process, so hash() would
            # give a different mask on every run and reseeding would not be reproducible.
            seed_value = zlib.crc32(f"{scenario.slug}:{i}".encode())
            mask = synthesise_mask(target, seed=seed_value)
            image = synthesise_image(mask, seed=seed_value)

            rng = np.random.default_rng(seed_value + 7)
            confidence = rng.normal(scenario.confidence, 0.03, mask.shape).clip(0.15, 0.999)

            reading = compute_choke_index(mask, confidence)

            # Latest reading lands two days ago, earlier ones step back weekly.
            when = now - timedelta(days=2 + days_apart * (count - 1 - i))
            reading_id = f"seed-{scenario.slug}-{i}"
            prefix = f"readings/{reading_id}"

            record = {
                "reading_id": reading_id,
                "drain_id": point.drain_id,
                "captured_at": when.isoformat(timespec="seconds"),
                "lat": point.lat,
                "lon": point.lon,
                "image_url": storage.put_image(f"{prefix}/source.jpg", _jpeg(image)),
                "overlay_url": storage.put_image(
                    f"{prefix}/overlay.png", _png(render_overlay(image, mask)), "image/png"
                ),
                "simulated": True,
                "source": "seed_demo",
                "site_name": scenario.name,
                "note": scenario.note,
                **reading.to_dict(),
            }
            storage.put_reading(record)
            records.append(record)

            print(
                f"  {scenario.name:<28} {when:%Y-%m-%d}  "
                f"target {target:.2f}  actual "
                f"{reading.choke_index if reading.choke_index is not None else float('nan'):.3f}  "
                f"{reading.severity.value}"
            )
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--clear",
        action="store_true",
        help="delete the local store before seeding (local backend only)",
    )
    parser.add_argument("--days-apart", type=int, default=7, help="days between readings")
    parser.add_argument("--only", nargs="*", metavar="SLUG", help="seed only these scenarios")
    parser.add_argument(
        "--list", action="store_true", help="list the scenarios and exit without writing"
    )
    args = parser.parse_args()

    if args.list:
        for scenario in SCENARIOS:
            series = " -> ".join(f"{v:.2f}" for v in scenario.series)
            print(f"{scenario.slug:<20} {scenario.name:<28} {series}")
            print(f"{'':<20} {scenario.note}")
        return 0

    storage = get_storage()

    if args.clear:
        if not isinstance(storage, LocalStorage):
            print("--clear only applies to the local backend; refusing to touch AWS.")
            return 1
        if storage.root.exists():
            shutil.rmtree(storage.root)
            print(f"cleared {storage.root}")
        storage = LocalStorage()

    only = set(args.only) if args.only else None
    unknown = (only or set()) - {s.slug for s in SCENARIOS}
    if unknown:
        print(f"unknown scenario(s): {', '.join(sorted(unknown))}. Try --list.")
        return 2

    print(f"seeding {storage.name} storage")
    records = seed(storage, days_apart=args.days_apart, only=only)

    drains = len({r["drain_id"] for r in records})
    print(f"\n{len(records)} simulated readings across {drains} drains.")
    print("Every record is flagged simulated=true and the dashboard badges it as such.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
