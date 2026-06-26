#!/usr/bin/env python3
"""
make_overlay_video.py  (v2)
===========================
Drop in two .osr files, get a recorded side-by-side video with the osu! overlay.

Changes in v2 (from feedback):
  - NO crop: each danser panel is rendered at the panel's exact resolution
    (via -sPatch Recording.FrameWidth/Height), so the full playfield AND danser's
    own live UI (combo bottom-left, score/acc top-right, pp, hit-error, keys) are
    visible. The previous cover-crop sliced the edges, including the combo counter.
  - SPOILER-FREE chrome: the persistent overlay no longer shows final grade /
    accuracy / score / max-combo (those spoil the result from frame one). Live
    gameplay numbers come from danser in-panel, exactly like real tournament
    overlays. The chrome is now a clean identity frame + a slim non-spoiler footer.
  - Triangles turned up so the lazer texture actually reads.

Layout is computed once (Layout) and used for BOTH the CSS panel rects and the
ffmpeg overlay coords, so they can never drift apart.

LIVE STATS — one-time danser setup (launcher GUI -> Gameplay):
  enable Combo, Score/Accuracy, PP Counter, Hit Error Meter, Key Overlay;
  turn OFF "Results Screen" (it shows the final grade = a spoiler, and adds tail
  length). These render inside each panel and are inherently real-time.

Requires: osr_parser.py, osu_api.py, match_assembler.py, sync_poc.py, Playwright,
danser-go, ffmpeg.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# Force UTF-8 stdio so the arrows/bullets/✓/× we print never crash on a Windows
# console or pipe (which default to cp1252 and can't encode U+2192 and friends).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# When running inside a frozen build (the .exe relaunched in --run-pipeline mode),
# Chromium is bundled inside the packaged Playwright. PLAYWRIGHT_BROWSERS_PATH=0
# tells Playwright to load the browser from the package itself rather than a
# per-user cache that doesn't exist on the end user's machine.
if getattr(sys, "frozen", False):
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

from match_assembler import MatchData, PlayerData, MapData, assemble_match
from osr_parser import parse_replay, mods_to_acronyms
import sync_poc


# ----------------------------------------------------------------------------- #
# LAYOUT — single source of truth for panel geometry (CSS + ffmpeg both use it)
# ----------------------------------------------------------------------------- #
class Layout:
    W, H = 1920, 1080
    TOP_H = 140
    FOOTER_H = 104
    PAD_X = 24
    PAD_Y = 16
    GAP = 24
    BORDER = 3

    panel_w = (W - 2 * PAD_X - GAP) // 2          # 924
    panel_h = H - TOP_H - FOOTER_H - 2 * PAD_Y    # 804
    panel_top = TOP_H + PAD_Y                      # 156
    left_x = PAD_X                                  # 24
    right_x = PAD_X + panel_w + GAP                 # 972

    # danser render = panel interior (inset by border); these are the render dims
    dz_w = panel_w - 2 * BORDER                     # 918
    dz_h = panel_h - 2 * BORDER                     # 798
    dz_lx = left_x + BORDER                          # 27
    dz_rx = right_x + BORDER                         # 975
    dz_y = panel_top + BORDER                        # 159


L = Layout

# Output resolution + framerate. SCALE multiplies the 1080p design: chrome/end
# card render via Chromium's device_scale_factor, danser renders its panels at the
# scaled size, and ffmpeg composites at scaled coords — so everything stays aligned.
SCALE = 1.0
FPS = 60
FFMPEG = "ffmpeg"   # overridable via --ffmpeg (the GUI passes a provisioned binary)
RES_SCALES = {"720p": 720 / 1080, "1080p": 1.0, "1440p": 1440 / 1080, "4k": 2160 / 1080}


def _s(v: float) -> int:
    """Scale a 1080p-design pixel value to the current output resolution."""
    return round(v * SCALE)


# ----------------------------------------------------------------------------- #
# Asset helpers
# ----------------------------------------------------------------------------- #
def _mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def _embed(path: str | Path | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    b = p.read_bytes()
    return f"data:{_mime(b)};base64," + base64.b64encode(b).decode()


def _fetch_flag(cc: str, cache: Path) -> str | None:
    if not cc:
        return None
    dest = cache / f"flag_{cc.lower()}.png"
    if not dest.exists():
        try:
            url = f"https://flagcdn.com/w80/{cc.lower()}.png"
            req = urllib.request.Request(url, headers={"User-Agent": "osu-overlay/0.2"})
            with urllib.request.urlopen(req, timeout=15) as r:
                dest.write_bytes(r.read())
        except Exception:
            return None
    return _embed(dest)


def _avatar_html(p: PlayerData) -> str:
    uri = _embed(p.avatar_path)
    return f'<img src="{uri}" alt="">' if uri else (p.display_name[:1] or "?").upper()


def _flag_html(p: PlayerData, cache: Path) -> str:
    uri = _fetch_flag(p.country_code, cache)
    return (f'<img class="flagimg" src="{uri}" alt="{p.country_code}">' if uri
            else f'<span class="flagtxt">{p.country_code or "??"}</span>')


# ----------------------------------------------------------------------------- #
# Build chrome HTML
# ----------------------------------------------------------------------------- #
def build_chrome_html(match: MatchData, cache: Path) -> str:
    Lp, Rp, M = match.left, match.right, match.map
    repl = {
        "L_AVATAR": _avatar_html(Lp), "R_AVATAR": _avatar_html(Rp),
        "L_NAME": Lp.display_name, "R_NAME": Rp.display_name,
        "L_FLAG": _flag_html(Lp, cache), "R_FLAG": _flag_html(Rp, cache),
        "L_RANK": Lp.rank_display, "R_RANK": Rp.rank_display,
        "L_PP": f"{Lp.pp:,.0f}pp" if Lp.pp else "—", "R_PP": f"{Rp.pp:,.0f}pp" if Rp.pp else "—",
        "L_MODS": Lp.mods, "R_MODS": Rp.mods,
        "MAP_TITLE": M.title or "Unknown", "MAP_ARTIST": M.artist or "",
        "MAP_DIFF": f"[{M.version}]" if M.version else "",
        "SR": f"{M.star_rating:.2f}", "MATCH_TITLE": match.match_title,
        # geometry
        "PL_LEFT": str(L.left_x), "PR_LEFT": str(L.right_x), "P_TOP": str(L.panel_top),
        "P_W": str(L.panel_w), "P_H": str(L.panel_h),
    }
    html = _CHROME_TEMPLATE
    for k, v in repl.items():
        html = html.replace("%%" + k + "%%", str(v))
    return html


# ----------------------------------------------------------------------------- #
# Playwright render
# ----------------------------------------------------------------------------- #
def render_chrome_png(html: str, out_png: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("Playwright missing:\n  pip install playwright --break-system-packages\n  playwright install chromium")
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as e:
            raise SystemExit(f"Chromium launch failed ({e}). Run: playwright install chromium")
        page = browser.new_page(viewport={"width": L.W, "height": L.H}, device_scale_factor=SCALE)
        page.set_content(html, wait_until="networkidle")
        page.evaluate("async () => { await document.fonts.ready; }")
        page.wait_for_timeout(350)
        page.screenshot(path=str(out_png), clip={"x": 0, "y": 0, "width": L.W, "height": L.H})
        browser.close()


# ----------------------------------------------------------------------------- #
# danser panel render at exact panel resolution (no crop needed downstream)
# ----------------------------------------------------------------------------- #
def _hsv(hex_color: str) -> dict:
    """Convert #rrggbb to danser's HSV dict (Hue 0-360, Sat/Val 0-1)."""
    import colorsys
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hh, ss, vv = colorsys.rgb_to_hsv(r, g, b)
    return {"Hue": round(hh * 360, 1), "Saturation": round(ss, 3), "Value": round(vv, 3)}


