"""Backend dispatch layer for the tracer.

A single module-level `_active_backend` is the swappable target. The default
backend is a no-op that does nothing — concrete backends call
`set_backend()` from inside their `backend_init()` to take over.

All public functions here forward to the active backend and swallow any
exception. Business code never sees a tracer error.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Imported lazily to avoid a circular import (noop_backend imports from here
# transitively via type hints in the future).
_active_backend = None  # type: ignore[assignment]


def _ensure_default() -> None:
    global _active_backend
    if _active_backend is None:
        from agent.tracer.backend import noop_backend

        _active_backend = noop_backend


def set_backend(backend) -> None:
    """Replace the active backend. Called by concrete `backend_init()`s."""
    global _active_backend
    _active_backend = backend


def backend_init(config: dict) -> None:
    """Initialise whichever backend is currently active. Most backends will
    instead expose their own `backend_init` that calls `set_backend()` —
    this top-level shim is here so generic call sites have a place to land.
    """
    _ensure_default()
    try:
        return _active_backend.backend_init(config)  # type: ignore[union-attr]
    except Exception:
        logger.debug("backend_init failed", exc_info=True)


def backend_start_span(
    span_id: str,
    parent_id: Optional[str],
    trace_id: str,
    span_type: str,
    name: str,
    attrs: dict,
) -> Any:
    _ensure_default()
    try:
        return _active_backend.backend_start_span(  # type: ignore[union-attr]
            span_id=span_id,
            parent_id=parent_id,
            trace_id=trace_id,
            span_type=span_type,
            name=name,
            attrs=attrs,
        )
    except Exception:
        logger.debug("backend_start_span failed", exc_info=True)
        return None


def backend_end_span(handle: Any, attrs: dict) -> None:
    _ensure_default()
    try:
        _active_backend.backend_end_span(handle, attrs)  # type: ignore[union-attr]
    except Exception:
        logger.debug("backend_end_span failed", exc_info=True)


def backend_set_attrs(handle: Any, attrs: dict) -> None:
    _ensure_default()
    try:
        _active_backend.backend_set_attrs(handle, attrs)  # type: ignore[union-attr]
    except Exception:
        logger.debug("backend_set_attrs failed", exc_info=True)


def backend_add_event(handle: Any, name: str, attrs: dict, timestamp: float) -> None:
    """Attach a SpanEvent to the active span (OTel data model layer 3).

    *timestamp* is a unix epoch seconds float; backends adapt to their own
    precision (Phoenix/OTel uses nanoseconds).
    """
    _ensure_default()
    fn = getattr(_active_backend, "backend_add_event", None)
    if fn is None:
        return  # backend doesn't implement events — silently skip
    try:
        fn(handle, name, attrs, timestamp)
    except Exception:
        logger.debug("backend_add_event failed", exc_info=True)


__all__ = [
    "set_backend",
    "backend_init",
    "backend_start_span",
    "backend_end_span",
    "backend_set_attrs",
    "backend_add_event",
]
