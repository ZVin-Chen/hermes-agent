"""Cross-thread / cross-process trace context propagation.

This is intentionally backend-agnostic. The dict returned by
`get_current_trace_context()` carries enough state for `attach_trace_context`
to install a synthetic parent span in another execution context, so that
spans created there are correctly stitched into the parent trace.
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Iterator, Optional

from agent.tracer.core import Span, _current_span, _new_span_id

logger = logging.getLogger(__name__)


def get_current_trace_context() -> dict:
    sp = _current_span.get()
    if sp is None:
        return {}
    return {
        "trace_id": sp.trace_id,
        "parent_span_id": sp.span_id,
    }


@contextlib.contextmanager
def attach_trace_context(ctx: Optional[dict]) -> Iterator[None]:
    """Install a remote-parent span so locally-created spans inherit
    `trace_id` and the supplied `parent_span_id`. The synthetic span is
    not exported to the backend — it exists only to propagate IDs.
    """
    if not ctx or "trace_id" not in ctx:
        yield
        return

    placeholder = Span(
        span_id=ctx.get("parent_span_id") or _new_span_id(),
        span_type="remote",
        name="remote_parent",
        attrs={},
        parent=None,
        trace_id=ctx["trace_id"],
        start_time=time.time(),
    )
    token = _current_span.set(placeholder)
    try:
        yield
    finally:
        try:
            _current_span.reset(token)
        except Exception:
            logger.debug("attach_trace_context reset failed", exc_info=True)


__all__ = ["get_current_trace_context", "attach_trace_context"]
