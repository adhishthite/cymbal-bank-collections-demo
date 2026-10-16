"""A scripted stand-in for the decision model and the consent check.

A rule maps a substring of the borrower text (or of the bot text) to answers. An unknown text gets an
unclear reply and an unclear consent. Set `off` to make both functions return None.
Set `off_assess` for the decision model only.
"""

import asyncio

DEFAULT_CHOICES = {
    "reply_act": "UNCLEAR",
    "offered_amount": "NONE",
    "payment_time": "NO_PAYMENT_OFFER",
    "addresses": "OTHER",
    "hardship": "NONE",
    "dispute": "NONE",
    "language_request": "NONE",
}
DEFAULT_SCORES = {"frustration": 0.5, "willingness": 1.0}


def choice(value: str, confidence: float = 0.9) -> dict:
    return {"choice": value, "confidence": confidence, "probabilities": {value: confidence}}


class FakeDecisions:
    def __init__(self) -> None:
        self.borrower_rules: list[tuple[str, dict]] = []
        self.bot_rules: list[tuple[str, dict]] = []
        self.consent_rules: list[tuple[str, dict | None]] = []
        self.off = False
        self.off_assess = False
        self.delay = 0.0

    def borrower(self, text: str, **answers) -> None:
        """Answers: a str is a choice, a float is a noul or a score value."""
        self.borrower_rules.append((text.lower(), answers))

    def bot(self, text: str, **nouls: float) -> None:
        self.bot_rules.append((text.lower(), nouls))

    def agrees(self, text: str, amount_inr: int | None = None) -> None:
        self.consent_rules.append((text.lower(), {"decision": "AGREE_TODAY", "amount_inr": amount_inr}))

    def silent(self, text: str) -> None:
        """The consent check returns None for this text, as after a timeout."""
        self.consent_rules.append((text.lower(), None))

    async def assess(self, state: dict, questions: dict) -> dict | None:
        await asyncio.sleep(self.delay)
        if self.off or self.off_assess:
            return None
        if "borrower" in state:
            text, rules = state["borrower"].lower(), self.borrower_rules
        else:
            text, rules = state["bot"].lower(), self.bot_rules
        given = next((a for t, a in rules if t in text), {})
        out: dict = {}
        for qid, q in questions.items():
            value = given.get(qid)
            if q.type == "choice":
                out[qid] = choice(value or DEFAULT_CHOICES[qid])
            elif q.type == "score":
                out[qid] = {"score": value if value is not None else DEFAULT_SCORES[qid], "confidence": 0.9}
            else:
                out[qid] = {"p": value if value is not None else 0.01}
        return out

    async def consent(self, state: dict) -> dict | None:
        await asyncio.sleep(self.delay)
        if self.off:
            return None
        text = state["borrower"].lower()
        for t, result in self.consent_rules:
            if t in text:
                return result
        return {"decision": "UNCLEAR", "amount_inr": None}
