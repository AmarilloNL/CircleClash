#!/usr/bin/env python3
"""
renderer_gui.py — desktop front-end for CircleClash
================================================
Drag two .osr replays (and optionally a .osk skin), pick a title, hit Render.
The heavy pipeline (make_overlay_video.py) runs as a *subprocess* via Qt's
QProcess, so a render crash can't take down the GUI and Playwright's sync API
never fights Qt's event loop. danser's "Progress: N%" lines are parsed straight
off the stream to drive the progress bar.

Settings (danser paths, Songs dir, output dir, optional osu! API creds, tail /
end-card seconds, nvenc) persist to a per-user config file in the OS app-data
dir, so credentials never live in the code or the binary.

Requires: PySide6  (pip install PySide6), plus the CircleClash modules alongside
this file. osr_parser is used (stdlib-only) to show player names on drop.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import html as ihtml
import tempfile
import zipfile
from pathlib import Path

from PySide6.QtCore import Qt, QProcess, QProcessEnvironment, Signal, QObject, QThread, QRectF
from PySide6.QtGui import (
    QFont, QDragEnterEvent, QDropEvent, QPainter, QColor,
    QLinearGradient, QRadialGradient, QPen, QBrush, QFontMetrics, QTextCursor, QIcon,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QHBoxLayout, QGridLayout, QFileDialog, QPlainTextEdit,
    QProgressBar, QDialog, QDoubleSpinBox, QCheckBox, QFormLayout, QFrame,
    QMessageBox, QGroupBox, QComboBox, QSlider, QScrollArea, QTabWidget,
)

try:
    import danser_setup
except Exception:
    danser_setup = None

try:
    import ffmpeg_setup
except Exception:
    ffmpeg_setup = None

HERE = Path(__file__).resolve().parent
PIPELINE = HERE / "make_overlay_video.py"


def resource_path(name: str) -> Path:
    """Resolve a bundled resource both from source and inside a PyInstaller build
    (where data files live under sys._MEIPASS)."""
    base = Path(getattr(sys, "_MEIPASS", HERE))
    p = base / name
    return p if p.exists() else HERE / name


def app_icon() -> QIcon:
    for name in ("icon.ico", "icon.png"):
        p = resource_path(name)
        if p.exists():
            return QIcon(str(p))
    return QIcon()

PINK = "#ff66ab"
ICE = "#66d9ff"
GOLD = "#ffd24a"
GREEN = "#7fe0a0"
RED = "#ff6b81"
INK = "#0a0a0d"
INK2 = "#101015"
PANEL = "#14141b"
PANEL2 = "#0d0d12"
LINE = "#22222c"
TXT = "#f1f1f6"
MUTED = "#8a8a99"
MUT2 = "#5b5b69"

UI_FONT = "'Exo 2','Inter','Segoe UI',sans-serif"
MONO_FONT = "'JetBrains Mono','DejaVu Sans Mono',monospace"
DISP_FAMILIES = ["Orbitron", "Exo 2", "Segoe UI", "sans-serif"]

# (config key, friendly label) — mirrors ENCODERS in make_overlay_video.py
ENCODER_OPTIONS = [
    ("x264",       "x264 · CPU · H.264 · most compatible"),
    ("x265",       "x265 · CPU · H.265 · smaller, slow"),
    ("nvenc_h264", "NVENC H.264 · GPU · fast"),
    ("nvenc_hevc", "NVENC HEVC · GPU · smaller files"),
    ("nvenc_av1",  "NVENC AV1 · GPU · smallest (RTX 40-series+)"),
]
QUALITY_OPTIONS = [
    ("lossless", "lossless · biggest"),
    ("high",     "high · recommended"),
    ("balanced", "balanced"),
    ("compact",  "compact · smallest"),
]

try:
    from osr_parser import parse_replay
except Exception:
    parse_replay = None


# --------------------------------------------------------------------------- #
# Config (per-user, OS app-data dir — secrets never touch the code)
# --------------------------------------------------------------------------- #
def config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData/Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    d = Path(base) / "osu-renderer"
    d.mkdir(parents=True, exist_ok=True)
    return d


CONFIG_PATH = config_dir() / "config.json"

DEFAULT_CONFIG = {
    "danser_bin": "",
    "ffmpeg_bin": "",
    "songs_dir": "",
    "skins_dir": "",
    "output_dir": str(Path.home()),
    "api_client_id": "",
    "api_client_secret": "",
    "tail_seconds": 7.0,
    "endcard_seconds": 3.0,
    "endcard_speed": 1.0,
    "encoder": "x264",
    "quality": "high",
    "no_fail": True,
    "force_skin_hits": True,
    "resolution": "1080p",
    "fps": 60,
    "left_music_volume": 100,
    "left_hitsound_volume": 100,
    "right_music_volume": 0,
    "right_hitsound_volume": 100,
    "master_volume": 100,
    # --- visual tweaks (danser HUD / background / cursor) ---
    "vis_bg_style": "dark",          # dark | dimmed | visible | blurred
    "vis_bloom": False,
    "vis_hit_lighting": False,
    "vis_aim_error": False,
    "vis_pp_components": False,
    "vis_prominent_ur": False,
    "vis_show_mods": False,
    "vis_ignore_sample_volume": False,
    "vis_no_storyboards": False,
    "vis_cursor_size": 100,          # percent of danser default (12 osu!px)
    "vis_trail_length": 100,         # percent of danser default
    "vis_show_pp": True,
    "vis_show_hitcounts": True,
    "vis_show_hiterror": True,
    "vis_show_keys": True,
    "vis_show_combo": True,
    "welcomed": False,
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def _sanitize(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip() or "match"


def _argb(hex6: str, alpha: int) -> str:
    """Qt stylesheets put alpha FIRST (#AARRGGBB), unlike CSS. alpha is 0-255."""
    return f"#{alpha:02x}{hex6.lstrip('#')}"


def _theme_qss() -> str:
    """The neon/Versus stylesheet, shared by the main window and every dialog so
    inputs, combos, sliders and buttons look consistent everywhere."""
    return f"""
        QMainWindow,QDialog,QWidget{{background:{INK};color:{TXT};font-family:{UI_FONT};font-size:14px;}}
        QLabel{{color:#cfcfd8;background:transparent;}}
        QLabel[role="field"]{{color:{MUTED};font-family:{MONO_FONT};font-size:11px;
            font-weight:600;letter-spacing:1px;text-transform:uppercase;}}
        QLabel[role="section"]{{color:{ICE};background:transparent;font-family:{MONO_FONT};
            font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;padding:2px 0;}}
        QLabel[role="status"]{{color:{MUTED};font-family:{MONO_FONT};font-size:12px;}}
        QFrame[role="panel"]{{background:{INK2};border:1px solid {LINE};border-radius:14px;}}
        QFrame[role="hr"]{{background:{LINE};border:none;max-height:1px;min-height:1px;}}
        QLineEdit,QDoubleSpinBox,QComboBox{{background:{PANEL2};border:1px solid {LINE};
            border-radius:10px;padding:8px 12px;color:{TXT};min-height:22px;}}
        QLineEdit:focus,QComboBox:focus,QDoubleSpinBox:focus{{border-color:{ICE};}}
        QComboBox::drop-down{{border:none;width:24px;}}
        QComboBox::down-arrow{{image:none;border-left:4px solid transparent;border-right:4px solid transparent;
            border-top:5px solid {MUTED};margin-right:10px;}}
        QComboBox QAbstractItemView{{background:{PANEL};border:1px solid {LINE};
            selection-background-color:{PINK};selection-color:{INK};outline:none;padding:4px;}}
        QCheckBox{{color:{TXT};background:transparent;spacing:9px;}}
        QCheckBox::indicator{{width:18px;height:18px;border:1px solid {LINE};border-radius:5px;background:{PANEL2};}}
        QCheckBox::indicator:hover{{border-color:{ICE};}}
        QCheckBox::indicator:checked{{border:none;
            background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {PINK},stop:1 {ICE});}}
        QSlider::groove:horizontal{{height:6px;background:{PANEL2};border:1px solid {LINE};border-radius:3px;}}
        QSlider::sub-page:horizontal{{border-radius:3px;
            background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {PINK},stop:1 {ICE});}}
        QSlider::handle:horizontal{{width:15px;height:15px;margin:-6px 0;border-radius:8px;
            background:{TXT};border:2px solid {ICE};}}
        QSlider::handle:horizontal:hover{{background:{ICE};}}
        QPushButton{{background:{PANEL};border:1px solid #2b2b36;border-radius:10px;
            padding:9px 16px;color:{TXT};font-weight:600;}}
        QPushButton:hover{{border-color:{ICE};}}
        QPushButton:disabled{{color:{MUT2};border-color:#1d1d25;background:#101016;}}
        QPushButton[role="primary"]{{border:none;color:{INK};font-weight:800;padding:10px 22px;
            background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {PINK},stop:1 {ICE});}}
        QPushButton[role="primary"]:disabled{{background:#23232c;color:{MUT2};}}
        QPushButton[role="link"]{{background:transparent;border:none;text-align:left;
            color:{MUTED};padding:4px 2px;font-weight:500;}}
        QPushButton[role="link"]:hover{{color:{TXT};}}
        QProgressBar{{background:{PANEL2};border:1px solid {LINE};border-radius:6px;}}
        QProgressBar::chunk{{border-radius:5px;
            background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {PINK},stop:1 {ICE});}}
        QPlainTextEdit#logview{{background:#08080c;color:#b9b9c6;
            font-family:{MONO_FONT};font-size:11px;border:1px solid {LINE};border-radius:10px;}}
        QScrollArea{{border:none;background:transparent;}}
        QTabWidget#dlgTabs::pane{{border:none;background:transparent;}}
        QTabBar{{background:transparent;}}
        QTabBar::tab{{background:transparent;color:{MUTED};padding:8px 14px;margin:0 2px;
            border:none;border-bottom:2px solid transparent;font-size:12px;}}
        QTabBar::tab:hover{{color:{TXT};}}
        QTabBar::tab:selected{{color:{TXT};border-bottom:2px solid {PINK};}}
        QScrollBar:vertical{{background:transparent;width:10px;margin:2px;}}
        QScrollBar::handle:vertical{{background:#2c2c38;border-radius:5px;min-height:30px;}}
        QScrollBar::handle:vertical:hover{{background:#3a3a48;}}
        QScrollBar::add-line,QScrollBar::sub-line{{height:0;}}
    """


def _pretty_detail(fname: str) -> str | None:
    """Pull a readable 'Artist - Title (Mapper) [Diff]' out of a danser/osu replay
    filename like 'miyana playing Artist - Title [Diff] (2026-06-15_21-16).osr'.
    Returns None for web-export names (e.g. 'solo-replay-osu_..._...osr')."""
    stem = re.sub(r"\.osr$", "", fname, flags=re.I)
    stem = re.sub(r"\s*\(\d{4}-\d{2}-\d{2}[_ ]\d{2}-\d{2}(-\d{2})?\)\s*$", "", stem)
    if " playing " in stem:
        tail = stem.split(" playing ", 1)[1].strip()
        return tail or None
    return None


def _elide(s: str, n: int = 52) -> str:
    """Middle-ellipsis a long string so it never wraps in the drop zone."""
    if len(s) <= n:
        return s
    head = (n - 1) // 2
    tail = n - 1 - head
    return s[:head] + "…" + s[-tail:]


# --------------------------------------------------------------------------- #
# danser auto-setup worker (runs the download off the UI thread)
# --------------------------------------------------------------------------- #
class DanserSetupWorker(QObject):
    progress = Signal(float, str)
    done = Signal(str, str)   # (danser_path, ffmpeg_path) — ffmpeg may be ""
    failed = Signal(str)      # message on failure

    def run(self):
        try:
            dpath = danser_setup.ensure(progress=lambda f, m: self.progress.emit(f, m))
        except Exception as e:
            self.failed.emit(str(e))
            return
        # ffmpeg is best-effort: if it's already around (managed or on PATH) reuse
        # it, otherwise try to fetch it, but never fail the whole setup over it.
        fpath = ""
        if ffmpeg_setup is not None:
            try:
                existing = ffmpeg_setup.find_local_ffmpeg()
                if existing is None and shutil.which("ffmpeg") is None:
                    self.progress.emit(0.0, "Fetching ffmpeg…")
                    existing = ffmpeg_setup.install(
                        progress=lambda f, m: self.progress.emit(f, m))
                if existing:
                    fpath = str(existing[0])
            except Exception:
                fpath = ""
        self.done.emit(dpath, fpath)


# --------------------------------------------------------------------------- #
# Drop zone widget
# --------------------------------------------------------------------------- #
class GradientTitle(QWidget):
    """CircleClash wordmark, painted pink → white → ice (Qt can't gradient-fill text via QSS)."""
    def __init__(self, text: str = "CircleClash"):
        super().__init__()
        self._text = text
        self._font = QFont()
        self._font.setFamilies(DISP_FAMILIES)
        self._font.setPixelSize(30)
        self._font.setWeight(QFont.Black)
        self._font.setLetterSpacing(QFont.AbsoluteSpacing, 4)
        self.setMinimumHeight(42)
        # reserve enough width for the text so a longer wordmark never clips
        self.setMinimumWidth(QFontMetrics(self._font).horizontalAdvance(text) + 12)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setFont(self._font)
        r = self.rect()
        g = QLinearGradient(r.left(), 0, r.right(), 0)
        g.setColorAt(0.0, QColor(PINK))
        g.setColorAt(0.40, QColor("#ffffff"))
        g.setColorAt(0.60, QColor("#ffffff"))
        g.setColorAt(1.0, QColor(ICE))
        p.setPen(QPen(QBrush(g), 1))
        p.drawText(r, Qt.AlignVCenter | Qt.AlignLeft, self._text)


class VsNode(QWidget):
    """Glowing pink/ice ring with VS, sits between the two drop zones."""
    def __init__(self):
        super().__init__()
        self.setFixedSize(64, 96)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy, R = 32, self.height() / 2, 23
        # connecting seam
        seam = QLinearGradient(0, 8, 0, self.height() - 8)
        seam.setColorAt(0, QColor(PINK)); seam.setColorAt(1, QColor(ICE))
        p.setPen(QPen(QBrush(seam), 2))
        p.drawLine(int(cx), 8, int(cx), self.height() - 8)
        # glow
        glow = QRadialGradient(cx, cy, 32)
        glow.setColorAt(0, QColor(255, 102, 171, 70))
        glow.setColorAt(0.6, QColor(102, 217, 255, 40))
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen); p.setBrush(glow)
        p.drawEllipse(QRectF(cx - 32, cy - 32, 64, 64))
        # disc fill
        fill = QRadialGradient(cx, cy, R)
        fill.setColorAt(0, QColor("#1a1a22")); fill.setColorAt(1, QColor("#0d0d12"))
        ring = QLinearGradient(cx - R, cy - R, cx + R, cy + R)
        ring.setColorAt(0, QColor(PINK)); ring.setColorAt(1, QColor(ICE))
        p.setBrush(fill); p.setPen(QPen(QBrush(ring), 2))
        p.drawEllipse(QRectF(cx - R, cy - R, 2 * R, 2 * R))
        # VS label
        f = QFont(); f.setFamilies(DISP_FAMILIES); f.setPixelSize(15); f.setWeight(QFont.Black)
        p.setFont(f); p.setPen(QColor(TXT))
        p.drawText(self.rect(), Qt.AlignCenter, "VS")


