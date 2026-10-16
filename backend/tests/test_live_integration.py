"""Opt-in: the scripted scenarios against the real gemini-3.8-live on Vertex AI (spec §7.7 DoD M2 item 1).

Run with `make test-live`, or LIVE_TESTS=1 uv run pytest tests/test_live_integration.py.
LIVE_RUNS=n runs each scenario n times. The borrower turns go in as text with
send_realtime_input(text=...). The model output is probabilistic, so a run can fail.
"""

import array
import asyncio
import os
import random
import subprocess
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from google.genai import types
from judge import ADVISORY, judge

from muthoot_collections import decisions, post_call, vertex
from muthoot_collections.live_session import DEFAULT_LIVE_MODEL, LiveCall, vertex_connect
from muthoot_collections.models import Session
from muthoot_collections.monitor import Monitor
from muthoot_collections.store import Store

# The `live` marker keeps the real decision model and consent check (conftest.py).
pytestmark = [
    pytest.mark.skipif(os.environ.get("LIVE_TESTS") != "1", reason="set LIVE_TESTS=1"),
    pytest.mark.live,
]
RUNS = range(int(os.environ.get("LIVE_RUNS", "1")))
QUIET_S = 3.0
SILENCE = "<silence>"
SILENCE_S = 20
TURN_TIMEOUT_S = 45

