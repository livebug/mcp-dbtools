---
name: release
description: '发布 mcp-dbtools 新版本并打 git tag。Use when: 发布版本、版本号升级、bump version、更新 CHANGELOG、git tag、release、发版、打 tag、制作发布包。'
argument-hint: '发布一个新版本（如 v1.1.0）'
user-invocable: true
---

# 版本发布流程

mcp-dbtools 遵循 Semantic Versioning。当前版本见 `CHANGELOG.md` 与 `pyproject.toml`。

## 何时使用
- 功能达到可发布状态，需要打 tag / 生成发布包时
- 版本号需要升级时

## 版本号位置（两处必须同步）
1. `pyproject.toml` → `[project] version`
2. `src/mcp_dbtools/__init__.py` → `__version__`

## 流程

### 1. 跑全量测试
```bash
. .venv/bin/activate && python -m pytest -q   # 必须全绿
```

### 2. 升级版本号
- 破坏性变更/正式发布 → 升主版本（`1.0.0` → `2.0.0`）
- 新增功能 → 升次版本（`1.0.0` → `1.1.0`）
- 修复 → 升补丁（`1.0.0` → `1.0.1`）

同步更新 `pyproject.toml` 与 `__init__.py`。

### 3. 更新 CHANGELOG.md
在 `## [Unreleased]`（如有）或顶部新增：

```markdown
## [1.1.0] - YYYY-MM-DD

### 新增
- ...

### 修复
- ...
```

### 4. 更新文档（如本次发布涉及功能变化）
- `README.md`（功能特性 / 工具表 / 端点 / 目录结构）
- `docs/使用说明.md`（版本标识）
- `.env.example`（新增配置项）

### 5. 重新构建离线部署包
```bash
bash scripts/build_offline.sh
# 产物 mcp-dbtools-offline-YYYYMMDD.tar.gz（wheel 含新版本号）
```
> 注意：`dist/`、`wheels/`、`offline/`、`*.tar.gz` 已被 .gitignore 忽略，不会进 git。

### 6. 提交并打 tag
```bash
git add -A
git commit -m "release: v1.1.0 <一句话描述>"
git tag -a v1.1.0 -m "mcp-dbtools v1.1.0"
git push origin main --tags
```

## 输出
- 提交 hash + tag 名（如 `v1.1.0`）
- 离线包：`mcp-dbtools-offline-YYYYMMDD.tar.gz`
- 摘要：新版本号、关键变化、产物位置

## 注意
- 打 tag 前确保测试全绿、文档与版本号同步。
- 若发布包含新增依赖，确认 `wheels/` 里已含（见 offline-deploy 技能）。
