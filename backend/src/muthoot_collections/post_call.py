"""Post-call summary with gemini-3.8-flash (spec §7.1). The summary has no numbers in it.

The backend runs it after the call lock is clear. It uses the async client, so it does not block
the event loop.
"""

import asyncio
import json
import os
from typing import Any

from google.genai import types

from . import vertex
from .models import CallRecord

FLASH_MODEL = "gemini-3.8-flash"
TIMEOUT_S = 30

INSTRUCTION = """\
You summarize a collections call between a Cymbal Bank voice bot and a gold loan borrower.
Write the summary in English, in 2 or 3 short sentences.
Tell what the borrower said, what the borrower wants, and what the bot agreed to do.
Do not write amounts, dates, percentages or other numbers. The backend reports those values.
Do not write phone numbers or other personal identifiers.
Set sentiment to the borrower's mood at the end of the call."""

SCHEMA = types.Schema(
    type="OBJECT",
    required=["summary", "sentiment"],
    properties={
        "summary": types.Schema(type="STRING"),
        "sentiment": types.Schema(type="STRING", enum=["POSITIVE", "NEUTRAL", "NEGATIVE", "DISTRESSED"]),
    },
)


async def summarize(record: CallRecord, row: dict[str, Any]) -> dict[str, Any]:
    lines = "\n".join(f"{t['role']}: {t['text']}" for t in record.transcript)
    prompt = f"Customer: {row['customer_name']}. Call attempt: {record.call_attempt}.\n\nTranscript:\n{lines}"
    async with asyncio.timeout(TIMEOUT_S):
        flash = await vertex.client(os.environ.get("GENAI_LOCATION_FLASH", "global"))
        resp = await flash.aio.models.generate_content(
            model=FLASH_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=INSTRUCTION,
                response_mime_type="application/json",
                response_schema=SCHEMA,
                thinking_config=types.ThinkingConfig(thinking_level="LOW"),
            ),
        )
    data = json.loads(resp.text)
    return {"summary": data["summary"], "sentiment": data["sentiment"], "model": FLASH_MODEL}