class DropZone(QFrame):
    fileDropped = Signal(str)

    def __init__(self, title: str, accent: str, exts: tuple[str, ...], side: str = "left"):
        super().__init__()
        self._exts = exts
        self._accent = accent
        self._side = side
        self.path: str | None = None
        self.setAcceptDrops(True)
        self.setMinimumHeight(150)
        self._title = title
        self._filled = False
        self._base_style()

        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(7)

        self.titleLabel = QLabel(title)
        self.titleLabel.setAlignment(Qt.AlignCenter)
        tf = QFont(); tf.setFamilies(DISP_FAMILIES); tf.setPixelSize(12); tf.setWeight(QFont.Bold)
        tf.setLetterSpacing(QFont.AbsoluteSpacing, 3)
        self.titleLabel.setFont(tf)
        self.titleLabel.setStyleSheet(f"color:{accent};background:transparent;")

        # short accent underline tick — reads as a heading, not an input box
        self.tick = QFrame()
        self.tick.setFixedSize(26, 2)
        self.tick.setStyleSheet(f"background:{accent};border:none;border-radius:1px;")

        # empty state: glyph + concise affordance
        self.hintLabel = QLabel("↓\ndrop .osr replay\nor click to browse")
        self.hintLabel.setAlignment(Qt.AlignCenter)
        self.hintLabel.setStyleSheet(f"color:{MUTED};font-size:12px;background:transparent;")

        # filled state: player name (headline) + map/file (muted, single line)
        self.nameLabel = QLabel("")
        self.nameLabel.setAlignment(Qt.AlignCenter)
        nf = QFont(); nf.setFamilies(["Exo 2", "Inter", "Segoe UI", "sans-serif"]); nf.setPixelSize(17); nf.setWeight(QFont.DemiBold)
        self.nameLabel.setFont(nf)
        self.nameLabel.setStyleSheet(f"color:{TXT};background:transparent;")
        self.nameLabel.hide()

        self.detailLabel = QLabel("")
        self.detailLabel.setAlignment(Qt.AlignCenter)
        self.detailLabel.setWordWrap(False)
        self.detailLabel.setStyleSheet(f"color:{MUTED};font-size:11px;background:transparent;")
        self.detailLabel.hide()

        lay.addWidget(self.titleLabel)
        lay.addWidget(self.tick, alignment=Qt.AlignHCenter)
        lay.addSpacing(2)
        lay.addWidget(self.hintLabel)
        lay.addWidget(self.nameLabel)
        lay.addWidget(self.detailLabel)

    def _radius(self) -> str:
        if self._side == "left":
            return "border-top-left-radius:14px;border-bottom-left-radius:14px;border-top-right-radius:5px;border-bottom-right-radius:5px;"
        return "border-top-right-radius:14px;border-bottom-right-radius:14px;border-top-left-radius:5px;border-bottom-left-radius:5px;"

    def _base_style(self, hot: bool = False):
        a = self._accent
        style = "solid" if (self._filled or hot) else "dashed"
        width = 2 if (self._filled or hot) else 1
        # accent-tinted vertical wash, stronger when hot/filled
        top = _argb(a, 0x26) if (hot or self._filled) else _argb(a, 0x14)
        self.setStyleSheet(
            f"QFrame{{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            f"stop:0 {top}, stop:0.75 {PANEL2}, stop:1 {PANEL2});"
            f"border:{width}px {style} {a};{self._radius()}}}")

    def mousePressEvent(self, e):
        filt = "osu files (" + " ".join(f"*{x}" for x in self._exts) + ")"
        f, _ = QFileDialog.getOpenFileName(self, f"Choose {self._title}", "", filt)
        if f:
            self.set_file(f)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls() and self._ok(e.mimeData().urls()[0].toLocalFile()):
            e.acceptProposedAction()
            self._base_style(hot=True)

    def dragLeaveEvent(self, e):
        self._base_style(hot=False)

    def dropEvent(self, e: QDropEvent):
        f = e.mimeData().urls()[0].toLocalFile()
        if self._ok(f):
            self.set_file(f)
        else:
            self._base_style(hot=False)

    def _ok(self, f: str) -> bool:
        return f.lower().endswith(self._exts)

    def set_file(self, f: str):
        self.path = f
        self._filled = True
        name = Path(f).name

        player = None
        if parse_replay and f.lower().endswith(".osr"):
            try:
                player = parse_replay(f).player
            except Exception:
                player = None

        # headline: player name if we can read it, else the bare filename stem
        self.nameLabel.setText(player or _elide(Path(f).stem, 40))
        # subline: a clean 'Artist - Title [Diff]' if the name encodes it, else the file
        detail = _pretty_detail(name) or name
        self.detailLabel.setText(_elide(detail, 56))
        self.detailLabel.setToolTip(name)

        self.hintLabel.hide()
        self.nameLabel.show()
        self.detailLabel.show()
        self._base_style(hot=False)
        self.fileDropped.emit(f)


