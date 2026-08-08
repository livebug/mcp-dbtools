"""配置加载：数据源定义 + 服务端设置。

支持：
- 通过环境变量 MCP_DBTOOLS_CONFIG 指定配置文件路径；
- 配置值中的 {ENV:VAR} 会被替换为对应环境变量的值（常用于密码，避免明文入库）；
- 自动加载项目根目录 .env 文件。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_ENV_PATTERN = re.compile(r"\{ENV:([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(Exception):
    """配置错误。"""


@dataclass
class DataSource:
    """单个数据源定义。"""

    name: str
    type: str
    jdbc_url: str
    driver_class: str
    username: str | None = None
    password: str | None = None
    jars: list[str] = field(default_factory=list)
    description: str = ""
    properties: dict[str, str] = field(default_factory=dict)
    # 额外透传给 jaydebeapi.connect 的命名参数（如 fetchsize）
    connect_kwargs: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], env: dict[str, str] | None = None) -> "DataSource":
        env = env if env is not None else dict(os.environ)

        def _sub(value: Any) -> Any:
            if isinstance(value, str):
                return _ENV_PATTERN.sub(lambda m: env.get(m.group(1), ""), value)
            return value

        name = str(raw.get("name", "")).strip()
        if not name:
            raise ConfigError("每个数据源必须包含非空 name")
        jdbc_url = _sub(raw.get("jdbc_url", ""))
        if not jdbc_url:
            raise ConfigError(f"数据源 {name} 缺少 jdbc_url")
        return cls(
            name=name,
            type=str(raw.get("type", "generic")).lower(),
            jdbc_url=jdbc_url,
            driver_class=_sub(raw.get("driver_class", "")),
            username=_sub(raw.get("username", "")) or None,
            password=_sub(raw.get("password", "")) or None,
            jars=[str(j) for j in raw.get("jars", [])],
            description=str(raw.get("description", "")),
            properties={str(k): str(v) for k, v in (raw.get("properties") or {}).items()},
            connect_kwargs=dict(raw.get("connect_kwargs") or {}),
        )

    @property
    def safe_dict(self) -> dict[str, Any]:
        """对外暴露时脱敏，不包含密码。"""
        return {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "jdbc_url": self.jdbc_url,
            "driver_class": self.driver_class,
            "username": self.username,
            "jars": self.jars,
            "properties": self.properties,
        }


@dataclass
class Settings:
    host: str = "0.0.0.0"
    port: int = 8000
    transport: str = "streamable-http"
    auth_token: str | None = None
    config_path: str = "config/datasources.json"
    drivers_dir: str = "drivers"
    max_rows: int = 300             # 单次查询默认返回行数上限（默认 300，防一次性拉大数据量）
    max_rows_limit: int = 10000     # limit 参数允许的最大值（超限按此截断）
    connect_timeout: int = 30
    # ---- 连接池与并发 ----
    pool_size: int = 2              # 每个数据源的 JDBC 连接池大小
    # ---- 熔断 ----
    circuit_fail_threshold: int = 3  # 连续失败 N 次触发熔断
    circuit_cooldown: int = 30       # 熔断冷却时间（秒）
    # ---- 大数据量异步导出 ----
    export_dir: str = "exports"      # 导出文件目录
    export_max_rows: int = 100000    # 单次导出最大行数
    # ---- 监控与审计 ----
    history_size: int = 500          # 执行历史环形缓冲条数
    audit_file: str | None = "logs/audit.jsonl"  # 审计日志(JSONL)路径，空则关闭
    audit_db: str | None = "logs/audit.db"  # 审计 SQLite 库（人工审计页面数据源）
    metrics_enabled: bool = True     # 是否开放 /metrics 端点
    # 审计 JSONL 轮转：单文件超过 audit_max_bytes 时轮转为 .1/.2/...（保留 audit_backup_count 份）
    audit_max_bytes: int = 10 * 1024 * 1024
    audit_backup_count: int = 5
    # ---- 导出文件清理 ----
    export_keep_seconds: int = 86400  # 导出文件保留时长（秒，默认 1 天），超时清理
    export_max_files: int = 100       # 最多保留的导出文件数，超出删除最旧的
    # ---- 健康检查与自动重连 ----
    health_check_interval: int = 30   # 后台探活/自动重连间隔（秒）
    # ---- 限流 ----
    rate_limit_enabled: bool = True   # 是否启用按客户端 IP 的 QPS 限流
    rate_limit_qps: float = 10.0      # 每客户端每秒请求上限
    rate_limit_burst: int = 20        # 突发令牌容量
    # ---- 元数据结果缓存 ----
    meta_cache_ttl: int = 60          # list_tables/describe_table 等元数据缓存有效期（秒）
    meta_cache_max_items: int = 256   # 元数据缓存最大条目数
    # ---- 显式事务 ----
    tx_timeout: int = 300             # 事务最大持续时间（秒），超时自动回滚并释放
    # ---- 脚本执行 ----
    script_root: str = "scripts/sql"  # SQL 脚本根目录（execute_script 限定于此）
    datasources: list[DataSource] = field(default_factory=list)


def _load_json(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"配置文件不存在: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件解析失败 {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件格式错误（应为 JSON 对象）: {p}")
    return data


def load_settings() -> Settings:
    """从环境变量 + 配置文件加载设置。"""
    load_dotenv()
    env = dict(os.environ)

    cfg = Settings(
        host=env.get("MCP_DBTOOLS_HOST", "0.0.0.0"),
        port=int(env.get("MCP_DBTOOLS_PORT", "8000")),
        transport=env.get("MCP_DBTOOLS_TRANSPORT", "streamable-http").lower(),
        auth_token=env.get("MCP_DBTOOLS_AUTH_TOKEN") or None,
        config_path=env.get("MCP_DBTOOLS_CONFIG", "config/datasources.json"),
        drivers_dir=env.get("MCP_DBTOOLS_DRIVERS_DIR", "drivers"),
        max_rows=int(env.get("MCP_DBTOOLS_MAX_ROWS", "300")),
        max_rows_limit=int(env.get("MCP_DBTOOLS_MAX_ROWS_LIMIT", "10000")),
        connect_timeout=int(env.get("MCP_DBTOOLS_CONNECT_TIMEOUT", "30")),
        pool_size=int(env.get("MCP_DBTOOLS_POOL_SIZE", "2")),
        circuit_fail_threshold=int(env.get("MCP_DBTOOLS_CIRCUIT_FAIL_THRESHOLD", "3")),
        circuit_cooldown=int(env.get("MCP_DBTOOLS_CIRCUIT_COOLDOWN", "30")),
        export_dir=env.get("MCP_DBTOOLS_EXPORT_DIR", "exports"),
        export_max_rows=int(env.get("MCP_DBTOOLS_EXPORT_MAX_ROWS", "100000")),
        history_size=int(env.get("MCP_DBTOOLS_HISTORY_SIZE", "500")),
        # 审计日志默认开启落盘；设为空字符串可关闭
        audit_file=env.get("MCP_DBTOOLS_AUDIT_FILE", "logs/audit.jsonl") or None,
        audit_db=env.get("MCP_DBTOOLS_AUDIT_DB", "logs/audit.db") or None,
        metrics_enabled=env.get("MCP_DBTOOLS_METRICS_ENABLED", "true").lower()
        in ("1", "true", "yes", "on"),
        audit_max_bytes=int(env.get("MCP_DBTOOLS_AUDIT_MAX_BYTES", str(10 * 1024 * 1024))),
        audit_backup_count=int(env.get("MCP_DBTOOLS_AUDIT_BACKUP_COUNT", "5")),
        export_keep_seconds=int(env.get("MCP_DBTOOLS_EXPORT_KEEP_SECONDS", "86400")),
        export_max_files=int(env.get("MCP_DBTOOLS_EXPORT_MAX_FILES", "100")),
        health_check_interval=int(env.get("MCP_DBTOOLS_HEALTH_CHECK_INTERVAL", "30")),
        rate_limit_enabled=env.get("MCP_DBTOOLS_RATE_LIMIT_ENABLED", "true").lower()
        in ("1", "true", "yes", "on"),
        rate_limit_qps=float(env.get("MCP_DBTOOLS_RATE_LIMIT_QPS", "10")),
        rate_limit_burst=int(env.get("MCP_DBTOOLS_RATE_LIMIT_BURST", "20")),
        meta_cache_ttl=int(env.get("MCP_DBTOOLS_META_CACHE_TTL", "60")),
        meta_cache_max_items=int(env.get("MCP_DBTOOLS_META_CACHE_MAX_ITEMS", "256")),
        tx_timeout=int(env.get("MCP_DBTOOLS_TX_TIMEOUT", "300")),
        script_root=env.get("MCP_DBTOOLS_SCRIPT_ROOT", "scripts/sql"),
    )

    raw = _load_json(cfg.config_path)
    for ds_raw in raw.get("datasources", []):
        if not isinstance(ds_raw, dict):
            continue
        cfg.datasources.append(DataSource.from_dict(ds_raw, env=env))

    if not cfg.datasources:
        raise ConfigError("未配置任何数据源，请检查 datasources.json")
    return cfg


def get_datasource(settings: Settings, name: str) -> DataSource:
    for ds in settings.datasources:
        if ds.name == name:
            return ds
    names = ", ".join(ds.name for ds in settings.datasources)
    raise ConfigError(f"未知数据源 '{name}'，可用数据源: {names}")
