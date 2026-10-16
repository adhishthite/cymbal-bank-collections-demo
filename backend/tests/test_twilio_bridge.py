"""Twilio mode end to end: the mode switch, the allow-list, the signed webhooks and a call over the
Media Stream socket, with a mocked Twilio REST API and a scripted Live model (spec §7.4 Phase 2).
"""

import asyncio
import base64
import json
import math
import re
import time
from array import array

import audioop
import httpx
import pytest
from conftest import TOKEN
from fake_live import FakeLive, fake_summary
from fastapi.testclient import TestClient
from google.genai import types
from starlette.websockets import WebSocketDisconnect

from muthoot_collections import twilio_bridge
from muthoot_collections.main import create_app
from muthoot_collections.twilio_bridge import Twilio, TwilioPhone, signature

KAVITA, SAJEEV = "GL3506468235", "GL0106397512"
PRESENTERS, STRANGER = ["+919800000001", "+919800000003"], "+919800000002"
AUTH_TOKEN = "test-auth-token"


@pytest.fixture
def twilio_api(store, monkeypatch):
    monkeypatch.setenv("DEMO_AGENT_TOKEN", TOKEN)
    monkeypatch.setenv("VIEW_TOKEN_KEY", "test-view-key")
    dials: list[dict] = []
    # The next REST outcomes: an exception class or an HTTP status. Empty means 201.
    replies: list = []

    def rest(request: httpx.Request) -> httpx.Response:
        dials.append(dict(httpx.QueryParams(request.content.decode())))
        reply = replies.pop(0) if replies else 201
        if isinstance(reply, type):
            raise reply("no response", request=request)
        return httpx.Response(reply, json={"sid": f"CA{len(dials)}"})

    twilio = Twilio(
        account_sid="AC1",
        api_key_sid="SK1",
        api_key_secret="secret",
        auth_token=AUTH_TOKEN,
        from_number="+19800000000",
        allowed=set(PRESENTERS),
        phone_map={SAJEEV: STRANGER, "*": PRESENTERS},
        transport=httpx.MockTransport(rest),
    )
    app = create_app(store, connect=FakeLive(), summarize=fake_summary, twilio=twilio)
    with TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        client.dials, client.replies = dials, replies
        yield client


def webhook(api, path: str, form: dict[str, str], sign: bool = True, host: str = "testserver"):
    url = f"http://{host}{path}"
    sig = signature(AUTH_TOKEN, url, list(form.items())) if sign else "forged"
    return api.post(path, data=form, headers={"X-Twilio-Signature": sig})


def mulaw(pcm: bytes) -> str:
    return base64.b64encode(audioop.lin2ulaw(pcm, 2)).decode()


GREETING_S = 0.3


async def greet_then_full_payment(s):
    # The bot speaks first. Live sends the greeting audio faster than real time.
    greeting = types.Part(
        inline_data=types.Blob(data=b"\x00\x00" * int(24000 * GREETING_S), mime_type="audio/pcm")
    )
    s._content(model_turn=types.Content(role="model", parts=[greeting]))
    s._content(turn_complete=True)
    # The test checks this audio after the call. If the gate drops the answer, the script goes on with
    # None, so the test fails and does not hang.
    try:
        s.first_audio = await asyncio.wait_for(s.audio_in.get(), 5)
    except TimeoutError:
        s.first_audio = None
    s.hear("I will pay it today.")
    await s.call("generate_upi_payment_link", loan_id=KAVITA, amount_inr=8612, settlement_type="FULL_DUE")
    s.interrupt()
    s.say("I have sent the link for the full amount.")


