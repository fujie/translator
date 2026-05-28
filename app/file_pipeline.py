"""
File pipeline: reads any audio/video file via ffmpeg and feeds PCM audio
to GPT Realtime for translation testing — without needing a physical mic
or virtual audio device.

Requires ffmpeg in PATH.
Supported: MP3, WAV, M4A, AAC, FLAC, OGG, OPUS, MP4, MOV, MKV, WebM, …

mode="mic"     → file treated as local-speaker input  (e.g. JP→EN)
mode="speaker" → file treated as remote-speaker input (e.g. EN→JP)

Translated audio plays through output_device_name (or system default).
Transcripts flow through the same callbacks as the live pipelines, so they
appear in the Live Transcript and Translation Log windows.

on_complete() is called from the pipeline thread when the file has been
fully processed; callers must dispatch UI updates to their own main thread.
"""
import asyncio
import logging
import shutil
import threading
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from audio_devices import find_device_index
from audio_lock import audio_init_lock
from gpt_realtime import RealtimeSession, SAMPLE_RATE, CHANNELS

logger = logging.getLogger(__name__)

# 85 ms per chunk — larger than the live pipelines to reduce API round-trips
CHUNK_FRAMES = 2048

# Trailing silence appended after EOF so the VAD fires end-of-speech
_TRAILING_SILENCE_SEC = 1.5

# Extra wait after silence for in-flight translations to arrive
_DRAIN_WAIT_SEC = 5.0


def check_ffmpeg() -> bool:
    """Return True if ffmpeg is available in PATH."""
    return shutil.which("ffmpeg") is not None


def _resolve(name: str, kind: str) -> Optional[int]:
    if not name:
        return None
    idx = find_device_index(name, kind)
    if idx is None:
        logger.error(f"Device not found ({kind}): '{name}'")
    return idx


class FilePipeline:
    """
    Streams an audio/video file through GPT Realtime for translation testing.
    """

    def __init__(
        self,
        api_key: str,
        file_path: str,
        mode: str = "mic",
        output_device_name: str = "",
        on_transcript: Optional[Callable[[str, str, str], None]] = None,
        on_transcript_delta: Optional[Callable[[str, str, str], None]] = None,
        model: str = "",
        context: str = "",
        on_complete: Optional[Callable[[], None]] = None,
    ):
        self.api_key             = api_key
        self.file_path           = file_path
        self.mode                = mode
        self.output_device_name  = output_device_name
        self.on_transcript       = on_transcript
        self.on_transcript_delta = on_transcript_delta
        self.model               = model
        self.context             = context
        self.on_complete         = on_complete

        self._running   = False
        self._session:    Optional[RealtimeSession]    = None
        self._output_out: Optional[sd.OutputStream]    = None
        self._thread:     Optional[threading.Thread]   = None
        self._loop:       Optional[asyncio.AbstractEventLoop] = None
        self._main_task:  Optional[asyncio.Task]       = None

    # ── Public ───────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        if not check_ffmpeg():
            raise RuntimeError(
                "ffmpeg が PATH に見つかりません。\n"
                "  macOS : brew install ffmpeg\n"
                "  Windows: choco install ffmpeg"
            )
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._loop and not self._loop.is_closed() and self._main_task:
            self._loop.call_soon_threadsafe(self._main_task.cancel)
        if self._thread:
            self._thread.join(timeout=8)

    def cleanup(self):
        if self._output_out:
            try:
                self._output_out.stop()
                self._output_out.close()
            except Exception:
                pass
            self._output_out = None

    # ── Thread entry ─────────────────────────────────────────────────────

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._main_task = self._loop.create_task(self._async_run())
            self._loop.run_until_complete(self._main_task)
        except (asyncio.CancelledError, RuntimeError):
            pass
        finally:
            try:
                pending = asyncio.all_tasks(self._loop)
                if pending:
                    for t in pending:
                        t.cancel()
                    self._loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                pass
            self._loop.close()

    # ── Async core ───────────────────────────────────────────────────────

    async def _async_run(self):
        output_idx = _resolve(self.output_device_name, "output")

        kw: dict = {}
        if self.model:
            kw["model"] = self.model
        if self.context:
            kw["context"] = self.context
        if self.on_transcript_delta:
            kw["on_transcript_delta"] = self.on_transcript_delta

        self._session = RealtimeSession(
            api_key=self.api_key,
            mode=self.mode,
            on_audio=self._on_translated_audio,
            on_transcript=self.on_transcript,
            **kw,
        )

        # Open output stream for translated-audio playback
        loop = asyncio.get_running_loop()

        def _open_output():
            with audio_init_lock:
                self._output_out = sd.OutputStream(
                    device=output_idx,
                    samplerate=SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="int16",
                )
                self._output_out.start()

        await loop.run_in_executor(None, _open_output)

        # Connect WebSocket in background
        session_task = asyncio.create_task(self._session.start())

        # Wait for WS handshake + session.update round-trip
        await asyncio.sleep(1.5)

        if self._running:
            await self._stream_file()

        # Tear down cleanly
        await self._session.stop()
        session_task.cancel()
        try:
            await session_task
        except (asyncio.CancelledError, Exception):
            pass

        logger.info(f"[file/{self.mode}] pipeline finished")

        if self.on_complete:
            self.on_complete()

    # ── ffmpeg streaming ─────────────────────────────────────────────────

    async def _stream_file(self):
        """Decode the file with ffmpeg and send PCM chunks at real-time pace."""
        cmd = [
            "ffmpeg", "-loglevel", "error",
            "-i", self.file_path,
            "-f",  "s16le",          # raw signed-16-bit little-endian
            "-ar", str(SAMPLE_RATE),
            "-ac", str(CHANNELS),
            "pipe:1",
        ]
        bytes_per_chunk  = CHUNK_FRAMES * CHANNELS * 2   # int16 → 2 bytes
        chunk_duration_s = CHUNK_FRAMES / SAMPLE_RATE    # seconds

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        logger.info(f"[file/{self.mode}] streaming '{self.file_path}'")

        try:
            while self._running:
                data = await proc.stdout.readexactly(bytes_per_chunk)
                self._session.send_audio(data)
                # Pace at ~real-time so the API's VAD can segment naturally
                await asyncio.sleep(chunk_duration_s * 0.9)

        except asyncio.IncompleteReadError:
            # Normal EOF — append trailing silence so VAD fires end-of-speech
            silence        = bytes(bytes_per_chunk)
            silence_chunks = int(SAMPLE_RATE * _TRAILING_SILENCE_SEC / CHUNK_FRAMES) + 1
            for _ in range(silence_chunks):
                if not self._running:
                    break
                self._session.send_audio(silence)
                await asyncio.sleep(chunk_duration_s * 0.9)
            # Wait for any in-flight translations to arrive
            if self._running:
                logger.info(f"[file/{self.mode}] EOF — waiting {_DRAIN_WAIT_SEC}s for final transcripts")
                await asyncio.sleep(_DRAIN_WAIT_SEC)

        except asyncio.CancelledError:
            try:
                proc.kill()
            except Exception:
                pass
            raise

        finally:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass

        logger.info(f"[file/{self.mode}] done streaming '{self.file_path}'")

    # ── Audio output ─────────────────────────────────────────────────────

    def _on_translated_audio(self, pcm16: bytes):
        if self._output_out and self._output_out.active:
            arr = np.frombuffer(pcm16, dtype=np.int16).reshape(-1, CHANNELS)
            self._output_out.write(arr)
