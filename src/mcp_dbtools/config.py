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
    max_rows: int = 1000
    connect_timeout: int = 30
    # ---- 监控与审计 ----
    history_size: int = 500          # 执行历史环形缓冲条数
    audit_file: str | None = "logs/audit.jsonl"  # 审计日志路径，空则关闭落盘
    metrics_enabled: bool = True     # 是否开放 /metrics 端点
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
        max_rows=int(env.get("MCP_DBTOOLS_MAX_ROWS", "1000")),
        connect_timeout=int(env.get("MCP_DBTOOLS_CONNECT_TIMEOUT", "30")),
        history_size=int(env.get("MCP_DBTOOLS_HISTORY_SIZE", "500")),
        audit_file=env.get("MCP_DBTOOLS_AUDIT_FILE") or None,
        metrics_enabled=env.get("MCP_DBTOOLS_METRICS_ENABLED", "true").lower()
        in ("1", "true", "yes", "on"),
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
