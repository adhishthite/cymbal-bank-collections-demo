"""Send one prompt to a local AdkApp or a deployed Agent Runtime engine.

Local:  uv run python query.py --agent hello "ping please"
Remote: uv run python query.py --remote projects/.../reasoningEngines/123 "Who are the calls..."
"""

import argparse
import asyncio
import os
import subprocess

AGENT_RUNTIME_LOCATION = "asia-south1"


def gcloud_project() -> str:
    out = subprocess.run(
        ["gcloud", "config", "get-value", "project"], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def local_app(agent: str):
    from vertexai.agent_engines import AdkApp

    if agent == "hello":
        from hello_agent.agent import root_agent
    else:
        from collections_agent.agent import root_agent
    return AdkApp(agent=root_agent)


def print_event(event: dict) -> None:
    for part in (event.get("content") or {}).get("parts") or []:
        if call := part.get("function_call"):
            print(f"[tool call] {call['name']}({call.get('args')})")
        elif result := part.get("function_response"):
            print(f"[tool result] {result['name']}: {str(result.get('response'))[:500]}")
        elif part.get("text") and not part.get("thought"):
            print(part["text"])


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt")
    parser.add_argument("--agent", choices=["hello", "collections"], default="hello")
    parser.add_argument("--remote", help="Agent Runtime resource name. Omit to run locally.")
    args = parser.parse_args()

    project = gcloud_project()
    if args.remote:
        import vertexai

        client = vertexai.Client(project=project, location=AGENT_RUNTIME_LOCATION)
        app = client.agent_engines.get(name=args.remote)
    else:
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project)
        app = local_app(args.agent)

    async for event in app.async_stream_query(message=args.prompt, user_id="local-test"):
        print_event(event)


if __name__ == "__main__":
    asyncio.run(main())