# --------------------------------------------------------------------------- #
# Settings dialog
# --------------------------------------------------------------------------- #
class WelcomeDialog(QDialog):
    """First-run welcome: explains the tool and shows a live dependency check."""
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome")
        self.setStyleSheet(_theme_qss())
        self.setMinimumWidth(540)
        root = QVBoxLayout(self); root.setContentsMargins(28, 24, 28, 20); root.setSpacing(14)

        root.addWidget(GradientTitle("CircleClash"))
        tag = QLabel("Turn two osu! replays into a side-by-side comparison video — "
                     "gameplay, a styled overlay and an animated results card.")
        tag.setWordWrap(True); tag.setStyleSheet(f"color:{MUTED};background:transparent;font-size:13px;")
        root.addWidget(tag)

        # live dependency check
        card = QFrame(); card.setProperty("role", "panel")
        cv = QVBoxLayout(card); cv.setContentsMargins(18, 16, 18, 16); cv.setSpacing(11)
        sec = QLabel("What you'll need"); sec.setProperty("role", "section")
        cv.addWidget(sec)

        cfg_ff = cfg.get("ffmpeg_bin", "")
        ffmpeg_ok = bool(cfg_ff) and (Path(cfg_ff).exists() or shutil.which(cfg_ff) is not None)
        if not ffmpeg_ok and ffmpeg_setup is not None:
            try:
                ffmpeg_ok = ffmpeg_setup.find_local_ffmpeg() is not None
            except Exception:
                pass
        if not ffmpeg_ok:
            ffmpeg_ok = shutil.which("ffmpeg") is not None
        danser_ok = bool(cfg.get("danser_bin")) and Path(cfg["danser_bin"]).exists()
        if not danser_ok and danser_setup is not None:
            try:
                danser_ok = danser_setup.find_local_danser() is not None
            except Exception:
                pass

        cv.addLayout(self._req("danser-go", danser_ok, "required · renders the gameplay",
                               "found" if danser_ok else "we'll set it up in a moment"))
        cv.addLayout(self._req("ffmpeg", ffmpeg_ok, "required · stitches the video",
                               "found" if ffmpeg_ok else "we'll set it up in a moment"))
        cv.addLayout(self._req("osu! API key", bool(cfg.get("api_client_id")),
                               "optional · avatars, ranks, flags & pp",
                               "configured" if cfg.get("api_client_id") else "add later in Settings"))
        root.addWidget(card)

        how = QLabel("Then just drop a replay onto each side and hit Render. "
                     "You can tweak paths, encoding and audio anytime in Settings.")
        how.setWordWrap(True); how.setStyleSheet(f"color:{MUTED};background:transparent;font-size:12px;")
        root.addWidget(how)

        row = QHBoxLayout(); row.addStretch(1)
        go = QPushButton("Let's go"); go.setProperty("role", "primary"); go.clicked.connect(self.accept)
        row.addWidget(go)
        root.addLayout(row)

    def _req(self, name: str, ok: bool, what: str, detail: str) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(11)
        icon = QLabel("✓" if ok else "•")
        icon.setStyleSheet(f"color:{ICE if ok else GOLD};background:transparent;"
                           f"font-size:15px;font-weight:800;")
        icon.setFixedWidth(14)
        txt = QLabel(f"<b style='color:{TXT}'>{name}</b> "
                     f"<span style='color:{MUT2}'>· {what}</span><br>"
                     f"<span style='color:{MUTED}'>{detail}</span>")
        txt.setWordWrap(True); txt.setStyleSheet("background:transparent;font-size:13px;")
        row.addWidget(icon, 0, Qt.AlignTop); row.addWidget(txt, 1)
        return row


