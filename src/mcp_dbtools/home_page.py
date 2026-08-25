"""首页导航页面（HTML，无外部依赖）。"""

HOME_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mcp-dbtools 控制台</title>
<style>
:root{--bg:#0f172a;--card:#1e293b;--border:#334155;--text:#e2e8f0;--muted:#94a3b8;--acc:#38bdf8}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;padding:32px;min-height:100vh}
.wrap{max-width:860px;margin:0 auto}
h1{font-size:22px;margin-bottom:4px}
.sub{color:var(--muted);margin-bottom:24px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px;text-decoration:none;color:var(--text);transition:border-color .15s,transform .1s}
.card:hover{border-color:var(--acc);transform:translateY(-2px)}
.card .ico{font-size:26px;margin-bottom:8px}
.card .t{font-weight:600;font-size:15px;margin-bottom:4px}
.card .d{color:var(--muted);font-size:12px}
.foot{margin-top:28px;color:var(--muted);font-size:12px}
.mono{font-family:ui-monospace,Consolas,monospace}
</style>
</head>
<body>
<div class="wrap">
<h1>🦞 mcp-dbtools 控制台</h1>
<div class="sub">数据库 MCP 服务 · 管理入口导航</div>

<div class="cards">
<a class="card" href="/datasources"><div class="ico">🔌</div><div class="t">数据源管理</div><div class="d">添加 / 编辑数据库链接，密码自动加密</div></a>
<a class="card" href="/logs"><div class="ico">📜</div><div class="t">日志查询</div><div class="d">服务日志检索与导出</div></a>
<a class="card" href="/audit"><div class="ico">🛡️</div><div class="t">审计管理</div><div class="d">操作审计记录与导出</div></a>
<a class="card" href="/health"><div class="ico">💚</div><div class="t">健康检查</div><div class="d">服务与数据源健康状态</div></a>
<a class="card" href="/metrics"><div class="ico">📊</div><div class="t">指标监控</div><div class="d">调用量 / 耗时等监控指标</div></a>
</div>

<div class="foot">MCP 端点：<span class="mono">POST /mcp</span>（streamable-http）· 供 Claude / VS Code / dsh 等客户端连接</div>
</div>
</body>
</html>
"""


def page_html() -> str:
    return HOME_HTML