def _render_patch(accent_hex: str, skin: str | None = None, songs_dir: str | None = None,
                  skins_dir: str | None = None, music_vol: float | None = None,
                  hit_vol: float | None = None, master_vol: float | None = None,
                  force_skin_hits: bool = True, vis: dict | None = None) -> str:
    """Resolution + a per-side Gameplay restyle, applied via -sPatch so it never
    touches the user's saved danser config. Also points danser at the right Songs
    folder, sets per-side skin, output resolution/FPS, and audio volumes.

    Hidden (clutter / redundant with the chrome): ScoreBoard, StrainGraph, Mods,
    Boundaries, HpBar. Kept + tinted to this side's accent: Score/accuracy, PP,
    Combo, HitCounter, HitErrorMeter, KeyOverlay. Explicitly-positioned elements
    (PP, hit counts) scale with the output resolution.
    """
    tint = _hsv(accent_hex)
    hud = round(0.7 * SCALE, 3)
    patch = {
        "Recording": {"FrameWidth": _s(L.dz_w), "FrameHeight": _s(L.dz_h), "FPS": FPS,
                      "AudioCodec": "aac", "AudioBitrate": "320k"},
        "Gameplay": {
            # --- hide the clutter ---
            "ScoreBoard": {"Show": False},
            "StrainGraph": {"Show": False},
            "Mods": {"Show": False},
            "HpBar": {"Show": False},
            "Boundaries": {"Enabled": False},
            "ShowResultsScreen": False,
            "ShowWarningArrows": False,
            # --- keep + tidy, tinted. Anchored to the panel's bottom-RIGHT with a
            #     fixed margin (BottomRight/Right align) so the value grows leftward
            #     and a long pp number (e.g. 1141.37) can never slide under the right
            #     border. Positions are in render-frame pixels, so they scale with res. ---
            "PPCounter": {
                "Show": True, "XPosition": _s(L.dz_w - 48), "YPosition": _s(L.dz_h - 86),
                "Align": "BottomRight", "Color": tint, "Decimals": 2, "Scale": hud,
            },
            "HitCounter": {
                "Show": True, "XPosition": _s(L.dz_w - 48), "YPosition": _s(L.dz_h - 50),
                "Spacing": _s(34), "FontScale": hud, "Align": "Right", "ValueAlign": "Right",
                "Color300": tint, "Color100": tint, "Color50": tint, "ColorMiss": tint,
                "Show300": True,
            },
            "ComboCounter": {"Show": True},
            "Score": {"Show": True, "ProgressBar": "Pie"},
            "HitErrorMeter": {"Show": True, "ShowUnstableRate": True},
            "KeyOverlay": {"Show": True},
        },
    }
    # ---- optional visual tweaks (all default to current behaviour) -------------
    v = vis or {}
    g = patch["Gameplay"]

    # HUD element visibility (Score/accuracy stays as set above unless toggled)
    g["PPCounter"]["Show"]     = v.get("show_pp", True)
    g["HitCounter"]["Show"]    = v.get("show_hitcounts", True)
    g["HitErrorMeter"]["Show"] = v.get("show_hiterror", True)
    g["KeyOverlay"]["Show"]    = v.get("show_keys", True)
    g["ComboCounter"]["Show"]  = v.get("show_combo", True)
    g["Score"]["Show"]         = v.get("show_score", True)

    # pp breakdown (aim / speed / acc) under the pp counter
    g["PPCounter"]["ShowPPComponents"] = bool(v.get("pp_components", False))

    # make the unstable-rate readout big + precise
    if v.get("prominent_ur"):
        g["HitErrorMeter"]["ShowUnstableRate"]     = True
        g["HitErrorMeter"]["UnstableRateScale"]    = round(1.6 * SCALE, 3)
        g["HitErrorMeter"]["UnstableRateDecimals"] = 2

    # per-side mods badge (each panel shows that replay's own mods)
    g["Mods"] = {"Show": bool(v.get("show_mods", False))}

    # hit lighting flashes on each hit
    if v.get("hit_lighting"):
        g["ShowHitLighting"] = True

    # aim-error scatter (off by default; danser's default position is off our
    # 918px frame, so anchor it inside — top-left, clear of combo/score/UR)
    if v.get("aim_error"):
        g["AimErrorMeter"] = {
            "Show": True, "Scale": round(0.9 * SCALE, 3),
            "XPosition": _s(96), "YPosition": _s(96), "Align": "Left",
            "ShowUnstableRate": False,
        }

    # ---- background / bloom (Playfield) ----------------------------------------
    bg_dim  = float(v.get("bg_dim", 0.95))          # danser default = 0.95 (dark)
    bg_blur = float(v.get("bg_blur", 0.0))          # 0 = off
    background = {
        "LoadStoryboards": bool(v.get("storyboards", True)),
        "LoadVideos":      bool(v.get("videos", False)),
        "Dim":  {"Intro": bg_dim, "Normal": bg_dim, "Breaks": bg_dim},
        "Blur": {"Enabled": bg_blur > 0,
                 "Values": {"Intro": 0.0, "Normal": bg_blur, "Breaks": bg_blur}},
    }
    playfield = {"Background": background}
    if v.get("bloom"):
        playfield["Bloom"] = {"Enabled": True}
    patch["Playfield"] = playfield

    # ---- cursor size / trail length --------------------------------------------
    cursor = {}
    csize = float(v.get("cursor_size", 0.0))        # absolute osu!px; 0 = default
    if csize > 0:
        cursor["CursorSize"] = csize
    tlen = float(v.get("trail_length", 1.0))        # multiplier on danser's default
    if abs(tlen - 1.0) > 1e-6:
        cursor["TrailMaxLength"] = max(1, int(round(2000 * tlen)))
    if cursor:
        patch["Cursor"] = cursor

    if songs_dir or skins_dir:
        general = {}
        if songs_dir:
            general["OsuSongsDir"] = songs_dir
        if skins_dir:
            general["OsuSkinsDir"] = skins_dir
        patch["General"] = general
    audio = {}
    if v.get("ignore_sample_volume"):
        # keep hitsounds at a constant level (ignore per-section volume changes)
        audio["IgnoreBeatmapSampleVolume"] = True
    if force_skin_hits:
        # use the skin's hitsounds instead of the ones baked into the beatmap, so
        # every map sounds consistent (and matches the chosen skin).
        audio["IgnoreBeatmapSamples"] = True
    if master_vol is not None:
        audio["GeneralVolume"] = master_vol
    if music_vol is not None:
        audio["MusicVolume"] = music_vol
    if hit_vol is not None:
        audio["SampleVolume"] = hit_vol
    if audio:
        patch["Audio"] = audio
    if skin:
        patch["Skin"] = {
            "CurrentSkin": skin,
            "UseColorsFromSkin": True,          # the skin's combo colours, not danser's
            "Cursor": {"UseSkinCursor": True},  # the skin's cursor, not danser's default
        }
    return "-sPatch=" + json.dumps(patch)


# ----------------------------------------------------------------------------- #
# Composite (no crop: danser already rendered at panel size)
# ----------------------------------------------------------------------------- #
def replay_end_seconds(path: str) -> float:
    """Absolute time of the last replay frame (~when the play ended), from the
    .osr cursor stream. danser renders the whole song past this, so we use it to
    trim the long outro/fade before the end card."""
    info = parse_replay(path, decode_frames=True)
    if not info.frames:
        return 0.0
    return sum(f.time_delta for f in info.frames) / 1000.0


def _mods2_arg(replay_path: str, force_nofail: bool = True) -> str | None:
    """Build danser's -mods2 override, but ONLY for osu!lazer replays and ONLY to
    inject NoFail. danser simulates HP with osu!stable's drain model, which is
    harsher than lazer's, so lazer replays sometimes falsely show as failed. NF is
    gameplay-neutral (no note positions or timing change, so the replay stays in
    perfect sync) and stops that. osu!stable replays are never touched here — they
    render exactly as recorded, real fails and all. Returns None when there's
    nothing to do (stable replay, NoFail disabled, or NF would be incompatible)."""
    if not force_nofail:
        return None
    try:
        info = parse_replay(replay_path)
    except Exception:
        return None
    if not info.is_lazer:          # stable replay -> leave completely alone
        return None
    acros = mods_to_acronyms(info.mods)
    # NoFail is incompatible with Sudden Death / Perfect in lazer's mod system, and
    # a complete SD/PF replay passed by definition, so there's no false-fail to fix.
    if {"SD", "PF"} & set(acros) or "NF" in acros:
        return None
    acros.append("NF")
    return "-mods2=" + json.dumps([{"acronym": a} for a in acros])


# Video encoders the user can pick. Each maps a quality level to that encoder's
# own rate-control number (CRF for CPU x26x, CQ for NVENC). Lower = better/bigger.
ENCODERS: dict[str, dict] = {
    "x264":       {"label": "x264 · CPU · H.264 · most compatible",
                   "args": ["-c:v", "libx264", "-preset", "medium"], "rc": "-crf",
                   "q": {"lossless": 16, "high": 18, "balanced": 21, "compact": 25}, "extra": []},
    "x265":       {"label": "x265 · CPU · H.265 · smaller, slow",
                   "args": ["-c:v", "libx265", "-preset", "medium"], "rc": "-crf",
                   "q": {"lossless": 18, "high": 20, "balanced": 24, "compact": 28}, "extra": ["-tag:v", "hvc1"]},
    "nvenc_h264": {"label": "NVENC H.264 · GPU · fast",
                   "args": ["-c:v", "h264_nvenc", "-preset", "p5"], "rc": "-cq",
                   "q": {"lossless": 17, "high": 20, "balanced": 24, "compact": 28}, "extra": []},
    "nvenc_hevc": {"label": "NVENC HEVC · GPU · smaller files",
                   "args": ["-c:v", "hevc_nvenc", "-preset", "p5"], "rc": "-cq",
                   "q": {"lossless": 18, "high": 21, "balanced": 25, "compact": 29}, "extra": ["-tag:v", "hvc1"]},
    "nvenc_av1":  {"label": "NVENC AV1 · GPU · smallest (RTX 40-series+)",
                   "args": ["-c:v", "av1_nvenc", "-preset", "p5"], "rc": "-cq",
                   "q": {"lossless": 20, "high": 24, "balanced": 28, "compact": 34}, "extra": []},
}
QUALITY_LEVELS = ["lossless", "high", "balanced", "compact"]


