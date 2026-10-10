"""Central configuration: class map, thresholds, paths.

Kept dependency-light (stdlib only) so it can be imported by the API container,
the training scripts, and the tests alike.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
CHECKPOINT_DIR = PROJECT_ROOT / "checkpoints"


class Class(IntEnum):
    """Segmentation classes. Values are the label indices written into mask PNGs."""

    STRUCTURE = 0  # concrete, road, kerb, background
    WATER = 1  # flowing or standing water
    WASTE = 2  # plastic, sludge, debris


NUM_CLASSES = len(Class)

CLASS_NAMES: dict[int, str] = {
    Class.STRUCTURE: "structure",
    Class.WATER: "water",
    Class.WASTE: "waste",
}

# Overlay colours (RGB). High contrast so a non-technical viewer reads the
# blockage instantly — this is what the demo video shows.
CLASS_COLORS: dict[int, tuple[int, int, int]] = {
    Class.STRUCTURE: (0, 0, 0),  # rendered transparent
    Class.WATER: (0, 150, 255),  # bright blue
    Class.WASTE: (255, 0, 110),  # magenta
}

# Model
BASE_MODEL = "nvidia/segformer-b0-finetuned-ade-512-512"
IMAGE_SIZE = 512

# ImageNet statistics — SegFormer's pretrained weights expect this normalization.
# Defined here, not in the dataset module, because inference (torch + PIL only) and
# training (albumentations) both need them and must not drift apart.
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


@dataclass(frozen=True)
class ChokeThresholds:
    """Tunables for the Choke Index. Calibrated against the Delhi validation set.

    ``min_channel_frac`` is a fraction of total frame pixels rather than an absolute
    count, so the same thresholds hold for a 480p traffic camera and a 4K phone photo.
    """

    watch: float = 0.40
    critical: float = 0.70
    min_channel_frac: float = 0.005  # 0.5% of the frame
    min_channel_px_floor: int = 512
    min_confidence: float = 0.55

    def min_channel_px(self, total_px: int) -> int:
        """Smallest channel area (water + waste) that counts as "a drain is in frame"."""
        return max(self.min_channel_px_floor, int(self.min_channel_frac * total_px))


DEFAULT_THRESHOLDS = ChokeThresholds()
