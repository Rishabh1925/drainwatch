"""Tests for the COCO polygon rasterizer.

Builds synthetic COCO files on disk rather than depending on TACO/UAVVaste being
downloaded, so the suite runs on a clean checkout.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from drainwatch.config import Class
from drainwatch.data.coco_masks import load_coco, mask_for_image, save_mask


def write_coco(tmp_path: Path, annotations: list[dict], *, categories=None) -> Path:
    """Write a minimal one-image COCO file and return its path."""
    doc = {
        "images": [{"id": 1, "file_name": "batch_1/000001.jpg", "width": 64, "height": 48}],
        "annotations": annotations,
        "categories": categories
        or [{"id": 7, "name": "Plastic bag"}, {"id": 9, "name": "Cigarette"}],
    }
    path = tmp_path / "annotations.json"
    path.write_text(json.dumps(doc))
    return path


def square(x0: int, y0: int, x1: int, y1: int) -> list[float]:
    """A flat COCO polygon for an axis-aligned rectangle."""
    return [x0, y0, x1, y0, x1, y1, x0, y1]


# --- loading --------------------------------------------------------------------


def test_load_coco_indexes_images_and_annotations(tmp_path):
    path = write_coco(
        tmp_path,
        [{"id": 1, "image_id": 1, "category_id": 7, "segmentation": [square(10, 10, 30, 30)]}],
    )

    index = load_coco(path)

    assert index.image_ids == [1]
    assert index.images[1]["width"] == 64
    assert len(index.anns_by_image[1]) == 1
    assert index.categories[7] == "Plastic bag"


def test_rle_annotations_are_skipped_and_counted(tmp_path):
    """RLE cannot be polygon-filled; the loss must be visible, not silent."""
    path = write_coco(
        tmp_path,
        [
            {"id": 1, "image_id": 1, "category_id": 7, "segmentation": {"counts": "abc", "size": [48, 64]}},
            {"id": 2, "image_id": 1, "category_id": 7, "segmentation": [square(5, 5, 15, 15)], "iscrowd": 1},
            {"id": 3, "image_id": 1, "category_id": 7, "segmentation": [square(20, 20, 40, 40)]},
        ],
    )

    index = load_coco(path)

    assert index.skipped_rle == 2
    assert len(index.anns_by_image[1]) == 1
    assert "2 RLE/crowd skipped" in index.summary()


def test_degenerate_polygons_are_skipped(tmp_path):
    path = write_coco(
        tmp_path,
        [
            {"id": 1, "image_id": 1, "category_id": 7, "segmentation": [[1, 1, 2, 2]]},  # 2 points
            {"id": 2, "image_id": 1, "category_id": 7, "segmentation": []},
        ],
    )

    index = load_coco(path)

    assert index.skipped_degenerate == 2
    assert index.anns_by_image == {}


def test_keep_category_filters_by_name(tmp_path):
    path = write_coco(
        tmp_path,
        [
            {"id": 1, "image_id": 1, "category_id": 7, "segmentation": [square(10, 10, 20, 20)]},
            {"id": 2, "image_id": 1, "category_id": 9, "segmentation": [square(30, 30, 40, 40)]},
        ],
    )

    index = load_coco(path, keep_category=lambda name: name == "Plastic bag")

    assert len(index.anns_by_image[1]) == 1
    assert index.anns_by_image[1][0]["category_id"] == 7


# --- rasterizing ----------------------------------------------------------------


def test_polygon_is_filled_with_target_class(tmp_path):
    path = write_coco(
        tmp_path,
        [{"id": 1, "image_id": 1, "category_id": 7, "segmentation": [square(10, 10, 30, 30)]}],
    )
    index = load_coco(path)

    mask = mask_for_image(index, 1)

    assert mask.shape == (48, 64)  # (height, width)
    assert mask[20, 20] == Class.WASTE  # interior
    assert mask[2, 2] == Class.STRUCTURE  # outside
    # ~21x21 filled; assert a sane range rather than PIL's exact edge convention.
    assert 350 < int((mask == Class.WASTE).sum()) < 500


def test_all_categories_collapse_into_one_waste_class(tmp_path):
    """The Choke Index cares about area, not whether it's a bottle or a bag."""
    path = write_coco(
        tmp_path,
        [
            {"id": 1, "image_id": 1, "category_id": 7, "segmentation": [square(5, 5, 15, 15)]},
            {"id": 2, "image_id": 1, "category_id": 9, "segmentation": [square(30, 30, 45, 40)]},
        ],
    )
    index = load_coco(path)

    mask = mask_for_image(index, 1)

    assert set(np.unique(mask)) == {int(Class.STRUCTURE), int(Class.WASTE)}


def test_image_with_no_annotations_is_all_structure(tmp_path):
    path = write_coco(tmp_path, [])
    index = load_coco(path)

    mask = mask_for_image(index, 1)

    assert (mask == Class.STRUCTURE).all()


def test_target_class_is_configurable(tmp_path):
    path = write_coco(
        tmp_path,
        [{"id": 1, "image_id": 1, "category_id": 7, "segmentation": [square(10, 10, 30, 30)]}],
    )
    index = load_coco(path)

    mask = mask_for_image(index, 1, target_class=Class.WATER)

    assert mask[20, 20] == Class.WATER


# --- persistence ----------------------------------------------------------------


def test_save_mask_round_trips_labels(tmp_path):
    mask = np.zeros((16, 16), dtype=np.uint8)
    mask[4:8, 4:8] = Class.WATER
    mask[10:14, 10:14] = Class.WASTE
    out = tmp_path / "nested" / "mask.png"

    save_mask(mask, out)

    assert out.exists()
    assert np.array_equal(np.asarray(Image.open(out)), mask)
