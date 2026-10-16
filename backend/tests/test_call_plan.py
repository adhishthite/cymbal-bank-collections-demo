from muthoot_collections.live_session import call_plan, system_instruction
from muthoot_collections.store import Store

# Brief acceptance values: the ask ladder from the full due amount down to the floor.
LADDERS = {
    "GL0106397512": [8185, 4911, 3274],  # Sajeev George, segment C
    "GL3506468235": [8612, 6459, 4306],  # Kavita Saxena, segment E
}
# A final call does not push: one Ask, the full due amount. An offer of the floor or more still gets a link.
FINAL = {"GL6095694164": (24950, 9980), "GL7471867597": (10794, 3598)}  # Venkatesh Shetty, Sunil Pandey
MONTHLY = "GL3649373616"  # Anitha Ravi, Monthly interest, segment D


def test_call_plan_acceptance_values(store: Store):
    def plan(loan_id):
        return call_plan(store.rows[loan_id], store.state[loan_id], store.today())

    for loan_id, amounts in LADDERS.items():
        assert [amount for _, amount in plan(loan_id)["rungs"]] == amounts, loan_id

    for loan_id, (due, floor) in FINAL.items():
        assert [a for _, a in plan(loan_id)["rungs"]] == [due] and plan(loan_id)["floor"] == floor
        text = system_instruction(store.rows[loan_id], store.state[loan_id], store.today())
        assert "Ask 2" not in text and f"the floor, ₹{floor:,}" in text, loan_id
        # When the borrower asks for less, the final call says the floor one time, with its reason.
        assert f"can pay ₹{floor:,} today" in text and "it clears 2 of the" in text, loan_id

    # A Monthly interest account has no part payment: one rung, the full due amount.
    assert [amount for _, amount in plan(MONTHLY)["rungs"]] == [store.rows[MONTHLY]["due_amount_inr"]]

    # Kavita has a broken promise count but no earlier call, so the bot must not cite it.
    assert "broken_promise" not in plan("GL3506468235")["levers"]
    assert "broken_promise" in plan("GL0106397512")["levers"]

    # The final call does not push: no hold, and no broken promise lever, even after a promise on a call.
    final = next(
        lid
        for lid, row in store.rows.items()
        if store.state[lid].call_attempt == 3
        and row["treatment_segment"].startswith("C")
        and row["last_contact_outcome"] == "Promised to pay"
        and row["last_contact_channel"] == "Voice bot"
    )
    assert "Hold" not in plan(final)["play"] and "broken_promise" not in plan(final)["levers"]

    # The instruction carries the plan amounts, so the model does not calculate them.
    sajeev = store.rows["GL0106397512"]
    text = system_instruction(sajeev, store.state["GL0106397512"], store.today())
    assert all(a in text for a in ("₹8,185", "₹4,911", "₹3,274"))
    # Each Ask says why: what it clears and what stays unpaid.
    assert "it clears 3 of the 5 months" in text and "Only 2 months stay unpaid" in text
