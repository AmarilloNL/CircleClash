"""Tests for NVENC pre-flight diagnostics (_encoder_check classifies the failure)."""
import subprocess
import types

import pytest

import make_overlay_video as m


def _fake_run(returncode, stderr=""):
    def _run(cmd, **kw):
        return types.SimpleNamespace(returncode=returncode, stderr=stderr, stdout="")
    return _run


def test_cpu_encoders_always_ok(monkeypatch):
    # should not even shell out for libx264/libx265
    monkeypatch.setattr(m.subprocess, "run", _fake_run(1, "should not be called"))
    assert m._encoder_check("x264") == (True, "")
    assert m._encoder_check("x265")[0] is True


def test_nvenc_ok_when_probe_succeeds(monkeypatch):
    monkeypatch.setattr(m.subprocess, "run", _fake_run(0, ""))
    ok, reason = m._encoder_check("nvenc_h264")
    assert ok and reason == ""


def test_missing_nvenc_build_is_reported(monkeypatch):
    monkeypatch.setattr(m.subprocess, "run",
                        _fake_run(1, "Unknown encoder 'h264_nvenc'"))
    ok, reason = m._encoder_check("nvenc_h264")
    assert not ok
    assert "without NVENC" in reason and "ffmpeg" in reason.lower()


def test_gpu_unreachable_is_reported(monkeypatch):
    monkeypatch.setattr(m.subprocess, "run",
                        _fake_run(1, "Cannot load libnvidia-encode.so.1"))
    ok, reason = m._encoder_check("nvenc_hevc")
    assert not ok
    assert "GPU" in reason or "encode library" in reason


def test_no_capable_device_is_reported(monkeypatch):
    monkeypatch.setattr(m.subprocess, "run",
                        _fake_run(1, "No NVENC capable devices found"))
    ok, reason = m._encoder_check("nvenc_h264")
    assert not ok and "GPU" in reason


def test_old_driver_is_reported(monkeypatch):
    monkeypatch.setattr(m.subprocess, "run",
                        _fake_run(1, "The minimum required Nvidia driver for nvenc is ..."))
    ok, reason = m._encoder_check("nvenc_av1")
    assert not ok and "driver" in reason


def test_unknown_failure_falls_back_to_last_line(monkeypatch):
    monkeypatch.setattr(m.subprocess, "run",
                        _fake_run(1, "some\nweird unexpected error"))
    ok, reason = m._encoder_check("nvenc_h264")
    assert not ok and reason == "weird unexpected error"


def test_old_driver_message_mentions_system_ffmpeg(monkeypatch):
    monkeypatch.setattr(m.subprocess, "run",
                        _fake_run(1, "The minimum required Nvidia driver for nvenc is ..."))
    ok, reason = m._encoder_check("nvenc_h264")
    assert not ok and "system ffmpeg" in reason


def test_encoder_check_uses_given_ffmpeg(monkeypatch):
    seen = {}

    def _run(cmd, **kw):
        seen["bin"] = cmd[0]
        return types.SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(m.subprocess, "run", _run)
    m._encoder_check("nvenc_h264", ffmpeg_bin="/usr/bin/ffmpeg")
    assert seen["bin"] == "/usr/bin/ffmpeg"
