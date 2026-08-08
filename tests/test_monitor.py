"""监控与审计模块单元测试。"""

from __future__ import annotations

import json

import pytest

from mcp_dbtools.monitor import Monitor, wrap_tool


def _mk_monitor(tmp_path, size=5):
    audit = tmp_path / "audit.jsonl"
    return Monitor(history_size=size, audit_file=str(audit)), audit


def test_record_and_history(tmp_path):
    m, _ = _mk_monitor(tmp_path)
    m.record_tool_call("execute_query", {"sql": "SELECT 1"}, duration=0.12, ok=True)
    m.record_tool_call("execute_query", {"sql": "SELECT BAD"}, duration=0.03, ok=False, error="bad sql")
    hist = m.execution_history(limit=10)
    assert len(hist) == 2
    # 最新在前
    assert hist[0]["tool"] == "execute_query"
    assert hist[0]["ok"] is False
    assert hist[0]["error"] == "bad sql"
    assert hist[1]["ok"] is True
    assert hist[1]["duration_ms"] == 120.0


def test_ring_buffer_limit(tmp_path):
    m, _ = _mk_monitor(tmp_path, size=3)
    for i in range(6):
        m.record_tool_call("t", {"i": i})
    assert len(m.execution_history(100)) == 3
    # 保留最近 3 条
    assert [h["args"]["i"] for h in m.execution_history(100)] == [5, 4, 3]


def test_filter_by_tool_and_ok(tmp_path):
    m, _ = _mk_monitor(tmp_path, size=20)
    m.record_tool_call("a", ok=True)
    m.record_tool_call("a", ok=False, error="e")
    m.record_tool_call("b", ok=True)
    assert len(m.execution_history(tool="a")) == 2
    assert len(m.execution_history(ok=False)) == 1
    assert len(m.execution_history(tool="b", ok=True)) == 1


def test_tool_summary(tmp_path):
    m, _ = _mk_monitor(tmp_path)
    m.record_tool_call("a", duration=0.1, ok=True)
    m.record_tool_call("a", duration=0.2, ok=False, error="x")
    m.record_tool_call("b", duration=0.5, ok=True)
    s = m.tool_summary()
    assert s["a"]["calls"] == 2
    assert s["a"]["errors"] == 1
    assert s["b"]["calls"] == 1
    assert abs(s["a"]["total_time_ms"] - 300.0) < 0.01


def test_audit_file_written(tmp_path):
    m, audit = _mk_monitor(tmp_path)
    m.record_tool_call("execute_query", {"sql": "SELECT * FROM t"}, ok=True)
    lines = audit.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["tool"] == "execute_query"
    assert entry["ok"] is True


def test_args_sanitized(tmp_path):
    m, audit = _mk_monitor(tmp_path, size=3)
    m.record_tool_call("execute_query", {"sql": "x" * 10000}, ok=True)
    hist = m.execution_history(1)
    assert len(hist[0]["args"]["sql"]) < 10000
    assert "..." in hist[0]["args"]["sql"]


def test_wrap_tool_records(tmp_path):
    m, _ = _mk_monitor(tmp_path)

    @wrap_tool(m, "demo")
    def demo(x: int) -> int:
        return x * 2

    assert demo(x=3) == 6
    hist = m.execution_history(1)
    assert hist[0]["tool"] == "demo"
    assert hist[0]["ok"] is True
    assert hist[0]["args"] == {"x": 3}


def test_wrap_tool_records_error(tmp_path):
    m, _ = _mk_monitor(tmp_path)

    @wrap_tool(m, "boom")
    def boom():
        raise ValueError("oops")

    with pytest.raises(ValueError):
        boom()
    hist = m.execution_history(1)
    assert hist[0]["ok"] is False
    assert "oops" in hist[0]["error"]
