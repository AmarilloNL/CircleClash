"""Tests for the N-input composite filter graph (phase 4).

Two layers:
  * pure string tests on _overlay_chain / _amix_chain (fast, always run)
  * a real ffmpeg smoke test that actually composites synthetic clips (skipped if
    ffmpeg is missing) — this is what proves the generated graph is valid for 2–4.
"""
import shutil
import subprocess

import pytest

import make_overlay_video as m
from make_overlay_video import panels_for, _overlay_chain, _amix_chain, _s


# --- pure filter-graph structure ------------------------------------------------

@pytest.mark.parametrize("n", [2, 3, 4])
def test_overlay_chain_has_scale_and_overlay_per_panel(n):
    panels = panels_for(n)
    fc = _overlay_chain(panels)
    assert fc.count(":flags=bicubic,setsar=1") == n      # one scale per clip
    assert fc.count("overlay=") == n                      # one overlay per panel
    # each clip input index 1..n is referenced
    for i in range(n):
        assert f"[{i + 1}:v]scale=" in fc


def test_overlay_chain_ends_without_label():
    # the caller appends [v]/[vov], so the chain must end on a bare overlay
    fc = _overlay_chain(panels_for(3))
    assert fc.rstrip().endswith(f"overlay={_s(panels_for(3)[-1].dz_x)}:{_s(panels_for(3)[-1].dz_y)}")


def test_overlay_positions_match_panel_dz():
    panels = panels_for(4)
    fc = _overlay_chain(panels)
    for p in panels:
        assert f"overlay={_s(p.dz_x)}:{_s(p.dz_y)}" in fc


def test_two_panel_chain_matches_legacy_coords():
    # regression guard: n=2 overlays at the classic left/right danser coords
    fc = _overlay_chain(panels_for(2))
    assert f"overlay={_s(m.L.dz_lx)}:{_s(m.L.dz_y)}" in fc
    assert f"overlay={_s(m.L.dz_rx)}:{_s(m.L.dz_y)}" in fc


@pytest.mark.parametrize("n", [2, 3, 4])
def test_amix_inputs_and_labels(n):
    a = _amix_chain(n)
    assert f"amix=inputs={n}:normalize=0" in a
    for i in range(n):
        assert f"[{i + 1}:a]" in a
    assert a.endswith("[amx]")


# --- real ffmpeg smoke test -----------------------------------------------------

ffmpeg = shutil.which("ffmpeg")


def _make_clip(path, w, h, color, dur=1.0):
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=30:duration={dur}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
         "-pix_fmt", "yuv420p", "-shortest", str(path)],
        check=True, capture_output=True)


def _make_png(path, w, h):
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", f"color=c=black:size={w}x{h}",
         "-frames:v", "1", str(path)],
        check=True, capture_output=True)


@pytest.mark.skipif(not ffmpeg, reason="ffmpeg not installed")
@pytest.mark.parametrize("n", [2, 3, 4])
def test_composite_runs_for_n_players(tmp_path, monkeypatch, n):
    monkeypatch.setattr(m, "FFMPEG", ffmpeg)
    monkeypatch.setattr(m, "FPS", 30)
    monkeypatch.setattr(m, "SCALE", 1.0)
    panels = panels_for(n)
    clips = []
    for i, p in enumerate(panels):
        c = tmp_path / f"clip{i}.mp4"
        _make_clip(c, p.dz_w, p.dz_h, i)
        clips.append(c)
    png = tmp_path / "overlay.png"
    _make_png(png, m.L.W, m.L.H)
    out = tmp_path / "out.mp4"

    m.composite(png, clips, panels, out, trim_to=None, encoder="x264", quality="compact")

    assert out.exists() and out.stat().st_size > 0
    # verify output resolution is the full canvas
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True)
    assert probe.stdout.strip() == f"{m.L.W},{m.L.H}"


@pytest.mark.skipif(not ffmpeg, reason="ffmpeg not installed")
def test_composite_rejects_mismatched_counts(tmp_path):
    panels = panels_for(3)
    with pytest.raises(ValueError):
        m.composite(tmp_path / "x.png", [tmp_path / "a.mp4"], panels, tmp_path / "o.mp4")
