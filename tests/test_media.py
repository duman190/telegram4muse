import base64
from pathlib import Path

import pytest

from telegram4muse import media

FILES = Path(__file__).parent / "media"


def b64(name: str) -> str:
    return base64.b64encode((FILES / name).read_bytes()).decode()


@pytest.mark.parametrize("name, kind, type_, mime", [
    ("forecast.jpg", "photo", "jpeg", "image/jpeg"),
    ("forecast.png", "photo", "png", "image/png"),
    ("voice.ogg", "voice", "ogg", "audio/ogg"),
    ("vorbis.ogg", "voice", "ogg", "audio/ogg"),
    ("voice.mp3", "voice", "mp3", "audio/mpeg"),
    ("voice.m4a", "voice", "m4a", "audio/mp4"),
])
def test_accepted_files(name, kind, type_, mime):
    file = media.prepare(kind, b64(name))
    assert (file.type, file.mime_type) == (type_, mime)
    assert file.data == (FILES / name).read_bytes()
    assert file.name.endswith("." + file.extension)
    if kind == "voice":
        assert file.duration == 3  # 2.6 s clips, rounded
        assert file.summary() == {"type": type_, "size": len(file.data), "duration": 3}
    else:
        assert file.duration is None
        assert file.summary() == {"type": type_, "size": len(file.data)}


def test_ogg_duration_from_page_headers():
    assert media.ogg_duration((FILES / "voice.ogg").read_bytes()) == pytest.approx(2.6, abs=0.01)
    assert media.ogg_duration((FILES / "vorbis.ogg").read_bytes()) == pytest.approx(2.6, abs=0.01)
    assert media.ogg_duration(b"OggS" + b"\0" * 40) is None
    assert media.ogg_duration(b"not ogg") is None


def test_explicit_duration_wins():
    assert media.prepare("voice", b64("voice.mp3"), 42).duration == 42


def test_data_urls_and_line_breaks_are_fine():
    raw = b64("forecast.png")
    wrapped = "\n".join(raw[i:i + 60] for i in range(0, len(raw), 60))
    assert media.prepare("photo", "data:image/png;base64," + wrapped).type == "png"


@pytest.mark.parametrize("kind, data, message", [
    ("photo", None, "base64-encoded"),
    ("photo", "   ", "base64-encoded"),
    ("photo", "not base64!", "isn't valid base64"),
    ("photo", "data:image/png;base64", "isn't a valid data: URL"),
    ("photo", "", "base64-encoded"),
    ("photo", base64.b64encode(b"hello").decode(), "must be JPEG, PNG"),
])
def test_rejected_data(kind, data, message):
    with pytest.raises(ValueError, match=message):
        media.prepare(kind, data)


def test_wrong_kind_of_file():
    with pytest.raises(ValueError, match="must be JPEG, PNG, not WEBP"):
        media.prepare("photo", b64("forecast.webp"))
    with pytest.raises(ValueError, match="must be JPEG, PNG, not OGG"):
        media.prepare("photo", b64("voice.ogg"))
    with pytest.raises(ValueError, match="must be OGG, MP3, M4A, not JPEG"):
        media.prepare("voice", b64("forecast.jpg"))


def test_size_limit(monkeypatch):
    monkeypatch.setitem(media.LIMITS, "photo", 100)
    with pytest.raises(ValueError, match="the limit is 0 MB"):
        media.prepare("photo", b64("forecast.jpg"))
