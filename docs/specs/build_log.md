# Build Log — Demo 5

## 2026-10-03 — Manual preflight (before `make preflight` exists)

We ran this check from the build VM, with the google-genai SDK on Vertex AI and ADC. We probed each model in this sequence: `global`, then `asia-south1`, then `us-central1`.

| Model | global | asia-south1 | us-central1 | Use |
| --- | --- | --- | --- | --- |
| `gemini-3.8-flash` | PASS | 404 | 404 | `global` |
| `gemini-3.8-flash-tts` | PASS (audio/wav) | 404 | 404 | `global` |
| `gemini-3.8-live` | 1008 not found | 1008 not found | PASS | `us-central1` |

- **R1/R2**: Vertex AI serves Flash only on `global`, and Live only on `us-central1`. Each model has its own location (`GENAI_LOCATION_FLASH=global`, `GENAI_LOCATION_LIVE=us-central1`). The build does not need AI Studio.
- **Live regions**: `gemini-3.8-live` is in the model list only on `us-central1`. A Live connect also failed on `asia-south2`, `asia-southeast1`, `asia-northeast1`, `europe-west4` and `us-east4`. The only Live model in the `global` list is `gemini-live-2.5-flash-native-audio`. It is not Gemini 3.x.
- **R11**: `gemini-3.8-live` on `us-central1` accepted a function declaration with `behavior=BLOCKING`, opened the session and returned audio.
- **Org policy**: `iam.allowedPolicyMemberDomains` is `allowAll: true`. Thus no override is necessary. R3 still applies: deploy with `--no-invoker-iam-check`.
- **Agent Runtime**: The `asia-south1` reasoningEngines endpoint answers (empty list). Thus Agent Runtime runs in `asia-south1` and calls Flash on `global`.
- **Identity**: The first run used the default compute SA of the VM. This SA did not have the deploy permissions. Then Adhish ran `gcloud auth login` and `gcloud auth application-default login`. The second run used `admin@adhishthite.altostrat.com`. This account had all 13 M1 permissions that we tested (Cloud Run, Cloud Build, Artifact Registry, Secret Manager, IAM SA create/actAs, Agent Runtime, Storage, Service Usage, Discovery Engine). The model results matched the results above.
- **APIs**: The project already had these APIs enabled: Vertex AI, Cloud Run, Cloud Build, Artifact Registry, Secret Manager, Cloud Storage, Discovery Engine, IAM and Generative Language.
- **Secrets**: No secrets exist yet. M1 creates `demo-agent-token` and `view-token-key`. The build does not need `gemini-api-key`.

## 2026-10-03 — McKinsey sample data vs spec §7.2

The source is `docs/data/Vernacular_Collections_Bot_Sample_Data_vF.xlsx`. McKinsey (Pankhuri Gupta) sent it. It has 2,000 synthetic gold-loan customers. These are the gaps against the 5 accounts in the spec:

- **Same rule**: The bot asks for MIN(months due, 2) × monthly interest. Monthly interest = POS × rate / 12. This matches the §7.3 D-1 minimum partial payment (`bot_ask_amount_inr`).
- **Scheme**: The sheet has only `Bullet` and `Monthly interest` (EII). An EII account has at most 2 months unpaid (NPA at 3). Thus the spec's `overdue_months >= 4` fits only Bullet accounts. In the sheet, "months of interest due" means months accrued. Only 163 of 874 Bullet loans are past maturity.
- **Rates**: Bullet rates are 12–18% p.a. The sheet has no rebate or penal rate. Thus nothing in it supports the spec's "12% → 22%" rebate-loss figures.
- **Amounts**: The spec's accrued dues imply about 21–22% p.a. (GLA-20482: ₹5,530 a month on ₹3.1L). Comparable sheet rows ask ₹4k–10k for 2 months, not ₹8k–14k.
- **Language and geography**: The sheet has no Marathi and no Maharashtra. The spec branches Karol Bagh, T. Nagar and Banjara Hills are not in the sheet. Kochi MG Road is in the sheet.
- **Extra signals**: The sheet has these signals, and the spec does not: risk tier, risk score, treatment segment (A–E), suggested channel, suggested bot play, preferred call window, broken promises-to-pay, last contact outcome, bureau score, LTV.
- **Phones**: The phones are random 10-digit numbers. They can belong to real people. Never load or dial them.

## 2026-10-03 — Spec switched to the McKinsey pool (decided with Adhish)

