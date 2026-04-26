"""Public tracer API.

Business code should import only from this module:

    from agent.tracer import trace_llm, trace_tool, current_span
"""

import contextvars
from contextlib import contextmanager

from agent.tracer.core import Span, _trace_source, current_span, span, trace
from agent.tracer.decorators import (
    trace_agent_run,
    trace_llm,
    trace_mcp,
    trace_reasoning,
    trace_retrieval,
    trace_skill,
    trace_tool,
)
from agent.tracer.propagation import attach_trace_context, get_current_trace_context


def set_trace_source(source: str) -> contextvars.Token:
    return _trace_source.set(source)


def get_trace_source() -> str:
    return _trace_source.get()


@contextmanager
def trace_source(source: str):
    prev = _trace_source.get()
    _trace_source.set(source)
    try:
        yield
    finally:
        _trace_source.set(prev)


__all__ = [
    "Span",
    "span",
    "trace",
    "trace_agent_run",
    "trace_llm",
    "trace_tool",
    "trace_mcp",
    "trace_skill",
    "trace_retrieval",
    "trace_reasoning",
    "current_span",
    "get_current_trace_context",
    "attach_trace_context",
    "set_trace_source",
    "get_trace_source",
    "trace_source",
]
