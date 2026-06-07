# 06 contains-studio-agents 参考分析报告

## 1. 基本信息

| 项 | 内容 |
|---|---|
| **名称** | Contains Studio Agents |
| **URL** | https://github.com/contains-studio/agents |
| **本地路径** | `reference_lab/ai_native_company/contains-studio-agents/` |
| **类型** | 纯 Markdown Agent 库（无运行时、无 CI、无 package） |
| **目标平台** | Claude Code Sub-Agents |
| **规模** | 7 部门 × 约 30+ Agent + 2 bonus |
| **Clone 状态** | ✅ 成功 |

## 2. 真正解决的问题

解决 **「通用 coding agent 缺乏组织分工」**：把产品、工程、设计、营销、运营、测试拆成**可触发专家**，用 YAML frontmatter + 长 system prompt + 内嵌 `<example>` 教模型何时自动选用谁。面向 **6 天冲刺式工作室**。

## 3. 关键目录表

| 路径 | 用途 |
|------|------|
| `engineering/*.md` | 工程类 Agent |
| `design/*.md` | UI/UX/品牌 Agent |
| `marketing/*.md` | 增长、社媒 Agent |
| `product/*.md` | 趋势、反馈、排期 Agent |
| `project-management/*.md` | 实验、交付、制片 Agent |
| `studio-operations/*.md` | 财务、法务、基础设施 Agent |
| `testing/*.md` | API/性能/工作流优化 Agent |
| `bonus/studio-coach.md` | 多 Agent 协调教练（主动触发） |
| `README.md` | 安装、目录、定制清单、文件模板 |

## 4. 可迁移机制

### 4.1 工作流编排

- 无代码编排；靠自然语言 + studio-coach 协调。不适合 TTS/安全 gate 的确定性 DAG。

### 4.2 Agent 角色分工

- 部门制 + kebab-case name；description 内 3–4 个 `<example>` 定义触发条件。

### 4.3 协议/规则

- 规则全在单文件 system prompt + README 定制清单；应外置到 `AGENTS.md` + `repo_protocol_standard.yaml`。

### 4.4 Skills/Commands

- 无 Skills 目录；`tools:` 白名单。与 Cursor Skills 互补：Sub-Agent 人格包。

### 4.5 Hooks/CI

- **无** hooks、无 CI。不能替代 `check_repo.py` / `agent_gate.py`。

### 4.6 模板/脚手架

- README 内 Agent File Structure Template（frontmatter + 500+ 字 prompt）。

### 4.7 多仓库治理

- 中央 persona 库 + 各业务 repo 的 `AGENTS.md` 引用「启用哪些角色」。

## 5. 启发

1. 用 **部门目录**（engineering/content/security）组织跨 9 条产品线的 Agent。
2. description 里嵌 Context + user + assistant + commentary 的 XML 示例，稳定触发。
3. 声明 **Proactive Agents**（studio-coach、test-writer-fixer）对应 PR 后自动跑。
4. README Required Components 清单可当新建 Agent 的 PR review checklist。
5. `tools:` 白名单写入 `repo_protocol_standard.yaml` 的 `agent_tool_policy`。
6. 6 天冲刺哲学映射 ROADMAP 的「每 Round 可交付」，但需可验收脚本。
7. **不要**把 30 个 Agent 全拷进每个 repo。
8. 与 AgentHub 类似：维护「精选子集 + 版本 pin」。
9. 每个业务 repo 应写明默认 Agent 阅读顺序 + 禁止事项。
10. `scripts/agent_gate.py` = persona 文件存在性 + frontmatter schema 检查。
11. studio-coach 话术可提炼为 dashboard「总指挥」系统提示。
12. 营销类 Agent 对多平台发布 repo 有直接复用价值。
13. test-writer-fixer 对应 pre-commit / CI 里的 agent 指令。
14. 性能指标应放在 `PROJECT_STATE.md` + `update_log.md`。
15. 只吸收 prompt 结构，禁止整文件复制进 `src/`。

## 6. 协议规则要点

- 安装：复制到 `~/.claude/agents/`。
- 每个 Agent：name / description / color / tools + 500+ 字职责。
- 部分 Agent 应主动介入（coach、test-writer-fixer）。
- 无数据边界、无 Round——必须由 `repo_protocol_standard.yaml` 补上。

## 7. 治理任务

1. 建 `personas/contains-studio/` 精选子集（≤12 个）+ manifest.yaml。
2. 为 novel/translation 各写 3 个定制 Agent，套用 README 模板。
3. 在 `repo_protocol_standard.yaml` 增加 `agent_persona` 节。
4. 实现 `scripts/agent_gate.py` 校验 persona 引用。
5. persona 库与业务代码分 repo。
6. 文档记录全局 vs 项目 agents 优先级。
7. 每季度审查 proactive 列表与 CI 触发器一致。
8. security gate fork legal-compliance-checker 思路。
9. 在 `update_log.md` 记录吸收结构、未复制正文。
10. 与 TTS 仓 `check_repo.py` 联动。

## 8. 风险

- 无测试、无版本锁；上游改 prompt 即可改变行为。
- 30+ Agent 增加选择噪音与 token 成本。
- 工具白名单与 Cursor 实际工具名可能不一致。
- 营销类 Agent 可能鼓励对外 API，需协议禁止泄露数据路径。

## 9. 结论

**最适合作为「角色电话簿」参考**，不适合作为 OS 或 CI 核心。最有价值的是 frontmatter + 多示例触发 + 部门分类 + proactive 列表；必须与 `AGENTS.md`、`repo_protocol_standard.yaml`、统一 `agent_gate.py` 组合使用。
