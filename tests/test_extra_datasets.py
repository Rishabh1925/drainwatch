"""Tests for drop-in domain-matched dataset ingestion (raw/extra/*).

The waste-in-water datasets the user drops here are the real fix for the synthetic-domain
gap, so their one load-bearing property gets a test: the *background* of an aquatic scene
must become WATER, not STRUCTURE. Labelling a canal's surface as concrete would teach the
model that water is structure and poison the exact boundary the Choke Index is read from.
"""

from __future__ import annotations

import json

import numpy as np
from PIL import Image

from drainwatch.config import Class
from drainwatch.data.coco_masks import CocoIndex, mask_for_image
from drainwatch.data.unify import BuildReport, add_extra_coco_datasets


def _write_coco_dataset(root, *, category="trash", polygon=(5, 5, 35, 5, 35, 35, 5, 35)):
    """A minimal Roboflow-style COCO export: <root>/train/_annotations.coco.json + image."""
    split = root / "train"
    split.mkdir(parents=True)
    Image.fromarray(np.full((40, 40, 3), 90, dtype=np.uint8)).save(split / "a.jpg")
    coco = {
        "images": [{"id": 1, "file_name": "a.jpg", "width": 40, "height": 40}],
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 1, "segmentation": [list(polygon)], "iscrowd": 0}
        ],
        "categories": [{"id": 1, "name": category}],
    }
    (split / "_annotations.coco.json").write_text(json.dumps(coco))


# --- background class ------------------------------------------------------------


def test_coco_background_can_be_water():
    """mask_for_image must honour a water background for aquatic datasets."""
    index = CocoIndex(
        images={1: {"id": 1, "file_name": "a.jpg", "height": 20, "width": 20}},
        anns_by_image={1: [{"category_id": 1, "polygons": [[2, 2, 10, 2, 10, 10, 2, 10]]}]},
    )

    mask = mask_for_image(index, 1, target_class=Class.WASTE, background_class=Class.WATER)

    present = set(np.unique(mask).tolist())
    assert int(Class.WATER) in present, "background was not water"
    assert int(Class.WASTE) in present, "the annotated polygon was not waste"
    assert int(Class.STRUCTURE) not in present, "an aquatic scene must have no structure background"


# --- drop-in ingestion -----------------------------------------------------------


def test_extra_dataset_is_ingested_as_waste_on_water(tmp_path):
    _write_coco_dataset(tmp_path / "extra" / "canal_litter")
    out = tmp_path / "processed"
    out.mkdir()
    report = BuildReport()
    manifest: list[dict] = []

    add_extra_coco_datasets(tmp_path, out, report, manifest=manifest)

    assert report.per_source["x_canal_litter_train"] == 1
    assert manifest and manifest[0]["source"] == "x_canal_litter_train"
    mask = np.asarray(Image.open(out / "masks" / "x_canal_litter_train_000001.png"))
    present = set(np.unique(mask).tolist())
    assert present == {int(Class.WATER), int(Class.WASTE)}, present


def test_dataset_json_override_changes_the_background(tmp_path):
    """A water-only or dry dataset can override the waste-on-water default."""
    ds = tmp_path / "extra" / "dry_litter"
    _write_coco_dataset(ds)
    (ds / "dataset.json").write_text(json.dumps({"background_class": "structure"}))
    out = tmp_path / "processed"
    out.mkdir()
    report = BuildReport()

    add_extra_coco_datasets(tmp_path, out, report, manifest=[])

    mask = np.asarray(Image.open(out / "masks" / "x_dry_litter_train_000001.png"))
    present = set(np.unique(mask).tolist())
    assert present == {int(Class.STRUCTURE), int(Class.WASTE)}, present


def test_category_keywords_filter_out_non_target_annotations(tmp_path):
    """A mixed dataset (trash + fish) can keep only the trash polygons."""
    ds = tmp_path / "extra" / "mixed"
    _write_coco_dataset(ds, category="fish")  # the only annotation is a fish
    (ds / "dataset.json").write_text(json.dumps({"category_keywords": ["trash", "plastic"]}))
    out = tmp_path / "processed"
    out.mkdir()
    report = BuildReport()

    add_extra_coco_datasets(tmp_path, out, report, manifest=[])

    # The fish annotation is filtered out, leaving an image of pure water background —
    # add_coco_source skips images with no kept polygons, so nothing is written.
    assert report.per_source["x_mixed_train"] == 0
    assert not (out / "masks").exists() or not list((out / "masks").glob("x_mixed*"))


def test_no_extra_dir_is_a_noop(tmp_path):
    out = tmp_path / "processed"
    out.mkdir()
    report = BuildReport()

    add_extra_coco_datasets(tmp_path, out, report, manifest=[])  # no raw/extra

    assert report.total == 0


def test_extra_dir_without_coco_json_is_reported_not_crashed(tmp_path):
    (tmp_path / "extra" / "empty").mkdir(parents=True)
    out = tmp_path / "processed"
    out.mkdir()
    report = BuildReport()

    add_extra_coco_datasets(tmp_path, out, report, manifest=[])

    assert any("no COCO json" in why for _, why in report.skipped)
