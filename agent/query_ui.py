"""Send one prompt to the A2UI agent over A2A JSON-RPC and print the reply parts.

Local (in-process ASGI app):  uv run python query_ui.py "Who are the calls to be done today?"
Streaming:                    uv run python query_ui.py --stream "Analyze the calls"
Cloud Run:                    uv run python query_ui.py --url https://...run.app "..."

The script validates each A2UI message against the GE composite catalog in tests/.
"""

import argparse
import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path

import httpx

from query import gcloud_project

A2UI_EXTENSION_URI = "https://a2ui.org/a2a-extension/a2ui/v0.9"
CATALOG = Path(__file__).parent / "tests" / "ge_composite_catalog_v0_9.json"


def request_body(prompt: str, method: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": {
            "message": {
                "role": "user",
                "messageId": uuid.uuid4().hex,
                "parts": [{"kind": "text", "text": prompt}],
                # The A2UI v0.9 A2A extension: the client lists its catalogs in message metadata.
                "metadata": {"a2uiClientCapabilities": {"v0.9": {"supportedCatalogIds": []}}},
            }
        },
    }


def final_parts(results: list[dict]) -> list[dict]:
    """Return the parts of the last status message (task) or of the message result."""
    parts: list[dict] = []
    for result in results:
        message = (result.get("status") or {}).get("message") or result
        parts = message.get("parts") or parts
    return parts


def validate(messages: list[dict]) -> None:
    from a2ui.schema.catalog import A2uiCatalog, CatalogConfig

    catalog = A2uiCatalog.from_config(CatalogConfig.from_path("ge", str(CATALOG)), version="0.9")
    errors = catalog.validate_components(messages)
    if errors:
        raise SystemExit(f"A2UI component errors: {errors}")
    catalog.validate(messages)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt")
    parser.add_argument("--url", help="Cloud Run URL. Omit to run the app in this process.")
    parser.add_argument("--stream", action="store_true", help="Use message/stream (SSE).")
    args = parser.parse_args()

    headers = {"X-A2A-Extensions": A2UI_EXTENSION_URI}
    if args.url:
        token = subprocess.run(
            ["gcloud", "auth", "print-identity-token"], capture_output=True, text=True, check=True
        ).stdout.strip()
        headers["Authorization"] = f"Bearer {token}"
        client = httpx.AsyncClient(base_url=args.url, timeout=120)
    else:
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", gcloud_project())
        from collections_ui_agent.server import app

        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://local", timeout=120)

    method = "message/stream" if args.stream else "message/send"
    body = request_body(args.prompt, method)
    results = []
    async with client:
        if args.stream:
            async with client.stream("POST", "/", json=body, headers=headers) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        event = json.loads(line[5:])
                        result = event.get("result") or event
                        state = (result.get("status") or {}).get("state", "")
                        print(f"[event] {result.get('kind')} {state}")
                        results.append(result)
        else:
            response = await client.post("/", json=body, headers=headers)
            response.raise_for_status()
            reply = response.json()
            if "error" in reply:
                raise SystemExit(f"JSON-RPC error: {reply['error']}")
            results.append(reply["result"])

    a2ui = []
    for part in final_parts(results):
        if part["kind"] == "text":
            print(f"[text] {part['text']}")
        elif (part.get("metadata") or {}).get("mimeType") == "application/json+a2ui":
            a2ui.append(part["data"])
            print(f"[a2ui] {json.dumps(part['data'], ensure_ascii=False)}")
    if a2ui:
        validate(a2ui)
        print(f"[valid] {len(a2ui)} A2UI messages pass the GE composite catalog v0.9")


if __name__ == "__main__":
    asyncio.run(main())
