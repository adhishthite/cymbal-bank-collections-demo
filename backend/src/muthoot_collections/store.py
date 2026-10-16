"""In-memory account store: pool load, query, status machine, invariants and KPIs.

Spec §7.2 and §7.3 C–F. The store is synchronous. Callers on the event loop call it with no
`await` inside a store method, so each method is atomic (AGENTS.md, check order step 8).
"""

import json
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import telemetry
from .models import (
    FROZEN,
    TERMINAL,
    AccountState,
    AccountStatus,
    CallRecord,
    Event,
    HandoffBrief,
    PaymentLink,
    Session,
)

IST = ZoneInfo("Asia/Kolkata")
DATA_FILE = Path(__file__).parent / "data" / "customers.jsonl"
QUEUE_CHANNELS = {"Voice bot", "Bot + call center"}
MAX_TEXT = 2000
MAX_ROWS = 50

S = AccountStatus
# (status, event) -> status. Escalation is allowed from each non-frozen status in a call.
TRANSITIONS: dict[tuple[AccountStatus, Event], AccountStatus] = {
    (S.DUE_FOR_CALL, Event.TRIGGER): S.CALL_IN_PROGRESS,
    (S.NO_OUTCOME, Event.TRIGGER): S.CALL_IN_PROGRESS,
    (S.DUE_FOR_CALL, Event.SEED): S.CALL_IN_PROGRESS,
    (S.CALL_IN_PROGRESS, Event.LINK_FULL): S.PAYMENT_LINK_SENT_TODAY,
    (S.CALLBACK_SCHEDULED, Event.LINK_FULL): S.PAYMENT_LINK_SENT_TODAY,
    (S.CALL_IN_PROGRESS, Event.LINK_PARTIAL): S.PARTIAL_SETTLEMENT_AGREED,
    (S.CALLBACK_SCHEDULED, Event.LINK_PARTIAL): S.PARTIAL_SETTLEMENT_AGREED,
    (S.CALL_IN_PROGRESS, Event.CALLBACK): S.CALLBACK_SCHEDULED,
    # The bot can move a callback in the same call. The new date, promise and notes replace the old ones.
    (S.CALLBACK_SCHEDULED, Event.CALLBACK): S.CALLBACK_SCHEDULED,
    (S.PARTIAL_SETTLEMENT_AGREED, Event.CALLBACK): S.PARTIAL_SETTLEMENT_AGREED,
    (S.CALL_IN_PROGRESS, Event.CALL_END): S.NO_OUTCOME,
    **{
        (status, ev): to
        for status in (
            S.CALL_IN_PROGRESS,
            S.PAYMENT_LINK_SENT_TODAY,
            S.PARTIAL_SETTLEMENT_AGREED,
            S.CALLBACK_SCHEDULED,
        )
        for ev, to in (
            (Event.ESCALATE_DISPUTE, S.DISPUTE_LOGGED),
            (Event.ESCALATE_HARDSHIP, S.ESCALATED_BRANCH_HARDSHIP),
        )
    },
}

SETTLEMENT_TYPES = {"FULL_DUE": Event.LINK_FULL, "PARTIAL_TWO_MONTH": Event.LINK_PARTIAL}
LINK_INTENTS = {"FULL_DUE": ["INT-01 WILL_PAY_NOW"], "PARTIAL_TWO_MONTH": ["INT-03 CANNOT_PAY_FULL"]}
CALLBACK_INTENTS = {
    "NEEDS_MORE_TIME": ["INT-02 NEEDS_MORE_TIME"],
    "CANNOT_PAY_FULL": ["INT-03 CANNOT_PAY_FULL"],
}
BRANCH_CALLBACK_DAYS = 2
REASON_CATEGORIES = {
    "PENAL_FEE_DISPUTE": (Event.ESCALATE_DISPUTE, ["INT-04 DISPUTES_CHARGES"]),
    "PAYMENT_OR_AMOUNT_DISPUTE": (Event.ESCALATE_DISPUTE, ["INT-04 DISPUTES_CHARGES"]),
    "MEDICAL_HARDSHIP": (Event.ESCALATE_HARDSHIP, ["INT-05 HUMAN_HANDOFF"]),
    "CUSTOMER_REQUESTED_HUMAN": (Event.ESCALATE_HARDSHIP, ["INT-05 HUMAN_HANDOFF"]),
    "HARDSHIP_AND_FEE_DISPUTE": (
        Event.ESCALATE_HARDSHIP,
        ["INT-05 HUMAN_HANDOFF", "INT-04 DISPUTES_CHARGES"],
    ),
}