def test_twilio_call(twilio_api, monkeypatch):
    api = twilio_api
    # /mobile is the default mode. The switch to twilio and back needs the admin token.
    assert api.get("/api/admin/telephony").json() == {"ok": True, "mode": "mobile", "twilio_configured": True}
    assert api.post("/api/admin/telephony", json={"mode": "twilio"}).json()["mode"] == "twilio"

    # D-10: a number that is not on the allow-list is rejected before Twilio and before the call lock.
    r = api.post("/api/agent/calls", json={"loan_id": SAJEEV})
    assert (r.status_code, r.json()["error"]) == (409, "No allow-listed phone number for this account.")
    assert api.dials == []

    api.app.state.connect.script = greet_then_full_payment
    r = api.post("/api/agent/calls", json={"loan_id": KAVITA})
    assert r.status_code == 200 and r.json()["mode"] == "twilio"
    call_id = r.json()["call_id"]
    dial = api.dials[0]
    assert dial["To"] in PRESENTERS and (dial["From"], dial["Timeout"]) == ("+19800000000", "45")
    assert dial["Url"] == f"http://testserver/api/twilio/voice?call_id={call_id}"

    # The TwiML webhook needs a good signature and the SID of the call that the backend dialed.
    voice = f"/api/twilio/voice?call_id={call_id}"
    assert webhook(api, voice, {"CallSid": "CA1"}, sign=False).status_code == 403
    assert "<Hangup/>" in webhook(api, voice, {"CallSid": "CA9"}).text
    # Twilio can sign the URL with the default port in it.
    twiml = webhook(api, voice, {"CallSid": "CA1"}, host="testserver:443").text
    assert '<Stream url="wss://testserver/ws/twilio">' in twiml
    token = re.search(r'name="token" value="([^"]+)"', twiml).group(1)

    def start(ws, call_sid="CA1", stream_token=token):
        ws.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
        ws.send_json(
            {
                "event": "start",
                "streamSid": "MZ1",
                "start": {
                    "streamSid": "MZ1",
                    "callSid": call_sid,
                    "customParameters": {"call_id": call_id, "token": stream_token},
                },
            }
        )

    # A stream with a forged token gets 4403, and the call still waits for the real stream.
    with api.websocket_connect("/ws/twilio") as ws:
        start(ws, stream_token="forged.token")
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 4403

    events = []
    with api.websocket_connect("/ws/twilio") as ws:
        start(ws)

        def speak(level: int) -> None:
            frame = mulaw(array("h", [level] * 160).tobytes())
            ws.send_json(
                {
                    "event": "media",
                    "streamSid": "MZ1",
                    "media": {"track": "inbound", "timestamp": "60000", "payload": frame},
                }
            )

        greeting = ws.receive_json()
        assert len(base64.b64decode(greeting["media"]["payload"])) == int(8000 * GREETING_S)
        speak(8000)  # "Hello?" while the greeting plays
        time.sleep(GREETING_S + 0.1)
        speak(0)
        while (msg := ws.receive_json())["event"] != "media":
            events.append(msg)
        # 20 ms of 24 kHz bot audio goes to the phone as 20 ms of 8 kHz μ-law.
        assert msg["streamSid"] == "MZ1" and abs(len(base64.b64decode(msg["media"]["payload"])) - 160) <= 2
        ws.send_json({"event": "stop", "streamSid": "MZ1", "stop": {"callSid": "CA1"}})
        with pytest.raises(WebSocketDisconnect):
            while True:
                ws.receive_json()
    # The gate dropped the loud "Hello?" from the greeting. The quiet answer after it reached Live as
    # 20 ms of 16 kHz PCM.
    audio = api.app.state.connect.sessions[-1].first_audio
    assert audio is not None and abs(len(audio) - 640) <= 4 and audioop.max(audio, 2) < 100
    # Barge-in clears the audio that Twilio holds. The phone gets no JSON cards.
    assert events == [{"event": "clear", "streamSid": "MZ1"}]
    account = api.get(f"/api/agent/accounts/{KAVITA}").json()
    assert account["status"] == "PAYMENT_LINK_SENT_TODAY"
    assert api.get(f"/api/agent/calls/{call_id}").json()["end_reason"] == "phone_closed"

    # An unanswered call ends from the status callback with NO_OUTCOME. Then the account is callable again.
    api.app.state.connect.script = None
    api.post("/api/admin/reset")
    missed = api.post("/api/agent/calls", json={"loan_id": KAVITA}).json()["call_id"]
    status = f"/api/twilio/status?call_id={missed}"
    assert webhook(api, status, {"CallSid": "CA2", "CallStatus": "no-answer"}).status_code == 204
    deadline = time.monotonic() + 5
    while api.get(f"/api/agent/accounts/{KAVITA}").json()["status"] != "NO_OUTCOME":
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert api.get(f"/api/agent/calls/{missed}").json()["end_reason"] == "no_answer"

    # No REST response, or a 5xx: Twilio can still have placed the call, so the call waits for the
    # webhooks. With no webhook at all, the stream deadline ends it.
    for reply, sid in ((httpx.ReadTimeout, "CA3"), (500, "CA4")):
        api.replies.append(reply)
        unconfirmed = api.post("/api/agent/calls", json={"loan_id": KAVITA})
        assert unconfirmed.status_code == 200
        unconfirmed = unconfirmed.json()["call_id"]
        assert "<Stream" in webhook(api, f"/api/twilio/voice?call_id={unconfirmed}", {"CallSid": sid}).text
        webhook(api, f"/api/twilio/status?call_id={unconfirmed}", {"CallSid": sid, "CallStatus": "completed"})
        assert api.get(f"/api/agent/calls/{unconfirmed}").json()["end_reason"] == "no_stream"
    monkeypatch.setattr(twilio_bridge, "STREAM_DEADLINE_S", 0.2)
    lost = api.post("/api/agent/calls", json={"loan_id": KAVITA}).json()["call_id"]
    deadline = time.monotonic() + 5
    while api.get(f"/api/agent/calls/{lost}").json()["end_reason"] != "no_answer":
        assert time.monotonic() < deadline
        time.sleep(0.01)

    # A rejected dial ends the call at once with NO_OUTCOME.
    api.replies.append(400)
    rejected = api.post("/api/agent/calls", json={"loan_id": KAVITA})
    assert rejected.status_code == 502
    assert api.get(f"/api/agent/calls/{rejected.json()['call_id']}").json()["end_reason"] == "dial_failed"
    assert api.get(f"/api/agent/accounts/{KAVITA}").json()["status"] == "NO_OUTCOME"

    # Back to /mobile: the fallback path works again at once, with no redeploy.
    api.post("/api/admin/telephony", json={"mode": "mobile"})
    assert (
        api.post("/api/agent/calls", json={"loan_id": KAVITA}).json()["error"]
        == "No /mobile phone is paired."
    )


