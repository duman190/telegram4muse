import asyncio
import json
import os
import stat

from aiohttp.test_utils import TestClient, TestServer

from fakes import FakeTelegram
from telegram4muse import config, server

CHAT = -1001234567890
TOKEN = "test-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

SURF = {
    "chat_id": CHAT,
    "note": "Weekend surf poll",
    "idempotency_key": "surf-2026-10-16",
    "steps": [
        {"action": "send_poll", "question": "Surf Saturday?", "options": ["Dawn", "Out"]},
        {"action": "pin_message", "message": {"step": 0}},
    ],
}


def make_config(**overrides) -> config.Config:
    values = dict(api_id=1, api_hash="0" * 32, agent_token=TOKEN,
                  allowed_chats=[{"id": CHAT, "title": "Surf Crew", "type": "supergroup"}])
    values.update(overrides)
    return config.Config(**values)


class Harness:
    def __init__(self, cfg=None, tg=None, store=None, peer="100.64.0.7"):
        self.cfg = cfg or make_config()
        self.tg = tg or FakeTelegram()
        self.store = store or server.JobStore(None)
        self.peer = peer

    def run(self, test):
        async def go():
            app = server.create_app(load_config=lambda: self.cfg, backend=self.tg, store=self.store,
                                    peer_of=lambda _request: self.peer)
            async with TestClient(TestServer(app)) as client:
                await test(client)
        asyncio.run(go())


def test_auth():
    async def test(c):
        assert (await c.get("/healthz")).status == 200
        assert (await c.get("/v1/status")).status == 401
        assert (await c.get("/v1/status", headers={"Authorization": "Bearer nope"})).status == 401
        r = await c.get("/v1/status", headers=AUTH)
        assert r.status == 200
        body = await r.json()
        assert body["telegram"] == {"signed_in": True, "account": "Dmitrii (@duman)", "problem": None}
        assert body["allowed_chats"] == 1
    Harness().run(test)


def test_only_allowed_peers_when_configured():
    async def blocked(c):
        r = await c.get("/v1/chats", headers=AUTH)
        assert r.status == 403
        assert (await r.json())["error"]["code"] == "forbidden"
    Harness(cfg=make_config(allowed_peers=["100.64.0.99"]), peer="100.64.0.7").run(blocked)

    async def allowed(c):
        assert (await c.get("/v1/chats", headers=AUTH)).status == 200
    Harness(cfg=make_config(allowed_peers=["100.64.0.99"]), peer="100.64.0.99").run(allowed)
    # The CLI on the Mac itself is always let through.
    Harness(cfg=make_config(allowed_peers=["100.64.0.99"]), peer="127.0.0.1").run(allowed)


def test_admin_groups_only_from_this_mac():
    async def remote(c):
        assert (await c.get("/admin/groups", headers=AUTH)).status == 403
    Harness(peer="100.64.0.7").run(remote)

    async def local(c):
        r = await c.get("/admin/groups")
        assert r.status == 200
        assert [g["title"] for g in (await r.json())["groups"]] == ["Surf Crew", "Family"]
    Harness(peer="127.0.0.1").run(local)


def test_chats_lists_only_allowed_groups():
    async def test(c):
        body = await (await c.get("/v1/chats", headers=AUTH)).json()
        assert body == {"chats": [{"id": CHAT, "title": "Surf Crew", "type": "supergroup"}]}
    Harness().run(test)


def test_runs_a_job_and_returns_the_result():
    h = Harness()

    async def test(c):
        r = await c.post("/v1/jobs", json=SURF, headers=AUTH)
        assert r.status == 200
        job = await r.json()
        assert job["status"] == "succeeded"
        assert job["error"] is None
        assert job["note"] == "Weekend surf poll"
        assert job["result"]["steps"][0]["result"]["message_id"] == 101
        assert job["result"]["steps"][1] == {"index": 1, "action": "pin_message", "ok": True, "result": {}}
        assert [call[0] for call in h.tg.calls] == ["send_poll", "pin_message"]

        assert (await (await c.get(f"/v1/jobs/{job['id']}", headers=AUTH)).json())["id"] == job["id"]
        assert [j["id"] for j in (await (await c.get("/v1/jobs?limit=5", headers=AUTH)).json())["jobs"]] == [job["id"]]
        assert (await c.get("/v1/jobs/job_nope", headers=AUTH)).status == 404

        # A retry with the same idempotency key returns the earlier result and posts nothing.
        again = await (await c.post("/v1/jobs", json=SURF, headers=AUTH)).json()
        assert again["id"] == job["id"]
        assert again["replayed"] is True
        assert len(h.tg.calls) == 2
    h.run(test)


def test_failed_steps_are_reported_not_raised():
    h = Harness()
    h.tg.fail_on = "pin_message"

    async def test(c):
        r = await c.post("/v1/jobs", json=SURF, headers=AUTH)
        assert r.status == 200
        job = await r.json()
        assert job["status"] == "failed"
        assert job["error"] == "step 1 (pin_message) failed: Chat admin privileges are required"
        assert job["result"]["steps"][0]["ok"] is True
    h.run(test)


def test_refusals():
    async def no_groups(c):
        r = await c.post("/v1/jobs", json=SURF, headers=AUTH)
        assert r.status == 409
        assert (await r.json())["error"]["code"] == "no_allowed_chats"
    Harness(cfg=make_config(allowed_chats=[])).run(no_groups)

    h = Harness()

    async def others(c):
        r = await c.post("/v1/jobs", json={**SURF, "chat_id": -1009}, headers=AUTH)
        assert r.status == 403
        assert (await r.json())["error"]["code"] == "chat_not_allowed"
        r = await c.post("/v1/jobs", data="{not json", headers=AUTH)
        assert r.status == 400
        assert (await r.json())["error"]["code"] == "invalid_json"
        r = await c.post("/v1/jobs", json={"chat_id": CHAT, "steps": [{"action": "send_poll", "question": "q", "options": ["a"]}]},
                         headers=AUTH)
        assert r.status == 400
        assert "2-12 entries" in (await r.json())["error"]["message"]
        r = await c.post("/v1/jobs", data="x" * (server.MAX_BODY + 10), headers=AUTH)
        assert r.status == 413
        assert h.tg.calls == []
    h.run(others)

    async def signed_out(c):
        r = await c.post("/v1/jobs", json=SURF, headers=AUTH)
        assert r.status == 503
        assert (await r.json())["error"]["code"] == "not_signed_in"
    Harness(tg=FakeTelegram(account=None)).run(signed_out)


def test_allowlist_changes_apply_without_a_restart():
    h = Harness()

    async def test(c):
        assert (await c.post("/v1/jobs", json={**SURF, "chat_id": -1009, "idempotency_key": None}, headers=AUTH)).status == 403
        h.cfg.allowed_chats.append({"id": -1009, "title": "Family", "type": "group"})
        assert (await c.post("/v1/jobs", json={**SURF, "chat_id": -1009, "idempotency_key": None}, headers=AUTH)).status == 200
    h.run(test)


def test_job_store_persists_privately(tmp_path, monkeypatch):
    monkeypatch.setenv("T4M_HOME", str(tmp_path / "home"))
    path = config.jobs_path()
    store = server.JobStore(path)
    store.add({"id": "job_1", "idempotency_key": "k", "status": "succeeded"})
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(config.home()).st_mode) == 0o700
    reloaded = server.JobStore(path)
    assert reloaded.by_key("k")["id"] == "job_1"
    assert json.loads(path.read_text())[0]["id"] == "job_1"
