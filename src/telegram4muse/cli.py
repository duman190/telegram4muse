"""`t4m`: set up, sign in, choose groups, and run the service."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import urllib.error
import urllib.request
from getpass import getpass

from . import config, launchd, tailscale

# Talk to the local service directly, never through a proxy.
_local = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _local_get(cfg: config.Config, path: str, timeout: float = 60) -> dict:
    """GET from the running service on 127.0.0.1. Raises URLError if it isn't running."""
    request = urllib.request.Request(f"http://127.0.0.1:{cfg.port}{path}",
                                     headers={"Authorization": f"Bearer {cfg.agent_token}"})
    with _local.open(request, timeout=timeout) as response:
        return json.load(response)


def _service_running(cfg: config.Config) -> bool:
    try:
        _local_get(cfg, "/healthz", timeout=2)
        return True
    except (urllib.error.URLError, OSError):
        return False


def fetch_groups(cfg: config.Config) -> list[dict]:
    """All your groups: from the running service if there is one (it holds the
    Telegram session), otherwise by connecting directly."""
    try:
        return _local_get(cfg, "/admin/groups")["groups"]
    except urllib.error.HTTPError as e:
        sys.exit(json.load(e)["error"]["message"])
    except (urllib.error.URLError, OSError):
        pass
    return asyncio.run(_groups_direct(cfg))


async def _groups_direct(cfg: config.Config) -> list[dict]:
    from . import telegram

    client = telegram.make_client(cfg)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            sys.exit("Not signed in to Telegram. Run `t4m login` first.")
        return await telegram.list_groups(client)
    finally:
        await client.disconnect()


def match_groups(groups: list[dict], term: str) -> list[dict]:
    """By id, else exact title (any case), else title substring."""
    if re.fullmatch(r"-?\d+", term.strip()):
        return [g for g in groups if g["id"] == int(term)]
    lowered = term.strip().lower()
    exact = [g for g in groups if g["title"].lower() == lowered]
    return exact or [g for g in groups if lowered in g["title"].lower()]


def _pick(groups: list[dict], term: str) -> dict:
    found = match_groups(groups, term)
    if not found:
        sys.exit(f'No group matches "{term}". See `t4m groups`.')
    if len(found) > 1:
        names = "\n".join(f"  {g['id']}  {g['title']}" for g in found)
        sys.exit(f'"{term}" matches more than one group; use the id:\n{names}')
    return found[0]


# --- commands ---

def cmd_setup(_args) -> None:
    try:
        old = config.load()
    except config.NotConfigured:
        old = None
    print("1. Telegram API credentials")
    print("   Log in at https://my.telegram.org/apps, open 'API development tools' and create an app.\n")
    raw_id = input(f"   api_id{f' [{old.api_id}]' if old else ''}: ").strip() or (str(old.api_id) if old else "")
    if not raw_id.isdigit():
        sys.exit("api_id is a number, like 1234567.")
    api_hash = getpass(f"   api_hash{' [keep current]' if old else ''} (hidden): ").strip() or (old.api_hash if old else "")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
        sys.exit("api_hash is 32 letters and digits.")

    print("\n2. Network")
    address = tailscale.ipv4()
    if address:
        print(f"   This Mac's Tailscale address: {address}. The service will listen only there.")
    else:
        print("   Tailscale isn't connected right now. That's fine for setup; the service waits for it.")
    peers_default = ", ".join(old.allowed_peers) if old and old.allowed_peers else ""
    raw_peers = input("   Muse's Tailscale IP, to accept calls only from it (Enter = any device on your tailnet)"
                      f"{f' [{peers_default}]' if peers_default else ''}: ").strip()
    peers = [p.strip() for p in raw_peers.split(",") if p.strip()] if raw_peers else (old.allowed_peers if old else [])

    cfg = config.Config(
        api_id=int(raw_id),
        api_hash=api_hash,
        agent_token=old.agent_token if old else config.new_token(),
        host=old.host if old else "auto",
        port=old.port if old else config.DEFAULT_PORT,
        allowed_peers=peers,
        allowed_chats=old.allowed_chats if old else [],
    )
    cfg.save()
    print(f"\n   Saved {config.config_path()}")

    print("\n3. Sign in to Telegram")
    cmd_login(None)
    print("\nNext:")
    print("  t4m groups               list your groups")
    print('  t4m allow "Surf Crew"    let Muse use one')
    print("  t4m service install      run in the background, start at login")
    print("  t4m status               the address and token to give Muse")


def cmd_login(_args) -> None:
    from . import telegram

    cfg = config.load()
    paused = launchd.is_loaded()
    if paused:
        print("Pausing the background service while you sign in...")
        launchd.stop()
    elif _service_running(cfg):
        sys.exit("Stop the running `t4m serve` first; it has the Telegram session open.")
    try:
        name = asyncio.run(telegram.login(cfg))
    finally:
        if paused:
            launchd.start()
    print(f"Signed in as {name}. The session is saved in {config.home()} and stays on this Mac.")


def cmd_groups(_args) -> None:
    cfg = config.load()
    groups = fetch_groups(cfg)
    allowed = cfg.allowed_chat_ids
    print(f"{'':2}{'id':>16}  {'type':<11}title")
    for g in sorted(groups, key=lambda g: (g["id"] not in allowed, g["title"].lower())):
        print(f"{'✓' if g['id'] in allowed else ' ':2}{g['id']:>16}  {g['type']:<11}{g['title']}")
    print(f"\n✓ = Muse may use it ({len(allowed)} allowed). Change with `t4m allow` / `t4m disallow`.")


