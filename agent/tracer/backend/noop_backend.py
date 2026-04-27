"""No-op backend. Active by default; ignores everything."""

from __future__ import annotations

from typing import Any, Optional


def backend_init(config: dict) -> None:
    return None


def backend_start_span(
    span_id: str,
    parent_id: Optional[str],
    trace_id: str,
    span_type: str,
    name: str,
    attrs: dict,
) -> Any:
    return None


def backend_end_span(handle: Any, attrs: dict) -> None:
    return None


def backend_set_attrs(handle: Any, attrs: dict) -> None:
    return None


def backend_add_event(handle: Any, name: str, attrs: dict, timestamp: float) -> None:
    return None
