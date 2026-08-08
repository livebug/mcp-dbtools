"""安全与性能机制测试：熔断、危险写操作确认、大数据量导出。"""

from __future__ import annotations

import time
from contextlib import contextmanager

import pytest

from mcp_dbtools.config import DataSource, Settings
from mcp_dbtools.export import ExportManager, _join_row
from mcp_dbtools.jdbc import JDBCError, JDBCManager, _sql_kind


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
    settings = Settings(datasources=[ds])
    m = JDBCManager(settings)

    with pytest.raises(JDBCError) as e:
        m.execute_query(ds, "DELETE FROM t")
    assert "confirm" in str(e.value)

    # confirm=True 放行（走 fake cursor）
    cur = _FakeCursor([(1,)])
    m.cursor = _fake_cursor(cur)
    m.execute_query(ds, "DELETE FROM t", confirm=True)
    assert cur.executed_sql == "DELETE FROM t"


# ---------- 熔断 ----------
def test_circuit_breaker(tmp_path):
    ds = _ds()
    settings = Settings(datasources=[ds], circuit_fail_threshold=2, circuit_cooldown=1)
    m = JDBCManager(settings)

    class BoomCursor:
        def __init__(self):
            self.exec_count = 0

        def execute(self, sql, params=None):
            self.exec_count += 1
            raise RuntimeError("boom")

        @property
        def description(self):
            return None

        def fetchmany(self, n):
            return []

        @property
        def rowcount(self):
            return -1

        def close(self):
            pass

    boom = BoomCursor()
    m.cursor = _fake_cursor(boom)

    with pytest.raises(JDBCError):
        m.execute_query(ds, "SELECT 1")
    with pytest.raises(JDBCError):
        m.execute_query(ds, "SELECT 1")
    # 连续 2 次失败 -> 第 3 次直接熔断，不再执行 SQL（exec_count 仍为 2）
    with pytest.raises(JDBCError) as e:
        m.execute_query(ds, "SELECT 1")
    assert "熔断" in str(e.value)
    assert m.circuit_status(ds.name)["open"] is True
    assert boom.exec_count == 2


# ---------- 导出 ----------
def test_export_flow(tmp_path):
    rows = [(1, "张三"), (2, "李四")]
    fake_jm = _FakeJM(rows)
    em = ExportManager(export_dir=str(tmp_path), max_rows=1000, jdbc_manager=fake_jm)

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
    # download_url 存在
    assert task["download_url"].endswith(f"/export/{task['id']}/download")


def test_export_custom_delimiter_and_escape(tmp_path):
    fake_jm = _FakeJM([("a,b", "x\"y")])
    em = ExportManager(export_dir=str(tmp_path), max_rows=1000, jdbc_manager=fake_jm)
    task = em.start(_ds(), "SELECT 1", delimiter="|", include_header=False)
    s = _wait_status(em, task["id"])
    assert s["status"] == "succeeded"
    content = em.get_file_path(task["id"]).read_text(encoding="utf-8")
    # 第一列含 | 分隔符？不含（用 | 分隔），含引号的值转义
    assert 'a,b|"x""y"' in content


def test_join_row():
    assert _join_row(["a", "b", None], ",") == "a,b,"
    assert _join_row(["a,b", "c"], ",") == '"a,b",c'
    assert _join_row(["a", "b"], "|") == "a|b"


def test_export_truncate_limit(tmp_path):
    rows = [(i,) for i in range(10)]
    fake_jm = _FakeJM(rows)
    em = ExportManager(export_dir=str(tmp_path), max_rows=1000, jdbc_manager=fake_jm)
    task = em.start(_ds(), "SELECT 1", limit=3)
    s = _wait_status(em, task["id"])
    assert s["rows"] == 3
    assert s["truncated"] is True


# ---------- helpers ----------
class _FakeCursor:
    def __init__(self, result):
        self._result = list(result)
        self.executed_sql = None

    def execute(self, sql, params=None):
        self.executed_sql = sql

    @property
    def description(self):
        return [("id",), ("name",)]

    def fetchmany(self, size):
        out, self._result = self._result[:size], self._result[size:]
        return out

    @property
    def rowcount(self):
        return -1

    def close(self):
        pass


def _fake_cursor(cur):
    @contextmanager
    def _cm(ds=None):
        try:
            yield cur
        finally:
            pass

    return _cm


class _FakeJM:
    """模拟 JDBCManager 的最小对象（导出后台线程用）。"""

    def __init__(self, rows):
        self.rows = rows

    def _acquire(self, ds):
        return _FakeConn(self.rows)

    def _release(self, ds, conn):
        pass

    def _record_success(self, name):
        pass

    def _record_failure(self, name):
        pass


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)


def _wait_status(em, export_id, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = em.status(export_id)
        if s and s["status"] in ("succeeded", "failed"):
            return s
        time.sleep(0.02)
    raise TimeoutError(f"导出超时: {em.status(export_id)}")