SCENARIOS = {
    "A": (
        "GL3506468235",
        [
            "Haan ji, main Kavita Saxena bol rahi hoon.",
            "Namaste, main kaam ke liye state ke bahar travel kar rahi thi, isliye miss ho gaya. "
            "Main aaj hi pay kar dungi.",
            "Haan, aaj end of day tak kar dungi. Link bhej dijiye.",
            "Theek hai, dhanyavaad.",
        ],
        "PAYMENT_LINK_SENT_TODAY",
    ),
    "B": (
        "GL0106397512",
        [
            "Yes, this is Sajeev George speaking.",
            "Business is tight before Diwali. I will pay all the interest after 5 months.",
            "I can do one month, 1,637 rupees.",
            "Okay, fine. Send me the link for 3,274 today.",
            "Yes, call me in 3 days about the rest. Thank you.",
        ],
        "PARTIAL_SETTLEMENT_AGREED",
    ),
    # The 2026-10-05 phone test: an unclear reply to the floor, then a clear yes. The bot asks and waits.
    "B_unclear": (
        "GL0106397512",
        [
            "Yes, this is Sajeev George speaking.",
            "Business is tight before Diwali. I will pay all the interest after 5 months.",
            "I can do one month, 1,637 rupees.",
            "Theek hai, chalo. Phir dekhta hoon.",
            "Actually I want to pay the minimum amount, 3,274, today. Send me the link.",
            "Yes, call me in 3 days about the rest. Thank you.",
        ],
        "PARTIAL_SETTLEMENT_AGREED",
    ),
    "C": (
        "GL6095694164",
        [
            "Haudu, naanu Venkatesh Shetty.",
            "Aapke branch staff ne mujhe 1,800 rupaye ki galat bounce penalty lagayi hai, aur abhi mera "
            "achanak hospital ka kharcha hua hai. Mujhe branch manager se milna hai.",
            "Haan, please branch ko bata dijiye. Dhanyavaad.",
        ],
        "ESCALATED_BRANCH_HARDSHIP",
    ),
    # The borrower offers more than the 2-month minimum (₹3,598) but less than the due (₹10,794).
    "above_minimum": (
        "GL7471867597",
        [
            "Yes, this is Sunil Pandey.",
            "I cannot pay the full amount right now.",
            "I can pay 5,000 rupees today.",
            "Yes, send me the link for 5,000. Call me next week about the rest.",
        ],
        "PARTIAL_SETTLEMENT_AGREED",
    ),
    # Final call: the borrower asks for less. The bot says the floor (₹3,598) one time and does not push.
    "final_less": (
        "GL7471867597",
        [
            "Haan, main Sunil Pandey bol raha hoon.",
            "Itna paisa abhi nahi hai. Aap kuch kam kar sakte ho?",
            "Theek hai, 3,598 aaj de dunga. Link bhej do.",
            "Haan, baaki ke liye agle hafte call kar lena.",
        ],
        "PARTIAL_SETTLEMENT_AGREED",
    ),
    # Monthly interest, segment A: pays at once. The bot adds the eNACH sentence after the link.
    "D": (
        "GL9147805557",
        [
            "Yes, Arun Subramanian here.",
            "Oh, I forgot about it. I will pay now.",
            "Yes, please send the link.",
            "Okay, thank you.",
        ],
        "PAYMENT_LINK_SENT_TODAY",
    ),
    # Monthly interest, segment D: no part payment. The bot tries once for an earlier date.
    "E": (
        "GL3649373616",
        [
            "Aamaa, naan Anitha Ravi.",
            "It is month end, sir. My salary comes in 2 days. Then I will pay all of it.",
            "No, before that it is not possible. In 2 days I will pay the full 13,222.",
            "Yes, okay. Thank you.",
        ],
        "CALLBACK_SCHEDULED",
    ),
    # Segment C, 6 months: refuses every rung. The bot stops after two refusals at the floor.
    "F": (
        "GL0780583622",
        [
            "Haan, main Ajay Agarwal bol raha hoon.",
            "Dekhiye, abhi mere paas paise nahi hain. Main abhi kuch nahi de sakta.",
            "Nahi ji, itna nahi ho payega.",
            "Nahi, yeh bhi nahi ho payega. Kaam bilkul band hai.",
            "Nahi, sach mein nahi hai. Itna bhi nahi.",
            "Nahi ji, abhi kuch bhi nahi de sakta.",
            "Nahi.",
            "Theek hai.",
        ],
        "CALLBACK_SCHEDULED",
    ),
    # Segment C: the borrower offers an amount between the floor (₹9,884) and the middle rung.
    "G": (
        "GL6247031377",
        [
            "Haan ji, Amit Kumar bol raha hoon.",
            "Itna sab ek saath nahi de sakta abhi.",
            "Main aaj 15,000 de sakta hoon.",
            "Haan, 15,000 ka link bhej dijiye. Baaki ke liye baad mein call kar lena.",
            "Theek hai, dhanyavaad.",
        ],
        "PARTIAL_SETTLEMENT_AGREED",
    ),
    # A payment that is not credited: the bot stops the ask and escalates as a dispute.
    "H": (
        "GL8443549101",
        [
            "Haan, Naveen Jain bol raha hoon.",
            "Maine pichhle hafte branch mein jaake payment kar diya tha. Abhi tak dikh nahi raha.",
            "Haan, branch se check karwa dijiye.",
            "Theek hai, dhanyavaad.",
        ],
        "DISPUTE_LOGGED",
    ),
    # Segment B: a busy borrower. The promise gets a date and an amount.
    "I": (
        "GL4554483990",
        [
            "Haan, Pankaj Agarwal.",
            "Main abhi dukaan mein hoon, busy hoon. Aap log kitni baar call karoge?",
            "Kal kar dunga.",
            "Nahi, aaj kuch nahi. Kal poora 1,660 de dunga.",
            "Haan theek hai, dhanyavaad.",
        ],
        "CALLBACK_SCHEDULED",
    ),
    # The husband answers. He gets no loan detail, and no tool runs.
    "wrong_person": (
        "GL4299194921",
        [
            "Hello? Main Seema ji ka pati bol raha hoon. Woh abhi ghar pe nahi hain.",
            "Kya kaam hai? Mujhe bata dijiye, main unko bata dunga.",
            "Theek hai, shaam ko 6 baje call kar lijiye.",
        ],
        "NO_OUTCOME",
    ),
    # The borrower picks up and says nothing: 20 s of quiet mic audio, then "..." twice. No tool may succeed.
    "silent": ("GL0246801055", [SILENCE, "...", "..."], "NO_OUTCOME"),
}


class TextPhone:
    """The phone side with no microphone. The test sends the borrower turns as text."""

    def __init__(self) -> None:
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.cards: list[dict] = []
        self.audio_bytes = 0

    async def receive(self) -> dict:
        return await self.inbox.get()

    async def send_json(self, message: dict) -> None:
        self.cards.append(message)

    async def send_bytes(self, data: bytes) -> None:
        self.audio_bytes += len(data)

    async def close(self, code: int = 1000) -> None:
        pass

    def hang_up(self) -> None:
        self.inbox.put_nowait({"type": "websocket.receive", "text": '{"type": "hangup"}'})


async def bot_finished(q: asyncio.Queue, required: bool = True) -> None:
    """Wait for a bot turn, then for a quiet gap, so a tool call and its follow-up turn can finish.
    If the bot turn is not required (the borrower is silent), a timeout is not an error."""
    try:
        async with asyncio.timeout(TURN_TIMEOUT_S if required else QUIET_S * 3):
            while (await q.get()).get("role") != "bot":
                pass
    except TimeoutError:
        if required:
            raise
        return
    while True:
        try:
            async with asyncio.timeout(QUIET_S):
                await q.get()
        except TimeoutError:
            return