# Spec §7.2 seeded outcomes: (loan_id, [(tool, kwargs), ...]).
SEEDS: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = [
    (
        "GL1814280043",
        [
            (
                "schedule_callback_reminder",
                {
                    "days_from_today": 3,
                    "promised_amount_inr": 17965,
                    "notes": "Promises full payment in 3 days",
                },
            )
        ],
    ),
    (
        "GL9061256232",
        [
            (
                "generate_upi_payment_link",
                {"amount_inr": 6868, "settlement_type": "PARTIAL_TWO_MONTH"},
            ),
            ("schedule_callback_reminder", {"days_from_today": 3, "notes": "Remaining ₹6,868"}),
        ],
    ),
]

ROW_FIELDS = [
    "customer_id",
    "loan_id",
    "customer_name",
    "branch",
    "language",
    "payment_scheme",
    "months_due",
    "due_amount_inr",
    "bot_ask_amount_inr",
    "risk_tier",
    "status",
    "call_attempt",
    "callable",
]
LEDGER_KEYS = ("duplicate", "link_id", "amount_inr", "next_callback_date", "code")
SUM_FIELDS = ["due_amount_inr", "bot_ask_amount_inr", "pos_inr"]


def now_ist() -> datetime:
    return datetime.now(IST)


def derive_call_attempt(row: dict[str, Any]) -> int:
    if row["last_contact_outcome"] == "Refused / disputed" or (row["broken_promises_3m"] or 0) >= 2:
        return 3
    if row["last_contact_channel"] in ("Voice bot", "Call center"):
        return 2
    return 1


def parse_window(spec: str) -> tuple[time, time]:
    start, end = spec.split("-")
    return time.fromisoformat(start), time.fromisoformat(end)


def inr(amount: int) -> str:
    """Indian digit grouping, e.g. 427700 -> ₹4,27,700."""
    s = str(amount)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        s = ",".join([head, *groups, tail]) if head else ",".join([*groups, tail])
    return f"₹{s}"


# Live passes this to a tool. It returns None to allow, or a fail dict to reject (check step 7b).
Authorize = Callable[[str, dict[str, Any]], dict[str, Any] | None]


