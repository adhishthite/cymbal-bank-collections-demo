Cymbal Bank CXO Workshop (Oct 7, 2026)

## Demo 5: Vernacular Voice Bot for Portfolio Collections & Recovery Management — Technical & Strategic Briefing

**Document Type**: Engineering Blueprint & Demonstration Runbook

**Target Milestone**: October 7, 2026 Executive CXO Workshop (Kochi, Kerala)

**Primary Stakeholders**: Cymbal Bank Leadership (CBOs, Collections & Audit Leadership), McKinsey & Co. Advisory Team

**Technical Lead & Builder**: Adhish Thite (Senior Solutions Architect – AI, Google Cloud India)

**Workshop Session Leads**: Rohit Verma & Supriya Waghmare (Presenting D0–D5 on-site)

**Target Platform**: Gemini Enterprise (custom ADK agent on Agent Runtime) + Gemini Live API voice bridge (/mobile in Phase 1, Twilio in Phase 2)

---

## 1. Executive Context & Business Rationale

Cymbal Bank is India's premier Non-Banking Financial Company (NBFC). It specializes in gold-backed loans. Approximately **90% of its asset book is in gold loans**.

### The Gold Loan Default Dynamics

**Sample data**: All accounts, names, amounts and rules in this document come from the McKinsey sample pool `docs/data/Vernacular_Collections_Bot_Sample_Data_vF.xlsx` (2,000 synthetic gold-loan customers, as-of date 2026-10-03). See §7.2.

Pledged gold jewelry (22K/24K) fully secures each gold loan. Thus a gold loan does not end as an unsecured write-off. But a gold loan still has a specific credit and operational risk:

