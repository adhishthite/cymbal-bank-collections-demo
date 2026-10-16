"""End-to-end flows through the HTTP API and the /mobile sockets, with a scripted Live model.

The acceptance values come from spec §7.3 F. The call flows follow the §3 scenarios.
"""

import asyncio
import json
import time
from contextlib import contextmanager

import pytest
from conftest import live_session
from starlette.websockets import WebSocketDisconnect

KAVITA, SAJEEV, SHETTY = "GL3506468235", "GL0106397512", "GL6095694164"


def kpis(api) -> dict:
    return api.get("/api/agent/analysis").json()


def test_start_of_day_and_seeds_only_kpis(api):
    due = api.get("/api/agent/calls-due?limit=5").json()
    assert (due["total_accounts"], due["total_due_inr"]) == (1291, 7435486)
    assert due["by_call_attempt"] == {"1": 583, "2": 417, "3": 291}
    assert len(due["top"]) == 5
    assert {(h["loan_id"], h["callable"]) for h in due["handled_today"]} == {
        ("GL1814280043", False),
        ("GL9061256232", False),
    }

    k = kpis(api)
    assert (k["total_committed_inr"], k["committed_pct"]) == (24833, 78.3)
    assert (k["called_accounts"], k["outcome_rate_pct"], k["partial_agreements"]) == (2, 100.0, 1)
    assert {c["loan_id"]: c["intent"] for c in k["calls"]} == {
        "GL1814280043": ["INT-02 NEEDS_MORE_TIME"],
        "GL9061256232": ["INT-03 CANNOT_PAY_FULL"],
    }


def pair(client) -> str:
    url = client.post("/api/admin/pair-link").json()["url"]
    return client.post("/api/mobile/pair", json={"pair": url.split("#pair=")[1]}).json()["device"]


@contextmanager
def control_socket(client, device):
    with client.websocket_connect("/ws/mobile") as ws:
        ws.send_json({"type": "auth", "device": device})
        assert ws.receive_json() == {"type": "ready"}
        yield ws


def ring(client, ctrl, loan_id) -> str:
    r = client.post("/api/agent/calls", json={"loan_id": loan_id})
    assert r.status_code == 200, r.json()
    incoming = ctrl.receive_json()
    assert incoming["type"] == "incoming_call" and incoming["call_id"] == r.json()["call_id"]
    return incoming["call_id"]


def phone_call(client, ctrl, device, loan_id, script, hang_up_on_outcome=False) -> list[dict]:
    """Ring the phone, accept and stream mic audio. Hang up after the bot's first audio, or at once
    when the account leaves CALL_IN_PROGRESS. Return the JSON messages that the phone got."""
    client.app.state.connect.script = script
    call_id = ring(client, ctrl, loan_id)
    messages = []
    with client.websocket_connect(f"/ws/live-call/{call_id}") as call:
        call.send_json({"type": "auth", "device": device})
        call.send_bytes(b"\x00\x00" * 320)
        if hang_up_on_outcome:
            deadline = time.monotonic() + 5
            while client.get(f"/api/agent/accounts/{loan_id}").json()["status"] == "CALL_IN_PROGRESS":
                assert time.monotonic() < deadline
                time.sleep(0.01)
        else:
            while "bytes" not in (msg := call.receive()):
                messages.append(json.loads(msg["text"]))
        call.send_json({"type": "hangup"})
        # The backend closes the socket after the post-call summary.
        while (msg := call.receive())["type"] != "websocket.close":
            if "text" in msg:
                messages.append(json.loads(msg["text"]))
    assert ctrl.receive_json() == {**messages[-1], "type": "call_ended", "call_id": call_id}
    return messages


async def scenario_a(s):
    await s.audio_in.get()
    s.hear("I was traveling and missed it. I will pay it today.")
    await s.call("generate_upi_payment_link", loan_id=KAVITA, amount_inr=8612, settlement_type="FULL_DUE")
    s.say("I have sent the link for the full amount.")


async def scenario_b(s):
    await s.audio_in.get()
    s.hear("I can do one month, 1,637.")
    await s.call(
        "generate_upi_payment_link", loan_id=SAJEEV, amount_inr=1637, settlement_type="PARTIAL_TWO_MONTH"
    )
    s.hear("Okay, two months, 3,274 then.")
    await s.call(
        "generate_upi_payment_link",
        loan_id=SAJEEV,
        amount_inr=3274.0,  # The JSON number can arrive as a float.
        settlement_type="PARTIAL_TWO_MONTH",
    )
    await s.call("schedule_callback_reminder", loan_id=SAJEEV, days_from_today=3, notes="Remaining ₹4,911")
    s.say_interrupted("I have sent the link.", " I will call you again in 3 days.")
    s.say("")  # The barge-in drops the cut audio. This turn gives the phone its first audio.


