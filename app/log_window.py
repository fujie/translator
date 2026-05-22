"""
Translation log window — tkinter implementation (cross-platform).

add_entry() is thread-safe and can be called from any pipeline thread.
poll() must be called periodically from the tkinter main thread to drain
the queue and update the text widget.
"""
import datetime
import queue
import tkinter as tk
from tkinter import ttk, filedialog
from typing import Optional


class LogEntry:
    def __init__(self, direction: str, original: str, translated: str):
        self.direction = direction
        self.original = original
        self.translated = translated
        self.timestamp = datetime.datetime.now().strftime("%H:%M:%S")


class LogWindow:
    def __init__(self, root: tk.Tk, max_entries: int = 200):
        self._root = root
        self.max_entries = max_entries
        self._entries: list[LogEntry] = []
        self._queue: queue.Queue = queue.Queue()
        self._window: Optional[tk.Toplevel] = None
        self._text: Optional[tk.Text] = None

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
    # Main-thread API
    # ------------------------------------------------------------------

    def show(self):
        if self._window is None or not self._window.winfo_exists():
            self._build()
        else:
            self._window.deiconify()
            self._window.lift()
            self._window.focus_force()

    def poll(self):
        """Drain queue → append to text widget. Call from main thread."""
        try:
            while True:
                entry = self._queue.get_nowait()
                if self._text:
                    self._append(entry)
        except queue.Empty:
            pass

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _build(self):
        self._window = tk.Toplevel(self._root)
        self._window.title("Translation Log")
        self._window.geometry("960x520")
        self._window.protocol("WM_DELETE_WINDOW", self._window.withdraw)

        # ── Text area ────────────────────────────────────────────────
        text_frame = tk.Frame(self._window)
        text_frame.pack(fill=tk.BOTH, expand=True)

        self._text = tk.Text(
            text_frame,
            font=("Courier New", 11),
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="white",
        )
        scrollbar = ttk.Scrollbar(text_frame, command=self._text.yview)
        self._text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # ── Button bar ────────────────────────────────────────────────
        btn_frame = tk.Frame(self._window, pady=6)
        btn_frame.pack(fill=tk.X, padx=8)
        tk.Button(btn_frame, text="Clear", width=8,
                  command=self._clear).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(btn_frame, text="Export…", width=8,
                  command=self._export).pack(side=tk.LEFT)

        # Replay existing history into the new window
        for entry in list(self._entries):
            self._append(entry)

    def _append(self, entry: LogEntry):
        if self._text is None:
            return
        arrow = "↑" if "local" in entry.direction else "↓"
        line = (
            f"[{entry.timestamp}] {arrow} {entry.direction:<10}  "
            f"{entry.original}\n"
            f"{'':>28}→  {entry.translated}\n\n"
        )
        self._text.configure(state=tk.NORMAL)
        self._text.insert(tk.END, line)
        self._text.see(tk.END)
        self._text.configure(state=tk.DISABLED)

    def _clear(self):
        self._entries.clear()
        if self._text:
            self._text.configure(state=tk.NORMAL)
            self._text.delete("1.0", tk.END)
            self._text.configure(state=tk.DISABLED)

    def _export(self):
        path = filedialog.asksaveasfilename(
            parent=self._window,
            defaultextension=".txt",
            initialfile=(
                f"translation_log_"
                f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            ),
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                for e in self._entries:
                    f.write(f"[{e.timestamp}] {e.direction}\n")
                    f.write(f"  Original:   {e.original}\n")
                    f.write(f"  Translated: {e.translated}\n\n")
