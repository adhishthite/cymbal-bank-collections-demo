"""HTTPS client for the backend `/api/agent/*` routes."""

import functools
import os
from typing import Any

import httpx
from opentelemetry import propagate

_TIMEOUT_S = 10.0


@functools.cache
def _token() -> str:
    # DEMO_AGENT_TOKEN is for local runs only. Agent Runtime reads the token from Secret Manager.
    local = os.environ.get("DEMO_AGENT_TOKEN")
    if local:
        return local
    from google.cloud import secretmanager

    name = os.environ["DEMO_TOKEN_SECRET"]
    response = secretmanager.SecretManagerServiceClient().access_secret_version(name=name)
    return response.payload.data.decode("utf-8").strip()


@functools.cache
def _client() -> httpx.Client:
    return httpx.Client(base_url=os.environ["BACKEND_URL"], timeout=_TIMEOUT_S)


def request(method: str, path: str, *, params: dict | None = None, json: Any = None) -> dict:
    """Send one request and return the backend JSON body unchanged.

    The backend sends business errors as 4xx with {"ok": false, "error": ...}.
    The model must see those bodies, so this function does not raise on status.
    """
    headers = {"Authorization": f"Bearer {_token()}"}
    # Continue the current trace into the backend (W3C traceparent).
    propagate.inject(headers)
    try:
        response = _client().request(method, path, params=params, json=json, headers=headers)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"backend unreachable: {type(exc).__name__}"}
    try:
        return response.json()
    except ValueError:
        return {"ok": False, "error": f"backend returned HTTP {response.status_code} with no JSON"}
