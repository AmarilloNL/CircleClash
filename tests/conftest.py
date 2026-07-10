"""Shared pytest fixtures + path setup so tests import the top-level modules."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest


# The four sample replays live in the repo root (all on the same beatmap).
SAMPLE_REPLAYS = sorted(ROOT.glob("solo-replay-osu_555797_*.osr"))


@pytest.fixture
def sample_replays():
    if len(SAMPLE_REPLAYS) < 2:
        pytest.skip("sample .osr replays not present")
    return [str(p) for p in SAMPLE_REPLAYS]
