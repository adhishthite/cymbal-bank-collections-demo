"""Preflight for Demo 5 (spec §7.0, R1, R11).

Run from the repo root: make preflight MILESTONE=m1

The script probes each model on Vertex AI in the location order global, asia-south1,
us-central1. The first location that passes is the model location. The script writes the
locations to .env.genai at the repo root.

For MILESTONE=m1, only gemini-3.8-flash is a gate. The Live and TTS results are report-only.
"""

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parents[2]
LOCATIONS = ["global", "asia-south1", "us-central1"]
LIVE_TIMEOUT_S = 5


def gcloud(*args: str) -> str:
    out = subprocess.run(["gcloud", *args], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def probe_flash(client: genai.Client, model: str) -> None:
    resp = client.models.generate_content(model=model, contents="Reply with the word OK.")
    if not resp.text:
        raise RuntimeError("empty response")


def probe_tts(client: genai.Client, model: str) -> None:
    resp = client.models.generate_content(
        model=model,
        contents="Namaste.",
        config=types.GenerateContentConfig(response_modalities=["AUDIO"]),
    )
    part = resp.candidates[0].content.parts[0]
    if not (part.inline_data and part.inline_data.data):
        raise RuntimeError("no audio in response")


async def probe_live_async(client: genai.Client, model: str) -> None:
    # R11: one BLOCKING tool, so the probe fails if the surface rejects the field.
    tool = types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="ping",
                description="Probe tool.",
                behavior="BLOCKING",
                parameters=types.Schema(type="OBJECT", properties={}),
            )
        ]
    )
    config = types.LiveConnectConfig(response_modalities=["AUDIO"], tools=[tool])
    async with client.aio.live.connect(model=model, config=config) as session:
        await session.send_realtime_input(text="Say hello.")
        async with asyncio.timeout(LIVE_TIMEOUT_S):
            async for msg in session.receive():
                if msg.server_content and msg.server_content.model_turn:
                    return
        raise RuntimeError("no audio before timeout")


def probe_live(client: genai.Client, model: str) -> None:
    asyncio.run(probe_live_async(client, model))


PROBES = {
    "gemini-3.8-flash": ("GENAI_LOCATION_FLASH", probe_flash),
    "gemini-3.8-flash-tts": ("GENAI_LOCATION_TTS", probe_tts),
}


def find_location(project: str, model: str, probe) -> tuple[str | None, list[str]]:
    errors = []
    for location in LOCATIONS:
        client = genai.Client(vertexai=True, project=project, location=location)
        try:
            probe(client, model)
            return location, errors
        except Exception as e:  # noqa: BLE001 - each failure is a result to report
            errors.append(f"{location}: {type(e).__name__}: {str(e)[:120]}")
    return None, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--milestone", default="all", choices=["m1", "all"])
    args = parser.parse_args()

    project = gcloud("config", "get-value", "project")
    account = gcloud("config", "get-value", "account")
    print(f"project: {project}\naccount: {account}")

    policy = gcloud(
        "org-policies",
        "describe",
        "iam.allowedPolicyMemberDomains",
        "--effective",
        f"--project={project}",
    )
    if "allowAll: true" not in policy:
        print(
            "STOP: iam.allowedPolicyMemberDomains can block allUsers. Human steps for Adhish:\n"
            f"  1. Create policy.yaml with name: projects/{project}/policies/"
            "iam.allowedPolicyMemberDomains and spec: {rules: [{allowAll: true}]}\n"
            "  2. Run: gcloud org-policies set-policy policy.yaml"
        )
        return 1
    print("org policy: allowAll")

    live_model = os.environ.get("LIVE_MODEL", "gemini-3.8-live")
    probes = dict(PROBES)
    probes[live_model] = ("GENAI_LOCATION_LIVE", probe_live)
    gates = {"gemini-3.8-flash"} if args.milestone == "m1" else set(probes)

    rows, env, failed_gate = [], {}, False
    for model, (env_key, probe) in probes.items():
        location, errors = find_location(project, model, probe)
        gate = model in gates
        result = "PASS" if location else ("FAIL" if gate else "FAIL (report-only)")
        rows.append((model, location or "-", result))
        for err in errors:
            print(f"  {model} {err}")
        if location:
            env[env_key] = location
        elif gate:
            failed_gate = True

    print(f"\n{'model':<30} {'location':<14} result")
    for model, location, result in rows:
        print(f"{model:<30} {location:<14} {result}")

    if failed_gate:
        print("\nSTOP: a gate model failed on Vertex AI.")
        return 1
    env_file = ROOT / ".env.genai"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
    print(f"\nwrote {env_file.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
