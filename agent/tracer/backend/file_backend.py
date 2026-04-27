"""JSONL file backend for local development.

Writes one span per line to ``$HERMES_HOME/logs/traces.jsonl``. Thread-safe;
rotates when the file grows past 50 MB.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_MAX_BYTES = 50 * 1024 * 1024
_lock = threading.Lock()
_log_path: Optional[Path] = None


def _resolve_path() -> Path:
    try:
        from hermes_constants import get_hermes_home

        base = Path(get_hermes_home())
    except Exception:
        base = Path.home() / ".hermes"
    return base / "logs" / "traces.jsonl"


def backend_init(config: dict) -> None:
    global _log_path
    _log_path = _resolve_path()
    try:
        _log_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        logger.debug("file backend mkdir failed", exc_info=True)

    from agent.tracer.backend import set_backend
    import sys

    set_backend(sys.modules[__name__])


def _rotate_if_needed(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > _MAX_BYTES:
            rotated = path.with_suffix(path.suffix + f".{int(time.time())}")
            path.rename(rotated)
    except Exception:
        logger.debug("file backend rotate failed", exc_info=True)


class _Handle:
    __slots__ = ("span_id", "parent_id", "trace_id", "span_type", "name", "attrs", "start_time", "events")

    def __init__(self, span_id, parent_id, trace_id, span_type, name, attrs, start_time):
        self.span_id = span_id
        self.parent_id = parent_id
        self.trace_id = trace_id
        self.span_type = span_type
        self.name = name
        self.attrs = dict(attrs)
        self.start_time = start_time
        self.events: list = []


def backend_start_span(
    span_id: str,
    parent_id: Optional[str],
    trace_id: str,
    span_type: str,
    name: str,
    attrs: dict,
) -> Any:
    return _Handle(span_id, parent_id, trace_id, span_type, name, attrs, time.time())


def backend_set_attrs(handle: Any, attrs: dict) -> None:
    if handle is None:
        return
    handle.attrs.update(attrs)


def backend_add_event(handle: Any, name: str, attrs: dict, timestamp: float) -> None:
    """Buffer the event on the handle; written out alongside the span on end."""
    if handle is None:
        return
    handle.events.append({
        "name": name,
        "timestamp": timestamp,
        "attrs": dict(attrs),
    })


def backend_end_span(handle: Any, attrs: dict) -> None:
    if handle is None or _log_path is None:
        return
    end = time.time()
    record = {
        "span_id": handle.span_id,
        "parent_id": handle.parent_id,
        "trace_id": handle.trace_id,
        "span_type": handle.span_type,
        "name": handle.name,
        "attrs": {**handle.attrs, **attrs},
        "start_time": handle.start_time,
        "duration_ms": (end - handle.start_time) * 1000.0,
        "events": list(handle.events),
    }
    line = json.dumps(record, default=str, ensure_ascii=False)
    with _lock:
        try:
            _rotate_if_needed(_log_path)
            with open(_log_path, "a", encoding="utf-8") as f:
                f.write(line + os.linesep)
        except Exception:
            logger.debug("file backend write failed", exc_info=True)
