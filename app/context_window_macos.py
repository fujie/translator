"""
Translation context editor — PyObjC / AppKit (macOS only).

Lets the user enter free-form context text (meeting background, participant
names, technical glossary) and optionally fetch additional text from URLs.
"""
import logging
import threading

import objc
from Foundation import NSObject, NSMakeRect, NSMakeSize
from AppKit import (
    NSWindow,
    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSBackingStoreBuffered,
    NSTextField, NSButton, NSScrollView, NSTextView,
    NSApplication, NSFont, NSColor,
    NSBezelStyleRounded,
    NSAlert,
)

from context_manager import fetch_url_text

logger = logging.getLogger(__name__)

_W, _H = 660, 500
_STYLE = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable

_PLACEHOLDER = (
    "# 会議のコンテキスト・固有名詞・専門用語を自由形式で記入してください。\n"
    "# 翻訳開始時に参照されます（gpt-realtime / gpt-realtime-2 のみ有効）。\n"
    "#\n"
    "# 記入例:\n"
    "# このミーティングはクラウド移行プロジェクトのレビューです。\n"
    "#\n"
    "# 参加者: John Smith (CTO), Alice Wang (リードエンジニア)\n"
    "#\n"
    "# 専門用語:\n"
    "#   IaC = Infrastructure as Code\n"
    "#   k8s = Kubernetes\n"
    "#   Terraform: インフラ構成管理ツール\n"
)


class ContextWindowController(NSObject):
    """
    Manages the context editor window.
    Keep an instance alive in TranslateApp to prevent GC.
    Call open() to show the window (creates it if needed).
    """

    def initWithConfig_onSave_(self, config, on_save):
        self = objc.super(ContextWindowController, self).init()
        if self is None:
            return None
        self._config    = config
        self._on_save   = on_save
        self._window    = None
        self._text_view = None
        self._status    = None
        self._build()
        return self

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build(self):
        self._window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(200, 200, _W, _H), _STYLE, NSBackingStoreBuffered, False
        )
        self._window.setTitle_("Translation Context")
        self._window.setReleasedWhenClosed_(False)
        self._window.setMinSize_(NSMakeSize(480, 340))

        cv = self._window.contentView()

        # ── Info label ────────────────────────────────────────────────
        info = NSTextField.alloc().initWithFrame_(NSMakeRect(12, _H - 58, _W - 24, 46))
        info.setStringValue_(
            "翻訳コンテキスト・固有名詞・専門用語を入力してください。"
            "翻訳開始時に参照されます。"
            "（gpt-realtime / gpt-realtime-2 のみ有効 — translate モデルは非対応）"
        )
        info.setEditable_(False)
        info.setBordered_(False)
        info.setDrawsBackground_(False)
        info.setSelectable_(False)
        info.cell().setWraps_(True)
        cv.addSubview_(info)

        # ── NSScrollView + NSTextView ─────────────────────────────────
        text_rect = NSMakeRect(12, 50, _W - 24, _H - 116)
        scroll = NSScrollView.alloc().initWithFrame_(text_rect)
        scroll.setHasVerticalScroller_(True)
        scroll.setHasHorizontalScroller_(False)
        scroll.setBorderType_(2)   # NSBezelBorder

        tv = NSTextView.alloc().initWithFrame_(
            NSMakeRect(0, 0, _W - 24, _H - 116)
        )
        mono = NSFont.fontWithName_size_("Menlo", 12)
        tv.setFont_(mono or NSFont.systemFontOfSize_(12))
        tv.setAutomaticQuoteSubstitutionEnabled_(False)
        tv.setAutomaticDashSubstitutionEnabled_(False)
        tv.setRichText_(False)

        saved = (self._config.get("context_text") or "").strip()
        tv.setString_(saved if saved else _PLACEHOLDER)

        scroll.setDocumentView_(tv)
        cv.addSubview_(scroll)
        self._text_view = tv

        # ── Status label ──────────────────────────────────────────────
        status = NSTextField.alloc().initWithFrame_(NSMakeRect(12, 34, _W - 24, 16))
        status.setStringValue_("")
        status.setEditable_(False)
        status.setBordered_(False)
        status.setDrawsBackground_(False)
        status.setSelectable_(False)
        status.setTextColor_(NSColor.grayColor())
        cv.addSubview_(status)
        self._status = status

        # ── Buttons ───────────────────────────────────────────────────
        def _btn(title, x, w, action):
            b = NSButton.alloc().initWithFrame_(NSMakeRect(x, 8, w, 26))
            b.setTitle_(title)
            b.setBezelStyle_(NSBezelStyleRounded)
            b.setTarget_(self)
            b.setAction_(action)
            cv.addSubview_(b)
            return b

        _btn("Clear",          12,        70,  "clearContext:")
        _btn("Add from URL…",  90,        120, "addFromURL:")
        _btn("Cancel",         _W - 196,  80,  "cancelContext:")
        save_btn = _btn("Save", _W - 108, 90,  "saveContext:")
        save_btn.setKeyEquivalent_("\r")

        self._window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    # ------------------------------------------------------------------
    # ObjC actions
    # ------------------------------------------------------------------

    def clearContext_(self, sender):
        self._text_view.setString_(_PLACEHOLDER)

    def addFromURL_(self, sender):
        """Show an input dialog, then fetch the URL in a background thread."""
        alert = NSAlert.alloc().init()
        alert.setMessageText_("URLを入力してください")
        alert.addButtonWithTitle_("OK")
        alert.addButtonWithTitle_("Cancel")

        input_field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 320, 22))
        alert.setAccessoryView_(input_field)
        alert.window().setInitialFirstResponder_(input_field)

        if alert.runModal() != 1000:   # NSAlertFirstButtonReturn
            return

        url = str(input_field.stringValue()).strip()
        if not url:
            return

        self._status.setStringValue_(f"Fetching {url} …")

        def _fetch():
            try:
                text = fetch_url_text(url)
                payload = f"--- {url} ---\n{text}"
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "appendURLText:", payload, False
                )
            except Exception as exc:
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "showURLError:", str(exc), False
                )

        threading.Thread(target=_fetch, daemon=True).start()

    def appendURLText_(self, payload):
        """Main-thread: append fetched text to the editor."""
        self._status.setStringValue_("")
        current = str(self._text_view.string())
        self._text_view.setString_(current + "\n\n" + payload)

    def showURLError_(self, msg):
        """Main-thread: show an error alert."""
        self._status.setStringValue_("")
        alert = NSAlert.alloc().init()
        alert.setMessageText_("URL Error")
        alert.setInformativeText_(str(msg))
        alert.runModal()

    def cancelContext_(self, sender):
        self._window.close()

    def saveContext_(self, sender):
        text = str(self._text_view.string()).strip()
        if text == _PLACEHOLDER.strip():
            text = ""
        new_cfg = dict(self._config)
        new_cfg["context_text"] = text
        self._on_save(new_cfg)
        self._window.close()
