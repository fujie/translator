"""
Speaker pipeline: captures remote audio from BlackHole 2ch (input side),
sends it to GPT Realtime for language detection + conditional translation,
then plays the result through the real output device.

GPT system prompt handles language logic:
  English → translate to Japanese → play
  Japanese → pass through → play
"""
import asyncio
import logging
import threading
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from audio_devices import find_device_index
from audio_lock import audio_init_lock
from gpt_realtime import RealtimeSession, SAMPLE_RATE, CHANNELS

logger = logging.getLogger(__name__)

CHUNK_FRAMES = 1024


def _resolve(name: str, kind: str) -> Optional[int]:
    if not name:
        return None
    idx = find_device_index(name, kind)
    if idx is None:
        logger.error(f"Device not found ({kind}): '{name}'")
    return idx


class SpeakerPipeline:
    def __init__(
        self,
        api_key: str,
        capture_device_name: str,
        output_device_name: str,
        on_transcript: Optional[Callable[[str, str, str], None]] = None,
        model: str = "",
    ):
        self.api_key = api_key
        self.capture_device_name = capture_device_name
        self.output_device_name = output_device_name
        self.on_transcript = on_transcript
        self.model = model

        self._running = False
        self._session: Optional[RealtimeSession] = None
        self._capture_in: Optional[sd.InputStream] = None
        self._output_out: Optional[sd.OutputStream] = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._main_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._loop and not self._loop.is_closed() and self._main_task:
            self._loop.call_soon_threadsafe(self._main_task.cancel)
        if self._thread:
            self._thread.join(timeout=5)

    # ------------------------------------------------------------------

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._main_task = self._loop.create_task(self._async_run())
            self._loop.run_until_complete(self._main_task)
        except (asyncio.CancelledError, RuntimeError):
            pass
        finally:
            # Drain all remaining tasks (e.g. websockets keepalive) before closing
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

    async def _async_run(self):
        capture_idx = _resolve(self.capture_device_name, "input")
        output_idx  = _resolve(self.output_device_name,  "output")

        if capture_idx is None and self.capture_device_name:
            return  # named device not found

        kw = {"model": self.model} if self.model else {}
        self._session = RealtimeSession(
            api_key=self.api_key,
            mode="speaker",
            on_audio=self._on_translated_audio,
            on_transcript=self.on_transcript,
            **kw,
        )

        # Serialize stream *creation* (Pa_OpenStream → AudioUnitInitialize) AND
        # start inside a shared lock.  Retry up to 3× with a short backoff to
        # survive the CoreAudio AUHAL -10863 race when another pipeline's render
        # callback is already running.
        def _create_and_start_streams():
            import time
            last_exc = None
            for attempt in range(3):
                try:
                    with audio_init_lock:
                        self._output_out = sd.OutputStream(
                            device=output_idx,  # None = system default
                            samplerate=SAMPLE_RATE,
                            channels=CHANNELS,
                            dtype="int16",
                        )
                        self._capture_in = sd.InputStream(
                            device=capture_idx,
                            samplerate=SAMPLE_RATE,
                            channels=CHANNELS,
                            dtype="int16",
                            blocksize=CHUNK_FRAMES,
                            callback=self._audio_callback,
                        )
                        self._output_out.start()
                        self._capture_in.start()
                    return  # success
                except Exception as exc:
                    last_exc = exc
                    logger.warning(f"Speaker stream init attempt {attempt + 1} failed: {exc}")
                    time.sleep(0.4)
            raise last_exc  # all retries exhausted

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _create_and_start_streams)

        logger.info("Speaker pipeline started")
        await self._session.start()

    def _audio_callback(self, indata: np.ndarray, frames: int, time, status):
        if status:
            logger.warning(f"Speaker capture status: {status}")
        if self._session:
            self._session.send_audio(indata.tobytes())

    def _on_translated_audio(self, pcm16: bytes):
        if self._output_out and self._output_out.active:
            arr = np.frombuffer(pcm16, dtype=np.int16).reshape(-1, CHANNELS)
            self._output_out.write(arr)

    def cleanup(self):
        for s in [self._capture_in, self._output_out]:
            if s:
                try:
                    s.stop()
                    s.close()
                except Exception:
                    pass
