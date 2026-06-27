#!/usr/bin/env python3
"""
sync_poc.py
===========
Milestone 1 for the osu! replay-comparison tool: prove that two danser renders
of the same map stay frame-locked when stacked side by side.

This script does the *minimum* needed to de-risk sync. No overlay, no GUI:
  1. Parse both .osr files and validate they're the same map at the same speed.
  2. Render each replay to video with danser, using IDENTICAL settings so the
     two outputs share timing structure (this is what guarantees sync).
  3. ffprobe both outputs and warn if their durations differ by > 1 frame.
  4. hstack them with ffmpeg into one comparison video.

If the stacked result stays aligned start-to-finish, the rest of the project is
just decoration on top of a sound foundation.

------------------------------------------------------------------------------
IMPORTANT: I could not run danser in the environment where this was written
(no GPU/display). It's written against danser's documented CLI for you to run
on CachyOS. The two things most likely to need tweaking for your install are
DANSER_BIN (executable name) and DANSER_VIDEO_DIR (where danser drops renders) --
both are CLI flags below.
------------------------------------------------------------------------------

Requires: osr_parser.py (same dir), danser-go, and ffmpeg/ffprobe on PATH.

Usage:
    python sync_poc.py left.osr right.osr \
        --danser-bin /path/to/danser-cli \
        --danser-video-dir ~/.local/share/danser/videos \
        --out comparison.mp4
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

from osr_parser import Mods, ReplayInfo, parse_replay

# On Windows, danser is a console app; spawned from the windowed GUI it would pop up
# its own CMD window. CREATE_NO_WINDOW suppresses that while keeping its output on our
# inherited stdout (so it shows in the GUI render log).
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _run_capture(cmd: list[str]) -> tuple[int, str]:
    """Run a console child (danser) with NO console window on Windows, capturing its
    combined output through a pipe, streaming it to our stdout in real time, and also
    returning it so callers can inspect what happened.

    Capturing via an explicit pipe is important on Windows: a --windowed frozen build
    has no valid inherited stdout, so a child launched with CREATE_NO_WINDOW and no
    redirection gets a broken stdout handle and can silently fail to render. Giving it
    our own pipe guarantees a valid handle *and* surfaces its progress in the GUI log."""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=_NO_WINDOW, bufsize=1, text=True,
        encoding="utf-8", errors="replace",
    )
    chunks: list[str] = []
    if proc.stdout is not None:
        for line in proc.stdout:
            print(line, end="", flush=True)
            chunks.append(line)
    return proc.wait(), "".join(chunks)


# --------------------------------------------------------------------------- #
# Mod / speed helpers
# --------------------------------------------------------------------------- #
def speed_multiplier(mods: Mods) -> float:
    """Audio/map speed implied by the mods. This is what determines render
    length, and therefore whether two renders can possibly stay in sync."""
    if Mods.HALF_TIME in mods:
        return 0.75
    if Mods.DOUBLE_TIME in mods or Mods.NIGHTCORE in mods:
        return 1.5
    return 1.0


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate_pair(left: ReplayInfo, right: ReplayInfo) -> None:
    """Raise (or warn) on conditions that make a side-by-side impossible or odd."""
    problems: list[str] = []
    warnings: list[str] = []

    if left.beatmap_md5 != right.beatmap_md5:
        problems.append(
            "Replays are on DIFFERENT beatmaps "
            f"(left={left.beatmap_md5[:8]}…, right={right.beatmap_md5[:8]}…). "
            "A side-by-side only makes sense on the same map."
        )

    ls, rs = speed_multiplier(left.mods), speed_multiplier(right.mods)
    if ls != rs:
        problems.append(
            f"Speed-affecting mods differ (left={left.mods_str} @ {ls}x, "
            f"right={right.mods_str} @ {rs}x). The renders would be different "
            "lengths and cannot stay in sync. Sync is only possible at equal speed."
        )

    if left.mode != right.mode or left.mode != 0:
        warnings.append(
            f"Modes: left={left.mode}, right={right.mode}. This tool targets "
            "osu!standard (mode 0); other modes are untested."
        )

    if problems:
        for p in problems:
            print(f"  ✗ {p}", file=sys.stderr)
        raise SystemExit("Cannot proceed: see errors above.")

    for w in warnings:
        print(f"  ⚠ {w}")


# --------------------------------------------------------------------------- #
# danser
# --------------------------------------------------------------------------- #
def find_danser(explicit: str | None) -> str:
    if explicit:
        if Path(explicit).exists() or shutil.which(explicit):
            return explicit
        raise SystemExit(f"danser binary not found at: {explicit}")
    for name in ("danser-cli", "danser", "danser-cli.exe", "danser.exe"):
        if shutil.which(name):
            return name
    raise SystemExit(
        "Could not locate a danser executable. Pass --danser-bin explicitly."
    )


def render_replay(
    danser_bin: str,
    replay_path: Path,
    out_name: str,
    *,
    extra_args: list[str] | None = None,
) -> None:
    """Render one .osr to video. Uses -quickstart so both renders share a
    zeroed lead-in and skipped intro -- the key to frame alignment.

    -out=<name> triggers recording to danser's configured video directory.
    We pass NO per-render visual overrides here: both renders inherit the exact
    same danser settings profile, which is precisely what keeps them in sync.
    """
    cmd = [
        danser_bin,
        f"-r={replay_path}",
        f"-out={out_name}",
        "-quickstart",      # skip intro, LeadInTime/LeadInHold = 0
        "-nodbcheck",       # don't rescan the song db every run
        "-noupdatecheck",
        "-preciseprogress", # emit render progress in clean 1% increments
    ]
    if extra_args:
        cmd.extend(extra_args)

    print(f"  → rendering {replay_path.name}  (out: {out_name})")
    print(f"    {' '.join(cmd)}")
    code, out = _run_capture(cmd)

    # danser's very first run writes its settings file from scratch, initialising its
    # song database against the *default* osu! Songs path before our -sPatch OsuSongsDir
    # (the render-songs folder) is in effect. That folder usually doesn't exist, so the
    # first render fails with "Failed to initialize database / Beatmap not found" — but
    # danser saves the corrected settings on the way out, so an immediate retry sees the
    # right path and succeeds. Detect that one-time miss and retry once.
    cold_miss = any(s in out for s in (
        "Failed to initialize database",
        "Beatmap not found",
        "does not exist",
    ))
    if cold_miss:
        print("  danser was initialising its settings for the first time; retrying once …")
        code, out = _run_capture(cmd)

    if code != 0:
        raise SystemExit(
            f"danser exited with code {code} for {replay_path.name}. "
            "If it's a 'map not found' error, the .osu for this beatmap MD5 isn't "
            "in danser's song folder yet (that's Milestone 2: auto-download)."
        )


def locate_output(video_dir: Path, out_name: str, since: float) -> Path:
    """danser writes <out_name>.<ext> into its video dir; extension is set in
    danser's settings. Find the matching file produced after `since`."""
    candidates = sorted(
        (p for p in video_dir.glob(f"{out_name}.*") if p.stat().st_mtime >= since - 2),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise SystemExit(
            f"Couldn't find danser's output for '{out_name}' in {video_dir}. "
            "Check --danser-video-dir matches your danser Recording output path."
        )
    return candidates[0]


# --------------------------------------------------------------------------- #
# ffmpeg / ffprobe
# --------------------------------------------------------------------------- #
def probe_duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True, text=True, creationflags=_NO_WINDOW,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return -1.0


def probe_fps(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True, text=True, creationflags=_NO_WINDOW,
    )
    txt = out.stdout.strip()
    if "/" in txt:
        num, den = txt.split("/")
        return float(num) / float(den) if float(den) else 0.0
    try:
        return float(txt)
    except ValueError:
        return 0.0


def check_sync(left: Path, right: Path) -> None:
    ld, rd = probe_duration(left), probe_duration(right)
    fps = probe_fps(left) or 60.0
    frame = 1.0 / fps
    delta = abs(ld - rd)
    print(f"\n  sync check:")
    print(f"    left  duration : {ld:.3f}s")
    print(f"    right duration : {rd:.3f}s")
    print(f"    delta          : {delta * 1000:.1f} ms  (1 frame ≈ {frame * 1000:.1f} ms @ {fps:.0f}fps)")
    if delta <= frame * 1.5:
        print("    ✓ within a frame — panels should stay locked.")
    else:
        print("    ⚠ durations diverge by more than a frame. Likely causes: a "
              "speed-mod mismatch slipped through, differing danser settings "
              "between renders, or a result-screen length difference.")


def composite_hstack(left: Path, right: Path, out: Path) -> None:
    """Stack the two panels horizontally, audio from the left render."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(left),
        "-i", str(right),
        "-filter_complex", "[0:v][1:v]hstack=inputs=2[v]",
        "-map", "[v]",
        "-map", "0:a?",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "aac", "-b:a", "192k",
        str(out),
    ]
    print(f"\n  → compositing side-by-side: {out}")
    result = subprocess.run(cmd, creationflags=_NO_WINDOW)
    if result.returncode != 0:
        raise SystemExit("ffmpeg hstack failed.")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="Milestone 1: danser dual-render sync POC")
    ap.add_argument("left", type=Path, help="left player's .osr")
    ap.add_argument("right", type=Path, help="right player's .osr")
    ap.add_argument("--danser-bin", default=None, help="path to danser-cli")
    ap.add_argument("--danser-video-dir", type=Path, required=True,
                    help="danser's Recording output directory")
    ap.add_argument("--out", type=Path, default=Path("comparison.mp4"),
                    help="final stacked video")
    ap.add_argument("--skip-render", action="store_true",
                    help="skip danser, reuse existing left_panel/right_panel outputs")
    args = ap.parse_args()

    for p in (args.left, args.right):
        if not p.exists():
            raise SystemExit(f"replay not found: {p}")

    print("Parsing replays…")
    left = parse_replay(str(args.left))
    right = parse_replay(str(args.right))
    print(f"  left : {left.player:<20} {left.mods_str:<8} {left.accuracy_pct:6.2f}%  {left.max_combo}x")
    print(f"  right: {right.player:<20} {right.mods_str:<8} {right.accuracy_pct:6.2f}%  {right.max_combo}x")

    print("\nValidating pair…")
    validate_pair(left, right)
    print("  ✓ same map, same speed — sync is possible.")

    danser_bin = find_danser(args.danser_bin)
    print(f"\nUsing danser: {danser_bin}")

    if not args.skip_render:
        t0 = time.time()
        render_replay(danser_bin, args.left, "sync_left")
        left_video = locate_output(args.danser_video_dir, "sync_left", t0)

        t1 = time.time()
        render_replay(danser_bin, args.right, "sync_right")
        right_video = locate_output(args.danser_video_dir, "sync_right", t1)
    else:
        left_video = locate_output(args.danser_video_dir, "sync_left", 0)
        right_video = locate_output(args.danser_video_dir, "sync_right", 0)

    check_sync(left_video, right_video)
    composite_hstack(left_video, right_video, args.out)

    print(f"\nDone. Scrub through {args.out} end-to-end and watch whether the two "
          "panels stay aligned. If they do, sync is proven and we can build on it.")


if __name__ == "__main__":
    main()