def cmd_allow(args) -> None:
    cfg = config.load()
    groups = fetch_groups(cfg)
    for term in args.groups:
        group = _pick(groups, term)
        if group["id"] in cfg.allowed_chat_ids:
            print(f"Already allowed: {group['title']}")
            continue
        cfg.allowed_chats.append(group)
        print(f"Allowed: {group['title']} ({group['id']})")
    cfg.save()


def cmd_disallow(args) -> None:
    cfg = config.load()
    for term in args.groups:
        group = _pick(cfg.allowed_chats, term)
        cfg.allowed_chats = [c for c in cfg.allowed_chats if c["id"] != group["id"]]
        print(f"Removed: {group['title']}")
    cfg.save()


def cmd_peers(args) -> None:
    cfg = config.load()
    if args.clear:
        cfg.allowed_peers = []
        cfg.save()
    elif args.addresses:
        cfg.allowed_peers = args.addresses
        cfg.save()
    print("Accepting calls from: " + (", ".join(cfg.allowed_peers) if cfg.allowed_peers else "any device on your tailnet"))


def cmd_token(args) -> None:
    cfg = config.load()
    if args.rotate:
        cfg.agent_token = config.new_token()
        cfg.save()
        print("New token; the old one no longer works. Update it in Muse:", file=sys.stderr)
    print(cfg.agent_token)


def cmd_status(_args) -> None:
    cfg = config.load()
    try:
        host = tailscale.resolve_host(cfg.host)
    except RuntimeError as e:
        host = f"(unknown: {e})"
    print(f"Address for Muse:  http://{host}:{cfg.port}")
    print("Token for Muse:    t4m token   (enter it in Muse's secure entry, not in chat)")
    print("Accepting calls:   " + (", ".join(cfg.allowed_peers) if cfg.allowed_peers else "any device on your tailnet"))
    print(f"Allowed groups:    {len(cfg.allowed_chats)}")
    for chat in cfg.allowed_chats:
        print(f"                   {chat['title']} ({chat['id']})")
    if sys.platform == "darwin":
        print(f"Background service: {'running' if launchd.is_loaded() else 'not installed (t4m service install)'}")
    try:
        telegram = _local_get(cfg, "/v1/status", timeout=15)["telegram"]
        print(f"Telegram:          {telegram['account'] or telegram['problem']}")
    except (urllib.error.URLError, OSError):
        print("Telegram:          (service not running)")
    print(f"Files:             {config.home()}")


def cmd_serve(_args) -> None:
    from . import server

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("telethon").setLevel(logging.WARNING)
    try:
        asyncio.run(server.serve())
    except (config.NotConfigured, RuntimeError, OSError) as e:
        sys.exit(f"telegram4muse: {e}")


def cmd_service(args) -> None:
    if args.action == "install":
        path = launchd.install(keep_awake=args.keep_awake)
        print(f"Installed {path}; it starts now and at every login.")
        print(f"Logs: {launchd.log_path()}")
    elif args.action == "uninstall":
        launchd.uninstall()
        print("Stopped and removed.")
    elif args.action == "restart":
        launchd.restart()
        print("Restarted.")
    elif args.action == "stop":
        launchd.stop()
        print("Stopped (starts again at next login; `t4m service uninstall` to remove).")
    elif args.action == "start":
        launchd.start()
        print("Started.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="t4m", description="Let Muse post to your allowed Telegram groups, as you.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="first-time setup: API credentials, network, sign-in").set_defaults(fn=cmd_setup)
    sub.add_parser("login", help="sign in to Telegram (again)").set_defaults(fn=cmd_login)
    sub.add_parser("groups", help="list your groups and which ones Muse may use").set_defaults(fn=cmd_groups)
    p = sub.add_parser("allow", help="let Muse use a group (id or name)")
    p.add_argument("groups", nargs="+", metavar="GROUP")
    p.set_defaults(fn=cmd_allow)
    p = sub.add_parser("disallow", help="take a group away from Muse")
    p.add_argument("groups", nargs="+", metavar="GROUP")
    p.set_defaults(fn=cmd_disallow)
    p = sub.add_parser("peers", help="limit which tailnet devices may call (e.g. only Muse)")
    p.add_argument("addresses", nargs="*", metavar="TAILSCALE_IP")
    p.add_argument("--clear", action="store_true", help="accept any device on the tailnet")
    p.set_defaults(fn=cmd_peers)
    p = sub.add_parser("token", help="print the token Muse uses")
    p.add_argument("--rotate", action="store_true", help="make a new one (the old one stops working)")
    p.set_defaults(fn=cmd_token)
    sub.add_parser("status", help="address, groups, service and sign-in state").set_defaults(fn=cmd_status)
    sub.add_parser("serve", help="run the service in the foreground").set_defaults(fn=cmd_serve)
    p = sub.add_parser("service", help="background service (launchd)")
    p.add_argument("action", choices=["install", "uninstall", "start", "stop", "restart"])
    p.add_argument("--keep-awake", action="store_true", help="keep the Mac from sleeping while on power")
    p.set_defaults(fn=cmd_service)

    args = parser.parse_args(argv)
    try:
        args.fn(args)
    except config.NotConfigured as e:
        sys.exit(str(e))
    except KeyboardInterrupt:
        sys.exit(130)
