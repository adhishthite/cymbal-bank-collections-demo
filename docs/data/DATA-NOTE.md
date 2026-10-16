# Data Note: Vernacular Collections Sample Pool

**Document**: `docs/data/DATA-NOTE.md`  
**Referenced Dataset**: `docs/data/Vernacular_Collections_Bot_Sample_Data_vF.xlsx` (757 KB)  
**Date Added**: October 3, 2026  
**Context**: Cymbal Bank CXO Workshop (Demo 5: Vernacular Voice Bot for Portfolio Collections & Recovery Management)

---

## 1. Provenance & Origin

* **Sender**: Pankhuri Gupta (`Pankhuri_Gupta@mckinsey.com`, McKinsey & Company, Gurgaon).
* **Delivery**: We received the file through the email thread *Google - Cymbal Bank workshop prep* on Saturday, October 3, 2026 at 21:39 IST.
* **Purpose**: McKinsey made this official sample data specifically for the **Interest Collection Voice Bot** (Demo 5). The data shows collections segmentation, channel allocation, and vernacular bot negotiation strategies.

---

## 2. Data Integrity & PII Safety Verification

> [!IMPORTANT]
> **100% Synthetic Data**: This dataset is completely artificial. McKinsey made it for demonstration models. It does **not** contain proprietary or real customer data from Cymbal Bank Core Banking Systems (CBS).

The `Assumptions` sheet confirms these key facts:
* **Fictitious Names & Identifiers**: Customer names and account IDs (`MFLC1000001`...) are synthetic.
* **Random 10-Digit Mobile Numbers**: The mobile numbers are random.
* **Safety Mandate**: The dataset explicitly includes this caution:
  > *"Mobile numbers / names: Randomly generated and fictitious. Random 10-digit numbers could belong to real people: replace with test numbers before any live voice-bot or WhatsApp trial."*

### Telephony Guardrail
* **Never dial numbers from this file on a real telephone carrier.**
* In tests and demonstrations, outbound calls MUST route through the `/mobile` browser Web Audio runner. As an alternative, they MUST use allow-listed presenter mobile numbers from the `demo-phone-map` secret.

---

## 3. Dataset Structure & Schema Breakdown

The workbook contains 4 sheets: `Customers`, `Summary`, `Assumptions` and `Data dictionary`. This note describes the first two. The `Assumptions` sheet gives the rules that §4 uses.

### A. `Customers` (2,000 Rows × 45 Columns)
This sheet contains 2,000 customer accounts with 45 fields:
* **Core Loan Info**: Customer ID, Name, Mobile, POS (Principal Outstanding, ₹), Loan Type (`Gold loan`), Payment Scheme (`Bullet` vs. `Monthly interest`).
* **Interest Due Tracking**: `Bullet: months of interest due` (1–6 months), `Monthly: months of interest unpaid` (1–2 months), `Due amount (₹)`, `Due since`.
* **Bot Negotiation Columns**:
  * `Bullet: months bot should try to collect` = `MIN(months due, 2)`. This column enforces the **2-month minimum partial payment floor**.
  * `Bot ask amount (₹)` = `ask months × monthly interest`.
  * `Suggested bot play` (e.g. *Negotiate minimum amount due*, *Reminder + payment link*, *Gentle reminder + eNACH nudge*).
* **Demographics & Language**: Preferred language, State, City, Servicing Branch.
* **Credit & Risk Signals**: Risk tier (`High`, `Medium`, `Low`), Composite Risk Score (0–100), LTV %, Bureau score, Broken promises-to-pay, Missed payments.

### B. Summary Breakdown
* **Payment Schemes**:
  * **Monthly interest (EII)**: 1,126 customers (56.3%), Total POS ₹20.57 Cr, Total Due ₹45.6 Lakh. An account becomes NPA after 3 months unpaid.
  * **Bullet repayment**: 874 customers (43.7%), Total POS ₹15.84 Cr, Total Due ₹64.5 Lakh. Interest and principal are due at maturity.
* **Suggested Channels**:
  * `Voice bot`: 772 customers (38.6%)
  * `Bot + call center`: 519 customers (26.0%) — High-risk accounts. The bot calls first, and human agents follow up.
  * `WhatsApp reminder`: 509 customers (25.5%)
  * `Call center`: 200 customers (10.0%)
* **Language Distribution (2,000 accounts)**:
  * **Hindi**: 428 (21.4%)
  * **Malayalam**: 421 (21.1%)
  * **Tamil**: 386 (19.3%)
  * **Telugu**: 346 (17.3%)
  * **Kannada**: 273 (13.7%)
  * **English**: 146 (7.3%)

---

## 4. Alignment with Demo 5 Specifications

This dataset directly confirms the business logic in [`docs/specs/demo5_briefing_spec.md`](../specs/demo5_briefing_spec.md):
1. **2-Month Partial Payment Ask**: The sheet explicitly sets `Max months of interest the bot asks for (minimum amount due) = 2`.
2. **Segment C Bullet Negotiation**: The sheet puts bullet loans with 5–6 months of accrued interest into Segment C, with the play *"Negotiate minimum amount due"*.
3. **Dynamic Tone & Escalation**: The sheet routes high-risk customers to `Bot + call center`. There, the bot makes the first contact and escalates disputes and hardships to branch managers.

---

## 5. How the Build Uses This Data

On 2026-10-03, this section replaced the earlier plan. The build uses all 2,000 accounts. It does not use a curated 5-account set or SQLite. The contract is spec §7.2 (`docs/specs/demo5_briefing_spec.md`). `backend/scripts/convert_workbook.py` writes `backend/src/muthoot_collections/data/customers.jsonl` and masks the phones to `+9110000NNNNN` (not a valid Indian mobile prefix). Spec §4 lists the scenario accounts.
