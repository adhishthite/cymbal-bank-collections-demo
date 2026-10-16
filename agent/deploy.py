"""Deploy an agent to Agent Runtime in asia-south1 and print its resource name.

Run from agent/, because extra_packages is a relative path (R6):
  uv run python deploy.py --agent hello
  uv run python deploy.py --agent collections --backend-url https://...run.app
  uv run python deploy.py --agent collections --backend-url ... --update projects/.../123
"""

import argparse
import os
from importlib.metadata import version

from query import AGENT_RUNTIME_LOCATION, gcloud_project, local_app

# R6: pin each package to the locally installed version to prevent pickle version skew.
PINNED = [
    "google-adk",
    "google-cloud-secret-manager",
    "httpx",
    "opentelemetry-api",
    "cloudpickle",
    "pydantic",
]

DISPLAY_NAMES = {"hello": "Cymbal Hello Probe", "collections": "Cymbal Bank Collections Agent"}
PACKAGES = {"hello": "hello_agent", "collections": "collections_agent"}


def requirements() -> list[str]:
    aiplatform = f"google-cloud-aiplatform[agent_engines,adk]=={version('google-cloud-aiplatform')}"
    return [aiplatform] + [f"{name}=={version(name)}" for name in PINNED]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", choices=["hello", "collections"], required=True)
    parser.add_argument("--backend-url", default=os.environ.get("BACKEND_URL"))
    parser.add_argument("--update", metavar="RESOURCE_NAME", help="Redeploy this engine.")
    args = parser.parse_args()

    project = gcloud_project()
    # Agent Runtime telemetry sends ADK spans to Cloud Trace. Message content stays out of spans.
    env_vars = {"GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY": "true"}
    if args.agent == "collections":
        if not args.backend_url:
            parser.error("--backend-url or env BACKEND_URL is required for the collections agent")
        env_vars["BACKEND_URL"] = args.backend_url
        env_vars["DEMO_TOKEN_SECRET"] = (
            f"projects/{project}/secrets/mcd5-demo-agent-token/versions/latest"
        )

    config = {
        "display_name": DISPLAY_NAMES[args.agent],
        "requirements": requirements(),
        "extra_packages": [PACKAGES[args.agent]],
        "env_vars": env_vars,
        "service_account": f"mcd5-agent-runtime@{project}.iam.gserviceaccount.com",
        "staging_bucket": f"gs://{project}-mcd5-agent-staging",
        "labels": {"app": "mcd5"},
    }
    print("requirements:", *config["requirements"], sep="\n  ")

    import vertexai

    client = vertexai.Client(project=project, location=AGENT_RUNTIME_LOCATION)
    app = local_app(args.agent)
    if args.update:
        remote = client.agent_engines.update(name=args.update, agent=app, config=config)
    else:
        remote = client.agent_engines.create(agent=app, config=config)
    print(remote.api_resource.name)


if __name__ == "__main__":
    main()
