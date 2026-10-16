"""Event fan-out for the /monitor transcript stream (spec §7.4, monitor stream)."""

import asyncio
import json
import os
from collections import deque
from collections.abc import Callable
from typing import Any

from .decisions import summarize_latency
from .store import Store

QUEUE_SIZE = 500
MAX_VIEWERS = 20
# Bounds for the decision panel data that a page gets again after a reconnect.
PANEL_TURNS = 12
PANEL_EVENTS = 20
LATENCY_SAMPLES = 200


class Monitor:
    def __init__(self, store: Store, live_model: str) -> None:
        self.store = store
        self.live_model = live_model
        self.queues: set[asyncio.Queue] = set()
        # Open streams. A dropped viewer counts until its stream generator ends.
        self.viewers = 0
        # The decision panel data of the latest call.
        self.panel_call: str | None = None
        self.panel_turns: dict[int, dict[str, Any]] = {}
        self.panel_events: deque[dict[str, Any]] = deque(maxlen=PANEL_EVENTS)
        self.latency: dict[str, deque[int]] = {
            k: deque(maxlen=LATENCY_SAMPLES) for k in ("decision", "consent", "tool_wait")
        }

    def reserve(self) -> Callable[[], None] | None:
        """Take a viewer slot. Return a release function that works once, or None if all are taken."""
        if self.viewers >= MAX_VIEWERS:
            return None
        self.viewers += 1
        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                self.viewers -= 1

        return release

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(QUEUE_SIZE)
        self.queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.queues.discard(q)

    def start_panel(self, call_id: str) -> None:
        """Drop the panel data of the earlier call."""
        self.panel_call = call_id
        self.panel_turns.clear()
        self.panel_events.clear()
        for samples in self.latency.values():
            samples.clear()

    def _record(self, event: dict[str, Any]) -> None:
        kind = event["type"]
        if kind not in ("decision", "consent", "gate", "audit", "tool"):
            return
        if event["call_id"] != self.panel_call:
            self.start_panel(event["call_id"])
        if kind in ("decision", "consent"):
            self.panel_turns.setdefault(event["turn_id"], {})[kind] = event
            while len(self.panel_turns) > PANEL_TURNS:
                del self.panel_turns[min(self.panel_turns)]
            if event["status"] == "ready":
                self.latency[kind].append(event["latency_ms"])
        elif kind == "tool":
            self.latency["tool_wait"].append(event["decision_wait_ms"])
        else:
            self.panel_events.append(event)

    def decisions(self, call_id: str) -> dict[str, Any]:
        mine = self.panel_call == call_id
        waits = list(self.latency["tool_wait"]) if mine else []
        return {
            "turns": [
                {"turn_id": i, "decision": t.get("decision"), "consent": t.get("consent")}
                for i, t in sorted(self.panel_turns.items())
            ]
            if mine
            else [],
            "events": list(self.panel_events) if mine else [],
            "latency": {
                "decision": summarize_latency(list(self.latency["decision"]) if mine else []),
                "consent": summarize_latency(list(self.latency["consent"]) if mine else []),
                "tool_wait": {"n": len(waits), "total_ms": sum(waits), "max_ms": max(waits, default=0)},
            },
        }

    def publish(self, event: dict[str, Any]) -> None:
        self._record(event)
        for q in list(self.queues):
            if q.full():
                # Disconnect a slow viewer. The page reconnects and gets a new state event.
                self.queues.discard(q)
                while not q.empty():
                    q.get_nowait()
                q.put_nowait(None)
            else:
                q.put_nowait(event)

    def state(self) -> dict[str, Any]:
        active = None
        for loan_id, st in self.store.state.items():
            rec = self.store.calls.get(st.active_call_id) if st.active_call_id else None
            if rec is not None and rec.source == "live":
                row = self.store.rows[loan_id]
                # live_session imports this module, so the import is here.
                from .live_session import call_plan

                rungs = call_plan(row, st, self.store.today())["rungs"]
                active = {
                    "call_id": rec.call_id,
                    "loan_id": loan_id,
                    "customer_name": row["customer_name"],
                    "language": st.language_override or row["language"],
                    "call_attempt": st.call_attempt,
                    "status": st.status.value,
                    "due_inr": row["due_amount_inr"],
                    "rungs": [{"months": m, "amount_inr": a} for m, a in rungs],
                }
        return {
            "type": "state",
            "active_call": active,
            "live_model": self.live_model,
            "decisions": self.decisions(active["call_id"]) if active else None,
            "locations": {k: v for k, v in os.environ.items() if k.startswith("GENAI_LOCATION_")},
        }

    def publish_state(self) -> None:
        self.publish(self.state())


def sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
