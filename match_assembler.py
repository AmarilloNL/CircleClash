"""
match_assembler.py
==================
The join layer. Turns two .osr files into one clean `MatchData` object that the
overlay compositor can consume without touching replay internals or the API.

For each match it:
  - parses both replays      (osr_parser)  -> names, mods, acc, score, combo, hits
  - enriches each player      (osu_api)    -> id, rank, country, pp, avatar
  - resolves the beatmap      (osu_api)    -> title, artist, diff, SR, cover
  - downloads avatars + cover into a local cache (so re-runs are instant)
  - computes the osu! letter grade for each play (the .osr doesn't store it)
  - degrades gracefully: renamed player -> pass an id override; map not found ->
    fields left blank with resolved=False, so the compositor can fall back.

Panel assignment follows argument order: first replay = LEFT (pink),
second = RIGHT (ice), matching sync_poc.py.

Depends on: osr_parser.py, osu_api.py  (same folder). Pure stdlib otherwise.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path

from osr_parser import Mods, ReplayInfo, parse_replay
from osu_api import OsuAPI, OsuUser, OsuBeatmap

# left/right accent colors (matches the Neon×Triangles design)
ACCENT = {"left": "#ff66ab", "right": "#66d9ff"}


# --------------------------------------------------------------------------- #
# Grade (osu!standard ranking rules — the .osr stores counts, not the letter)
# --------------------------------------------------------------------------- #
def compute_grade(n300: int, n100: int, n50: int, nmiss: int, mods: Mods) -> tuple[str, bool]:
    """Return (grade_letter, is_silver). Silver = HD/FL on an S or SS."""
    total = n300 + n100 + n50 + nmiss
    if total == 0:
        return ("D", False)
    r300 = n300 / total
    r50 = n50 / total
    silver = bool(mods & (Mods.HIDDEN | Mods.FLASHLIGHT))

    if n100 == 0 and n50 == 0 and nmiss == 0:
        return ("SS", silver)
    if r300 > 0.90 and r50 < 0.01 and nmiss == 0:
        return ("S", silver)
    if (r300 > 0.80 and nmiss == 0) or r300 > 0.90:
        return ("A", False)
    if (r300 > 0.70 and nmiss == 0) or r300 > 0.80:
        return ("B", False)
    if r300 > 0.60:
        return ("C", False)
    return ("D", False)


# --------------------------------------------------------------------------- #
# Data shapes (everything the overlay needs, nothing it doesn't)
# --------------------------------------------------------------------------- #
@dataclass
class PlayerData:
    side: str                 # "left" | "right"
    accent: str               # hex accent for this side

    # from the replay
    play_username: str        # username as stored in the .osr (play-time)
    mods: str                 # e.g. "HDHR" / "NM"
    accuracy: float           # percent, e.g. 91.19
    score: int
    max_combo: int
    perfect: bool
    n300: int
    n100: int
    n50: int
    nmiss: int
    grade: str                # "SS"|"S"|"A"|"B"|"C"|"D"
    silver: bool

    # from the API (may be unresolved)
    resolved: bool = False
    user_id: int | None = None
    username: str | None = None       # canonical (current) username
    global_rank: int | None = None
    rank_display: str = "—"
    country_code: str = ""
    country_name: str = ""
    pp: float | None = None
    avatar_url: str = ""
    avatar_path: str | None = None    # local cached file

    @property
    def display_name(self) -> str:
        return self.username or self.play_username


@dataclass
class MapData:
    md5: str
    resolved: bool = False
    beatmap_id: int | None = None
    set_id: int | None = None
    title: str = ""
    artist: str = ""
    version: str = ""                 # difficulty name
    star_rating_api: float = 0.0      # nomod SR from the API
    star_rating_danser: float | None = None  # per-play SR if captured at render
    creator: str = ""
    cover_url: str = ""
    cover_path: str | None = None

    @property
    def star_rating(self) -> float:
        """Prefer danser's per-play SR (honest to what's on screen) when present."""
        return self.star_rating_danser if self.star_rating_danser is not None else self.star_rating_api


@dataclass
class MatchData:
    left: PlayerData
    right: PlayerData
    map: MapData
    match_title: str = "friendly · bo1"

    @property
    def winner_side(self) -> str:
        return "left" if self.left.score >= self.right.score else "right"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["winner_side"] = self.winner_side
        d["map"]["star_rating"] = self.map.star_rating
        d["left"]["display_name"] = self.left.display_name
        d["right"]["display_name"] = self.right.display_name
        return d


# --------------------------------------------------------------------------- #
# Build a PlayerData from a parsed replay + API enrichment
# --------------------------------------------------------------------------- #
def _build_player(
    side: str,
    info: ReplayInfo,
    api: OsuAPI | None,
    cache: Path,
    id_override: int | None,
) -> PlayerData:
    grade, silver = compute_grade(info.count_300, info.count_100, info.count_50, info.count_miss, info.mods)

    p = PlayerData(
        side=side,
        accent=ACCENT[side],
        play_username=info.player,
        mods=info.mods_str,
        accuracy=round(info.accuracy_pct, 2),
        score=info.score,
        max_combo=info.max_combo,
        perfect=info.perfect,
        n300=info.count_300,
        n100=info.count_100,
        n50=info.count_50,
        nmiss=info.count_miss,
        grade=grade,
        silver=silver,
    )

    # resolve: prefer explicit id override (handles renamed players), else username
    user: OsuUser | None = None
    if api is not None:
        if id_override is not None:
            user = api.get_user(id_override, by="id")
        if user is None:
            user = api.get_user(info.player, by="username")

    if user:
        p.resolved = True
        p.user_id = user.id
        p.username = user.username
        p.global_rank = user.global_rank
        p.rank_display = user.rank_display
        p.country_code = user.country_code
        p.country_name = user.country_name
        p.pp = user.pp
        p.avatar_url = user.avatar_url
        avatar_dest = cache / f"avatar_{user.id}.img"
        got = OsuAPI.download(user.avatar_url, avatar_dest)
        p.avatar_path = str(got) if got else None

    return p


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def assemble_match(
    left_osr: str,
    right_osr: str,
    *,
    api: OsuAPI | None = None,
    cache_dir: str = "cache",
    left_id: int | None = None,
    right_id: int | None = None,
    match_title: str = "friendly · bo1",
) -> MatchData:
    if api is None:
        try:
            api = OsuAPI()
        except Exception:
            print("  ! No osu! API key set — continuing without avatars, ranks, flags or pp. "
                  "(Add credentials in Settings to enable them.)", flush=True)
            api = None
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)

    left_info = parse_replay(left_osr)
    right_info = parse_replay(right_osr)

    if left_info.beatmap_md5 != right_info.beatmap_md5:
        raise ValueError("The two replays are on different beatmaps.")

    left = _build_player("left", left_info, api, cache, left_id)
    right = _build_player("right", right_info, api, cache, right_id)

    # map
    md5 = left_info.beatmap_md5
    bm: OsuBeatmap | None = api.lookup_beatmap_by_md5(md5) if api is not None else None
    mp = MapData(md5=md5)
    if bm:
        mp.resolved = True
        mp.beatmap_id = bm.id
        mp.set_id = bm.set_id
        mp.title = bm.title
        mp.artist = bm.artist
        mp.version = bm.version
        mp.star_rating_api = bm.star_rating
        mp.creator = bm.creator
        mp.cover_url = bm.cover_url
        cover_dest = cache / f"cover_{bm.set_id}.jpg"
        got = OsuAPI.download(bm.cover_url, cover_dest)
        mp.cover_path = str(got) if got else None

    return MatchData(left=left, right=right, map=mp, match_title=match_title)


# --------------------------------------------------------------------------- #
# CLI:  python match_assembler.py left.osr right.osr [--left-id N] [--right-id N]
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Assemble a MatchData object from two replays")
    ap.add_argument("left", help="left player's .osr")
    ap.add_argument("right", help="right player's .osr")
    ap.add_argument("--left-id", type=int, default=None, help="force left player's osu! id (if renamed)")
    ap.add_argument("--right-id", type=int, default=None, help="force right player's osu! id (if renamed)")
    ap.add_argument("--title", default="friendly · bo1", help="match title shown in the overlay")
    ap.add_argument("--json", default="match.json", help="where to write the assembled data")
    args = ap.parse_args()

    match = assemble_match(
        args.left, args.right,
        left_id=args.left_id, right_id=args.right_id,
        match_title=args.title,
    )

    def line(p: PlayerData) -> None:
        flag = p.country_code or "??"
        tail = f"{p.pp:.0f}pp" if p.pp else "unranked"
        status = "" if p.resolved else "  [UNRESOLVED — try --{side}-id]".format(side=p.side)
        print(f"  {p.side:>5}: {p.display_name:<18} {flag}  {p.rank_display:<10} "
              f"{p.grade:<2} {p.accuracy:6.2f}%  {p.max_combo}x  {p.mods:<6} {tail}{status}")

    print(f"\nMAP : {match.map.artist} - {match.map.title} [{match.map.version}]  ★{match.map.star_rating:.2f}")
    if not match.map.resolved:
        print("      [map UNRESOLVED — not on osu! / not submitted]")
    print("PLAYERS:")
    line(match.left)
    line(match.right)
    print(f"WINNER: {match.winner_side} ({(match.left if match.winner_side=='left' else match.right).display_name})")

    Path(args.json).write_text(json.dumps(match.to_dict(), indent=2, ensure_ascii=False))
    print(f"\nWrote {args.json}  (+ cached avatars/cover in ./cache/)")
