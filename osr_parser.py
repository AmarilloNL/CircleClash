"""
osr_parser.py
=============
Parser for osu! replay files (.osr), the foundational data layer for the
side-by-side replay comparison tool.

A .osr file stores the replay *header* (player, mods, score, hit counts, the
beatmap's MD5 hash) plus an LZMA-compressed stream of cursor/key frames.

For the overlay we mostly need the header. We deliberately do NOT compute UR or
re-render anything ourselves -- danser-go produces all of that. The beatmap hash
is what lets us locate (or download) the matching .osu so danser can render it.

Format reference: https://osu.ppy.sh/wiki/en/Client/File_formats/osr_%28file_format%29

Pure standard library. No third-party dependencies.
"""

from __future__ import annotations

import lzma
import struct
from dataclasses import dataclass, field
from enum import IntFlag
from typing import BinaryIO


# --------------------------------------------------------------------------- #
# Mods
# --------------------------------------------------------------------------- #
class Mods(IntFlag):
    NONE          = 0
    NO_FAIL       = 1 << 0
    EASY          = 1 << 1
    TOUCH_DEVICE  = 1 << 2
    HIDDEN        = 1 << 3
    HARD_ROCK     = 1 << 4
    SUDDEN_DEATH  = 1 << 5
    DOUBLE_TIME   = 1 << 6
    RELAX         = 1 << 7
    HALF_TIME     = 1 << 8
    NIGHTCORE     = 1 << 9   # always set together with DOUBLE_TIME
    FLASHLIGHT    = 1 << 10
    AUTOPLAY      = 1 << 11
    SPUN_OUT      = 1 << 12
    AUTOPILOT     = 1 << 13  # "Relax2"
    PERFECT       = 1 << 14  # always set together with SUDDEN_DEATH
    KEY4          = 1 << 15
    KEY5          = 1 << 16
    KEY6          = 1 << 17
    KEY7          = 1 << 18
    KEY8          = 1 << 19
    FADE_IN       = 1 << 20
    RANDOM        = 1 << 21
    CINEMA        = 1 << 22
    TARGET        = 1 << 23
    KEY9          = 1 << 24
    KEY_COOP      = 1 << 25
    KEY1          = 1 << 26
    KEY3          = 1 << 27
    KEY2          = 1 << 28
    SCORE_V2      = 1 << 29
    MIRROR        = 1 << 30


# Short acronyms in the canonical osu! display order. Used to build the string
# you'd hand to danser's -mods flag and to label the overlay (e.g. "HDHR").
_MOD_ACRONYMS: list[tuple[Mods, str]] = [
    (Mods.NO_FAIL, "NF"),
    (Mods.EASY, "EZ"),
    (Mods.TOUCH_DEVICE, "TD"),
    (Mods.HIDDEN, "HD"),
    (Mods.DOUBLE_TIME, "DT"),
    (Mods.NIGHTCORE, "NC"),
    (Mods.HALF_TIME, "HT"),
    (Mods.HARD_ROCK, "HR"),
    (Mods.SUDDEN_DEATH, "SD"),
    (Mods.PERFECT, "PF"),
    (Mods.FLASHLIGHT, "FL"),
    (Mods.RELAX, "RX"),
    (Mods.AUTOPILOT, "AP"),
    (Mods.SPUN_OUT, "SO"),
    (Mods.AUTOPLAY, "AT"),
    (Mods.CINEMA, "CN"),
    (Mods.MIRROR, "MR"),
    (Mods.SCORE_V2, "V2"),
]


# osu!lazer stamps exported replays with a game version >= 30000000 (the
# LegacyScoreEncoder version scheme); osu!stable uses dated builds like 20210520,
# always well below this. So the version field alone tells the two clients apart.
LAZER_MIN_VERSION = 30000000


