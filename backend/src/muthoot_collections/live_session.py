"""Live voice call: Gemini Live session, tool dispatch, transcripts and call end (spec §5, §7.3 B, §7.4).

One LiveCall bridges one phone call socket and one Live session. The phone sends 16 kHz PCM. Live
sends 24 kHz PCM back. The store runs the three Live tools. The call ends in a `finally` path.
"""

import asyncio
import contextlib
import json
import os
import time
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import date
from typing import Any

from google.genai import types
from opentelemetry import context as otel_context
from opentelemetry import trace

from . import decisions, recordings, telemetry, vertex
from .decisions import Assess, BorrowerTurn, Consent, Result
from .models import AccountState, Session
from .monitor import Monitor
from .store import Store, fail, inr
from .telephony_mobile import SEND_TIMEOUT_S, close_socket, send_json

DEFAULT_LIVE_MODEL = "gemini-3.8-live"
# A male prebuilt Live voice.
LIVE_VOICE = "Charon"
LIVE_LANGUAGE_CODE = "en-IN"
# About 1 s of 24 kHz audio in 20-40 ms chunks. On overflow, the oldest chunk goes first.
AUDIO_QUEUE = 50
# Deadlines for the Live connect and setup, and for each send to Live. A timeout ends the call.
LIVE_SETUP_S = 10
LIVE_SEND_S = 5
# At a hangup, the writer gets this long to deliver the queued JSON messages (cards).
SHUTDOWN_S = 2
# Decision requests start this long after the latest transcript fragment of a borrower turn.
DEBOUNCE_S = 0.25
# Wait budgets for a tool call, from its arrival: a borrower fragment, then the consent check, then the decision model.
WAIT_FRAGMENT_S = 1.0
WAIT_CONSENT_S = 2.0
WAIT_DECISION_S = 0.6
# The bot speaks first on an outbound call. This text tells the model that the borrower answered.
CALL_CONNECTED = "CALL_CONNECTED"

Connect = Callable[[types.LiveConnectConfig], AbstractAsyncContextManager[Any]]
Summarize = Callable[[Any, dict[str, Any]], Awaitable[dict[str, Any]]]


def live_tools(live_model: str) -> types.Tool:
    behavior = "NON_BLOCKING" if "extended-thinking" in live_model else "BLOCKING"
    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="generate_upi_payment_link",
                description=(
                    "Sends a (mocked) SMS/WhatsApp UPI link for the full interest due (FULL_DUE), "
                    "or for a partial payment (PARTIAL_TWO_MONTH): any amount from the floor in the "
                    "call plan up to less than the due amount."
                ),
                behavior=behavior,
                parameters=types.Schema(
                    type="OBJECT",
                    required=["loan_id", "amount_inr", "settlement_type"],
                    properties={
                        "loan_id": types.Schema(type="STRING", description="Loan ID, e.g. GL0106397512"),
                        "amount_inr": types.Schema(type="INTEGER", description="Amount in INR"),
                        "settlement_type": types.Schema(
                            type="STRING", enum=["FULL_DUE", "PARTIAL_TWO_MONTH"]
                        ),
                    },
                ),
            ),
            types.FunctionDeclaration(
                name="schedule_callback_reminder",
                description="Books a callback 1 to 7 days from today, with an optional promised amount.",
                behavior=behavior,
                parameters=types.Schema(
                    type="OBJECT",
                    required=["loan_id", "days_from_today", "notes"],
                    properties={
                        "loan_id": types.Schema(type="STRING"),
                        "days_from_today": types.Schema(type="INTEGER", description="1 to 7"),
                        "promised_amount_inr": types.Schema(
                            type="INTEGER", description="Optional amount promised by the callback date"
                        ),
                        "notes": types.Schema(type="STRING"),
                        "customer_intent": types.Schema(
                            type="STRING",
                            enum=["NEEDS_MORE_TIME", "CANNOT_PAY_FULL"],
                            description="Optional. NEEDS_MORE_TIME if not set.",
                        ),
                    },
                ),
            ),
            types.FunctionDeclaration(
                name="escalate_to_branch_manager",
                description=(
                    "Freezes automated calling, stores a handoff brief for the branch team, "
                    "and ends negotiation."
                ),
                behavior=behavior,
                parameters=types.Schema(
                    type="OBJECT",
                    required=[
                        "loan_id",
                        "reason_category",
                        "disputed_fee_inr",
                        "customer_summary",
                        "recommended_action",
                    ],
                    properties={
                        "loan_id": types.Schema(type="STRING"),
                        "reason_category": types.Schema(
                            type="STRING",
                            enum=[
                                "MEDICAL_HARDSHIP",
                                "PENAL_FEE_DISPUTE",
                                "HARDSHIP_AND_FEE_DISPUTE",
                                "CUSTOMER_REQUESTED_HUMAN",
                                "PAYMENT_OR_AMOUNT_DISPUTE",
                            ],
                        ),
                        "disputed_fee_inr": types.Schema(
                            type="INTEGER", description="0 if no fee is disputed"
                        ),
                        "customer_summary": types.Schema(type="STRING"),
                        "recommended_action": types.Schema(type="STRING"),
                    },
                ),
            ),
        ]
    )


# Tool name -> (allowed arguments, required arguments, integer arguments), from the declarations.
TOOL_ARGS = {
    fd.name: (
        set(fd.parameters.properties),
        set(fd.parameters.required),
        {k for k, s in fd.parameters.properties.items() if s.type == types.Type.INTEGER},
    )
    for fd in live_tools(DEFAULT_LIVE_MODEL).function_declarations
}

