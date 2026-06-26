# Changelog

## v1.1.2

### Fixed
- **No more CMD pop-up on Windows:** danser and ffmpeg no longer spawn their own console
  windows during a render — their output flows into the in-app log, the same as on Linux.
- **danser output folder is auto-derived** from the danser binary, fixing
  *"Couldn't find danser's output for 'ov_left'"* when a stale path was stored.
- **danser binary path auto-fills** in Settings after first-run setup, instead of looking empty.

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
