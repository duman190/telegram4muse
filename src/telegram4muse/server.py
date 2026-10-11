"""The HTTP API Muse calls over Tailscale.

    GET  /healthz            no auth
    GET  /v1/status          signed in? as whom? how many allowed groups
    GET  /v1/chats           the allowed groups
    POST /v1/jobs            run a job now; returns the result
    GET  /v1/jobs            recent jobs, newest first
    GET  /v1/jobs/{id}       one job
    GET  /admin/groups       all your groups; only from this Mac (used by `t4m groups`)

Who may call: the service listens only on the Mac's Tailscale address (plus
127.0.0.1 for the CLI) and answers /v1 only for devices in `allowed_peers`
(Muse's Tailscale IP) and for this Mac itself. On a tailnet an address can't be
spoofed: Tailscale ties it to one device's key, so no token is needed.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import secrets
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from aiohttp import web

from . import __version__, config, jobs

log = logging.getLogger("telegram4muse")

MAX_BODY = 64 * 1024
KEEP_JOBS = 200


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobStore:
    """Recent jobs, kept on disk so a retried request with the same
    idempotency_key returns the earlier result instead of posting twice."""

    def __init__(self, path: Path | None):
        self.path = path
        self.jobs: list[dict] = []
        if path and path.exists():
            self.jobs = json.loads(path.read_text(encoding="utf-8"))

    def get(self, job_id: str) -> dict | None:
        return next((j for j in self.jobs if j["id"] == job_id), None)

    def by_key(self, key: str) -> dict | None:
        return next((j for j in self.jobs if j.get("idempotency_key") == key), None)

    def add(self, job: dict) -> None:
        self.jobs = (self.jobs + [job])[-KEEP_JOBS:]
        if self.path:
            config.write_private(self.path, json.dumps(self.jobs, indent=1, ensure_ascii=False))

    def recent(self, limit: int) -> list[dict]:
        return list(reversed(self.jobs[-limit:]))


def _error(status: int, code: str, message: str) -> web.Response:
    return web.json_response({"error": {"code": code, "message": message}}, status=status)


def _ip(address: str | None):
    """Parsed address (IPv4-mapped IPv6 unwrapped), or None."""
    try:
        ip = ipaddress.ip_address(address or "")
    except ValueError:
        return None
    return getattr(ip, "ipv4_mapped", None) or ip


def _is_loopback(address: str | None) -> bool:
    ip = _ip(address)
    return bool(ip and ip.is_loopback)


def _is_allowed_peer(address: str | None, allowed: list[str]) -> bool:
    ip = _ip(address)
    return ip is not None and any(ip == _ip(a) for a in allowed)


def create_app(*, load_config: Callable[[], config.Config], backend: Any, store: JobStore,
               peer_of: Callable[[web.Request], str | None] = lambda r: r.remote) -> web.Application:
    """`backend` implements jobs.TelegramActions plus account() and groups()."""
    run_lock = asyncio.Lock()

    @web.middleware
    async def guard(request: web.Request, handler):
        if request.path == "/healthz":
            return await handler(request)
        cfg = load_config()
        peer = peer_of(request)
        if request.path.startswith("/admin/"):
            if not _is_loopback(peer):
                return _error(403, "forbidden", "admin endpoints are only available on this Mac")
        elif not _is_loopback(peer) and not _is_allowed_peer(peer, cfg.allowed_peers):
            log.warning("refused a request from %s (not an allowed device)", peer)
            return _error(403, "forbidden", f"This device ({peer}) isn't allowed to call. On the Mac, run: t4m peers {peer}")
        try:
            return await handler(request)
        except web.HTTPException as e:
            return _error(e.status, e.reason.lower().replace(" ", "_"), e.reason)

    async def healthz(_request):
        return web.json_response({"ok": True})

    async def status(_request):
        cfg = load_config()
        try:
            account = await backend.account()
            problem = None if account else "Not signed in. Run `t4m login` on the Mac."
        except Exception as e:  # noqa: BLE001
            account, problem = None, f"Can't reach Telegram: {e}"
        return web.json_response({
            "telegram": {"signed_in": account is not None, "account": account, "problem": problem},
            "allowed_chats": len(cfg.allowed_chats),
            "version": __version__,
        })

    async def chats(_request):
        return web.json_response({"chats": load_config().allowed_chats})

    async def create_job(request):
        cfg = load_config()
        if request.content_length and request.content_length > MAX_BODY:
            return _error(413, "body_too_large", f"body must be under {MAX_BODY} bytes")
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _error(400, "invalid_json", "body is not valid JSON")
        try:
            spec = jobs.normalize_job(body)
        except jobs.ValidationError as e:
            return _error(400, "invalid_request", str(e))

        allowed = cfg.allowed_chat_ids
        if not allowed:
            return _error(409, "no_allowed_chats", "No groups are allowed yet. On the Mac: t4m groups, then t4m allow <group>.")
        denied = sorted({s["chat_id"] for s in spec["steps"]} - allowed)
        if denied:
            return _error(403, "chat_not_allowed", f"chat {', '.join(map(str, denied))} isn't allowed (see GET /v1/chats)")

        # One job at a time, so steps from different jobs never interleave.
        async with run_lock:
            key = spec["idempotency_key"]
            if key and (earlier := store.by_key(key)):
                return web.json_response({**earlier, "replayed": True})
            try:
                account = await backend.account()
            except Exception as e:  # noqa: BLE001
                return _error(503, "telegram_unreachable", f"Can't reach Telegram: {e}")
            if account is None:
                return _error(503, "not_signed_in", "Telegram isn't signed in on the Mac. Run `t4m login` there.")
            job = {
                "id": f"job_{secrets.token_hex(8)}",
                "note": spec["note"],
                "idempotency_key": key,
                "created_at": now_iso(),
                "steps": spec["steps"],
            }
            # Re-read the allowlist: it may have changed while this job waited its turn.
            report = await jobs.run_job(spec["steps"], backend, load_config().allowed_chat_ids)
            job.update(status=report["status"], finished_at=now_iso(), result={"steps": report["steps"]},
                       error=report["error"])
            store.add(job)
        titles = {c["id"]: c.get("title", "") for c in cfg.allowed_chats}
        where = ", ".join(sorted({titles.get(s["chat_id"], str(s["chat_id"])) for s in spec["steps"]}))
        log.info("%s %s in %s: %s%s", job["id"], job["note"] or "job", where, job["status"],
                 f" ({job['error']})" if job["error"] else "")
        return web.json_response(job)

    async def list_jobs(request):
        try:
            limit = min(max(int(request.query.get("limit", "20")), 1), 100)
        except ValueError:
            limit = 20
        return web.json_response({"jobs": store.recent(limit)})

    async def get_job(request):
        job = store.get(request.match_info["job_id"])
        if not job:
            return _error(404, "not_found", "no such job")
        return web.json_response(job)

    async def admin_groups(_request):
        account = await backend.account()
        if account is None:
            return _error(503, "not_signed_in", "Not signed in. Run `t4m login`.")
        return web.json_response({"account": account, "groups": await backend.groups()})

    app = web.Application(middlewares=[guard], client_max_size=MAX_BODY)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/v1/status", status)
    app.router.add_get("/v1/chats", chats)
    app.router.add_post("/v1/jobs", create_job)
    app.router.add_get("/v1/jobs", list_jobs)
    app.router.add_get("/v1/jobs/{job_id}", get_job)
    app.router.add_get("/admin/groups", admin_groups)
    return app


async def serve() -> None:
    """Runs the service until SIGTERM/SIGINT (launchd sends SIGTERM)."""
    from . import tailscale, telegram

    cfg = config.load()
    host = tailscale.resolve_host(cfg.host)
    client = telegram.make_client(cfg)
    await client.connect()
    backend = telegram.TelethonBackend(client)
    if await backend.account() is None:
        log.warning("Not signed in to Telegram yet. Run `t4m login`; jobs will fail until then.")

    app = create_app(load_config=config.load, backend=backend, store=JobStore(config.jobs_path()))
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    hosts = [host] if _is_loopback(host) else [host, "127.0.0.1"]
    for h in hosts:
        await web.TCPSite(runner, h, cfg.port).start()
    log.info("Telegram4Muse listening on http://%s:%d", host, cfg.port)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    log.info("Stopping")
    await runner.cleanup()
    await client.disconnect()
