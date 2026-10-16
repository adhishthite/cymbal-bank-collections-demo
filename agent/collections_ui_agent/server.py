"""A2A server for the GE agent with A2UI cards (A2A v0.3, JSON-RPC, A2UI v0.9).

GE renders A2UI only for agents that it calls over A2A v0.3. This app runs on Cloud Run.
Run it locally: uv run uvicorn collections_ui_agent.server:app --port 8081
"""

import logging
import os
import re
import uuid
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentSkill,
    DataPart,
    Part,
    TaskState,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import new_agent_parts_message, new_agent_text_message, new_task
from a2a.utils.errors import ServerError
from google.adk.runners import InMemoryRunner
from google.genai import types
from opentelemetry import trace
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from collections_agent.agent import root_agent

from . import cards

log = logging.getLogger(__name__)
A2UI_EXTENSION_URI = "https://a2ui.org/a2a-extension/a2ui/v0.9"
# The GE v0.9 sample sends this MIME type (a2ui-agent-sdk create_a2ui_part for version 0.9).
A2UI_MIME_TYPE = "application/json+a2ui"
APP_NAME = "cymbal_collections_ui"
LOAN_ID = re.compile(r"GL\d{10}")

UI_INSTRUCTION = """\
1. You help the Cymbal Bank collections team. The chat shows a card with the tables and KPIs \
of each tool result. Do not repeat the card in your text, and do not mention the card.
2. For "Who are the calls to be done today?", call `get_calls_due_today`. Write one line with \
the total accounts and the total due.
3. For any other question about accounts, branches, languages, segments, risk or amounts, call \
`query_accounts`. Report only what it returns.
4. Trigger a call only when the user names a customer or loan ID, and only if the account is \
callable. Resolve a name with one `query_accounts` call: filter `customer_name` on the exact name, \
and filter `branch` with a "contains" match on the place (branch names look like \
"Hubballi - Civil Lines"). If more than one account matches, ask which one. Show the branch and \
loan ID.
5. For "Analyze the calls", call `analyze_collection_calls`. Write one line with the committed \
amount and the committed percentage.
6. For "Show me the escalations", call `get_handoff_briefs`. Write one line with the count.
7. For the status of one loan, call `get_call_status`. Write one line with the status.
8. For "Show me the transcript" or "What was said on the call with X", resolve the name as in \
rule 4, with one `query_accounts` call. Then call `get_call_transcript`. Write one line with the \
outcome. The card shows the conversation.
9. Never invent amounts, statuses, or outcomes.
10. Write rupee amounts with Indian digit grouping, for example ₹74,35,486.
11. Keep each answer to two short lines. Do not add headings or a summary.
"""

ui_agent = root_agent.model_copy(
    update={"name": "cymbal_collections_ui_agent", "instruction": UI_INSTRUCTION}
)


def agent_card(url: str) -> AgentCard:
    return AgentCard(
        name="Cymbal Bank Collections Agent + UI",
        description=(
            "Daily collections queue, live borrower calls, call status, handoff briefs and call "
            "analysis for Cymbal Bank gold loans, with A2UI cards."
        ),
        url=url,
        version="1.0.0",
        protocol_version="0.3.0",
        preferred_transport="JSONRPC",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(
            streaming=True,
            extensions=[
                AgentExtension(
                    uri=A2UI_EXTENSION_URI,
                    description="Provides agent driven UI using the A2UI JSON format.",
                    params={"supportedCatalogIds": [cards.CATALOG_ID]},
                )
            ],
        ),
        skills=[
            AgentSkill(
                id="collections",
                name="Collections queue and calls",
                description="Show today's call queue, start a call, and analyze the calls.",
                tags=["collections", "gold-loan"],
                examples=[
                    "Who are the calls to be done today?",
                    "Analyze the calls",
                    "Show me the escalations",
                ],
            )
        ],
    )


def action_prompt(part: DataPart) -> str | None:
    """Turn an A2UI v0.9 button action into a user prompt. Return None for other data."""
    action = part.data.get("action") if part.data.get("version") == cards.VERSION else None
    if not isinstance(action, dict) or action.get("name") != cards.CALL_ACTION:
        return None
    context = action.get("context")
    loan_id = str(context.get("loan_id", "")) if isinstance(context, dict) else ""
    # The action comes from the client. Accept only a loan ID shape before it goes to the model.
    if not LOAN_ID.fullmatch(loan_id):
        return None
    return f"Call the borrower of loan ID {loan_id}."


