"""
Live Transcript window — tkinter (Windows / Linux).

Shows source and translated text side-by-side as they stream in.
Includes an audio mix slider for the speaker pipeline (0 % = original only,
100 % = translated only).
"""
import datetime
import logging
import queue
import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk

logger = logging.getLogger(__name__)


class LiveTranscriptWindow:
    """
    Thread-safe live transcript view.
    - Pipeline threads call add_delta() / commit().
    - Main thread calls poll() every ~200 ms and show() on demand.
    - on_mix_change(ratio: float) is called when the user moves the slider.
    """

    def __init__(self, root: tk.Tk, config: dict, on_mix_change):
        self._root         = root
        self._config       = config
        self._on_mix_change = on_mix_change   # callback(ratio: 0.0–1.0)
        self._queue: queue.Queue = queue.Queue()

        # In-progress streaming text per direction
        self._cur: dict[str, dict] = {}

        # Widget refs (set in _build)
        self._win          = None
        self._src_cur_var  = tk.StringVar()
        self._tgt_cur_var  = tk.StringVar()
        self._src_hist     = None   # ScrolledText
        self._tgt_hist     = None   # ScrolledText
        self._mix_var      = None
        self._mix_pct_var  = None

    # ── Public (thread-safe) ──────────────────────────────────────────

    def show(self):
        if self._win and self._win.winfo_exists():
            self._win.lift()
            self._win.focus_set()
            return
        self._build()

    def add_delta(self, direction: str, src_delta: str, tgt_delta: str):
        """Called from pipeline thread; queued for main-thread processing."""
        self._queue.put(("delta", direction, src_delta, tgt_delta))

    def commit(self, direction: str, src: str, tgt: str):
        """Called when an utterance completes; queued for history flush."""
        self._queue.put(("commit", direction, src, tgt))

    def poll(self):
        """Drain queue. Must be called from the tkinter main thread."""
        while True:
            try:
                msg = self._queue.get_nowait()
            except queue.Empty:
                break
            self._handle(msg)

    # ── UI construction ───────────────────────────────────────────────

    def _build(self):
        win = tk.Toplevel(self._root)
        self._win = win
        win.title("Live Transcript")
        win.geometry("980x620")
        win.minsize(640, 420)

        # ── Column headers ─────────────────────────────────────────────
        hdr = ttk.Frame(win, padding=(8, 8, 8, 0))
        hdr.pack(fill=tk.X)
        hdr.columnconfigure(0, weight=1)
        hdr.columnconfigure(1, weight=1)
        ttk.Label(hdr, text="▶ Original (Source)",     font=("", 10, "bold")).grid(row=0, column=0, sticky=tk.W)
        ttk.Label(hdr, text="▶ Translated (Target)",   font=("", 10, "bold")).grid(row=0, column=1, sticky=tk.W, padx=(6, 0))

        # ── Streaming current text (labels) ────────────────────────────
        cur_frame = ttk.Frame(win, padding=(8, 4, 8, 0))
        cur_frame.pack(fill=tk.X)
        cur_frame.columnconfigure(0, weight=1)
        cur_frame.columnconfigure(1, weight=1)

        self._src_cur_var = tk.StringVar()
        self._tgt_cur_var = tk.StringVar()

        ttk.Label(
            cur_frame, textvariable=self._src_cur_var,
            foreground="#004ea8", wraplength=450, justify=tk.LEFT, font=("", 10),
        ).grid(row=0, column=0, sticky=tk.W)
        ttk.Label(
            cur_frame, textvariable=self._tgt_cur_var,
            foreground="#a85800", wraplength=450, justify=tk.LEFT, font=("", 10),
        ).grid(row=0, column=1, sticky=tk.W, padx=(6, 0))

        ttk.Separator(win, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=4)

        # ── History text areas ──────────────────────────────────────────
        hist = ttk.Frame(win, padding=(8, 0, 8, 0))
        hist.pack(fill=tk.BOTH, expand=True)

        self._src_hist = scrolledtext.ScrolledText(
            hist, wrap=tk.WORD, font=("", 10), state=tk.DISABLED, width=44,
        )
        self._src_hist.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 3))
        self._src_hist.tag_configure("local",  foreground="#004ea8")
        self._src_hist.tag_configure("remote", foreground="#007700")

        self._tgt_hist = scrolledtext.ScrolledText(
            hist, wrap=tk.WORD, font=("", 10), state=tk.DISABLED, width=44,
        )
        self._tgt_hist.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(3, 0))
        self._tgt_hist.tag_configure("local",  foreground="#a85800")
        self._tgt_hist.tag_configure("remote", foreground="#007700")

        # ── Audio Mix slider ────────────────────────────────────────────
        mix_frame = ttk.Frame(win, padding=(8, 6, 8, 2))
        mix_frame.pack(fill=tk.X)

        ttk.Label(mix_frame, text="音声 Mix:").pack(side=tk.LEFT)
        ttk.Label(mix_frame, text="原音声 0").pack(side=tk.LEFT, padx=(8, 2))

        init_pct = int(self._config.get("speaker_mix_ratio", 100))
        self._mix_var     = tk.IntVar(value=init_pct)
        self._mix_pct_var = tk.StringVar(value=f"{init_pct}%")

        ttk.Scale(
            mix_frame, from_=0, to=100, orient=tk.HORIZONTAL,
            variable=self._mix_var, command=self._on_slider,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

        ttk.Label(mix_frame, text="100 翻訳音声").pack(side=tk.LEFT, padx=(2, 6))
        ttk.Label(mix_frame, textvariable=self._mix_pct_var, width=5).pack(side=tk.LEFT)

        # ── Bottom buttons ──────────────────────────────────────────────
        btn_frame = ttk.Frame(win, padding=(8, 2, 8, 10))
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="Clear",   command=self._clear ).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="Export…", command=self._export).pack(side=tk.LEFT, padx=(8, 0))

    # ── Queue handler ──────────────────────────────────────────────────

    def _handle(self, msg):
        cmd = msg[0]

        if cmd == "delta":
            _, direction, src_delta, tgt_delta = msg
            d = self._cur.setdefault(direction, {"src": "", "tgt": ""})
            if src_delta:
                d["src"] += src_delta
                self._src_cur_var.set(d["src"])
            if tgt_delta:
                d["tgt"] += tgt_delta
                self._tgt_cur_var.set(d["tgt"])

        elif cmd == "commit":
            _, direction, src, tgt = msg
            d = self._cur.get(direction, {"src": "", "tgt": ""})
            final_src = src or d.get("src", "")
            final_tgt = tgt or d.get("tgt", "")
            d["src"] = d["tgt"] = ""

            # Clear streaming labels only if no other direction is active
            all_blank = all(
                not v["src"] and not v["tgt"]
                for v in self._cur.values()
            )
            if all_blank:
                self._src_cur_var.set("")
                self._tgt_cur_var.set("")

            if final_src or final_tgt:
                tag = "local" if "local" in direction else "remote"
                arrow = "↑" if tag == "local" else "↓"
                ts = datetime.datetime.now().strftime("%H:%M:%S")
                self._prepend(self._src_hist, f"[{ts} {arrow}] {final_src}\n", tag)
                self._prepend(self._tgt_hist, f"[{ts} {arrow}] {final_tgt}\n", tag)

    def _prepend(self, widget, text: str, tag: str):
        """Insert at the top of the history widget (newest first)."""
        if widget is None:
            return
        widget.config(state=tk.NORMAL)
        widget.insert("1.0", text, tag)
        widget.config(state=tk.DISABLED)

    # ── Slider ─────────────────────────────────────────────────────────

    def _on_slider(self, val):
        pct = int(float(val))
        self._mix_pct_var.set(f"{pct}%")
        self._on_mix_change(pct / 100.0)

    # ── Buttons ────────────────────────────────────────────────────────

    def _clear(self):
        for w in [self._src_hist, self._tgt_hist]:
            if w:
                w.config(state=tk.NORMAL)
                w.delete("1.0", tk.END)
                w.config(state=tk.DISABLED)
        self._src_cur_var.set("")
        self._tgt_cur_var.set("")
        for d in self._cur.values():
            d["src"] = d["tgt"] = ""

    def _export(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("All", "*.*")],
            parent=self._win,
        )
        if not path:
            return
        src_text = self._src_hist.get("1.0", tk.END) if self._src_hist else ""
        tgt_text = self._tgt_hist.get("1.0", tk.END) if self._tgt_hist else ""
        lines_src = [l for l in src_text.splitlines() if l.strip()]
        lines_tgt = [l for l in tgt_text.splitlines() if l.strip()]
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# Live Transcript Export — {datetime.datetime.now():%Y-%m-%d %H:%M}\n\n")
            for s, t in zip(lines_src, lines_tgt):
                f.write(f"[Original]   {s}\n[Translated] {t}\n\n")
            # If unequal length, append remainder
            for s in lines_src[len(lines_tgt):]:
                f.write(f"[Original]   {s}\n\n")
            for t in lines_tgt[len(lines_src):]:
                f.write(f"[Translated] {t}\n\n")