# Bump this to force a one-time danser-db rebuild for all users on upgrade
# (clears stale entries left by older versions that could crash the render).
_DB_RESET_TOKEN = "1"


def _refresh_danser_db(danser_bin: str, *, staged_status: str | None = None) -> None:
    """danser keeps a beatmap database that, with -nodbcheck, never drops entries
    for maps that have since moved or been removed — so it can resolve a replay's
    md5 to a dead path and hard-crash ("no such file or directory").

    We rebuild the db (danser re-imports from the small render-songs folder, which
    is quick) in exactly two cases, and otherwise leave it alone so already-staged
    renders stay fast:
      1. once per install, to clear stale entries from before this fix existed;
      2. whenever we just changed render-songs this run (a fresh download or link
         can collide with an old entry for the same md5)."""
    base = Path(danser_bin).resolve().parent
    marker = base / ".circleclash-dbreset"
    try:
        first_time = marker.read_text(encoding="utf-8").strip() != _DB_RESET_TOKEN
    except Exception:
        first_time = True
    changed = staged_status in ("linked", "downloaded", "downloaded_patched", "version_mismatch")
    if not (first_time or changed):
        return

    removed = False
    for name in ("danser.db", "danser.db-shm", "danser.db-wal"):
        try:
            (base / name).unlink()
            removed = True
        except FileNotFoundError:
            pass
        except Exception:
            pass
    try:
        marker.write_text(_DB_RESET_TOKEN, encoding="utf-8")
    except Exception:
        pass
    if removed:
        reason = "one-time cleanup of stale entries" if first_time else "freshly staged map"
        print(f"  danser db refreshed ({reason}; rebuilt from render-songs)")


def _venc(encoder: str = "x264", quality: str = "high") -> list[str]:
    """Video-encoder ffmpeg args for the chosen encoder + quality level. NVENC
    options use your 4080's hardware encoders (fast; AV1 needs RTX 40-series);
    x264/x265 are the portable CPU fallbacks for machines without NVENC."""
    enc = ENCODERS.get(encoder, ENCODERS["x264"])
    qval = enc["q"].get(quality, enc["q"]["high"])
    return enc["args"] + [enc["rc"], str(qval)] + enc["extra"] + ["-pix_fmt", "yuv420p"]


def _encoder_available(encoder: str) -> bool:
    """Quick pre-flight: can this ffmpeg actually open the chosen encoder here?

    CPU encoders (libx264/libx265) are always assumed present. For GPU (NVENC)
    encoders we briefly try to encode a single black frame and check the exit
    code — this catches cases like the bundled bleeding-edge ffmpeg whose
    av1_nvenc needs a newer NVIDIA driver than is installed (the encoder fails to
    *open*, regardless of input, so a 1-frame probe surfaces it in <1s instead of
    blowing up minutes into the real composite)."""
    spec = ENCODERS.get(encoder)
    if not spec:
        return False
    args = spec["args"]
    try:
        codec = args[args.index("-c:v") + 1]
    except (ValueError, IndexError):
        return True
    if codec.startswith("lib"):          # libx264 / libx265 -> CPU, always fine
        return True
    probe = [FFMPEG, "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "color=c=black:s=320x240:r=10:d=0.2",
             "-pix_fmt", "yuv420p", "-frames:v", "1", "-c:v", codec, "-f", "null", "-"]
    try:
        r = subprocess.run(probe, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def composite(overlay_png: Path, left_mp4: Path, right_mp4: Path, out: Path,
              trim_to: float | None = None, fade: float = 0.4,
              encoder: str = "x264", quality: str = "high") -> None:
    """Overlay the two panels into the chrome. If trim_to is given, end the
    gameplay there with a short fade (cuts danser's long post-song tail)."""
    vchain = (
        f"[1:v]scale={_s(L.dz_w)}:{_s(L.dz_h)}:flags=bicubic,setsar=1[A];"
        f"[2:v]scale={_s(L.dz_w)}:{_s(L.dz_h)}:flags=bicubic,setsar=1[B];"
        f"[0:v][A]overlay={_s(L.dz_lx)}:{_s(L.dz_y)}[a];"
        f"[a][B]overlay={_s(L.dz_rx)}:{_s(L.dz_y)}"
    )
    # Mix BOTH panels' audio. Each side's music/hitsound levels are baked in at
    # danser render time (per-side patch), so a straight sum (normalize=0) honours
    # them — e.g. P1 music=1 / P2 music=0 gives a single music bed under both
    # players' hitsounds. normalize=0 stops amix from auto-attenuating by input
    # count so muted sides don't quiet the mix; alimiter then catches any peaks the
    # sum pushes past 0 dBFS, so loud sections don't clip/distort.
    amix = ("[1:a][2:a]amix=inputs=2:normalize=0:dropout_transition=0,"
            "alimiter=limit=0.95:attack=5:release=50[amx]")
    cmd = [FFMPEG, "-y",
           "-loop", "1", "-framerate", str(FPS), "-i", str(overlay_png),
           "-i", str(left_mp4), "-i", str(right_mp4)]

    if trim_to and trim_to > fade:
        st = trim_to - fade
        fc = (vchain + "[vov];"
              f"[vov]fade=t=out:st={st:.3f}:d={fade}[v];"
              + amix + ";"
              f"[amx]afade=t=out:st={st:.3f}:d={fade}[aud]")
        cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[aud]",
                "-t", f"{trim_to:.3f}"]
    else:
        fc = vchain + "[v];" + amix
        cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[amx]", "-shortest"]

    # 48 kHz / 256k AAC, matching the end card so the final concat stays lossless
    cmd += _venc(encoder, quality) + ["-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2", str(out)]
    print(f"\n  -> compositing gameplay: {out}"
          + (f"  (trimmed to {trim_to:.1f}s)" if trim_to else ""))
    if subprocess.run(cmd).returncode != 0:
        raise SystemExit("ffmpeg composite failed.")


# ----------------------------------------------------------------------------- #
# End card (animated results screen)
# ----------------------------------------------------------------------------- #
_GRADE_COLOR = {"SS": "#ffd24a", "S": "#ffd24a", "A": "#8be01f",
                "B": "#66ccff", "C": "#cc66ff", "D": "#ff5e5e"}


def _grade_color(g: str) -> str:
    return _GRADE_COLOR.get(g, "#ffffff")


def _cmp_rows(Lp, Rp) -> str:
    """Build the comparison table rows: each metric shows both values, a
    tug-of-war bar (proportional to the counts), and the better side highlighted."""
    rows = [
        ("Score",     Lp.score,     Rp.score,     "num",   True),
        ("Max Combo", Lp.max_combo, Rp.max_combo, "combo", True),
        ("300",       Lp.n300,      Rp.n300,      "num",   True),
        ("100",       Lp.n100,      Rp.n100,      "num",   True),
        ("50",        Lp.n50,       Rp.n50,       "num",   True),
        ("Miss",      Lp.nmiss,     Rp.nmiss,     "num",   False),  # fewer is better
    ]
    out = []
    for label, lv, rv, fmt, higher_better in rows:
        total = lv + rv
        lpct = (lv / total * 100) if total else 50
        if lv == rv:
            lwin = rwin = False
        elif higher_better:
            lwin, rwin = lv > rv, rv > lv
        else:
            lwin, rwin = lv < rv, rv < lv
        out.append(
            f'<div class="row">'
            f'<div class="rtop">'
            f'<span class="lv countup{" win" if lwin else ""}" data-final="{lv}" data-fmt="{fmt}">0</span>'
            f'<span class="lab">{label}</span>'
            f'<span class="rv countup{" win" if rwin else ""}" data-final="{rv}" data-fmt="{fmt}">0</span>'
            f'</div>'
            f'<div class="bar" data-l="{lpct:.1f}"><span class="bl"></span><span class="br"></span></div>'
            f'</div>'
        )
    return "".join(out)


