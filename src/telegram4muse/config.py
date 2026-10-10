"""Where things live, and the config file.

Everything is kept in one private folder (mode 0700):

    ~/Library/Application Support/telegram4muse/
        config.json        api_id/api_hash, Muse's token, address, allowed groups
        telegram.session   your Telegram login (Telethon); never leaves this Mac
        jobs.json          recent jobs, for idempotent retries and lookups

Set T4M_HOME to use a different folder.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_PORT = 8787


def home() -> Path:
    path = os.environ.get("T4M_HOME") or "~/Library/Application Support/telegram4muse"
    return Path(path).expanduser()


def config_path() -> Path:
    return home() / "config.json"


def session_path() -> Path:
    # Telethon appends ".session".
    return home() / "telegram"


def jobs_path() -> Path:
    return home() / "jobs.json"


def ensure_home() -> Path:
    path = home()
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def write_private(path: Path, text: str) -> None:
    """Atomically writes a file only the current user can read."""
    ensure_home()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def new_token() -> str:
    return secrets.token_urlsafe(32)


@dataclass
class Config:
    api_id: int
    api_hash: str
    agent_token: str = field(default_factory=new_token)
    # "auto" = this Mac's Tailscale IPv4 address, looked up at startup.
    host: str = "auto"
    port: int = DEFAULT_PORT
    # If set, only these tailnet addresses may call the API (e.g. Muse's).
    allowed_peers: list[str] = field(default_factory=list)
    # Groups Muse may use: [{"id": -100..., "title": "...", "type": "supergroup"}]
    allowed_chats: list[dict] = field(default_factory=list)

    @property
    def allowed_chat_ids(self) -> set[int]:
        return {int(c["id"]) for c in self.allowed_chats}

    def save(self) -> None:
        write_private(config_path(), json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n")


class NotConfigured(Exception):
    pass


def load() -> Config:
    path = config_path()
    if not path.exists():
        raise NotConfigured(f"No config at {path}. Run `t4m setup` first.")
    data = json.loads(path.read_text(encoding="utf-8"))
    known = Config.__dataclass_fields__.keys()
    return Config(**{k: v for k, v in data.items() if k in known})
