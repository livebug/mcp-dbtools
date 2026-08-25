"""数据源管理页面（HTML，无外部依赖，适配内网离线环境）。

功能：查看/添加/编辑/删除数据源；密码明文输入后自动加密为 {ENC:...} 存储。
密钥：MCP_DBTOOLS_SECRET_KEY（与运行时解密一致）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote

from . import crypto

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>数据源管理 - mcp-dbtools</title>
<style>
:root{--bg:#0f172a;--card:#1e293b;--border:#334155;--text:#e2e8f0;--muted:#94a3b8;
--acc:#38bdf8;--ok:#22c55e;--err:#ef4444;--warn:#f59e0b}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;padding:24px}
h1{font-size:20px;margin-bottom:4px}
.sub{color:var(--muted);margin-bottom:20px}
.toolbar{margin-bottom:16px}
button{padding:8px 16px;border:1px solid var(--border);border-radius:6px;background:var(--acc);color:#0f172a;font-weight:600;cursor:pointer;font-size:13px}
button.ghost{background:transparent;color:var(--text)}
button.danger{background:transparent;color:var(--err)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden}
th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--border);font-size:13px;vertical-align:top}
th{background:#1a2942;color:var(--muted);font-weight:600;white-space:nowrap}
tr:hover{background:#223150}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px}
.badge.enc{background:rgba(34,197,94,.15);color:var(--ok)}
.badge.env{background:rgba(56,189,248,.15);color:var(--acc)}
.badge.plain{background:rgba(245,158,11,.15);color:var(--warn)}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:#a5b4fc;word-break:break-all}
.muted{color:var(--muted)}
.nav a{color:var(--acc);text-decoration:none;font-size:13px}
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:50;align-items:center;justify-content:center}
.modal.open{display:flex}
.modal-box{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:22px;width:680px;max-width:94vw;max-height:90vh;overflow:auto}
.modal-box h2{font-size:16px;margin-bottom:14px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.grid .full{grid-column:1/-1}
label{display:block;font-size:12px;color:var(--muted);margin-bottom:3px}
input,select,textarea{width:100%;padding:7px 10px;border:1px solid var(--border);border-radius:6px;background:#0b1220;color:var(--text);font-size:13px}
.actions{display:flex;gap:8px;justify-content:flex-end;margin-top:14px}
.hint{font-size:12px;color:var(--muted);margin-top:6px}
/* toast 提示 */
#toast{position:fixed;top:20px;right:20px;z-index:99;display:flex;flex-direction:column;gap:8px}
.toast-item{background:var(--card);border:1px solid var(--border);border-left:4px solid var(--acc);border-radius:8px;padding:10px 16px;font-size:13px;box-shadow:0 4px 14px rgba(0,0,0,.4);opacity:0;transform:translateX(20px);transition:all .25s}
.toast-item.show{opacity:1;transform:none}
.toast-item.ok{border-left-color:var(--ok)}
.toast-item.err{border-left-color:var(--err)}
#refreshInfo{display:inline-block;color:var(--muted);font-size:12px;margin-left:10px}
button:disabled{opacity:.5;cursor:not-allowed}
</style>
</head>
<body>
<div class="nav"><a href="/">← 返回首页</a></div>
<h1>🔌 数据源管理</h1>
<div class="sub">添加 / 编辑 / 删除数据库链接 · 密码自动加密为 <span class="mono">{ENC:...}</span> 存储</div>

<div class="toolbar">
<button onclick="openForm()">＋ 添加数据源</button>
<button class="ghost" onclick="load(true)">🔄 刷新</button>
<span id="refreshInfo"></span>
</div>

<table>
<thead><tr><th>名称</th><th>类型</th><th>JDBC URL</th><th>驱动</th><th>用户名</th><th>密码</th><th>Jars</th><th>操作</th></tr></thead>
<tbody id="rows"></tbody>
</table>
<p class="hint" style="margin-top:10px">⚠ 保存后需 <b>重启服务</b> 生效（配置在启动时加载）。密码类型：<span class="badge enc">ENC</span> 密文 <span class="badge env">ENV</span> 环境变量 <span class="badge plain">明文</span></p>

<div class="modal" id="formModal">
<div class="modal-box">
<h2 id="formTitle">添加数据源</h2>
<form onsubmit="return save()">
<div class="grid">
<div><label>名称 *</label><input id="f_name" required placeholder="db2_test"></div>
<div><label>类型 *</label>
<select id="f_type">
<option value="gaussdb">gaussdb / opengauss</option>
<option value="postgresql">postgresql</option>
<option value="tdh">tdh / inceptor</option>
<option value="hive">hive</option>
<option value="db2">db2</option>
<option value="generic">generic（自定义）</option>
</select></div>
<div class="full"><label>JDBC URL *</label><input id="f_url" required placeholder="jdbc:db2://127.0.0.1:50000/sample"></div>
<div class="full"><label>驱动类 *</label><input id="f_driver" required placeholder="com.ibm.db2.jcc.DB2Driver"></div>
<div><label>用户名</label><input id="f_user" placeholder="db2inst1"></div>
<div><label>密码 <span class="muted" id="pwd_hint"></span></label><input id="f_pwd" type="password" placeholder="留空=不修改（新增时必填）"></div>
<div><label>Jars（逗号分隔）</label><input id="f_jars" placeholder="db2jcc4.jar"></div>
<div><label>描述</label><input id="f_desc"></div>
</div>
<div class="actions">
<button type="button" class="ghost" onclick="closeForm()">取消</button>
<button type="submit">保存</button>
</div>
</form>
</div>
</div>

<script>
let editing = null;

function showToast(msg, type) {
  const box = document.getElementById('toast');
  const el = document.createElement('div');
  el.className = 'toast-item ' + (type || '');
  el.textContent = msg;
  box.appendChild(el);
  requestAnimationFrame(() => el.classList.add('show'));
  setTimeout(() => { el.classList.remove('show'); setTimeout(() => el.remove(), 300); }, 3000);
}

function pwdBadge(p) {
  if (!p) return '<span class="badge plain">无</span>';
  if (p.startsWith('{ENC:')) return '<span class="badge enc">ENC 密文</span>';
  if (p.startsWith('{ENV:')) return '<span class="badge env">ENV</span>';
  return '<span class="badge plain">明文 ⚠</span>';
}

function load(manual) {
  const info = document.getElementById('refreshInfo');
  if (manual) info.textContent = '加载中…';
  fetch('/datasources/api').then(r => {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }).then(ds => {
    const tb = document.getElementById('rows');
    if (!ds || ds.length === 0) {
      tb.innerHTML = '<tr><td colspan="8" class="muted" style="text-align:center;padding:24px">暂无数据源，点击「添加数据源」创建</td></tr>';
    } else {
      tb.innerHTML = ds.map(d => `<tr>
      <td><b>${d.name}</b><br><span class="muted">${d.description || ''}</span></td>
      <td>${d.type}</td>
      <td class="mono">${d.jdbc_url}</td>
      <td class="mono">${d.driver_class}</td>
      <td>${d.username || ''}</td>
      <td>${pwdBadge(d.password)}</td>
      <td class="mono">${(d.jars || []).join(', ')}</td>
      <td><button class="ghost" onclick='edit(${JSON.stringify(d)})'>编辑</button>
          <button class="danger" onclick="del('${d.name}')">删除</button></td>
    </tr>`).join('');
    }
    info.textContent = '已刷新 ' + new Date().toLocaleTimeString();
    if (manual) showToast('列表已刷新（共 ' + (ds ? ds.length : 0) + ' 个数据源）', 'ok');
  }).catch(e => {
    info.textContent = '';
    if (manual) showToast('刷新失败: ' + e.message, 'err');
  });
}

function openForm() {
  editing = null;
  document.getElementById('formTitle').textContent = '添加数据源';
  ['f_name','f_url','f_driver','f_user','f_pwd','f_jars','f_desc'].forEach(i => document.getElementById(i).value = '');
  document.getElementById('pwd_hint').textContent = '';
  document.getElementById('formModal').classList.add('open');
}

function edit(d) {
  editing = d.name;
  document.getElementById('formTitle').textContent = '编辑数据源：' + d.name;
  document.getElementById('f_name').value = d.name;
  document.getElementById('f_type').value = d.type;
  document.getElementById('f_url').value = d.jdbc_url;
  document.getElementById('f_driver').value = d.driver_class;
  document.getElementById('f_user').value = d.username || '';
  document.getElementById('f_pwd').value = '';
  document.getElementById('f_jars').value = (d.jars || []).join(', ');
  document.getElementById('f_desc').value = d.description || '';
  document.getElementById('pwd_hint').textContent = '已存 ' + (d.password ? (d.password.startsWith('{ENC:') ? '密文' : '当前值') : '无') + '，留空不修改';
  document.getElementById('formModal').classList.add('open');
}

function closeForm() { document.getElementById('formModal').classList.remove('open'); }

function save() {
  const btn = document.querySelector('#formModal button[type=submit]');
  btn.disabled = true;
  const body = new URLSearchParams({
    name: document.getElementById('f_name').value.trim(),
    type: document.getElementById('f_type').value,
    jdbc_url: document.getElementById('f_url').value.trim(),
    driver_class: document.getElementById('f_driver').value.trim(),
    username: document.getElementById('f_user').value.trim(),
    password: document.getElementById('f_pwd').value,
    jars: document.getElementById('f_jars').value.trim(),
    description: document.getElementById('f_desc').value.trim(),
    original: editing || '',
  });
  fetch('/datasources/api/save', {method: 'POST', body}).then(r => r.json()).then(d => {
    btn.disabled = false;
    if (d.ok) {
      closeForm(); load();
      showToast('✅ ' + d.message, 'ok');
    } else {
      showToast('保存失败: ' + (d.error || ''), 'err');
    }
  }).catch(e => {
    btn.disabled = false;
    showToast('保存出错: ' + e.message, 'err');
  });
  return false;
}

function del(name) {
  if (!confirm('确认删除数据源 ' + name + ' ？')) return;
  const body = new URLSearchParams({name});
  fetch('/datasources/api/delete', {method: 'POST', body}).then(r => r.json()).then(d => {
    if (d.ok) { load(); showToast('🗑 ' + d.message, 'ok'); }
    else showToast('删除失败: ' + (d.error || ''), 'err');
  }).catch(e => showToast('删除出错: ' + e.message, 'err'));
}

load();
</script>
<div id="toast"></div>
</body>
</html>
"""


