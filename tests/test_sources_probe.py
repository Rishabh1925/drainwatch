"""Tests for dataset source probing, with the network faked.

The logic under test exists for a situation that cannot be reproduced on demand: a server
that rejects HEAD but serves GET. Since `drainwatch data check` is the gate the whole data
pipeline sits behind, a false "URL needs updating" there sends you hunting for a
replacement dataset that was never needed — and a false "fine" lets a dead URL through to
a 1 GB download that fails at the end. Both are worth a fake urlopen.
"""

from __future__ import annotations

import urllib.error
from pathlib import Path

import pytest

from drainwatch.data import sources


class FakeResponse:
    """Minimal stand-in for the context manager urlopen returns."""

    def __init__(self, status: int = 200, headers: dict | None = None, body: bytes = b"x"):
        self.status = status
        self.headers = headers or {}
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, _size: int = -1) -> bytes:
        body, self._body = self._body, b""
        return body


def install(monkeypatch, handler):
    """Route urlopen through ``handler(url, method, headers)``."""

    def fake_urlopen(request, timeout=None):  # noqa: ARG001
        return handler(request.full_url, request.get_method(), dict(request.headers))

    monkeypatch.setattr(sources.urllib.request, "urlopen", fake_urlopen)


# --- the HEAD / ranged-GET fallback ---------------------------------------------


def test_head_success_is_enough(monkeypatch):
    calls = []

    def handler(url, method, headers):  # noqa: ARG001
        calls.append(method)
        return FakeResponse(200, {"Content-Length": "1048576000"})

    install(monkeypatch, handler)

    ok, detail = sources._probe("https://example.invalid/data.zip")

    assert ok
    assert calls == ["HEAD"], "a working HEAD must not be followed by a second request"
    assert "1049 MB" in detail


@pytest.mark.parametrize("code", [403, 405, 400, 501])
def test_head_rejection_falls_back_to_a_ranged_get(monkeypatch, code):
    """The case this function exists for. S3-backed hosts and MIT's dataset server both
    do this: HEAD is refused, GET is fine."""
    seen = []

    def handler(url, method, headers):
        seen.append((method, headers.get("Range")))
        if method == "HEAD":
            raise urllib.error.HTTPError(url, code, "nope", {}, None)
        return FakeResponse(206, {"Content-Range": "bytes 0-0/734003200"})

    install(monkeypatch, handler)

    ok, detail = sources._probe("https://example.invalid/data.zip")

    assert ok, f"HEAD {code} must not be treated as a dead URL"
    assert seen[1] == ("GET", "bytes=0-0"), "the fallback must ask for one byte, not the file"
    assert "734 MB" in detail  # size recovered from Content-Range, not Content-Length
    assert f"HEAD {code}" in detail  # the quirk stays visible in the report


def test_a_real_404_is_still_a_failure(monkeypatch):
    """The fallback must not paper over a genuinely dead URL."""

    def handler(url, method, headers):  # noqa: ARG001
        raise urllib.error.HTTPError(url, 404, "gone", {}, None)

    install(monkeypatch, handler)

    ok, detail = sources._probe("https://example.invalid/moved.json")

    assert not ok
    assert "404" in detail
    assert "needs updating" in detail


def test_head_ok_but_get_also_dead_is_a_failure(monkeypatch):
    def handler(url, method, headers):  # noqa: ARG001
        code = 403 if method == "HEAD" else 404
        raise urllib.error.HTTPError(url, code, "nope", {}, None)

    install(monkeypatch, handler)

    ok, detail = sources._probe("https://example.invalid/data.zip")

    assert not ok
    assert "GET 404" in detail


def test_connection_error_reports_unreachable(monkeypatch):
    def handler(url, method, headers):  # noqa: ARG001
        raise urllib.error.URLError("no route to host")

    install(monkeypatch, handler)

    ok, detail = sources._probe("https://example.invalid/data.zip")

    assert not ok
    assert "unreachable" in detail


# --- fallback URLs on a Source --------------------------------------------------


