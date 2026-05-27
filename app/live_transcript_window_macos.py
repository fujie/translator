"""
Live Transcript window — PyObjC / AppKit (macOS only).

Shows source and translated text side-by-side in real time, with an
audio mix slider for the speaker pipeline.

Thread model: pipeline threads call add_delta() / commit(), which put items in a
queue.  The rumps.Timer calls poll() every 200 ms from the AppKit main thread.
"""
import datetime
import logging
import queue
from typing import Optional

import objc
from Foundation import NSObject, NSMakeRect
from AppKit import (
    NSWindow,
    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable,
    NSBackingStoreBuffered,
    NSTextField, NSButton, NSScrollView, NSTextView, NSSlider,
    NSFont, NSColor, NSApplication, NSSavePanel,
    NSBezelStyleRounded,
)

logger = logging.getLogger(__name__)

_STYLE = (
    NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
    NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable
)
_W, _H  = 980, 620
_BAR_H  = 72   # button bar + mix slider strip at the bottom
_CUR_H  = 56   # current-utterance label area at the top


class _LTDelegate(NSObject):
    """Routes button/slider actions to LiveTranscriptWindow."""

    def initWithOwner_(self, owner):
        self = objc.super(_LTDelegate, self).init()
        if self is None:
            return None
        self._owner = owner
        return self

    def clearLT_(self, sender):
        self._owner._clear()

    def exportLT_(self, sender):
        self._owner._export()

    def mixChanged_(self, sender):
        pct = int(sender.floatValue())
        self._owner._on_mix_slider(pct)


