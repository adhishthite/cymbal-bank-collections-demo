"""Phase 1 telephony: HMAC tokens, one-time pairing, the /ws/mobile control socket and the ring.

Spec §7.4 "Mobile and Monitor Wire Protocol (M2)". The HMAC key comes from env VIEW_TOKEN_KEY.
On Cloud Run, the Secret Manager secret mcd5-view-token-key gives the value.
"""

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect

from . import telemetry

TOKEN_TTL = timedelta(hours=24)
# A device credential lasts through a rehearsal day and the demo day.
DEVICE_TTL = timedelta(hours=72)
RING_TIMEOUT_S = 45.0
# A stalled phone must not block the call end. Each send and close has its own limit.
SEND_TIMEOUT_S = 3.0
CALLER = "Cymbal Bank — AI Collections Desk"


# Two base64url parts. Other text (non-ASCII too) is a normal auth failure.
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Tokens:
    """Signed tokens: base64url(JSON payload) "." base64url(HMAC-SHA256 of the payload)."""

    def __init__(self, key: str, now: Callable[[], datetime]) -> None:
        self.key = key.encode()
        self.now = now

    def mint(self, kind: str, subject: str, ttl: timedelta | None) -> tuple[str, datetime | None]:
        expires = self.now() + ttl if ttl else None
        payload = _b64(
            json.dumps(
                {"k": kind, "s": subject, "exp": int(expires.timestamp()) if expires else None},
                separators=(",", ":"),
            ).encode()
        )
        return f"{payload}.{self._sign(payload)}", expires

    def check(self, token: Any, kind: str) -> str | None:
        """Return the subject of a good token of this kind, else None."""
        if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
            return None
        payload, sig = token.split(".")
        # Compare bytes: compare_digest raises TypeError on non-ASCII str.
        if not hmac.compare_digest(sig.encode(), self._sign(payload).encode()):
            return None
        try:
            data = json.loads(_unb64(payload))
        except ValueError:
            return None
        if data.get("k") != kind:
            return None
        if data.get("exp") is not None and data["exp"] <= self.now().timestamp():
            return None
        return data.get("s")

    def _sign(self, payload: str) -> str:
        return _b64(hmac.new(self.key, payload.encode(), hashlib.sha256).digest())


async def send_json(ws: Any, message: dict[str, Any]) -> bool:
    """Send to a phone socket. The phone can go away at any time, so a failed send is a result."""
    try:
        async with asyncio.timeout(SEND_TIMEOUT_S):
            await ws.send_json(message)
        return True
    except (WebSocketDisconnect, RuntimeError, OSError, TimeoutError):
        return False


async def close_socket(ws: Any, code: int = 1000) -> None:
    with contextlib.suppress(WebSocketDisconnect, RuntimeError, OSError, TimeoutError):
        async with asyncio.timeout(SEND_TIMEOUT_S):
            await ws.close(code=code)


async def read_auth(ws: WebSocket, timeout_s: float = 10.0) -> Any:
    """Read the first socket message, {"type": "auth", "device": ...}, and return the credential."""
    try:
        async with asyncio.timeout(timeout_s):
            msg = await ws.receive_json()
    except (TimeoutError, WebSocketDisconnect, ValueError, KeyError):
        return None
    return msg.get("device") if isinstance(msg, dict) and msg.get("type") == "auth" else None


class MobilePhone:
    """The single paired /mobile phone.

    A new pairing replaces the earlier device. The trigger needs a connected control socket,
    not only a paired credential, because a ring needs a live socket.
    """

    def __init__(self, tokens: Tokens) -> None:
        self.tokens = tokens
        # In memory only. Thus a used pair link can work once more after a restart.
        self.used_pairs: set[str] = set()
        self.device_id: str | None = None
        self.control: Any = None
        # All open control sockets, oldest first. A second /mobile tab must not take the phone offline.
        self.controls: list[Any] = []
        # Each authenticated socket (control or call) -> its device. Pairing closes the old ones.
        self.sockets: dict[Any, str] = {}
        # call_id -> (no-answer timer, incoming_call message), while the phone rings.
        self.ringing: dict[str, tuple[asyncio.Task, dict[str, Any]]] = {}
        self.ring_timeout_s = RING_TIMEOUT_S

    def pair_link_token(self) -> tuple[str, datetime | None]:
        return self.tokens.mint("pair", secrets.token_urlsafe(12), TOKEN_TTL)

    async def pair(self, token: Any) -> str | None:
        nonce = self.tokens.check(token, "pair")
        if nonce is None or nonce in self.used_pairs:
            return None
        self.used_pairs.add(nonce)
        self.device_id = secrets.token_urlsafe(12)
        # The next pairing revokes the earlier credential. DEVICE_TTL also limits it.
        credential, _ = self.tokens.mint("device", self.device_id, DEVICE_TTL)
        self.control = None
        # Revoke the old device: close its control sockets and its call socket, which ends the call.
        for ws in [ws for ws, device in self.sockets.items() if device != self.device_id]:
            self.sockets.pop(ws, None)
            await close_socket(ws, 4401)
        telemetry.event("mobile_paired")
        return credential

    def device(self, credential: Any) -> str | None:
        """Return the device ID if the credential is the current pairing, else None."""
        device_id = self.tokens.check(credential, "device")
        if device_id is None:
            return None
        # Trade-off: after a restart (scale to zero), the first unexpired credential becomes the pairing.
        # Thus a replaced phone can take over until DEVICE_TTL ends, but no re-pair is necessary.
        if self.device_id is None:
            self.device_id = device_id
        return device_id if device_id == self.device_id else None

    async def push(self, message: dict[str, Any]) -> bool:
        if self.control is None:
            return False
        targets = [c for c in self.controls if self.sockets.get(c) == self.device_id] or [self.control]
        results = [await send_json(ws, message) for ws in targets]
        return any(results)

    async def ring(
        self,
        call_id: str,
        message: dict[str, Any],
        on_no_answer: Callable[[str, str], Awaitable[None]],
    ) -> None:
        async def timeout() -> None:
            await asyncio.sleep(self.ring_timeout_s)
            if self.ringing.pop(call_id, None) is not None:
                await on_no_answer(call_id, "no_answer")

        self.ringing[call_id] = (asyncio.create_task(timeout()), message)
        # If this push fails, the phone gets the ring when its control socket reconnects.
        await self.push(message)

    def ring_messages(self) -> list[dict[str, Any]]:
        return [message for _, message in self.ringing.values()]

    def answer(self, call_id: str) -> bool:
        """Stop the ring. Return False if the call does not ring (not started, or answered)."""
        ring = self.ringing.pop(call_id, None)
        if ring is None:
            return False
        ring[0].cancel()
        return True