RULES = """\
# Role
You are the AI collections assistant of Cymbal Bank. You call gold loan borrowers who have unpaid interest.
Your goals: collect the interest before it becomes a delinquency, help the borrower avoid an auction, and keep the relationship respectful.
Say "interest due". Do not say "overdue" or "default" unless days_past_due in the context is more than 0.
In the negotiation, obey the tone for this call_attempt first, then the play, then the other rules.
The identity, compliance and tool rules always apply.

# Call start
When you receive the text CALL_CONNECTED, speak first. Do not say this text.
In your first sentence, speak {language}. Say that you are the AI assistant of Cymbal Bank and that the call is recorded.
If the person only says "Hello?", "Who is this?" or "From where?", say the short introduction again once, calmly.
Then ask if you are speaking with the borrower, by the customer name, for example "Am I speaking with Mr. Sunil Pandey?".
Do not ask for a loan ID, digits or any other proof. This is an outbound call to the borrower's number.
Do not say any amount or loan detail before the person says yes.
If the person is not the borrower, for example a family member, do not share any loan detail. Ask for a good time to call the borrower. Then end the call politely. Do not call a tool.
If the borrower does not answer, or you cannot confirm the identity, do not call a tool. Say that you will try again later. Then stop talking.

# Call flow
1. Say the due amount and the months of interest that it covers. Ask for the payment today.
2. If the borrower does not agree and gives no reason, ask one open question about the reason.
3. Acknowledge the reason in a few words. Then make the next ask.
4. Speak as if the borrower will pay: "Please clear it today." Do not ask for permission to make an Ask. Do not say that you send a link before the borrower agrees.
5. Make the ask sound small and quick: "the link takes one minute".
6. Give each lower ask as help for the borrower, for today.
7. Do not accept "later". Get a date and an amount for each promise. If the borrower did not say the amount, ask it before you book. Ask one time for an earlier date, and wait for the answer. Then book the promise. Do not ask again.
8. Say the commitment again. Then say in one sentence what you recorded.

# Negotiation
Follow the call plan: give one lever, then make the current Ask.
With each Ask, say in one short sentence why this amount, from the "Why" of that Ask in the call plan, in your own words.
The main consequence is the interest that keeps adding up each month. Use the interest lever with the first Ask.
On a first or second call, stay on each Ask until the call plan lets you go lower. Each Ask is worth a real try.
On the final call, follow the play in the call plan. Do not hold, and do not repeat an Ask.
The offer rule comes first: when the borrower offers an amount of the floor or more for today, say yes at once and send the link for that amount. Do not hold, and do not ask for more.
A hold is the same Ask one more time with a new lever, after a refusal. Do a hold in this sequence:
1. Answer the reason that the borrower gave. Say in a few words that you heard it.
2. Give a lever that fits that reason. For example, for "business is slow", say what the delay costs.
3. Ask for the same amount again, in new words.
Give a lower Ask as help for the borrower, not as a defeat: "Then let us do this much today, so the interest stops."
An unclear reply to an Ask, for example "okay, let me see", "theek hai, dekhta hoon", a complaint or a question, is not a yes and not a refusal. Ask one time if the borrower will pay that amount today. Do not call a tool in that reply.
If the next reply is also unclear, count it as a refusal. A clear no also counts as a refusal.
A promise of an amount on a date is not a refusal, also after "no, not today". Book that promise.
After two refusals of the last Ask, or of a date when there is only one Ask, with no promise, book a callback in 3 days at once. It needs no yes. Then tell the borrower the date and stop the ask.
Say only amounts from the call plan or the amount that the borrower offered. Do not calculate amounts. For a remainder, say "the remaining amount".
Do not say "policy", "the system", "I cannot accept" or "minimum payment required". Give a lever as the reason.

# How you sound
Talk like a real person on a phone call, not like a script.
React to what the borrower just said before you ask for anything.
Use short, natural acknowledgements in the call language, for example "achha", "samajh sakta hoon", "haan ji" or "seri sir". Do not use the same one two times in a row.
Use the borrower's name now and then, not in each reply.
Say each amount one time in a reply.
Do not say a sentence that you already said. For a hold, use new words.
Give one fact, then ask one question. Do not lecture.

# Compliance
Do not threaten, shame, harass or abuse the borrower.
Do not contact or mention family, friends or employers.
Give consequences only as the levers in the call plan. For the CIBIL score, say "can". Never say "will" or "has".
Never say a credit score number. Never mention other lenders.
Mention statutory notices only if days_past_due is more than 0.
Never say that an auction is certain or immediate.
Stop the ask at once and call escalate_to_branch_manager if the borrower says that a payment is not credited, disputes the balance or a charge, reports a hardship (for example a hospital expense), or asks for a person.
No money or no work, with no medical or other emergency, is not a hardship. It is a refusal.

# Tools
Always use the loan ID from the context.
Send a link only after the borrower clearly offers that amount for today, or clearly says yes to it for today. An amount in a refusal, a question or a complaint is not an offer. Book any other callback only after a yes to the date. A complaint or a question is not a yes.
A link is for a payment today. For a payment on a later date, book a callback and send no link.
For the full due amount, use settlement_type FULL_DUE. For a lower amount, use PARTIAL_TWO_MONTH and that exact amount. Then ask if a callback date for the remaining amount suits the borrower. After a yes, book it at once with no promised_amount_inr and no customer_intent.
For a callback with no payment link, always set customer_intent:
- NEEDS_MORE_TIME if the borrower promises an amount by a date. Set promised_amount_inr to that amount.
- CANNOT_PAY_FULL for the callback after two refusals. Do not set promised_amount_inr.
For an escalation, set reason_category: PAYMENT_OR_AMOUNT_DISPUTE for a payment not credited or a disputed balance, PENAL_FEE_DISPUTE for a disputed charge only, MEDICAL_HARDSHIP for a hardship only, HARDSHIP_AND_FEE_DISPUTE for both, CUSTOMER_REQUESTED_HUMAN if the borrower only asks for a person. Set disputed_fee_inr to the disputed charge, or 0. Write customer_summary and recommended_action in English.
Do not say that you sent a link, booked a callback or escalated until the tool returns ok true.
After an escalation, tell the borrower that the branch will call on branch_callback_date from the tool result.
If a tool returns ok false with guidance, nothing changed. Follow the guidance. Do not call that tool again before the borrower answers.
If a tool returns ok false with no guidance, tell the borrower the correct limit from the error. Do not promise anything else.
If a tool returns next_step, do that step in your next reply.
If a tool returns duplicate true, you sent it before. Say that the link was sent earlier. Do not say that you sent it again.
After the tools, thank the borrower and say goodbye.
If the borrower speaks after the goodbye, answer in one short sentence in their language. Do not say tool results or other English system text.

# Tone for this call_attempt
{tone}

# Language
Start in {language}. Speak as Indian call center agents speak: natural code-mixed speech, not a literal translation.
Use the English words "payment", "link", "interest", "outstanding amount" and "call back", and "sir", "ma'am" or "ji".
You are a male agent. In Hindi and other languages with gendered verbs, use the male forms, for example "bol raha hoon", not "bol rahi hoon".
When you speak English, always use an Indian English accent and Indian phrasing. Never use an American or British accent.
Say amounts in words that are natural for the language.
If the borrower changes the language, reply in the new language from your next reply.
Keep each reply short: one to three sentences.

# Call plan
{plan}

# Account context
{context}
"""

TONES = {
    1: "This is the first reminder. Be courteous, warm and brief. Confirm the payment date.",
    2: (
        "This is the second call. Be firm, factual and consultative. Make no threats. "
        "Say that this is the second call. "
        "For the callback after a partial payment link, propose 3 days."
    ),
    3: (
        "This is the final call. Be empathetic and calm. Listen first. Do not argue. Do not push for payment. "
        "If there is a hardship or a dispute, escalate with a clear summary and a recommended action."
    ),
}