async def scenario_c(s):
    await s.audio_in.get()
    s.hear("The branch charged me a wrong 1,800 penalty, and I had a hospital expense.")
    await s.call(
        "escalate_to_branch_manager",
        loan_id=SHETTY,
        reason_category="HARDSHIP_AND_FEE_DISPUTE",
        disputed_fee_inr=1800,
        customer_summary="Hospital expense; disputes a ₹1,800 bounce charge.",
        recommended_action="Check the charge; discuss a 2-month plan.",
    )
    s.say("I have passed your case to the branch team.")
    await asyncio.Event().wait()


def test_scripted_path_over_the_phone(app_client):
    """Pair, ring, and run Scenarios A, B and C through the phone sockets and a scripted Live model."""
    api = app_client
    device = pair(api)
    with control_socket(api, device) as ctrl:
        a = phone_call(api, ctrl, device, KAVITA, scenario_a)
        b = phone_call(api, ctrl, device, SAJEEV, scenario_b)
        # The borrower hangs up right after the escalation. The card still reaches the phone.
        c = phone_call(api, ctrl, device, SHETTY, scenario_c, hang_up_on_outcome=True)

    assert a == [
        {
            "type": "payment_link",
            "amount_inr": 8612,
            "settlement_type": "FULL_DUE",
            "upi_url": "upi://pay?pa=collections@cymbalbank&am=8612&tn=GL3506468235",
        },
        {"type": "call_ended", "reason": "hangup", "status": "PAYMENT_LINK_SENT_TODAY"},
    ]
    # B: the ₹1,637 counter-offer is refused (D-1); only the ₹3,274 link reaches the phone.
    session_b = api.app.state.connect.sessions[1]
    assert "₹3,274" in session_b.results[0]["error"]
    # After a partial link, the result tells the bot to offer a callback for the rest.
    assert session_b.results[1]["remaining_amount_inr"] == 4911 and "next_step" in session_b.results[1]
    assert [m["type"] for m in b] == ["payment_link", "callback", "interrupted", "call_ended"]
    assert (b[0]["amount_inr"], b[1]["date"], b[3]["status"]) == (
        3274,
        "2026-10-10",
        "PARTIAL_SETTLEMENT_AGREED",
    )
    assert c == [
        {"type": "escalated", "branch": "Hubballi - Civil Lines"},
        {"type": "call_ended", "reason": "hangup", "status": "ESCALATED_BRANCH_HARDSHIP"},
    ]

    # The bot speaks first: the model gets CALL_CONNECTED and the account context.
    assert session_b.texts == ["CALL_CONNECTED"]
    instruction = api.app.state.connect.configs[1].system_instruction
    assert "- Ask 3: ₹3,274 (2 months)." in instruction and "Start in Malayalam" in instruction

    last_call = api.get(f"/api/agent/accounts/{SAJEEV}").json()["last_call"]
    assert [t["role"] for t in last_call["transcript_excerpt"]] == ["customer", "customer", "bot"]
    assert last_call["summary"]["sentiment"] == "NEUTRAL"
    # The full call record: the whole transcript and every tool call, the rejected ₹1,637 link too.
    calls = api.get(f"/api/agent/accounts/{SAJEEV}/calls").json()["calls"]
    assert [c["outcome"] for c in calls] == ["PARTIAL_SETTLEMENT_AGREED"] and "transcript" not in calls[0]
    full = api.get(f"/api/agent/calls/{calls[0]['call_id']}").json()
    assert full["customer_name"] == "Sajeev George" and full["end_reason"] == "hangup"
    assert [t["role"] for t in full["transcript"]] == ["customer", "customer", "bot"]
    # Late transcription after the barge-in joins the cut bot turn. It is not a second bot turn.
    assert full["transcript"][-1]["text"] == "I have sent the link. I will call you again in 3 days."
    assert [(t["name"], t["args"].get("amount_inr"), t["ok"]) for t in full["tool_calls"]] == [
        ("generate_upi_payment_link", 1637, False),
        ("generate_upi_payment_link", 3274, True),
        ("schedule_callback_reminder", None, True),
    ]
    assert api.get("/api/agent/calls/nope").status_code == 404
    assert api.get("/api/agent/accounts/nope/calls").status_code == 404
    brief = api.get("/api/agent/handoffs").json()["briefs"][0]
    assert brief["loan_id"] == SHETTY and brief["transcript"][0]["role"] == "customer"
    # The branch calls back in 2 days. The bot gets the same date in the tool result.
    assert brief["branch_callback_date"] == "2026-10-09"
    assert api.app.state.connect.sessions[2].results[0]["branch_callback_date"] == "2026-10-09"

    k = kpis(api)
    assert {c["loan_id"]: c["intent"] for c in k["calls"] if c["source"] == "live"} == {
        KAVITA: ["INT-01 WILL_PAY_NOW"],
        SAJEEV: ["INT-03 CANNOT_PAY_FULL"],
        SHETTY: ["INT-05 HUMAN_HANDOFF", "INT-04 DISPUTES_CHARGES"],
    }
    assert (k["called_accounts"], k["called_due_inr"], k["outcome_rate_pct"]) == (5, 73448, 100.0)
    assert (k["calls_with_outcome_live"], k["calls_with_outcome_seeded"]) == (3, 2)
    assert (k["links_sent_today_inr"], k["promised_within_7_days_inr"]) == (18754, 17965)
    assert (k["total_committed_inr"], k["committed_pct"]) == (36719, 50.0)
    assert (k["partial_agreements"], k["escalations"]) == (2, 1)
    assert k["escalation_details"][0]["disputed_fee_inr"] == 1800

    # A frozen account cannot be called again; reset restores the seed state.
    assert api.post("/api/agent/calls", json={"loan_id": SHETTY}).status_code == 409
    api.post("/api/admin/reset")
    assert kpis(api)["total_committed_inr"] == 24833


