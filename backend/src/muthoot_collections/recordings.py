"""Durable call records in gs://<RECORDINGS_BUCKET>/<call_id>/ (AGENTS.md, call recordings).

The backend service account has only roles/storage.objectCreator. It can create an object but
cannot read or overwrite it. Thus the backend writes each object once, with no retry.
"""

import asyncio
import json
import os
from typing import Any
from urllib.parse import quote

import httpx

from . import telemetry, vertex

UPLOAD_URL = "https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
TIMEOUT_S = 10
# The total limit includes a credential refresh. The call lock is already clear at this point.
TOTAL_TIMEOUT_S = 20


async def save_transcript(call: dict[str, Any]) -> None:
    """Write <call_id>/transcript.json. A failure is logged. It never stops the call end."""
    fields = {"call_id": call["call_id"], "loan_id": call["loan_id"]}
    bucket = os.environ.get("RECORDINGS_BUCKET")
    if not bucket:
        telemetry.event("transcript_not_saved", reason="RECORDINGS_BUCKET is not set", **fields)
        return
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT_S):
            resp = await _upload(bucket, call)
        if resp.is_success:
            telemetry.event("transcript_saved", **fields)
        else:
            telemetry.log.warning(
                "transcript_save_failed", extra={"fields": {**fields, "status": resp.status_code}}
            )
    except Exception as e:
        telemetry.log.warning(
            "transcript_save_failed", extra={"fields": {**fields, "error": type(e).__name__}}
        )


async def _upload(bucket: str, call: dict[str, Any]) -> httpx.Response:
    creds = await vertex.credentials()
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as http:
        return await http.post(
            UPLOAD_URL.format(bucket=quote(bucket, safe="")),
            # ifGenerationMatch=0: create only. A second write for the same call fails.
            params={
                "uploadType": "media",
                "name": f"{call['call_id']}/transcript.json",
                "ifGenerationMatch": "0",
            },
            headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
            content=json.dumps(call, ensure_ascii=False).encode(),
        )