def mods_to_string(mods: Mods) -> str:
    """Render a Mods flag set as an osu!-style acronym string, e.g. 'HDHR'.

    NC implies DT and PF implies SD in the bitfield, so we suppress the implied
    component to avoid 'DTNC' / 'SDPF' duplicates -- matching how osu! displays.
    """
    effective = mods
    if Mods.NIGHTCORE in effective:
        effective &= ~Mods.DOUBLE_TIME
    if Mods.PERFECT in effective:
        effective &= ~Mods.SUDDEN_DEATH

    return "".join(ac for flag, ac in _MOD_ACRONYMS if flag in effective) or "NM"


# Acronyms safe to hand danser's -mods2 (lazer) override. We drop AT/CN (would
# hijack the replay with autoplay/cinema), V2 (ScoreV2 is scoring, not a render
# mod), and the implied DT-under-NC / SD-under-PF so the set matches osu! exactly.
def mods_to_acronyms(mods: Mods) -> list[str]:
    """Replay mods as a clean acronym list (e.g. ['HD','DT']) for -mods2."""
    effective = mods
    if Mods.NIGHTCORE in effective:
        effective &= ~Mods.DOUBLE_TIME
    if Mods.PERFECT in effective:
        effective &= ~Mods.SUDDEN_DEATH
    skip = {"AT", "CN", "V2"}
    return [ac for flag, ac in _MOD_ACRONYMS if flag in effective and ac not in skip]


# --------------------------------------------------------------------------- #
# Replay frame (decoded from the LZMA stream)
# --------------------------------------------------------------------------- #
@dataclass
class ReplayFrame:
    time_delta: int   # ms since previous frame (the raw 'w' value)
    x: float          # cursor x in osu!pixels (0..512)
    y: float          # cursor y in osu!pixels (0..384)
    keys: int         # bitfield: M1=1, M2=2, K1=4, K2=8, Smoke=16


# --------------------------------------------------------------------------- #
# Replay header / info
# --------------------------------------------------------------------------- #
@dataclass
class ReplayInfo:
    mode: int                 # 0=std, 1=taiko, 2=ctb, 3=mania
    version: int              # game version that produced the replay
    beatmap_md5: str          # MD5 of the .osu -- used to resolve/download the map
    player: str
    replay_md5: str
    count_300: int
    count_100: int
    count_50: int
    count_geki: int
    count_katu: int
    count_miss: int
    score: int
    max_combo: int
    perfect: bool             # True if the play was a full combo
    mods: Mods
    life_bar: str             # raw "time|hp,time|hp,..." graph string
    timestamp_ticks: int      # .NET ticks
    online_score_id: int
    frames: list[ReplayFrame] = field(default_factory=list)

    # --- convenience -------------------------------------------------------- #
    @property
    def is_lazer(self) -> bool:
        """True if this replay was produced by osu!lazer (vs osu!stable)."""
        return self.version >= LAZER_MIN_VERSION

    @property
    def mods_str(self) -> str:
        return mods_to_string(self.mods)

    @property
    def accuracy(self) -> float:
        """osu!standard accuracy as a 0..1 fraction. (Other modes differ.)"""
        total = self.count_300 + self.count_100 + self.count_50 + self.count_miss
        if total == 0:
            return 1.0
        weighted = 300 * self.count_300 + 100 * self.count_100 + 50 * self.count_50
        return weighted / (300 * total)

    @property
    def accuracy_pct(self) -> float:
        return self.accuracy * 100.0