class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(600)
        self.setStyleSheet(_theme_qss())
        self.cfg = dict(cfg)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # --- header band ---
        header = QWidget()
        header.setObjectName("dlgHeader")
        header.setStyleSheet(f"#dlgHeader{{background:{INK2};border-bottom:1px solid {LINE};}}")
        hl = QVBoxLayout(header); hl.setContentsMargins(24, 18, 24, 16); hl.setSpacing(3)
        ht = QLabel("Settings")
        htf = QFont(); htf.setFamilies(["Exo 2", "Inter", "Segoe UI", "sans-serif"])
        htf.setPixelSize(20); htf.setWeight(QFont.Bold)
        ht.setFont(htf); ht.setStyleSheet(f"color:{TXT};background:transparent;")
        hs = QLabel("Paths, encoding and audio · saved only on this machine")
        hs.setStyleSheet(f"color:{MUTED};background:transparent;font-size:12px;")
        hl.addWidget(ht); hl.addWidget(hs)
        outer.addWidget(header)

        # --- tabbed body ---
        tabs = QTabWidget()
        tabs.setObjectName("dlgTabs")
        tabs.setDocumentMode(True)

        # Paths
        pl = self._tab(tabs, "Paths")
        pf = self._form()
        # Show the actual installed binaries even if config somehow lost the path:
        # resolve the managed/portable danser + ffmpeg live when the dialog opens.
        danser_val = cfg.get("danser_bin", "")
        if (not danser_val or not Path(danser_val).exists()) and danser_setup:
            try:
                loc = danser_setup.find_local_danser()
                if loc:
                    danser_val = str(loc)
            except Exception:
                pass
        ffmpeg_val = cfg.get("ffmpeg_bin", "")
        if (not ffmpeg_val or not Path(ffmpeg_val).exists()) and ffmpeg_setup:
            try:
                got = ffmpeg_setup.find_local_ffmpeg()
                if got:
                    ffmpeg_val = str(got[0])
            except Exception:
                pass
        self.danser_bin = self._file_row(pf, "danser binary", danser_val, pick_file=True)
        self.ffmpeg_bin = self._file_row(pf, "ffmpeg binary", ffmpeg_val, pick_file=True)
        self.songs = self._file_row(pf, "osu! Songs folder (your library)", cfg["songs_dir"], pick_file=False)
        self.skins = self._file_row(pf, "osu! Skins folder", cfg.get("skins_dir", ""), pick_file=False)
        self.output = self._file_row(pf, "output folder", cfg["output_dir"], pick_file=False)
        pl.addLayout(pf)
        # Diagnostic: spell out where we looked and what we found, so an empty danser
        # field is explainable instead of mysterious.
        diag = "The packaged app fills in danser and ffmpeg for you."
        try:
            if danser_setup and not danser_val:
                root = Path(danser_setup.data_root())
                found_any = None
                try:
                    for p in root.rglob("*"):
                        if p.is_file() and p.name.lower().startswith("danser-cli"):
                            found_any = p
                            break
                except Exception:
                    pass
                if found_any:
                    diag = (f"danser is at {found_any} but wasn't auto-detected — "
                            f"click the ⋯ button to select it.")
                else:
                    diag = (f"danser not found under {root / 'danser'}. It may still be "
                            f"downloading or the download failed; reopen Settings later, "
                            f"or use ⋯ to pick danser-cli.exe yourself.")
        except Exception:
            pass
        pl.addWidget(self._hint(diag))
        pl.addStretch(1)

        # osu! API
        al = self._tab(tabs, "osu! API")
        af = self._form()
        self.cid = QLineEdit(cfg["api_client_id"])
        self.csecret = QLineEdit(cfg["api_client_secret"]); self.csecret.setEchoMode(QLineEdit.Password)
        af.addRow("client id", self.cid)
        af.addRow("client secret", self.csecret)
        al.addLayout(af)
        al.addWidget(self._hint("Optional, but enables avatars, ranks, flags and pp. Register a "
                                "personal OAuth app at osu! → Settings → OAuth."))
        al.addStretch(1)

        # Timing
        tl = self._tab(tabs, "Timing")
        tf = self._form()
        self.tail = QDoubleSpinBox(); self.tail.setRange(0, 30); self.tail.setValue(cfg["tail_seconds"]); self.tail.setSuffix(" s")
        self.hold = QDoubleSpinBox(); self.hold.setRange(0, 30); self.hold.setValue(cfg["endcard_seconds"]); self.hold.setSuffix(" s")
        self.espeed = QDoubleSpinBox(); self.espeed.setRange(0.3, 3.0); self.espeed.setSingleStep(0.05)
        self.espeed.setValue(cfg.get("endcard_speed", 1.0)); self.espeed.setSuffix("×")
        tf.addRow("gameplay tail after last note", self.tail)
        tf.addRow("end-card hold", self.hold)
        tf.addRow("results animation speed", self.espeed)
        tl.addLayout(tf)
        tl.addStretch(1)

        # Encoding
        el = self._tab(tabs, "Encoding")
        ef = self._form()
        enc_default = cfg.get("encoder") or ("nvenc_h264" if cfg.get("nvenc") else "x264")
        self.encoder = QComboBox()
        for key, label in ENCODER_OPTIONS:
            self.encoder.addItem(label, key)
        i = self.encoder.findData(enc_default)
        self.encoder.setCurrentIndex(i if i >= 0 else 0)
        self.encoder.setToolTip("NVENC options use your GPU's hardware encoder (much faster; AV1 "
                                "needs an RTX 40-series). x264/x265 run on CPU and work anywhere.")
        ef.addRow("encoder", self.encoder)
        self.quality = QComboBox()
        for key, label in QUALITY_OPTIONS:
            self.quality.addItem(label, key)
        qi = self.quality.findData(cfg.get("quality", "high"))
        self.quality.setCurrentIndex(qi if qi >= 0 else 1)
        self.quality.setToolTip("Quality vs file-size trade-off. 'high' is visually clean; "
                                "'compact' trades some quality for much smaller files.")
        ef.addRow("quality", self.quality)
        el.addLayout(ef)
        self.nofail = QCheckBox("auto-fix osu!lazer false fails")
        self.nofail.setChecked(cfg.get("no_fail", True))
        self.nofail.setToolTip("Detects osu!lazer replays (which danser's stable HP model can "
                               "falsely show as failed) and renders just those as NoFail. "
                               "osu!stable replays are always left exactly as recorded.")
        el.addWidget(self.nofail)
        el.addWidget(self._hint("x264 (CPU) works everywhere; if an NVENC encoder isn't supported "
                                "by your GPU/driver, CircleClash falls back to x264 automatically."))
        el.addStretch(1)

        # Audio
        aul = self._tab(tabs, "Audio")
        self.skinhits = QCheckBox("force skin hitsounds (ignore the beatmap's hitsounds)")
        self.skinhits.setChecked(cfg.get("force_skin_hits", True))
        self.skinhits.setToolTip("Use the chosen skin's hitsounds for every map instead of the "
                                 "samples baked into each beatmap, for a consistent sound.")
        aul.addWidget(self.skinhits)
        sf = self._form()
        self.vol_l_music = self._slider(cfg.get("left_music_volume", cfg.get("music_volume", 100)))
        self.vol_l_hit = self._slider(cfg.get("left_hitsound_volume", cfg.get("hitsound_volume", 100)))
        self.vol_r_music = self._slider(cfg.get("right_music_volume", 0))
        self.vol_r_hit = self._slider(cfg.get("right_hitsound_volume", cfg.get("hitsound_volume", 100)))
        self.vol_master = self._slider(cfg.get("master_volume", 100))
        sf.addRow("P1 music", self.vol_l_music["w"])
        sf.addRow("P1 hitsounds", self.vol_l_hit["w"])
        sf.addRow("P2 music", self.vol_r_music["w"])
        sf.addRow("P2 hitsounds", self.vol_r_hit["w"])
        sf.addRow("master", self.vol_master["w"])
        aul.addLayout(sf)
        aul.addWidget(self._hint("Both players play the same song, so P2 music defaults to 0 to "
                                 "avoid doubling the track. Turn it up to crossfade, or mute a "
                                 "side's hitsounds to hear only one player."))
        aul.addStretch(1)

        # Visual
        vl = self._tab(tabs, "Visual")
        vf = self._form()
        self.bgstyle = QComboBox()
        for key, label in (("dark", "Dark (default)"), ("dimmed", "Dimmed background"),
                           ("visible", "Background visible"), ("blurred", "Background blurred")):
            self.bgstyle.addItem(label, key)
        bi = self.bgstyle.findData(cfg.get("vis_bg_style", "dark"))
        self.bgstyle.setCurrentIndex(bi if bi >= 0 else 0)
        self.bgstyle.setToolTip("How much of the beatmap background shows behind the playfield.")
        vf.addRow("background", self.bgstyle)
        self.cursorsize = self._range_slider(cfg.get("vis_cursor_size", 100), 50, 200)
        self.traillen = self._range_slider(cfg.get("vis_trail_length", 100), 25, 200)
        vf.addRow("cursor size", self.cursorsize["w"])
        vf.addRow("cursor trail", self.traillen["w"])
        vl.addLayout(vf)

        def _chk(text, key, default, tip=""):
            c = QCheckBox(text); c.setChecked(cfg.get(key, default))
            if tip:
                c.setToolTip(tip)
            vl.addWidget(c)
            return c
        self.bloom        = _chk("bloom / glow effect", "vis_bloom", False)
        self.hitlighting  = _chk("hit lighting (flash on each hit)", "vis_hit_lighting", False)
        self.aimerror     = _chk("aim-error scatter meter", "vis_aim_error", False,
                                 "danser's aim-error plot, anchored top-left of each panel.")
        self.ppcomponents = _chk("pp breakdown (aim / speed / acc)", "vis_pp_components", False)
        self.prominentur  = _chk("prominent unstable rate", "vis_prominent_ur", False,
                                 "Enlarge the UR readout and show 2 decimals.")
        self.showmods     = _chk("show each side's mods badge", "vis_show_mods", False)
        self.ignsamplevol = _chk("ignore hitsound volume changes", "vis_ignore_sample_volume", False,
                                 "Keep hitsounds at a constant level instead of following the "
                                 "map's per-section volume.")
        self.nostoryboard = _chk("disable storyboards", "vis_no_storyboards", False)
        vl.addWidget(self._hint("HUD elements (uncheck to hide). Score + accuracy always show."))
        self.hud_pp        = _chk("pp counter", "vis_show_pp", True)
        self.hud_hitcounts = _chk("300 / 100 / 50 / miss counts", "vis_show_hitcounts", True)
        self.hud_hiterror  = _chk("hit-error bar", "vis_show_hiterror", True)
        self.hud_keys      = _chk("key overlay", "vis_show_keys", True)
        self.hud_combo     = _chk("combo counter", "vis_show_combo", True)
        vl.addStretch(1)

        outer.addWidget(tabs, 1)

        # --- footer band ---
        footer = QWidget()
        footer.setObjectName("dlgFooter")
        footer.setStyleSheet(f"#dlgFooter{{background:{INK2};border-top:1px solid {LINE};}}")
        fl = QHBoxLayout(footer); fl.setContentsMargins(24, 12, 24, 12)
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        ok = QPushButton("Save"); ok.setProperty("role", "primary"); ok.clicked.connect(self.accept)
        fl.addStretch(1); fl.addWidget(cancel); fl.addWidget(ok)
        outer.addWidget(footer)

        self.resize(640, 700)

    def _form(self) -> QFormLayout:
        f = QFormLayout()
        f.setHorizontalSpacing(16); f.setVerticalSpacing(9)
        f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return f

    def _section(self, text: str) -> QLabel:
        lbl = QLabel(text); lbl.setProperty("role", "section")
        return lbl

    def _tab(self, tabs: QTabWidget, title: str) -> QVBoxLayout:
        """Create a scrollable page in the tab widget and return its layout."""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        tabs.addTab(scroll, title)
        return lay

    def _hint(self, text: str) -> QLabel:
        lbl = QLabel(text); lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color:{MUTED};background:transparent;font-size:11px;")
        return lbl

    def _file_row(self, form, label, value, pick_file):
        edit = QLineEdit(value)
        btn = QPushButton("…"); btn.setFixedWidth(36)

        def pick():
            if pick_file:
                f, _ = QFileDialog.getOpenFileName(self, label)
            else:
                f = QFileDialog.getExistingDirectory(self, label)
            if f:
                edit.setText(f)
        btn.clicked.connect(pick)
        row = QHBoxLayout(); row.addWidget(edit); row.addWidget(btn)
        w = QWidget(); w.setLayout(row)
        form.addRow(label, w)
        return edit

    def _slider(self, value):
        s = QSlider(Qt.Horizontal); s.setRange(0, 100); s.setValue(int(value))
        lbl = QLabel(f"{int(value)}%"); lbl.setFixedWidth(42)
        s.valueChanged.connect(lambda v: lbl.setText(f"{v}%"))
        row = QHBoxLayout(); row.addWidget(s); row.addWidget(lbl)
        w = QWidget(); w.setLayout(row)
        return {"s": s, "w": w}

    def _range_slider(self, value, lo, hi):
        s = QSlider(Qt.Horizontal); s.setRange(lo, hi); s.setValue(int(value))
        lbl = QLabel(f"{int(value)}%"); lbl.setFixedWidth(46)
        s.valueChanged.connect(lambda v: lbl.setText(f"{v}%"))
        row = QHBoxLayout(); row.addWidget(s); row.addWidget(lbl)
        w = QWidget(); w.setLayout(row)
        return {"s": s, "w": w}

    def result_config(self) -> dict:
        return {
            "danser_bin": self.danser_bin.text().strip(),
            "ffmpeg_bin": self.ffmpeg_bin.text().strip(),
            "songs_dir": self.songs.text().strip(),
            "skins_dir": self.skins.text().strip(),
            "output_dir": self.output.text().strip() or str(Path.home()),
            "api_client_id": self.cid.text().strip(),
            "api_client_secret": self.csecret.text().strip(),
            "tail_seconds": self.tail.value(),
            "endcard_seconds": self.hold.value(),
            "endcard_speed": self.espeed.value(),
            "encoder": self.encoder.currentData(),
            "quality": self.quality.currentData(),
            "no_fail": self.nofail.isChecked(),
            "force_skin_hits": self.skinhits.isChecked(),
            "left_music_volume": self.vol_l_music["s"].value(),
            "left_hitsound_volume": self.vol_l_hit["s"].value(),
            "right_music_volume": self.vol_r_music["s"].value(),
            "right_hitsound_volume": self.vol_r_hit["s"].value(),
            "master_volume": self.vol_master["s"].value(),
            "vis_bg_style": self.bgstyle.currentData(),
            "vis_cursor_size": self.cursorsize["s"].value(),
            "vis_trail_length": self.traillen["s"].value(),
            "vis_bloom": self.bloom.isChecked(),
            "vis_hit_lighting": self.hitlighting.isChecked(),
            "vis_aim_error": self.aimerror.isChecked(),
            "vis_pp_components": self.ppcomponents.isChecked(),
            "vis_prominent_ur": self.prominentur.isChecked(),
            "vis_show_mods": self.showmods.isChecked(),
            "vis_ignore_sample_volume": self.ignsamplevol.isChecked(),
            "vis_no_storyboards": self.nostoryboard.isChecked(),
            "vis_show_pp": self.hud_pp.isChecked(),
            "vis_show_hitcounts": self.hud_hitcounts.isChecked(),
            "vis_show_hiterror": self.hud_hiterror.isChecked(),
            "vis_show_keys": self.hud_keys.isChecked(),
            "vis_show_combo": self.hud_combo.isChecked(),
        }


