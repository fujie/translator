"""
macOS menu bar app — PyObjC / AppKit throughout (no tkinter).

All UI is created on the AppKit main thread.
Pipeline threads communicate via thread-safe queues in LogWindow.
"""
import json
import logging
import os
import sys

import objc
import rumps
from Foundation import NSObject, NSMakeRect
from AppKit import (
    NSWindow, NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSBackingStoreBuffered,
    NSTextField, NSSecureTextField, NSPopUpButton, NSButton,
    NSFont, NSApplication, NSColor,
)

sys.path.insert(0, os.path.dirname(__file__))

from audio_devices import list_devices
from mic_pipeline import MicPipeline
from speaker_pipeline import SpeakerPipeline
from log_window_macos import LogWindow

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "settings.json")

_SETTINGS_STYLE = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
_W, _H = 540, 476  # +46 px for the extra Speaker Model row

_MODELS = [
    "gpt-realtime-translate",
    "gpt-realtime-2",
    "gpt-realtime",
    "gpt-realtime-mini",
]


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(cfg: dict):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ------------------------------------------------------------------
# Settings window (PyObjC — runs on the AppKit main thread)
# ------------------------------------------------------------------

class _SettingsController(NSObject):
    """Manages the settings form window. Kept alive as a TranslateApp attribute."""

    def initWithConfig_devices_onSave_(self, config, devices, on_save):
        self = objc.super(_SettingsController, self).init()
        if self is None:
            return None
        self._config = config
        self._devices = devices
        self._on_save = on_save
        self._window = None
        self._api_field = None
        self._model_popup = None
        self._speaker_model_popup = None
        self._input_popup = None
        self._output_popup = None
        self._pt_popup = None
        self._tr_popup = None
        self._sp_popup = None
        self._build()
        return self

    def _build(self):
        self._window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(300, 300, _W, _H), _SETTINGS_STYLE, NSBackingStoreBuffered, False
        )
        self._window.setTitle_("Realtime Translate – Settings")
        self._window.setReleasedWhenClosed_(False)

        content = self._window.contentView()
        row = _H - 50

        def label(text, y):
            lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(16, y, 220, 20))
            lbl.setStringValue_(text)
            lbl.setEditable_(False)
            lbl.setBordered_(False)
            lbl.setDrawsBackground_(False)
            lbl.setSelectable_(False)
            content.addSubview_(lbl)

        def secure_field(current, y):
            fld = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(230, y, 290, 22))
            fld.setStringValue_(current or "")
            content.addSubview_(fld)
            return fld

        def popup(options, current, y):
            pb = NSPopUpButton.alloc().initWithFrame_(NSMakeRect(230, y - 2, 295, 26))
            for opt in options:
                pb.addItemWithTitle_(opt)
            sel = current if current in options else options[0]
            pb.selectItemWithTitle_(sel)
            content.addSubview_(pb)
            return pb

        input_names = ["(default)"] + [
            d["name"] for d in self._devices if d["max_input_channels"] > 0
        ]
        output_names = ["(default)"] + [
            d["name"] for d in self._devices if d["max_output_channels"] > 0
        ]

        cfg = self._config
        STEP = 46

        label("OpenAI API Key:", row)
        self._api_field = secure_field(cfg.get("openai_api_key", ""), row)
        row -= STEP

        label("Mic Model:", row)
        self._model_popup = popup(
            _MODELS, cfg.get("realtime_model", "gpt-realtime-translate"), row
        )
        row -= STEP

        label("Speaker Model:", row)
        self._speaker_model_popup = popup(
            _MODELS, cfg.get("speaker_model", "gpt-realtime-2"), row
        )
        row -= STEP

        label("Real Microphone:", row)
        self._input_popup = popup(
            input_names, cfg.get("input_device") or "(default)", row
        )
        row -= STEP

        label("Real Speaker:", row)
        self._output_popup = popup(
            output_names, cfg.get("output_device") or "(default)", row
        )
        row -= STEP

        label("Virtual Mic A – pass-through:", row)
        self._pt_popup = popup(
            output_names, cfg.get("mic_passthrough_device") or "(default)", row
        )
        row -= STEP

        label("Virtual Mic B – JP→EN:", row)
        self._tr_popup = popup(
            output_names, cfg.get("mic_translated_device", "BlackHole 16ch"), row
        )
        row -= STEP

        label("Speaker Capture device:", row)
        self._sp_popup = popup(
            input_names, cfg.get("speaker_capture_device", "BlackHole 2ch"), row
        )
        row -= STEP + 4

        cancel_btn = NSButton.alloc().initWithFrame_(NSMakeRect(_W - 196, 12, 80, 28))
        cancel_btn.setTitle_("Cancel")
        cancel_btn.setBezelStyle_(1)
        cancel_btn.setTarget_(self)
        cancel_btn.setAction_("cancelSettings:")
        content.addSubview_(cancel_btn)

        save_btn = NSButton.alloc().initWithFrame_(NSMakeRect(_W - 108, 12, 90, 28))
        save_btn.setTitle_("Save")
        save_btn.setBezelStyle_(1)
        save_btn.setKeyEquivalent_("\r")
        save_btn.setTarget_(self)
        save_btn.setAction_("saveSettings:")
        content.addSubview_(save_btn)

        self._window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def saveSettings_(self, sender):
        new_cfg = dict(self._config)
        new_cfg["openai_api_key"]        = str(self._api_field.stringValue()).strip()
        new_cfg["realtime_model"]         = str(self._model_popup.titleOfSelectedItem())
        new_cfg["speaker_model"]          = str(self._speaker_model_popup.titleOfSelectedItem())
        new_cfg["input_device"]           = self._popup_val(self._input_popup)
        new_cfg["output_device"]          = self._popup_val(self._output_popup)
        new_cfg["mic_passthrough_device"] = self._popup_val(self._pt_popup)
        new_cfg["mic_translated_device"]  = self._popup_val(self._tr_popup)
        new_cfg["speaker_capture_device"] = self._popup_val(self._sp_popup)
        save_config(new_cfg)
        self._on_save(new_cfg)
        self._window.close()

    def cancelSettings_(self, sender):
        self._window.close()

    @objc.python_method
    def _popup_val(self, pb) -> str:
        val = str(pb.titleOfSelectedItem())
        return "" if val == "(default)" else val


