"""Build A2UI v0.9 cards from the backend JSON of one tool call.

The code makes each card from the tool result. The model does not write the cards.
Then each number on a card is a backend number (spec rule: code computes, model narrates).
Use only components from the Gemini Enterprise composite catalog.
"""

from typing import Any

VERSION = "v0.9"
CATALOG_ID = (
    "https://www.gstatic.com/vertexaisearch/a2ui/v0_9/gemini_enterprise_composite_catalog.json"
)
CALL_ACTION = "call_account"
# The queue card shows a Call button for this many callable rows. More buttons crowd the card.
MAX_CALL_BUTTONS = 5
# The transcript card shows this many lines. A longer table makes the GE chat hard to scroll.
MAX_TRANSCRIPT_LINES = 60

Message = dict[str, Any]


def inr(amount: float | int | None) -> str:
    """Write a rupee amount with Indian digit grouping, for example ₹74,35,486."""
    if amount is None:
        return "—"
    sign = "-" if amount < 0 else ""
    whole = str(round(abs(amount)))
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        pairs = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join([head, *pairs, tail])
    return f"₹{sign}{whole}"


def _label(value: str | None) -> str:
    return value.replace("_", " ").capitalize() if value else "—"


def _summary(summary: dict[str, Any] | None) -> str:
    # The backend stores the post-call summary as {"summary", "sentiment", "model"}.
    return (summary or {}).get("summary") or "—"


def _yes(flag: bool) -> str:
    return "Yes" if flag else "No"


class _Card:
    """Collect the components and the data model of one surface."""

    def __init__(self, surface_id: str, title: str):
        self.surface_id = surface_id
        # Do not give a component the ID "body": validators read the usageHint "body" as a child ID.
        self.components: list[dict[str, Any]] = [
            {"id": "root", "component": "MaterialCard", "children": ["content"]},
        ]
        self.body: list[str] = []
        self.data: dict[str, Any] = {}
        self.text(title, "h3")

    def add(self, component: dict[str, Any]) -> str:
        self.components.append(component)
        self.body.append(component["id"])
        return component["id"]

    def text(self, text: str, hint: str = "body") -> str:
        cid = f"text-{len(self.components)}"
        return self.add({"id": cid, "component": "MaterialText", "text": text, "usageHint": hint})

    def table(self, columns: list[tuple[str, str]], rows: list[dict[str, str]]) -> str:
        # Rows go in the data model, as in the GE sample templates. Table cells must be strings.
        cid = f"table-{len(self.components)}"
        self.data[cid] = rows
        return self.add(
            {
                "id": cid,
                "component": "MaterialTable",
                "columns": [{"header": h, "field": f} for h, f in columns],
                "rows": {"path": f"/{cid}"},
            }
        )

    def call_buttons(self, accounts: list[dict[str, Any]]) -> None:
        ids = []
        for row in accounts[:MAX_CALL_BUTTONS]:
            cid = f"call-{row['loan_id']}"
            self.components.append(
                {
                    "id": cid,
                    "component": "MaterialButton",
                    "label": f"Call {row['customer_name']}",
                    "variant": "stroked",
                    "leadingIcon": "call",
                    "action": {
                        "event": {"name": CALL_ACTION, "context": {"loan_id": row["loan_id"]}}
                    },
                }
            )
            ids.append(cid)
        if ids:
            self.add({"id": "call-row", "component": "MaterialRow", "children": ids})

    def messages(self) -> list[Message]:
        components = [
            *self.components,
            {
                "id": "content",
                "component": "MaterialColumn",
                "align": "stretch",
                "children": self.body,
            },
        ]
        out: list[Message] = [
            {
                "version": VERSION,
                "createSurface": {"surfaceId": self.surface_id, "catalogId": CATALOG_ID},
            },
            {
                "version": VERSION,
                "updateComponents": {"surfaceId": self.surface_id, "components": components},
            },
        ]
        if self.data:
            out.append(
                {
                    "version": VERSION,
                    "updateDataModel": {
                        "surfaceId": self.surface_id,
                        "path": "/",
                        "value": self.data,
                    },
                }
            )
        return out


def _counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{k} {v:,}" for k, v in counts.items())


