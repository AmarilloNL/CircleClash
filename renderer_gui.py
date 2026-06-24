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
import tempfile
import zipfile
from pathlib import Path

from PySide6.QtCore import Qt, QProcess, QProcessEnvironment, Signal, QObject, QThread, QRectF
from PySide6.QtGui import (
    QFont, QDragEnterEvent, QDropEvent, QPainter, QColor,
    QLinearGradient, QRadialGradient, QPen, QBrush, QFontMetrics,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QHBoxLayout, QGridLayout, QFileDialog, QPlainTextEdit,
    QProgressBar, QDialog, QDoubleSpinBox, QCheckBox, QFormLayout, QFrame,
    QMessageBox, QGroupBox, QComboBox, QSlider, QScrollArea,
)

try:
    import danser_setup
except Exception:
    danser_setup = None

HERE = Path(__file__).resolve().parent
PIPELINE = HERE / "make_overlay_video.py"

PINK = "#ff66ab"
ICE = "#66d9ff"
GOLD = "#ffd24a"
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
    "danser_video_dir": "",
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
    "resolution": "1080p",
    "fps": 60,
    "left_music_volume": 100,
    "left_hitsound_volume": 100,
    "right_music_volume": 0,
    "right_hitsound_volume": 100,
    "master_volume": 100,
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
    done = Signal(str)      # path on success
    failed = Signal(str)    # message on failure

    def run(self):
        try:
            path = danser_setup.ensure(progress=lambda f, m: self.progress.emit(f, m))
            self.done.emit(str(path))
        except Exception as e:
            self.failed.emit(str(e))


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
                               "found on PATH" if ffmpeg_ok else "install it and add it to your PATH"))
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
        header.setStyleSheet(f"background:{INK2};border-bottom:1px solid {LINE};")
        hl = QVBoxLayout(header); hl.setContentsMargins(24, 18, 24, 16); hl.setSpacing(3)
        ht = QLabel("Settings")
        htf = QFont(); htf.setFamilies(["Exo 2", "Inter", "Segoe UI", "sans-serif"])
        htf.setPixelSize(20); htf.setWeight(QFont.Bold)
        ht.setFont(htf); ht.setStyleSheet(f"color:{TXT};background:transparent;")
        hs = QLabel("Paths, encoding and audio · saved only on this machine")
        hs.setStyleSheet(f"color:{MUTED};background:transparent;font-size:12px;")
        hl.addWidget(ht); hl.addWidget(hs)
        outer.addWidget(header)

        # --- scrollable body ---
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        content = QWidget()
        cl = QVBoxLayout(content); cl.setContentsMargins(24, 18, 24, 20); cl.setSpacing(8)

        # Paths
        cl.addWidget(self._section("Paths"))
        pf = self._form()
        self.danser_bin = self._file_row(pf, "danser binary", cfg["danser_bin"], pick_file=True)
        self.danser_video = self._file_row(pf, "danser video output dir", cfg["danser_video_dir"], pick_file=False)
        self.songs = self._file_row(pf, "osu! Songs folder (your library)", cfg["songs_dir"], pick_file=False)
        self.skins = self._file_row(pf, "osu! Skins folder", cfg.get("skins_dir", ""), pick_file=False)
        self.output = self._file_row(pf, "output folder", cfg["output_dir"], pick_file=False)
        cl.addLayout(pf)

        # osu! API
        cl.addSpacing(6)
        cl.addWidget(self._section("osu! API · optional"))
        af = self._form()
        self.cid = QLineEdit(cfg["api_client_id"])
        self.csecret = QLineEdit(cfg["api_client_secret"]); self.csecret.setEchoMode(QLineEdit.Password)
        af.addRow("client id", self.cid)
        af.addRow("client secret", self.csecret)
        cl.addLayout(af)
        cl.addWidget(self._hint("Optional, but enables avatars, ranks, flags and pp. Register a "
                                "personal OAuth app at osu! → Settings → OAuth."))

        # Timing
        cl.addSpacing(6)
        cl.addWidget(self._section("Timing"))
        tf = self._form()
        self.tail = QDoubleSpinBox(); self.tail.setRange(0, 30); self.tail.setValue(cfg["tail_seconds"]); self.tail.setSuffix(" s")
        self.hold = QDoubleSpinBox(); self.hold.setRange(0, 30); self.hold.setValue(cfg["endcard_seconds"]); self.hold.setSuffix(" s")
        self.espeed = QDoubleSpinBox(); self.espeed.setRange(0.3, 3.0); self.espeed.setSingleStep(0.05)
        self.espeed.setValue(cfg.get("endcard_speed", 1.0)); self.espeed.setSuffix("×")
        tf.addRow("gameplay tail after last note", self.tail)
        tf.addRow("end-card hold", self.hold)
        tf.addRow("results animation speed", self.espeed)
        cl.addLayout(tf)

        # Encoding
        cl.addSpacing(6)
        cl.addWidget(self._section("Encoding"))
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
        cl.addLayout(ef)
        self.nofail = QCheckBox("auto-fix osu!lazer false fails")
        self.nofail.setChecked(cfg.get("no_fail", True))
        self.nofail.setToolTip("Detects osu!lazer replays (which danser's stable HP model can "
                               "falsely show as failed) and renders just those as NoFail. "
                               "osu!stable replays are always left exactly as recorded.")
        cl.addWidget(self.nofail)

        # Audio
        cl.addSpacing(6)
        cl.addWidget(self._section("Audio"))
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
        cl.addLayout(sf)
        cl.addWidget(self._hint("Both players play the same song, so P2 music defaults to 0 to "
                                "avoid doubling the track. Turn it up to crossfade, or mute a "
                                "side's hitsounds to hear only one player."))

        cl.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        # --- footer band ---
        footer = QWidget()
        footer.setStyleSheet(f"background:{INK2};border-top:1px solid {LINE};")
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

    def result_config(self) -> dict:
        return {
            "danser_bin": self.danser_bin.text().strip(),
            "danser_video_dir": self.danser_video.text().strip(),
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
            "left_music_volume": self.vol_l_music["s"].value(),
            "left_hitsound_volume": self.vol_l_hit["s"].value(),
            "right_music_volume": self.vol_r_music["s"].value(),
            "right_hitsound_volume": self.vol_r_hit["s"].value(),
            "master_volume": self.vol_master["s"].value(),
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
        self._build()
        self._apply_theme()
        self.populate_skins()
        self._setup_thread = None
        if not self.cfg.get("welcomed"):
            WelcomeDialog(self.cfg, self).exec()
            self.cfg["welcomed"] = True
            save_config(self.cfg)
        if not self.cfg["danser_bin"]:
            self._first_run_danser()

    def _first_run_danser(self):
        """If danser isn't configured, try a previous auto-install, else offer to
        download it now (falling back to manual setup)."""
        if danser_setup is not None:
            local = danser_setup.find_local_danser()
            if local:
                self.cfg["danser_bin"] = str(local)
                if not self.cfg.get("danser_video_dir"):
                    self.cfg["danser_video_dir"] = str(Path(local).resolve().parent / "videos")
                save_config(self.cfg)
                self._refresh_enabled()
                return
            choice = QMessageBox.question(
                self, "Set up danser",
                "CircleClash uses danser-go to render the gameplay, but it isn't installed yet.\n\n"
                "Download and set it up automatically now? (~small download, kept in this app's "
                "data folder — danser is GPL-3.0 and fetched from its official GitHub release.)\n\n"
                "Choose No to point at an existing danser yourself.",
                QMessageBox.Yes | QMessageBox.No)
            if choice == QMessageBox.Yes:
                self.download_danser()
                return
        # fallback: manual
        QMessageBox.information(self, "First-time setup",
                                "Point CircleClash at your danser binary, its video output "
                                "folder, and your osu! Songs folder to get started.")
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

    def _on_danser_done(self, path):
        self._setup_thread.quit(); self._setup_thread.wait()
        self.cfg["danser_bin"] = path
        if not self.cfg.get("danser_video_dir"):
            self.cfg["danser_video_dir"] = str(Path(path).resolve().parent / "videos")
        save_config(self.cfg)
        self.status.setText("danser ready — finish setup in Settings (Songs folder, output)")
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

        # log (collapsible)
        self.logToggle = QPushButton("Show log ▸"); self.logToggle.setCheckable(True)
        self.logToggle.clicked.connect(self._toggle_log)
        self.logToggle.setProperty("role", "link")
        root.addWidget(self.logToggle)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setVisible(False)
        self.log.setMaximumBlockCount(4000)
        self.log.setObjectName("logview")
        self.log.setMinimumHeight(200)
        root.addWidget(self.log)

        self.resize(760, 640)
        self._refresh_enabled()

    def _apply_theme(self):
        self.setStyleSheet(_theme_qss())

    # ---- actions ----
    def _toggle_log(self):
        on = self.logToggle.isChecked()
        self.log.setVisible(on)
        self.logToggle.setText("Hide log ▾" if on else "Show log ▸")

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
        ready = bool(self.leftZone.path and self.rightZone.path
                     and self.cfg.get("danser_bin") and self.cfg.get("danser_video_dir"))
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
        # danser reads a small managed folder (instant import); the user's library
        # is just a source for maps they already own.
        render_songs = str(danser_setup.render_songs_dir()) if danser_setup else self.cfg.get("songs_dir", "")
        pargs = [
            self.leftZone.path, self.rightZone.path,
            "--title", self.titleEdit.text() or "friendly · bo1",
            "--danser-bin", self.cfg["danser_bin"],
            "--danser-video-dir", self.cfg["danser_video_dir"],
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
        if self.cfg.get("api_client_id"):
            env.insert("OSU_CLIENT_ID", self.cfg["api_client_id"])
        if self.cfg.get("api_client_secret"):
            env.insert("OSU_CLIENT_SECRET", self.cfg["api_client_secret"])

        self.tracker = ProgressTracker()
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

    def _on_output(self):
        data = bytes(self.proc.readAllStandardOutput()).decode(errors="replace")
        for line in data.splitlines():
            self.log.appendPlainText(line)
            pct, status = self.tracker.update(line)
            if pct is not None:
                self.progress.setValue(pct)
            if status:
                self.status.setText(status)

    def _on_finished(self, code, _status):
        ok = code == 0
        self.proc = None
        self.cancelBtn.setEnabled(False)
        self.settingsBtn.setEnabled(True)
        self._refresh_enabled()
        if ok:
            self.progress.setValue(100)
            self.status.setText(f"Done → {self._out_target.name}")
        else:
            self.status.setText(f"Render failed (exit {code}) — see log")
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
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    # One executable, two roles. When the GUI (frozen as an .exe) needs to render,
    # it relaunches itself with --run-pipeline as the first argument; we detect that
    # here and hand off to the render pipeline instead of starting the GUI. This is
    # what lets a single PyInstaller build act as both the app and its worker.
    if len(sys.argv) > 1 and sys.argv[1] == "--run-pipeline":
        # In a --windowed frozen build PyInstaller can set stdio to None; reattach to
        # the pipe QProcess handed us so the GUI can still read render progress.
        if sys.stdout is None:
            try:
                sys.stdout = os.fdopen(1, "w", buffering=1)
            except Exception:
                sys.stdout = open(os.devnull, "w")
        if sys.stderr is None:
            try:
                sys.stderr = os.fdopen(2, "w", buffering=1)
            except Exception:
                sys.stderr = open(os.devnull, "w")
        import make_overlay_video
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        make_overlay_video.main()
    else:
        main()
