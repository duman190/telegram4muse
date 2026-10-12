---
name: telegram4muse
description: Post polls, messages, photos and voice notes, pin or unpin them, close polls, and read poll results in Dmitrii's allowed Telegram groups. Posts go out as Dmitrii, from Dmitrii's Mac, over Tailscale. Use it for the weekly surf poll and any other posting to those groups.
---

# Telegram4Muse

Dmitrii's MacBook runs Telegram4Muse, a small service signed in to Dmitrii's
Telegram account. You reach it over Tailscale. You send it a **job**; it posts
right away, **as Dmitrii**, and answers with the result in the same response.

You never hold Telegram credentials, and you can only act in the groups
Dmitrii allowed on the Mac. Anything else is refused.

## Setup (once)

One setting, `T4M_URL`: the address `t4m status` prints on the Mac, e.g.
`http://100.101.102.103:8787`.

There's no token. The Mac recognizes you by your device's Tailscale address,
so call it from your tailnet device ("muse"). Calls from anywhere else get
`403 forbidden`.

```sh
curl -s "$T4M_URL/v1/status"
curl -s "$T4M_URL/v1/chats"
curl -s "$T4M_URL/v1/jobs" -H "Content-Type: application/json" -d @job.json
```

Allow up to 5 minutes for `POST /v1/jobs`: uploading photos and voice notes,
or reading voters on a big poll, takes a while.

## Rules

1. **Get chat ids from `GET /v1/chats`.** Never guess one. If the group you need
   isn't listed, ask Dmitrii to run `t4m allow` on the Mac and pick it.
2. **Everything you post appears as Dmitrii.** Write in Dmitrii's voice, and
   don't post anything Dmitrii hasn't asked for or agreed to as a routine.
3. **Set `idempotency_key` on every job that posts** (e.g. `surf-poll-2026-10-16`).
   If a request times out or the connection drops, send the exact same job
   again: the Mac returns the earlier result (`"replayed": true`) instead of
   posting twice. Leave it off read-only jobs, or you'll get the old answer back.
4. **If the Mac doesn't answer** (connection refused or timeout), it's probably
   asleep or offline. Tell Dmitrii, then retry later with the same
   `idempotency_key`.
5. **Message ids** are Telegram's ids in that chat. Save the ones you get back
   (workspace memory) to pin, close or read that poll later.

## Jobs

A job is 1-10 steps that run in order. Put `chat_id` on the job to apply it to
every step, or on each step. A step can point at a message an earlier step
created with `{"step": N}`, or at an older message with `{"message_id": ID}`.

| action | fields | result |
|---|---|---|
| `send_poll` | `question` (≤255 chars), `options` (2-12, each ≤100); optional `is_anonymous` (default false), `allows_multiple_answers` (false), `silent` (false), `reply_to` | `message_id`, `chat_id`, `link` |
| `send_message` | `text` (≤4096); optional `format`: `plain` (default), `markdown` (`**bold**`, `__italic__`, `[text](url)`) or `html`; `silent`, `reply_to` | `message_id`, `chat_id`, `link` |
| `send_photo` | `data`: the image, base64 (JPEG or PNG, ≤10 MB); optional `caption` (≤1024), `format`, `silent`, `reply_to` | `message_id`, `chat_id`, `link` |
| `send_voice` | `data`: the audio, base64 (OGG Opus preferred; MP3 or M4A also work; ≤20 MB); optional `caption` (≤1024), `format`, `silent`, `reply_to`, `duration` in seconds (read from the file if left out) | `message_id`, `chat_id`, `link` |
| `pin_message` | `message`; optional `notify` (default false: pin quietly) | |
| `unpin_message` | `message` | |
| `stop_poll` | `message` (already closed is fine) | |
| `get_poll_results` | `message`; optional `include_voters` (default true) | `poll`: `question`, `total_voter_count`, `is_closed`, `options` with `text`, `voter_count`, `vote_percentage` and, for non-anonymous polls, `voters` (`name`, `username`) |

Optional job fields: `note` (a short label for the Mac's log), `idempotency_key`.

**Files go inside the job, base64-encoded** (a `data:image/png;base64,` prefix
is fine). The Mac doesn't download URLs, so fetch an image first and send its
bytes. Job history keeps only each file's type and size. Captions are limited to
1024 characters; put a longer forecast in a `send_message` reply instead.

```python
import base64, json
def b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()
job = {"chat_id": CHAT_ID, "steps": [{"action": "send_photo", "data": b64("forecast.png"), "caption": "..."}]}
json.dump(job, open("job.json", "w"))
```

The response is the finished job: `status` is `succeeded` or `failed`. On
failure, `error` says what went wrong, and `result.steps[i]` shows which steps
ran (`ok: true`), which failed, and which were skipped. Steps that already ran
are not undone.

Errors before anything runs come back as `{"error": {"code", "message"}}`:
`400 invalid_request` (the message names the field), `403 chat_not_allowed`,
`403 forbidden` (your device isn't allowed; the message has the exact
`t4m peers ...` command for Dmitrii to run on the Mac), `409 no_allowed_chats`,
`503 not_signed_in` (Dmitrii needs to run `t4m login`).

## Weekly surf poll

Friday morning, after the forecast:

1. `GET /v1/chats` and find the surf group's `id`.
2. Post the poll, reply to it with the forecast image (forecast in the caption)
   and the audio, then pin the poll. If you saved last week's poll id, close and
   unpin it **last**, so a problem with the old poll can't block the new one:

```json
{
  "chat_id": -1001234567890,
  "note": "Weekend surf poll",
  "idempotency_key": "surf-poll-2026-10-16",
  "steps": [
    {"action": "send_poll", "question": "Surf this weekend?",
     "options": ["Sat dawn patrol", "Sat midday", "Sunday", "Out this week"],
     "allows_multiple_answers": true},
    {"action": "send_photo", "reply_to": {"step": 0}, "data": "<forecast image, base64>",
     "format": "markdown", "caption": "**Forecast** Sat: 4-5ft, offshore till 10. Sun: 3ft, onshore by noon."},
    {"action": "send_voice", "reply_to": {"step": 0}, "data": "<audio, base64>"},
    {"action": "pin_message", "message": {"step": 0}},
    {"action": "unpin_message", "message": {"message_id": 4182}},
    {"action": "stop_poll", "message": {"message_id": 4182}}
  ]
}
```

3. Save `result.steps[0].result.message_id` as this week's poll once step 0 is
   `ok`, even if a later cleanup step failed.

To see who's in before the session:

```json
{"chat_id": -1001234567890,
 "steps": [{"action": "get_poll_results", "message": {"message_id": 4250}}]}
```
