"""A scripted stand-in for the Gemini Live session. A script plays the model side of one call."""

import asyncio
from contextlib import asynccontextmanager

from google.genai import types

BOT_AUDIO = b"\x01\x00" * 480  # 20 ms of 24 kHz PCM


class FakeSession:
    def __init__(self) -> None:
        self.out: asyncio.Queue = asyncio.Queue()
        self.audio_in: asyncio.Queue = asyncio.Queue()
        self.texts: list[str] = []
        self.responses: asyncio.Queue = asyncio.Queue()
        self.results: list[dict] = []

    # ---- the SDK surface that LiveCall uses ----

    async def send_realtime_input(self, *, audio=None, text=None) -> None:
        if audio is not None:
            self.audio_in.put_nowait(audio.data)
        if text is not None:
            self.texts.append(text)

    async def send_tool_response(self, *, function_responses) -> None:
        self.responses.put_nowait([r.response for r in function_responses])

    async def receive(self):
        while True:
            msg = await self.out.get()
            yield msg
            if msg.server_content and msg.server_content.turn_complete:
                return

    # ---- script helpers ----

    def _content(self, **kwargs) -> None:
        self.out.put_nowait(types.LiveServerMessage(server_content=types.LiveServerContent(**kwargs)))

    def hear(self, text: str) -> None:
        self._content(input_transcription=types.Transcription(text=text, finished=True))

    def say(self, text: str) -> None:
        audio = types.Part(inline_data=types.Blob(data=BOT_AUDIO, mime_type="audio/pcm;rate=24000"))
        self._content(model_turn=types.Content(role="model", parts=[audio]))
        self._content(output_transcription=types.Transcription(text=text))
        self._content(turn_complete=True)

    def interrupt(self) -> None:
        self._content(interrupted=True)

    def say_interrupted(self, heard: str, late: str) -> None:
        """A barge-in cuts the bot. Live sends the rest of the transcription after `interrupted`."""
        audio = types.Part(inline_data=types.Blob(data=BOT_AUDIO, mime_type="audio/pcm;rate=24000"))
        self._content(model_turn=types.Content(role="model", parts=[audio]))
        self._content(output_transcription=types.Transcription(text=heard))
        self._content(interrupted=True)
        self._content(output_transcription=types.Transcription(text=late))
        self._content(turn_complete=True)

    async def call(self, name: str, **args) -> dict:
        fc = types.FunctionCall(id=f"fc-{len(self.results)}", name=name, args=args)
        self.out.put_nowait(types.LiveServerMessage(tool_call=types.LiveServerToolCall(function_calls=[fc])))
        result = (await self.responses.get())[0]
        self.results.append(result)
        return result


class FakeLive:
    """The `connect` callable for create_app. Set `script` before each call."""

    def __init__(self) -> None:
        self.script = None
        self.sessions: list[FakeSession] = []
        self.configs: list[types.LiveConnectConfig] = []

    @asynccontextmanager
    async def __call__(self, config: types.LiveConnectConfig):
        session = FakeSession()
        self.sessions.append(session)
        self.configs.append(config)
        task = asyncio.create_task(self.script(session)) if self.script else None
        try:
            yield session
        finally:
            if task:
                task.cancel()


async def fake_summary(record, row) -> dict:
    return {"summary": "Scripted call.", "sentiment": "NEUTRAL"}
