# Third-party notices

CircleClash itself is licensed under the MIT License (see `LICENSE`). It relies on
the third-party components listed below, each under its own license. Components are
either **bundled** into the packaged executable or **fetched at runtime** and run as
separate processes.

> This file is informational, not legal advice. If you redistribute CircleClash
> widely, review each component's license terms for yourself.

## Bundled in the packaged app

| Component | License | Notes |
|---|---|---|
| **Qt / PySide6** | LGPL v3 | The GUI toolkit. LGPL: the Qt libraries may be replaced by the user. The license text ships with PySide6, and unmodified Qt libraries are used. |
| **Playwright (Python)** | Apache-2.0 | Drives the headless browser that renders the overlay. |
| **Chromium** | BSD-3-Clause (plus bundled component licenses) | The headless browser Playwright downloads/bundles. |

## Fetched at runtime (not bundled)

These are downloaded on first run into the app's data folder and invoked as separate
processes. CircleClash does not link against them, so they keep their own licenses
and do not affect CircleClash's MIT license.

| Component | License | Source |
|---|---|---|
| **danser-go** | GPL-3.0 | Official GitHub releases — https://github.com/Wieku/danser-go |
| **ffmpeg** | GPL (v2 or later; the build includes GPLv3 components) | Static build from BtbN — https://github.com/BtbN/FFmpeg-Builds |

The ffmpeg build used is BtbN's "gpl" variant; its bundled `LICENSE`/`README` files
travel with the binary inside the downloaded archive.

## Python packages

Direct dependencies are listed in `requirements.txt` (PySide6, Playwright). Their
own dependencies carry their respective licenses.