# --------------------------------------------------------------------------- #
# Progress parsing — map pipeline stdout to a 0-100 bar + status text
# --------------------------------------------------------------------------- #
class ProgressTracker:
    """danser reports per-render %, plus our pipeline prints stage markers."""
    def __init__(self):
        self.danser_run = 0  # 0 before first, 1 = left, 2 = right
        self.stage = "starting"

    def update(self, line: str):
        """Return (percent|None, status|None)."""
        if "Assembling match data" in line:
            return 2, "Reading replays + match data"
        if "Checking beatmap" in line:
            return 3, "Checking beatmap"
        if "downloading set" in line:
            return 3, "Downloading beatmap"
        if "Rendering overlay chrome" in line:
            return 5, "Rendering overlay"
        if "danser-go version" in line:
            self.danser_run += 1
            who = "left" if self.danser_run == 1 else "right"
            return (6 if self.danser_run == 1 else 36), f"Rendering {who} gameplay (danser)"
        # danser's pre-render database phase (slow on first run with a big library)
        if "Scanning" in line and ".osu" in line:
            return None, "danser: scanning your beatmap library…"
        if "new directories will be imported" in line:
            return None, "danser: importing beatmaps (first run — slow for big libraries, one-time)…"
        if re.search(r"Import(ing|ed)\b", line):
            return None, "danser: importing beatmaps…"
        if "Loaded" in line and "total" in line:
            return None, "danser: library ready — starting render…"
        m = re.search(r"Progress:\s*(\d+)%", line)
        if m and self.danser_run in (1, 2):
            p = int(m.group(1))
            base = 6 if self.danser_run == 1 else 36
            return base + int(p * 0.30), None  # each danser run spans 30%
        if "compositing gameplay" in line:
            return 72, "Compositing gameplay + overlay"
        if "rendering end card" in line:
            return 82, "Rendering results end card"
        if "joining gameplay" in line:
            return 94, "Joining gameplay + end card"
        if "Done ->" in line:
            return 100, "Done"
        return None, None


