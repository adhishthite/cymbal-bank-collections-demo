# Review Feedback — Voice Bot, 2026-10-04

Adhish gave this feedback after the first Twilio calls (18:44–18:51 IST). The feedback is not built yet. The live deploy is commit `d5a2353`.

## 1. Feedback on the bot behavior

1. Use more signals from the XLSX data. The bot uses only some of the columns now.
1. Use `suggested_bot_play` as a signal that controls the call plan. Do not only list it in the context.
1. Add more complexity and persuasion to the negotiation.
1. Do not go directly to the 2-month minimum. Start with a higher amount. Then negotiate down step by step. The 2-month minimum is the floor, not the first ask.
1. Be more convincing. For example, say that unpaid interest hurts the borrower's CIBIL score, and ask the borrower to pay this amount to protect it. Say consequences as facts. Do not threaten.
1. Speak English with an Indian English accent wherever the bot speaks English.
1. Start in the customer's default language from the data. If the borrower changes the language, follow the change.
1. Keep the intent signals from the client brief (section 2).

## 2. The client brief

This is the full client brief for Demo 5:

> **Demo 5: Vernacular Voice Bot for Portfolio Collections & Recovery Management**
>
> Deploy automated outbound voice assistants for interest payment reminders, structured payment restructuring, and customer query management.
>
> Workflow Overview:
> - System identifies high-risk Gold Loan accounts with 4–5 months of overdue interest
> - Agent will pull the customer-specific context: amount due, due date, product, previous interactions etc.
> - Agent makes an outbound call to the flagged customers and understands the intent:
>   - Will pay now
>   - Needs more time
>   - Cannot pay full amount
>   - Disputes amount / transaction
>   - Wants to speak to someone
> - Voice Bot executes rule-based negotiations to encourage partial recovery (e.g., securing 2 months of pending interest)
> - Human handoff with context (if needed): transcript + customer intent + reason for escalation + recommended action
> - Callback / reminder scheduled automatically, in case the customer says he will pay later, can't pay, raises dispute etc.

Spec §3 Stage 3 maps the five intents to INT-01 to INT-05.

## 3. Findings from the code and the data

These findings are for the person who builds the change.

1. **Unused columns.** The Live system instruction does not use these columns from `customers.jsonl`: `pos_inr`, `interest_rate_pct`, `due_since`, `bullet_months_to_collect`, `monthly_months_unpaid`, `monthly_months_to_npa`, `missed_payments_6m`, `enach_registered`, `bureau_score`, `off_us_active_loans`, `off_us_delinquency`, `whatsapp_opt_in`, `suggested_channel`, `channel_rationale`, `fallback_channel`, `last_interest_payment_date`, `days_since_last_payment`, `loan_start_date`, `tenure_months`, `city` and `state`.
1. **Bot plays.** Each `suggested_bot_play` value belongs to one treatment segment:

    | Suggested bot play | Segment | Accounts |
    | --- | --- | --- |
    | Reminder + payment link | E - Other early delinquent | 725 |
    | Gentle reminder + eNACH nudge | A - Regular monthly payer | 524 |
    | Standard reminder (no human follow-up) | B - Small-ticket | 276 |
    | Reminder + part-payment negotiation, call center follow-up | D - Bouncing EII | 244 |
    | Negotiate minimum amount due | C - Accrued bullet | 231 |

1. **Amount steps.** On monthly-interest accounts, `bot_ask_amount_inr` is equal to `due_amount_inr`. The store (D-1) then accepts no partial amount. Only bullet accounts can step down in amount. On monthly-interest accounts, the bot can negotiate the date, not the amount.
1. **Middle step.** The bot must not calculate amounts. Thus the code must calculate each middle step and put it in the context. Example for Sajeev George: ₹8,185 (full due), then ₹4,911 (3 months), then ₹3,274 (the 2-month floor).
1. **Credit score rule.** The current rules (`live_session.py` RULES, spec §5) allow credit score talk only if `days_past_due > 0`. Kavita Saxena and Sajeev George have `days_past_due = 0`. Thus the CIBIL feedback needs a change to this rule.
1. **Conflicts to change.** `TONES[2]` says "Make the 2-month ask". Spec §3 INT-03 says that the bot "asks for the 2-month minimum". Change both with the new negotiation.
1. **Tests to change.** `test_live_integration.py` Scenario B and `test_e2e.py` assert the current flow. Change them with the new negotiation.
1. **Callback after a dispute.** The brief says that a dispute also gets a callback or reminder. Now `escalate_to_branch_manager` only makes the handoff. It does not book a callback. The bot books a callback only for "pay later" and for the remaining amount after a partial payment.
1. **Callback after "cannot pay".** The brief says that "can't pay" gets a callback. RULES item 5 books a callback after two refusals. Make sure that each "cannot pay" end of call has a callback.
1. **Queue filter.** The brief says "high-risk accounts with 4–5 months of overdue interest". Monthly-interest accounts in the data have only 1–2 months unpaid. Bullet accounts have 1–6 months. Check that the queue and the demo accounts agree with the brief, or tell the client the difference.
1. **Handoff content.** The brief asks for the transcript, the intent, the reason and the recommended action. The handoff brief has the intent, `escalation_reason` and `recommended_action`. Confirm that GE shows the transcript with the handoff brief.

## 4. Twilio call quality (barge-in and VAD)

Adhish reported barge-in and VAD problems on Twilio calls. The `/mobile` calls did not have these problems. The call transcripts show two causes:

1. **The borrower's "Hello" stops the greeting.** On a phone call, the borrower says "Hello" after the answer. The bot then stops its greeting and starts again. In one call, this occurred three times, and the borrower hung up after 35 s.
1. **The bot hears its own voice.** In one call, the customer transcript has the bot's own words ("record your name and"). The phone was probably on speaker. A phone call through Twilio has no echo cancellation. The browser on `/mobile` has echo cancellation.

Work-arounds for a demo, with no code change:

1. Hold the phone to the ear, or use wired earphones. Do not use the speaker.
1. After you answer, do not speak until the bot completes its first sentence.

Possible code changes (not built):

1. Ignore the borrower audio during the bot's first sentence.
1. Set a lower start-of-speech sensitivity for Twilio calls only.

## 5. GE response time

The GE UI agent took 66 s to answer "Can you give me the transcript?" The backend answered the two tool calls in about 5 ms. The Flash model step after the tool took about 55 s. The cause is not known. Add timing logs for the model calls in the UI agent to find the cause.
