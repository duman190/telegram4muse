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
    with _local.open(f"http://127.0.0.1:{cfg.port}{path}", timeout=timeout) as response:
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


def _words(text: str) -> str:
    """Lowercase words only: "Surf! (Bay Area)" -> "surf bay area"."""
    return " ".join(re.sub(r"[\W_]+", " ", text.lower()).split())


def match_groups(groups: list[dict], query: str) -> list[dict]:
    """By id; else exact title; else the same words ignoring punctuation and
    case; else titles containing every word typed."""
    query = query.strip()
    if re.fullmatch(r"-?\d+", query):
        return [g for g in groups if g["id"] == int(query)]
    exact = [g for g in groups if g["title"].strip().lower() == query.lower()]
    if exact:
        return exact
    words = _words(query)
    if not words:
        return []
    same = [g for g in groups if _words(g["title"]) == words]
    return same or [g for g in groups if all(w in _words(g["title"]) for w in words.split())]


def _pick(groups: list[dict], query: str) -> dict:
    found = match_groups(groups, query)
    if not found:
        sys.exit(f'No group matches "{query}". Run the same command without a name to pick from a list.')
    if len(found) > 1:
        names = "\n".join(f"  {g['id']:>16}  {g['title']}" for g in found)
        sys.exit(f'"{query}" matches more than one group:\n{names}\n'
                 "Add more words, use the id, or run the command without a name to pick from a list.")
    return found[0]


def _resolve(groups: list[dict], words: list[str]) -> list[dict]:
    """Command-line words are one name (no quotes needed), or one or more ids."""
    if all(re.fullmatch(r"-?\d+", w) for w in words):
        return [_pick(groups, w) for w in words]
    return [_pick(groups, " ".join(words))]


def _interactive() -> bool:
    return sys.stdin.isatty()


def _choose(groups: list[dict], allowed: set[int], question: str) -> list[dict]:
    """Numbered list; the person types numbers. Sidesteps shell quoting entirely."""
    if not _interactive():
        sys.exit("Give a group name or id.")
    width = len(str(len(groups)))
    for n, g in enumerate(groups, 1):
        print(f"{n:>{width}}. {'✓' if g['id'] in allowed else ' '} {g['title']}  ({g['type']}, {g['id']})")
    answer = input(question).strip()
    chosen = []
    for part in re.split(r"[,\s]+", answer) if answer else []:
        if not part.isdigit() or not 1 <= int(part) <= len(groups):
            sys.exit(f'"{part}" isn\'t a number from the list.')
        chosen.append(groups[int(part) - 1])
    return chosen


def _parse_peers(values: list[str]) -> list[str]:
    peers = []
    for value in values:
        for item in value.split(","):
            if item.strip():
                try:
                    peers.append(tailscale.device_ip(item))
                except ValueError as e:
                    sys.exit(str(e))
    return peers


def _describe_peers(cfg: config.Config) -> str:
    if cfg.allowed_peers:
        return ", ".join(cfg.allowed_peers) + " (and this Mac)"
    return "only this Mac. Allow Muse with: t4m peers <muse's Tailscale name or IP>"


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
    print("   Only the devices you name here can call the service (plus this Mac).")
    peers_default = ", ".join(old.allowed_peers) if old and old.allowed_peers else ""
    raw_peers = input("   Muse's Tailscale name or IP, from `tailscale status`"
                      f"{f' [{peers_default}]' if peers_default else ''}: ").strip()
    peers = _parse_peers([raw_peers]) if raw_peers else (old.allowed_peers if old else [])
    if peers:
        print(f"   Allowed: {', '.join(peers)}")

    cfg = config.Config(
        api_id=int(raw_id),
        api_hash=api_hash,
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
    print("  t4m allow                pick the groups Muse may use")
    print("  t4m service install      run in the background, start at login")
    print("  t4m status               the address to give Muse")
    if not peers:
        print("  t4m peers <muse>         let Muse's device call (by Tailscale name or IP)")


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
    print(f"\n✓ = Muse may use it ({len(allowed)} allowed). Change with `t4m allow` / `t4m disallow`;"
          " without a name they show a list to pick from.")


def cmd_allow(args) -> None:
    cfg = config.load()
    groups = sorted(fetch_groups(cfg), key=lambda g: g["title"].lower())
    if args.name:
        chosen = _resolve(groups, args.name)
    else:
        chosen = _choose(groups, cfg.allowed_chat_ids, "Number(s) to allow, e.g. 3 or 3,5 (Enter to cancel): ")
    changed = False
    for group in chosen:
        if group["id"] in cfg.allowed_chat_ids:
            print(f"Already allowed: {group['title']}")
            continue
        cfg.allowed_chats.append(group)
        changed = True
        print(f"Allowed: {group['title']} ({group['id']})")
    if changed:
        cfg.save()


def cmd_disallow(args) -> None:
    cfg = config.load()
    if not cfg.allowed_chats:
        print("No groups are allowed.")
        return
    allowed = sorted(cfg.allowed_chats, key=lambda g: g["title"].lower())
    if args.name:
        chosen = _resolve(allowed, args.name)
    else:
        chosen = _choose(allowed, set(), "Number(s) to remove (Enter to cancel): ")
    removed = {g["id"] for g in chosen}
    if removed:
        cfg.allowed_chats = [c for c in cfg.allowed_chats if c["id"] not in removed]
        cfg.save()
    for group in chosen:
        print(f"Removed: {group['title']}")


def cmd_peers(args) -> None:
    cfg = config.load()
    if args.clear:
        cfg.allowed_peers = []
        cfg.save()
    elif args.devices:
        cfg.allowed_peers = _parse_peers(args.devices)
        cfg.save()
    print("Accepting calls from " + _describe_peers(cfg))


def cmd_status(_args) -> None:
    cfg = config.load()
    try:
        host = tailscale.resolve_host(cfg.host)
    except RuntimeError as e:
        host = f"(unknown: {e})"
    print(f"Address for Muse:  http://{host}:{cfg.port}")
    print("Accepting calls:   from " + _describe_peers(cfg))
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
    name_help = "group name (no quotes needed, punctuation optional) or id; leave out to pick from a list"
    p = sub.add_parser("allow", help="let Muse use a group")
    p.add_argument("name", nargs="*", metavar="NAME_OR_ID", help=name_help)
    p.set_defaults(fn=cmd_allow)
    p = sub.add_parser("disallow", help="take a group away from Muse")
    p.add_argument("name", nargs="*", metavar="NAME_OR_ID", help=name_help)
    p.set_defaults(fn=cmd_disallow)
    p = sub.add_parser("peers", help="which tailnet devices may call (Muse's)")
    p.add_argument("devices", nargs="*", metavar="DEVICE", help="Tailscale name (like muse) or IP; replaces the list")
    p.add_argument("--clear", action="store_true", help="no devices; only this Mac may call")
    p.set_defaults(fn=cmd_peers)
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
