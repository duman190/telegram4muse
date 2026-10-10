"""Running the service in the background with launchd (macOS)."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.telegram4muse.server"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "telegram4muse.log"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def plist(keep_awake: bool = False, python: str | None = None) -> bytes:
    args = [python or sys.executable, "-m", "telegram4muse", "serve"]
    if keep_awake:
        # Holds a "don't sleep" assertion while the service runs. Only works on
        # power, and closing the lid still sleeps the Mac.
        args = ["/usr/bin/caffeinate", "-s", *args]
    env = {"PYTHONUNBUFFERED": "1"}
    if os.environ.get("T4M_HOME"):
        env["T4M_HOME"] = os.environ["T4M_HOME"]
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": args,
        "RunAtLoad": True,
        # Restart if it exits, e.g. when Tailscale wasn't up yet at login.
        "KeepAlive": True,
        "ThrottleInterval": 15,
        "StandardOutPath": str(log_path()),
        "StandardErrorPath": str(log_path()),
        "EnvironmentVariables": env,
    })


def _launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    if sys.platform != "darwin":
        raise RuntimeError("The background service uses launchd, which is macOS only. Use `t4m serve` instead.")
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=check)


def is_loaded() -> bool:
    if sys.platform != "darwin":
        return False
    return _launchctl("print", f"{_domain()}/{LABEL}", check=False).returncode == 0


def install(keep_awake: bool = False) -> Path:
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    log_path().parent.mkdir(parents=True, exist_ok=True)
    if is_loaded():
        _launchctl("bootout", f"{_domain()}/{LABEL}", check=False)
    path.write_bytes(plist(keep_awake))
    _launchctl("bootstrap", _domain(), str(path))
    return path


def start() -> None:
    if not is_loaded() and plist_path().exists():
        _launchctl("bootstrap", _domain(), str(plist_path()))


def stop() -> None:
    if is_loaded():
        _launchctl("bootout", f"{_domain()}/{LABEL}", check=False)


def uninstall() -> None:
    stop()
    plist_path().unlink(missing_ok=True)


def restart() -> None:
    if is_loaded():
        _launchctl("kickstart", "-k", f"{_domain()}/{LABEL}")
    else:
        start()