def test_ade20k_has_an_http_fallback_for_its_https_url():
    """MIT has served this over plain http historically, and a filtering proxy will
    refuse that — so both schemes are tried before the source is called dead."""
    candidates = sources.SOURCES["ade20k"].candidate_urls()

    assert len(candidates) == 2
    assert candidates[0].startswith("https://"), "https must be preferred"
    assert candidates[1].startswith("http://")


def test_check_sources_tries_fallbacks_before_reporting_dead(monkeypatch, tmp_path):
    """A source is only dead when *every* candidate fails."""
    attempted = []

    def handler(url, method, headers):  # noqa: ARG001
        attempted.append(url)
        if url.startswith("https://"):
            raise urllib.error.URLError("tls refused by proxy")
        return FakeResponse(200, {"Content-Length": "100"})

    install(monkeypatch, handler)
    # Replace the whole registry: check_sources() walks every entry, so patching one key
    # would still send real requests for the other two.
    monkeypatch.setattr(
        sources,
        "SOURCES",
        {
            "ade20k": sources.Source(
                name="t",
                url="https://example.invalid/a.zip",
                dest=tmp_path / "absent.zip",
                fallback_urls=("http://example.invalid/a.zip",),
            )
        },
    )

    ok, detail = sources.check_sources()["ade20k"]

    assert ok, "the http fallback succeeded, so the source is not dead"
    assert attempted == ["https://example.invalid/a.zip", "http://example.invalid/a.zip"]
    assert "http://" in detail


def test_a_source_already_on_disk_is_not_probed(monkeypatch, tmp_path):
    """Re-running `data check` after a 1 GB download must not re-request anything."""

    def handler(url, method, headers):  # noqa: ARG001
        pytest.fail("a present file must short-circuit before any network call")

    install(monkeypatch, handler)
    present = tmp_path / "ADEChallengeData2016.zip"
    present.write_bytes(b"pretend this is a gigabyte")
    monkeypatch.setattr(
        sources,
        "SOURCES",
        {"ade20k": sources.Source(name="t", url="https://example.invalid/a.zip", dest=present)},
    )

    ok, detail = sources.check_sources()["ade20k"]

    assert ok
    assert "present on disk" in detail


# --- fetch_source over multiple candidates --------------------------------------


def test_fetch_source_falls_through_to_a_working_url(monkeypatch, tmp_path):
    def handler(url, method, headers):  # noqa: ARG001
        if url.startswith("https://"):
            raise urllib.error.URLError("tls refused by proxy")
        return FakeResponse(200, body=b"payload")

    install(monkeypatch, handler)
    dest = tmp_path / "out" / "a.zip"
    monkeypatch.setitem(
        sources.SOURCES,
        "ade20k",
        sources.Source(
            name="t",
            url="https://example.invalid/a.zip",
            dest=dest,
            fallback_urls=("http://example.invalid/a.zip",),
        ),
    )

    assert sources.fetch_source("ade20k") is True
    assert dest.read_bytes() == b"payload"
    assert not list(dest.parent.glob("*.part")), "the .part file must be renamed away"


def test_fetch_source_raises_the_last_error_when_all_candidates_fail(monkeypatch, tmp_path):
    def handler(url, method, headers):  # noqa: ARG001
        raise urllib.error.URLError(f"dead: {url}")

    install(monkeypatch, handler)
    monkeypatch.setitem(
        sources.SOURCES,
        "ade20k",
        sources.Source(
            name="t",
            url="https://example.invalid/a.zip",
            dest=tmp_path / "a.zip",
            fallback_urls=("http://example.invalid/a.zip",),
        ),
    )

    with pytest.raises(urllib.error.URLError, match="http://"):
        sources.fetch_source("ade20k")


def test_download_file_skips_what_is_already_there(monkeypatch, tmp_path):
    """Downloads are resumable by being idempotent, which is what makes an interrupted
    1 GB pull safe to simply re-run."""

    def handler(url, method, headers):  # noqa: ARG001
        pytest.fail("an existing non-empty file must not be re-fetched")

    install(monkeypatch, handler)
    dest: Path = tmp_path / "a.zip"
    dest.write_bytes(b"already here")

    assert sources.download_file("https://example.invalid/a.zip", dest) is False
