/* mcp-dbtools Web 控制台（零构建，原生 ES2020）。
 * 数据接口统一走 /api/v1，鉴权用 Bearer Token（sessionStorage）。 */
(() => {
  "use strict";

  const TOKEN_KEY = "mcp_dbtools_token";
  const app = { token: "", info: null };

  /* ---------- Token 引导 ---------- */
  (function bootstrap() {
    const u = new URL(location.href);
    const fromUrl = u.searchParams.get("token");
    if (fromUrl) {
      app.token = fromUrl;
      sessionStorage.setItem(TOKEN_KEY, fromUrl);
      u.searchParams.delete("token");
      history.replaceState({}, "", u.pathname + u.search + u.hash);
    } else {
      app.token = sessionStorage.getItem(TOKEN_KEY) || "";
    }
  })();

  /* ---------- 工具函数 ---------- */
  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (v) =>
    String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const cell = (v) => (v === null || v === undefined ? '<span class="muted">-</span>' : esc(typeof v === "object" ? JSON.stringify(v) : v));
  const bytes = (n) => {
    if (!n) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
    return `${i ? n.toFixed(1) : n} ${units[i]}`;
  };

  function table(headers, rows, empty = "暂无数据") {
    if (!rows.length) return `<div class="empty">${esc(empty)}</div>`;
    return `<div class="scroll"><table><thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows
      .map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`)
      .join("")}</tbody></table></div>`;
  }

  function alertBox(msg, kind = "err") {
    return `<div class="alert ${kind}">${esc(msg)}</div>`;
  }

  /* ---------- API ---------- */
  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    if (opts.body !== undefined && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    if (app.token) headers["Authorization"] = `Bearer ${app.token}`;
    const res = await fetch(`/api/v1${path}`, {
      method: opts.method || (opts.body !== undefined ? "POST" : "GET"),
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
    let payload = null;
    try { payload = await res.json(); } catch (_) { /* 非 JSON 响应 */ }
    if (res.status === 401) {
      askToken();
      throw new Error("未认证：请先设置 Token");
    }
    if (!res.ok || (payload && payload.ok === false)) {
      throw new Error((payload && payload.error && payload.error.message) || `${res.status} ${res.statusText}`);
    }
    return payload ? payload.data : null;
  }

  async function fetchBlob(path) {
    const res = await fetch(`/api/v1${path}`, { headers: app.token ? { Authorization: `Bearer ${app.token}` } : {} });
    if (!res.ok) throw new Error(`下载失败（HTTP ${res.status}）`);
    return res.blob();
  }

  /* ---------- Token 弹窗 ---------- */
  function askToken() {
    const dlg = $("#token-dialog");
    if (!dlg || dlg.open) return;
    const input = $("#token-input");
    input.value = "";
    dlg.returnValue = "";
    dlg.showModal();
    dlg.addEventListener("close", () => {
      if (dlg.returnValue !== "save") return;
      const v = input.value.trim();
      if (!v) return;
      app.token = v;
      sessionStorage.setItem(TOKEN_KEY, v);
      route(true);
    }, { once: true });
  }

  /* ---------- 视图 ---------- */
  async function ensureInfo() {
    if (!app.info) app.info = await api("/system/info");
    return app.info;
  }

  function paintChrome(info, health) {
    $("#ver").textContent = `v${info.version}`;
    $("#mode").textContent = info.mode === "remote" ? "远程" : "本地";
    $("#mcp-endpoint").textContent = info.endpoints.mcp;
    const pill = $("#health-pill");
    if (!health) {
      pill.className = "pill";
      pill.textContent = "未知";
      return;
    }
    const okCount = health.datasources.filter((d) => d.ok).length;
    pill.className = `pill ${health.status === "ok" ? "ok" : "bad"}`;
    pill.textContent = health.status === "ok" ? `全部正常 ${okCount}/${health.datasources.length}` : `降级 ${okCount}/${health.datasources.length}`;
  }

  async function viewOverview(el) {
    const [info, health, metrics] = await Promise.all([ensureInfo(), api("/system/health"), api("/system/metrics")]);
    paintChrome(info, health);
    const dsRows = health.datasources.map((d) => [
      esc(d.datasource),
      d.ok ? '<span class="badge ok">正常</span>' : '<span class="badge err">异常</span>',
      d.latency_ms === null || d.latency_ms === undefined ? "-" : `${d.latency_ms} ms`,
      cell(d.last_health),
      d.error ? `<span class="muted">${esc(d.error)}</span>` : "",
    ]);
    const toolRows = Object.entries(metrics.tool_summary).map(([name, s]) => [
      `<span class="mono">${esc(name)}</span>`, esc(s.calls), esc(s.errors),
      `${esc(s.total_time_ms)} ms`, s.calls ? `${(s.total_time_ms / s.calls).toFixed(1)} ms` : "-",
    ]);
    el.innerHTML = `
      <div class="grid">
        <div class="stat"><div class="k">运行时长</div><div class="v">${Math.floor(metrics.uptime_seconds / 60)} 分钟</div></div>
        <div class="stat"><div class="k">数据源</div><div class="v">${info.datasources.length}</div></div>
        <div class="stat"><div class="k">进程内存</div><div class="v">${metrics.memory_mb.process_rss ? metrics.memory_mb.process_rss + " MB" : "-"}</div></div>
        <div class="stat"><div class="k">JVM 堆</div><div class="v">${metrics.memory_mb.jvm_heap ? Math.round(metrics.memory_mb.jvm_heap.used_mb) + " MB" : "-"}</div></div>
      </div>
      <div class="card"><h2>数据源健康</h2>${table(["名称", "状态", "延迟", "最近探测", "错误"], dsRows, "未配置数据源")}</div>
      <div class="card"><h2>工具调用统计</h2>${table(["工具", "调用", "失败", "总耗时", "平均"], toolRows, "暂无调用记录")}</div>`;
  }

  async function viewDatasources(el) {
    const data = await api("/datasources");
    const rows = data.items.map((d) => [
      `<b>${esc(d.name)}</b>`,
      esc(d.type),
      `<span class="mono">${esc(d.jdbc_url)}</span>`,
      esc(d.driver_class),
      d.password_set ? `<span class="badge ok">${esc(d.password_kind)}</span>` : '<span class="badge">未设置</span>',
      `<div class="row">
         <button class="link" data-act="test" data-name="${esc(d.name)}">测试</button>
         ${data.write_allowed ? `<button class="link" data-act="edit" data-name="${esc(d.name)}">编辑</button>
         <button class="link danger" data-act="del" data-name="${esc(d.name)}">删除</button>` : ""}
       </div>`,
    ]);
    el.innerHTML = `
      <div class="toolbar">
        ${data.write_allowed ? '<button class="primary" id="ds-add">＋ 新增数据源</button>' : '<span class="muted">当前为远程只读模式，数据源配置不可修改</span>'}
        <span class="muted">修改后需重启服务生效</span>
      </div>
      <div id="ds-msg"></div>
      <div id="ds-form"></div>
      <div class="card"><h2>数据源（${data.total}）</h2>${table(["名称", "类型", "JDBC URL", "驱动", "密码", "操作"], rows)}</div>`;

    const msg = $("#ds-msg");
    const formBox = $("#ds-form");

    el.onclick = async (ev) => {
      const btn = ev.target.closest("button[data-act]");
      if (!btn) return;
      const name = btn.dataset.name;
      try {
        if (btn.dataset.act === "del") {
          if (!confirm(`确认删除数据源 ${name}？`)) return;
          await api(`/datasources/${encodeURIComponent(name)}`, { method: "DELETE" });
          route(true);
        } else if (btn.dataset.act === "test") {
          msg.innerHTML = alertBox(`正在测试 ${name} …`, "ok");
          const r = await api(`/datasources/${encodeURIComponent(name)}/test`, { method: "POST" });
          msg.innerHTML = alertBox(`${name} 连接正常：${r.product || ""} ${r.version || ""}`, "ok");
        } else if (btn.dataset.act === "edit") {
          const detail = await api(`/datasources/${encodeURIComponent(name)}`);
          showForm(detail.item);
        }
      } catch (e) {
        msg.innerHTML = alertBox(e.message);
      }
    };

    const addBtn = $("#ds-add");
    if (addBtn) addBtn.addEventListener("click", () => showForm(null));

    function showForm(item) {
      const d = item || { name: "", type: "gaussdb", jdbc_url: "", driver_class: "", username: "", jars: [], description: "" };
      formBox.innerHTML = `
        <div class="card">
          <h2>${item ? "编辑数据源 " + esc(d.name) : "新增数据源"}</h2>
          <div class="form-grid">
            <label>名称<input id="f-name" value="${esc(d.name)}" ${item ? "readonly" : ""}></label>
            <label>类型<input id="f-type" value="${esc(d.type)}"></label>
            <label>JDBC URL<input id="f-url" value="${esc(d.jdbc_url)}"></label>
            <label>驱动类<input id="f-driver" value="${esc(d.driver_class)}"></label>
            <label>用户名<input id="f-user" value="${esc(d.username || "")}"></label>
            <label>密码<input id="f-pwd" type="password" placeholder="${item && d.password_set ? "留空表示保持不变" : "新增必填"}" autocomplete="new-password"></label>
            <label>驱动 jar（逗号分隔）<input id="f-jars" value="${esc((d.jars || []).join(","))}"></label>
            <label>描述<input id="f-desc" value="${esc(d.description || "")}"></label>
          </div>
          <div class="row" style="margin-top:14px">
            <button class="primary" id="f-save">保存</button>
            <button class="ghost" id="f-cancel">取消</button>
          </div>
        </div>`;
      $("#f-save").addEventListener("click", async () => {
        const body = {
          name: $("#f-name").value.trim(),
          type: $("#f-type").value.trim(),
          jdbc_url: $("#f-url").value.trim(),
          driver_class: $("#f-driver").value.trim(),
          username: $("#f-user").value.trim(),
          password: $("#f-pwd").value,
          jars: $("#f-jars").value,
          description: $("#f-desc").value.trim(),
        };
        if (item) body.original = item.name;
        try {
          if (item) await api(`/datasources/${encodeURIComponent(item.name)}`, { method: "PUT", body });
          else await api("/datasources", { body });
          route(true);
        } catch (e) {
          msg.innerHTML = alertBox(e.message);
        }
      });
      $("#f-cancel").addEventListener("click", () => { formBox.innerHTML = ""; });
    }
  }

  async function viewQuery(el) {
    const info = await ensureInfo();
    paintChrome(info, null);
    const options = info.datasources.map((d) => `<option value="${esc(d.name)}">${esc(d.name)}（${esc(d.type)}）</option>`).join("");
    el.innerHTML = `
      <div class="card">
        <h2>执行 SQL</h2>
        <div class="form-grid" style="grid-template-columns:minmax(180px,240px) 120px 1fr">
          <label>数据源<select id="q-ds">${options}</select></label>
          <label>返回行数<input id="q-limit" type="number" min="1" value="100"></label>
          <label class="check" style="flex-direction:row;align-items:center;padding-top:20px">
            <input id="q-confirm" type="checkbox"> 确认执行写操作
          </label>
        </div>
        <label style="margin-top:12px">SQL<textarea id="q-sql" placeholder="SELECT * FROM ..."></textarea></label>
        <div class="row" style="margin-top:12px">
          <button class="primary" id="q-run">执行</button>
          <span class="muted" id="q-cost"></span>
        </div>
      </div>
      <div id="q-out"></div>`;

    $("#q-run").addEventListener("click", async () => {
      const out = $("#q-out");
      const body = {
        datasource: $("#q-ds").value,
        sql: $("#q-sql").value,
        limit: parseInt($("#q-limit").value, 10) || undefined,
        confirm: $("#q-confirm").checked,
      };
      out.innerHTML = '<div class="empty">执行中…</div>';
      try {
        const r = await api("/query/execute", { body });
        $("#q-cost").textContent = `${r.execution_time_ms} ms`;
        if (!r.columns || !r.columns.length) {
          out.innerHTML = alertBox(r.affected_rows !== undefined ? `执行成功，影响 ${r.affected_rows} 行` : "执行成功（无结果集）", "ok");
          return;
        }
        const rows = r.rows.map((row) => row.map(cell));
        out.innerHTML = `<div class="card">
          <h2>结果 ${r.row_count} 行${r.truncated ? "（已截断）" : ""}</h2>
          ${table(r.columns, rows)}
        </div>`;
      } catch (e) {
        out.innerHTML = alertBox(e.message);
      }
    });
  }

  async function viewExports(el) {
    const info = await ensureInfo();
    const data = await api("/exports");
    const options = info.datasources.map((d) => `<option value="${esc(d.name)}">${esc(d.name)}</option>`).join("");
    const badge = (s) => ({ succeeded: "ok", failed: "err", running: "run", pending: "run" }[s] ? `<span class="badge ${s === "succeeded" ? "ok" : s === "failed" ? "err" : "run"}">${esc(s)}</span>` : esc(s));
    const rows = data.items.map((t) => [
      `<span class="mono">${esc(t.id)}</span>`,
      esc(t.datasource),
      badge(t.status),
      esc(t.rows),
      esc(t.created_at),
      t.status === "succeeded" ? `<button class="link" data-dl="${esc(t.id)}">下载</button>` : `<span class="muted">${esc(t.error || "")}</span>`,
    ]);
    el.innerHTML = `
      <div class="card">
        <h2>新建导出任务</h2>
        <div class="form-grid" style="grid-template-columns:minmax(180px,240px) 120px 140px 1fr">
          <label>数据源<select id="e-ds">${options}</select></label>
          <label>最大行数<input id="e-limit" type="number" min="1" value="100000"></label>
          <label>分隔符<input id="e-delim" value=","></label>
          <label class="check" style="flex-direction:row;align-items:center;padding-top:20px"><input id="e-header" type="checkbox" checked> 含表头</label>
        </div>
        <label style="margin-top:12px">SQL<textarea id="e-sql" placeholder="SELECT * FROM ..."></textarea></label>
        <div class="row" style="margin-top:12px"><button class="primary" id="e-run">开始导出</button></div>
        <div id="e-msg"></div>
      </div>
      <div class="card"><h2>导出任务（${data.total}）</h2>${table(["任务", "数据源", "状态", "行数", "创建时间", "文件"], rows)}</div>`;

    $("#e-run").addEventListener("click", async () => {
      try {
        await api("/exports", {
          body: {
            datasource: $("#e-ds").value,
            sql: $("#e-sql").value,
            limit: parseInt($("#e-limit").value, 10) || undefined,
            delimiter: $("#e-delim").value || ",",
            include_header: $("#e-header").checked,
          },
        });
        route(true);
      } catch (e) {
        $("#e-msg").innerHTML = alertBox(e.message);
      }
    });

    el.onclick = async (ev) => {
      const btn = ev.target.closest("button[data-dl]");
      if (!btn) return;
      try {
        const blob = await fetchBlob(`/exports/${encodeURIComponent(btn.dataset.dl)}/download`);
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `export-${btn.dataset.dl}.txt`;
        a.click();
        URL.revokeObjectURL(a.href);
      } catch (e) {
        $("#e-msg").innerHTML = alertBox(e.message);
      }
    };
  }

  async function viewTools(el) {
    const data = await api("/mcp/tools");
    const byGroup = new Map();
    data.items.forEach((t) => {
      if (!byGroup.has(t.group)) byGroup.set(t.group, []);
      byGroup.get(t.group).push(t);
    });

    const paramRows = (schema) => {
      const props = (schema && schema.properties) || {};
      const required = new Set((schema && schema.required) || []);
      return Object.entries(props).map(([name, p]) => [
        `<span class="mono">${esc(name)}</span>`,
        esc(p.type || (p.anyOf ? "any" : "string")),
        required.has(name) ? '<span class="badge ok">必填</span>' : '<span class="muted">可选</span>',
        esc(p.description || p.title || ""),
        p.default === undefined ? "" : `<span class="mono">${esc(typeof p.default === "object" ? JSON.stringify(p.default) : p.default)}</span>`,
      ]);
    };

    function render(q) {
      const kw = (q || "").trim().toLowerCase();
      let shown = 0;
      const html = [...byGroup.entries()].map(([group, tools]) => {
        const list = tools.filter((t) => !kw || t.name.toLowerCase().includes(kw) || t.description.toLowerCase().includes(kw));
        if (!list.length) return "";
        shown += list.length;
        return `<div class="card"><h2>${esc(group)} · ${list.length}</h2>${list.map((t) => `
          <details class="tool" ${list.length <= 3 ? "open" : ""}>
            <summary>
              <span class="mono tool-name">${esc(t.name)}</span>
              ${t.requires_confirm ? '<span class="badge err">需 confirm</span>' : ""}
              <span class="muted">${esc(t.description)}</span>
            </summary>
            ${t.parameters && t.parameters.properties && Object.keys(t.parameters.properties).length
              ? table(["参数", "类型", "必填", "说明", "默认"], paramRows(t.parameters))
              : '<div class="empty">无参数</div>'}
          </details>`).join("")}</div>`;
      }).join("");
      $("#t-list").innerHTML = html || '<div class="empty">没有匹配的工具</div>';
      $("#t-count").textContent = `共 ${data.total} 个工具，当前显示 ${shown} 个`;
    }

    el.innerHTML = `
      <div class="toolbar">
        <input id="t-q" placeholder="搜索工具名 / 描述" style="width:300px">
        <span class="muted" id="t-count"></span>
        <span class="muted">参数 schema 与 MCP <span class="mono">tools/list</span> 同源</span>
      </div>
      <div id="t-list"></div>`;
    $("#t-q").addEventListener("input", (ev) => render(ev.target.value));
    render("");
  }

  async function viewAudit(el) {
    const state = { page: 1, pageSize: 20, tool: "", q: "", ok: "" };
    el.innerHTML = `
      <div class="toolbar">
        <input id="a-tool" placeholder="工具名，如 execute_query" style="width:220px">
        <input id="a-q" placeholder="关键字（参数/错误）" style="width:220px">
        <select id="a-ok" style="width:120px"><option value="">全部状态</option><option value="1">成功</option><option value="0">失败</option></select>
        <button class="primary" id="a-search">查询</button>
        <span class="muted" id="a-total"></span>
      </div>
      <div id="a-out"></div>`;

    async function load() {
      const qs = new URLSearchParams({ page: state.page, page_size: state.pageSize });
      if (state.tool) qs.set("tool", state.tool);
      if (state.q) qs.set("q", state.q);
      if (state.ok !== "") qs.set("ok", state.ok);
      const data = await api(`/audit/events?${qs}`);
      $("#a-total").textContent = `共 ${data.total} 条`;
      const rows = data.items.map((it) => [
        `<span class="mono">${esc(it.id)}</span>`,
        esc(it.ts),
        `<span class="mono">${esc(it.tool)}</span>`,
        esc(it.client_ip || "-"),
        it.ok ? '<span class="badge ok">成功</span>' : '<span class="badge err">失败</span>',
        `${esc(it.duration_ms)} ms`,
        `<span class="mono muted">${esc(JSON.stringify(it.args || {}).slice(0, 160))}</span>`,
        it.error ? `<span class="muted">${esc(it.error).slice(0, 160)}</span>` : "",
      ]);
      $("#a-out").innerHTML = `${table(["ID", "时间", "工具", "客户端 IP", "结果", "耗时", "参数", "错误"], rows)}
        <div class="row" style="margin-top:12px">
          <button id="a-prev" class="ghost" ${state.page <= 1 ? "disabled" : ""}>上一页</button>
          <span class="muted">第 ${state.page} 页 / 共 ${Math.max(1, Math.ceil(data.total / state.pageSize))} 页</span>
          <button id="a-next" class="ghost" ${state.page * state.pageSize >= data.total ? "disabled" : ""}>下一页</button>
        </div>`;
      $("#a-prev").addEventListener("click", () => { state.page -= 1; load(); });
      $("#a-next").addEventListener("click", () => { state.page += 1; load(); });
    }

    $("#a-search").addEventListener("click", () => {
      state.tool = $("#a-tool").value.trim();
      state.q = $("#a-q").value.trim();
      state.ok = $("#a-ok").value;
      state.page = 1;
      load().catch((e) => { $("#a-out").innerHTML = alertBox(e.message); });
    });
    await load();
  }

  async function viewLogs(el) {
    el.innerHTML = `
      <div class="toolbar">
        <input id="l-lines" type="number" min="1" max="2000" value="200" style="width:110px">
        <input id="l-q" placeholder="关键字过滤" style="width:240px">
        <button class="primary" id="l-load">刷新</button>
        <span class="muted" id="l-info"></span>
      </div>
      <pre class="log" id="l-out">加载中…</pre>`;
    async function load() {
      const qs = new URLSearchParams({ lines: $("#l-lines").value || "200" });
      const q = $("#l-q").value.trim();
      if (q) qs.set("q", q);
      const data = await api(`/logs?${qs}`);
      $("#l-info").textContent = data.file ? `${data.file} · ${data.size_text || bytes(data.size)} · 返回 ${data.returned} 行${data.truncated ? "（已截断）" : ""}` : "未配置文件日志";
      $("#l-out").textContent = data.content || "（空）";
    }
    $("#l-load").addEventListener("click", () => load().catch((e) => { $("#l-out").textContent = e.message; }));
    await load().catch((e) => { $("#l-out").textContent = e.message; });
  }

  /* ---------- 路由 ---------- */
  const ROUTES = {
    overview: { title: "概览", view: viewOverview },
    datasources: { title: "数据源", view: viewDatasources },
    query: { title: "查询", view: viewQuery },
    tools: { title: "工具", view: viewTools },
    exports: { title: "导出", view: viewExports },
    audit: { title: "审计", view: viewAudit },
    logs: { title: "日志", view: viewLogs },
  };

  function currentSection() {
    const m = location.pathname.match(/^\/ui\/([a-z-]*)/);
    const s = (m && m[1]) || "";
    return ROUTES[s] ? s : "overview";
  }

  let rendering = 0;
  async function route(force) {
    const section = currentSection();
    document.title = `${ROUTES[section].title} · mcp-dbtools`;
    $("#title").textContent = ROUTES[section].title;
    document.querySelectorAll("#nav a").forEach((a) => {
      a.classList.toggle("active", a.dataset.route === section);
    });
    const el = $("#view");
    const token = ++rendering;
    el.innerHTML = '<div class="empty">加载中…</div>';
    try {
      await ROUTES[section].view(el);
    } catch (e) {
      if (token !== rendering) return;
      el.innerHTML = alertBox(e.message);
    }
    if (force) { /* 强制重渲染时无需额外处理 */ }
  }

  document.addEventListener("click", (ev) => {
    const a = ev.target.closest("a[href^='/ui']");
    if (!a || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return;
    ev.preventDefault();
    if (location.pathname !== a.getAttribute("href")) history.pushState({}, "", a.getAttribute("href"));
    route();
  });
  window.addEventListener("popstate", () => route());
  $("#token-btn").addEventListener("click", askToken);

  const clock = $("#clock");
  setInterval(() => { clock.textContent = new Date().toLocaleTimeString(); }, 1000);

  route();
})();
