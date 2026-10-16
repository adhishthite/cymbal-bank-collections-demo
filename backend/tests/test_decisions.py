"""The evidence gates, driven through the phone sockets with a scripted Live and decision model.

Each script has the borrower speak and the bot answer in turns. A bot turn with no audio keeps the phone
call open. The last bot audio makes the test phone hang up.
"""

import asyncio

from fake_live import FakeSession
from google.genai import types
from test_e2e import SAJEEV, control_socket, pair, phone_call

LINK = {"loan_id": SAJEEV, "amount_inr": 3274, "settlement_type": "PARTIAL_TWO_MONTH"}
CALLBACK = {"loan_id": SAJEEV, "days_from_today": 3, "notes": "call center follow-up"}


def bot_text(s: FakeSession, text: str) -> None:
    """A bot turn with a transcript and no audio."""
    s._content(output_transcription=types.Transcription(text=text))
    s._content(turn_complete=True)


async def goodbye(s: FakeSession) -> None:
    # The bot audio makes the test phone hang up. The pause lets the background checks finish first.
    await asyncio.sleep(0.2)
    s.say("Goodbye.")


def run_call(client, script, loan_id: str = SAJEEV) -> list[dict]:
    """Run one call, on Sajeev's account by default. Return the monitor events of the call."""
    device = pair(client)
    events = client.app.state.monitor.subscribe()
    with control_socket(client, device) as ctrl:
        phone_call(client, ctrl, device, loan_id, script)
    out = []
    while not events.empty():
        out.append(events.get_nowait())
    return [e for e in out if e["type"] in ("decision", "consent", "gate", "audit", "tool")]


def of(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e["type"] == kind]


def test_link_needs_a_clear_yes(app_client, fake_decisions):
    fake_decisions.agrees("send 3,274 today", 3274)
    snapshots = []
    results = []

    async def script(s):
        await s.audio_in.get()
        bot_text(s, "Your interest due is ₹8,185. Please pay ₹3,274 today.")
        s.hear("theek hai, chalo, phir dekhta hoon")
        results.append(await s.call("generate_upi_payment_link", **LINK))
        bot_text(s, "Will you pay ₹3,274 today?")
        s.hear("yes, send 3,274 today")
        results.append(await s.call("generate_upi_payment_link", **LINK))
        snapshots.append(app_client.app.state.monitor.state())
        await goodbye(s)

    events = run_call(app_client, script)

    blocked, sent = results
    assert (blocked["ok"], blocked["code"]) == (False, "CONSENT_REQUIRED") and "No link was sent" in blocked[
        "guidance"
    ]
    assert sent["ok"] and not sent["duplicate"]
    assert [(g["allowed"], g["code"]) for g in of(events, "gate")] == [
        (False, "CONSENT_REQUIRED"),
        (True, None),
    ]
    first, second = of(events, "decision")[:2]
    assert (first["turn_id"], first["act"], first["current_ask_inr"]) == (1, "UNCLEAR", 3274)
    assert second["turn_id"] == 2
    assert [
        (c["turn_id"], c["decision"], c["amount_inr"]) for c in of(events, "consent") if c["turn_id"] == 2
    ] == [(2, "AGREE_TODAY", 3274)]
    # A page that connects during the call gets the panel data at once.
    panel = snapshots[0]["decisions"]
    assert [t["turn_id"] for t in panel["turns"]] == [1, 2]
    assert panel["latency"]["decision"]["n"] >= 2 and panel["latency"]["consent"]["n"] >= 2
    assert panel["latency"]["tool_wait"]["n"] == 2 and len(panel["events"]) >= 2


def test_refusal_after_an_earlier_yes_gets_no_link(app_client, fake_decisions):
    fake_decisions.agrees("yes, send 3,274 today", 3274)
    fake_decisions.borrower("changed my mind", reply_act="REFUSE")
    results = []

    async def script(s):
        await s.audio_in.get()
        bot_text(s, "Please pay ₹3,274 today.")
        s.hear("yes, send 3,274 today")
        await asyncio.sleep(0.3)
        bot_text(s, "Shall I send the link?")
        # The tool call arrives before the transcript of the refusal. It must not use the earlier yes.
        call = asyncio.create_task(s.call("generate_upi_payment_link", **LINK))
        await asyncio.sleep(0.1)
        s.hear("no, changed my mind")
        results.append(await call)
        await goodbye(s)

    run_call(app_client, script)

    assert (results[0]["ok"], results[0]["code"]) == (False, "CONSENT_REQUIRED")