def fail(error: str) -> dict[str, Any]:
    return {"ok": False, "error": error}


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class Store:
    def __init__(
        self,
        *,
        now: Callable[[], datetime] = now_ist,
        call_window: str = "08:00-19:00",
        call_window_bypass: bool = False,
        seed_outcomes: bool = True,
        data_file: Path = DATA_FILE,
    ) -> None:
        self.now = now
        self.window = parse_window(call_window)
        self.call_window_bypass = call_window_bypass
        self.seed_outcomes = seed_outcomes
        self.data_file = data_file
        self.reset()

    # ---- load and reset ----

    def reset(self) -> None:
        with self.data_file.open(encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        self.rows: dict[str, dict[str, Any]] = {r["loan_id"]: r for r in rows}
        for r in rows:
            r["months_due"] = r["bullet_months_due"] or r["monthly_months_unpaid"]
            r["in_queue"] = r["suggested_channel"] in QUEUE_CHANNELS
        self.state: dict[str, AccountState] = {
            r["loan_id"]: AccountState(status=S.DUE_FOR_CALL, call_attempt=derive_call_attempt(r))
            for r in rows
        }
        self.calls: dict[str, CallRecord] = {}
        self.links: list[PaymentLink] = []
        self.briefs: list[HandoffBrief] = []
        self.audit_log: list[dict[str, Any]] = []
        self.idempotent: dict[tuple, dict[str, Any]] = {}
        self.columns = set(rows[0]) | {"status", "call_attempt", "callable", "in_queue"}
        self.languages = {r["language"] for r in rows}
        self.audit_log.append(
            telemetry.audit(
                who="admin",
                what="reset",
                loan_id="*",
                before=None,
                after={"accounts": len(rows), "seed_outcomes": self.seed_outcomes},
                source="admin",
                call_id=None,
                at=self.now(),
            )
        )
        if self.seed_outcomes:
            self.seed_outcome()

    # ---- read views ----

    def callable(self, loan_id: str) -> bool:
        return self.state[loan_id].status in (S.DUE_FOR_CALL, S.NO_OUTCOME)

    def view(self, loan_id: str) -> dict[str, Any]:
        st = self.state[loan_id]
        return {
            **self.rows[loan_id],
            "status": st.status.value,
            "call_attempt": st.call_attempt,
            "language_override": st.language_override,
            "callable": self.callable(loan_id),
        }

    def row(self, loan_id: str, extra: list[str] = ()) -> dict[str, Any]:
        v = self.view(loan_id)
        out = {k: v[k] for k in [*ROW_FIELDS, *(e for e in extra if e not in ROW_FIELDS)]}
        # Tool results stay in the GE session history. Leave out a field that is almost always empty.
        if v["language_override"]:
            out["language_override"] = v["language_override"]
        return out

    def counted(self, rec: CallRecord) -> bool:
        """Seeded records are fixtures and count on any date. Live records count on their IST day."""
        return rec.source == "seeded" or rec.started_at.astimezone(IST).date() == self.today()

    def today(self) -> date:
        return self.now().astimezone(IST).date()

    def calls_due(self, limit: int = 10) -> dict[str, Any]:
        limit = max(1, min(limit, MAX_ROWS))
        queue = [r for r in self.rows.values() if r["in_queue"]]
        top = sorted(queue, key=lambda r: (-r["risk_score"], -r["due_amount_inr"]))[:limit]
        attempts = Counter(self.state[r["loan_id"]].call_attempt for r in queue)
        handled = {}
        for rec in self.calls.values():
            if self.counted(rec):
                row = self.rows[rec.loan_id]
                handled[rec.loan_id] = {
                    "loan_id": rec.loan_id,
                    "customer_name": row["customer_name"],
                    "branch": row["branch"],
                    "status": self.state[rec.loan_id].status.value,
                    "callable": self.callable(rec.loan_id),
                    "last_call_source": rec.source,
                    "last_call_outcome": rec.final_status.value if rec.final_status else None,
                }
        return {
            "ok": True,
            "total_accounts": len(queue),
            "total_due_inr": sum(r["due_amount_inr"] for r in queue),
            "by_language": dict(Counter(r["language"] for r in queue).most_common()),
            "by_treatment_segment": dict(Counter(r["treatment_segment"] for r in queue).most_common()),
            "by_risk_tier": dict(Counter(r["risk_tier"] for r in queue).most_common()),
            "by_call_attempt": {str(k): attempts[k] for k in sorted(attempts)},
            "top": [self.row(r["loan_id"]) for r in top],
            "handled_today": list(handled.values()),
        }

    def query_accounts(
        self,
        filters: dict[str, Any] | None = None,
        group_by: str | None = None,
        sort_by: str | None = None,
        descending: bool = True,
        limit: int = 10,
    ) -> dict[str, Any]:
        filters = filters or {}
        for col in [*filters, group_by, sort_by]:
            if col is not None and col not in self.columns:
                return fail(f"Unknown column: {col}. Columns: {', '.join(sorted(self.columns))}.")
        try:
            preds = [(col, _predicate(spec)) for col, spec in filters.items()]
        except ValueError as e:
            return fail(str(e))

        views = [self.view(loan_id) for loan_id in self.rows]
        for col, spec in filters.items():
            if isinstance(spec, dict) and "contains" not in spec:
                sample = next((v[col] for v in views if v[col] is not None), None)
                for bound in spec.values():
                    if bound is not None and isinstance(bound, str) != isinstance(sample, str):
                        return fail(f"Bad range bound {bound!r} for column {col} (example value {sample!r}).")
        matched = [v for v in views if all(p(v[col]) for col, p in preds)]
        if sort_by:
            present = [v for v in matched if v[sort_by] is not None]
            missing = [v for v in matched if v[sort_by] is None]
            matched = sorted(present, key=lambda v: v[sort_by], reverse=descending) + missing

        groups = []
        if group_by:
            buckets: dict[Any, list[dict[str, Any]]] = {}
            for v in matched:
                buckets.setdefault(v[group_by], []).append(v)
            groups = sorted(
                ({group_by: k, "count": len(vs), **_sums(vs)} for k, vs in buckets.items()),
                key=lambda g: -g["count"],
            )
        extra = [c for c in [*filters, group_by, sort_by] if c]
        limit = max(0, min(limit, MAX_ROWS))
        return {
            "ok": True,
            "matched": len(matched),
            "totals": _sums(matched),
            "groups": groups,
            "rows": [self.row(v["loan_id"], extra) for v in matched[:limit]],
        }

    def account_status(self, loan_id: str) -> dict[str, Any]:
        if loan_id not in self.rows:
            return fail(f"Unknown loan_id: {loan_id}.")
        st = self.state[loan_id]
        rec = self.calls.get(st.last_call_id) if st.last_call_id else None
        return {
            "ok": True,
            **self.row(loan_id, ["monthly_interest_inr", "maturity_date", "days_past_due"]),
            "calling_frozen": st.calling_frozen,
            "promised_by": st.promised_by.isoformat() if st.promised_by else None,
            "next_callback_date": st.next_callback_date.isoformat() if st.next_callback_date else None,
            "promised_amount_inr": st.promised_amount_inr,
            "callback_notes": st.callback_notes,
            "payment_links": [_link_out(lk) for lk in self.links if lk.loan_id == loan_id],
            "last_call": _call_out(rec, excerpt=True) if rec else None,
        }

    def call_detail(self, call_id: str) -> dict[str, Any]:
        """The full call record: transcript and tool calls."""
        rec = self.calls.get(call_id)
        if rec is None:
            return fail(f"Unknown call_id: {call_id}.")
        return {
            "ok": True,
            **_call_out(rec, excerpt=False),
            "customer_name": self.rows[rec.loan_id]["customer_name"],
            "transcript": rec.transcript,
            "tool_calls": rec.tool_calls,
        }

    def account_calls(self, loan_id: str) -> dict[str, Any]:
        """The calls of one account, newest first, with no transcripts."""
        if loan_id not in self.rows:
            return fail(f"Unknown loan_id: {loan_id}.")
        recs = sorted(
            (rec for rec in self.calls.values() if rec.loan_id == loan_id),
            key=lambda rec: rec.started_at,
            reverse=True,
        )
        return {"ok": True, "loan_id": loan_id, "calls": [_call_out(rec, excerpt=False) for rec in recs]}

    def record_tool_call(self, call_id: str, name: str, args: dict[str, Any], result: dict[str, Any]) -> None:
        if rec := self.calls.get(call_id):
            rec.tool_calls.append(
                {
                    "name": name,
                    "args": args,
                    "ok": result["ok"],
                    "error": result.get("error"),
                    # Keep a small subset of the result.
                    "result": {k: result[k] for k in LEDGER_KEYS if k in result},
                    "at": self.now().isoformat(),
                }
            )

    def handoffs(self) -> dict[str, Any]:
        briefs = []
        for b in self.briefs:
            rec = self.calls.get(b.call_id)
            briefs.append(
                {
                    "loan_id": b.loan_id,
                    "call_id": b.call_id,
                    "source": b.source,
                    "status": self.state[b.loan_id].status.value,
                    "profile": b.profile,
                    "customer_intent": b.customer_intent,
                    "reason_category": b.reason_category,
                    "disputed_fee_inr": b.disputed_fee_inr,
                    "escalation_reason": b.customer_summary,
                    "recommended_action": b.recommended_action,
                    "branch_callback_date": b.branch_callback_date.isoformat(),
                    "transcript": rec.transcript if rec else [],
                    "created_at": b.created_at.isoformat(),
                }
            )
        return {"ok": True, "count": len(briefs), "briefs": briefs}

    def analysis(self) -> dict[str, Any]:
        """Spec §7.3 F. Every number comes from the store."""
        queue = [r for r in self.rows.values() if r["in_queue"]]
        records = [rec for rec in self.calls.values() if self.counted(rec)]
        called = {rec.loan_id for rec in records}
        called_due = sum(self.rows[loan]["due_amount_inr"] for loan in called)
        with_outcome = [rec for rec in records if rec.final_status in TERMINAL]
        links_today = [
            lk
            for lk in self.links
            if lk.source == "seeded" or lk.created_at.astimezone(IST).date() == self.today()
        ]
        promised = sum(
            st.promised_amount_inr or 0
            for st in self.state.values()
            if st.status in (S.CALLBACK_SCHEDULED, S.PARTIAL_SETTLEMENT_AGREED)
        )
        committed_links = sum(
            lk.amount_inr for lk in links_today if self.state[lk.loan_id].status not in FROZEN
        )
        total_committed = committed_links + promised
        escalations = [
            {
                "loan_id": b.loan_id,
                "customer_name": self.rows[b.loan_id]["customer_name"],
                "branch": self.rows[b.loan_id]["branch"],
                "status": self.state[b.loan_id].status.value,
                "disputed_fee_inr": b.disputed_fee_inr,
            }
            for b in self.briefs
            if self.state[b.loan_id].status in FROZEN
        ]
        return {
            "ok": True,
            "queue_accounts": len(queue),
            "queue_due_inr": sum(r["due_amount_inr"] for r in queue),
            "called_accounts": len(called),
            "called_due_inr": called_due,
            "calls_attempted": len(records),
            "calls_with_outcome": len(with_outcome),
            "calls_with_outcome_live": sum(rec.source == "live" for rec in with_outcome),
            "calls_with_outcome_seeded": sum(rec.source == "seeded" for rec in with_outcome),
            "outcome_rate_pct": _pct(len(with_outcome), len(records)),
            "links_sent_today_inr": sum(lk.amount_inr for lk in links_today),
            "promised_within_7_days_inr": promised,
            "total_committed_inr": total_committed,
            "committed_pct": _pct(total_committed, called_due),
            "partial_agreements": sum(st.status == S.PARTIAL_SETTLEMENT_AGREED for st in self.state.values()),
            "escalations": len(escalations),
            "escalation_details": escalations,
            "calls": [
                {
                    **_call_out(rec, excerpt=False),
                    "customer_name": self.rows[rec.loan_id]["customer_name"],
                    "status": self.state[rec.loan_id].status.value,
                }
                for rec in records
            ],
        }

    # ---- state changes ----

    def _transition(
        self,
        loan_id: str,
        event: Event,
        *,
        who: str,
        source: str,
        call_id: str | None,
        details: dict[str, Any] | None = None,
    ) -> AccountStatus:
        st = self.state[loan_id]
        before = st.status
        after = TRANSITIONS[(before, event)]
        with telemetry.tracer.start_as_current_span(
            "store.transition",
            attributes={"loan_id": loan_id, "call_id": call_id or "", "event": event.value},
        ):
            st.status = after
            self.audit_log.append(
                telemetry.audit(
                    who=who,
                    what=event.value,
                    loan_id=loan_id,
                    before=before.value,
                    after={"status": after.value, **(details or {})} if details else after.value,
                    source=source,
                    call_id=call_id,
                    at=self.now(),
                )
            )
        return after

    def in_call_window(self) -> bool:
        if self.call_window_bypass:
            return True
        start, end = self.window
        return start <= self.now().astimezone(IST).time() < end

    def start_call(self, loan_id: str) -> dict[str, Any]:
        """Trigger checks D-5, callable status, D-11 and D-9. The caller checks telephony."""
        if loan_id not in self.rows:
            return fail(f"Unknown loan_id: {loan_id}.")
        st = self.state[loan_id]
        if st.calling_frozen:
            return fail("Automated calling is frozen on this account after an escalation.")
        if not self.callable(loan_id):
            return fail(f"The account is not callable. Its status is {st.status.value}.")
        if any(s.active_call_id for s in self.state.values()):
            return fail("Another call is in progress.")
        if not self.in_call_window():
            return fail("Calls are allowed only from 08:00 to 19:00 IST.")
        call_id = str(uuid.uuid4())
        self._open_call(loan_id, call_id, source="live", event=Event.TRIGGER, who="ge-agent")
        return {"ok": True, "call_id": call_id}

    def _open_call(self, loan_id: str, call_id: str, *, source: str, event: Event, who: str):
        st = self.state[loan_id]
        self._transition(loan_id, event, who=who, source=source, call_id=call_id)
        st.active_call_id = call_id
        st.last_call_id = call_id
        self.calls[call_id] = CallRecord(
            call_id=call_id,
            loan_id=loan_id,
            source=source,
            call_attempt=st.call_attempt,
            started_at=self.now(),
        )

    def end_call(self, call_id: str, reason: str) -> None:
        """Spec §7.4 call end. Clear the lock first, then set NO_OUTCOME if no outcome tool ran."""
        rec = self.calls.get(call_id)
        # A reset during the call already removed the record and the lock.
        if rec is None or rec.ended_at is not None:
            return
        st = self.state[rec.loan_id]
        if st.active_call_id == call_id:
            st.active_call_id = None
        if st.status == S.CALL_IN_PROGRESS:
            self._transition(rec.loan_id, Event.CALL_END, who="backend", source=rec.source, call_id=call_id)
        rec.ended_at = self.now()
        rec.end_reason = reason
        rec.final_status = st.status

    def seed_outcome(self) -> None:
        """R7: open a seed call, run the same tool functions, then end the call."""
        for loan_id, tools in SEEDS:
            call_id = f"seed-{loan_id}"
            self._open_call(loan_id, call_id, source="seeded", event=Event.SEED, who="seed")
            session = Session(call_id=call_id, loan_id=loan_id, source="seeded")
            for name, kwargs in tools:
                result = getattr(self, name)(session, loan_id=loan_id, **kwargs)
                self.record_tool_call(call_id, name, {"loan_id": loan_id, **kwargs}, result)
                if not result["ok"]:
                    raise RuntimeError(f"seed {loan_id} {name} failed: {result['error']}")
            self.end_call(call_id, reason="seeded")

    def set_language(self, loan_id: str, language: str | None) -> dict[str, Any]:
        if loan_id not in self.rows:
            return fail(f"Unknown loan_id: {loan_id}.")
        if language is not None and language not in self.languages:
            return fail(f"Unknown language: {language}. Use one of {sorted(self.languages)}.")
        st = self.state[loan_id]
        before = st.language_override
        st.language_override = language
        self.audit_log.append(
            telemetry.audit(
                who="admin",
                what="set_language_override",
                loan_id=loan_id,
                before=before,
                after=language,
                source="admin",
                call_id=None,
                at=self.now(),
            )
        )
        return {"ok": True, "loan_id": loan_id, "language_override": language}

    # ---- Live tools (spec §7.3 B, check order in AGENTS.md) ----

    def _precheck(self, session: Session, loan_id: Any) -> dict[str, Any] | None:
        """Check order steps 2 and 3: session binding (D-8) and active call."""
        if loan_id != session.loan_id:
            return fail("This call can act only on the loan of the current customer.")
        if self.state[loan_id].active_call_id != session.call_id:
            return fail("There is no active call on this account.")
        return None

    def generate_upi_payment_link(
        self,
        session: Session,
        *,
        loan_id: Any,
        amount_inr: Any,
        settlement_type: Any,
        authorize: Authorize | None = None,
    ) -> dict[str, Any]:
        # 1. Shape.
        if not isinstance(loan_id, str) or not is_int(amount_inr):
            return fail("loan_id must be text and amount_inr must be a whole number.")
        if not isinstance(settlement_type, str) or settlement_type not in SETTLEMENT_TYPES:
            return fail("settlement_type must be FULL_DUE or PARTIAL_TWO_MONTH.")
        # 2-3. Binding and active call.
        if err := self._precheck(session, loan_id):
            return err
        # 4. Idempotency (D-7).
        key = ("link", session.call_id, loan_id, settlement_type, amount_inr)
        if key in self.idempotent:
            return {**self.idempotent[key], "duplicate": True}
        st, row = self.state[loan_id], self.rows[loan_id]
        # 5. Frozen (D-5).
        if st.calling_frozen:
            return fail("Calling is frozen on this account. The branch team will contact the customer.")
        # 6. Amount rules (D-3, D-2, D-1).
        due, floor = row["due_amount_inr"], row["bot_ask_amount_inr"]
        if not 1 <= amount_inr <= due:
            return fail(f"The amount must be between ₹1 and the due amount ({inr(due)}).")
        if settlement_type == "FULL_DUE" and amount_inr != due:
            return fail(f"A full payment link must be for the exact due amount ({inr(due)}).")
        if settlement_type == "PARTIAL_TWO_MONTH":
            if floor == due:
                return fail(f"This account's minimum is the full due amount ({inr(due)}).")
            if not floor <= amount_inr < due:
                return fail(f"Minimum partial payment is 2 months' interest ({inr(floor)}).")
        # 7. Status guard (D-6).
        event = SETTLEMENT_TYPES[settlement_type]
        if (st.status, event) not in TRANSITIONS:
            return fail(f"A payment link is not allowed in status {st.status.value}.")
        # 7b. Evidence gate. It runs after the replay check, so a duplicate needs no new evidence.
        if authorize and (rejected := authorize("generate_upi_payment_link", {"amount_inr": amount_inr})):
            return rejected
        # 8. Change state.
        self._transition(
            loan_id,
            event,
            who="voice-bot",
            source=session.source,
            call_id=session.call_id,
            details={"amount_inr": amount_inr, "settlement_type": settlement_type},
        )
        if settlement_type == "FULL_DUE":
            st.promised_by = datetime.combine(self.today(), time(23, 59), tzinfo=IST)
            st.next_callback_date = None
            st.promised_amount_inr = None
            st.callback_notes = None
        else:
            # The link replaces an earlier promise in the KPIs. The callback date stays for the remainder.
            st.promised_amount_inr = None
        self.calls[session.call_id].intent = LINK_INTENTS[settlement_type]
        link = PaymentLink(
            link_id=f"lnk-{uuid.uuid4().hex[:12]}",
            loan_id=loan_id,
            call_id=session.call_id,
            source=session.source,
            amount_inr=amount_inr,
            settlement_type=settlement_type,
            upi_url=f"upi://pay?pa=collections@cymbalbank&am={amount_inr}&tn={loan_id}",
            created_at=self.now(),
        )
        self.links.append(link)
        telemetry.event(
            "payment_link_sent",
            link_id=link.link_id,
            loan_id=loan_id,
            call_id=session.call_id,
            amount_inr=amount_inr,
            settlement_type=settlement_type,
            phone=_mask(row["phone"]),
            channel="sms+whatsapp (mocked)",
        )
        result = {
            "ok": True,
            "link_id": link.link_id,
            "amount_inr": amount_inr,
            "settlement_type": settlement_type,
            "status": st.status.value,
            "duplicate": False,
        }
        self.idempotent[key] = result
        return result

    def schedule_callback_reminder(
        self,
        session: Session,
        *,
        loan_id: Any,
        days_from_today: Any,
        notes: Any,
        promised_amount_inr: Any = None,
        customer_intent: Any = None,
        authorize: Authorize | None = None,
    ) -> dict[str, Any]:
        # 1. Shape.
        if not isinstance(loan_id, str) or not is_int(days_from_today) or not isinstance(notes, str):
            return fail("loan_id and notes must be text and days_from_today a whole number.")
        if promised_amount_inr is not None and not is_int(promised_amount_inr):
            return fail("promised_amount_inr must be a whole number.")
        if customer_intent is not None and (
            not isinstance(customer_intent, str) or customer_intent not in CALLBACK_INTENTS
        ):
            return fail(f"customer_intent must be one of {', '.join(CALLBACK_INTENTS)}.")
        if len(notes) > MAX_TEXT:
            return fail(f"notes can have at most {MAX_TEXT} characters.")
        # 2-3. Binding and active call.
        if err := self._precheck(session, loan_id):
            return err
        # 4. No idempotency key: D-7 covers links and escalations only.
        st, due = self.state[loan_id], self.rows[loan_id]["due_amount_inr"]
        # 5. Frozen (D-5).
        if st.calling_frozen:
            return fail("Calling is frozen on this account. The branch team will contact the customer.")
        # 6. Range and amount (D-4, D-3).
        if not 1 <= days_from_today <= 7:
            return fail("A callback must be 1 to 7 days from today.")
        if promised_amount_inr is not None and not 1 <= promised_amount_inr <= due:
            return fail(f"The promised amount must be between ₹1 and the due amount ({inr(due)}).")
        # Spec §3 Scenario B: the callback after a partial link has no promise. A promise that the
        # model computes would add to promised_within_7_days_inr in the KPIs.
        if promised_amount_inr is not None and st.status == S.PARTIAL_SETTLEMENT_AGREED:
            return fail("After a partial payment link, book the callback without a promised amount.")
        # 7. Status guard (D-6).
        if (st.status, Event.CALLBACK) not in TRANSITIONS:
            return fail(f"A callback is not allowed in status {st.status.value}.")
        # 7b. Evidence gate.
        if authorize and (
            rejected := authorize(
                "schedule_callback_reminder",
                {"customer_intent": customer_intent, "promised_amount_inr": promised_amount_inr},
            )
        ):
            return rejected
        # 8. Change state.
        callback_date = self.today() + timedelta(days=days_from_today)
        self._transition(
            loan_id,
            Event.CALLBACK,
            who="voice-bot",
            source=session.source,
            call_id=session.call_id,
            details={
                "next_callback_date": callback_date.isoformat(),
                "promised_amount_inr": promised_amount_inr,
                "customer_intent": customer_intent,
            },
        )
        st.next_callback_date = callback_date
        st.promised_amount_inr = promised_amount_inr
        st.callback_notes = notes
        rec = self.calls[session.call_id]
        # The last outcome sets the intent. A callback after a link in this call is for the remaining amount,
        # so the link intent stays. A frozen account gets no callback, so an escalation intent stays.
        if not any(lk.call_id == session.call_id for lk in self.links):
            rec.intent = CALLBACK_INTENTS[customer_intent or "NEEDS_MORE_TIME"]
        return {
            "ok": True,
            "next_callback_date": st.next_callback_date.isoformat(),
            "promised_amount_inr": promised_amount_inr,
            "status": st.status.value,
        }

    def escalate_to_branch_manager(
        self,
        session: Session,
        *,
        loan_id: Any,
        reason_category: Any,
        disputed_fee_inr: Any,
        customer_summary: Any,
        recommended_action: Any,
        authorize: Authorize | None = None,
    ) -> dict[str, Any]:
        # 1. Shape.
        texts = (customer_summary, recommended_action)
        if not isinstance(loan_id, str) or not is_int(disputed_fee_inr):
            return fail("loan_id must be text and disputed_fee_inr a whole number.")
        if not all(isinstance(t, str) and t.strip() for t in texts):
            return fail("customer_summary and recommended_action must be non-empty text.")
        if any(len(t) > MAX_TEXT for t in texts):
            return fail(f"customer_summary and recommended_action can have at most {MAX_TEXT} characters.")
        if not isinstance(reason_category, str) or reason_category not in REASON_CATEGORIES:
            return fail(f"reason_category must be one of {', '.join(REASON_CATEGORIES)}.")
        # 2-3. Binding and active call.
        if err := self._precheck(session, loan_id):
            return err
        # 4. Idempotency (D-7): one escalation for each (loan_id, call_id).
        key = ("escalate", loan_id, session.call_id)
        if key in self.idempotent:
            return {**self.idempotent[key], "duplicate": True}
        st, row = self.state[loan_id], self.rows[loan_id]
        # 5. Frozen (D-5).
        if st.calling_frozen:
            return fail("This account is already escalated to the branch team.")
        # 6. Fee range (D-12).
        if not 0 <= disputed_fee_inr <= row["due_amount_inr"]:
            return fail(f"disputed_fee_inr must be between 0 and {row['due_amount_inr']}.")
        # 7. Status guard (D-6).
        event, intent = REASON_CATEGORIES[reason_category]
        if (st.status, event) not in TRANSITIONS:
            return fail(f"An escalation is not allowed in status {st.status.value}.")
        # 7b. Evidence gate.
        if authorize and (
            rejected := authorize("escalate_to_branch_manager", {"reason_category": reason_category})
        ):
            return rejected
        # 8. Change state.
        branch_callback_date = self.today() + timedelta(days=BRANCH_CALLBACK_DAYS)
        self._transition(
            loan_id,
            event,
            who="voice-bot",
            source=session.source,
            call_id=session.call_id,
            details={
                "reason_category": reason_category,
                "branch_callback_date": branch_callback_date.isoformat(),
            },
        )
        # The branch team now owns the account. Clear the bot promise so that the account status does not
        # show it next to the branch callback date.
        st.next_callback_date = branch_callback_date
        st.promised_by = None
        st.promised_amount_inr = None
        st.callback_notes = None
        self.calls[session.call_id].intent = intent
        self.briefs.append(
            HandoffBrief(
                loan_id=loan_id,
                call_id=session.call_id,
                source=session.source,
                profile=_profile(row),
                reason_category=reason_category,
                customer_intent=intent,
                disputed_fee_inr=disputed_fee_inr,
                customer_summary=customer_summary,
                recommended_action=recommended_action,
                branch_callback_date=branch_callback_date,
                created_at=self.now(),
            )
        )
        result = {
            "ok": True,
            "status": st.status.value,
            "calling_frozen": True,
            "branch": row["branch"],
            "branch_callback_date": branch_callback_date.isoformat(),
            "duplicate": False,
        }
        self.idempotent[key] = result
        return result


def _predicate(spec: Any) -> Callable[[Any], bool]:
    if isinstance(spec, list):
        wanted = [_norm(x) for x in spec]
        return lambda v: _norm(v) in wanted
    if isinstance(spec, dict):
        if set(spec) == {"contains"} and isinstance(spec["contains"], str):
            needle = spec["contains"].lower()
            return lambda v: v is not None and needle in str(v).lower()
        if spec and set(spec) <= {"min", "max"}:
            lo, hi = spec.get("min"), spec.get("max")
            for bound in (lo, hi):
                if bound is not None and not isinstance(bound, int | float | str):
                    raise ValueError(f"Bad range bound: {bound!r}.")

            def in_range(v: Any) -> bool:
                if v is None or isinstance(v, bool):
                    return False
                try:
                    return (lo is None or v >= lo) and (hi is None or v <= hi)
                except TypeError:
                    return False

            return in_range
        raise ValueError(
            f"Bad filter {spec!r}. Use a value, a list, {{'min': n, 'max': n}} or {{'contains': 'text'}}."
        )
    wanted_one = _norm(spec)
    return lambda v: _norm(v) == wanted_one


def _norm(value: Any) -> Any:
    # Case-insensitive match for text, so "malayalam" matches "Malayalam".
    return value.casefold() if isinstance(value, str) else value


def _sums(views: list[dict[str, Any]]) -> dict[str, int]:
    return {f: sum(v[f] or 0 for v in views) for f in SUM_FIELDS}


def _pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def _mask(phone: str) -> str:
    return phone[:3] + "X" * (len(phone) - 5) + phone[-2:]


def _profile(row: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "customer_id",
        "loan_id",
        "customer_name",
        "branch",
        "pos_inr",
        "ltv_pct",
        "risk_tier",
        "months_due",
        "due_amount_inr",
        "bot_ask_amount_inr",
        "maturity_date",
        "language",
    ]
    return {k: row[k] for k in keys}


def _link_out(lk: PaymentLink) -> dict[str, Any]:
    return {
        "link_id": lk.link_id,
        "amount_inr": lk.amount_inr,
        "settlement_type": lk.settlement_type,
        "upi_url": lk.upi_url,
        "source": lk.source,
        "created_at": lk.created_at.isoformat(),
    }


def _call_out(rec: CallRecord, *, excerpt: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "call_id": rec.call_id,
        "loan_id": rec.loan_id,
        "source": rec.source,
        "call_attempt": rec.call_attempt,
        "started_at": rec.started_at.isoformat(),
        "ended_at": rec.ended_at.isoformat() if rec.ended_at else None,
        "end_reason": rec.end_reason,
        "outcome": rec.final_status.value if rec.final_status else None,
        "intent": rec.intent,
        "summary": rec.summary,
    }
    if excerpt:
        out["transcript_excerpt"] = rec.transcript[-6:]
    return out