# ------------------------------------------------------------------
# Menu bar app
# ------------------------------------------------------------------

class TranslateApp(rumps.App):
    def __init__(self):
        super().__init__("🌐", quit_button=None)

        self.config = load_config()
        self.log_window = LogWindow(max_entries=self.config.get("log_max_entries", 200))

        self._mic_pipeline: MicPipeline | None = None
        self._speaker_pipeline: SpeakerPipeline | None = None
        self._mic_active = False
        self._speaker_active = False
        self._settings_ctrl: _SettingsController | None = None

        self.mic_item     = rumps.MenuItem("🎙 Mic: OFF",     callback=self.toggle_mic)
        self.speaker_item = rumps.MenuItem("🔊 Speaker: OFF", callback=self.toggle_speaker)

        self.menu = [
            self.mic_item,
            self.speaker_item,
            None,
            rumps.MenuItem("Translation Log…", callback=self.open_log),
            rumps.MenuItem("Settings…",        callback=self.open_settings),
            None,
            rumps.MenuItem("Quit",             callback=self.quit_app),
        ]

        self._poll_timer = rumps.Timer(self._poll_log, 0.2)
        self._poll_timer.start()

        if not self.config.get("openai_api_key"):
            rumps.alert(
                title="Setup Required",
                message="Please open Settings… and enter your OpenAI API key.",
            )

    # ------------------------------------------------------------------
    # Pipeline control
    # ------------------------------------------------------------------

    def toggle_mic(self, _):
        if self._mic_active:
            self._stop_mic()
        else:
            self._start_mic()

    def toggle_speaker(self, _):
        if self._speaker_active:
            self._stop_speaker()
        else:
            self._start_speaker()

    def _start_mic(self):
        if not self._require_api_key():
            return
        self._mic_pipeline = MicPipeline(
            api_key=self.config["openai_api_key"],
            input_device_name=self.config.get("input_device") or "",
            passthrough_device_name=self.config.get("mic_passthrough_device") or "",
            translated_device_name=self.config.get("mic_translated_device", "BlackHole 16ch"),
            on_transcript=self.log_window.add_entry,
            model=self.config.get("realtime_model", ""),
        )
        self._mic_pipeline.start()
        self._mic_active = True
        self.mic_item.title = "🎙 Mic: ON"
        self._update_icon()

    def _stop_mic(self):
        if self._mic_pipeline:
            self._mic_pipeline.stop()
            self._mic_pipeline.cleanup()
            self._mic_pipeline = None
        self._mic_active = False
        self.mic_item.title = "🎙 Mic: OFF"
        self._update_icon()

    def _start_speaker(self):
        if not self._require_api_key():
            return
        spk_model = (
            self.config.get("speaker_model")
            or self.config.get("realtime_model", "")
        )
        self._speaker_pipeline = SpeakerPipeline(
            api_key=self.config["openai_api_key"],
            capture_device_name=self.config.get("speaker_capture_device", "BlackHole 2ch"),
            output_device_name=self.config.get("output_device") or "",
            on_transcript=self.log_window.add_entry,
            model=spk_model,
        )
        self._speaker_pipeline.start()
        self._speaker_active = True
        self.speaker_item.title = "🔊 Speaker: ON"
        self._update_icon()

    def _stop_speaker(self):
        if self._speaker_pipeline:
            self._speaker_pipeline.stop()
            self._speaker_pipeline.cleanup()
            self._speaker_pipeline = None
        self._speaker_active = False
        self.speaker_item.title = "🔊 Speaker: OFF"
        self._update_icon()

    def _require_api_key(self) -> bool:
        if not self.config.get("openai_api_key"):
            rumps.alert("API key not set. Open Settings… to configure.")
            return False
        return True

    def _update_icon(self):
        if self._mic_active and self._speaker_active:
            self.title = "🌐●"
        elif self._mic_active:
            self.title = "🎙●"
        elif self._speaker_active:
            self.title = "🔊●"
        else:
            self.title = "🌐"

    def open_log(self, _):
        self.log_window.show()

    def open_settings(self, _):
        def on_save(new_cfg):
            self.config = new_cfg
            was_mic     = self._mic_active
            was_speaker = self._speaker_active
            if was_mic:
                self._stop_mic()
            if was_speaker:
                self._stop_speaker()
            if was_mic:
                self._start_mic()
            if was_speaker:
                self._start_speaker()

        self._settings_ctrl = _SettingsController.alloc().initWithConfig_devices_onSave_(
            dict(self.config),
            list_devices(),
            on_save,
        )

    def quit_app(self, _):
        self._stop_mic()
        self._stop_speaker()
        rumps.quit_application()

    def _poll_log(self, _):
        self.log_window.poll()
