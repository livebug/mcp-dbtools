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
bash scripts/build_offline.sh --platform all
# 产物 mcp-dbtools-offline-{linux-*.tar.gz,windows-*.zip}（wheel 含新版本号 + source/ 源码快照）
```
> 注意：`dist/`、`wheels*/`、`offline/`、`*.tar.gz`、`*.zip` 已被 .gitignore 忽略，不会进 git。
> **CI 会自动做这一步**（见第 6 步），本地构建仅用于内网紧急发布或不想等 CI 时。

### 6. 提交并打 tag
```bash
git add -A
git commit -m "release: v1.1.0 <一句话描述>"
git tag -a v1.1.0 -m "mcp-dbtools v1.1.0"
git push origin main --tags
```

> 推送 tag 后 **GitHub Actions（`.github/workflows/release.yml`）自动构建并发布 Release**：
> 跑测试 → 校验 tag 与 `pyproject.toml`/`__init__.py` 版本号一致 → 构建两个离线包 →
> 生成 `SHA256SUMS.txt` → 创建 Release 附件（项目 wheel + Linux/Windows 离线包）。
> GitHub 另会**自动附带 Source code (zip/tar.gz)** 纯源码包（约 0.4 MB），供内网只取源码时使用。
> **版本号写错会导致 CI 在「校验 tag 与版本号一致」这步直接失败**，Release 不会发布，必须先改版本号再重打 tag。

### 7. 验证 CI 与 Release
```bash
gh run list --limit 3          # 看 Release 工作流是否在跑
gh run watch <run-id>          # 跟踪日志
gh release view v1.1.0         # 确认附件：wheel + 两个离线包 + SHA256SUMS.txt
```
> **坑：仓库里第一次新增 workflow 时，与 tag 同一次 push 可能不触发**（GitHub 注册 workflow 与
> push 事件存在竞态，事件匹配不到任何 workflow 被静默忽略）。补救：
> `gh workflow run Release --ref v1.1.0`（带 tag ref 手动派发，仍会正常发 Release）；
> 之后的 tag 推送都能自动触发。

## 输出
- 提交 hash + tag 名（如 `v1.1.0`）
- 离线包：`mcp-dbtools-offline-YYYYMMDD.tar.gz`
- 摘要：新版本号、关键变化、产物位置

## 注意
- 打 tag 前确保测试全绿、文档与版本号同步。
- 若发布包含新增依赖，确认 `wheels/` 里已含（见 offline-deploy 技能）。
