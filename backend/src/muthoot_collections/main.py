"""FastAPI app: /api/agent/* for the GE agent, /api/admin/* for the operator, the /mobile phone
sockets, the /monitor stream, /health and the static frontend.

All routes are `async def`. They run on the event loop and call the synchronous store with
no `await` in between, so two requests never interleave inside a store method.
"""

import asyncio
import hmac
import os
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from starlette.background import BackgroundTask
from starlette.websockets import WebSocketDisconnect

from . import decisions, post_call, telemetry
from .decisions import Assess, Consent
from .live_session import (
    DEFAULT_LIVE_MODEL,
    Connect,
    LiveCall,
    Summarize,
    call_plan,
    current_span_context,
    end_call,
    vertex_connect,
)
from .models import LanguageRequest, PairRequest, QueryRequest, Session, TelephonyRequest, TriggerRequest
from .monitor import Monitor, sse
from .store import Store
from .telephony_mobile import CALLER, TOKEN_TTL, MobilePhone, Tokens, close_socket, read_auth, send_json
from .twilio_bridge import TWIML_HANGUP, UNANSWERED, Twilio, TwilioPhone, read_start, twiml_stream

KEEPALIVE_S = 15
# The stream token only has to last from the TwiML fetch to the stream start.
STREAM_TOKEN_TTL = timedelta(minutes=5)
DEFAULT_STATIC_DIR = Path(__file__).resolve().parents[3] / "frontend" / "out"


def env_flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).lower() == "true"


def require_token(authorization: str = Header(default="")) -> None:
    expected = os.environ.get("DEMO_AGENT_TOKEN", "")
    scheme, _, token = authorization.partition(" ")
    if (
        not expected
        or scheme.lower() != "bearer"
        or not hmac.compare_digest(token.encode(), expected.encode())
    ):
        raise HTTPException(status_code=401, detail="Bad or missing bearer token.")


def reply(result: dict[str, Any], error_status: int) -> JSONResponse:
    return JSONResponse(result, status_code=200 if result["ok"] else error_status)


def tag(loan_id: str, call_id: str | None = None) -> None:
    span = trace.get_current_span()
    span.set_attribute("loan_id", loan_id)
    if call_id:
        span.set_attribute("call_id", call_id)


def base_url(request: Request) -> str:
    return (os.environ.get("PUBLIC_BASE_URL") or str(request.base_url)).rstrip("/")


DEMO_SCENARIOS: list[dict[str, Any]] = [
    {
        "scenario": "A",
        "loan_id": "GL3506468235",
        "title": "Scenario A · Polite Reminder (Full Due Today)",
        "summary": (
            "Attempt 1 in Hindi. Borrower was traveling, agrees to clear the full ₹8,612 interest "
            "today via instant UPI payment link."
        ),
        "expected_outcome": "PAYMENT_LINK_SENT_TODAY",
        "sample_lines": [
            "Haan ji, main Kavita Saxena bol rahi hoon.",
            (
                "Main kaam ke liye state ke bahar travel kar rahi thi, isliye miss ho gaya. "
                "Main aaj hi pay kar dungi."
            ),
            "Haan, aaj end of day tak kar dungi. Link bhej dijiye.",
        ],
    },
    {
        "scenario": "B",
        "loan_id": "GL0106397512",
        "title": "Scenario B · 2-Month Floor Negotiation",
        "summary": (
            "Attempt 2 in Malayalam. 1-month counter-offer (₹1,637) is rejected by store invariant D-1; "
            "settles at the 2-month floor (₹3,274) + 3-day callback for remainder."
        ),
        "expected_outcome": "PARTIAL_SETTLEMENT_AGREED",
        "sample_lines": [
            "Yes, this is Sajeev George speaking.",
            "Business is tight before Diwali. I can do one month, 1,637 rupees.",
            "Okay, fine. Send me the link for 3,274 today.",
            "Yes, call me in 3 days about the rest. Thank you.",
        ],
    },
    {
        "scenario": "C",
        "loan_id": "GL6095694164",
        "title": "Scenario C · Language Switch & Hardship Escalation",
        "summary": (
            "Attempt 3 in Kannada → switches mid-call to Hindi. Medical emergency + ₹1,800 bounce fee "
            "dispute freezes automated calling and creates a Branch Credit Manager handoff brief."
        ),
        "expected_outcome": "ESCALATED_BRANCH_HARDSHIP",
        "sample_lines": [
            "Haudu, naanu Venkatesh Shetty.",
            (
                "Aapke branch staff ne mujhe 1,800 rupaye ki galat bounce penalty lagayi hai, "
                "aur abhi mera achanak hospital ka kharcha hua hai. Mujhe branch manager se milna hai."
            ),
            "Haan, please branch ko bata dijiye. Dhanyavaad.",
        ],
    },
]


