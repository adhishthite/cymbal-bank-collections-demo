"""Phase 2 telephony: a real outbound PSTN call through Twilio (spec §7.4 Phase 2).

The backend dials with the Calls REST API. Twilio fetches TwiML from /api/twilio/voice, which
connects a Media Stream to /ws/twilio. TwilioPhone gives LiveCall the same socket surface as the
/mobile call socket, so live_session.py is the same in both modes.
"""

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import math
import os
import random
import time
from array import array
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any
from xml.sax.saxutils import quoteattr

import audioop
import httpx
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import telemetry

API = "https://api.twilio.com/2010-04-01"
# Twilio stops the ring after this time and sends the no-answer status.
RING_TIMEOUT_S = 45
# If no Media Stream starts in this time, the backend ends the call. This covers a lost status callback.
STREAM_DEADLINE_S = 90.0
HTTP_TIMEOUT_S = 10.0
# Final call statuses before an answer. A call with one of these never had a stream.
UNANSWERED = {"no-answer", "busy", "failed", "canceled"}
# The gate opens at the latest this time after the first bot audio, or after the stream start if no audio
# went out. The greeting is one or two sentences (about 5 to 10 s), so the cap does not cut it.
# A lost signal makes the bot deaf for this time at most.
GREETING_CAP_S = 15.0
# The gate holds at most this many inbound frames (20 ms each, so 3 s) that arrive after the greeting
# audio ends and before the gate opens.
HELD_FRAMES = 150


def _lowpass(taps: int = 31, cutoff_hz: float = 3400, rate: int = 24000) -> list[float]:
    """Hamming-windowed sinc low-pass, unity gain. It removes the bot audio above the 8 kHz band."""
    mid, fc = (taps - 1) / 2, cutoff_hz / rate
    h = [
        (2 * fc if n == mid else math.sin(2 * math.pi * fc * (n - mid)) / (math.pi * (n - mid)))
        * (0.54 - 0.46 * math.cos(2 * math.pi * n / (taps - 1)))
        for n in range(taps)
    ]
    return [v / sum(h) for v in h]


LOWPASS = _lowpass()


class Downsampler:
    """24 kHz to 8 kHz PCM16. ratecv has no anti-alias filter, so sibilants would fold into the voice band.

    The FIR uses audioop.mul and audioop.add, so each tap is one C call for the whole chunk.
    """

    def __init__(self) -> None:
        self.history = bytes(2 * (len(LOWPASS) - 1))
        # The position of the next input sample in the 3-sample decimation cycle.
        self.phase = 0

    def __call__(self, pcm24k: bytes) -> bytes:
        n, keep = len(pcm24k) // 2, 2 * (len(LOWPASS) - 1)
        x = self.history + pcm24k[: 2 * n]
        # audioop.add clips each partial sum. Sum at half scale (the sum of |taps| is about 1.46),
        # and restore the scale at the end, so only a true output peak can clip.
        acc = bytes(2 * n)
        for k, h in enumerate(LOWPASS):
            start = keep - 2 * k
            acc = audioop.add(acc, audioop.mul(x[start : start + 2 * n], 2, h / 2), 2)
        self.history = x[len(x) - keep :]
        out = array("h", audioop.mul(acc, 2, 2.0))[(-self.phase) % 3 :: 3]
        self.phase = (self.phase + n) % 3
        return out.tobytes()


def signature(auth_token: str, url: str, params: list[tuple[str, str]]) -> str:
    """X-Twilio-Signature: base64(HMAC-SHA1(url + each sorted key and value)) with the Auth Token."""
    data = url + "".join(k + v for k, v in sorted(params))
    return base64.b64encode(hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()).decode()


