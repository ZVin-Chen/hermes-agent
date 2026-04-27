"""Semantic decorators built on top of `trace()`.

These give business code intent-revealing names (`@trace_llm`, `@trace_tool`,
etc.) while delegating all heavy lifting to `core.trace`.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from agent.tracer.core import trace

logger = logging.getLogger(__name__)


def _get(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def trace_llm(
    model: Optional[str] = None,
    capture_messages: bool = True,
    name: Optional[str] = "llm.completion",
):
    """Decorator for LLM calls.

    Extracts model name, messages, output, usage tokens, stop reason and
    temperature from the function's bound args / result.

    *name* controls the span name shown in trace UIs.  Defaults to
    ``"llm.completion"``; pass ``name="llm.{model}"`` (or any other format
    string) to embed bound-arg values.
    """

    def extractor(bound: dict, result: Any = None) -> dict:
        out: dict = {}
        # Find the kwargs-style payload: support both flat kwargs and a
        # nested `api_kwargs` dict (used by AIAgent._interruptible_api_call).
        payload = bound.get("api_kwargs") if isinstance(bound.get("api_kwargs"), dict) else bound

        m = model or _get(payload, "model")
        if m is not None:
            out["model"] = m
        if capture_messages:
            msgs = _get(payload, "messages")
            if msgs is not None:
                out["messages"] = msgs
        temp = _get(payload, "temperature")
        if temp is not None:
            out["temperature"] = temp

        if result is not None:
            # Try usage extraction (OpenAI-shaped: result.usage.{prompt,completion}_tokens
            # or anthropic-shaped: result.usage.{input,output}_tokens).
            usage = getattr(result, "usage", None)
            if usage is not None:
                in_tok = (
                    getattr(usage, "input_tokens", None)
                    or getattr(usage, "prompt_tokens", None)
                )
                out_tok = (
                    getattr(usage, "output_tokens", None)
                    or getattr(usage, "completion_tokens", None)
                )
                if in_tok is not None or out_tok is not None:
                    out["tokens"] = {"input": in_tok, "output": out_tok}

            stop_reason = (
                getattr(result, "stop_reason", None)
                or getattr(result, "finish_reason", None)
            )
            if stop_reason is not None:
                out["stop_reason"] = stop_reason

            content = getattr(result, "content", None)
            if content is not None:
                out["content"] = content
            else:
                choices = getattr(result, "choices", None)
                if choices:
                    try:
                        first = choices[0]
                        msg = getattr(first, "message", None)
                        if msg is not None:
                            out["content"] = getattr(msg, "content", None)
                    except Exception:
                        pass
        return out

    return trace("llm", name=name, capture_input=False, capture_output=False, attrs_extractor=extractor)


def trace_tool(name: Optional[str] = None, has_side_effect: bool = False):
    """Decorator for local tool calls.

    `name` may be a literal string OR a Python format string referencing a
    bound argument, e.g. ``@trace_tool(name="{function_name}")``.
    """

    def extractor(bound: dict, result: Any = None) -> dict:
        out: dict = {"has_side_effect": has_side_effect}
        # Resolve a tool_name from either an explicit `name` kwarg or
        # common arg names like `function_name`, `tool_name`, `name`.
        tn = _get(bound, "function_name", "tool_name", "name")
        if tn is not None:
            out["tool_name"] = tn
        # Tool input is usually a dict argument named `args`/`arguments`/`params`.
        ti = _get(bound, "args", "arguments", "params", "kwargs")
        if ti is not None:
            out["input"] = ti
        if result is not None:
            out["output"] = result
        return out

    # Resolve template names like "{function_name}" lazily inside trace() by
    # passing the literal string; users can override with a static string.
    return trace("tool", name=name, capture_input=False, capture_output=False, attrs_extractor=extractor)


def trace_mcp(server: str, tool: str, transport: str = "stdio"):
    def extractor(bound: dict, result: Any = None) -> dict:
        out = {
            "mcp.server": server,
            "mcp.tool": tool,
            "mcp.transport": transport,
        }
        ti = _get(bound, "args", "arguments", "params")
        if ti is not None:
            out["input"] = ti
        if result is not None:
            out["output"] = result
        return out

    return trace("tool", name=f"mcp:{server}:{tool}", capture_input=False, capture_output=False, attrs_extractor=extractor)


def trace_skill(name: str, version: Optional[str] = None):
    def extractor(bound: dict, result: Any = None) -> dict:
        out: dict = {"skill.name": name}
        if version is not None:
            out["skill.version"] = version
        return out

    return trace("chain", name=f"skill:{name}", attrs_extractor=extractor)


def trace_retrieval(name: Optional[str] = None):
    def extractor(bound: dict, result: Any = None) -> dict:
        out: dict = {}
        if result is not None:
            docs = None
            if isinstance(result, dict):
                docs = result.get("documents") or result.get("docs")
            else:
                docs = getattr(result, "documents", None) or getattr(result, "docs", None)
            if docs is not None:
                try:
                    out["retrieval.doc_count"] = len(docs)
                except Exception:
                    pass
        return out

    return trace("retriever", name=name, attrs_extractor=extractor)


def trace_reasoning(name: Optional[str] = None):
    return trace("chain", name=name)


def trace_agent_run(
    version_provider: Optional[Callable[[], dict]] = None,
    name: Optional[str] = "agent.run",
):
    """Decorator for the top-level agent run.

    *name* is the human-readable span name (defaults to ``"agent.run"``).
    """

    def extractor(bound: dict, result: Any = None) -> dict:
        out: dict = {}
        if version_provider is not None:
            try:
                # Allow `version_provider(self)` if it accepts the agent instance.
                self_obj = bound.get("self")
                try:
                    meta = version_provider(self_obj) if self_obj is not None else version_provider()
                except TypeError:
                    meta = version_provider()
                if isinstance(meta, dict):
                    for k, v in meta.items():
                        out[k] = v
            except Exception:
                logger.debug("version_provider failed", exc_info=True)
        user_msg = _get(bound, "user_message", "message")
        if user_msg is not None:
            out["user_message"] = user_msg
        return out

    return trace("agent", name=name, capture_input=False, capture_output=True, attrs_extractor=extractor)


__all__ = [
    "trace_llm",
    "trace_tool",
    "trace_mcp",
    "trace_skill",
    "trace_retrieval",
    "trace_reasoning",
    "trace_agent_run",
]
