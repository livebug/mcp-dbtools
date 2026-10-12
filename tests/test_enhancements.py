"""新增优化项测试：审计轮转、导出清理、健康检查、限流、元数据缓存、显式事务。"""

from __future__ import annotations

import time

import httpx
import pytest

from mcp_dbtools.bridge import BridgeError
from mcp_dbtools.config import DataSource, Settings
from mcp_dbtools.export import ExportManager
from mcp_dbtools.jdbc import JDBCError, JDBCManager
from mcp_dbtools.monitor import Monitor
from mcp_dbtools.ratelimit import RateLimitMiddleware, TokenBucket

from fake_bridge import FakeBridge, connection_open_response, make_manager


def _ds(name="g"):
    return DataSource(
        name=name, type="gaussdb", jdbc_url="jdbc:opengauss://h:5432/db",
        driver_class="org.opengauss.Driver", jars=["x.jar"],
    )


# ---------- 审计日志轮转 ----------
def test_audit_rotation(tmp_path):
    audit = tmp_path / "audit.jsonl"
    m = Monitor(
        history_size=1000, audit_file=str(audit), audit_db=None,
        audit_max_bytes=200, audit_backup_count=2,
    )
    for i in range(300):
        m.record_tool_call("t", {"i": i, "payload": "x" * 50})
    files = sorted(p.name for p in tmp_path.glob("audit.jsonl*"))
    assert "audit.jsonl.1" in files
    assert len(files) <= 3


def test_audit_no_rotation_when_disabled(tmp_path):
    audit = tmp_path / "audit.jsonl"
    m = Monitor(
        history_size=1000, audit_file=str(audit), audit_db=None,
        audit_max_bytes=0,
    )
    for i in range(50):
        m.record_tool_call("t", {"i": i})
    files = sorted(p.name for p in tmp_path.glob("audit.jsonl*"))
    assert files == ["audit.jsonl"]


# ---------- 导出文件清理 ----------
class _FakeJM:
    def __init__(self, rows):
        self.rows = list(rows)

    def execute_paged(self, ds, sql, page_size=500, max_rows=None):
        yield {"columns": ["id"], "rows": self.rows, "truncated": False}

    def _record_success(self, name):
        pass

    def _record_failure(self, name):
        pass


