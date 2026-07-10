"""Parser tests against the four real sample replays (same beatmap)."""
import pytest

from osr_parser import parse_replay, Mods

# Known-answer values captured from the sample replays (osu!standard).
EXPECTED = {
    "solo-replay-osu_555797_1208982203.osr":
        dict(player="Bubbleman", mods="HR", score=103039905, max_combo=2044,
             n300=1562, n100=28, n50=0, nmiss=0),
    "solo-replay-osu_555797_1783992329.osr":
        dict(player="Raikouhou", mods="HR", score=103804838, max_combo=2045,
             n300=1576, n100=14, n50=0, nmiss=0),
    "solo-replay-osu_555797_3158571457.osr":
        dict(player="Kamensh1k", mods="HD", score=103470320, max_combo=2044,
             n300=1568, n100=22, n50=0, nmiss=0),
    "solo-replay-osu_555797_3484840253.osr":
        dict(player="PikaPwn", mods="HD", score=103490718, max_combo=2045,
             n300=1567, n100=23, n50=0, nmiss=0),
}

SHARED_MD5 = "a84050da9b68ca1bd8e2d1700b9c6ca5"


def test_all_samples_parse_and_share_map(sample_replays):
    md5s = {parse_replay(p).beatmap_md5 for p in sample_replays}
    assert md5s == {SHARED_MD5}, "sample replays should all be on the same beatmap"


@pytest.mark.parametrize("name,exp", EXPECTED.items())
def test_known_replay_values(sample_replays, name, exp):
    path = next((p for p in sample_replays if p.endswith(name)), None)
    if path is None:
        pytest.skip(f"{name} not present")
    r = parse_replay(path)
    assert r.player == exp["player"]
    assert r.mods_str == exp["mods"]
    assert r.score == exp["score"]
    assert r.max_combo == exp["max_combo"]
    assert (r.count_300, r.count_100, r.count_50, r.count_miss) == \
        (exp["n300"], exp["n100"], exp["n50"], exp["nmiss"])
    assert r.mode == 0
    assert 0.0 <= r.accuracy_pct <= 100.0


def test_hr_and_hd_flags_decode():
    # sanity: the mod bitfields we saw map to the right flags
    assert Mods.HARD_ROCK == 16
    assert Mods.HIDDEN == 8
