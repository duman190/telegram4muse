import asyncio
import base64
import json
import os
import stat
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from fakes import FakeTelegram
from telegram4muse import config, server

CHAT = -1001234567890
MUSE = "100.64.0.99"

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
    values = dict(api_id=1, api_hash="0" * 32, allowed_peers=[MUSE],
                  allowed_chats=[{"id": CHAT, "title": "Surf Crew", "type": "supergroup"}])
    values.update(overrides)
    return config.Config(**values)


class Harness:
    def __init__(self, cfg=None, tg=None, store=None, peer=MUSE):
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


def test_status():
    async def test(c):
        assert (await c.get("/healthz")).status == 200
        r = await c.get("/v1/status")
        assert r.status == 200
        body = await r.json()
        assert body["telegram"] == {"signed_in": True, "account": "Dmitrii (@duman)", "problem": None}
        assert body["allowed_chats"] == 1
    Harness().run(test)


def test_only_allowed_devices_and_this_mac_may_call():
    async def refused(c):
        r = await c.post("/v1/jobs", json=SURF)
        assert r.status == 403
        error = (await r.json())["error"]
        assert error["code"] == "forbidden"
        assert "t4m peers 100.64.0.7" in error["message"]
        assert (await c.get("/v1/chats")).status == 403
        assert (await c.get("/healthz")).status == 200
    Harness(peer="100.64.0.7").run(refused)
    # With no devices allowed, only the Mac itself gets in.
    Harness(cfg=make_config(allowed_peers=[]), peer="100.64.0.7").run(refused)

    async def allowed(c):
        assert (await c.get("/v1/chats")).status == 200
    Harness(peer=MUSE).run(allowed)
    Harness(peer=f"::ffff:{MUSE}").run(allowed)
    Harness(cfg=make_config(allowed_peers=[]), peer="127.0.0.1").run(allowed)
    Harness(cfg=make_config(allowed_peers=[]), peer="::1").run(allowed)

    async def no_address(c):
        assert (await c.get("/v1/chats")).status == 403
    Harness(peer=None).run(no_address)


def test_admin_groups_only_from_this_mac():
    async def remote(c):
        assert (await c.get("/admin/groups")).status == 403
    Harness(peer=MUSE).run(remote)

    async def local(c):
        r = await c.get("/admin/groups")
        assert r.status == 200
        assert [g["title"] for g in (await r.json())["groups"]] == ["Surf Crew", "Family"]
    Harness(peer="127.0.0.1").run(local)


def test_chats_lists_only_allowed_groups():
    async def test(c):
        body = await (await c.get("/v1/chats")).json()
        assert body == {"chats": [{"id": CHAT, "title": "Surf Crew", "type": "supergroup"}]}
    Harness().run(test)


def test_runs_a_job_and_returns_the_result():
    h = Harness()

    async def test(c):
        r = await c.post("/v1/jobs", json=SURF)
        assert r.status == 200
        job = await r.json()
        assert job["status"] == "succeeded"
        assert job["error"] is None
        assert job["note"] == "Weekend surf poll"
        assert job["result"]["steps"][0]["result"]["message_id"] == 101
        assert job["result"]["steps"][1] == {"index": 1, "action": "pin_message", "ok": True, "result": {}}
        assert [call[0] for call in h.tg.calls] == ["send_poll", "pin_message"]

        assert (await (await c.get(f"/v1/jobs/{job['id']}")).json())["id"] == job["id"]
        assert [j["id"] for j in (await (await c.get("/v1/jobs?limit=5")).json())["jobs"]] == [job["id"]]
        assert (await c.get("/v1/jobs/job_nope")).status == 404

        # A retry with the same idempotency key returns the earlier result and posts nothing.
        again = await (await c.post("/v1/jobs", json=SURF)).json()
        assert again["id"] == job["id"]
        assert again["replayed"] is True
        assert len(h.tg.calls) == 2
    h.run(test)


