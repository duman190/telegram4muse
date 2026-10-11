# telegram4muse

Lets [Muse](muse-skill/SKILL.md) post polls and messages to your Telegram groups
**as you**, from your Mac, over Tailscale. Built for one job first: the Friday
surf poll (post the poll, reply with the forecast, pin it), but it handles any
posting to groups you allow.

```
 Muse (cloud agent)                 your MacBook                      Telegram
 ┌──────────────┐   Tailscale     ┌───────────────────────────┐     ┌──────────┐
 │ Friday job   │ ──────────────▶ │ t4m service (launchd)     │ ──▶ │ Surf Crew│
 │ + skill      │  POST /v1/jobs  │  · Muse's device only     │     │  poll    │
 │              │ ◀────────────── │  · allowed groups only    │     │  reply   │
 └──────────────┘    result       │  · Telethon session (you) │     │  pin     │
                                  └───────────────────────────┘     └──────────┘
```

- **Your login stays on the Mac.** You sign in once in Terminal; the Telethon
  session file sits in a private folder (`0700`/`0600`) and never leaves the
  machine. Muse gets nothing secret, not even a token.
- **Allowed groups only.** You pick groups with `t4m allow`; every job is checked
  against that list before anything is sent. Private chats can't be allowed.
- **Muse's device only.** The service listens only on the Mac's Tailscale
  address and answers only the devices you name with `t4m peers` (Muse's), plus
  the Mac itself. Tailscale ties each address to one device's key, so another
  device can't pretend to be Muse.
- **Kill switch.** The session shows up in Telegram → Settings → Devices as
  "Mac (Telegram4Muse)". End it there, or run `t4m peers --clear`, and Muse is out.

## What Muse can do

In allowed groups only: send a poll, send a message (optionally as a reply),
pin and unpin, close a poll, and read poll results including who voted for
what. Jobs chain steps ("post a poll, reply to it, pin it") and are safe to
retry. The full contract is in [muse-skill/SKILL.md](muse-skill/SKILL.md).

## Setup

You need: the MacBook with [Tailscale](https://tailscale.com/download/mac)
connected, Python 3.9 or newer, and about ten minutes. The `python3` that comes
with macOS (3.9) works; if Terminal offers to install the developer tools the
first time you run it, say yes.

### 1. Install

```sh
git clone https://github.com/duman190/telegram4muse.git
cd telegram4muse
python3 -m venv ~/.telegram4muse
~/.telegram4muse/bin/pip install --upgrade pip
~/.telegram4muse/bin/pip install .
sudo mkdir -p /usr/local/bin && sudo ln -sf ~/.telegram4muse/bin/t4m /usr/local/bin/t4m   # puts `t4m` on your PATH
```

If you use [uv](https://docs.astral.sh/uv/), `uv tool install .` does the same in one step.

### 2. Telegram API credentials, then sign in

Log in at <https://my.telegram.org/apps>, open **API development tools** and
create an app (any name; platform "Desktop"). Then:

```sh
t4m setup
```

It asks for the `api_id` and `api_hash`, then Muse's Tailscale device name or
IP (`tailscale status` lists your devices; the name `muse` works), then signs
you in to Telegram: phone number, the code Telegram sends to your Telegram app,
and your two-step password if you have one.

### 3. Choose the groups Muse may use

```sh
t4m allow                   # numbered list of your groups; type the number(s)
t4m allow surf bay area     # or by name: no quotes needed, punctuation ignored
t4m groups                  # what's allowed (✓)
t4m disallow                # take one back; applies immediately
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
t4m peers muse   # if you skipped it in setup: Muse's Tailscale name or IP
t4m status       # "Address for Muse: http://100.x.y.z:8787"
```

Give Muse the address (as `T4M_URL`) and [muse-skill/SKILL.md](muse-skill/SKILL.md)
as a workspace skill. There's no token to hand over. Then ask Muse to call
`GET /v1/status` and post a test poll in a test group before pointing the
Friday job at it. If Muse gets `403 forbidden`, the error includes the exact
`t4m peers ...` command to run.

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
restricts nothing. (`t4m peers` already makes the service answer only Muse; the
access controls keep Muse away from your other devices.) Don't put the service
behind `tailscale serve` or Funnel: it already listens on the tailnet, and
proxied requests would look like they come from the Mac itself.

**Programs on the Mac can call it.** Calls from the Mac itself are always
allowed; that's how the `t4m` commands talk to the service. Anything running
as you could also read the Telegram session file, so this doesn't open a new
door.

**macOS firewall.** If it's on and blocks incoming connections, allow the
Python that runs `t4m` in System Settings → Network → Firewall → Options.

**Telegram's rules.** This signs in as you with Telegram's official API (via
[Telethon](https://github.com/LonamiWebs/Telethon)), the way third-party apps
do. A weekly poll is ordinary use; mass messaging gets accounts limited.

**Re-signing in.** `t4m login` pauses the background service while you sign in.

**Updating.** In your clone: `git pull && ~/.telegram4muse/bin/pip install . && t4m service restart`.

## Commands

| | |
|---|---|
| `t4m setup` | API credentials, network, sign-in |
| `t4m login` | sign in again |
| `t4m groups` | list groups; ✓ marks allowed ones |
| `t4m allow [NAME_OR_ID]` / `t4m disallow [NAME_OR_ID]` | change what Muse may use; no name shows a list to pick from |
| `t4m peers [DEVICE...]` / `t4m peers --clear` | which tailnet devices may call (Tailscale name or IP) |
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
