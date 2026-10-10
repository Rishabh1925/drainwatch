"""Merge the public sources into one 3-class dataset.

Output layout under ``data/processed``::

    images/<sample_id>.jpg     # RGB, long side capped at MAX_SIDE
    masks/<sample_id>.png      # uint8 labels: 0 structure, 1 water, 2 waste
    manifest.json              # per-sample provenance + class pixel counts
    splits.json                # {"train": [...], "val": [...]}

Two choices worth flagging:

*Provenance is recorded per sample.* When waste IoU comes out poor, the first question
is always "poor on which source?" — the manifest answers it without a re-run.

*Splits are stratified by source.* A random split would happily put most ADE20K water
images in train and most TACO litter in val, making the val metrics measure domain
transfer rather than model quality.

Class pixel counts are tallied because ``waste`` occupies a small minority of pixels;
the training loss needs those counts to weight classes, or it will learn to predict
``structure`` everywhere and score a deceptively high accuracy.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from drainwatch.config import NUM_CLASSES, PROCESSED_DIR, RAW_DIR, Class
from drainwatch.data.ade_water import iter_water_images
from drainwatch.data.coco_masks import load_coco, mask_for_image

MAX_SIDE = 640  # above the 512 training size, far below native; saves disk and IO
JPEG_QUALITY = 90
DEFAULT_VAL_FRAC = 0.15
DEFAULT_SEED = 42
# Enough to be a real training signal rather than a curiosity, and cheap: compositing is
# a few hundred milliseconds per sample, so this adds minutes to a build, not hours.
DEFAULT_COMPOSITES = 700


@dataclass
class BuildReport:
    """What the build actually produced, per source."""

    per_source: Counter[str] = field(default_factory=Counter)
    class_pixels: Counter[int] = field(default_factory=Counter)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.per_source.values())

    def class_balance(self) -> dict[str, float]:
        """Fraction of all labelled pixels per class — drives the loss weights."""
        total = sum(self.class_pixels.values()) or 1
        return {
            Class(cls).name.lower(): self.class_pixels[cls] / total for cls in range(NUM_CLASSES)
        }

    def summary(self) -> str:
        lines = [f"{self.total} samples"]
        for source, n in sorted(self.per_source.items()):
            lines.append(f"  {source:<12} {n:>5}")
        lines.append("class pixel balance:")
        for name, frac in self.class_balance().items():
            lines.append(f"  {name:<12} {frac:>7.2%}")
        if self.skipped:
            lines.append(f"{len(self.skipped)} skipped (first 5): {self.skipped[:5]}")
        return "\n".join(lines)


def _resize_pair(
    image: Image.Image, mask: np.ndarray, *, max_side: int = MAX_SIDE
) -> tuple[Image.Image, np.ndarray]:
    """Downscale an image/mask pair together, keeping labels intact.

    The mask uses NEAREST — any interpolation would invent label values that mean
    nothing (e.g. 1.5 between water and waste).
    """
    h, w = mask.shape
    if image.size != (w, h):
        image = image.resize((w, h), Image.BILINEAR)

    longest = max(h, w)
    if longest <= max_side:
        return image.convert("RGB"), mask

    scale = max_side / longest
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    resized_mask = np.asarray(
        Image.fromarray(mask).resize(new_size, Image.NEAREST), dtype=np.uint8
    )
    return image.convert("RGB").resize(new_size, Image.BILINEAR), resized_mask


def write_sample(
    sample_id: str,
    image: Image.Image,
    mask: np.ndarray,
    out_dir: Path,
    *,
    max_side: int = MAX_SIDE,
) -> dict[int, int]:
    """Write one image/mask pair. Returns its class pixel counts."""
    image, mask = _resize_pair(image, mask, max_side=max_side)

    (out_dir / "images").mkdir(parents=True, exist_ok=True)
    (out_dir / "masks").mkdir(parents=True, exist_ok=True)
    image.save(out_dir / "images" / f"{sample_id}.jpg", quality=JPEG_QUALITY, optimize=True)
    Image.fromarray(mask).save(out_dir / "masks" / f"{sample_id}.png", optimize=True)

    values, counts = np.unique(mask, return_counts=True)
    return {int(v): int(c) for v, c in zip(values, counts, strict=True)}


def add_coco_source(
    source_name: str,
    annotations_path: Path,
    images_root: Path,
    out_dir: Path,
    report: BuildReport,
    *,
    target_class: Class = Class.WASTE,
    background_class: Class = Class.STRUCTURE,
    keep_category: Callable[[str], bool] | None = None,
    limit: int | None = None,
    manifest: list[dict] | None = None,
) -> None:
    """Rasterize a COCO litter dataset (TACO, UAVVaste, or a drop-in extra) into the set.

    Images missing from disk are skipped and recorded — TACO's Flickr links rot, and a
    handful of 404s should not abort the build.

    ``background_class`` is ``structure`` for dry-ground litter and ``water`` for a
    waste-in-water dataset (see :func:`mask_for_image`). ``keep_category`` filters COCO
    categories by name, so a mixed dataset (trash + fish + ROV) can contribute only its
    trash polygons.
    """
    index = load_coco(annotations_path, keep_category=keep_category)
    added = 0

    for image_id in index.image_ids:
        if limit is not None and added >= limit:
            break

        record = index.images[image_id]
        image_path = images_root / record["file_name"]
        if not image_path.exists():
            report.skipped.append((f"{source_name}:{record['file_name']}", "image not downloaded"))
            continue
        # An image with no usable polygons contributes only background.
        if not index.anns_by_image.get(image_id):
            continue

        mask = mask_for_image(
            index, image_id, target_class=target_class, background_class=background_class
        )
        sample_id = f"{source_name}_{image_id:06d}"

        try:
            with Image.open(image_path) as image:
                counts = write_sample(sample_id, image, mask, out_dir)
        except OSError as exc:  # truncated/corrupt download
            report.skipped.append((f"{source_name}:{record['file_name']}", f"unreadable: {exc}"))
            continue

        report.per_source[source_name] += 1
        report.class_pixels.update(counts)
        if manifest is not None:
            manifest.append(
                {
                    "sample_id": sample_id,
                    "source": source_name,
                    "origin": str(record["file_name"]),
                    "class_pixels": counts,
                }
            )
        added += 1


def add_ade_water_source(
    ade_root: Path,
    out_dir: Path,
    report: BuildReport,
    *,
    limit: int | None = None,
    manifest: list[dict] | None = None,
) -> None:
    """Add ADE20K water images as the ``water`` class."""
    for i, sample in enumerate(iter_water_images(ade_root, limit=limit)):
        sample_id = f"ade_{i:06d}"
        try:
            with Image.open(sample.image_path) as image:
                counts = write_sample(sample_id, image, sample.mask, out_dir)
        except OSError as exc:
            report.skipped.append((f"ade:{sample.image_path.name}", f"unreadable: {exc}"))
            continue

        report.per_source["ade"] += 1
        report.class_pixels.update(counts)
        if manifest is not None:
            manifest.append(
                {
                    "sample_id": sample_id,
                    "source": "ade",
                    "origin": sample.image_path.name,
                    "water_frac": round(sample.water_frac, 4),
                    "class_pixels": counts,
                }
            )


def add_composite_source(
    ade_root: Path,
    donors: list,
    out_dir: Path,
    report: BuildReport,
    *,
    count: int,
    seed: int = DEFAULT_SEED,
    manifest: list[dict] | None = None,
) -> None:
    """Generate images that contain **both** water and waste, which no source provides.

    See :mod:`drainwatch.data.composite` for why this is load-bearing rather than
    decorative: the Choke Index is a function of the water-waste boundary, and without
    these samples that boundary appears in exactly zero training images.

    Targets are **stratified across the three severity bands** rather than drawn from one
    uniform range. A uniform target compresses toward the middle once pastes clip and
    overlap — observed empirically as a realised spread of 0.15–0.71 from a 0.05–0.88
    request, which left CRITICAL (the dispatch band the product turns on) badly
    under-sampled. Cycling the bands guarantees each is pushed for an equal share, and
    because the high band aims past 0.70 the inevitable undershoot still lands in or near
    CRITICAL instead of short of WATCH. The *realised* index is measured from the output
    and recorded in the manifest — the target is a request, not a promise.
    """
    from drainwatch.data.composite import band_target, composite_waste_on_water

    if not donors:
        report.skipped.append(("composite", "no waste donors available"))
        return

    rng = random.Random(seed)
    made = 0

    # ADE water scenes are the substrate: their water labels are real, human-drawn.
    for sample in iter_water_images(ade_root, limit=None):
        if made >= count:
            break

        # Round-robin the bands so coverage does not depend on how the paste logic
        # happens to land. High band aims past the 0.70 cutoff to absorb undershoot.
        target = band_target(made, rng)
        try:
            with Image.open(sample.image_path) as handle:
                image, mask, realised = composite_waste_on_water(
                    handle.convert("RGB"),
                    sample.mask,
                    donors,
                    rng=rng,
                    target_choke=target,
                )
        except OSError as exc:
            report.skipped.append((f"composite:{sample.image_path.name}", f"unreadable: {exc}"))
            continue

        # A composite that ended up with no waste teaches nothing this source is for.
        if realised <= 0.0:
            continue

        sample_id = f"composite_{made:06d}"
        counts = write_sample(sample_id, image, mask, out_dir)
        report.per_source["composite"] += 1
        report.class_pixels.update(counts)
        if manifest is not None:
            manifest.append(
                {
                    "sample_id": sample_id,
                    "source": "composite",
                    "origin": sample.image_path.name,
                    "target_choke": round(target, 4),
                    "realised_choke": round(realised, 4),
                    "class_pixels": counts,
                }
            )
        made += 1

    if made < count:
        report.skipped.append(
            ("composite", f"wanted {count}, made {made} — ran out of ADE water scenes")
        )


def make_splits(
    manifest: list[dict],
    *,
    val_frac: float = DEFAULT_VAL_FRAC,
    seed: int = DEFAULT_SEED,
) -> dict[str, list[str]]:
    """Split train/val, stratified by source.

    Stratifying matters: an unstratified split can land nearly all water imagery in
    train and nearly all litter imagery in val, so the val score would measure domain
    transfer instead of model quality.
    """
    if not 0.0 < val_frac < 1.0:
        raise ValueError(f"val_frac must be in (0, 1), got {val_frac}")

    by_source: dict[str, list[str]] = {}
    for entry in manifest:
        by_source.setdefault(entry["source"], []).append(entry["sample_id"])

    rng = random.Random(seed)
    train: list[str] = []
    val: list[str] = []
    for source in sorted(by_source):
        ids = sorted(by_source[source])
        rng.shuffle(ids)
        n_val = max(1, round(len(ids) * val_frac)) if len(ids) > 1 else 0
        val.extend(ids[:n_val])
        train.extend(ids[n_val:])

    return {"train": sorted(train), "val": sorted(val)}


def build_unified_dataset(
    out_dir: Path = PROCESSED_DIR,
    *,
    raw_dir: Path = RAW_DIR,
    val_frac: float = DEFAULT_VAL_FRAC,
    seed: int = DEFAULT_SEED,
    limit_per_source: int | None = None,
    composites: int = DEFAULT_COMPOSITES,
) -> BuildReport:
    """Build the full dataset from whatever sources are present under ``raw_dir``.

    Missing sources are skipped with a note rather than raising, so the pipeline can be
    exercised with only one dataset downloaded.

    Args:
        composites: how many waste-in-water composites to synthesise. Needs both TACO
            and ADE20K present. Set to 0 to disable — but read
            :mod:`drainwatch.data.composite` first, because without them no training
            image contains both of the classes the Choke Index is computed from.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    report = BuildReport()
    manifest: list[dict] = []

    coco_sources = [
        ("taco", raw_dir / "taco" / "annotations.json", raw_dir / "taco" / "images"),
        ("uavvaste", raw_dir / "uavvaste" / "annotations.json", raw_dir / "uavvaste" / "images"),
    ]
    for name, annotations, images_root in coco_sources:
        if not annotations.exists():
            report.skipped.append((name, f"annotations missing at {annotations}"))
            continue
        add_coco_source(
            name,
            annotations,
            images_root,
            out_dir,
            report,
            limit=limit_per_source,
            manifest=manifest,
        )

    ade_root = raw_dir / "ADEChallengeData2016"
    if ade_root.is_dir():
        add_ade_water_source(
            ade_root, out_dir, report, limit=limit_per_source, manifest=manifest
        )
    else:
        report.skipped.append(("ade", f"not extracted at {ade_root}"))

    # Drop-in domain-matched datasets (real waste-in-water / drains) under raw/extra/*.
    # These carry the water/waste boundary with real masks, so they matter more than any
    # single source above — they are the direct fix for the synthetic-domain gap.
    add_extra_coco_datasets(raw_dir, out_dir, report, limit=limit_per_source, manifest=manifest)

    if composites > 0:
        if not ade_root.is_dir():
            report.skipped.append(("composite", "needs ADE20K water scenes as the substrate"))
        else:
            donors = _collect_donors(raw_dir, report)
            add_composite_source(
                ade_root,
                donors,
                out_dir,
                report,
                count=composites,
                seed=seed,
                manifest=manifest,
            )

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (out_dir / "splits.json").write_text(
        json.dumps(make_splits(manifest, val_frac=val_frac, seed=seed), indent=2)
    )
    return report