def a2ui_part(message: dict[str, Any]) -> Part:
    # A2A v0.3 DataPart.data is an object, so each A2UI message goes in its own part.
    return Part(root=DataPart(data=message, metadata={"mimeType": A2UI_MIME_TYPE}))


class CollectionsUiExecutor(AgentExecutor):
    def __init__(self) -> None:
        self.runner = InMemoryRunner(agent=ui_agent, app_name=APP_NAME)

    async def run_agent(self, prompt: str, session_id: str) -> tuple[str, list[tuple[str, dict]]]:
        """Run one ADK turn. Return the final text and each (tool name, tool result)."""
        sessions = self.runner.session_service
        user_id = "ge"
        if not await sessions.get_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        ):
            await sessions.create_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
        text: list[str] = []
        results: list[tuple[str, dict]] = []
        message = types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
        async for event in self.runner.run_async(
            user_id=user_id, session_id=session_id, new_message=message
        ):
            for p in (event.content and event.content.parts) or []:
                if p.function_response:
                    results.append((p.function_response.name, p.function_response.response or {}))
                elif p.text and not p.thought and event.is_final_response():
                    text.append(p.text)
        return "".join(text).strip(), results

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if A2UI_EXTENSION_URI in context.requested_extensions:
            context.add_activated_extension(A2UI_EXTENSION_URI)
        prompt = None
        for part in context.message.parts:
            if isinstance(part.root, DataPart):
                prompt = action_prompt(part.root) or prompt
        prompt = prompt or context.get_user_input()

        task = context.current_task or new_task(context.message)
        if not context.current_task:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        if not prompt.strip():
            # An unknown action, a bad loan ID or a renderer error event: do not call the model.
            await updater.update_status(
                TaskState.completed,
                new_agent_text_message("I could not read that request.", task.context_id, task.id),
                final=True,
            )
            return
        try:
            text, results = await self.run_agent(prompt, task.context_id)
        except Exception:
            # Deliberate boundary: GE must always get a final status, not a task stuck in "working".
            log.exception("agent turn failed")
            await updater.update_status(
                TaskState.failed,
                new_agent_text_message(
                    "The collections agent could not answer. Try again.", task.context_id, task.id
                ),
                final=True,
            )
            return
        parts = [Part(root=TextPart(text=text or "Done."))]
        for tool, result in results:
            surface_id = f"{tool}-{uuid.uuid4().hex[:8]}"
            try:
                messages = cards.build(tool, result, surface_id) or []
            except Exception:
                # A bad card must not hide the text answer or the other cards.
                log.exception("card build failed for %s", tool)
                continue
            parts.extend(a2ui_part(m) for m in messages)
        await updater.update_status(
            TaskState.completed,
            new_agent_parts_message(parts, task.context_id, task.id),
            final=True,
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise ServerError(error=UnsupportedOperationError())


def setup_tracing() -> None:
    # K_SERVICE is set only on Cloud Run. There, ADK spans and the request span go to Cloud Trace,
    # and backend_client sends the traceparent on to the backend.
    if not os.environ.get("K_SERVICE"):
        return
    from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

    provider = TracerProvider()
    provider.add_span_processor(
        BatchSpanProcessor(CloudTraceSpanExporter(project_id=os.environ["GOOGLE_CLOUD_PROJECT"]))
    )
    trace.set_tracer_provider(provider)


def build_app(url: str):
    card = agent_card(url)
    handler = DefaultRequestHandler(
        agent_executor=CollectionsUiExecutor(), task_store=InMemoryTaskStore()
    )
    # The middleware continues an incoming W3C traceparent, for example from GE.
    return OpenTelemetryMiddleware(
        A2AStarletteApplication(agent_card=card, http_handler=handler).build()
    )


# AGENT_URL is the public Cloud Run URL. GE uses the URL in its registered copy of the card.
setup_tracing()
app = build_app(os.environ.get("AGENT_URL", "http://localhost:8081"))
