"""Tests for ADE20K water-class resolution.

This module gates two things: the entire ``water`` class, and the substrate every
composite is pasted onto. It had two bugs that each produced *no error at the call site*
and surfaced much later as a baffling "no water classes matched":

* the real release ships ``objectInfo150.txt``, not ``.csv``; and
* that file is TAB-separated, so a comma reader parses every row as one field and the
  class table comes back empty.

The fixture below is a faithful slice of the real ``objectInfo150.txt`` — tab-separated,
with the comma-bearing synonym names and the trailing-space ``Idx`` cells the real file
has — so these tests would have caught both bugs.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from drainwatch.config import Class
from drainwatch.data.ade_water import (
    iter_water_images,
    load_ade_class_table,
    water_class_indices,
)

# A real slice: tabs between columns, commas inside Name, trailing space on some Idx.
OBJECT_INFO_TSV = (
    "Idx\tRatio\tTrain\tVal\tName\n"
    "1\t0.1576\t11664\t1172\twall\n"
    "2\t0.1072\t6046\t612\tbuilding, edifice\n"
    "7\t0.0398\t4023\t408\troad, route\n"
    "22 \t0.0074\t709\t75\twater\n"
    "27\t0.0053\t651\t57\tsea\n"
    "32\t0.0044\t1292\t184\tseat\n"
    "57\t0.0017\t198\t18\tpool table, billiard table, snooker table\n"
    "61\t0.0015\t320\t26\triver\n"
    "110\t0.0005\t66\t3\tswimming pool, swimming bath, natatorium\n"
    "114\t0.0005\t80\t9\twaterfall, falls\n"
    "129\t0.0004\t52\t5\tlake\n"
)


@pytest.fixture
def ade_root(tmp_path):
    (tmp_path / "objectInfo150.txt").write_text(OBJECT_INFO_TSV)
    return tmp_path


# --- parsing --------------------------------------------------------------------


def test_tab_separated_file_parses(ade_root):
    """The bug that mattered: a comma reader returns an empty table here."""
    table = load_ade_class_table(ade_root)

    assert table[22] == "water"
    assert table[2] == "building, edifice"  # the comma-in-name case survives
    assert len(table) == 11


def test_the_txt_filename_is_found_not_only_csv(ade_root):
    """The release is objectInfo150.txt; the old code looked only for .csv."""
    # No .csv present, only .txt — must still work.
    assert not (ade_root / "objectInfo150.csv").exists()
    assert load_ade_class_table(ade_root)[27] == "sea"


def test_a_csv_mirror_still_works(tmp_path):
    """Some mirrors rename it .csv with comma delimiters; detect, don't assume."""
    (tmp_path / "objectInfo150.csv").write_text(
        "Idx,Ratio,Train,Val,Name\n22,0.0074,709,75,water\n27,0.0053,651,57,sea\n"
    )

    table = load_ade_class_table(tmp_path)

    assert table == {22: "water", 27: "sea"}


def test_missing_file_names_the_real_filename(tmp_path):
    with pytest.raises(FileNotFoundError, match="objectInfo150.txt"):
        load_ade_class_table(tmp_path)


def test_a_file_in_the_wrong_format_fails_loudly_not_empty(tmp_path):
    """A table that parses to zero classes must raise here, not three functions later."""
    (tmp_path / "objectInfo150.txt").write_text("this is not the file you think it is\n")

    with pytest.raises(ValueError, match="zero classes"):
        load_ade_class_table(tmp_path)


# --- water-class resolution -----------------------------------------------------


def test_all_six_water_classes_are_matched(ade_root):
    """water, sea, river, swimming pool, waterfall, lake — the real set."""
    assert water_class_indices(ade_root) == {22, 27, 61, 110, 114, 129}


def test_seat_is_not_mistaken_for_sea(ade_root):
    """Whole-synonym matching: 'sea' must not substring-match 'seat' (idx 32)."""
    assert 32 not in water_class_indices(ade_root)


def test_pool_table_is_not_mistaken_for_water(ade_root):
    """'pool table' (idx 57) is furniture, not water — the whole-synonym rule's job."""
    assert 57 not in water_class_indices(ade_root)


def test_unmatched_synonyms_raise(ade_root):
    with pytest.raises(ValueError, match="no ADE20K classes matched"):
        water_class_indices(ade_root, synonyms=frozenset({"lava", "magma"}))


# --- mask derivation ------------------------------------------------------------


def test_water_images_are_remapped_to_drainwatch_classes(tmp_path):
    """An ADE annotation (1..150) becomes a DrainWatch mask of WATER / STRUCTURE."""
    (tmp_path / "objectInfo150.txt").write_text(OBJECT_INFO_TSV)
    ann_dir = tmp_path / "annotations" / "training"
    img_dir = tmp_path / "images" / "training"
    ann_dir.mkdir(parents=True)
    img_dir.mkdir(parents=True)

    # Top half water (class 22), bottom half wall (class 1).
    labels = np.full((40, 40), 1, dtype=np.uint8)
    labels[:20] = 22
    Image.fromarray(labels).save(ann_dir / "ADE_train_00000001.png")
    Image.fromarray(np.zeros((40, 40, 3), dtype=np.uint8)).save(
        img_dir / "ADE_train_00000001.jpg"
    )

    (sample,) = list(iter_water_images(tmp_path))

    assert set(np.unique(sample.mask)) == {int(Class.STRUCTURE), int(Class.WATER)}
    assert (sample.mask[:20] == int(Class.WATER)).all()
    assert (sample.mask[20:] == int(Class.STRUCTURE)).all()
    assert sample.water_frac == pytest.approx(0.5)


def test_images_below_the_water_threshold_are_filtered(tmp_path):
    """A frame that is 1% water teaches nothing about water but adds a near-empty frame."""
    (tmp_path / "objectInfo150.txt").write_text(OBJECT_INFO_TSV)
    ann_dir = tmp_path / "annotations" / "training"
    img_dir = tmp_path / "images" / "training"
    ann_dir.mkdir(parents=True)
    img_dir.mkdir(parents=True)

    labels = np.full((100, 100), 1, dtype=np.uint8)
    labels[0, :] = 22  # 1% water, below the 2% default
    Image.fromarray(labels).save(ann_dir / "ADE_train_00000001.png")
    Image.fromarray(np.zeros((100, 100, 3), dtype=np.uint8)).save(
        img_dir / "ADE_train_00000001.jpg"
    )

    assert list(iter_water_images(tmp_path)) == []
