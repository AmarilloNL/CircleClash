"""Tests for the list-first MatchData model, accents, and assemble_match."""
import pytest

from match_assembler import (
    MatchData, MapData, PlayerData, assign_accents, assemble_match,
    PALETTE, TEAM_ACCENTS,
)


def _player(name, score, side="left", accent="#ffffff", team=None):
    return PlayerData(
        side=side, accent=accent, team=team, play_username=name,
        mods="NM", accuracy=99.0, score=score, max_combo=100, perfect=False,
        n300=100, n100=0, n50=0, nmiss=0, grade="S", silver=False,
    )


def _match(scores, mode="ffa", teams=None):
    players = [_player(f"p{i}", s, team=(teams[i] if teams else None))
               for i, s in enumerate(scores)]
    return MatchData(players=players, map=MapData(md5="x"), mode=mode, teams=teams)


# --- compatibility shims ----------------------------------------------------

def test_left_right_shims():
    m = _match([10, 20])
    assert m.left is m.players[0]
    assert m.right is m.players[1]


def test_from_pair_builds_two_player_list():
    a, b = _player("a", 1), _player("b", 2, side="right")
    m = MatchData.from_pair(a, b, MapData(md5="x"))
    assert m.players == [a, b]
    assert m.mode == "ffa" and m.teams is None


def test_winner_side_back_compat():
    assert _match([30, 10]).winner_side == "left"
    assert _match([10, 30]).winner_side == "right"


# --- ranking / winners ------------------------------------------------------

def test_ranking_orders_by_score_desc():
    m = _match([10, 40, 25, 5])
    assert m.ranking() == [1, 2, 0, 3]


def test_ffa_winner_is_top_scorer():
    assert _match([10, 40, 25, 5]).winner() == 1


def test_ranking_tie_is_stable_by_index():
    # equal scores keep input order (sorted is stable)
    assert _match([50, 50]).ranking() == [0, 1]


# --- teams ------------------------------------------------------------------

def test_team_scores_sum_members():
    m = _match([10, 20, 30, 5], mode="team", teams=[0, 1, 0, 1])
    assert m.team_score(0) == 40   # players 0 + 2
    assert m.team_score(1) == 25   # players 1 + 3


def test_team_winner_is_higher_total():
    m = _match([10, 20, 30, 5], mode="team", teams=[0, 1, 0, 1])
    assert m.winner() == 0


def test_team_ids_first_appearance_order():
    m = _match([1, 2, 3, 4], mode="team", teams=[1, 0, 1, 0])
    assert m.team_ids() == [1, 0]


# --- accents ----------------------------------------------------------------

def test_ffa_accents_follow_palette():
    assert assign_accents(3) == PALETTE[:3]


def test_ffa_accents_wrap_beyond_palette():
    got = assign_accents(6)
    assert got[4] == PALETTE[0] and got[5] == PALETTE[1]


def test_team_accents_group_by_hue_family():
    got = assign_accents(4, mode="team", teams=[0, 1, 0, 1])
    # team 0 members get the two pink-family variants; team 1 the ice-family
    assert got[0] == TEAM_ACCENTS[0][0] and got[2] == TEAM_ACCENTS[0][1]
    assert got[1] == TEAM_ACCENTS[1][0] and got[3] == TEAM_ACCENTS[1][1]


# --- assemble_match validation (no API / parsing needed for these) ----------

def test_assemble_rejects_too_many(tmp_path):
    with pytest.raises(ValueError):
        assemble_match([str(tmp_path / f"{i}.osr") for i in range(5)])


def test_assemble_rejects_too_few(tmp_path):
    with pytest.raises(ValueError):
        assemble_match([str(tmp_path / "only.osr")])


def test_assemble_team_requires_teams(sample_replays):
    with pytest.raises(ValueError):
        assemble_match(sample_replays[:2], mode="team")  # teams missing


def test_assemble_rejects_mismatched_id_overrides(sample_replays):
    with pytest.raises(ValueError):
        assemble_match(sample_replays[:2], id_overrides=[1])  # wrong length


# --- real assembly end to end (no API key -> unresolved but still builds) ----

def test_assemble_two_real_replays(sample_replays, tmp_path):
    m = assemble_match(sample_replays[:2], cache_dir=str(tmp_path / "cache"))
    assert len(m.players) == 2
    assert m.left.play_username and m.right.play_username
    assert m.players[0].accent == PALETTE[0]
    assert m.map.md5 == "a84050da9b68ca1bd8e2d1700b9c6ca5"


def test_assemble_four_real_replays_ffa(sample_replays, tmp_path):
    if len(sample_replays) < 4:
        pytest.skip("need 4 sample replays")
    m = assemble_match(sample_replays[:4], cache_dir=str(tmp_path / "cache"))
    assert len(m.players) == 4
    assert [p.accent for p in m.players] == PALETTE[:4]
    # winner is a valid index
    assert 0 <= m.winner() < 4


def test_legacy_two_positional_call(sample_replays, tmp_path):
    # old call style must still work
    m = assemble_match(sample_replays[0], sample_replays[1],
                       cache_dir=str(tmp_path / "cache"))
    assert len(m.players) == 2


def test_to_dict_roundtrip_shape(sample_replays, tmp_path):
    m = assemble_match(sample_replays[:2], cache_dir=str(tmp_path / "cache"))
    d = m.to_dict()
    assert "players" in d and len(d["players"]) == 2
    assert "winner" in d and "winner_side" in d
    assert d["players"][0]["display_name"]
