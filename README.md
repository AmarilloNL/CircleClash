# CircleClash

Turn two osu! replays into a **side-by-side comparison video** — real gameplay rendered by
[danser-go](https://github.com/Wieku/danser-go), a styled neon overlay, and an animated results
card at the end. Drop a `.osr` on each side, hit **Render**, get a shareable `.mp4`.

> Works with both **osu!stable** and **osu!lazer** replays. Lazer replays that danser would
> falsely show as "failed" are auto-corrected.

---

## Download

**Easiest (when available):** grab the prebuilt app from the
[**Releases**](https://github.com/AmarilloNL/CircleClash/releases) page — no Python needed.
You will still need **ffmpeg** installed (see below); danser is downloaded automatically on first run.

**From source:** follow the platform instructions below. This always works and is the recommended
route until prebuilt binaries are published.

---

## What you need

| Requirement | Required? | Notes |
|---|---|---|
| **Python 3.10 or newer** | yes (from source) | 3.10, 3.11, 3.12 all fine |
| **ffmpeg** | yes | must be reachable on your `PATH` |
| **danser-go** | yes | the app offers to download it automatically on first run |
| **osu! API key** | optional | adds avatars, ranks, flags and pp to the overlay |
| **NVIDIA GPU** | optional | only needed for the NVENC (GPU) encoders; everyone else uses x264/x265 |

---

## Install — Linux

These steps use a **virtual environment**. On modern distros (Arch, Debian 12+, Fedora, …) a plain
`pip install` into the system Python is blocked with an *"externally-managed-environment"* error —
the virtual environment avoids that completely, so please don't skip it.

### 1. Install Python + ffmpeg with your package manager

**Arch / CachyOS / Manjaro / EndeavourOS**
```bash
sudo pacman -S --needed python python-pip ffmpeg git
```

**Debian / Ubuntu / Linux Mint / Pop!_OS**
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv ffmpeg git
```

**Fedora / Nobara**
```bash
sudo dnf install python3 python3-pip git
# ffmpeg lives in RPM Fusion (the version in Fedora's own repos is codec-limited):
sudo dnf install https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm
sudo dnf install ffmpeg
```

**openSUSE**
```bash
sudo zypper install python3 python3-pip ffmpeg git
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

## Install — Windows

### 1. Install Python
Download the latest **Python 3.x** from [python.org/downloads](https://www.python.org/downloads/).
In the installer, **tick "Add python.exe to PATH"** on the first screen before clicking Install —
this one checkbox prevents the most common Windows error.

### 2. Install ffmpeg
Open **PowerShell** and run:
```powershell
winget install Gyan.FFmpeg
```
Then **close and reopen** PowerShell so the new `PATH` is picked up. Check it works:
```powershell
ffmpeg -version
```
(No winget? Download a build from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/), unzip it, and add
its `bin` folder to your `PATH`.)

### 3. Get CircleClash + install its Python packages
Download the source (green **Code → Download ZIP** on GitHub, or the release zip) and unzip it.
Then in PowerShell, inside the unzipped folder:
```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
playwright install chromium
```
> If `Activate.ps1` is blocked, run PowerShell once as Administrator and execute
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then try again.

### 4. Run it
```powershell
py renderer_gui.py
```

---

## Install — macOS

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

1. **danser-go** — if it isn't found, CircleClash offers to download it automatically (kept in the
   app's own data folder). Choose *No* to point at an existing danser binary instead.
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

- **Paths** — danser binary, danser video output dir, your osu! Songs & Skins folders, output folder.
- **osu! API** — optional client id/secret (avatars, ranks, flags, pp).
- **Timing** — gameplay tail after the last note, end-card hold, results animation speed.
- **Encoding**
  - *Encoder:* `x264`/`x265` (CPU, works everywhere) or `NVENC H.264/HEVC/AV1` (NVIDIA GPU; AV1 needs an RTX 40-series).
  - *Quality:* `lossless → high → balanced → compact` (quality vs file size).
  - *Auto-fix osu!lazer false fails:* on by default; only touches lazer replays.
- **Audio** — independent **P1/P2 music** and **P1/P2 hitsound** volumes plus a master. Both players
  play the same song, so P2 music defaults to 0 (turn it up to crossfade, or mute a side's hitsounds
  to hear only one player).

## osu! API key (optional)

1. Go to osu! → **Settings → OAuth → New OAuth Application**.
2. Give it any name; the callback URL can be left blank.
3. Copy the **Client ID** and **Client Secret** into CircleClash → **Settings → osu! API**.

Credentials are stored only on your machine and are never committed to git.

---

## Troubleshooting

- **"Playwright missing" / the overlay doesn't render** — run `playwright install chromium` inside
  your activated virtual environment.
- **"ffmpeg not found"** — ffmpeg isn't on your `PATH`. Reinstall per the steps above and reopen your
  terminal. Verify with `ffmpeg -version`.
- **NVENC encoder fails** — you don't have a supported NVIDIA GPU/driver (AV1 needs RTX 40-series).
  Switch the encoder to **x264** in Settings.
- **The video won't play / looks broken in some players** — HEVC and AV1 aren't supported everywhere.
  For maximum compatibility (and for sharing locally), use the **x264** encoder.
- **`externally-managed-environment` on pip (Linux)** — you skipped the virtual environment. Create
  one with `python3 -m venv .venv && source .venv/bin/activate`, then install again.
- **A replay's map can't be found** — set your osu! **Songs folder** in Settings, and make sure the
  beatmap is actually downloaded in osu!.

---

## Credits & licenses

CircleClash orchestrates two external tools that it does **not** bundle:

- **[danser-go](https://github.com/Wieku/danser-go)** (GPL-3.0) — renders the gameplay. Downloaded
  from its official GitHub releases on first run.
- **[ffmpeg](https://ffmpeg.org/)** — mixes the audio and stitches the final video.
- **[Playwright](https://playwright.dev/)** — headless Chromium that renders the overlay and results card.

Because danser is fetched at runtime rather than shipped, its license doesn't constrain this
repository's license. *(Add your own `LICENSE` file — MIT is a common choice.)*

---

## For maintainers — building the apps

A ready-to-use GitHub Actions workflow is included at `.github/workflows/build.yml`. It builds a
**single-file** [PyInstaller](https://pyinstaller.org/) executable for **Windows** and **Linux** and
attaches them to the GitHub Release. To cut a release:

```bash
git tag v1.0.0
git push origin v1.0.0
```

The workflow then produces `CircleClash-windows.exe` and `CircleClash-linux` (one file each) and
uploads them to the release for that tag. You can also trigger a test build manually from the
**Actions** tab.

How the packaging works (worth knowing if you tweak it):

- **One executable, two roles.** The GUI relaunches itself with `--run-pipeline` to do the actual
  render, so a single PyInstaller build is both the app and its render worker.
- **Single file (`--onefile`).** Convenient to share, but the exe unpacks to a temp folder on launch;
  because of the self-relaunch above, that happens once for the GUI and again for each render, adding
  a few seconds on top of a large (~300-400 MB) bundle. If startup ever feels too slow, switch the
  workflow to `--onedir` (a folder you zip) — it doesn't unpack on launch.
- **Chromium is bundled.** The overlay/results card is rendered with Playwright's Chromium, installed
  with `PLAYWRIGHT_BROWSERS_PATH=0` and collected via `--collect-all playwright`; the frozen app sets
  the same variable so it loads the bundled browser. *(For a much smaller build, the alternative is to
  render the overlay with Qt/Pillow instead of HTML and drop Chromium entirely.)*
- **ffmpeg and danser are not bundled** — danser is auto-fetched on first run; ffmpeg stays a
  documented prerequisite.

> First CI run tip: if Chromium isn't found at runtime, double-check the `PLAYWRIGHT_BROWSERS_PATH: '0'`
> env on both the install and build steps — that's the usual culprit.
