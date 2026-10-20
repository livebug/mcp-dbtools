"""数据源配置文件的读写（Web 控制台与 REST API 共用）。

职责边界：只负责 datasources.json 的结构化读写与密码加密，不做 HTTP 处理。
密码规则与运行时一致：明文落盘前加密为 ``{ENC:...}``；``{ENV:...}`` 引用原样保留。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .. import crypto

# 表单/JSON 里允许出现的字段（顺序即前端展示顺序参考）
FIELDS = ("name", "type", "jdbc_url", "driver_class", "username", "password", "jars", "description")


def _load_raw(config_path: str | Path) -> dict:
    p = Path(config_path)
    if not p.is_file():
        return {"datasources": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"配置解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("配置格式错误（应为 JSON 对象）")
    return data


def _save_raw(config_path: str | Path, data: dict) -> None:
    """原子写：先写临时文件再替换，避免半截配置导致服务起不来。"""
    p = Path(config_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def list_datasources(config_path: str | Path) -> list[dict]:
    """返回配置文件中的原始条目（含密文密码，调用方负责脱敏）。"""
    return _load_raw(config_path).get("datasources", [])


def get_raw(config_path: str | Path, name: str) -> dict | None:
    return next((d for d in list_datasources(config_path) if d.get("name") == name), None)


def save(config_path: str | Path, form: dict) -> tuple[bool, str]:
    """新增/编辑数据源：``original`` 为空表示新增，否则按原名编辑。"""
    name = (form.get("name") or "").strip()
    if not name:
        return False, "名称必填"
    original = (form.get("original") or "").strip()

    raw = _load_raw(config_path)
    ds_list = raw.setdefault("datasources", [])

    pwd = form.get("password") or ""
    secret = os.environ.get("MCP_DBTOOLS_SECRET_KEY", "")
    if pwd:
        if pwd.startswith("{ENC:") or pwd.startswith("{ENV:"):
            new_pwd = pwd
        elif secret:
            new_pwd = crypto.encrypt_password(pwd, secret)
        else:
            return False, "未设置 MCP_DBTOOLS_SECRET_KEY，无法加密密码（请先设置环境变量）"
    else:
        if original:
            old = next((d for d in ds_list if d.get("name") == original), None)
            new_pwd = old.get("password", "") if old else ""
        else:
            return False, "新增数据源必须填写密码"

    entry = {
        "name": name,
        "type": (form.get("type") or "generic").strip(),
        "jdbc_url": (form.get("jdbc_url") or "").strip(),
        "driver_class": (form.get("driver_class") or "").strip(),
        "username": (form.get("username") or "").strip(),
        "password": new_pwd,
        "jars": [j.strip() for j in (form.get("jars") or "").split(",") if j.strip()],
        "description": (form.get("description") or "").strip(),
    }

    if original:
        idx = next((i for i, d in enumerate(ds_list) if d.get("name") == original), -1)
        if idx < 0:
            return False, f"原数据源 {original} 不存在"
        for k in ("properties", "connect_kwargs"):
            if k in ds_list[idx]:
                entry[k] = ds_list[idx][k]
        ds_list[idx] = entry
    else:
        if any(d.get("name") == name for d in ds_list):
            return False, f"数据源 {name} 已存在"
        ds_list.append(entry)

    _save_raw(config_path, raw)
    return True, "已保存（重启服务生效）"


def delete(config_path: str | Path, name: str) -> tuple[bool, str]:
    raw = _load_raw(config_path)
    ds_list = raw.get("datasources", [])
    new_list = [d for d in ds_list if d.get("name") != name]
    if len(new_list) == len(ds_list):
        return False, f"数据源 {name} 不存在"
    raw["datasources"] = new_list
    _save_raw(config_path, raw)
    return True, "已删除（重启服务生效）"


def mask(entry: dict) -> dict:
    """对外输出脱敏：密码只表明"已设置/未设置"，绝不回传明文或密文。"""
    out = {k: v for k, v in entry.items() if k != "password"}
    pwd = entry.get("password") or ""
    out["password_set"] = bool(pwd)
    out["password_kind"] = (
        "encrypted" if pwd.startswith("{ENC:") else "env" if pwd.startswith("{ENV:") else "plain" if pwd else ""
    )
    return out
