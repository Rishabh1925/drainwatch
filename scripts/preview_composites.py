#!/usr/bin/env python3
"""Eyeball the synthetic waste-in-water training data before training on it.

Composites are the *only* samples that teach the water-waste boundary, and the Choke
Index is computed entirely from that boundary. So a bug here does not raise — it
produces a trainable dataset that teaches the wrong thing, and no metric will say so.
This script is the five minutes that catches it.

It answers three questions:

1. **Does donor extraction work on the real data?** Polygon rescaling in particular:
   TACO annotations declare the original dimensions (up to 1537x2049) while the
   downloaded file is the 640px Flickr variant. Rasterizing at the declared size would
   put every cut-out in the wrong place and nothing downstream would complain.
2. **Will the donor pool fit in memory?** The whole pool is resident at once during
   ``data build``; this projects the full-pool size from a sample.
3. **Do the composites look like drains?** Renders side-by-side panels to open, and
   reports the realised Choke Index distribution — which must span the 0.40 and 0.70
   decision bands, not cluster at one end.

    python3 scripts/preview_composites.py                 # donors only (no ADE needed)
    python3 scripts/preview_composites.py -n 12           # render 12 composites
"""

from __future__ import annotations

import argparse
import random
import re
import resource
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from drainwatch.choke.index import compute_choke_index  # noqa: E402
from drainwatch.choke.overlay import render_overlay  # noqa: E402
from drainwatch.config import RAW_DIR  # noqa: E402
from drainwatch.data.ade_water import iter_water_images  # noqa: E402
from drainwatch.data.coco_masks import load_coco  # noqa: E402
from drainwatch.data.composite import band_target, composite_waste_on_water, iter_waste_donors  # noqa: E402

EXPECTED_POOL = 6000  # rough full-pool size, for the memory projection
# Our own panel filenames, e.g. "08_target0.82_got0.71_critical.jpg". Only files matching
# this are cleared between runs, so a stray image the user dropped in the folder survives.
PANEL_PATTERN = re.compile(r"^\d+_target\d\.\d{2}_got\d\.\d{2}_\w+\.jpg$")


def collect(raw_dir: Path, sample_limit: int | None) -> list:
    """Extract donors from every available litter source, with timing and memory."""
    donors: list = []
    for name in ("taco", "uavvaste"):
        annotations = raw_dir / name / "annotations.json"
        if not annotations.exists():
            print(f"[{name}] no annotations at {annotations} — skipping")
            continue

        index = load_coco(annotations)
        print(f"[{name}] {index.summary()}")

        started = time.time()
        found = list(
            iter_waste_donors(
                index, raw_dir / name / "images", limit=sample_limit, origin_prefix=name
            )
        )
        elapsed = time.time() - started
        if not found:
            print(f"[{name}] NO DONORS — images not downloaded, or every instance too small")
            continue

        per = elapsed / len(found) * 1000
        print(f"[{name}] {len(found)} donors in {elapsed:.1f}s ({per:.0f} ms each)")
        donors.extend(found)

    return donors


