"""Tracing, JSON logs and audit records (AGENTS.md, observability).

Cloud Run sends each stdout JSON line to Cloud Logging. The `logging.googleapis.com/trace`
field links a log line to its Cloud Trace span. Do not put transcripts, tokens or phone
numbers in log fields or span attributes.
"""

import json
import logging
import os
import sys
from datetime import datetime
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
tracer = trace.get_tracer("muthoot_collections")
log = logging.getLogger("muthoot_collections")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        entry.update(getattr(record, "fields", {}))
        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            entry["logging.googleapis.com/trace"] = f"projects/{PROJECT}/traces/{ctx.trace_id:032x}"
            entry["logging.googleapis.com/spanId"] = f"{ctx.span_id:016x}"
            entry["logging.googleapis.com/trace_sampled"] = ctx.trace_flags.sampled
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


def setup() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)

    provider = TracerProvider()
    # K_SERVICE is set only on Cloud Run. Local runs and tests do not export spans.
    if os.environ.get("K_SERVICE"):
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

        provider.add_span_processor(BatchSpanProcessor(CloudTraceSpanExporter(project_id=PROJECT)))
    trace.set_tracer_provider(provider)


def event(message: str, **fields: Any) -> None:
    log.info(message, extra={"fields": fields})


def audit(
    *,
    who: str,
    what: str,
    loan_id: str,
    before: Any,
    after: Any,
    source: str,
    call_id: str | None,
    at: datetime,
) -> dict[str, Any]:
    record = {
        "who": who,
        "what": what,
        "loan_id": loan_id,
        "call_id": call_id,
        "before": before,
        "after": after,
        "source": source,
        "at": at.isoformat(),
    }
    log.info("audit", extra={"fields": {"type": "audit", **record}})
    return record