# Play by the first letter of treatment_segment: (instruction, lever order). The plan keeps only
# the levers that apply to the account, in this order.
NOTES = 'In callback notes, write "call center follow-up".'
PLAYS = {
    "A": (
        "Ask for the full due amount today. If the borrower cannot pay today, ask for a date within 7 days. "
        "Be gentle. Use one lever only. Do not hold.",
        ["interest", "calls", "cibil"],
    ),
    "B": (
        "Ask for the full due amount. Use one lever only. Keep the call short.",
        ["interest", "calls", "cibil"],
    ),
    "C": (
        "Hold each Ask one time with a new lever before the next Ask. " + NOTES,
        ["interest", "maturity", "broken_promise", "calls", "cibil"],
    ),
    "D": (
        "Ask for the full due amount today. If the borrower refuses, ask for the full amount in 2 to 3 days. "
        "Then ask for the full amount within 7 days. " + NOTES,
        ["interest", "npa", "broken_promise", "calls", "cibil"],
    ),
    "E": (
        "Ask for the full due amount today. If there is only one Ask, ask for a date within 7 days. Hold one time.",
        ["interest", "maturity", "broken_promise", "calls", "cibil"],
    ),
}


def call_plan(row: dict[str, Any], st: AccountState, today: date) -> dict[str, Any]:
    """Build the one negotiation plan for this call. Code computes every amount, so the model does not."""
    due, floor, month = row["due_amount_inr"], row["bot_ask_amount_inr"], row["monthly_interest_inr"]
    segment = row["treatment_segment"][0]
    play, order = PLAYS[segment]
    if st.call_attempt == 3:
        # The final call does not push, so the segment play with its holds does not apply.
        # Phone calls (2026-10-05): the hold rules made the bot push on final calls, and it refused to go lower
        # when the borrower asked for less. The final call listens first and says the floor one time.
        play = (
            "This is the final call. Do not push. Say the due amount one time. Then listen, and answer questions. "
            "If the borrower cannot pay it today, ask what amount or date works for them. "
            "Do not hold an Ask. Do not repeat an Ask." + (" " + NOTES if segment in "CD" else "")
        )
    # Rungs are (months, amount). Only a Bullet account with more than 2 months due has a floor below the due.
    rungs = [(row["months_due"], due)]
    # The final call does not push (spec §5), so it has no lower Asks. An offer of the floor or more still gets a link.
    if floor < due and st.call_attempt != 3:
        middle = (row["months_due"] + 2) // 2
        if segment != "B" and middle > 2:
            rungs.append((middle, middle * month))
        rungs.append((floor // month, floor))

    monthly = row["payment_scheme"] == "Monthly interest"
    maturity = date.fromisoformat(row["maturity_date"])
    facts = {
        # McKinsey (2026-10-05): Tier 3 borrowers care more about the interest that builds up than about
        # CIBIL. Thus this lever comes first in each play, and it shows the cost of a delay.
        "interest": f"The interest grows by {inr(month)} every month that it stays unpaid. "
        f"In 3 more months, the amount due becomes {inr(due + 3 * month)}. Paying today stops it from growing.",
        "cibil": "If this interest stays unpaid, it can affect your CIBIL score. A payment today protects it."
        + (" The account is already past due." if row["days_past_due"] > 0 else ""),
        "calls": "A payment of the full due amount today stops the reminder calls.",
    }
    if (maturity - today).days <= 60:
        facts["maturity"] = (
            f"The loan matures on {maturity:%d %B %Y}. Then the interest and the principal are both due."
            if maturity >= today
            else f"The loan matured on {maturity:%d %B %Y}. The interest and the principal are both due now."
        )
    if monthly and row["monthly_months_to_npa"] == 1:
        facts["npa"] = "One more missed month makes the account non-performing."
    # The lever says "on the last call", so the last contact must be a call with a promise. Kavita has
    # the count but no prior contact. The final call does not push, so it gets no such lever.
    if (
        st.call_attempt == 2
        and row["last_contact_outcome"] == "Promised to pay"
        and row["last_contact_channel"] in ("Voice bot", "Call center")
    ):
        facts["broken_promise"] = "On the last call you promised to pay, and the payment has not come in."

    after = None
    if monthly and row["enach_registered"] == "N":
        after = "The branch can set up auto-debit (eNACH), so the interest gets paid on time every month."
    elif monthly and row["enach_registered"] == "Y" and (row["missed_payments_6m"] or 0) > 0:
        after = (
            "A payment was missed in the last 6 months with auto-debit on, so keep the bank balance ready."
        )
    return {
        "play": play,
        "rungs": rungs,
        "floor": floor if floor < due else None,
        # Refusals of an Ask before the next Ask. Plays A and B do not hold. The final call has one Ask only.
        "refusals_per_ask": 1 if segment in "AB" else 2,
        "levers": {k: facts[k] for k in order if k in facts},
        "after_agreement": after,
    }


def _why(months: int, amount: int, all_months: int, due: int) -> str:
    # McKinsey dry run (2026-10-05): say why each amount is asked. The reason gives months, not rupees. The backend
    # reads the last plan amount in a bot reply as the Ask, so a rupee remainder would change the Ask.
    if amount >= due:
        return f"Why: it clears all {all_months} months of unpaid interest, so no interest due stays on the loan."
    return (
        f"Why: it clears {months} of the {all_months} months of unpaid interest. "
        f"Only {all_months - months} months stay unpaid. Say this in months, not as a rupee amount."
    )


def _plan_text(plan: dict[str, Any]) -> str:
    rungs = plan["rungs"]
    all_months, due = rungs[0]
    if len(rungs) == 1 and plan["floor"] is not None:
        floor = inr(plan["floor"])
        ask = "\n".join(
            [
                f"Ask: {inr(rungs[0][1])}, the full due amount. Do not ask for a lower amount, except the floor below.",
                _why(all_months, due, all_months, due),
                f"When the borrower offers an amount for today, compare it only with the floor, {floor}:",
                f"- {floor} or more: say yes at once and send the link for that amount. Do not ask for more.",
                f"- Less than {floor}, or a request for a lower amount: say one time, as help, that the borrower can pay {floor} today. "
                f"{_why(plan['floor'] // (due // all_months), plan['floor'], all_months, due)} "
                "Then ask when the borrower can pay.",
            ]
        )
    elif len(rungs) == 1:
        ask = (
            f"Ask: {inr(rungs[0][1])}, the full due amount. There is no part payment. Negotiate the date only. "
            + _why(all_months, due, all_months, due)
        )
    else:
        # Two separate lists. When one text gave the rungs and the offer rule together, Live used the middle
        # rung as a threshold for offers and refused offers above the floor.
        floor = inr(rungs[-1][1])
        asks = [
            f"- Ask 1: {inr(due)}, the full due ({all_months} months). {_why(all_months, due, all_months, due)}"
        ]
        # McKinsey dry run (2026-10-05): the bot went down one Ask at each refusal, which gave the amount away
        # too easily. A play that holds now needs two refusals of an Ask before the next Ask.
        times = "two times" if plan["refusals_per_ask"] == 2 else "one time"
        asks += [
            f"- Ask {i}: {inr(a)} ({m} months). Say it only after the borrower refuses Ask {i - 1} {times}. "
            + _why(m, a, all_months, due)
            for i, (m, a) in enumerate(rungs[1:], 2)
        ]
        ask = "\n".join(
            ["What you ask, in order. Make one Ask in a reply:", *asks]
            + [
                f"When the borrower offers an amount for today, compare it only with the floor, {floor}:",
                f"- {floor} or more: say yes at once and send the link for that amount. Do not ask for more.",
                f"- Less than {floor}: do not accept it. It is a refusal of your current Ask.",
            ]
        )
    # Live read English levers word for word in Hindi and Malayalam calls, and sometimes gave two at once.
    lines = [
        f"Play: {plan['play']}",
        ask,
        "Levers, in order. These are facts, not lines: say one in a reply, in the call language, in your own words.",
    ]
    lines += [f"- {fact}" for fact in plan["levers"].values()]
    if plan["after_agreement"]:
        lines.append(f"After the payment or the callback is agreed, say one time: {plan['after_agreement']}")
    return "\n".join(lines)


def system_instruction(row: dict[str, Any], st: AccountState, today: date) -> str:
    language = st.language_override or row["language"]
    due = row["due_amount_inr"]
    context = {
        "today": today.isoformat(),
        "customer_name": row["customer_name"],
        "loan_id": row["loan_id"],
        "branch": row["branch"],
        "scheme": row["payment_scheme"],
        "months_of_interest_due": row["months_due"],
        "due_amount": inr(due),
        "monthly_interest": inr(row["monthly_interest_inr"]),
        "days_past_due": row["days_past_due"],
        "call_attempt": st.call_attempt,
    }
    lines = "\n".join(f"- {k}: {v}" for k, v in context.items())
    return RULES.format(
        tone=TONES[st.call_attempt],
        language=language,
        plan=_plan_text(call_plan(row, st, today)),
        context=lines,
    )


def live_config(live_model: str, instruction: str) -> types.LiveConnectConfig:
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        system_instruction=instruction,
        tools=[live_tools(live_model)],
        # A male voice. The instruction tells the bot to use male grammatical forms too.
        # en-IN: without a language code, the English accent was American in 3 of 4 test greetings with
        # the real call instruction. With en-IN it was Indian in 4 of 4. Hindi stayed native.
        speech_config=types.SpeechConfig(
            language_code=LIVE_LANGUAGE_CODE,
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=LIVE_VOICE)
            ),
        ),
    )
    if "extended-thinking" in live_model:
        config.thinking_config = types.ThinkingConfig(thinking_level="low")
    return config


