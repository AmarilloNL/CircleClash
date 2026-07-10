"""Tests for replay-sync validation (speed-mod compatibility)."""
import types

import pytest

import make_overlay_video as m
from sync_poc import speed_multiplier
from osr_parser import Mods


def test_speed_multiplier_values():
    assert speed_multiplier(Mods(0)) == 1.0
    assert speed_multiplier(Mods.HIDDEN | Mods.HARD_ROCK) == 1.0
    assert speed_multiplier(Mods.HALF_TIME) == 0.75
    assert speed_multiplier(Mods.DOUBLE_TIME) == 1.5
    assert speed_multiplier(Mods.NIGHTCORE) == 1.5


def test_validate_sync_passes_on_matching_speed(sample_replays):
    # the four samples are all HR/HD -> 1.0x, so this must not raise
    m.validate_sync(sample_replays[:4] if len(sample_replays) >= 4 else sample_replays[:2])


def _fake_parse(mods_by_path):
    def _p(path, **kw):
        return types.SimpleNamespace(player=path, mods=mods_by_path[path],
                                     mods_str="?")
    return _p


def test_validate_sync_rejects_mixed_speed(monkeypatch):
    paths = ["a.osr", "b.osr"]
    monkeypatch.setattr(m, "parse_replay",
                        _fake_parse({"a.osr": Mods(0), "b.osr": Mods.DOUBLE_TIME}))
    with pytest.raises(SystemExit) as e:
        m.validate_sync(paths)
    assert "different speeds" in str(e.value)


def test_validate_sync_allows_same_nonstandard_speed(monkeypatch):
    # two DoubleTime replays are fine — same speed, still syncable
    paths = ["a.osr", "b.osr"]
    monkeypatch.setattr(m, "parse_replay",
                        _fake_parse({"a.osr": Mods.DOUBLE_TIME,
                                     "b.osr": Mods.DOUBLE_TIME | Mods.HIDDEN}))
    m.validate_sync(paths)   # must not raise


def test_report_sync_no_ffprobe_is_silent(monkeypatch, capsys):
    monkeypatch.setattr(m, "_ffprobe_bin", lambda: None)
    m.report_sync([])          # should not raise or print
    assert capsys.readouterr().out == ""
