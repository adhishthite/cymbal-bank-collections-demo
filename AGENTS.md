# Agent Instructions — Cymbal Bank Demo 5

The contract is `docs/specs/demo5_briefing_spec.md`. If §7 and §1–6 do not agree, use §7. §8 lists the open risks. Record each change to the spec, with its reason, in `docs/specs/build_log.md`.

## Writing standard: ASD-STE100

Write all English text in ASD-STE100 Simplified Technical English. This rule applies to docs, this file, `build_log.md`, code comments, commit messages and model instructions. Spoken lines for the borrower are not in scope; write them in natural speech.

- Write one instruction in each sentence. Put the verb first (imperative).
- Keep instructions to 20 words or fewer. Keep descriptive sentences to 25 words or fewer.
- Use the active voice. Write "the backend rejects the call", not "the call is rejected".
- Use one word for one meaning. When you select a term, use it in all locations.
- Use simple verb tenses. Do not use "-ing" words as nouns.
- Use articles ("a", "the") and complete sentences. Do not use telegraphic style.
- Put one topic in each paragraph. Keep paragraphs to 6 sentences or fewer.
- Use a numbered list for steps that must occur in sequence.
- Write warnings and cautions before the step they apply to.

## How we build

Build from first principles. Build the smallest thing that meets the acceptance values in the spec.

- Add a module, abstraction, config flag or dependency only when the current milestone needs it.
- Handle the failures that the demo can hit: the §7.3 D invariants and the scripted scenarios.
- Do not handle hypothetical edge cases. This is a 4-day build for one live demo.
- Write plain, direct code. A comment tells why the code does a thing. A comment does not repeat the code.
- Do not add filler text or speculative scaffolding.

### Tests

Write a small, high-level test suite. Many fine-grained tests make code changes harder.

- Prefer end-to-end tests. Drive the HTTP API and the scripted scenarios, and assert the outcomes.
- Assert the spec acceptance values, for example the §7.3 F KPIs. Do not pin every intermediate value.
- Do not write one test for each guard or branch. Cover the guardrails inside realistic flows.
- Do not test implementation details, such as internal fields or helper functions.

## Decisions (made with Adhish, 2026-10-03)

### Frontend

Use Next.js, not Vite (§7.5 says Vite). Build it with `output: "export"`. FastAPI serves the static files from the single Cloud Run service. All pages are client components. The WebSockets and the store stay in FastAPI.

### Time

Use IST for all times. Each datetime is timezone-aware, in `ZoneInfo("Asia/Kolkata")`. One injectable `now()` gives the time. "Today", the call window, `promised_by` and the callback dates come from `now()`. Tests set a fixed clock.

### Live tool check order

`store.py` does these checks in this sequence. The sequence lets an idempotent replay succeed after its first call changed the status.

1. Check the shape: types, enums and lengths.
2. Check the session binding (D-8).
3. Check that the call is active: `active_call_id == session.call_id`.
4. Look up the idempotency key (D-7). If the key exists, return the stored result with `duplicate: true`.
5. Check that the account is not frozen (D-5).
6. Check the amount and range rules (D-1 to D-4, D-12).
7. Check the status guard (D-6). Use a `(status, event) -> status` table.
8. Change the state. Do not put an `await` between step 4 and step 8.

### Region

Use `global` first. If a service or model is not available there, use India (`asia-south1`, Mumbai). Use the US (`us-central1`) only when neither is available. This rule replaces the `us-central1` in §7.0.

- Put Cloud Run, the buckets and Agent Runtime in `asia-south1`.
- Use `global` for `gemini-3.8-flash` and `gemini-3.8-flash-tts`.
- Use `us-central1` for `gemini-3.8-live`. Preflight found it in no other location.
- Put a resource and its staging bucket in the same region.
- Use Vertex AI only. There is no AI Studio fallback.

### Resource names

The GCP project has resources from other work. Thus each Demo 5 cloud resource has the prefix `mcd5` (Cymbal Bank Collections Demo 5).

