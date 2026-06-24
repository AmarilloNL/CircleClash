"""
osu_api.py
==========
Minimal osu! API v2 client for the replay-comparison tool.

What it resolves for the overlay:
  - a player (by username from the .osr, or by id) -> avatar URL, global rank,
    country code, pp, canonical username
  - a beatmap (by the MD5 stored in the .osr) -> title, artist, difficulty name,
    star rating, cover art

Auth: OAuth2 "client credentials" grant. You register one OAuth application on
the osu! site and get a client_id + client_secret; this module trades those for
a short-lived token with `public` scope. No user login required.

  1. Go to  https://osu.ppy.sh/home/account/edit  ->  "OAuth" section
  2. "New OAuth Application". Name it anything; the callback URL is unused for
     client-credentials, so any valid URL (e.g. http://localhost) is fine.
  3. Copy the Client ID and Client Secret.
  4. Provide them either as environment variables:
         export OSU_CLIENT_ID=12345
         export OSU_CLIENT_SECRET=xxxxxxxx
     or in a JSON file next to this script named  osu_credentials.json :
         { "client_id": 12345, "client_secret": "xxxxxxxx" }

  *** Never commit osu_credentials.json. Add it to .gitignore. ***

Pure standard library. No third-party dependencies.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

TOKEN_URL = "https://osu.ppy.sh/oauth/token"
API_BASE = "https://osu.ppy.sh/api/v2"
_UA = "osu-replay-comparison-tool/0.1 (+local)"


# --------------------------------------------------------------------------- #
# Data shapes
# --------------------------------------------------------------------------- #
@dataclass
class OsuUser:
    id: int
    username: str
    avatar_url: str
    country_code: str        # e.g. "KR"
    country_name: str        # e.g. "South Korea"
    global_rank: int | None  # None if unranked / inactive
    pp: float | None

    @property
    def rank_display(self) -> str:
        return f"#{self.global_rank:,}" if self.global_rank else "—"


@dataclass
class OsuBeatmap:
    id: int
    set_id: int
    title: str
    artist: str
    version: str             # difficulty name, e.g. "[rollback]"
    star_rating: float       # nomod SR from the API
    creator: str
    cover_url: str
    list_url: str


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #
def _load_credentials() -> tuple[int, str]:
    cid = os.environ.get("OSU_CLIENT_ID")
    secret = os.environ.get("OSU_CLIENT_SECRET")
    if cid and secret:
        return int(cid), secret

    cred_file = Path(__file__).with_name("osu_credentials.json")
    if cred_file.exists():
        data = json.loads(cred_file.read_text())
        return int(data["client_id"]), str(data["client_secret"])

    raise RuntimeError(
        "No osu! API credentials found. Set OSU_CLIENT_ID and OSU_CLIENT_SECRET "
        "environment variables, or create osu_credentials.json next to osu_api.py. "
        "See the header of osu_api.py for how to register an OAuth app."
    )


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class OsuAPI:
    def __init__(self, client_id: int | None = None, client_secret: str | None = None):
        if client_id is None or client_secret is None:
            client_id, client_secret = _load_credentials()
        self._cid = client_id
        self._secret = client_secret
        self._token: str | None = None
        self._token_expiry: float = 0.0

    # ---- low-level HTTP --------------------------------------------------- #
    def _post_json(self, url: str, payload: dict) -> dict:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": _UA},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())

    def _get_json(self, path: str, params: dict | None = None) -> dict:
        url = f"{API_BASE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url, method="GET",
            headers={
                "Authorization": f"Bearer {self._get_token()}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": _UA,
            },
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())

    # ---- token ------------------------------------------------------------ #
    def _get_token(self) -> str:
        # refresh a minute before actual expiry
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        try:
            data = self._post_json(TOKEN_URL, {
                "client_id": self._cid,
                "client_secret": self._secret,
                "grant_type": "client_credentials",
                "scope": "public",
            })
        except urllib.error.HTTPError as e:
            if e.code in (400, 401):
                raise RuntimeError(
                    "osu! API rejected the credentials (HTTP "
                    f"{e.code}). Double-check OSU_CLIENT_ID / OSU_CLIENT_SECRET."
                ) from e
            raise
        self._token = data["access_token"]
        self._token_expiry = time.time() + int(data.get("expires_in", 3600))
        return self._token

    # ---- public lookups --------------------------------------------------- #
    def get_user(self, user: str | int, *, by: str = "username") -> OsuUser | None:
        """Resolve a user. `by` is 'username' or 'id'.

        Note: the .osr stores the username as it was AT PLAY TIME. If the player
        later renamed, a username lookup may 404 (or hit a different account).
        In that case, pass the numeric id with by='id' as an override.
        """
        params = {"key": by} if by in ("username", "id") else None
        try:
            d = self._get_json(f"/users/{urllib.parse.quote(str(user))}/osu", params)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

        stats = d.get("statistics") or {}
        country = d.get("country") or {}
        return OsuUser(
            id=d["id"],
            username=d["username"],
            avatar_url=d.get("avatar_url", ""),
            country_code=d.get("country_code", country.get("code", "")),
            country_name=country.get("name", ""),
            global_rank=stats.get("global_rank"),
            pp=stats.get("pp"),
        )

    def lookup_beatmap_by_md5(self, md5: str) -> OsuBeatmap | None:
        """Resolve a beatmap from the MD5 checksum stored in a .osr."""
        try:
            d = self._get_json("/beatmaps/lookup", {"checksum": md5})
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

        bset = d.get("beatmapset") or {}
        covers = bset.get("covers") or {}
        return OsuBeatmap(
            id=d["id"],
            set_id=d.get("beatmapset_id", bset.get("id", 0)),
            title=bset.get("title", ""),
            artist=bset.get("artist", ""),
            version=d.get("version", ""),
            star_rating=float(d.get("difficulty_rating", 0.0)),
            creator=bset.get("creator", ""),
            cover_url=covers.get("cover@2x", covers.get("cover", "")),
            list_url=covers.get("list@2x", covers.get("list", "")),
        )

    # ---- asset download (no auth; public CDN) ----------------------------- #
    @staticmethod
    def download(url: str, dest: Path) -> Path | None:
        """Download a public asset (avatar, cover) to `dest`. Returns the path,
        or None if the URL was empty / failed."""
        if not url:
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                dest.write_bytes(r.read())
            return dest
        except Exception:
            return None


# --------------------------------------------------------------------------- #
# CLI:  python osu_api.py Amarillo Yiul
#       python osu_api.py <32-char-md5>
# --------------------------------------------------------------------------- #
def _looks_like_md5(s: str) -> bool:
    return len(s) == 32 and all(c in "0123456789abcdefABCDEF" for c in s)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("usage:")
        print("  python osu_api.py <username> [<username> ...]   # resolve players")
        print("  python osu_api.py <beatmap_md5>                 # resolve a map")
        raise SystemExit(1)

    api = OsuAPI()

    for arg in sys.argv[1:]:
        if _looks_like_md5(arg):
            bm = api.lookup_beatmap_by_md5(arg)
            if bm:
                print(f"MAP  {bm.artist} - {bm.title} [{bm.version}]")
                print(f"     ★{bm.star_rating:.2f}  by {bm.creator}  (id {bm.id})")
                print(f"     cover: {bm.cover_url}")
            else:
                print(f"MAP  {arg}: not found")
        else:
            u = api.get_user(arg)
            if u:
                print(f"USER {u.username}  {u.country_code} ({u.country_name})  "
                      f"{u.rank_display}  {u.pp:.0f}pp" if u.pp else
                      f"USER {u.username}  {u.country_code}  {u.rank_display}")
                print(f"     id {u.id}  avatar {u.avatar_url}")
            else:
                print(f"USER {arg}: not found (renamed? try the numeric id)")
