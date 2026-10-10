"""SegFormer loading and inference.

SegFormer-b0 initialised from ``nvidia/segformer-b0-finetuned-ade-512-512`` rather than
from ImageNet weights: ADE20K pretraining means the encoder already represents water,
road and earth, which is most of what this task needs. That warm start is worth more
than extra epochs on a small dataset, and it is why a from-scratch U-Net is the wrong
choice at this data scale.

Only torch, transformers and PIL are needed here — no albumentations — so the API
container stays small.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageFilter

# ── CPU-only compatibility patch ─────────────────────────────────────────────
# transformers 4.48+ calls torch.accelerator.current_accelerator() at module
# import time. In torch 2.6+ this raises RuntimeError when no GPU is present
# (e.g. ECS Fargate CPU tasks). Patch it before the transformers import.
try:
    torch.accelerator.current_accelerator()
except (RuntimeError, AttributeError):
    import torch.accelerator as _acc
    _acc.current_accelerator = lambda: torch.device("cpu")
# ─────────────────────────────────────────────────────────────────────────────

from transformers import SegformerForSemanticSegmentation

from drainwatch.config import (
    BASE_MODEL,
    CLASS_NAMES,
    IMAGE_SIZE,
    IMAGENET_MEAN,
    IMAGENET_STD,
    NUM_CLASSES,
)


def pick_device(prefer: str | None = None) -> torch.device:
    """Best available device: MPS on Apple silicon, then CUDA, then CPU.

    Set ``PYTORCH_ENABLE_MPS_FALLBACK=1`` when training on MPS — a few ops still lack
    Metal kernels and will otherwise hard-fail mid-epoch instead of falling back.

    The MPS-built-but-unavailable case gets an explicit warning because it is the one
    failure here that is otherwise *invisible*: the fallback to CPU succeeds, training
    runs, and the only symptom is that every epoch takes roughly three times longer than
    planned. Measured on an M-series CPU, SegFormer-b0 at 512px is ~1.0 s/image — slow
    enough that the difference between "GPU" and "silently CPU" is a whole night.

    Note that torch's own error for this reads "The MPS backend is supported on MacOS
    13.0+" regardless of the real cause; it is a generic fallback message, so do not
    trust it to mean the OS is too old.
    """
    if prefer:
        return torch.device(prefer)
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_built():
        print(
            "warning: torch has MPS compiled in but reports it unavailable — falling back "
            "to CPU, which is ~3x slower. Common causes: a sandboxed or restricted process "
            "with no Metal device access, or a headless session. Check with:\n"
            "  python3 -c 'import torch; print(torch.backends.mps.is_available())'"
        )
    return torch.device("cpu")


def load_model(
    checkpoint: Path | str | None = None,
    *,
    base: str = BASE_MODEL,
) -> SegformerForSemanticSegmentation:
    """Load the segmentation model.

    Args:
        checkpoint: a directory written by ``save_pretrained``. When omitted, the
            pretrained base is loaded and its 150-class ADE20K head is replaced with a
            freshly initialised 3-class head (``ignore_mismatched_sizes``), keeping the
            encoder and decoder weights.
        base: HuggingFace model id used when ``checkpoint`` is None.
    """
    id2label = {int(k): v for k, v in CLASS_NAMES.items()}
    label2id = {v: k for k, v in id2label.items()}

    if checkpoint is not None:
        return SegformerForSemanticSegmentation.from_pretrained(
            str(checkpoint), num_labels=NUM_CLASSES, id2label=id2label, label2id=label2id
        )

    return SegformerForSemanticSegmentation.from_pretrained(
        base,
        num_labels=NUM_CLASSES,
        id2label=id2label,
        label2id=label2id,
        ignore_mismatched_sizes=True,  # 150-class ADE head -> 3-class drain head
    )


def preprocess(image: Image.Image, *, image_size: int = IMAGE_SIZE) -> torch.Tensor:
    """Image -> normalized (1, 3, S, S) tensor. Mirrors the val transform exactly."""
    resized = image.convert("RGB").resize((image_size, image_size), Image.BILINEAR)
    array = np.asarray(resized, dtype=np.float32) / 255.0
    array = (array - np.asarray(IMAGENET_MEAN, dtype=np.float32)) / np.asarray(
        IMAGENET_STD, dtype=np.float32
    )
    return torch.from_numpy(array).permute(2, 0, 1).unsqueeze(0)


def denoise_mask(mask: np.ndarray, *, size: int = 5) -> np.ndarray:
    """Majority-filter away speckle.

    Isolated misclassified pixels inflate the waste count and make the Choke Index
    jitter between frames of the same drain. A mode filter is the right operation for a
    label image — unlike a blur, it can only ever output a label that was already there.
    """
    if size <= 1:
        return mask
    filtered = Image.fromarray(mask).filter(ImageFilter.ModeFilter(size))
    return np.asarray(filtered, dtype=np.uint8)


@torch.inference_mode()
def predict(
    model: SegformerForSemanticSegmentation,
    image: Image.Image,
    *,
    device: torch.device | None = None,
    image_size: int = IMAGE_SIZE,
    denoise: int = 5,
) -> tuple[np.ndarray, np.ndarray]:
    """Segment one image.

    Logits are upsampled to the *original* resolution before argmax, so the returned
    mask aligns pixel-for-pixel with the source photo for overlay rendering. (SegFormer
    emits logits at a quarter of the input resolution.)

    Returns:
        ``(mask, confidence)`` — both (H, W) at the source image's size. ``mask`` holds
        :class:`~drainwatch.config.Class` values; ``confidence`` is the max softmax
        probability per pixel, consumed by the Choke Index's review gate.
    """
    device = device or pick_device()
    model = model.to(device).eval()

    width, height = image.size
    pixel_values = preprocess(image, image_size=image_size).to(device)

    logits = model(pixel_values=pixel_values).logits  # (1, C, S/4, S/4)
    logits = torch.nn.functional.interpolate(
        logits, size=(height, width), mode="bilinear", align_corners=False
    )

    probs = logits.softmax(dim=1)[0]
    confidence, mask = probs.max(dim=0)

    mask_np = mask.to("cpu").numpy().astype(np.uint8)
    confidence_np = confidence.to("cpu").numpy().astype(np.float32)

    return denoise_mask(mask_np, size=denoise), confidence_np