- Start each name with `mcd5-`: Cloud Run `mcd5-backend`, service accounts `mcd5-backend` and `mcd5-agent-runtime`, secrets `mcd5-demo-agent-token` and `mcd5-view-token-key`.
- Name each bucket `<project>-mcd5-<purpose>`, because bucket names are global.
- Put the label `app=mcd5` on each resource that accepts labels.
- Exception: GE shows the display name of the collections agent to the audience. Thus its name stays "Cymbal Bank Collections Agent".

### Call recordings

Record and transcribe each live call. At the end of the call, write two files to `gs://<project>-mcd5-call-recordings/<call_id>/`:

- A stereo 16 kHz WAV file. The customer is on the left channel. The bot is on the right channel.
- The transcript, as JSON.

The bucket is private, with uniform access and public access prevention. It deletes objects after 30 days. Put each bot audio chunk at max(now, end of the bot track). When the Live API sends `interrupted`, cut the bot track at now.

### Playback from GE

GE gets a link, not a GCS URL. `get_call_status` and `get_handoff_briefs` return `<PUBLIC_BASE_URL>/recordings/<call_id>?t=<token>`. FastAPI checks the token and streams the WAV file from the bucket.

The token is an HMAC signature made with the Secret Manager secret `mcd5-view-token-key`. Each token is for one `call_id` and is valid for 24 hours. The same token method protects the `/monitor` transcript stream and the one-time `/mobile` pairing links (R10). Do not use signed URLs. Then no extra IAM grant is necessary.

### Data

The data is the McKinsey pool of 2,000 accounts. The backend loads all accounts from `backend/src/muthoot_collections/data/customers.jsonl` into memory. There is no database.

- To make the file again, run `uv run backend/scripts/convert_workbook.py`.
- Do not edit the file by hand.
- The phone numbers in the file are masked test numbers.
- Spec §7.2 gives the queue, the `call_attempt` rule and the amount fields.

### Observability, security and governance

Build these in from the first commit. Do not add them later. From M1:

- Send OpenTelemetry traces to Cloud Trace. Make a span for each GE tool call, backend request, Live tool call and store transition. Put `call_id` and `loan_id` on each span.
- Propagate the trace from end to end. The GE agent's HTTP client sends a W3C `traceparent` header. The backend continues that trace into the call. One trace covers the GE prompt, the backend request and the call.
- Record one `live.turn` span for each voice turn. Put these stage times on it: borrower speech end, first audio from Live, first audio sent to the phone, playback start on the phone. Also record the tool-call time and barge-ins.
- Send structured JSON logs to Cloud Logging.
- Write an audit record for each state change. The record gives who, what, the state before and after, and the source (`live`, `seeded` or `admin`).
- Give each service account only the permissions it needs.
- Keep secrets only in Secret Manager.
- Do not put transcripts, tokens or phone numbers in logs or span attributes.
- Model Armor templates on model input and output are a candidate. Make the decision before M2.

### Toolchain

- Use `uv` for Python. It installs Python 3.13. The VM has no system Python 3.13.
- Use the latest `bun` for the frontend.
- Use Cloud Build for container images. The VM has no Docker.

## Skills

Use skills only from official Google publishers. These are the GitHub organizations `google`, `google-gemini`, `GoogleCloudPlatform` and `firebase`. Before you install a skill, look at the repository URL and confirm the owner.

These skills are installed globally: `gemini-api`, `gemini-live-api-dev`, `google-agents-cli-adk-code`, `google-agents-cli-deploy`, `google-agents-cli-publish`, `cloud-run-basics`, `cloud-build-basics`, `google-cloud-storage-basics`, `google-cloud-solution-agentic-ai-bidirectional-streaming`.

To add a skill:

```bash
npx skills find <query>                       # search skills.sh
npx skills add <owner/repo@skill> -g -y       # e.g. google/agents-cli@google-agents-cli-eval
```

We checked these candidates but did not install them: `google/agents-cli@google-agents-cli-{eval,observability,scaffold}`, `google/skills@{gcloud,google-cloud-recipe-auth,agent-platform-deploy,gemini-live-api}`. Do not install `google/adk-docs@adk-*`. It duplicates the agents-cli set.
