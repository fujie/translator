"""
Translation log window — PyObjC / AppKit implementation.

All UI lives on the main (AppKit) thread.
Background pipeline threads call add_entry() which puts items in a queue.
The main app's rumps Timer calls poll() to drain the queue and update the NSTextView.
"""
import datetime
import queue
from typing import Optional

import objc
from Foundation import NSObject, NSMakeRect
from AppKit import (
    NSWindow, NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable,
    NSBackingStoreBuffered, NSTextView, NSScrollView, NSButton,
    NSFont, NSApplication, NSSavePanel,
)

_STYLE = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
          NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable)
_W, _H = 960, 520
_BAR_H = 36  # height of button bar at bottom


class LogEntry:
    def __init__(self, direction: str, original: str, translated: str):
        self.direction = direction
        self.original = original
        self.translated = translated
        self.timestamp = datetime.datetime.now().strftime("%H:%M:%S")


class _Delegate(NSObject):
    """NSObject helper — routes button actions back to LogWindow."""

    def initWithOwner_(self, owner):
        self = objc.super(_Delegate, self).init()
        if self is None:
            return None
        self._owner = owner
        return self

    def clearLog_(self, sender):
        self._owner._clear()

    def exportLog_(self, sender):
        self._owner._export()


class LogWindow:
    def __init__(self, max_entries: int = 200):
        self.max_entries = max_entries
        self._entries: list[LogEntry] = []
        self._queue: queue.Queue = queue.Queue()
        self._window: Optional[NSWindow] = None
        self._text_view: Optional[NSTextView] = None
        self._delegate: Optional[_Delegate] = None

    # ------------------------------------------------------------------
    # Thread-safe — called from pipeline threads
    # ------------------------------------------------------------------

    def add_entry(self, direction: str, original: str, translated: str):
        entry = LogEntry(direction, original, translated)
        self._entries.append(entry)
        if len(self._entries) > self.max_entries:
            self._entries.pop(0)
        self._queue.put(entry)

    # ------------------------------------------------------------------
    # Main-thread API — called by rumps callbacks / Timer
    # ------------------------------------------------------------------

    def show(self):
        if self._window is None:
            self._build()
        self._window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def poll(self):
        """Drain queue → append to NSTextView. Called by rumps Timer (main thread)."""
        if self._text_view is None:
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
            return
        try:
            while True:
                self._append(self._queue.get_nowait())
        except queue.Empty:
            pass

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _build(self):
        self._window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(200, 200, _W, _H), _STYLE, NSBackingStoreBuffered, False
        )
        self._window.setTitle_("Translation Log")
        self._window.setReleasedWhenClosed_(False)

        self._delegate = _Delegate.alloc().initWithOwner_(self)
        content = self._window.contentView()

        # ── Button bar (bottom strip) ────────────────────────────────
        clear_btn = NSButton.alloc().initWithFrame_(NSMakeRect(8, 6, 72, 24))
        clear_btn.setTitle_("Clear")
        clear_btn.setBezelStyle_(1)  # NSBezelStyleRounded
        clear_btn.setTarget_(self._delegate)
        clear_btn.setAction_("clearLog:")
        content.addSubview_(clear_btn)

        export_btn = NSButton.alloc().initWithFrame_(NSMakeRect(86, 6, 80, 24))
        export_btn.setTitle_("Export…")
        export_btn.setBezelStyle_(1)
        export_btn.setTarget_(self._delegate)
        export_btn.setAction_("exportLog:")
        content.addSubview_(export_btn)

        # ── Scrollable text view (rest of the window) ─────────────────
        sv_rect = NSMakeRect(0, _BAR_H, _W, _H - _BAR_H)
        scroll = NSScrollView.alloc().initWithFrame_(sv_rect)
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setBorderType_(2)              # NSBezelBorder
        scroll.setAutoresizingMask_(2 | 16)   # widthSizable | heightSizable

        self._text_view = NSTextView.alloc().initWithFrame_(sv_rect)
        self._text_view.setEditable_(False)
        self._text_view.setFont_(NSFont.fontWithName_size_("Menlo", 11))
        self._text_view.setAutoresizingMask_(2 | 16)

        scroll.setDocumentView_(self._text_view)
        content.addSubview_(scroll)

        # Replay history for newly created window
        for entry in list(self._entries):
            self._append(entry)

    def _append(self, entry: LogEntry):
        if self._text_view is None:
            return
        arrow = "↑" if "local" in entry.direction else "↓"
        line = (
            f"[{entry.timestamp}] {arrow} {entry.direction:<10}  "
            f"{entry.original}\n"
            f"{'':>28}→  {entry.translated}\n\n"
        )
        storage = self._text_view.textStorage()
        storage.replaceCharactersInRange_withString_((storage.length(), 0), line)
        self._text_view.scrollToEndOfDocument_(None)

    def _clear(self):
        self._entries.clear()
        if self._text_view:
            storage = self._text_view.textStorage()
            storage.replaceCharactersInRange_withString_((0, storage.length()), "")

    def _export(self):
        panel = NSSavePanel.savePanel()
        fname = f"translation_log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        panel.setNameFieldStringValue_(fname)
        result = panel.runModal()
        if result == 1:  # NSModalResponseOK
            path = str(panel.URL().path())
            with open(path, "w", encoding="utf-8") as f:
                for e in self._entries:
                    f.write(f"[{e.timestamp}] {e.direction}\n")
                    f.write(f"  Original:   {e.original}\n")
                    f.write(f"  Translated: {e.translated}\n\n")
