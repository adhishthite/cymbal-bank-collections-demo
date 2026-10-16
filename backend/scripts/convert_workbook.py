# /// script
# requires-python = ">=3.13"
# dependencies = ["openpyxl==3.1.5"]
# ///
"""Convert the McKinsey sample workbook into the JSONL the backend loads.

Run from the repo root: uv run backend/scripts/convert_workbook.py

openpyxl lives only in this script's inline metadata, so the backend never
parses Excel at runtime. Output is deterministic: same workbook, same bytes.
"""

import json
from datetime import datetime
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "docs/data/Vernacular_Collections_Bot_Sample_Data_vF.xlsx"
OUT = ROOT / "backend/src/muthoot_collections/data/customers.jsonl"

# Workbook header -> JSONL key. Every one of the 45 columns is kept.
COLUMNS = {
    "Customer ID": "customer_id",
    "Customer name": "customer_name",
    "Mobile no.": "phone",
    "POS (₹)": "pos_inr",
    "Loan type": "loan_type",
    "Payment scheme": "payment_scheme",
    "Bullet: months of interest due": "bullet_months_due",
    "Bullet: months bot should try to collect": "bullet_months_to_collect",
    "Monthly: months of interest unpaid": "monthly_months_unpaid",
    "Monthly: months to NPA": "monthly_months_to_npa",
    "Due amount (₹)": "due_amount_inr",
    "Due since": "due_since",
    "Risk tier": "risk_tier",
    "Suggested channel": "suggested_channel",
    "Channel rationale": "channel_rationale",
    "Fallback channel": "fallback_channel",
    "Treatment segment": "treatment_segment",
    "Suggested bot play": "suggested_bot_play",
    "Preferred language": "language",
    "WhatsApp opt-in (Y/N)": "whatsapp_opt_in",
    "Preferred call window": "preferred_call_window",
    "State": "state",
    "City": "city",
    "Branch": "branch",
    "Loan account no.": "loan_id",
    "Loan start date": "loan_start_date",
    "Tenure (months)": "tenure_months",
    "Maturity date": "maturity_date",
    "Interest rate (% p.a.)": "interest_rate_pct",
    "Monthly interest (₹)": "monthly_interest_inr",
    "Bot ask amount (₹)": "bot_ask_amount_inr",
    "Days past due": "days_past_due",
    "Last interest payment date": "last_interest_payment_date",
    "Days since last payment": "days_since_last_payment",
    "eNACH registered": "enach_registered",
    "Missed interest payments, last 6M": "missed_payments_6m",
    "Broken promises-to-pay, last 3M": "broken_promises_3m",
    "Bureau score": "bureau_score",
    "Off-us active loans (#)": "off_us_active_loans",
    "Off-us delinquency (Y/N)": "off_us_delinquency",
    "LTV (%)": "ltv_pct",
    "Risk score (0-100)": "risk_score",
    "Last contact date": "last_contact_date",
    "Last contact channel": "last_contact_channel",
    "Last contact outcome": "last_contact_outcome",
}


def clean(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if value == "NA":
        return None
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def main() -> None:
    sheet = openpyxl.load_workbook(SRC, data_only=True, read_only=True)["Customers"]
    rows = sheet.iter_rows(values_only=True)
    header = next(rows)
    if set(header) != set(COLUMNS):
        raise SystemExit(f"Workbook columns changed: {set(header) ^ set(COLUMNS)}")
    keys = [COLUMNS[h] for h in header]

    records = []
    for n, row in enumerate(rows, start=1):
        record = {k: clean(v) for k, v in zip(keys, row, strict=True)}
        # The workbook's numbers are random and may belong to real people.
        # +91 1xxxx is not a valid Indian mobile prefix, so these can never ring.
        record["phone"] = f"+9110000{n:05d}"
        records.append(record)

    if len({r["loan_id"] for r in records}) != len(records):
        raise SystemExit("loan_id is not unique")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(records)} accounts to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