class Twilio:
    """The Twilio account, the dial allow-list and the calls that ring but have no stream yet."""

    def __init__(
        self,
        *,
        account_sid: str,
        api_key_sid: str,
        api_key_secret: str,
        auth_token: str,
        from_number: str,
        allowed: set[str],
        phone_map: dict[str, str | list[str]],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.account_sid = account_sid
        self.auth = (api_key_sid, api_key_secret)
        self.auth_token = auth_token
        self.from_number = from_number
        self.allowed = allowed
        self.phone_map = phone_map
        self.transport = transport
        # call_id -> [Twilio call SID, stream deadline timer], from the dial to the stream start.
        # The SID is None until the dial response or the first signed webhook gives it.
        self.pending: dict[str, list[Any]] = {}

    @classmethod
    def from_env(cls) -> "Twilio | None":
        """Return None if a value is missing. Then the backend cannot switch to twilio mode."""
        names = (
            "TWILIO_ACCOUNT_SID",
            "TWILIO_API_KEY_SID",
            "TWILIO_API_KEY_SECRET",
            "TWILIO_AUTH_TOKEN",
            "TWILIO_FROM_NUMBER",
            "TWILIO_ALLOWED_NUMBERS",
            "TWILIO_PHONE_MAP",
        )
        env = {name: os.environ.get(name, "").strip() for name in names}
        if not all(env.values()):
            return None
        try:
            phone_map = json.loads(env["TWILIO_PHONE_MAP"])
        except ValueError:
            # A bad map must not stop the startup: /mobile is the fallback.
            telemetry.event("twilio_not_configured", reason="TWILIO_PHONE_MAP is not valid JSON")
            return None
        return cls(
            account_sid=env["TWILIO_ACCOUNT_SID"],
            api_key_sid=env["TWILIO_API_KEY_SID"],
            api_key_secret=env["TWILIO_API_KEY_SECRET"],
            auth_token=env["TWILIO_AUTH_TOKEN"],
            from_number=env["TWILIO_FROM_NUMBER"],
            allowed={n.strip() for n in env["TWILIO_ALLOWED_NUMBERS"].split(",") if n.strip()},
            phone_map=phone_map,
        )

    def number_for(self, loan_id: str) -> str | None:
        """D-10: dial only an allow-listed number. "*" maps all other loans. A list gives a random pick."""
        mapped = self.phone_map.get(loan_id) or self.phone_map.get("*") or []
        numbers = [n for n in ([mapped] if isinstance(mapped, str) else mapped) if n in self.allowed]
        return random.choice(numbers) if numbers else None

    def valid(self, url: str, params: list[tuple[str, str]], sig: str) -> bool:
        # Twilio's own validator also tries the URL with ":443", because Twilio does not always sign the
        # same form of the URL.
        scheme, _, rest = url.partition("://")
        host, slash, path = rest.partition("/")
        forms = {url, f"{scheme}://{host}:443{slash}{path}"} if ":" not in host else {url}
        return any(
            hmac.compare_digest(signature(self.auth_token, form, params).encode(), sig.encode())
            for form in forms
        )

    async def dial(self, to: str, call_id: str, base: str) -> str:
        """Start the outbound call. Return the Twilio call SID. Raise httpx.HTTPError on a failure."""
        async with httpx.AsyncClient(
            auth=self.auth, timeout=HTTP_TIMEOUT_S, transport=self.transport
        ) as http:
            resp = await http.post(
                f"{API}/Accounts/{self.account_sid}/Calls.json",
                data={
                    "To": to,
                    "From": self.from_number,
                    "Url": f"{base}/api/twilio/voice?call_id={call_id}",
                    "StatusCallback": f"{base}/api/twilio/status?call_id={call_id}",
                    "StatusCallbackEvent": "completed",
                    "Timeout": str(RING_TIMEOUT_S),
                },
            )
            resp.raise_for_status()
            return resp.json()["sid"]

    def ringing(self, call_id: str, on_timeout: Callable[[str, str], Awaitable[None]]) -> None:
        """Register the call before the dial. Twilio can send a webhook before the dial response."""

        async def deadline() -> None:
            await asyncio.sleep(STREAM_DEADLINE_S)
            if self.pending.pop(call_id, None) is not None:
                await on_timeout(call_id, "no_answer")

        self.pending[call_id] = [None, asyncio.create_task(deadline())]

    def claim(self, call_id: str, call_sid: Any) -> bool:
        """Bind the first SID to the call. Return True if the call rings with this SID."""
        entry = self.pending.get(call_id)
        if entry is None or not isinstance(call_sid, str) or not call_sid:
            return False
        if entry[0] is None:
            entry[0] = call_sid
        return entry[0] == call_sid

    def answered(self, call_id: str, call_sid: Any) -> bool:
        """Stop the deadline when the stream starts or the call ends. False if the call does not ring."""
        if not self.claim(call_id, call_sid):
            return False
        self.pending.pop(call_id)[1].cancel()
        return True

    def drop(self, call_id: str) -> None:
        if (entry := self.pending.pop(call_id, None)) is not None:
            entry[1].cancel()


def twiml_stream(ws_url: str, call_id: str, token: str) -> str:
    # The token goes in a Stream parameter, not in the URL, so that no log gets it.
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Connect><Stream url={quoteattr(ws_url)}>"
        f'<Parameter name="call_id" value={quoteattr(call_id)}/>'
        f'<Parameter name="token" value={quoteattr(token)}/>'
        "</Stream></Connect></Response>"
    )


TWIML_HANGUP = '<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>'


async def read_start(ws: WebSocket, timeout_s: float = 10.0) -> dict[str, Any] | None:
    """Read the Media Stream messages up to `start`, and return its `start` object."""
    try:
        async with asyncio.timeout(timeout_s):
            while True:
                msg = json.loads(await ws.receive_text())
                if msg.get("event") == "start":
                    return msg.get("start")
    except (TimeoutError, WebSocketDisconnect, ValueError, KeyError, AttributeError):
        return None


