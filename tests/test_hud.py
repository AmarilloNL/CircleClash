"""Tests for danser HUD (pp counter + hit counts), now user-positionable.

Position is driven by pp_x/pp_y (0..1) mapped across danser's HUD space and _s()-scaled
so it lands the same fraction at every resolution. Higher pp_x/pp_y -> further
right/down; hit counts ride just above pp.
"""
import json

import pytest

import make_overlay_video as m
from make_overlay_video import panels_for


def _patch(panel, scale=1.0, vis=None):
    m.SCALE = scale
    raw = m._render_patch("#ff66ab", panel=panel, vis=vis)
    return json.loads(raw[len("-sPatch="):])


def test_pp_and_hits_shown_and_anchored():
    g = _patch(panels_for(2)[0])["Gameplay"]
    assert g["PPCounter"]["Show"] is True and g["HitCounter"]["Show"] is True
    assert g["PPCounter"]["Align"] == "BottomRight"
    # hit counts sit just above pp (smaller Y)
    assert g["HitCounter"]["YPosition"] < g["PPCounter"]["YPosition"]


def test_higher_pp_x_moves_right():
    left = _patch(panels_for(2)[0], vis={"pp_x": 0.3, "pp_y": 0.88})["Gameplay"]
    right = _patch(panels_for(2)[0], vis={"pp_x": 0.9, "pp_y": 0.88})["Gameplay"]
    assert right["PPCounter"]["XPosition"] > left["PPCounter"]["XPosition"]


def test_higher_pp_y_moves_down():
    up = _patch(panels_for(2)[0], vis={"pp_x": 0.86, "pp_y": 0.3})["Gameplay"]
    down = _patch(panels_for(2)[0], vis={"pp_x": 0.86, "pp_y": 0.9})["Gameplay"]
    assert down["PPCounter"]["YPosition"] > up["PPCounter"]["YPosition"]


def test_default_position_is_lower_right():
    p = panels_for(2)[0]
    g = _patch(p)["Gameplay"]
    # default 0.86/0.88 -> right of centre and low in the frame
    assert g["PPCounter"]["XPosition"] > 0.86 * 1.72 * p.dz_w - 2
    assert g["PPCounter"]["YPosition"] > 0.88 * 1.49 * p.dz_h - 2


@pytest.mark.parametrize("scale", [0.667, 1.0, 2.0])
def test_position_fraction_is_resolution_independent(scale):
    # same 0..1 position -> position scales linearly with SCALE (so the same panel-fraction)
    p = panels_for(2)[0]
    x1 = _patch(p, scale=1.0)["Gameplay"]["PPCounter"]["XPosition"]
    xs = _patch(p, scale=scale)["Gameplay"]["PPCounter"]["XPosition"]
    assert xs == round(x1 * scale) or abs(xs - x1 * scale) <= 1


def test_recording_dims_scale_with_resolution():
    p = panels_for(2)[0]
    assert _patch(p, 1.0)["Recording"]["FrameWidth"] == p.dz_w
    assert _patch(p, 0.667)["Recording"]["FrameWidth"] == round(p.dz_w * 0.667)
