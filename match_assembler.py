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

# Per-player accents for free-for-all, in panel order. The first two match the
# classic 1v1 pink/ice; 3rd/4th extend the neon palette (green, gold).
PALETTE = ["#ff66ab", "#66d9ff", "#8be01f", "#ffd24a"]

# Team accents: two hue families, a brighter + softer variant per teammate so
# same-team panels are grouped by colour yet still tell apart.
TEAM_ACCENTS = [
    ["#ff66ab", "#ff9ccb"],   # team A — pink family
    ["#66d9ff", "#99e6ff"],   # team B — ice family
]

# panel index -> the `side` label kept on PlayerData for back-compat / logging
SIDE_NAMES = ["left", "right", "p3", "p4"]


def assign_accents(n: int, mode: str = "ffa", teams: list[int] | None = None) -> list[str]:
    """Accent hex per player. FFA cycles PALETTE; team mode groups by team hue."""
    if mode == "team" and teams:
        seen: dict[int, int] = {}
        out = []
        for t in teams:
            variant = seen.get(t, 0)
            seen[t] = variant + 1
            fam = TEAM_ACCENTS[t % len(TEAM_ACCENTS)]
            out.append(fam[variant % len(fam)])
        return out
    return [PALETTE[i % len(PALETTE)] for i in range(n)]


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
    side: str                 # "left" | "right" | "p3" | "p4" (panel order label)
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

    # grouping (team mode only)
    team: int | None = None   # team index in team mode; None in free-for-all

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
    players: list[PlayerData]          # 2..4, index = panel order
    map: MapData
    match_title: str = "friendly · bo1"
    mode: str = "ffa"                  # "ffa" | "team"
    teams: list[int] | None = None     # team index per player; None in ffa

    # --- compatibility shims: existing 1v1 code reads .left / .right ---
    @property
    def left(self) -> PlayerData:
        return self.players[0]

    @property
    def right(self) -> PlayerData:
        return self.players[1]

    @classmethod
    def from_pair(cls, left: PlayerData, right: PlayerData, map: MapData,
                  match_title: str = "friendly · bo1") -> "MatchData":
        return cls(players=[left, right], map=map, match_title=match_title)

    # --- ranking / winners (generalised from the old winner_side) ---
    def ranking(self) -> list[int]:
        """Player indices, best score first."""
        return sorted(range(len(self.players)),
                      key=lambda i: self.players[i].score, reverse=True)

    def team_ids(self) -> list[int]:
        """Distinct team ids present, in first-appearance order."""
        seen: list[int] = []
        for t in (self.teams or []):
            if t not in seen:
                seen.append(t)
        return seen

    def team_score(self, t: int) -> int:
        if not self.teams:
            return 0
        return sum(p.score for i, p in enumerate(self.players) if self.teams[i] == t)

    def winner(self) -> int:
        """FFA: index of the top-scoring player. Team: winning team id."""
        if self.mode == "team" and self.teams:
            return max(self.team_ids(), key=self.team_score)
        return self.ranking()[0]

    @property
    def winner_side(self) -> str:
        """Back-compat: which of the first two panels won (1v1 semantics)."""
        return "left" if self.players[0].score >= self.players[1].score else "right"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["winner_side"] = self.winner_side
        d["winner"] = self.winner()
        d["map"]["star_rating"] = self.map.star_rating
        for i, p in enumerate(self.players):
            d["players"][i]["display_name"] = p.display_name
        return d


# --------------------------------------------------------------------------- #
# Build a PlayerData from a parsed replay + API enrichment
# --------------------------------------------------------------------------- #
def _build_player(
    side: str,
    accent: str,
    info: ReplayInfo,
    api: OsuAPI | None,
    cache: Path,
    id_override: int | None,
    team: int | None = None,
) -> PlayerData:
    grade, silver = compute_grade(info.count_300, info.count_100, info.count_50, info.count_miss, info.mods)

    p = PlayerData(
        side=side,
        accent=accent,
        team=team,
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
    paths,
    right_osr: str | None = None,
    *,
    api: OsuAPI | None = None,
    cache_dir: str = "cache",
    left_id: int | None = None,
    right_id: int | None = None,
    id_overrides: list[int | None] | None = None,
    match_title: str = "friendly · bo1",
    mode: str = "ffa",
    teams: list[int] | None = None,
) -> MatchData:
    """Assemble a MatchData from 2–4 replays of the same beatmap.

    Two call styles are supported:
      * legacy 1v1:  assemble_match(left_osr, right_osr, left_id=…, right_id=…)
      * N-player:    assemble_match([p1, p2, p3], id_overrides=[…], mode=…, teams=…)
    """
    # Normalise the legacy (left_osr, right_osr) positional form into a list.
    if isinstance(paths, (str, Path)):
        osr_paths = [paths, right_osr]
        if id_overrides is None:
            id_overrides = [left_id, right_id]
    else:
        osr_paths = list(paths)

    if not (2 <= len(osr_paths) <= 4):
        raise ValueError(f"CircleClash supports 2–4 players, got {len(osr_paths)}.")
    if id_overrides is None:
        id_overrides = [None] * len(osr_paths)
    if len(id_overrides) != len(osr_paths):
        raise ValueError("id_overrides length must match the number of replays.")
    if mode == "team" and (not teams or len(teams) != len(osr_paths)):
        raise ValueError("team mode requires a `teams` entry per player.")

    if api is None:
        try:
            api = OsuAPI()
        except Exception:
            print("  ! No osu! API key set — continuing without avatars, ranks, flags or pp. "
                  "(Add credentials in Settings to enable them.)", flush=True)
            api = None
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)

    infos = [parse_replay(p) for p in osr_paths]
    md5 = infos[0].beatmap_md5
    if any(info.beatmap_md5 != md5 for info in infos):
        raise ValueError("The replays are on different beatmaps.")

    accents = assign_accents(len(osr_paths), mode, teams)
    players = [
        _build_player(SIDE_NAMES[i], accents[i], info, api, cache, id_overrides[i],
                      team=(teams[i] if teams else None))
        for i, info in enumerate(infos)
    ]

    # map (resolved once — all replays share it)
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

    return MatchData(players=players, map=mp, match_title=match_title,
                     mode=mode, teams=(list(teams) if teams else None))


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
