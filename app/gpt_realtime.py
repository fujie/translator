"""
GPT Realtime API client over WebSocket.

Uses gpt-realtime (and variants) with the conversation session shape.
Translation direction is controlled via the instructions (system prompt).

Note: gpt-realtime-translate exists in the model list but its inference
backend returns Invalid URL errors — do not use it.
"""
import asyncio
import base64
import json
import logging
import ssl
from typing import Callable, Optional

import certifi
import websockets
from websockets.asyncio.client import ClientConnection

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

logger = logging.getLogger(__name__)

REALTIME_BASE_URL = "wss://api.openai.com/v1/realtime"
DEFAULT_MODEL = "gpt-realtime"
SAMPLE_RATE = 24000
CHANNELS = 1

# ── System prompts ────────────────────────────────────────────────────────────

MIC_SYSTEM_PROMPT = (
    "You are a real-time speech translator. "
    "The user will speak in Japanese. "
    "Translate everything they say into natural spoken English. "
    "Output only the translated speech audio. "
    "Do not add commentary or explanations."
)

SPEAKER_SYSTEM_PROMPT = (
    "You are a real-time speech translator. "
    "Listen to the incoming audio and detect the language automatically. "
    "If the speaker is speaking English, translate their speech into natural spoken Japanese. "
    "If the speaker is speaking Japanese, repeat their speech as-is in Japanese without translation. "
    "Output only the audio. Do not add commentary or explanations."
)

# Keep these as aliases so existing imports don't break
MIC_INSTRUCTIONS = MIC_SYSTEM_PROMPT
SPEAKER_INSTRUCTIONS = SPEAKER_SYSTEM_PROMPT

VAD_CONFIG = {
    "type": "server_vad",
    "threshold": 0.5,
    "prefix_padding_ms": 300,
    "silence_duration_ms": 500,
}


class RealtimeSession:
    def __init__(
        self,
        api_key: str,
        mode: str,           # "mic" | "speaker"
        on_audio: Callable[[bytes], None],
        on_transcript: Optional[Callable[[str, str, str], None]] = None,
        model: str = DEFAULT_MODEL,
    ):
        self.api_key = api_key
        self.mode = mode
        self.model = model
        self.on_audio = on_audio
        self.on_transcript = on_transcript
        self._ws: Optional[ClientConnection] = None
        self._running = False
        self._send_queue: asyncio.Queue = asyncio.Queue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # Public interface (thread-safe)
    # ------------------------------------------------------------------

    def send_audio(self, pcm16_bytes: bytes):
        if self._loop and self._running:
            self._loop.call_soon_threadsafe(self._send_queue.put_nowait, pcm16_bytes)

    async def start(self):
        self._running = True
        self._loop = asyncio.get_running_loop()
        await self._connect_and_run()

    async def stop(self):
        self._running = False
        if self._ws:
            await self._ws.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _connect_and_run(self):
        url = f"{REALTIME_BASE_URL}?model={self.model}"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        while self._running:
            try:
                async with websockets.connect(url, additional_headers=headers, ssl=_SSL_CTX) as ws:
                    self._ws = ws
                    logger.info(f"[{self.mode}] Connected (model={self.model})")
                    await self._configure_session(ws)
                    await asyncio.gather(
                        self._send_loop(ws),
                        self._receive_loop(ws),
                    )
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"[{self.mode}] Connection closed: {e}")
            except Exception as e:
                logger.error(f"[{self.mode}] Error: {e}")
            if self._running:
                logger.info(f"[{self.mode}] Reconnecting in 2s…")
                await asyncio.sleep(2)

    async def _configure_session(self, ws):
        instructions = MIC_SYSTEM_PROMPT if self.mode == "mic" else SPEAKER_SYSTEM_PROMPT
        # Both gpt-realtime and gpt-realtime-translate use the nested
        # audio.input / audio.output structure — not flat input_audio_format etc.
        session = {
            "type": "realtime",
            "output_modalities": ["audio"],
            "instructions": instructions,
            "audio": {
                "input": {"turn_detection": VAD_CONFIG},
                "output": {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE}},
            },
        }
        msg = {"type": "session.update", "session": session}
        logger.debug(f"[{self.mode}] session.update → {json.dumps(session, ensure_ascii=False)}")
        await ws.send(json.dumps(msg))

    async def _send_loop(self, ws):
        while self._running:
            try:
                chunk = await asyncio.wait_for(self._send_queue.get(), timeout=0.1)
                encoded = base64.b64encode(chunk).decode()
                await ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": encoded}))
            except asyncio.TimeoutError:
                continue

    async def _receive_loop(self, ws):
        input_transcript = ""
        output_transcript = ""

        async for raw in ws:
            if not self._running:
                break
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")
            logger.debug(f"[{self.mode}] ← {etype}")

            # ── Audio output ──────────────────────────────────────────
            # gpt-realtime uses response.output_audio.delta
            if etype in ("response.output_audio.delta", "response.audio.delta"):
                audio_b64 = event.get("delta", "")
                if audio_b64:
                    self.on_audio(base64.b64decode(audio_b64))

            # ── Input transcript (user speech → text) ─────────────────
            elif etype == "conversation.item.input_audio_transcription.completed":
                input_transcript = event.get("transcript", "")

            # ── Output transcript (translated text) ───────────────────
            # gpt-realtime uses response.output_audio_transcript.done
            elif etype in ("response.output_audio_transcript.done", "response.audio_transcript.done"):
                output_transcript = event.get("transcript", "")
                self._emit_transcript(input_transcript, output_transcript)
                input_transcript = output_transcript = ""

            # ── Session ───────────────────────────────────────────────
            elif etype == "session.created":
                logger.info(f"[{self.mode}] session.created OK")

            elif etype == "session.updated":
                logger.info(f"[{self.mode}] session.updated OK")

            # ── Errors ────────────────────────────────────────────────
            elif etype == "error":
                err = event.get("error", event)
                logger.error(f"[{self.mode}] API error: {err}")

            # ── Ignored (verbose but normal) ──────────────────────────
            elif etype in (
                "input_audio_buffer.speech_started",
                "input_audio_buffer.speech_stopped",
                "input_audio_buffer.committed",
                "conversation.item.added",
                "conversation.item.done",
                "response.created",
                "response.done",
                "response.output_item.added",
                "response.output_item.done",
                "response.content_part.added",
                "response.content_part.done",
                "response.output_audio.done",
                "response.output_audio_transcript.delta",
                "response.audio_transcript.delta",
                "response.text.delta",
                "response.text.done",
                "rate_limits.updated",
            ):
                pass  # expected, no action needed

            else:
                logger.debug(f"[{self.mode}] unhandled event: {etype}")

    def _emit_transcript(self, original: str, translated: str):
        if self.on_transcript and (original or translated):
            direction = "↑ local" if self.mode == "mic" else "↓ remote"
            self.on_transcript(direction, original, translated)
