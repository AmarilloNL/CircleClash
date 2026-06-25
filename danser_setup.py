"""
danser_setup.py
===============
Auto-install danser-go so users don't have to download it separately. We do NOT
bundle danser (it's GPL-3.0; bundling would make our whole distribution GPL).
Instead we fetch the official release from danser's GitHub into the app-data dir
and configure the path — small download, no license entanglement, easy updates.

Asset naming (verified against the releases page):
    danser-<version>-linux.zip
    danser-<version>-win.zip
There is no official macOS build.

Public API:
    danser_dir() -> Path                      # where we keep danser
    find_local_danser() -> Path | None        # already-installed binary, if any
    latest_version() -> str                   # resolve newest tag (no API needed)
    install(progress=None) -> Path            # download+extract, return binary path
    ensure(progress=None) -> Path             # find or install

`progress` is an optional callback(fraction_0_to_1, message) for the GUI.
Pure standard library.
"""

from __future__ import annotations

import io
import os
import platform
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = "Wieku/danser-go"
_UA = "osu-replay-comparison/0.3 (+local)"


# --------------------------------------------------------------------------- #
# Locations
# --------------------------------------------------------------------------- #
def _data_root() -> Path:
    # Portable mode: keep everything (danser, ffmpeg, render-songs, config) in a
    # single "CircleClash-data" folder next to the app, so the whole tool lives in
    # one place the user controls. Active automatically for the packaged app, or
    # opt-in from source via CIRCLECLASH_PORTABLE=1 (or =/path/to/anchor). Falls
    # back to the per-user data dir if that spot isn't writable (e.g. Program Files).
    anchor: Path | None = None
    env = os.environ.get("CIRCLECLASH_PORTABLE")
    if env and env not in ("0", "1"):
        anchor = Path(env)
    elif env == "1":
        anchor = Path.cwd()
    elif getattr(sys, "frozen", False):
        anchor = Path(sys.executable).resolve().parent
    if anchor is not None:
        cand = anchor / "CircleClash-data"
        try:
            cand.mkdir(parents=True, exist_ok=True)
            t = cand / ".write-test"
            t.write_text("ok", encoding="utf-8")
            t.unlink()
            return cand
        except Exception:
            pass  # not writable -> fall through to the per-user data dir

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(base) / "osu-renderer"


def data_root() -> Path:
    """Public accessor for the shared app-data root (portable-aware)."""
    return _data_root()


def danser_dir() -> Path:
    d = _data_root() / "danser"
    d.mkdir(parents=True, exist_ok=True)
    return d


def render_songs_dir() -> Path:
    """Small folder danser actually reads — holds only maps needed for renders,
    so danser's database import is instant regardless of the user's library size."""
    d = _data_root() / "render-songs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _bin_name() -> str:
    # danser ships a CLI launcher; on Windows it's danser-cli.exe, else danser-cli
    return "danser-cli.exe" if sys.platform == "win32" else "danser-cli"


def find_local_danser() -> Path | None:
    """Return a usable danser-cli we previously installed, if present."""
    root = danser_dir()
    target = _bin_name()
    for p in root.rglob(target):
        if p.is_file():
            return p
    return None


# --------------------------------------------------------------------------- #
# Platform -> asset
# --------------------------------------------------------------------------- #
def _platform_tag() -> str:
    if sys.platform == "win32":
        return "win"
    if sys.platform == "darwin":
        raise RuntimeError(
            "danser-go has no official macOS build. macOS users need to build danser "
            "themselves or run under a compatibility layer; point CircleClash at it manually.")
    return "linux"


# --------------------------------------------------------------------------- #
# Version resolution (without the GitHub API, to dodge rate limits)
# --------------------------------------------------------------------------- #
def latest_version() -> str:
    """Resolve the newest release tag by following the /releases/latest redirect."""
    url = f"https://github.com/{REPO}/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    # don't auto-follow so we can read the redirect Location
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        opener.open(req, timeout=20)
        loc = ""
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
    m = re.search(r"/tag/([^/\s]+)", loc)
    if m:
        return m.group(1)
    # fallback: assume a known-good version
    return "0.11.0"


def _asset_url(version: str) -> str:
    tag = _platform_tag()
    name = f"danser-{version}-{tag}.zip"
    return f"https://github.com/{REPO}/releases/download/{version}/{name}", name


# --------------------------------------------------------------------------- #
# Download + extract
# --------------------------------------------------------------------------- #
def _download(url: str, progress=None, label="") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=120) as r:
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


def install(progress=None) -> Path:
    """Download the right danser build and extract it. Returns the binary path."""
    version = latest_version()
    url, name = _asset_url(version)
    if progress:
        progress(0.02, f"Fetching danser {version}…")
    data = _download(url, progress=progress, label="downloading danser")
    if not data[:2] == b"PK":
        raise RuntimeError("Downloaded danser asset wasn't a valid zip.")

    dest = danser_dir() / version
    dest.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(0.95, "Extracting danser…")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(dest)

    binp = find_local_danser()
    if not binp:
        raise RuntimeError("danser extracted but the danser-cli binary wasn't found.")
    if sys.platform != "win32":
        try:
            os.chmod(binp, 0o755)
            launcher = binp.with_name("danser")
            if launcher.exists():
                os.chmod(launcher, 0o755)
        except Exception:
            pass
    if progress:
        progress(1.0, f"danser {version} ready")
    return binp


def ensure(progress=None) -> Path:
    """Return an installed danser-cli, downloading it if needed."""
    existing = find_local_danser()
    if existing:
        return existing
    return install(progress=progress)


# --------------------------------------------------------------------------- #
# CLI:  python danser_setup.py
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    def _p(frac, msg):
        bar = "#" * int(frac * 30)
        print(f"\r[{bar:<30}] {msg}        ", end="", flush=True)
    try:
        path = ensure(progress=_p)
        print(f"\ndanser ready at: {path}")
    except Exception as e:
        print(f"\nfailed: {e}")
        sys.exit(1)
