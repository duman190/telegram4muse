"""The Telethon glue, against a fake client. Every request we build is
serialized with bytes(), which fails if it doesn't match Telegram's schema."""

import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace

from telethon import TelegramClient
from telethon.sessions import MemorySession
from telethon.tl import functions, types

from telegram4muse import media, telegram

MEDIA = Path(__file__).parent / "media"

CHAT = -1001234567890
PEER = types.InputPeerChannel(channel_id=1234567890, access_hash=42)


def text(value):
    return types.TextWithEntities(text=value, entities=[])


def poll_message(message_id=55, closed=False, public=True):
    poll = types.Poll(
        id=999, question=text("Surf Saturday?"), hash=7, closed=closed, public_voters=public,
        answers=[types.PollAnswer(text=text("Dawn"), option=b"0"),
                 types.PollAnswer(text=text("Midday"), option=b"1"),
                 types.PollAnswer(text=text("Out"), option=b"2")],
    )
    results = types.PollResults(total_voters=3, results=[
        types.PollAnswerVoters(option=b"0", voters=2),
        types.PollAnswerVoters(option=b"1", voters=1),
    ])
    return SimpleNamespace(id=message_id, media=types.MessageMediaPoll(poll=poll, results=results))


class FakeClient:
    def __init__(self, cached=True):
        self.cached = cached
        self.calls = []
        self.next_id = 100
        self.message = poll_message()

    async def get_input_entity(self, chat_id):
        assert chat_id == CHAT
        if not self.cached:
            raise ValueError("Could not find the input entity")
        return PEER

    async def get_dialogs(self):
        self.calls.append(("get_dialogs",))
        self.cached = True

    async def get_entity(self, chat_id):
        return types.Channel(id=1234567890, title="Surf Crew", photo=types.ChatPhotoEmpty(), date=None,
                             megagroup=True, access_hash=42)

    async def send_message(self, peer, message="", *, file=None, reply_to=None, silent=None, parse_mode=()):
        if file is not None:
            bytes(file)
        self.calls.append(("send_message", peer, message, file, reply_to, silent, parse_mode))
        self.next_id += 1
        return SimpleNamespace(id=self.next_id)

    async def pin_message(self, peer, message_id, *, notify=False):
        self.calls.append(("pin_message", peer, message_id, notify))

    async def unpin_message(self, peer, message_id):
        self.calls.append(("unpin_message", peer, message_id))

    async def get_messages(self, peer, ids):
        self.calls.append(("get_messages", peer, ids))
        return self.message

    async def __call__(self, request):
        bytes(request)
        self.calls.append((type(request).__name__, request))
        if isinstance(request, functions.messages.GetPollVotesRequest):
            return types.messages.VotesList(
                count=3,
                votes=[
                    types.MessagePeerVote(peer=types.PeerUser(11), option=b"0", date=None),
                    types.MessagePeerVoteMultiple(peer=types.PeerUser(12), options=[b"0", b"1"], date=None),
                    types.MessagePeerVoteInputOption(peer=types.PeerUser(13), date=None),
                ],
                chats=[],
                users=[types.User(id=11, first_name="Kai", last_name="Moana", username="kai"),
                       types.User(id=12, first_name="Lena"),
                       types.User(id=13, first_name="Sam")],
                next_offset=None,
            )
        return None


def run(coro):
    return asyncio.run(coro)


def test_build_poll_serializes_with_new_style_answers():
    media = telegram.build_poll("Surf Saturday?", ["Dawn", "Out"], is_anonymous=False, allows_multiple_answers=True)
    assert bytes(media)
    assert media.poll.public_voters is True
    assert media.poll.multiple_choice is True
    assert [a.text.text for a in media.poll.answers] == ["Dawn", "Out"]
    assert all(isinstance(a, types.InputPollAnswer) for a in media.poll.answers)


def test_send_poll_loads_dialogs_once_if_needed_and_returns_a_link():
    client = FakeClient(cached=False)
    backend = telegram.TelethonBackend(client)
    result = run(backend.send_poll(CHAT, "Surf Saturday?", ["Dawn", "Out"], is_anonymous=False,
                                   allows_multiple_answers=False, silent=True, reply_to=None))
    assert result == {"message_id": 101, "chat_id": CHAT, "link": "https://t.me/c/1234567890/101"}
    assert client.calls[0] == ("get_dialogs",)
    name, peer, message, file, reply_to, silent, _ = client.calls[1]
    assert (name, peer, message, reply_to, silent) == ("send_message", PEER, "", None, True)
    assert isinstance(file, types.InputMediaPoll)


def test_send_message_is_plain_text_unless_asked():
    client = FakeClient()
    backend = telegram.TelethonBackend(client)
    run(backend.send_message(CHAT, "4-5ft *offshore*", silent=False, reply_to=101, format="plain"))
    run(backend.send_message(CHAT, "**4-5ft**", silent=False, reply_to=None, format="markdown"))
    assert client.calls[0][1:] == (PEER, "4-5ft *offshore*", None, 101, False, None)
    assert client.calls[1][-1] == "md"


def test_pin_and_unpin():
    client = FakeClient()
    backend = telegram.TelethonBackend(client)
    run(backend.pin_message(CHAT, 101, notify=False))
    run(backend.unpin_message(CHAT, 90))
    assert client.calls == [("pin_message", PEER, 101, False), ("unpin_message", PEER, 90)]