# --------------------------------------------------------------------------- #
# Binary reader for osu!'s little-endian format
# --------------------------------------------------------------------------- #
class _OsuReader:
    def __init__(self, stream: BinaryIO):
        self._s = stream

    def _read(self, n: int) -> bytes:
        b = self._s.read(n)
        if len(b) != n:
            raise EOFError(f"Expected {n} bytes, got {len(b)} (truncated .osr?)")
        return b

    def byte(self) -> int:
        return self._read(1)[0]

    def short(self) -> int:
        return struct.unpack("<H", self._read(2))[0]

    def integer(self) -> int:
        return struct.unpack("<I", self._read(4))[0]

    def long(self) -> int:
        return struct.unpack("<q", self._read(8))[0]

    def uleb128(self) -> int:
        result = 0
        shift = 0
        while True:
            b = self.byte()
            result |= (b & 0x7F) << shift
            if not (b & 0x80):
                return result
            shift += 7

    def string(self) -> str:
        """osu! string: 0x00 = empty, 0x0b = ULEB128 length + UTF-8 bytes."""
        indicator = self.byte()
        if indicator == 0x00:
            return ""
        if indicator == 0x0B:
            length = self.uleb128()
            return self._read(length).decode("utf-8")
        raise ValueError(f"Bad string indicator byte: 0x{indicator:02x}")

    def raw(self, n: int) -> bytes:
        return self._read(n)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def parse_replay(path: str, *, decode_frames: bool = False) -> ReplayInfo:
    """Parse a .osr file.

    Parameters
    ----------
    path : str
        Path to the .osr file.
    decode_frames : bool
        If True, also LZMA-decompress and parse the cursor/key frame stream.
        Not needed for overlay metadata (danser handles gameplay), so it
        defaults to False to keep parsing cheap.
    """
    with open(path, "rb") as f:
        r = _OsuReader(f)

        mode = r.byte()
        version = r.integer()
        beatmap_md5 = r.string()
        player = r.string()
        replay_md5 = r.string()
        count_300 = r.short()
        count_100 = r.short()
        count_50 = r.short()
        count_geki = r.short()
        count_katu = r.short()
        count_miss = r.short()
        score = r.integer()
        max_combo = r.short()
        perfect = r.byte() == 1
        mods = Mods(r.integer())
        life_bar = r.string()
        timestamp_ticks = r.long()

        compressed_len = r.integer()
        compressed = r.raw(compressed_len) if compressed_len > 0 else b""

        online_score_id = r.long()

    frames: list[ReplayFrame] = []
    if decode_frames and compressed:
        frames = _decode_frames(compressed)

    return ReplayInfo(
        mode=mode,
        version=version,
        beatmap_md5=beatmap_md5,
        player=player,
        replay_md5=replay_md5,
        count_300=count_300,
        count_100=count_100,
        count_50=count_50,
        count_geki=count_geki,
        count_katu=count_katu,
        count_miss=count_miss,
        score=score,
        max_combo=max_combo,
        perfect=perfect,
        mods=mods,
        life_bar=life_bar,
        timestamp_ticks=timestamp_ticks,
        online_score_id=online_score_id,
        frames=frames,
    )


def _decode_frames(compressed: bytes) -> list[ReplayFrame]:
    """LZMA-decompress and parse the comma-separated 'w|x|y|z' frame stream."""
    raw = lzma.decompress(compressed).decode("ascii", errors="ignore")
    frames: list[ReplayFrame] = []
    for chunk in raw.split(","):
        if not chunk:
            continue
        parts = chunk.split("|")
        if len(parts) != 4:
            continue
        w, x, y, z = parts
        # The final "-12345|0|0|seed" frame carries the RNG seed, not a real
        # cursor position; skip it.
        if w == "-12345":
            continue
        frames.append(
            ReplayFrame(
                time_delta=int(w),
                x=float(x),
                y=float(y),
                keys=int(z),
            )
        )
    return frames


# --------------------------------------------------------------------------- #
# Quick manual check:  python osr_parser.py some_replay.osr
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("usage: python osr_parser.py <replay.osr>")
        raise SystemExit(1)

    info = parse_replay(sys.argv[1], decode_frames=True)
    print(f"Player      : {info.player}")
    print(f"Mode        : {info.mode}  (0=std 1=taiko 2=ctb 3=mania)")
    print(f"Mods        : {info.mods_str}  (raw={int(info.mods)})")
    print(f"Accuracy    : {info.accuracy_pct:.2f}%")
    print(f"Combo       : {info.max_combo}x  (FC={info.perfect})")
    print(f"Score       : {info.score:,}")
    print(f"300/100/50/X: {info.count_300}/{info.count_100}/{info.count_50}/{info.count_miss}")
    print(f"Beatmap MD5 : {info.beatmap_md5}")
    print(f"Frames      : {len(info.frames)}")
