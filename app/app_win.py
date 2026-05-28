"""
Windows / Linux system-tray app.

pystray  → tray icon + menu
tkinter  → Settings window, Log window, dialogs (built-in, no extra deps)

Thread model
------------
  Main thread  : tkinter root.mainloop()
  Tray thread  : pystray icon.run_detached()  (daemon)
  Pipeline threads: MicPipeline / SpeakerPipeline (daemon)

Tray callbacks fire on the pystray thread; they use root.after(0, fn)
to dispatch safely to the tkinter main thread before touching any UI.

NOTE: Do NOT use this on macOS — pystray's AppKit backend conflicts with
tkinter's Tcl/Tk AppKit integration, causing a fatal GIL error.
On macOS, app_macos.py (rumps + PyObjC) is used instead.
"""
import json
import logging
import os
import sys
import tkinter as tk
from tkinter import messagebox, ttk, filedialog
from typing import Optional

import pystray
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(__file__))

from audio_devices import list_devices
from mic_pipeline import MicPipeline
from speaker_pipeline import SpeakerPipeline
from log_window import LogWindow
from context_window import ContextWindow
from live_transcript_window import LiveTranscriptWindow
from file_pipeline import FilePipeline, check_ffmpeg

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "settings.json")

_MODELS = [
    "gpt-realtime-translate",
    "gpt-realtime-2",
    "gpt-realtime",
    "gpt-realtime-mini",
]


# ── Config helpers ────────────────────────────────────────────────────

def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(cfg: dict):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ── Tray icon image ───────────────────────────────────────────────────