def build_endcard_html(match: MatchData, cache: Path) -> str:
    """Versus-table end card with compact identity cards. Seekable via
    window.seek(t): slide-in, count-up, bar-fill, winner banner."""
    Lp, Rp, M = match.left, match.right, match.map
    win = match.winner_side
    repl = {
        "L_AVATAR": _avatar_html(Lp), "R_AVATAR": _avatar_html(Rp),
        "L_NAME": Lp.display_name, "R_NAME": Rp.display_name,
        "L_FLAG": _flag_html(Lp, cache), "R_FLAG": _flag_html(Rp, cache),
        "L_RANK": Lp.rank_display, "R_RANK": Rp.rank_display,
        "L_MODS": Lp.mods, "R_MODS": Rp.mods,
        "L_GRADE": Lp.grade, "R_GRADE": Rp.grade,
        "L_GRADE_COLOR": _grade_color(Lp.grade), "R_GRADE_COLOR": _grade_color(Rp.grade),
        "L_ACC": f"{Lp.accuracy:.2f}", "R_ACC": f"{Rp.accuracy:.2f}",
        "MAP_TITLE": M.title or "Unknown", "MAP_ARTIST": M.artist or "",
        "MAP_DIFF": f"[{M.version}]" if M.version else "", "SR": f"{M.star_rating:.2f}",
        "MATCH_TITLE": match.match_title,
        "WIN_LEFT": "true" if win == "left" else "false",
        "WIN_RIGHT": "true" if win == "right" else "false",
        "ROWS": _cmp_rows(Lp, Rp),
    }
    html = _ENDCARD_TEMPLATE
    for k, v in repl.items():
        html = html.replace("%%" + k + "%%", str(v))
    return html


def render_endcard_preview(match: MatchData, cache: Path, out_png: Path) -> None:
    """Render the end card at its settled final state (for fast design review)."""
    from playwright.sync_api import sync_playwright
    html = build_endcard_html(match, cache)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": L.W, "height": L.H}, device_scale_factor=SCALE)
        page.set_content(html, wait_until="networkidle")
        page.evaluate("async () => { await document.fonts.ready; }")
        page.evaluate("window.seek(99)")  # jump to settled state
        page.wait_for_timeout(150)
        page.screenshot(path=str(out_png), clip={"x": 0, "y": 0, "width": L.W, "height": L.H})
        browser.close()


# Seconds for the whole intro → count-up → winner banner → pulse to fully settle
# at 1.0x speed. The seek timeline is calm and at rest by this point.
ENDCARD_SETTLE = 3.0

# The end card is a slow results animation: capturing it at the gameplay fps
# (up to 240) screenshots hundreds of near-identical 4K frames for no visible
# gain. Cap the *capture* rate here; ffmpeg frame-dups back to the gameplay fps.
ENDCARD_CAP_FPS = 60


def render_endcard_video(match: MatchData, cache: Path, out_mp4: Path,
                         speed: float = 1.0, hold_sec: float = 3.0, fade_sec: float = 0.6,
                         fps: int | None = None, encoder: str = "x264", quality: str = "high") -> None:
    """Frame-step the seekable end card through its full animation (scaled by
    `speed`), hold the settled frame for `hold_sec`, then fade to black over
    `fade_sec` so the video ends cleanly instead of hard-cutting. Output matches
    the gameplay encode so they concat clean."""
    from playwright.sync_api import sync_playwright
    if fps is None:
        fps = FPS
    speed = max(0.1, speed)
    # The end card is a slow results animation — capturing it at the gameplay fps
    # (e.g. 240) means screenshotting hundreds of near-identical 4K frames for no
    # visible benefit. Capture at a sane rate and let ffmpeg frame-dup back up to
    # the gameplay fps so the lossless stream-copy concat still matches.
    cap_fps = min(fps, ENDCARD_CAP_FPS)

    frames_dir = Path("_endcard_frames")
    if frames_dir.exists():
        for f in frames_dir.glob("*.png"):
            f.unlink()
    frames_dir.mkdir(exist_ok=True)

    html = build_endcard_html(match, cache)
    anim_real = ENDCARD_SETTLE / speed          # wall-clock length of the animation
    n_anim = max(1, int(anim_real * cap_fps))
    print(f"  rendering end card: {n_anim} frames (~{anim_real:.1f}s @ {speed:.2f}x, "
          f"capture {cap_fps}fps -> output {fps}fps) + {hold_sec}s hold + {fade_sec}s fade-out",
          flush=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": L.W, "height": L.H}, device_scale_factor=SCALE)
        page.set_content(html, wait_until="networkidle")
        page.evaluate("async () => { await document.fonts.ready; }")
        for i in range(n_anim):
            seek_t = (i / cap_fps) * speed       # scale real time -> timeline time
            page.evaluate(f"window.seek({seek_t})")
            page.screenshot(path=str(frames_dir / f"f{i:05d}.png"),
                            clip={"x": 0, "y": 0, "width": L.W, "height": L.H})
            if (i + 1) % 30 == 0 or i + 1 == n_anim:
                print(f"    end card: captured {i + 1}/{n_anim} frames", flush=True)
        browser.close()

    total = anim_real + hold_sec
    fade_st = max(0.0, total - fade_sec)
    # tpad holds the settled frame; fps={fps} dups frames up to the gameplay rate
    # so the output framerate matches the gameplay clip (clean stream-copy concat).
    vf = (f"[0:v]tpad=stop_mode=clone:stop_duration={hold_sec},fps={fps},"
          f"fade=t=out:st={fade_st:.3f}:d={fade_sec},format=yuv420p[v]")
    cmd = [
        FFMPEG, "-y",
        "-framerate", str(cap_fps), "-i", str(frames_dir / "f%05d.png"),
        "-f", "lavfi", "-t", f"{total}", "-i", "anullsrc=r=48000:cl=stereo",
        "-filter_complex", vf,
        "-map", "[v]", "-map", "1:a",
        *_venc(encoder, quality),
        "-c:a", "aac", "-b:a", "256k", "-t", f"{total}",
        str(out_mp4),
    ]
    if subprocess.run(cmd).returncode != 0:
        raise SystemExit("ffmpeg end-card assembly failed.")

    # the end card mp4 now holds everything; drop the hundreds of PNG frames so a
    # 4K/240fps run doesn't leave a big _endcard_frames folder behind.
    shutil.rmtree(frames_dir, ignore_errors=True)


def concat_videos(gameplay: Path, endcard: Path, out: Path,
                  encoder: str = "x264", quality: str = "high") -> None:
    """Append the end card to the gameplay. Try lossless stream-copy concat first
    (both files share our encode settings); fall back to a re-encode if needed."""
    listfile = Path("_concat_list.txt")
    listfile.write_text(f"file '{gameplay.resolve()}'\nfile '{endcard.resolve()}'\n")
    copy_cmd = [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listfile), "-c", "copy", str(out)]
    print(f"\n  -> joining gameplay + end card: {out}")
    if subprocess.run(copy_cmd).returncode == 0:
        listfile.unlink(missing_ok=True)
        return
    # fallback: re-encode concat (robust against any param mismatch), same encoder
    print("  (stream-copy concat failed; re-encoding)")
    fc = "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]"
    reenc = [FFMPEG, "-y", "-i", str(gameplay), "-i", str(endcard),
             "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
             *_venc(encoder, quality),
             "-c:a", "aac", "-b:a", "256k", "-ar", "48000", str(out)]
    if subprocess.run(reenc).returncode != 0:
        raise SystemExit("ffmpeg concat failed.")
    listfile.unlink(missing_ok=True)


