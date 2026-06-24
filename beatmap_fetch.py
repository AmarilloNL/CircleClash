"""
beatmap_fetch.py
================
Ensure a replay's beatmap is present in danser's Songs folder so danser can
render it. Required for public use: most users won't already own the map.

The .osr stores only the beatmap's MD5 (which is the md5 of the .osu file). To
download we need the beatmapset id, then we pull the .osz from a mirror, extract
it into Songs, and VERIFY by hashing the extracted .osu files — confirming the
exact version the replay needs is there. If the set was updated since the replay
(its current md5 differs), we fetch that exact .osu by hash as a fallback.

danser imports new Songs subfolders on its next run even with -nodbcheck, so once
the folder is in place the render just works.

Pure standard library (urllib + zipfile + hashlib). No third-party deps.

NOTE: mirror endpoints below are best-effort and may need tweaking for the exact
current API shapes — they're isolated in MIRRORS/RESOLVERS for easy adjustment.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

_UA = "osu-replay-comparison/0.3 (+local)"

# Download mirrors, tried in order. {setid} is substituted. noVideo keeps it small.
DOWNLOAD_MIRRORS = [
    ("osu.direct", "https://osu.direct/api/d/{setid}?noVideo=1"),
    ("nerinyan",   "https://api.nerinyan.moe/d/{setid}?nv=1"),
    ("catboy.best", "https://catboy.best/d/{setid}"),
]

# md5 -> setid resolvers (only needed when the osu! API isn't available).
# Each: (name, url_template, json_path_to_setid)
MD5_RESOLVERS = [
    ("osu.direct", "https://osu.direct/api/v2/md5/{md5}"),
    ("catboy.best", "https://catboy.best/api/v2/md5/{md5}"),
]

# exact .osu by md5 (updated-map fallback)
OSU_BY_MD5 = "https://osu.direct/api/osu/{md5}"


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def _get(url: str, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _get_json(url: str, timeout: int = 30) -> dict:
    return json.loads(_get(url, timeout).decode())


def _safe_name(setid: int, artist: str, title: str) -> str:
    base = f"{setid} {artist} - {title}".strip().rstrip(" -")
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", base)
    return base[:120] or str(setid)


# --------------------------------------------------------------------------- #
# Resolve setid
# --------------------------------------------------------------------------- #
def resolve_setid(md5: str, api=None) -> tuple[int | None, str, str]:
    """(setid, artist, title). Prefers the osu! API; falls back to mirrors."""
    if api is not None:
        try:
            bm = api.lookup_beatmap_by_md5(md5)
            if bm:
                return bm.set_id, bm.artist, bm.title
        except Exception:
            pass
    for _name, tmpl in MD5_RESOLVERS:
        try:
            d = _get_json(tmpl.format(md5=md5))
            bset = d.get("beatmapset") or {}
            sid = d.get("beatmapset_id") or d.get("set_id") or bset.get("id")
            if sid:
                return int(sid), bset.get("artist", ""), bset.get("title", "")
        except Exception:
            continue
    return None, "", ""


# --------------------------------------------------------------------------- #
# Local presence + hashing
# --------------------------------------------------------------------------- #
def _folder_has_md5(folder: Path, md5: str) -> bool:
    md5 = md5.lower()
    for osu in folder.glob("*.osu"):
        try:
            if hashlib.md5(osu.read_bytes()).hexdigest() == md5:
                return True
        except Exception:
            continue
    return False


def _existing_set_folder(songs_dir: Path, setid: int) -> Path | None:
    pref = f"{setid} "
    for p in songs_dir.iterdir():
        if p.is_dir() and (p.name == str(setid) or p.name.startswith(pref)):
            return p
    return None


# --------------------------------------------------------------------------- #
# Download + extract
# --------------------------------------------------------------------------- #
def download_set(setid: int, songs_dir: Path, artist: str = "", title: str = "") -> Path | None:
    for name, tmpl in DOWNLOAD_MIRRORS:
        try:
            print(f"    downloading set {setid} from {name} ...")
            data = _get(tmpl.format(setid=setid))
            if not data or data[:2] != b"PK":      # not a zip (error page / empty)
                continue
            folder = songs_dir / _safe_name(setid, artist, title)
            folder.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                z.extractall(folder)
            return folder
        except Exception as e:
            print(f"    {name} failed: {e}")
            continue
    return None


def _patch_exact_osu(md5: str, folder: Path) -> bool:
    """Map was updated; fetch the exact .osu version by md5 and drop it in."""
    try:
        data = _get(OSU_BY_MD5.format(md5=md5))
        if data and b"osu file format" in data[:64]:
            (folder / f"{md5}.osu").write_bytes(data)
            return True
    except Exception:
        pass
    return False


# --------------------------------------------------------------------------- #
# Render-songs: a SMALL folder danser reads, holding only the maps we need.
# This keeps danser's database tiny so its import is instant — no multi-minute
# scan of the user's whole library on every run.
# --------------------------------------------------------------------------- #
def find_in_library(library: str | Path | None, set_id: int | None, md5: str) -> Path | None:
    """Look for the exact map in the user's existing osu! library (so we can reuse
    a map they already own instead of re-downloading)."""
    if not library:
        return None
    library = Path(library)
    if not library.is_dir():
        return None
    candidates = []
    if set_id:
        pref = f"{set_id} "
        for p in library.iterdir():
            if p.is_dir() and (p.name == str(set_id) or p.name.startswith(pref)):
                candidates.append(p)
    for p in candidates:
        if _folder_has_md5(p, md5):
            return p
    return None


def _link_or_copy(src: Path, dest_root: Path) -> Path:
    """Symlink the map folder into render-songs (instant, no disk use); fall back
    to a copy where symlinks aren't available (e.g. some Windows setups)."""
    dest = dest_root / src.name
    if dest.exists():
        return dest
    try:
        os.symlink(src, dest, target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        shutil.copytree(src, dest)
    return dest


def ensure_render_map(md5: str, render_songs: str | Path, *, set_id: int | None = None,
                      library: str | Path | None = None, api=None) -> tuple[str, Path | None]:
    """Make sure the map for `md5` is in the small render-songs folder danser reads.

    Order: already there -> reuse from the user's library (symlink) -> download.
    Returns (status, folder): present | linked | downloaded | downloaded_patched
    | version_mismatch | unresolved | download_failed
    """
    render = Path(render_songs)
    render.mkdir(parents=True, exist_ok=True)

    # already staged?
    for p in render.iterdir():
        if p.is_dir() and _folder_has_md5(p, md5):
            return ("present", p)

    artist = title = ""
    if set_id is None:
        set_id, artist, title = resolve_setid(md5, api)

    # reuse a copy the user already owns
    owned = find_in_library(library, set_id, md5)
    if owned:
        return ("linked", _link_or_copy(owned, render))

    # otherwise download into render-songs
    if set_id is None:
        return ("unresolved", None)
    folder = download_set(set_id, render, artist, title)
    if not folder:
        return ("download_failed", None)
    if _folder_has_md5(folder, md5):
        return ("downloaded", folder)
    if _patch_exact_osu(md5, folder):
        return ("downloaded_patched", folder)
    return ("version_mismatch", folder)


# --------------------------------------------------------------------------- #
# Public entry point (legacy: ensure straight into a Songs dir)
# --------------------------------------------------------------------------- #
def ensure_beatmap(md5: str, songs_dir: str | Path, *, set_id: int | None = None, api=None) -> tuple[str, Path | None]:
    """Make sure the beatmap for `md5` exists under songs_dir.

    Returns (status, folder) where status is one of:
      present | downloaded | downloaded_patched | version_mismatch
      | unresolved | download_failed
    """
    songs = Path(songs_dir)
    if not songs.is_dir():
        return ("unresolved", None)

    artist = title = ""
    if set_id is None:
        set_id, artist, title = resolve_setid(md5, api)
        if set_id is None:
            return ("unresolved", None)

    # already have this set with the exact version?
    existing = _existing_set_folder(songs, set_id)
    if existing and _folder_has_md5(existing, md5):
        return ("present", existing)

    folder = download_set(set_id, songs, artist, title)
    if not folder:
        return ("download_failed", None)

    if _folder_has_md5(folder, md5):
        return ("downloaded", folder)

    # the live set's md5 differs (updated map) — fetch the exact version
    if _patch_exact_osu(md5, folder):
        return ("downloaded_patched", folder)

    return ("version_mismatch", folder)


# --------------------------------------------------------------------------- #
# CLI:  python beatmap_fetch.py <songs_dir> <replay.osr> [<replay2.osr> ...]
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from osr_parser import parse_replay

    if len(sys.argv) < 3:
        print("usage: python beatmap_fetch.py <songs_dir> <replay.osr> [...]")
        raise SystemExit(1)

    songs_dir = sys.argv[1]
    api = None
    try:
        from osu_api import OsuAPI
        api = OsuAPI()
    except Exception:
        print("(no osu! API creds — using mirror md5 resolvers)")

    for osr in sys.argv[2:]:
        md5 = parse_replay(osr).beatmap_md5
        status, folder = ensure_beatmap(md5, songs_dir, api=api)
        print(f"{osr}: {status}" + (f"  -> {folder}" if folder else ""))