def test_two_models_must_agree_when_both_answer(app_client, fake_decisions):
    fake_decisions.agrees("send 3,274 today", 3274)
    fake_decisions.borrower("no send 3,274", reply_act="REFUSE")
    results = []

    async def script(s):
        await s.audio_in.get()
        bot_text(s, "Please pay ₹3,274 today.")
        s.hear("no send 3,274 today")
        results.append(await s.call("generate_upi_payment_link", **LINK))
        bot_text(s, "Will you pay ₹3,274 today?")
        # The decision model is off. The consent check alone allows the link.
        fake_decisions.off_assess = True
        s.hear("yes send 3,274 today")
        results.append(await s.call("generate_upi_payment_link", **LINK))
        await goodbye(s)

    run_call(app_client, script)

    assert (results[0]["ok"], results[0]["code"]) == (False, "CONSENT_REQUIRED")
    assert results[1]["ok"]


def test_callback_after_two_refusals(app_client, fake_decisions):
    fake_decisions.borrower("cannot pay that", reply_act="REFUSE")
    fake_decisions.borrower("not even that", reply_act="REFUSE")
    results = []

    async def script(s):
        await s.audio_in.get()
        bot_text(s, "Please pay ₹8,185 today.")
        s.hear("no, I cannot pay that")
        results.append(
            await s.call("schedule_callback_reminder", **CALLBACK, customer_intent="CANNOT_PAY_FULL")
        )
        bot_text(s, "Can you pay ₹4,911 today?")
        s.hear("no, not even that")
        results.append(
            await s.call("schedule_callback_reminder", **CALLBACK, customer_intent="CANNOT_PAY_FULL")
        )
        await goodbye(s)

    events = run_call(app_client, script)

    assert (results[0]["ok"], results[0]["code"]) == (False, "REFUSALS_REQUIRED")
    assert results[1]["ok"]
    assert [d["refusals"] for d in of(events, "decision")][-1] == 2
    assert [(g["allowed"], g["unverified"]) for g in of(events, "gate")] == [(False, False), (True, False)]


def test_missing_evidence_blocks_links_but_not_callbacks(app_client, fake_decisions):
    results = []

    async def script(s):
        await s.audio_in.get()
        # The consent check is late: the wait at tool time ends and the link stays blocked.
        fake_decisions.delay = 1.0
        s.hear("yes, send it")
        results.append(await s.call("generate_upi_payment_link", **LINK))
        bot_text(s, "Will you pay ₹3,274 today?")
        # The decision model is off: a callback is allowed and the gate event says unverified.
        fake_decisions.delay, fake_decisions.off = 0, True
        s.hear("I cannot pay")
        results.append(
            await s.call("schedule_callback_reminder", **CALLBACK, customer_intent="CANNOT_PAY_FULL")
        )
        await goodbye(s)

    events = run_call(app_client, script)

    assert (results[0]["ok"], results[0]["code"]) == (False, "CONSENT_REQUIRED")
    assert results[1]["ok"]
    assert [(g["allowed"], g["unverified"]) for g in of(events, "gate")] == [(False, False), (True, True)]
    assert of(events, "decision")[-1]["status"] == "unavailable"


def test_duplicate_link_and_false_resend_claim(app_client, fake_decisions):
    fake_decisions.agrees("send 3,274 today", 3274)
    fake_decisions.bot("resent the link", claims_link_resent=0.95)

    async def script(s):
        await s.audio_in.get()
        bot_text(s, "Please pay ₹3,274 today.")
        s.hear("yes, send 3,274 today")
        await s.call("generate_upi_payment_link", **LINK)
        bot_text(s, "I have sent the link.")
        # The replay needs no new consent. The bot then says that it sent the link again.
        s.hear("send it again")
        await s.call("generate_upi_payment_link", **LINK)
        bot_text(s, "I have resent the link.")
        await goodbye(s)

    events = run_call(app_client, script)

    tools = of(events, "tool")
    assert [(t["ok"], t["duplicate"]) for t in tools] == [(True, False), (True, True)]
    assert [g["allowed"] for g in of(events, "gate")] == [True]
    assert [f for a in of(events, "audit") for f in a["flags"]] == ["CLAIMED_RESEND_ON_DUPLICATE"]


