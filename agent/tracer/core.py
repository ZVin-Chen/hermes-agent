"""Core tracing primitives.

Defines `Span`, the implicit parent context (`_current_span`), the `span()`
context manager, and the generic `trace()` decorator factory. All backend
calls are wrapped in try/except so a misbehaving backend never crashes
business logic.
"""

from __future__ import annotations

import contextlib
import functools
import inspect
import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

logger = logging.getLogger(__name__)

_MAX_STR_LEN = 10_000
_TRUNCATED_SUFFIX = "...[truncated]"

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[\s-]?)?\(?\d{3}\)?[\s-]?\d{3}[\s-]?\d{4}\b")
_CC_RE = re.compile(r"\b(?:\d[ -]*?){13,16}\b")


@dataclass
class SpanEvent:
    """A timestamped point-in-time annotation on a Span (OTel data model layer 3).

    Distinct from a child span: events have no duration, they mark *when*
    something happened inside an enclosing span.
    """
    name: str
    timestamp: float
    attrs: dict = field(default_factory=dict)


@dataclass
class Span:
    span_id: str
    span_type: str
    name: str
    attrs: dict = field(default_factory=dict)
    parent: Optional["Span"] = None
    trace_id: Optional[str] = None
    start_time: float = 0.0
    end_time: Optional[float] = None
    backend_handle: Any = None
    events: list = field(default_factory=list)

    @property
    def duration_ms(self) -> Optional[float]:
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000.0

    def set_attr(self, key: str, value: Any) -> None:
        self.attrs[key] = value
        try:
            from agent.tracer.backend import backend_set_attrs

            backend_set_attrs(self.backend_handle, {key: value})
        except Exception:
            logger.debug("backend_set_attrs failed", exc_info=True)

    def add_event(self, name: str, **attrs: Any) -> None:
        """Attach a SpanEvent to this span and forward to the backend."""
        ts = time.time()
        evt = SpanEvent(name=name, timestamp=ts, attrs=dict(attrs))
        self.events.append(evt)
        try:
            from agent.tracer.backend import backend_add_event

            backend_add_event(self.backend_handle, name, evt.attrs, ts)
        except Exception:
            logger.debug("backend_add_event failed", exc_info=True)


def add_event(name: str, **attrs: Any) -> None:
    """Module-level helper: attach an event to whatever span is currently active.

    No-op when no span is active.  Safe to call from anywhere — never raises.
    """
    sp = _current_span.get()
    if sp is None:
        return
    try:
        sp.add_event(name, **attrs)
    except Exception:
        logger.debug("add_event failed", exc_info=True)


_current_span: ContextVar[Optional[Span]] = ContextVar("_current_span", default=None)
_trace_source: ContextVar[str] = ContextVar("trace_source", default="interactive")


def current_span() -> Optional[Span]:
    return _current_span.get()


def _new_span_id() -> str:
    return uuid.uuid4().hex[:16]


def _new_trace_id() -> str:
    return uuid.uuid4().hex


def _redact_text(text: str) -> str:
    text = _EMAIL_RE.sub("[EMAIL]", text)
    text = _PHONE_RE.sub("[PHONE]", text)
    text = _CC_RE.sub("[CC]", text)
    return text


def _truncate(s: str) -> str:
    if len(s) > _MAX_STR_LEN:
        return s[:_MAX_STR_LEN] + _TRUNCATED_SUFFIX
    return s


def _safe_value(v: Any, _depth: int = 0) -> Any:
    if _depth > 6:
        return repr(v)[:200]
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        return _truncate(_redact_text(v))
    if isinstance(v, (list, tuple)):
        return [_safe_value(x, _depth + 1) for x in v]
    if isinstance(v, dict):
        return {str(k): _safe_value(val, _depth + 1) for k, val in v.items()}
    try:
        json.dumps(v, default=str, allow_nan=False)
        return v
    except Exception:
        try:
            return _truncate(_redact_text(repr(v)))
        except Exception:
            return "<unserializable>"


def _safe_serialize(*objs: Any, **named: Any) -> dict:
    """Return a JSON-safe dict from positional + named values."""
    out: dict = {}
    if objs:
        out["args"] = [_safe_value(o) for o in objs]
    for k, v in named.items():
        if k in ("self", "cls"):
            continue
        out[k] = _safe_value(v)
    return out


@contextlib.contextmanager
def span(span_type: str, name: str, **attrs: Any) -> Iterator[Span]:
    parent = _current_span.get()
    trace_id = parent.trace_id if parent else _new_trace_id()
    sp = Span(
        span_id=_new_span_id(),
        span_type=span_type,
        name=name,
        attrs=dict(attrs),
        parent=parent,
        trace_id=trace_id,
        start_time=time.time(),
    )
    sp.attrs.setdefault("source", _trace_source.get())
    try:
        from agent.tracer.backend import backend_start_span

        sp.backend_handle = backend_start_span(
            span_id=sp.span_id,
            parent_id=parent.span_id if parent else None,
            trace_id=trace_id,
            span_type=span_type,
            name=name,
            attrs=sp.attrs,
        )
    except Exception:
        logger.debug("backend_start_span failed", exc_info=True)

    prev = _current_span.get()
    _current_span.set(sp)
    try:
        yield sp
    except BaseException as e:
        sp.attrs["error"] = repr(e)[:500]
        sp.attrs["error_type"] = type(e).__name__
        raise
    finally:
        sp.end_time = time.time()
        try:
            from agent.tracer.backend import backend_end_span

            backend_end_span(sp.backend_handle, dict(sp.attrs))
        except Exception:
            logger.debug("backend_end_span failed", exc_info=True)
        _current_span.set(prev)


