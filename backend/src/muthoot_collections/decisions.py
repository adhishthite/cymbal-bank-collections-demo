"""Decision model, consent check and store gates for the live call.

Live decides alone if the borrower agreed. Code cannot check that. Thus a fast decision model reads each
borrower turn and each bot turn. A second model, the consent check, reads the borrower's yes. The store
rejects a tool call when this evidence does not support it. The gate policy is a pure function.
"""

import asyncio
import contextvars
import json
import os
import re
import statistics
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from google.genai import types
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, Score

from . import telemetry, vertex

Assess = Callable[[dict, dict], Awaitable[dict | None]]
Consent = Callable[[dict], Awaitable[dict | None]]

DECISION_TIMEOUT_S = 0.8
CONSENT_TIMEOUT_S = 2.5
CONSENT_MODEL = "gemini-3.5-flash-lite"

# The call sets these in its task context. The spans and logs of the two functions read them.
call_attrs: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("call_attrs", default=None)

LANGUAGES = "English, Hindi, Malayalam, Kannada, Tamil or mixed; native script or romanized"

BORROWER_QUESTIONS: dict[str, Any] = {
    "reply_act": Choice(
        instructions=(
            "How does `borrower` answer `bot_last`? An offered alternative amount counts as OFFER, "
            "also if the borrower rejects the earlier amount. Only mentioning an amount is not an offer."
        ),
        criteria={
            "ACCEPT": "A clear yes to the bot's proposal.",
            "OFFER": "Offers a specific other amount or part.",
            "REFUSE": "A clear no.",
            "PROMISE_LATER": "Will pay on a later day or period.",
            "QUESTION": "Asks something.",
            "UNCLEAR": "Hesitation, 'let me see', a complaint, or anything else.",
            "NOT_A_REPLY": "A greeting, an identity answer or noise.",
        },
    ),
    "payment_time": Choice(
        instructions=(
            "When does the borrower's payment offer or agreement apply? "
            "Do not infer today only because the bot asked for today."
        ),
        criteria={
            "TODAY": "The borrower will pay today.",
            "LATER": "The borrower will pay on a later day.",
            "CONDITIONAL": "The borrower will pay only if a condition is met.",
            "UNSPECIFIED": "The borrower offers to pay but gives no time.",
            "NO_PAYMENT_OFFER": "The borrower offers no payment.",
        },
    ),
    "addresses": Choice(
        instructions=(
            "Which proposal in `bot_last` does the reply answer? If both a payment and a callback date "
            "were proposed and the reply does not tell which, choose AMBIGUOUS."
        ),
        criteria={
            "PAYMENT_ASK": "The payment ask.",
            "CALLBACK_DATE": "The callback date.",
            "IDENTITY": "The identity check.",
            "OTHER": "Something else.",
            "AMBIGUOUS": "It is not clear which proposal.",
        },
    ),
    "hardship": Choice(
        instructions="What hardship, if any, does the borrower give as a reason?",
        criteria={
            "NONE": "No hardship.",
            "MEDICAL": "A medical reason.",
            "JOB_LOSS": "Loss of a job.",
            "BUSINESS": "A business problem.",
            "OTHER_EMERGENCY": "Another emergency.",
            "UNCLEAR": "A hardship that is not clear.",
        },
    ),
    "dispute": Choice(
        instructions="What does the borrower dispute?",
        criteria={
            "NONE": "Nothing.",
            "PAYMENT_NOT_CREDITED": "A payment that the borrower made is not credited.",
            "BALANCE": "The balance or the due amount.",
            "FEE_OR_PENALTY": "A fee or a penalty.",
            "UNCLEAR": "A dispute that is not clear.",
        },
    ),
    "wrong_person": Noul(
        instructions=(
            "Does the speaker say they are not the account holder, "
            "or that the account holder is not available?"
        )
    ),
    "wants_human": Noul(instructions="Does the borrower ask to speak to a person, the branch or a manager?"),
    "already_paid": Noul(instructions="Does the borrower say that the payment was already made?"),
    "language_request": Choice(
        instructions=(
            "Which language does the borrower explicitly ask the bot to speak? "
            "Script or loan words are not a request."
        ),
        criteria={
            "NONE": "No request.",
            "HINDI": "Hindi.",
            "MALAYALAM": "Malayalam.",
            "KANNADA": "Kannada.",
            "TAMIL": "Tamil.",
            "ENGLISH": "English.",
        },
    ),
    "reopens": Noul(
        instructions=(
            "Does the borrower now want to pay, or change what they said before, for example with "
            "'actually' or 'wait'? A courtesy word only is no."
        )
    ),
    "frustration": Score(
        instructions="How frustrated is the borrower?",
        criteria=["Calm or friendly.", "Mildly annoyed.", "Clearly frustrated.", "Angry or hostile."],
    ),
    "willingness": Score(
        instructions="How willing is the borrower to pay?",
        criteria=[
            "Will not pay anything.",
            "Reluctant, no commitment.",
            "Open to a smaller amount or a later date.",
            "Ready to pay today.",
        ],
    ),
}

