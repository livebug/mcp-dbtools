---
name: security-review
description: '审查 mcp-dbtools 的安全配置与安全机制是否到位。Use when: 安全审查、安全检查、安全配置、鉴权 token、限流、熔断、密码加密、审计检查、漏洞排查、加固。'
argument-hint: '对当前配置做一次安全审查'
user-invocable: true
---

# 安全配置审查

对 mcp-dbtools 的配置与部署做安全检查，识别明文密码、开放接口、缺失防护等问题。

## 何时使用
- 部署前 / 上线前做安全核查
- 排查安全相关告警或配置疑问

## 审查清单

### 1. 密码与密钥
- [ ] `config/datasources.json` / `datasources.json.example` 中**无明文密码**（应全是 `{ENC:...}` 或占位）
- [ ] `MCP_DBTOOLS_SECRET_KEY` 已设置且**未提交**到 git（`.env` 被 .gitignore 忽略）
- [ ] 生成密文：`echo -n "pw" | python scripts/encrypt_password.py --stdin`
- [ ] 修改密钥后所有 `{ENC:...}` 已重新生成

### 2. 访问控制
- [ ] 已设置 `MCP_DBTOOLS_AUTH_TOKEN`（未设置则 `/mcp` 免鉴权，仅适合内网调试）
- [ ] 对外暴露时建议 **HTTPS 或内网**（token 明文比较，不适用公网裸 HTTP）
- [ ] `/metrics`、`/audit/api`、`/logs/api`、`/export/{id}/download` 需鉴权（默认已保护）

### 3. 限流与防滥用
- [ ] `MCP_DBTOOLS_RATE_LIMIT_ENABLED=true`（默认）
- [ ] `MCP_DBTOOLS_RATE_LIMIT_QPS` 数值合理（默认 10）
- [ ] 熔断阈值 `MCP_DBTOOLS_CIRCUIT_FAIL_THRESHOLD`（默认 3）与冷却时间合理

### 4. 写操作与注入
- [ ] 写操作二次确认 `confirm=true` 机制未绕过（`execute_query` / `execute_script` / `execute_in_transaction`）
- [ ] `execute_script` 默认 `read_only=true`，`MCP_DBTOOLS_SCRIPT_ROOT` 已限定
- [ ] 工具不接受任意 JDBC URL / 任意文件路径（脚本路径防目录穿越）

### 5. 数据规模与泄露
- [ ] `MCP_DBTOOLS_MAX_ROWS` / `MAX_ROWS_LIMIT` 防 OOM
- [ ] `list_datasources` 与审计输出不含密码（`safe_dict` / 审计 args 脱敏）
- [ ] `logs/`、`exports/`、`*.log` 均被 .gitignore 忽略

### 6. 依赖与版本
- [ ] `mcp` 锁定 1.x（<2.0）
- [ ] 依赖版本无已知高危漏洞（可用 `pip-audit` 等检查）

## 输出
- 通过 / 存在问题的清单（逐项 ✅/⚠️/❌）
- 每个问题的**修复建议**与涉及的配置项 / 文件

## 注意
- 审查只读为主，发现问题报告而非擅自改动；重大改动征求确认。
- 涉及真实密码/密钥时绝不输出明文，只指出「第 N 处存在明文」。