def test_twilio_mode_needs_config(app_client):
    r = app_client.post("/api/admin/telephony", json={"mode": "twilio"})
    assert (r.status_code, r.json()["error"]) == (409, "Twilio is not configured.")
    assert app_client.post("/api/twilio/voice", data={"CallSid": "CA1"}).status_code == 403


class SentFrames:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


def test_bot_audio_reaches_the_phone_without_aliasing():
    """Speech-band audio passes. A 6 kHz sibilant would fold to 2 kHz at 8 kHz, so it must be removed."""

    async def phone_rms(hz: int, amplitude: int = 8000) -> tuple[int, int, int]:
        ws = SentFrames()
        phone = TwilioPhone(ws, "MZ1")
        pcm = array(
            "h", (int(amplitude * math.sin(2 * math.pi * hz * i / 24000)) for i in range(4800))
        ).tobytes()
        for i in range(0, len(pcm), 960):  # 20 ms chunks, as Live sends them
            await phone.send_bytes(pcm[i : i + 960])
        out = b"".join(audioop.ulaw2lin(base64.b64decode(m["media"]["payload"]), 2) for m in ws.sent)
        return len(out) // 2, audioop.rms(out[200:], 2), audioop.max(out[200:], 2)  # skip the warm-up

    samples, speech, _ = asyncio.run(phone_rms(1000))
    _, sibilant, _ = asyncio.run(phone_rms(6000))
    assert samples == 1600  # 200 ms at 8 kHz
    assert speech > 5000 and sibilant < 300
    # A loud speech tone keeps its peak: the filter must not clip inside its sum.
    assert asyncio.run(phone_rms(300, amplitude=31000))[2] >= 31000


