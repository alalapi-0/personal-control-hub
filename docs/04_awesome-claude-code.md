# 04 awesome-claude-code 参考分析报告

## 1. 基本信息

| 项 | 内容 |
|---|---|
| **名称** | Awesome Claude Code |
| **URL** | https://github.com/hesreallyhim/awesome-claude-code |
| **本地路径** | `reference_lab/ai_native_company/awesome-claude-code/` |
| **类型** | Curated 资源索引 + Python 自动化工具链（CSV→README） |
| **License** | MIT |
| **版本** | 2.0.1 |
| **Clone 状态** | ✅ 成功 |

## 2. 真正解决的问题

Claude Code 生态碎片化（slash commands、CLAUDE.md、hooks、MCP、workflows）。本仓库做 **可检索、可验证、可 CI 的精选目录**，`THE_RESOURCES_TABLE.csv` 为单一事实来源。主 README 正重构中——索引价值在 `resources/` 与脚本。

## 3. 关键目录表

| 路径 | 用途 |
|------|------|
| `THE_RESOURCES_TABLE.csv` | 资源 SSOT |
| `templates/categories.yaml` | 分类体系 |
| `scripts/readme/` | README 生成管线 |
| `scripts/resources/` | 提交、排序、PR 创建 |
| `scripts/validation/` | 链接与单资源校验 |
| `scripts/maintenance/check_repo_health.py` | 仓库健康检查 |
| `resources/slash-commands/` | slash 命令样例 |
| `resources/claude.md-files/` | CLAUDE.md 范例 |
| `resources/workflows-knowledge-guides/` | 工作流指南 |
| `tests/` | 生成器/校验测试 |
| `.github/workflows/` | CI、链接校验、repo health |

## 4. 可迁移机制

### 4.1 工作流编排

- 收录 Design-Review-Workflow 等模式；Issue 驱动资源提交流程。

### 4.2 Agent 角色分工

- 资源级定义（如 design-review-agent）；分类组织（Agents、Hooks、Skills）。

### 4.3 协议/规则

- **CSV-first**；`find_repo_root(pyproject.toml)`；提交需 validate_links。

### 4.4 Skills/Commands

- `resources/slash-commands/*`：pr-review、release、create-worktrees 等可抄用 prompt。

### 4.5 Hooks/CI

- `make ci`：pytest、README 树一致性、链接检查。

### 4.6 模板/脚手架

- README 生成器 + 多风格输出（awesome/classic/flat）。

### 4.7 多仓库治理

- 中心 catalog + 各 repo 采纳条目；适合建 **`lab_resources.csv`**。

## 5. 启发

1. 建 **`lab/reference_catalog.csv`** 索引 15 个参考 repo + 各子项目 AGENTS/skill/hook。
2. Dashboard repo 用 **check_repo_health.py** 思路扫描子 repo：缺 AGENTS、缺 protocol。
3. 从 `resources/claude.md-files/` 挑 MCP enhanced 范例改 Playwright/MCP 文档。
4. 采用 **Design-Review-Workflow** 做动漫/像素 UI 评审。
5. `create-worktrees` slash 适合 **多 repo 并行 agent 会话**。
6. `release.md` slash 与 security-gate 流程合并。
7. 资源 ID 生成器用于 lab 内部工具条目编号。
8. README 树工具展示 solo-founder OS 目录导航。
9. 链接校验 CI 可移植到 lab docs。
10. `repo_protocol_standard.yaml` 增加 **resource_id** 字段。
11. 分类 yaml 启发：lab 工具分 Pipeline / Governance / Content / Infra。
12. 避免把 awesome 列表当作已审计代码。
13. 非正式 issue 检测启发 Round 描述模板化。
14. 官方 Anthropic quickstarts 作 Cursor 规则基线对比。
15. 与 `reference_lab/ai_native_company` 交叉索引避免重复说明。

## 6. 协议规则要点

- CSV 为 SSOT；README 为生成物。
- 脚本从 repo root 运行：`python -m scripts.*`。
- 新分类改 `categories.yaml` 再跑生成器。
- 资源提交以 Issue 模板为主路径。

## 7. 治理任务

1. 创建 lab 级 CSV catalog（资源类型、路径、许可、适用 repo）。
2. Dashboard 每周跑 health check 脚本。
3. 从 awesome 精选 10 条 slash 写入 lab 标准命令库。
4. 为 MCP/Playwright 建单独 category。
5. 指定采纳流程：review → CSV 行 → 子 repo copy。
6. 子 repo 互链使用稳定 resource_id。
7. 禁止未收录的第三方 hook 直接进 production repo。
8. 与 reference_lab 交叉索引。
9. 跟踪上游 README 重构完成后再同步 TOC。
10. 文档站生成 pipeline（可选）。

## 8. 风险

- 主 README 不可用期新人易误判价值。
- 收录资源质量参差，无安全审计。
- 生成管线复杂，solo founder 维护 CSV 成本高。
- 易陷入「收集癖」而非落地 protocol。

## 9. 结论

**awesome-claude-code 是 lab 的「情报与编目层」**，不是执行框架。最适合驱动 dashboard、catalog.csv 与 health CI；执行层用 Spec Kit + agent-skills + 自选 subagents。当前应直接挖掘 `resources/` 与 `scripts/`，并自建 lab catalog。
