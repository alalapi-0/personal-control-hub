# 01 spec-kit 参考分析报告

## 1. 基本信息

| 项 | 内容 |
|---|---|
| **名称** | GitHub Spec Kit / Specify CLI |
| **URL** | https://github.com/github/spec-kit |
| **本地路径** | `reference_lab/ai_native_company/spec-kit/` |
| **类型** | 规范驱动开发（SDD）工具链 + Python CLI + 多 Agent 集成层 |
| **License** | MIT |
| **版本线索** | `pyproject.toml`：`specify-cli` 0.9.x dev |
| **Clone 状态** | ✅ 成功（depth 1） |

## 2. 真正解决的问题

把「规格说明」从一次性文档变成**可执行主产物**：通过 `/speckit.*` 命令链（constitution → specify → clarify → plan → tasks → analyze → implement）把需求、技术计划、任务分解与实现串成可重复流程，并支持 30+ AI 编码 Agent 的统一脚手架（`specify init --integration <agent>`）。

核心矛盾：**vibe coding 与可预测交付之间的鸿沟**——用分支化 feature spec（`specs/001-xxx/`）、模板、脚本与可选 YAML workflow 引擎，让多轮澄清与门禁成为默认路径。

## 3. 关键目录表

| 路径 | 用途 |
|------|------|
| `src/specify_cli/` | Specify CLI 主实现 |
| `src/specify_cli/integrations/` | 各 Agent 集成子包（Markdown/TOML/YAML/Skills） |
| `src/specify_cli/workflows/` | Workflow 引擎（gate、fan-out、shell 等） |
| `templates/` | spec/plan/tasks/constitution 与 command 模板 |
| `templates/commands/` | `/speckit.*` 命令源模板 |
| `scripts/bash/`, `scripts/powershell/` | init 后项目内脚本 |
| `workflows/speckit/workflow.yml` | 内置全 SDD 周期 workflow |
| `extensions/` | 扩展（git、agent-context 等） |
| `presets/lean/`, `presets/scaffold/` | 预设覆盖模板/术语 |
| `AGENTS.md` | 贡献者：如何新增 Agent 集成 |
| `spec-driven.md` | SDD 方法论长文 |
| `.github/workflows/` | pytest/ruff、release、社区 catalog |

## 4. 可迁移机制

### 4.1 工作流编排

- Slash 命令流水线：constitution → specify → clarify → plan → tasks → analyze → implement。
- YAML Workflow 引擎：`gate` 人工审批、`fan-out/fan-in`、可恢复状态（`.specify/workflows/runs/<run_id>/`）。
- 命令模板含 **handoffs**（specify 可 handoff 到 plan/clarify）。

### 4.2 Agent 角色分工

- 不内置人格 Agent，**同一套 SDD 命令**适配不同宿主（Claude/Copilot/Codex/Cursor）。
- handoffs 实现轻量角色切换，非长期组织编制。

### 4.3 协议/规则

- **Constitution**（`.specify/memory/constitution.md`）：全阶段最高原则。
- Feature 分支命名：`feat|fix|docs/<slug>`。
- 模板优先级栈：project overrides > presets > extensions > core。
- agent-context 扩展：`<!-- SPECKIT START/END -->` 管理 `AGENTS.md` 片段。

### 4.4 Skills/Commands

- 双轨：Slash commands 或 Skills 模式（`speckit-<name>/SKILL.md`）。
- 命令模板占位符：`$ARGUMENTS`、`{SCRIPT}`、`__AGENT__`。

### 4.5 Hooks/CI

- Extension hooks：`extensions.yml` 的 `hooks.before_specify` 等。
- CI：ruff + pytest 多 OS/Python。
- 项目级 bash：`check-prerequisites.sh`、`create-new-feature.sh`。

### 4.6 模板/脚手架

- `specify init` 生成 `.specify/` + `specs/`；lean preset 精简五命令。
- plan 阶段产物：`contracts/`、`data-model.md`、`research.md`。

### 4.7 多仓库治理

- 组织级 catalog：`SPECKIT_CATALOG_URL` 覆盖上游目录。
- 每个产品 repo `specify init` + 共享 org preset（合规 spec 格式、安全 plan 门禁）。

## 5. 启发（针对 lab 多 repo 一人公司 OS）

