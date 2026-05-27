"""
Translation context editor — tkinter (Windows / Linux).

Lets the user enter free-form context text (meeting background, participant
names, technical glossary, etc.) and optionally fetch additional text from
URLs.  The saved text is injected into the GPT system prompt at session start.
"""
import logging
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, simpledialog, ttk

from context_manager import fetch_url_text

logger = logging.getLogger(__name__)

_PLACEHOLDER = """\
# 会議のコンテキスト・固有名詞・専門用語を自由形式で記入してください。
# 翻訳開始時に参照されます（gpt-realtime / gpt-realtime-2 のみ有効）。
#
# 記入例:
# このミーティングはクラウド移行プロジェクトのレビューです。
#
# 参加者: John Smith (CTO), Alice Wang (リードエンジニア)
#
# 専門用語:
#   IaC = Infrastructure as Code
#   k8s = Kubernetes
#   Terraform: インフラ構成管理ツール
"""


class ContextWindow:
    """
    Translation context editor window (tkinter).
    Call show() each time; a new Toplevel is created with the latest config.
    """

    def __init__(self, root: tk.Tk, config: dict, on_save):
        self._root   = root
        self._config = config
        self._on_save = on_save

    def show(self):
        self._build()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build(self):
        win = tk.Toplevel(self._root)
        win.title("Translation Context")
        win.geometry("660x500")
        win.minsize(480, 340)
        win.grab_set()   # modal

        # ── Info banner ───────────────────────────────────────────────
        ttk.Label(
            win,
            text=(
                "翻訳コンテキスト・固有名詞・専門用語を入力してください。\n"
                "翻訳開始時に参照されます。"
                "（gpt-realtime / gpt-realtime-2 のみ有効 — translate モデルは非対応）"
            ),
            wraplength=630,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=12, pady=(10, 4))

        # ── Text area ─────────────────────────────────────────────────
        frame = ttk.Frame(win, padding=(12, 0, 12, 0))
        frame.pack(fill=tk.BOTH, expand=True)

        txt = scrolledtext.ScrolledText(frame, wrap=tk.WORD, font=("", 11), undo=True)
        txt.pack(fill=tk.BOTH, expand=True)
        self._txt = txt

        saved = (self._config.get("context_text") or "").strip()
        txt.insert(tk.END, saved if saved else _PLACEHOLDER)

        # ── Status bar ────────────────────────────────────────────────
        status_var = tk.StringVar(value="")
        self._status_var = status_var
        ttk.Label(win, textvariable=status_var, foreground="gray").pack(
            anchor=tk.W, padx=14, pady=(2, 0)
        )

        # ── Buttons ───────────────────────────────────────────────────
        btn_frame = ttk.Frame(win, padding=(12, 4, 12, 12))
        btn_frame.pack(fill=tk.X)

        ttk.Button(btn_frame, text="Clear",
                   command=lambda: self._clear(win)).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="Add from URL…",
                   command=lambda: self._add_url(win)).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btn_frame, text="Cancel",
                   command=win.destroy).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn_frame, text="Save",
                   command=lambda: self._save(win)).pack(side=tk.RIGHT)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _clear(self, win: tk.Toplevel):
        if messagebox.askyesno("Confirm", "コンテキストをクリアしますか？", parent=win):
            self._txt.delete("1.0", tk.END)
            self._txt.insert(tk.END, _PLACEHOLDER)

    def _add_url(self, win: tk.Toplevel):
        url = simpledialog.askstring(
            "Add from URL", "URLを入力してください:", parent=win
        )
        if not url:
            return

        self._status_var.set(f"Fetching {url} …")
        win.update_idletasks()

        def _fetch():
            try:
                text = fetch_url_text(url)
                self._root.after(0, lambda: self._append_url(url, text))
            except Exception as exc:
                self._root.after(0, lambda: self._url_error(str(exc), win))

        threading.Thread(target=_fetch, daemon=True).start()

    def _append_url(self, url: str, text: str):
        self._status_var.set("")
        snippet = f"\n\n--- {url} ---\n{text}"
        self._txt.insert(tk.END, snippet)
        self._txt.see(tk.END)

    def _url_error(self, msg: str, win: tk.Toplevel):
        self._status_var.set("")
        messagebox.showerror("URL Error", msg, parent=win)

    def _save(self, win: tk.Toplevel):
        text = self._txt.get("1.0", tk.END).strip()
        # Don't treat the placeholder as real content
        if text == _PLACEHOLDER.strip():
            text = ""
        new_cfg = dict(self._config)
        new_cfg["context_text"] = text
        self._on_save(new_cfg)
        win.destroy()