def _make_icon(mic_on: bool = False, spk_on: bool = False) -> Image.Image:
    """Draw a simple 64×64 icon; green when active, grey when idle."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if mic_on and spk_on:
        color = (50, 180, 50, 255)    # both active → green
    elif mic_on:
        color = (50, 130, 220, 255)   # mic only → blue
    elif spk_on:
        color = (220, 130, 50, 255)   # speaker only → orange
    else:
        color = (120, 120, 120, 255)  # idle → grey
    d.ellipse([6, 6, 58, 58], fill=color)
    # small inner circle for depth
    d.ellipse([20, 20, 44, 44], fill=(255, 255, 255, 60))
    return img


# ── Settings window ───────────────────────────────────────────────────

class SettingsWindow:
    """Modal settings form built with tkinter."""

    def __init__(
        self,
        root: tk.Tk,
        config: dict,
        devices: list[dict],
        on_save,
    ):
        self._root = root
        self._config = config
        self._on_save = on_save
        self._build(devices)

    def _build(self, devices: list[dict]):
        win = tk.Toplevel(self._root)
        win.title("Realtime Translate – Settings")
        win.resizable(False, False)
        win.grab_set()   # modal

        input_names  = ["(default)"] + [
            d["name"] for d in devices if d["max_input_channels"] > 0
        ]
        output_names = ["(default)"] + [
            d["name"] for d in devices if d["max_output_channels"] > 0
        ]

        # (label, config_key, options_list_or_None, widget_type, default_when_empty)
        rows = [
            ("OpenAI API Key",            "openai_api_key",        None,         "password", ""),
            ("Mic Model",                 "realtime_model",        _MODELS,      "combo",    "gpt-realtime-translate"),
            ("Speaker Model",             "speaker_model",         _MODELS,      "combo",    "gpt-realtime-2"),
            ("Real Microphone",           "input_device",          input_names,  "combo",    "(default)"),
            ("Real Speaker",              "output_device",         output_names, "combo",    "(default)"),
            ("Virtual Mic A – pass-thru", "mic_passthrough_device",output_names, "combo",    "(default)"),
            ("Virtual Mic B – translated","mic_translated_device", output_names, "combo",    "(default)"),
            ("Speaker Capture device",    "speaker_capture_device",input_names,  "combo",    "(default)"),
        ]

        frame = ttk.Frame(win, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        _vars: dict[str, tk.StringVar] = {}

        for i, (label, key, options, kind, default_val) in enumerate(rows):
            ttk.Label(frame, text=label + ":").grid(
                row=i, column=0, sticky=tk.W, pady=5, padx=(0, 10)
            )
            var = tk.StringVar()
            if kind == "password":
                current = self._config.get(key, "")
                var.set(current or "")
                entry = ttk.Entry(frame, textvariable=var, width=42, show="•")
                entry.grid(row=i, column=1, sticky=tk.EW)
            else:
                config_val = self._config.get(key, "") or ""
                current = config_val if config_val else default_val
                cb = ttk.Combobox(
                    frame, textvariable=var,
                    values=options, width=40, state="readonly",
                )
                cb.set(current if current in options else options[0])
                cb.grid(row=i, column=1, sticky=tk.EW)
            _vars[key] = var

        frame.columnconfigure(1, weight=1)

        # ── Buttons ──────────────────────────────────────────────────
        btn_frame = ttk.Frame(win, padding=(16, 0, 16, 14))
        btn_frame.pack(fill=tk.X)

        def _save():
            new_cfg = dict(self._config)
            for k, v in _vars.items():
                val = v.get().strip()
                new_cfg[k] = "" if val == "(default)" else val
            save_config(new_cfg)
            self._on_save(new_cfg)
            win.destroy()

        ttk.Button(btn_frame, text="Cancel",
                   command=win.destroy).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn_frame, text="Save",
                   command=_save).pack(side=tk.RIGHT)

        win.update_idletasks()
        win.minsize(win.winfo_width(), win.winfo_height())


# ── Main app ──────────────────────────────────────────────────────────

class TranslateApp:
    def __init__(self):
        self.config = load_config()

        self._mic_active  = False
        self._spk_active  = False
        self._file_active = False
        self._mic_pipeline:  Optional[MicPipeline]   = None
        self._spk_pipeline:  Optional[SpeakerPipeline] = None
        self._file_pipeline: Optional[FilePipeline]  = None

        # Hidden tkinter root — all UI lives on this thread
        self._root = tk.Tk()
        self._root.withdraw()
        self._root.title("Realtime Translate")

        self.log_window = LogWindow(
            self._root,
            max_entries=self.config.get("log_max_entries", 200),
        )
        self.live_transcript = LiveTranscriptWindow(
            self._root,
            self.config,
            self._on_mix_change,
        )

        # Poll log + transcript queues 5× per second from main thread
        self._root.after(200, self._poll_log)

        # Build tray icon (pystray runs in its own daemon thread)
        self._icon = pystray.Icon(
            "Realtime Translate",
            _make_icon(),
            "Realtime Translate",
            menu=self._build_menu(),
        )


    # ------------------------------------------------------------------
    # Tray menu
    # ------------------------------------------------------------------

    def _build_menu(self) -> pystray.Menu:
        mic_label = "🎙 Mic: ON " if self._mic_active else "🎙 Mic: OFF"
        spk_label = "🔊 Speaker: ON " if self._spk_active else "🔊 Speaker: OFF"
        file_mic_label = "⏹ Stop File Test" if self._file_active else "📂 Test File – Mic…"
        file_spk_label = "⏹ Stop File Test" if self._file_active else "📂 Test File – Speaker…"
        return pystray.Menu(
            pystray.MenuItem(mic_label,              self._cb_toggle_mic),
            pystray.MenuItem(spk_label,              self._cb_toggle_spk),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(file_mic_label,         self._cb_test_file_mic),
            pystray.MenuItem(file_spk_label,         self._cb_test_file_spk),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Translation Log…",     self._cb_open_log),
            pystray.MenuItem("Live Transcript…",     self._cb_open_live_transcript),
            pystray.MenuItem("Translation Context…", self._cb_open_context),
            pystray.MenuItem("Settings…",            self._cb_open_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit",                 self._cb_quit),
        )

    def _refresh_tray(self):
        """Update icon image + menu labels. Must be called from main thread."""
        self._icon.icon = _make_icon(self._mic_active, self._spk_active)
        self._icon.menu = self._build_menu()

    # ------------------------------------------------------------------
    # Tray callbacks — pystray thread → dispatch to tkinter main thread
    # ------------------------------------------------------------------

    def _cb_toggle_mic(self, *_):
        self._root.after(0, self._toggle_mic)

    def _cb_toggle_spk(self, *_):
        self._root.after(0, self._toggle_spk)

    def _cb_open_log(self, *_):
        self._root.after(0, self.log_window.show)

    def _cb_open_live_transcript(self, *_):
        self._root.after(0, self.live_transcript.show)

    def _cb_test_file_mic(self, *_):
        self._root.after(0, lambda: self._handle_test_file("mic"))

    def _cb_test_file_spk(self, *_):
        self._root.after(0, lambda: self._handle_test_file("speaker"))

    def _cb_open_context(self, *_):
        self._root.after(0, self._open_context)

    def _cb_open_settings(self, *_):
        self._root.after(0, self._open_settings)

    def _cb_quit(self, *_):
        self._root.after(0, self._quit)

    # ------------------------------------------------------------------
    # Pipeline control (main thread)
    # ------------------------------------------------------------------

    def _toggle_mic(self):
        if self._mic_active:
            self._stop_mic()
        else:
            self._start_mic()

    def _toggle_spk(self):
        if self._spk_active:
            self._stop_spk()
        else:
            self._start_spk()

    def _start_mic(self):
        if not self._require_api_key():
            return
        self._mic_pipeline = MicPipeline(
            api_key=self.config["openai_api_key"],
            input_device_name=self.config.get("input_device", ""),
            passthrough_device_name=self.config.get("mic_passthrough_device", ""),
            translated_device_name=self.config.get("mic_translated_device", ""),
            on_transcript=self._on_transcript,
            on_transcript_delta=self.live_transcript.add_delta,
            model=self.config.get("realtime_model", ""),
            context=self.config.get("context_text", ""),
        )
        self._mic_pipeline.start()
        self._mic_active = True
        self._refresh_tray()

    def _stop_mic(self):
        if self._mic_pipeline:
            self._mic_pipeline.stop()
            self._mic_pipeline.cleanup()
            self._mic_pipeline = None
        self._mic_active = False
        self._refresh_tray()

    def _start_spk(self):
        if not self._require_api_key():
            return
        spk_model = (
            self.config.get("speaker_model")
            or self.config.get("realtime_model", "")
        )
        mix_ratio = self.config.get("speaker_mix_ratio", 100) / 100.0
        self._spk_pipeline = SpeakerPipeline(
            api_key=self.config["openai_api_key"],
            capture_device_name=self.config.get("speaker_capture_device", ""),
            output_device_name=self.config.get("output_device", ""),
            on_transcript=self._on_transcript,
            on_transcript_delta=self.live_transcript.add_delta,
            model=spk_model,
            context=self.config.get("context_text", ""),
            mix_ratio=mix_ratio,
        )
        self._spk_pipeline.start()
        self._spk_active = True
        self._refresh_tray()

    def _stop_spk(self):
        if self._spk_pipeline:
            self._spk_pipeline.stop()
            self._spk_pipeline.cleanup()
            self._spk_pipeline = None
        self._spk_active = False
        self._refresh_tray()

    def _on_transcript(self, direction: str, src: str, tgt: str):
        """Route completed utterances to both the log window and the live transcript."""
        self.log_window.add_entry(direction, src, tgt)
        self.live_transcript.commit(direction, src, tgt)

    def _on_mix_change(self, ratio: float):
        """Called by the live transcript mix slider; updates the active pipeline and persists."""
        if self._spk_pipeline:
            self._spk_pipeline.set_mix_ratio(ratio)
        self.config["speaker_mix_ratio"] = int(round(ratio * 100))
        save_config(self.config)

    # ── File-based translation test ────────────────────────────────────

    def _handle_test_file(self, mode: str):
        """Called on main thread when a file-test menu item is clicked."""
        if self._file_active:
            self._stop_file_test()
            return
        mode_label = "マイク (JP→EN)" if mode == "mic" else "スピーカー (EN→JP)"
        path = filedialog.askopenfilename(
            title=f"翻訳テスト – {mode_label} モード",
            filetypes=[
                ("Audio/Video", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus "
                                "*.mp4 *.mov *.mkv *.webm *.avi"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        self._start_file_test(path, mode)

    def _start_file_test(self, path: str, mode: str):
        if not self._require_api_key():
            return
        model = (
            self.config.get("realtime_model", "") if mode == "mic"
            else (self.config.get("speaker_model") or self.config.get("realtime_model", ""))
        )
        self._file_pipeline = FilePipeline(
            api_key=self.config["openai_api_key"],
            file_path=path,
            mode=mode,
            output_device_name=self.config.get("output_device", ""),
            on_transcript=self._on_transcript,
            on_transcript_delta=self.live_transcript.add_delta,
            model=model,
            context=self.config.get("context_text", ""),
            on_complete=self._on_file_test_complete,
        )
        try:
            self._file_pipeline.start()
        except RuntimeError as e:
            from tkinter import messagebox
            messagebox.showerror("ffmpeg が必要です", str(e))
            self._file_pipeline = None
            return
        self._file_active = True
        self._refresh_tray()
        self.live_transcript.show()

    def _stop_file_test(self):
        if self._file_pipeline:
            self._file_pipeline.stop()
            self._file_pipeline.cleanup()
            self._file_pipeline = None
        self._file_active = False
        self._refresh_tray()

    def _on_file_test_complete(self):
        # Called from pipeline thread — dispatch to tkinter main thread
        self._root.after(0, self._stop_file_test)

    def _require_api_key(self) -> bool:
        if not self.config.get("openai_api_key"):
            messagebox.showerror(
                "API Key Required",
                "OpenAI API キーが設定されていません。\n"
                "「Settings…」から設定してください。",
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Translation Context
    # ------------------------------------------------------------------

    def _open_context(self):
        ContextWindow(self._root, self.config, self._on_context_saved).show()

    def _on_context_saved(self, new_cfg: dict):
        self.config = new_cfg
        save_config(new_cfg)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _open_settings(self):
        SettingsWindow(
            self._root,
            self.config,
            list_devices(),
            self._on_settings_saved,
        )

    def _on_settings_saved(self, new_cfg: dict):
        self.config = new_cfg
        was_mic = self._mic_active
        was_spk = self._spk_active
        if was_mic:
            self._stop_mic()
        if was_spk:
            self._stop_spk()
        if was_mic:
            self._start_mic()
        if was_spk:
            self._start_spk()

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _poll_log(self):
        self.log_window.poll()
        self.live_transcript.poll()
        self._root.after(200, self._poll_log)

    def _quit(self):
        self._stop_mic()
        self._stop_spk()
        self._icon.stop()
        self._root.quit()

    def run(self):
        if not self.config.get("openai_api_key"):
            self._root.after(
                200,
                lambda: messagebox.showinfo(
                    "Setup Required",
                    "config/settings.json に OpenAI API キーを設定してください。\n\n"
                    "トレイアイコンの「Settings…」から設定できます。",
                ),
            )
        self._icon.run_detached()   # pystray in background thread
        self._root.mainloop()       # tkinter on main thread
