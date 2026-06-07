# 07 gh-aw 参考分析报告

## 1. 基本信息

| 项 | 内容 |
|---|---|
| **名称** | GitHub Agentic Workflows (gh-aw) |
| **URL** | https://github.com/github/gh-aw |
| **本地路径** | `reference_lab/ai_native_company/gh-aw/` |
| **类型** | Go CLI 扩展（`gh aw`）+ Markdown 工作流 → GitHub Actions |
| **核心公式** | Actions + Agent + Safety（safe-outputs、沙箱、只读默认权限） |
| **Clone 状态** | ✅ 成功 |

## 2. 真正解决的问题

把 **「在 GitHub 上让 AI 自动改仓库」** 从危险脚本变成 **可编译、可审计、默认只读** 的制品：自然语言 `.github/workflows/*.md` 工作流；写操作只能走 sanitized safe-outputs；Copilot 引擎必须通过 GitHub MCP；对 coding agent 强制 PR + 预提交验证。

## 3. 关键目录表

| 路径 | 用途 |
|------|------|
| `cmd/`、`pkg/` | Go 编译器、workflow 解析、引擎适配 |
| `.github/workflows/*.md` | 238+ 狗食工作流 |
| `.github/workflows/*.lock.yml` | 编译产物（必须入库） |
| `.github/aw/` | 工作流语法、safe-outputs、subagents、patterns |
| `.github/skills/` | Copilot Chat 用专项 skills |
| `AGENTS.md` | 贡献者强制规范（MCP、lint、recompile、PR） |
| `Makefile` | agent-finish、agent-report-progress、recompile |
| `specs/security-architecture-spec.md` | 安全架构 |
| `schemas/agent-output.json` | Agent 输出 schema |

## 4. 可迁移机制

### 4.1 工作流编排

- Markdown + YAML frontmatter → `gh aw compile` → GHA；触发器：issue、PR、cron、webhook、slash_command。

### 4.2 Agent 角色分工

- 单工作流内 subagents；仓库级「一个 workflow 一个使命」。

### 4.3 协议/规则

- `AGENTS.md` 极强：必须 PR、`make agent-report-progress`、MCP 规则。可直接作为 lab `.github` 模板蓝本。

### 4.4 Skills/Commands

- `.github/skills/agentic-workflows` 等；BE LAZY 按需加载。

### 4.5 Hooks/CI

- `agent-report-progress` = build+fmt+lint+test-unit；`agent-finish` 全量。**等价于 `agent_gate.py` 的工业级实现**。

### 4.6 模板/脚手架

- `create-agentic-workflow.md` 最小 frontmatter；`schema-demos/`。

### 4.7 多仓库治理

- `package.md` 打包工作流仓库；适合 lab 级中央 compliance workflows 分发到子 repo。

## 5. 启发

1. **主 Agent job 只读 + safe-outputs 写**——LLM 只产出 patch 建议，merge 走人工或签名脚本。
2. 双检查点写入 `agent_gate.py` 的 `--stage pre-edit|pre-pr`。
3. `.lock.yml` 必须跟踪：编译后的 CI 配置对 agent 可复现。
4. `strict: true` + `network.allowed` 白名单：TTS/小说仓对外网 API 同样声明。
5. Copilot 必须用 gh-proxy——MCP security gate 禁止 agent 直接 `gh api` 读敏感仓。
6. `list_code_scanning_alerts` 必须带 state/severity 过滤。
7. `specs/security-architecture-spec.md` 作 security-release-gate 威胁模型附录。
8. 238 个 dogfood workflow 按场景抄 3–5 个（PR review、weekly summary）。
9. 与 `repo_protocol_standard.yaml` 的 `data_protection.never_commit` 同源思想。
9. Agent 改动必须 PR，即使自审自并。
10. 多语言 monorepo 的 agent_gate 应分语言 profile。
11. pytest 可分 slow/governance 标签。
12. 业务流水线用 Python Round，**平台治理**用 gh-aw，职责分离。
13. 记录 gh-aw release 版本 pin。
14. 定义哪些写操作必须人工 approve。
15. 在 protocol yaml 增加 `ci_agent_workflows` 允许名单。

## 6. 协议规则要点

- frontmatter 改 → `gh aw compile`。
- agent 主 job `contents: read`；写 PR/issue 走 safe-outputs。
- Copilot：GitHub 数据仅经 MCP。
- 任何文件变更必须 PR；禁止跳过 agent-report-progress。
- `.lock.yml` 禁止加入 `.gitignore`。

## 7. 治理任务

1. 在 security-release-gate 试点 1 个 gh aw 工作流。
2. 起草 `scripts/agent_gate.py`，子命令对齐 agent-report-progress / agent-finish。
3. 将 AGENTS.md MCP/PR 条款精简进各业务 repo。
4. 建 `docs/governance/github_agent_rules.yaml` 摘录 safe-output 允许列表。
5. 禁止 Copilot/Claude 直接 `gh api` 读私有数据路径。
6. 每周 workflow health 检查各 repo CI 绿度。
7. 版本 pin gh-aw release。
8. 定义写操作人工 approve 矩阵。
9. protocol yaml 增加 ci_agent_workflows 节。
10. 文档化 gh-aw 与 Round 流水线职责分离。

## 8. 风险

- GitHub 绑定；非 GitHub 托管 repo 收益有限。
- frontmatter/schema/compile 学习曲线高。
- 历史版本 billing bug；引擎费用叠加 Actions 分钟。
- safe-output 配置错误仍可能合并不当 PR。
- 仓库体积大，克隆与认知负担高。

## 9. 结论

**多 repo 治理与 security-release-gate 的首选参考**。核心价值是只读 agent + 显式写通道 + 编译锁文件 + Agent 贡献双检查点 + MCP 访问规范。应把 `make agent-report-progress` 语义落地为全 lab 的 `scripts/agent_gate.py`。