def test_failed_steps_are_reported_not_raised():
    h = Harness()
    h.tg.fail_on = "pin_message"

    async def test(c):
        r = await c.post("/v1/jobs", json=SURF)
        assert r.status == 200
        job = await r.json()
        assert job["status"] == "failed"
        assert job["error"] == "step 1 (pin_message) failed: Chat admin privileges are required"
        assert job["result"]["steps"][0]["ok"] is True
    h.run(test)


def test_refusals():
    async def no_groups(c):
        r = await c.post("/v1/jobs", json=SURF)
        assert r.status == 409
        assert (await r.json())["error"]["code"] == "no_allowed_chats"
    Harness(cfg=make_config(allowed_chats=[])).run(no_groups)

    h = Harness()

    async def others(c):
        r = await c.post("/v1/jobs", json={**SURF, "chat_id": -1009})
        assert r.status == 403
        assert (await r.json())["error"]["code"] == "chat_not_allowed"
        r = await c.post("/v1/jobs", data="{not json")
        assert r.status == 400
        assert (await r.json())["error"]["code"] == "invalid_json"
        r = await c.post("/v1/jobs", json={"chat_id": CHAT, "steps": [{"action": "send_poll", "question": "q", "options": ["a"]}]})
        assert r.status == 400
        assert "2-12 entries" in (await r.json())["error"]["message"]
        r = await c.post("/v1/jobs", data="x" * (server.MAX_BODY + 10))
        assert r.status == 413
        assert h.tg.calls == []
    h.run(others)

    async def signed_out(c):
        r = await c.post("/v1/jobs", json=SURF)
        assert r.status == 503
        assert (await r.json())["error"]["code"] == "not_signed_in"
    Harness(tg=FakeTelegram(account=None)).run(signed_out)


def test_allowlist_changes_apply_without_a_restart():
    h = Harness()

    async def test(c):
        assert (await c.post("/v1/jobs", json={**SURF, "chat_id": -1009, "idempotency_key": None})).status == 403
        h.cfg.allowed_chats.append({"id": -1009, "title": "Family", "type": "group"})
        assert (await c.post("/v1/jobs", json={**SURF, "chat_id": -1009, "idempotency_key": None})).status == 200
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


def test_media_job_runs_and_history_keeps_no_file_data(tmp_path, monkeypatch):
    monkeypatch.setenv("T4M_HOME", str(tmp_path / "home"))
    photo = base64.b64encode((Path(__file__).parent / "media" / "forecast.jpg").read_bytes()).decode()
    h = Harness(store=server.JobStore(config.jobs_path()))

    async def test(c):
        body = {"chat_id": CHAT, "idempotency_key": "photo-1", "steps": [
            {"action": "send_photo", "data": photo, "caption": "Saturday"},
        ]}
        r = await c.post("/v1/jobs", json=body)
        assert r.status == 200
        job = await r.json()
        assert job["status"] == "succeeded"
        assert job["steps"][0]["file"] == {"type": "jpeg", "size": 1260}
        assert photo not in await r.text()
    h.run(test)
    assert photo[:100] not in config.jobs_path().read_text()
    assert h.tg.calls == [("send_photo", CHAT, "jpeg", 1260, "Saturday", None)]


def test_large_bodies_are_accepted_up_to_the_limit():
    assert server.MAX_BODY >= 40 * 1024 * 1024


def test_calls_from_muse_are_logged(caplog):
    caplog.set_level("INFO", logger="telegram4muse")

    async def test(c):
        await c.get("/v1/chats")
        await c.post("/v1/jobs", json={**SURF, "chat_id": -1009})
    Harness().run(test)
    messages = [r.getMessage() for r in caplog.records]
    assert f"GET /v1/chats from {MUSE}: 200" in messages
    assert any(m.startswith(f"POST /v1/jobs from {MUSE}: 403") and "chat_not_allowed" in m for m in messages)

    caplog.clear()

    async def local(c):
        await c.get("/v1/chats")
    Harness(peer="127.0.0.1").run(local)
    assert not any("/v1/chats" in r.getMessage() for r in caplog.records)  # the CLI's own calls stay quiet
