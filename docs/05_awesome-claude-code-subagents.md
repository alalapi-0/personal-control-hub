# 05 awesome-claude-code-subagents 参考分析报告

## 1. 基本信息

| 项 | 内容 |
|---|---|
| **名称** | Awesome Claude Code Subagents |
| **URL** | https://github.com/VoltAgent/awesome-claude-code-subagents |
| **本地路径** | `reference_lab/ai_native_company/awesome-claude-code-subagents/` |
| **类型** | 154+ 子 Agent 定义库 + Claude Plugin 分类安装 |
| **License** | MIT |
| **规模** | 10 categories，154+ subagents |
| **Clone 状态** | ✅ 成功 |

## 2. 真正解决的问题

Claude Code 需要细粒度专家子代理，自写成本高。本库提供标准化 YAML frontmatter + 工具权限 + checklist 的 `.md` 文件，支持 marketplace 按类安装（`voltagent-lang`、`voltagent-qa-sec` 等）。

## 3. 关键目录表

| 路径 | 用途 |
|------|------|
| `categories/01-core-development/` … `10-research-analysis/` | 分领域 agent 定义 |
| `categories/09-meta-orchestration/` | 多 agent 编排 |
| `categories/04-quality-security/` | code-reviewer、penetration-tester 等 |
| `.claude-plugin/marketplace.json` | 插件市场元数据 |
| `install-agents.sh` | 交互式安装/卸载 |
| `tools/subagent-catalog/` | `/subagent-catalog:*` 搜索 |
| `CLAUDE.md` | 子 agent 文件格式、工具权限矩阵 |

## 4. 可迁移机制

### 4.1 工作流编排

- 单 agent 内 Development Workflow；Meta 类：agent-organizer、workflow-orchestrator、codebase-orchestrator。

### 4.2 Agent 角色分工

- 工具权限按角色类型：只读审查 vs 研究 vs 写代码。
- 项目 `.claude/agents/` 优先于全局。

### 4.3 协议/规则

- Frontmatter：name、description、tools、model。
- 改 categories 必须 bump marketplace 版本（CI 强制）。

### 4.4 Skills/Commands

- subagent-catalog：search/fetch/list/cache（12h TTL）。

### 4.5 Hooks/CI

- `enforce-plugin-version-bump.yml`；质量靠格式约定 + PR 校验。

### 4.6 模板/脚手架

- 每个 `.md` 即模板；`install-agents.sh` 或 curl 单机安装。

### 4.7 多仓库治理

- 全局 vs 项目 agents：跨 repo 共享通用专家；项目级放 domain 专家。

## 5. 启发

1. **按 repo 最小安装**：翻译 repo 只装 quality-security + 语言类。
2. 自建 `categories/lab/novel-pipeline.md` 等 lab 专用 agent。
3. **codebase-orchestrator** 对齐 `repo_protocol` 中大 refactor 需审批。
4. **security-engineer** + **penetration-tester** 纳入 security-release-gate。
5. **ai-writing-auditor** 用于小说/翻译「AI 腔」检测。
6. **deployment-engineer** 用于多平台发布 CI。
7. **context-manager** 指导长会话小说章节上下文裁剪。
8. 不要用 agent-organizer 替代 dashboard。
9. `subagent-catalog` 可接到 dashboard 搜索已安装 agents。
10. 与 agent-skills 的 code-reviewer **重名**：在 `AGENTS.md` 声明覆盖版。
11. 像素游戏：game-developer 类仅作 prompt 参考。
12. `scripts/agent_gate.py` 检查 `.claude/agents/` 是否含禁止的高权限 agent。
13. Marketplace 版本 bump 规则可移植到 lab monorepo plugins。
14. **禁止全量安装 154 agents**。
15. 优先 lab 私有 fork 目录 `categories/lab/`。

## 6. 协议规则要点

- description 决定自动委派，要写清 When to invoke。
- 工具列表最小权限。
- 项目 agent 覆盖全局同名。
- Meta orchestrator 不能嵌套 spawn。

## 7. 治理任务

1. 制定 **lab 允许 agent 白名单**（按 repo 类型）。
2. 为 8 条业务线各选 ≤5 个上游 agent。
3. Dashboard 记录已安装 agent 与版本。
4. 编写 3 个 lab 定制 agent（Novel、Translation、TTS-Dataset）。
5. 发布前强制 security + code-reviewer。
6. 定期 install-agents.sh 更新与 diff review。
7. 禁止在未隔离 repo 启用 penetration-tester。
8. orchestration 类 agent 限制为 **人类显式调用**。
9. agent tools 含 MCP 时单独审批。
10. 贡献回上游可选，优先 lab 私有 fork。

## 8. 风险

- 154 agents 选择瘫痪；自动委派可能招错专家。
- 部分 agent 过于激进（Bash/Write）。
- 与 agent-skills persona 重复/冲突。
- 子 agent 质量不一，无统一 Verification 块。

## 9. 结论

**本库是 lab 的「专家劳动力市场」**：按 repo 按需安装，配合 agent-skills 的 process 与 Spec Kit/BMAD 的制品流程。应：**全局少量通用专家 + 每 repo 2–5 个领域 agent + 自写 3 个 pipeline agent**，白名单 gate 限制高权限工具。
