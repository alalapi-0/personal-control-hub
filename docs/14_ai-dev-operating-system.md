# 14 ai-dev-operating-system 参考分析报告

## 1. 基本信息

| 项 | 内容 |
|---|---|
| **名称** | AI Dev Operating System |
| **URL** | https://github.com/lglucas/ai-dev-operating-system |
| **本地路径** | `reference_lab/ai_native_company/ai-dev-operating-system/` |
| **类型** | Day-zero AI 辅助 SaaS 开发操作系统 |
| **版本** | v0.4.5 |
| **License** | MIT |
| **Clone 状态** | ✅ 成功 |

## 2. 真正解决的问题

AI coding agent 从 chaos 启动 → 结构化执行：WIZARD 17 阶段强制顺序；session-log 决策记忆；BP / Product Brief / Technical Plan / Sprints 文档分层；detach-os 防误 push；Vibe Coder Pack（secrets、legal、bug-triage）；External Repo Registry。

## 3. 关键目录表

| 路径 | 用途 |
|------|------|
| `START-HERE.md` | Claude 首读入口 |
| `CLAUDE.md` | 10 条 Golden rules |
| `ETHOS.md` | 15 条 manifesto |
| `WIZARD.md` | Project Genesis Wizard 17 阶段 |
| `.claude/agents/` | 11 专 Agent |
| `.claude/skills/` | 28+ skills |
| `.claude/rules/` | security、privacy、git 等模块化规则 |
| `docs/registry/INDEX.md` | 外部 repo 包注册表 |
| `scripts/{init-project,detach-os}.sh` | 初始化与脱离 OS origin |
| `.github/workflows/ci.yml` | 结构校验 + 禁 .env + 链接检查 |

## 4. 可迁移机制

### 4.1 工作流编排

- WIZARD 17 阶段：Ideation → research → BP → Product/Technical Plan → Prototype Lab → Sprint 0/1。

### 4.2 Agent 角色分工

- coordinator、devils-advocate、business-red-team、technical-security-red-team、legal-compliance 等。

### 4.3 协议/规则

- ETHOS 15 原则；Golden rules：不先 coding；BP 人工 review 门禁；secrets never in repo。

### 4.4 Skills/Commands

- project-genesis、sprint-management、registry-pick、secrets-discipline、cost-watchdog。

### 4.5 Hooks/CI

- CI 必须有 .claude/{agents,rules,skills,commands}；禁 root 重复目录；禁提交 .env。

### 4.6 模板/脚手架

- templates/project/CLAUDE.md；stack-packs（generic-saas、nextjs-supabase）。

### 4.7 多仓库治理

- OS 与产品分离；registry-pick 按 stack/domain 推荐外部 pack，只推荐不自动安装。

## 5. 启发

1. 以 ai-dev-os 作 **新 repo 模板根**，各业务 repo Use template 衍生。
2. ETHOS.md 写入 lab 根，各 repo AGENTS.md 引用。
3. multi-ai-review 用于出版定价、TTS 版权、anime 内容合规。
4. 文档分层：novel 的「大纲」≠「章节 sprint」≠「世界观 bible」分文件。
5. registry 思维：MCP、Cursor skills、AgentHub hooks 统一 catalog。
6. Prototype Lab 先于 production：pixel-game/anime UI 先 3 个 HTML 方向。
7. /processize：手动跑通 novel→translation→TTS 一次再自动化。
8. session-log 是 feature：跨 repo 决策必须写 session-log。
9. detach-os.sh 防误 push——lab 模板仓需同样机制。
10. secrets-discipline + cost-watchdog 对非代码创始人管 9 repo 必备。
11. agent_gate.py 对齐 CI 结构校验（.claude 目录完整性）。
12. 衍生 repo 定义 fast-path Wizard（仍保留 red-team + session-log）。
13. security-gate 对齐 docs/security-baseline.md。
14. dashboard 消费 docs/SPRINTS.md + session-log。
15. 与 AgentHub 分工：ai-dev-os 立项；AgentHub 运行期 Harness。

## 6. 协议规则要点

- 不先 coding；BP 人工 review 门禁。
- ETHOS 冲突时 ETHOS 赢；变更需 session-log。
- Stage 0.5：origin 不得指向 OS repo。
- research-discipline：事实/推断/假设/开放问题分离。

## 7. 治理任务

1. fork 为 creative-studio-os 模板仓。
2. 每个新 repo 走精简 WIZARD（8 阶段 fast-path）。
3. lab 根 ETHOS.md + 中文本地化 START-HERE。
4. 建 docs/registry/ 索引 MCP 与 skill pack。
5. CI 移植结构校验到各 repo。
6. 重大决策强制 multi-ai-review。
7. 每 Sprint /daily-standup + /verify-build-works 文档化。
8. 发布前 release-check + legal-compliance-agent。
9. protocol yaml 引用 ETHOS 原则 ID。
10. v0.5 plugin manifest 跟踪上游。

## 8. 风险

- SaaS 中心偏见；game/content 需自定义 stack-pack。
- Wizard 过长；9 repo 全走完整 Wizard 会拖慢。
- 无原生 Gate GUI；不如 AgentHub 可视化。
- Registry 维护成本。
- 葡萄牙语默认需改或接受混合。

## 9. 结论

**新 repo 从 0 到 1 的最佳操作系统模板**：WIZARD + ETHOS + 文档分层 + red-team + registry + vibe-coder 安全层。与 AgentHub（运行期）、solo-playbook（商业验证）、unicorn-book（宪法）组成完整 stack。
