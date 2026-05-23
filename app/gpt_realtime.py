"""
GPT Realtime API client over WebSocket.

Supports two distinct API shapes:

gpt-realtime (and variants without "translate"):
  - Endpoint : wss://api.openai.com/v1/realtime
  - Session  : instructions + VAD config + output_modalities
  - Send     : input_audio_buffer.append
  - Receive  : response.output_audio.delta / response.output_audio_transcript.done

gpt-realtime-translate:
  - Endpoint : wss://api.openai.com/v1/realtime/translations
  - Session  : audio.output.language  (no instructions, no VAD)
  - Send     : session.input_audio_buffer.append
  - Receive  : session.output_audio.delta / session.output_transcript.delta
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

REALTIME_BASE_URL           = "wss://api.openai.com/v1/realtime"
REALTIME_TRANSLATE_BASE_URL = "wss://api.openai.com/v1/realtime/translations"
DEFAULT_MODEL = "gpt-realtime-translate"
SAMPLE_RATE = 24000
CHANNELS = 1

# ── System prompts (gpt-realtime only) ───────────────────────────────────────

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

MIC_INSTRUCTIONS     = MIC_SYSTEM_PROMPT
SPEAKER_INSTRUCTIONS = SPEAKER_SYSTEM_PROMPT

VAD_CONFIG = {
    "type": "server_vad",
    "threshold": 0.5,
    "prefix_padding_ms": 300,
    "silence_duration_ms": 500,
}

# ── Target language for gpt-realtime-translate ───────────────────────────────
# mic mode    : Japanese → English
# speaker mode: English → Japanese
# NOTE: audio.input.language and audio.input.turn_detection are NOT accepted
# by the /v1/realtime/translations endpoint — only audio.output.language works.
_TRANSLATE_TARGET_LANG = {
    "mic":     "en",
    "speaker": "ja",
}

# Sentence-ending punctuation used to flush transcript buffers
_SENTENCE_END = frozenset(".。!！?？\n")


def _is_translate_model(model: str) -> bool:
    """Return True when the model uses the translations endpoint."""
    return bool(model) and "translate" in model.lower()


class RealtimeSession:
    def __init__(
        self,
        api_key: str,
        mode: str,                    # "mic" | "speaker"
        on_audio: Callable[[bytes], None],
        on_transcript: Optional[Callable[[str, str, str], None]] = None,
        model: str = DEFAULT_MODEL,
    ):
        self.api_key      = api_key
        self.mode         = mode
        self.on_audio     = on_audio
        self.on_transcript = on_transcript
        self._ws: Optional[ClientConnection] = None
        self._running = False
        self._send_queue: asyncio.Queue = asyncio.Queue()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        self.model = model or DEFAULT_MODEL

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
        if _is_translate_model(self.model):
            url = f"{REALTIME_TRANSLATE_BASE_URL}?model={self.model}"
        else:
            url = f"{REALTIME_BASE_URL}?model={self.model}"

        headers = {"Authorization": f"Bearer {self.api_key}"}

        while self._running:
            try:
                async with websockets.connect(
                    url, additional_headers=headers, ssl=_SSL_CTX
                ) as ws:
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
        if _is_translate_model(self.model):
            # gpt-realtime-translate: only audio.output.language is accepted.
            # audio.input.language and audio.input.turn_detection are rejected
            # by the translations endpoint as unknown parameters.
            target_lang = _TRANSLATE_TARGET_LANG.get(self.mode, "en")
            session = {
                "audio": {
                    "output": {
                        "language": target_lang,
                    },
                }
            }
            logger.info(
                f"[{self.mode}] translate mode → target={target_lang}"
            )
        else:
            # gpt-realtime: full session config with instructions + VAD
            instructions = (
                MIC_SYSTEM_PROMPT if self.mode == "mic" else SPEAKER_SYSTEM_PROMPT
            )
            session = {
                "type": "realtime",
                "output_modalities": ["audio"],
                "instructions": instructions,
                "audio": {
                    "input":  {"turn_detection": VAD_CONFIG},
                    "output": {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE}},
                },
            }

        msg = {"type": "session.update", "session": session}
        logger.debug(
            f"[{self.mode}] session.update → {json.dumps(session, ensure_ascii=False)}"
        )
        await ws.send(json.dumps(msg))

    async def _send_loop(self, ws):
        # gpt-realtime-translate uses "session.input_audio_buffer.append"
        # gpt-realtime uses          "input_audio_buffer.append"
        append_event = (
            "session.input_audio_buffer.append"
            if _is_translate_model(self.model)
            else "input_audio_buffer.append"
        )

        while self._running:
            try:
                chunk = await asyncio.wait_for(self._send_queue.get(), timeout=0.1)
                encoded = base64.b64encode(chunk).decode()
                await ws.send(json.dumps({"type": append_event, "audio": encoded}))
            except asyncio.TimeoutError:
                continue

    async def _receive_loop(self, ws):
        if _is_translate_model(self.model):
            await self._receive_loop_translate(ws)
        else:
            await self._receive_loop_realtime(ws)

    # ------------------------------------------------------------------
    # Receive loop: gpt-realtime
    # ------------------------------------------------------------------

    async def _receive_loop_realtime(self, ws):
        input_transcript  = ""
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

            if etype in ("response.output_audio.delta", "response.audio.delta"):
                audio_b64 = event.get("delta", "")
                if audio_b64:
                    self.on_audio(base64.b64decode(audio_b64))

            elif etype == "conversation.item.input_audio_transcription.completed":
                input_transcript = event.get("transcript", "")

            elif etype in (
                "response.output_audio_transcript.done",
                "response.audio_transcript.done",
            ):
                output_transcript = event.get("transcript", "")
                self._emit_transcript(input_transcript, output_transcript)
                input_transcript = output_transcript = ""

            elif etype == "session.created":
                logger.info(f"[{self.mode}] session.created OK")

            elif etype == "session.updated":
                logger.info(f"[{self.mode}] session.updated OK")

            elif etype == "error":
                logger.error(f"[{self.mode}] API error: {event.get('error', event)}")

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
                pass

            else:
                logger.debug(f"[{self.mode}] unhandled event: {etype}")

    # ------------------------------------------------------------------
    # Receive loop: gpt-realtime-translate
    # ------------------------------------------------------------------

    async def _receive_loop_translate(self, ws):
        input_transcript  = ""
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

            # ── Translated audio output ───────────────────────────────
            if etype == "session.output_audio.delta":
                audio_b64 = event.get("delta", "")
                if audio_b64:
                    self.on_audio(base64.b64decode(audio_b64))

            # ── Translated text (output transcript) ───────────────────
            elif etype == "session.output_transcript.delta":
                delta = event.get("delta", "")
                output_transcript += delta
                # Flush eagerly on sentence boundary so log updates in real time
                if delta and delta[-1] in _SENTENCE_END:
                    self._emit_transcript(input_transcript, output_transcript.strip())
                    input_transcript = output_transcript = ""

            # ── Output transcript completed (flush remainder) ─────────
            elif etype == "session.output_transcript.done":
                done_text = event.get("transcript", "").strip() or output_transcript.strip()
                if done_text:
                    self._emit_transcript(input_transcript.strip(), done_text)
                input_transcript = output_transcript = ""

            # ── Source text (input transcript) ────────────────────────
            elif etype == "session.input_transcript.delta":
                input_transcript += event.get("delta", "")

            # ── Input transcript completed ────────────────────────────
            elif etype == "session.input_transcript.done":
                done_text = event.get("transcript", "").strip()
                if done_text:
                    input_transcript = done_text  # overwrite with authoritative value

            # ── Turn ended — audio output finished for this utterance ─
            elif etype == "session.output_audio.done":
                # Final flush if transcript events haven't already flushed
                if input_transcript or output_transcript:
                    self._emit_transcript(
                        input_transcript.strip(), output_transcript.strip()
                    )
                    input_transcript = output_transcript = ""

            # ── Session closed (flush any remaining transcript) ───────
            elif etype == "session.closed":
                logger.info(f"[{self.mode}] session.closed")
                if input_transcript or output_transcript:
                    self._emit_transcript(
                        input_transcript.strip(), output_transcript.strip()
                    )
                    input_transcript = output_transcript = ""

            elif etype in ("session.created", "session.updated"):
                logger.info(f"[{self.mode}] {etype} OK")

            elif etype == "error":
                logger.error(f"[{self.mode}] API error: {event.get('error', event)}")

            else:
                logger.debug(f"[{self.mode}] unhandled event: {etype}")

    # ------------------------------------------------------------------

    def _emit_transcript(self, original: str, translated: str):
        if self.on_transcript and (original or translated):
            direction = "↑ local" if self.mode == "mic" else "↓ remote"
            self.on_transcript(direction, original, translated)