class TwilioPhone:
    """The Media Stream as a /mobile call socket: 16 kHz PCM in, 24 kHz PCM out, JSON control messages.

    Twilio sends and expects 8 kHz G.711 μ-law in base64 JSON frames.
    """

    # LiveCall sends {"type": "greeting_done"} after the first bot turn only to a phone with this flag.
    wants_greeting_done = True

    def __init__(self, ws: WebSocket, stream_sid: str, clock: Callable[[], float] = time.monotonic) -> None:
        self.ws = ws
        self.stream_sid = stream_sid
        self.clock = clock
        self.up_state: Any = None
        self.down = Downsampler()
        # Twilio streams from the answer, while LiveCall still connects to Live. The borrower's "Hello?"
        # from that time would reach Live after CALL_CONNECTED and interrupt the greeting.
        self.started = clock()
        self.skip_ms: int | None = None
        # The greeting gate. The borrower's "Hello?" during the greeting also interrupts it, so drop the
        # inbound audio until the greeting has played on the phone. Live sends audio faster than real time
        # and Twilio holds it, so the gate uses a playback clock and not the send time.
        # Live can pause in the middle of the greeting, so the playback clock alone does not show the
        # greeting end. The gate also waits for the greeting_done message from LiveCall.
        self.gate_open = False
        self.greeted = False
        self.first_out: float | None = None
        self.playback_end = 0.0
        # Borrower audio from after the end of the bot audio waits here for the gate, so the start of a
        # fast answer is not lost. More bot audio drops it, because then it was a pause in the greeting.
        self.held: deque[bytes] = deque(maxlen=HELD_FRAMES)

    def _gate(self, now: float) -> bool:
        """Return True when the inbound audio can go to Live. Open the gate once, and log it."""
        if self.gate_open:
            return True
        capped = now >= (self.started if self.first_out is None else self.first_out) + GREETING_CAP_S
        if not capped and not (self.greeted and now >= self.playback_end):
            return False
        self.gate_open = True
        telemetry.event(
            "twilio_greeting_gate_open",
            stream_sid=self.stream_sid,
            closed_ms=int((now - self.started) * 1000),
            capped=capped,
        )
        return True

    async def receive(self) -> dict[str, Any]:
        if self.skip_ms is None:
            self.skip_ms = int((self.clock() - self.started) * 1000)
        while True:
            msg = await self.ws.receive()
            if msg["type"] == "websocket.disconnect":
                return msg
            if not msg.get("text"):
                continue
            try:
                data = json.loads(msg["text"])
            except ValueError:
                continue
            event = data.get("event")
            if event == "stop":
                return {"type": "websocket.disconnect", "code": 1000}
            media = data.get("media") or {}
            if event == "media" and media.get("track", "inbound") == "inbound":
                if int(media.get("timestamp") or 0) < self.skip_ms:
                    continue
                ulaw = base64.b64decode(media["payload"])
                if not self.gate_open:
                    now = self.clock()
                    if not self._gate(now):
                        if now >= self.playback_end:
                            self.held.append(ulaw)
                        continue
                    ulaw = b"".join(self.held) + ulaw
                    self.held.clear()
                pcm8k = audioop.ulaw2lin(ulaw, 2)
                pcm16k, self.up_state = audioop.ratecv(pcm8k, 2, 1, 8000, 16000, self.up_state)
                return {"type": "websocket.receive", "bytes": pcm16k}

    async def send_bytes(self, pcm24k: bytes) -> None:
        if not self.gate_open:
            now = self.clock()
            if self.first_out is None:
                self.first_out = now
            # 24 kHz PCM16 is 48,000 bytes for each second of audio.
            self.playback_end = max(now, self.playback_end) + len(pcm24k) / 48000
            self.held.clear()
        payload = base64.b64encode(audioop.lin2ulaw(self.down(pcm24k), 2)).decode()
        await self.ws.send_text(
            json.dumps({"event": "media", "streamSid": self.stream_sid, "media": {"payload": payload}})
        )

    async def send_json(self, message: dict[str, Any]) -> None:
        # A phone call has no screen. Barge-in drops the audio that Twilio holds. The call end hangs up
        # at once, so the line is not silent while LiveCall writes the summary.
        if message.get("type") == "interrupted":
            # The clear stops the playback now, so the playback clock ends now at the latest.
            self.playback_end = min(self.playback_end, self.clock())
            await self.ws.send_text(json.dumps({"event": "clear", "streamSid": self.stream_sid}))
        elif message.get("type") == "greeting_done":
            self.greeted = True
        elif message.get("type") == "call_ended":
            await self.close()

    async def close(self, code: int = 1000) -> None:
        # With <Connect><Stream>, a closed stream ends the TwiML, so Twilio hangs up the call.
        with contextlib.suppress(RuntimeError, OSError):
            await self.ws.close(code=code)
