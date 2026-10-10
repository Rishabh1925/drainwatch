#!/usr/bin/env python3
"""Does SegFormer-b0 actually train on this Mac's GPU, and how fast?

Two questions, neither answerable by reading documentation:

1. **Are the Metal kernels there?** PyTorch's MPS backend has gaps. A missing kernel does
   not fail at model construction — it fails mid-backward-pass, which on a real run means
   an hour into epoch one. ``PYTORCH_ENABLE_MPS_FALLBACK=1`` routes misses to CPU, but a
   fallback inside the training loop is a silent 10x slowdown, so it is worth knowing
   whether one fires at all.
2. **What does an epoch cost?** Seconds-per-step times dataset size is the only honest
   input to the schedule. Guessing it is how a day allocated to training becomes two.

Runs forward + backward, because inference-only timing is roughly a third of training
cost and would flatter the estimate. No downloads: the model is built from a config, so
this measures the hardware and the kernels, not HuggingFace.

    python3 scripts/mps_smoke.py
    python3 scripts/mps_smoke.py --device cpu --dataset-size 400
"""

from __future__ import annotations

import argparse
import os
import platform
import time

# Must precede the torch import to take effect.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import torch  # noqa: E402
from transformers import SegformerConfig, SegformerForSemanticSegmentation  # noqa: E402

NUM_CLASSES = 3


def build_model(real_b0: bool) -> torch.nn.Module:
    """SegFormer-b0's true architecture, randomly initialised.

    The b0 geometry is hard-coded rather than downloaded so this runs offline. Parameter
    count and layer shapes are what determine speed; the weights' *values* do not, so
    random init measures the same thing as the pretrained checkpoint would.
    """
    if not real_b0:
        return SegformerForSemanticSegmentation(
            SegformerConfig(num_labels=NUM_CLASSES, depths=[1, 1, 1, 1])
        )
    return SegformerForSemanticSegmentation(
        SegformerConfig(
            num_labels=NUM_CLASSES,
            depths=[2, 2, 2, 2],
            hidden_sizes=[32, 64, 160, 256],
            num_attention_heads=[1, 2, 5, 8],
            decoder_hidden_size=256,
        )
    )


def sync(device: torch.device) -> None:
    """Make timing honest. MPS and CUDA queue work asynchronously, so without this the
    measured time is how long it took to *enqueue* the ops, not to run them."""
    if device.type == "mps":
        torch.mps.synchronize()
    elif device.type == "cuda":
        torch.cuda.synchronize()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default=None, help="mps, cpu, cuda (default: best available)")
    parser.add_argument("--image-size", type=int, default=512)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--steps", type=int, default=6, help="timed steps after warmup")
    parser.add_argument("--dataset-size", type=int, default=1200, help="for the epoch estimate")
    parser.add_argument("--tiny", action="store_true", help="use the tiny config, not real b0")
    args = parser.parse_args()

    if args.device:
        device = torch.device(args.device)
    elif torch.backends.mps.is_available():
        device = torch.device("mps")
    elif torch.cuda.is_available():
        device = torch.device("cuda")
    else:
        device = torch.device("cpu")

    print(f"torch {torch.__version__} · {platform.machine()} · python {platform.python_version()}")
    print(f"mps available: {torch.backends.mps.is_available()}  built: {torch.backends.mps.is_built()}")
    print(f"device: {device}  ·  fallback: {os.environ.get('PYTORCH_ENABLE_MPS_FALLBACK')}")

    model = build_model(real_b0=not args.tiny).to(device).train()
    params = sum(p.numel() for p in model.parameters())
    print(f"model: {'tiny' if args.tiny else 'segformer-b0'}  ·  {params / 1e6:.2f}M params")
    print(f"input: {args.batch_size}x3x{args.image_size}x{args.image_size}\n")

    optimizer = torch.optim.AdamW(model.parameters(), lr=6e-5)
    # Class weights as the real loop uses them — the weighted loss is itself an op that
    # has to exist on the device.
    weights = torch.tensor([0.3, 1.0, 1.7], device=device)
    criterion = torch.nn.CrossEntropyLoss(weight=weights)

    images = torch.randn(args.batch_size, 3, args.image_size, args.image_size, device=device)
    labels = torch.randint(
        0, NUM_CLASSES, (args.batch_size, args.image_size, args.image_size), device=device
    )

    def step() -> float:
        optimizer.zero_grad(set_to_none=True)
        logits = model(pixel_values=images).logits
        # SegFormer emits logits at 1/4 resolution; the real loop upsamples to match the
        # labels. Skipping it here would skip a bilinear interpolate on the device.
        logits = torch.nn.functional.interpolate(
            logits, size=labels.shape[-2:], mode="bilinear", align_corners=False
        )
        # Flatten with reshape, matching train.py: the 4-D cross_entropy backward calls
        # .view() on the non-contiguous upsampled logits and raises on MPS. This probe has
        # to exercise the same loss path the real loop uses, or it would pass while
        # training crashes.
        loss = criterion(
            logits.permute(0, 2, 3, 1).reshape(-1, NUM_CLASSES), labels.reshape(-1)
        )
        loss.backward()
        optimizer.step()
        return float(loss.detach().cpu())

    try:
        t0 = time.perf_counter()
        loss = step()
        sync(device)
        print(f"warmup step: {time.perf_counter() - t0:.2f}s  (loss {loss:.4f})")
    except Exception as exc:  # noqa: BLE001
        print(f"\nFAILED on the first step: {type(exc).__name__}: {exc}")
        print("A missing Metal kernel would surface here. Retry with --device cpu.")
        return 1

    timings = []
    for i in range(args.steps):
        t0 = time.perf_counter()
        loss = step()
        sync(device)
        elapsed = time.perf_counter() - t0
        timings.append(elapsed)
        print(f"  step {i + 1}: {elapsed:.3f}s  (loss {loss:.4f})")

    timings.sort()
    median = timings[len(timings) // 2]
    per_image = median / args.batch_size
    steps_per_epoch = -(-args.dataset_size // args.batch_size)  # ceil
    epoch_s = steps_per_epoch * median

    print(f"\nmedian step: {median:.3f}s  ·  {per_image:.3f}s/image  ·  {1 / per_image:.1f} img/s")
    print(f"projected epoch ({args.dataset_size} images): {epoch_s / 60:.1f} min")
    print(f"projected 20 epochs: {epoch_s * 20 / 3600:.1f} h")

    if not torch.isfinite(torch.tensor(loss)):
        print("\nWARNING: loss is not finite — numerically broken on this device.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