def _wait_status(em, export_id, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = em.status(export_id)
        if s and s["status"] in ("succeeded", "failed"):
            return s
        time.sleep(0.02)
    raise TimeoutError(f"导出超时: {em.status(export_id)}")


def test_export_cleanup_by_age(tmp_path):
    em = ExportManager(export_dir=str(tmp_path), jdbc_manager=_FakeJM([(1,)]))
    t = em.start(_ds(), "SELECT 1", filename="old")
    _wait_status(em, t["id"])
    with em._lock:
        em._tasks[t["id"]]["finished_at"] = "2000-01-01 00:00:00"
    res = em.cleanup(keep_seconds=60)
    assert t["id"] in res["removed"]
    assert em.status(t["id"]) is None
    assert em.get_file_path(t["id"]) is None


def test_export_cleanup_by_count(tmp_path):
    em = ExportManager(export_dir=str(tmp_path), jdbc_manager=_FakeJM([(1,)]), max_files=2)
    for i in range(3):
        em.start(_ds(), "SELECT 1", filename=f"f{i}")
    for t in em.list():
        _wait_status(em, t["id"])
    em.cleanup(max_files=2)
    assert len(em.list()) == 2


def test_export_cleanup_keeps_running(tmp_path):
    em = ExportManager(export_dir=str(tmp_path), jdbc_manager=_FakeJM([(1,)]), max_files=1)
    t = em.start(_ds(), "SELECT 1", filename="run")
    em.cleanup(max_files=1)
    assert em.status(t["id"]) is not None
    _wait_status(em, t["id"])


# ---------- 健康检查 ----------
def test_health_check_ok():
    ds = _ds()
    m = make_manager(Settings(datasources=[ds]), respond={
        "driver.register": {},
        "connection.open": connection_open_response(),
        "query.execute": {"hasResultSet": False, "updateCount": 1},
    })
    h = m.health(ds, deep=False)  # 从未探测过 -> 真实探测
    assert h["ok"] is True
    assert h["latency_ms"] is not None
    assert h["last_health"] is not None


def test_health_check_cached():
    ds = _ds()
    m = make_manager(Settings(datasources=[ds]), respond={
        "driver.register": {},
        "connection.open": connection_open_response(),
        "query.execute": {"hasResultSet": False, "updateCount": 1},
    })
    m.health(ds, deep=False)  # 真实探测一次
    first_calls = m._bridge.count("query.execute")
    m.health(ds, deep=False)  # 命中缓存，不再探测
    assert m._bridge.count("query.execute") == first_calls


# ---------- 元数据缓存 ----------
def test_meta_cache_hit():
    settings = Settings(datasources=[_ds()], meta_cache_ttl=60)
    m = JDBCManager(settings)
    calls = {"n": 0}

    def producer():
        calls["n"] += 1
        return ["public"]

    r1 = m._cached_meta(("schemas", "g"), None, producer)
    r2 = m._cached_meta(("schemas", "g"), None, producer)
    assert r1 == r2 == ["public"]
    assert calls["n"] == 1
    stats = m.meta_cache_stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1


def test_meta_cache_ttl_zero_disables():
    settings = Settings(datasources=[_ds()], meta_cache_ttl=0)
    m = JDBCManager(settings)
    calls = {"n": 0}

    def producer():
        calls["n"] += 1
        return ["a"]

    m._cached_meta("k", None, producer)
    m._cached_meta("k", None, producer)
    assert calls["n"] == 2


# ---------- 显式事务（中继到桥的 connection.*Transaction） ----------
def _tx_respond() -> dict:
    state = {"active": False}

    def begin(params):
        if state["active"]:
            raise BridgeError("TRANSACTION_ACTIVE", "connection already has an active transaction")
        state["active"] = True
        return {"connectionId": params["connectionId"], "active": True, "elapsedMillis": 1, "timeoutMillis": 300000}

    def commit(params):
        state["active"] = False
        return {"status": "committed"}

    def rollback(params):
        state["active"] = False
        return {"status": "rolled_back"}

    def status(params):
        return {"active": state["active"], "elapsedMillis": 1} if state["active"] else {"active": False}

    return {
        "driver.register": {},
        "connection.open": connection_open_response(),
        "connection.beginTransaction": begin,
        "connection.commitTransaction": commit,
        "connection.rollbackTransaction": rollback,
        "connection.transactionStatus": status,
        "query.execute": {"hasResultSet": True, "queryId": "q1",
                          "columns": [{"name": "id", "label": "id"}],
                          "rows": [[1], [2]], "totalRows": 2, "truncated": False},
    }


def test_transaction_commit():
    ds = _ds()
    m = make_manager(Settings(datasources=[ds]), respond=_tx_respond())
    st = m.begin_transaction(ds)
    assert st["active"] is True

    res = m.execute_in_transaction(ds, "SELECT * FROM t")
    assert res["row_count"] == 2
    assert res["in_transaction"] is True

    with pytest.raises(JDBCError) as e:
        m.execute_in_transaction(ds, "DELETE FROM t")
    assert "confirm" in str(e.value)

    m.commit_transaction(ds)
    assert m.transaction_status(ds)["active"] is False


def test_transaction_rollback():
    ds = _ds()
    m = make_manager(Settings(datasources=[ds]), respond=_tx_respond())
    m.begin_transaction(ds)
    out = m.rollback_transaction(ds)
    assert out["status"] == "rolled_back"
    assert m.transaction_status(ds)["active"] is False


def test_transaction_requires_begin():
    ds = _ds()
    m = make_manager(Settings(datasources=[ds]), respond=_tx_respond())
    with pytest.raises(JDBCError) as e:
        m.execute_in_transaction(ds, "SELECT 1")
    assert "begin_transaction" in str(e.value)


def test_transaction_single_active():
    ds = _ds()
    m = make_manager(Settings(datasources=[ds]), respond=_tx_respond())
    m.begin_transaction(ds)
    with pytest.raises(JDBCError) as e:
        m.begin_transaction(ds)
    assert "活动事务" in str(e.value)


# ---------- 限流 ----------
def test_token_bucket():
    b = TokenBucket(rate=1.0, capacity=5)
    for _ in range(5):
        assert b.take() is True
    assert b.take() is False
    time.sleep(1.1)
    assert b.take() is True


async def test_rate_limit_middleware():
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    def _ok(request):
        return JSONResponse({"ok": True})

    inner = Starlette(routes=[Route("/", _ok), Route("/health", _ok)])
    app = RateLimitMiddleware(inner, qps=0.0, burst=2, exempt_paths=("/health",))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        assert (await client.get("/")).status_code == 200
        assert (await client.get("/")).status_code == 200
        assert (await client.get("/")).status_code == 429
        assert (await client.get("/health")).status_code == 200
