import asyncio

import pytest

from fakes import FakeTelegram
from telegram4muse.jobs import ValidationError, normalize_job, run_job

CHAT = -1001234567890

SURF = {
    "chat_id": CHAT,
    "note": "Weekend surf poll",
    "steps": [
        {"action": "send_poll", "question": "Surf Saturday?", "options": ["Dawn patrol", "Midday", "Out"]},
        {"action": "send_message", "text": "Forecast: 4-5ft, light offshore", "reply_to": {"step": 0}},
        {"action": "pin_message", "message": {"step": 0}},
    ],
}


def test_normalizes_the_surf_job():
    job = normalize_job(SURF)
    assert job["note"] == "Weekend surf poll"
    assert job["idempotency_key"] is None
    assert job["steps"] == [
        {"action": "send_poll", "chat_id": CHAT, "question": "Surf Saturday?",
         "options": ["Dawn patrol", "Midday", "Out"], "is_anonymous": False,
         "allows_multiple_answers": False, "silent": False},
        {"action": "send_message", "chat_id": CHAT, "text": "Forecast: 4-5ft, light offshore", "format": "plain",
         "silent": False, "reply_to": {"step": 0}},
        {"action": "pin_message", "chat_id": CHAT, "message": {"step": 0}, "notify": False},
    ]


def test_chat_id_as_string_and_per_step_override():
    job = normalize_job({"chat_id": str(CHAT), "steps": [
        {"action": "send_message", "text": "a"},
        {"action": "send_message", "chat_id": -42, "text": "b"},
    ]})
    assert [s["chat_id"] for s in job["steps"]] == [CHAT, -42]


def test_bare_number_is_a_message_id():
    job = normalize_job({"chat_id": CHAT, "steps": [{"action": "get_poll_results", "message": 7}]})
    assert job["steps"][0]["message"] == {"message_id": 7}
    assert job["steps"][0]["include_voters"] is True


@pytest.mark.parametrize("body, message", [
    ({}, "steps must be a non-empty list"),
    ({"steps": [], "run_at": "x"}, "unknown field(s): run_at"),
    ({"steps": [{"action": "send_message", "text": "x"}]}, "chat_id is required"),
    ({"chat_id": CHAT, "steps": [{"action": "delete_chat"}]}, "action must be one of"),
    ({"chat_id": CHAT, "steps": [{"action": "send_poll", "question": "q", "options": ["a"]}]}, "2-12 entries"),
    ({"chat_id": CHAT, "steps": [{"action": "send_poll", "question": "q", "options": ["a", " "]}]}, "options[1] must not be empty"),
    ({"chat_id": CHAT, "steps": [{"action": "send_poll", "question": "x" * 256, "options": ["a", "b"]}]}, "at most 255"),
    ({"chat_id": CHAT, "steps": [{"action": "pin_message", "message": {"step": 0}}]}, "earlier step"),
    ({"chat_id": CHAT, "steps": [{"action": "send_message", "text": "a"}, {"action": "pin_message", "message": {"step": 0}},
                                 {"action": "stop_poll", "message": {"step": 1}}]}, "doesn't create a message"),
    ({"steps": [{"action": "send_message", "chat_id": 1, "text": "a"},
                {"action": "pin_message", "chat_id": 2, "message": {"step": 0}}]}, "different chat"),
    ({"chat_id": CHAT, "steps": [{"action": "pin_message", "message": {"step": 0, "message_id": 1}}]}, "exactly one"),
    ({"chat_id": "--5", "steps": [{"action": "send_message", "text": "a"}]}, "non-zero integer"),
    ({"chat_id": True, "steps": [{"action": "send_message", "text": "a"}]}, "non-zero integer"),
    ({"chat_id": CHAT, "steps": [{"action": "send_message", "text": "a", "silent": "yes"}]}, "true or false"),
    ({"chat_id": CHAT, "steps": [{"action": "send_message", "text": "a", "format": "rtf"}]}, "format must be one of"),
    ({"chat_id": CHAT, "steps": [{"action": "send_message", "text": "a"}] * 11}, "at most 10 steps"),
])
def test_rejects_bad_jobs(body, message):
    with pytest.raises(ValidationError) as e:
        normalize_job(body)
    assert message in str(e.value)


def run(steps, tg, allowed=frozenset({CHAT}), **kw):
    return asyncio.run(run_job(steps, tg, set(allowed), **kw))


def test_runs_steps_in_order_and_resolves_references():
    tg = FakeTelegram()
    report = run(normalize_job(SURF)["steps"], tg)
    assert report["status"] == "succeeded"
    assert report["error"] is None
    assert tg.calls == [
        ("send_poll", CHAT, "Surf Saturday?", ("Dawn patrol", "Midday", "Out"), False, False, None),
        ("send_message", CHAT, "Forecast: 4-5ft, light offshore", 101, False, "plain"),
        ("pin_message", CHAT, 101, False),
    ]
    assert [s["ok"] for s in report["steps"]] == [True, True, True]
    assert report["steps"][0]["result"]["message_id"] == 101
    assert report["steps"][1]["result"]["message_id"] == 102


def test_refuses_the_whole_job_if_any_chat_is_not_allowed():
    tg = FakeTelegram()
    steps = normalize_job({"steps": [
        {"action": "send_message", "chat_id": CHAT, "text": "ok"},
        {"action": "send_message", "chat_id": -1009, "text": "not ok"},
    ]})["steps"]
    report = run(steps, tg)
    assert report["status"] == "failed"
    assert "-1009 is not an allowed group" in report["error"]
    assert tg.calls == []
    assert all(s["skipped"] for s in report["steps"])


def test_stops_at_first_failure_and_skips_the_rest():
    tg = FakeTelegram()
    tg.fail_on = "send_message"
    report = run(normalize_job(SURF)["steps"], tg)
    assert report["status"] == "failed"
    assert report["error"] == "step 1 (send_message) failed: Chat admin privileges are required"
    assert [s["ok"] for s in report["steps"]] == [True, False, False]
    assert report["steps"][2]["skipped"] is True
    assert len(tg.calls) == 2


def test_a_hung_step_times_out():
    tg = FakeTelegram()
    tg.hang_on = "send_poll"
    report = run(normalize_job(SURF)["steps"], tg, step_timeout=0.05)
    assert report["status"] == "failed"
    assert report["steps"][0]["error"] == "Telegram didn't answer in time"


def test_poll_results_step():
    tg = FakeTelegram()
    report = run(normalize_job({"chat_id": CHAT, "steps": [{"action": "get_poll_results", "message": 5}]})["steps"], tg)
    assert report["steps"][0]["result"]["poll"]["options"][0]["voters"][0]["name"] == "Kai"
    assert tg.calls == [("get_poll_results", CHAT, 5, True)]
