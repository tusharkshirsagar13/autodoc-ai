"""Logging setup with secret redaction."""

from __future__ import annotations

import logging
import os
import re

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)([^\s,'\"]+)"),
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)([^\s,'\"]+)"),
]


def redact(text: str) -> str:
    """Remove anything that looks like a credential from a log string."""
    if not text:
        return text
    for env_name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        secret = os.getenv(env_name)
        if secret and len(secret) > 6:
            text = text.replace(secret, "[REDACTED]")
    text = _SECRET_PATTERNS[0].sub("[REDACTED]", text)
    for pattern in _SECRET_PATTERNS[1:]:
        text = pattern.sub(lambda m: m.group(1) + "[REDACTED]", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        record.msg = redact(message)
        record.args = ()
        return True


_configured = False


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger("autodoc")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(handler)
    root.propagate = False
    # Quieten noisy third-party loggers
    for noisy in ("chromadb", "httpx", "urllib3", "sentence_transformers"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"autodoc.{name}")