1. 在 `02_novel_generation`、`03_novel_translation` 等 repo 采用 **`specs/<round-id>/spec.md` + `plan.md` + `tasks.md`**，与 `docs/governance/repo_protocol_standard.yaml` 的 Round 模型对齐。
2. 把 `repo_protocol_standard.yaml` 的 `core_principles` 映射为各 repo 的 **constitution 等价物**。
3. 为 **security-release-gate** 加自定义 extension：发布前检查清单「像给 spec 写单元测试」。
4. 在 lab 根维护 **`integration registry` 表**（仿 `INTEGRATION_REGISTRY`），统一 Cursor/Claude/Codex 命令目录。
5. 用 **workflow gate** 模拟 Round 验收：Round 结束必须 `approve` 才进入下一 implement 批次。
6. **fan-out** 可用于「翻译 QA + 安全扫描 + 术语一致性」三路子代理。
7. 新建 `scripts/agent_gate.py`：包装 `check-prerequisites` + protocol yaml 校验 + 可选 analyze 输出解析。
8. `AGENTS.md` 增加 **managed section 标记**（SPECKIT 式 START/END），避免多 Agent 互相覆盖。
9. 像素资产 / 动漫视频 repo 用 **feature 分支与 spec 目录一一对应**，便于 dashboard 聚合 PR 状态。
10. 多平台发布 repo：plan 阶段强制 **`contracts/`**（API/内容 schema）。
11. **Lean preset**：内容生产 repo 用单文件 spec/plan/tasks 降 ceremony。
12. 参考 `hooks.before_specify`：治理 Round 禁止真实 API/模型调用（对齐 `07_audio_asr_tts/AGENTS.md`）。
13. Dashboard repo 索引各子 repo 的 **`specs/*/spec.md` 状态 + workflow run_id**。
14. Playwright/MCP：E2E 场景写进 spec 的 Acceptance Checklist，implement 前 analyze 交叉一致性。
15. 勿全盘安装 Specify CLI 到每个 repo；在 **模板 monorepo** 维护 overrides 再 copy 到子项目。

## 6. 协议规则要点

- Spec 阶段只写 **what/why**，plan 阶段才定技术栈。
- **clarify 在 plan 之前**为推荐路径。
- Constitution 优先于 plan/implement 中的过度设计。
- Extension/preset/社区产物：安装前自行 review。
- CLI 集成 `key` 对 CLI 工具必须等于可执行文件名。

## 7. 治理任务（5–10 条）

1. 选定 2–3 个 pilot repo（translation + security-gate）跑通 SDD 目录结构。
2. 起草 lab 级 **constitution**（数据不进 git、Round 粒度、许可证吸收规则）。
3. 定义 org preset：中文 spec 章节 + `reading_order` 字段。
4. 实现 `scripts/agent_gate.py`：校验 AGENTS.md 阅读顺序与 protocol yaml 存在性。
5. 为 workflow catalog 建私有 JSON（仅内部 extension/workflow）。
6. 统一各 repo `AGENTS.md` 中 Spec Kit managed section 或等效块。
7. 在 CI 中对 pilot repo 跑 `check-prerequisites`（可移植 bash）。
8. 记录每个子域（TTS/翻译/发布）的 **handoff 矩阵**。
9. 季度审查社区 extension 安装清单。
10. Dashboard 展示各 repo 当前 feature spec 分支与 gate 状态。

## 8. 风险

- Workflow/社区扩展执行本地 shell 与 Agent CLI，供应链与误操作风险高。
- 模板过重导致 solo founder **流程疲劳**。
- 30+ 集成维护成本高，多工具栈 **版本漂移**。
- `implement` 直接跑构建命令，无沙箱可能破坏工作区。
- 偏软件工程 greenfield，内容生产管线需 lean 改造。

## 9. 结论

**Spec Kit 最适合作为 lab 的「单 feature / 单 Round 交付协议层」**：把 `repo_protocol_standard.yaml` + Round roadmap 落成可执行的 spec/plan/tasks 树，并用 gate/workflow 做人工验收。建议 pilot 1–2 个代码型 repo，内容型 repo 用 lean 模板；与 BMAD（产品敏捷）、agent-skills（工程纪律）互补而非替代。
