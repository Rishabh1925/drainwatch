"""End-to-end smoke test for the fine-tuning loop.

This runs the *real* :func:`drainwatch.model.train.train` — real DataLoader, real
augmentation, real weighted loss, real backward pass, real checkpoint write — against a
tiny randomly-initialised SegFormer on a four-image synthetic dataset. It takes seconds.

Why it is worth the lines: every other test in this suite covers a pure function. The
training loop is where the pieces meet, and the ways it fails are not subtle in
retrospect but are invisible beforehand — a shape mismatch between logits and labels, a
device left behind on CPU, ``save_pretrained`` never reached, a checkpoint selection rule
that never fires. Finding any of those during the one real training run, on the day the
schedule allocates to training, costs that day.

The model is built from a config rather than downloaded, so this runs offline and in
seconds. It therefore proves the *plumbing*, never the accuracy — the random net's IoU is
meaningless and nothing here asserts on it.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("torch", reason="training extras not installed")
pytest.importorskip("albumentations", reason="training extras not installed")

from PIL import Image  # noqa: E402

from drainwatch.config import Class  # noqa: E402
from drainwatch.model.train import TrainConfig, train  # noqa: E402

IMAGE_SIZE = 64  # SegFormer downsamples 32x; 64 leaves a 2x2 final stage


def tiny_model(num_labels: int = 3):
    """A randomly initialised SegFormer, ~100k params, built offline from a config.

    Using the real pretrained base would mean a network download and a far slower test,
    and would test HuggingFace rather than this project.
    """
    from transformers import SegformerConfig, SegformerForSemanticSegmentation

    config = SegformerConfig(
        num_labels=num_labels,
        depths=[1, 1, 1, 1],
        hidden_sizes=[8, 16, 32, 64],
        num_attention_heads=[1, 1, 1, 1],
        decoder_hidden_size=16,
    )
    return SegformerForSemanticSegmentation(config)


def write_dataset(root: Path, *, val_classes=(Class.WATER, Class.WASTE)) -> None:
    """A four-sample dataset on disk in the exact layout ``train`` expects.

    Args:
        val_classes: classes present in the validation masks. The default has both
            scored classes; restricting it is how the "no scored classes" guard is tested.
    """
    (root / "images").mkdir(parents=True, exist_ok=True)
    (root / "masks").mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(7)
    manifest = []
    splits: dict[str, list[str]] = {"train": [], "val": []}

    for index in range(4):
        split = "train" if index < 2 else "val"
        sample_id = f"synth_{index:02d}"

        mask = np.zeros((96, 128), dtype=np.uint8)  # structure everywhere
        present = (Class.WATER, Class.WASTE) if split == "train" else val_classes
        if Class.WATER in present:
            mask[20:50, :] = int(Class.WATER)
        if Class.WASTE in present:
            mask[50:80, 30:100] = int(Class.WASTE)

        image = rng.integers(0, 255, (96, 128, 3), dtype=np.uint8)
        Image.fromarray(image).save(root / "images" / f"{sample_id}.jpg", quality=92)
        Image.fromarray(mask).save(root / "masks" / f"{sample_id}.png")

        values, counts = np.unique(mask, return_counts=True)
        manifest.append(
            {
                "sample_id": sample_id,
                "source": "synthetic",
                "class_pixels": {str(int(v)): int(c) for v, c in zip(values, counts, strict=True)},
            }
        )
        splits[split].append(sample_id)

    (root / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (root / "splits.json").write_text(json.dumps(splits, indent=2))


@pytest.fixture
def dataset(tmp_path) -> Path:
    root = tmp_path / "processed"
    write_dataset(root)
    return root


def run(dataset: Path, out_dir: Path, **overrides) -> dict:
    config = TrainConfig(
        epochs=overrides.pop("epochs", 2),
        batch_size=2,
        image_size=IMAGE_SIZE,
        num_workers=0,
        patience=overrides.pop("patience", 6),
    )
    return train(
        root=dataset,
        out_dir=out_dir,
        config=config,
        device=__import__("torch").device("cpu"),  # deterministic and fast at this size
        model=tiny_model(),
        **overrides,
    )


# --- the happy path -------------------------------------------------------------


def test_training_runs_and_writes_a_usable_checkpoint(dataset, tmp_path):
    out_dir = tmp_path / "checkpoints"

    report = run(dataset, out_dir)

    best = out_dir / "best"
    assert best.is_dir(), "no checkpoint written — save_pretrained was never reached"
    assert (best / "config.json").exists()
    # Either serialization format is fine; what matters is that weights exist at all.
    assert list(best.glob("*.safetensors")) or list(best.glob("*.bin"))
    assert report["best_epoch"] >= 1
    assert report["checkpoint"] == str(best)


def test_the_checkpoint_can_be_loaded_back(dataset, tmp_path):
    """A checkpoint that cannot be reloaded is not a checkpoint.

    This is the handoff between training and serving — the one seam where a mismatch
    (wrong num_labels, missing config) surfaces only at deploy time.
    """
    from transformers import SegformerForSemanticSegmentation

    out_dir = tmp_path / "checkpoints"
    run(dataset, out_dir)

    reloaded = SegformerForSemanticSegmentation.from_pretrained(out_dir / "best")

    assert reloaded.config.num_labels == len(Class)


def test_report_is_written_and_json_serializable(dataset, tmp_path):
    """``json.dumps`` emits bare ``NaN`` for float nan, which is invalid JSON and will
    break the dashboard that reads this file. Round-tripping through a strict parse is
    the only way to catch it."""
    out_dir = tmp_path / "checkpoints"

    report = run(dataset, out_dir)

    on_disk = (out_dir / "training_report.json").read_text()
    json.loads(on_disk, parse_constant=lambda c: pytest.fail(f"invalid JSON constant: {c}"))
    assert len(report["history"]) == 2
    assert report["train_samples"] == 2
    assert report["val_samples"] == 2


def test_history_records_per_class_iou_by_name(dataset, tmp_path):
    """The history is read by a human deciding whether the quality gate is met, so the
    classes have to be named, not indexed."""
    report = run(dataset, tmp_path / "checkpoints")

    iou = report["history"][0]["iou"]

    assert set(iou) == {"structure", "water", "waste"}


def test_loss_is_finite(dataset, tmp_path):
    """A NaN loss means the weighted cross-entropy or the learning rate is wrong, and
    every later metric is meaningless."""
    report = run(dataset, tmp_path / "checkpoints")

    for entry in report["history"]:
        assert np.isfinite(entry["train_loss"]), entry


# --- the failure modes that must be loud ----------------------------------------


def test_empty_split_fails_before_training_starts(tmp_path):
    root = tmp_path / "processed"
    write_dataset(root)
    (root / "splits.json").write_text(json.dumps({"train": [], "val": []}))

    with pytest.raises(ValueError, match="empty split"):
        run(root, tmp_path / "checkpoints")


def test_validation_without_water_or_waste_fails_before_the_first_epoch(tmp_path):
    """The silent-ruin case, caught by the pre-flight manifest check.

    Checkpoint selection ranks epochs by water+waste IoU. With neither class in the
    validation split there is nothing to rank by, so "best" degenerates to "first" and
    the run produces a checkpoint nobody should trust.

    Note what does *not* catch this: the IoU itself. An untrained model predicts water
    and waste pixels even when the truth holds none, so the union is non-empty and the
    IoU comes out a real 0.0 rather than NaN. Hence the check reads the manifest instead
    of waiting for a metric.
    """
    root = tmp_path / "processed"
    write_dataset(root, val_classes=())  # validation masks are all structure
    out_dir = tmp_path / "checkpoints"

    with pytest.raises(ValueError, match="no water or waste"):
        run(root, out_dir)

    assert not (out_dir / "best").exists(), "failed before the first epoch, so nothing to save"


def test_validation_missing_only_one_scored_class_warns_but_proceeds(tmp_path, capsys):
    """Half a signal is still a signal — water IoU alone can rank epochs.

    Training is allowed, but the operator has to be told the quality gate cannot pass.
    """
    root = tmp_path / "processed"
    write_dataset(root, val_classes=(Class.WATER,))

    report = run(root, tmp_path / "checkpoints")

    assert report["best_epoch"] >= 1
    assert "no waste pixels" in capsys.readouterr().out


def test_nan_score_is_rejected_rather_than_silently_skipped():
    """The in-loop backstop, exercised directly.

    Reachable only when the validation split holds no water or waste *and* the model
    predicts neither — i.e. a collapsed model. Constructing the confusion matrix is the
    honest way to test it; no training run would reproduce it on demand.
    """
    import numpy as np

    from drainwatch.model.metrics import ConfusionMatrix
    from drainwatch.model.train import _score

    confusion = ConfusionMatrix(num_classes=len(Class))
    only_structure = np.zeros((8, 8), dtype=np.uint8)
    confusion.update(only_structure, only_structure)

    assert np.isnan(_score(confusion))


def test_missing_dataset_points_at_the_command_that_builds_it(tmp_path):
    with pytest.raises(FileNotFoundError, match="data build"):
        run(tmp_path / "does_not_exist", tmp_path / "checkpoints")
