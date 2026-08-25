// =============================================================
// mcp-dbtools pm2 部署配置（Windows 版）
//
// 前置：
//   1. 已完成 install_offline.bat 离线安装
//   2. 已安装 pm2：npm install -g pm2
//
// 使用（把下面的 D:/mcp-dbtools 改成你的实际部署目录）：
//   pm2 start deploy/ecosystem-windows.config.cjs
//   pm2 save                          // 保存进程列表
//   pm2-startup install               // Windows 开机自启（需 npm i -g pm2-windows-startup）
//
// 常用：
//   pm2 logs mcp-dbtools              // 查看日志
//   pm2 reload mcp-dbtools            // 重启
//   pm2 stop mcp-dbtools
// =============================================================
module.exports = {
  apps: [
    {
      name: "mcp-dbtools",
      // Windows 虚拟环境 python（请改成你的部署目录）
      script: "D:/mcp-dbtools/.venv/Scripts/python.exe",
      args: "-m mcp_dbtools --transport streamable-http",
      cwd: "D:/mcp-dbtools",
      interpreter: "none",
      instances: 1,
      autorestart: true,
      max_restarts: 10,
      min_uptime: "10s",
      restart_delay: 3000,
      kill_timeout: 10000,
      env: {
        NODE_ENV: "production",
        // MCP 服务配置（如需改端口/鉴权，改这里或系统环境变量）
        MCP_DBTOOLS_HOST: "0.0.0.0",
        MCP_DBTOOLS_PORT: "8000",
        MCP_DBTOOLS_TRANSPORT: "streamable-http",
        MCP_DBTOOLS_CONFIG: "D:/mcp-dbtools/config/datasources.json",
        MCP_DBTOOLS_DRIVERS_DIR: "D:/mcp-dbtools/drivers",
        MCP_DBTOOLS_MAX_ROWS: "1000",
        // 监控与审计
        MCP_DBTOOLS_HISTORY_SIZE: "500",
        MCP_DBTOOLS_AUDIT_FILE: "D:/mcp-dbtools/logs/audit.jsonl",
        MCP_DBTOOLS_METRICS_ENABLED: "true",
        // 脚本执行根目录
        MCP_DBTOOLS_SCRIPT_ROOT: "D:/mcp-dbtools/scripts/sql",
        // 鉴权 Token（留空表示不鉴权）
        MCP_DBTOOLS_AUTH_TOKEN: "",
        // 数据源密码（{ENV:VAR} 引用，避免明文写入 datasources.json）
        GAUSS_PASSWORD: "",
        TDH_PASSWORD: "",
      },
      out_file: "D:/mcp-dbtools/logs/out.log",
      error_file: "D:/mcp-dbtools/logs/error.log",
      merge_logs: true,
      time: true,
    },
  ],
};
