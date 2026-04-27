"""Fault isolation tests for the tracer.

Verifies that backend failures, unserializable values, and decorator usage
never propagate exceptions into business code.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import patch

from agent.tracer import trace_llm
from agent.tracer.core import Span, _safe_value, span


def _boom(*_a, **_kw):
    raise RuntimeError("backend exploded")


def test_backend_start_span_raises_yields_valid_span():
    with patch("agent.tracer.backend.backend_start_span", side_effect=_boom):
        with span("test", "start_fails") as sp:
            assert isinstance(sp, Span)
            assert sp.span_id
            assert sp.name == "start_fails"
            ran = True
            assert ran


def test_backend_end_span_raises_does_not_propagate():
    with patch("agent.tracer.backend.backend_end_span", side_effect=_boom):
        # Exiting the context must not raise.
        with span("test", "end_fails") as sp:
            assert isinstance(sp, Span)
        # If we reach here, no exception leaked.
        assert sp.end_time is not None


def test_backend_set_attrs_raises_does_not_propagate():
    with patch("agent.tracer.backend.backend_set_attrs", side_effect=_boom):
        with span("test", "set_attrs_fails") as sp:
            sp.set_attr("key", "value")  # must not raise
            assert sp.attrs["key"] == "value"


def test_safe_value_handles_unserializable_object():
    class Weird:
        def __repr__(self) -> str:
            return "Weird()"

        def __str__(self) -> str:
            # Defeats json.dumps(..., default=str) by raising inside default().
            raise TypeError("not stringifiable")

    obj = Weird()
    # Confirm json.dumps rejects this even with default=str.
    try:
        json.dumps(obj, default=str, allow_nan=False)
        rejected = False
    except Exception:
        rejected = True
    assert rejected

    result = _safe_value(obj)
    # Should fall back to repr-based string without raising.
    assert isinstance(result, str)
    assert "Weird" in result


def test_safe_value_handles_object_that_breaks_repr():
    class Hostile:
        def __repr__(self) -> str:
            raise ValueError("no repr for you")

    # _safe_value must not crash even when both json.dumps and repr fail.
    result = _safe_value(Hostile())
    assert result == "<unserializable>"


def test_trace_llm_decorator_with_crashing_backend_returns_correct_result():
    @trace_llm(model="test-model")
    def fake_llm(prompt: str) -> str:
        return f"echo:{prompt}"

    with patch("agent.tracer.backend.backend_start_span", side_effect=_boom), \
         patch("agent.tracer.backend.backend_end_span", side_effect=_boom), \
         patch("agent.tracer.backend.backend_set_attrs", side_effect=_boom):
        out = fake_llm("hello")

    assert out == "echo:hello"


def _run_all():
    tests = [
        test_backend_start_span_raises_yields_valid_span,
        test_backend_end_span_raises_does_not_propagate,
        test_backend_set_attrs_raises_does_not_propagate,
        test_safe_value_handles_unserializable_object,
        test_safe_value_handles_object_that_breaks_repr,
        test_trace_llm_decorator_with_crashing_backend_returns_correct_result,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_all())
