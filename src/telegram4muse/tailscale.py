"""Tailscale addresses: this Mac's (to listen only on the tailnet) and other
devices' (to decide who may call)."""

from __future__ import annotations

import ipaddress
import shutil
import socket
import subprocess
from pathlib import Path

TAILNET = ipaddress.ip_network("100.64.0.0/10")
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
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


def _cli() -> str | None:
    for cli in CLI_PATHS:
        path = shutil.which(cli) or (cli if Path(cli).is_file() else None)
        if path:
            return path
    return None


def _cli_ipv4(*args: str) -> str | None:
    """First tailnet IPv4 printed by `tailscale ip -4 [device]`."""
    cli = _cli()
    if not cli:
        return None
    try:
        out = subprocess.run([cli, "ip", "-4", *args], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return next((word for word in out.split() if _on_tailnet(word)), None)


def ipv4() -> str | None:
    """This machine's Tailscale IPv4 address, or None if Tailscale isn't up."""
    address = _cli_ipv4()
    if address:
        return address
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


def device_ip(name_or_ip: str) -> str:
    """A tailnet device's address, from its IP or its name (like "muse").
    Raises ValueError if it can't be found."""
    value = name_or_ip.strip()
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        if ip not in TAILNET and ip not in TAILNET_V6:
            raise ValueError(f"{value} isn't a Tailscale address (those start with 100.)")
        return str(ip)
    address = _cli_ipv4(value)
    if address:
        return address
    try:  # MagicDNS
        address = socket.gethostbyname(value)
    except OSError:
        address = ""
    if _on_tailnet(address):
        return address
    raise ValueError(f'Can\'t find a tailnet device called "{value}". Use its IP from `tailscale status`.')
