"""Day-1 probe: one tool, Flash pinned to `global` while the engine runs in asia-south1."""

from google.adk.agents import Agent
from google.adk.models import Gemini


def ping() -> dict:
    """Check that tool calls work. Call this tool when the user asks you to ping."""
    return {"ok": True}


root_agent = Agent(
    name="hello_agent",
    # R2: Agent Runtime reserves GOOGLE_CLOUD_LOCATION, so pin the model location in code.
    model=Gemini(
        model="gemini-3.8-flash", client_kwargs={"enterprise": True, "location": "global"}
    ),
    instruction="Reply in one short sentence. When the user asks you to ping, call `ping`.",
    tools=[ping],
)
