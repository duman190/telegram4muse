"""Photos and voice notes sent in a job: decoding, type checks, durations.

Muse sends files inside the job as base64, so the Mac never fetches anything
from a URL it was handed.
"""

from __future__ import annotations

import base64
import binascii
import io
from dataclasses import dataclass

MB = 1024 * 1024
# Telegram's limit for photos; voice notes are much smaller in practice.
LIMITS = {"photo": 10 * MB, "voice": 20 * MB}

# type -> (file extension, MIME type)
TYPES = {
    # Telegram shows only these as photos (WEBP would arrive as a file or sticker).
    "photo": {"jpeg": ("jpg", "image/jpeg"), "png": ("png", "image/png")},
    "voice": {"ogg": ("ogg", "audio/ogg"), "mp3": ("mp3", "audio/mpeg"), "m4a": ("m4a", "audio/mp4")},
}


@dataclass
class File:
    data: bytes
    type: str
    extension: str
    mime_type: str
    duration: int | None = None  # seconds, voice notes only

    @property
    def name(self) -> str:
        return f"{'forecast' if self.mime_type.startswith('image/') else 'voice'}.{self.extension}"

    def summary(self) -> dict:
        """What's kept in job history instead of the bytes."""
        info = {"type": self.type, "size": len(self.data)}
        if self.duration is not None:
            info["duration"] = self.duration
        return info


def decode_base64(value: object) -> bytes:
    """Base64 text (a data: URL prefix and line breaks are fine) to bytes."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("must be the file's contents, base64-encoded")
    text = value.strip()
    if text.startswith("data:"):
        comma = text.find(",")
        if comma < 0:
            raise ValueError("isn't a valid data: URL")
        text = text[comma + 1:]
    try:
        return base64.b64decode("".join(text.split()), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("isn't valid base64") from None


def sniff(data: bytes) -> str | None:
    """File type from its first bytes."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "mp3"
    if data[4:8] == b"ftyp":
        return "m4a"
    return None


def ogg_duration(data: bytes) -> float | None:
    """Length of an Ogg Opus or Ogg Vorbis file, from its page headers."""
    if not data.startswith(b"OggS") or len(data) < 28:
        return None
    segments = data[26]
    start = 27 + segments
    first = data[start:start + sum(data[27:start])]
    if first.startswith(b"OpusHead") and len(first) >= 12:
        rate, pre_skip = 48000, int.from_bytes(first[10:12], "little")
    elif first.startswith(b"\x01vorbis") and len(first) >= 16:
        rate, pre_skip = int.from_bytes(first[12:16], "little"), 0
    else:
        return None
    if rate <= 0:
        return None
    # The last page's granule position is the total sample count.
    pos = data.rfind(b"OggS")
    while pos >= 0:
        if pos + 14 <= len(data):
            granule = int.from_bytes(data[pos + 6:pos + 14], "little", signed=True)
            if granule > 0:
                return max(0.0, (granule - pre_skip) / rate)
        pos = data.rfind(b"OggS", 0, pos)
    return None


def _metadata_duration(data: bytes, name: str) -> float | None:
    """MP3/M4A length via hachoir, if it's installed and can read the file."""
    try:
        from hachoir.core import config as hachoir_config
        from hachoir.metadata import extractMetadata
        from hachoir.parser import guessParser
        from hachoir.stream import InputIOStream
    except ImportError:
        return None
    hachoir_config.quiet = True
    try:
        parser = guessParser(InputIOStream(io.BytesIO(data), source=name, tags=[("filename", name)]))
        metadata = extractMetadata(parser) if parser else None
    except Exception:  # noqa: BLE001 - a missing duration isn't worth failing over
        return None
    if metadata and metadata.has("duration"):
        return metadata.get("duration").total_seconds()
    return None


def prepare(kind: str, raw_data: object, duration: float | None = None) -> File:
    """Decodes and checks a photo or voice note ("photo" or "voice"). `duration`
    (seconds) overrides what's read from the file. Raises ValueError."""
    data = decode_base64(raw_data)
    if not data:
        raise ValueError("is empty")
    if len(data) > LIMITS[kind]:
        raise ValueError(f"is {len(data) / MB:.1f} MB; the limit is {LIMITS[kind] // MB} MB")
    found = sniff(data)
    allowed = TYPES[kind]
    if found not in allowed:
        names = ", ".join(t.upper() for t in allowed)
        raise ValueError(f"must be {names}" + (f", not {found.upper()}" if found else ""))
    extension, mime_type = allowed[found]
    file = File(data=data, type=found, extension=extension, mime_type=mime_type)
    if kind == "voice":
        if duration is not None:
            seconds = float(duration)
        elif found == "ogg":
            seconds = ogg_duration(data)
        else:
            seconds = _metadata_duration(data, file.name)
        file.duration = max(1, round(seconds)) if seconds else None
    return file
