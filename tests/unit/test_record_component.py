import sys

from astrbot.core.message.components import Record


def test_decode_file_uri_preserves_posix_absolute_path(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    assert Record._decode_file_uri("file:///home/user/a%20b.wav") == "/home/user/a b.wav"


def test_decode_file_uri_normalizes_windows_drive_path(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    assert Record._decode_file_uri("file:///C:/Users/demo/a%20b.wav") == "C:/Users/demo/a b.wav"


def test_decode_file_uri_accepts_legacy_windows_backslashes():
    assert Record._decode_file_uri(
        r"file:///C:\Users\demo\a%20b.wav"
    ) == "C:/Users/demo/a b.wav"