async def quiet_mic(live) -> None:
    """Stream low mic noise in real time, as a phone does when the borrower says nothing."""
    rng = random.Random(7)
    for _ in range(SILENCE_S * 10):
        frame = array.array("h", (rng.randint(-60, 60) for _ in range(1600))).tobytes()
        await live.send_realtime_input(audio=types.Blob(data=frame, mime_type="audio/pcm;rate=16000"))
        await asyncio.sleep(0.1)


async def run_scenario(loan_id: str, lines: list[str]) -> tuple[Store, list[dict]]:
    os.environ.setdefault("GENAI_LOCATION_LIVE", "us-central1")
    project = subprocess.run(
        ["gcloud", "config", "get-value", "project"], capture_output=True, text=True, check=True
    ).stdout.strip()
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project)

    # Each test has its own event loop. The cached async client belongs to the earlier loop.
    vertex.reset()
    store = Store(call_window_bypass=True)
    monitor = Monitor(store, DEFAULT_LIVE_MODEL)
    events = monitor.subscribe()
    seen: list[dict] = []
    real = vertex_connect(DEFAULT_LIVE_MODEL)
    live_ready: asyncio.Queue = asyncio.Queue()

    @asynccontextmanager
    async def connect(config):
        async with real(config) as session:
            live_ready.put_nowait(session)
            yield session

    call_id = store.start_call(loan_id)["call_id"]
    phone = TextPhone()
    call = LiveCall(
        store=store,
        monitor=monitor,
        session=Session(call_id=call_id, loan_id=loan_id),
        phone=phone,
        connect=connect,
        live_model=DEFAULT_LIVE_MODEL,
        summarize=post_call.summarize,
        notify_control=lambda message: asyncio.sleep(0),
    )
    task = asyncio.create_task(call.run())
    tap: asyncio.Queue = asyncio.Queue()

    async def record() -> None:
        while True:
            event = await events.get()
            seen.append(event)
            tap.put_nowait(event)

    recorder = asyncio.create_task(record())
    live = await asyncio.wait_for(live_ready.get(), 30)
    await bot_finished(tap)  # the greeting
    for line in lines:
        seen.append({"type": "transcript", "role": "customer (text)", "text": line})
        if line == SILENCE:
            await quiet_mic(live)
            continue
        call.hear_text(line)
        await live.send_realtime_input(text=line)
        await bot_finished(tap, required=line.strip(".") != "")
    phone.hang_up()
    await asyncio.wait_for(task, 60)
    recorder.cancel()
    return store, seen


