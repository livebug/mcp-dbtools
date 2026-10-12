"""BridgeClient 的单元测试：用假桥子进程（Python 脚本说 NDJSON）驱动真实客户端。"""

from __future__ import annotations

import json
import sys
import threading
import time

import pytest

from mcp_dbtools.bridge import BridgeClient, BridgeError, BridgeNotRunningError

# 假桥：读一行 NDJSON，回一行 NDJSON；支持 ping / shutdown / 慢请求 / 抛错。
_FAKE_BRIDGE = r'''
import json, sys, time
print(json.dumps({"type":"event","method":"bridge.ready","params":{"pid":0}}), flush=True)
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    frame = json.loads(line)
    mid = frame.get("id")
    method = frame.get("method")
    if method == "system.shutdown":
        print(json.dumps({"id": mid, "ok": True, "result": {"stopping": True}}), flush=True)
        break
    if method == "system.ping":
        print(json.dumps({"id": mid, "ok": True, "result": {"pong": True}}), flush=True)
    elif method == "slow.op":
        time.sleep(0.3)
        print(json.dumps({"id": mid, "ok": True, "result": {"slow": True}}), flush=True)
    elif method == "fail.sql":
        print(json.dumps({"id": mid, "ok": False, "error": {"code":"SQL_ERROR","message":"relation missing","sqlState":"42P01","errorCode":99}}), flush=True)
    else:
        print(json.dumps({"id": mid, "ok": True, "result": {}}), flush=True)
'''


def _make_client() -> BridgeClient:
    return BridgeClient("unused.jar", command=[sys.executable, "-c", _FAKE_BRIDGE], request_timeout=5.0)


def test_ping_round_trip():
    client = _make_client()
    try:
        client.start()
        result = client.request("system.ping")
        assert result == {"pong": True}
    finally:
        client.close()


def test_error_is_mapped():
    client = _make_client()
    try:
        client.start()
        with pytest.raises(BridgeError) as exc:
            client.request("fail.sql")
        assert exc.value.code == "SQL_ERROR"
        assert exc.value.sql_state == "42P01"
        assert exc.value.error_code == 99
    finally:
        client.close()


def test_concurrent_requests_correlate():
    client = _make_client()
    try:
        client.start()
        results = {}

        def worker(name, method):
            results[name] = client.request(method)

        threads = [
            threading.Thread(target=worker, args=("ping", "system.ping")),
            threading.Thread(target=worker, args=("slow", "slow.op")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert results["ping"] == {"pong": True}
        assert results["slow"] == {"slow": True}
    finally:
        client.close()


def test_shutdown_stops_process():
    client = _make_client()
    client.start()
    assert client.alive
    client.close()
    assert not client.alive


def test_request_without_start_raises():
    client = _make_client()
    with pytest.raises(BridgeNotRunningError):
        client.request("system.ping")
