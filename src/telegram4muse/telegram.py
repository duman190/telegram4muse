"""The Telegram side, via Telethon (an MTProto client: you, not a bot)."""

from __future__ import annotations

import io
import platform
import random
from getpass import getpass
from typing import Any

from telethon import TelegramClient
from telethon.tl import functions, types

from . import __version__, config, media

MAX_VOTERS = 1000
PARSE_MODES = {"plain": None, "markdown": "md", "html": "html"}


def make_client(cfg: config.Config) -> TelegramClient:
    config.ensure_home()
    client = TelegramClient(
        str(config.session_path()),
        cfg.api_id,
        cfg.api_hash,
        # Shows up in Telegram > Settings > Devices, where you can end the session any time.
        device_model="Mac (Telegram4Muse)",
        system_version=f"macOS {platform.mac_ver()[0]}".strip(),
        app_version=__version__,
        # We only send and read on request; no need to process every update for every chat.
        receive_updates=False,
    )
    session_file = config.session_path().with_suffix(".session")
    if session_file.exists():
        session_file.chmod(0o600)
    return client


async def login(cfg: config.Config) -> str:
    """Interactive sign-in in the terminal. Returns the account name."""
    client = make_client(cfg)
    await client.start(
        phone=lambda: input("Phone number, with country code (e.g. +14155550100): "),
        code_callback=lambda: input("Login code (Telegram sent it to your Telegram app): "),
        password=lambda: getpass("Two-step verification password: "),
    )
    try:
        return await account_name(client)
    finally:
        config.session_path().with_suffix(".session").chmod(0o600)
        await client.disconnect()


async def account_name(client: TelegramClient) -> str:
    me = await client.get_me()
    name = " ".join(p for p in (me.first_name, me.last_name) if p) or str(me.id)
    return f"{name} (@{me.username})" if me.username else name


def chat_kind(entity: Any) -> str | None:
    if isinstance(entity, types.Chat):
        return None if entity.deactivated or entity.migrated_to else "group"
    if isinstance(entity, types.Channel):
        return "supergroup" if entity.megagroup else "channel"
    return None


async def list_groups(client: TelegramClient) -> list[dict]:
    """Groups and channels you're in (archived ones too). Private chats never appear."""
    groups = []
    async for dialog in client.iter_dialogs():
        kind = chat_kind(dialog.entity)
        if kind:
            groups.append({"id": dialog.id, "title": dialog.name, "type": kind})
    return groups


def _text(value: str) -> types.TextWithEntities:
    return types.TextWithEntities(text=value, entities=[])


def build_poll(question: str, options: list[str], *, is_anonymous: bool, allows_multiple_answers: bool) -> types.InputMediaPoll:
    poll = types.Poll(
        id=random.getrandbits(62),
        question=_text(question),
        answers=[types.InputPollAnswer(text=_text(o)) for o in options],
        hash=0,
        public_voters=not is_anonymous,
        multiple_choice=allows_multiple_answers,
    )
    return types.InputMediaPoll(poll=poll)


def _person(peer: Any, users: dict, chats: dict) -> dict:
    if isinstance(peer, types.PeerUser) and peer.user_id in users:
        user = users[peer.user_id]
        name = " ".join(p for p in (user.first_name, user.last_name) if p) or "Deleted account"
        return {"id": user.id, "name": name, "username": user.username}
    chat_id = getattr(peer, "channel_id", None) or getattr(peer, "chat_id", None)
    if chat_id in chats:
        return {"id": chat_id, "name": chats[chat_id].title, "username": None}
    return {"id": None, "name": "Unknown", "username": None}