def test_stop_poll_edits_the_poll_closed_and_skips_closed_ones():
    client = FakeClient()
    backend = telegram.TelethonBackend(client)
    run(backend.stop_poll(CHAT, 55))
    name, request = client.calls[-1]
    assert name == "EditMessageRequest"
    assert request.id == 55
    assert request.media.poll.closed is True
    assert request.media.poll.id == 999

    client = FakeClient()
    client.message = poll_message(closed=True)
    run(telegram.TelethonBackend(client).stop_poll(CHAT, 55))
    assert [c[0] for c in client.calls] == ["get_messages"]


def test_poll_results_with_voters():
    client = FakeClient()
    results = run(telegram.TelethonBackend(client).poll_results(CHAT, 55, include_voters=True))
    assert results["question"] == "Surf Saturday?"
    assert results["total_voter_count"] == 3
    assert results["is_anonymous"] is False
    assert [(o["text"], o["voter_count"], o["vote_percentage"]) for o in results["options"]] == [
        ("Dawn", 2, 67), ("Midday", 1, 33), ("Out", 0, 0)]
    assert [v["name"] for v in results["options"][0]["voters"]] == ["Kai Moana", "Lena"]
    assert results["options"][0]["voters"][0]["username"] == "kai"
    assert [v["name"] for v in results["options"][1]["voters"]] == ["Lena"]
    assert results["options"][2]["voters"] == []


def test_anonymous_poll_results_have_no_voters():
    client = FakeClient()
    client.message = poll_message(public=False)
    results = run(telegram.TelethonBackend(client).poll_results(CHAT, 55, include_voters=True))
    assert "voters" not in results["options"][0]
    assert not any(c[0] == "GetPollVotesRequest" for c in client.calls)


def test_not_a_poll():
    client = FakeClient()
    client.message = SimpleNamespace(id=55, media=None)
    try:
        run(telegram.TelethonBackend(client).poll_results(CHAT, 55, include_voters=True))
    except ValueError as e:
        assert "isn't a poll" in str(e)
    else:
        raise AssertionError("expected an error")


def test_group_kinds():
    assert telegram.chat_kind(types.Chat(id=1, title="g", photo=types.ChatPhotoEmpty(), participants_count=3,
                                         date=None, version=1)) == "group"
    assert telegram.chat_kind(types.Channel(id=2, title="s", photo=types.ChatPhotoEmpty(), date=None,
                                            megagroup=True)) == "supergroup"
    assert telegram.chat_kind(types.Channel(id=3, title="c", photo=types.ChatPhotoEmpty(), date=None,
                                            broadcast=True)) == "channel"
    assert telegram.chat_kind(types.User(id=4, first_name="Mom")) is None


def prepared(kind, name):
    return media.prepare(kind, base64.b64encode((MEDIA / name).read_bytes()).decode())


class UploadProbe(TelegramClient):
    """Real Telethon, with the network cut off right before the message is sent."""

    def __init__(self):
        super().__init__(MemorySession(), 1, "0" * 32)
        self.requests = []

    async def _call(self, sender, request, ordered=False, flood_sleep_threshold=None):
        bytes(request)
        self.requests.append(request)
        if isinstance(request, functions.upload.SaveFilePartRequest):
            return True
        raise ConnectionError("not really sending")

    async def get_input_entity(self, peer):
        return PEER


def sent_media_request(kind, name, **kwargs):
    async def go():
        # Created inside the loop: on Python 3.9 Telethon needs one at construction.
        client = UploadProbe()
        backend = telegram.TelethonBackend(client)
        send = backend.send_photo if kind == "photo" else backend.send_voice
        try:
            await send(CHAT, prepared(kind, name), **kwargs)
        except ConnectionError:
            pass
        return client

    client = run(go())
    assert any(isinstance(r, functions.upload.SaveFilePartRequest) for r in client.requests)
    request = client.requests[-1]
    assert isinstance(request, functions.messages.SendMediaRequest)
    return request


def test_photo_goes_out_as_a_photo_with_a_formatted_caption():
    request = sent_media_request("photo", "forecast.jpg", caption="**Sat** 4-5ft", format="markdown",
                                 silent=True, reply_to=7)
    assert isinstance(request.media, types.InputMediaUploadedPhoto)
    assert request.message == "Sat 4-5ft"
    assert [type(e).__name__ for e in request.entities] == ["MessageEntityBold"]
    assert request.reply_to.reply_to_msg_id == 7
    assert request.silent is True


def test_voice_goes_out_as_a_voice_note_with_its_length():
    for name, mime in [("voice.ogg", "audio/ogg"), ("voice.mp3", "audio/mpeg"), ("voice.m4a", "audio/mp4")]:
        request = sent_media_request("voice", name, caption=None, format="plain", silent=False, reply_to=None)
        assert isinstance(request.media, types.InputMediaUploadedDocument)
        assert request.media.mime_type == mime
        audio = [a for a in request.media.attributes if isinstance(a, types.DocumentAttributeAudio)]
        assert len(audio) == 1 and audio[0].voice is True and audio[0].duration == 3
        assert request.message == ""