class LogRouter:
    """Classify a raw pipeline/danser output line into a tidy op for the clean log.

    Returns a list of (kind, value) ops; kind is one of:
      "phase" (value=name)   — a new stage begins
      "prog"  (value=int %)  — render progress for the current stage
      "ok" / "info" / "warn" / "err" (value=text) — standalone lines
    An empty list means: drop from the clean view (still kept for verbose).
    """
    # danser's chatty internals — never shown in the clean view
    _NOISE = (
        "SettingsManager:", "ApiConnector:", "Current config:", "DatabaseManager:",
        "Initializing", "Initialized", "GL Vendor", "GL Renderer", "GL Version",
        "GLSL Version", "GL Extensions", "BASS", "Quicksand", "SkinManager:",
        "Creating window", "Window created", "OpenGL", "GLFW", "loaded!",
    )

    def feed(self, line: str):
        s = line.strip()
        if not s:
            return []

        # --- danser render progress (with -preciseprogress) -> live % ---
        m = re.search(r"Progress:\s*(\d+)\s*%", s)
        if m:
            return [("prog", int(m.group(1)))]

        # --- pipeline phase markers ---
        if s.startswith("Assembling match data"):
            return [("phase", "Reading replays & match data")]
        if s.startswith("Rendering overlay chrome"):
            return [("phase", "Building overlay")]
        if s.startswith("Staging beatmap into"):
            return [("phase", "Staging beatmap")]
        if "→ rendering" in s and "ov_left" in s:
            return [("phase", "Rendering P1 gameplay")]
        if "→ rendering" in s and "ov_right" in s:
            return [("phase", "Rendering P2 gameplay")]
        if "compositing gameplay" in s:
            return [("phase", "Compositing gameplay + overlay")]
        if s.startswith("rendering end card") or "rendering end card" in s:
            return [("phase", "Rendering results card")]
        if "joining gameplay" in s:
            return [("phase", "Joining gameplay + results")]

        # --- standalone info / status lines worth keeping ---
        if s.startswith("OK overlay_base"):
            return [("ok", "overlay ready")]
        mb = re.match(r"beatmap:\s*(.+)", s)
        if mb:
            return [("ok", f"beatmap {mb.group(1)}")]
        if s.startswith("downloading set"):
            return [("info", s)]
        if "danser db refreshed" in s:
            return [("info", "danser database refreshed")]
        if s.startswith("audio:"):
            return [("info", s)]
        if "no-fail:" in s:
            return [("info", s)]
        if "·" in s and (" vs " in s):           # the "P1 vs P2 · Artist - Title ★SR" line
            return [("info", s)]

        # --- warnings / errors ---
        if "No osu! API key" in s or "map metadata unavailable" in s:
            return [("warn", "no osu! API key — rendering without avatars/ranks/pp")]
        if "isn't usable on this system" in s or "Falling back to x264" in s:
            return [("warn", "selected GPU encoder unavailable (driver too old) — "
                             "falling back to x264")]
        if "ffmpeg composite failed" in s or "Conversion failed" in s.strip():
            return [("err", "video compositing failed — see verbose log for ffmpeg output")]
        if s.startswith("panic:"):
            reason = s[len("panic:"):].strip()
            # keep it short: drop the long path, keep the human part
            reason = re.sub(r"open .*/([^/]+):\s*", r"", reason) or reason
            return [("err", f"danser crashed: {reason[:160]}")]
        if "danser exited with code" in s:
            return [("err", s)]
        if re.match(r"(error|fatal|traceback|exception)\b", s, re.I):
            return [("err", s[:160])]

        # everything else (danser internals, the -sPatch command dump, GL spam) -> drop
        return []


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CircleClash — osu! 1v1 comparison")
        self.cfg = load_config()
        self.proc: QProcess | None = None
        self.tracker = ProgressTracker()
        self._raw = []; self._clean = []
        self._cur_phase = None; self._cur_phase_t = None; self._pending = []; self._live = False
        self._build()
        self._apply_theme()
        self.populate_skins()
        self._setup_thread = None
        if not self.cfg.get("welcomed"):
            WelcomeDialog(self.cfg, self).exec()
            self.cfg["welcomed"] = True
            save_config(self.cfg)
        self._resolve_ffmpeg()
        self._resolve_danser()
        if not self.cfg["danser_bin"] or not self._have_ffmpeg():
            self._first_run_danser()

    def _resolve_ffmpeg(self):
        """Pick up an ffmpeg we already have (a managed copy, or one on PATH) into
        config, without downloading. Provisioning happens in the first-run flow."""
        cur = self.cfg.get("ffmpeg_bin", "")
        if cur and Path(cur).exists():
            return
        if ffmpeg_setup is not None:
            got = ffmpeg_setup.find_local_ffmpeg()
            if got:
                self.cfg["ffmpeg_bin"] = str(got[0])
                save_config(self.cfg)
                return
        onpath = shutil.which("ffmpeg")
        if onpath:
            self.cfg["ffmpeg_bin"] = onpath
            save_config(self.cfg)

    def _resolve_danser(self):
        """Pick up a danser we already have (managed/portable copy) into config so the
        Settings field shows it, without downloading. Mirrors _resolve_ffmpeg."""
        cur = self.cfg.get("danser_bin", "")
        if cur and Path(cur).exists():
            return
        if danser_setup is not None:
            local = danser_setup.find_local_danser()
            if local:
                self.cfg["danser_bin"] = str(local)
                save_config(self.cfg)

    def _have_danser(self) -> bool:
        cur = self.cfg.get("danser_bin", "")
        if cur and Path(cur).exists():
            return True
        return bool(danser_setup and danser_setup.find_local_danser())

    def _have_ffmpeg(self) -> bool:
        cur = self.cfg.get("ffmpeg_bin", "")
        if cur and (Path(cur).exists() or shutil.which(cur)):
            return True
        return shutil.which("ffmpeg") is not None

    def _first_run_danser(self):
        """If danser or ffmpeg isn't configured, try a previous auto-install, else
        offer to download them now (falling back to manual setup)."""
        if danser_setup is not None:
            local = danser_setup.find_local_danser()
            if local:
                self.cfg["danser_bin"] = str(local)
                save_config(self.cfg)
                self._refresh_enabled()
                if self._have_ffmpeg():
                    return
                # danser's fine, only ffmpeg is missing — offer just that
                if ffmpeg_setup is not None and QMessageBox.question(
                        self, "Set up ffmpeg",
                        "CircleClash uses ffmpeg to stitch the video, but it isn't installed "
                        "yet.\n\nDownload it automatically now? (kept in this app's data folder).\n\n"
                        "Choose No to install ffmpeg yourself and add it to your PATH.",
                        QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
                    self.download_danser()
                return
            need = "danser-go" + ("" if self._have_ffmpeg() else " and ffmpeg")
            choice = QMessageBox.question(
                self, "Set up CircleClash",
                f"CircleClash needs {need} to render, but they aren't installed yet.\n\n"
                "Download and set everything up automatically now? (kept in this app's data "
                "folder — danser is GPL-3.0 and fetched from its official GitHub release; "
                "ffmpeg is a static build.)\n\n"
                "Choose No to point at an existing danser yourself.",
                QMessageBox.Yes | QMessageBox.No)
            if choice == QMessageBox.Yes:
                self.download_danser()
                return
        # fallback: manual
        QMessageBox.information(self, "First-time setup",
                                "Point CircleClash at your danser binary and your osu! Songs "
                                "folder to get started.")
        self.open_settings()

    def download_danser(self):
        if danser_setup is None:
            QMessageBox.warning(self, "Unavailable", "danser_setup.py is missing from the app folder.")
            return
        self.status.setText("Setting up danser…")
        self.progress.setValue(0)
        self.renderBtn.setEnabled(False)
        self.settingsBtn.setEnabled(False)

        self._setup_thread = QThread(self)
        self._setup_worker = DanserSetupWorker()
        self._setup_worker.moveToThread(self._setup_thread)
        self._setup_thread.started.connect(self._setup_worker.run)
        self._setup_worker.progress.connect(self._on_danser_progress)
        self._setup_worker.done.connect(self._on_danser_done)
        self._setup_worker.failed.connect(self._on_danser_failed)
        self._setup_thread.start()

    def _on_danser_progress(self, frac, msg):
        self.progress.setValue(int(frac * 100))
        self.status.setText(msg)

    def _on_danser_done(self, path, ffmpeg_path):
        self._setup_thread.quit(); self._setup_thread.wait()
        self.cfg["danser_bin"] = path
        if ffmpeg_path:
            self.cfg["ffmpeg_bin"] = ffmpeg_path
        save_config(self.cfg)
        ready = "danser + ffmpeg ready" if ffmpeg_path else "danser ready"
        self.status.setText(f"{ready} — finish setup in Settings (Songs folder, output)")
        self.settingsBtn.setEnabled(True)
        self._refresh_enabled()
        self.open_settings()

    def _on_danser_failed(self, msg):
        self._setup_thread.quit(); self._setup_thread.wait()
        self.settingsBtn.setEnabled(True)
        self.status.setText("danser setup failed")
        QMessageBox.warning(self, "danser setup failed",
                            f"{msg}\n\nYou can point at an existing danser in Settings instead.")
        self.open_settings()

    # ---- UI ----
    def _flabel(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setProperty("role", "field")
        return lab

    def _build(self):
        central = QWidget(); self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setContentsMargins(24, 20, 24, 20); root.setSpacing(16)

        # header — gradient wordmark + tagline
        root.addWidget(GradientTitle("CircleClash"))
        sub = QLabel("drop two replays · get a side-by-side comparison video")
        sub.setStyleSheet(f"color:{MUTED};font-size:13px;")
        root.addWidget(sub)

        # versus row: P1  ·  VS  ·  P2
        zones = QHBoxLayout(); zones.setSpacing(0)
        self.leftZone = DropZone("PLAYER 1", PINK, (".osr",), side="left")
        self.rightZone = DropZone("PLAYER 2", ICE, (".osr",), side="right")
        self.leftZone.fileDropped.connect(self._refresh_enabled)
        self.rightZone.fileDropped.connect(self._refresh_enabled)
        zones.addWidget(self.leftZone, 1)
        zones.addWidget(VsNode(), 0, Qt.AlignVCenter)
        zones.addWidget(self.rightZone, 1)
        root.addLayout(zones)

        # ---- control panel ----
        panel = QFrame(); panel.setProperty("role", "panel")
        proot = QVBoxLayout(panel); proot.setContentsMargins(18, 18, 18, 18); proot.setSpacing(15)

        meta = QGridLayout(); meta.setHorizontalSpacing(12); meta.setVerticalSpacing(12)
        meta.addWidget(self._flabel("Match title"), 0, 0)
        self.titleEdit = QLineEdit("friendly · bo1")
        meta.addWidget(self.titleEdit, 0, 1, 1, 3)

        meta.addWidget(self._flabel("P1 skin"), 1, 0)
        self.skinLeft = QComboBox(); self.skinLeft.setEditable(False)
        bL = QPushButton("Import .osk"); bL.clicked.connect(lambda: self.import_osk("left"))
        meta.addWidget(self.skinLeft, 1, 1)
        meta.addWidget(bL, 1, 2, 1, 2)

        meta.addWidget(self._flabel("P2 skin"), 2, 0)
        self.skinRight = QComboBox(); self.skinRight.setEditable(False)
        bR = QPushButton("Import .osk"); bR.clicked.connect(lambda: self.import_osk("right"))
        meta.addWidget(self.skinRight, 2, 1)
        meta.addWidget(bR, 2, 2, 1, 2)

        meta.addWidget(self._flabel("Resolution"), 3, 0)
        self.resCombo = QComboBox(); self.resCombo.addItems(["720p", "1080p", "1440p", "4k"])
        self.resCombo.setCurrentText(self.cfg.get("resolution", "1080p"))
        meta.addWidget(self.resCombo, 3, 1)
        meta.addWidget(self._flabel("FPS"), 3, 2)
        self.fpsCombo = QComboBox(); self.fpsCombo.addItems(["30", "60", "120", "240"])
        self.fpsCombo.setCurrentText(str(self.cfg.get("fps", 60)))
        meta.addWidget(self.fpsCombo, 3, 3)
        meta.setColumnStretch(1, 1)
        proot.addLayout(meta)

        # buttons row
        btns = QHBoxLayout()
        self.settingsBtn = QPushButton("⚙  Settings"); self.settingsBtn.clicked.connect(self.open_settings)
        self.renderBtn = QPushButton("▶  Render"); self.renderBtn.clicked.connect(self.start_render)
        self.renderBtn.setProperty("role", "primary")
        self.cancelBtn = QPushButton("Cancel"); self.cancelBtn.clicked.connect(self.cancel_render); self.cancelBtn.setEnabled(False)
        self.openBtn = QPushButton("Open output folder"); self.openBtn.clicked.connect(self.open_output)
        btns.addWidget(self.settingsBtn); btns.addStretch(1)
        btns.addWidget(self.openBtn); btns.addWidget(self.cancelBtn); btns.addWidget(self.renderBtn)
        proot.addLayout(btns)

        # progress + status
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0)
        self.progress.setTextVisible(False); self.progress.setFixedHeight(10)
        self.status = QLabel("Ready"); self.status.setProperty("role", "status")
        proot.addWidget(self.progress); proot.addWidget(self.status)
        root.addWidget(panel)

        # log (collapsible) + verbose toggle
        logrow = QHBoxLayout(); logrow.setContentsMargins(0, 0, 0, 0)
        self.logToggle = QPushButton("Show log ▸"); self.logToggle.setCheckable(True)
        self.logToggle.clicked.connect(self._toggle_log)
        self.logToggle.setProperty("role", "link")
        self.verboseChk = QCheckBox("verbose")
        self.verboseChk.setToolTip("Show the full raw danser/ffmpeg output instead of the tidy summary.")
        self.verboseChk.toggled.connect(self._rebuild_log)
        self.verboseChk.setVisible(False)
        logrow.addWidget(self.logToggle); logrow.addStretch(1); logrow.addWidget(self.verboseChk)
        root.addLayout(logrow)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setVisible(False)
        self.log.setMaximumBlockCount(8000)
        self.log.setObjectName("logview")
        self.log.setMinimumHeight(160)
        root.addWidget(self.log, 1)            # expands to fill when visible
        root.addStretch(1)                     # absorbs slack when the log is hidden
        self._root = root
        self._tail_idx = root.count() - 1      # index of the trailing stretch

        self.resize(760, 640)
        self._refresh_enabled()

    def _apply_theme(self):
        self.setStyleSheet(_theme_qss())

    # ---- actions ----
    def _toggle_log(self):
        on = self.logToggle.isChecked()
        self.log.setVisible(on)
        self.verboseChk.setVisible(on)
        self.logToggle.setText("Hide log ▾" if on else "Show log ▸")
        # When the log is open the log itself takes the slack; when closed the
        # trailing stretch does, so the drop zones / panel never get stretched.
        self._root.setStretch(self._tail_idx, 0 if on else 1)
        # grow to give the log room, and restore the exact previous height on close
        if on:
            self._pre_log_h = self.height()
            self.resize(self.width(), self.height() + 260)
        else:
            self.resize(self.width(), getattr(self, "_pre_log_h", self.height()))

    def open_settings(self):
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec() == QDialog.Accepted:
            self.cfg.update(dlg.result_config())
            save_config(self.cfg)
            self.populate_skins()
            self._refresh_enabled()

    def _skins_root(self) -> Path | None:
        d = self.cfg.get("skins_dir")
        return Path(d) if d and Path(d).is_dir() else None

    def populate_skins(self):
        """Fill both skin dropdowns from the configured skins folder."""
        root = self._skins_root()
        names = []
        if root:
            names = sorted([p.name for p in root.iterdir() if p.is_dir()], key=str.lower)
        for combo in (self.skinLeft, self.skinRight):
            cur = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(default skin)")
            combo.addItems(names)
            if cur and cur in names:
                combo.setCurrentText(cur)
            combo.blockSignals(False)

    def import_osk(self, side: str):
        """Extract a .osk into the skins folder, then select it for this side."""
        root = self._skins_root()
        if not root:
            QMessageBox.information(self, "Set a skins folder",
                                   "Pick your osu! Skins folder in Settings first, then imported "
                                   "skins land there and show up in these menus.")
            return
        f, _ = QFileDialog.getOpenFileName(self, "Import .osk", "", "osu! skin (*.osk)")
        if not f:
            return
        try:
            name = _sanitize(Path(f).stem)
            dest = root / name
            dest.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(f) as z:
                z.extractall(dest)
            self.populate_skins()
            (self.skinLeft if side == "left" else self.skinRight).setCurrentText(name)
        except Exception as e:
            QMessageBox.warning(self, "Skin error", f"Could not import skin:\n{e}")

    def _skin_for(self, combo) -> str | None:
        t = combo.currentText()
        return None if (not t or t.startswith("(default")) else t

    def _refresh_enabled(self, *_):
        ready = bool(self.leftZone.path and self.rightZone.path and self._have_danser())
        self.renderBtn.setEnabled(ready and self.proc is None)

    def _out_path(self) -> Path:
        lname = rname = None
        if parse_replay:
            try: lname = parse_replay(self.leftZone.path).player
            except Exception: pass
            try: rname = parse_replay(self.rightZone.path).player
            except Exception: pass
        lname = lname or Path(self.leftZone.path).stem
        rname = rname or Path(self.rightZone.path).stem
        title = _sanitize(self.titleEdit.text())
        fname = _sanitize(f"{lname} vs {rname} - {title}") + ".mp4"
        return Path(self.cfg["output_dir"]) / fname

    def start_render(self):
        if self.proc is not None:
            return
        # capture current main-window selections into config
        self.cfg["resolution"] = self.resCombo.currentText()
        self.cfg["fps"] = int(self.fpsCombo.currentText())
        save_config(self.cfg)

        out = self._out_path()
        # Make sure we have a concrete danser path: prefer the configured one, else the
        # managed/portable copy. Write it back so Settings shows it from now on.
        danser_bin = self.cfg.get("danser_bin", "")
        if (not danser_bin or not Path(danser_bin).exists()) and danser_setup:
            loc = danser_setup.find_local_danser()
            if loc:
                danser_bin = str(loc)
                self.cfg["danser_bin"] = danser_bin
                save_config(self.cfg)
        # danser reads a small managed folder (instant import); the user's library
        # is just a source for maps they already own.
        render_songs = str(danser_setup.render_songs_dir()) if danser_setup else self.cfg.get("songs_dir", "")
        pargs = [
            self.leftZone.path, self.rightZone.path,
            "--title", self.titleEdit.text() or "friendly · bo1",
            "--danser-bin", danser_bin,
            "--out", str(out),
            "--tail-seconds", str(self.cfg["tail_seconds"]),
            "--endcard-seconds", str(self.cfg["endcard_seconds"]),
            "--endcard-speed", str(self.cfg.get("endcard_speed", 1.0)),
            "--resolution", self.cfg["resolution"],
            "--fps", str(self.cfg["fps"]),
            "--left-music-volume", str(self.cfg.get("left_music_volume", self.cfg.get("music_volume", 100)) / 100),
            "--right-music-volume", str(self.cfg.get("right_music_volume", 0) / 100),
            "--left-hitsound-volume", str(self.cfg.get("left_hitsound_volume", self.cfg.get("hitsound_volume", 100)) / 100),
            "--right-hitsound-volume", str(self.cfg.get("right_hitsound_volume", self.cfg.get("hitsound_volume", 100)) / 100),
            "--master-volume", str(self.cfg.get("master_volume", 100) / 100),
        ]
        if render_songs:
            pargs += ["--songs-dir", render_songs]
        if self.cfg.get("songs_dir"):
            pargs += ["--library-dir", self.cfg["songs_dir"]]
        if self.cfg.get("skins_dir"):
            pargs += ["--skins-dir", self.cfg["skins_dir"]]
        sl = self._skin_for(self.skinLeft)
        sr = self._skin_for(self.skinRight)
        if sl:
            pargs += ["--left-skin", sl]
        if sr:
            pargs += ["--right-skin", sr]
        enc = self.cfg.get("encoder") or ("nvenc_h264" if self.cfg.get("nvenc") else "x264")
        pargs += ["--encoder", enc, "--quality", self.cfg.get("quality", "high")]
        if not self.cfg.get("no_fail", True):
            pargs += ["--keep-fails"]
        if not self.cfg.get("force_skin_hits", True):
            pargs += ["--beatmap-hitsounds"]
        ff = self.cfg.get("ffmpeg_bin", "")
        if ff and (Path(ff).exists() or shutil.which(ff)):
            pargs += ["--ffmpeg", ff]

        # --- visual tweaks ---
        c = self.cfg
        bg_dim, bg_blur = {
            "dark": (0.95, 0.0), "dimmed": (0.7, 0.0),
            "visible": (0.3, 0.0), "blurred": (0.7, 0.6),
        }.get(c.get("vis_bg_style", "dark"), (0.95, 0.0))
        pargs += ["--bg-dim", str(bg_dim), "--bg-blur", str(bg_blur),
                  "--cursor-size", str(round(12 * c.get("vis_cursor_size", 100) / 100, 2)),
                  "--trail-length", str(round(c.get("vis_trail_length", 100) / 100, 3))]
        for flag, key in (("--no-storyboards", "vis_no_storyboards"),
                          ("--bloom", "vis_bloom"), ("--hit-lighting", "vis_hit_lighting"),
                          ("--aim-error", "vis_aim_error"), ("--pp-components", "vis_pp_components"),
                          ("--prominent-ur", "vis_prominent_ur"), ("--show-mods", "vis_show_mods"),
                          ("--ignore-sample-volume", "vis_ignore_sample_volume")):
            if c.get(key):
                pargs += [flag]
        for flag, key in (("--hide-pp", "vis_show_pp"), ("--hide-hitcounts", "vis_show_hitcounts"),
                          ("--hide-hiterror", "vis_show_hiterror"), ("--hide-keys", "vis_show_keys"),
                          ("--hide-combo", "vis_show_combo")):
            if not c.get(key, True):
                pargs += [flag]

        # Frozen (.exe): relaunch ourselves in pipeline mode. From source: run the
        # make_overlay_video.py script with the current interpreter.
        if getattr(sys, "frozen", False):
            launch = ["--run-pipeline", *pargs]
            workdir = tempfile.gettempdir()
        else:
            launch = ["-u", str(PIPELINE), *pargs]
            workdir = str(HERE)

        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        # Force UTF-8 stdio in the worker so the arrows/bullets/✓ it prints don't crash
        # on Windows (whose pipes/console default to cp1252).
        env.insert("PYTHONUTF8", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        if self.cfg.get("api_client_id"):
            env.insert("OSU_CLIENT_ID", self.cfg["api_client_id"])
        if self.cfg.get("api_client_secret"):
            env.insert("OSU_CLIENT_SECRET", self.cfg["api_client_secret"])

        self.tracker = ProgressTracker()
        self._router = LogRouter()
        self._raw = []                 # every raw line (verbose view)
        self._clean = []               # tidy html lines (clean view)
        self._cur_phase = None
        self._cur_phase_t = None
        self._pending = []
        self._live = False             # last clean line is an updatable progress line?
        self._render_t0 = time.monotonic()
        self.progress.setValue(0)
        self.status.setText("Starting render…")
        self.log.clear()
        self._out_target = out

        self.proc = QProcess(self)
        self.proc.setProcessEnvironment(env)
        self.proc.setWorkingDirectory(workdir)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._on_output)
        self.proc.finished.connect(self._on_finished)
        self.proc.start(sys.executable, launch)

        self.renderBtn.setEnabled(False)
        self.cancelBtn.setEnabled(True)
        self.settingsBtn.setEnabled(False)

    # ---- tidy log rendering ----
    @staticmethod
    def _line(text, color, bold=False, indent=0):
        weight = "font-weight:600;" if bold else ""
        pad = "&nbsp;" * indent
        return f'<span style="color:{color};{weight}">{pad}{ihtml.escape(text)}</span>'

    def _showing_clean(self) -> bool:
        return not self.verboseChk.isChecked()

    def _widget_replace_last(self, html):
        c = self.log.textCursor()
        c.movePosition(QTextCursor.End)
        c.movePosition(QTextCursor.StartOfBlock, QTextCursor.KeepAnchor)
        c.removeSelectedText()
        c.insertHtml(html)
        self.log.setTextCursor(c)
        self.log.ensureCursorVisible()

    def _clean_add(self, html):
        self._clean.append(html); self._live = False
        if self._showing_clean():
            self.log.appendHtml(html); self.log.ensureCursorVisible()

    def _phase_live(self, html):
        if self._live:
            self._clean[-1] = html
            if self._showing_clean():
                self._widget_replace_last(html)
        else:
            self._clean.append(html); self._live = True
            if self._showing_clean():
                self.log.appendHtml(html); self.log.ensureCursorVisible()

    def _finalize_phase(self):
        if self._cur_phase is None:
            return
        dur = time.monotonic() - (self._cur_phase_t or time.monotonic())
        done = self._line(f"✓ {self._cur_phase}  ({dur:.0f}s)", GREEN)
        if self._live:
            self._clean[-1] = done; self._live = False
            if self._showing_clean():
                self._widget_replace_last(done)
        else:
            self._clean_add(done)
        for sub in self._pending:           # sub-steps, grouped under the finished phase
            self._clean_add(sub)
        self._pending = []
        self._cur_phase = None; self._cur_phase_t = None

    def _apply_op(self, kind, val):
        if kind == "phase":
            self._finalize_phase()
            self._cur_phase = val; self._cur_phase_t = time.monotonic(); self._pending = []
            self._phase_live(self._line(f"⏳ {val}…", ICE, bold=True))
        elif kind == "prog":
            if self._cur_phase is not None:
                self._phase_live(self._line(f"⏳ {self._cur_phase}…  {val}%", ICE, bold=True))
        elif kind in ("ok", "info", "warn"):
            sub = {"ok": self._line(f"✓ {val}", GREEN, indent=3),
                   "info": self._line(val, MUTED, indent=3),
                   "warn": self._line(f"⚠ {val}", GOLD, indent=3)}[kind]
            if self._cur_phase is not None:
                self._pending.append(sub)   # deferred until the phase line finalizes
            else:
                self._clean_add(sub)
        elif kind == "err":
            # the phase failed; drop its pending sub-steps and show the error plainly
            self._pending = []; self._cur_phase = None; self._cur_phase_t = None; self._live = False
            self._clean_add(self._line(f"✗ {val}", RED, bold=True))

    def _rebuild_log(self):
        """Switch the visible log between the tidy view and full raw output."""
        self.log.clear()
        if self.verboseChk.isChecked():
            if self._raw:
                self.log.appendPlainText("\n".join(self._raw))
        else:
            for h in self._clean:
                self.log.appendHtml(h)
        self.log.ensureCursorVisible()

    def _on_output(self):
        data = bytes(self.proc.readAllStandardOutput()).decode(errors="replace")
        for line in data.splitlines():
            self._raw.append(line)
            if self.verboseChk.isChecked():
                self.log.appendPlainText(line)
            pct, status = self.tracker.update(line)   # drives the bar + status label
            if pct is not None:
                self.progress.setValue(pct)
            if status:
                self.status.setText(status)
            for kind, val in self._router.feed(line):  # drives the tidy log
                self._apply_op(kind, val)

    def _append_summary(self, total):
        try:
            size_s = f"{self._out_target.stat().st_size / 1_048_576:.1f} MB"
        except Exception:
            size_s = "—"
        res = self.cfg.get("resolution", "1080p")
        enc = self.cfg.get("encoder", "x264"); q = self.cfg.get("quality", "high")
        mm, ss = divmod(int(total), 60)
        t_s = f"{mm}m {ss:02d}s" if mm else f"{ss}s"
        self._clean_add(self._line("─" * 28, LINE))
        self._clean_add(self._line(f"✓ Done  ·  {self._out_target.name}  ·  {size_s}", GREEN, bold=True))
        self._clean_add(self._line(f"{res} · {enc}/{q} · {t_s} total", MUTED, indent=3))
        self._clean_add(self._line(str(self._out_target), MUTED, indent=3))

    def _on_finished(self, code, _status):
        ok = code == 0
        self.proc = None
        self.cancelBtn.setEnabled(False)
        self.settingsBtn.setEnabled(True)
        self._refresh_enabled()
        self._finalize_phase()
        total = time.monotonic() - getattr(self, "_render_t0", time.monotonic())
        if ok:
            self.progress.setValue(100)
            self.status.setText(f"Done → {self._out_target.name}")
            self._append_summary(total)
        else:
            self.status.setText(f"Render failed (exit {code}) — see log")
            self._clean_add(self._line(f"✗ Render failed (exit {code}) — toggle 'verbose' for full output",
                                       RED, bold=True))
            if not self.logToggle.isChecked():
                self.logToggle.setChecked(True); self._toggle_log()

    def cancel_render(self):
        if self.proc is not None:
            self.proc.kill()
            self.status.setText("Cancelled")

    def open_output(self):
        out_dir = self.cfg.get("output_dir") or str(Path.home())
        if sys.platform == "win32":
            os.startfile(out_dir)  # noqa
        elif sys.platform == "darwin":
            QProcess.startDetached("open", [out_dir])
        else:
            QProcess.startDetached("xdg-open", [out_dir])


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AmarilloNL.CircleClash")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    icon = app_icon()
    app.setWindowIcon(icon)
    w = MainWindow()
    w.setWindowIcon(icon)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    # One executable, two roles. When the GUI (frozen as an .exe) needs to render,
    # it relaunches itself with --run-pipeline as the first argument; we detect that
    # here and hand off to the render pipeline instead of starting the GUI. This is
    # what lets a single PyInstaller build act as both the app and its worker.
    if len(sys.argv) > 1 and sys.argv[1] == "--run-pipeline":
        # In a --windowed frozen build PyInstaller can set stdio to None; reattach to
        # the pipe QProcess handed us. Force UTF-8 in every case — Windows pipes/consoles
        # default to cp1252, which can't encode the arrows/bullets/✓ the pipeline prints
        # (otherwise the render dies with a UnicodeEncodeError on the first '→').
        for _fd, _name in ((1, "stdout"), (2, "stderr")):
            _stream = getattr(sys, _name)
            if _stream is None:
                try:
                    setattr(sys, _name, os.fdopen(_fd, "w", buffering=1,
                                                  encoding="utf-8", errors="replace"))
                except Exception:
                    setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))
            else:
                try:
                    _stream.reconfigure(encoding="utf-8", errors="replace")
                except Exception:
                    pass
        import make_overlay_video
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        make_overlay_video.main()
    else:
        main()
