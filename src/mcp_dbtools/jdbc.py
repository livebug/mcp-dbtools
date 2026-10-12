"""数据库访问层：通过 open-dbclient Java 桥（NDJSON-RPC）连接任意 JDBC 数据源。

与旧版（jaydebeapi/JPype 内嵌 JVM）相比，本模块不再持有任何数据库方言 SQL：
元数据一律走桥的 ``DatabaseMetaData`` 能力探测，查询走 ``query.execute`` 分页，
事务走 ``connection.begin/commit/rollbackTransaction``。方言适配这件事完全交给
JDBC 驱动自己回答，控制面只做策略（写操作拦截、行数上限、熔断、审计）。

对外 API 保持与 tools.py / server.py / export.py 的既有契约不变。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time as _time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .bridge import BridgeClient, BridgeError, BridgeNotRunningError
from .config import DataSource, Settings

logger = logging.getLogger(__name__)

_DB2_PING_SQL = "SELECT 1 FROM SYSIBM.SYSDUMMY1"
_PING_SQL = "SELECT 1"


def _parse_bridge_url(url: str) -> tuple[str, int]:
    """解析桥地址 "host:port"（可含 scheme），返回 (host, port)。"""
    raw = url.strip()
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    host, sep, port_s = raw.rpartition(":")
    if not sep:
        raise JDBCError(f"桥地址格式错误（应为 host:port）: {url}")
    try:
        port = int(port_s)
    except ValueError as exc:
        raise JDBCError(f"桥端口格式错误: {port_s}") from exc
    if not 0 < port < 65536:
        raise JDBCError(f"桥端口超出范围: {port}")
    return host or "127.0.0.1", port

# 桥错误码 → 控制面用户可读中文（桥内部消息是英文，这里做一层本地化）
_BRIDGE_ERROR_MESSAGES = {
    "TRANSACTION_ACTIVE": "数据源已有活动事务，请先 commit 或 rollback",
    "TRANSACTION_NOT_FOUND": "无活动事务，请先 begin_transaction",
    "TRANSACTION_TIMEOUT": "事务已超时并自动回滚",
    "CONNECTION_NOT_FOUND": "连接未打开",
    "UNSUPPORTED": "驱动不支持该操作",
    "DRIVER_NOT_FOUND": "未找到 JDBC 驱动",
    "DRIVER_LOAD_FAILED": "JDBC 驱动加载失败",
    "CONNECTION_FAILED": "数据库连接失败",
}


class JDBCError(Exception):
    """数据库操作错误。"""


# ----------------------------------------------------------------------
# 语句分类与脚本解析（与控制面策略相关，纯函数，无 JDBC 依赖）
# ----------------------------------------------------------------------
_WRITE_PREFIXES = (
    "insert", "update", "delete", "drop", "truncate", "alter", "create",
    "grant", "revoke", "merge", "replace", "call", "commit", "rollback",
    "vacuum", "copy", "comment", "reindex",
)

_PARAM_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _is_readonly(sql: str) -> bool:
    """判断语句是否只读（SELECT/SHOW/DESCRIBE/EXPLAIN/WITH 开头）。"""
    s = sql.strip().lower()
    return s.startswith(("select", "show", "describe", "desc ", "explain", "with"))


def _sql_kind(sql: str) -> str:
    """粗略识别语句类型：write / read（依据首个关键字，用于危险操作拦截）。"""
    s = sql.strip().lstrip("(").lower()
    for kw in _WRITE_PREFIXES:
        if s.startswith(kw):
            return "write"
    return "read"


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


class JDBCManager:
    """管理到各数据源的 JDBC 访问（通过 Java 桥）。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        remote = _parse_bridge_url(settings.bridge_url) if settings.bridge_url else None
        self._bridge = BridgeClient(
            settings.bridge_jar, java=settings.java, request_timeout=settings.bridge_timeout, remote=remote
        )
        self._bridge_started = False
        self._opened: set[str] = set()
        self._capabilities: dict[str, dict[str, Any]] = {}
        self._state_lock = threading.RLock()

        # 元数据结果缓存：key -> (expire_at, result)
        self._meta_cache: dict[Any, tuple[float, Any]] = {}
        self._meta_cache_lock = threading.RLock()
        self._meta_cache_hits = 0
        self._meta_cache_misses = 0

        # 熔断状态：name -> {failures, open_until, half_open}
        self._circuits: dict[str, dict[str, Any]] = {}
        # 每个数据源的连接/执行统计（监控用）
        self._ds_stats: dict[str, dict[str, Any]] = {}
        # 后台探活
        self._health_thread: threading.Thread | None = None
        self._health_stop = threading.Event()

    # ------------------------------------------------------------------
    # 桥生命周期
    # ------------------------------------------------------------------
    def _ensure_bridge(self) -> None:
        """启动桥子进程并注册驱动（幂等）。"""
        with self._state_lock:
            if self._bridge_started and self._bridge.alive:
                return
            self._bridge.start()
            self._bridge_started = True
            self._register_drivers()

    def _register_drivers(self) -> None:
        """把全部数据源的驱动 jar 一次性注册给桥。"""
        jars: list[str] = []
        classes: list[str] = []
        seen: set[str] = set()
        for ds in self.settings.datasources:
            for jar in ds.jars:
                p = (Path(self.settings.drivers_dir) / jar).resolve()
                if p.is_file() and str(p) not in seen:
                    seen.add(str(p))
                    jars.append(str(p))
            if ds.driver_class and ds.driver_class not in classes:
                classes.append(ds.driver_class)
        result = self._bridge.request(
            "driver.register", {"jarPaths": jars, "driverClassNames": classes}
        )
        failures = result.get("failures") or []
        if failures:
            for failure in failures:
                logger.warning("驱动加载失败: %s", failure)

    def _ensure_connection(self, ds: DataSource) -> None:
        """惰性打开数据源连接（幂等；桥按 connectionId 复用连接池）。"""
        self._ensure_bridge()
        if ds.name in self._opened:
            return
        with self._state_lock:
            if ds.name in self._opened:
                return
            result = self._request(
                "connection.open",
                {
                    "connectionId": ds.name,
                    "driverClassName": ds.driver_class,
                    "url": ds.jdbc_url,
                    "user": ds.username,
                    "password": ds.password,
                    "properties": ds.properties,
                    "poolSize": max(1, self.settings.pool_size),
                    "connectTimeoutSeconds": max(1, self.settings.connect_timeout),
                },
            )
            self._capabilities[ds.name] = result.get("capabilities") or {}
            self._opened.add(ds.name)
            self._touch(ds.name, connected=True)
            logger.info("已连接数据源 %s", ds.name)

    def _request(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        """发请求并把桥错误统一映射为 JDBCError。"""
        try:
            return self._bridge.request(method, params, timeout=timeout)
        except BridgeError as exc:
            message = _BRIDGE_ERROR_MESSAGES.get(exc.code, exc.message)
            raise JDBCError(f"{method} 失败: {message}") from exc
        except BridgeNotRunningError as exc:
            raise JDBCError(f"数据库桥不可用: {exc}") from exc
        except TimeoutError as exc:
            raise JDBCError(f"{method} 超时") from exc

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
                "last_error": None,
                "last_health": None,
                "health_latency_ms": None,
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
        st = dict(self._stats(name))
        qc = st["query_count"]
        st["avg_query_time_ms"] = round(st["total_query_time_ms"] / qc, 2) if qc else 0.0
        return st

    def all_status(self) -> list[dict[str, Any]]:
        return [self.ds_status(ds.name) for ds in self.settings.datasources]

    def _record_statement(self, ds: DataSource, cost_ms: float, rows: int) -> None:
        with self._state_lock:
            st = self._stats(ds.name)
            st["query_count"] += 1
            st["rows_returned"] += rows
            st["total_query_time_ms"] += cost_ms
            st["last_active"] = datetime.now().isoformat(timespec="milliseconds")

    # ------------------------------------------------------------------
    # 熔断
    # ------------------------------------------------------------------
    def _check_circuit(self, name: str) -> None:
        c = self._circuits.get(name)
        if not c:
            return
        now = _time.time()
        if c["open_until"] and now < c["open_until"]:
            raise JDBCError(
                f"数据源 {name} 处于熔断状态（连续失败 {c['failures']} 次），"
                f"请 {int(c['open_until'] - now) + 1}s 后重试"
            )
        if c["open_until"] and now >= c["open_until"]:
            c["open_until"] = 0.0
            c["half_open"] = True

    def _record_success(self, name: str) -> None:
        c = self._circuits.setdefault(name, {"failures": 0, "open_until": 0.0, "half_open": False})
        c["failures"] = 0
        c["half_open"] = False
        c["open_until"] = 0.0

    def _record_failure(self, name: str) -> None:
        c = self._circuits.setdefault(name, {"failures": 0, "open_until": 0.0, "half_open": False})
        c["failures"] += 1
        if c["failures"] >= self.settings.circuit_fail_threshold:
            c["open_until"] = _time.time() + self.settings.circuit_cooldown
            c["half_open"] = False

    def circuit_status(self, name: str) -> dict[str, Any]:
        c = self._circuits.get(name)
        if not c:
            return {"name": name, "open": False, "failures": 0}
        now = _time.time()
        return {
            "name": name,
            "open": bool(c["open_until"] and now < c["open_until"]),
            "failures": c["failures"],
            "half_open": c.get("half_open", False),
            "cooldown_left_seconds": max(0, int(c["open_until"] - now)) if c["open_until"] else 0,
        }

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def execute_query(
        self, ds: DataSource, sql: str, limit: int | None = None, confirm: bool = False
    ) -> dict[str, Any]:
        """执行 SQL，返回 {columns, rows, row_count, truncated, execution_time_ms}。

        - 写操作默认拦截，需 confirm=True 才执行（并记审计）；
        - limit 上限受 max_rows_limit 限制。
        """
        if not sql or not sql.strip():
            raise JDBCError("SQL 不能为空")
        if _sql_kind(sql) == "write" and not confirm:
            raise JDBCError(
                "检测到写操作语句（DELETE/UPDATE/INSERT/DROP 等），已拦截；"
                "如确认执行请传 confirm=true（将记录审计）"
            )
        self._check_circuit(ds.name)
        limit = min(max(1, limit if limit is not None else self.settings.max_rows), self.settings.max_rows_limit)
        return self._execute_raw(ds, sql, limit)

    def _execute_raw(self, ds: DataSource, sql: str, limit: int) -> dict[str, Any]:
        """执行 SQL（不重复做写操作拦截；调用方保证已通过策略检查）。"""
        self._ensure_connection(ds)
        t0 = _time.monotonic()
        try:
            result = self._request(
                "query.execute",
                {
                    "connectionId": ds.name,
                    "sql": sql,
                    "pageSize": limit,
                    "maxRows": limit,
                    "columnRemarks": False,
                },
            )
            self._record_success(ds.name)
        except Exception as exc:  # noqa: BLE001
            self._record_failure(ds.name)
            self._stats(ds.name)["error_count"] += 1
            self._stats(ds.name)["last_error"] = str(exc)
            raise
        cost_ms = round((_time.monotonic() - t0) * 1000.0, 2)

        if not result.get("hasResultSet"):
            affected = int(result.get("updateCount") or 0)
            self._record_statement(ds, cost_ms, 0)
            return {
                "datasource": ds.name,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "affected_rows": affected,
                "execution_time_ms": cost_ms,
                "max_allowed": limit,
            }

        columns = [str(c.get("label") or c.get("name") or "") for c in result.get("columns", [])]
        rows = result.get("rows", []) or []
        self._record_statement(ds, cost_ms, len(rows))
        return {
            "datasource": ds.name,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": bool(result.get("truncated", False)),
            "execution_time_ms": cost_ms,
            "max_allowed": limit,
        }

    def execute_paged(
        self, ds: DataSource, sql: str, page_size: int = 500, max_rows: int | None = None
    ) -> Iterator[dict[str, Any]]:
        """分页执行查询，逐页 yield {"columns", "rows", "truncated"}。

        首个 yield 带 columns（后续为 None），truncated 表示命中行数上限。
        供 export.py 流式落盘，避免一次性把大结果集塞进内存。
        """
        self._ensure_connection(ds)
        cap = max_rows or self.settings.export_max_rows
        result = self._request(
            "query.execute",
            {
                "connectionId": ds.name,
                "sql": sql,
                "pageSize": page_size,
                "maxRows": cap,
                "columnRemarks": False,
            },
        )
        query_id = result.get("queryId")
        if not result.get("hasResultSet"):
            yield {"columns": [], "rows": [], "truncated": False}
            return
        columns = [str(c.get("label") or c.get("name") or "") for c in result.get("columns", [])]
        yield {"columns": columns, "rows": result.get("rows", []), "truncated": bool(result.get("truncated", False))}

        total = int(result.get("totalRows") or 0)
        offset = page_size
        while offset < total:
            page = self._request(
                "query.fetch", {"queryId": query_id, "offset": offset, "limit": page_size}
            )
            rows = page.get("rows", []) or []
            if not rows:
                break
            yield {"columns": None, "rows": rows, "truncated": False}
            offset += len(rows)

        try:
            self._request("query.close", {"queryId": query_id}, timeout=5.0)
        except Exception:  # noqa: BLE001 结果集清理尽力而为
            pass

    def test_connection(self, ds: DataSource) -> dict[str, Any]:
        """测试连接并返回数据库产品信息。"""
        self._check_circuit(ds.name)
        self._ensure_connection(ds)
        caps = self._capabilities.get(ds.name, {})
        self._record_success(ds.name)
        return {
            "datasource": ds.name,
            "ok": True,
            "product": caps.get("databaseProductName") or ds.type,
            "version": caps.get("databaseProductVersion") or "",
        }

    # ------------------------------------------------------------------
    # 元数据（走 DatabaseMetaData，无方言 SQL）
    # ------------------------------------------------------------------
    def _cached_meta(self, key: Any, ttl: int | None, producer) -> Any:
        ttl = self.settings.meta_cache_ttl if ttl is None else ttl
        if ttl <= 0:
            return producer()
        now = _time.monotonic()
        with self._meta_cache_lock:
            hit = self._meta_cache.get(key)
            if hit and hit[0] > now:
                self._meta_cache_hits += 1
                return hit[1]
            self._meta_cache_misses += 1
        result = producer()
        with self._meta_cache_lock:
            self._meta_cache[key] = (now + ttl, result)
            if len(self._meta_cache) > self.settings.meta_cache_max_items:
                expired = sorted(self._meta_cache.items(), key=lambda kv: kv[1][0])
                for k, _ in expired[: len(self._meta_cache) - self.settings.meta_cache_max_items // 2]:
                    self._meta_cache.pop(k, None)
        return result

    def meta_cache_stats(self) -> dict[str, Any]:
        with self._meta_cache_lock:
            return {
                "items": len(self._meta_cache),
                "hits": self._meta_cache_hits,
                "misses": self._meta_cache_misses,
            }

    def list_schemas(self, ds: DataSource) -> list[str]:
        return self._cached_meta(("schemas", ds.name), None, lambda: self._list_schemas_uncached(ds))

    def _list_schemas_uncached(self, ds: DataSource) -> list[str]:
        self._ensure_connection(ds)
        result = self._request("metadata.schemas", {"connectionId": ds.name})
        return [str(s) for s in result.get("schemas", [])]

    def list_tables(
        self, ds: DataSource, schema: str | None = None, search: str | None = None
    ) -> list[dict[str, str]]:
        key = ("tables", ds.name, schema, search)
        return self._cached_meta(key, None, lambda: self._list_tables_uncached(ds, schema, search))

    def _list_tables_uncached(
        self, ds: DataSource, schema: str | None = None, search: str | None = None
    ) -> list[dict[str, str]]:
        self._ensure_connection(ds)
        params: dict[str, Any] = {"connectionId": ds.name, "namePattern": "%"}
        if schema:
            params["schema"] = schema
        if search:
            params["namePattern"] = f"%{search}%"
        result = self._request("metadata.tables", params)
        tables: list[dict[str, str]] = []
        for t in result.get("tables", []):
            tables.append(
                {
                    "schema": str(t.get("schema") or ""),
                    "table": str(t.get("name") or ""),
                    "type": str(t.get("type") or ""),
                }
            )
        return tables

    def describe_table(
        self, ds: DataSource, table: str, schema: str | None = None
    ) -> list[dict[str, str]]:
        key = ("describe", ds.name, table, schema)
        return self._cached_meta(key, None, lambda: self._describe_table_uncached(ds, table, schema))

    def _describe_table_uncached(
        self, ds: DataSource, table: str, schema: str | None = None
    ) -> list[dict[str, str]]:
        self._ensure_connection(ds)
        params: dict[str, Any] = {"connectionId": ds.name, "table": table}
        if schema:
            params["schema"] = schema
        result = self._request("metadata.columns", params)
        cols: list[dict[str, str]] = []
        for c in result.get("columns", []):
            cols.append(
                {
                    "column": str(c.get("name") or ""),
                    "type": str(c.get("displayType") or c.get("typeName") or ""),
                    "nullable": "Y" if c.get("nullable") else "N",
                    "default": str(c.get("defaultValue") or ""),
                    "comment": str(c.get("remarks") or ""),
                }
            )
        return cols

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------
    @staticmethod
    def _ping_sql(ds: DataSource) -> str:
        if ds.type == "db2":
            return _DB2_PING_SQL
        return _PING_SQL

    def health(self, ds: DataSource, deep: bool = True) -> dict[str, Any]:
        """数据源健康检查。deep=False 时返回缓存状态（若从未探测则做一次真实探测）。"""
        st = self._stats(ds.name)
        if not deep and st.get("last_health") is not None:
            return {
                "datasource": ds.name,
                "ok": bool(st["connected"]),
                "latency_ms": st.get("health_latency_ms"),
                "last_health": st.get("last_health"),
                "error": st.get("last_error"),
            }
        self._check_circuit(ds.name)
        t0 = _time.monotonic()
        try:
            self._ensure_connection(ds)
            self._request(
                "query.execute",
                {"connectionId": ds.name, "sql": self._ping_sql(ds), "pageSize": 1, "maxRows": 1},
            )
            alive, err = True, None
            self._record_success(ds.name)
        except Exception as exc:  # noqa: BLE001
            alive, err = False, str(exc)
        latency_ms = round((_time.monotonic() - t0) * 1000.0, 2)
        st["last_health"] = datetime.now().isoformat(timespec="milliseconds")
        st["health_latency_ms"] = latency_ms
        st["connected"] = alive
        st["last_error"] = err
        return {
            "datasource": ds.name,
            "ok": alive,
            "latency_ms": latency_ms,
            "last_health": st["last_health"],
            "error": err,
        }

    def start_health_checker(self, interval: float | None = None) -> None:
        if self._health_thread is not None and self._health_thread.is_alive():
            return
        interval = interval or max(5, self.settings.health_check_interval)
        self._health_stop.clear()

        def _loop() -> None:
            while not self._health_stop.wait(interval):
                for ds in list(self.settings.datasources):
                    try:
                        self.health(ds, deep=True)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("健康检查失败 %s: %s", ds.name, exc)

        self._health_thread = threading.Thread(target=_loop, name="ds-health-checker", daemon=True)
        self._health_thread.start()

    def stop_health_checker(self) -> None:
        self._health_stop.set()
        if self._health_thread is not None:
            try:
                self._health_thread.join(timeout=2.0)
            except RuntimeError:
                pass

    # ------------------------------------------------------------------
    # 显式事务（BEGIN / COMMIT / ROLLBACK，事务由桥按 connectionId 管理）
    # ------------------------------------------------------------------
    def begin_transaction(self, ds: DataSource) -> dict[str, Any]:
        self._check_circuit(ds.name)
        self._ensure_connection(ds)
        result = self._request(
            "connection.beginTransaction",
            {"connectionId": ds.name, "timeoutSeconds": max(0, self.settings.tx_timeout)},
        )
        return {"datasource": ds.name, "active": True, **self._tx_status_fields(result)}

    def execute_in_transaction(
        self, ds: DataSource, sql: str, limit: int | None = None, confirm: bool = False
    ) -> dict[str, Any]:
        """在活动事务连接上执行 SQL（不自动提交）。事务已激活时 query.execute 会自动路由到该连接。"""
        if not sql or not sql.strip():
            raise JDBCError("SQL 不能为空")
        if _sql_kind(sql) == "write" and not confirm:
            raise JDBCError(
                "检测到写操作语句（DELETE/UPDATE/INSERT/DROP 等），已拦截；"
                "如确认执行请传 confirm=true（将记录审计）"
            )
        status = self.transaction_status(ds)
        if not status.get("active"):
            raise JDBCError(f"数据源 {ds.name} 无活动事务，请先 begin_transaction")
        self._check_circuit(ds.name)
        limit = min(max(1, limit if limit is not None else self.settings.max_rows), self.settings.max_rows_limit)
        result = self._execute_raw(ds, sql, limit)
        result["in_transaction"] = True
        return result

    def commit_transaction(self, ds: DataSource) -> dict[str, Any]:
        result = self._request("connection.commitTransaction", {"connectionId": ds.name})
        self._record_success(ds.name)
        return {"datasource": ds.name, "status": result.get("status", "committed")}

    def rollback_transaction(self, ds: DataSource) -> dict[str, Any]:
        result = self._request("connection.rollbackTransaction", {"connectionId": ds.name})
        self._record_success(ds.name)
        return {"datasource": ds.name, "status": result.get("status", "rolled_back")}

    @staticmethod
    def _tx_status_fields(result: dict[str, Any]) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if "elapsedMillis" in result:
            fields["elapsed_seconds"] = round(float(result["elapsedMillis"]) / 1000.0, 2)
        if "timeoutMillis" in result:
            fields["timeout_seconds"] = int(result["timeoutMillis"]) // 1000
        return fields

    def transaction_status(self, ds: DataSource) -> dict[str, Any]:
        result = self._request("connection.transactionStatus", {"connectionId": ds.name})
        payload: dict[str, Any] = {
            "datasource": ds.name,
            "active": bool(result.get("active")),
        }
        if result.get("active"):
            payload.update(self._tx_status_fields(result))
            if result.get("expired"):
                payload["note"] = "事务已超时，将在下一次操作时自动回滚"
        return payload

    def all_transaction_status(self) -> list[dict[str, Any]]:
        return [self.transaction_status(ds) for ds in self.settings.datasources]

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------
    def close_all(self) -> None:
        """关闭所有数据源连接并停止桥进程。"""
        with self._state_lock:
            opened = list(self._opened)
        for name in opened:
            try:
                self._request("connection.close", {"connectionId": name}, timeout=5.0)
            except Exception:  # noqa: BLE001
                pass
        with self._state_lock:
            self._opened.clear()
            self._capabilities.clear()
            for name in self._ds_stats:
                self._ds_stats[name]["connected"] = False
        try:
            self._bridge.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("关闭桥失败: %s", exc)
        self._bridge_started = False

    # ------------------------------------------------------------------
    # 脚本执行（文件层面，与控制面策略相关）
    # ------------------------------------------------------------------
    def save_script(self, name: str, content: str, overwrite: bool = False) -> dict[str, Any]:
        if not name or not name.strip():
            raise JDBCError("脚本名不能为空")
        if not content or not content.strip():
            raise JDBCError("脚本内容不能为空")

        root = Path(self.settings.script_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        target = (root / name.strip()).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise JDBCError(
                f"脚本必须写入脚本根目录内（{self._script_root_display()}），禁止写到目录外: {name}"
            ) from exc
        if target.is_dir():
            raise JDBCError(f"目标已存在且为目录: {name}")
        existed = target.exists()
        if existed and not overwrite:
            raise JDBCError(f"脚本已存在: {name}（如需覆盖请传 overwrite=true）")

        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise JDBCError(f"写入脚本失败: {exc}") from exc
        rel = str(target.relative_to(root))
        size = len(content.encode("utf-8"))
        logger.info("保存脚本: %s (%d 字节) -> %s", rel, size, target)
        return {
            "saved": rel,
            "bytes": size,
            "overwritten": existed,
            "message": f"脚本已保存: {rel}；执行时 execute_script 的 script_path 直接传 \"{rel}\" 即可",
            "_audit_extra": {
                "script_name": rel,
                "script_content": content,
                "script_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
                "bytes": size,
            },
        }

    @staticmethod
    def _safe_path_display(value: str | Path) -> str:
        raw = str(value).replace("\\", "/").rstrip("/")
        if not raw:
            return ""
        if not re.match(r"^(/|[A-Za-z]:/|//)", raw):
            return raw
        parts = [seg for seg in raw.split("/") if seg and not re.fullmatch(r"[A-Za-z]:", seg)]
        return "/".join(parts[-2:]) or raw

    def _script_root_display(self) -> str:
        return self._safe_path_display(self.settings.script_root) or "scripts/sql"

    def _script_rel(self, p: Path) -> str:
        root = Path(self.settings.script_root).resolve()
        try:
            return str(p.relative_to(root))
        except ValueError:
            return p.name

    def resolve_script_file(self, script_path: str) -> Path:
        if not script_path or not str(script_path).strip():
            raise JDBCError("脚本路径不能为空")
        root = Path(self.settings.script_root).resolve()
        raw = Path(str(script_path).strip())
        candidates = [raw] if raw.is_absolute() else [root / raw, raw]
        for cand in candidates:
            p = cand.resolve()
            if not p.is_file():
                continue
            try:
                p.relative_to(root)
            except ValueError as exc:
                raise JDBCError(
                    f"脚本必须在脚本根目录内（{self._script_root_display()}），"
                    f"禁止执行目录外文件: {script_path}"
                ) from exc
            return p
        raise JDBCError(f"脚本文件不存在: {script_path}（脚本根目录: {self._script_root_display()}）")

    def execute_script(
        self,
        ds: DataSource,
        script_path: str,
        params: dict[str, Any] | None = None,
        read_only: bool = True,
        limit: int | None = None,
        env: dict[str, str] | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        p = self.resolve_script_file(script_path)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise JDBCError(f"读取脚本失败: {exc}") from exc

        resolved = resolve_script_params(text, params, env)
        statements = split_sql_script(resolved)
        if not statements:
            raise JDBCError(f"脚本 {p.name} 中没有可执行的 SQL 语句")

        if read_only:
            for s in statements:
                if not _is_readonly(s):
                    first = s.strip().splitlines()[0][:80] if s.strip() else ""
                    raise JDBCError(f"只读模式禁止执行非查询语句（如需执行请设 read_only=false）: {first}")
        else:
            if any(_sql_kind(s) == "write" for s in statements) and not confirm:
                raise JDBCError(
                    "脚本包含写操作语句（DELETE/UPDATE/INSERT/DROP 等），已拦截；"
                    "如确认执行请传 read_only=false 且 confirm=true（将记录审计）"
                )

        self._check_circuit(ds.name)
        limit = min(limit if limit is not None else self.settings.max_rows, self.settings.max_rows_limit)
        results: list[dict[str, Any]] = []
        t0_total = _time.monotonic()
        for idx, sql in enumerate(statements, 1):
            rec: dict[str, Any] = {"index": idx, "ok": True, "statement_preview": sql[:120]}
            t0 = _time.monotonic()
            try:
                raw = self._execute_raw(ds, sql, limit)
                rec.update(
                    {
                        "columns": raw.get("columns", []),
                        "rows": raw.get("rows", []),
                        "row_count": raw.get("row_count", 0),
                        "truncated": raw.get("truncated", False),
                        "affected_rows": raw.get("affected_rows", 0),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                rec["ok"] = False
                rec["error"] = str(exc)
            rec["execution_time_ms"] = round((_time.monotonic() - t0) * 1000.0, 2)
            results.append(rec)
            if not rec["ok"]:
                break

        return {
            "datasource": ds.name,
            "script_path": self._script_rel(p),
            "params": params or {},
            "statement_count": len(results),
            "results": results,
            "total_time_ms": round((_time.monotonic() - t0_total) * 1000.0, 2),
            "_audit_extra": {
                "script_content": resolved,
                "script_raw": text,
                "script_sha256": hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16],
                "statement_count": len(results),
                "params_used": params or {},
            },
        }