# An answer is yes when the clause asserts a completed action. A question, a future plan or a negation is no.
BOT_QUESTIONS: dict[str, Any] = {
    "claims_link_sent": Noul(instructions="Does `bot` say that a payment link has been sent?"),
    "claims_link_resent": Noul(instructions="Does `bot` say that it sent a payment link again?"),
    "claims_callback_booked": Noul(instructions="Does `bot` say that a callback has been booked?"),
    "claims_escalated": Noul(
        instructions="Does `bot` say that the case was passed to the branch or a manager?"
    ),
    "system_text": Noul(
        instructions=(
            "Does `bot` say internal system or tool text to the borrower, for example "
            "'The requested action has been completed' or 'ok true'?"
        )
    ),
    "unsupported_promise": Noul(
        instructions=(
            "Does `bot` promise a waiver, a fee reversal, a guaranteed result or anything that the "
            "actions do not support?"
        )
    ),
}

_NUMBER = re.compile(r"\d[\d,]*")
_PHRASES = [
    (re.compile(r"\b(one month|ek mahina|oru masam)\b", re.I), "one month"),
    (re.compile(r"\b(two months|do mahine|randu masam)\b", re.I), "two months"),
    (re.compile(r"\bminimum\b", re.I), "minimum"),
    (re.compile(r"\b(full|poora|all)\b", re.I), "full"),
]


def borrower_questions(candidates: list[dict]) -> dict[str, Any]:
    """The borrower questions. The offered amount question exists only when the text has candidates."""
    if not candidates:
        return BORROWER_QUESTIONS
    criteria = {c["id"]: f"{c['amount_inr']} rupees (from: {c['from']})." for c in candidates}
    criteria["NONE"] = "The borrower offers no amount."
    criteria["AMBIGUOUS"] = "The borrower offers an amount, but it is not clear which one."
    offered = Choice(
        instructions=(
            "Select the amount the borrower offers to pay. "
            "Exclude amounts the borrower rejects, quotes, disputes or asks about."
        ),
        criteria=criteria,
    )
    return {**BORROWER_QUESTIONS, "offered_amount": offered}


def numbers_in(text: str) -> list[int]:
    out = []
    for m in _NUMBER.finditer(text):
        digits = m.group().replace(",", "")
        if digits:
            out.append(int(digits))
    return out


def ask_in(text: str, plan_amounts: list[int], floor: int, due: int, years: set[int]) -> int | None:
    """The last amount in a bot text that can be an Ask: a plan Ask, or an offer from floor to due."""
    found = []
    for m in _NUMBER.finditer(text):
        token = m.group()
        n = int(token.replace(",", ""))
        # The bot can say a date, e.g. "28 September 2026". A year has no comma. An amount of 2,026 does.
        if "," not in token.rstrip(",") and n in years:
            continue
        if n in plan_amounts or floor <= n <= due:
            found.append(n)
    return found[-1] if found else None


def amount_candidates(text: str, floor: int, due: int, monthly: int) -> list[dict]:
    """Amounts that the borrower text can mean. Code finds them, so the decision model only selects one."""
    raw = [(n, "number") for n in numbers_in(text) if n >= 100]
    for pattern, label in _PHRASES:
        if pattern.search(text):
            amount = {"one month": monthly, "two months": 2 * monthly, "minimum": floor, "full": due}[label]
            raw.append((amount, label))
    seen: set[int] = set()
    out = []
    for amount, label in raw:
        if amount not in seen:
            seen.add(amount)
            out.append({"id": f"A{len(out) + 1}", "amount_inr": amount, "from": label})
    return out


@dataclass
class Result:
    """The answer of one request, and the revision of the borrower turn that it read."""

    revision: int
    value: dict | None
    candidates: list[dict] = field(default_factory=list)


@dataclass
class BorrowerTurn:
    turn_id: int
    bot_last: str
    ask_inr: int | None
    fragments: list[str] = field(default_factory=list)
    revision: int = 0
    assessment: Result | None = None
    consent: Result | None = None
    tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    debounce: asyncio.Task | None = None

    @property
    def text(self) -> str:
        # Live sends transcription deltas with their own spaces. A space join splits "3,274" into "3 ,274".
        return " ".join("".join(self.fragments).split())


