# GE agent registration

The build agent registered both agents on 2026-10-03 with the Discovery Engine API (`POST .../assistants/default_assistant/agents`, `adkAgentDefinition.provisionedReasoningEngine`). Both are `ENABLED`. They are in the existing GE app **Cymbal Bank-Demo-App** (`muthoot-demo-app_1791049776634`, location `global`).

| Agent | Display name | Reasoning engine | GE agent ID |
| --- | --- | --- | --- |
| Day-1 probe | MCD5 Hello Probe | `projects/355880143137/locations/asia-south1/reasoningEngines/2045804111198158848` | `11930827076953452188` |
| Collections | Cymbal Bank Collections Agent | `projects/355880143137/locations/asia-south1/reasoningEngines/1026759142389317632` | `14862378329357101849` |

Description for the collections agent: "Daily collections queue, live borrower calls, call status, handoff briefs and call analysis for Cymbal Bank gold loans."

## Manual steps (if a registration must be done again)

1. Open the Gemini Enterprise console. Select the app Cymbal Bank-Demo-App.
2. Go to Agents > Add agent > **Custom agent via Agent Runtime**.
3. Paste the resource name. Enter the display name and the description. Save.

The Discovery Engine service agent has `roles/discoveryengine.serviceAgent`. This role includes `aiplatform.reasoningEngines.query`, so no extra grant is necessary.

## Checks in the GE app (human, in the GE chat UI)

1. Hello probe: type "Please ping." The agent calls `ping` and replies.
2. Collections agent: type "Who are the calls to be done today?" The reply gives 1,291 accounts, ₹74,35,486 due, and the top rows with status, call attempt and callable.
3. Collections agent: type "Analyze the calls". The reply gives ₹24,833 committed (78.3%), 2 called accounts and 1 partial agreement.

If GE does not accept an `asia-south1` engine, tell the build agent. The fallback is a redeploy to `us-central1` (spec §7.6).

## Redeploy

Run from `agent/`: `make deploy-agent BACKEND_URL=https://mcd5-backend-355880143137.asia-south1.run.app UPDATE=<resource name>`. An update keeps the resource name, so the GE registration stays valid.
