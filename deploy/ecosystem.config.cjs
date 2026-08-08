// =============================================================
// pm2 部署配置（内网生产环境）
//
// 前置：已在 /opt/mcp-dbtools 完成离线安装（见 deploy/install_offline.sh）
//
// 启动:
//   pm2 start deploy/ecosystem.config.cjs
//   pm2 save                      // 保存进程列表，开机自启需配合 pm2 startup
//
// 常用:
//   pm2 logs mcp-dbtools          // 查看日志
//   pm2 reload mcp-dbtools        // 重启
//   pm2 stop mcp-dbtools
// =============================================================
module.exports = {
  apps: [
    {
      name: "mcp-dbtools",
      // 使用虚拟环境中的 python 解释器执行模块
      script: "/opt/mcp-dbtools/.venv/bin/python",
      args: "-m mcp_dbtools --transport streamable-http",
      cwd: "/opt/mcp-dbtools",
      interpreter: "none",
      instances: 1,
      autorestart: true,
      max_restarts: 10,
      min_uptime: "10s",
      restart_delay: 3000,
      kill_timeout: 10000,
      env: {
        NODE_ENV: "production",
        // MCP 服务配置（如需改端口/鉴权，改这里或 .env）
        MCP_DBTOOLS_HOST: "0.0.0.0",
        MCP_DBTOOLS_PORT: "8000",
        MCP_DBTOOLS_TRANSPORT: "streamable-http",
        MCP_DBTOOLS_CONFIG: "/opt/mcp-dbtools/config/datasources.json",
        MCP_DBTOOLS_DRIVERS_DIR: "/opt/mcp-dbtools/drivers",
        MCP_DBTOOLS_MAX_ROWS: "1000",
        // 监控与审计
        MCP_DBTOOLS_HISTORY_SIZE: "500",
        MCP_DBTOOLS_AUDIT_FILE: "/opt/mcp-dbtools/logs/audit.jsonl",
        MCP_DBTOOLS_METRICS_ENABLED: "true",
        // 脚本执行根目录
        MCP_DBTOOLS_SCRIPT_ROOT: "/opt/mcp-dbtools/scripts/sql",
        // 鉴权 Token（留空表示不鉴权）。建议通过 pm2 env 或系统环境注入
        MCP_DBTOOLS_AUTH_TOKEN: "",
        // 数据源密码（{ENV:VAR} 引用，避免明文写入 datasources.json）
        GAUSS_PASSWORD: "",
        TDH_PASSWORD: "",
      },
      out_file: "/opt/mcp-dbtools/logs/out.log",
      error_file: "/opt/mcp-dbtools/logs/error.log",
      merge_logs: true,
      time: true,
    },
  ],
};