def test_cancelled_tool_call_makes_no_link(app_client, fake_decisions):
    """Live cancels a pending tool call when the borrower interrupts. The store must not change."""
    fake_decisions.agrees("send 3,274 today", 3274)

    async def script(s):
        await s.audio_in.get()
        bot_text(s, "Please pay ₹3,274 today.")
        s.hear("yes, send 3,274 today")
        fc = types.FunctionCall(id="fc-cancel", name="generate_upi_payment_link", args=LINK)
        s.out.put_nowait(types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[fc])))
        s.out.put_nowait(
            types.LiveServerMessage(
                tool_call_cancellation=types.LiveServerToolCallCancellation(ids=["fc-cancel"])
            )
        )
        await goodbye(s)
        assert s.responses.empty()

    events = run_call(app_client, script)

    assert of(events, "tool") == [] and of(events, "gate") == []
    assert not [lk for lk in app_client.app.state.store.links if lk.loan_id == SAJEEV]


def test_ask_amount_in_late_text_after_a_barge_in(app_client, fake_decisions):
    fake_decisions.agrees("haan", None)
    results = []

    async def script(s):
        await s.audio_in.get()
        bot_text(s, "Your interest due is ₹8,185. Can you pay it today?")
        s.hear("nahi, itna nahi")
        await asyncio.sleep(0.1)
        # The borrower cuts in. Live sends the amount of the new Ask after `interrupted`.
        s._content(output_transcription=types.Transcription(text="Okay. Then can you pay"))
        s._content(interrupted=True)
        s.hear("haan")
        s._content(output_transcription=types.Transcription(text=" ₹3,274 today?"))
        s._content(turn_complete=True)
        results.append(await s.call("generate_upi_payment_link", **LINK))
        await goodbye(s)

    run_call(app_client, script)

    # The yes answers the ₹3,274 Ask, not the ₹8,185 one before it.
    assert results[0]["ok"], results[0]


def test_a_spoken_year_is_not_the_ask(app_client, fake_decisions):
    # Amit Agarwal: floor ₹1,834, due ₹5,502, matured 28 September 2026. The year is inside the Ask range.
    amit = "GL4904098331"
    fake_decisions.agrees("yes, I will pay today", None)
    results = []

    async def script(s):
        await s.audio_in.get()
        bot_text(s, "Please clear ₹5,502 today. The loan matured on 28 September 2026, so it is all due now.")
        s.hear("yes, I will pay today")
        link = {"loan_id": amit, "amount_inr": 5502, "settlement_type": "FULL_DUE"}
        results.append(await s.call("generate_upi_payment_link", **link))
        await goodbye(s)

    events = run_call(app_client, script, amit)

    assert of(events, "decision")[0]["current_ask_inr"] == 5502
    assert results[0]["ok"], results[0]


def test_barge_in_speech_stays_one_borrower_turn(app_client, fake_decisions):
    async def script(s):
        await s.audio_in.get()
        bot_text(s, "Your interest due is ₹8,185.")
        s.hear("okay")
        # The borrower cuts in. Live sends turn_complete for the cut turn while the borrower still speaks.
        s._content(output_transcription=types.Transcription(text="Can you pay"))
        s._content(interrupted=True)
        s._content(input_transcription=types.Transcription(text="wait, wait,"))
        s._content(turn_complete=True)
        s.hear(" I already paid last week.")
        await goodbye(s)

    run_call(app_client, script)

    rec = list(app_client.app.state.store.calls.values())[-1]
    customer = [t["text"] for t in rec.transcript if t["role"] == "customer"]
    assert customer == ["okay", "wait, wait, I already paid last week."]
