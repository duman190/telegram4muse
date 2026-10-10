# telegram4muse

Lets [Muse](muse-skill/SKILL.md) post polls and messages to your Telegram groups
**as you**, from your Mac, over Tailscale. Built for one job first: the Friday
surf poll (post the poll, reply with the forecast, pin it), but it handles any
posting to groups you allow.

```
 Muse (cloud agent)                 your MacBook                      Telegram
 ┌──────────────┐   Tailscale     ┌───────────────────────────┐     ┌──────────┐
 │ Friday job   │ ──────────────▶ │ t4m service (launchd)     │ ──▶ │ Surf Crew│
 │ + skill      │  POST /v1/jobs  │  · token + tailnet only   │     │  poll    │
 │              │ ◀────────────── │  · allowed groups only    │     │  reply   │
 └──────────────┘    result       │  · Telethon session (you) │     │  pin     │
                                  └───────────────────────────┘     └──────────┘
```

- **Your login stays on the Mac.** You sign in once in Terminal; the Telethon
  session file sits in a private folder (`0700`/`0600`) and never leaves the
  machine. Muse only gets an API token for this service.
- **Allowed groups only.** You pick groups with `t4m allow`; every job is checked
  against that list before anything is sent. Private chats can't be allowed.
- **Tailnet only.** The service listens on the Mac's Tailscale address (plus
  `127.0.0.1` for the CLI), requires a bearer token, and can be limited to Muse's
  device.
- **Kill switch.** The session shows up in Telegram → Settings → Devices as
  "Mac (Telegram4Muse)". End it there, or run `t4m token --rotate`, and Muse is out.

## What Muse can do

In allowed groups only: send a poll, send a message (optionally as a reply),
pin and unpin, close a poll, and read poll results including who voted for
what. Jobs chain steps ("post a poll, reply to it, pin it") and are safe to
retry. The full contract is in [muse-skill/SKILL.md](muse-skill/SKILL.md).

## Setup

You need: the MacBook with [Tailscale](https://tailscale.com/download/mac)
connected, Python 3.10 or newer, and about ten minutes.

### 1. Install

```sh
git clone https://github.com/duman190/telegram4muse.git
cd telegram4muse
# Either with uv (https://docs.astral.sh/uv/):
uv tool install .
# ...or with a plain virtualenv (macOS's built-in Python is too old; use Homebrew's):
#   brew install python@3.13
#   python3.13 -m venv ~/.telegram4muse && ~/.telegram4muse/bin/pip install .
#   ln -s ~/.telegram4muse/bin/t4m /opt/homebrew/bin/t4m
```

### 2. Telegram API credentials, then sign in

Log in at <https://my.telegram.org/apps>, open **API development tools** and
create an app (any name; platform "Desktop"). Then:

```sh
t4m setup
```

It asks for the `api_id` and `api_hash`, optionally Muse's Tailscale IP, then
signs you in to Telegram: phone number, the code Telegram sends to your
Telegram app, and your two-step password if you have one.

### 3. Choose the groups Muse may use

```sh
t4m groups                 # all your groups and channels, with ids
t4m allow "Surf Crew"      # by name or id; repeat for more
t4m disallow "Surf Crew"   # take it back; applies immediately
```

### 4. Run it in the background

```sh
t4m service install               # starts now and at every login
t4m service install --keep-awake  # same, and keeps the Mac awake while on power
t4m status                        # the address and sign-in state
```

Logs are in `~/Library/Logs/telegram4muse.log`.

### 5. Connect Muse

```sh
t4m status    # "Address for Muse: http://100.x.y.z:8787"
t4m token     # the token
```

Give both to Muse through its secure entry (as `T4M_URL` and `T4M_TOKEN`), not
in chat, and give it [muse-skill/SKILL.md](muse-skill/SKILL.md) as a workspace
skill. Then ask Muse to call `GET /v1/status` and post a test poll in a test
group before pointing the Friday job at it.

## Good to know

**The Mac has to be awake.** A sleeping Mac is off the tailnet, so Muse's
request fails (Muse is told to report it and retry later). With the lid open
and on power, `--keep-awake` prevents idle sleep. Closing the lid still sleeps
a MacBook unless an external display is attached.

**Lock Muse's device down in Tailscale (recommended).** Muse can currently
reach every device on your tailnet, including the iPhone and iPads. In the
[access controls](https://login.tailscale.com/admin/acls), tag Muse's device
(say `tag:muse`) and grant it just this port on the Mac:

```jsonc
"tagOwners": { "tag:muse": ["autogroup:admin"] },
"grants": [
  // ...your existing rules...
  { "src": ["tag:muse"], "dst": ["<your-mac's-tailscale-ip>"], "ip": ["tcp:8787"] }
]
```

Tagged devices aren't covered by rules for your own devices (`autogroup:member`),
so this rule is all Muse gets. If your policy still has the default allow-all
rule (`"src": ["*"]`), change that `*` to `autogroup:member`, or the tag
restricts nothing. You can also run `t4m peers <muse's tailscale ip>` so the
service itself only answers Muse. Don't put it behind `tailscale serve` or
Funnel: it already listens on the tailnet, and Funnel would put it on the internet.

**macOS firewall.** If it's on and blocks incoming connections, allow the
Python that runs `t4m` in System Settings → Network → Firewall → Options.

**Telegram's rules.** This signs in as you with Telegram's official API (via
[Telethon](https://github.com/LonamiWebs/Telethon)), the way third-party apps
do. A weekly poll is ordinary use; mass messaging gets accounts limited.

**Re-signing in.** `t4m login` pauses the background service while you sign in.

## Commands

| | |
|---|---|
| `t4m setup` | API credentials, network, sign-in |
| `t4m login` | sign in again |
| `t4m groups` | list groups; ✓ marks allowed ones |
| `t4m allow GROUP...` / `t4m disallow GROUP...` | change what Muse may use (name or id) |
| `t4m peers [IP...]` / `t4m peers --clear` | which tailnet devices may call |
| `t4m token [--rotate]` | print or replace Muse's token |
| `t4m status` | address, groups, service, sign-in |
| `t4m service install [--keep-awake] \| uninstall \| start \| stop \| restart` | launchd |
| `t4m serve` | run in the foreground (for debugging) |

Files live in `~/Library/Application Support/telegram4muse/` (`T4M_HOME`
overrides): `config.json`, `telegram.session`, and `jobs.json` (recent jobs,
used for safe retries).

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

Tests use a fake Telegram, and check that every Telegram request the code builds
serializes against Telethon's schema.
