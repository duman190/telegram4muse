# telegram4muse: iPhone app prototype

> **Parked.** The working version is the Mac service on [`main`](../../tree/main).
> This branch keeps the earlier design, in which the iPhone does the posting,
> in case it's worth finishing later.

An iPhone app holds your Telegram session (TDLib) behind Face ID. A small bridge
server queues jobs from Muse and wakes the phone with a silent push. The phone
posts as you and reports the result back.

```
 Muse ──POST /v1/jobs──▶ bridge (Node) ──silent push──▶ iPhone app (TDLib, Face ID)
   ▲                       │  queue, no Telegram creds         │ posts poll / reply / pin
   └──GET /v1/jobs/{id}────┘◀──────── result ─────────────────┘
```

## What's here

| Path | What it is | State |
|---|---|---|
| `bridge/` | Node 20+ job queue and APNs sender, no dependencies | 34 tests pass (`cd bridge && npm test`), including contract tests against the app's fixtures |
| `ios/T4MCore/` | Swift package: job executor, TDLib request builders and parsers, bridge client | Tests written; never run locally (no Swift toolchain was available). `core.yml` runs them on Linux CI |
| `ios/Telegram4Muse/` | SwiftUI app: Face ID lock, sign-in, group picker, push and background handling | Written against TDLib 1.8.68 ([TDLibFramework](https://github.com/Swiftgram/TDLibFramework)). Compiled only by `ios.yml` on macOS CI; never run on a device |
| `muse-skill/` | Skill for Muse plus `t4m.py`, a stdlib-only client | Checked end to end against a local bridge |

## How it works

- **Bridge**: `POST /v1/jobs` validates the job, checks the allowed groups the
  phone published, and sends a silent push. If the phone hasn't picked the job
  up after 10 minutes, it sends a visible "job waiting" notification. Jobs
  expire after 6 hours. Two tokens: `AGENT_TOKEN` for Muse, `DEVICE_TOKEN` for
  the phone.
- **App**: on a push (or a background refresh, or when opened) it claims due
  jobs, runs them with TDLib, and reports back. Every step is checked against
  the groups you allowed on the phone before anything is sent.
- **Security**: TDLib's database is encrypted with a key kept in the Keychain
  (this device only, available after first unlock so pushes work while the phone
  is locked). Face ID locks the app's screens; background jobs run without it
  by design.

## What finishing it would take

1. A paid Apple Developer account (push notifications need one), an APNs key
   (`.p8`), and a bundle id.
2. Deploy the bridge (`bridge/Dockerfile`, `bridge/fly.toml.example`) with
   `AGENT_TOKEN`, `DEVICE_TOKEN` and the `APNS_*` settings (`bridge/.env.example`).
3. On a Mac with Xcode: `brew install xcodegen`, copy
   `ios/Config/Local.xcconfig.example` to `Local.xcconfig`, then
   `cd ios && xcodegen` and run on the iPhone.
4. In the app: unlock with Face ID, enter `api_id`/`api_hash` from
   my.telegram.org, sign in, choose groups, and add the bridge URL and `DEVICE_TOKEN`.
5. Give Muse `muse-skill/` with `T4M_BRIDGE_URL` and `T4M_AGENT_TOKEN`.

**Known limits:** iOS can delay or drop silent pushes and never wakes an app
you've force-quit. The visible notification is the fallback: the job runs when
you tap it.