def _load_raw(config_path: str | Path) -> dict:
    p = Path(config_path)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"datasources": []}


def _save_raw(config_path: str | Path, data: dict) -> None:
    p = Path(config_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def page_html() -> str:
    return PAGE


def api_list(config_path: str) -> list[dict]:
    """返回脱敏数据源列表（密码保持原样，前端判断类型展示）。"""
    raw = _load_raw(config_path)
    return raw.get("datasources", [])


def api_save(config_path: str, form: dict) -> tuple[bool, str]:
    """保存数据源：密码明文自动加密为 {ENC:...}；original 为空则新增。"""
    name = (form.get("name") or "").strip()
    if not name:
        return False, "名称必填"
    original = (form.get("original") or "").strip()

    raw = _load_raw(config_path)
    ds_list = raw.setdefault("datasources", [])

    # 密码处理
    pwd = form.get("password") or ""
    secret = os.environ.get("MCP_DBTOOLS_SECRET_KEY", "")
    if pwd:
        if pwd.startswith("{ENC:") or pwd.startswith("{ENV:"):
            new_pwd = pwd  # 已是密文/环境变量引用，原样存
        elif secret:
            new_pwd = crypto.encrypt_password(pwd, secret)
        else:
            return False, "未设置 MCP_DBTOOLS_SECRET_KEY，无法加密密码（请先设置环境变量）"
    else:
        # 未填密码：新增必须填；编辑保持原值
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
        if idx >= 0:
            # 保留原条目里未在表单中的字段（如 properties/connect_kwargs）
            old = ds_list[idx]
            for k in ("properties", "connect_kwargs"):
                if k in old:
                    entry[k] = old[k]
            ds_list[idx] = entry
        else:
            return False, f"原数据源 {original} 不存在"
    else:
        if any(d.get("name") == name for d in ds_list):
            return False, f"数据源 {name} 已存在"
        ds_list.append(entry)

    _save_raw(config_path, raw)
    return True, "已保存（重启服务生效）"


def api_delete(config_path: str, name: str) -> tuple[bool, str]:
    raw = _load_raw(config_path)
    ds_list = raw.get("datasources", [])
    new_list = [d for d in ds_list if d.get("name") != name]
    if len(new_list) == len(ds_list):
        return False, f"数据源 {name} 不存在"
    raw["datasources"] = new_list
    _save_raw(config_path, raw)
    return True, "已删除（重启服务生效）"
