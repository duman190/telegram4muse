"""A stand-in for Telegram that records what it was asked to do."""

from __future__ import annotations

import asyncio


class FakeTelegram:
    def __init__(self, account: str | None = "Dmitrii (@duman)"):
        self.calls: list[tuple] = []
        self.next_id = 100
        self.fail_on: str | None = None
        self.hang_on: str | None = None
        self.account_name = account
        self.all_groups = [
            {"id": -1001234567890, "title": "Surf Crew", "type": "supergroup"},
            {"id": -1009, "title": "Family", "type": "group"},
        ]

    async def _record(self, action: str, *details) -> None:
        self.calls.append((action, *details))
        if self.hang_on == action:
            await asyncio.sleep(3600)
        if self.fail_on == action:
            raise RuntimeError("Chat admin privileges are required")

    def _new(self, chat_id: int) -> dict:
        self.next_id += 1
        return {"message_id": self.next_id, "chat_id": chat_id, "link": f"https://t.me/c/1234567890/{self.next_id}"}

    async def account(self):
        return self.account_name

    async def groups(self):
        return self.all_groups

    async def send_poll(self, chat_id, question, options, *, is_anonymous, allows_multiple_answers, silent, reply_to):
        await self._record("send_poll", chat_id, question, tuple(options), is_anonymous, allows_multiple_answers, reply_to)
        return self._new(chat_id)

    async def send_message(self, chat_id, text, *, silent, reply_to, format):
        await self._record("send_message", chat_id, text, reply_to, silent, format)
        return self._new(chat_id)

    async def pin_message(self, chat_id, message_id, *, notify):
        await self._record("pin_message", chat_id, message_id, notify)

    async def unpin_message(self, chat_id, message_id):
        await self._record("unpin_message", chat_id, message_id)

    async def stop_poll(self, chat_id, message_id):
        await self._record("stop_poll", chat_id, message_id)

    async def poll_results(self, chat_id, message_id, *, include_voters):
        await self._record("get_poll_results", chat_id, message_id, include_voters)
        return {"question": "Surf Saturday?", "total_voter_count": 1, "is_closed": False, "is_anonymous": False,
                "options": [{"text": "Dawn", "voter_count": 1, "vote_percentage": 100,
                             "voters": [{"id": 11, "name": "Kai", "username": "kai"}]}]}
