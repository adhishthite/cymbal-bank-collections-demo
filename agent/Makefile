# Run each target from agent/: extra_packages in deploy.py is a relative path (R6).
AGENT ?= collections
PROMPT ?= Who are the calls to be done today?

.PHONY: deploy-hello deploy-agent deploy-ui-agent query query-ui test lint format

deploy-hello:
	uv run python deploy.py --agent hello

# Set BACKEND_URL to the Cloud Run URL. Set UPDATE to a resource name to redeploy.
deploy-agent:
	uv run python deploy.py --agent collections --backend-url "$(BACKEND_URL)" $(if $(UPDATE),--update $(UPDATE))

# Set REMOTE to a resource name to query the deployed engine. Omit it to run locally.
query:
	uv run python query.py --agent $(AGENT) $(if $(REMOTE),--remote $(REMOTE)) "$(PROMPT)"

# The A2UI agent runs on Cloud Run, not Agent Runtime: GE needs A2A v0.3 JSON-RPC (register_ge_ui.md).
# GE sends a Google OIDC token, so Cloud Run keeps its invoker IAM check.
PROJECT = $(shell gcloud config get-value project 2>/dev/null)
PROJECT_NUMBER = $(shell gcloud projects describe $(PROJECT) --format 'value(projectNumber)' 2>/dev/null)
UI_SERVICE := mcd5-ui-agent
REGION := asia-south1

deploy-ui-agent:
	$(if $(BACKEND_URL),,$(error Set BACKEND_URL to the backend Cloud Run URL))
	gcloud run deploy $(UI_SERVICE) --source . --region $(REGION) \
		--service-account mcd5-agent-runtime@$(PROJECT).iam.gserviceaccount.com \
		--labels app=mcd5 --no-allow-unauthenticated --min-instances 0 --max-instances 1 \
		--set-secrets DEMO_AGENT_TOKEN=mcd5-demo-agent-token:latest \
		--set-env-vars GOOGLE_CLOUD_PROJECT=$(PROJECT),BACKEND_URL=$(BACKEND_URL),AGENT_URL=https://$(UI_SERVICE)-$(PROJECT_NUMBER).$(REGION).run.app

# Omit URL to run the A2A app in this process. Set STREAM=1 to use message/stream.
query-ui:
	uv run python query_ui.py $(if $(URL),--url $(URL)) $(if $(STREAM),--stream) "$(PROMPT)"

test:
	uv run pytest -q

lint:
	uv run ruff format --check . && uv run ruff check .

format:
	uv run ruff format . && uv run ruff check --fix .
