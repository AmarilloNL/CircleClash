"""
ffmpeg_setup.py
===============
Auto-provision ffmpeg + ffprobe so users don't have to install them. Mirrors
danser_setup: we fetch a static build from BtbN/FFmpeg-Builds into the (portable)
app-data dir and hand back the paths.

ffmpeg is run as a separate process — CircleClash never links against it — so the
bundled binary stays under its own license (BtbN ships GPL builds) and does not
affect CircleClash's MIT license. The license text travels with the binary inside
the downloaded archive.

Asset naming (BtbN auto-builds, tag "latest"):
    ffmpeg-master-latest-win64-gpl.zip
    ffmpeg-master-latest-linux64-gpl.tar.xz
    ffmpeg-master-latest-linuxarm64-gpl.tar.xz

Public API:
    ffmpeg_dir() -> Path
    find_local_ffmpeg() -> tuple[Path, Path] | None   # (ffmpeg, ffprobe)
    install(progress=None) -> tuple[Path, Path]
    ensure(progress=None) -> tuple[Path, Path]

`progress` is an optional callback(fraction_0_to_1, message) for the GUI.
Pure standard library.
"""

from __future__ import annotations

import io
import os
import platform
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

from danser_setup import data_root  # shared, portable-aware app-data root

REPO = "BtbN/FFmpeg-Builds"
_TAG = "latest"
_UA = "circleclash-ffmpeg/1.0 (+local)"


# --------------------------------------------------------------------------- #
# Locations
# --------------------------------------------------------------------------- #
def ffmpeg_dir() -> Path:
    d = data_root() / "ffmpeg"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _bin_names() -> tuple[str, str]:
    if sys.platform == "win32":
        return "ffmpeg.exe", "ffprobe.exe"
    return "ffmpeg", "ffprobe"


def find_local_ffmpeg() -> tuple[Path, Path] | None:
    """Return (ffmpeg, ffprobe) we previously installed, if both are present."""
    ffm_name, ffp_name = _bin_names()
    root = ffmpeg_dir()
    ffm = next(iter(root.rglob(ffm_name)), None)
    ffp = next(iter(root.rglob(ffp_name)), None)
    if ffm and ffp:
        return ffm, ffp
    return None


# --------------------------------------------------------------------------- #
# Platform -> asset
# --------------------------------------------------------------------------- #
def _asset_name() -> str:
    if sys.platform == "win32":
        return "ffmpeg-master-latest-win64-gpl.zip"
    if sys.platform == "darwin":
        raise RuntimeError(
            "BtbN has no macOS ffmpeg build. Install ffmpeg yourself (e.g. `brew "
            "install ffmpeg`) and point CircleClash at it in Settings.")
    arch = platform.machine().lower()
    if arch in ("aarch64", "arm64"):
        return "ffmpeg-master-latest-linuxarm64-gpl.tar.xz"
    return "ffmpeg-master-latest-linux64-gpl.tar.xz"


# --------------------------------------------------------------------------- #
# Download + extract
# --------------------------------------------------------------------------- #
def _download(url: str, progress=None, label="") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=180) as r:
        total = int(r.headers.get("Content-Length", 0))
        buf = io.BytesIO()
        read = 0
        while True:
            chunk = r.read(262144)
            if not chunk:
                break
            buf.write(chunk)
            read += len(chunk)
            if progress and total:
                progress(read / total, f"{label} {read // 1048576}/{total // 1048576} MB")
    return buf.getvalue()


def _extract(name: str, data: bytes, dest: Path) -> None:
    if name.endswith(".zip"):
        if data[:2] != b"PK":
            raise RuntimeError("Downloaded ffmpeg asset wasn't a valid zip.")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(dest)
    else:  # .tar.xz
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as t:
            try:
                t.extractall(dest, filter="data")   # py3.12+: silence the deprecation
            except TypeError:
                t.extractall(dest)                  # older Python: no filter kwarg


def install(progress=None) -> tuple[Path, Path]:
    """Download the right ffmpeg build and extract it. Returns (ffmpeg, ffprobe)."""
    name = _asset_name()
    url = f"https://github.com/{REPO}/releases/download/{_TAG}/{name}"
    if progress:
        progress(0.02, "Fetching ffmpeg…")
    data = _download(url, progress=progress, label="downloading ffmpeg")

    dest = ffmpeg_dir()
    if progress:
        progress(0.95, "Extracting ffmpeg…")
    _extract(name, data, dest)

    got = find_local_ffmpeg()
    if not got:
        raise RuntimeError("ffmpeg extracted but the binaries weren't found.")
    ffm, ffp = got
    if sys.platform != "win32":
        for b in (ffm, ffp):
            try:
                os.chmod(b, 0o755)
            except Exception:
                pass
    if progress:
        progress(1.0, "ffmpeg ready")
    return ffm, ffp


def ensure(progress=None) -> tuple[Path, Path]:
    """Return installed (ffmpeg, ffprobe), downloading them if needed."""
    existing = find_local_ffmpeg()
    if existing:
        return existing
    return install(progress=progress)


# --------------------------------------------------------------------------- #
# CLI:  python ffmpeg_setup.py
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    def _p(frac, msg):
        bar = "#" * int(frac * 30)
        print(f"\r[{bar:<30}] {msg}        ", end="", flush=True)
    try:
        m, p = ensure(progress=_p)
        print(f"\nffmpeg ready at:  {m}\nffprobe ready at: {p}")
    except Exception as e:
        print(f"\nfailed: {e}")
        sys.exit(1)
