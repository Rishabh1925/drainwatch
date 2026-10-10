"""Rasterize COCO polygon annotations into DrainWatch class masks.

Both TACO and UAVVaste ship COCO-format annotations, so one rasterizer serves both —
they differ only in which categories map to which DrainWatch class.

Deliberately uses PIL's polygon fill rather than ``pycocotools``: pycocotools needs a C
extension build that is a recurring source of pain on new Python versions, and litter
datasets annotate with polygons anyway. RLE / ``iscrowd`` annotations are skipped and
counted, so the loss is visible rather than silent.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from drainwatch.config import Class

MIN_POLYGON_COORDS = 6  # 3 points × (x, y)


@dataclass
class CocoIndex:
    """A COCO annotations file, indexed for per-image mask construction."""

    images: dict[int, dict] = field(default_factory=dict)
    anns_by_image: dict[int, list[dict]] = field(default_factory=dict)
    categories: dict[int, str] = field(default_factory=dict)
    skipped_rle: int = 0
    skipped_degenerate: int = 0

    @property
    def image_ids(self) -> list[int]:
        return sorted(self.images)

    def summary(self) -> str:
        n_anns = sum(len(v) for v in self.anns_by_image.values())
        parts = [
            f"{len(self.images)} images",
            f"{n_anns} usable annotations",
            f"{len(self.categories)} categories",
        ]
        if self.skipped_rle:
            parts.append(f"{self.skipped_rle} RLE/crowd skipped")
        if self.skipped_degenerate:
            parts.append(f"{self.skipped_degenerate} degenerate polygons skipped")
        return ", ".join(parts)


def load_coco(
    annotations_path: Path,
    *,
    keep_category: Callable[[str], bool] | None = None,
) -> CocoIndex:
    """Load and index a COCO annotations file.

    Args:
        annotations_path: path to ``annotations.json``.
        keep_category: optional predicate on the category *name*. When given, only
            matching annotations are indexed — e.g. to drop non-litter categories.

    Returns:
        A :class:`CocoIndex`. Annotations whose segmentation is RLE-encoded, or whose
        polygons have fewer than three points, are dropped and tallied.
    """
    with annotations_path.open() as fh:
        raw = json.load(fh)

    index = CocoIndex(
        images={img["id"]: img for img in raw["images"]},
        categories={c["id"]: c["name"] for c in raw.get("categories", [])},
    )

    for ann in raw.get("annotations", []):
        if keep_category is not None:
            name = index.categories.get(ann["category_id"], "")
            if not keep_category(name):
                continue

        seg = ann.get("segmentation")
        # RLE comes through as a dict (or iscrowd=1); we cannot fill those with PIL.
        if isinstance(seg, dict) or ann.get("iscrowd", 0) == 1:
            index.skipped_rle += 1
            continue
        if not seg:
            index.skipped_degenerate += 1
            continue

        polygons = [p for p in seg if len(p) >= MIN_POLYGON_COORDS]
        if not polygons:
            index.skipped_degenerate += 1
            continue

        index.anns_by_image.setdefault(ann["image_id"], []).append(
            {"category_id": ann["category_id"], "polygons": polygons}
        )

    return index


def mask_for_image(
    index: CocoIndex,
    image_id: int,
    *,
    target_class: Class = Class.WASTE,
    background_class: Class = Class.STRUCTURE,
) -> np.ndarray:
    """Build a (H, W) label mask for one image.

    Every annotated polygon is filled with ``target_class``; everything else is
    ``background_class``. Collapsing all litter categories into a single ``waste`` class
    is intentional — the Choke Index only cares about area, not whether the blockage is a
    bottle or a bag.

    ``background_class`` matters for domain-matched sources. A litter dataset shot on dry
    ground wants the background as ``structure`` (the default). A *waste-in-water* dataset
    (trash floating in a canal, debris in a drain) wants the background as ``water`` — its
    unlabelled pixels really are water, and labelling them ``structure`` would teach the
    model that water is concrete, poisoning the very water/waste boundary the Choke Index
    is computed from.
    """
    record = index.images[image_id]
    h, w = int(record["height"]), int(record["width"])

    canvas = Image.new("L", (w, h), int(background_class))
    draw = ImageDraw.Draw(canvas)

    for ann in index.anns_by_image.get(image_id, []):
        for polygon in ann["polygons"]:
            draw.polygon(polygon, fill=int(target_class))

    return np.asarray(canvas, dtype=np.uint8)


def save_mask(mask: np.ndarray, path: Path) -> None:
    """Write a label mask as a palettized PNG (small, lossless, label-preserving)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # No ``mode=`` argument: Pillow 13 removes it, and a 2-D uint8 array already maps
    # to "L" by inference. Passing it would only be a deprecation warning now and a
    # hard failure later.
    Image.fromarray(mask).save(path, optimize=True)
