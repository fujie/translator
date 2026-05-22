"""
Mic pipeline: reads from the physical input device and simultaneously:
  - Writes raw PCM16 audio to BlackHole 2ch  (pass-through, Virtual Mic A)
  - Sends audio to GPT Realtime for JP→EN translation,
    then writes translated PCM16 to BlackHole 16ch (Virtual Mic B)
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

CHUNK_FRAMES = 1024  # ~43ms at 24 kHz


def _resolve(name: str, kind: str) -> Optional[int]:
    """Return device index, or None to use sounddevice default."""
    if not name:
        return None
    idx = find_device_index(name, kind)
    if idx is None:
        logger.error(f"Device not found ({kind}): '{name}'")
    return idx


class MicPipeline:
    def __init__(
        self,
        api_key: str,
        input_device_name: str,
        passthrough_device_name: str,
        translated_device_name: str,
        on_transcript: Optional[Callable[[str, str, str], None]] = None,
        model: str = "",
    ):
        self.api_key = api_key
        self.input_device_name = input_device_name
        self.passthrough_device_name = passthrough_device_name
        self.translated_device_name = translated_device_name
        self.on_transcript = on_transcript
        self.model = model

        self._running = False
        self._session: Optional[RealtimeSession] = None
        self._passthrough_out: Optional[sd.OutputStream] = None
        self._translated_out: Optional[sd.OutputStream] = None
        self._input_stream: Optional[sd.InputStream] = None
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
        input_idx       = _resolve(self.input_device_name,       "input")
        passthrough_idx = _resolve(self.passthrough_device_name, "output")
        translated_idx  = _resolve(self.translated_device_name,  "output")

        # If a device name is given but not found, abort
        if passthrough_idx is None and self.passthrough_device_name:
            logger.error(f"Passthrough device not found: '{self.passthrough_device_name}'")
            return
        if translated_idx is None and self.translated_device_name:
            logger.error(f"Translated device not found: '{self.translated_device_name}'")
            return

        kw = {"model": self.model} if self.model else {}
        self._session = RealtimeSession(
            api_key=self.api_key,
            mode="mic",
            on_audio=self._on_translated_audio,
            on_transcript=self.on_transcript,
            **kw,
        )

        # Serialize stream *creation* (Pa_OpenStream → AudioUnitInitialize) AND
        # start inside a shared lock.  Retry up to 3× with a short backoff to
        # survive the CoreAudio AUHAL -10863 race when another pipeline's render
        # callback is already running.
        # passthrough_out is optional: skip entirely when no device is configured
        # to avoid opening a stream on the default output (which causes echo/feedback).
        def _create_and_start_streams():
            import time
            last_exc = None
            for attempt in range(3):
                try:
                    with audio_init_lock:
                        if self.passthrough_device_name:
                            self._passthrough_out = sd.OutputStream(
                                device=passthrough_idx,
                                samplerate=SAMPLE_RATE,
                                channels=CHANNELS,
                                dtype="int16",
                            )
                        if self.translated_device_name:
                            self._translated_out = sd.OutputStream(
                                device=translated_idx,
                                samplerate=SAMPLE_RATE,
                                channels=CHANNELS,
                                dtype="int16",
                            )
                        self._input_stream = sd.InputStream(
                            device=input_idx,  # None = system default
                            samplerate=SAMPLE_RATE,
                            channels=CHANNELS,
                            dtype="int16",
                            blocksize=CHUNK_FRAMES,
                            callback=self._audio_callback,
                        )
                        if self._passthrough_out:
                            self._passthrough_out.start()
                        if self._translated_out:
                            self._translated_out.start()
                        self._input_stream.start()
                    return  # success
                except Exception as exc:
                    last_exc = exc
                    logger.warning(f"Mic stream init attempt {attempt + 1} failed: {exc}")
                    time.sleep(0.4)
            raise last_exc  # all retries exhausted

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _create_and_start_streams)

        logger.info("Mic pipeline started")
        await self._session.start()

    def _audio_callback(self, indata: np.ndarray, frames: int, time, status):
        if status:
            logger.warning(f"Mic input status: {status}")
        # Pass-through → BlackHole 2ch
        if self._passthrough_out and self._passthrough_out.active:
            self._passthrough_out.write(indata)
        # Send to GPT for translation
        if self._session:
            self._session.send_audio(indata.tobytes())

    def _on_translated_audio(self, pcm16: bytes):
        """Write GPT-translated audio to BlackHole 16ch."""
        if self._translated_out and self._translated_out.active:
            arr = np.frombuffer(pcm16, dtype=np.int16).reshape(-1, CHANNELS)
            self._translated_out.write(arr)

    def cleanup(self):
        for s in [self._input_stream, self._passthrough_out, self._translated_out]:
            if s:
                try:
                    s.stop()
                    s.close()
                except Exception:
                    pass
