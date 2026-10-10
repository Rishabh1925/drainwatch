"""Derive ``water`` masks from ADE20K (SceneParse150).

ADE20K's 150-class annotations already contain water, river, sea and lake regions with
real human-drawn masks, which is exactly the ``water`` class DrainWatch needs — and it
costs nothing to annotate.

Class indices are resolved by reading the dataset's own ``objectInfo150.csv`` and
matching class *names*, rather than hardcoding integers. Hardcoded indices are the
classic way this silently breaks when a dataset revision reorders classes; matching on
names fails loudly instead, and ``describe_water_classes`` lets you eyeball the result.

Expected layout after unzipping ``ADEChallengeData2016.zip``::

    ADEChallengeData2016/
      images/training/ADE_train_%08d.jpg
      annotations/training/ADE_train_%08d.png   # uint8: 0 = unlabeled, 1..150 = classes
      objectInfo150.txt                          # TAB-separated: Idx Ratio Train Val Name
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from drainwatch.config import Class

# Matched against the comma-separated synonym list in objectInfo150.csv.
WATER_SYNONYMS: frozenset[str] = frozenset(
    {"water", "sea", "river", "lake", "waterfall", "falls", "swimming pool", "pool"}
)

# Images whose water region is tiny teach the model almost nothing about water but do
# add a lot of near-empty frames, so they are filtered out.
MIN_WATER_FRAC = 0.02


@dataclass(frozen=True)
class AdeSample:
    """One ADE20K image that contains enough water to be worth training on."""

    image_path: Path
    mask: np.ndarray  # (H, W) with Class.WATER / Class.STRUCTURE
    water_frac: float


def load_ade_class_table(root: Path) -> dict[int, str]:
    """Parse ``objectInfo150`` into ``{index: synonyms}``.

    The SceneParse150 release ships ``objectInfo150.txt`` and it is **TAB-separated**;
    some mirrors rename it ``.csv``. Both the filename and the delimiter are detected
    rather than assumed, because the failure mode of guessing wrong is silent and
    delayed: a comma reader on the tab file parses every line as a single field, every
    row is skipped as malformed, the table comes back empty, and the error finally
    surfaces as a baffling "no ADE20K classes matched water" in a different function.

    The ``Name`` column itself contains comma-separated synonyms (e.g. ``"water"``,
    ``"building, edifice"``), so once the rows are split on the real delimiter the
    trailing fields are rejoined.
    """
    path = next(
        (root / name for name in ("objectInfo150.txt", "objectInfo150.csv") if (root / name).exists()),
        None,
    )
    if path is None:
        raise FileNotFoundError(
            f"objectInfo150.txt not found in {root} — unzip ADEChallengeData2016.zip into {root.parent}"
        )

    lines = path.read_text().splitlines()
    if not lines:
        raise ValueError(f"{path} is empty")
    # Pick the delimiter the header actually uses, so a renamed-but-still-TSV file works.
    header = lines[0]
    delimiter = "\t" if header.count("\t") >= header.count(",") else ","

    table: dict[int, str] = {}
    for row in csv.reader(lines[1:], delimiter=delimiter):
        if len(row) < 5 or not row[0].strip().isdigit():
            continue
        table[int(row[0].strip())] = ",".join(row[4:]).strip().strip('"')
    if not table:
        raise ValueError(
            f"{path} parsed to zero classes with delimiter {delimiter!r} — the file "
            "format is not what was expected; inspect its first lines"
        )
    return table


def water_class_indices(
    root: Path, *, synonyms: frozenset[str] = WATER_SYNONYMS
) -> set[int]:
    """Resolve which ADE20K class indices count as water.

    Matches whole synonyms only, so ``water`` does not accidentally capture
    ``water tower`` or ``watering can``.
    """
    table = load_ade_class_table(root)
    matched = set()
    for idx, names in table.items():
        parts = {part.strip().lower() for part in names.split(",")}
        if parts & synonyms:
            matched.add(idx)

    if not matched:
        raise ValueError(
            f"no ADE20K classes matched {sorted(synonyms)} — inspect objectInfo150.csv; "
            "the dataset revision may use different names"
        )
    return matched


def describe_water_classes(root: Path) -> list[str]:
    """Human-readable list of the matched classes. Eyeball this before training."""
    table = load_ade_class_table(root)
    return [f"{idx}: {table[idx]}" for idx in sorted(water_class_indices(root))]


def iter_water_images(
    root: Path,
    *,
    split: str = "training",
    min_water_frac: float = MIN_WATER_FRAC,
    limit: int | None = None,
) -> Iterator[AdeSample]:
    """Yield ADE20K images whose water area clears ``min_water_frac``.

    Args:
        root: the ``ADEChallengeData2016`` directory.
        split: ``"training"`` or ``"validation"``.
        min_water_frac: minimum fraction of the frame that must be water.
        limit: stop after yielding this many samples.

    Yields:
        :class:`AdeSample` with a mask already remapped to DrainWatch classes.
    """
    water = water_class_indices(root)
    ann_dir = root / "annotations" / split
    img_dir = root / "images" / split
    if not ann_dir.is_dir():
        raise FileNotFoundError(f"{ann_dir} not found — check the ADE20K extraction path")

    yielded = 0
    for ann_path in sorted(ann_dir.glob("*.png")):
        if limit is not None and yielded >= limit:
            return

        labels = np.asarray(Image.open(ann_path))
        water_sel = np.isin(labels, list(water))
        frac = float(water_sel.mean())
        if frac < min_water_frac:
            continue

        image_path = img_dir / f"{ann_path.stem}.jpg"
        if not image_path.exists():
            continue

        mask = np.full(labels.shape, int(Class.STRUCTURE), dtype=np.uint8)
        mask[water_sel] = int(Class.WATER)

        yield AdeSample(image_path=image_path, mask=mask, water_frac=frac)
        yielded += 1