def _resolve_span_name(template: str, bound: dict) -> str:
    """Render ``{placeholder}`` references in *template* using bound args.

    Falls back to the literal template if anything goes wrong (missing key,
    unhashable arg, etc.) so a malformed name never breaks the call.
    """
    if not template or "{" not in template:
        return template
    try:
        return template.format(**bound)
    except Exception:
        # Try a tolerant pass: replace only the placeholders we can resolve,
        # leave the rest as-is.
        try:
            class _Tolerant(dict):
                def __missing__(self, key):  # noqa: D401
                    return "{" + key + "}"
            return template.format_map(_Tolerant(**bound))
        except Exception:
            return template


def _bind_args(func: Callable, args: tuple, kwargs: dict) -> dict:
    try:
        sig = inspect.signature(func)
        bound = sig.bind_partial(*args, **kwargs)
        return dict(bound.arguments)
    except Exception:
        return {"args": list(args), "kwargs": dict(kwargs)}


def _resolve_attrs(
    attrs_extractor: Optional[Callable],
    bound_args: dict,
    result: Any = None,
    is_result: bool = False,
) -> dict:
    if attrs_extractor is None:
        return {}
    try:
        if is_result:
            extracted = attrs_extractor(bound_args, result)
        else:
            extracted = attrs_extractor(bound_args)
        if not isinstance(extracted, dict):
            return {}
        return {k: _safe_value(v) for k, v in extracted.items()}
    except Exception:
        logger.debug("attrs_extractor failed", exc_info=True)
        return {}


def trace(
    span_type: str,
    name: Optional[str] = None,
    capture_input: bool = True,
    capture_output: bool = True,
    attrs_extractor: Optional[Callable] = None,
):
    """Generic decorator factory.

    Supports sync/async functions and sync/async generators. For generators,
    the span lasts until the generator is exhausted (or closed). For async
    generators, `first_chunk_latency_ms` is recorded.
    """

    def decorator(func: Callable):
        span_name = name or getattr(func, "__qualname__", getattr(func, "__name__", "anonymous"))

        if inspect.isasyncgenfunction(func):

            @functools.wraps(func)
            async def async_gen_wrapper(*args, **kwargs):
                bound = _bind_args(func, args, kwargs)
                init_attrs = _resolve_attrs(attrs_extractor, bound)
                if capture_input:
                    init_attrs.setdefault("input", _safe_serialize(**bound))
                with span(span_type, _resolve_span_name(span_name, bound), **init_attrs) as sp:
                    started = time.time()
                    first = True
                    chunks: list = []
                    agen = func(*args, **kwargs)
                    try:
                        async for item in agen:
                            if first:
                                sp.set_attr(
                                    "first_chunk_latency_ms",
                                    (time.time() - started) * 1000.0,
                                )
                                first = False
                            if capture_output and len(chunks) < 200:
                                chunks.append(item)
                            yield item
                    finally:
                        if capture_output:
                            sp.set_attr("output", _safe_value(chunks))
                        post = _resolve_attrs(
                            attrs_extractor, bound, result=chunks, is_result=True
                        )
                        for k, v in post.items():
                            sp.set_attr(k, v)

            return async_gen_wrapper

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                bound = _bind_args(func, args, kwargs)
                init_attrs = _resolve_attrs(attrs_extractor, bound)
                if capture_input:
                    init_attrs.setdefault("input", _safe_serialize(**bound))
                with span(span_type, _resolve_span_name(span_name, bound), **init_attrs) as sp:
                    result = await func(*args, **kwargs)
                    if capture_output:
                        sp.set_attr("output", _safe_value(result))
                    post = _resolve_attrs(
                        attrs_extractor, bound, result=result, is_result=True
                    )
                    for k, v in post.items():
                        sp.set_attr(k, v)
                    return result

            return async_wrapper

        if inspect.isgeneratorfunction(func):

            @functools.wraps(func)
            def gen_wrapper(*args, **kwargs):
                bound = _bind_args(func, args, kwargs)
                init_attrs = _resolve_attrs(attrs_extractor, bound)
                if capture_input:
                    init_attrs.setdefault("input", _safe_serialize(**bound))
                with span(span_type, _resolve_span_name(span_name, bound), **init_attrs) as sp:
                    chunks: list = []
                    gen = func(*args, **kwargs)
                    try:
                        for item in gen:
                            if capture_output and len(chunks) < 200:
                                chunks.append(item)
                            yield item
                    finally:
                        if capture_output:
                            sp.set_attr("output", _safe_value(chunks))
                        post = _resolve_attrs(
                            attrs_extractor, bound, result=chunks, is_result=True
                        )
                        for k, v in post.items():
                            sp.set_attr(k, v)

            return gen_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            bound = _bind_args(func, args, kwargs)
            init_attrs = _resolve_attrs(attrs_extractor, bound)
            if capture_input:
                init_attrs.setdefault("input", _safe_serialize(**bound))
            with span(span_type, _resolve_span_name(span_name, bound), **init_attrs) as sp:
                result = func(*args, **kwargs)
                if capture_output:
                    sp.set_attr("output", _safe_value(result))
                post = _resolve_attrs(
                    attrs_extractor, bound, result=result, is_result=True
                )
                for k, v in post.items():
                    sp.set_attr(k, v)
                return result

        return sync_wrapper

    return decorator


__all__ = [
    "Span",
    "span",
    "trace",
    "current_span",
    "_current_span",
    "_safe_serialize",
]
