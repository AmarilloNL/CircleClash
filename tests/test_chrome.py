"""Structural tests for the overlay chrome builder dispatch (phase 3).

These build the HTML string only (no Playwright render), so they're fast and offline.
"""
from pathlib import Path

import pytest

from make_overlay_video import build_chrome_html
from match_assembler import MatchData, MapData, PlayerData, assign_accents


def _p(name, accent, mods="NM", team=None):
    return PlayerData(
        side="left", accent=accent, team=team, play_username=name, mods=mods,
        accuracy=99.0, score=100, max_combo=100, perfect=False,
        n300=100, n100=0, n50=0, nmiss=0, grade="S", silver=False,
    )


def _match(n, mode="ffa", teams=None):
    accents = assign_accents(n, mode, teams)
    players = [_p(f"Player{i}", accents[i], mods=("HR" if i % 2 else "HD"),
                  team=(teams[i] if teams else None)) for i in range(n)]
    return MatchData(players=players, map=MapData(md5="x", title="Song", artist="Artist",
                     version="Insane"), match_title="test cup", mode=mode, teams=teams)


@pytest.fixture
def cache(tmp_path):
    return tmp_path


def test_two_players_use_classic_template(cache):
    html = build_chrome_html(_match(2), cache)
    assert "head to head" in html          # classic top-bar plate
    assert "mpanel" not in html            # not the multi layout


@pytest.mark.parametrize("n", [3, 4])
def test_multi_players_use_panel_layout(cache, n):
    html = build_chrome_html(_match(n), cache)
    assert html.count('class="mpanel"') == n
    assert "head to head" not in html
    for i in range(n):
        assert f"Player{i}" in html


def test_multi_includes_each_accent(cache):
    m = _match(4)
    html = build_chrome_html(m, cache)
    for p in m.players:
        assert p.accent in html


def test_team_mode_shows_tags_and_label(cache):
    html = build_chrome_html(_match(4, mode="team", teams=[0, 1, 0, 1]), cache)
    assert "2v2" in html
    assert 'class="pteam"' in html
    assert ">A<" in html and ">B<" in html   # team tags rendered


def test_ffa_has_no_team_tags(cache):
    html = build_chrome_html(_match(4), cache)
    assert 'class="pteam"' not in html


def test_html_escapes_player_names(cache):
    m = _match(3)
    m.players[0].play_username = "a<b>&c"
    m.players[0].username = None
    html = build_chrome_html(m, cache)
    assert "a<b>&c" not in html
    assert "a&lt;b&gt;&amp;c" in html
