# GE registration: Cymbal Bank Collections Agent + UI (A2UI)

This agent shows A2UI v0.9 cards in GE. The code is in `collections_ui_agent/`. GE shows A2UI only for an agent that is registered as an A2A agent. The ADK registration in `register_ge.md` does not show A2UI.

## Why Cloud Run, not Agent Runtime

1. GE supports the A2A v0.3 streaming mechanism. The GE A2UI v0.9 tutorial uses the `JSONRPC` transport.
2. The installed Agent Runtime A2A template (`vertexai.agent_engines.A2aAgent`, aiplatform 2.3.0) requires A2A v1.0 with an `HTTP+JSON` primary interface. Its v0.3 option is REST only.
3. GE calls an Agent Runtime A2A agent with an end-user OAuth token. This needs an OAuth client and a Discovery Engine authorization resource with the `cloud-platform` scope. Each user must also give consent.
4. GE calls a Cloud Run A2A agent with a Google OIDC token. This needs one IAM grant only.

Sources: [Register and manage A2A agents](https://docs.cloud.google.com/gemini/enterprise/docs/register-and-manage-an-a2a-agent), [Host an A2UI agent with Cloud Run](https://docs.cloud.google.com/gemini/enterprise/docs/a2ui-agents/tutorial-host-agent-cloud-run).

## Values

| Item | Value |
| --- | --- |
| Cloud Run service | `mcd5-ui-agent`, `asia-south1`, SA `mcd5-agent-runtime`, label `app=mcd5` |
| Agent URL | `https://mcd5-ui-agent-355880143137.asia-south1.run.app` |
| GE app | `muthoot-demo-app_1791049776634`, project number `355880143137`, location `global` |
| Display name | Cymbal Bank Collections Agent + UI |
| GE agent ID | `3651103152243872492` (registered 2026-10-04, `ENABLED`) |
| A2UI extension | `https://a2ui.org/a2a-extension/a2ui/v0.9` |
| Catalog | `https://www.gstatic.com/vertexaisearch/a2ui/v0_9/gemini_enterprise_composite_catalog.json` |

## How GE authenticates

GE makes a Google-signed OIDC ID token for the Discovery Engine service agent `service-355880143137@gcp-sa-discoveryengine.iam.gserviceaccount.com`. The audience is the agent URL. GE sends the token in the `X-Serverless-Authorization` header. Cloud Run checks that the service agent has `roles/run.invoker`, then forwards the request. The app does no other auth check. The service keeps the Cloud Run invoker IAM check on (`--no-allow-unauthenticated`).

## Steps

Run the steps from `agent/`. Each step changes cloud resources. Get approval before each step.

1. Deploy the service:

   ```bash
   make deploy-ui-agent BACKEND_URL=https://mcd5-backend-355880143137.asia-south1.run.app
   ```

2. Let GE call the service:

   ```bash
   gcloud run services add-iam-policy-binding mcd5-ui-agent --region asia-south1 \
     --member serviceAccount:service-355880143137@gcp-sa-discoveryengine.iam.gserviceaccount.com \
     --role roles/run.invoker
   ```

3. Test the service with your own identity token. You must have `roles/run.invoker`:

   ```bash
   make query-ui URL=https://mcd5-ui-agent-355880143137.asia-south1.run.app STREAM=1
   ```

4. Register the agent. GE keeps a static copy of the agent card. This command sends the card that the service serves:

   ```bash
   CARD=$(curl -fsS -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
     https://mcd5-ui-agent-355880143137.asia-south1.run.app/.well-known/agent-card.json)
   jq -n --arg card "$CARD" '{
     name: "mcd5-ui-agent",
     displayName: "Cymbal Bank Collections Agent + UI",
     description: "Daily collections queue, live borrower calls, call status, handoff briefs and call analysis for Cymbal Bank gold loans, with A2UI cards.",
     a2aAgentDefinition: {jsonAgentCard: $card}
   }' > /tmp/mcd5-ui-agent.json
   curl -fsS -X POST \
     -H "Authorization: Bearer $(gcloud auth print-access-token)" \
     -H "Content-Type: application/json" \
     -H "X-Goog-User-Project: adhish-base-project-1" \
     https://discoveryengine.googleapis.com/v1alpha/projects/355880143137/locations/global/collections/default_collection/engines/muthoot-demo-app_1791049776634/assistants/default_assistant/agents \
     -d @/tmp/mcd5-ui-agent.json
   ```

   The body has no `authorizationConfig`. The agent does not use Google Cloud resources for the user. The docs make this field optional.

5. Record the GE agent ID from the response in this file.

The card that step 4 sends has these values: `protocolVersion` `0.3.0`, `preferredTransport` `JSONRPC`, `url` the agent URL, `capabilities.streaming` `true`, and one extension with the A2UI v0.9 URI and `params.supportedCatalogIds` set to the GE composite catalog.

## Checks in the GE app (human, in the GE chat UI)

1. Type "Who are the calls to be done today?". A text line and a card appear. The card shows 1,291 accounts and ₹74,35,486 due, the splits, a table of the top rows, and Call buttons.
2. Type "Analyze the calls". The card shows ₹24,833 committed (78.3%), a KPI table and one row for each call.
3. Type "Show me the escalations". A handoff brief card appears.
4. Push a Call button only when a paired phone is ready. The button starts a live call for that loan.

If GE shows text but no card, look in the Cloud Run logs for the request. Then compare the A2UI parts with `make query-ui`.

## Redeploy

Run `make deploy-ui-agent BACKEND_URL=...` again. The service URL stays the same, so the GE registration stays valid. If the agent card changes, register the card again (PATCH the agent, or delete it and do step 4).