- **Interest accrual vs. collateral value**: Cymbal Bank disburses the principal against the gold value at the pledge date, within the applicable RBI LTV limit. Unpaid interest continues to increase, and the LTV moves toward the auction threshold.
- **Two schemes**: A **Bullet** loan pays interest and principal at maturity. Thus interest accrues for months (1–6 in the sample). A **Monthly interest (EII)** loan becomes NPA after 3 months of unpaid interest (1–2 months unpaid in the sample).
- **Accrued is not overdue**: Most Bullet accounts in the queue have not reached maturity (`days_past_due = 0`). Their interest is accrued and unpaid, but it is not contractually late. The bot says "interest due". It never says "overdue" or "default". The bot uses the maturity date as the reason to pay now. This is McKinsey's Segment C play: "Accrued bullet → negotiate minimum amount due".
- **Minimum amount due**: When a borrower cannot pay the full amount, the bot asks for at least 2 months of interest now: `MIN(months due, 2) × monthly interest` (the workbook's `Bot ask amount`).
- **Operational friction**: Demand notices, branch visits and auctions make customers unhappy. They also damage the brand and add administrative cost.

### Purpose of Demo 5

Demo 5 shows Cymbal Bank leadership how **Gemini Enterprise (GE)** and the **Gemini Live API** collect unpaid gold loan interest **before** it becomes delinquency or auction risk:

1. In real GE, the collections persona asks *"Who are the calls to be done today?"*. The agent gives a summary of today's call queue: the 1,291 accounts that McKinsey routes to the voice bot. It lists the top accounts with the call attempt (1st, 2nd, 3rd/final). The persona can ask any question about the 2,000-account pool.
1. The bot calls borrowers live, with two-way voice in Hindi, Malayalam, Tamil, Telugu, Kannada or English. Malayalam and Kannada depend on the Go / No-Go smoke test in §6.
1. The bot negotiates. It does more than send a reminder. It asks for the full amount first. Then it asks for at least 2 months of interest now and holds that floor against lower counter-offers. Then it books a callback for the remaining amount. Backend code enforces the floor.
1. The backend records each call result. The GE agent shows the updated status, the handoff briefs and the *"Analyze the calls"* summary. Backend code computes all numbers.

## 2. Platform Architecture & Live Call Delivery Mechanism

### Gemini Enterprise Is the Operator Surface

- **Real GE, not a look-alike**: As agreed with Supriya Waghmare on October 3, 2026, the full agent journey runs in the real **Gemini Enterprise (GE)** app. A custom ADK agent runs on **Agent Runtime (Agent Engine)**. An admin connects it in the GE console with **Custom agent via Agent Runtime** (Gemini Enterprise > app > Agents > Add agent). See §7.6.
- **Start and close in GE**: The collections persona types *"Who are the calls to be done today?"* to start the demo. The persona types *"Analyze the calls"* to close it. GE also shows the handoff briefs for escalated calls.
- **No fake GE UI**: No custom page copies the GE header or layout. An optional, unbranded `/monitor` page shows only the live transcript.

### Live Calling Mechanism (No Recorded Audio)

- **Mandate**: Rohit Verma and Dipanjan Bhattacharya asked for live calls in the October 1 sync (*"this can't be a recording"*). All demo calls are live two-way voice.
- **Audio pipeline**: Browser AudioWorklet (Phase 1) or Twilio Media Streams (Phase 2) -> backend WebSocket -> Gemini Live API. The input is 16 kHz 16-bit mono PCM. The output is 24 kHz 16-bit mono PCM. The Live API server VAD handles barge-in.
- **Telephony modes** (`TELEPHONY_MODE=mobile|twilio`):
    - **Phase 1, `mobile` (default)**: The `/mobile` web page runs on the presenter's phone (QR code plus pairing code). This is the dev path and the venue fallback.
    - **Phase 2, `twilio` (planned, in scope)**: The backend makes a real outbound PSTN call through Twilio. It calls only allow-listed, verified numbers. See §7.4.
- **State sync**: The backend store is the single source of truth. The GE agent reads and changes state only through its tools (§7.3).

## 3. The 4-Stage Closed-Loop Workflow

```
STAGE 1  Start of day in real Gemini Enterprise (ADK agent on Agent Runtime)
         "Who are the calls to be done today?" -> queue of 1,291 voice-bot accounts, top rows, call attempt 1/2/3
            |
            v
STAGE 2  Live outbound call (Gemini Live API; /mobile in Phase 1, Twilio in Phase 2)
         Tone follows call_attempt: 1 = courteous, 2 = firm, 3 = empathetic and final
            |
            v
STAGE 3  Intent + deterministic negotiation (Live tools, backend invariants)
         INT-01 full link | INT-02 callback <= 7 days | INT-03 ask steps, partial link + callback, or automatic callback
         INT-04 dispute | INT-05 human handoff (brief stored in backend)
            |
            v
STAGE 4  Back in Gemini Enterprise
         Status per account, handoff briefs, "Analyze the calls" (KPIs computed by code, narrated by model)
```
### Stage 1: Start of Day in Gemini Enterprise

The collections persona opens the Demo 5 agent in the real GE app.

- **Start prompt (literal)**: *"Who are the calls to be done today?"*
- **Agent action**: The agent calls `get_calls_due_today` (§7.3). The tool returns the queue totals: accounts, total due, and the split by language, segment, risk tier and call attempt. It also returns the top rows by risk score, and `handled_today` (each account with a call record today). Each row has `status`, `call_attempt`, the minimum amount due, and `callable` (§7.3 A). With the default seeded outcomes, `handled_today` shows MFLC1000619 and MFLC1000564 with their seeded status and `callable = false`. The scenario accounts are not in the top rows (risk ranks 280–1082), so the presenter names them.
- **Columns shown**: Customer ID, loan ID, customer name, branch, language, scheme, months due, due amount, minimum amount due, risk tier, **call attempt (1st / 2nd / 3rd-final)**, status, callable.
- **Any question**: The agent sends follow-up questions to `query_accounts`, for example *"How many high-risk Malayalam accounts in Kochi?"* or *"Total due by segment"*. Backend code computes every number.
- **Call trigger**: The persona names the branch, for example *"Call Kavita Saxena at New Delhi Bus Stand"* or *"Call Sajeev George in Kochi"*. Names repeat in the pool, and all five scenario names repeat. Thus the agent resolves the name with `query_accounts` and prefers accounts in the queue. If more than one account still matches, the agent asks which account to call. Then it calls `trigger_outbound_collection_call` with the `loan_id`. The agent can call any account in the pool, but only one call can be active at a time (§7.3 D).

### Stage 2: Live Outbound Calling and Tone by Call Attempt

Only `call_attempt` sets the tone: **1 = courteous reminder**, **2 = firm (repeat miss)**, **3 = empathetic final call with human handoff if needed**. On each call, the bot first says that it is Cymbal Bank's AI assistant and that the call is recorded. It confirms the borrower's identity before it states any amount (§5).

#### Scenario A: 1st Call, Courteous Reminder (Kavita Saxena, MFLC1001779 / GL3506468235, Hindi, call\_attempt = 1)

- **Account**: The account is Bullet, with 4 months of interest due (₹8,612), at New Delhi - Bus Stand. The risk is low, and there is no prior contact. The bot play is *Reminder + payment link*.
- **Borrower**: *"Namaste, I was traveling out of state for work and missed it. I will pay it today."*
- **Tone**: The bot is courteous and brief.
- **Bot flow**:
    - The bot asks: *"Thank you. Shall I note that you will pay by end of day today?"* The borrower says yes.
    - The bot calls `generate_upi_payment_link(loan_id="GL3506468235", amount_inr=8612, settlement_type="FULL_DUE")`.
    - The backend sets `promised_by` to today 23:59 Asia/Kolkata. It sets the status to `PAYMENT_LINK_SENT_TODAY`. The backend mocks SMS, WhatsApp and UPI (§7.3).

#### Scenario B: 2nd Call, Firm Tone, Negotiation to the 2-Month Minimum (Sajeev George, MFLC1000090 / GL0106397512, Malayalam, call\_attempt = 2)

- **Account**: The account is Bullet, with 5 months of interest due (₹8,185 at ₹1,637 a month), at Kochi - Market Road. The risk is high, with 1 broken promise-to-pay. The last contact was by voice bot ("Promised to pay"). The maturity date is 2026-10-20. The account is in Segment C, and the bot play is *Negotiate minimum amount due*.
- **Tone**: The bot is firm, factual and consultative. It makes no threats.
- **Negotiation (the point of this scenario)**: the call plan (§5) gives the bot three Asks. Ask 1 is the full ₹8,185 (5 months). Ask 2 is ₹4,911 (3 months). Ask 3 is the floor, ₹3,274 (2 months). The bot makes one Ask in each reply.
    - The bot states the history: *"This is our second call. Last time you promised to pay, and it has not come in."* Then it asks for the full ₹8,185 of interest due.
    - **Borrower**: *"Business is tight before Diwali. I will pay all the interest after 5 months."*
    - The bot gives one fact from the call plan, not a threat. For example, interest increases by ₹1,637 each month, or the loan matures on 20 October and then interest and principal are both due.
    - The bot makes Ask 2: *"You don't need to pay the full ₹8,185 today. If you pay 3 months' interest, ₹4,911, today, it brings the dues down. Shall I send the link for ₹4,911?"*
    - **Borrower counter-offers**: *"I can do one month, ₹1,637."* This amount is below the floor, so the bot treats it as a refusal of its last Ask. It does not accept the amount.
    - The bot makes Ask 3 with a new fact: *"I understand. ₹3,274 is the lowest I can take today. Shall I send the link for ₹3,274?"* (If the bot tries a link for ₹1,637, the backend rejects it under D-1. The bot then states the minimum from the error.)
    - The borrower agrees. The bot calls `generate_upi_payment_link(loan_id="GL0106397512", amount_inr=3274, settlement_type="PARTIAL_TWO_MONTH")`.
    - The bot asks if a callback in 3 days for the remaining amount suits the borrower. After the borrower says yes, it calls `schedule_callback_reminder(loan_id="GL0106397512", days_from_today=3, notes="call center follow-up")` with no `promised_amount_inr` and no `customer_intent`. After `ok: true`, the bot says: *"I have sent the link. I will call you again in 3 days about the remaining amount."*
    - The final status is `PARTIAL_SETTLEMENT_AGREED`, and `next_callback_date` is today + 3 days. The call intent is INT-03.
    - If the borrower names an amount from ₹3,274 up to less than ₹8,185, the bot says yes at once and sends the link for that amount. It does not ask for more.
- **Malayalam fallback**: If the Malayalam smoke test in §6 fails, set `language_override = Hindi` on GL0106397512 with `POST /api/admin/language`. Then run Scenario B in Hindi with the same data.

#### Scenario C: 3rd / Final Call, Hardship, Dispute and Language Switch, Human Handoff (Venkatesh Shetty, MFLC1000092 / GL6095694164, Kannada, call\_attempt = 3)

- **Account**: The account is Bullet, with 5 months of interest due (₹24,950), at Hubballi - Civil Lines. The risk is high. The last contact was by call center ("Refused / disputed"). Two accounts in the queue have the name Venkatesh Shetty. Thus the presenter says *"Call Venkatesh Shetty in Hubballi"*.
- **Borrower (starts in Kannada, then switches to Hindi mid-call)**: *"Your branch staff charged me incorrect bounce penalties of ₹1,800, and I just had an unexpected hospital expenditure. I need to meet the branch manager."*
- **Language**: The Live model follows the switch and continues in Hindi. Application code does no language detection (§5).
- **Tone**: The bot is empathetic and calm. It does not argue, and it does not push for payment.
- **Bot flow**:
    - The bot acknowledges the hardship and the dispute.
    - The bot calls `escalate_to_branch_manager(loan_id="GL6095694164", reason_category="HARDSHIP_AND_FEE_DISPUTE", disputed_fee_inr=1800, customer_summary=..., recommended_action=...)`.
    - The backend freezes automated calls on the account, stores the handoff brief, and sets the status to `ESCALATED_BRANCH_HARDSHIP`.
    - The tool result has `branch_callback_date`, which is today + 2 days. The bot says: *"Mr. Shetty, I have noted your dispute about the ₹1,800 charge and your hospital expense. I have passed your case to the Hubballi - Civil Lines branch team with full details. A person from the branch will call you on <branch callback date>."*

#### Further Live Test Scenarios (D to I, and the Wrong-Person Case)

These scenarios are not part of the stage path. `make test-live` drives each one against the real `gemini-3.8-live` with text borrower turns (§7.7). The accounts are ordinary pool rows. They cover the plays and the tool rules that Scenarios A–C do not reach.

| **Scenario** | **Loan ID / Customer** | **Language, Attempt, Segment** | **Borrower** | **End State** |
| --- | --- | --- | --- | --- |
| D | `GL9147805557` Arun Subramanian | English, 1, A (Monthly interest, ₹6,066) | Forgot and pays now. | `PAYMENT_LINK_SENT_TODAY`, `FULL_DUE` link of ₹6,066. The bot adds the eNACH sentence after the link. |
| E | `GL3649373616` Anitha Ravi | Tamil, 2, D (Monthly interest, ₹13,222) | Salary comes in 2 days. Then the borrower pays all. | `CALLBACK_SCHEDULED`, 2 days, promise of ₹13,222. The bot asks one time for an earlier date. |
| F | `GL0780583622` Ajay Agarwal | Hindi, 2, C (6 months, ₹7,926) | Refuses every Ask. | `CALLBACK_SCHEDULED`, no link, no promised amount. The bot books the callback with `CANNOT_PAY_FULL` after two refusals of the floor. |
| G | `GL6247031377` Amit Kumar | Hindi, 2, C (6 months, ₹29,652) | Offers ₹15,000. This is above the floor (₹9,884) and below Ask 2. | `PARTIAL_SETTLEMENT_AGREED`, link of ₹15,000, callback with no promised amount. |
| H | `GL8443549101` Naveen Jain | Hindi, 1, E | Says that a payment is not credited. | `DISPUTE_LOGGED`, `PAYMENT_OR_AMOUNT_DISPUTE`. |
| I | `GL4554483990` Pankaj Agarwal | Hindi, 1, B (₹1,660) | Is busy. Promises the full amount tomorrow. | `CALLBACK_SCHEDULED`, 1 day, promise of ₹1,660. |
| `wrong_person` | `GL4299194921` Seema Yadav | Hindi, 1, E | The husband answers. | `NO_OUTCOME`. The bot shares no loan detail and calls no tool. |

### Stage 3: The 5 Intent Classes


| **Intent ID** | **Intent Name** | **Customer Utterance Pattern** | **Tool Call and Resulting Status (§7.3)** |
| --- | --- | --- | --- |
| INT-01 | `WILL_PAY_NOW` | *"I can pay right away on UPI."* | `generate_upi_payment_link(settlement_type="FULL_DUE") -> PAYMENT_LINK_SENT_TODAY` |
| INT-02 | `NEEDS_MORE_TIME` | *"Please give me 5 days until salary credits."* | `schedule_callback_reminder(days_from_today=1..7) -> CALLBACK_SCHEDULED` |
| INT-03 | `CANNOT_PAY_FULL` | *"I want to pay after 5 months / can't pay all."* | The bot asks the full due amount, then the middle Ask, then the floor (the 2-month minimum). It gives one fact with each Ask. It treats an offer below the floor as a refusal. Then: `generate_upi_payment_link(settlement_type="PARTIAL_TWO_MONTH") -> PARTIAL_SETTLEMENT_AGREED, plus schedule_callback_reminder for the remaining amount`. After two refusals of the last Ask, the bot books a callback in 3 days at once, with `customer_intent="CANNOT_PAY_FULL"` and no promised amount: `-> CALLBACK_SCHEDULED` |
| INT-04 | `DISPUTES_CHARGES` | *"Your branch added wrongful penalty charges."* or *"I paid, but it is not credited."* | `escalate_to_branch_manager(reason_category="PENAL_FEE_DISPUTE" / "PAYMENT_OR_AMOUNT_DISPUTE") -> DISPUTE_LOGGED, calling frozen`. The tool result and the handoff brief carry a branch callback date (today + 2 days). The bot tells the borrower this date. |
| INT-05 | `HUMAN_HANDOFF` | *"I want to speak with the branch manager."* or severe hardship | `escalate_to_branch_manager(reason_category="MEDICAL_HARDSHIP" / "HARDSHIP_AND_FEE_DISPUTE" / "CUSTOMER_REQUESTED_HUMAN") -> ESCALATED_BRANCH_HARDSHIP, calling frozen`. The tool result and the handoff brief carry a branch callback date (today + 2 days). The bot tells the borrower this date. |

Each call record has an `intent`: a list of intent IDs, for example `["INT-03 CANNOT_PAY_FULL"]`. `store.py` sets it from the successful tool calls (§7.3 C). The call record is empty if the call has no outcome.

### Stage 4: Back in Gemini Enterprise

#### 1. Status Update (Read Through the GE Agent)

After each call, the persona asks a question in GE, for example *"What happened on the call with Sajeev George?"* The agent calls `get_call_status`. These are the expected end states after the 3 live calls and the 2 seeded outcomes (§7.2):


| **Loan ID** | **Customer** | **Source** | **Final Status** | **Amount Detail** |
| --- | --- | --- | --- | --- |
| `GL3506468235` | Kavita Saxena | Live (Scenario A) | `PAYMENT_LINK_SENT_TODAY` | ₹8,612 full link, promise-to-pay by end of day |
| `GL0106397512` | Sajeev George | Live (Scenario B) | `PARTIAL_SETTLEMENT_AGREED` | ₹3,274 link today, callback in 3 days |
| `GL1814280043` | Saravanan Raman | Seeded | `CALLBACK_SCHEDULED` | Promise of ₹17,965 in 3 days |
| `GL6095694164` | Venkatesh Shetty | Live (Scenario C) | `ESCALATED_BRANCH_HARDSHIP` | ₹1,800 disputed fee, calling frozen |
| `GL9061256232` | Ravi Varma | Seeded | `PARTIAL_SETTLEMENT_AGREED` | ₹6,868 link today, callback in 3 days |

#### 2. Human Handoff Brief (Shown in GE)

The backend stores the brief when `escalate_to_branch_manager` succeeds. The persona asks *"Show me the escalations"*, and the agent calls `get_handoff_briefs`. Each brief has the four parts from the master doc: **transcript + customer intent + reason for escalation + recommended action**. This is the expected content for GL6095694164:

- **Borrower profile**: Venkatesh Shetty (MFLC1000092 / GL6095694164, Hubballi - Civil Lines, POS ₹4,27,700, LTV 77.9%, high risk, 5 months of interest due, ₹24,950).
- **Customer intent**: `HUMAN_HANDOFF` (INT-05) with `DISPUTES_CHARGES` (INT-04). `store.py` derives the intent from the `reason_category` tool argument (see the map in §7.3 C).
- **Escalation reason**: The borrower has a medical hardship (hospitalization). The borrower also disputes a ₹1,800 penal bounce charge.
- **Branch callback date**: `branch_callback_date`, which is the date of the escalation + 2 days. The bot tells this date to the borrower, and the account status shows it as `next_callback_date`.
- **Recommended action**: Check the ₹1,800 charge against the branch fee record. If the charge is wrong, reverse it. After the hardship review, discuss a payment plan that starts with 2 months' interest (₹9,980), before maturity on 2026-10-28.
- **Transcript**: The full call transcript, from the input and output transcription of the Live session.

#### 3. Closing Command: "Analyze the Calls"

To close the demo, the persona types the literal prompt *"Analyze the calls"* in GE. The agent calls `analyze_collection_calls`. **Backend code computes every number from the store. The model only narrates the returned JSON. It must not add or change numbers.** These are the expected values with the end states above:

- **Today's queue (context)**: 1,291 accounts, ₹74,35,486 due.
- **Accounts called today**: 5, with ₹73,448 due (₹8,612 + ₹8,185 + ₹24,950 + ₹17,965 + ₹13,736).
- **Calls with an outcome**: 5 (3 live, 2 seeded outcomes). **Outcome rate**: 100.0% (5 of 5 call records). The backend computes both values. They are not hard-coded.
- **Payment links sent today**: ₹18,754 (₹8,612 + ₹3,274 + ₹6,868).
- **Promises to pay within 7 days (callbacks with an amount)**: ₹17,965 (GL1814280043, 3 days).
- **Total committed (links today + promises within 7 days)**: ₹36,719 = 50.0% of the due on accounts called today.
- **2-month partial agreements**: 2 accounts (GL0106397512, GL9061256232).
- **Escalations**: 1 (GL6095694164, Hubballi - Civil Lines branch), with ₹1,800 disputed.
- If a live call ends differently, the numbers change with the store. The values above are the acceptance test for the scripted path (§7.7).

## 4. Demonstration Dataset (McKinsey Sample Pool)

*All 2,000 accounts are synthetic (McKinsey sample pool, §7.2). The agent can query or call any account. The converter replaces the phone numbers with invalid test numbers (`+9110000xxxxx`), and the backend never dials them. Real calls go only to allow-listed presenter numbers (§7.4).*

The 5 accounts below carry the scripted demo path. They are ordinary pool rows. The code has no special case for them, except for the seeded outcomes.

| **Customer ID / Loan ID** | **Customer Name** | **Language** | **Branch** | **Scheme** | **Months Due** | **Due** | **2-Month Minimum** | **Risk** | **Call Attempt** | **Demo Scenario** |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MFLC1001779 / GL3506468235 | Kavita Saxena | Hindi | New Delhi - Bus Stand | Bullet | 4 | ₹8,612 | ₹4,306 | Low | 1 | Live Scenario A (INT-01): pays ₹8,612 via UPI link today |
| MFLC1000090 / GL0106397512 | Sajeev George | Malayalam (Hindi fallback) | Kochi - Market Road | Bullet | 5 | ₹8,185 | ₹3,274 | High | 2 | Live Scenario B (INT-03): negotiated to 2-month payment of ₹3,274, callback in 3 days |
| MFLC1000619 / GL1814280043 | Saravanan Raman | Tamil | Tiruchirappalli - Station Road | Bullet | 5 | ₹17,965 | ₹7,186 | High | 2 | Seeded outcome (INT-02 NEEDS\_MORE\_TIME): promises ₹17,965 in 3 days, callback scheduled |
| MFLC1000092 / GL6095694164 | Venkatesh Shetty | Kannada, switches to Hindi | Hubballi - Civil Lines | Bullet | 5 | ₹24,950 | ₹9,980 | High | 3 | Live Scenario C (INT-04 + INT-05): hardship and ₹1,800 fee dispute, human handoff |
| MFLC1000564 / GL9061256232 | Ravi Varma | Telugu | Warangal - Town Hall | Bullet | 4 | ₹13,736 | ₹6,868 | High | 2 | Seeded outcome (INT-03): agrees to 2-month payment of ₹6,868, callback in 3 days |

The Live test scenarios D to I and the wrong-person case use other pool rows. The table in §3 Stage 2 lists them.

## 5. Live Voice Agent System Instructions & Tool Map

This section defines the **Live voice agent** (Stage 2–3). §7.6 defines the GE agent (Stage 1 and 4). The model matrix is in §7.1. **§7.3 is the single source of truth for all tool names and parameters.** This section does not repeat the signatures.

### System Instructions (Injected Server-Side in LiveConnectConfig)

At the start of the session, the backend builds the system instruction from a fixed rule text (`RULES`), the tone for the `call_attempt` (`TONES`), a call plan and the account context. Code builds the plan and the context from the account's row (`call_plan` and `system_instruction` in `live_session.py`).

**Account context**: today, customer name, loan ID, branch, scheme, months of interest due, due amount, monthly interest, `days_past_due` and `call_attempt`. The language comes from the `language_override` if it has a value, and else from the row. Code removed these fields from the context: the 2-month minimum, the remaining amount after the minimum, the maturity date, LTV, risk tier, treatment segment, suggested bot play, broken promises-to-pay, last contact and preferred call window. The model read the 2-month minimum out as "the minimum" and skipped the middle Ask. The call plan now carries the facts that the bot needs from the removed fields.

**Call plan**: code computes every amount, so the model calculates none. The plan has these parts:

1. **Asks**: Ask 1 is the full due amount. A Bullet account with more than 2 months due also has the floor (`bot_ask_amount_inr`, the 2-month minimum) as the last Ask. A middle Ask of `(months due + 2) // 2` months of interest sits between them. The middle Ask exists only if it is more than 2 months and the segment is not B. Example for Sajeev George: ₹8,185, ₹4,911, ₹3,274. A Monthly interest account, or an account with 2 months or less due, has one Ask: the full due amount. The bot negotiates the date only. A final call (`call_attempt` 3) has one Ask, the full due amount, because the bot does not push (see the no-push play). It says the floor only when the borrower asks for less or offers less. It accepts an offer of the floor or more, with a link for that amount.
1. **Offer rule**: when the borrower names an amount, the bot compares it only with the floor. An amount of the floor or more gets an immediate yes and a link for that amount. An amount below the floor is a refusal of the last Ask, and the bot makes the next Ask. Two separate lists give the Asks and the offer rule, because one combined list made Live use the middle Ask as a threshold.
1. **Levers**: a lever is a fact that the bot says in its own words, one in each reply. The plan keeps the levers that apply to the account, in the order of the play:
    1. `interest`: the interest grows by the monthly interest each month. The lever also gives the amount due in 3 more months (due amount + 3 × monthly interest). This lever applies to each account.
    1. `cibil`: unpaid interest *can* affect the CIBIL score, and a payment today protects it. The plan adds that the account is past due only if `days_past_due > 0`. This lever applies to each account.
    1. `calls`: a payment of the full due amount today stops the reminder calls. This lever applies to each account.
    1. `maturity`: the maturity date, and that interest and principal are then both due. It applies only if the maturity date is 60 days or less from today. It says "matured" for a date in the past.
    1. `npa`: one more missed month makes the account non-performing. It applies only to a Monthly interest account with `monthly_months_to_npa = 1`.
    1. `broken_promise`: the borrower promised to pay on the last call, and the payment did not come in. It applies only if `call_attempt` is 2, the last contact outcome is "Promised to pay", and the last contact channel is "Voice bot" or "Call center".
1. **Play by segment** (the first letter of `treatment_segment`), with the lever order:
    1. **A**: ask the full due amount today. If the borrower cannot pay today, ask for a date within 7 days. Be gentle, use one lever only, and do not hold. Levers: `interest`, `calls`, `cibil`.
    1. **B**: ask the full due amount, use one lever only, and keep the call short. Levers: `interest`, `calls`, `cibil`.
    1. **C**: hold each Ask one time with a new lever before the next Ask. Levers: `interest`, `maturity`, `broken_promise`, `calls`, `cibil`.
    1. **D**: ask the full due amount today. If the borrower refuses, ask for the full amount in 2 to 3 days, and then within 7 days. Levers: `interest`, `npa`, `broken_promise`, `calls`, `cibil`.
    1. **E**: ask the full due amount today. If there is only one Ask, ask for a date within 7 days. Hold one time. Levers: `interest`, `maturity`, `broken_promise`, `calls`, `cibil`.
    1. Segments C and D add the callback note rule: write "call center follow-up" in the callback notes.
1. **No-push play for `call_attempt` 3**: the plan replaces the segment play. The bot says the due amount one time, then listens and answers questions. If the borrower cannot pay, it asks what amount or date works. If the borrower asks for less or offers less than the floor, the bot says the floor one time as help, with what it clears. It does not hold or repeat an Ask. The callback note rule for C and D still applies. The `broken_promise` lever does not apply on this call.
1. **After agreement**: on a Monthly interest account, the bot says one more fact after the agreement. If eNACH is not registered, the branch can set up auto-debit. If eNACH is registered and the account missed a payment in the last 6 months, the borrower must keep the bank balance ready.

The system instruction has these parts:

1. **Role**: You are Cymbal Bank's AI collections assistant. You call gold loan borrowers with unpaid interest. Your goals are to collect unpaid interest before it becomes delinquency, help the borrower avoid auction, and keep the relationship respectful. Say "interest due". Do not say "overdue" or "default" unless `days_past_due > 0` in the context. **Precedence**: in the negotiation, the bot obeys the tone for the `call_attempt` first, then the play, then the other rules. The identity, compliance and tool rules always apply.
1. **Call start and identity**:
    1. After the text `CALL_CONNECTED`, the bot speaks first, in the call language. In its first sentence, it says that it is Cymbal Bank's AI assistant and that the call is recorded.
    1. If the person only says "Hello?", "Who is this?" or "From where?", the bot says the short introduction again one time.
    1. The bot asks if it speaks with the borrower, by name, for example "Am I speaking with Mr. Sunil Pandey?". It asks for no loan ID, digits or other proof, because the call goes to the borrower's own number (§8 R28). It says no amount and no loan detail before the person says yes.
    1. If the person is not the borrower, the bot shares no loan detail. It asks for a good time to call the borrower, ends the call politely, and calls no tool.
    1. If the borrower does not answer, or the bot cannot confirm the identity, the bot calls no tool. It says that it will try again later, and then stops talking.
1. **Compliance (always)**:
    1. Do not threaten, shame, harass or abuse the borrower. Do not contact or mention family, friends or employers.
    1. Give consequences only as the levers in the call plan. For the CIBIL score, say "can". Never say "will" or "has". Never say a credit score number. Never mention other lenders.
    1. Mention statutory notices only if `days_past_due > 0`. Never say that an auction is certain or immediate. The CIBIL lever does not depend on `days_past_due`.
    1. Stop the ask at once and call `escalate_to_branch_manager` if the borrower says that a payment is not credited, disputes the balance or a charge, reports a hardship, or asks for a person. No money or no work, with no medical or other emergency, is not a hardship. It is a refusal.
    1. The backend allows calls only from 08:00 to 19:00 Asia/Kolkata (`CALL_WINDOW_IST`). `CALL_WINDOW_BYPASS=true` is only for dev and rehearsal.
1. **Call flow** (the persuasion flow):
    1. Say the due amount and the months of interest that it covers. Ask for the payment today.
    1. If the borrower does not agree and gives no reason, ask one open question about the reason.
    1. Acknowledge the reason in a few words. Then make the next Ask.
    1. Speak as if the borrower will pay. Do not ask for permission. Do not say that you send a link before the borrower agrees.
    1. Make the ask sound small and quick. Give each lower Ask as help for the borrower, for today.
    1. Do not accept "later". Get a date and an amount for each promise. Ask one time for an earlier date. Then book the promise.
    1. Say the commitment again. Then say in one sentence what you recorded.
1. **Negotiation rules**:
    1. Follow the call plan. Give one lever, then make the current Ask. With each Ask, the bot says why that amount: how many months it clears and how many stay unpaid. The plan gives the reason in months, not rupees, because the backend reads the last plan amount in a bot reply as the Ask. An offer of the floor or more always gets a yes at once, also during a hold. A hold is the same Ask one more time with a new lever, after a refusal. In a hold, the bot answers the borrower's reason, gives a lever that fits it, and asks for the same amount in new words.
    1. In plays C, D and E, the bot goes to the next Ask only after two refusals of the current Ask. Plays A and B go down after one refusal. An offer below the floor is a refusal of the current Ask.
    1. The bot talks like a person on a phone call. It reacts to what the borrower said before it asks, varies its acknowledgements, says each amount one time in a reply, and does not repeat a sentence.
    1. After two refusals of the last Ask (or of a date, when there is only one Ask) with no promise, the bot books a callback in 3 days at once, with no yes from the borrower. Then it tells the borrower the date and stops the ask. The client brief says that the system schedules callbacks automatically when the customer cannot pay.
    1. State only amounts from the call plan or the amount that the borrower offered. For a remainder, say "the remaining amount". Do not say "policy". Give a lever as the reason. If a tool returns `ok: false`, tell the borrower the correct limit from the error. Do not promise anything else.
1. **Tone by `call_attempt`**:
    1. **1 (first reminder)**: Be courteous, warm and brief. Confirm the payment date.
    1. **2 (second call)**: Be firm, factual and consultative. Make no threats. Say that this is the second call. For the callback after a partial payment link, propose 3 days.
    1. **3 (final call)**: Be empathetic and calm. Listen first. Do not argue. Do not push for payment. If there is hardship or a dispute, escalate with a clear summary and recommended action.
1. **Language**: Start in the borrower's preferred language, or in `language_override` if it has a value. Speak as Indian call center agents speak: natural code-mixed speech, not a literal translation. Use the English words "payment", "link", "interest", "outstanding amount" and "call back", and "sir", "ma'am" or "ji". When the bot speaks English, it uses an Indian accent and Indian phrasing. It says amounts in words that are natural for the language. If the borrower switches language, the bot replies in the new language from its next reply. Each reply has one to three sentences. Application code does no language detection. The backend sets `SpeechConfig.language_code="en-IN"` (`LIVE_LANGUAGE_CODE`) for all calls. Without it, the prebuilt voice spoke English with an American accent. Hindi calls with this code still sounded native in a test. Verify this behavior in the smoke test (§6).
1. **Tools**: Use only the Live tools in §7.3 (`generate_upi_payment_link`, `schedule_callback_reminder`, `escalate_to_branch_manager`). Always use the loan ID from the context.
    1. **Consent**: send a link only for an amount that the borrower names or says yes to. Book any other callback only after a yes to the date. A complaint or a question is not a yes. The callback after two refusals is the one exception (see Negotiation rules).
    1. **Link**: a link is for a payment today. For a payment on a later date, book a callback and send no link. For the full due amount, use `FULL_DUE`. For a lower amount, use `PARTIAL_TWO_MONTH` and that exact amount.
    1. **Callback after a partial link**: the bot asks if a callback date for the remaining amount suits the borrower. After a yes, it books the callback at once with no `promised_amount_inr` and no `customer_intent`.
    1. **Other callbacks**: always set `customer_intent`. Use `NEEDS_MORE_TIME` if the borrower promises an amount by a date, and set `promised_amount_inr` to that amount. Use `CANNOT_PAY_FULL` for the callback after two refusals, with no `promised_amount_inr`.
    1. **Escalation**: set `reason_category`: `PAYMENT_OR_AMOUNT_DISPUTE` for a payment not credited or a disputed balance, `PENAL_FEE_DISPUTE` for a disputed charge only, `MEDICAL_HARDSHIP` for a hardship only, `HARDSHIP_AND_FEE_DISPUTE` for both, `CUSTOMER_REQUESTED_HUMAN` if the borrower only asks for a person. Set `disputed_fee_inr` to the disputed charge, or 0. Write `customer_summary` and `recommended_action` in English. After the escalation, the bot tells the borrower that the branch will call on `branch_callback_date` from the tool result.
    1. Wait for the tool response. Do not say that you sent a link, booked a callback or escalated until the tool returns `ok: true`. After the tools, thank the borrower and say goodbye.

**WARNING: Malayalam (`ml-IN`) is not in the Live API's documented list of 24 supported `languageCode` values. The quality of Kannada is also not verified.** Native-audio Live models detect many more languages automatically. Run the Go / No-Go smoke test in §6 before the workshop. If Malayalam fails, run Scenario B in Hindi. If Kannada fails, Scenario C starts in Hindi and switches to English.

### Tool Map (Signatures in §7.3)


| **Surface** | **Model** | **Tools** |
| --- | --- | --- |
| GE agent (ADK on Agent Runtime) | `gemini-3.8-flash` | `get_calls_due_today, query_accounts, trigger_outbound_collection_call, get_call_status, get_handoff_briefs, analyze_collection_calls` |
| Live voice session | `gemini-3.8-live (or LIVE_MODEL)` | `generate_upi_payment_link, schedule_callback_reminder, escalate_to_branch_manager` |

## 6. Pre-Workshop Verification & Dry-Run Checklist (Before Oct 7)

- **Verification 1 (Demo numbering)**: Confirm that all slides and McKinsey agendas show **Demo 5: Vernacular Voice Bot for Portfolio Collections & Recovery Management** (after Demo 1 Antigravity, Demo 2 Lead Conversion, Demo 3 Underwriting, and Demo 4 Audits).
- **Verification 2 (Preflight)**: `make preflight` passes and logs the location for each model (§7.0). It stops if a model fails on Vertex AI.
- **Verification 3 (GE start prompt)**: In the real GE app, *"Who are the calls to be done today?"* returns the 1,291-account queue totals and the top rows with status, call attempt, 2-month minimum, and `callable`. *"How many high-risk Malayalam accounts are in Kochi?"* returns a number that `query_accounts` computes.
- **Verification 4 (Live call, Phase 1 `mobile`)**: From GE, trigger a call. The paired `/mobile` phone rings, and audio goes in both directions. Barge-in stops playback at once. Record the measured response latency: the time from the end of the borrower speech to the first bot audio.
- **Verification 5 (Scenario B script)**: Do a dry run of Scenario B in Malayalam and in Hindi. The bot asks for ₹8,185, refuses the ₹1,637 counter-offer, gets agreement on ₹3,274, sends the link, and books a 3-day callback.
- **Verification 6 (Malayalam Go / No-Go)**: Run 3 full Scenario B calls in Malayalam with a native speaker. The result is **Go** if the bot understands the borrower, replies in natural Malayalam, and states ₹3,274 correctly in all 3 runs. If not, the result is **No-Go**. For **No-Go**, set `language_override = Hindi` on GL0106397512. Then run Scenario B in Hindi at the workshop. Record the result in this doc.
- **Verification 7 (Language switch)**: Do a dry run of Scenario C. The borrower starts in Kannada and switches to Hindi. Judge the Kannada quality with the same method as Malayalam. The bot follows in Hindi without code changes.
- **Verification 8 (Closed loop in GE)**: After Scenario C, GE shows `GL6095694164` as `ESCALATED_BRANCH_HARDSHIP` with the handoff brief. *"Analyze the calls"* returns the expected values in §3 Stage 4.
- **Verification 9 (Reset and idle)**: `POST /api/admin/reset` reloads the pool (§7.2) and applies the seeded outcomes when `SEED_OUTCOMES=true`. Run it before each rehearsal and before the live session. With `make demo-up`, the store keeps its state through 20 minutes of idle time.
- **Verification 10 (Twilio, Phase 2)**: With `TELEPHONY_MODE=twilio`, a call reaches an allow-listed presenter phone and runs Scenario A end to end. The backend rejects a call to a number that is not allow-listed, before it calls Twilio.
- **Verification 11 (Fallback)**: The pre-rendered TTS fallback plays only if live calls fail. The presenter tells the audience that it is a fallback recording.

## 7. Technical Build Contract (Single Source of Truth for AI Coding Agent)

This section is the engineering contract for an AI coding agent. The agent uses it to build and verify Demo 5 end to end. If this section conflicts with Sections 1–6, this section wins. **Follow the build order in §7.8.** If a load-bearing assumption fails, stop and report to Adhish. Ask Adhish before you change the design.

### 7.0 Environment & Agent Privileges

- **Project**: Read the project at runtime with `gcloud config get-value project`. Never hardcode a project ID. The build machine has gcloud ADC for Adhish's Argolis project. Adhish is Owner and GE Admin. No other auth design is in scope.
- **Locations** (preflight 2026-10-03, see `build_log.md`): Cloud Run, the buckets and Agent Runtime are in `asia-south1`. The GE app uses `global`. The timezone for all business rules is `Asia/Kolkata` (zoneinfo).
- **Model surface: Vertex AI only**. Use ADC and one location for each model: `genai.Client(vertexai=True, project=<from gcloud>, location=<model location>)`.
    - `gemini-3.8-flash` and `gemini-3.8-flash-tts`: `global` (`GENAI_LOCATION_FLASH`, `GENAI_LOCATION_TTS`).
    - `gemini-3.8-live`: `us-central1` (`GENAI_LOCATION_LIVE`). Vertex AI does not serve it on `global` or in India.
    - Preflight writes these locations to `.env.genai`. The backend logs them at startup and shows them on `/monitor`.
    - There is no AI Studio fallback. If a model fails on Vertex, STOP. Never use a model that is not Gemini 3.x.
- **The agent MAY**:
    - Enable these APIs: Vertex AI, Cloud Run, Cloud Build, Artifact Registry, Secret Manager, Cloud Storage, Discovery Engine.
    - Create service accounts in this project and grant them roles in this project.
    - Create the staging bucket `gs://<project>-mcd5-agent-staging`.
    - Deploy Cloud Run (`--allow-unauthenticated`) and Agent Runtime.
    - Create and read secrets.
- **The agent MUST NOT**:
    - Delete or change resources that it did not create.
    - Change IAM or org policy.
    - Change a GE app other than Cymbal Bank-Demo-App. (Adhish lifted the GE registration rule on 2026-10-03. The agent registers the agents in Cymbal Bank-Demo-App, §7.6.)
    - Make real calls, or send SMS or WhatsApp messages, to a number that is not on the allow-list.
    - Put secrets in code, logs, or the repo.
- **Preflight (`make preflight`)**:
    - Print the project and the active account.
    - Run `gcloud org-policies describe iam.allowedPolicyMemberDomains --effective --project=$P`. If it blocks `allUsers`, STOP. Give Adhish these human steps:
        1. Create `policy.yaml` with `name: projects/$P/policies/iam.allowedPolicyMemberDomains` and `spec: {rules: [{allowAll: true}]}`.
        1. Run `gcloud org-policies set-policy policy.yaml`.
    - On Vertex, list the models (`client.models.list()`). Then probe each model:
        - `gemini-3.8-flash`: Make one short `generate_content` call.
        - `gemini-3.8-flash-tts`: Make one short TTS `generate_content` call with audio output.
        - `gemini-3.8-live` (and `gemini-3.8-live-extended-thinking` only if `LIVE_MODEL` selects it): Make a 5 s `client.aio.live.connect(...)`. It must open without error.
    - Probe each model on `global`, then `asia-south1`, then `us-central1`. The first location that passes is the location of the model.
    - Write `.env.genai`. Print a table: model, location, result.
- **Secrets (Secret Manager)**:
    - `mcd5-demo-agent-token`: the bearer token for `/api/agent/*` and `/api/admin/*`.
    - `mcd5-view-token-key`: the HMAC key for one-time pairing links, `/monitor` view tokens and recording links.
    - Phase 2 only: `mcd5-twilio-account-sid`, `mcd5-twilio-api-key-sid`, `mcd5-twilio-api-key-secret` (Standard key, for the REST API), `mcd5-twilio-auth-token` (signature checks only), `mcd5-twilio-from-number`, `mcd5-twilio-allowed-numbers` (E.164, comma-separated), `mcd5-demo-phone-map` (JSON, loan ID → an allow-listed number or a list of them, with a random pick; the key `"*"` maps all other loans).
- **Runtime config (env)**:
    - `TELEPHONY_MODE=mobile|twilio` (default `mobile`). This is the mode at startup. `POST /api/admin/telephony {"mode"}` changes it at run time. `/mobile` works in both modes and is the fallback.
    - `LIVE_MODEL` (default `gemini-3.8-live`).
    - `GENAI_LOCATION_*` (from preflight).
    - `CALL_WINDOW_IST=08:00-19:00`.
    - `CALL_WINDOW_BYPASS=false`. Set it to `true` only for dev and rehearsal. Never use it on the demo deploy.
    - `SEED_OUTCOMES=true`.
    - `PUBLIC_BASE_URL` (the https Cloud Run URL).
    - `BACKEND_URL` (for the ADK agent, with the same value as `PUBLIC_BASE_URL`).
- **Human prerequisites (Adhish)**:
    - Before Oct 6, confirm that the Argolis GE app exists, and that Supriya has access and a license.
    - Register the agent in GE (§7.6).
    - Apply the org-policy override if preflight asks for it.
    - Make the Malayalam Go / No-Go judgment (§6).
    - Close the Twilio prerequisites (§7.4) before Phase 2.

### 7.1 Gemini 3.x Model & SDK Matrix (Strict Policy)

**SDK policy**:

- Use **`google-genai >= 2.25.0`** (Python) for **all model calls**, on Vertex AI at the `GENAI_LOCATION_*` locations.
- Use **`google-adk`** and **`google-cloud-aiplatform[agent_engines,adk]`** only to build and deploy the GE agent to Agent Runtime.
- The only banned package is the deprecated **`google-generativeai`**.
- The browser never calls models. Thus the frontend does not need `@google/genai`.
- Use only Gemini 3.x models.


| **Subsystem** | **Model ID** | **SDK Entry Point and Configuration** | **Purpose** |
| --- | --- | --- | --- |
| Live voice agent (default) | `gemini-3.8-live (env LIVE_MODEL)` | `client.aio.live.connect(model=LIVE_MODEL, config=LiveConnectConfig(response_modalities=["AUDIO"], input_audio_transcription=AudioTranscriptionConfig(), output_audio_transcription=AudioTranscriptionConfig(), system_instruction=..., tools=[live_tools(LIVE_MODEL)])). Tools are BLOCKING. No thinking_config (not supported on this model).` | Two-way vernacular call. 16 kHz PCM in, 24 kHz PCM out. Transcripts feed the call record, the handoff brief, and `/monitor.` |
| Live voice agent (optional) | `gemini-3.8-live-extended-thinking (set LIVE_MODEL)` | Same config plus `thinking_config=ThinkingConfig(thinking_level="low"). Tools must be NON_BLOCKING, with no scheduling field. Keep listening after turn_complete. Use interaction_status (IN_PROGRESS / IDLE).` | Background reasoning with spoken fillers. Not the default. |
| GE agent | `gemini-3.8-flash` | `google-adk Agent(...) wrapped in AdkApp, deployed to Agent Runtime (§7.6).` | Start prompt, call triggers, status, handoff briefs, narration of "Analyze the calls". |
| Post-call summary | `gemini-3.8-flash` | `client.models.generate_content(model="gemini-3.8-flash", ...) with a JSON response schema. Runs once per call at call end.` | Per-call summary and sentiment. No numbers come from this step. |
| Fallback audio | `gemini-3.8-flash-tts` | `client.models.generate_content(model="gemini-3.8-flash-tts", ...). Pre-render once to files. Included in preflight.` | Use only if live calling fails at the venue. The presenter says it is a recording. |
| Out of scope | `gemini-3.5-transcribe-live` | Not used. Transcription comes from the Live session. | — |

### 7.2 Complete Deterministic Dataset Contract (McKinsey Sample Pool, 2,000 Accounts)

*McKinsey sample data (`docs/data/Vernacular_Collections_Bot_Sample_Data_vF.xlsx`, as-of date 2026-10-03). The workbook's amounts are authoritative. The backend never calculates them again.*

- **Source of truth at runtime**: `backend/src/muthoot_collections/data/customers.jsonl`. It has one account on each line, with all 45 workbook columns in snake_case. `backend/scripts/convert_workbook.py` generates it (`uv run backend/scripts/convert_workbook.py`). Only that script uses openpyxl. The output is deterministic, and the repo contains it. The backend loads it with `json` at startup and on `POST /api/admin/reset`, and keeps it in memory. There is no database.
- **Keys**: `loan_id` (workbook `Loan account no.`, e.g. `GL0106397512`) is the key for each tool and endpoint. The output shows `customer_id` (`MFLC…`) next to it. Names are not unique.
- **Phones**: The workbook's numbers are random and can belong to real people. The converter replaces them with `+9110000NNNNN` (row number). This is not a valid Indian mobile prefix. The backend only shows these numbers and never dials them.
- **Amount fields**:
    - `due_amount_inr` = months due × `monthly_interest_inr` (the workbook's `Due amount`). Months due is `bullet_months_due` for Bullet and `monthly_months_unpaid` for Monthly interest.
    - `bot_ask_amount_inr` = MIN(months due, 2) × `monthly_interest_inr` (the workbook's `Bot ask amount`). This is the 2-month minimum. For 1,425 of 2,000 accounts, it equals `due_amount_inr`. Thus a partial payment is not possible on these accounts.
    - A check of the converter's run found that both formulas hold on all 2,000 rows.
- **Call queue**: The queue contains the accounts whose `suggested_channel` is `Voice bot` or `Bot + call center`. It has 1,291 accounts and ₹74,35,486 due. The agent can still query and call each account in the pool.
- **`call_attempt`** (the backend derives it at load, because the workbook has no such column):
    - 3 if `last_contact_outcome` is `Refused / disputed` or `broken_promises_3m >= 2`;
    - else 2 if `last_contact_channel` is `Voice bot` or `Call center`;
    - else 1.
    - The queue has 583 first calls, 417 second calls and 291 final calls.
- **"Today" for amounts**: The amounts and `days_past_due` keep their values from the workbook as-of date. The call window, link dates and callback dates use the real Asia/Kolkata clock.
- **Language**: `language` is the default. `language_override` (default null, set with `POST /api/admin/language`) replaces it for the next call. A reset clears it.

**Seeded outcomes (accounts not called live)**: "Seeded outcome" always means a result that the admin transition `seed_outcome` applies (§7.3 C). When `SEED_OUTCOMES=true` (the default), it runs at startup and after each reset. It skips pairing, the call window and the Live session. It still runs the amount and enum invariants. It does not change `call_attempt`. The backend marks each result with `source="seeded"`.


| **Loan ID** | **Seeded Intent** | **Seeded Tool Calls** | **Resulting Status** |
| --- | --- | --- | --- |
| GL1814280043 (Saravanan Raman) | INT-02 `NEEDS_MORE_TIME` | `schedule_callback_reminder(days_from_today=3, promised_amount_inr=17965, notes="Promises full payment in 3 days")` | `CALLBACK_SCHEDULED` |
| GL9061256232 (Ravi Varma) | INT-03 `CANNOT_PAY_FULL` | `generate_upi_payment_link(amount_inr=6868, settlement_type="PARTIAL_TWO_MONTH") then schedule_callback_reminder(days_from_today=3, notes="Remaining ₹6,868")` | `PARTIAL_SETTLEMENT_AGREED` |

### 7.3 Deterministic Tool Schemas, Statuses & Backend Guardrails

This section is the **single source of truth** for tool names, parameters, statuses, invariants, and KPIs. **`store.py` enforces every invariant in Python before any state change.** Prompts are not a control.

#### A. GE Agent Tools (ADK on Agent Runtime, gemini-3.8-flash)

Each tool is a thin Python function. It calls the backend over HTTPS with `Authorization: Bearer <mcd5-demo-agent-token>` and returns the backend JSON unchanged.


| **Tool** | **Signature** | **Backend Endpoint** | **Returns** |
| --- | --- | --- | --- |
| `get_calls_due_today` | `get_calls_due_today(limit: int = 10) -> dict` | `GET /api/agent/calls-due?limit=` | The call queue (§7.2): `total_accounts`, `total_due_inr`, counts by `language`, `treatment_segment`, `risk_tier` and `call_attempt`, and the top `limit` rows (max 50) by `risk_score` desc, then `due_amount_inr` desc. Each row: identity, branch, language, scheme, months due, `due_amount_inr`, `bot_ask_amount_inr`, `risk_tier`, `status`, `call_attempt`, `language_override`, and `callable` (true only if status is `DUE_FOR_CALL` or `NO_OUTCOME` and calling is not frozen). Also `handled_today`: every account with a call record today (seeded records always count), with status and outcome. |
| `query_accounts` | `query_accounts(filters: dict \| None = None, group_by: str \| None = None, sort_by: str \| None = None, descending: bool = True, limit: int = 10) -> dict` | `POST /api/agent/accounts/query` | Any question about the pool. `filters` maps a column to a value (equality), a list (any of), `{"min": n, "max": n}` (numbers and ISO dates), or `{"contains": "text"}` (case-insensitive). Columns: every JSONL column plus `status`, `call_attempt`, `callable`, `in_queue`. Returns `matched` (count), `totals` (`due_amount_inr`, `bot_ask_amount_inr`, `pos_inr` sums), `groups` (per `group_by` value: count and the same sums, sorted by count desc) and `rows` (at most `limit`, max 50). An unknown column or bad filter returns `{ok: false, error}`. |
| `trigger_outbound_collection_call` | `trigger_outbound_collection_call(loan_id: str) -> dict` | `POST /api/agent/calls {"loan_id": ...}` | `{ok, call_id, mode}, or {ok: false, error} (not callable, another call active, outside call window, no paired /mobile, number not allow-listed).` |
| `get_call_status` | `get_call_status(loan_id: str) -> dict` | `GET /api/agent/accounts/{loan_id}` | Status, `call_attempt, last call outcome, intent and source (live or seeded), amounts, promised_by, next_callback_date, post-call summary, transcript excerpt.` For a frozen account, `next_callback_date` is the branch callback date (section C). |
| `get_handoff_briefs` | `get_handoff_briefs() -> dict` | `GET /api/agent/handoffs` | All handoff briefs: profile, customer intent, reason, recommended action, `branch_callback_date`, full transcript. |
| `analyze_collection_calls` | `analyze_collection_calls() -> dict` | `GET /api/agent/analysis` | Deterministic KPI JSON (section F) plus per-call summaries. |

**Call records (bearer)**: each call record has `intent`, a list of intent IDs (section C). `GET /api/agent/accounts/{loan_id}/calls` returns the calls of one account, newest first, with no transcripts. `GET /api/agent/calls/{call_id}` returns one call with the full transcript and each tool call (`name`, `args`, `ok`, `error`, `at`), rejected calls too. At call end, the backend also writes that JSON once to `gs://<project>-mcd5-call-recordings/<call_id>/transcript.json`.

#### B. Live Voice Tools (LIVE\_MODEL)

`live_tools(LIVE_MODEL)` builds the declarations:

- **`gemini-3.8-live`**: `behavior="BLOCKING"`. The model waits for the result. The store tools run in memory (under 50 ms).
- **`gemini-3.8-live-extended-thinking`**: `behavior="NON_BLOCKING"` (required), with no scheduling field.

The backend binds `loan_id` to the session. If the model sends a different `loan_id`, the backend rejects the call. `customer_intent` sets the call intent (section C). The backend rejects a value that is not in the enum.

```
from google.genai import types
def live_tools(live_model: str) -> types.Tool:
    behavior = "NON_BLOCKING" if "extended-thinking" in live_model else "BLOCKING"
    return types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="generate_upi_payment_link",
            description=(
                "Sends a (mocked) SMS/WhatsApp UPI link for the full interest due (FULL_DUE), "
                "or for a partial payment (PARTIAL_TWO_MONTH): any amount from the floor in the "
                "call plan up to less than the due amount."
            ),
            behavior=behavior,
            parameters=types.Schema(
                type="OBJECT",
                required=["loan_id", "amount_inr", "settlement_type"],
                properties={
                    "loan_id": types.Schema(type="STRING", description="Loan ID, e.g. GL0106397512"),
                    "amount_inr": types.Schema(type="INTEGER", description="Amount in INR"),
                    "settlement_type": types.Schema(type="STRING", enum=["FULL_DUE", "PARTIAL_TWO_MONTH"]),
                },
            ),
        ),
        types.FunctionDeclaration(
            name="schedule_callback_reminder",
            description="Books a callback 1 to 7 days from today, with an optional promised amount.",
            behavior=behavior,
            parameters=types.Schema(
                type="OBJECT",
                required=["loan_id", "days_from_today", "notes"],
                properties={
                    "loan_id": types.Schema(type="STRING"),
                    "days_from_today": types.Schema(type="INTEGER", description="1 to 7"),
                    "promised_amount_inr": types.Schema(type="INTEGER", description="Optional amount promised by the callback date"),
                    "notes": types.Schema(type="STRING"),
                    "customer_intent": types.Schema(
                        type="STRING",
                        enum=["NEEDS_MORE_TIME", "CANNOT_PAY_FULL"],
                        description="Optional. NEEDS_MORE_TIME if not set.",
                    ),
                },
            ),
        ),
        types.FunctionDeclaration(
            name="escalate_to_branch_manager",
            description="Freezes automated calling, stores a handoff brief for the branch team, and ends negotiation.",
            behavior=behavior,
            parameters=types.Schema(
                type="OBJECT",
                required=["loan_id", "reason_category", "disputed_fee_inr", "customer_summary", "recommended_action"],
                properties={
                    "loan_id": types.Schema(type="STRING"),
                    "reason_category": types.Schema(
                        type="STRING",
                        enum=["MEDICAL_HARDSHIP", "PENAL_FEE_DISPUTE", "HARDSHIP_AND_FEE_DISPUTE", "CUSTOMER_REQUESTED_HUMAN", "PAYMENT_OR_AMOUNT_DISPUTE"],
                    ),
                    "disputed_fee_inr": types.Schema(type="INTEGER", description="0 if no fee is disputed"),
                    "customer_summary": types.Schema(type="STRING"),
                    "recommended_action": types.Schema(type="STRING"),
                },
            ),
        ),
    ])
```
**Tool response protocol**: On `message.tool_call`, run each function call in `store.py`. Then send this response:

```
await session.send_tool_response(function_responses=[types.FunctionResponse(id=fc.id, name=fc.name, response=result)])
```
`result` is always `{"ok": true, ...}` or `{"ok": false, "error": "<message the bot can say>"}`. The bot never claims success before `ok: true`.

#### C. Status Enum, Call Fields and Allowed Transitions

- **`AccountStatus`**: `DUE_FOR_CALL`, `CALL_IN_PROGRESS`, `PAYMENT_LINK_SENT_TODAY`, `PARTIAL_SETTLEMENT_AGREED`, `CALLBACK_SCHEDULED`, `DISPUTE_LOGGED`, `ESCALATED_BRANCH_HARDSHIP`, `NO_OUTCOME`.
- **Account call fields**:
    - `active_call_id`: The trigger sets it, and the call end clears it. "During a call" means `active_call_id == session.call_id`.
    - `call_attempt`: The attempt number of the current or last call. It starts at the value that §7.2 derives. It does not change after an outcome.
    - `promised_by`: On a `FULL_DUE` link, the backend sets it to today 23:59 Asia/Kolkata ("by end of day"). `get_call_status` returns it. The KPIs do not use it.
    - `next_callback_date`, `language_override`, `calling_frozen`.
    - On a frozen account, `next_callback_date` is the branch callback date. An escalation sets it to today + 2 days (`BRANCH_CALLBACK_DAYS`), and it clears `promised_by`, `promised_amount_inr` and the callback notes. Then the account status does not show a bot promise next to the branch date.
    - A `PARTIAL_TWO_MONTH` link clears `promised_amount_inr` from an earlier promise callback in the same call. `next_callback_date` stays for the remaining amount. Thus the KPIs count the link and not the promise.


| **From** | **Event** | **To** |
| --- | --- | --- |
| `DUE_FOR_CALL` or `NO_OUTCOME` | `trigger_outbound_collection_call succeeds` | `CALL_IN_PROGRESS (sets active_call_id = uuid4; call_attempt unchanged on a NO_OUTCOME retry)` |
| `DUE_FOR_CALL` | `seed_outcome (admin/startup only, source=seeded)` | Opens call record `seed-<loan_id>, applies the §7.2 seeded tool calls, and ends with their status. Skips pairing, call window and Live session. Runs D-1 to D-4, D-12 and enum checks. call_attempt unchanged.` |
| `CALL_IN_PROGRESS`, or `CALLBACK_SCHEDULED` during a call | `generate_upi_payment_link(FULL_DUE)` | `PAYMENT_LINK_SENT_TODAY` (clears the callback and its promised amount) |
| `CALL_IN_PROGRESS or CALLBACK_SCHEDULED, during a call` | `generate_upi_payment_link(PARTIAL_TWO_MONTH)` | `PARTIAL_SETTLEMENT_AGREED` |
| `CALL_IN_PROGRESS` | `schedule_callback_reminder` | `CALLBACK_SCHEDULED` |
| `CALLBACK_SCHEDULED, during a call` | `schedule_callback_reminder` | `CALLBACK_SCHEDULED` (moves the callback: the date, promised amount and notes overwrite the old values) |
| `PARTIAL_SETTLEMENT_AGREED, during a call` | `schedule_callback_reminder` | `PARTIAL_SETTLEMENT_AGREED (sets next_callback_date)` |
| Any non-frozen status, during a call | `escalate_to_branch_manager(PENAL_FEE_DISPUTE or PAYMENT_OR_AMOUNT_DISPUTE)` | `DISPUTE_LOGGED (frozen)` |
| Any non-frozen status, during a call | `escalate_to_branch_manager(other categories)` | `ESCALATED_BRANCH_HARDSHIP (frozen)` |
| `CALL_IN_PROGRESS` | Call ends (any reason, §7.4) with no outcome tool | `NO_OUTCOME` |
| Any | `POST /api/admin/reset` | Replace the whole store (pool, call records, link log, briefs, pairing kept), then `seed_outcome if SEED_OUTCOMES=true` |

- **Terminal for the demo day**: `PAYMENT_LINK_SENT_TODAY`, `PARTIAL_SETTLEMENT_AGREED`, `CALLBACK_SCHEDULED`, `DISPUTE_LOGGED`, `ESCALATED_BRANCH_HARDSHIP`.
- **`reason_category` → status and customer intent** (the handoff brief stores both):


| **`reason_category`** | **Status** | **Customer intent** |
| --- | --- | --- |
| `PENAL_FEE_DISPUTE` | `DISPUTE_LOGGED` | INT-04 |
| `PAYMENT_OR_AMOUNT_DISPUTE` | `DISPUTE_LOGGED` | INT-04 |
| `MEDICAL_HARDSHIP` | `ESCALATED_BRANCH_HARDSHIP` | INT-05 |
| `CUSTOMER_REQUESTED_HUMAN` | `ESCALATED_BRANCH_HARDSHIP` | INT-05 |
| `HARDSHIP_AND_FEE_DISPUTE` | `ESCALATED_BRANCH_HARDSHIP` | INT-05 + INT-04 |

- **Call `intent`** (the call record stores it; `store.py` sets it from the successful tool calls):
    1. `generate_upi_payment_link(FULL_DUE)` sets INT-01. `generate_upi_payment_link(PARTIAL_TWO_MONTH)` sets INT-03.
    1. `schedule_callback_reminder` with `customer_intent` `NEEDS_MORE_TIME` (the default) sets INT-02. With `CANNOT_PAY_FULL` it sets INT-03. If the same call already has a link, the callback does not change the intent.
    1. `escalate_to_branch_manager` sets the intent from the table above. It replaces an earlier intent.
    1. A call with no successful tool call has an empty `intent`.
- **Audit event name**: the audit record of a dispute escalation has the event name `escalate_to_branch_manager(PENAL_FEE_DISPUTE)`, also for `PAYMENT_OR_AMOUNT_DISPUTE`. The audit details carry the real `reason_category` and the `branch_callback_date`.

#### D. Backend Invariants (Deterministic, Unit-Tested)

1. **Minimum partial payment**: For `PARTIAL_TWO_MONTH`, require `bot_ask_amount_inr <= amount_inr < due_amount_inr`. If not, return `{ok: false, error: "Minimum partial payment is 2 months' interest (₹<floor>)."}`. If `bot_ask_amount_inr == due_amount_inr`, return `{ok: false, error: "This account's minimum is the full due amount (₹<due>)."}`.
1. **Full payment is exact**: For `FULL_DUE`, require `amount_inr == due_amount_inr`.
1. **Amount cap**: `amount_inr` and `promised_amount_inr` must be `>= 1` and `<= due_amount_inr`.
1. **Callback range**: Require `1 <= days_from_today <= 7`. If not, return `{ok: false, error: "A callback must be 1 to 7 days from today."}`. The bot says the reason, not "policy". After a partial link (`PARTIAL_SETTLEMENT_AGREED`), reject a callback with `promised_amount_inr`. The borrower made no promise for the remaining amount, and a computed promise would change the KPIs.
1. **Frozen accounts**: If the status is `DISPUTE_LOGGED` or `ESCALATED_BRANCH_HARDSHIP`, reject the trigger and all payment and callback tools.
1. **Status guard**: Reject any transition that is not in the table in section C.
1. **Idempotent link sends**: The key is `(call_id, loan_id, settlement_type, amount_inr)`. Thus a rehearsal earlier on the same day never blocks the live run. A repeat returns the existing `link_id` with `duplicate: true` and sends nothing new. Escalation is idempotent for each `(loan_id, call_id)`.
1. **Session binding**: The `loan_id` in a Live tool call must equal the session's `loan_id`.
1. **Call window**: Reject triggers outside 08:00–19:00 Asia/Kolkata, unless `CALL_WINDOW_BYPASS=true`. Unit tests cover both edges.
1. **Dial allow-list**: In `twilio` mode, dial only numbers in `mcd5-twilio-allowed-numbers`. Never dial the sample phone numbers.
1. **Single active call**: Reject the trigger if any account has a value in `active_call_id` (`error: "Another call is in progress."`).
1. **Escalation fields**: Require `0 <= disputed_fee_inr <= due_amount_inr`. `notes`, `customer_summary` and `recommended_action` each have a maximum of 2,000 characters.

No invariant D-1 to D-12 changed. D-6 now covers the new transition in section C (a callback in `CALLBACK_SCHEDULED`). The backend rejects a `customer_intent` or `reason_category` value that is not in its enum, in the shape check before the other checks.

#### E. Mocked Side Effects

The backend **mocks** SMS, WhatsApp and UPI. A "sent" link writes a structured log entry (`link_id`, `loan_id`, amount, masked phone, timestamp) and shows a payment-link card on `/mobile`. The UPI URL uses a fake payee (`upi://pay?pa=collections@cymbalbank&am=<amount>&tn=<loan_id>`). Nothing processes the UPI URL.

#### F. Deterministic KPIs (GET /api/agent/analysis)

`store.py` computes these KPIs. The model only narrates them.

- `queue_accounts`, `queue_due_inr` = the call queue (§7.2): 1,291 and ₹74,35,486. These values are context only.
- "Today" is the Asia/Kolkata date for live records. Seeded records are fixtures. They always count, whatever the date of the last reset.
- `called_accounts`, `called_due_inr` = the accounts with a call record today, and the sum of their `due_amount_inr`.
- `calls_attempted` = the call records created today (live + seeded outcomes). This count includes `NO_OUTCOME`.
- `calls_with_outcome` = the call records that ended in a terminal status, split into `live` and `seeded`.
- `outcome_rate_pct` = `calls_with_outcome / calls_attempted`, 1 decimal place (R8). It is not a connect rate, because seeded records never connected. Show it with the live / seeded split.
- `links_sent_today_inr` = the sum of link amounts sent today. This is activity, so it includes links on accounts with a later escalation.
- `promised_within_7_days_inr` = the sum of the open callback's `promised_amount_inr` on `CALLBACK_SCHEDULED` and `PARTIAL_SETTLEMENT_AGREED` accounts.
- `total_committed_inr` = links sent today on accounts that are not escalated + `promised_within_7_days_inr`.
- `committed_pct` = `total_committed_inr / called_due_inr`, 1 decimal place. (Against the whole queue, it rounds to 0.)
- `partial_agreements` = the count of `PARTIAL_SETTLEMENT_AGREED`.
- `escalations` = the count of `DISPUTE_LOGGED` + `ESCALATED_BRANCH_HARDSHIP`, with branch and disputed fee.
- **Expected values, seeds only (M1)**: queue 1,291 / ₹74,35,486; 2 called, ₹31,701 due; 2 attempted, 2 with outcome (0 live, 2 seeded); outcome rate 100.0%; ₹6,868 links today; ₹17,965 promised; ₹24,833 committed (78.3%); 1 partial agreement; 0 escalations.
- **Expected values, scripted path (M2)**: queue 1,291 / ₹74,35,486; 5 called, ₹73,448 due; 5 attempted, 5 with outcome (3 live, 2 seeded); outcome rate 100.0%; ₹18,754 links today; ₹17,965 promised; ₹36,719 committed (50.0%); 2 partial agreements; 1 escalation (₹1,800).

### 7.4 Live Audio Architecture, Telephony Modes (mobile, twilio) & Cloud Run Deployment

The backend has one call pipeline with two telephony adapters. `TELEPHONY_MODE` selects the adapter. `live_session.py` is the same in both modes.

#### Common Audio Path

- **Pipeline**: Audio source -> backend WebSocket -> `client.aio.live.connect(...)`. Input: `session.send_realtime_input(audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000"))`. Output: 24 kHz 16-bit mono PCM.
- **Session context**: At connect, the backend adds the account context and `language_override` (if set) to the system instruction (§5).
- **Tool dispatch**: Follow the §7.3 B protocol. Tools are `BLOCKING` on `gemini-3.8-live` and `NON_BLOCKING` on extended-thinking.
- **Barge-in**: The Live API server VAD detects it. When `server_content.interrupted == True`, tell the adapter to drop all queued playback at once.
- **Extended thinking only**: Continue to listen after `turn_complete`. The turn ends only on `interaction_status == IDLE`.
- **Transcripts**: Store the input and output transcription for each call. They feed the call record, the handoff brief, and `/monitor` (SSE).
- **Call end**: A call ends only on borrower hang-up (End on `/mobile`), a 45 s no-answer timeout, a phone socket close, or a Live session error. An outcome tool never ends the call, because Scenario B needs two tools in one call. The call end runs in a `finally` path, in this sequence:
    1. Clear `active_call_id`.
    1. If no outcome tool succeeded, set `NO_OUTCOME`.
    1. Close the Live session.
    1. Run the post-call summary and the recording.

    A failure in the summary or the recording never leaves the call lock set.

#### Phase 1: TELEPHONY\_MODE=mobile (Default, Dev Path, Venue Fallback)

1. **Pairing (one-time link)**: `make pair-link` (admin token) calls `POST /api/admin/pair-link`. It prints a URL and QR code: `<PUBLIC_BASE_URL>/mobile#pair=<token>`. The backend signs the token with HMAC and `mcd5-view-token-key`. The token is valid for 24 h and works once. When a phone opens the link, the backend pairs that phone and replaces any earlier pairing. The page stores the returned device credential for reconnects.

    Mint a few links before the session. Give them to the on-site presenters, so they can pair again without the admin token. `/monitor` does not show or confirm a pairing code (R10).
1. **Reconnect**: `/ws/mobile` reconnects automatically with the device credential (backoff 1, 2, 4 s, up to 30 s). Cloud Run closes sockets at 3600 s.
1. **Ring**: On `trigger_outbound_collection_call`, the backend pushes `{"type": "incoming_call", "call_id", "caller": "Cymbal Bank — AI Collections Desk"}` to the paired phone. If no phone is paired, the trigger returns `ok: false`.
1. **Accept**: When the presenter taps **Accept**:
    1. Create or resume the `AudioContext`.
    1. Call `getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}})`.
    1. The `AudioWorklet` resamples the device rate (usually 48 kHz) to 16 kHz 16-bit mono PCM and streams it to `WS /ws/live-call/{call_id}`. The backend accepts the socket only with the paired device credential and only for the current `active_call_id`.
    1. Play the 24 kHz PCM through a separate `AudioContext({sampleRate: 24000})`.
    1. Show the mocked payment-link cards.
    1. Use earphones on stage, so speaker echo does not trigger barge-in.
1. **No answer**: If no one answers in 45 s, the status becomes `NO_OUTCOME`.

#### Mobile and Monitor Wire Protocol (M2)

Tokens go in the URL fragment (`#`), the request body, the first socket message or a header. They never go in a URL path or query, because Cloud Run logs the request URL.

1. **Pair link**: `POST /api/admin/pair-link` (bearer) returns `{"ok": true, "url": "<PUBLIC_BASE_URL>/mobile#pair=<token>", "expires_at"}`.
1. **Pair**: The `/mobile` page reads the fragment and removes it from the address bar. It sends `POST /api/mobile/pair {"pair": "<token>"}`. The backend returns `{"ok": true, "device": "<credential>"}`, or 401 if the token is bad, expired or used. The page keeps the credential in `localStorage`.
1. **Control socket** `WS /ws/mobile`:
    1. The first message from the phone is `{"type": "auth", "device": "<credential>"}`. If the credential is not the current pairing, the backend closes the socket with code 4401. The page then shows "Pair again".
    1. The backend sends `{"type": "ready"}`. Then it sends `{"type": "incoming_call", "call_id", "caller", "customer_name", "language"}` when a call starts.
    1. The phone sends `{"type": "decline", "call_id"}` to decline. The backend then ends the call with `NO_OUTCOME`.
    1. The backend sends `{"type": "call_ended", "call_id", "reason", "status"}` when a call ends.
1. **Call socket** `WS /ws/live-call/{call_id}`: To open this socket is to accept the call.
    1. The first message is `{"type": "auth", "device": "<credential>"}`. If the credential is bad, the backend closes with 4401. If `call_id` is not the `active_call_id`, it closes with 4403.
    1. Phone to backend: binary frames of 16 kHz 16-bit little-endian mono PCM, about 20–100 ms each. The text message `{"type": "hangup"}` ends the call.
    1. Backend to phone: binary frames of 24 kHz 16-bit little-endian mono PCM. Text messages: `{"type": "interrupted"}` (drop all queued playback now), `{"type": "payment_link", "amount_inr", "settlement_type", "upi_url"}`, `{"type": "callback", "date", "promised_amount_inr"}`, `{"type": "escalated", "branch"}` and `{"type": "call_ended", "reason", "status"}`.
    1. When the socket closes, the call ends.
1. **Monitor link**: `POST /api/admin/monitor-link` (bearer) returns `{"ok": true, "url": "<PUBLIC_BASE_URL>/monitor#t=<view token>"}`. The view token is valid for 24 h.
1. **Monitor stream**: `GET /api/monitor/stream` with `Authorization: Bearer <view token>` returns `text/event-stream`. The page reads it with `fetch`, because `EventSource` cannot send a header. Each event has a JSON `data` line with a `type`:
    1. `state`: `{"active_call": null | {"call_id", "loan_id", "customer_name", "language", "call_attempt", "status"}, "live_model", "locations"}`. The backend sends it on connect and after each change.
    1. `transcript`: `{"call_id", "role": "customer" | "bot", "text"}`, one event for each finished turn.
    1. `tool`: `{"call_id", "name", "args", "ok", "error"}`.
    1. `call_ended`: `{"call_id", "reason", "status"}`.

#### Phase 2: TELEPHONY\_MODE=twilio (After v1)

1. **Outbound call**: `client.calls.create(to=<number from mcd5-demo-phone-map>, from_=<mcd5-twilio-from-number>, url=f"{PUBLIC_BASE_URL}/api/twilio/voice", status_callback=f"{PUBLIC_BASE_URL}/api/twilio/status", status_callback_event=["completed"], timeout=45)`. The number must be in `mcd5-twilio-allowed-numbers`. Never use the sample phones in §7.2.
1. **TwiML webhook**: `POST /api/twilio/voice` returns:

```
<Response><Connect><Stream url="wss://<PUBLIC_BASE_URL host>/ws/twilio"><Parameter name="call_id" value="{call_id}"/></Stream></Connect></Response>
```
1. **Media Streams bridge** (`WS /ws/twilio`, `twilio_bridge.py`):
    1. Handle the inbound events: `connected`, `start`, `media`, `mark`, `stop`.
    1. Read `call_id` from `start.customParameters` and store `start.streamSid`.
    1. For inbound audio, decode 8 kHz G.711 μ-law to PCM16. Then resample it to 16 kHz for Live.
    1. For outbound audio, resample Live's 24 kHz output to 8 kHz and encode it to μ-law. Then send `{"event": "media", "streamSid": ..., "media": {"payload": <base64>}}`.
    1. On `interrupted`, send `{"event": "clear", "streamSid": ...}`. Each outbound `media`, `mark` and `clear` message contains `streamSid`.
    1. Do not use stdlib `audioop` (Python 3.13 removed it). Use a numpy μ-law codec or `audioop-lts`.
    1. Check the message shapes against twilio.com/docs/voice/media-streams/websocket-messages.
1. **Greeting gate** (`TwilioPhone`): Twilio streams the inbound audio from the answer. The borrower's "Hello?" during the greeting also interrupts the greeting. Thus the bridge sends no inbound audio to Live until the greeting has played on the phone.
    1. The gate opens when two conditions are true. `LiveCall` has sent the `greeting_done` message, and the playback clock has reached the end of the bot audio. `LiveCall` sends `greeting_done` at the end of its first turn, only to a phone with `wants_greeting_done` (`TwilioPhone`). `/mobile` does not get it.
    1. The playback clock adds the length of each audio chunk to the end of the bot audio (24 kHz PCM16 is 48,000 bytes for each second). Live sends audio faster than real time and Twilio holds it, so the send time does not show the playback time. A `clear` on barge-in moves the end to the current time at the latest.
    1. The gate opens at the latest 15 s (`GREETING_CAP_S`) after the first bot audio, or after the stream start if no bot audio went out. A lost `greeting_done` makes the bot deaf for 15 s at most.
    1. Borrower audio that arrives after the bot audio ends and before the gate opens waits in a buffer of 150 frames (3 s). The bridge sends it in order when the gate opens. More bot audio before the gate opens clears the buffer, because that audio was a pause in the greeting.
    1. When the gate opens, the bridge writes the log event `twilio_greeting_gate_open` with `stream_sid`, `closed_ms` and `capped`.
1. **Status callback**: `POST /api/twilio/status` maps `no-answer`, `busy`, `failed` and `canceled` to `NO_OUTCOME`.
1. **Signatures**: On both webhooks, validate `X-Twilio-Signature` against `PUBLIC_BASE_URL` + path + query, with `https`. Do not rebuild the URL from the proxied request.
1. **Credentials**: Read them from Secret Manager (§7.0). Never log the auth token.
1. **Human prerequisites (Adhish)**:
    1. (a) A Twilio account. It can be a paid account, or a trial with its limits (calls only to verified caller IDs, plus a trial message).
    1. (b) A Twilio number that can call India, with India enabled in Voice Geo Permissions.
    1. (c) Presenter phones added as verified caller IDs and listed in `mcd5-twilio-allowed-numbers` and `mcd5-demo-phone-map`.
    1. (d) A compliance check for outbound calls in India (TRAI/DLT rules for commercial calls, and caller ID display for international numbers).

#### Cloud Run Deployment (Backend + Static Frontend)

- **Service**: Use one service in `asia-south1`. It serves FastAPI and the built Next.js static export (`/mobile`, `/monitor`). Deploy with `--allow-unauthenticated` (preflight checks the org policy).
- **Dev settings**: Use `--min-instances=0 --max-instances=1` (the store is in memory), `--session-affinity` and `--timeout=3600`.
- **Demo settings**: `make demo-up` runs `gcloud run services update <svc> --min-instances=1 --no-cpu-throttling`. `make demo-down` sets `--min-instances=0 --cpu-throttling` again. Use demo-up only for Oct 6–7. Then run demo-down.
- **Access control**: Each `/api/agent/*` and `/api/admin/*` call needs the bearer token. `/ws/mobile` and `/ws/live-call/{call_id}` need the paired device credential. `/ws/live-call` also needs `call_id == active_call_id` (call IDs are uuid4). `/api/twilio/*` needs a valid Twilio signature.
- **Admin endpoints**:
    - `POST /api/admin/reset`: Reload the pool (§7.2). Then apply the seeded outcomes if `SEED_OUTCOMES=true`.
    - `POST /api/admin/pair-link`: Mint a one-time pairing link.
    - `POST /api/admin/language {loan_id, language}`: Set or clear `language_override`.

### 7.5 Repository Structure & Build Automation Standards

```
muthoot-collections-demo/
  docs/specs/
    demo5_briefing_spec.md        # Mirror of this document
  agent/                          # GE agent: google-adk + google-cloud-aiplatform[agent_engines,adk]
    pyproject.toml
    collections_agent/
      agent.py                    # ADK Agent(model="gemini-3.8-flash") + 6 GE tools (§7.3 A)
      backend_client.py           # HTTPS client for /api/agent/* with bearer token
    deploy.py                     # Deploys to Agent Runtime in asia-south1, prints resource name
    register_ge.md                # Written by the agent: resource name + console steps for Adhish (§7.6)
    Makefile
  backend/                        # Python 3.13, uv, ruff, pytest
    pyproject.toml
    src/muthoot_collections/
      main.py                     # FastAPI: /api/agent/*, /api/admin/*, /ws/mobile, /ws/live-call, /ws/twilio, /api/twilio/voice, /health
      models.py                   # Pydantic: Account, AccountStatus, CallRecord, HandoffBrief, KpiReport
      store.py                    # Pool load, query, status machine, invariants, KPIs (§7.2, §7.3 C–F)
      data/customers.jsonl        # 2,000 accounts, generated by scripts/convert_workbook.py (§7.2)
      live_session.py             # Live session manager (LIVE_MODEL), tool dispatch, transcripts
      telephony_mobile.py         # Phase 1 pairing, incoming-call push, /ws/mobile reconnect
      twilio_bridge.py            # Phase 2 calls.create, TwiML, mu-law <-> PCM bridge
      post_call.py                # gemini-3.8-flash per-call summary (JSON schema)
    scripts/
      preflight.py                # make preflight: org policy, model probes, writes .env.genai (§7.0)
      convert_workbook.py         # docs/data xlsx -> data/customers.jsonl, phones masked (§7.2)
    tests/
      test_store_invariants.py    # D-1..D-12, transitions, seed_outcome, call window edges
      test_query.py               # query_accounts filters, group_by, queue totals, call_attempt rule
      test_kpis.py                # Seeds-only and scripted-path values from §7.3 F
      test_api_routes.py          # Auth, reset, seed-outcomes, pairing, trigger errors
      test_twilio_bridge.py       # mu-law/PCM round trip, allow-list rejection
  frontend/                       # Next.js (static export) + React 19 + TypeScript + bun + biome + ShadCN (OKLCH)
    package.json
    src/
      pages/MobileCallReceiver.tsx  # /mobile: pairing, ringing, AudioWorklet, link cards
      pages/Monitor.tsx             # /monitor: unbranded live transcript + status (optional)
  Makefile                        # start, dev, test, format, lint, clean, check, preflight, deploy-backend, deploy-agent, demo-up, demo-down, reset
```
### 7.6 Gemini Enterprise Integration (ADK on Agent Runtime)

1. **Build the agent**: In `agent/collections_agent/agent.py`:

`root_agent = Agent(name="muthoot_collections_agent", model="gemini-3.8-flash", instruction=INSTRUCTION, tools=[get_calls_due_today, query_accounts, trigger_outbound_collection_call, get_call_status, get_handoff_briefs, analyze_collection_calls])`. `INSTRUCTION`:

    1. You help the Cymbal Bank collections team.
    1. For *"Who are the calls to be done today?"*, call `get_calls_due_today`. Give the queue totals and splits. Then give a table of the top rows with status, call attempt and callable.
    1. For any other question about accounts, branches, languages, segments, risk or amounts, call `query_accounts`. Report only what it returns.
    1. Trigger a call only when the user names a customer or loan ID, and only if the account is callable. Resolve a name with `query_accounts`. If more than one account matches, ask which one, and show the branch and loan ID.
    1. For *"Analyze the calls"*, call `analyze_collection_calls`. Present only the numbers that it returns.
    1. Never invent amounts, statuses, or outcomes.
1. **Backend access**: The tools read `BACKEND_URL` from env. They read the token once from Secret Manager `projects/$P/secrets/mcd5-demo-agent-token/versions/latest`.
1. **Service account**: Create `mcd5-agent-runtime@$P.iam.gserviceaccount.com`. Grant it `roles/aiplatform.user` on the project, and `roles/secretmanager.secretAccessor` on `mcd5-demo-agent-token`.
1. **Deploy** (`make deploy-agent` → `agent/deploy.py`):

```
vertexai.init(project=P, location="asia-south1", staging_bucket=f"gs://{P}-mcd5-agent-staging")  # bucket in asia-south1
# The engine runs in asia-south1, where Flash is not served. agent.py pins the model to `global`
# in code (R2): GOOGLE_CLOUD_LOCATION is reserved in Agent Runtime and cannot be set via env_vars.
app = agent_engines.AdkApp(agent=root_agent)
remote = agent_engines.create(
    agent_engine=app,
    display_name="Cymbal Bank Collections Agent",
    requirements=["google-cloud-aiplatform[agent_engines,adk]", "google-adk", "httpx", "google-cloud-secret-manager"],
    extra_packages=["collections_agent"],
    env_vars={"BACKEND_URL": PUBLIC_BASE_URL, "DEMO_TOKEN_SECRET": f"projects/{P}/secrets/mcd5-demo-agent-token/versions/latest"},
    service_account=f"mcd5-agent-runtime@{P}.iam.gserviceaccount.com",
)
print(remote.resource_name)
```
    - **Verified (web search)**: `AdkApp(agent=...)` and `agent_engines.create(...)` with `requirements`, `extra_packages`, `env_vars` and `service_account`.
    - **Not verified**: We do not know if this SDK version requires `staging_bucket`. Create the bucket anyway. We also do not know if a `global` GE app accepts an `asia-south1` Agent Runtime agent. The day-1 probe (§7.8) gives the answer. If GE refuses the agent, redeploy to `us-central1` (Flash stays pinned to `global`).
    - **Local test before GE**: `AdkApp.stream_query` (or `remote.stream_query`) with the start prompt must return the queue totals and top rows.
1. **Register in GE (human step, Adhish)**: The agent writes `agent/register_ge.md`. The file contains the resource name, the display name "Cymbal Bank Collections Agent", a one-line description and the console steps. The steps are: Gemini Enterprise console > workshop app > Agents > Add agent > **Custom agent via Agent Runtime** > paste the resource name > save. Adhish registers the agent with GE Admin rights. At runtime, the agent runs as `muthoot-agent-runtime`.
1. **Fallback**: If the Agent Runtime registration fails, tell Adhish first. Then expose the agent over A2A. Register it as an A2A agent.
1. **In-GE journey**: The start prompt, the call triggers, the status questions, *"Show me the escalations"*, and *"Analyze the calls"* all run in the real GE chat. Live audio runs on the phone (`/mobile`, or Twilio in Phase 2).

### 7.7 Definition of Done

The build is done only when all of these items are true. Record the evidence for each item. **\[Human\]** marks the items that need Adhish or a native speaker.

1. **\[M1\]** `make preflight` passes and logs the location for each model (§7.0).
1. **\[M1\]** `make check` passes (format, lint, all tests, including `test_kpis.py` for both §7.3 F expected-value sets).
1. **\[M1\]** The backend runs on Cloud Run (§7.4 dev settings). `/health` returns 200.
1. **\[M1\]** The ADK agent runs on Agent Runtime. `stream_query` with *"Who are the calls to be done today?"* returns the queue totals and top rows with status, call attempt and callable. *"Analyze the calls"* returns the seeds-only values.
1. **\[M1\] \[Human\]** Adhish registers the agent in GE. The same two prompts work in the real GE app.
1. **\[M2\]** An integration test drives a Live session with `send_realtime_input(text=...)` borrower turns for Scenarios A, B and C. The scenarios end in the §3 Stage 4 statuses. The same test also drives Scenarios D to I, the wrong-person case and the silent borrower (§3 Stage 2), and each ends in its listed state. `test_call_plan.py` checks the call plan amounts for the Asks of the scripted accounts (§5).
1. **\[M2\] \[Human\]** The persona triggers Scenarios A, B and C from GE. The scenarios run by voice over `/mobile` and end in the §3 Stage 4 statuses. GE shows each status and the GL6095694164 handoff brief. *"Analyze the calls"* returns the scripted-path values.
1. **\[M3\]** `POST /api/admin/reset` restores the seed state, and the team can repeat the full run. With `make demo-up`, the store keeps its state through 20 minutes of idle time.
1. **\[M3\] \[Human\]** This doc contains the Malayalam Go / No-Go result (§6, Verification 6).
1. **\[M3\]** The TTS fallback files exist and play.
1. **\[Phase 2\] \[Human\]** After Adhish closes the Twilio prerequisites, Verification 10 passes.

### 7.8 Build Order & Milestones

The build agent follows this order. Speed is the priority. Ship each milestone before you start the next one.

1. **M1: GE path first (deployable on day 1)**
    1. Run `make preflight`. For M1, only `gemini-3.8-flash` must pass. Record the Live and TTS results.
    1. Do the day-1 probe. Deploy a one-tool hello agent to Agent Runtime in `asia-south1`, with Flash pinned to `global`. Run `stream_query`. Ask Adhish to register the agent in GE. Build the 6 tools only after this probe works.
    1. Build the backend: the store with the §7.2 pool and `query_accounts`, `seed_outcome`, `/api/agent/*`, `/api/admin/reset`, the KPIs and the tests.
    1. Deploy it to Cloud Run.
    1. Build the ADK agent with the 6 GE tools. Deploy it to Agent Runtime. Write `agent/register_ge.md`.
    1. **Done when**: Adhish can register the agent in GE, *"Who are the calls to be done today?"* returns the queue, and *"Analyze the calls"* returns the seeds-only KPIs.
1. **M2: Live voice on `/mobile`**
    1. First, make one `/mobile` voice exchange with barge-in on the deployed service (phone → Cloud Run `asia-south1` → Live `us-central1`). Measure the response latency. Agree a usable limit with Adhish before you build the remaining items. If the latency is too high, try Cloud Run in `us-central1`.
    1. Build the one-time pairing links and the incoming-call push.
    1. Build `live_session.py` with the Live tools and the response protocol.
    1. Build Scenarios A, B and C.
    1. Build the transcripts, the post-call summary, the handoff briefs and `/monitor`.
    1. Build the call recordings (AGENTS.md) after Scenarios A–C pass.
1. **M3: Rehearsal hardening**
    1. Build `make demo-up` / `make demo-down`, the reset and `language_override`.
    1. Hold the Malayalam Go / No-Go session with a native speaker **\[Human\]**.
    1. Make the TTS fallback files.
1. **Phase 2 (after v1): Twilio**
    1. Build the Twilio adapter (§7.4). Start only when the human prerequisites are complete **\[Human\]**.
- **Human-gated items**:
    - GE registration and GE app access for Supriya (M1).
    - The org-policy override, only if preflight asks for it (M1).
    - The Malayalam judgment (M3).
    - The Twilio prerequisites (Phase 2).

## 8. Known Risks & Open Items (Final Review)

These items come from the final review on 2026-10-03. Items marked **Resolved** are fixed in Sections 1–7. Sections 1–7 do not fix the other items. The build agent MUST read this table before M1. It MUST handle each item during the build and record the outcome in `docs/specs/build_log.md`.

| ID | Severity | Milestone | Risk | Recommended handling |
| :--- | :--- | :--- | :--- | :--- |
| R1 | High | M1 | `make preflight` stops the whole build if `gemini-3.8-live` fails on Vertex, but M1 does not use Live. On Vertex, `gemini-3.8-live` can return `1008 Publisher model was not found`. | Add `make preflight MILESTONE=m1`. For M1, only `gemini-3.8-flash` is a gate. Live and TTS results are report-only until M2. |
| R2 | High | M1 | Vertex can serve Gemini 3.x on `global` only, but Section 7.0 sets `us-central1` for models. `GOOGLE_CLOUD_LOCATION` is reserved in Agent Runtime, and `env_vars` cannot set it. | Probe `gemini-3.8-flash` on `global` and on `us-central1`. Record `GENAI_LOCATION_FLASH`. Set the model location in agent code. After the deploy, verify with a remote query. **Resolved 2026-10-03**: preflight found Flash on `global` only. Agent code pins `global`. There is no AI Studio fallback. |
| R3 | High | M1 | Section 7.0 allows role grants and public Cloud Run access, but MUST NOT says "Change IAM". The two rules conflict. | Allowed IAM changes: create the project service accounts, grant them roles inside this project, and grant Secret Manager accessor on the demo secrets. Deploy Cloud Run with `--no-invoker-iam-check` (public access with no `allUsers` binding, so Domain Restricted Sharing does not block it). Org policy changes stay forbidden. |
| R4 | High | M1 | The doc names no Cloud Run runtime service account, and the repo tree has no Dockerfile. `--source backend/` does not build `frontend/`. | Add service account `mcd5-backend@<project>.iam.gserviceaccount.com` and deploy with `--service-account`. Add a root multi-stage Dockerfile (bun build, then Python runtime). For M1, a backend-only deploy is acceptable. |
| R5 | Medium | M1 | The deploy code in Section 7.6 uses the module-level `agent_engines.create`. The current ADK quickstart uses `vertexai.Client(project, location).agent_engines.create(agent=app, config={...})` with `google-cloud-aiplatform[agent_engines,adk]>=1.112`. | Use the client form, with `requirements`, `extra_packages`, `env_vars`, `service_account` and `staging_bucket` in `config`. Keep the module form only as a fallback. |
| R6 | Medium | M1 | Unpinned packages can cause pickle version skew between local and remote. `extra_packages` is a relative path, and the tree has no `agent/__init__.py`. | Pin requirements with `==` to the local versions. Run the deploy from `agent/` in the Makefile target. Add `__init__.py`. |
| R7 | Medium | M1 | `seed_outcome` starts at `DUE_FOR_CALL` with no session. The partial-link rule needs `CALL_IN_PROGRESS` and `active_call_id`. | `seed_outcome` sets `CALL_IN_PROGRESS` with `active_call_id = seed-<loan_id>`, calls the same store functions with the session check off, then ends the call. |
| R8 | Medium | M1 | `connect_rate` has no format, and "today" has no time zone. | Use `connect_rate_pct` with 1 decimal place. "Today" is the Asia/Kolkata date. |
| R9 | Medium | M2 | `NO_OUTCOME` accounts have `callable = false`, and no tool or endpoint retries them. | Let the trigger tool accept `NO_OUTCOME`, or add `POST /api/admin/retry`. `call_attempt` does not change on retry. **Resolved 2026-10-03**: the trigger accepts `NO_OUTCOME`; `callable` includes it. |
| R10 | Medium | M2 | A pairing through `/monitor` puts the demo token in a public page. Sections 2, 7.0 and 7.5 disagree on what `/monitor` shows. The transcript stream has no auth rule. | Confirm the pairing only through the CLI or Makefile. `/monitor` shows status, transcript and model surface. Protect the transcript stream with a short-lived view token. **Resolved 2026-10-03**: one-time pairing links (§7.4); both sockets need the device credential. |
| R11 | Medium | M2 | On `gemini-3.8-live`, if you leave out `behavior`, the result is NON_BLOCKING. We have not verified that each SDK version and surface accepts `behavior="BLOCKING"`. | Add a preflight probe that opens a Live session with one BLOCKING tool on the chosen surface. STOP if it fails. Never drop the field silently. |
| R12 | Low | M1 | We have not verified the `google-genai` pin and the client constructor (`vertexai=True` vs `enterprise=True`) against the installed SDK. | Pin a version that resolves. Use the constructor that the installed SDK accepts. |
| R13 | Low | M2 | The Section 5 WARNING gives a fixed count of supported language codes. The count can be out of date. | Keep the `ml-IN` flag. Ignore the count. The Malayalam smoke test decides. |
| R14 | Low | M1 | The AI Studio note in Section 7.6 can put a raw API key in `env_vars`. | Resolved: the AI Studio fallback is removed. |
| R15 | Medium | M2 | The Live model sometimes skips the middle Ask, or steps down to the next Ask after the first refusal with no hold. The outcomes stay correct, because the store enforces the floor. | Accept. Scenario B prints whether the middle Ask came before the floor (3 of 3 runs on the final prompt). Watch this in each rehearsal. |
| R16 | Medium | Phase 2 | The greeting gate drops the borrower's speech during the greeting (15 s at most, §7.4). A borrower who answers the identity question early must say it again. | Accept. The gate stops the "Hello?" barge-in that ended calls in the first Twilio tests. Tell the presenter to wait until the bot finishes the greeting. |
| R17 | Closed | Phase 2 | The greeting gate is verified on five real Twilio calls (2026-10-05). Each call logged `twilio_greeting_gate_open` with `capped: false`. A "Hello?" during the greeting did not reach Live. | Keep the 15 s cap. Check `capped` in the logs after each rehearsal. |
| R18 | Low | M2 | An offer above the due amount gets a yes in speech, because the offer rule says to accept the floor or more. Then the store rejects the link (D-2, D-3) and the bot states the limit from the error. | Accept. The store is the control, and the bot corrects itself. |
| R19 | Low | M2 | The audit event name of each dispute escalation is `escalate_to_branch_manager(PENAL_FEE_DISPUTE)`, also for `PAYMENT_OR_AMOUNT_DISPUTE`. The audit details carry the real `reason_category`. | Accept for the demo. Rename the event if an audit report must group by the category. |
| R20 | Low | M2 | The Live scenario pass rates (36 of 36) come from 3 runs of each scenario. The sample is small, and the model varies between runs. | Run the Live tests with `LIVE_RUNS=3` or more after each change to the instruction, and before the workshop. |
| R21 | Low | Later | **Not built.** This is an ambitious later step (Adhish decided it): move the Ask order from the prompt into code, with context during the call. Two forms exist. (a) A fourth Live tool that the bot calls after each refusal, and that returns the next Ask. (b) Next-step guidance in the results of the existing tools, or a turn that the backend adds with `send_client_content`. Example for (b): after a partial link, the result tells the bot to ask if a callback date suits the borrower, and to book only after a yes. | Deferred. Code does not know when the borrower refuses. Only the model knows. Form (a) changes the §7.3 B tool contract and needs a new round of Live runs. The current prompt passed 36 of 36 Live runs. |
| R22 | Low | M2 | `gemini-3.8-live-extended-thinking` is not an option. It is not available for this project in India (Adhish, 2026-10-05). The build uses `gemini-3.8-live` with `BLOCKING` tools. | Do not select this model with `LIVE_MODEL`. The spec text about its tool behavior (§7.1, §7.3 B) stays as reference only. |
| R23 | Medium | M2 | The store gates read borrower turns from the Live input transcription. A barge-in can split one borrower reply into two turns, for example "yes ... actually tomorrow". Then each part gets its own consent check. | Accept for the demo. Tell the presenter to let the bot finish a sentence. A fix must separate late transcription of the earlier reply from the new speech. |
| R24 | Medium | M2 | The consent check (Gemini 3.5 Flash-Lite) can reject a real yes. Then the bot asks the borrower to confirm the amount again. The probe found 83 correct of 84 checks (one timeout) on 28 replies in English, Hindi and Malayalam. | Accept. A false reject costs one more turn. A false accept sends a link with no yes, which is worse. Rehearse the scripted yes lines. |
| R25 | Low | M2 | The decision model is an external API in the call path. From Cloud Run, three phone calls measured a median of 356 ms and a max of 667 ms. | The gates do not wait for it longer than 0.6 s. Without its result, links still need the consent check, and callbacks go through as "unverified". Check the latency strip on /monitor at each rehearsal. |
| R26 | Low | M2 | The rehearsal judge found that Live sometimes books the 2-day callback in scenario E with no question about an earlier date (call flow rule 7). The end state is correct. | Accept. The judge reports it. Do not change the prompt for it before the demo. |
| R27 | Medium | M2 | The link gate waits for the consent check until 3.0 s after the link call arrives (`WAIT_FRAGMENT_S` 1.0 s + `WAIT_CONSENT_S` 2.0 s). The consent check starts when the borrower stops speaking, before the call arrives. On Cloud Run the consent check took a median of 1,339 ms and a max of 1,555 ms. The two link calls waited 1,431 ms and 1,519 ms. A consent check that misses the limit makes the gate reject a real yes. | Mitigated on 2026-10-05. A new revision cancels a request in flight. The final transcription starts the checks with no debounce. `WAIT_CONSENT_S` went from 1.5 s to 2.0 s. The higher limit adds no delay when the check succeeds. It adds 0.5 s of silence only when the check fails. |
| R28 | Medium | M2 | The bot confirms identity by name only (McKinsey feedback, 2026-10-05). It asks for no loan digits. Anyone who answers and says yes to the name hears the amount and the loan details. | Accept for the demo. The data is synthetic and the calls reach only allow-listed phones. Before production, agree a verification step with the Cymbal Bank compliance team (RBI fair practices, DPDP Act). |

**Not verified (test in the Argolis project):**

- The roles that the Agent Runtime service account needs.
- Whether a `global` GE app accepts an `asia-south1` Agent Runtime agent (day-1 probe).
- The exact Gemini Enterprise console label.
- Malayalam and Kannada quality, and the Kannada to Hindi switch (human test).

Preflight verified the model locations on 2026-10-03.
