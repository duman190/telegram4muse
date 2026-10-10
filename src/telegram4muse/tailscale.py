"""Finding this Mac's Tailscale address, so the service listens only on the tailnet."""

from __future__ import annotations

import ipaddress
import shutil
import socket
import subprocess
from pathlib import Path

TAILNET = ipaddress.ip_network("100.64.0.0/10")
CLI_PATHS = (
    "tailscale",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    "/opt/homebrew/bin/tailscale",
    "/usr/local/bin/tailscale",
)


def _on_tailnet(address: str) -> bool:
    try:
        return ipaddress.ip_address(address) in TAILNET
    except ValueError:
        return False


def ipv4() -> str | None:
    """This machine's Tailscale IPv4 address, or None if Tailscale isn't up."""
    for cli in CLI_PATHS:
        path = shutil.which(cli) or (cli if Path(cli).is_file() else None)
        if not path:
            continue
        try:
            out = subprocess.run([path, "ip", "-4"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        for line in out.split():
            if _on_tailnet(line.strip()):
                return line.strip()
    # No CLI: ask the OS which local address it would use to reach Tailscale's
    # DNS server. No packet is sent.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("100.100.100.100", 53))
            address = s.getsockname()[0]
    except OSError:
        return None
    return address if _on_tailnet(address) else None


def resolve_host(host: str) -> str:
    if host != "auto":
        return host
    address = ipv4()
    if not address:
        raise RuntimeError("Can't find this Mac's Tailscale address. Is Tailscale running and connected?")
    return address
