"""Tests for the panels_for() layout engine (phase 2 of multi-player)."""
import pytest

from make_overlay_video import panels_for, Layout, PanelRect


def test_two_player_matches_legacy_constants():
    """n=2 must reproduce the classic side-by-side coords so 1v1 output is unchanged."""
    a, b = panels_for(2)
    # outer panels
    assert (a.x, a.y, a.w, a.h) == (Layout.left_x, Layout.panel_top, Layout.panel_w, Layout.panel_h)
    assert (b.x, b.y, b.w, b.h) == (Layout.right_x, Layout.panel_top, Layout.panel_w, Layout.panel_h)
    # danser interior (what the old L.dz_* constants held)
    assert (a.dz_x, a.dz_y, a.dz_w, a.dz_h) == (Layout.dz_lx, Layout.dz_y, Layout.dz_w, Layout.dz_h)
    assert (b.dz_x, b.dz_y, b.dz_w, b.dz_h) == (Layout.dz_rx, Layout.dz_y, Layout.dz_w, Layout.dz_h)


def _within_canvas(panels):
    for p in panels:
        assert p.x >= Layout.PAD_X
        assert p.y >= Layout.TOP_H
        assert p.x + p.w <= Layout.W - Layout.PAD_X
        assert p.y + p.h <= Layout.H - Layout.FOOTER_H


@pytest.mark.parametrize("n", [2, 3, 4])
def test_counts_and_bounds(n):
    panels = panels_for(n)
    assert len(panels) == n
    _within_canvas(panels)


@pytest.mark.parametrize("n", [2, 3, 4])
def test_all_panels_equal_size(n):
    panels = panels_for(n)
    sizes = {(p.w, p.h) for p in panels}
    assert len(sizes) == 1, "all cells in a layout must be the same size"


def test_panels_do_not_overlap():
    for n in (2, 3, 4):
        panels = panels_for(n)
        for i in range(len(panels)):
            for j in range(i + 1, len(panels)):
                a, b = panels[i], panels[j]
                disjoint = (a.x + a.w <= b.x or b.x + b.w <= a.x or
                            a.y + a.h <= b.y or b.y + b.h <= a.y)
                assert disjoint, f"panels {i},{j} overlap at n={n}"


def test_three_players_are_columns():
    panels = panels_for(3)
    assert len({p.y for p in panels}) == 1          # single row
    assert len({p.x for p in panels}) == 3          # three columns
    xs = sorted(p.x for p in panels)
    assert xs == [p.x for p in panels]              # left-to-right order


def test_four_players_are_two_by_two():
    panels = panels_for(4)
    assert len({p.y for p in panels}) == 2          # two rows
    assert len({p.x for p in panels}) == 2          # two columns
    # row-major order: 0=TL 1=TR 2=BL 3=BR
    assert panels[0].y == panels[1].y < panels[2].y == panels[3].y
    assert panels[0].x == panels[2].x < panels[1].x == panels[3].x


def test_gap_between_columns_is_layout_gap():
    a, b = panels_for(2)
    assert b.x - (a.x + a.w) == Layout.GAP


def test_rejects_unsupported_counts():
    for n in (0, 1, 5, 9):
        with pytest.raises(ValueError):
            panels_for(n)


def test_four_player_teams_fall_into_columns():
    # With a 2x2 grid, teams [0,1,0,1] put team 0 in the left column, team 1 in the right.
    panels = panels_for(4, mode="team")
    teams = [0, 1, 0, 1]
    left_col_x = min(p.x for p in panels)
    for i, t in enumerate(teams):
        in_left = panels[i].x == left_col_x
        assert (t == 0) == in_left