def queue_card(result: dict[str, Any], surface_id: str) -> list[Message]:
    card = _Card(surface_id, "Today's call queue")
    card.text(
        f"{result['total_accounts']:,} accounts · {inr(result['total_due_inr'])} due", "subtitle1"
    )
    card.text(f"Risk: {_counts(result['by_risk_tier'])}", "caption")
    card.text(f"Language: {_counts(result['by_language'])}", "caption")
    card.text(
        "Call attempt: " + ", ".join(f"#{k} {v:,}" for k, v in result["by_call_attempt"].items()),
        "caption",
    )
    top = result["top"]
    card.table(
        [
            ("Name", "name"),
            ("Loan ID", "loan_id"),
            ("Branch", "branch"),
            ("Language", "language"),
            ("Due", "due"),
            ("2-month minimum", "ask"),
            ("Risk", "risk"),
            ("Attempt", "attempt"),
            ("Status", "status"),
            ("Callable", "callable"),
        ],
        [
            {
                "name": r["customer_name"],
                "loan_id": r["loan_id"],
                "branch": r["branch"],
                "language": r["language"],
                "due": inr(r["due_amount_inr"]),
                "ask": inr(r["bot_ask_amount_inr"]),
                "risk": r["risk_tier"],
                "attempt": str(r["call_attempt"]),
                "status": _label(r["status"]),
                "callable": _yes(r["callable"]),
            }
            for r in top
        ],
    )
    handled = result.get("handled_today") or []
    if handled:
        card.text(f"Handled today: {len(handled)}", "subtitle2")
        card.table(
            [("Name", "name"), ("Loan ID", "loan_id"), ("Status", "status")],
            [
                {"name": h["customer_name"], "loan_id": h["loan_id"], "status": _label(h["status"])}
                for h in handled
            ],
        )
    card.call_buttons([r for r in top if r["callable"]])
    return card.messages()


def analysis_card(result: dict[str, Any], surface_id: str) -> list[Message]:
    card = _Card(surface_id, "Collection call analysis")
    card.text(
        f"Committed {inr(result['total_committed_inr'])} · "
        f"{result['committed_pct']}% of called due",
        "subtitle1",
    )
    card.table(
        [("KPI", "kpi"), ("Value", "value")],
        [
            {
                "kpi": "Called accounts",
                "value": f"{result['called_accounts']:,} of {result['queue_accounts']:,}",
            },
            {"kpi": "Due on called accounts", "value": inr(result["called_due_inr"])},
            {"kpi": "Payment links today", "value": inr(result["links_sent_today_inr"])},
            {"kpi": "Promised within 7 days", "value": inr(result["promised_within_7_days_inr"])},
            {"kpi": "Total committed", "value": inr(result["total_committed_inr"])},
            {"kpi": "Outcome rate", "value": f"{result['outcome_rate_pct']}%"},
            {"kpi": "Partial agreements", "value": f"{result['partial_agreements']:,}"},
            {"kpi": "Escalations", "value": f"{result['escalations']:,}"},
        ],
    )
    calls = result.get("calls") or []
    if calls:
        card.text("Calls", "subtitle2")
        card.table(
            [
                ("Name", "name"),
                ("Loan ID", "loan_id"),
                ("Source", "source"),
                ("Attempt", "attempt"),
                ("Outcome", "outcome"),
                ("Summary", "summary"),
            ],
            [
                {
                    "name": c.get("customer_name") or "—",
                    "loan_id": c["loan_id"],
                    "source": _label(c["source"]),
                    "attempt": str(c["call_attempt"]),
                    "outcome": _label(c.get("outcome")),
                    "summary": _summary(c.get("summary")),
                }
                for c in calls
            ],
        )
    return card.messages()