class TelethonBackend:
    """Implements jobs.TelegramActions, plus what the server needs."""

    def __init__(self, client: TelegramClient):
        self.client = client

    async def account(self) -> str | None:
        """Account name, or None if not signed in."""
        if not self.client.is_connected():
            await self.client.connect()
        if not await self.client.is_user_authorized():
            return None
        return await account_name(self.client)

    async def groups(self) -> list[dict]:
        return await list_groups(self.client)

    async def _peer(self, chat_id: int):
        try:
            return await self.client.get_input_entity(chat_id)
        except ValueError:
            # Not in the session cache yet (e.g. right after login): load the chat list once.
            await self.client.get_dialogs()
            return await self.client.get_input_entity(chat_id)

    async def _link(self, chat_id: int, message_id: int) -> str | None:
        try:
            entity = await self.client.get_entity(chat_id)
        except Exception:  # noqa: BLE001 - a link is a nice-to-have
            return None
        if not isinstance(entity, types.Channel):
            return None  # basic groups have no message links
        username = entity.username or next((u.username for u in (entity.usernames or []) if u.active), None)
        return f"https://t.me/{username}/{message_id}" if username else f"https://t.me/c/{entity.id}/{message_id}"

    async def _sent(self, chat_id: int, message: Any) -> dict:
        return {"message_id": message.id, "chat_id": chat_id, "link": await self._link(chat_id, message.id)}

    async def _poll_message(self, peer: Any, message_id: int) -> Any:
        message = await self.client.get_messages(peer, ids=message_id)
        if message is None:
            raise LookupError(f"message {message_id} not found")
        if not isinstance(message.media, types.MessageMediaPoll):
            raise ValueError(f"message {message_id} isn't a poll")
        return message

    # --- jobs.TelegramActions ---

    async def send_poll(self, chat_id, question, options, *, is_anonymous, allows_multiple_answers, silent, reply_to):
        peer = await self._peer(chat_id)
        media = build_poll(question, options, is_anonymous=is_anonymous, allows_multiple_answers=allows_multiple_answers)
        message = await self.client.send_message(peer, file=media, reply_to=reply_to, silent=silent)
        return await self._sent(chat_id, message)

    async def send_message(self, chat_id, text, *, silent, reply_to, format="plain"):
        peer = await self._peer(chat_id)
        message = await self.client.send_message(peer, text, reply_to=reply_to, silent=silent,
                                                 parse_mode=PARSE_MODES[format])
        return await self._sent(chat_id, message)

    async def _send_file(self, chat_id, file: media.File, *, caption, format, silent, reply_to, voice: bool):
        peer = await self._peer(chat_id)
        stream = io.BytesIO(file.data)
        stream.name = file.name  # Telethon picks photo vs. document from the name
        attributes = None
        if voice:
            attributes = [types.DocumentAttributeAudio(duration=file.duration or 0, voice=True)]
        message = await self.client.send_file(
            peer, stream, caption=caption or "", parse_mode=PARSE_MODES[format], reply_to=reply_to,
            silent=silent, mime_type=file.mime_type, voice_note=voice, attributes=attributes,
        )
        return await self._sent(chat_id, message)

    async def send_photo(self, chat_id, file, *, caption, format, silent, reply_to):
        return await self._send_file(chat_id, file, caption=caption, format=format, silent=silent,
                                     reply_to=reply_to, voice=False)

    async def send_voice(self, chat_id, file, *, caption, format, silent, reply_to):
        return await self._send_file(chat_id, file, caption=caption, format=format, silent=silent,
                                     reply_to=reply_to, voice=True)

    async def pin_message(self, chat_id, message_id, *, notify):
        await self.client.pin_message(await self._peer(chat_id), message_id, notify=notify)

    async def unpin_message(self, chat_id, message_id):
        await self.client.unpin_message(await self._peer(chat_id), message_id)

    async def stop_poll(self, chat_id, message_id):
        peer = await self._peer(chat_id)
        message = await self._poll_message(peer, message_id)
        poll = message.media.poll
        if poll.closed:
            return
        poll.closed = True
        await self.client(functions.messages.EditMessageRequest(peer=peer, id=message_id, media=types.InputMediaPoll(poll=poll)))

    async def poll_results(self, chat_id, message_id, *, include_voters):
        peer = await self._peer(chat_id)
        media = (await self._poll_message(peer, message_id)).media
        poll, results = media.poll, media.results
        counts = {r.option: r.voters or 0 for r in (results.results or [])}
        total = results.total_voters or 0
        voters = await self._voters(peer, message_id) if include_voters and poll.public_voters else None
        options = []
        for answer in poll.answers:
            count = counts.get(answer.option, 0)
            option = {
                "text": answer.text.text,
                "voter_count": count,
                "vote_percentage": round(100 * count / total) if total else 0,
            }
            if voters is not None:
                option["voters"] = voters.get(answer.option, [])
            options.append(option)
        return {
            "question": poll.question.text,
            "total_voter_count": total,
            "is_closed": bool(poll.closed),
            "is_anonymous": not poll.public_voters,
            "options": options,
        }

    async def _voters(self, peer: Any, message_id: int) -> dict[bytes, list[dict]]:
        by_option: dict[bytes, list[dict]] = {}
        offset = None
        seen = 0
        while seen < MAX_VOTERS:
            page = await self.client(functions.messages.GetPollVotesRequest(peer=peer, id=message_id, limit=50, offset=offset))
            users = {u.id: u for u in page.users}
            chats = {c.id: c for c in page.chats}
            for vote in page.votes:
                person = _person(vote.peer, users, chats)
                if isinstance(vote, types.MessagePeerVote):
                    chosen = [vote.option]
                elif isinstance(vote, types.MessagePeerVoteMultiple):
                    chosen = vote.options
                else:
                    chosen = []  # voted, but Telegram doesn't say for which option
                for option in chosen:
                    by_option.setdefault(option, []).append(person)
            seen += len(page.votes)
            if not page.votes or not page.next_offset:
                break
            offset = page.next_offset
        return by_option
