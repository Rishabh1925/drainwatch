"""Tests for the dataset merge.

Two things here are silent failure modes — they produce a dataset that trains fine and
reports plausible numbers while being wrong:

* a mask resized with interpolation invents label values that mean nothing;
* an unstratified split puts most water imagery in train and most litter in val, so the
  val score measures domain transfer rather than model quality.

Both are tested directly. No torch import: this module is numpy + PIL only.
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image

from drainwatch.config import Class
from drainwatch.data.unify import (
    BuildReport,
    _resize_pair,
    build_unified_dataset,
    make_splits,
    write_sample,
)


def _manifest(counts: dict[str, int]) -> list[dict]:
    """A manifest with ``counts[source]`` samples per source."""
    return [
        {"sample_id": f"{source}_{i:04d}", "source": source}
        for source, n in counts.items()
        for i in range(n)
    ]


# ----------------------------------------------------------------------------- splits


def test_split_is_stratified_by_source():
    """Every source must appear in val, or val measures the wrong thing."""
    splits = make_splits(_manifest({"taco": 100, "uavvaste": 40, "ade": 60}), val_frac=0.2)

    val_sources = {sample_id.rsplit("_", 1)[0] for sample_id in splits["val"]}
    assert val_sources == {"taco", "uavvaste", "ade"}


def test_split_proportions_hold_per_source():
    splits = make_splits(_manifest({"taco": 100, "ade": 50}), val_frac=0.2)

    taco_val = [s for s in splits["val"] if s.startswith("taco")]
    ade_val = [s for s in splits["val"] if s.startswith("ade")]
    assert len(taco_val) == 20
    assert len(ade_val) == 10


def test_train_and_val_do_not_overlap():
    splits = make_splits(_manifest({"taco": 30, "ade": 30}), val_frac=0.15)

    assert not set(splits["train"]) & set(splits["val"])
    assert len(splits["train"]) + len(splits["val"]) == 60


def test_split_is_deterministic_for_a_seed():
    manifest = _manifest({"taco": 50, "ade": 20})
    assert make_splits(manifest, seed=7) == make_splits(manifest, seed=7)


def test_different_seeds_give_different_splits():
    manifest = _manifest({"taco": 50, "ade": 20})
    assert make_splits(manifest, seed=1) != make_splits(manifest, seed=2)


def test_single_sample_source_goes_to_train():
    """One sample cannot be both trained on and validated against; training wins.

    Sending it to val instead would mean a source contributed nothing to the model while
    still moving the metric.
    """
    splits = make_splits(_manifest({"taco": 20, "rare": 1}), val_frac=0.5)

    assert "rare_0000" in splits["train"]
    assert "rare_0000" not in splits["val"]


def test_empty_manifest_yields_empty_splits():
    assert make_splits([]) == {"train": [], "val": []}


@pytest.mark.parametrize("val_frac", [0.0, 1.0, -0.1, 1.5])
def test_invalid_val_frac_is_rejected(val_frac):
    with pytest.raises(ValueError, match="val_frac"):
        make_splits(_manifest({"taco": 10}), val_frac=val_frac)


# ----------------------------------------------------------------------------- resize


def test_resize_preserves_label_values():
    """NEAREST only. A bilinear resize would produce 1.5 between water and waste."""
    mask = np.zeros((900, 1200), dtype=np.uint8)
    mask[400:500, 300:900] = Class.WATER
    mask[450:470, 700:800] = Class.WASTE
    image = Image.new("RGB", (1200, 900))

    _, resized = _resize_pair(image, mask, max_side=640)

    assert set(np.unique(resized)).issubset({0, 1, 2})
    assert resized.dtype == np.uint8


def test_resize_caps_the_long_side_and_keeps_aspect():
    mask = np.zeros((900, 1200), dtype=np.uint8)
    image, resized = _resize_pair(Image.new("RGB", (1200, 900)), mask, max_side=640)

    assert max(resized.shape) == 640
    assert resized.shape == (480, 640)
    assert image.size == (640, 480)  # PIL is (w, h); the pair must stay aligned


def test_small_images_are_left_alone():
    mask = np.zeros((300, 400), dtype=np.uint8)
    image, resized = _resize_pair(Image.new("RGB", (400, 300)), mask, max_side=640)

    assert resized.shape == (300, 400)
    assert image.size == (400, 300)


def test_mismatched_image_is_resized_to_the_mask():
    """ADE20K annotations occasionally differ from their image by a pixel or two."""
    mask = np.zeros((300, 400), dtype=np.uint8)
    image, resized = _resize_pair(Image.new("RGB", (402, 301)), mask, max_side=640)

    assert image.size == (400, 300)
    assert resized.shape == (300, 400)


# ------------------------------------------------------------------------ write_sample


def test_write_sample_round_trips_the_mask_exactly(tmp_path):
    """Lossless PNG for masks; a JPEG mask would corrupt labels invisibly."""
    mask = np.zeros((64, 64), dtype=np.uint8)
    mask[:32] = Class.WATER
    mask[32:48] = Class.WASTE

    counts = write_sample("s_0", Image.new("RGB", (64, 64), (90, 90, 90)), mask, tmp_path)

    with Image.open(tmp_path / "masks" / "s_0.png") as written:
        assert np.array_equal(np.asarray(written, dtype=np.uint8), mask)
    assert (tmp_path / "images" / "s_0.jpg").exists()
    assert counts == {0: 16 * 64, 1: 32 * 64, 2: 16 * 64}


def test_class_pixel_counts_feed_the_loss_weights(tmp_path):
    report = BuildReport()
    mask = np.zeros((40, 40), dtype=np.uint8)
    mask[:4] = Class.WASTE  # 10% waste — the minority class the weights exist for

    report.class_pixels.update(write_sample("s_0", Image.new("RGB", (40, 40)), mask, tmp_path))
    balance = report.class_balance()

    assert balance["waste"] == pytest.approx(0.10)
    assert balance["structure"] == pytest.approx(0.90)
    assert balance["water"] == pytest.approx(0.0)  # absent, not missing from the dict


def test_class_balance_on_an_empty_report_does_not_divide_by_zero():
    assert BuildReport().class_balance() == {"structure": 0.0, "water": 0.0, "waste": 0.0}


def test_report_summary_names_every_source():
    report = BuildReport()
    report.per_source.update({"taco": 120, "ade": 80})
    summary = report.summary()

    assert "200 samples" in summary
    assert "taco" in summary and "ade" in summary


# ------------------------------------------------------------------------------ build


def test_build_with_no_sources_writes_empty_manifest_and_splits(tmp_path):
    """A missing dataset is a note in the report, not an exception.

    The pipeline has to be runnable with one source downloaded, or nothing can be tested
    until every multi-gigabyte download has finished.
    """
    report = build_unified_dataset(tmp_path / "processed", raw_dir=tmp_path / "raw")

    assert report.total == 0
    # taco, uavvaste, ade, and composite — which needs ADE20K as its substrate.
    reasons = dict(report.skipped)
    assert set(reasons) == {"taco", "uavvaste", "ade", "composite"}
    assert json.loads((tmp_path / "processed" / "manifest.json").read_text()) == []
    assert json.loads((tmp_path / "processed" / "splits.json").read_text()) == {
        "train": [],
        "val": [],
    }


def test_composites_can_be_switched_off(tmp_path):
    """``--composites 0`` must not leave a stray skip note implying something failed."""
    report = build_unified_dataset(
        tmp_path / "processed", raw_dir=tmp_path / "raw", composites=0
    )

    assert "composite" not in dict(report.skipped)