def briefs_card(result: dict[str, Any], surface_id: str) -> list[Message]:
    card = _Card(surface_id, f"Handoff briefs ({result['count']})")
    if not result["briefs"]:
        card.text("No call needs a human handoff.")
    for b in result["briefs"]:
        p = b["profile"]
        intent = ", ".join(b.get("customer_intent") or [])
        card.add({"id": f"div-{b['call_id']}", "component": "MaterialDivider"})
        card.text(f"{p['customer_name']} · {p['loan_id']} · {p['branch']}", "subtitle1")
        card.table(
            [("Field", "field"), ("Value", "value")],
            [
                {"field": "Reason", "value": _label(b["reason_category"])},
                {"field": "Escalation", "value": b.get("escalation_reason") or "—"},
                {"field": "Customer intent", "value": intent or "—"},
                {"field": "Recommended action", "value": b.get("recommended_action") or "—"},
                {"field": "Disputed fee", "value": inr(b.get("disputed_fee_inr"))},
                {"field": "Due", "value": inr(p["due_amount_inr"])},
                {"field": "Risk", "value": p["risk_tier"]},
                {"field": "Language", "value": p["language"]},
                {"field": "Status", "value": _label(b["status"])},
            ],
        )
    return card.messages()


def account_card(result: dict[str, Any], surface_id: str) -> list[Message]:
    card = _Card(surface_id, f"{result['customer_name']} · {result['loan_id']}")
    card.text(f"{result['branch']} · {result['language']}", "subtitle1")
    last = result.get("last_call") or {}
    links = result.get("payment_links") or []
    rows = [
        ("Status", _label(result["status"])),
        ("Call attempt", str(result["call_attempt"])),
        ("Callable", _yes(result["callable"])),
        ("Due", inr(result["due_amount_inr"])),
        ("2-month minimum", inr(result["bot_ask_amount_inr"])),
        ("Monthly interest", inr(result.get("monthly_interest_inr"))),
        ("Risk", result["risk_tier"]),
        ("Promised amount", inr(result.get("promised_amount_inr"))),
        ("Promised by", result.get("promised_by") or "—"),
        ("Next callback", result.get("next_callback_date") or "—"),
        ("Payment links", f"{len(links)} · {inr(sum(lk['amount_inr'] for lk in links))}"),
        (
            "Last call",
            f"{_label(last.get('outcome'))} ({last.get('source')})" if last else "—",
        ),
        ("Summary", _summary(last.get("summary"))),
    ]
    card.table(
        [("Field", "field"), ("Value", "value")], [{"field": k, "value": v} for k, v in rows]
    )
    if result["callable"]:
        card.call_buttons([result])
    return card.messages()


def transcript_card(result: dict[str, Any], surface_id: str) -> list[Message]:
    card = _Card(surface_id, f"Call transcript · {result['customer_name']} · {result['loan_id']}")
    card.text(
        f"{_label(result.get('outcome'))} · attempt {result['call_attempt']} · "
        f"{_label(result['source'])} · {result.get('started_at') or '—'}",
        "subtitle1",
    )
    summary = result.get("summary") or {}
    if summary.get("summary"):
        card.text(summary["summary"])
    if summary.get("sentiment"):
        card.text(f"Sentiment: {_label(summary['sentiment'])}", "caption")
    lines = result.get("transcript") or []
    if lines:
        speaker = {"customer": "Customer", "bot": "AI agent"}
        card.table(
            [("Speaker", "speaker"), ("Text", "text")],
            [
                {"speaker": speaker.get(t["role"], t["role"]), "text": t["text"]}
                for t in lines[:MAX_TRANSCRIPT_LINES]
            ],
        )
        if len(lines) > MAX_TRANSCRIPT_LINES:
            card.text(f"{len(lines) - MAX_TRANSCRIPT_LINES} more lines", "caption")
    else:
        card.text("This call has no transcript.")
    tool_calls = result.get("tool_calls") or []
    if tool_calls:
        card.text("Tool calls", "subtitle2")
        card.table(
            [("Tool", "tool"), ("Result", "result"), ("Error", "error")],
            [
                {
                    "tool": c["name"],
                    "result": "OK" if c["ok"] else "Rejected",
                    "error": c.get("error") or "—",
                }
                for c in tool_calls
            ],
        )
    return card.messages()


BUILDERS = {
    "get_calls_due_today": queue_card,
    "analyze_collection_calls": analysis_card,
    "get_handoff_briefs": briefs_card,
    "get_call_status": account_card,
    "get_call_transcript": transcript_card,
}


def build(tool: str, result: dict[str, Any], surface_id: str) -> list[Message] | None:
    """Return the card messages for one tool result, or None when the tool has no card."""
    builder = BUILDERS.get(tool)
    if builder is None or not result.get("ok"):
        return None
    return builder(result, surface_id)
