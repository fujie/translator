"""
Translation context manager.

Provides:
  fetch_url_text(url)         -- fetch a URL, strip HTML, return plain text
  build_context_prompt(text)  -- wrap context text for injection into system prompts
"""
import logging
import re
from html.parser import HTMLParser
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

MAX_URL_CHARS = 3000   # max characters extracted per URL (avoid huge prompts)

_CONTEXT_HEADER = (
    "--- Translation Context"
    " (refer to the following information to improve translation accuracy) ---"
)
_CONTEXT_FOOTER = "--- End of Translation Context ---"


# ── HTML text extractor ───────────────────────────────────────────────────────

class _HTMLTextExtractor(HTMLParser):
    """Strip HTML tags and collect visible text."""

    _SKIP_TAGS  = frozenset({"script", "style", "nav", "header", "footer", "noscript"})
    _BLOCK_TAGS = frozenset({
        "p", "div", "br", "li", "tr", "blockquote", "pre", "article", "section",
        "h1", "h2", "h3", "h4", "h5", "h6",
    })

    def __init__(self):
        super().__init__()
        self._buf: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS:
            self._buf.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)

    def handle_data(self, data):
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self._buf.append(stripped)

    def get_text(self) -> str:
        text = " ".join(self._buf)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_url_text(url: str, max_chars: int = MAX_URL_CHARS) -> str:
    """
    Fetch *url*, strip HTML, return plain text.
    Raises RuntimeError on network or HTTP failure.
    Output is truncated to *max_chars* characters.
    """
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; RealtimeTranslate/1.0)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            content_type = resp.headers.get("Content-Type", "")
            charset = "utf-8"
            if "charset=" in content_type:
                # e.g. "text/html; charset=Shift_JIS"
                charset = content_type.split("charset=")[-1].strip().split(";")[0]
            raw = resp.read().decode(charset, errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"URL fetch failed: {exc}") from exc

    extractor = _HTMLTextExtractor()
    extractor.feed(raw)
    text = extractor.get_text()

    if len(text) > max_chars:
        text = text[:max_chars] + " …（以降省略）"
    return text


def build_context_prompt(context_text: str) -> str:
    """
    Wrap *context_text* in a labelled block ready to prepend to a system prompt.
    Returns empty string if the context is blank.
    """
    text = (context_text or "").strip()
    if not text:
        return ""
    return f"{_CONTEXT_HEADER}\n{text}\n{_CONTEXT_FOOTER}\n\n"