def borrower_state(turn: BorrowerTurn, row: dict[str, Any], candidates: list[dict]) -> dict:
    return {
        "bot_last": turn.bot_last,
        "borrower": turn.text,
        "current_ask_inr": turn.ask_inr,
        "floor_inr": row["bot_ask_amount_inr"],
        "due_inr": row["due_amount_inr"],
        "monthly_interest_inr": row["monthly_interest_inr"],
        "amount_candidates": candidates,
        "languages": LANGUAGES,
    }


# ---- the two model calls ----


def _record(kind: str, status: str, started: float, questions: int) -> int:
    ms = int((time.perf_counter() - started) * 1000)
    attrs = call_attrs.get() or {}
    with telemetry.tracer.start_as_current_span(
        f"decision.{kind}",
        attributes={**attrs, "kind": kind, "status": status, "latency_ms": ms, "questions": questions},
    ):
        telemetry.event(
            "decision_request", kind=kind, status=status, latency_ms=ms, questions=questions, **attrs
        )
    return ms


_client: AsyncTypeSafeClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


def reset() -> None:
    """Forget the client. Tests call this before each new event loop."""
    global _client, _client_loop
    _client = _client_loop = None


def _decision_client() -> AsyncTypeSafeClient:
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client_loop is not loop:
        _client, _client_loop = AsyncTypeSafeClient(), loop
    return _client


def _plain(resp: Any) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for qid, a in resp.choices.items():
        out[qid] = {"choice": a.choice, "confidence": a.confidence, "probabilities": dict(a.probabilities)}
    for qid, a in resp.nouls.items():
        out[qid] = {"p": a.noul}
    for qid, a in resp.scores.items():
        out[qid] = {"score": a.score, "confidence": a.confidence}
    return out


async def real_assess(state: dict, questions: dict) -> dict | None:
    """One decision request. Return None when the model is off, late or failed."""
    kind = "borrower" if "borrower" in state else "bot"
    started = time.perf_counter()
    if not os.environ.get("TYPESAFE_API_KEY"):
        _record(kind, "off", started, len(questions))
        return None
    status, answers = "ok", None
    try:
        async with asyncio.timeout(DECISION_TIMEOUT_S):
            answers = _plain(await _decision_client().system_one(state=state, questions=questions))
    except TimeoutError:
        status = "timeout"
    except Exception as exc:  # The call goes on without this evidence.
        status = "error"
        telemetry.log.warning(
            "decision_failed", extra={"fields": {**(call_attrs.get() or {}), "error": type(exc).__name__}}
        )
    _record(kind, status, started, len(questions))
    return answers


CONSENT_PROMPT = """\
A collections bot asked the borrower to pay Rs {ask} today. Classify the borrower's reply.
AGREE_TODAY: a clear yes to pay that amount today, or an offer of an exact amount today.
It is also AGREE_TODAY when the borrower pays an amount today and asks for a call later about the rest.
REFUSE: a clear no. LATER: a payment on a later date, with no payment today.
UNCLEAR: anything else (hesitation, complaint, question).
"Let me see" or "I will see" (dekhta hoon, nokkam) is hesitation, also after "okay": UNCLEAR.
"Okay, send the link" or "seri, link ayakku" is a yes: AGREE_TODAY.
The reply can be English, Hindi, Malayalam, Kannada, Tamil, Telugu or mixed.
"Haan", "seri", "haudu", "aamaa", "sari" and "avunu" are a yes only if no condition or delay follows.
An okay word, then "call later" (pinne vilikku, baad mein call karo), is LATER.
The bot said: "{bot_last}"
Reply: "{borrower}"
amount_inr: the amount the borrower agrees to or offers for today, as a whole number of rupees, or null.
"""

CONSENT_SCHEMA = types.Schema(
    type="OBJECT",
    required=["decision", "amount_inr"],
    properties={
        "decision": types.Schema(type="STRING", enum=["AGREE_TODAY", "REFUSE", "LATER", "UNCLEAR"]),
        "amount_inr": types.Schema(type="INTEGER", nullable=True),
    },
)


