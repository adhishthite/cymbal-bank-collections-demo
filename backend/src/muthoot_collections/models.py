"""Status enum, store records and API request bodies (spec §7.3 C)."""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel


class AccountStatus(StrEnum):
    DUE_FOR_CALL = "DUE_FOR_CALL"
    CALL_IN_PROGRESS = "CALL_IN_PROGRESS"
    PAYMENT_LINK_SENT_TODAY = "PAYMENT_LINK_SENT_TODAY"
    PARTIAL_SETTLEMENT_AGREED = "PARTIAL_SETTLEMENT_AGREED"
    CALLBACK_SCHEDULED = "CALLBACK_SCHEDULED"
    DISPUTE_LOGGED = "DISPUTE_LOGGED"
    ESCALATED_BRANCH_HARDSHIP = "ESCALATED_BRANCH_HARDSHIP"
    NO_OUTCOME = "NO_OUTCOME"


class Event(StrEnum):
    TRIGGER = "trigger_outbound_collection_call"
    SEED = "seed_outcome"
    LINK_FULL = "generate_upi_payment_link(FULL_DUE)"
    LINK_PARTIAL = "generate_upi_payment_link(PARTIAL_TWO_MONTH)"
    CALLBACK = "schedule_callback_reminder"
    ESCALATE_DISPUTE = "escalate_to_branch_manager(PENAL_FEE_DISPUTE)"
    ESCALATE_HARDSHIP = "escalate_to_branch_manager(other)"
    CALL_END = "call_end"


TERMINAL = {
    AccountStatus.PAYMENT_LINK_SENT_TODAY,
    AccountStatus.PARTIAL_SETTLEMENT_AGREED,
    AccountStatus.CALLBACK_SCHEDULED,
    AccountStatus.DISPUTE_LOGGED,
    AccountStatus.ESCALATED_BRANCH_HARDSHIP,
}
FROZEN = {AccountStatus.DISPUTE_LOGGED, AccountStatus.ESCALATED_BRANCH_HARDSHIP}


@dataclass
class AccountState:
    status: AccountStatus
    call_attempt: int
    active_call_id: str | None = None
    promised_by: datetime | None = None
    next_callback_date: date | None = None
    promised_amount_inr: int | None = None
    callback_notes: str | None = None
    language_override: str | None = None
    last_call_id: str | None = None

    @property
    def calling_frozen(self) -> bool:
        return self.status in FROZEN


@dataclass
class CallRecord:
    call_id: str
    loan_id: str
    source: str  # "live" or "seeded"
    call_attempt: int
    started_at: datetime
    ended_at: datetime | None = None
    end_reason: str | None = None
    final_status: AccountStatus | None = None
    transcript: list[dict[str, str]] = field(default_factory=list)
    summary: dict[str, Any] | None = None
    # Each Live tool call in the call, a rejected call too: name, args, ok, error, at.
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # The borrower intent, e.g. ["INT-02 NEEDS_MORE_TIME"]. The store sets it from the successful tool calls.
    intent: list[str] = field(default_factory=list)


@dataclass
class PaymentLink:
    link_id: str
    loan_id: str
    call_id: str
    source: str
    amount_inr: int
    settlement_type: str
    upi_url: str
    created_at: datetime


@dataclass
class HandoffBrief:
    loan_id: str
    call_id: str
    source: str
    profile: dict[str, Any]
    reason_category: str
    customer_intent: list[str]
    disputed_fee_inr: int
    customer_summary: str
    recommended_action: str
    branch_callback_date: date
    created_at: datetime


@dataclass(frozen=True)
class Session:
    """The call that a Live tool call belongs to. The backend binds it at connect (D-8)."""

    call_id: str
    loan_id: str
    source: str = "live"


class QueryRequest(BaseModel):
    filters: dict[str, Any] | None = None
    group_by: str | None = None
    sort_by: str | None = None
    descending: bool = True
    limit: int = 10


class TriggerRequest(BaseModel):
    loan_id: str


class LanguageRequest(BaseModel):
    loan_id: str
    language: str | None = None


class PairRequest(BaseModel):
    pair: str


class TelephonyRequest(BaseModel):
    mode: Literal["mobile", "twilio"]
