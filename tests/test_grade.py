"""Known-answer tests for compute_grade (osu!standard letter grades)."""
import pytest

from match_assembler import compute_grade
from osr_parser import Mods


def g(n300, n100, n50, nmiss, mods=Mods(0)):
    return compute_grade(n300, n100, n50, nmiss, mods)


def test_ss_all_300():
    assert g(1000, 0, 0, 0) == ("SS", False)


def test_ss_is_silver_with_hidden():
    assert g(1000, 0, 0, 0, Mods.HIDDEN) == ("SS", True)


def test_ss_is_silver_with_flashlight():
    assert g(1000, 0, 0, 0, Mods.FLASHLIGHT) == ("SS", True)


def test_s_high_300_no_miss():
    # >90% 300s, no 50s, no miss -> S (silver only with HD/FL)
    grade, silver = g(950, 50, 0, 0)
    assert grade == "S" and silver is False


def test_s_silver_with_hidden():
    grade, silver = g(950, 50, 0, 0, Mods.HIDDEN)
    assert grade == "S" and silver is True


def test_a_grade():
    # >80% 300 and no miss (but not S because a 50 is present) -> A
    grade, silver = g(850, 140, 10, 0)
    assert grade == "A" and silver is False


def test_a_never_silver():
    # Silver is reserved for S/SS; an A with HD stays non-silver.
    assert g(850, 140, 10, 0, Mods.HIDDEN) == ("A", False)


def test_b_grade():
    # >70% 300s and no miss -> B (a miss would demote it to C)
    grade, _ = g(750, 250, 0, 0)
    assert grade == "B"


def test_c_grade():
    grade, _ = g(650, 250, 90, 10)
    assert grade == "C"


def test_d_grade():
    grade, _ = g(500, 300, 150, 50)
    assert grade == "D"


def test_empty_play_is_d():
    assert g(0, 0, 0, 0) == ("D", False)


def test_miss_demotes_from_s():
    # Same ratios but with a miss cannot be S (S requires nmiss == 0 at that tier).
    grade, _ = g(950, 49, 0, 1)
    assert grade != "S"