class PhoneSocket(SentFrames):
    """A Media Stream socket on a fake clock. Each inbound frame sets the clock to its arrival time."""

    def __init__(self) -> None:
        super().__init__()
        self.now = 0.0
        self.inbox: list[tuple[float, int]] = []

    async def receive(self) -> dict:
        if not self.inbox:
            return {"type": "websocket.disconnect", "code": 1000}
        self.now, sample = self.inbox.pop(0)
        frame = {
            "track": "inbound",
            "timestamp": str(int(self.now * 1000)),
            "payload": mulaw(array("h", [sample] * 160).tobytes()),
        }
        return {"type": "websocket.receive", "text": json.dumps({"event": "media", "media": frame})}


def test_greeting_gate(caplog):
    """The borrower's "Hello?" during the greeting does not reach Live. Speech after the greeting does."""
    second = b"\x00\x00" * 24000  # 1 s of 24 kHz bot audio

    async def heard(phone: TwilioPhone, ws: PhoneSocket, *frames: tuple[float, int]) -> list[int] | None:
        """Return the level of each 20 ms frame in the first message to Live, or None if no frame passes."""
        ws.inbox.extend(frames)
        msg = await phone.receive()
        return [round(v, -3) for v in array("h", msg["bytes"])[160::320]] if "bytes" in msg else None

    async def call() -> None:
        ws = PhoneSocket()
        phone = TwilioPhone(ws, "MZ1", clock=lambda: ws.now)
        ws.now = 0.3  # Live connects. The skip_ms rule drops the audio from before this time.
        assert await heard(phone, ws, (0.5, 1000)) is None  # "Hello?" before the greeting starts
        # Live sends 1 s of greeting at 0.6 s, faster than real time, and then pauses for 1.9 s.
        ws.now = 0.6
        await phone.send_bytes(second)
        assert await heard(phone, ws, (1.0, 2000), (2.0, 3000), (2.4, 4000)) is None
        # The rest of the greeting plays from 2.5 s to 4.5 s. The audio from the pause is dropped.
        ws.now = 2.5
        await phone.send_bytes(second * 2)
        assert await heard(phone, ws, (3.0, 5000), (4.4, 5000), (4.6, 6000), (4.7, 7000)) is None
        # The turn end comes after the greeting has played. The answer from 4.6 s reaches Live in order.
        await phone.send_json({"type": "greeting_done"})
        assert await heard(phone, ws, (4.8, 8000)) == [6000, 7000, 8000]
        # The gate stays open for the rest of the call, also while the bot speaks.
        await phone.send_bytes(second)
        assert await heard(phone, ws, (4.9, 1000)) == [1000]

        # A barge-in that Live detects clears the phone, so the playback clock ends at the clear.
        ws = PhoneSocket()
        phone = TwilioPhone(ws, "MZ2", clock=lambda: ws.now)
        await phone.receive()
        await phone.send_bytes(second * 10)
        ws.now = 2.0
        await phone.send_json({"type": "interrupted"})
        await phone.send_json({"type": "greeting_done"})
        assert ws.sent[-1] == {"event": "clear", "streamSid": "MZ2"}
        assert await heard(phone, ws, (2.4, 1000)) == [1000]

        # The cap: with no turn end, the gate opens 15 s after the first bot audio.
        ws = PhoneSocket()
        phone = TwilioPhone(ws, "MZ3", clock=lambda: ws.now)
        await phone.receive()
        await phone.send_bytes(second * 60)
        assert await heard(phone, ws, (14.9, 1000), (15.0, 2000)) == [2000]

        # No bot audio at all: the cap counts from the stream start.
        ws = PhoneSocket()
        phone = TwilioPhone(ws, "MZ4", clock=lambda: ws.now)
        assert await heard(phone, ws, (14.9, 1000), (15.0, 2000)) == [1000, 2000]

    with caplog.at_level("INFO"):
        asyncio.run(call())
    opened = [r.fields for r in caplog.records if r.getMessage() == "twilio_greeting_gate_open"]
    assert opened == [
        {"stream_sid": "MZ1", "closed_ms": 4800, "capped": False},
        {"stream_sid": "MZ2", "closed_ms": 2400, "capped": False},
        {"stream_sid": "MZ3", "closed_ms": 15000, "capped": True},
        {"stream_sid": "MZ4", "closed_ms": 15000, "capped": True},
    ]