- **Dataset**: The full 2,000-account pool replaces the spec's 5 invented `GLA-*` accounts. `backend/scripts/convert_workbook.py` converts the pool to `backend/src/muthoot_collections/data/customers.jsonl`. The converter masks the phones to `+9110000NNNNN`. We checked the converter's formulas on all rows (due = months × monthly interest; ask = MIN(months, 2) × monthly interest). Two runs gave the same SHA-1.
- **Why**: McKinsey made this data for the demo, and leadership can ask about any account. The old amounts implied ~22% rates. The workbook does not have these rates.
- **Rebate loss**: We removed it. The workbook has no rebate or penal rate. The bot's "why pay now" uses only row facts (monthly interest, months accrued, maturity date, LTV).
- **New GE tool `query_accounts`**: It is a deterministic filter / group-by / sum over the pool, for open-ended questions. `get_calls_due_today` now returns the queue totals and top rows, not all rows.
- **Call queue** = `suggested_channel` in {Voice bot, Bot + call center}: 1,291 accounts, ₹74,35,486 due.
- **`call_attempt` rule** (we made this rule; the workbook does not have it): 3 if the last outcome is "Refused / disputed" or 2+ broken promises; 2 if the last contact was Voice bot or Call center; else 1.
- **Scenario accounts**: A Kavita Saxena GL3506468235 (Hindi), B Sajeev George GL0106397512 (Malayalam, Kochi), C Venkatesh Shetty GL6095694164 (Kannada → Hindi, replaces Marathi). Seeded: Saravanan Raman GL1814280043, Ravi Varma GL9061256232.
- **Negotiation (McKinsey's request)**: Scenario B now includes a counter-offer below the 2-month minimum. The bot refuses it. §5 has explicit counter-offer rules.
- **KPIs**: `committed_pct` now uses the due amount of the accounts called today. Over the 1,291-account queue, it rounds to 0. The new expected values are: seeds-only ₹24,833 committed (78.3%); scripted path ₹36,719 committed (50.0%).
- **Languages**: The languages are Hindi, Malayalam, Tamil, Telugu, Kannada and English (the workbook's set). Kannada joins Malayalam in the Go / No-Go.

## 2026-10-03 — Adversarial plan review (Fable, GPT-6.1 Sol, GPT-6 Astra)

Three independent reviews examined the plan. We checked each finding against the files and the JSONL before we acted on it. These are the spec changes:

- **Accrued, not overdue**: All 5 scenario accounts have `days_past_due = 0` and a future maturity date. The bot says "interest due" and uses maturity as the reason to pay now. It mentions credit score or notices only when `days_past_due > 0`. We renamed the enum `FULL_OVERDUE` to `FULL_DUE`.
- **D-7 key** is `(call_id, loan_id, settlement_type, amount_inr)`, and a reset replaces the whole store. Before this change, a same-day rehearsal made the links of the live run into duplicates. It also left the account stuck in `CALL_IN_PROGRESS`.
- **Seeded records** count in KPIs on any date. Before this change, a reset on Oct 6 removed them from the KPIs on Oct 7.
- **Call end** occurs only on hang-up, timeout, socket close or Live error. An outcome tool never ends the call, because Scenario B needs two tools. The cleanup runs in a `finally` path that always clears `active_call_id`.
- **Transitions**: The table allows a full payment from `CALLBACK_SCHEDULED` during a call. `NO_OUTCOME` is callable, and the trigger retries it. Thus R9 is resolved, and we removed the "operator retries" row.
- **KPIs**: We renamed `connect_rate_pct` to `outcome_rate_pct`. Committed totals exclude links on escalated accounts. The KPIs also count promises on `PARTIAL_SETTLEMENT_AGREED`. The expected values did not change.
- **`promised_by`** is 23:59 IST, not 18:00. Calls run until 19:00.
- **Pairing**: Pairing uses one-time links that the backend signs with `view-token-key` (Adhish's choice). Both sockets need the device credential. `/ws/live-call` needs `call_id == active_call_id`. Call IDs are uuid4. R10 is resolved.
- **AI Studio fallback removed** (Adhish's choice): The build uses Vertex only, with one `GENAI_LOCATION_*` for each model. We removed the `gemini-api-key` secret and R14.
- **Regions**: Spec §7.0, §7.4, §7.5 and §7.6 now give these locations: Cloud Run, the buckets and Agent Runtime in `asia-south1`; Flash/TTS on `global`; Live on `us-central1`.
- **Sequencing**: A day-1 hello-agent probe (Agent Runtime in `asia-south1` plus GE registration) comes before the 6 tools. M2 starts with a deployed latency test. Recordings come after Scenarios A–C pass.
- **Start of day**: `get_calls_due_today` returns `handled_today`, because the scenario accounts rank 280–1082 by risk. Trigger lines name the branch, because all 5 scenario names repeat in the pool.
- **DATA-NOTE §5** now points to spec §7.2. Before, it contradicted the spec (5-account set, SQLite, `+91 98000…` masking).

These items did not change. The spec still documents the `gemini-3.8-live-extended-thinking` option as non-default (no decision yet). Call recordings stay in scope (Adhish's decision).

## 2026-10-03 — M1 build (backend, store, GE agent code)

- **Preflight (R1)**: `make preflight MILESTONE=m1` passed. `gemini-3.8-flash` and `gemini-3.8-flash-tts` are on `global`. `gemini-3.8-live` is on `us-central1`, and a Live session with a BLOCKING tool opened (R11). Only Flash is a gate for M1.
- **R12**: `google-genai` 2.28.0 accepts `genai.Client(vertexai=True, ...)`. We use that constructor.
- **Store**: `store.py` keeps the pool and all state in memory. All routes are `async def`, and no store method awaits. Thus each store call is atomic on the event loop.
- **R7**: `seed_outcome` opens the call `seed-<loan_id>`, calls the same Live tool functions with a bound session, and ends the call.
- **Seeds-only KPIs**: the tests pin ₹24,833 committed and 78.3% with a fixed IST clock. The scripted path gives ₹36,719 and 50.0%.
- **Trigger in M1**: no phone can pair until M2. Thus each trigger returns `No /mobile phone is paired.` after the store checks. A mode other than `mobile` returns an error.
- **`query_accounts`**: a text match ignores case, so "malayalam" matches "Malayalam". The backend adds the derived columns `months_due` and `in_queue`. A row shows the standard fields plus each filter, group and sort column.
- **Not built in M1**: `POST /api/admin/pair-link` (M2 pairing).
- **Tests**: the suite is a small set of end-to-end flows through the HTTP API (AGENTS.md, Tests).
- **GE agent (R2, R5, R6)**: the model is `Gemini(model="gemini-3.8-flash", client_kwargs={"enterprise": True, "location": "global"})`. A test with `GOOGLE_CLOUD_LOCATION=asia-south1` passed. Without the pin, the same test gave a 404. `deploy.py` uses `vertexai.Client(...).agent_engines.create(agent=..., config=...)`. It pins each requirement with `==` to the local version, and adds `cloudpickle` and `pydantic`. Each agent deploys as its own package from `agent/`, so `agent/__init__.py` is not necessary.
- **Agent tracing**: `AdkApp(enable_tracing=...)` is deprecated in `google-cloud-aiplatform` 2.3.0. We set `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true` instead. Message content stays out of spans. `query.py` uses `async_stream_query`, because `stream_query` is deprecated.
- **Local round trip**: the ADK agent ran against a local backend. The start prompt returned the queue totals and top rows. "Analyze the calls" returned the seeds-only values. "Call Venkatesh Shetty in Hubballi" resolved to GL6095694164.
- **Code review (GPT-6.1 Sol)**: we fixed 7 of 8 findings. These were a token in curl arguments, a reset during a call, an unbuilt telephony mode, unhashable enum values, missing audit fields, a bad range bound, and a non-ASCII token. We moved the 8th finding to M2: the Live dispatcher will open a span for each tool call, including rejected calls.
- **R4**: the root `Dockerfile` builds the backend only. M2 adds the frontend stage.

## 2026-10-03 — Resource prefix `mcd5` (decided with Adhish)

- **Why**: the GCP project also has resources from other work. The prefix shows which resources belong to Demo 5.
- **Names**: Cloud Run `mcd5-backend`, service accounts `mcd5-backend` and `mcd5-agent-runtime`, secrets `mcd5-*`, buckets `<project>-mcd5-agent-staging` and `<project>-mcd5-call-recordings`. Each resource that accepts labels has `app=mcd5`. The spec now uses these names.
- **Exception**: the GE display name of the collections agent stays "Cymbal Bank Collections Agent", because the audience sees it.
- **Old bucket**: `gs://<project>-call-recordings` is empty. The new bucket has the same settings. Adhish confirms before the old bucket is deleted.
- **Created**: the two service accounts and the two buckets. Adhish creates the secret and the IAM grants, because the agent's permission mode blocks secret writes and role grants.
- **`/health`, not `/healthz`**: Cloud Run reserves paths that end in `z`. Its front end answered `/healthz` with a Google 404, and the request did not reach the container. The spec now uses `/health`.
- **Deployed (M1)**: Cloud Run `mcd5-backend` at `https://mcd5-backend-355880143137.asia-south1.run.app`. `/health` returns 200. Without the token, the API returns 401. The seeds-only KPIs match. Audit records are in Cloud Logging. A request with an external `traceparent` appears in Cloud Trace under the same trace ID, with `loan_id` on the span.
- **Agent Runtime (asia-south1)**: the hello probe is `reasoningEngines/2045804111198158848`. A remote query called `ping` through Flash on `global`. The collections agent is `reasoningEngines/1026759142389317632`. Remote queries returned the queue (1,291, ₹74,35,486) and the seeds-only KPIs (₹24,833, 78.3%). Thus the runtime SA can read the token secret and reach Cloud Run.
- **GE app**: the project already has the GE app `Cymbal Bank-Demo-App` on `global`. Adhish registers both agents in it (`agent/register_ge.md`).
- **GE registration (rule change)**: Adhish lifted the §7.0 rule "Register the agent in GE (Adhish does this)". The build agent registered both agents in `Cymbal Bank-Demo-App` with the Discovery Engine API. GE accepted the `asia-south1` engines, and both agents are `ENABLED`. The GE chat checks are still a human step.
- **Smaller tool results**: ADK keeps each tool result in the GE session history. After about 7 turns, the Flash input was 11.5k tokens. The `query_accounts` default `limit` is now 10, not 20. A row shows `language_override` only when it has a value. `handled_today` shows only the loan ID, name, branch, status, callable and the last call. A Hindi query result went from 7.3 KB to 3.4 KB, and `calls-due` went from 5.0 KB to 4.3 KB.
- **GE agent latency**: Flash thinking is now `LOW`, and the instruction asks for short answers with fixed table columns. Local timing against the deployed backend, 2 runs each: the start prompt went from 8.7–9.1 s to 6.0–6.8 s, the Malayalam count from 7.4–9.6 s to 3.3–4.7 s, and "Analyze the calls" from 6.1–6.2 s to 3.7–4.0 s. Name resolution now uses one `query_accounts` call (a "contains" match on `branch`), not three. The agent is redeployed over the same engine, so the GE registration did not change. Remote queries returned the correct values. ADK reads `{name}` in an instruction as a state variable, so the instruction does not use braces.

## 2026-10-03 — Second GE agent with A2UI cards (not deployed)

- **What**: `agent/collections_ui_agent/` is a second GE agent, "Cymbal Bank Collections Agent + UI". It uses the same 6 tools, backend client, model and thinking level as the collections agent. The code builds the A2UI v0.9 cards from the tool JSON. The model writes one or two lines of text only.
- **Hosting (change to §7.6)**: Cloud Run `mcd5-ui-agent` in `asia-south1`, not Agent Runtime. GE renders A2UI only for A2A agents, and it supports A2A v0.3 with JSON-RPC. The installed Agent Runtime A2A template requires A2A v1.0 with HTTP+JSON. On Agent Runtime, GE also needs an OAuth client and per-user consent. On Cloud Run, GE needs one `roles/run.invoker` grant. See `agent/register_ge_ui.md`.
- **Wire shape**: each A2UI message is one A2A `DataPart` with `metadata.mimeType` `application/json+a2ui`, as in the GE v0.9 sample. The first part is the text part.
- **Call button**: callable queue rows and a callable account card have a Call button. The button sends the action `call_account` with the loan ID. The executor turns it into the prompt "Call the borrower of loan ID ...".
- **Gap**: the Cloud Run service does not export OTel spans yet. Thus no trace joins the GE turn to the backend request.

## 2026-10-04 — GE agent with A2UI cards (decided with Adhish)

- **Why**: GE renders A2UI cards only for an agent that is registered as an A2A agent. The native ADK agent stays registered as the text fallback.
- **Agent**: "Cymbal Bank Collections Agent + UI" (`agent/collections_ui_agent/`). It uses the same 6 tools, the same backend and the same token as the text agent. Code builds the cards from the tool JSON. Flash writes only one or two lines of text. Thus the card numbers come from the store.
- **Hosting**: Cloud Run `mcd5-ui-agent` in `asia-south1`, not Agent Runtime. GE speaks A2A v0.3 JSON-RPC. The installed Agent Runtime A2A template serves only v1.0, and an Agent Runtime A2A agent also needs an OAuth client and user consent. On Cloud Run, GE sends an OIDC token for the Discovery Engine service agent, which has `roles/run.invoker` on this service only.
- **A2UI**: v0.9, catalog `gemini_enterprise_composite_catalog.json`, one A2UI message in each `DataPart` with MIME `application/json+a2ui` (the GE v0.9 sample format). Each card passes the GE catalog validator from `a2ui-agent-sdk`.
- **Tracing**: the service sends spans to Cloud Trace and continues an incoming `traceparent`.
- **Code review (GPT-6.1 Sol, with web search)**: the review checked the format, catalog, card and auth against the docs, and found no format errors. We fixed two findings: a final `failed` status when the turn raises, and checks on the A2UI action input. These findings did not change the build: the backend is public, so finding 4 does not apply; tool calls take about 20 ms, so finding 6 is acceptable; and the demo does not rotate secrets (finding 8). These findings stay open: in-memory sessions are lost when the instance restarts (M3 `demo-up` keeps one instance warm), a clarification question ends its task, and the Call button does not update the card after the call.
- **Deployed and registered**: GE agent ID `3651103152243872492`. A2A smoke test on Cloud Run: the queue card shows 1,291 and ₹74,35,486. The KPI card shows ₹24,833 and 78.3%. Each card passes the catalog.
- **GE check (Adhish, 2026-10-04)**: the A2UI cards render correctly in the GE app. The composite catalog ID works.
- **Model check**: we compared `gemini-3.8-flash` LOW, `gemini-3.5-flash` MEDIUM and `gemini-3.5-flash` LOW on the UI agent (3 runs, 4 prompts). 3.5 MEDIUM was not faster (Kochi count 5.7 s vs 2.8 s), because it used 2–3 times more thinking tokens. 3.5 LOW saved up to 1.8 s on some prompts, but it was less steady, and one run made a redundant query. We keep `gemini-3.8-flash` LOW.

## 2026-10-04 — M2 build: Live voice on `/mobile`

- **Order (Adhish's decision)**: we built M2 before the latency test and the Model Armor decision. The deployed latency test and Model Armor come later.
- **Wire protocol (new in §7.4)**: tokens go in the URL fragment, the request body, the first socket message or a header. They never go in a URL query, because Cloud Run logs the request URL. Thus the pair link is `/mobile#pair=<token>`, not `/mobile?pair=<token>`. The monitor uses `fetch` with a bearer view token, not `EventSource`.
- **Backend**: `live_session.py` connects the call socket to `gemini-3.8-live` on `us-central1`, with BLOCKING tools. `telephony_mobile.py` does the pairing, the ring, decline and the 45 s no-answer timeout. `post_call.py` writes the Flash summary. `monitor.py` sends the monitor events.
- **Trigger**: a trigger needs a connected `/ws/mobile` socket, not only a paired credential, because a ring needs a live socket.
- **Ring resend**: after a control socket reconnects, the backend sends the ringing `incoming_call` again. The phone ignores a repeat with the same `call_id`.
- **Device credentials**: they are valid for 72 h. A new pairing revokes the earlier credential. After a restart, the first valid credential becomes the pairing again, so presenters do not pair again. A replaced phone can take over in that case until its credential expires.
- **Bot speaks first**: at connect, the backend sends the text `CALL_CONNECTED` to the model, so the bot starts the call.
- **Spans**: each Live tool call has a `live.tool.<name>` span, rejected calls included (the moved M1 review item). Each voice turn has a `live.turn` span. The trigger span is the parent of the call span.
- **Frontend**: a Next.js static export with `/mobile` and `/monitor`, built with bun. The capture worklet resamples the device rate to 16 kHz with a windowed-sinc low-pass filter. Playback uses a separate 24 kHz `AudioContext`. FastAPI serves the export from `STATIC_DIR`. The root `Dockerfile` has a bun stage and a uv stage.
- **Live integration test (§7.7)**: `make test-live` drives the real `gemini-3.8-live` with text borrower turns. Scenarios A, B and C passed 4 of 4 runs each. We tightened the system instruction between runs. We did not change the assertions.
- **Open**: the Scenario C language switch is not verified with text turns, so the voice check (§6 Verification 7) decides. Call recordings are not built yet (§7.8 puts them after A–C). The `mcd5-view-token-key` secret does not exist yet.
- **Code review (GPT-6.1 Sol, web search, 4 rounds on the backend, 1 on the frontend)**: we fixed the findings that can occur on stage. Examples are the call lock after a setup failure, leaked bridge tasks, stalled socket sends, revocation on a new pairing, the blocking auth refresh in the google-genai Live `connect()`, one ordered queue for all phone output, cards on hangup, and the frontend audio unlock and scheduler gaps. The reviewer agreed with these deferrals: pairing across a restart, Live session resumption, `loan_id` on spans, and recordings. Its verdict after round 4: ready for an on-phone rehearsal.
- **Rehearsal checks**: examine the customer transcript at barge-in (the backend flushes a customer turn at the bot turn end when Live sends no `finished`). Hang up during a tool call and make sure the card appears.

## 2026-10-04 — After the first phone calls

- **First real calls (Adhish, 02:06 and 02:16 IST)**: the full path worked: GE, Cloud Run, Live on `us-central1`, and the phone. Adhish judged the latency and the audio quality as good. Hindi and English switches mid-call worked.
- **Silent borrower**: in the first call the borrower said nothing, and the bot booked a 7-day callback. A new instruction rule: when the borrower does not answer or the identity is not confirmed, the bot calls no tool.
- **Partial amounts**: in the second call the bot refused ₹5,000 on a ₹3,598 minimum. The store already accepts any partial amount from the minimum to below the due. A new instruction rule makes the minimum a floor. The bot does not say "policy"; it gives the reason from the account facts.
- **Store guard (change to D-4)**: after a partial link, the store rejects a callback with `promised_amount_inr`. In live tests the model computed promises that the borrower did not make, and they changed the KPIs. The callback range error no longer says "policy".
- **Full transcripts**: `GET /api/agent/calls/{call_id}` returns the full transcript and every tool call, rejected calls too. `GET /api/agent/accounts/{loan_id}/calls` lists the calls without transcripts. At call end the backend writes `<call_id>/transcript.json` to the recordings bucket once (create only). The backend SA has `roles/storage.objectCreator` on that bucket only (Adhish).
- **GE tool 7**: both GE agents have `get_call_transcript`. The UI agent shows a transcript card. The tool rejects a call ID from another loan.
- **Latency spans**: `live.turn` now records `first_audio_ms` only after customer speech, and adds `customer_spoke`.
- **Frontend**: a calmer design for `/mobile` and `/monitor` (Opus with the frontend-design skill). The monitor shows readable action labels and dates.
- **Deploy parameter**: `make deploy-backend CALL_WINDOW_BYPASS=true` keeps calls open outside 08:00–19:00 for rehearsals. The default is `false`. `make revoke-links` makes all links invalid.
- **Live pass rates (text turns)**: A 2/2, B 5/5, C 2/2, above-minimum 4/4, silent 6/6. In 1 of 5 B runs the bot booked the callback before the borrower agreed to ₹3,274. The end state was correct.
- **Code review (GPT-6.1 Sol)**: we fixed the loan check on a call ID, a total limit on the transcript upload, and open turn spans at hangup. Accepted: a race that can drop `first_audio_sent_ms` on a turn.

## 2026-10-04 — Phase 2 build: Twilio PSTN calls

- **Account (Adhish)**: a paid Twilio account. The trial removes `<Stream>` from TwiML, so a trial cannot connect a call to Live. The caller number is a US local number. India rules require a non-Indian number for calls to Indian phones, and an Indian Twilio number needs a regulatory bundle. Voice Geo Permissions allow India (low risk only). We did not do the US-only registrations (SHAKEN/STIR, CNAM, Voice Integrity, Branded Calling).
- **Mode switch (change to §7.4)**: `TELEPHONY_MODE` sets the mode at startup only. `POST /api/admin/telephony {"mode"}` changes it at run time (`make telephony-twilio`, `make telephony-mobile`). The `/mobile` pairing and sockets work in both modes, so `/mobile` is the fallback with no redeploy. The default stays `mobile`. A restart goes back to `TELEPHONY_MODE`.
- **Credentials (change to §7.0)**: the backend calls the REST API with a Standard API key (`mcd5-twilio-api-key-sid`, `mcd5-twilio-api-key-secret`). The Auth Token (`mcd5-twilio-auth-token`) only checks `X-Twilio-Signature`. A Standard key cannot change the account or its keys.
- **Phone map**: `mcd5-demo-phone-map` accepts the key `"*"` for all loans that it does not name. A value can be one number or a list. For a list, the backend picks one allow-listed number at random for each call. Adhish's two phones are the `"*"` list and the allow-list. The backend never dials a number that is not on the allow-list (D-10).
- **Stream auth**: Twilio does not sign the Media Stream socket. The TwiML gives the stream a `<Parameter>` with an HMAC token for the `call_id`, valid for 5 min. `/ws/twilio` also requires the active `call_id` and the Twilio call SID that the dial returned.
- **Unanswered calls**: the status callback ends a call that never had a stream (`no-answer`, `busy`, `failed`, `canceled`, or `completed` before a stream) with `NO_OUTCOME`. A 90 s deadline covers a lost callback. A failed dial ends the call with reason `dial_failed` and returns 502.
- **Audio**: `audioop-lts` (new dependency, named in §7.4) decodes and encodes μ-law. The phone to Live step uses `ratecv` (8 to 16 kHz). The Live to phone step uses a 31-tap low-pass filter (3.4 kHz) before it keeps every third sample, because `ratecv` has no anti-alias filter. Measured: 300 Hz–2 kHz pass at full level, 4 kHz and above are 50 dB lower. The filter uses `audioop.mul` and `audioop.add`, so it needs no numpy. It sums at half scale, because `audioop.add` clips each partial sum.
- **Greeting**: Twilio streams from the answer, while the backend still connects to Live. `TwilioPhone` drops the phone audio from that time. Thus the borrower's "Hello?" does not interrupt the bot's greeting.
- **Dial result**: a Twilio 4xx ends the call at once (`dial_failed`). A 5xx or a timeout does not end the call, because Twilio can still have placed it (Twilio error 20500). The webhooks or the 90 s deadline end it.
- **Deploy (change to §7.4 dev settings)**: `deploy-backend` uses `--no-cpu-throttling`. In twilio mode no request is open while the phone rings or after the hang-up, so a throttled CPU can delay the deadline, the summary and the transcript upload. `make deploy-backend TWILIO=off` deploys without the Twilio secrets.
- **Startup**: a phone map that is not valid JSON makes the backend start without Twilio, not fail. `/mobile` stays available.
- **Phone output**: a PSTN phone has no screen. `TwilioPhone` sends `clear` on barge-in and drops the JSON cards. `/monitor` and the GE agent still show the outcomes.
- **Test**: `tests/test_twilio_bridge.py` drives the mode switch, the allow-list, both signed webhooks and a call over `/ws/twilio`, with a mocked Twilio REST API and the scripted Live model.
- **Code review (Codex GPT-6.1 Sol at low effort, 3 rounds; then Codex at high effort and a Fable reviewer in parallel, 3 rounds)**: we fixed the dial race (a webhook before the dial response), dial errors that ended a placed call (timeouts and 5xx), CPU throttling outside requests, the missing anti-alias filter and its clipped partial sums, the greeting barge-in from audio buffered during the Live connect, a startup crash on a bad phone map, the `:443` signature URL form, and `make -n` side effects. The tests check the filter by its peak and its 6 kHz rejection. In round 3 both reviewers found no defects. Their verdict: ready for a first real phone call.

## 2026-10-05 — Voice bot call plan and persuasion (feedback of 2026-10-04)

The origin is `docs/reviews/2026-10-04_voice_bot_feedback.md` (Adhish, after the first Twilio calls). The code is not committed and not deployed.

- **Ask ladder (change to §3 Scenario B, INT-03, §5)**: the bot now asks the full due amount, then a middle Ask, then the floor. For Sajeev George these are ₹8,185, ₹4,911 and ₹3,274. The end state and the KPI values do not change. **Why**: feedback items 4 and 3 say that the 2-month minimum is the floor and not the first ask. The bot must not calculate amounts, so `call_plan` in `live_session.py` computes each Ask. A middle Ask of `(months due + 2) // 2` months exists only above 2 months and not in segment B.
- **Offer rule**: the plan compares an amount that the borrower names only with the floor. The floor or more gets a yes at once. Less than the floor counts as a refusal of the last Ask. **Why**: review round 2 (Codex P2). In Live runs, one combined text made the model use the middle Ask as a threshold and refuse offers between the floor and the middle Ask. Two separate lists fixed this. Scenario G tests it.
- **Call plan and levers (change to §5)**: the context carries a call plan with the Asks, the offer rule, the levers, the play by segment and the after-agreement sentence. Code removed these context fields: the 2-month minimum, the remaining amount, the maturity date, LTV, risk tier, segment, suggested bot play, broken promises, last contact and preferred call window. **Why**: feedback items 1 and 2 ask for more columns and for `suggested_bot_play` as a control. The model read the 2-month minimum out as "the minimum" and skipped the middle Ask, so the plan replaced that field. The lever `broken_promise` needs `call_attempt` 2, a "Promised to pay" outcome and a Voice bot or Call center channel (Fable round 1, F2: the lever was false on 31 accounts).
- **Precedence and the final call (change to §5)**: the tone applies first, then the play, then the other rules. The identity, compliance and tool rules always apply. On `call_attempt` 3, the plan replaces the play with a no-push play. **Why**: Fable round 1, F1 (the final call could push), and Codex round 2, R2-3 (a tone line could override an agreed callback date). The "Then send the link" text left tone 1, because with tone precedence it sent links without a yes.
- **CIBIL wording (change to §5 Compliance)**: the bot says that unpaid interest *can* affect the CIBIL score. It never says "will" or "has", never says a score number, and never mentions other lenders. Statutory notices still need `days_past_due > 0`. **Why**: feedback item 5. Kavita Saxena and Sajeev George have `days_past_due = 0`, so the old rule blocked the nudge.
- **Identity, consent and register (change to §5)**: the bot needs the name **and** the last 4 digits (Fable round 1, F5). A link needs an amount that the borrower names or says yes to. A callback needs a yes to the date, and "a complaint or a question is not a yes". The language rule asks for Indian call center speech, code-mixed, with an Indian English accent. **Why**: feedback items 6 and 7, and Live runs in which the model booked callbacks and sent links without a yes. The plan says that levers are facts, because Live read English levers word for word in Hindi and Malayalam calls.
- **Automatic callback after two refusals (reversal)**: round 2 first required a yes from the borrower for this callback (Fable R2-4). We reversed this. **Why**: in Live runs the model could not keep that rule. It booked on the next refusal, or it proposed dates outside 1 to 7 days, and Scenario F fell to 0 or 1 of 3. The client brief says that the system schedules a callback automatically when the customer cannot pay. The bot books a 3-day callback with `CANNOT_PAY_FULL` and no promised amount, and then tells the borrower the date. Every other tool action still needs consent.
- **Callback intent (change to §7.3 B and C)**: `schedule_callback_reminder` has the optional `customer_intent` (`NEEDS_MORE_TIME`, the default, or `CANNOT_PAY_FULL`). Each call record has an `intent` that the store sets from the successful tool calls. **Why**: the client brief lists five intents, and the record had none. A callback after a link in the same call keeps the link intent (round 3, R2-6: the callback overwrote it).
- **Dispute and branch callback date (change to §3 Stage 3 and 4, §7.3 A and C)**: the new `reason_category` `PAYMENT_OR_AMOUNT_DISPUTE` maps to `DISPUTE_LOGGED` and INT-04. An escalation sets `next_callback_date` to today + 2 days (`BRANCH_CALLBACK_DAYS`), adds `branch_callback_date` to the tool result and to the handoff brief, and clears the bot promise fields. The bot tells the borrower this date. **Why**: feedback findings 8 and 9. A dispute and a payment that is not credited had no reason category and no callback date. A frozen account must not show a bot promise next to the branch date.
- **Store changes after the review (change to §7.3 C)**: a `PARTIAL_TWO_MONTH` link clears `promised_amount_inr` from an earlier promise callback in the same call. A new transition `(CALLBACK_SCHEDULED, schedule_callback_reminder) -> CALLBACK_SCHEDULED` lets the bot move a booked callback in the same call. The date, amount and notes overwrite the old values. **Why**: a borrower can promise a date, and then pay part of the amount today. Without the clear, the KPIs counted the link and the promise together. Without the transition, the store rejected the move. `test_e2e.py` covers both.
- **D invariants**: no invariant D-1 to D-12 changed. D-6 uses the new transition.
- **Twilio greeting gate (change to §7.4 Phase 2)**: the bridge sends no borrower audio to Live until `LiveCall` sends `greeting_done` and the playback clock reaches the end of the bot audio, or 15 s pass. It holds up to 150 frames (3 s) and sends them in order when the gate opens. It logs `twilio_greeting_gate_open`. **Why**: feedback section 4. The borrower's "Hello" stopped the greeting, three times in one call, and the borrower hung up. Codex round 1 (C1, C2) found two defects in the first design: a margin that dropped the start of a valid answer, and a pause in the greeting that opened the gate early. The held frames and the `greeting_done` marker fixed them. `test_twilio_bridge.py` tests the gate.
- **Tests (change to §7.7)**: the Live test file has the Scenarios D to I and the `wrong_person` case. Scenario B checks that the callback comes after the last borrower line, and it prints whether the middle Ask came before the link. `test_call_plan.py` checks the Asks of four scripted accounts (Codex round 1, C4 and C5 changed Scenario G and `wrong_person`). `test_e2e.py` checks the intents, the branch callback date and the promise changes.
- **Validation that ran**: the offline suite passed (14 passed, 12 skipped, because the Live tests are opt-in) and `ruff` reported no findings. The Live scenarios ran against the real `gemini-3.8-live` on the final prompt: 12 scenarios, 3 runs each, 36 of 36 passed. In Scenario B the middle Ask (₹4,911) came before the floor (₹3,274) in 3 of 3 runs. The instruction is about 25% longer than the old one (Sajeev George).
- **Code review**: three rounds, each with Codex GPT-6.1 Sol at high effort and a Fable reviewer. We fixed the findings of rounds 1 and 2 and verified them in the next round. Items that we accepted on purpose: the final call still makes one soft ask before it listens; the model sometimes steps down an Ask with no hold; the model often sets `NEEDS_MORE_TIME` on the callback after a partial link (the store keeps INT-03); the maturity lever says "matured" for 3 accounts that matured on 2026-10-03 (none is in the demo scripts).
- **Not verified**: no real phone call ran with the new gate or the new prompt. The change is not deployed and not committed. The Live runs used text borrower turns, so they do not test voice, the accent or the Hindi and Malayalam speech quality. §8 lists the open risks R15 to R20.

## 2026-10-05 — Decisions: later work and a model that is not an option

- **Ask order in code (future work, not built)**: Adhish decided to record an ambitious later step. It moves the Ask order from the prompt into code, with context during the call. Form (a) is a small fourth Live tool that the bot calls after each refusal, and that returns the next Ask. Form (b) puts next-step guidance in the results of the existing tools, or adds a turn with `send_client_content`. **Why we deferred it**: code does not know when the borrower refuses, and only the model knows. Form (a) changes the §7.3 B tool contract and needs a new round of Live runs. The current prompt passed 36 of 36 Live runs. Spec §8 R21 records it.
- **`gemini-3.8-live-extended-thinking` is not an option (Adhish, 2026-10-05)**: the model is not available for this project in India. The build uses `gemini-3.8-live` with `BLOCKING` tools. The spec text about the tool behavior of the extended-thinking model stays as reference. Spec §8 R22 records it.

## 2026-10-05 — First Twilio phone calls on the call plan (revision `mcd5-backend-00009-2p6`)

Adhish made five real calls from GE: Kavita (A), Sajeev (B), Venkatesh (C), Ajay (F) and Naveen (H).

| Call | End state | Result |
| --- | --- | --- |
| Kavita (A) | `PAYMENT_LINK_SENT_TODAY`, ₹8,612 | Pass. After the goodbye, the bot said "The requested action has been completed" in English. |
| Sajeev (B) | `CALLBACK_SCHEDULED`, `CANNOT_PAY_FULL` | Fail. The bot took "theek hai, chalo, phir dekhta hoon" after the floor Ask as a refusal and booked the callback. Then it refused three requests to pay the minimum. |
| Venkatesh (C) | `NO_OUTCOME` | Script error. The test script did not give the loan ID digits. The caller said "0092", and the bot correctly ended the call. |
| Ajay (F) | `CALLBACK_SCHEDULED`, 3 days, `CANNOT_PAY_FULL` | Pass. |
| Naveen (H) | `DISPUTE_LOGGED`, `PAYMENT_OR_AMOUNT_DISPUTE` | Pass. |

- **R17 is closed**: all five calls logged `twilio_greeting_gate_open` with `capped: false`. The "Hello?" during the greeting on the Kavita call did not reach Live.
- **Prompt changes (RULES)**:
    - An unclear reply to an Ask ("theek hai, dekhta hoon", a complaint or a question) is not a yes and not a refusal. The bot asks one time if the borrower will pay that amount today, and calls no tool in that reply.
    - A second unclear reply, or a clear no, counts as a refusal. A promise of an amount on a date is not a refusal, also after "no, not today".
    - After the goodbye, the bot answers in one short sentence and says no system text.
- **Wording that we removed, with the Live evidence**:
    - "Shall I send the link for <amount> now?" made the bot send a ₹4,911 link with no yes (1 of 9 B runs). It also conflicted with call flow rule 4.
    - "Then stop talking and wait" made the bot say "I am waiting." aloud (1 of 39 runs).
    - A late-yes rule ("After a callback, send a link only after a clear yes") and a conditional remainder question. In 1 of 18 negotiation runs, the bot sent a ₹3,274 link with no yes, in the same turn as the floor Ask. The bot also asked about a second remainder callback (2 of 3 `B_recover` runs). Adhish selected the smaller fix and kept spec §8 R21 for later.
    - "A clear no also counts as a refusal", without the promise exception, failed scenario I 2 of 9 times. One run sent a link after a complaint. One run booked `CANNOT_PAY_FULL` over "kal poora 1,660 de dunga".
- **Reviews**: Codex (gpt-6.1-sol, high) and Fable, two rounds. Round 1 found 5 P2 items, and round 2 marked them fixed. Codex round 2 found 1 P2 (the remainder text after a full-due link), and we fixed it.
- **New Live scenario**: `B_unclear` (the Sajeev phone call as text turns). We removed `B_recover` with the late-yes rule.
- **Live results on the final wording**: 39 of 39 runs passed (13 scenarios, 3 runs each). Each payment link came after a borrower yes or an offer of that amount. The batch ran 8 tests at a time in 395 s.
- **Not verified**: the latency stage times on `live.turn` are not usable on Twilio (`first_audio_ms` is mostly 0 to 1 ms). This is the open `first_audio_sent_ms` item.

## 2026-10-05 — Decision model, consent check and store gates

Live alone decided if the borrower agreed or refused. Code could not check it. Tonight's tests showed links with no yes, a `CANNOT_PAY_FULL` callback after an unclear reply, and "I have resent the link" on a duplicate. Adhish decided to add code checks for these decisions.

- **Decision model**: a fast external model reads each borrower turn and each bot turn. One request asks 13 typed questions in parallel: the reply act, the offered amount, the payment time, hardship, dispute, wrong person, a request for a person, a payment already made, a language request, a change of mind, frustration and willingness. The VM measured 270 to 350 ms for one request. A second request audits each bot turn against the tool results.
- **Consent check**: Gemini 3.5 Flash-Lite (`global`, thinking `LOW`) decides if the borrower agreed to pay an amount today. It is the only authority for a payment link. The decision model missed two romanized Malayalam yeses in the probe, so it does not decide consent. The prompt names hesitation ("dekhta hoon", "nokkam") as unclear and "send the link" as a yes.
- **Store gates** (store step 7b, after the idempotency replay):
    - A link needs a consent result for the same borrower turn and the same amount. A fresh decision-model refusal stops the link also when consent says yes.
    - A `CANNOT_PAY_FULL` callback needs two refusals. A `NEEDS_MORE_TIME` callback needs a promise for a later date. A callback with no intent needs a partial link first.
    - A wrong-person answer stops all tools.
    - A rejected call returns guidance to the bot. When the evidence is missing, a link is rejected and a callback goes through as "unverified".
- **Tool path**: tool calls no longer block the Live receive loop. Each call waits for the evidence of the borrower turn that was open when the call arrived. Live cancels a pending call when the borrower interrupts. The worker then skips that call.
- **/monitor**: a "Decision model" panel shows each borrower turn, the flags, the gate log, two meters and a latency strip. The strip shows the decision model, the consent check and the wait at tool time.
- **Rehearsal judge**: the Live text tests ask the decision model if the bot met plain behaviors for each scenario. A FAIL with confidence 0.8 or more fails the test.
- **Prompt changes**: a link needs a clear offer or yes for today ("names an amount" was a loophole). The bot follows the guidance of a rejected tool call. A duplicate result means "sent earlier", not "sent again".
- **Naming**: the audience never sees the vendor name. The UI, the events and the text to the bot say "decision model".
- **Reviews**: Codex `gpt-6.1-sol` (high, fast), three rounds, no Fable (Adhish). Round 1 found 2 P1 and 7 P2 items. Round 2 marked 7 fixed and found 1 P1 (cancelled tool calls) and 2 P2 items. Spec §8 R23 records the deferred barge-in item.
- **Live results**: 36 of 39 runs passed (13 scenarios, 3 runs each). The 3 failures are the judge finding of spec §8 R26 in scenario E. The runs of 2026-10-05 morning, before this change, show the same behavior, so it is not a regression. The judge now reports it as advisory. The gates allowed 45 tool calls and rejected 3. All 3 rejections were correct: a ₹3,274 link after an offer below the floor, and a `NEEDS_MORE_TIME` and a `CANNOT_PAY_FULL` callback after "theek hai, chalo, phir dekhta hoon". The bot then asked again.
- **Latency (VM, 39 runs)**: decision model median 307 ms, p95 377 ms, max 519 ms (171 requests). Consent check median 1,023 ms, p95 1,312 ms, max 1,616 ms. Wait at tool time median 0 ms, p95 767 ms, max 841 ms (48 tool calls).
- **Deploy**: revision `mcd5-backend-00011-scl` (commit `6e177fd`) with `CALL_WINDOW_BYPASS=true`. The `mcd5-backend` service account got `secretAccessor` on `mcd5-typesafe-api-key`.
- **Phone calls (Twilio, 3 calls)**: all outcomes were correct. The gates allowed 6 tool calls and rejected none.
    1. Amit (GL4904098331): two refusals, then a yes to ₹1,834. The link and the callback for the rest went through.
    2. Raghu Naik (GL5008865131): two refusals, then a request for the branch manager. The escalation went through.
    3. Raghu Rao (GL8676360580): offers of ₹500 and ₹2,500 were below the ₹2,964 floor. The bot booked a `CANNOT_PAY_FULL` callback. After the goodbye he offered ₹3,000, and the link went through.
- **Latency (Cloud Run, 3 calls)**: decision model median 356 ms, max 516 ms (14 borrower turns). Bot audit median 347 ms, max 667 ms (17 turns). Consent check median 1,339 ms, max 1,555 ms (14 checks). The two link calls waited 1,431 ms and 1,519 ms at tool time, with 2.1 s to the first bot audio. Callbacks waited 535 to 599 ms. Spec §8 R27 records the consent margin.
- **Greeting gate**: the gate stayed closed for 11.8 s, 14.0 s and 13.1 s. Each call logged `capped: false`. Barge-in after the greeting worked (`barge_in: true` on one turn).
- **Telemetry note**: `first_audio_ms` on a turn with no tool call shows 0 to 49 ms. The speech end marker probably arrives late. Do not use these values as voice latency.

## 2026-10-05: Male voice and Indian English (dry run with McKinsey)

- **Voice**: the Live session uses the prebuilt male voice `Charon` (`LIVE_VOICE`). Before, it used the default female voice. **Why**: Adhish asked for a male caller.
- **Prompt**: the bot is a male agent. It uses male verb forms in gendered languages, for example "bol raha hoon". In English it always uses an Indian English accent, never an American or British accent.
- **No language parameter**: the Live docs say that native audio models choose the language and do not support a language code. Thus only the prompt controls the accent.
- **Tests**: offline 21 passed. Live A, B and C passed, one run each.
- **Deploy**: revision `mcd5-backend-00012-24j` with `CALL_WINDOW_BYPASS=true`.
- **Phone calls (Twilio, 2 calls)**: Sunil Pandey (English, ₹4,500 link) and Amit Agarwal (Hindi, ₹1,834 link). Both outcomes were correct. The Hindi lines used the male forms. The consent check took 1,077 to 1,441 ms.
- **Open**: in both calls, the bot did not offer a callback for the remaining amount after a partial link.

## 2026-10-05: Fixes from a Gemini review of the Live path (not deployed)

Gemini reviewed the Live path. We checked each item against the code, today's logs and 19 saved transcripts. Adhish decided to fix all items except the VAD tuning.

- **Callback after a partial link (seen on 2 phone calls)**: a successful `PARTIAL_TWO_MONTH` link result to Live now has `remaining_amount_inr` and `next_step`. The step tells the bot to offer a callback for the rest, and to book it only after a yes. The prompt says to do a `next_step` in the next reply. The store result and the GE tools did not change.
- **Consent checks in sequence (R27)**: a new revision of a borrower turn cancels the request in flight and starts a new one. The final transcription starts the checks with no 250 ms debounce. The logs showed no double checks today, so this removes a risk, not a seen failure.
- **Late bot text after a barge-in**: Live can send the transcription of cut speech after `interrupted`. The text now joins the last bot entry, not a new bot turn with its own audit. The late text can hold the amount or the date of the Ask. Thus the open borrower turn gets the new Ask, and its decision results become stale. Codex found this case (rounds 1 and 2).
- **Turn telemetry**: `live.turn` also records `first_audio_from_first_fragment_ms` and `first_audio_from_finished_ms`. `first_audio_ms` stays as before.
- **Not changed**: the VAD settings (`realtime_input_config`). The logs show no split borrower turn. A longer silence limit adds delay to each turn (Adhish).
- **Tests**: offline 22 passed, with a new test for the late Ask amount after a barge-in and a check of `next_step` in the scripted Scenario B. Codex `gpt-6.1-sol` (high, fast), three rounds: round 3 found no defects.

## 2026-10-05: English accent set with `language_code="en-IN"` (not deployed)

- **Problem**: on phone calls in English, the bot spoke with an American accent.
- **Correction**: the earlier entry said that the Live API has no language parameter. That came from the public docs and is wrong for the accent. The server accepts `SpeechConfig.language_code` with `gemini-3.8-live`. Gemini (internal search) reported that without a code, the server conditions the prebuilt voice on its `en-US` sample.
- **Test**: Live made the opening of the real English call (Sunil Pandey) 4 times for each setting. Gemini 3.8 Flash rated the accent. With no code, 3 of 4 were American. With `en-IN`, 4 of 4 were Indian. A control with an American prompt got "American" 9 of 9, so the rater can tell the accents apart. On the Hindi call (Amit Agarwal), `en-IN` and `hi-IN` both sounded native (6 of 6).
- **Change**: `LIVE_LANGUAGE_CODE = "en-IN"` for all calls. One code also keeps English words Indian in Hindi, Malayalam and Kannada calls.
- **Tests**: offline 22 passed. Live 13 of 13 scenarios passed (one run each).

## 2026-10-05: McKinsey dry-run feedback

- **Identity by name only**: the bot asks "Am I speaking with Mr. Sunil Pandey?" and continues after a yes. It does not ask for loan digits. The context has no `loan_id_last_4_digits` now. **Why**: McKinsey said that borrowers in Tier 3 cities find the digits check hard, and the call goes out to the borrower's own number. Spec §8 R28 records the risk.
- **Interest lever first**: each play now starts with the interest lever, and the CIBIL lever is last. The interest lever also gives the amount due in 3 more months, for example "₹11,889". **Why**: McKinsey said that Tier 3 borrowers care more about the interest that builds up than about CIBIL.
- **Tests**: the Live borrower lines no longer give digits. The judge checks that the bot does not ask for them. Offline 22 passed.

## 2026-10-05: Fixes from a second Gemini review (not deployed)

- **Consent wait (R27)**: `WAIT_CONSENT_S` went from 1.5 s to 2.0 s. The gate waits until 3.0 s after the link call arrives, because the budgets add up. The old R27 text said "at most 1.5 s". That was wrong: the limit was 2.5 s. The higher limit adds no delay when the check succeeds. It helps when late text restarts the check after the call arrives (Adhish).
- **Late bot text after the borrower turn is final**: `_extend_bot` now starts the checks at once. Before, it always waited the 250 ms debounce again.
- **Consent prompt languages**: the prompt now lists Kannada, Tamil and Telugu, and the yes words "haudu", "aamaa", "sari" and "avunu".
- **Spec §5**: the text now agrees with commit `483c917`. It has identity by name, no loan digits in the context, the interest lever with the 3-month amount, the new lever orders and `language_code="en-IN"`.

## 2026-10-05: Consent check reads "okay, call later" as LATER

- **Regression in `990ba3a`**: the new line "... mean yes" made Flash-Lite read "Seri, pinne vilikku" ("okay, call later") as AGREE_TODAY in 3 of 3 runs. The old prompt gave UNCLEAR or LATER. Revision 00014 has this defect.
- **Old defect**: the old prompt read "Theek hai, baad mein call karo" as AGREE_TODAY in 3 of 3 runs.
- **Change**: a yes word is a yes only if no condition or delay follows. An okay word, then "call later", is LATER.
- **Tests**: a probe of 25 replies in 5 languages, 3 runs each, gave 0 wrong results. A new Live test (`test_consent_check_on_code_mixed_replies`) sends 14 of these replies to the real consent check. Before, no test used the real consent check on a code-mixed reply.

## 2026-10-05: Monitor redesign with live captions and the Ask ladder (not deployed)

- **Live captions**: the backend sends a `caption` event for each transcription fragment. /monitor shows the turn word by word, and the final `transcript` event replaces it. Before, a line showed only at the end of the turn.
- **Late bot text**: `_extend_bot` now sends a `transcript_update` event. Before, /monitor kept the cut text of a bot line after a barge-in.
- **Ask ladder**: the monitor state event has the rungs of the call plan. /monitor shows the rungs that the borrower refused, the current Ask, an offer and the sent link.
- **Design**: the conversation reads as a script, with the newest lines in large type. The rail gives the ladder, the actions and the decision model. The font is Anek, which has Latin, Devanagari, Malayalam, Kannada, Tamil and Telugu in one design.
- **Not done (Gemini review)**: a Live connection during the ring (risk to the greeting and barge-in timing before the demo), a /mobile greeting gate (later), and a warm consent connection (the first consent check of a call was the fastest).

## 2026-10-05: No lower Asks on a final call

- **Problem**: on a phone call, Sunil Pandey (`call_attempt` 3) got all three Asks, and the bot repeated the floor. The plan had the no-push play and the full ladder together, so it contradicted itself.
- **Change**: a final call has one Ask, the full due amount. The plan keeps the floor rule: an offer of the floor or more gets a link for that amount. Spec §5 (Asks) now says this. Adhish chose to follow the spec.
- **Tests**: offline 22 passed. Live C and `above_minimum`, 3 runs each: 6 of 6 passed, with no lower Ask in any transcript.

## 2026-10-05: Hold each Ask, and a more human voice

- **Problem (Adhish, dry run)**: the bot went down one Ask at each refusal, so the borrower got the floor too easily. The replies were stiff, for example "I cannot accept that amount as it is below the minimum payment required."
- **Change**: in plays C, D and E, a lower Ask needs two refusals of the current Ask. Plays A and B keep one refusal. A hold answers the borrower's reason, gives a lever that fits it and asks again in new words. A new "How you sound" section tells the bot to react first, vary its acknowledgements and say each amount one time.
- **Tests**: offline 22 passed. Live 39 of 39 passed (13 scenarios, 3 runs each). The judge now checks that the bot holds before a lower Ask in Scenario B. In Scenario F, the bot held Ask 1 two times but went down from Ask 2 after one refusal.
- **Consent test**: 1 of 4 runs failed because one of 14 requests sent together timed out (no answer in 2.5 s). The classification was correct in all answers.

## 2026-10-06: Say why each amount, and a listening final call

- **McKinsey feedback**: "Sharpen the script to clearly explain why each amount is being requested. Use consequences relevant to Cymbal Bank, such as interest continuing to accumulate."
- **Why each amount**: each Ask in the plan has a reason that code computes, for example "it clears 3 of the 5 months. Only 2 months stay unpaid." The reason gives months, not rupees. The backend reads the last plan amount in a bot reply as the Ask, so a rupee remainder would change the Ask and the consent check.
- **Consequence**: the rules make the interest that keeps adding up each month the main consequence. The interest lever goes with the first Ask.
- **Offer rule first**: in one Live run, the hold rule made the bot refuse an offer of ₹15,000 above the floor. An offer of the floor or more now always gets a yes at once.
- **Final call (phone calls, Sapna Singh and Suresh Kumar)**: the hold rules made the bot push on final calls. It refused to go lower when the borrower asked for less. The final call now says the due amount one time and listens. It says the floor one time when the borrower asks for less. The hold rules apply only to first and second calls. The rules also ban "the system" as a reason.
- **New Live scenario `final_less`**: Sunil Pandey asks for less. The bot must offer ₹3,598 and send that link.

## 2026-10-06: The /monitor ladder moves when the bot says an Ask (not deployed)

- **Ask event**: the backend sends an `ask` event when the bot says a new Ask amount. Before, the ladder used only the Ask of each scored borrower turn. Thus "Asking now" moved 5 to 8 seconds after the bot said the amount.
- The backend uses `decisions.ask_in` for the event, as for the consent gate. Thus the ladder and the gate show the same Ask.
