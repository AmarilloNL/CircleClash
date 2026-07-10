# Changelog

## v1.3.0

### Fixed
- **PP counter position is now adjustable.** danser's HUD coordinate space is larger than the
  recording frame and the safe zone near the edge is narrow, so pp/hit-count placement is exposed
  as two **Settings → Visual** sliders — *PP position — right* and *— down* (0–100%, default
  lower-right). Values scale with the render resolution (earlier fixed coordinates overflowed at
  720p and fell off-screen), and the hit counts follow just above pp. If it ever vanishes, lower
  the slider a touch.
- **NVENC wrongly falling back to x264 on capable GPUs.** The GPU-encoder pre-flight used to
  swallow ffmpeg's error and always blame an old driver, so cards like an RTX 4080 Super got
  bounced to x264. Now:
  - it reports the *actual* reason in the log (no NVENC in this ffmpeg / GPU unreachable /
    driver older than this ffmpeg's NVENC API);
  - if the bundled ffmpeg can't do NVENC, CircleClash **automatically retries with your system
    ffmpeg** (the bundled build is bleeding-edge and can outrun the driver's NVENC API, whereas
    the distro's ffmpeg matches it — it's the one danser already uses) before ever dropping to
    x264;
  - a custom **ffmpeg path set in Settings is now honoured** even when the auto-downloaded copy
    is present, so you can pin CircleClash to a specific ffmpeg.

### Changed
- **Clearer Visual settings.** Settings → Visual is now grouped into **Background · Cursor ·
  Effects · In-game HUD**. The background choice is relabelled *visibility* with plain options
  (*Hidden — solid dark (recommended)*, *Faint*, *Visible*, *Blurred*) instead of the confusing
  "dim" wording, and a **Freeze background (stop storyboard / video motion)** toggle stops the
  animated background some maps have. The **PP counter** toggle is now clearly labelled
  ("small, bottom-right of each panel") — it's on by default; if you'd previously unchecked the
  old cryptic "pp counter" box it will have stayed off, so re-tick it here to bring it back.

### Added
- **Per-player audio:** the Settings → Audio tab now shows a music + hitsound slider for
  **each** player (following how many you've added), instead of a fixed P1/P2 pair, plus
  the master. Only P1 carries the music bed by default so the shared track isn't doubled;
  turn any player up to crossfade or mute their hitsounds to drop them from the mix.
- **Replay-sync guard:** CircleClash now refuses to render replays that run at different
  speeds (e.g. DoubleTime vs NoMod) — their panels could never stay aligned. The main
  window flags a mismatch the moment the replays load (Render is disabled with a clear
  reason; Preview still works), and the render pipeline validates it too and reports panel
  length alignment after rendering. Same-speed replays stay frame-locked as before.
- **Multi-player (2–4 players):** CircleClash can now compare **up to four** replays of
  the same map, not just two. The main window has a dynamic player row — **＋ Add player**
  (up to 4) and per-slot remove — with per-player replay drop zone and skin picker. Layout
  adapts automatically: 2 = side by side, 3 = columns, 4 = 2×2 grid. A **Mode** selector
  adds **2v2 Teams** (at four players): pick each player's Team A/B and the results card
  shows combined team totals and the winning team; free-for-all shows a ranked leaderboard.
  1v1 renders exactly as before.
- **Preview button** — render just the **overlay frame** or the **results card** to a
  quick still image (a few seconds, no danser/ffmpeg gameplay pass) so you can check
  names, avatars, map metadata and colours *before* committing to a full multi-minute
  render. Lights up as soon as both replays are loaded (it doesn't need danser). Found
  next to **Render**; pick *Overlay frame* or *Results card* from its menu.

### Internal (multi-player groundwork — no user-facing change yet)
- **List-first match model:** `MatchData` now holds `players: list[PlayerData]` (with
  `mode` and `teams`) instead of a fixed `left`/`right` pair, keeping `.left`/`.right`
  as compatibility properties so 1v1 rendering is byte-for-byte unchanged. `assemble_match`
  accepts 2–4 replays (the legacy two-argument call still works). This is phase 1 of
  planned 2–4 player support (free-for-all + 2v2 teams) — see `DESIGN_multiplayer.md`.
- **Layout engine:** new `panels_for(n)` returns the gameplay-panel geometry for 2–4
  players (2 = side by side, 3 = columns, 4 = 2×2 grid). `n=2` reproduces the classic
  side-by-side coordinates exactly (asserted in tests), so 1v1 output is untouched; the
  render/composite steps will consume it in later phases.
- **Overlay chrome for 3–4 players:** `build_chrome_html` now renders a per-panel-label
  layout for 3+ players — a slim centred map bar plus one bordered gameplay panel per
  player, each with an identity strip (avatar, name, rank/pp, mods). Team mode tints
  panels by team hue-family and adds an A/B tag; teammates fall into adjacent columns.
  1v1 keeps its bespoke top-bar design entirely unchanged (the builder dispatches on
  player count). Visible via the Preview button.
- **N-player render + composite:** the danser render loop and the ffmpeg composite now
  handle 2–4 players — danser renders one clip per player at its panel's size, and the
  composite scales/overlays every clip into the chrome and mixes all their audio. The
  pipeline CLI accepts 2–4 replays (`replays…`, plus `--mode`, `--teams`, `--skins`,
  `--player-music`, `--player-hits`, `--ids`); the legacy `left right --left-skin …`
  form still works unchanged. `_render_patch` renders at the per-panel size. n=2 output
  is unchanged (the 2-panel filter graph is equivalent to the old hand-written one).
- **Results card for 3–4 players:** the end card now renders a ranked FFA leaderboard
  (rows sorted by score, #1 crowned and glowing, per-player accents, score/acc/combo/grade
  columns) or, in 2v2, two team columns with combined totals and the winning team
  highlighted. Fully animated/seekable like the 1v1 card, so it frame-steps into the video
  identically. 1v1 keeps its bespoke versus card unchanged (builder dispatches on count).
- **Test suite:** added `tests/` (pytest) covering the replay parser, grade computation,
  the match model / ranking / team logic, the layout engine, the chrome dispatch, the
  composite filter graph (with a real ffmpeg end-to-end composite for 2/3/4 players), and
  the results-card dispatch. Run with `python -m pytest`.

## v1.2.0

### Fixed
- **"Import .osk" buttons render cleanly:** the skin row now keeps the dropdown and its button
  in their own full-width row (with a stable button width), so the button text no longer clips
  or overlaps at certain window sizes.
- **Composite step no longer fails with "file not found":** the app now always passes a real ffmpeg
  path to the render pipeline (resolved fresh from the managed copy), and the pipeline falls back to
  the ffmpeg danser bundles next to itself if needed — so the final video assembles even when there's
  no system ffmpeg on PATH (typical on Windows) or the stored path went stale after moving the folder.
- **First render on a fresh install works now:** danser's very first run builds its settings file
  against the default osu! Songs path and fails db init ("Beatmap not found") before our render-songs
  path takes effect — but it saves the corrected settings on the way out. The pipeline now detects
  that one-time miss and retries danser once automatically, so the first render no longer fails.
- **No more CMD pop-up on Windows, and rendering works there:** danser and ffmpeg are now run with
  their output captured through a pipe and streamed into the in-app log (like on Linux). This both
  removes the console pop-up *and* gives them a valid output channel — without it, a windowed build
  could leave danser with a broken stdout so it silently produced no video.
- **danser output folder is auto-derived** from the danser binary, fixing
  *"Couldn't find danser's output for 'ov_left'"* when a stale path was stored.
- **danser binary path auto-fills** in Settings (resolved from the managed/portable copy and written
  back), and the render pipeline falls back to that copy if the field is ever empty.

### Added
- **App icon** (taskbar + window).

### Changed
- **Faster startup:** the app now ships as a one-folder build (zipped) instead of a single file,
  so it no longer unpacks ~300-400 MB to a temp dir on every launch. Extract the zip and run
  `CircleClash` / `CircleClash.exe` inside.
- **Settings is tidier:** the duplicate *danser video output dir* field is gone — only the final
  *output folder* remains.

## v1.1.0 – v1.1.1

### Added — Visual options (Settings → Visual)
All optional and off by default, so existing renders look the same unless you opt in.

- **Background:** dark / dimmed / visible / blurred.
- **Cursor:** adjustable size and trail length.
- **Effects:** bloom/glow, hit lighting, aim-error scatter meter, prominent unstable rate,
  pp breakdown (aim/speed/acc), per-side mods badge, ignore hitsound volume changes, disable storyboards.
- **HUD toggles:** hide the pp counter, hit counts, hit-error bar, key overlay or combo
  (score + accuracy always show).

### Changed
- **Settings is now tabbed** (Paths · osu! API · Timing · Encoding · Audio · Visual) instead of one
  long scrolling list.

### Fixed
- **PP counter clipping:** the pp counter (and hit counts) are now anchored to the bottom-right
  corner, so long values like `1141.37` no longer slide under the panel border.
- **Encoder fallback:** if the selected NVENC encoder can't open on your system (e.g. `av1_nvenc`
  needs a newer NVIDIA driver), CircleClash now detects it up front and automatically falls back to
  x264 so the render still finishes, instead of failing at the composite step.
- **Windows render crash:** the worker no longer dies with a `UnicodeEncodeError` on Windows — its
  output is now forced to UTF-8 so progress glyphs (→ · ✓ ×) can't break the cp1252 console/pipe.

### Notes
- Background dim is now uniform across intro / gameplay / breaks (previously breaks brightened
  slightly), for a cleaner, less distracting comparison video.

## Earlier

- **v1.0.x** — initial public releases: side-by-side osu! replay comparison rendering via danser-go +
  Playwright + ffmpeg, single-file Windows/Linux builds, automatic danser + ffmpeg provisioning,
  portable mode, optional osu! API metadata, per-side audio mixing, and the phased render log.