def report_pool(donors: list) -> None:
    held = sum(d.patch.nbytes + d.alpha.nbytes for d in donors)
    areas = sorted(d.area for d in donors)
    sides = [max(d.alpha.shape) for d in donors]

    print()
    print(f"pool: {len(donors)} donors, {held / 1e6:.1f} MB resident")
    print(
        f"      projected at {EXPECTED_POOL} donors: "
        f"{held / len(donors) * EXPECTED_POOL / 1e9:.2f} GB"
    )
    print(f"      longest side: max {max(sides)}, median {sides[len(sides) // 2]}")
    print(
        f"      opaque area:  p10 {areas[len(areas) // 10]}, "
        f"median {areas[len(areas) // 2]}, p90 {areas[9 * len(areas) // 10]}"
    )
    print(f"peak RSS: {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9:.2f} GB")

    # A pool whose instances are all tiny cannot reach a high Choke Index, so the
    # generated set would never cover CRITICAL and the model would never see one.
    if areas[len(areas) // 2] < 1024:
        print(
            "\nWARNING: median donor is under 1024 px. High choke targets will need many "
            "pastes and may not be reachable — check the realised distribution below."
        )


def render(donors: list, raw_dir: Path, out_dir: Path, count: int, seed: int) -> None:
    ade_root = raw_dir / "ADEChallengeData2016"
    if not ade_root.is_dir():
        print(f"\nADE20K not extracted at {ade_root} — cannot render composites yet.")
        print("Re-run this after `drainwatch data download` finishes.")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    # Clear previous panels. Without this the directory accumulates runs, and opening a
    # file named target0.82_got0.71 from an older run while the current run reports every
    # target hit exactly is genuinely misleading — you cannot tell which panels reflect
    # the code you just changed. Only our own output naming is removed.
    stale = [p for p in out_dir.glob("*.jpg") if PANEL_PATTERN.match(p.name)]
    for path in stale:
        path.unlink()
    if stale:
        print(f"cleared {len(stale)} panel(s) from a previous run")

    rng = random.Random(seed)
    pairs: list[tuple[float, float]] = []  # (target, realised)
    made = 0

    for sample in iter_water_images(ade_root, limit=None):
        if made >= count:
            break

        # Same band-stratified target the real build uses, so this previews what training
        # will actually see — not a different uniform distribution.
        target = band_target(made, rng)
        with Image.open(sample.image_path) as handle:
            image, mask, realised = composite_waste_on_water(
                handle.convert("RGB"), sample.mask, donors, rng=rng, target_choke=target
            )
        if realised <= 0.0:
            continue

        reading = compute_choke_index(mask, np.full(mask.shape, 0.9, dtype=np.float32))
        overlay = render_overlay(image, mask)

        # Side by side: the composite as the model sees it, and the labels it is given.
        panel = Image.new("RGB", (image.width * 2, image.height))
        panel.paste(image, (0, 0))
        panel.paste(overlay, (image.width, 0))
        name = f"{made:02d}_target{target:.2f}_got{realised:.2f}_{reading.severity.value}.jpg"
        panel.save(out_dir / name, quality=88)

        pairs.append((target, realised))
        made += 1

    if not pairs:
        print("\nNo composites produced — no ADE scene had usable water.")
        return

    values = np.array([r for _, r in pairs])
    print(f"\n{made} composites written to {out_dir}")

    # target -> realised, so systematic undershoot (not just the aggregate spread) is
    # visible. A high target landing far short means the paste logic cannot cover that
    # much of this scene's water, which is the thing worth seeing before a training run.
    print("target -> realised (largest gap first):")
    for target, realised in sorted(pairs, key=lambda p: p[1] - p[0])[:8]:
        bar = "+" if realised >= target else "-"
        print(f"  {target:.2f} -> {realised:.2f}  {bar}{abs(realised - target):.2f}")
    print("realised Choke Index:")
    print(f"  min {values.min():.2f}  median {np.median(values):.2f}  max {values.max():.2f}")
    bands = {
        "ok      (<0.40)": int((values < 0.40).sum()),
        "watch   (0.40-0.70)": int(((values >= 0.40) & (values < 0.70)).sum()),
        "critical (>=0.70)": int((values >= 0.70).sum()),
    }
    for label, n in bands.items():
        print(f"  {label:<22} {n:>3}  {'#' * n}")
    if min(bands.values()) == 0:
        print(
            "\nWARNING: at least one severity band has no samples. The model will not "
            "see the configuration it has to classify there."
        )
    print("\nOpen the panels and check: does the litter sit *in* the water, is the tint on")
    print("the right pixels, and is there a visible seam or dark halo around any object?")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", type=int, default=0, help="composites to render (needs ADE20K)")
    parser.add_argument("--raw-dir", default=str(RAW_DIR))
    parser.add_argument("--out", default=None, help="default: data/previews/composites")
    parser.add_argument(
        "--donor-limit",
        type=int,
        default=200,
        help="cap donors per source for a fast check; 0 means all",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    out_dir = Path(args.out) if args.out else raw_dir.parent / "previews" / "composites"

    donors = collect(raw_dir, args.donor_limit or None)
    if not donors:
        print("\nNo donors at all — composites are impossible. Check `data download`.")
        return 1

    report_pool(donors)
    if args.n:
        render(donors, raw_dir, out_dir, args.n, args.seed)
    else:
        print("\nPass -n 12 to render composites once ADE20K has downloaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
