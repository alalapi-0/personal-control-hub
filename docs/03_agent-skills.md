# 03 agent-skills 参考分析报告

## 1. 基本信息

| 项 | 内容 |
|---|---|
| **名称** | Agent Skills (Addy Osmani) |
| **URL** | https://github.com/addyosmani/agent-skills |
| **本地路径** | `reference_lab/ai_native_company/agent-skills/` |
| **类型** | Skill 包 + Slash 命令 + Persona + Hooks |
| **License** | MIT |
| **Clone 状态** | ✅ 成功 |

## 2. 真正解决的问题

AI 编码代理默认走捷径（跳过 spec、测试、安全审查）。本仓库把 **Google 式工程实践** 编码为可执行 Skill 工作流（步骤 + 反合理化表 + 验证证据），并用 **7 个生命周期命令**（`/spec` … `/ship`）编排。

## 3. 关键目录表

| 路径 | 用途 |
|------|------|
| `skills/*/SKILL.md` | 23 个 skill（含 meta `using-agent-skills`） |
| `.claude/commands/` | Claude slash 命令 |
| `.gemini/commands/` | Gemini TOML 命令 |
| `agents/` | code-reviewer、security-auditor、test-engineer |
| `references/` | 测试/安全/性能/a11y/orchestration 清单 |
| `hooks/` | SessionStart、sdd-cache |
| `docs/` | 各 IDE 安装指南、skill-anatomy |
| `scripts/validate-skills.js` | Skill frontmatter 校验 |
| `AGENTS.md` / `CLAUDE.md` | 仓库级 Agent 指南 |

## 4. 可迁移机制

### 4.1 工作流编排

- 生命周期：DEFINE → PLAN → BUILD → VERIFY → REVIEW → SHIP。
- **`/ship`**：并行 fan-out 三 persona → 主会话 merge → GO/NO-GO + **强制 rollback plan**。

### 4.2 Agent 角色分工

- Persona 不调用 persona；slash command 或用户是 orchestrator。
- 三专家：审查、安全、测试；`/ship` 唯一认可的多 persona 并行模式。

### 4.3 协议/规则

- Skill 必含：Overview、When to Use、Process、Rationalizations、Red Flags、Verification。
- **Anti-rationalization 表**；**security-and-hardening**：Always / Ask First / Never。
- **doubt-driven-development**：高 stakes 时 CLAIM→DOUBT→RECONCILE。

### 4.4 Skills/Commands

- 命令是入口，自动拉起对应 skill；skills 也可按上下文自动触发。

### 4.5 Hooks/CI

- SessionStart hook；sdd-cache：Pre/Post WebFetch 304 重验证。
- CI：validate-skills + Claude Code 安装测试。

### 4.6 模板/脚手架

- `docs/skill-anatomy.md`；`references/*-checklist.md` 作渐进披露。

### 4.7 多仓库治理

- 全 lab 安装同一 plugin；各 repo `AGENTS.md` 声明 intent→skill 映射。
- orchestration-patterns.md 明确 **反模式：router persona**。

## 5. 启发

1. 把 `07_audio_asr_tts/scripts/check_repo.py` 扩展为 **`agent_gate.py`**：protocol yaml + Verification 证据。
2. 在 `security-release-gate` 复用 **`/ship` 三合一** 模式做发布前评审。
3. 为翻译 repo 定制 skill：**glossary-consistency**。
4. 小说生成：**incremental-implementation** + 每章 commit-as-save-point。
5. **browser-testing-with-devtools** 用于多平台发布与 Playwright 互补。
6. **context-engineering**：各 repo `AGENTS.md` + `repo_protocol` 作 session 必读。
7. **ci-cd-and-automation** skill 指导各子 repo GitHub Actions 最小集。
8. 禁止自建「总调度 Agent」；用 dashboard 文档列出 slash/技能顺序。
9. **documentation-and-adrs**：每个 pipeline 写 ADR 目录。
10. **deprecation-and-migration**：对齐 protocol 的 forbidden copy 规则。
11. Hooks：Round 0 禁用 WebFetch 到生产 API。
12. TTS：**security-and-hardening** 的 Ask First 对齐新外部 ASR 提供商。
13. 像素资产 repo 用 **performance-optimization** 的 measure-first。
14. lab 根 fork **精简 skill 子集**（23→8）。
15. `references/orchestration-patterns.md` 写入 multi-agent 规范，禁止 subagent 套娃。

## 6. 协议规则要点

- 有 1% 可能匹配 skill 就必须 invoke。
- Verification：**「看起来对」永远不够**。
- `/ship` 默认并行三评审；仅 ≤2 文件且 <50 行且无 auth/支付 可跳过。
- Persona 不能 spawn persona。
- SKILL.md 建议 <500 行，细节放 references。

## 7. 治理任务

1. 选定 lab 统一插件源（marketplace 或 `--plugin-dir`）。
2. 每 repo `AGENTS.md` 增加 **intent→skill** 表。
3. 实现 `agent_gate.py`：检查关键 skill 是否在该 Round 被引用。
4. 发布 repo 强制 `/ship` 或等价三报告模板。
5. 维护 lab 级 `references/security-checklist.md`。
6. CI 对所有自有 `skills/` 跑 validate-skills 逻辑。
7. sdd-cache 仅对文档域名白名单。
8. 与 Spec Kit：`/spec` 与 `speckit.specify` 二选一。
9. 记录每 Round 的 Verification 证据路径在 `update_log.md`。
10. 培训：反合理化表作为 Code Review 注释分类。

## 8. 风险

- 23 skills 全量安装上下文与合规成本高。
- `/ship` 并行三 subagent 消耗大。
- Google 文化预设可能不匹配中文内容生产。
- 与 BMAD/Spec Kit **三重流程叠加** 风险。

## 9. 结论

**agent-skills 是 lab 的「工程纪律与发布门禁层」**：最适合 security-release-gate、工具链 repo 和写代码的 pipeline。与 `repo_protocol_standard.yaml` + `check_repo.py` 天然契合；建议 fork 精简技能集 + 强制 Verification，并把 `/ship` 作为跨 repo 发布协议。