def test_unanswered_calls_and_phone_auth(app_client):
    api = app_client
    no_phone = api.post("/api/agent/calls", json={"loan_id": KAVITA}).json()
    assert no_phone["error"] == "No /mobile phone is paired."

    link = api.post("/api/admin/pair-link").json()["url"].split("#pair=")[1]
    device = api.post("/api/mobile/pair", json={"pair": link}).json()["device"]
    assert api.post("/api/mobile/pair", json={"pair": link}).status_code == 401  # one-time link
    assert api.post("/api/mobile/pair", json={"pair": "ü.ü"}).status_code == 401

    api.app.state.phone.ring_timeout_s = 0.2
    with control_socket(api, device) as ctrl:
        # No answer in time, then a decline: both end with NO_OUTCOME, and the account stays callable.
        missed = ring(api, ctrl, KAVITA)
        assert ctrl.receive_json() == {
            "type": "call_ended",
            "call_id": missed,
            "reason": "no_answer",
            "status": "NO_OUTCOME",
        }
        # A second /mobile tab that opens and closes leaves the first tab online.
        with control_socket(api, device):
            pass
        declined = ring(api, ctrl, KAVITA)
        ctrl.send_json({"type": "decline", "call_id": declined})
        assert ctrl.receive_json()["reason"] == "declined"
        assert api.get(f"/api/agent/accounts/{KAVITA}").json()["status"] == "NO_OUTCOME"

        # The call socket needs the device credential and the active call.
        for call_id, cred, code in (
            (missed, device, 4403),
            (declined, "forged", 4401),
            (declined, "ü.ü", 4401),
        ):
            with api.websocket_connect(f"/ws/live-call/{call_id}") as call:
                call.send_json({"type": "auth", "device": cred})
                with pytest.raises(WebSocketDisconnect) as closed:
                    call.receive_json()
                assert closed.value.code == code

        # A new pairing replaces the phone during a call: the old device's sockets get 4401, the call ends.
        api.app.state.connect.script = None
        active = ring(api, ctrl, KAVITA)
        with api.websocket_connect(f"/ws/live-call/{active}") as call:
            call.send_json({"type": "auth", "device": device})
            new_device = pair(api)
            for ws in (ctrl, call):
                with pytest.raises(WebSocketDisconnect) as closed:
                    ws.receive_json()
                assert closed.value.code == 4401
            # The phone acknowledges the close. TestClient cancels the server task on exit, so wait for
            # the call end to finish first.
            call.send({"type": "websocket.disconnect", "code": 4401})
            deadline = time.monotonic() + 5
            while api.app.state.phone.sockets and time.monotonic() < deadline:
                time.sleep(0.01)
        assert api.get(f"/api/agent/accounts/{KAVITA}").json()["status"] == "NO_OUTCOME"
    with api.websocket_connect("/ws/mobile") as old:
        old.send_json({"type": "auth", "device": device})
        with pytest.raises(WebSocketDisconnect) as closed:
            old.receive_json()
        assert closed.value.code == 4401
    with control_socket(api, new_device) as ctrl:
        ringing = ring(api, ctrl, SAJEEV)
    # A phone that reconnects during a ring gets the ring again.
    with control_socket(api, new_device) as ctrl:
        assert ctrl.receive_json()["call_id"] == ringing

    # The monitor stream needs a view token from the monitor link.
    assert "/monitor#t=" in api.post("/api/admin/monitor-link").json()["url"]
    assert api.get("/api/monitor/stream", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_guardrails_during_a_call(api, store):
    s = live_session(api, SAJEEV)
    # Only one call at a time, and tools act only on the bound loan.
    assert (
        api.post("/api/agent/calls", json={"loan_id": KAVITA}).json()["error"]
        == "Another call is in progress."
    )
    assert not store.generate_upi_payment_link(
        s, loan_id=KAVITA, amount_inr=8612, settlement_type="FULL_DUE"
    )["ok"]
    # Callbacks are capped at 7 days; a replayed link is not sent twice.
    assert not store.schedule_callback_reminder(s, loan_id=SAJEEV, days_from_today=8, notes="")["ok"]
    # The borrower promises the full amount in 3 days, then pays 2 months' interest today.
    assert store.schedule_callback_reminder(
        s, loan_id=SAJEEV, days_from_today=3, notes="Full amount", promised_amount_inr=8185
    )["ok"]
    first = store.generate_upi_payment_link(
        s, loan_id=SAJEEV, amount_inr=3274, settlement_type="PARTIAL_TWO_MONTH"
    )
    again = store.generate_upi_payment_link(
        s, loan_id=SAJEEV, amount_inr=3274, settlement_type="PARTIAL_TWO_MONTH"
    )
    assert again["duplicate"] and again["link_id"] == first["link_id"]
    # The link replaces the promise, so the KPIs count ₹3,274 once and not ₹8,185 on top.
    assert api.get(f"/api/agent/accounts/{SAJEEV}").json()["promised_amount_inr"] is None
    k = kpis(api)
    assert (k["promised_within_7_days_inr"], k["total_committed_inr"]) == (17965, 24833 + 3274)
    # The callback for the remaining amount keeps the partial link intent.
    store.schedule_callback_reminder(
        s, loan_id=SAJEEV, days_from_today=3, notes="", customer_intent="NEEDS_MORE_TIME"
    )
    store.end_call(s.call_id, "hangup")
    assert [c["intent"] for c in kpis(api)["calls"] if c["source"] == "live"] == [["INT-03 CANNOT_PAY_FULL"]]
    # After the call ends, the tools are closed.
    assert not store.schedule_callback_reminder(s, loan_id=SAJEEV, days_from_today=3, notes="")["ok"]


def test_callback_intent_and_payment_dispute(api, store):
    """Scenario F (cannot pay, callback) and a promise that a payment dispute replaces."""
    ajay, anitha = "GL0780583622", "GL3649373616"
    s = live_session(api, ajay)
    assert not store.schedule_callback_reminder(
        s, loan_id=ajay, days_from_today=3, notes="", customer_intent="WILL_PAY"
    )["ok"]
    # The bot books tomorrow with a promise. The borrower then asks for 3 days and cannot promise an amount.
    assert store.schedule_callback_reminder(
        s, loan_id=ajay, days_from_today=1, notes="Pays tomorrow", promised_amount_inr=1000
    )["ok"]
    assert kpis(api)["promised_within_7_days_inr"] == 17965 + 1000
    assert store.schedule_callback_reminder(
        s, loan_id=ajay, days_from_today=3, notes="Call center follow-up", customer_intent="CANNOT_PAY_FULL"
    )["ok"]
    moved = api.get(f"/api/agent/accounts/{ajay}").json()
    assert {
        k: moved[k] for k in ("status", "next_callback_date", "promised_amount_inr", "callback_notes")
    } == {
        "status": "CALLBACK_SCHEDULED",
        "next_callback_date": "2026-10-10",
        "promised_amount_inr": None,
        "callback_notes": "Call center follow-up",
    }
    assert kpis(api)["promised_within_7_days_inr"] == 17965
    store.end_call(s.call_id, "hangup")

    # The borrower promises the full amount in 2 days, then says an earlier payment is not credited.
    s = live_session(api, anitha)
    store.schedule_callback_reminder(
        s, loan_id=anitha, days_from_today=2, notes="", promised_amount_inr=13222
    )
    assert kpis(api)["promised_within_7_days_inr"] == 17965 + 13222
    escalated = store.escalate_to_branch_manager(
        s,
        loan_id=anitha,
        reason_category="PAYMENT_OR_AMOUNT_DISPUTE",
        disputed_fee_inr=0,
        customer_summary="Says last month's payment is not credited.",
        recommended_action="Check the payment record.",
    )
    assert (escalated["status"], escalated["branch_callback_date"]) == ("DISPUTE_LOGGED", "2026-10-09")
    store.end_call(s.call_id, "hangup")

    status = api.get(f"/api/agent/accounts/{anitha}").json()
    assert (status["calling_frozen"], status["next_callback_date"], status["promised_amount_inr"]) == (
        True,
        "2026-10-09",
        None,
    )
    k = kpis(api)
    # The frozen account adds nothing to the committed KPIs.
    assert (k["promised_within_7_days_inr"], k["total_committed_inr"], k["escalations"]) == (17965, 24833, 1)
    assert {c["loan_id"]: c["intent"] for c in k["calls"] if c["source"] == "live"} == {
        ajay: ["INT-03 CANNOT_PAY_FULL"],
        anitha: ["INT-04 DISPUTES_CHARGES"],
    }


def test_unanswered_call_is_retryable(api, store):
    s = live_session(api, KAVITA)
    store.end_call(s.call_id, "no_answer")
    assert api.get(f"/api/agent/accounts/{KAVITA}").json()["status"] == "NO_OUTCOME"
    live_session(api, KAVITA)


def test_call_window(api, clock):
    clock.at = clock.at.replace(hour=7, minute=59)
    assert "08:00 to 19:00" in api.post("/api/agent/calls", json={"loan_id": KAVITA}).json()["error"]
    clock.at = clock.at.replace(hour=19, minute=0)
    assert api.post("/api/agent/calls", json={"loan_id": KAVITA}).status_code == 409
    clock.at = clock.at.replace(hour=8, minute=0)
    assert api.post("/api/agent/calls", json={"loan_id": KAVITA}).status_code == 200


def test_query_accounts(api):
    r = api.post(
        "/api/agent/accounts/query",
        json={
            "filters": {
                "language": "malayalam",
                "risk_tier": "High",
                "due_amount_inr": {"min": 5000},
            },
            "group_by": "city",
        },
    ).json()
    assert r["ok"] and r["matched"] == sum(g["count"] for g in r["groups"]) > 0
    assert all(row["due_amount_inr"] >= 5000 for row in r["rows"])

    by_name = api.post(
        "/api/agent/accounts/query",
        json={"filters": {"customer_name": "Venkatesh Shetty", "in_queue": True}},
    ).json()
    assert SHETTY in {row["loan_id"] for row in by_name["rows"]} and by_name["matched"] == 2

    assert api.post("/api/agent/accounts/query", json={"filters": {"nope": 1}}).status_code == 400


def test_language_override_and_auth(api):
    assert api.post("/api/admin/language", json={"loan_id": SAJEEV, "language": "Hindi"}).json()["ok"]
    assert api.get(f"/api/agent/accounts/{SAJEEV}").json()["language_override"] == "Hindi"
    assert api.get("/api/agent/analysis", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert api.get("/health", headers={"Authorization": ""}).status_code == 200


def test_cymbal_bank_rebrand_and_demo_cockpit_endpoints(app_client):
    api = app_client
    boot = api.get("/api/demo/bootstrap", headers={"Authorization": ""}).json()
    assert boot["ok"] and boot["bank"] == "Cymbal Bank"
    assert len(boot["scenarios"]) == 3
    assert [s["loan_id"] for s in boot["scenarios"]] == [KAVITA, SAJEEV, SHETTY]
    assert boot["scenarios"][1]["rungs"] == [
        {"months": 5, "amount_inr": 8185},
        {"months": 3, "amount_inr": 4911},
        {"months": 2, "amount_inr": 3274},
    ]

    # Pair via the browser demo endpoint without requiring a bearer token.
    pair_res = api.post("/api/demo/pair-link", headers={"Authorization": ""}).json()
    assert pair_res["ok"] and pair_res["pair"] in pair_res["url"]
    device = api.post("/api/mobile/pair", json={"pair": pair_res["pair"]}).json()["device"]

    with control_socket(api, device) as ctrl:
        dial = api.post(
            "/api/demo/start-call",
            json={"loan_id": KAVITA},
            headers={"Authorization": ""},
        ).json()
        assert dial["ok"] and dial["mode"] == "mobile"
        incoming = ctrl.receive_json()
        assert incoming["caller"] == "Cymbal Bank — AI Collections Desk"
        assert incoming["customer_name"] == "Kavita Saxena"

    reset_res = api.post("/api/demo/reset", headers={"Authorization": ""}).json()
    assert reset_res["ok"] and reset_res["accounts"] == 2000
