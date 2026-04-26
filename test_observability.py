#!/usr/bin/env python3
"""Smoke and safety tests for the observability tracer integration."""

import sys
import traceback


def test_imports():
    """All tracer modules must import cleanly."""
    from agent.tracer import (
        Span,
        trace,
        span,
        current_span,
        trace_source,
        set_trace_source,
        get_trace_source,
        trace_agent_run,
        trace_llm,
        trace_tool,
        trace_mcp,
    )
    from agent.tracer.backend.noop_backend import backend_init
    from agent.tracer.backend.file_backend import backend_init as file_init
    from agent.tracer.backend.phoenix_backend import backend_init as phoenix_init
    print("[PASS] test_imports")


def test_noop_backend_does_not_crash():
    """Tracer with noop backend must be fully silent."""
    from agent.tracer.core import span
    from agent.tracer.backend.noop_backend import backend_init

    backend_init({})
    with span("test", "noop") as sp:
        sp.set_attr("key", "value")
    print("[PASS] test_noop_backend_does_not_crash")


def test_decorator_on_sync_function():
    """@trace() on a sync function must not alter behaviour."""
    from agent.tracer import trace

    @trace("test")
    def add(a, b):
        return a + b

    assert add(2, 3) == 5
    print("[PASS] test_decorator_on_sync_function")


def test_decorator_on_async_function():
    """@trace() on an async function must not alter behaviour."""
    import asyncio
    from agent.tracer import trace

    @trace("test")
    async def async_add(a, b):
        return a + b

    assert asyncio.run(async_add(2, 3)) == 5
    print("[PASS] test_decorator_on_async_function")


def test_decorator_on_generator():
    """@trace() on a generator must yield the same values."""
    from agent.tracer import trace

    @trace("test")
    def gen():
        yield 1
        yield 2
        yield 3

    assert list(gen()) == [1, 2, 3]
    print("[PASS] test_decorator_on_generator")


def test_decorator_exception_safety():
    """Decorator must re-raise exceptions and still emit the span."""
    from agent.tracer import trace

    @trace("test")
    def boom():
        raise ValueError("intentional")

    try:
        boom()
        assert False, "should have raised"
    except ValueError:
        pass
    print("[PASS] test_decorator_exception_safety")


def test_pii_redaction():
    """PII redaction must mask email and phone in strings."""
    from agent.tracer.core import _redact_text

    text = "Contact me at alice@example.com or call 123-456-7890"
    redacted = _redact_text(text)
    assert "[EMAIL]" in redacted
    assert "[PHONE]" in redacted
    print("[PASS] test_pii_redaction")


def test_trace_source_context_manager():
    """trace_source() context manager must set and reset correctly."""
    from agent.tracer import get_trace_source, trace_source

    before = get_trace_source()
    with trace_source("eval"):
        assert get_trace_source() == "eval"
    assert get_trace_source() == before
    print("[PASS] test_trace_source_context_manager")


def test_file_backend_output():
    """File backend must produce a valid line of JSON per span."""
    import json
    import os
    import tempfile
    from agent.tracer.core import span
    from agent.tracer.backend.file_backend import backend_init

    # Use a temp directory as home to avoid polluting real logs
    with tempfile.TemporaryDirectory() as td:
        os.environ["HERMES_HOME"] = td
        backend_init({})
        with span("test", "span1") as sp:
            sp.set_attr("foo", "bar")
        log_path = os.path.join(td, "logs", "traces.jsonl")
        with open(log_path, "r") as f:
            lines = [json.loads(line) for line in f]
        assert len(lines) == 1
        assert lines[0]["name"] == "span1"
        assert lines[0]["attrs"]["foo"] == "bar"
    print("[PASS] test_file_backend_output")


def test_agent_import():
    """run_agent must still import after observability changes."""
    from run_agent import AIAgent
    from tools.registry import ToolRegistry
    print("[PASS] test_agent_import")


def main():
    tests = [
        test_imports,
        test_noop_backend_does_not_crash,
        test_decorator_on_sync_function,
        test_decorator_on_async_function,
        test_decorator_on_generator,
        test_decorator_exception_safety,
        test_pii_redaction,
        test_trace_source_context_manager,
        test_file_backend_output,
        test_agent_import,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception:
            print(f"[FAIL] {t.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\nResults: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
