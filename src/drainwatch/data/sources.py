"""Dataset sources and download helpers.

DrainWatch trains on public mask datasets so that no training set has to be hand
annotated — the single biggest time sink for a solo build. Three sources:

======================  ==================  =========================================
Source                  Supplies            Notes
======================  ==================  =========================================
TACO                    ``waste`` masks     ~1.5k litter-in-the-wild images, COCO
                                            polygons. Images live on Flickr and are
                                            fetched per-record from the annotations.
UAVVaste                ``waste`` masks     ~770 drone litter images, COCO polygons.
ADE20K (SceneParse150)  ``water`` masks     Water/river/sea classes, remapped.
======================  ==================  =========================================

VERIFY BEFORE RELYING ON THESE URLS. They were written without network access; run
``drainwatch data check`` to confirm each resolves, and read each project's licence
before redistributing anything. Fallbacks if a source moves: ZeroWaste, Roboflow
Universe litter/flood datasets, Open Images ``Waste``.

Downloads are resumable: anything already on disk is skipped, so an interrupted run
can simply be repeated.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from drainwatch.config import RAW_DIR

USER_AGENT = "DrainWatch/0.1 (hackathon project; dataset fetch)"
TIMEOUT = 60


@dataclass(frozen=True)
class Source:
    """A remote artifact needed for dataset construction."""

    name: str
    url: str
    dest: Path
    note: str = ""
    fallback_urls: tuple[str, ...] = ()

    def candidate_urls(self) -> tuple[str, ...]:
        return (self.url, *self.fallback_urls)


SOURCES: dict[str, Source] = {
    "taco_annotations": Source(
        name="TACO annotations",
        url="https://raw.githubusercontent.com/pedropro/TACO/master/data/annotations.json",
        dest=RAW_DIR / "taco" / "annotations.json",
        note="COCO polygons, 60 litter categories -> collapsed to `waste`.",
    ),
    "uavvaste_annotations": Source(
        name="UAVVaste annotations",
        url="https://raw.githubusercontent.com/PUTvision/UAVVaste/main/annotations/annotations.json",
        dest=RAW_DIR / "uavvaste" / "annotations.json",
        note="COCO polygons, drone imagery.",
    ),
    "ade20k": Source(
        name="ADE20K SceneParse150",
        # https, with a plain-http fallback tried automatically: MIT's server has served
        # this over http historically, but a filtering proxy or a captive network will
        # refuse a plaintext 1 GB transfer, and "unreachable" would then look like a dead
        # URL rather than a blocked scheme.
        url="https://data.csail.mit.edu/places/ADEchallenge/ADEChallengeData2016.zip",
        dest=RAW_DIR / "ADEChallengeData2016.zip",
        note="~1 GB. Supplies `water` masks. Check the MIT terms of use.",
        fallback_urls=("http://data.csail.mit.edu/places/ADEchallenge/ADEChallengeData2016.zip",),
    ),
}


@dataclass
class DownloadReport:
    """Outcome of a batch download, so partial failure is visible rather than silent."""

    fetched: int = 0
    skipped: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)

    def merge(self, other: DownloadReport, *, prefix: str = "") -> None:
        self.fetched += other.fetched
        self.skipped += other.skipped
        self.failed.extend((f"{prefix}{name}", why) for name, why in other.failed)

    def summary(self) -> str:
        parts = [f"{self.fetched} fetched", f"{self.skipped} already present"]
        if self.failed:
            parts.append(f"{len(self.failed)} FAILED")
        lines = [", ".join(parts)]
        for name, why in self.failed[:10]:
            lines.append(f"  FAIL {name}: {why}")
        if len(self.failed) > 10:
            lines.append(f"  ... and {len(self.failed) - 10} more")
        return "\n".join(lines)



def download_file(url: str, dest: Path, *, timeout: int = TIMEOUT) -> bool:
    """Fetch ``url`` to ``dest``. Returns False if it was already there.

    Writes to a ``.part`` file and renames on success, so an interrupted download
    never leaves a truncated file that a later run would mistake for complete.
    """
    if dest.exists() and dest.stat().st_size > 0:
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    with urllib.request.urlopen(request, timeout=timeout) as response, tmp.open("wb") as fh:
        while chunk := response.read(1 << 16):
            fh.write(chunk)

    tmp.rename(dest)
    return True


def fetch_source(key: str) -> bool:
    """Download one entry from :data:`SOURCES`, trying its fallback URLs in order.

    Raises the *last* error if every candidate fails, so the message the caller reports
    describes a real attempt rather than a scheme that was never going to work.
    """
    source = SOURCES[key]
    last: Exception | None = None
    for url in source.candidate_urls():
        try:
            return download_file(url, source.dest)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last = exc
    raise last if last else RuntimeError(f"no candidate URLs for {key}")


def download_coco_images(
    annotations_path: Path,
    dest_dir: Path,
    *,
    limit: int | None = None,
    url_fields: tuple[str, ...] = ("flickr_640_url", "flickr_url", "coco_url", "url"),
) -> DownloadReport:
    """Fetch the images listed in a COCO annotations file.

    TACO and UAVVaste both ship annotations without pixels; each image record carries a
    URL. ``url_fields`` is tried in order, which is why ``flickr_640_url`` comes first —
    the 640px variant is far faster to pull and still above the 512px training size.

    A record with no usable URL is recorded as a failure rather than skipped silently:
    UAVVaste in particular may require its own downloader, and that needs to be obvious
    from the report instead of showing up later as a mysteriously tiny dataset.

    Args:
        annotations_path: COCO ``annotations.json``.
        dest_dir: root for downloaded images; ``file_name`` paths are preserved.
        limit: stop after this many image records (useful for a smoke test).
        url_fields: record keys to try, in priority order.

    Returns:
        A :class:`DownloadReport`. Individual failures are collected, not raised — a
        handful of dead links should not abort a 1,500-image pull.
    """
    with annotations_path.open() as fh:
        records = json.load(fh)["images"]

    report = DownloadReport()
    for record in records[:limit]:
        url = next((record[f] for f in url_fields if record.get(f)), None)
        if not url:
            report.failed.append(
                (record.get("file_name", "?"), f"no URL field in record (tried {url_fields})")
            )
            continue

        dest = dest_dir / record["file_name"]
        try:
            if download_file(url, dest):
                report.fetched += 1
            else:
                report.skipped += 1
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            report.failed.append((record["file_name"], str(exc)))

    return report


def extract_zip(zip_path: Path, dest_dir: Path, *, expect_dir: Path | None = None) -> bool:
    """Extract ``zip_path`` into ``dest_dir``. Returns False if already extracted."""
    import zipfile

    if expect_dir is not None and expect_dir.is_dir():
        return False
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(dest_dir)
    return True


def download_all(
    raw_dir: Path = RAW_DIR,
    *,
    sources: list[str] | None = None,
    image_limit: int | None = None,
) -> DownloadReport:
    """Download and unpack every requested source.

    One source failing does not stop the others: the dataset builder tolerates missing
    sources, so a partial download still yields something trainable.

    Args:
        raw_dir: destination root (``data/raw``).
        sources: subset of ``{"taco", "uavvaste", "ade"}``; default is all three.
        image_limit: cap images fetched per source.
    """
    wanted = set(sources or ["taco", "uavvaste", "ade"])
    report = DownloadReport()

    # ADE20K first, deliberately. It is one bulk transfer, and it is the *only* source of
    # the `water` class as well as the substrate every composite is pasted onto — nothing
    # downstream can start without it. The litter sources are thousands of small
    # per-image fetches at the mercy of Flickr, and UAVVaste in particular publishes no
    # 640px variant, so every one of its 772 records pulls a 3840x2160 original. Running
    # that first would leave the critical path waiting hours on the least important data.
    if "ade" in wanted:
        ade_dir = raw_dir / "ADEChallengeData2016"
        if ade_dir.is_dir():
            print("[ade] already extracted")
            report.skipped += 1
        else:
            print("[ade] downloading (~1 GB)...")
            try:
                report.fetched += fetch_source("ade20k")
                print("[ade] extracting...")
                extract_zip(SOURCES["ade20k"].dest, raw_dir, expect_dir=ade_dir)
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                report.failed.append(("ade/ADEChallengeData2016.zip", str(exc)))

    coco_sources = [
        ("taco", "taco_annotations", raw_dir / "taco"),
        ("uavvaste", "uavvaste_annotations", raw_dir / "uavvaste"),
    ]
    for name, key, root in coco_sources:
        if name not in wanted:
            continue
        print(f"[{name}] annotations...")
        try:
            report.fetched += fetch_source(key)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            report.failed.append((f"{name}/annotations.json", str(exc)))
            continue

        print(f"[{name}] images...")
        sub = download_coco_images(
            SOURCES[key].dest, root / "images", limit=image_limit
        )
        report.merge(sub, prefix=f"{name}/")
        print(f"[{name}] {sub.summary().splitlines()[0]}")

        # TACO ships annotations but not pixels — every image is a Flickr link, and a
        # dataset published in 2020 has had years for those to rot. A quiet 40% loss
        # leaves a dataset that trains fine and generalises badly, with no error anywhere
        # to explain why. So the rate gets stated out loud, at the moment it is knowable.
        attempted = sub.fetched + sub.skipped + len(sub.failed)
        if attempted and len(sub.failed) / attempted > 0.15:
            pct = 100 * len(sub.failed) / attempted
            print(
                f"[{name}] WARNING: {pct:.0f}% of images failed to download "
                f"({len(sub.failed)}/{attempted}). Dead upstream links are the usual "
                f"cause. Check `data build` class counts before training — if `waste` is "
                f"thin, lean harder on UAVVaste or add a fallback source."
            )

    return report


def _probe(url: str, *, timeout: int = 20) -> tuple[bool, str]:
    """Is ``url`` fetchable? Tries HEAD, then a one-byte ranged GET.

    The second attempt is the point. Plenty of servers — S3-backed buckets, CDNs, and
    MIT's dataset host among them — answer HEAD with 403 or 405 while GET works fine.
    A HEAD-only check reports those as dead, and a false "URL needs updating" costs an
    afternoon of hunting for a replacement dataset that was never needed. ``Range:
    bytes=0-0`` asks for one byte, so the fallback is nearly free even for a 1 GB zip.
    """
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            size = response.headers.get("Content-Length")
            suffix = f" ({int(size) / 1e6:.0f} MB)" if size else ""
            return True, f"HTTP {response.status}{suffix}  {url}"
    except urllib.error.HTTPError as exc:
        if exc.code not in (403, 405, 400, 501):
            return False, f"HTTP {exc.code} — URL needs updating: {url}"
        head_problem = f"HEAD {exc.code}"
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return False, f"unreachable: {exc}"

    ranged = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"}
    )
    try:
        with urllib.request.urlopen(ranged, timeout=timeout) as response:
            total = response.headers.get("Content-Range", "").rpartition("/")[2]
            suffix = f" ({int(total) / 1e6:.0f} MB)" if total.isdigit() else ""
            return True, f"HTTP {response.status} via ranged GET ({head_problem}){suffix}  {url}"
    except urllib.error.HTTPError as exc:
        return False, f"{head_problem}, GET {exc.code} — URL needs updating: {url}"
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return False, f"{head_problem}, then unreachable: {exc}"


def check_sources() -> dict[str, tuple[bool, str]]:
    """Probe every source URL. Returns ``{key: (ok, detail)}``.

    Run this first — the URLs in :data:`SOURCES` were written without network access and
    are unverified. A boolean rather than a formatted string so the CLI can set an exit
    code without parsing prose. Fallback URLs are tried in order, so a source is only
    reported dead when every candidate fails.
    """
    results: dict[str, tuple[bool, str]] = {}
    for key, source in SOURCES.items():
        if source.dest.exists():
            results[key] = (True, f"present on disk ({source.dest})")
            continue

        attempts = []
        for url in source.candidate_urls():
            ok, detail = _probe(url)
            if ok:
                results[key] = (True, detail)
                break
            attempts.append(detail)
        else:
            results[key] = (False, "; ".join(attempts))
    return results

