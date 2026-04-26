"""Phoenix (Arize) backend using OpenInference + OpenTelemetry.

All OTel calls are wrapped in try/except so a misbehaving exporter never
takes down the agent. Maps our internal span types and attribute keys to
the OpenInference semantic conventions where possible.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Module-level state populated by backend_init().
_tracer = None
_initialized = False

# OpenInference helpers, resolved lazily so import is cheap until enabled.
_OI_SPAN_KIND = None
_OI_ATTRS = None
_trace_api = None
_StatusCode = None


_TYPE_TO_KIND = {
    "agent": "AGENT",
    "llm": "LLM",
    "tool": "TOOL",
    "chain": "CHAIN",
    "retriever": "RETRIEVER",
}


def backend_init(config: dict) -> None:
    global _tracer, _initialized, _OI_SPAN_KIND, _OI_ATTRS, _trace_api, _StatusCode

    if _initialized:
        return

    os.environ.setdefault("OPENINFERENCE_BASE64_IMAGE_MAX_LENGTH", "0")

    try:
        from opentelemetry import trace as trace_api
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.trace.status import StatusCode

        try:
            from openinference.semconv.trace import (
                OpenInferenceSpanKindValues,
                SpanAttributes,
            )

            _OI_SPAN_KIND = OpenInferenceSpanKindValues
            _OI_ATTRS = SpanAttributes
        except Exception:
            logger.debug("openinference semconv not available", exc_info=True)
            _OI_SPAN_KIND = None
            _OI_ATTRS = None

        endpoint = config.get("endpoint", "http://localhost:6006/v1/traces")
        project_name = config.get("project_name", "hermes-agent")

        resource = Resource.create({"service.name": project_name, "openinference.project.name": project_name})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace_api.set_tracer_provider(provider)

        _tracer = trace_api.get_tracer("hermes-agent")
        _trace_api = trace_api
        _StatusCode = StatusCode
        _initialized = True

        from agent.tracer.backend import set_backend
        import sys

        set_backend(sys.modules[__name__])
    except Exception:
        logger.warning("Phoenix backend init failed; tracing disabled", exc_info=True)


def _kind_for(span_type: str):
    if _OI_SPAN_KIND is None:
        return None
    name = _TYPE_TO_KIND.get(span_type)
    if name is None:
        return None
    try:
        return getattr(_OI_SPAN_KIND, name).value
    except Exception:
        return None


def _to_oi_attr_value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (str, bool, int, float)):
        return v
    try:
        return json.dumps(v, default=str, ensure_ascii=False)
    except Exception:
        try:
            return repr(v)
        except Exception:
            return "<unserializable>"


def _map_attrs(span_type: str, attrs: dict) -> dict:
    """Translate our internal attr keys into OpenInference semantic keys."""
    out: dict = {}
    if _OI_ATTRS is None:
        for k, v in attrs.items():
            out[k] = _to_oi_attr_value(v)
        return out

    A = _OI_ATTRS
    for key, val in attrs.items():
        try:
            if key == "messages":
                out[A.LLM_INPUT_MESSAGES] = _to_oi_attr_value(val)
            elif key in ("output", "content"):
                attr = A.LLM_OUTPUT_MESSAGES if span_type == "llm" else (
                    A.TOOL_OUTPUT if span_type == "tool" else None
                )
                if attr is not None:
                    out[attr] = _to_oi_attr_value(val)
                else:
                    out[key] = _to_oi_attr_value(val)
            elif key == "model":
                out[A.LLM_MODEL_NAME] = _to_oi_attr_value(val)
            elif key == "tokens" and isinstance(val, dict):
                if val.get("input") is not None:
                    out[A.LLM_TOKEN_COUNT_PROMPT] = val.get("input")
                if val.get("output") is not None:
                    out[A.LLM_TOKEN_COUNT_COMPLETION] = val.get("output")
            elif key == "tool_name":
                out[A.TOOL_NAME] = _to_oi_attr_value(val)
            elif key == "input" and span_type == "tool":
                out[A.TOOL_PARAMETERS] = _to_oi_attr_value(val)
            else:
                out[key] = _to_oi_attr_value(val)
        except Exception:
            out[key] = _to_oi_attr_value(val)
    return out


class _OtelHandle:
    __slots__ = ("span", "ctx_token", "span_type")

    def __init__(self, span, ctx_token, span_type):
        self.span = span
        self.ctx_token = ctx_token
        self.span_type = span_type


def backend_start_span(
    span_id: str,
    parent_id: Optional[str],
    trace_id: str,
    span_type: str,
    name: str,
    attrs: dict,
) -> Any:
    if _tracer is None:
        return None
    try:
        otel_span = _tracer.start_span(name)
        kind = _kind_for(span_type)
        if kind is not None and _OI_ATTRS is not None:
            try:
                otel_span.set_attribute(_OI_ATTRS.OPENINFERENCE_SPAN_KIND, kind)
            except Exception:
                pass
        for k, v in _map_attrs(span_type, attrs).items():
            try:
                otel_span.set_attribute(k, v)
            except Exception:
                pass
        # Make this span the OTel current span so nested OTel-aware libs link.
        try:
            from opentelemetry import context as otel_context
            from opentelemetry.trace import set_span_in_context

            ctx = set_span_in_context(otel_span)
            token = otel_context.attach(ctx)
        except Exception:
            token = None
        return _OtelHandle(otel_span, token, span_type)
    except Exception:
        logger.debug("phoenix backend_start_span failed", exc_info=True)
        return None


def backend_set_attrs(handle: Any, attrs: dict) -> None:
    if handle is None:
        return
    try:
        for k, v in _map_attrs(handle.span_type, attrs).items():
            try:
                handle.span.set_attribute(k, v)
            except Exception:
                pass
    except Exception:
        logger.debug("phoenix backend_set_attrs failed", exc_info=True)


def backend_end_span(handle: Any, attrs: dict) -> None:
    if handle is None:
        return
    try:
        for k, v in _map_attrs(handle.span_type, attrs).items():
            try:
                handle.span.set_attribute(k, v)
            except Exception:
                pass
        if "error" in attrs and _StatusCode is not None:
            try:
                handle.span.set_status(_StatusCode.ERROR)
            except Exception:
                pass
        try:
            handle.span.end()
        finally:
            if handle.ctx_token is not None:
                try:
                    from opentelemetry import context as otel_context

                    otel_context.detach(handle.ctx_token)
                except Exception:
                    pass
    except Exception:
        logger.debug("phoenix backend_end_span failed", exc_info=True)
