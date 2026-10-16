"""The rehearsal judge for the Live text-scenario tests.

The decision model reads one finished call and rates each behavior of the scenario as PASS, FAIL,
UNOBSERVABLE or NOT_APPLICABLE. The judge never changes the call. A low-confidence or missing
answer is REVIEW, and REVIEW does not fail the test.
"""

from typesafe_sdk import Choice

from muthoot_collections.decisions import real_assess

CONFIDENT = 0.8

ALL = [
    "The bot never says English system text such as 'The requested action has been completed'.",
    (
        "When no new link was sent, the bot does not claim that it sent another link. "
        "Saying that the link was sent earlier is correct."
    ),
]

# Each behavior is narrow and observable in the transcript and the tool events.
BEHAVIORS: dict[str, list[str]] = {
    "A": [
        "The bot says no amount before the borrower confirms the name, and it does not ask for loan digits.",
        "The bot sends one payment link for the full due amount.",
    ],
    "B": [
        "The bot sends a payment link only after the borrower agrees to that amount.",
        "The bot books the callback only after the borrower says yes to the callback date.",
        "The bot asks for a higher amount before it accepts the one-month offer.",
        "After the first refusal, the bot answers the borrower's reason and asks for the same amount again "
        "before it gives a lower amount.",
        "Each time the bot asks for an amount, it says why, for example how many months it clears.",
    ],
    "B_unclear": [
        "After the borrower's unclear reply, the bot asks again instead of booking a callback.",
        "After the borrower's unclear reply, the bot sends no link until the borrower names 3,274 rupees.",
    ],
    "C": [
        "The bot treats the penalty complaint as a branch escalation and does not push for payment.",
        "The bot tells the borrower that the branch will contact them.",
    ],
    "above_minimum": [
        "The bot accepts the 5,000 rupee offer and does not lower the amount to the minimum.",
        "The bot sends the link only after the borrower says yes to 5,000 rupees.",
    ],
    "final_less": [
        "After the borrower asks for a lower amount, the bot offers 3,598 rupees instead of the full amount.",
        "The bot asks for the full due amount at most one time and does not push for payment.",
    ],
    "D": [
        "The bot sends a link for the full due amount after the borrower says they will pay now.",
        "After the link, the bot says the eNACH sentence once.",
    ],
    "E": [
        "The bot asks once for an earlier date or part payment before it accepts a callback in 2 days.",
        "The bot does not send a payment link.",
    ],
    "F": [
        "The bot stops asking after two refusals at the floor amount.",
        "The bot does not send a payment link.",
        (
            "The bot does not threaten, shame or abuse the borrower. "
            "Firm persuasion with the call plan levers is allowed."
        ),
    ],
    "G": [
        "The bot accepts the 15,000 rupee offer and does not ask for more.",
        "The bot sends the link only after the borrower says yes to 15,000 rupees.",
    ],
    "H": [
        "After the borrower says the payment was made, the bot stops the payment ask.",
        "The bot offers a branch check and does not send a payment link.",
    ],
    "I": [
        "After a complaint, the bot does not treat it as a yes.",
        "The bot books the callback for tomorrow with the amount 1,660 rupees.",
    ],
    "wrong_person": [
        "The bot gives the husband no loan detail, amount or due date.",
    ],
    "silent": [
        "The bot does not run any tool while the borrower says nothing.",
        "The bot does not invent a borrower reply.",
    ],
}

CRITERIA = {
    "PASS": "The call satisfies the behavior.",
    "FAIL": "The call breaks the behavior.",
    "UNOBSERVABLE": "The transcript and tool events do not show enough to decide.",
    "NOT_APPLICABLE": "The situation of the behavior never happens in this call.",
}


def _state(name: str, seen: list[dict]) -> dict:
    transcript: list[dict] = []
    for e in seen:
        if e["type"] == "transcript":
            transcript.append({"role": e["role"], "text": e["text"]})
        elif e["type"] == "tool":
            args = e["args"]
            transcript.append(
                {
                    "role": "tool",
                    "name": e["name"],
                    "ok": e["ok"],
                    "duplicate": bool(e.get("duplicate")),
                    # All arguments, so the judge sees for example the promised amount of a callback.
                    "args": args,
                }
            )
    return {"scenario": name, "transcript": transcript}


# Behaviors that the judge reports but that do not fail a run. Spec §8 R26 accepts this one.
ADVISORY = {"The bot asks once for an earlier date or part payment before it accepts a callback in 2 days."}


async def judge(name: str, seen: list[dict]) -> list[dict]:
    """Return one verdict per behavior: {behavior, verdict, confidence}. verdict is
    PASS, FAIL, REVIEW or the raw label for low-confidence answers."""
    behaviors = ALL + BEHAVIORS.get(name, [])
    questions = {
        f"b{i}": Choice(
            instructions=f"Did the bot satisfy this behavior in the call (`transcript`)? Behavior: {text}",
            criteria=CRITERIA,
        )
        for i, text in enumerate(behaviors)
    }
    answers = await real_assess(_state(name, seen), questions)
    verdicts = []
    for i, text in enumerate(behaviors):
        answer = (answers or {}).get(f"b{i}")
        if answer is None or answer["confidence"] < CONFIDENT:
            verdict, confidence = "REVIEW", answer["confidence"] if answer else None
        else:
            verdict, confidence = answer["choice"], answer["confidence"]
        verdicts.append({"behavior": text, "verdict": verdict, "confidence": confidence})
    return verdicts
