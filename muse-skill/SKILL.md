---
name: telegram4muse
description: Post polls and messages, pin or unpin them, close polls, and read poll results in Dmitrii's allowed Telegram groups. Posts go out as Dmitrii, from Dmitrii's iPhone, through the Telegram4Muse bridge. Use it for the weekly surf poll and any other posting to those groups.
---

# Telegram4Muse

Dmitrii's iPhone runs Telegram4Muse, an app signed in to Dmitrii's Telegram account.
You queue **jobs** on a small bridge server. The bridge wakes the phone with a
push notification, the phone runs the job **as Dmitrii**, and the result comes
back to the bridge for you to read.

You never hold Telegram credentials. You can only act in the groups Dmitrii
allowed in the app; the phone refuses anything else.

## Setup (once)

Two secrets, entered through secure entry (never pasted in chat):

- `T4M_BRIDGE_URL`: the bridge address, e.g. `https://telegram4muse-bridge.fly.dev`
- `T4M_AGENT_TOKEN`: the bridge's `AGENT_TOKEN`

Every request sends `Authorization: Bearer $T4M_AGENT_TOKEN`. The helper script
`t4m.py` in this skill wraps the API (Python 3, standard library only):

```sh
python3 t4m.py status          # phone connected? groups allowed? job counts
python3 t4m.py chats           # allowed groups: id, title, type
python3 t4m.py submit job.json --wait   # queue a job and wait up to 2 minutes
python3 t4m.py job JOB_ID      # check a job later
```

Plain `curl` works too; see the API below.

## Rules

1. **Get chat ids from `GET /v1/chats`.** Never guess one. If the group you need
   isn't listed, ask Dmitrii to allow it in the app (Choose groups).
2. **Everything you post appears as Dmitrii.** Write in Dmitrii's voice, and
   don't post anything Dmitrii hasn't asked for or agreed to as a routine.
3. **Set `idempotency_key` on scheduled jobs** (e.g. `surf-poll-2026-10-16`). If
   you retry, the bridge returns the existing job instead of posting twice.
4. **Don't blindly retry a failed job.** `lease_expired` means the phone took
   the job and never reported back, so some steps may have run. Check the group
   (`get_poll_results` on the message id, if you have it) or ask Dmitrii first.
5. **Message ids come only from this bridge.** Use `message_id` values returned
   in earlier job results. Ids from other Telegram tools won't match.
6. **Save the poll's `message_id`** (workspace memory) so you can pin, close or
   read that poll later.

## Jobs

A job is 1-10 steps that run in order. Put `chat_id` on the job to apply it to
every step, or on each step. A step can point at a message an earlier step
created with `{"step": N}`, or at an older message with `{"message_id": ID}`.

| action | fields | result |
|---|---|---|
| `send_poll` | `question` (≤255 chars), `options` (2-12, each ≤100), optional `is_anonymous` (default false), `allows_multiple_answers` (false), `allows_revoting` (false), `silent` (false), `reply_to` | `message_id`, `chat_id`, `link` (when the group has links) |
| `send_message` | `text` (≤4096), optional `silent`, `reply_to` | `message_id`, `chat_id`, `link` |
| `pin_message` | `message`, optional `notify` (default false: pin quietly) | |
| `unpin_message` | `message` | |
| `stop_poll` | `message` | |
| `get_poll_results` | `message`, optional `include_voters` (default true) | `poll`: question, total_voter_count, is_closed, options with text, voter_count, vote_percentage and voters (`name`, `username`) for non-anonymous polls |

Optional job fields: `note` (short label, shown on Dmitrii's phone),
`run_at` (ISO-8601 with offset; default now), `expires_at` (default run_at + 6h;
if the phone hasn't picked the job up by then it becomes `expired`),
`idempotency_key`.

Job `status`: `queued` → `running` → `succeeded` | `failed`; also `canceled`,
`expired`. A failed job has `error`, and `result.steps[i]` says which step
failed (`ok: false`) and which were skipped.

## Weekly surf poll

On Friday morning:

1. `python3 t4m.py status`. If `device` is null or `last_seen_at` is days old,
   tell Dmitrii the phone app isn't connected, and stop.
2. `python3 t4m.py chats` and find the surf group's `id`.
3. If you saved last week's poll id, close and unpin it in the same job.
   Put those steps last, so a problem with last week's poll (say, someone
   already closed it) can't stop this week's from going out.
4. Submit, then wait:

```json
{
  "chat_id": -1001234567890,
  "note": "Weekend surf poll",
  "idempotency_key": "surf-poll-2026-10-16",
  "steps": [
    {"action": "send_poll", "question": "Surf this weekend?",
     "options": ["Sat dawn patrol", "Sat midday", "Sunday", "Out this week"],
     "allows_multiple_answers": true},
    {"action": "pin_message", "message": {"step": 0}},
    {"action": "unpin_message", "message": {"message_id": 5242880}},
    {"action": "stop_poll", "message": {"message_id": 5242880}}
  ]
}
```

5. Save `result.steps[0].result.message_id` as this week's poll as soon as
   step 0 is `ok`, even if a later cleanup step failed.
   If the job is still `queued` after 2 minutes, the phone didn't wake up
   (iOS sometimes holds silent pushes). The bridge shows Dmitrii a "job
   waiting" notification after 10 minutes; check back later with
   `t4m.py job JOB_ID`. Don't queue a second copy.

Before the session, read the results:

```json
{"chat_id": -1001234567890, "steps": [{"action": "get_poll_results", "message": {"message_id": 6291456}}]}
```

## API reference

Base URL `$T4M_BRIDGE_URL`, JSON in and out, errors look like
`{"error": {"code": "...", "message": "..."}}`.

| | | |
|---|---|---|
| `GET /v1/status` | | `device` (registered_at, last_seen_at, push_ready, last_push), `allowed_chats`, `jobs` counts |
| `GET /v1/chats` | | `chats: [{id, title, type}]` |
| `POST /v1/jobs` | job | `201` with the job (`200` if `idempotency_key` matched an existing job) |
| `GET /v1/jobs?limit=20` | | `jobs`, newest first |
| `GET /v1/jobs/{id}` | | the job, with `result` once finished |
| `POST /v1/jobs/{id}/cancel` | | only while `queued` |

Error codes: `400 invalid_request` (the message says which field),
`401 unauthorized`, `403 chat_not_allowed`, `404 not_found`,
`409 no_allowed_chats` (Dmitrii hasn't picked any groups yet),
`409 not_cancelable`.
