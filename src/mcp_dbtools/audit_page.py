"""人工审计管理页面（HTML，无外部依赖，适配内网离线环境）。"""

AUDIT_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>审计管理 - mcp-dbtools</title>
<style>
:root{--bg:#0f172a;--card:#1e293b;--border:#334155;--text:#e2e8f0;--muted:#94a3b8;
--acc:#38bdf8;--ok:#22c55e;--err:#ef4444;--warn:#f59e0b}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;padding:24px}
h1{font-size:20px;margin-bottom:4px}
.sub{color:var(--muted);margin-bottom:20px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px}
.card .v{font-size:24px;font-weight:700}
.card .l{color:var(--muted);font-size:12px}
.filters{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;margin-bottom:16px;display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;align-items:end}
.filters label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}
input,select,button{width:100%;padding:7px 10px;border:1px solid var(--border);border-radius:6px;background:#0b1220;color:var(--text);font-size:13px}
button{cursor:pointer;background:var(--acc);color:#0f172a;font-weight:600;border:none}
button.ghost{background:transparent;border:1px solid var(--border);color:var(--text)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden}
th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--border);font-size:13px;vertical-align:top}
th{background:#1a2942;color:var(--muted);font-weight:600;white-space:nowrap}
tr:hover{background:#223150}
.ok{color:var(--ok);font-weight:600}
.err{color:var(--err);font-weight:600}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px}
.badge.ok{background:rgba(34,197,94,.15)}
.badge.err{background:rgba(239,68,68,.15)}
.sql{font-family:ui-monospace,Consolas,monospace;color:#a5b4fc;word-break:break-all;max-width:420px}
.pager{display:flex;align-items:center;gap:12px;margin-top:14px;justify-content:flex-end;color:var(--muted)}
.pager button{width:auto;padding:6px 14px}
.muted{color:var(--muted)}
/* 弹窗 */
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:50;align-items:center;justify-content:center}
.modal.open{display:flex}
.modal-box{background:var(--card);border:1px solid var(--border);border-radius:12px;width:min(720px,92vw);max-height:86vh;overflow:auto;padding:20px}
.modal-box h3{margin-bottom:12px}
.modal-box .row{margin-bottom:8px}
.modal-box .row .k{color:var(--muted);font-size:12px}
pre.json{background:#0b1220;border:1px solid var(--border);border-radius:6px;padding:10px;overflow:auto;white-space:pre-wrap;word-break:break-all;font-size:12px}
.token-row{display:flex;gap:10px;margin-bottom:14px;align-items:center}
.token-row input{max-width:280px}
#toast{position:fixed;top:16px;right:16px;background:#334155;padding:10px 16px;border-radius:8px;display:none}
</style>
</head>
<body>
<h1>📋 审计管理</h1>
<div class="sub">mcp-dbtools 数据库 MCP 服务 · 人工审计查询页面 · <span id="svc-uptime"></span></div>

<div class="token-row" id="tokenRow">
  <span class="muted">访问令牌：</span>
  <input id="tokenInput" placeholder="留空表示无需令牌">
  <button class="ghost" onclick="saveToken()">保存</button>
</div>

<div class="cards">
  <div class="card"><div class="v" id="c-total">-</div><div class="l">审计总数</div></div>
  <div class="card"><div class="v" id="c-today">-</div><div class="l">今日</div></div>
  <div class="card"><div class="v ok" id="c-ok">-</div><div class="l">成功</div></div>
  <div class="card"><div class="v err" id="c-err">-</div><div class="l">失败</div></div>
</div>

<div class="filters">
  <div><label>开始日期</label><input type="date" id="f-from"></div>
  <div><label>结束日期</label><input type="date" id="f-to"></div>
  <div><label>工具</label><input id="f-tool" placeholder="如 execute_query"></div>
  <div><label>客户端 IP</label><input id="f-ip" placeholder="如 127.0.0.1"></div>
  <div><label>关键字（SQL/错误）</label><input id="f-q" placeholder="搜索 SQL 内容"></div>
  <div><label>状态</label>
    <select id="f-ok"><option value="">全部</option><option value="1">成功</option><option value="0">失败</option></select>
  </div>
  <div><button onclick="search(1)">查询</button></div>
</div>

<table>
  <thead><tr>
    <th>时间</th><th>IP</th><th>工具</th><th>参数 / SQL</th><th>耗时(ms)</th><th>状态</th><th></th>
  </tr></thead>
  <tbody id="tbody"><tr><td colspan="7" class="muted" style="text-align:center">加载中…</td></tr></tbody>
</table>

<div class="pager">
  <span id="page-info"></span>
  <button class="ghost" onclick="search(page-1)">上一页</button>
  <button class="ghost" onclick="search(page+1)">下一页</button>
  <button class="ghost" onclick="exportCsv()">导出 CSV</button>
</div>

<div class="modal" id="modal"><div class="modal-box" id="modalBox"></div></div>
<div id="toast"></div>

<script>
let page = 1, pageSize = 20, total = 0;
const $ = id => document.getElementById(id);

function token() { return $('tokenInput').value.trim(); }
function saveToken() { localStorage.setItem('audit_token', token()); toast('令牌已保存'); }

function api(params) {
  const q = new URLSearchParams(params);
  const headers = {};
  const tk = token();
  if (tk) headers['Authorization'] = 'Bearer ' + tk;
  return fetch('/audit/api?' + q.toString(), { headers }).then(r => {
    if (r.status === 401) throw new Error('未授权：请填写访问令牌');
    if (!r.ok) throw new Error('请求失败 ' + r.status);
    return r.json();
  });
}

function fmtTs(ts) { return ts ? ts.replace('T', ' ').slice(0, 19) : '-'; }
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function sqlPreview(args) {
  const sql = args && (args.sql || args.script_path);
  if (sql) return '<span class="sql">' + esc(String(sql)) + '</span>';
  return '<span class="muted">' + esc(JSON.stringify(args || {}).slice(0, 80)) + '</span>';
}

async function loadSummary() {
  try {
    const s = await api({ action: 'summary' });
    $('c-total').textContent = s.total;
    $('c-today').textContent = s.today;
    $('c-ok').textContent = s.success;
    $('c-err').textContent = s.failed;
  } catch (e) { toast(e.message); }
}

async function search(p) {
  page = p = p || 1;
  const params = {
    action: 'list', page, page_size: pageSize,
    tool: $('f-tool').value.trim() || '', ip: $('f-ip').value.trim() || '',
    q: $('f-q').value.trim() || '', ok: $('f-ok').value,
  };
  if ($('f-from').value) params['from'] = $('f-from').value + 'T00:00:00';
  if ($('f-to').value) params['to'] = $('f-to').value + 'T23:59:59';
  try {
    const d = await api(params);
    total = d.total;
    const tb = $('tbody');
    if (!d.items.length) { tb.innerHTML = '<tr><td colspan="7" class="muted" style="text-align:center">无记录</td></tr>'; }
    else tb.innerHTML = d.items.map(r => `
      <tr>
        <td>${fmtTs(r.ts)}</td>
        <td>${esc(r.client_ip || '-')}</td>
        <td>${esc(r.tool)}</td>
        <td>${sqlPreview(r.args)}</td>
        <td>${r.duration_ms != null ? r.duration_ms.toFixed(1) : '-'}</td>
        <td>${r.ok ? '<span class="badge ok">成功</span>' : '<span class="badge err">失败</span>'}</td>
        <td><button class="ghost" onclick="detail(${r.id})">详情</button></td>
      </tr>`).join('');
    const pages = Math.max(1, Math.ceil(total / pageSize));
    $('page-info').textContent = `第 ${page}/${pages} 页 · 共 ${total} 条`;
  } catch (e) { toast(e.message); }
}

async function detail(id) {
  try {
    const d = await api({ action: 'get', id });
    const r = d.item;
    $('modalBox').innerHTML = `
      <h3>审计详情 #${r.id}</h3>
      <div class="row"><div class="k">时间</div>${fmtTs(r.ts)}</div>
      <div class="row"><div class="k">工具</div>${esc(r.tool)}</div>
      <div class="row"><div class="k">客户端 IP</div>${esc(r.client_ip || '-')}</div>
      <div class="row"><div class="k">User-Agent</div>${esc(r.user_agent || '-')}</div>
      <div class="row"><div class="k">耗时</div>${r.duration_ms != null ? r.duration_ms.toFixed(2) + ' ms' : '-'}</div>
      <div class="row"><div class="k">状态</div>${r.ok ? '成功' : '失败'}</div>
      ${r.error ? `<div class="row"><div class="k">错误</div><pre class="json">${esc(r.error)}</pre></div>` : ''}
      <div class="row"><div class="k">参数</div><pre class="json">${esc(JSON.stringify(r.args, null, 2))}</pre></div>
      <div style="margin-top:14px"><button class="ghost" onclick="$('modal').classList.remove('open')">关闭</button></div>`;
    $('modal').classList.add('open');
  } catch (e) { toast(e.message); }
}

function exportCsv() {
  const params = new URLSearchParams({
    action: 'export', tool: $('f-tool').value.trim() || '', ip: $('f-ip').value.trim() || '',
    q: $('f-q').value.trim() || '', ok: $('f-ok').value,
  });
  if ($('f-from').value) params.set('from', $('f-from').value + 'T00:00:00');
  if ($('f-to').value) params.set('to', $('f-to').value + 'T23:59:59');
  const tk = token();
  const headers = tk ? { 'Authorization': 'Bearer ' + tk } : {};
  fetch('/audit/api?' + params.toString(), { headers }).then(r => {
    if (r.status === 401) throw new Error('未授权');
    return r.json();
  }).then(d => {
    if (!d.items.length) return toast('无数据可导出');
    const rows = [['时间','IP','工具','参数','耗时ms','状态','错误']];
    d.items.forEach(r => rows.push([fmtTs(r.ts), r.client_ip||'', r.tool, JSON.stringify(r.args), r.duration_ms, r.ok?'成功':'失败', r.error||'']));
    const csv = rows.map(r => r.map(c => '"' + String(c).replace(/"/g,'""') + '"').join(',')).join('\\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob(['\\ufeff' + csv], { type: 'text/csv;charset=utf-8' }));
    a.download = 'audit_' + new Date().toISOString().slice(0,10) + '.csv';
    a.click();
  }).catch(e => toast(e.message));
}

function toast(msg) { const t = $('toast'); t.textContent = msg; t.style.display = 'block'; setTimeout(() => t.style.display = 'none', 2600); }

// 初始化：恢复 token，绑定回车查询
(function(){
  const saved = localStorage.getItem('audit_token') || '';
  const q = new URLSearchParams(location.search).get('token');
  $('tokenInput').value = q || saved;
  if (q) localStorage.setItem('audit_token', q);
  ['f-tool','f-ip','f-q'].forEach(id => $(id).addEventListener('keydown', e => { if (e.key === 'Enter') search(1); }));
  loadSummary();
  search(1);
})();
</script>
</body>
</html>
"""
