"""GE agent for the Cymbal Bank collections team (spec §7.3 A, §7.6)."""

from google.adk.agents import Agent
from google.adk.models import Gemini
from google.genai import types

from .backend_client import request

INSTRUCTION = """\
1. You help the Cymbal Bank collections team.
2. For "Who are the calls to be done today?", call `get_calls_due_today`. Give the queue totals \
and splits in a few short lines. Then give one table of the top rows with these columns: name, \
loan ID, branch, language, due, 2-month minimum, risk, call attempt, status, callable.
3. For any other question about accounts, branches, languages, segments, risk or amounts, call \
`query_accounts`. Report only what it returns.
4. Trigger a call only when the user names a customer or loan ID, and only if the account is \
callable. Resolve a name with one `query_accounts` call: filter `customer_name` on the exact name, \
and filter `branch` with a "contains" match on the place (branch names look like \
"Hubballi - Civil Lines"). If more than one account matches, ask which one. Show the branch and \
loan ID.
5. For "Analyze the calls", call `analyze_collection_calls`. Present only the numbers that it \
returns. Give the KPIs as a short list, then one line for each call.
6. For "Show me the transcript" or "What was said on the call with X", resolve the name as in \
rule 4, with one `query_accounts` call. Then call `get_call_transcript`. Show each line as \
"Customer:" or "AI agent:" and the text. Keep the original language. Translate only when the user \
asks. Do not invent lines. If the transcript is empty, say that the call has no transcript.
7. Never invent amounts, statuses, or outcomes.
8. Write rupee amounts with Indian digit grouping, for example ₹74,35,486.
9. Keep each answer short. Do not repeat a value in two places. Do not add headings or a summary.
"""


def get_calls_due_today(limit: int = 10) -> dict:
    """Get today's call queue.

    Use this tool for "Who are the calls to be done today?".

    Args:
        limit: The number of top rows to return. The maximum is 50.

    Returns:
        total_accounts, total_due_inr, counts by language, treatment_segment, risk_tier and
        call_attempt, and the top rows by risk_score, then due_amount_inr. Each row has
        identity, branch, language, scheme, months due, due_amount_inr, bot_ask_amount_inr,
        risk_tier, status, call_attempt, language_override and callable. handled_today lists
        each account with a call record today, with its status and outcome.
    """
    return request("GET", "/api/agent/calls-due", params={"limit": limit})


def query_accounts(
    filters: dict | None = None,
    group_by: str | None = None,
    sort_by: str | None = None,
    descending: bool = True,
    limit: int = 10,
) -> dict:
    """Answer any question about the account pool: count, filter, group, sort and sum.

    Use this tool to find an account by customer name or loan ID before you trigger a call.

    Args:
        filters: Maps a column name to a condition. All conditions must match. The forms are:
            a single value for equality, for example {"language": "Hindi"};
            a list for "any of", for example {"risk_tier": ["High", "Medium"]};
            {"min": n, "max": n} for a number or ISO date range, either bound optional,
            for example {"months_due": {"min": 2}} or {"maturity_date": {"max": "2026-12-31"}};
            {"contains": "text"} for a case-insensitive text match,
            for example {"customer_name": {"contains": "saxena"}}.
            The main columns are: customer_id, customer_name, loan_id, branch, city, state,
            language, payment_scheme, months_due, due_amount_inr, bot_ask_amount_inr, pos_inr,
            risk_tier (Low, Medium or High), risk_score, treatment_segment, suggested_channel,
            suggested_bot_play, maturity_date, days_past_due, ltv_pct, broken_promises_3m,
            last_contact_outcome, status, call_attempt, callable, in_queue.
        group_by: A column name. Returns the count and sums for each value of this column.
        sort_by: A column name to sort the rows by.
        descending: Sort the rows from high to low when true.
        limit: The number of rows to return. The maximum is 50.

    Returns:
        matched (the count), totals (sums of due_amount_inr, bot_ask_amount_inr and pos_inr),
        groups (for group_by: count and the same sums, by count high to low) and rows.
        An unknown column or a bad filter returns {"ok": false, "error": ...}.
    """
    body = {
        "filters": filters,
        "group_by": group_by,
        "sort_by": sort_by,
        "descending": descending,
        "limit": limit,
    }
    return request("POST", "/api/agent/accounts/query", json=body)