# ----------------------------------------------------------------------------- #
# Main
# ----------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="Render a side-by-side osu! overlay video from two replays")
    ap.add_argument("left"); ap.add_argument("right")
    ap.add_argument("--danser-bin", default=None)
    ap.add_argument("--danser-video-dir", default=None)
    ap.add_argument("--match-json", default=None)
    ap.add_argument("--left-id", type=int, default=None)
    ap.add_argument("--right-id", type=int, default=None)
    ap.add_argument("--title", default="friendly · bo1")
    ap.add_argument("--cache", default="cache")
    ap.add_argument("--chrome-only", action="store_true")
    ap.add_argument("--endcard-preview", action="store_true", help="render just the end-card final frame to PNG")
    ap.add_argument("--no-endcard", action="store_true", help="skip the results end card")
    ap.add_argument("--endcard-seconds", type=float, default=3.0, help="how long the end card holds the settled frame")
    ap.add_argument("--endcard-speed", type=float, default=1.0, help="results-screen animation speed (1.0 = default, <1 slower, >1 faster)")
    ap.add_argument("--endcard-fade", type=float, default=0.6, help="fade-out-to-black length at the very end (seconds)")
    ap.add_argument("--tail-seconds", type=float, default=2.0, help="gameplay kept after the last note before the end card")
    ap.add_argument("--songs-dir", default=None, help="small folder danser reads + downloads into (its OsuSongsDir); keeps danser's import instant")
    ap.add_argument("--library-dir", default=None, help="user's existing osu! Songs folder; maps they already own are reused from here")
    ap.add_argument("--skins-dir", default=None, help="osu! Skins folder danser reads skins from (its OsuSkinsDir)")
    ap.add_argument("--left-skin", default=None, help="danser skin name for the left player's panel")
    ap.add_argument("--right-skin", default=None, help="danser skin name for the right player's panel")
    ap.add_argument("--resolution", choices=list(RES_SCALES.keys()), default="1080p", help="output resolution")
    ap.add_argument("--fps", type=int, default=60, help="output framerate (danser render + composite)")
    ap.add_argument("--ffmpeg", default=None,
                    help="path to the ffmpeg binary (defaults to 'ffmpeg' on PATH)")
    ap.add_argument("--music-volume", type=float, default=None, help="0-1 music volume (fallback for both sides)")
    ap.add_argument("--hitsound-volume", type=float, default=None, help="0-1 hitsound (sample) volume (fallback for both sides)")
    ap.add_argument("--master-volume", type=float, default=None, help="0-1 master volume (both sides)")
    # per-player audio: pick which side's music/hitsounds you hear. Defaults give a
    # single music bed (from P1) under BOTH players' hitsounds.
    ap.add_argument("--beatmap-hitsounds", action="store_true",
                    help="use the beatmap's own hitsounds instead of the skin's "
                         "(default forces skin hitsounds for a consistent sound)")
    ap.add_argument("--keep-fails", action="store_true",
                    help="render every replay exactly as recorded, including fails. By default "
                         "only osu!lazer replays are auto-rendered NoFail (danser's stable HP "
                         "model false-fails them); osu!stable replays are always left as-is")
    ap.add_argument("--left-music-volume", type=float, default=None, help="0-1 P1 music (default 1.0)")
    ap.add_argument("--right-music-volume", type=float, default=None, help="0-1 P2 music (default 0.0, avoids doubling the same track)")
    ap.add_argument("--left-hitsound-volume", type=float, default=None, help="0-1 P1 hitsounds (default 1.0)")
    ap.add_argument("--right-hitsound-volume", type=float, default=None, help="0-1 P2 hitsounds (default 1.0)")
    ap.add_argument("--encoder", choices=list(ENCODERS), default="x264",
                    help="video encoder for the composite + end card (default x264)")
    ap.add_argument("--quality", choices=QUALITY_LEVELS, default="high",
                    help="quality/size trade-off: lossless > high > balanced > compact (default high)")
    ap.add_argument("--nvenc", action="store_true",
                    help="(deprecated) shorthand for --encoder nvenc_h264")
    # ---- visual tweaks (danser HUD / background / cursor), all opt-in --------
    ap.add_argument("--bg-dim", type=float, default=0.95,
                    help="background dim 0-1 (0=full bg, 1=black; default 0.95)")
    ap.add_argument("--bg-blur", type=float, default=0.0,
                    help="background blur 0-1 (0=off; default 0)")
    ap.add_argument("--no-storyboards", action="store_true", help="don't load beatmap storyboards")
    ap.add_argument("--videos", action="store_true", help="load the beatmap's background video")
    ap.add_argument("--bloom", action="store_true", help="danser bloom/glow effect")
    ap.add_argument("--hit-lighting", action="store_true", help="flash hit lighting on each hit")
    ap.add_argument("--aim-error", action="store_true", help="show the aim-error scatter meter")
    ap.add_argument("--pp-components", action="store_true", help="break the pp counter into aim/speed/acc")
    ap.add_argument("--prominent-ur", action="store_true", help="enlarge + add decimals to the unstable rate")
    ap.add_argument("--ignore-sample-volume", action="store_true",
                    help="ignore the map's per-section hitsound volume changes")
    ap.add_argument("--show-mods", action="store_true", help="show each side's mods badge")
    ap.add_argument("--cursor-size", type=float, default=0.0,
                    help="cursor size in osu!px (0 = danser default 12)")
    ap.add_argument("--trail-length", type=float, default=1.0,
                    help="cursor-trail length multiplier (1.0 = danser default)")
    ap.add_argument("--hide-pp", action="store_true", help="hide the pp counter")
    ap.add_argument("--hide-hitcounts", action="store_true", help="hide the 300/100/50/miss counts")
    ap.add_argument("--hide-hiterror", action="store_true", help="hide the hit-error bar")
    ap.add_argument("--hide-keys", action="store_true", help="hide the key overlay")
    ap.add_argument("--hide-combo", action="store_true", help="hide the combo counter")
    ap.add_argument("--skip-render", action="store_true")
    ap.add_argument("--out", default="overlay_final.mp4")
    ap.add_argument("--png", default="overlay_base.png")
    args = ap.parse_args()

    global SCALE, FPS, FFMPEG
    SCALE = RES_SCALES.get(args.resolution, 1.0)
    FPS = max(1, int(args.fps))
    if args.ffmpeg:
        FFMPEG = args.ffmpeg
    if args.resolution != "1080p" or FPS != 60:
        print(f"  output: {args.resolution} (scale {SCALE:.3f}) @ {FPS}fps")

    cache = Path(args.cache); cache.mkdir(parents=True, exist_ok=True)

    if args.match_json:
        print(f"Loading {args.match_json} ...")
        d = json.loads(Path(args.match_json).read_text())
        match = MatchData(
            left=PlayerData(**{k: v for k, v in d["left"].items() if k in PlayerData.__annotations__}),
            right=PlayerData(**{k: v for k, v in d["right"].items() if k in PlayerData.__annotations__}),
            map=MapData(**{k: v for k, v in d["map"].items() if k in MapData.__annotations__}),
            match_title=d.get("match_title", args.title),
        )
    else:
        print("Assembling match data ...")
        match = assemble_match(args.left, args.right, cache_dir=args.cache,
                               left_id=args.left_id, right_id=args.right_id, match_title=args.title)
    if match.map.title or match.map.artist:
        print(f"  {match.left.display_name} vs {match.right.display_name}  ·  "
              f"{match.map.artist} - {match.map.title} ★{match.map.star_rating:.2f}")
    else:
        print(f"  {match.left.display_name} vs {match.right.display_name}  ·  "
              f"(map metadata unavailable — no osu! API key)")

    if args.endcard_preview:
        print("Rendering end-card preview ...")
        render_endcard_preview(match, cache, Path("endcard_preview.png"))
        print("  OK endcard_preview.png  (open to review the results design)")
        return

    print("Rendering overlay chrome ...")
    html = build_chrome_html(match, cache)
    Path("overlay_debug.html").write_text(html, encoding="utf-8")
    png = Path(args.png)
    render_chrome_png(html, png)
    print(f"  OK {png}")

    if args.chrome_only:
        print("\nchrome-only: open the PNG to review the design.")
        return

    if not args.danser_video_dir:
        raise SystemExit("--danser-video-dir is required for the full pipeline (or use --chrome-only).")
    video_dir = Path(args.danser_video_dir)
    danser = sync_poc.find_danser(args.danser_bin)
    staged_status = None

    # Stage just this match's map into the small render-songs folder danser reads
    # (reuse from the user's library if owned, else download). Keeps danser's
    # import instant no matter how large the user's library is.
    if args.songs_dir:
        from beatmap_fetch import ensure_render_map
        print(f"Staging beatmap into {args.songs_dir} ...")
        status, folder = ensure_render_map(match.map.md5, args.songs_dir,
                                           set_id=match.map.set_id, library=args.library_dir)
        staged_status = status
        msg = {
            "present": "already staged",
            "linked": "reused from your library",
            "downloaded": "downloaded",
            "downloaded_patched": "downloaded (patched to exact replay version)",
            "version_mismatch": "downloaded, but exact replay version not found — danser may fail",
            "unresolved": "could not resolve the beatmapset — is the map online?",
            "download_failed": "download failed on all mirrors",
        }.get(status, status)
        print(f"  beatmap: {msg}")
        if status in ("unresolved", "download_failed"):
            print("  (continuing anyway in case danser already has it cached)")

    def _vol(side, glob, default):
        return side if side is not None else (glob if glob is not None else default)
    lm = _vol(args.left_music_volume, args.music_volume, 1.0)
    rm = _vol(args.right_music_volume, args.music_volume, 0.0)
    lh = _vol(args.left_hitsound_volume, args.hitsound_volume, 1.0)
    rh = _vol(args.right_hitsound_volume, args.hitsound_volume, 1.0)
    print(f"  audio: P1 music {lm:.2f} / hits {lh:.2f}  ·  P2 music {rm:.2f} / hits {rh:.2f}")

    skin_hits = not args.beatmap_hitsounds
    vis = {
        "bg_dim": args.bg_dim, "bg_blur": args.bg_blur,
        "storyboards": not args.no_storyboards, "videos": args.videos,
        "bloom": args.bloom, "hit_lighting": args.hit_lighting,
        "aim_error": args.aim_error, "pp_components": args.pp_components,
        "prominent_ur": args.prominent_ur, "ignore_sample_volume": args.ignore_sample_volume,
        "show_mods": args.show_mods, "cursor_size": args.cursor_size,
        "trail_length": args.trail_length,
        "show_pp": not args.hide_pp, "show_hitcounts": not args.hide_hitcounts,
        "show_hiterror": not args.hide_hiterror, "show_keys": not args.hide_keys,
        "show_combo": not args.hide_combo,
    }
    left_patch = _render_patch(match.left.accent, skin=args.left_skin, songs_dir=args.songs_dir,
                               skins_dir=args.skins_dir, music_vol=lm,
                               hit_vol=lh, master_vol=args.master_volume, force_skin_hits=skin_hits,
                               vis=vis)
    right_patch = _render_patch(match.right.accent, skin=args.right_skin, songs_dir=args.songs_dir,
                                skins_dir=args.skins_dir, music_vol=rm,
                                hit_vol=rh, master_vol=args.master_volume, force_skin_hits=skin_hits,
                                vis=vis)
    print(f"  danser panel resolution: {L.dz_w}x{L.dz_h} (no crop)  ·  stats tinted per side")

    if not args.skip_render:
        _refresh_danser_db(danser, staged_status=staged_status)
        force_nf = not args.keep_fails
        l_mods2 = _mods2_arg(args.left, force_nofail=force_nf)
        r_mods2 = _mods2_arg(args.right, force_nofail=force_nf)
        nf_sides = [s for s, m in (("P1", l_mods2), ("P2", r_mods2)) if m]
        if nf_sides:
            print(f"  no-fail: {', '.join(nf_sides)} detected as osu!lazer -> rendering NoFail "
                  f"(stops danser's false fails; stable replays untouched)")
        t0 = time.time()
        sync_poc.render_replay(danser, Path(args.left), "ov_left",
                               extra_args=[a for a in (left_patch, l_mods2) if a])
        left_mp4 = sync_poc.locate_output(video_dir, "ov_left", t0)
        t1 = time.time()
        sync_poc.render_replay(danser, Path(args.right), "ov_right",
                               extra_args=[a for a in (right_patch, r_mods2) if a])
        right_mp4 = sync_poc.locate_output(video_dir, "ov_right", t1)
    else:
        left_mp4 = sync_poc.locate_output(video_dir, "ov_left", 0)
        right_mp4 = sync_poc.locate_output(video_dir, "ov_right", 0)

    # trim danser's long post-song outro: end gameplay shortly after the last note
    end_l = replay_end_seconds(args.left)
    end_r = replay_end_seconds(args.right)
    play_end = max(end_l, end_r)
    trim_to = (play_end + args.tail_seconds) if play_end > 0 else None
    if trim_to:
        print(f"  last note ~{play_end:.1f}s -> trimming gameplay to {trim_to:.1f}s (+{args.tail_seconds:.1f}s tail)")

    # resolve the encoder (--nvenc is a legacy shorthand for nvenc_h264)
    encoder = "nvenc_h264" if (args.nvenc and args.encoder == "x264") else args.encoder
    quality = args.quality

    # x264 (CPU) is the reliable default; the NVENC options are an opt-in. If the
    # chosen GPU encoder can't actually open here (e.g. av1_nvenc needs a newer
    # NVIDIA driver than installed), fall back to x264 so the render still finishes
    # instead of dying minutes in at the composite step.
    if encoder != "x264" and not _encoder_available(encoder):
        print(f"  ! encoder '{ENCODERS[encoder]['label']}' isn't usable on this "
              f"system — your GPU driver may be too old for it. Falling back to "
              f"x264 (CPU). Pick NVENC H.264/HEVC in Settings if your driver "
              f"supports those, or update your NVIDIA driver for AV1.")
        encoder = "x264"

    print(f"  encoder: {ENCODERS[encoder]['label']}  ·  quality: {quality}")

    out = Path(args.out)
    if args.no_endcard:
        composite(png, left_mp4, right_mp4, out, trim_to=trim_to, encoder=encoder, quality=quality)
        print(f"\nDone -> {out}")
        return

    # gameplay to a temp file, then append the animated end card
    gameplay = Path("_gameplay.mp4")
    composite(png, left_mp4, right_mp4, gameplay, trim_to=trim_to, encoder=encoder, quality=quality)
    endcard = Path("_endcard.mp4")
    render_endcard_video(match, cache, endcard, speed=args.endcard_speed,
                         hold_sec=args.endcard_seconds, fade_sec=args.endcard_fade,
                         encoder=encoder, quality=quality)
    concat_videos(gameplay, endcard, out, encoder=encoder, quality=quality)
    gameplay.unlink(missing_ok=True)
    endcard.unlink(missing_ok=True)
    print(f"\nDone -> {out}  (gameplay + results end card)")


