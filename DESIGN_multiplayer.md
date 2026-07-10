# Design: multi-player support (2–4 players, FFA + teams)

Status: **approved design, not yet implemented.**
Decisions locked in (2026-07-08): up to **4 players**, both **free-for-all and 2v2 team**
modes, **unified per-panel labels** for identity.

This document is the spec for generalising CircleClash from strictly 1v1 to N-player
(N ∈ 2..4). It exists so the implementation — which touches every layer — stays coherent
across multiple work sessions.

---

## 1. Goals & non-goals

**Goals**
- Render 2, 3 or 4 replays of the same beatmap into one comparison video.
- Free-for-all (everyone ranked individually) **and** team mode (2v2: two teams, combined
  score, team winner).
- Identity (avatar + name + mods) shown as a per-panel corner label that scales to any N.
- 2-player output stays visually close to today (side-by-side), just with the identity
  label moved onto each panel.

**Non-goals (for this pass)**
- >4 players, uneven teams (3v1), or >2 teams.
- Different beatmaps per player (still one shared map, as today).
- Re-syncing replays (that's the separate sync feature).

---

## 2. Layouts (fixed 1080p design; SCALE handles other resolutions)

Canvas 1920×1080. Slim top bar (map) + gameplay panels + slim footer (title).
Usable interior after padding: **1872 × 804**.

| N | Layout | Panel (px) | Gameplay note |
|---|--------|-----------|---------------|
| 2 | side by side | 918 × 798 | large (unchanged) |
| 3 | equal columns | 608 × 804 | medium, tall/narrow |
| 4 | 2 × 2 grid | 924 × 390 | smaller, wide/short |

Rejected: 4-in-a-row (450×804) — smallest gameplay of all options.

**Team mode grouping (2v2):** teammates sit adjacent and share a team accent hue family.
- 4 players: grid with team A on the left column (P1, P3), team B on the right column
  (P2, P4) — or top row / bottom row; pick whichever reads as "two teams" best during
  implementation. A thin divider/team-colour band separates the two teams.

`Layout` gains a function:

```python
def panels_for(n: int, mode: str) -> list[PanelRect]:
    """Return n PanelRects (x, y, w, h + danser interior) for the given player
    count and mode ('ffa'|'team'). All panels within a layout are equal-sized."""
```

`PanelRect` carries outer rect (chrome border) and the inset danser render rect
(`dz_*`). Everything downstream (chrome HTML, danser dims, composite coords) reads
from this list instead of the hard-coded `left_*`/`right_*` constants.

---

## 3. Data model (`match_assembler.py`)

Introduce a list-first model, keep `left`/`right` as compat shims so the transition is
incremental and low-risk.

```python
@dataclass
class MatchData:
    players: list[PlayerData]          # 2..4, index = panel order
    map: MapData
    match_title: str = "friendly · bo1"
    mode: str = "ffa"                  # "ffa" | "team"
    teams: list[int] | None = None     # team index (0/1) per player; None in ffa

    # --- compatibility shims (existing 2p code keeps working) ---
    @property
    def left(self)  -> PlayerData: return self.players[0]
    @property
    def right(self) -> PlayerData: return self.players[1]

    def ranking(self) -> list[int]:    # player indices, best score first
        return sorted(range(len(self.players)),
                      key=lambda i: self.players[i].score, reverse=True)

    def team_score(self, t: int) -> int:
        return sum(p.score for i, p in enumerate(self.players)
                   if self.teams and self.teams[i] == t)

    def winner(self) -> ...            # index (ffa) or team id (team)
```

`PlayerData` gains an `accent` per player already exists; for team mode the accent is
derived from the team (two hue families: team A = pink family, team B = ice family, with
a light/dark variant per teammate so they're still distinguishable).

`assemble_match` gets an N-arg form: `assemble_match(osr_paths: list[str], ...)`.
Keep a 2-arg wrapper `assemble_match(left, right, ...)` delegating to the list form.
Validation: all replays must share `beatmap_md5` (existing check, generalised).

`to_dict` / `--match-json` round-trip includes `players`, `mode`, `teams`.

---

## 4. Overlay chrome (`make_overlay_video.py`)

`build_chrome_html` becomes a loop instead of `%%L_*%%`/`%%R_*%%` substitution:

- Slim top bar: map artist/title/diff/SR centered. (No more big flanking identity blocks.)
- For each panel in `panels_for(...)`: emit a positioned `<div>` label at the panel's
  top-left corner — avatar, name, mods badge, accent underline. In team mode add a small
  team tag (A/B) and use the team accent.
- Footer: match title (+ mode badge "2v2" / "FFA").

Keep the two existing single-template constants only as reference; the new builder
constructs the panel divs programmatically from the layout list.

---

## 5. danser render loop

Replace the two hard-coded `ov_left`/`ov_right` calls with a loop over players:

```python
for i, p in enumerate(match.players):
    patch = _render_patch(accent=p.accent, skin=skins[i], music_vol=music[i],
                          hit_vol=hits[i], ..., panel=panels[i])   # panel gives dz_w/h
    sync_poc.render_replay(danser, Path(osr_paths[i]), f"ov_{i}", extra_args=[patch, mods2[i]])
    clips[i] = sync_poc.locate_output(video_dir, f"ov_{i}", t0)
```

Panel resolution passed to `_render_patch`/danser = that layout's cell danser dims
(equal within a layout).

---

## 6. Composite (ffmpeg)

`composite()` builds its `filter_complex` programmatically for N inputs:

- Scale each danser clip `[i+1:v]` to its cell `dz_w×dz_h`, overlay at `(dz_x, dz_y)`
  onto the chrome, chained.
- `amix=inputs=N:normalize=0` over all N audio streams, then `alimiter` (as today).
- Trim/fade logic unchanged.

Audio defaults generalise: **player 1 music = 1.0, players 2..N music = 0.0** (single
music bed), **all hitsounds = 1.0**. Per-player override in the GUI.

---

## 7. End card (results screen)

- **FFA:** rank all N by score; N rows/columns, winner highlighted. `_cmp_rows`
  generalises from 2 columns to N (grid of stat rows × N players).
- **Team:** two team columns; each shows its members and the **team total**; the winning
  team is highlighted. Reuse the comparison-row machinery, grouped by team.

---

## 8. GUI (`renderer_gui.py`)

Biggest surface. Replace the fixed 2-DropZone versus row with a dynamic player area:

- **Player list**: 2–4 rows. Each row = DropZone (or compact slot) + skin selector +
  (in team mode) a team A/B toggle. "Add player" button up to 4, remove down to 2.
- **Mode control**: FFA / 2v2. Team toggle only enabled at exactly 4 players (2v2), and
  the layout auto-assigns adjacent panels to teams.
- **Audio tab**: per-player music/hitsound sliders (generated from the current count)
  instead of the fixed P1/P2 controls.
- **Pipeline args**: move from `--left*/--right*` to repeatable/grouped args, or pass a
  richer `--match-json` describing players + per-player skin/audio/mods, letting the
  pipeline compute patches. `--match-json` is the lower-arg-explosion path and already
  exists — extend it to carry the render spec, not just display metadata.
- Preview (already built) auto-works once chrome is N-aware.

---

## 9. Backwards compatibility & rollout

Phased so each phase is shippable and testable:

1. **Data model** — list-first `MatchData` + compat shims + N-arg `assemble_match`.
   (No visible change; 2p still renders identically.) Add unit tests here.
2. **Layout engine** — `panels_for()` + `PanelRect`; 2p returns today's exact coords.
3. **Chrome builder** — per-panel labels; verify 2p via the Preview button.
4. **Render + composite loop** — N danser clips + N-input ffmpeg graph.
5. **End card** — N-way FFA, then team totals.
6. **GUI** — dynamic player list, mode + team controls, per-player audio.
7. **Team mode** polish — team accents, grouped panels, team endcard.

Each phase keeps N=2 pixel-parity as the regression guard (compare Preview output
against a saved baseline).

---

## 10. Open implementation questions (decide during build, not blocking)

- Team accent scheme: two hue families with per-teammate light/dark, vs a shared team
  colour + a small A/B tag. Lean: shared team hue + light/dark teammate variant.
- 4p team grouping: left/right columns vs top/bottom rows. Lean: left/right columns.
- Endcard for 4-way FFA: horizontal 4-column vs 2×2 mirror of the gameplay grid.