def vertex_connect(live_model: str) -> Connect:
    @asynccontextmanager
    async def connect(config: types.LiveConnectConfig):
        client = await vertex.client(os.environ.get("GENAI_LOCATION_LIVE", "us-central1"))
        async with client.aio.live.connect(model=live_model, config=config) as session:
            yield session

    return connect


def end_call(store: Store, monitor: Monitor, call_id: str, reason: str) -> dict[str, Any]:
    """Spec §7.4 call end, steps 1 and 2. Synchronous, so nothing can run between them."""
    store.end_call(call_id, reason)
    rec = store.calls.get(call_id)
    status = store.state[rec.loan_id].status.value if rec else None
    message = {"type": "call_ended", "call_id": call_id, "reason": reason, "status": status}
    telemetry.event(
        "call_ended", loan_id=rec.loan_id if rec else None, reason=reason, status=status, call_id=call_id
    )
    monitor.publish(message)
    monitor.publish_state()
    return message


def _whole(value: Any) -> Any:
    # Function call arguments arrive as JSON numbers. Accept 3274.0 as the integer 3274.
    return int(value) if isinstance(value, float) and value.is_integer() else value


class LiveCall:
    def __init__(
        self,
        *,
        store: Store,
        monitor: Monitor,
        session: Session,
        phone: Any,
        connect: Connect,
        live_model: str,
        summarize: Summarize,
        notify_control: Callable[[dict[str, Any]], Awaitable[Any]],
        parent: trace.SpanContext | None = None,
        assess: Assess | None = None,
        consent: Consent | None = None,
    ) -> None:
        self.store = store
        self.monitor = monitor
        self.session = session
        self.phone = phone
        self.connect = connect
        self.live_model = live_model
        self.summarize = summarize
        self.notify_control = notify_control
        self.parent = parent
        # Look the functions up at call time, so tests can replace them.
        self.assess = assess or decisions.real_assess
        self.consent = consent or decisions.real_consent
        self.ended: dict[str, Any] | None = None
        self.heard: list[str] = []
        self.said: list[str] = []
        # All phone output (audio bytes and JSON messages) goes through this queue, in order.
        self.out: deque[bytes | dict[str, Any]] = deque()
        self.out_ready = asyncio.Event()
        self.writer_idle = asyncio.Event()
        # Turn timing for the live.turn span. Times are time.time_ns().
        # heard_at is the last transcription fragment. It arrives late, often with the first bot audio.
        # first_heard_at and finished_at give two more anchors for the same borrower turn.
        self.heard_at: int | None = None
        self.first_heard_at: int | None = None
        self.finished_at: int | None = None
        # Live can send the transcription of interrupted speech after `interrupted`. It belongs to that turn.
        self.after_barge_in = False
        self.turn: dict[str, Any] | None = None
        # The turn whose first audio chunk is still in the phone queue.
        self.unsent: dict[str, Any] | None = None
        self.tool_ms = 0
        self.wait_ms = 0
        self.greeted = False
        # The evidence tracker (separate from the display transcript).
        self.row = store.rows[session.loan_id]
        today = store.today()
        plan = call_plan(self.row, store.state[session.loan_id], today)
        self.plan_amounts = [amount for _, amount in plan["rungs"]]
        # The bot only says dates near today: a maturity within 60 days, or a callback within 7 days.
        self.years = {today.year - 1, today.year, today.year + 1}
        self.b_turns: list[BorrowerTurn] = []
        self.open_turn: BorrowerTurn | None = None
        self.last_bot = ""
        self.current_ask: int | None = None
        # Tool results since the last bot turn ended, and the tools that worked in this call.
        self.actions: list[dict[str, Any]] = []
        self.done: set[str] = set()
        # Each tool call carries its evidence binding from the time it arrives:
        # (call, arrival time, the open turn or None, the number of turns at arrival).
        self.tool_queue: asyncio.Queue[tuple[types.LiveServerToolCall, float, BorrowerTurn | None, int]] = (
            asyncio.Queue()
        )
        self.evidence = asyncio.Event()
        self.background: set[asyncio.Task] = set()
        # Live cancels a pending tool call when the borrower interrupts. The worker skips these ids.
        self.cancelled: set[str] = set()
        self.gate_wait_ms = 0
        self.gate_target: BorrowerTurn | None = None

    @property
    def attrs(self) -> dict[str, str]:
        return {"call_id": self.session.call_id, "loan_id": self.session.loan_id}

    async def run(self) -> None:
        """Own the call from accept to close. The outer `finally` always ends the call."""
        ctx = trace.set_span_in_context(trace.NonRecordingSpan(self.parent)) if self.parent else None
        reason = "live_error"
        decisions.call_attrs.set(self.attrs)
        self.monitor.start_panel(self.session.call_id)
        with telemetry.tracer.start_as_current_span("live.call", context=ctx, attributes=self.attrs):
            try:
                loan_id = self.session.loan_id
                instruction = system_instruction(
                    self.store.rows[loan_id], self.store.state[loan_id], self.store.today()
                )
                async with contextlib.AsyncExitStack() as stack:
                    try:
                        async with asyncio.timeout(LIVE_SETUP_S):
                            live = await stack.enter_async_context(
                                self.connect(live_config(self.live_model, instruction))
                            )
                            await live.send_realtime_input(text=CALL_CONNECTED)
                        reason = await self._bridge(live)
                    finally:
                        self._end(reason)
                # Leaving the exit stack closes the Live session (call end step 3).
            except Exception:
                telemetry.log.exception("live_call_failed", extra={"fields": self.attrs})
            finally:
                try:
                    self._end(reason)
                    await send_json(self.phone, {k: v for k, v in self.ended.items() if k != "call_id"})
                    await self.notify_control(self.ended)
                    await self._summary()
                finally:
                    await close_socket(self.phone)
                # The durable copy has the summary in it, so it goes last.
                if self.store.calls.get(self.session.call_id) is not None:
                    await recordings.save_transcript(self.store.call_detail(self.session.call_id))

    def _end(self, reason: str) -> None:
        if self.ended is None:
            self._stop_background()
            self._flush_customer()
            self._flush_bot()
            self._close_turns()
            self.ended = end_call(self.store, self.monitor, self.session.call_id, reason)

    def _close_turns(self) -> None:
        # A hangup can come before turn_complete. The writer has stopped, so end the open spans now.
        if self.turn is not None:
            turn, self.turn = self.turn, None
            turn["ended"] = True
            self._finish_turn(turn)
        if self.unsent is not None:
            self._first_chunk_gone()

    async def _summary(self) -> None:
        """Call end step 4. The lock is already clear, so a failure here only loses the summary."""
        rec = self.store.calls.get(self.session.call_id)
        if rec is None or not rec.transcript:
            return
        try:
            rec.summary = await self.summarize(rec, self.store.rows[rec.loan_id])
        except Exception:
            telemetry.log.warning("post_call_summary_failed", exc_info=True, extra={"fields": self.attrs})

    async def _bridge(self, live: Any) -> str:
        """Run the uplink, the downlink and the phone writer. The call ends when one of them ends."""
        tasks = {
            "uplink": asyncio.create_task(self._uplink(live)),
            "downlink": asyncio.create_task(self._downlink(live)),
            "phone_writer": asyncio.create_task(self._phone_writer()),
            "tools": asyncio.create_task(self._tool_worker(live)),
        }
        try:
            await asyncio.wait(tasks.values(), return_when=asyncio.FIRST_COMPLETED)
            up, writer = tasks["uplink"], tasks["phone_writer"]
            phone_gone = writer.done() or (up.done() and not up.exception() and up.result() == "phone_closed")
            if not phone_gone:
                await self._drain(tasks)
        finally:
            for task in tasks.values():
                task.cancel()
            # Collect each result and exception. A cancel of run() itself goes on after this cleanup.
            results = dict(
                zip(tasks, await asyncio.gather(*tasks.values(), return_exceptions=True), strict=True)
            )
        for name, result in results.items():
            if isinstance(result, Exception):
                telemetry.log.warning(
                    "live_task_failed",
                    extra={"fields": {**self.attrs, "task": name, "error": type(result).__name__}},
                )
        up = results["uplink"]
        if isinstance(up, str):
            return up
        if isinstance(results["phone_writer"], Exception):
            return "phone_send_failed"
        return "live_error"

    async def _drain(self, tasks: dict[str, asyncio.Task]) -> None:
        """The phone socket still works: stop the producers, drop the audio, deliver the JSON messages."""
        stopped = [tasks[name] for name in ("uplink", "downlink", "tools")]
        for task in stopped:
            task.cancel()
        await asyncio.gather(*stopped, return_exceptions=True)
        self._drop_audio()
        if self.out:
            self.writer_idle.clear()
            self.out_ready.set()
        idle = asyncio.create_task(self.writer_idle.wait())
        await asyncio.wait(
            {idle, tasks["phone_writer"]}, timeout=SHUTDOWN_S, return_when=asyncio.FIRST_COMPLETED
        )
        idle.cancel()

    async def _uplink(self, live: Any) -> str:
        while True:
            msg = await self.phone.receive()
            if msg["type"] == "websocket.disconnect":
                return "phone_closed"
            if msg.get("bytes"):
                async with asyncio.timeout(LIVE_SEND_S):
                    await live.send_realtime_input(
                        audio=types.Blob(data=msg["bytes"], mime_type="audio/pcm;rate=16000")
                    )
            elif msg.get("text"):
                try:
                    if json.loads(msg["text"]).get("type") == "hangup":
                        return "hangup"
                except (ValueError, AttributeError):
                    continue

    async def _downlink(self, live: Any) -> None:
        # receive() stops at each turn end. Keep listening until the call ends.
        while True:
            async for msg in live.receive():
                await self._handle(live, msg)

    async def _handle(self, live: Any, msg: types.LiveServerMessage) -> None:
        if msg.go_away:
            # Calls are short, so there is no session resumption. Log the event to see if it occurs.
            telemetry.event("live_go_away", time_left=str(msg.go_away.time_left), **self.attrs)
        # The transcript goes first. Then a tool call in the same message finds the borrower text.
        if msg.server_content:
            self._server_content(msg.server_content)
        if msg.tool_call_cancellation:
            self.cancelled.update(msg.tool_call_cancellation.ids or [])
        if msg.tool_call:
            # Bind the call to its evidence now. A later borrower turn must not change what the call answers.
            self.tool_queue.put_nowait(
                (msg.tool_call, time.perf_counter(), self.open_turn, len(self.b_turns))
            )

    def _server_content(self, sc: types.LiveServerContent) -> None:
        if sc.input_transcription:
            if sc.input_transcription.text:
                self.heard.append(sc.input_transcription.text)
                self._caption("customer", self.heard)
                self.heard_at = time.time_ns()
                self.first_heard_at = self.first_heard_at or self.heard_at
                self._fragment(sc.input_transcription.text)
            if sc.input_transcription.finished:
                self.finished_at = time.time_ns()
                self._flush_customer()
                self._decide_now()
        if sc.model_turn:
            for part in sc.model_turn.parts or []:
                if part.inline_data and part.inline_data.data:
                    self._turn_audio(part.inline_data.data)
                    self._emit(part.inline_data.data)
        if sc.output_transcription and sc.output_transcription.text:
            if self.after_barge_in:
                self._extend_bot(sc.output_transcription.text)
            else:
                self.said.append(sc.output_transcription.text)
                self._caption("bot", self.said)
        if sc.interrupted:
            # Drop the queued bot audio. The marker goes after any send in progress.
            self._drop_audio()
            self._emit({"type": "interrupted"})
            self._end_turn(barge_in=True)
            self.after_barge_in = True
        if sc.turn_complete:
            if self.after_barge_in:
                # The barge-in already ended this bot turn. The borrower still speaks, so keep that turn open.
                self.after_barge_in = False
            else:
                self._end_turn(barge_in=False)

    # ---- turns and transcripts ----

    def _turn_audio(self, chunk: bytes) -> None:
        """Open a bot turn at its first audio chunk. The anchor is the last customer transcription chunk."""
        if self.turn is not None:
            return
        # A new bot turn. Without a turn_complete after the barge-in, its text must not join the old turn.
        self.after_barge_in = False
        if self.unsent is not None:
            self._first_chunk_gone()
        now, anchor, self.heard_at = time.time_ns(), self.heard_at, None
        first, finished = self.first_heard_at, self.finished_at
        self.first_heard_at = self.finished_at = None
        span = telemetry.tracer.start_span(
            "live.turn",
            start_time=anchor or now,
            attributes={**self.attrs, "customer_spoke": anchor is not None},
        )
        # A turn with no customer speech before it (e.g. the greeting) has no first_audio_ms.
        if anchor is not None:
            span.set_attribute("first_audio_ms", (now - anchor) // 1_000_000)
            if first is not None:
                span.set_attribute("first_audio_from_first_fragment_ms", (now - first) // 1_000_000)
            if finished is not None:
                span.set_attribute("first_audio_from_finished_ms", (now - finished) // 1_000_000)
        self.turn = {
            "span": span,
            "anchor": anchor,
            "first_chunk": chunk,
            "ended": False,
            "tool_ms": self.tool_ms,
            "wait_ms": self.wait_ms,
        }
        self.tool_ms = self.wait_ms = 0
        # The span ends when the turn ends and the writer has sent (or dropped) this first chunk.
        self.unsent = self.turn

    def _end_turn(self, *, barge_in: bool) -> None:
        # A customer turn without `finished` ends with the bot turn.
        self._flush_customer()
        text = self._flush_bot()
        if text or self.turn is not None:
            self._bot_turn_done(text)
        # Speech during a bot turn: after a barge-in it starts the next turn. After a complete
        # turn it is late transcription of the speech that the turn answered.
        if not barge_in:
            self.heard_at = self.first_heard_at = self.finished_at = None
        if self.turn is None:
            return
        if not self.greeted:
            self.greeted = True
            # The Twilio greeting gate opens after this message. The queue sends it after the greeting audio.
            # /mobile does not get it, because its wire protocol has no such message.
            if getattr(self.phone, "wants_greeting_done", False):
                self._emit({"type": "greeting_done"})
        turn, self.turn = self.turn, None
        turn["span"].set_attributes(
            {"tool_ms": turn["tool_ms"], "decision_wait_ms": turn["wait_ms"], "barge_in": barge_in}
        )
        turn["ended"] = True
        self._finish_turn(turn)

    def _first_chunk_sent(self, item: bytes | dict[str, Any]) -> None:
        turn = self.unsent
        if turn is None or item is not turn["first_chunk"]:
            return
        if turn["anchor"] is not None:
            turn["span"].set_attribute("first_audio_sent_ms", (time.time_ns() - turn["anchor"]) // 1_000_000)
        self._first_chunk_gone()

    def _first_chunk_gone(self) -> None:
        turn, self.unsent = self.unsent, None
        self._finish_turn(turn)

    def _finish_turn(self, turn: dict[str, Any]) -> None:
        if turn["ended"] and turn is not self.unsent:
            turn["span"].end()

    def _drop_audio(self) -> None:
        self.out = deque(item for item in self.out if not isinstance(item, bytes))
        # A dropped first chunk has no send time.
        if self.unsent is not None and self.unsent["first_chunk"] not in self.out:
            self._first_chunk_gone()

    def _flush_customer(self) -> None:
        self._append("customer", self.heard)

    def _flush_bot(self) -> str:
        return self._append("bot", self.said)

    def _extend_bot(self, text: str) -> None:
        """Add late transcription of interrupted speech to the last bot entry. It opens no new turn."""
        rec = self.store.calls.get(self.session.call_id)
        # The barge-in speech of the borrower can already sit after the cut bot entry.
        entry = next((t for t in reversed(rec.transcript) if t["role"] == "bot"), None) if rec else None
        if entry is None:
            return
        entry["text"] = " ".join((entry["text"] + text).split())
        self.last_bot = entry["text"]
        self.monitor.publish(
            {
                "type": "transcript_update",
                "call_id": self.session.call_id,
                "role": "bot",
                "text": entry["text"],
            }
        )
        # The late text can hold the amount of the Ask. A borrower turn already open answers this Ask too.
        self._set_ask(self.last_bot)
        if (turn := self.open_turn) is not None:
            turn.bot_last, turn.ask_inr = self.last_bot, self.current_ask
            # The late text can change the amount or the date. Results for the cut question are stale.
            # A new revision makes the gate wait for fresh ones.
            turn.revision += 1
            if turn.debounce is not None and not turn.debounce.done():
                turn.debounce.cancel()
                turn.debounce = self._spawn(self._debounced(turn))
            else:
                # The debounce already ran. Another 250 ms wait only takes gate budget.
                self._start_all(turn)

    def _set_ask(self, bot_text: str) -> None:
        floor, due = self.row["bot_ask_amount_inr"], self.row["due_amount_inr"]
        ask = decisions.ask_in(bot_text, self.plan_amounts, floor, due, self.years)
        if ask is None or ask == self.current_ask:
            return
        self.current_ask = ask
        # The /monitor ladder moves when the bot says the Ask, not when the borrower turn gets a decision.
        self.monitor.publish({"type": "ask", "call_id": self.session.call_id, "ask_inr": ask})

    def _caption(self, role: str, buf: list[str]) -> None:
        # The live caption on /monitor. The transcript event for the same turn replaces it.
        self.monitor.publish(
            {"type": "caption", "call_id": self.session.call_id, "role": role, "text": "".join(buf).strip()}
        )

    def _append(self, role: str, buf: list[str]) -> str:
        text = "".join(buf).strip()
        buf.clear()
        if not text:
            return ""
        rec = self.store.calls.get(self.session.call_id)
        if rec is not None:
            rec.transcript.append({"role": role, "text": text})
        self.monitor.publish(
            {"type": "transcript", "call_id": self.session.call_id, "role": role, "text": text}
        )
        return text

    # ---- evidence tracker ----

    def hear_text(self, text: str) -> None:
        """Add a fragment of borrower speech, as an input transcription does. The text harness calls this."""
        self._fragment(f" {text}")

    def _spawn(self, coro: Awaitable[None]) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.background.add(task)
        task.add_done_callback(self.background.discard)
        task.add_done_callback(self._log_failure)
        return task

    @staticmethod
    def _log_failure(task: asyncio.Task) -> None:
        # Log the exception type only. The text can hold borrower words.
        if not task.cancelled() and (exc := task.exception()) is not None:
            telemetry.log.warning("decision_task_failed", extra={"fields": {"error": type(exc).__name__}})

    def _stop_background(self) -> None:
        for task in list(self.background):
            task.cancel()

    def _fragment(self, text: str) -> None:
        if self.open_turn is None:
            self.open_turn = BorrowerTurn(len(self.b_turns) + 1, self.last_bot, self.current_ask)
            self.b_turns.append(self.open_turn)
        turn = self.open_turn
        turn.fragments.append(text)
        turn.revision += 1
        if turn.debounce is not None:
            turn.debounce.cancel()
        turn.debounce = self._spawn(self._debounced(turn))
        self.evidence.set()

    async def _debounced(self, turn: BorrowerTurn) -> None:
        await asyncio.sleep(DEBOUNCE_S)
        self._start_all(turn)

    def _decide_now(self) -> None:
        # The final transcription needs no debounce. A tool call can already wait for this evidence.
        turn = self.open_turn
        if turn is not None and turn.debounce is not None and not turn.debounce.done():
            turn.debounce.cancel()
            self._start_all(turn)

    def _start_all(self, turn: BorrowerTurn) -> None:
        # A request in flight is for an older revision. Cancel it, so that two slow requests never
        # run one after the other while a tool call waits (spec §8 R27).
        for kind in ("assess", "consent"):
            if (old := turn.tasks.get(kind)) is not None:
                old.cancel()
            turn.tasks[kind] = self._spawn(self._decide(turn, kind))

    async def _decide(self, turn: BorrowerTurn, kind: str) -> None:
        revision, started = turn.revision, time.perf_counter()
        try:
            candidates: list[dict] = []
            if kind == "assess":
                row = self.row
                candidates = decisions.amount_candidates(
                    turn.text, row["bot_ask_amount_inr"], row["due_amount_inr"], row["monthly_interest_inr"]
                )
                value = await self.assess(
                    decisions.borrower_state(turn, row, candidates), decisions.borrower_questions(candidates)
                )
            else:
                value = await self.consent(
                    {"ask_inr": turn.ask_inr, "bot_last": turn.bot_last, "borrower": turn.text}
                )
            ms = int((time.perf_counter() - started) * 1000)
            result = Result(revision, value, candidates)
            if kind == "assess":
                turn.assessment = result
                refusals = decisions.refusal_count(self.b_turns, self.row["bot_ask_amount_inr"])
                event = decisions.decision_event(self.session.call_id, turn, result, refusals, ms)
            else:
                turn.consent = result
                event = decisions.consent_event(self.session.call_id, turn, result, ms)
            self.monitor.publish(event)
            self.evidence.set()
        finally:
            # A newer request can already hold this slot.
            if turn.tasks.get(kind) is asyncio.current_task():
                del turn.tasks[kind]

    def _bot_turn_done(self, text: str) -> None:
        # The next borrower fragment opens a new turn.
        self.open_turn = None
        if not text:
            return
        self.last_bot = text
        self._set_ask(text)
        actions, self.actions = self.actions, []
        # Copy the inputs now. The audit runs later, and a new turn or tool call can change them.
        latest = self.b_turns[-1] if self.b_turns else None
        borrower, turn_id = (latest.text, latest.turn_id) if latest else ("", 0)
        self._spawn(self._audit(text, borrower, turn_id, list(actions), set(self.done)))

    async def _audit(
        self, text: str, borrower: str, turn_id: int, actions: list[dict[str, Any]], done: set[str]
    ) -> None:
        state = {
            "bot": text,
            "borrower_before": borrower,
            "actions_this_turn": actions,
            "plan_amounts_inr": self.plan_amounts,
        }
        started = time.perf_counter()
        answers = await self.assess(state, decisions.BOT_QUESTIONS)
        if answers is None:
            return
        self.monitor.publish(
            {
                "type": "audit",
                "call_id": self.session.call_id,
                "turn_id": turn_id,
                "flags": decisions.audit_flags(answers, actions, done),
                "latency_ms": int((time.perf_counter() - started) * 1000),
            }
        )

    async def _wait_for_evidence(
        self, names: list[str], arrival: float, bound: BorrowerTurn | None, turns_at_arrival: int
    ) -> tuple[int, BorrowerTurn | None]:
        """Wait for the evidence of the turn that the call answers. Return the wait in ms and that turn.

        All deadlines and the wait count from the arrival of the call, not from the start of the worker.
        """

        async def until(ready: Callable[[], bool], deadline: float) -> None:
            while not ready():
                left = deadline - time.perf_counter()
                if left <= 0:
                    return
                # No await sits between the check and the clear, so no wake-up is lost.
                self.evidence.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.evidence.wait(), left)

        deadline = arrival + WAIT_FRAGMENT_S
        # Without an open turn, the call can only answer a borrower turn that starts after it arrived.
        await until(lambda: bound is not None or len(self.b_turns) > turns_at_arrival, deadline)
        target = bound or (self.b_turns[turns_at_arrival] if len(self.b_turns) > turns_at_arrival else None)
        if target is not None:

            def fresh(kind: str) -> bool:
                result = getattr(target, kind)
                return result is not None and result.revision == target.revision

            if "generate_upi_payment_link" in names:
                deadline += WAIT_CONSENT_S
                await until(lambda: fresh("consent"), deadline)
            deadline += WAIT_DECISION_S
            await until(lambda: fresh("assessment"), deadline)
        return int((time.perf_counter() - arrival) * 1000), target

    def _authorize(self, name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        partial = any(
            lk.call_id == self.session.call_id and lk.settlement_type == "PARTIAL_TWO_MONTH"
            for lk in self.store.links
        )
        rejected, unverified = decisions.gate(
            name, args, self.b_turns, self.row["bot_ask_amount_inr"], self.gate_target, partial
        )
        code = rejected["code"] if rejected else None
        self.monitor.publish(
            {
                "type": "gate",
                "call_id": self.session.call_id,
                "tool": name,
                "allowed": rejected is None,
                "code": code,
                "unverified": unverified,
                "wait_ms": self.gate_wait_ms,
            }
        )
        telemetry.audit(
            who="decision-gate",
            what=f"gate:{name}",
            loan_id=self.session.loan_id,
            before=None,
            after={"allowed": rejected is None, "code": code, "unverified": unverified},
            source="live",
            call_id=self.session.call_id,
            at=self.store.now(),
        )
        return rejected

    # ---- tools ----

    async def _tool_worker(self, live: Any) -> None:
        """Run the tool calls one at a time. The receive loop does not wait for them."""
        while True:
            await self._tools(live, *await self.tool_queue.get())

    async def _tools(
        self,
        live: Any,
        tool_call: types.LiveServerToolCall,
        arrival: float,
        bound: BorrowerTurn | None,
        turns_at_arrival: int,
    ) -> None:
        function_calls = tool_call.function_calls or []
        wait_ms, self.gate_target = await self._wait_for_evidence(
            [fc.name for fc in function_calls], arrival, bound, turns_at_arrival
        )
        self.gate_wait_ms = wait_ms
        # A cancel can arrive during the evidence wait. Live expects no response for a cancelled call.
        if skipped := [fc.name for fc in function_calls if fc.id in self.cancelled]:
            telemetry.event("live_tool_cancelled", names=skipped, **self.attrs)
            function_calls = [fc for fc in function_calls if fc.id not in self.cancelled]
            if not function_calls:
                return
        started = time.perf_counter()
        calls = [(fc, self._run_tool(fc.name, dict(fc.args or {}), wait_ms)) for fc in function_calls]
        # A tool call before the first audio counts toward the next turn.
        elapsed = int((time.perf_counter() - started) * 1000)
        if self.turn is not None:
            self.turn["tool_ms"] += elapsed
            self.turn["wait_ms"] += wait_ms
        else:
            self.tool_ms += elapsed
            self.wait_ms += wait_ms
        # Queue the cards before the await. A hangup during the send then cannot lose a card for a
        # change that the store already made.
        for fc, result in calls:
            if result["ok"] and not result.get("duplicate"):
                self._emit(self._phone_card(fc.name, result))
        if any(result["ok"] for _, result in calls):
            self.monitor.publish_state()
        async with asyncio.timeout(LIVE_SEND_S):
            await live.send_tool_response(
                function_responses=[
                    types.FunctionResponse(id=fc.id, name=fc.name, response=result) for fc, result in calls
                ]
            )

    def _run_tool(self, name: str, args: dict[str, Any], wait_ms: int = 0) -> dict[str, Any]:
        """Run one Live tool in the store. Each call gets a span, a rejected call too."""
        with telemetry.tracer.start_as_current_span(f"live.tool.{name}", attributes=self.attrs) as span:
            if name not in TOOL_ARGS:
                result = fail(f"Unknown tool: {name}.")
            else:
                allowed, required, integers = TOOL_ARGS[name]
                args = {k: _whole(v) if k in integers else v for k, v in args.items()}
                if unknown := set(args) - allowed:
                    result = fail(f"Unknown arguments: {', '.join(sorted(unknown))}.")
                elif missing := required - set(args):
                    result = fail(f"Missing arguments: {', '.join(sorted(missing))}.")
                else:
                    result = getattr(self.store, name)(self.session, **args, authorize=self._authorize)
            span.set_attribute("ok", result["ok"])
        duplicate = bool(result.get("duplicate"))
        telemetry.event("live_tool", name=name, ok=result["ok"], error=result.get("error"), **self.attrs)
        self.store.record_tool_call(self.session.call_id, name, args, result)
        self.actions.append(
            {"tool": name, "ok": result["ok"], "duplicate": duplicate, "amount_inr": result.get("amount_inr")}
        )
        if result["ok"]:
            self.done.add(name)
        self.monitor.publish(
            {
                "type": "tool",
                "call_id": self.session.call_id,
                "name": name,
                "args": args,
                "ok": result["ok"],
                "error": result.get("error"),
                "duplicate": duplicate,
                "decision_wait_ms": wait_ms,
            }
        )
        # On the phone, Live often said goodbye after a partial link and skipped the callback for the rest.
        # The prompt rule alone was not enough. A step in the tool result is closer to the next reply.
        if (
            name == "generate_upi_payment_link"
            and result["ok"]
            and not duplicate
            and result.get("settlement_type") == "PARTIAL_TWO_MONTH"
            and "schedule_callback_reminder" not in self.done
        ):
            remaining = self.row["due_amount_inr"] - result["amount_inr"]
            result = {
                **result,
                "remaining_amount_inr": remaining,
                "next_step": (
                    "Tell the borrower that the link is sent. Then ask if a callback date for the "
                    f"remaining {inr(remaining)} suits them. Book the callback only after a yes."
                ),
            }
        return result

    def _phone_card(self, name: str, result: dict[str, Any]) -> dict[str, Any]:
        if name == "generate_upi_payment_link":
            link = next(lk for lk in self.store.links if lk.link_id == result["link_id"])
            return {
                "type": "payment_link",
                "amount_inr": link.amount_inr,
                "settlement_type": link.settlement_type,
                "upi_url": link.upi_url,
            }
        if name == "schedule_callback_reminder":
            return {
                "type": "callback",
                "date": result["next_callback_date"],
                "promised_amount_inr": result["promised_amount_inr"],
            }
        return {"type": "escalated", "branch": result["branch"]}

    # ---- phone ----

    def _emit(self, item: bytes | dict[str, Any]) -> None:
        """Queue phone output. On overflow, drop the oldest audio. Never drop a JSON message."""
        if isinstance(item, bytes) and sum(isinstance(x, bytes) for x in self.out) >= AUDIO_QUEUE:
            for i, queued in enumerate(self.out):
                if isinstance(queued, bytes):
                    del self.out[i]
                    if self.unsent is not None and queued is self.unsent["first_chunk"]:
                        self._first_chunk_gone()
                    break
        self.out.append(item)
        self.out_ready.set()

    async def _phone_writer(self) -> None:
        """The only sender to the phone during the call. A failed or stalled send ends the call."""
        while True:
            while not self.out:
                self.out_ready.clear()
                self.writer_idle.set()
                await self.out_ready.wait()
            self.writer_idle.clear()
            item = self.out.popleft()
            async with asyncio.timeout(SEND_TIMEOUT_S):
                if isinstance(item, bytes):
                    await self.phone.send_bytes(item)
                else:
                    await self.phone.send_json(item)
            self._first_chunk_sent(item)


def current_span_context() -> trace.SpanContext | None:
    ctx = trace.get_current_span(otel_context.get_current()).get_span_context()
    return ctx if ctx.is_valid else None
