"""JDBC 连接管理层。

基于 jaydebeapi（JPype 桥接 JVM 与 JDBC），负责：
- JDBC 驱动 jar 解析（drivers/ 目录）；
- 每个数据源一个常驻连接 + 失败自动重连（线程安全）；
- SQL 查询执行与结果 JSON 化；
- 按方言（openGauss / TDH-Hive / 通用）提供元数据查询。
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time as _time
from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

import jaydebeapi

from .config import DataSource, Settings

logger = logging.getLogger(__name__)

# 通用方言（基于 information_schema），openGauss / GaussDB / PostgreSQL 均可使用
_PG_SYSTEM_SCHEMAS = "('pg_catalog','information_schema','db4ai','dbe_perf','dbe_pldeveloper','snapshot')"
_PG_SCHEMAS_SQL = "SELECT schema_name FROM information_schema.schemata ORDER BY 1"
_PG_TABLES_SQL = (
    "SELECT table_schema, table_name, table_type "
    "FROM information_schema.tables "
    f"WHERE table_schema NOT IN {_PG_SYSTEM_SCHEMAS} "
    "ORDER BY 1, 2"
)
_PG_COLUMNS_SQL = (
    "SELECT column_name, data_type, is_nullable, column_default "
    "FROM information_schema.columns "
    "WHERE table_schema = ? AND table_name = ? "
    "ORDER BY ordinal_position"
)

# TDH / Hive 方言
_HIVE_SCHEMAS_SQL = "SHOW DATABASES"
_HIVE_TABLES_SQL = "SHOW TABLES"  # 当前库；也支持 SHOW TABLES IN {schema}
_HIVE_DESCRIBE_SQL = "DESCRIBE {table}"  # 也支持 DESCRIBE {schema}.{table}


class JDBCError(Exception):
    """JDBC 操作异常。"""


def _jsonable(value: Any) -> Any:
    """将 JDBC 返回值转换为可 JSON 序列化的 Python 对象。"""
    if value is None:
        return None
    # 注意使用 isinstance：JPype 的 JDouble/JInt 等是 float/int 子类
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return "0x" + value.hex()
    if isinstance(value, Decimal):
        return _decimal_to_number(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}

    # Java 对象（JPype 包装），按模块前缀识别
    t = type(value)
    if t.__module__.startswith(("java.", "jpype.")):
        fq = f"{t.__module__}.{t.__name__}"
        if "BigDecimal" in fq:
            try:
                return _java_bigdecimal(value)
            except Exception:
                return str(value)
        if hasattr(value, "doubleValue"):
            try:
                d = float(value.doubleValue())
                if hasattr(value, "longValue"):
                    try:
                        lv = value.longValue()
                        if d == float(lv):
                            return int(lv)
                    except Exception:
                        pass
                return d
            except Exception:
                pass
        if hasattr(value, "toString"):
            return str(value.toString())
    return str(value)


def _decimal_to_number(value: Decimal) -> Any:
    try:
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    except (OverflowError, ValueError):
        return str(value)


def _java_bigdecimal(value: Any) -> Any:
    try:
        if value.scale() <= 0:
            return int(value)
        return float(value)
    except Exception:
        return str(value)


def _rows_to_jsonable(rows: list[tuple]) -> list[list[Any]]:
    return [[_jsonable(v) for v in row] for row in rows]


class JDBCManager:
    """管理到各数据源的 JDBC 连接。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.drivers_dir = Path(settings.drivers_dir)
        self._connections: dict[str, jaydebeapi.Connection] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._jar_cache: dict[str, list[str]] = {}
        self._meta_cache: dict[str, Any] = {}
        # 每个数据源的连接/执行统计（监控用）
        self._ds_stats: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # 监控统计
    # ------------------------------------------------------------------
    def _stats(self, name: str) -> dict[str, Any]:
        if name not in self._ds_stats:
            self._ds_stats[name] = {
                "name": name,
                "connected": False,
                "connected_at": None,
                "last_active": None,
                "query_count": 0,
                "error_count": 0,
                "rows_returned": 0,
                "total_query_time_ms": 0.0,
                "avg_query_time_ms": 0.0,
            }
        return self._ds_stats[name]

    def _touch(self, name: str, *, connected: bool | None = None) -> dict[str, Any]:
        st = self._stats(name)
        st["last_active"] = datetime.now().isoformat(timespec="milliseconds")
        if connected is not None:
            st["connected"] = connected
            if connected:
                st["connected_at"] = st["last_active"]
        return st

    def ds_status(self, name: str) -> dict[str, Any]:
        """单个数据源的连接/执行状态。"""
        with self._lock_for(name):
            st = dict(self._stats(name))
        qc = st["query_count"]
        st["avg_query_time_ms"] = round(st["total_query_time_ms"] / qc, 2) if qc else 0.0
        return st

    def all_status(self) -> list[dict[str, Any]]:
        """所有数据源的连接/执行状态。"""
        return [self.ds_status(ds.name) for ds in self.settings.datasources]

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------
    def _jar_paths(self, ds: DataSource) -> list[str]:
        if ds.name in self._jar_cache:
            return self._jar_cache[ds.name]
        paths: list[str] = []
        for jar in ds.jars:
            p = self.drivers_dir / jar
            if p.is_file():
                paths.append(str(p.resolve()))
            else:
                raise JDBCError(
                    f"数据源 {ds.name} 缺少 JDBC 驱动: {jar}。"
                    f"请将其放入 {self.drivers_dir}（可运行 scripts/download_drivers.py）"
                )
        self._jar_cache[ds.name] = paths
        return paths

    def _lock_for(self, name: str) -> threading.RLock:
        if name not in self._locks:
            self._locks[name] = threading.RLock()
        return self._locks[name]

    def _new_connection(self, ds: DataSource) -> jaydebeapi.Connection:
        jars = self._jar_paths(ds)
        if not ds.driver_class:
            raise JDBCError(f"数据源 {ds.name} 未配置 driver_class")
        logger.info("连接数据源 %s (%s)", ds.name, ds.type)
        try:
            conn = jaydebeapi.connect(
                ds.driver_class,
                ds.jdbc_url,
                [ds.username, ds.password] if ds.username is not None else None,
                jars,
            )
        except Exception as exc:  # noqa: BLE001
            raise JDBCError(f"连接数据源 {ds.name} 失败: {exc}") from exc
        conn.jconn.setAutoCommit(True)
        return conn

    def _get_connection(self, ds: DataSource) -> jaydebeapi.Connection:
        with self._lock_for(ds.name):
            conn = self._connections.get(ds.name)
            if conn is None or not self._is_alive(conn):
                try:
                    if conn is not None:
                        conn.close()
                except Exception:  # noqa: BLE001
                    pass
                conn = self._new_connection(ds)
                self._connections[ds.name] = conn
            self._touch(ds.name, connected=True)
            return conn

    @staticmethod
    def _is_alive(conn: jaydebeapi.Connection) -> bool:
        try:
            cur = conn.cursor()
            try:
                cur.execute("SELECT 1")
                cur.fetchall()
            finally:
                cur.close()
            return True
        except Exception:  # noqa: BLE001
            return False

    @contextmanager
    def cursor(self, ds: DataSource) -> Iterator[Any]:
        """获取数据源游标（自动建立/复用连接）。"""
        conn = self._get_connection(ds)
        cur = conn.cursor()
        try:
            yield cur
        finally:
            try:
                cur.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def execute_query(
        self, ds: DataSource, sql: str, limit: int | None = None
    ) -> dict[str, Any]:
        """执行只读 SQL，返回 {columns, rows, row_count, truncated}。"""
        limit = limit if limit is not None else self.settings.max_rows
        if not sql or not sql.strip():
            raise JDBCError("SQL 不能为空")
        t0 = _time.monotonic()
        # 将 limit 应用到内部以便批量抓取（LIMIT 语法差异大，故仅做客户端截断）
        try:
            with self.cursor(ds) as cur:
                cur.execute(sql)
                cols = [d[0] for d in (cur.description or [])]
                rows: list[tuple] = []
                truncated = False
                while True:
                    batch = cur.fetchmany(500)
                    if not batch:
                        break
                    rows.extend(batch)
                    if len(rows) >= limit:
                        rows = rows[:limit]
                        truncated = True
                        break
        except JDBCError:
            with self._lock_for(ds.name):
                st = self._stats(ds.name)
                st["error_count"] += 1
            raise
        except Exception as exc:  # noqa: BLE001
            with self._lock_for(ds.name):
                st = self._stats(ds.name)
                st["error_count"] += 1
            raise JDBCError(f"执行 SQL 失败 ({ds.name}): {exc}") from exc
        cost_ms = (_time.monotonic() - t0) * 1000.0
        with self._lock_for(ds.name):
            st = self._stats(ds.name)
            st["query_count"] += 1
            st["rows_returned"] += len(rows)
            st["total_query_time_ms"] += cost_ms
            st["last_active"] = datetime.now().isoformat(timespec="milliseconds")
        return {
            "datasource": ds.name,
            "columns": cols,
            "rows": _rows_to_jsonable(rows),
            "row_count": len(rows),
            "truncated": truncated,
            "execution_time_ms": round(cost_ms, 2),
        }

    # ------------------------------------------------------------------
    # 元数据
    # ------------------------------------------------------------------
    def test_connection(self, ds: DataSource) -> dict[str, Any]:
        """测试连接并返回数据库产品信息。"""
        conn = self._get_connection(ds)
        try:
            meta = conn.jconn.getMetaData()
            product = str(meta.getDatabaseProductName())
            version = str(meta.getDatabaseProductVersion())
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取数据库元数据失败: %s", exc)
            product, version = ds.type, "unknown"
        return {
            "datasource": ds.name,
            "ok": True,
            "product": product,
            "version": version,
        }

    def list_schemas(self, ds: DataSource) -> list[str]:
        if ds.type in ("tdh", "hive", "inceptor"):
            sql = _HIVE_SCHEMAS_SQL
        else:
            sql = _PG_SCHEMAS_SQL
        with self.cursor(ds) as cur:
            cur.execute(sql)
            return [str(r[0]) for r in cur.fetchall() if r]

    def list_tables(
        self, ds: DataSource, schema: str | None = None, search: str | None = None
    ) -> list[dict[str, str]]:
        if ds.type in ("tdh", "hive", "inceptor"):
            sql = (
                f"SHOW TABLES IN {_quote(schema)}" if schema else _HIVE_TABLES_SQL
            )
            with self.cursor(ds) as cur:
                cur.execute(sql)
                rows = cur.fetchall()
            tables = []
            for r in rows:
                table = str(r[0])
                if search and search.lower() not in table.lower():
                    continue
                tables.append({"schema": schema or "", "table": table})
            return tables

        # 通用 / openGauss 方言
        sql = _PG_TABLES_SQL
        params: list[Any] = []
        if schema:
            sql = (
                "SELECT table_schema, table_name, table_type "
                "FROM information_schema.tables "
                f"WHERE table_schema = ? AND table_schema NOT IN {_PG_SYSTEM_SCHEMAS} "
                "ORDER BY 2"
            )
            params.append(schema)
        with self.cursor(ds) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        tables = []
        for r in rows:
            s, t, tt = (str(r[0]), str(r[1]), str(r[2] if len(r) > 2 else ""))
            if search and search.lower() not in t.lower():
                continue
            tables.append({"schema": s, "table": t, "type": tt})
        return tables

    def describe_table(
        self, ds: DataSource, table: str, schema: str | None = None
    ) -> list[dict[str, str]]:
        if ds.type in ("tdh", "hive", "inceptor"):
            target = f"{_quote(schema)}.{_quote(table)}" if schema else _quote(table)
            with self.cursor(ds) as cur:
                cur.execute(_HIVE_DESCRIBE_SQL.format(table=target))
                rows = cur.fetchall()
            cols = []
            for r in rows:
                if not r or not str(r[0]).strip():
                    continue
                parts = [str(v) for v in r if v is not None]
                cols.append(
                    {
                        "column": parts[0],
                        "type": parts[1] if len(parts) > 1 else "",
                        "comment": parts[2] if len(parts) > 2 else "",
                    }
                )
            return cols

        if not schema:
            schema = self._guess_schema(ds, table)
        with self.cursor(ds) as cur:
            cur.execute(_PG_COLUMNS_SQL, [schema, table])
            rows = cur.fetchall()
        cols = []
        for r in rows:
            cols.append(
                {
                    "column": str(r[0]),
                    "data_type": str(r[1]),
                    "nullable": str(r[2]) if len(r) > 2 and r[2] is not None else "",
                    "default": str(r[3]) if len(r) > 3 and r[3] is not None else "",
                }
            )
        return cols

    def _guess_schema(self, ds: DataSource, table: str) -> str:
        """若未指定 schema，尝试在用户 schema 下找表。"""
        user = ds.username or "public"
        with self.cursor(ds) as cur:
            cur.execute(
                "SELECT table_schema FROM information_schema.tables "
                "WHERE table_name = ? ORDER BY table_schema = ?, table_schema",
                [table, user],
            )
            row = cur.fetchone()
        return str(row[0]) if row else "public"

    def close_all(self) -> None:
        for name, conn in self._connections.items():
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        self._connections.clear()
        for name in self._ds_stats:
            self._ds_stats[name]["connected"] = False

    # ------------------------------------------------------------------
    # 脚本执行
    # ------------------------------------------------------------------
    def resolve_script_file(self, script_path: str) -> Path:
        """解析并校验脚本文件位于脚本根目录内（防目录穿越）。"""
        root = Path(self.settings.script_root).resolve()
        p = Path(script_path).resolve()
        if not p.is_file():
            raise JDBCError(f"脚本文件不存在: {script_path}")
        try:
            p.relative_to(root)
        except ValueError as exc:
            raise JDBCError(
                f"脚本必须在脚本根目录内（{root}），禁止执行目录外文件: {p}"
            ) from exc
        return p

    def execute_script(
        self,
        ds: DataSource,
        script_path: str,
        params: dict[str, Any] | None = None,
        read_only: bool = True,
        limit: int | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """执行 SQL 脚本文件，支持 ${VAR} 参数占位符。

        参数来源优先级：params -> 环境变量 -> 缺失报错。
        read_only=True 时仅允许 SELECT/SHOW/DESCRIBE/EXPLAIN/WITH 语句。
        任一语句失败即停止后续。
        """
        p = self.resolve_script_file(script_path)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise JDBCError(f"读取脚本失败: {exc}") from exc

        resolved = resolve_script_params(text, params, env)
        statements = split_sql_script(resolved)
        if not statements:
            raise JDBCError(f"脚本 {p} 中没有可执行的 SQL 语句")

        if read_only:
            for s in statements:
                if not _is_readonly(s):
                    first = s.strip().splitlines()[0][:80] if s.strip() else ""
                    raise JDBCError(
                        f"只读模式禁止执行非查询语句（如需执行请设 read_only=false）: {first}"
                    )

        limit = limit if limit is not None else self.settings.max_rows
        results: list[dict[str, Any]] = []
        t0_total = _time.monotonic()
        for idx, sql in enumerate(statements, 1):
            rec: dict[str, Any] = {
                "index": idx,
                "ok": True,
                "statement_preview": sql[:120],
            }
            t0 = _time.monotonic()
            try:
                with self.cursor(ds) as cur:
                    cur.execute(sql)
                    if cur.description:
                        cols = [d[0] for d in cur.description]
                        rows: list[tuple] = []
                        truncated = False
                        while True:
                            batch = cur.fetchmany(500)
                            if not batch:
                                break
                            rows.extend(batch)
                            if len(rows) >= limit:
                                rows = rows[:limit]
                                truncated = True
                                break
                        rec["columns"] = cols
                        rec["rows"] = _rows_to_jsonable(rows)
                        rec["row_count"] = len(rows)
                        rec["truncated"] = truncated
                    else:
                        rec["affected_rows"] = (
                            int(cur.rowcount) if cur.rowcount is not None and cur.rowcount >= 0 else 0
                        )
                with self._lock_for(ds.name):
                    st = self._stats(ds.name)
                    st["query_count"] += 1
                    st["rows_returned"] += len(rec.get("rows", []))
                    st["last_active"] = datetime.now().isoformat(timespec="milliseconds")
            except Exception as exc:  # noqa: BLE001
                rec["ok"] = False
                rec["error"] = str(exc)
                with self._lock_for(ds.name):
                    self._stats(ds.name)["error_count"] += 1
            rec["execution_time_ms"] = round((_time.monotonic() - t0) * 1000.0, 2)
            results.append(rec)
            if not rec["ok"]:
                break  # 失败即停止后续语句

        return {
            "datasource": ds.name,
            "script_path": str(p),
            "params": params or {},
            "statement_count": len(results),
            "results": results,
            "total_time_ms": round((_time.monotonic() - t0_total) * 1000.0, 2),
        }


def _quote(ident: str) -> str:
    """对标识符做基础转义，防止注入与语法错误。"""
    return ident.replace("`", "").replace(";", "")


# ----------------------------------------------------------------------
# 脚本解析与参数替换
# ----------------------------------------------------------------------
_PARAM_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve_script_params(
    text: str, params: dict[str, Any] | None = None, env: dict[str, str] | None = None
) -> str:
    """替换脚本中的 ${VAR} 占位符：优先 params，其次环境变量，缺失则报错。"""
    params = params or {}
    env = env if env is not None else os.environ
    missing: list[str] = []

    def _repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key in params:
            return str(params[key])
        if key in env:
            return str(env[key])
        missing.append(key)
        return m.group(0)

    out = _PARAM_RE.sub(_repl, text)
    if missing:
        raise JDBCError(
            "缺少脚本参数，请在 params 或环境变量中提供: " + ", ".join(sorted(set(missing)))
        )
    return out


def split_sql_script(text: str) -> list[str]:
    """按分号拆分多条 SQL，忽略引号内分号与 -- 行注释。"""
    statements: list[str] = []
    current: list[str] = []
    in_single = in_double = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "'" and not in_double:
            in_single = not in_single
            current.append(c)
            i += 1
            continue
        if c == '"' and not in_single:
            in_double = not in_double
            current.append(c)
            i += 1
            continue
        if c == "-" and i + 1 < n and text[i + 1] == "-" and not in_single and not in_double:
            # 跳过 -- 行注释（不追加到当前语句）
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == ";" and not in_single and not in_double:
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            i += 1
            continue
        current.append(c)
        i += 1
    stmt = "".join(current).strip()
    if stmt:
        statements.append(stmt)
    return statements


def _is_readonly(sql: str) -> bool:
    """判断语句是否只读（SELECT/SHOW/DESCRIBE/EXPLAIN/WITH 开头）。"""
    s = sql.strip().lower()
    return s.startswith(("select", "show", "describe", "desc ", "explain", "with"))