def create_app(
    store: Store | None = None,
    connect: Connect | None = None,
    summarize: Summarize = post_call.summarize,
    twilio: Twilio | None = None,
    assess: Assess | None = None,
    consent: Consent | None = None,
) -> FastAPI:
    app = FastAPI(title="Cymbal Bank Collections Voice Backend")
    app.state.store = store or Store(
        call_window=os.environ.get("CALL_WINDOW_IST", "08:00-19:00"),
        call_window_bypass=env_flag("CALL_WINDOW_BYPASS", "true"),
        seed_outcomes=env_flag("SEED_OUTCOMES", "true"),
    )
    twilio = twilio or Twilio.from_env()
    # The mode can change at run time (POST /api/admin/telephony). /mobile always stays available as
    # the fallback. A restart goes back to TELEPHONY_MODE.
    telephony_mode = os.environ.get("TELEPHONY_MODE", "mobile")
    if telephony_mode == "twilio" and twilio is None:
        telephony_mode = "mobile"
    app.state.telephony_mode = telephony_mode
    app.state.twilio = twilio
    live_model = os.environ.get("LIVE_MODEL", DEFAULT_LIVE_MODEL)
    view_key = os.environ.get("VIEW_TOKEN_KEY", "")
    telemetry.event(
        "startup",
        telephony_mode=telephony_mode,
        twilio_configured=twilio is not None,
        live_model=live_model,
        view_token_key_set=bool(view_key),
        call_window_bypass=app.state.store.call_window_bypass,
        **{k: v for k, v in os.environ.items() if k.startswith("GENAI_LOCATION_")},
    )
    # Without the secret, a random key works for this process only. Links fail after a restart.
    tokens = Tokens(view_key or secrets.token_urlsafe(32), app.state.store.now)
    phone = app.state.phone = MobilePhone(tokens)
    monitor = app.state.monitor = Monitor(app.state.store, live_model)
    app.state.connect = connect
    # The trigger request carries the GE trace. The call socket continues that trace.
    call_traces: dict[str, trace.SpanContext | None] = {}

    def get_store() -> Store:
        return app.state.store

    def get_connect() -> Connect:
        if app.state.connect is None:
            app.state.connect = vertex_connect(live_model)
        return app.state.connect

    async def end_unanswered(call_id: str, reason: str) -> None:
        call_traces.pop(call_id, None)
        await phone.push(end_call(get_store(), monitor, call_id, reason))

    agent = APIRouter(prefix="/api/agent", dependencies=[Depends(require_token)])
    admin = APIRouter(prefix="/api/admin", dependencies=[Depends(require_token)])

    @agent.get("/calls-due")
    async def calls_due(limit: int = 10) -> dict[str, Any]:
        return get_store().calls_due(limit)

    @agent.post("/accounts/query")
    async def query(body: QueryRequest) -> JSONResponse:
        return reply(get_store().query_accounts(**body.model_dump()), 400)

    @agent.post("/calls")
    async def trigger(request: Request, body: TriggerRequest) -> JSONResponse:
        tag(body.loan_id)
        store = get_store()
        mode = app.state.telephony_mode
        if body.loan_id not in store.rows:
            return reply({"ok": False, "error": f"Unknown loan_id: {body.loan_id}."}, 404)
        if mode == "twilio":
            # D-10: check the allow-list before the call lock and before Twilio.
            number = twilio.number_for(body.loan_id)
            if number is None:
                return reply({"ok": False, "error": "No allow-listed phone number for this account."}, 409)
        # A ring needs a connected control socket, not only a paired credential.
        elif phone.control is None:
            return reply({"ok": False, "error": "No /mobile phone is paired."}, 409)
        result = store.start_call(body.loan_id)
        if not result["ok"]:
            return reply(result, 409)
        call_id = result["call_id"]
        tag(body.loan_id, call_id)
        call_traces[call_id] = current_span_context()
        monitor.publish_state()
        if mode == "twilio":
            twilio.ringing(call_id, end_unanswered)
            try:
                call_sid = await twilio.dial(number, call_id, base_url(request))
            except httpx.HTTPError as e:
                status = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else None
                # Only a 4xx is a sure rejection. After a 5xx or no response, Twilio can still have
                # placed the call (Twilio error 20500), so the webhooks or the stream deadline end it.
                rejected = status is not None and status < 500
                telemetry.log.warning(
                    "twilio_dial_failed" if rejected else "twilio_dial_unconfirmed",
                    extra={
                        "fields": {
                            "call_id": call_id,
                            "loan_id": body.loan_id,
                            "status": status,
                            "error": type(e).__name__,
                        }
                    },
                )
                if rejected and call_id in twilio.pending:
                    twilio.drop(call_id)
                    await end_unanswered(call_id, "dial_failed")
                if store.state[body.loan_id].active_call_id != call_id:
                    return reply(
                        {"ok": False, "call_id": call_id, "error": "The phone call could not start."}, 502
                    )
            else:
                twilio.claim(call_id, call_sid)
        else:
            st, row = store.state[body.loan_id], store.rows[body.loan_id]
            ring = {
                "type": "incoming_call",
                "call_id": call_id,
                "caller": CALLER,
                "customer_name": row["customer_name"],
                "language": st.language_override or row["language"],
            }
            await phone.ring(call_id, ring, end_unanswered)
        return reply({**result, "mode": mode}, 409)

    @agent.get("/accounts/{loan_id}")
    async def account(loan_id: str) -> JSONResponse:
        tag(loan_id)
        return reply(get_store().account_status(loan_id), 404)

    @agent.get("/accounts/{loan_id}/calls")
    async def account_calls(loan_id: str) -> JSONResponse:
        tag(loan_id)
        return reply(get_store().account_calls(loan_id), 404)

    @agent.get("/calls/{call_id}")
    async def call_detail(call_id: str) -> JSONResponse:
        trace.get_current_span().set_attribute("call_id", call_id)
        return reply(get_store().call_detail(call_id), 404)

    @agent.get("/handoffs")
    async def handoffs() -> dict[str, Any]:
        return get_store().handoffs()

    @agent.get("/analysis")
    async def analysis() -> dict[str, Any]:
        return get_store().analysis()

    @admin.post("/reset")
    async def reset() -> dict[str, Any]:
        store = get_store()
        store.reset()
        return {"ok": True, "accounts": len(store.rows), "seeded": store.seed_outcomes}

    @admin.post("/language")
    async def language(body: LanguageRequest) -> JSONResponse:
        tag(body.loan_id)
        return reply(get_store().set_language(body.loan_id, body.language), 400)

    @admin.get("/telephony")
    async def telephony() -> dict[str, Any]:
        return {"ok": True, "mode": app.state.telephony_mode, "twilio_configured": twilio is not None}

    @admin.post("/telephony")
    async def set_telephony(body: TelephonyRequest) -> JSONResponse:
        if body.mode == "twilio" and twilio is None:
            return reply({"ok": False, "error": "Twilio is not configured."}, 409)
        telemetry.event("telephony_mode", before=app.state.telephony_mode, after=body.mode)
        app.state.telephony_mode = body.mode
        return reply({"ok": True, "mode": body.mode}, 409)

    @admin.post("/pair-link")
    async def pair_link(request: Request) -> dict[str, Any]:
        token, expires = phone.pair_link_token()
        return {"ok": True, "url": f"{base_url(request)}/mobile#pair={token}", "expires_at": expires}

    @admin.post("/monitor-link")
    async def monitor_link(request: Request) -> dict[str, Any]:
        token, expires = tokens.mint("view", "monitor", TOKEN_TTL)
        return {"ok": True, "url": f"{base_url(request)}/monitor#t={token}", "expires_at": expires}

    @app.post("/api/mobile/pair")
    async def mobile_pair(body: PairRequest) -> JSONResponse:
        device = await phone.pair(body.pair)
        if device is None:
            return reply({"ok": False, "error": "The pairing link is bad, expired or used."}, 401)
        return reply({"ok": True, "device": device}, 401)

    @app.websocket("/ws/mobile")
    async def ws_mobile(ws: WebSocket) -> None:
        await ws.accept()
        device = phone.device(await read_auth(ws))
        if device is None:
            await close_socket(ws, 4401)
            return
        phone.control = ws
        phone.controls.append(ws)
        phone.sockets[ws] = device
        try:
            if not await send_json(ws, {"type": "ready"}):
                return
            # A phone that reconnects during a ring gets the ring again.
            for message in phone.ring_messages():
                if not await send_json(ws, message):
                    return
            while True:
                msg = await ws.receive_json()
                if (
                    isinstance(msg, dict)
                    and msg.get("type") == "decline"
                    and phone.answer(msg.get("call_id"))
                ):
                    await end_unanswered(msg["call_id"], "declined")
        except (WebSocketDisconnect, ValueError):
            pass
        finally:
            phone.sockets.pop(ws, None)
            phone.controls.remove(ws)
            if phone.control is ws:
                # Fall back to another open socket of the paired device. Revoked sockets have left `sockets`.
                live = [c for c in phone.controls if phone.sockets.get(c) == phone.device_id]
                phone.control = live[-1] if live else None

    @app.websocket("/ws/live-call/{call_id}")
    async def ws_live_call(ws: WebSocket, call_id: str) -> None:
        await ws.accept()
        device = phone.device(await read_auth(ws))
        if device is None:
            await close_socket(ws, 4401)
            return
        store = get_store()
        rec = store.calls.get(call_id)
        if rec is None or store.state[rec.loan_id].active_call_id != call_id or not phone.answer(call_id):
            await close_socket(ws, 4403)
            return
        phone.sockets[ws] = device
        # LiveCall.run owns the call from here. Its `finally` ends the call and closes the socket.
        call = LiveCall(
            store=store,
            monitor=monitor,
            session=Session(call_id=call_id, loan_id=rec.loan_id),
            phone=ws,
            connect=get_connect(),
            live_model=live_model,
            summarize=summarize,
            notify_control=phone.push,
            parent=call_traces.pop(call_id, None),
            assess=assess or decisions.real_assess,
            consent=consent or decisions.real_consent,
        )
        try:
            await call.run()
        finally:
            phone.sockets.pop(ws, None)

    async def twilio_form(request: Request) -> dict[str, str]:
        """Check X-Twilio-Signature against PUBLIC_BASE_URL + path + query (spec §7.4 Signatures)."""
        params = parse_qsl((await request.body()).decode(), keep_blank_values=True)
        url = f"{base_url(request)}{request.url.path}"
        if request.url.query:
            url += f"?{request.url.query}"
        if twilio is None or not twilio.valid(url, params, request.headers.get("X-Twilio-Signature", "")):
            raise HTTPException(status_code=403, detail="Bad Twilio signature.")
        return dict(params)

    @app.post("/api/twilio/voice")
    async def twilio_voice(request: Request, call_id: str = "") -> Response:
        form = await twilio_form(request)
        store = get_store()
        rec = store.calls.get(call_id)
        if (
            rec is None
            or store.state[rec.loan_id].active_call_id != call_id
            or not twilio.claim(call_id, form.get("CallSid"))
        ):
            return Response(TWIML_HANGUP, media_type="application/xml")
        token, _ = tokens.mint("twilio", call_id, STREAM_TOKEN_TTL)
        ws_url = "wss://" + base_url(request).split("://", 1)[1] + "/ws/twilio"
        return Response(twiml_stream(ws_url, call_id, token), media_type="application/xml")

    @app.post("/api/twilio/status")
    async def twilio_status(request: Request, call_id: str = "") -> Response:
        form = await twilio_form(request)
        call_sid, status = form.get("CallSid", ""), form.get("CallStatus", "")
        # A call with a stream ends through LiveCall. This only ends a call that never got a stream.
        if status in UNANSWERED | {"completed"} and twilio.answered(call_id, call_sid):
            await end_unanswered(call_id, status.replace("-", "_") if status in UNANSWERED else "no_stream")
        return Response(status_code=204)

    @app.websocket("/ws/twilio")
    async def ws_twilio(ws: WebSocket) -> None:
        await ws.accept()
        start = await read_start(ws)
        params = (start or {}).get("customParameters") or {}
        call_id = params.get("call_id")
        store = get_store()
        rec = store.calls.get(call_id) if isinstance(call_id, str) else None
        if (
            twilio is None
            or rec is None
            or tokens.check(params.get("token"), "twilio") != call_id
            or store.state[rec.loan_id].active_call_id != call_id
            or not twilio.answered(call_id, start.get("callSid"))
        ):
            await close_socket(ws, 4403)
            return
        call = LiveCall(
            store=store,
            monitor=monitor,
            session=Session(call_id=call_id, loan_id=rec.loan_id),
            phone=TwilioPhone(ws, start["streamSid"]),
            connect=get_connect(),
            live_model=live_model,
            summarize=summarize,
            notify_control=phone.push,
            parent=call_traces.pop(call_id, None),
            assess=assess or decisions.real_assess,
            consent=consent or decisions.real_consent,
        )
        await call.run()

    @app.get("/api/monitor/stream")
    async def monitor_stream(authorization: str = Header(default="")) -> StreamingResponse:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or tokens.check(token, "view") is None:
            raise HTTPException(status_code=401, detail="Bad or missing view token.")

        # Check and reserve with no await in between.
        release = monitor.reserve()
        if release is None:
            raise HTTPException(status_code=503, detail="Too many monitor viewers.")

        async def events():
            q = monitor.subscribe()
            try:
                yield sse(monitor.state())
                while True:
                    try:
                        async with asyncio.timeout(KEEPALIVE_S):
                            event = await q.get()
                    except TimeoutError:
                        # A comment line keeps proxies from closing an idle stream.
                        yield ": keepalive\n\n"
                        continue
                    if event is None:
                        return
                    yield sse(event)
            finally:
                monitor.unsubscribe(q)
                release()

        # The background task releases the slot if the generator never starts.
        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
            background=BackgroundTask(release),
        )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True}

    demo = APIRouter(prefix="/api/demo")

    @demo.get("/bootstrap")
    async def demo_bootstrap(request: Request) -> dict[str, Any]:
        s = get_store()
        view_token, _ = tokens.mint("view", "monitor", TOKEN_TTL)
        scenarios = []
        for meta in DEMO_SCENARIOS:
            lid = meta["loan_id"]
            if lid in s.rows:
                row_data = s.row(
                    lid,
                    [
                        "monthly_interest_inr",
                        "maturity_date",
                        "days_past_due",
                        "treatment_segment",
                        "last_contact_outcome",
                    ],
                )
                plan = call_plan(s.rows[lid], s.state[lid], s.today())
                scenarios.append(
                    {
                        **meta,
                        "account": row_data,
                        "rungs": [{"months": m, "amount_inr": a} for m, a in plan["rungs"]],
                        "floor_inr": plan["floor"] or row_data["bot_ask_amount_inr"],
                    }
                )
        return {
            "ok": True,
            "bank": "Cymbal Bank",
            "telephony_mode": app.state.telephony_mode,
            "phone_online": phone.control is not None,
            "phone_paired": phone.device_id is not None,
            "live_model": live_model,
            "call_window_bypass": s.call_window_bypass,
            "active_call": monitor.state()["active_call"],
            "monitor_token": view_token,
            "monitor_url": f"{base_url(request)}/monitor?t={view_token}#t={view_token}",
            "scenarios": scenarios,
            "queue": s.calls_due(limit=10),
            "analysis": s.analysis(),
            "handoffs": s.handoffs()["briefs"],
        }

    @demo.post("/pair-link")
    async def demo_pair_link(request: Request) -> dict[str, Any]:
        token, expires = phone.pair_link_token()
        return {
            "ok": True,
            "pair": token,
            "url": f"{base_url(request)}/mobile?pair={token}#pair={token}",
            "expires_at": expires,
        }

    @demo.post("/monitor-link")
    async def demo_monitor_link(request: Request) -> dict[str, Any]:
        token, expires = tokens.mint("view", "monitor", TOKEN_TTL)
        return {
            "ok": True,
            "token": token,
            "url": f"{base_url(request)}/monitor?t={token}#t={token}",
            "expires_at": expires,
        }

    @demo.post("/start-call")
    async def demo_start_call(body: TriggerRequest) -> JSONResponse:
        tag(body.loan_id)
        s = get_store()
        if body.loan_id not in s.rows:
            return reply({"ok": False, "error": f"Unknown loan_id: {body.loan_id}."}, 404)
        if phone.control is None:
            return reply(
                {
                    "ok": False,
                    "error": (
                        "No /mobile Web Phone tab is connected. "
                        "Click '1. Open & Auto-Pair /mobile Web Phone (New Tab)' first."
                    ),
                },
                409,
            )
        prev_bypass = s.call_window_bypass
        s.call_window_bypass = True
        try:
            result = s.start_call(body.loan_id)
        finally:
            s.call_window_bypass = prev_bypass
        if not result["ok"]:
            return reply(result, 409)
        call_id = result["call_id"]
        tag(body.loan_id, call_id)
        call_traces[call_id] = current_span_context()
        monitor.publish_state()
        st, row = s.state[body.loan_id], s.rows[body.loan_id]
        ring = {
            "type": "incoming_call",
            "call_id": call_id,
            "caller": CALLER,
            "customer_name": row["customer_name"],
            "language": st.language_override or row["language"],
        }
        await phone.ring(call_id, ring, end_unanswered)
        return reply({**result, "mode": "mobile"}, 409)

    @demo.post("/reset")
    async def demo_reset() -> dict[str, Any]:
        s = get_store()
        s.reset()
        monitor.publish_state()
        return {"ok": True, "accounts": len(s.rows), "seeded": s.seed_outcomes}

    @demo.post("/language")
    async def demo_language(body: LanguageRequest) -> JSONResponse:
        tag(body.loan_id)
        return reply(get_store().set_language(body.loan_id, body.language), 400)

    app.include_router(agent)
    app.include_router(admin)
    app.include_router(demo)
    # Mount the static export last, so that the API routes match first.
    static_dir = Path(os.environ.get("STATIC_DIR", DEFAULT_STATIC_DIR))
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health")
    return app


telemetry.setup()
app = create_app()
