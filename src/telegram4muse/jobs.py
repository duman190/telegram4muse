"""Jobs: what Muse asks for, how it's validated, and how it's run.

A job is a short list of steps run in order, for example:

    {"chat_id": -1001234567890,
     "steps": [{"action": "send_poll", "question": "Surf Saturday?", "options": ["Dawn", "Out"]},
               {"action": "pin_message", "message": {"step": 0}},
               {"action": "send_message", "text": "Forecast: 4-5ft", "reply_to": {"step": 0}}]}

A step refers to a message created earlier in the same job with {"step": N},
or to an older one with {"message_id": ID}.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Protocol

ACTIONS = ("send_poll", "send_message", "pin_message", "unpin_message", "stop_poll", "get_poll_results")
CREATES_MESSAGE = {"send_poll", "send_message"}

MAX_STEPS = 10
MAX_QUESTION = 255
MIN_OPTIONS, MAX_OPTIONS = 2, 12
MAX_OPTION = 100
MAX_TEXT = 4096
MAX_NOTE = 500
MAX_KEY = 200
FORMATS = ("plain", "markdown", "html")
STEP_TIMEOUT = 90  # seconds; Telethon itself waits out short flood limits


class ValidationError(ValueError):
    pass


def _fail(message: str):
    raise ValidationError(message)


def parse_chat_id(value: Any, where: str) -> int:
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) or value == 0:
        _fail(f"{where}: chat_id must be a non-zero integer")
    return value


def _text(value: Any, where: str, limit: int, required: bool = True) -> str | None:
    if value is None:
        if required:
            _fail(f"{where} is required")
        return None
    if not isinstance(value, str):
        _fail(f"{where} must be a string")
    value = value.strip()
    if required and not value:
        _fail(f"{where} must not be empty")
    if len(value) > limit:
        _fail(f"{where} must be at most {limit} characters")
    return value


def _flag(value: Any, where: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        _fail(f"{where} must be true or false")
    return value


def _message_ref(value: Any, where: str, index: int, steps: list[dict], chat_id: int) -> dict:
    if isinstance(value, int) and not isinstance(value, bool):
        value = {"message_id": value}
    if not isinstance(value, dict):
        _fail(f'{where} must be {{"step": n}} or {{"message_id": n}}')
    if ("step" in value) == ("message_id" in value):
        _fail(f'{where} must have exactly one of "step" or "message_id"')
    if "step" in value:
        step = value["step"]
        if isinstance(step, bool) or not isinstance(step, int) or not 0 <= step < index:
            _fail(f"{where}.step must refer to an earlier step (0..{index - 1})")
        if steps[step]["action"] not in CREATES_MESSAGE:
            _fail(f"{where}.step {step} is a {steps[step]['action']} step, which doesn't create a message")
        if steps[step]["chat_id"] != chat_id:
            _fail(f"{where}.step {step} is in a different chat")
        return {"step": step}
    message_id = value["message_id"]
    if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
        _fail(f"{where}.message_id must be a positive integer")
    return {"message_id": message_id}


def _step(raw: Any, index: int, steps: list[dict], default_chat: Any) -> dict:
    where = f"steps[{index}]"
    if not isinstance(raw, dict):
        _fail(f"{where} must be an object")
    action = raw.get("action")
    if action not in ACTIONS:
        _fail(f"{where}.action must be one of: {', '.join(ACTIONS)}")
    chat = raw.get("chat_id", default_chat)
    if chat is None:
        _fail(f"{where}.chat_id is required (or set chat_id on the job)")
    chat_id = parse_chat_id(chat, where)

    def ref(key: str) -> dict:
        return _message_ref(raw.get(key), f"{where}.{key}", index, steps, chat_id)

    step: dict[str, Any] = {"action": action, "chat_id": chat_id}
    if action == "send_poll":
        step["question"] = _text(raw.get("question"), f"{where}.question", MAX_QUESTION)
        options = raw.get("options")
        if not isinstance(options, list):
            _fail(f"{where}.options must be a list of strings")
        if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
            _fail(f"{where}.options must have {MIN_OPTIONS}-{MAX_OPTIONS} entries")
        step["options"] = [_text(o, f"{where}.options[{i}]", MAX_OPTION) for i, o in enumerate(options)]
        step["is_anonymous"] = _flag(raw.get("is_anonymous"), f"{where}.is_anonymous", False)
        step["allows_multiple_answers"] = _flag(raw.get("allows_multiple_answers"), f"{where}.allows_multiple_answers", False)
        step["silent"] = _flag(raw.get("silent"), f"{where}.silent", False)
        if raw.get("reply_to") is not None:
            step["reply_to"] = ref("reply_to")
    elif action == "send_message":
        step["text"] = _text(raw.get("text"), f"{where}.text", MAX_TEXT)
        step["format"] = raw.get("format", "plain")
        if step["format"] not in FORMATS:
            _fail(f"{where}.format must be one of: {', '.join(FORMATS)}")
        step["silent"] = _flag(raw.get("silent"), f"{where}.silent", False)
        if raw.get("reply_to") is not None:
            step["reply_to"] = ref("reply_to")
    elif action == "pin_message":
        step["message"] = ref("message")
        step["notify"] = _flag(raw.get("notify"), f"{where}.notify", False)
    elif action in ("unpin_message", "stop_poll"):
        step["message"] = ref("message")
    elif action == "get_poll_results":
        step["message"] = ref("message")
        step["include_voters"] = _flag(raw.get("include_voters"), f"{where}.include_voters", True)
    return step


def normalize_job(body: Any) -> dict:
    """Validates a job from Muse and fills in defaults. Raises ValidationError."""
    if not isinstance(body, dict):
        _fail("body must be a JSON object")
    unknown = set(body) - {"chat_id", "steps", "note", "idempotency_key"}
    if unknown:
        _fail(f"unknown field(s): {', '.join(sorted(unknown))}")
    raw_steps = body.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        _fail("steps must be a non-empty list")
    if len(raw_steps) > MAX_STEPS:
        _fail(f"at most {MAX_STEPS} steps per job")
    steps: list[dict] = []
    for i, raw in enumerate(raw_steps):
        steps.append(_step(raw, i, steps, body.get("chat_id")))
    return {
        "steps": steps,
        "note": _text(body.get("note"), "note", MAX_NOTE, required=False),
        "idempotency_key": _text(body.get("idempotency_key"), "idempotency_key", MAX_KEY, required=False),
    }


class TelegramActions(Protocol):
    """What a job can do on Telegram. telegram.py implements it with Telethon."""

    async def send_poll(self, chat_id: int, question: str, options: list[str], *, is_anonymous: bool,
                        allows_multiple_answers: bool, silent: bool, reply_to: int | None) -> dict: ...

    async def send_message(self, chat_id: int, text: str, *, silent: bool, reply_to: int | None,
                           format: str) -> dict: ...

    async def pin_message(self, chat_id: int, message_id: int, *, notify: bool) -> None: ...

    async def unpin_message(self, chat_id: int, message_id: int) -> None: ...

    async def stop_poll(self, chat_id: int, message_id: int) -> None: ...

    async def poll_results(self, chat_id: int, message_id: int, *, include_voters: bool) -> dict: ...


def _resolve(ref: dict, created: dict[int, int]) -> int:
    if "message_id" in ref:
        return ref["message_id"]
    if ref["step"] not in created:
        raise RuntimeError(f"step {ref['step']} didn't create a message")
    return created[ref["step"]]


async def _perform(step: dict, tg: TelegramActions, created: dict[int, int]) -> dict:
    action, chat = step["action"], step["chat_id"]
    if action == "send_poll":
        reply_to = _resolve(step["reply_to"], created) if "reply_to" in step else None
        return await tg.send_poll(chat, step["question"], step["options"], is_anonymous=step["is_anonymous"],
                                  allows_multiple_answers=step["allows_multiple_answers"], silent=step["silent"],
                                  reply_to=reply_to)
    if action == "send_message":
        reply_to = _resolve(step["reply_to"], created) if "reply_to" in step else None
        return await tg.send_message(chat, step["text"], silent=step["silent"], reply_to=reply_to,
                                     format=step["format"])
    message_id = _resolve(step["message"], created)
    if action == "pin_message":
        await tg.pin_message(chat, message_id, notify=step["notify"])
        return {}
    if action == "unpin_message":
        await tg.unpin_message(chat, message_id)
        return {}
    if action == "stop_poll":
        await tg.stop_poll(chat, message_id)
        return {}
    if action == "get_poll_results":
        return {"poll": await tg.poll_results(chat, message_id, include_voters=step["include_voters"])}
    raise RuntimeError(f"unsupported action {action}")


def _describe(error: BaseException) -> str:
    if isinstance(error, asyncio.TimeoutError):
        return "Telegram didn't answer in time"
    return str(error) or type(error).__name__


async def run_job(steps: list[dict], tg: TelegramActions, allowed_chat_ids: set[int],
                  step_timeout: float = STEP_TIMEOUT) -> dict:
    """Runs normalized steps in order. Stops at the first failure and marks the
    rest skipped. Refuses the whole job, sending nothing, if any step targets a
    chat that isn't allowed."""
    denied = sorted({s["chat_id"] for s in steps} - allowed_chat_ids)
    if denied:
        return {
            "status": "failed",
            "steps": [{"index": i, "action": s["action"], "ok": False, "skipped": True} for i, s in enumerate(steps)],
            "error": f"chat {', '.join(map(str, denied))} is not an allowed group. Nothing was sent.",
        }

    outcomes: list[dict] = []
    created: dict[int, int] = {}
    error: str | None = None
    for i, step in enumerate(steps):
        if error:
            outcomes.append({"index": i, "action": step["action"], "ok": False, "skipped": True})
            continue
        try:
            result = await asyncio.wait_for(_perform(step, tg, created), step_timeout)
        except Exception as e:  # noqa: BLE001 - reported back to Muse
            error = f"step {i} ({step['action']}) failed: {_describe(e)}"
            outcomes.append({"index": i, "action": step["action"], "ok": False, "error": _describe(e)})
            continue
        if "message_id" in result:
            created[i] = result["message_id"]
        outcomes.append({"index": i, "action": step["action"], "ok": True, "result": result})
    return {"status": "failed" if error else "succeeded", "steps": outcomes, "error": error}
