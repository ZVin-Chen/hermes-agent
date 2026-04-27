#!/usr/bin/env python3
"""End-to-end integration test: verify traces land in Phoenix."""

import json
import time
import urllib.request

from agent.tracer import span, trace_llm, trace_tool, trace_agent_run
from agent.tracer.backend.phoenix_backend import backend_init


PHOENIX_URL = "http://localhost:6006"


def _wait_for_span_in_phoenix(trace_id: str, timeout: float = 10.0):
    """Poll Phoenix REST API until the trace appears."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                f"{PHOENIX_URL}/v1/traces?trace_id={trace_id}",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read().decode())
                if data.get("data"):
                    return data["data"]
        except Exception:
            pass
        time.sleep(0.5)
    return []


def test_phoenix_backend_receives_span():
    backend_init({
        "endpoint": f"{PHOENIX_URL}/v1/traces",
        "project_name": "hermes-agent-test",
    })

    with span("agent", "test-agent-run") as sp:
        sp.set_attr("model", "test-model")
        sp.set_attr("provider", "test-provider")
        trace_id = sp.trace_id
        with span("llm", "test-llm-call") as sp2:
            sp2.set_attr("model", "gpt-test")
            sp2.set_attr("temperature", 0.7)
        with span("tool", "test-tool-call") as sp3:
            sp3.set_attr("tool_name", "test_tool")

    # Allow OTLP exporter async flush
    time.sleep(2)

    traces = _wait_for_span_in_phoenix(trace_id, timeout=15)
    assert traces, f"Trace {trace_id} not found in Phoenix after polling"

    spans = traces[0].get("spans", [])
    span_names = {s["name"] for s in spans}
    span_types = {s["span_kind"] for s in spans}

    assert "test-agent-run" in span_names, f"agent span missing: {span_names}"
    assert "test-llm-call" in span_names, f"llm span missing: {span_names}"
    assert "test-tool-call" in span_names, f"tool span missing: {span_names}"

    print(f"[PASS] Phoenix received {len(spans)} spans for trace {trace_id[:8]}...")
    print(f"       Span names: {span_names}")
    print(f"       Span kinds: {span_types}")


def main():
    print("Waiting for Phoenix to be ready...")
    for _ in range(20):
        try:
            urllib.request.urlopen(f"{PHOENIX_URL}/healthz", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    else:
        raise RuntimeError("Phoenix not reachable")

    print("Phoenix OK. Running end-to-end trace test...")
    test_phoenix_backend_receives_span()
    print("\nAll integration tests passed.")


if __name__ == "__main__":
    main()
