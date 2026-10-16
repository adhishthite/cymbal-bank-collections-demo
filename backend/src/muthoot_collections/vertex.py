"""Google credentials and Vertex AI clients. The credentials refresh off the event loop.

google-genai 2.28.0 loads and refreshes ADC synchronously in Client() and in live.connect(). A
refresh is a blocking HTTP call. Thus this module refreshes in a thread before each use. With a
valid token, the SDK does no refresh of its own.
"""

import asyncio
import os

import google.auth
from google import genai
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

_creds: Credentials | None = None
_project: str | None = None
_clients: dict[str, genai.Client] = {}
_lock: asyncio.Lock | None = None


def reset() -> None:
    """Forget the clients and the lock. Tests call this before each new event loop."""
    global _lock
    _clients.clear()
    _lock = None


async def credentials() -> Credentials:
    """ADC with a valid token. The load and the refresh run in a thread."""
    global _creds, _project, _lock
    _lock = _lock or asyncio.Lock()
    # One load or refresh at a time.
    async with _lock:
        if _creds is None:
            creds, adc_project = await asyncio.to_thread(google.auth.default, scopes=SCOPES)
            await asyncio.to_thread(creds.refresh, Request())
            _creds, _project = creds, os.environ.get("GOOGLE_CLOUD_PROJECT") or adc_project
        # `valid` is False when the token is missing or near its expiry.
        elif not _creds.valid:
            await asyncio.to_thread(_creds.refresh, Request())
        return _creds


async def client(location: str) -> genai.Client:
    await credentials()
    async with _lock:
        if location not in _clients:
            _clients[location] = genai.Client(
                vertexai=True, project=_project, location=location, credentials=_creds
            )
        return _clients[location]