class LiveTranscriptWindow:
    """
    Lazy-built NSWindow for live transcript streaming.
    Must be used from the AppKit main thread (via poll / show).
    """

    def __init__(self, config: dict, on_mix_change):
        self._config        = config
        self._on_mix_change = on_mix_change   # callback(ratio: 0.0–1.0)
        self._queue: queue.Queue = queue.Queue()
        self._cur: dict[str, dict] = {}

        self._window: Optional[NSWindow] = None
        self._src_cur: Optional[NSTextField] = None
        self._tgt_cur: Optional[NSTextField] = None
        self._src_view: Optional[NSTextView] = None
        self._tgt_view: Optional[NSTextView] = None
        self._mix_slider: Optional[NSSlider] = None
        self._mix_label: Optional[NSTextField] = None
        self._delegate = None

    # ── Public (thread-safe) ──────────────────────────────────────────

    def show(self):
        if self._window is None:
            self._build()
        self._window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def add_delta(self, direction: str, src_delta: str, tgt_delta: str):
        self._queue.put(("delta", direction, src_delta, tgt_delta))

    def commit(self, direction: str, src: str, tgt: str):
        self._queue.put(("commit", direction, src, tgt))

    def poll(self):
        """Called by rumps.Timer from main thread every 200 ms."""
        if self._window is None:
            # Drain queue even if window not open yet
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
            return
        try:
            while True:
                msg = self._queue.get_nowait()
                self._handle(msg)
        except queue.Empty:
            pass

    # ── UI construction ───────────────────────────────────────────────

    def _build(self):
        self._delegate = _LTDelegate.alloc().initWithOwner_(self)

        self._window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(80, 80, _W, _H), _STYLE, NSBackingStoreBuffered, False
        )
        self._window.setTitle_("Live Transcript")
        self._window.setReleasedWhenClosed_(False)
        cv = self._window.contentView()
        mid = _W // 2

        # ── Column header labels ──────────────────────────────────────
        def hdr_label(text, x, w):
            f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, _H - 24, w, 18))
            f.setStringValue_(text)
            f.setEditable_(False); f.setBordered_(False)
            f.setDrawsBackground_(False); f.setSelectable_(False)
            f.setFont_(NSFont.boldSystemFontOfSize_(11))
            cv.addSubview_(f)

        hdr_label("▶ Original (Source)",   8,       mid - 12)
        hdr_label("▶ Translated (Target)", mid + 4, mid - 12)

        # ── Current streaming text fields ─────────────────────────────
        top_y = _H - 24 - _CUR_H
        def cur_field(x, w, color):
            f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, top_y, w, _CUR_H - 4))
            f.setEditable_(False)
            f.setBordered_(True)
            f.setDrawsBackground_(True)
            f.setSelectable_(True)
            f.setStringValue_("")
            f.cell().setWraps_(True)
            f.setTextColor_(color)
            cv.addSubview_(f)
            return f

        self._src_cur = cur_field(8,       mid - 12, NSColor.colorWithRed_green_blue_alpha_(0, 0.31, 0.66, 1))
        self._tgt_cur = cur_field(mid + 4, mid - 12, NSColor.colorWithRed_green_blue_alpha_(0.66, 0.34, 0, 1))

        # ── History text views ────────────────────────────────────────
        hist_y  = _BAR_H + 2
        hist_h  = top_y - hist_y - 4

        def hist_scroll(x, w):
            rect = NSMakeRect(x, hist_y, w, hist_h)
            scroll = NSScrollView.alloc().initWithFrame_(rect)
            scroll.setHasVerticalScroller_(True)
            scroll.setBorderType_(2)   # NSBezelBorder
            tv = NSTextView.alloc().initWithFrame_(rect)
            tv.setEditable_(False)
            mono = NSFont.fontWithName_size_("Menlo", 10)
            tv.setFont_(mono or NSFont.systemFontOfSize_(10))
            scroll.setDocumentView_(tv)
            cv.addSubview_(scroll)
            return tv

        self._src_view = hist_scroll(8,       mid - 12)
        self._tgt_view = hist_scroll(mid + 4, mid - 12)

        # ── Mix slider strip ──────────────────────────────────────────
        mix_y = 38

        def small_lbl(text, x, w):
            f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, mix_y + 3, w, 16))
            f.setStringValue_(text)
            f.setEditable_(False); f.setBordered_(False)
            f.setDrawsBackground_(False); f.setSelectable_(False)
            cv.addSubview_(f)
            return f

        small_lbl("音声 Mix:", 8, 68)
        small_lbl("原音声 0", 80, 64)

        slider = NSSlider.alloc().initWithFrame_(NSMakeRect(148, mix_y, _W - 320, 22))
        slider.setMinValue_(0)
        slider.setMaxValue_(100)
        init_pct = int(self._config.get("speaker_mix_ratio", 100))
        slider.setFloatValue_(float(init_pct))
        slider.setTarget_(self._delegate)
        slider.setAction_("mixChanged:")
        cv.addSubview_(slider)
        self._mix_slider = slider

        small_lbl("100 翻訳音声", _W - 164, 86)
        self._mix_label = small_lbl(f"{init_pct}%", _W - 72, 40)

        # ── Button bar ────────────────────────────────────────────────
        for title, x, action in [
            ("Clear",   8,  "clearLT:"),
            ("Export…", 86, "exportLT:"),
        ]:
            b = NSButton.alloc().initWithFrame_(NSMakeRect(x, 6, 72, 24))
            b.setTitle_(title)
            b.setBezelStyle_(NSBezelStyleRounded)
            b.setTarget_(self._delegate)
            b.setAction_(action)
            cv.addSubview_(b)

    # ── Queue handler ─────────────────────────────────────────────────

    def _handle(self, msg):
        cmd = msg[0]

        if cmd == "delta":
            _, direction, src_delta, tgt_delta = msg
            d = self._cur.setdefault(direction, {"src": "", "tgt": ""})
            if src_delta:
                d["src"] += src_delta
                if self._src_cur:
                    self._src_cur.setStringValue_(d["src"])
            if tgt_delta:
                d["tgt"] += tgt_delta
                if self._tgt_cur:
                    self._tgt_cur.setStringValue_(d["tgt"])

        elif cmd == "commit":
            _, direction, src, tgt = msg
            d = self._cur.get(direction, {"src": "", "tgt": ""})
            final_src = src or d.get("src", "")
            final_tgt = tgt or d.get("tgt", "")
            d["src"] = d["tgt"] = ""

            all_blank = all(
                not v["src"] and not v["tgt"]
                for v in self._cur.values()
            )
            if all_blank:
                if self._src_cur: self._src_cur.setStringValue_("")
                if self._tgt_cur: self._tgt_cur.setStringValue_("")

            if final_src or final_tgt:
                arrow = "↑" if "local" in direction else "↓"
                ts = datetime.datetime.now().strftime("%H:%M:%S")
                line = f"[{ts} {arrow}] "
                self._prepend(self._src_view, line + final_src + "\n")
                self._prepend(self._tgt_view, line + final_tgt + "\n")

    @objc.python_method
    def _prepend(self, tv: NSTextView, text: str):
        if tv is None:
            return
        storage = tv.textStorage()
        storage.replaceCharactersInRange_withString_((0, 0), text)

    # ── Slider / buttons ──────────────────────────────────────────────

    @objc.python_method
    def _on_mix_slider(self, pct: int):
        if self._mix_label:
            self._mix_label.setStringValue_(f"{pct}%")
        self._on_mix_change(pct / 100.0)

    @objc.python_method
    def _clear(self):
        for tv in [self._src_view, self._tgt_view]:
            if tv:
                storage = tv.textStorage()
                storage.replaceCharactersInRange_withString_((0, storage.length()), "")
        if self._src_cur: self._src_cur.setStringValue_("")
        if self._tgt_cur: self._tgt_cur.setStringValue_("")
        for d in self._cur.values():
            d["src"] = d["tgt"] = ""

    @objc.python_method
    def _export(self):
        panel = NSSavePanel.savePanel()
        fname = f"live_transcript_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        panel.setNameFieldStringValue_(fname)
        if panel.runModal() != 1:
            return
        path = str(panel.URL().path())
        src_text = str(self._src_view.string()) if self._src_view else ""
        tgt_text = str(self._tgt_view.string()) if self._tgt_view else ""
        lines_src = [l for l in src_text.splitlines() if l.strip()]
        lines_tgt = [l for l in tgt_text.splitlines() if l.strip()]
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# Live Transcript Export — {datetime.datetime.now():%Y-%m-%d %H:%M}\n\n")
            for s, t in zip(lines_src, lines_tgt):
                f.write(f"[Original]   {s}\n[Translated] {t}\n\n")
            for s in lines_src[len(lines_tgt):]:
                f.write(f"[Original]   {s}\n\n")
            for t in lines_tgt[len(lines_src):]:
                f.write(f"[Translated] {t}\n\n")
