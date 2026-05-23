"""
Entry point — selects the platform-appropriate UI implementation.

  macOS   → app_macos.py  (rumps + PyObjC / AppKit)
  Windows → app_win.py    (pystray + tkinter)
  Linux   → app_win.py    (pystray + tkinter)

Both implementations share the same audio pipeline code
(gpt_realtime, mic_pipeline, speaker_pipeline, audio_devices, audio_lock).
"""
import platform
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

if platform.system() == "Darwin":
    from app_macos import TranslateApp
else:
    from app_win import TranslateApp

if __name__ == "__main__":
    TranslateApp().run()