@pytest.mark.parametrize("run", RUNS)
@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenario_with_real_live_model(name, run):
    loan_id, lines, expected = SCENARIOS[name]
    store, seen = asyncio.run(run_scenario(loan_id, lines))
    st = store.state[loan_id]
    for e in seen:
        if e["type"] == "transcript":
            print(f"{e['role']}: {e['text']}")
        elif e["type"] == "tool":
            print(f"TOOL {e['name']} {e['args']} ok={e['ok']} {e['error'] or ''}")
        elif e["type"] in ("decision", "consent", "gate"):
            keys = (
                "turn_id",
                "status",
                "act",
                "decision",
                "amount_inr",
                "allowed",
                "code",
                "wait_ms",
                "latency_ms",
            )
            print(e["type"].upper(), {k: e[k] for k in keys if e.get(k) is not None})
    print("summary:", store.calls[st.last_call_id].summary)

    assert st.status.value == expected
    assert st.active_call_id is None
    links = [(lk.amount_inr, lk.settlement_type) for lk in store.links if lk.loan_id == loan_id]
    callbacks = [
        e["args"] for e in seen if e["type"] == "tool" and e["ok"] and "days_from_today" in e["args"]
    ]
    if name == "B":
        # The script says yes to the callback date only in its last line. The bot books after that yes.
        last_line = max(i for i, e in enumerate(seen) if e.get("role") == "customer (text)")
        assert all(
            i > last_line
            for i, e in enumerate(seen)
            if e["type"] == "tool" and e["ok"] and "days_from_today" in e["args"]
        )
    if name == "B":
        # Best effort, so a print and not an assert: the transcription can write ₹4,911 in words.
        first_link = next(i for i, e in enumerate(seen) if e["type"] == "tool" and "amount_inr" in e["args"])
        said = "".join(e["text"] for e in seen[:first_link] if e.get("role") == "bot")
        print("MIDDLE_RUNG_ASKED:", "4911" in said.replace(",", "").replace(" ", ""))
        assert st.next_callback_date == store.today() + timedelta(days=3)
        assert [lk.amount_inr for lk in store.links if lk.loan_id == loan_id] == [3274]
    tools = [i for i, e in enumerate(seen) if e["type"] == "tool" and e["ok"]]
    customer = [i for i, e in enumerate(seen) if e.get("role") == "customer (text)"]
    if name == "B_unclear":
        assert links == [(3274, "PARTIAL_TWO_MONTH")]
        # The reply to the unclear line is a question, with no tool.
        unclear = customer[3]
        assert not [i for i in tools if unclear < i < customer[4]]
    if name == "C":
        assert [b.disputed_fee_inr for b in store.briefs] == [1800]
    if name == "above_minimum":
        assert [(lk.amount_inr, lk.settlement_type) for lk in store.links if lk.loan_id == loan_id] == [
            (5000, "PARTIAL_TWO_MONTH")
        ]
    if name == "final_less":
        assert links == [(3598, "PARTIAL_TWO_MONTH")]
    if name == "D":
        assert links == [(6066, "FULL_DUE")]
    if name == "E":
        assert st.next_callback_date == store.today() + timedelta(days=2)
        assert st.promised_amount_inr == 13222
    if name == "F":
        assert not links and st.promised_amount_inr is None
        assert callbacks[-1].get("customer_intent") == "CANNOT_PAY_FULL"
    if name == "G":
        assert links == [(15000, "PARTIAL_TWO_MONTH")]
        # The callback for the remaining amount carries no promised amount.
        assert any(not cb.get("promised_amount_inr") for cb in callbacks)
    if name == "H":
        assert [b.reason_category for b in store.briefs] == ["PAYMENT_OR_AMOUNT_DISPUTE"]
    if name == "I":
        assert st.next_callback_date == store.today() + timedelta(days=1)
        assert st.promised_amount_inr == 1660
    if name in ("silent", "wrong_person"):
        assert not [e for e in seen if e["type"] == "tool" and e["ok"]]
    if name == "wrong_person":
        # Best effort: the transcription writes numbers as digits or as words, and this finds digits only.
        said = "".join(e["text"] for e in seen if e["type"] == "transcript" and e["role"] == "bot")
        said = said.replace(",", "").replace(" ", "")
        row = store.rows[loan_id]
        assert str(row["due_amount_inr"]) not in said and loan_id[-4:] not in said

    if os.environ.get("TYPESAFE_API_KEY"):
        # A REVIEW verdict has low confidence or no answer. It prints and does not fail.
        verdicts = asyncio.run(judge(name, seen))
        for v in verdicts:
            print(f"JUDGE {v['verdict']} ({v['confidence']}): {v['behavior']}")
        assert not [v for v in verdicts if v["verdict"] == "FAIL" and v["behavior"] not in ADVISORY]


# Code-mixed replies to the floor Ask. A yes sends a link, so an okay word with a delay must not be a yes.
CONSENT_BOT = (
    "Sajeev sir, if you pay 2 months' interest, ₹3,274, today, the interest stops growing. "
    "Shall I send the link?"
)
CONSENT_CASES = [
    *[(True, t) for t in ("Seri", "Seri, link ayakku", "Shari, innu adakkam", "Haudu, link kalisi")],
    *[(True, t) for t in ("Aamaa, link anuppunga", "Avunu, link pampandi", "Theek hai, aaj kar dunga")],
    (True, "Haan, 3,274 ka link bhejo, baaki ke liye baad mein call karna"),
    *[(False, t) for t in ("Seri, nokkam", "Seri, pinne vilikku", "Sari, paakkalaam", "Haan, dekhta hoon")],
    *[(False, t) for t in ("Theek hai, baad mein call karo", "Illa, innu pattilla")],
]


def test_consent_check_on_code_mixed_replies():
    async def classify() -> list:
        return await asyncio.gather(
            *[
                decisions.real_consent({"ask_inr": 3274, "bot_last": CONSENT_BOT, "borrower": text})
                for _, text in CONSENT_CASES
            ]
        )

    results = asyncio.run(classify())
    wrong = [
        (text, r and r["decision"])
        for (agree, text), r in zip(CONSENT_CASES, results, strict=True)
        if r is None or (r["decision"] == "AGREE_TODAY") != agree
    ]
    assert not wrong
