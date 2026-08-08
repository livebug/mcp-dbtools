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
from collections import deque
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
        self._locks: dict[str, threading.RLock] = {}
        self._jar_cache: dict[str, list[str]] = {}
        # 元数据结果缓存：key -> (expire_at, result)
        self._meta_cache: dict[Any, tuple[float, Any]] = {}
        self._meta_cache_lock = threading.RLock()
        self._meta_cache_hits = 0
        self._meta_cache_misses = 0
        # 连接池：每数据源最多 pool_size 个空闲连接
        self._pools: dict[str, deque] = {}
        self._pool_size = max(1, settings.pool_size)
        # 熔断状态：name -> {failures, open_until, half_open}
        self._circuits: dict[str, dict[str, Any]] = {}
        # 每个数据源的连接/执行统计（监控用）
        self._ds_stats: dict[str, dict[str, Any]] = {}
        # 显式事务：name -> {conn, begun_at, auto_commit}
        self._tx_conns: dict[str, dict[str, Any]] = {}
        # 后台探活/自动重连
        self._health_thread: threading.Thread | None = None
        self._health_stop = threading.Event()

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

    def _acquire(self, ds: DataSource) -> jaydebeapi.Connection:
        """从连接池获取一个可用连接（无则新建）。"""
        with self._lock_for(ds.name):
            pool = self._pools.get(ds.name) or deque()
            while pool:
                conn = pool.popleft()
                if self._is_alive(conn):
                    self._touch(ds.name, connected=True)
                    return conn
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
            conn = self._new_connection(ds)
            self._touch(ds.name, connected=True)
            return conn

    def _release(self, ds: DataSource, conn: jaydebeapi.Connection) -> None:
        """归还连接到连接池（池满则关闭）。"""
        with self._lock_for(ds.name):
            pool = self._pools.setdefault(ds.name, deque())
            if len(pool) < self._pool_size:
                pool.append(conn)
            else:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

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
        """获取数据源游标（连接池获取/归还）。"""
        conn = self._acquire(ds)
        cur = conn.cursor()
        try:
            yield cur
        finally:
            try:
                cur.close()
            except Exception:  # noqa: BLE001
                pass
            self._release(ds, conn)

    # ------------------------------------------------------------------
    # 健康检查 / 自动重连
    # ------------------------------------------------------------------
    def health(self, ds: DataSource, deep: bool = True) -> dict[str, Any]:
        """数据源健康检查：真实执行 SELECT 1 探测连接。

        deep=False 时优先返回缓存状态（不触发数据库调用）；
        但若从未探测过（last_health 为空）会做一次真实探测，保证首次结果可信。
        成功后自动重连（连接池取出的坏连接会被丢弃并新建）。
        """
        st = self._stats(ds.name)
        if not deep and st.get("last_health") is not None:
            with self._lock_for(ds.name):
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
            conn = self._acquire(ds)
            try:
                alive = self._is_alive(conn)
            finally:
                self._release(ds, conn)
        except JDBCError as exc:
            alive, err = False, str(exc)
        except Exception as exc:  # noqa: BLE001
            alive, err = False, str(exc)
        latency_ms = round((_time.monotonic() - t0) * 1000.0, 2)
        with self._lock_for(ds.name):
            st["last_health"] = datetime.now().isoformat(timespec="milliseconds")
            st["health_latency_ms"] = latency_ms
            st["connected"] = alive
            if alive:
                st["last_error"] = None
                self._record_success(ds.name)
            else:
                st["last_error"] = err
        return {
            "datasource": ds.name,
            "ok": alive,
            "latency_ms": latency_ms,
            "last_health": st["last_health"],
            "error": None if alive else err,
        }

    def start_health_checker(self, interval: float | None = None) -> None:
        """启动后台探活/自动重连线程（daemon），定期对每个数据源做 SELECT 1 探测。

        探测失败会更新状态并在下次请求时通过 _acquire 自动重连。
        """
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

        self._health_thread = threading.Thread(
            target=_loop, name="ds-health-checker", daemon=True
        )
        self._health_thread.start()

    def stop_health_checker(self) -> None:
        self._health_stop.set()
        if self._health_thread is not None:
            try:
                self._health_thread.join(timeout=2.0)
            except RuntimeError:
                pass

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
            c["half_open"] = True  # 半开：放行一次试探

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
        """查询数据源熔断状态（监控用）。"""
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
        """执行 SQL，返回 {columns, rows, row_count, truncated}。

        - 写操作（DELETE/UPDATE/INSERT/DROP 等）默认拦截，需 confirm=True 才执行；
        - limit 上限受 max_rows_limit 限制，默认返回 max_rows 条（防一次性拉大数据量）。
        """
        limit = limit if limit is not None else self.settings.max_rows
        limit = min(max(1, limit), self.settings.max_rows_limit)
        if not sql or not sql.strip():
            raise JDBCError("SQL 不能为空")
        if _sql_kind(sql) == "write" and not confirm:
            raise JDBCError(
                "检测到写操作语句（DELETE/UPDATE/INSERT/DROP 等），已拦截；"
                "如确认执行请传 confirm=true（将记录审计）"
            )
        self._check_circuit(ds.name)
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
            self._record_success(ds.name)
        except JDBCError:
            self._record_failure(ds.name)
            with self._lock_for(ds.name):
                st = self._stats(ds.name)
                st["error_count"] += 1
            raise
        except Exception as exc:  # noqa: BLE001
            self._record_failure(ds.name)
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
            "max_allowed": self.settings.max_rows_limit,
        }

    # ------------------------------------------------------------------
    # 元数据
    # ------------------------------------------------------------------
    def _cached_meta(self, key: Any, ttl: int | None, producer):
        """带 TTL 的元数据结果缓存（线程安全）。

        key 须可哈希（如 tuple）；ttl<=0 时不做缓存直接调用 producer。
        """
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
                # 超过上限：清掉最旧的（简单策略：保留最近 max_items//2 条）
                expired = sorted(
                    self._meta_cache.items(), key=lambda kv: kv[1][0]
                )
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

    def test_connection(self, ds: DataSource) -> dict[str, Any]:
        """测试连接并返回数据库产品信息。"""
        self._check_circuit(ds.name)
        conn = self._acquire(ds)
        try:
            meta = conn.jconn.getMetaData()
            product = str(meta.getDatabaseProductName())
            version = str(meta.getDatabaseProductVersion())
            self._record_success(ds.name)
        except Exception as exc:  # noqa: BLE001
            self._record_failure(ds.name)
            logger.warning("读取数据库元数据失败: %s", exc)
            product, version = ds.type, "unknown"
        finally:
            self._release(ds, conn)
        return {
            "datasource": ds.name,
            "ok": True,
            "product": product,
            "version": version,
        }

    def list_schemas(self, ds: DataSource) -> list[str]:
        return self._cached_meta(("schemas", ds.name), None, lambda: self._list_schemas_uncached(ds))

    def _list_schemas_uncached(self, ds: DataSource) -> list[str]:
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
        key = ("tables", ds.name, schema, search)
        return self._cached_meta(
            key, None, lambda: self._list_tables_uncached(ds, schema, search)
        )

    def _list_tables_uncached(
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
        key = ("describe", ds.name, table, schema)
        return self._cached_meta(
            key, None, lambda: self._describe_table_uncached(ds, table, schema)
        )

    def _describe_table_uncached(
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

    # ------------------------------------------------------------------
    # 显式事务（BEGIN / COMMIT / ROLLBACK）
    # ------------------------------------------------------------------
    def begin_transaction(self, ds: DataSource) -> dict[str, Any]:
        """开启事务：从连接池独占一个连接并关闭自动提交。

        同一数据源同时只允许一个活动事务；超时（tx_timeout）会自动回滚释放。
        """
        self._check_circuit(ds.name)
        with self._lock_for(ds.name):
            if ds.name in self._tx_conns:
                self._expire_tx_locked(ds.name)
            if ds.name in self._tx_conns:
                raise JDBCError(
                    f"数据源 {ds.name} 已有活动事务，请先 commit 或 rollback"
                )
            conn = self._acquire(ds)
            auto = bool(conn.jconn.getAutoCommit())
            try:
                conn.jconn.setAutoCommit(False)
            except Exception as exc:  # noqa: BLE001
                self._release(ds, conn)
                raise JDBCError(f"开启事务失败 ({ds.name}): {exc}") from exc
            self._tx_conns[ds.name] = {
                "conn": conn,
                "begun_at": _time.monotonic(),
                "auto_commit": auto,
            }
        return self.transaction_status(ds)

    def execute_in_transaction(
        self, ds: DataSource, sql: str, limit: int | None = None, confirm: bool = False
    ) -> dict[str, Any]:
        """在活动事务连接上执行 SQL（不自动提交）。"""
        if not sql or not sql.strip():
            raise JDBCError("SQL 不能为空")
        if _sql_kind(sql) == "write" and not confirm:
            raise JDBCError(
                "检测到写操作语句（DELETE/UPDATE/INSERT/DROP 等），已拦截；"
                "如确认执行请传 confirm=true（将记录审计）"
            )
        self._check_circuit(ds.name)
        with self._lock_for(ds.name):
            if ds.name not in self._tx_conns:
                raise JDBCError(f"数据源 {ds.name} 无活动事务，请先 begin_transaction")
            if self._expire_tx_locked(ds.name):
                raise JDBCError(
                    f"数据源 {ds.name} 事务已超时自动回滚（>{self.settings.tx_timeout}s），请重新 begin_transaction"
                )
            conn = self._tx_conns[ds.name]["conn"]
        limit = min(
            limit if limit is not None else self.settings.max_rows,
            self.settings.max_rows_limit,
        )
        t0 = _time.monotonic()
        try:
            cur = conn.cursor()
            try:
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
                if cur.rowcount is not None and not cols:
                    affected = int(cur.rowcount) if cur.rowcount >= 0 else 0
                else:
                    affected = 0
            finally:
                try:
                    cur.close()
                except Exception:  # noqa: BLE001
                    pass
            self._record_success(ds.name)
        except Exception as exc:  # noqa: BLE001
            self._record_failure(ds.name)
            with self._lock_for(ds.name):
                st = self._stats(ds.name)
                st["error_count"] += 1
            raise JDBCError(f"事务内执行 SQL 失败 ({ds.name}): {exc}") from exc
        cost_ms = (_time.monotonic() - t0) * 1000.0
        with self._lock_for(ds.name):
            st = self._stats(ds.name)
            st["query_count"] += 1
            st["rows_returned"] += len(rows)
            st["total_query_time_ms"] += cost_ms
        return {
            "datasource": ds.name,
            "in_transaction": True,
            "columns": cols if cols else [],
            "rows": _rows_to_jsonable(rows),
            "row_count": len(rows),
            "affected_rows": affected,
            "truncated": truncated,
            "execution_time_ms": round(cost_ms, 2),
            "hint": "事务尚未提交，可继续执行或 commit_transaction / rollback_transaction",
        }

    def commit_transaction(self, ds: DataSource) -> dict[str, Any]:
        """提交并结束事务，连接归还连接池。"""
        with self._lock_for(ds.name):
            if ds.name not in self._tx_conns:
                raise JDBCError(f"数据源 {ds.name} 无活动事务，请先 begin_transaction")
            if self._expire_tx_locked(ds.name):
                raise JDBCError(
                    f"数据源 {ds.name} 事务已超时自动回滚（>{self.settings.tx_timeout}s）"
                )
            rec = self._tx_conns.pop(ds.name)
        try:
            rec["conn"].jconn.commit()
            self._record_success(ds.name)
            outcome = "committed"
        except Exception as exc:  # noqa: BLE001
            self._record_failure(ds.name)
            try:
                rec["conn"].jconn.rollback()
            except Exception:  # noqa: BLE001
                pass
            outcome = "rollback_after_error"
            self._finalize_tx_conn(ds.name, rec)
            raise JDBCError(f"提交事务失败 ({ds.name}): {exc}") from exc
        self._finalize_tx_conn(ds.name, rec)
        return {"datasource": ds.name, "status": outcome}

    def rollback_transaction(self, ds: DataSource) -> dict[str, Any]:
        """回滚并结束事务，连接归还连接池。"""
        with self._lock_for(ds.name):
            if ds.name not in self._tx_conns:
                raise JDBCError(f"数据源 {ds.name} 无活动事务，请先 begin_transaction")
            self._expire_tx_locked(ds.name)
            rec = self._tx_conns.pop(ds.name)
        try:
            rec["conn"].jconn.rollback()
        except Exception as exc:  # noqa: BLE001
            self._finalize_tx_conn(ds.name, rec)
            raise JDBCError(f"回滚事务失败 ({ds.name}): {exc}") from exc
        self._finalize_tx_conn(ds.name, rec)
        return {"datasource": ds.name, "status": "rolled_back"}

    def transaction_status(self, ds: DataSource) -> dict[str, Any]:
        """查询数据源是否有活动事务及其已持续时间（秒）。"""
        with self._lock_for(ds.name):
            if ds.name not in self._tx_conns:
                return {"datasource": ds.name, "active": False}
            self._expire_tx_locked(ds.name)
            if ds.name not in self._tx_conns:
                return {
                    "datasource": ds.name,
                    "active": False,
                    "note": "事务已超时自动回滚",
                }
            rec = self._tx_conns[ds.name]
            return {
                "datasource": ds.name,
                "active": True,
                "elapsed_seconds": round(_time.monotonic() - rec["begun_at"], 2),
                "timeout_seconds": self.settings.tx_timeout,
            }

    def all_transaction_status(self) -> list[dict[str, Any]]:
        """所有数据源的事务状态（监控用）。"""
        return [self.transaction_status(ds) for ds in self.settings.datasources]

    def _expire_tx_locked(self, name: str) -> bool:
        """检测并回滚超时事务（调用方需持有 _lock_for(name)）。返回是否发生了超时回滚。"""
        rec = self._tx_conns.get(name)
        if not rec:
            return False
        if _time.monotonic() - rec["begun_at"] <= self.settings.tx_timeout:
            return False
        rec = self._tx_conns.pop(name)
        try:
            rec["conn"].jconn.rollback()
        except Exception:  # noqa: BLE001
            pass
        self._finalize_tx_conn(name, rec)
        logger.warning("数据源 %s 事务超时（%ss），已自动回滚", name, self.settings.tx_timeout)
        return True

    def _finalize_tx_conn(self, name: str, rec: dict[str, Any]) -> None:
        """恢复自动提交并归还连接池。"""
        try:
            rec["conn"].jconn.setAutoCommit(rec["auto_commit"])
        except Exception:  # noqa: BLE001
            pass
        with self._lock_for(name):
            pool = self._pools.setdefault(name, deque())
            if len(pool) < self._pool_size:
                pool.append(rec["conn"])
            else:
                try:
                    rec["conn"].close()
                except Exception:  # noqa: BLE001
                    pass

    def close_all(self) -> None:
        # 先处理活动事务连接：回滚并关闭，避免服务关闭时泄漏
        with self._lock:
            for name, rec in list(self._tx_conns.items()):
                try:
                    rec["conn"].jconn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    rec["conn"].close()
                except Exception:  # noqa: BLE001
                    pass
            self._tx_conns.clear()
        for name, pool in self._pools.items():
            for conn in pool:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        self._pools.clear()
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
        confirm: bool = False,
    ) -> dict[str, Any]:
        """执行 SQL 脚本文件，支持 ${VAR} 参数占位符。

        参数来源优先级：params -> 环境变量 -> 缺失报错。
        read_only=True 时仅允许 SELECT/SHOW/DESCRIBE/EXPLAIN/WITH 语句；
        read_only=False 且脚本含写操作（DELETE/UPDATE 等）时需 confirm=True 二次确认。
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
        else:
            # 非只读模式：脚本含写操作时需二次确认
            if any(_sql_kind(s) == "write" for s in statements) and not confirm:
                raise JDBCError(
                    "脚本包含写操作语句（DELETE/UPDATE/INSERT/DROP 等），已拦截；"
                    "如确认执行请传 read_only=false 且 confirm=true（将记录审计）"
                )

        self._check_circuit(ds.name)
        limit = min(
            limit if limit is not None else self.settings.max_rows,
            self.settings.max_rows_limit,
        )
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
                self._record_success(ds.name)
                with self._lock_for(ds.name):
                    st = self._stats(ds.name)
                    st["query_count"] += 1
                    st["rows_returned"] += len(rec.get("rows", []))
                    st["last_active"] = datetime.now().isoformat(timespec="milliseconds")
            except Exception as exc:  # noqa: BLE001
                self._record_failure(ds.name)
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


_WRITE_PREFIXES = (
    "insert", "update", "delete", "drop", "truncate", "alter", "create",
    "grant", "revoke", "merge", "replace", "call", "commit", "rollback",
    "vacuum", "copy", "comment", "reindex",
)


def _sql_kind(sql: str) -> str:
    """粗略识别语句类型：write / read（依据首个关键字，用于危险操作拦截）。"""
    s = sql.strip().lstrip("(").lower()
    for kw in _WRITE_PREFIXES:
        if s.startswith(kw):
            return "write"
    return "read"
