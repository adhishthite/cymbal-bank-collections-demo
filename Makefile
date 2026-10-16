MILESTONE ?= all
BACKEND := uv run --project backend

.PHONY: preflight test format lint check dev deploy-backend backend-url reset pair-link monitor-link test-live revoke-links telephony telephony-twilio telephony-mobile

preflight:
	$(BACKEND) python backend/scripts/preflight.py --milestone $(MILESTONE)

test:
	cd backend && uv run pytest -q

format:
	cd backend && uv run ruff format .

lint:
	cd backend && uv run ruff check .

check:
	cd backend && uv run ruff format --check . && uv run ruff check . && uv run pytest -q

# Opt-in: real gemini-3.8-live on Vertex AI with text borrower turns (spec §7.7 DoD M2 item 1).
test-live:
	cd backend && LIVE_TESTS=1 uv run pytest -q tests/test_live_integration.py

dev:
	cd backend && CALL_WINDOW_BYPASS=true uv run uvicorn muthoot_collections.main:app --reload --port 8080

# Cloud Run (spec §7.4 dev settings). Web-UI-only telephony by default (TELEPHONY_MODE=mobile, TWILIO=off).
PROJECT = $(shell gcloud config get-value project 2>/dev/null)
REGION := asia-south1
SERVICE := cymbal-collections-voice
LIVE_MODEL ?= gemini-3.8-live
CALL_WINDOW_BYPASS ?= true
# The mode after a deploy or restart. Use `make telephony-twilio` or `make telephony-mobile` to switch at run time.
TELEPHONY_MODE ?= mobile
# TWILIO=off deploys without the Twilio secrets: /mobile only.
TWILIO ?= off
TWILIO_SECRETS = $(if $(filter on,$(TWILIO)),$(TWILIO_SECRET_LIST))
TWILIO_SECRET_LIST := ,TWILIO_ACCOUNT_SID=mcd5-twilio-account-sid:latest,TWILIO_FROM_NUMBER=mcd5-twilio-from-number:latest,TWILIO_API_KEY_SID=mcd5-twilio-api-key-sid:latest,TWILIO_API_KEY_SECRET=mcd5-twilio-api-key-secret:latest,TWILIO_AUTH_TOKEN=mcd5-twilio-auth-token:latest,TWILIO_ALLOWED_NUMBERS=mcd5-twilio-allowed-numbers:latest,TWILIO_PHONE_MAP=mcd5-demo-phone-map:latest
# The URL of an existing service. On the first deploy, run the deploy again to set it.
PUBLIC_BASE_URL ?= $(shell gcloud run services describe $(SERVICE) --region $(REGION) --format 'value(status.url)' 2>/dev/null)
-include .env.genai

deploy-backend:
	gcloud run deploy $(SERVICE) --source . --region $(REGION) \
		--service-account mcd5-backend@$(PROJECT).iam.gserviceaccount.com \
		--labels app=mcd5 --no-invoker-iam-check --min-instances 0 --max-instances 1 --session-affinity --timeout 3600 \
		--no-cpu-throttling \
		--set-secrets DEMO_AGENT_TOKEN=mcd5-demo-agent-token:latest,VIEW_TOKEN_KEY=mcd5-view-token-key:latest,TYPESAFE_API_KEY=mcd5-typesafe-api-key:latest$(TWILIO_SECRETS) \
		--set-env-vars GOOGLE_CLOUD_PROJECT=$(PROJECT),TELEPHONY_MODE=$(TELEPHONY_MODE),LIVE_MODEL=$(LIVE_MODEL),PUBLIC_BASE_URL=$(PUBLIC_BASE_URL),SEED_OUTCOMES=true,CALL_WINDOW_IST=08:00-19:00,CALL_WINDOW_BYPASS=$(CALL_WINDOW_BYPASS),GENAI_LOCATION_FLASH=$(GENAI_LOCATION_FLASH),GENAI_LOCATION_TTS=$(GENAI_LOCATION_TTS),GENAI_LOCATION_LIVE=$(GENAI_LOCATION_LIVE),GENAI_LOCATION_CONSENT=global,RECORDINGS_BUCKET=$(PROJECT)-mcd5-call-recordings

backend-url:
	@gcloud run services describe $(SERVICE) --region $(REGION) --format 'value(status.url)'

# Admin calls read the token from Secret Manager. The token never goes to a file.
reset:
	printf "Authorization: Bearer %s\n" "$$(gcloud secrets versions access latest --secret mcd5-demo-agent-token)" | curl -fsS -X POST -H @- "$$($(MAKE) -s backend-url)/api/admin/reset"

# The admin token goes to curl on stdin, and never in process arguments.
ADMIN_POST = printf "Authorization: Bearer %s\n" "$$(gcloud secrets versions access latest --secret mcd5-demo-agent-token)" | curl -fsS -X POST -H @-
URL_FROM_JSON = python3 -c 'import json, sys; print(json.load(sys.stdin)["url"])'

# One-time /mobile pairing link (24 h, single use). Prints a QR code if qrencode is installed.
pair-link:
	@url="$$($(ADMIN_POST) "$$($(MAKE) -s backend-url)/api/admin/pair-link" | $(URL_FROM_JSON))" && echo "$$url" && \
		if command -v qrencode >/dev/null; then printf '%s' "$$url" | qrencode -t ansiutf8; fi

# /monitor link with a 24 h view token.
monitor-link:
	@$(ADMIN_POST) "$$($(MAKE) -s backend-url)/api/admin/monitor-link" | $(URL_FROM_JSON)

# Show or switch the telephony mode. The switch takes effect at once, with no redeploy.
# /mobile stays paired and ready in both modes, so `telephony-mobile` is the fallback.
# The URL comes from a make variable, not $(MAKE), so `make -n` does not switch the mode.
telephony:
	@printf "Authorization: Bearer %s\n" "$$(gcloud secrets versions access latest --secret mcd5-demo-agent-token)" | curl -fsS -H @- "$(PUBLIC_BASE_URL)/api/admin/telephony" && echo

telephony-twilio telephony-mobile: telephony-%:
	@$(ADMIN_POST) -H 'Content-Type: application/json' -d '{"mode": "$*"}' "$(PUBLIC_BASE_URL)/api/admin/telephony" && echo

# Make all pair links, phone credentials and monitor links invalid.
# A new key version takes effect only in a new revision. The new revision also resets the store.
revoke-links:
	openssl rand -base64 48 | tr -d '\n' | gcloud secrets versions add mcd5-view-token-key --data-file=-
	gcloud run services update $(SERVICE) --region $(REGION) --update-env-vars KEY_ROTATED_AT=$$(date +%s)
