"""Headless GUI logic tests for the dynamic 2–4 player controls (phase 6).

Runs Qt in offscreen mode. Skipped entirely if PySide6 isn't installed.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

import renderer_gui as g  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    yield a


@pytest.fixture
def win(app, monkeypatch, tmp_path):
    # stub config so __init__ skips the welcome + first-run download dialogs
    fake = {"welcomed": True, "danser_bin": "renderer_gui.py",
            "ffmpeg_bin": "/usr/bin/ffmpeg", "output_dir": str(tmp_path),
            "resolution": "1080p", "fps": 60, "tail_seconds": 2.0, "endcard_seconds": 3.0}
    monkeypatch.setattr(g, "load_config", lambda: dict(fake))
    monkeypatch.setattr(g, "save_config", lambda c: None)
    monkeypatch.setattr(g.MainWindow, "_have_danser", lambda self: True)
    w = g.MainWindow()
    yield w
    w.deleteLater()


def _fill(win, n):
    while len(win.slots) < n:
        win._add_player_slot()
    for i, s in enumerate(win.slots):
        s.zone.path = f"p{i}.osr"
    win._refresh_enabled()


def test_starts_with_two_slots(win):
    assert len(win.slots) == 2


def test_add_and_cap_at_four(win):
    for _ in range(5):
        win._add_player_slot()
    assert len(win.slots) == 4
    assert not win.addPlayerBtn.isEnabled()


def test_cannot_remove_below_two(win):
    win._remove_player_slot(win.slots[-1])
    assert len(win.slots) == 2
    # remove buttons hidden at the floor
    assert not win.slots[0].removeBtn.isVisible()


def test_remove_reindexes_titles(win):
    _fill(win, 4)
    win._remove_player_slot(win.slots[1])
    assert [s.zone._title for s in win.slots] == ["PLAYER 1", "PLAYER 2", "PLAYER 3"]


def test_ffa_replay_args_are_just_paths(win):
    _fill(win, 3)
    assert win._replay_args() == ["p0.osr", "p1.osr", "p2.osr"]


def test_team_mode_only_at_four(win):
    _fill(win, 3)
    # the 2v2 item is disabled with 3 players
    assert not win.modeCombo.model().item(1).isEnabled()
    win._add_player_slot()
    win.slots[3].zone.path = "p3.osr"
    assert win.modeCombo.model().item(1).isEnabled()


def test_team_args_and_column_grouping(win):
    _fill(win, 4)
    win.modeCombo.setCurrentIndex(1)
    win._sync_player_ui()
    args = win._replay_args()
    assert "--mode" in args and "team" in args
    ti = args.index("--teams")
    assert args[ti + 1:ti + 5] == ["0", "1", "0", "1"]   # alternating -> columns


def test_reducing_players_reverts_team_mode(win):
    _fill(win, 4)
    win.modeCombo.setCurrentIndex(1); win._sync_player_ui()
    assert win._team_mode()
    win._remove_player_slot(win.slots[3])
    assert not win._team_mode()
    assert win.modeCombo.currentIndex() == 0


def test_skins_arg_marks_defaults_with_dash(win):
    _fill(win, 3)
    win.slots[1].skin.addItem("Aristia")
    win.slots[1].skin.setCurrentText("Aristia")
    args = win._replay_args()
    si = args.index("--skins")
    assert args[si + 1:si + 4] == ["-", "Aristia", "-"]


def test_render_disabled_until_all_filled(win):
    win._add_player_slot()          # 3 slots, none filled
    assert not win.renderBtn.isEnabled()
    for i, s in enumerate(win.slots):
        s.zone.path = f"p{i}.osr"
    win._refresh_enabled()
    assert win.renderBtn.isEnabled()


# --- per-player audio helpers ---------------------------------------------------

def test_player_audio_defaults():
    cfg = {"player_music": [100, 0, 0, 0], "player_hits": [100, 100, 100, 100]}
    assert g.player_music_vol(cfg, 0) == 100
    assert g.player_music_vol(cfg, 1) == 0
    assert g.player_hits_vol(cfg, 2) == 100


def test_player_audio_legacy_fallback():
    # no player_* lists -> fall back to legacy left/right, then defaults
    cfg = {"left_music_volume": 80, "right_music_volume": 20}
    assert g.player_music_vol(cfg, 0) == 80
    assert g.player_music_vol(cfg, 1) == 20
    assert g.player_music_vol(cfg, 3) == 0      # extra player: no music bed
    assert g.player_hits_vol(cfg, 3) == 100     # extra player: hitsounds on


# --- sync warning gating --------------------------------------------------------

def test_sync_warning_blocks_render(win, monkeypatch):
    from osr_parser import Mods
    _fill(win, 2)
    # make the two replays report different speeds
    speeds = {"p0.osr": Mods(0), "p1.osr": Mods.DOUBLE_TIME}
    monkeypatch.setattr(g, "parse_replay",
                        lambda p, **k: type("R", (), {"mods": speeds[p], "mods_str": "x"})())
    monkeypatch.setattr(g, "speed_multiplier",
                        lambda mods: 1.5 if mods == Mods.DOUBLE_TIME else 1.0)
    win._refresh_enabled()
    assert win._sync_warning() is not None
    assert not win.renderBtn.isEnabled()        # blocked
    assert win.previewBtn.isEnabled()           # preview still allowed
