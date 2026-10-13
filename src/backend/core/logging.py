"""Logging configuration with secret redaction."""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_REDACTIONS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(api[_-]?key|secret|password|token)(\"?\s*[:=]\s*\"?)([^\s\"',}]{4,})"),
]


def redact(text: str) -> str:
    """Mask credential-looking substrings in ``text``."""
    for pattern in _REDACTIONS:
        if pattern.groups >= 3:
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}***REDACTED***", text)
        else:
            text = pattern.sub("***REDACTED***", text)
    return text


class RedactingFilter(logging.Filter):
    """Filter that scrubs secrets from every log record before it is emitted."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.getMessage()))
        record.args = ()
        return True


def setup_logging(log_dir: Path, level: int = logging.INFO) -> None:
    """Configure root logging once: console + rotating file, both redacted."""
    root = logging.getLogger()
    if getattr(root, "_art_configured", False):
        return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_dir / "backend.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"))
    except OSError:
        pass
    for handler in handlers:
        handler.setFormatter(fmt)
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
    root._art_configured = True  # type: ignore[attr-defined]
