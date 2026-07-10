"""Structural tests for the results-card builder dispatch (phase 5)."""
from pathlib import Path

import pytest

from make_overlay_video import build_endcard_html
from match_assembler import MatchData, MapData, PlayerData, assign_accents


def _p(name, accent, score, grade="S", team=None):
    return PlayerData(
        side="left", accent=accent, team=team, play_username=name, mods="HD",
        accuracy=98.5, score=score, max_combo=1200, perfect=False,
        n300=1000, n100=10, n50=0, nmiss=0, grade=grade, silver=False,
    )


def _match(scores, mode="ffa", teams=None):
    accents = assign_accents(len(scores), mode, teams)
    players = [_p(f"P{i}", accents[i], s, team=(teams[i] if teams else None))
               for i, s in enumerate(scores)]
    return MatchData(players=players, map=MapData(md5="x", title="Song", artist="Art",
                     version="Hard"), match_title="cup", mode=mode, teams=teams)


@pytest.fixture
def cache(tmp_path):
    return tmp_path


def test_two_players_use_classic_versus_card(cache):
    html = build_endcard_html(_match([10, 20]), cache)
    assert 'class="vs"' in html
    assert 'class="board' not in html and 'class="teams"' not in html


@pytest.mark.parametrize("n", [3, 4])
def test_ffa_uses_leaderboard(cache, n):
    html = build_endcard_html(_match(list(range(n))), cache)
    assert 'class="board' in html
    assert html.count('class="lbrow') == n
    assert 'class="vs"' not in html


def test_leaderboard_orders_by_score_and_crowns_winner(cache):
    # scores by index [10, 30, 20] -> P1 (score 30) is the top/winning row
    html = build_endcard_html(_match([10, 30, 20]), cache)
    # isolate the winrow's markup (from its opening div to the next leaderboard row)
    win_block = html.split('reveal winrow"', 1)[1].split('class="lbrow', 1)[0]
    assert "P1" in win_block            # highest scorer sits in the winner row
    assert "&#9818;" in win_block       # crown shown for the winner


def test_team_card_has_two_columns_and_winner(cache):
    html = build_endcard_html(_match([10, 40, 30, 5], mode="team", teams=[0, 1, 0, 1]), cache)
    assert html.count('class="tcol') == 2
    assert "TEAM A" in html and "TEAM B" in html
    assert "winrow" in html             # winning team highlighted
    # team 0 total = 40, team 1 = 45 -> team B (index 1) wins; its column is the winrow
    assert 'class="teams"' in html


def test_multi_endcard_is_seekable(cache):
    # render_endcard_video depends on window.seek existing
    html = build_endcard_html(_match([1, 2, 3]), cache)
    assert "window.seek" in html
    assert "countup" in html            # count-up spans drive the number animation


def test_multi_endcard_escapes_names(cache):
    m = _match([1, 2, 3])
    m.players[0].play_username = "<x>&"
    m.players[0].username = None
    html = build_endcard_html(m, cache)
    assert "<x>&" not in html
    assert "&lt;x&gt;&amp;" in html
