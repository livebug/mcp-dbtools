"""服务日志查看页面（HTML，无外部依赖，适配内网离线环境）+ 日志文件尾部读取。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

LOGS_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>服务日志 - mcp-dbtools</title>
<style>
:root{--bg:#0f172a;--card:#1e293b;--border:#334155;--text:#e2e8f0;--muted:#94a3b8;--acc:#38bdf8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;padding:24px}
h1{font-size:20px;margin-bottom:4px}
.sub{color:var(--muted);margin-bottom:18px}
.controls{display:flex;gap:10px;margin-bottom:12px;align-items:center;flex-wrap:wrap}
input,select,button{padding:7px 10px;border:1px solid var(--border);border-radius:6px;background:#0b1220;color:var(--text);font-size:13px}
button{cursor:pointer;background:var(--acc);color:#0f172a;font-weight:600;border:none}
button.ghost{background:transparent;border:1px solid var(--border);color:var(--text)}
input[type=number],select{width:110px}
input#q{min-width:200px;flex:1}
pre#logbox{background:#0b1220;border:1px solid var(--border);border-radius:10px;padding:14px;overflow:auto;
font:12px/1.5 ui-monospace,Consolas,monospace;white-space:pre-wrap;word-break:break-all;max-height:calc(100vh - 190px)}
.muted{color:var(--muted)}
#stat{color:var(--muted);font-size:12px;margin-bottom:8px}
</style>
</head>
<body>
<h1>📜 服务日志</h1>
<div class="sub">mcp-dbtools 应用日志（尾部查看）· <span id="stat"></span></div>

<div class="controls">
  <input id="tokenInput" placeholder="访问令牌（调 /logs/api 用，可留空）" style="max-width:280px">
  <label class="muted">行数</label>
  <select id="lines">
    <option value="100">100</option>
    <option value="200" selected>200</option>
    <option value="500">500</option>
    <option value="1000">1000</option>
  </select>
  <input id="q" placeholder="关键字过滤（如 ERROR / datasource 名称）">
  <button onclick="load(true)">刷新</button>
  <button class="ghost" id="autoBtn" onclick="toggleAuto()">自动刷新：开</button>
</div>

<pre id="logbox">加载中…</pre>

<script>
let auto = true;
function token(){ return document.getElementById('tokenInput').value.trim(); }
function saveToken(){ localStorage.setItem('logs_token', token()); }
function restoreToken(){ var t = localStorage.getItem('logs_token'); if (t) document.getElementById('tokenInput').value = t; }

async function api(params){
  const headers = {};
  const tk = token();
  if (tk) headers['Authorization'] = 'Bearer ' + tk;
  const qs = new URLSearchParams(params).toString();
  const r = await fetch('/logs/api?' + qs, { headers });
  if (r.status === 401) return { unauthorized: true };
  return r.json();
}

async function load(manual){
  const d = await api({
    action: 'tail',
    lines: document.getElementById('lines').value,
    q: document.getElementById('q').value.trim() || ''
  });
  const box = document.getElementById('logbox');
  if (!d) return;
  if (d.unauthorized) { box.textContent = '未授权：请在上方填写访问令牌并刷新'; return; }
  if (d.error) { box.textContent = '错误：' + d.error; return; }
  box.textContent = d.content || '(日志为空)';
  document.getElementById('stat').textContent =
    (d.file ? d.file + ' · ' : '') + (d.size_text || '') +
    (d.truncated ? ' · 已截断（仅显示末尾 ' + d.returned + ' 行）' : '');
  if (manual) box.scrollTop = box.scrollHeight;
}
function toggleAuto(){
  auto = !auto;
  document.getElementById('autoBtn').textContent = '自动刷新：' + (auto ? '开' : '关');
  if (auto) load(true);
}
restoreToken();
document.getElementById('tokenInput').addEventListener('change', saveToken);
document.getElementById('q').addEventListener('keydown', function(e){ if (e.key === 'Enter') load(true); });
setInterval(function(){ if (auto) load(false); }, 5000);
load(true);
</script>
</body>
</html>
"""


def tail_lines(path: str | Path, lines: int = 200, q: str | None = None) -> dict[str, Any]:
    """读取日志文件末尾 N 行（可选关键字过滤）。

    返回: {content, file, size, size_text, truncated, returned}
    - 日志文件有轮转上限（默认 10MB），直接读取并解析成本可控；
    - q 非空时先按关键字过滤再取末尾 N 行。
    """
    p = Path(path)
    n = min(max(1, lines), 2000)
    size = 0
    if not p.is_file():
        return {
            "content": "",
            "file": str(p),
            "size": 0,
            "size_text": "0 B",
            "truncated": False,
            "returned": 0,
        }
    try:
        size = p.stat().st_size
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {
            "content": f"读取日志失败: {exc}",
            "file": str(p),
            "size": size,
            "size_text": _fmt_size(size),
            "error": str(exc),
        }
    all_lines = text.splitlines()
    if q:
        ql = q.lower()
        all_lines = [ln for ln in all_lines if ql in ln.lower()]
    picked = all_lines[-n:]
    return {
        "content": "\n".join(picked),
        "file": str(p),
        "size": size,
        "size_text": _fmt_size(size),
        "truncated": len(all_lines) > n,
        "returned": len(picked),
    }


def _fmt_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return f"{size} B"
