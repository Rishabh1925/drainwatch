"""Command line interface.

Every subcommand imports its heavy dependencies *inside* the handler. ``drainwatch data
check`` must work with only numpy and pillow installed — it is the first thing run on a
fresh machine, and making it wait on a torch install would be backwards.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from drainwatch.config import CHECKPOINT_DIR, PROCESSED_DIR, RAW_DIR


def cmd_data_check(args: argparse.Namespace) -> int:
    """HEAD every dataset URL and report what is actually reachable."""
    from drainwatch.data.sources import check_sources

    results = check_sources()
    failures = 0
    for name, (ok, detail) in sorted(results.items()):
        print(f"{'OK  ' if ok else 'FAIL'}  {name:<28} {detail}")
        failures += not ok
    if failures:
        print(f"\n{failures} source(s) unreachable — see data/sources.py for fallbacks.")
    return 1 if failures else 0


def cmd_data_download(args: argparse.Namespace) -> int:
    from drainwatch.data.sources import download_all

    report = download_all(RAW_DIR, sources=args.source, image_limit=args.image_limit)
    print(report.summary())
    # Some per-image failures are expected (TACO's Flickr links rot), so only a pull
    # that produced nothing at all counts as a failed command.
    if report.fetched == 0 and report.skipped == 0:
        print("\nNothing downloaded. Run `drainwatch data check` first.", file=sys.stderr)
        return 1
    return 0


def cmd_data_build(args: argparse.Namespace) -> int:
    from drainwatch.data.unify import DEFAULT_COMPOSITES, build_unified_dataset

    composites = DEFAULT_COMPOSITES if args.composites is None else args.composites
    report = build_unified_dataset(
        PROCESSED_DIR,
        raw_dir=RAW_DIR,
        limit_per_source=args.limit,
        val_frac=args.val_frac,
        composites=composites,
    )
    print(report.summary())
    if report.total == 0:
        print("\nNothing built. Run `drainwatch data download` first.", file=sys.stderr)
        return 1
    if composites > 0 and report.per_source["composite"] == 0:
        print(
            "\nWARNING: no composites were produced, so no training image contains both "
            "`water` and `waste` — the two classes the Choke Index is computed from. "
            "See the skipped list above and src/drainwatch/data/composite.py.",
            file=sys.stderr,
        )
    return 0


def cmd_data_preview(args: argparse.Namespace) -> int:
    """Render image+mask overlays so the labels can be eyeballed before training.

    Worth the five minutes: a class-remapping bug produces a perfectly trainable dataset
    that teaches the model the wrong thing, and no metric will tell you.
    """
    import json

    import numpy as np
    from PIL import Image

    from drainwatch.choke.index import compute_choke_index
    from drainwatch.choke.overlay import render_overlay

    splits = json.loads((PROCESSED_DIR / "splits.json").read_text())
    sample_ids = (splits["train"] + splits["val"])[: args.n]
    out_dir = PROCESSED_DIR / "preview"
    out_dir.mkdir(parents=True, exist_ok=True)

    for sample_id in sample_ids:
        with Image.open(PROCESSED_DIR / "images" / f"{sample_id}.jpg") as image:
            with Image.open(PROCESSED_DIR / "masks" / f"{sample_id}.png") as mask_img:
                mask = np.asarray(mask_img, dtype=np.uint8)
                render_overlay(image, mask).save(out_dir / f"{sample_id}.png")
        reading = compute_choke_index(mask)
        print(f"{sample_id:<24} CI={reading.choke_index}  {reading.severity.value}")

    print(f"\n{len(sample_ids)} overlays written to {out_dir}")
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    from drainwatch.model.segformer import pick_device
    from drainwatch.model.train import TrainConfig, train

    config = TrainConfig(
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        num_workers=args.num_workers,
    )
    # ``--device`` exists as an escape hatch: an MPS backward-pass bug that survives the
    # contiguity fixes would otherwise make training impossible to run at all, and a
    # 12-hour CPU run that finishes beats a 4-hour GPU run that crashes at step one.
    train(
        root=PROCESSED_DIR,
        out_dir=CHECKPOINT_DIR,
        config=config,
        base_model=args.base_model,
        device=pick_device(args.device) if args.device else None,
    )
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from drainwatch.model.evaluate import evaluate_checkpoint

    confusion = evaluate_checkpoint(
        Path(args.checkpoint), root=Path(args.root), split=args.split, batch_size=args.batch_size
    )
    print(f"split: {args.split}   root: {args.root}")
    print(confusion.report())
    return 0 if confusion.meets_gate() else 1


def cmd_analyze(args: argparse.Namespace) -> int:
    """Score one image locally — no server, no AWS. The fastest feedback loop there is."""
    import json

    from PIL import Image

    from drainwatch.choke.index import compute_choke_index
    from drainwatch.choke.overlay import render_overlay
    from drainwatch.geo.sources import extract_captured_at, extract_gps
    from drainwatch.model.segformer import load_model, predict

    checkpoint = Path(args.checkpoint) if args.checkpoint else CHECKPOINT_DIR / "best"
    model = load_model(checkpoint if checkpoint.exists() else None)
    if not checkpoint.exists():
        print(f"warning: no checkpoint at {checkpoint} — using the untrained 3-class head")

    with Image.open(args.image) as image:
        image.load()
        mask, confidence = predict(model, image)
        reading = compute_choke_index(mask, confidence)
        point = extract_gps(image)
        when = extract_captured_at(image)
        if args.overlay:
            render_overlay(image, mask).save(args.overlay)
            print(f"overlay -> {args.overlay}")

    result = reading.to_dict()
    result["lat"] = point.lat if point else None
    result["lon"] = point.lon if point else None
    result["drain_id"] = point.drain_id if point else None
    result["captured_at"] = when
    print(json.dumps(result, indent=2))
    return 0


def cmd_geo_pull(args: argparse.Namespace) -> int:
    from drainwatch.geo.sources import (
        DTU_BBOX,
        download_mapillary,
        group_by_drain,
        search_mapillary,
    )

    bbox = tuple(args.bbox) if args.bbox else DTU_BBOX
    images = search_mapillary(bbox, limit=args.limit)
    print(f"{len(images)} captures in bbox {bbox}")

    grouped = group_by_drain(images)
    repeats = {k: v for k, v in grouped.items() if len(v) > 1}
    print(f"{len(grouped)} distinct locations, {len(repeats)} with repeat captures")
    if repeats:
        # This is the check that decides whether the trend view runs on real data.
        example, captures = next(iter(sorted(repeats.items(), key=lambda kv: -len(kv[1]))))
        dates = [c.captured_at for c in captures]
        print(f"  best: {example} with {len(captures)} captures: {dates[:5]}")
    else:
        print("  no repeat captures — demo history must be simulated and labelled as such")

    if args.download:
        out_dir = Path(args.out or RAW_DIR / "mapillary")
        paths = download_mapillary(images[: args.limit], out_dir)
        print(f"{len(paths)} images -> {out_dir}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("drainwatch.api.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="drainwatch", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    data = sub.add_parser("data", help="dataset download and preparation").add_subparsers(
        dest="data_command", required=True
    )

    p = data.add_parser("check", help="verify dataset URLs are reachable")
    p.set_defaults(func=cmd_data_check)

    p = data.add_parser("download", help="download raw datasets")
    p.add_argument(
        "--source", action="append", choices=["taco", "uavvaste", "ade"],
        help="repeatable; default is all",
    )
    p.add_argument("--image-limit", type=int, default=None, help="cap images per source")
    p.set_defaults(func=cmd_data_download)

    p = data.add_parser("build", help="merge sources into the 3-class dataset")
    p.add_argument("--limit", type=int, default=None, help="cap samples per source")
    p.add_argument("--val-frac", type=float, default=0.15)
    p.add_argument(
        "--composites",
        type=int,
        default=None,
        help="waste-in-water composites to synthesise (default 700; 0 disables). "
        "These are the only samples containing both classes the Choke Index uses.",
    )
    p.set_defaults(func=cmd_data_build)

    p = data.add_parser("preview", help="render overlays to eyeball the masks")
    p.add_argument("-n", type=int, default=20)
    p.set_defaults(func=cmd_data_preview)

    p = sub.add_parser("train", help="fine-tune SegFormer")
    # 12 rather than 20: measured ~20 min/epoch on CPU at 512px, and the ADE20K warm
    # start means the encoder already represents water and road. See the README.
    p.add_argument("--epochs", type=int, default=12)
    p.add_argument("--batch-size", type=int, default=4)
    p.add_argument("--lr", type=float, default=6e-5)
    p.add_argument("--num-workers", type=int, default=2)
    p.add_argument("--base-model", default=None, help="e.g. nvidia/segformer-b2-finetuned-ade-512-512")
    p.add_argument(
        "--device",
        default=None,
        help="mps, cuda or cpu (default: best available). Use 'cpu' if the MPS backward "
        "pass crashes with a 'view size is not compatible' error — slower but reliable.",
    )
    p.set_defaults(func=cmd_train)

    p = sub.add_parser("eval", help="per-class IoU on a split")
    p.add_argument("--checkpoint", default=str(CHECKPOINT_DIR / "best"))
    p.add_argument("--root", default=str(PROCESSED_DIR), help="use the Delhi set to report it separately")
    p.add_argument("--split", default="val")
    p.add_argument("--batch-size", type=int, default=4)
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("analyze", help="score a single image locally")
    p.add_argument("image")
    p.add_argument("--checkpoint", default=None)
    p.add_argument("--overlay", default=None, help="write the overlay PNG here")
    p.set_defaults(func=cmd_analyze)

    geo = sub.add_parser("geo", help="geotagged imagery").add_subparsers(
        dest="geo_command", required=True
    )
    p = geo.add_parser("pull", help="search (and optionally download) Mapillary imagery")
    p.add_argument("--bbox", type=float, nargs=4, metavar=("W", "S", "E", "N"), default=None)
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--download", action="store_true")
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_geo_pull)

    p = sub.add_parser("serve", help="run the API + dashboard")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
