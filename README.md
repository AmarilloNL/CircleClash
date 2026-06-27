# CircleClash

Turn two osu! replays into a **side-by-side comparison video** — real gameplay rendered by
[danser-go](https://github.com/Wieku/danser-go), a styled neon overlay, and an animated results
card at the end. Drop a `.osr` on each side, hit **Render**, get a shareable `.mp4`.

> Works with both **osu!stable** and **osu!lazer** replays. Lazer replays that danser would
> falsely show as "failed" are auto-corrected.

---

## Download

### Windows — just grab the app

1. Go to the [**Releases**](https://github.com/AmarilloNL/CircleClash/releases) page and download
   **`CircleClash-windows.zip`** from the latest release.
2. Extract it into its own folder (e.g. `Documents\CircleClash`). Inside you'll find
   `CircleClash.exe` next to a `_internal` folder — keep them together.
3. Double-click `CircleClash.exe`. On first run it creates a `CircleClash-data` folder right next to
   it for everything it needs.

No Python, no ffmpeg install, nothing to add to your PATH. On first launch CircleClash downloads
**danser-go** and **ffmpeg** automatically into that data folder, so the whole tool lives in one place
you can move or delete as a unit.

> You don't need to build anything. Running from source (below) is entirely optional — only useful if
> you want to modify the code.

### Linux

There's no prebuilt Linux release — on Linux you run from source (it's a quick venv setup, and the
app still provisions danser and ffmpeg itself). See [Linux — run from source](#linux--run-from-source) below.

---

## What you need

**Using the prebuilt app, you need nothing** — it provisions danser and ffmpeg itself. The table below
only applies if you choose to run from source.

| Requirement | Required? | Notes |
|---|---|---|
| **Python 3.10 or newer** | from source only | 3.10, 3.11, 3.12 all fine |
| **ffmpeg** | auto | downloaded on first run; or use a system ffmpeg if you already have one |
| **danser-go** | auto | downloaded automatically on first run |
| **osu! API key** | optional | adds avatars, ranks, flags and pp to the overlay |
| **NVIDIA GPU** | optional | only needed for the NVENC (GPU) encoders; everyone else uses x264/x265 |

---

## Linux — run from source

> On Linux you run CircleClash from source (no prebuilt release). It still downloads danser and ffmpeg
> for you on first run.

These steps use a **virtual environment**. On modern distros (Arch, Debian 12+, Fedora, …) a plain
`pip install` into the system Python is blocked with an *"externally-managed-environment"* error —
the virtual environment avoids that completely, so please don't skip it.

### 1. Install Python (ffmpeg is optional)

CircleClash downloads ffmpeg automatically on first run, so you don't have to install it. If you'd
rather use your distro's ffmpeg, the package is listed alongside Python below.

**Arch / CachyOS / Manjaro / EndeavourOS**
```bash
sudo pacman -S --needed python python-pip git    # add `ffmpeg` to use the system build
```

**Debian / Ubuntu / Linux Mint / Pop!_OS**
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv git    # add `ffmpeg` for the system build
```

**Fedora / Nobara**
```bash
sudo dnf install python3 python3-pip git
# optional system ffmpeg (RPM Fusion; Fedora's own build is codec-limited):
# sudo dnf install https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm && sudo dnf install ffmpeg
```

**openSUSE**
```bash
sudo zypper install python3 python3-pip git    # add `ffmpeg` for the system build
```

> NVENC (GPU encoding) on Linux also needs the **proprietary NVIDIA driver**. On the open/nouveau
> driver, stick to the **x264** or **x265** encoder in Settings.

### 2. Get CircleClash + install its Python packages
```bash
git clone https://github.com/AmarilloNL/CircleClash.git
cd CircleClash

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium          # <-- don't forget this; the overlay won't render without it
```

### 3. Run it
```bash
python renderer_gui.py
```

Next time, just `cd CircleClash`, run `source .venv/bin/activate`, then `python renderer_gui.py`.

---

## Windows

**You don't need this section to use CircleClash** — download `CircleClash-windows.zip` from
[**Releases**](https://github.com/AmarilloNL/CircleClash/releases), extract it and run `CircleClash.exe` (see [Download](#download)).
danser, ffmpeg and everything else are handled for you.

<details>
<summary><b>Optional: run from source on Windows</b> (only if you want to modify the code)</summary>

### 1. Install Python
Download the latest **Python 3.x** from [python.org/downloads](https://www.python.org/downloads/).
In the installer, **tick "Add python.exe to PATH"** on the first screen before clicking Install.

### 2. Get CircleClash + install its Python packages
Download the source (green **Code → Download ZIP** on GitHub) and unzip it. Then in PowerShell, inside
the unzipped folder:
```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
playwright install chromium
```
> If `Activate.ps1` is blocked, run PowerShell once as Administrator and execute
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then try again.

### 3. Run it
```powershell
py renderer_gui.py
```
ffmpeg and danser are downloaded automatically on first run, just like the packaged app — you don't
need to install them separately.

</details>

---

## macOS — run from source

> There's no prebuilt macOS app, and the automatic danser/ffmpeg download isn't available on macOS
> (neither ships an official macOS build), so on macOS you install both yourself via Homebrew below
> and point CircleClash at danser in Settings.

> macOS has no NVENC, so in Settings choose the **x264** or **x265** encoder (the NVENC options are
> for NVIDIA GPUs only).

### 1. Install Homebrew (if you don't have it)
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### 2. Install Python + ffmpeg
```bash
brew install python ffmpeg git
```

### 3. Get CircleClash + install its Python packages
```bash
git clone https://github.com/AmarilloNL/CircleClash.git
cd CircleClash

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium
```

### 4. Run it
```bash
python renderer_gui.py
```

---

## First run

A short welcome appears and checks your setup. Then:

1. **danser-go + ffmpeg** — if they aren't found, CircleClash offers to download them automatically
   (kept in the app's own data folder next to it). Choose *No* to point at an existing danser binary
   yourself.
2. **osu! Songs folder** — open **Settings** and set this to your osu! `Songs` folder so beatmaps
   can be located.
3. **osu! API** *(optional)* — see below.

## Using CircleClash

1. Drag a `.osr` replay onto **Player 1**, and another onto **Player 2** (or click a panel to browse).
2. Pick each player's **skin**, the **resolution** and **FPS**.
3. Press **Render**. Progress shows in the bar; expand **Show log** for details.
4. The finished `.mp4` lands in your configured **output folder**.

> Tip: for quick iteration render at **1080p / 60**. Full **4K / 240** looks great but takes much longer.

## Settings reference

- **Paths** — danser binary, **ffmpeg binary**, your osu! Songs & Skins
  folders, output folder. (The packaged app fills in danser and ffmpeg for you.)
- **osu! API** — optional client id/secret (avatars, ranks, flags, pp).
- **Timing** — gameplay tail after the last note, end-card hold, results animation speed.
- **Encoding**
  - *Encoder:* `x264`/`x265` (CPU, works everywhere) or `NVENC H.264/HEVC/AV1` (NVIDIA GPU; AV1 needs an RTX 40-series).
  - *Quality:* `lossless → high → balanced → compact` (quality vs file size).
  - *Auto-fix osu!lazer false fails:* on by default; only touches lazer replays.
- **Audio** — independent **P1/P2 music** and **P1/P2 hitsound** volumes plus a master. Both players
  play the same song, so P2 music defaults to 0 (turn it up to crossfade, or mute a side's hitsounds
  to hear only one player).
- **Visual** — tune the danser HUD, background and cursor (all optional, off by default):
  - *Background:* dark / dimmed / visible / blurred.
  - *Cursor size* and *trail length.*
  - *Effects:* bloom/glow, hit lighting, aim-error scatter meter, prominent unstable rate,
    pp breakdown (aim/speed/acc), per-side mods badge, ignore hitsound volume changes, disable storyboards.
  - *HUD elements:* hide the pp counter, hit counts, hit-error bar, key overlay or combo
    (score + accuracy always show).

## osu! API key (optional)

1. Go to osu! → **Settings → OAuth → New OAuth Application**.
2. Give it any name; the callback URL can be left blank.
3. Copy the **Client ID** and **Client Secret** into CircleClash → **Settings → osu! API**.

Credentials are stored only on your machine and are never committed to git.

---

## Troubleshooting

- **"Playwright missing" / the overlay doesn't render** — run `playwright install chromium` inside
  your activated virtual environment.
- **"ffmpeg not found"** — let CircleClash fetch it: it downloads ffmpeg on first run, or you can set
  the **ffmpeg binary** path in Settings → Paths. If you prefer a system ffmpeg, install it and make
  sure it's on your `PATH`, then reopen the app.
- **NVENC encoder fails** — your GPU/driver doesn't support the chosen NVENC codec (AV1 needs an
  RTX 40-series *and* a recent driver). CircleClash now detects this and **automatically falls back to
  x264** so the render still finishes; pick **NVENC H.264/HEVC** or **x264** in Settings to avoid the
  fallback, or update your NVIDIA driver for AV1.
- **The video won't play / looks broken in some players** — HEVC and AV1 aren't supported everywhere.
  For maximum compatibility (and for sharing locally), use the **x264** encoder.
- **`externally-managed-environment` on pip (Linux)** — you skipped the virtual environment. Create
  one with `python3 -m venv .venv && source .venv/bin/activate`, then install again.
- **A replay's map can't be found** — set your osu! **Songs folder** in Settings, and make sure the
  beatmap is actually downloaded in osu!.

---

## Credits & licenses

CircleClash orchestrates external tools that it does **not** bundle — it fetches them at runtime:

- **[danser-go](https://github.com/Wieku/danser-go)** (GPL-3.0) — renders the gameplay. Downloaded
  from its official GitHub releases on first run.
- **[ffmpeg](https://ffmpeg.org/)** — mixes the audio and stitches the final video. A static build
  (from [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds)) is downloaded on first run, or you
  can point at a system ffmpeg.
- **[Playwright](https://playwright.dev/)** — headless Chromium that renders the overlay and results card.

danser and ffmpeg are fetched at runtime and run as separate processes, so they keep their own
licenses and don't constrain this repository. CircleClash itself is released under the **MIT** license
(see `LICENSE`). Bundled and fetched components and their licenses are listed in
[`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md).

---

## For maintainers — building the apps

A ready-to-use GitHub Actions workflow is included at `.github/workflows/build.yml`. It builds a
**one-folder** [PyInstaller](https://pyinstaller.org/) app for **Windows**, zips it, and
attaches them to the GitHub Release. To cut a release:

```bash
git tag v1.1.0
git push origin v1.1.0
```

The workflow then produces `CircleClash-windows.zip` and
uploads them to the release for that tag. You can also trigger a test build manually from the
**Actions** tab.

How the packaging works (worth knowing if you tweak it):

- **One executable, two roles.** The GUI relaunches itself with `--run-pipeline` to do the actual
  render, so a single PyInstaller build is both the app and its render worker.
- **One folder (`--onedir`), zipped.** The build is a folder (an executable next to an `_internal`
  directory) that gets zipped for the release. Unlike `--onefile`, it doesn't unpack a ~300-400 MB
  bundle to a temp dir on every launch, so it starts near-instantly — which matters here because the
  self-relaunch above would otherwise pay that cost twice (GUI + each render).
- **Chromium is bundled.** The overlay/results card is rendered with Playwright's Chromium, installed
  with `PLAYWRIGHT_BROWSERS_PATH=0` and collected via `--collect-all playwright`; the frozen app sets
  the same variable so it loads the bundled browser. *(For a much smaller build, the alternative is to
  render the overlay with Qt/Pillow instead of HTML and drop Chromium entirely.)*
- **danser and ffmpeg are auto-provisioned, not embedded** — on first run the app downloads danser
  (GPL-3.0, from its official GitHub release) and a static ffmpeg build (GPL, from BtbN/FFmpeg-Builds)
  into its data folder. They run as separate processes, so CircleClash's own MIT license is unaffected
  and the binaries keep their own licenses. This keeps the executable small and the downloads easy to
  update.
- **Portable by default** — the packaged app stores danser, ffmpeg, render-songs and config in a
  `CircleClash-data` folder next to the executable, so the whole tool lives in one place the user
  controls. If that location isn't writable (e.g. Program Files) it falls back to the per-user data
  dir. From source you can opt in with `CIRCLECLASH_PORTABLE=1` (or `=/path/to/anchor`).

> First CI run tip: if Chromium isn't found at runtime, double-check the `PLAYWRIGHT_BROWSERS_PATH: '0'`
> env on both the install and build steps — that's the usual culprit.
