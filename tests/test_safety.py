"""安全与性能机制测试：熔断、危险写操作确认、大数据量导出。"""

from __future__ import annotations

import time

import pytest

from mcp_dbtools.bridge import BridgeError
from mcp_dbtools.config import DataSource, Settings
from mcp_dbtools.export import ExportManager, _join_row
from mcp_dbtools.jdbc import JDBCError, JDBCManager, _sql_kind

from fake_bridge import FakeBridge, connection_open_response, make_manager, query_result


def _ds(name="g"):
    return DataSource(name=name, type="gaussdb", jdbc_url="jdbc:opengauss://h:5432/db",
                      driver_class="org.opengauss.Driver", jars=["x.jar"])


# ---------- 语句类型识别 ----------
def test_sql_kind():
    assert _sql_kind("SELECT 1") == "read"
    assert _sql_kind("  with t as (select 1) select * from t") == "read"
    assert _sql_kind("DELETE FROM t") == "write"
    assert _sql_kind("update t set a=1") == "write"
    assert _sql_kind("DROP TABLE t") == "write"
    assert _sql_kind("insert into t values (1)") == "write"


# ---------- 危险写操作二次确认 ----------
def test_write_requires_confirm(tmp_path):
    ds = _ds()
    m = make_manager(Settings(datasources=[ds]))

    with pytest.raises(JDBCError) as e:
        m.execute_query(ds, "DELETE FROM t")
    assert "confirm" in str(e.value)
    # 拦截发生在请求桥之前
    assert m._bridge.count("query.execute") == 0


def test_write_allowed_with_confirm(tmp_path):
    ds = _ds()
    m = make_manager(Settings(datasources=[ds]), respond={
        "driver.register": {},
        "connection.open": connection_open_response(),
        "query.execute": query_result(update_count=3),
    })
    res = m.execute_query(ds, "DELETE FROM t", confirm=True)
    assert res["affected_rows"] == 3
    assert m._bridge.last_params("query.execute")["sql"] == "DELETE FROM t"


# ---------- 熔断 ----------
def test_circuit_breaker(tmp_path):
    ds = _ds()
    settings = Settings(datasources=[ds], circuit_fail_threshold=2, circuit_cooldown=1)
    calls = {"n": 0}

    def _boom(params):
        calls["n"] += 1
        raise BridgeError("SQL_ERROR", "boom")

    m = make_manager(settings, respond={
        "driver.register": {},
        "connection.open": connection_open_response(),
        "query.execute": _boom,
    })

    for _ in range(2):
        with pytest.raises(JDBCError):
            m.execute_query(ds, "SELECT 1")
    # 连续 2 次失败 -> 第 3 次直接熔断，不再请求桥
    with pytest.raises(JDBCError) as e:
        m.execute_query(ds, "SELECT 1")
    assert "熔断" in str(e.value)
    assert m.circuit_status(ds.name)["open"] is True
    assert calls["n"] == 2


# ---------- 导出 ----------
class _FakeJM:
    """模拟 JDBCManager 的 execute_paged 接口（导出后台线程用）。"""

    def __init__(self, rows, columns=None):
        self.rows = list(rows)
        self.columns = columns or ["id", "name"]

    def execute_paged(self, ds, sql, page_size=500, max_rows=None):
        cap = min(len(self.rows), max_rows if max_rows else len(self.rows))
        truncated = len(self.rows) > cap
        yield {"columns": self.columns, "rows": self.rows[:cap], "truncated": truncated}

    def _record_success(self, name):
        pass

    def _record_failure(self, name):
        pass


def test_export_flow(tmp_path):
    rows = [(1, "张三"), (2, "李四")]
    em = ExportManager(export_dir=str(tmp_path), max_rows=1000, jdbc_manager=_FakeJM(rows))

    task = em.start(_ds(), "SELECT id,name FROM t", delimiter=",", include_header=True)
    s = _wait_status(em, task["id"])
    assert s["status"] == "succeeded"
    assert s["rows"] == 2
    assert s["columns"] == ["id", "name"]

    path = em.get_file_path(task["id"])
    assert path is not None
    content = path.read_text(encoding="utf-8")
    lines = content.strip().splitlines()
    assert lines[0] == "id,name"
    assert "张三" in lines[1]
    assert task["download_url"].endswith(f"/api/v1/exports/{task['id']}/download")


def test_export_custom_delimiter_and_escape(tmp_path):
    em = ExportManager(export_dir=str(tmp_path), max_rows=1000, jdbc_manager=_FakeJM([("a,b", "x\"y")]))
    task = em.start(_ds(), "SELECT 1", delimiter="|", include_header=False)
    s = _wait_status(em, task["id"])
    assert s["status"] == "succeeded"
    content = em.get_file_path(task["id"]).read_text(encoding="utf-8")
    assert 'a,b|"x""y"' in content


def test_join_row():
    assert _join_row(["a", "b", None], ",") == "a,b,"
    assert _join_row(["a,b", "c"], ",") == '"a,b",c'
    assert _join_row(["a", "b"], "|") == "a|b"


def test_export_truncate_limit(tmp_path):
    rows = [(i,) for i in range(10)]
    em = ExportManager(export_dir=str(tmp_path), max_rows=1000, jdbc_manager=_FakeJM(rows))
    task = em.start(_ds(), "SELECT 1", limit=3)
    s = _wait_status(em, task["id"])
    assert s["rows"] == 3
    assert s["truncated"] is True


def _wait_status(em, export_id, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = em.status(export_id)
        if s and s["status"] in ("succeeded", "failed"):
            return s
        time.sleep(0.02)
    raise TimeoutError(f"导出超时: {em.status(export_id)}")