async def real_consent(state: dict) -> dict | None:
    """The consent check on Flash-Lite. Return None when the model is late or failed."""
    started = time.perf_counter()
    status, result = "ok", None
    prompt = CONSENT_PROMPT.format(
        ask=state["ask_inr"] or "the amount in the bot's last sentence",
        bot_last=state["bot_last"],
        borrower=state["borrower"],
    )
    try:
        async with asyncio.timeout(CONSENT_TIMEOUT_S):
            client = await vertex.client(os.environ.get("GENAI_LOCATION_CONSENT", "global"))
            resp = await client.aio.models.generate_content(
                model=CONSENT_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=CONSENT_SCHEMA,
                    temperature=0,
                    thinking_config=types.ThinkingConfig(thinking_level="LOW"),
                ),
            )
        data = json.loads(resp.text)
        amount = data.get("amount_inr")
        result = {
            "decision": data["decision"],
            "amount_inr": amount if isinstance(amount, int) and not isinstance(amount, bool) else None,
        }
    except TimeoutError:
        status = "timeout"
    except Exception as exc:  # The call goes on without this evidence.
        status = "error"
        telemetry.log.warning(
            "consent_failed", extra={"fields": {**(call_attrs.get() or {}), "error": type(exc).__name__}}
        )
    _record("consent", status, started, 1)
    return result


# ---- reading the answers ----


def _prob(answer: dict, key: str) -> float:
    probabilities = answer.get("probabilities") or {}
    return probabilities.get(key, answer.get("confidence", 0.0) if answer.get("choice") == key else 0.0)


def _p(answers: dict, qid: str) -> float:
    return answers.get(qid, {}).get("p", 0.0)


def _choice(answers: dict, qid: str) -> str | None:
    return answers.get(qid, {}).get("choice")


def offered_amount(result: Result) -> int | None:
    choice = _choice(result.value or {}, "offered_amount")
    return next((c["amount_inr"] for c in result.candidates if c["id"] == choice), None)


def is_refusal(result: Result, floor: int) -> bool:
    answers = result.value or {}
    act = answers.get("reply_act", {})
    if act.get("choice") == "REFUSE":
        return _prob(act, "REFUSE") >= 0.6
    offer = offered_amount(result)
    return act.get("choice") == "OFFER" and offer is not None and offer < floor


def fresh_answers(turn: BorrowerTurn) -> dict | None:
    """The assessment answers, only when they read the latest revision of the turn."""
    result = turn.assessment
    return result.value if result and result.revision == turn.revision else None


def refusal_count(turns: list[BorrowerTurn], floor: int) -> int:
    """Refusals over the call. Two unclear replies in a row count as one."""
    count = run = 0
    for turn in turns:
        if fresh_answers(turn) is None:
            run = 0
            continue
        if _choice(turn.assessment.value, "reply_act") == "UNCLEAR":
            run += 1
            if run == 2:
                count, run = count + 1, 0
            continue
        run = 0
        count += is_refusal(turn.assessment, floor)
    return count


def decision_event(call_id: str, turn: BorrowerTurn, result: Result, refusals: int, latency_ms: int) -> dict:
    a = result.value
    event: dict[str, Any] = {
        "type": "decision",
        "call_id": call_id,
        "turn_id": turn.turn_id,
        "revision": result.revision,
        "status": "ready" if a else "unavailable",
        "current_ask_inr": turn.ask_inr,
        "refusals": refusals,
        "latency_ms": latency_ms,
    }
    if a:
        act = a.get("reply_act", {})
        event |= {
            "act": act.get("choice"),
            "act_confidence": act.get("confidence"),
            "offer_inr": offered_amount(result),
            "payment_time": _choice(a, "payment_time"),
            "addresses": _choice(a, "addresses"),
            "hardship": _choice(a, "hardship"),
            "dispute": _choice(a, "dispute"),
            "wrong_person": _p(a, "wrong_person"),
            "wants_human": _p(a, "wants_human"),
            "already_paid": _p(a, "already_paid"),
            "reopens": _p(a, "reopens"),
            "language_request": _choice(a, "language_request"),
            "frustration": a.get("frustration", {}).get("score"),
            "willingness": a.get("willingness", {}).get("score"),
        }
    return event


def consent_event(call_id: str, turn: BorrowerTurn, result: Result, latency_ms: int) -> dict:
    value = result.value
    return {
        "type": "consent",
        "call_id": call_id,
        "turn_id": turn.turn_id,
        "revision": result.revision,
        "status": "ready" if value else "unavailable",
        "decision": value["decision"] if value else None,
        "amount_inr": value["amount_inr"] if value else None,
        "latency_ms": latency_ms,
    }