# ============================================================================= #
# CHROME TEMPLATE — identity frame, spoiler-free, slim footer
# ============================================================================= #
_CHROME_TEMPLATE = r"""<!DOCTYPE html><html><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Exo+2:wght@400;500;700;800;900&family=JetBrains+Mono:wght@400;500;700&family=Orbitron:wght@500;700;900&display=swap" rel="stylesheet">
<style>
:root{--ink:#0a0a0d;--pink:#ff66ab;--pink-soft:#ff9ccb;--ice:#66d9ff;--line:#23232c;--muted:#7d7488;}
*{box-sizing:border-box;margin:0;padding:0;}
html,body{width:1920px;height:1080px;overflow:hidden;font-family:"Exo 2",sans-serif;}
#stage{position:absolute;inset:0;color:#f3ecf7;
  background:radial-gradient(110% 70% at 0% 45%,rgba(255,102,171,.15),transparent 52%),
             radial-gradient(110% 70% at 100% 45%,rgba(102,217,255,.15),transparent 52%),var(--ink);}
#stage::before{content:"";position:absolute;inset:0;z-index:1;pointer-events:none;
  background:repeating-linear-gradient(0deg,transparent 0 3px,rgba(255,255,255,.013) 3px 4px);}
.grid{position:absolute;inset:0;z-index:0;opacity:.45;
  background:linear-gradient(90deg,rgba(255,255,255,.035) 1px,transparent 1px) 0 0/76px 100%;}
.trifield{position:absolute;inset:0;z-index:0;overflow:hidden;}
.tri{position:absolute;}

.top{position:absolute;top:0;left:0;right:0;height:140px;z-index:6;display:flex;align-items:stretch;}
.nameblock{flex:1;display:flex;align-items:center;gap:24px;padding:0 48px;}
.nameblock.l{background:linear-gradient(90deg,rgba(255,102,171,.14),transparent 70%);}
.nameblock.r{flex-direction:row-reverse;text-align:right;background:linear-gradient(270deg,rgba(102,217,255,.14),transparent 70%);}
.edge{width:6px;align-self:stretch;background:var(--c);box-shadow:0 0 20px var(--c);}
.av{position:relative;width:92px;height:92px;flex:none;}
.av .ap{position:absolute;inset:-10px;border-radius:50%;border:3px solid var(--c);opacity:.42;}
.av .disc{position:absolute;inset:0;border-radius:50%;border:4px solid var(--c);overflow:hidden;
  background:radial-gradient(circle at 35% 30%,#2c2336,#100c16);display:grid;place-items:center;
  font-weight:900;font-size:36px;color:var(--c);box-shadow:0 0 24px -4px var(--c);}
.av .disc img{width:100%;height:100%;object-fit:cover;}
.nm{font-weight:800;font-size:44px;line-height:1;letter-spacing:.5px;}
.sub{margin-top:10px;display:flex;align-items:center;gap:12px;font-family:"JetBrains Mono",monospace;font-size:17px;color:var(--muted);}
.nameblock.r .sub{flex-direction:row-reverse;}
.sub .rk b{color:var(--c);}
.flagimg{width:30px;height:20px;border-radius:3px;object-fit:cover;box-shadow:0 0 0 1px rgba(255,255,255,.15);}
.flagtxt{font-weight:700;color:#e9e2f0;}
.dot{color:var(--muted);}

.plate{width:600px;flex:none;background:#0c0c12;border-bottom:2px solid var(--line);
  clip-path:polygon(46px 0,calc(100% - 46px) 0,100% 100%,0 100%);
  display:flex;align-items:center;justify-content:center;gap:22px;}
.med{position:relative;width:92px;height:92px;flex:none;display:grid;place-items:center;}
.med .r{position:absolute;border-radius:50%;border:2px solid;}
.med .r1{inset:0;border-color:rgba(255,255,255,.16);}
.med .r2{inset:11px;border-color:rgba(255,102,171,.55);}
.med .r3{inset:22px;border-color:rgba(102,217,255,.55);}
.med .ap2{position:absolute;inset:-8px;border-radius:50%;border:2px solid rgba(255,255,255,.18);}
.med .core{width:48px;height:48px;border-radius:50%;background:linear-gradient(135deg,var(--pink),var(--ice));
  display:grid;place-items:center;color:#160f1d;font-family:"Orbitron";font-weight:900;font-size:16px;
  box-shadow:0 0 26px -6px rgba(255,102,171,.8);line-height:1;}
.ttl{text-align:left;max-width:330px;}
.ttl .eyebrow{font-family:"JetBrains Mono",monospace;font-size:10px;letter-spacing:4px;text-transform:uppercase;color:var(--muted);}
.ttl .song{font-weight:800;font-size:24px;line-height:1.05;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.ttl .row{margin-top:5px;display:flex;align-items:center;gap:10px;}
.ttl .artist{font-family:"JetBrains Mono",monospace;font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:200px;}
.ttl .diff{font-size:11px;color:var(--pink-soft);border:1px solid rgba(255,102,171,.32);border-radius:999px;padding:2px 9px;white-space:nowrap;}

.panel{position:absolute;background:linear-gradient(180deg,#08070b,#060509);z-index:2;
  top:%%P_TOP%%px;width:%%P_W%%px;height:%%P_H%%px;}
.panel.l{left:%%PL_LEFT%%px;border:3px solid var(--pink);box-shadow:0 0 44px -16px var(--pink);}
.panel.r{left:%%PR_LEFT%%px;border:3px solid var(--ice);box-shadow:0 0 44px -16px var(--ice);}

.footer{position:absolute;bottom:0;left:0;right:0;height:104px;z-index:6;
  display:grid;grid-template-columns:1fr auto 1fr;align-items:center;
  border-top:2px solid var(--line);background:linear-gradient(180deg,#0b0b10,#090910);}
.fside{display:flex;align-items:center;gap:14px;padding:0 52px;}
.fside.r{flex-direction:row-reverse;}
.modchip{font-family:"JetBrains Mono",monospace;font-size:15px;letter-spacing:1.5px;color:#e9e2f0;
  border:1px solid var(--c);border-radius:7px;padding:6px 13px;box-shadow:0 0 16px -6px var(--c);}
.modlab{font-family:"JetBrains Mono",monospace;font-size:11px;letter-spacing:3px;text-transform:uppercase;color:var(--muted);}
.fcenter{text-align:center;padding:0 50px;border-left:1px solid var(--line);border-right:1px solid var(--line);min-width:360px;}
.fcenter .mt{font-weight:800;font-size:24px;letter-spacing:1px;text-transform:uppercase;}
.fcenter .mm{font-family:"JetBrains Mono",monospace;font-size:12px;letter-spacing:3px;color:var(--muted);margin-top:5px;text-transform:uppercase;}
</style></head>
<body><div id="stage">
  <div class="grid"></div>
  <div class="trifield" id="tris"></div>

  <div class="top">
    <div class="nameblock l" style="--c:var(--pink)">
      <div class="edge"></div>
      <div class="av"><div class="ap"></div><div class="disc">%%L_AVATAR%%</div></div>
      <div><div class="nm">%%L_NAME%%</div>
        <div class="sub">%%L_FLAG%%<span class="rk">%%L_RANK%%</span><span class="dot">·</span><span>%%L_PP%%</span></div></div>
    </div>
    <div class="plate">
      <div class="med"><div class="ap2"></div><div class="r r1"></div><div class="r r2"></div><div class="r r3"></div><div class="core">%%SR%%</div></div>
      <div class="ttl"><div class="eyebrow">head to head</div>
        <div class="song">%%MAP_TITLE%%</div>
        <div class="row"><span class="artist">%%MAP_ARTIST%%</span><span class="diff">%%MAP_DIFF%%</span></div></div>
    </div>
    <div class="nameblock r" style="--c:var(--ice)">
      <div class="edge"></div>
      <div class="av"><div class="ap"></div><div class="disc">%%R_AVATAR%%</div></div>
      <div><div class="nm">%%R_NAME%%</div>
        <div class="sub">%%R_FLAG%%<span class="rk">%%R_RANK%%</span><span class="dot">·</span><span>%%R_PP%%</span></div></div>
    </div>
  </div>

  <div class="panel l"></div>
  <div class="panel r"></div>

  <div class="footer">
    <div class="fside" style="--c:var(--pink)"><span class="modlab">mods</span><span class="modchip">%%L_MODS%%</span></div>
    <div class="fcenter"><div class="mt">%%MATCH_TITLE%%</div><div class="mm">%%MAP_DIFF%% · ★%%SR%%</div></div>
    <div class="fside r" style="--c:var(--ice)"><span class="modlab">mods</span><span class="modchip">%%R_MODS%%</span></div>
  </div>
</div>
<script>
  const tf=document.getElementById('tris');
  for(let i=0;i<26;i++){
    const side=Math.random()<.5,size=20+Math.random()*46;
    const t=document.createElementNS('http://www.w3.org/2000/svg','svg');
    t.setAttribute('viewBox','0 0 100 100');t.setAttribute('width',size);t.setAttribute('height',size);
    t.classList.add('tri');
    t.innerHTML='<polygon points="50,5 95,95 5,95" fill="'+(side?'#ff66ab':'#66d9ff')+'"/>';
    t.style.left=(Math.random()*100)+'%';
    t.style.bottom=(Math.random()*1080-40)+'px';
    t.style.transform='rotate('+(Math.random()*40-20)+'deg)';
    t.style.opacity=(.09+Math.random()*.12).toFixed(3);
    tf.appendChild(t);
  }
</script>
</body></html>"""