def add_extra_coco_datasets(
    raw_dir: Path,
    out_dir: Path,
    report: BuildReport,
    *,
    limit: int | None = None,
    manifest: list[dict] | None = None,
) -> None:
    """Ingest any drop-in COCO-segmentation datasets under ``raw_dir/extra/<name>/``.

    This is the domain-gap fix without hand-labelling: drop a real waste-in-water or
    drain dataset here (Roboflow Universe exports "COCO Segmentation" in exactly this
    layout — ``<name>/train/_annotations.coco.json`` plus images — and academic sets can
    be rearranged into it) and the build folds it in alongside TACO/UAVVaste/ADE.

    The default mapping is **waste on a water background**, because that is what these
    datasets almost always are (litter in a canal, trash in a drain) and it is the
    co-occurrence the synthetic composites were only *approximating*. Real masks of that
    boundary are worth far more than any number of composites. Override per folder with a
    ``dataset.json`` holding ``target_class`` / ``background_class`` /
    ``category_keywords`` when a dataset is, say, water-only.
    """
    extra_root = raw_dir / "extra"
    if not extra_root.is_dir():
        return

    name_to_class = {c.name.lower(): c for c in Class}
    for dataset_dir in sorted(p for p in extra_root.iterdir() if p.is_dir()):
        target_class = Class.WASTE
        background_class = Class.WATER
        keep_category: Callable[[str], bool] | None = None

        config_path = dataset_dir / "dataset.json"
        if config_path.exists():
            try:
                cfg = json.loads(config_path.read_text())
                target_class = name_to_class.get(
                    str(cfg.get("target_class", "")).lower(), target_class
                )
                background_class = name_to_class.get(
                    str(cfg.get("background_class", "")).lower(), background_class
                )
                keywords = [k.lower() for k in cfg.get("category_keywords", [])]
                if keywords:
                    keep_category = lambda name, kw=keywords: any(k in name.lower() for k in kw)
            except (ValueError, OSError) as exc:
                report.skipped.append((f"extra:{dataset_dir.name}", f"bad dataset.json: {exc}"))

        # A COCO export may be split into train/valid/test, each with its own json; we
        # re-split ourselves, so ingest every split as its own source stratum.
        annotation_files = sorted(dataset_dir.rglob("*_annotations.coco.json")) or sorted(
            dataset_dir.rglob("annotations*.json")
        )
        if not annotation_files:
            report.skipped.append((f"extra:{dataset_dir.name}", "no COCO json found"))
            continue

        for ann_path in annotation_files:
            source_name = f"x_{dataset_dir.name}_{ann_path.parent.name}"
            add_coco_source(
                source_name,
                ann_path,
                ann_path.parent,  # Roboflow stores images beside the json
                out_dir,
                report,
                target_class=target_class,
                background_class=background_class,
                keep_category=keep_category,
                limit=limit,
                manifest=manifest,
            )


def _collect_donors(raw_dir: Path, report: BuildReport) -> list:
    """Gather litter cut-outs from every available COCO litter source.

    Both sources are used when present: TACO is consumer litter at eye level, UAVVaste is
    drone-altitude, and a donor pool from one viewpoint only would bias every composite
    toward that scale.
    """
    from drainwatch.data.composite import iter_waste_donors

    donors: list = []
    for name in ("taco", "uavvaste"):
        annotations = raw_dir / name / "annotations.json"
        if not annotations.exists():
            continue
        index = load_coco(annotations)
        found = list(
            iter_waste_donors(index, raw_dir / name / "images", origin_prefix=name)
        )
        print(f"[composite] {len(found)} donors from {name}")
        donors.extend(found)

    if not donors:
        report.skipped.append(("composite", "no litter instances could be cut out"))
    return donors