def audit_flags(answers: dict, actions: list[dict], done: set[str]) -> list[str]:
    """Compare the bot claims with the action ledger. `done` has the tools that worked in the call."""
    new_link = any(
        a["tool"] == "generate_upi_payment_link" and a["ok"] and not a["duplicate"] for a in actions
    )
    checks = [
        ("CLAIMED_LINK_WITHOUT_SEND", "claims_link_sent", "generate_upi_payment_link" not in done),
        ("CLAIMED_RESEND_ON_DUPLICATE", "claims_link_resent", not new_link),
        (
            "CLAIMED_CALLBACK_WITHOUT_BOOKING",
            "claims_callback_booked",
            "schedule_callback_reminder" not in done,
        ),
        ("CLAIMED_ESCALATION_WITHOUT_ACTION", "claims_escalated", "escalate_to_branch_manager" not in done),
        ("SYSTEM_TEXT", "system_text", True),
        ("UNSUPPORTED_PROMISE", "unsupported_promise", True),
    ]
    return [flag for flag, qid, unsupported in checks if unsupported and _p(answers, qid) >= 0.8]


# ---- store gates ----


def _reject(code: str, error: str, guidance: str) -> dict:
    return {"ok": False, "error": error, "guidance": guidance, "code": code}


def gate(
    tool: str,
    args: dict,
    turns: list[BorrowerTurn],
    floor: int,
    target: BorrowerTurn | None,
    partial_link: bool = False,
) -> tuple[dict | None, bool]:
    """Return (reject, unverified). `reject` is None to allow. `unverified` means the evidence is missing.

    `target` is the borrower turn that the tool call answers. A stale answer counts as a missing answer.
    """
    if any((a := fresh_answers(t)) is not None and _p(a, "wrong_person") >= 0.9 for t in turns):
        return _reject(
            "WRONG_PERSON",
            "The speaker is not the account holder.",
            "Do not discuss the account. Thank the speaker and end the call.",
        ), False
    if tool == "generate_upi_payment_link":
        fresh = target and target.consent and target.consent.revision == target.revision
        consent = target.consent.value if fresh else None
        # The consent check decides. A strong REFUSE from the decision model also blocks the link, so a
        # link needs two models to agree when both answer. The decision model being down must not block links.
        answers = fresh_answers(target) if target else None
        act = (answers or {}).get("reply_act", {})
        refused = act.get("choice") == "REFUSE" and _prob(act, "REFUSE") >= 0.8
        if consent and consent["decision"] == "AGREE_TODAY" and not refused:
            # Without an amount in the reply, a yes means the Ask that was current when the turn opened.
            amount = consent["amount_inr"] if consent["amount_inr"] is not None else target.ask_inr
            if amount is not None and amount == args.get("amount_inr"):
                return None, False
        return _reject(
            "CONSENT_REQUIRED",
            "The borrower has not clearly agreed to pay this amount today.",
            "No link was sent. If the borrower refused, continue the call plan. "
            "Otherwise ask the borrower to confirm the amount for today, then wait.",
        ), False

    intent = args.get("customer_intent")
    # After a partial link, a callback is the remainder callback, whatever intent the bot sets.
    if tool == "schedule_callback_reminder" and partial_link:
        return None, False
    # A callback with no intent is only the remainder callback after a partial link (prompt, # Tools).
    if tool == "schedule_callback_reminder" and intent is None:
        return _reject(
            "INTENT_REQUIRED",
            "A callback with no payment link needs customer_intent.",
            "No callback was booked. Book it again with customer_intent CANNOT_PAY_FULL after two "
            "refusals, or NEEDS_MORE_TIME with the promised amount.",
        ), False
    if tool == "schedule_callback_reminder" and intent in ("CANNOT_PAY_FULL", "NEEDS_MORE_TIME"):
        known = bool(turns) and all(fresh_answers(t) for t in turns)
        if intent == "CANNOT_PAY_FULL":
            if not known:
                return None, True
            if refusal_count(turns, floor) >= 2:
                return None, False
            return _reject(
                "REFUSALS_REQUIRED",
                "The borrower has not refused two times.",
                "No callback was booked. The borrower has not refused two times. "
                "Ask again if the borrower will pay the current amount today.",
            ), False
        last_two = turns[-2:]
        if any(
            (a := fresh_answers(t)) is not None
            and (_choice(a, "payment_time") == "LATER" or _choice(a, "reply_act") == "PROMISE_LATER")
            for t in last_two
        ):
            return None, False
        if not last_two or not all(fresh_answers(t) for t in last_two):
            return None, True
        return _reject(
            "PROMISE_REQUIRED",
            "The borrower has not promised a payment for a later date.",
            "No callback was booked. Ask the borrower for the date and the amount of the payment.",
        ), False
    return None, False


def summarize_latency(values: list[int]) -> dict:
    return {
        "n": len(values),
        "median_ms": int(statistics.median(values)) if values else None,
        "max_ms": max(values) if values else None,
    }