# ============================================================================= #
# END-CARD TEMPLATE — animated results screen, seekable via window.seek(t)
# ============================================================================= #
_ENDCARD_TEMPLATE = r"""<!DOCTYPE html><html><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Exo+2:wght@400;500;700;800;900&family=JetBrains+Mono:wght@400;500;700&family=Orbitron:wght@500;700;900&display=swap" rel="stylesheet">
<style>
:root{--ink:#0a0a0d;--pink:#ff66ab;--ice:#66d9ff;--line:#23232c;--muted:#7d7488;}
*{box-sizing:border-box;margin:0;padding:0;}
html,body{width:1920px;height:1080px;overflow:hidden;font-family:"Exo 2",sans-serif;}
#stage{position:absolute;inset:0;color:#f3ecf7;
  background:radial-gradient(120% 80% at 0% 50%,rgba(255,102,171,.15),transparent 55%),
             radial-gradient(120% 80% at 100% 50%,rgba(102,217,255,.15),transparent 55%),var(--ink);}
#stage::before{content:"";position:absolute;inset:0;z-index:1;pointer-events:none;
  background:repeating-linear-gradient(0deg,transparent 0 3px,rgba(255,255,255,.013) 3px 4px);}
.grid{position:absolute;inset:0;z-index:0;opacity:.4;
  background:linear-gradient(90deg,rgba(255,255,255,.035) 1px,transparent 1px) 0 0/76px 100%;}
.trifield{position:absolute;inset:0;z-index:0;overflow:hidden;}
.tri{position:absolute;}
.gold{color:#ffd24a;}

.head{position:absolute;top:40px;left:0;right:0;text-align:center;z-index:5;}
.head .eyebrow{font-family:"JetBrains Mono",monospace;font-size:13px;letter-spacing:8px;text-transform:uppercase;color:var(--muted);}
.head .ttl{font-weight:900;font-size:48px;letter-spacing:3px;margin-top:6px;}
.head .map{font-family:"JetBrains Mono",monospace;font-size:14px;color:var(--muted);margin-top:7px;}

/* identity cards */
.card{position:absolute;top:188px;width:792px;height:150px;z-index:4;display:flex;align-items:center;gap:22px;
  padding:0 30px;border:2px solid var(--c);border-radius:16px;box-shadow:0 0 50px -20px var(--c);
  background:linear-gradient(90deg,var(--cdim),rgba(14,11,18,.92));}
.card.l{left:88px;} .card.r{right:88px;flex-direction:row-reverse;text-align:right;background:linear-gradient(270deg,var(--cdim),rgba(14,11,18,.92));}
.ava{width:92px;height:92px;border-radius:50%;border:4px solid var(--c);overflow:hidden;flex:none;box-shadow:0 0 22px -6px var(--c);
  background:radial-gradient(circle at 35% 30%,#2c2336,#100c16);display:grid;place-items:center;font-weight:900;font-size:36px;color:var(--c);}
.ava img{width:100%;height:100%;object-fit:cover;}
.who{flex:1;min-width:0;}
.nm{font-weight:900;font-size:40px;line-height:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.meta{margin-top:8px;display:flex;align-items:center;gap:10px;font-family:"JetBrains Mono",monospace;font-size:15px;color:var(--muted);}
.card.r .meta{flex-direction:row-reverse;}
.flagimg{width:28px;height:19px;border-radius:3px;object-fit:cover;box-shadow:0 0 0 1px rgba(255,255,255,.15);}
.flagtxt{font-weight:700;color:#e9e2f0;}
.accbox{text-align:center;}
.accbox .a{font-family:"Orbitron";font-weight:700;font-size:38px;line-height:1;}
.accbox .a small{font-size:18px;color:var(--muted);}
.accbox .al{font-family:"JetBrains Mono",monospace;font-size:10px;letter-spacing:2px;color:var(--muted);text-transform:uppercase;margin-top:3px;}
.gbadge{width:104px;height:104px;flex:none;border-radius:14px;display:grid;place-items:center;
  font-family:"Orbitron";font-weight:900;font-size:66px;border:2px solid currentColor;background:rgba(255,255,255,.03);}

.winbadge{position:absolute;top:150px;z-index:6;display:flex;align-items:center;gap:10px;
  background:linear-gradient(135deg,var(--c),#fff);color:#160f1d;font-weight:900;font-size:22px;letter-spacing:3px;
  padding:9px 22px;border-radius:9px;text-transform:uppercase;box-shadow:0 0 40px -8px var(--c);}
.winbadge.l{left:88px;} .winbadge.r{right:88px;}

.vs{position:absolute;left:50%;top:263px;transform:translate(-50%,-50%);z-index:5;
  font-family:"Orbitron";font-weight:900;font-size:46px;color:#fff;text-shadow:0 0 24px rgba(255,255,255,.4);}

/* comparison table */
.cmp{position:absolute;top:382px;left:50%;transform:translateX(-50%);width:1240px;z-index:3;
  background:rgba(16,12,22,.55);border:1px solid var(--line);border-radius:18px;padding:14px 40px;}
.row{padding:15px 0;}
.row+.row{border-top:1px solid var(--line);}
.rtop{display:flex;align-items:baseline;justify-content:space-between;font-family:"Orbitron";font-weight:700;font-size:30px;}
.rtop .lab{font-family:"JetBrains Mono",monospace;font-weight:400;font-size:14px;letter-spacing:3px;color:var(--muted);text-transform:uppercase;}
.lv,.rv{color:#6b6577;min-width:200px;}
.lv{text-align:left;} .rv{text-align:right;}
.lv.win{color:var(--pink);} .rv.win{color:var(--ice);}
.bar{height:7px;border-radius:4px;margin-top:11px;background:#16131c;overflow:hidden;display:flex;}
.bar .bl{background:linear-gradient(90deg,var(--pink),#ff9ccb);width:50%;}
.bar .br{background:linear-gradient(90deg,#a6e9ff,var(--ice));width:50%;margin-left:auto;}
</style></head>
<body><div id="stage">
  <div class="grid"></div>
  <div class="trifield" id="tris"></div>

  <div class="head" id="head">
    <div class="eyebrow">final results</div>
    <div class="ttl">%%MATCH_TITLE%%</div>
    <div class="map">%%MAP_ARTIST%% - %%MAP_TITLE%% %%MAP_DIFF%% &middot; &#9733;%%SR%%</div>
  </div>

  <div class="winbadge l" id="winL" style="--c:var(--pink)"><span>&#9818;</span>WINNER</div>
  <div class="winbadge r" id="winR" style="--c:var(--ice)"><span>&#9818;</span>WINNER</div>

  <div class="card l" id="cardL" style="--c:var(--pink);--cdim:rgba(255,102,171,.13)">
    <div class="ava">%%L_AVATAR%%</div>
    <div class="who"><div class="nm">%%L_NAME%%</div>
      <div class="meta">%%L_FLAG%%<span>%%L_RANK%%</span><span>%%L_MODS%%</span></div></div>
    <div class="accbox"><div class="a"><span class="countup" data-final="%%L_ACC%%" data-fmt="pct">0.00</span><small>%</small></div><div class="al">acc</div></div>
    <div class="gbadge" style="color:%%L_GRADE_COLOR%%">%%L_GRADE%%</div>
  </div>

  <div class="card r" id="cardR" style="--c:var(--ice);--cdim:rgba(102,217,255,.13)">
    <div class="ava">%%R_AVATAR%%</div>
    <div class="who"><div class="nm">%%R_NAME%%</div>
      <div class="meta">%%R_FLAG%%<span>%%R_RANK%%</span><span>%%R_MODS%%</span></div></div>
    <div class="accbox"><div class="a"><span class="countup" data-final="%%R_ACC%%" data-fmt="pct">0.00</span><small>%</small></div><div class="al">acc</div></div>
    <div class="gbadge" style="color:%%R_GRADE_COLOR%%">%%R_GRADE%%</div>
  </div>

  <div class="vs" id="vs">VS</div>

  <div class="cmp" id="cmp">%%ROWS%%</div>
</div>
<script>
  const tf=document.getElementById('tris');
  for(let i=0;i<28;i++){
    const side=Math.random()<.5,size=20+Math.random()*50;
    const t=document.createElementNS('http://www.w3.org/2000/svg','svg');
    t.setAttribute('viewBox','0 0 100 100');t.setAttribute('width',size);t.setAttribute('height',size);
    t.classList.add('tri');
    t.innerHTML='<polygon points="50,5 95,95 5,95" fill="'+(side?'#ff66ab':'#66d9ff')+'"/>';
    t.style.left=(Math.random()*100)+'%';t.style.top=(Math.random()*100)+'%';
    t.style.transform='rotate('+(Math.random()*40-20)+'deg)';
    t.style.opacity=(.05+Math.random()*.08).toFixed(3);
    tf.appendChild(t);
  }
  const WIN_LEFT=%%WIN_LEFT%%, WIN_RIGHT=%%WIN_RIGHT%%;
  const clamp=(x)=>Math.max(0,Math.min(1,x));
  const ease=(x)=>1-Math.pow(1-clamp(x),3);
  const back=(x)=>{x=clamp(x);const c=1.9;return 1+(c+1)*Math.pow(x-1,3)+c*Math.pow(x-1,2);};
  const fmtNum=(n)=>Math.round(n).toLocaleString('en-US');

  window.seek=function(t){
    const slide=ease(t/0.6);
    document.getElementById('cardL').style.transform='translateX('+(-960*(1-slide))+'px)';
    document.getElementById('cardL').style.opacity=slide;
    document.getElementById('cardR').style.transform='translateX('+(960*(1-slide))+'px)';
    document.getElementById('cardR').style.opacity=slide;
    const headO=ease((t-0.15)/0.5);
    document.getElementById('head').style.opacity=headO;
    const vsO=ease((t-0.4)/0.5);
    document.getElementById('vs').style.opacity=vsO;
    const cmpO=ease((t-0.45)/0.55);
    const cmp=document.getElementById('cmp');
    cmp.style.opacity=cmpO;
    cmp.style.transform='translateX(-50%) translateY('+(30*(1-cmpO))+'px)';

    const cu=ease((t-0.6)/1.1);
    document.querySelectorAll('.countup').forEach(el=>{
      const f=parseFloat(el.dataset.final), fmt=el.dataset.fmt, v=f*cu;
      if(fmt==='pct') el.textContent=v.toFixed(2);
      else if(fmt==='combo') el.textContent=Math.round(v)+'x';
      else el.textContent=fmtNum(v);
    });
    document.querySelectorAll('.bar').forEach(b=>{
      const tgt=parseFloat(b.dataset.l), w=50+(tgt-50)*cu;
      b.querySelector('.bl').style.width=w+'%';
      b.querySelector('.br').style.width=(100-w)+'%';
    });

    const wEl=WIN_LEFT?document.getElementById('winL'):(WIN_RIGHT?document.getElementById('winR'):null);
    document.getElementById('winL').style.display=WIN_LEFT?'flex':'none';
    document.getElementById('winR').style.display=WIN_RIGHT?'flex':'none';
    const loser=WIN_LEFT?document.getElementById('cardR'):(WIN_RIGHT?document.getElementById('cardL'):null);
    if(wEl){
      const o=clamp((t-1.7)/0.4), wb=back((t-1.7)/0.6);
      wEl.style.opacity=o;
      wEl.style.transform='translateY('+(-36*(1-clamp(wb)))+'px) scale('+(0.6+0.4*clamp(wb))+')';
      const pulse=t>2.3?(1+0.05*Math.sin((t-2.3)*6)*Math.exp(-(t-2.3)*2.2)):1;
      wEl.style.filter='drop-shadow(0 0 '+(16*pulse)+'px var(--c))';
      if(loser) loser.style.opacity=String(1-0.22*clamp((t-1.7)/0.5));
    }
  };
  window.seek(0);
</script>
</body></html>"""


if __name__ == "__main__":
    main()