def trigger_outbound_collection_call(loan_id: str) -> dict:
    """Start a live outbound collection call to the borrower of one loan.

    Call this tool only when the user names the customer or loan ID, and only when the
    account is callable. Do not call it for a list of accounts.

    Args:
        loan_id: The loan ID, for example "GL3506468235".

    Returns:
        {"ok": true, "call_id": ..., "mode": ...} when the call starts. Otherwise
        {"ok": false, "error": ...}: not callable, another call is active, outside the call
        window, no paired phone, or the number is not allow-listed. Tell the user the error.
    """
    return request("POST", "/api/agent/calls", json={"loan_id": loan_id})


def get_call_status(loan_id: str) -> dict:
    """Get the current status and the last call result for one loan.

    Args:
        loan_id: The loan ID, for example "GL3506468235".

    Returns:
        status, call_attempt, the last call outcome and its source (live or seeded), amounts,
        promised_by, next_callback_date, the post-call summary and a transcript excerpt.
    """
    return request("GET", f"/api/agent/accounts/{loan_id}")


def get_handoff_briefs() -> dict:
    """Get all human handoff briefs (escalations).

    Use this tool for "Show me the escalations".

    Returns:
        For each handoff: the borrower profile, customer intent, reason, recommended action,
        and the full transcript.
    """
    return request("GET", "/api/agent/handoffs")


def analyze_collection_calls() -> dict:
    """Get the collection KPIs and per-call summaries.

    Use this tool for "Analyze the calls". Present only the numbers that it returns.

    Returns:
        Deterministic KPI values and a summary for each call.
    """
    return request("GET", "/api/agent/analysis")


def get_call_transcript(loan_id: str, call_id: str | None = None) -> dict:
    """Get the full transcript of one call for one loan.

    Use this tool for "Show me the transcript" or "What was said on the call with X".

    Args:
        loan_id: The loan ID, for example "GL3506468235".
        call_id: The call ID. Omit it to get the newest call for the loan.

    Returns:
        customer_name, source (live or seeded), call_attempt, started_at, ended_at, end_reason,
        outcome, summary (with sentiment) or null, transcript (a list of lines, each with role
        "customer" or "bot" and text) and tool_calls (name, args, ok, error, at).
        A seeded call has an empty transcript. An account with no calls returns
        {"ok": false, "error": ...}.
    """
    if call_id is None:
        listing = request("GET", f"/api/agent/accounts/{loan_id}/calls")
        if not listing.get("ok"):
            return listing
        if not listing["calls"]:
            return {"ok": False, "error": "No calls for this account yet."}
        call_id = listing["calls"][0]["call_id"]
    call = request("GET", f"/api/agent/calls/{call_id}")
    # A call ID from an earlier turn can belong to another borrower. Show only this loan's call.
    if call.get("ok") and call.get("loan_id") != loan_id:
        return {"ok": False, "error": f"Call {call_id} is not a call on loan {loan_id}."}
    return call


root_agent = Agent(
    name="cymbal_collections_agent",
    # R2: Agent Runtime reserves GOOGLE_CLOUD_LOCATION, so pin the model location in code.
    model=Gemini(
        model="gemini-3.8-flash", client_kwargs={"enterprise": True, "location": "global"}
    ),
    instruction=INSTRUCTION,
    # Each turn needs a tool choice, not deep reasoning. Low thinking cuts the GE response time.
    generate_content_config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_level="LOW")
    ),
    tools=[
        get_calls_due_today,
        query_accounts,
        trigger_outbound_collection_call,
        get_call_status,
        get_handoff_briefs,
        analyze_collection_calls,
        get_call_transcript,
    ],
)
