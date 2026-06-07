# 00 个人 AI 公司 OS 综合迁移方案

> 基于 15 个参考仓库深度分析，面向 `/Volumes/AI_WORK_SSD/lab` 一人 + 多 Agent + 多 repo 场景。  
> 详细单库分析见 [01–15 报告](./README.md)。

---

## 1. 愿景与边界

### 1.1 愿景

构建 **「首席智能官 + 硅基员工矩阵 + 硬护栏」** 的个人 AI 公司 OS：

- **你（CEO）**：愿景、品味、Gate 审批、资产池配置
- **Meta 层（1–2 个 repo）**：Dashboard 编排、Security Gate、MCP 网关、治理文档
- **业务层（N 个 repo）**：小说、翻译、像素游戏、动漫视频、出版、TTS 等，各自 Round 推进
- **参考层（只读）**：`reference_lab/` 吸收思想，禁止复制进 `src/`

### 1.2 非目标

- 不合并为单一 Kortix/OMC monorepo
- 不默认同时启用 Spec Kit + BMAD + agent-skills 三套全流程（会流程疲劳）
- 不在治理 Round 跑真实模型/API/训练
- 不提交 reference_lab 大型 clone（见 README .gitignore）

---

## 2. 分层架构（15 库映射）

```
┌─────────────────────────────────────────────────────────────┐
│  CEO（人类）— Gate 审批 / 资产池 / 商业决策（solo-playbook）   │
├─────────────────────────────────────────────────────────────┤
│  Meta OS 层                                                  │
│  • Dashboard（OneManCompany 任务树 + AgentHub Kanban 思想）   │
│  • security-release-gate（gh-aw + agent-skills /ship + Hooks） │
│  • MCP 网关（open-computer-use 审批分级）                      │
│  • governance docs（unicorn-book 宪法/护栏 + ai-dev-os ETHOS）│
├─────────────────────────────────────────────────────────────┤
│  业务 Repo 层（各管线独立 Round）                              │
│  • 立项：ai-dev-os WIZARD fast-path                          │
│  • 交付：spec-kit lean spec/plan/tasks（代码型）              │
│  • 规划：BMAD help catalog（产品型，可选）                     │
│  • 执行：AGENTS.md + 精选 subagents + domain skills          │
├─────────────────────────────────────────────────────────────┤
│  参考 & 索引层（不执行）                                       │
│  • awesome-claude-code / awesome-opc / subagents catalog     │
│  • reference_lab/ai_native_company/（本地 clone）              │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 核心文件与目录标准（全 lab 推广）

以 `reference_lab/07_audio_asr_tts/` 为金标准，推广到各业务 repo：

| 文件/目录 | 职责 | 参考来源 |
|-----------|------|---------|
| `AGENTS.md` | 阅读顺序、禁止事项、Round 规则、intent→skill | 07_audio + ai-dev-os CLAUDE.md |
| `docs/governance/repo_protocol_standard.yaml` | 宪法级原则、数据保护、Round、guardrails | 07_audio + unicorn-book 4.2 |
| `docs/governance/update_log.md` | 永久记忆（审计日志） | ai-dev-os session-log |
| `docs/governance/gate_checklist.yaml` | G0–G6 或精简 G0–G4 | AgentHub gate-keeper |
| `docs/ROADMAP_*_ROUNDS.md` | 分阶段交付 | 07_audio 40 Rounds |
| `PROJECT_STATE.md` | 当前 Round、风险、任务树链接 | OneManCompany + suna |
| `scripts/check_repo.py` | 结构/协议/禁提交校验 | 07_audio 已有 |
| `scripts/agent_gate.py` | **新建**：pre-edit / pre-pr / gate-record | gh-aw Makefile + AgentHub |
| `specs/<round-id>/` | spec.md, plan.md, tasks.md（代码/工具 repo） | spec-kit lean |
| `.tasks/sprint-N/` 或 `.knowledge/` | 任务 Markdown（Dashboard 可读） | AgentHub |
| `.claude/agents/` | 2–5 个领域 agent（白名单） | subagents + contains-studio |
| `docs/agent_skills/` | 领域 SKILL.md（TTS、翻译术语等） | agent-skills anatomy |

### 3.1 `repo_protocol_standard.yaml` 建议扩展节

在现有 07_audio 版本基础上增加：

```yaml
protocol_version: 1
lab_os_version: "2026.06"

round_states:  # OneManCompany
  - pending
  - processing
  - completed      # Agent 自称完成
  - accepted       # 人类验收
  - finished

guardrails:  # unicorn-book 4.2
  constitution_principles:
    - 数据与密钥永不进 git
    - 参考代码不复制进 src
    - 治理轮不调用生产 API
  output_schema_required:
    - security-release-gate
    - multi-platform-publish

automation_approval_levels:  # open-computer-use
  L0: 只读查询
  L1: 写 repo 内文件（需 check_repo 绿）
  L2: shell/网络（需 human_approve）
  L3: 发布/支付/删数据（CEO 显式批准）

asset_pool:  # unicorn-book 5.1
  hard: [stripe, domains, api_quotas]
  soft: [brand, communities]
  cognitive: [skill_library_path: docs/agent_skills/]

agent_tool_policy:  # contains-studio
  default_allowed: [Read, Grep, Glob]
  require_approval: [Bash, Write, WebFetch]

ci_agent_workflows:  # gh-aw
  allowed: [round-audit, dependency-report, pr-review-bot]
```

---

## 4. `scripts/agent_gate.py` 设计（统一门禁）

**目标**：全 lab 单一 CLI，替代分散的 check_repo + 未来 Hook 前置检查。

### 4.1 子命令

| 命令 | 时机 | 检查项（参考） |
|------|------|----------------|
| `gate pre-round` | Round 开始前 | AGENTS.md 存在、protocol yaml 可读、Round 编号合法 |
| `gate pre-edit` | Agent 大改前 | check_repo.py、git clean、无 .env staged |
| `gate pre-pr` | 开 PR 前 | pytest 子集、check_repo、文档同 PR（AgentHub doc-governance） |
| `gate release` | 发布前 | G2 LLM 信任边界清单、/ship 三报告路径存在 |
| `gate record --gate G2 --pass` | Gate 通过记录 | 写 update_log.md |

### 4.2 实现路径

1. **Phase 0**：包装现有 `check_repo.py`（复制逻辑或 subprocess）
2. **Phase 1**：移植 AgentHub G0–G4 YAML criteria
3. **Phase 2**：解析 `specs/*/tasks.md` 完成度（spec-kit）
4. **Phase 3**：可选调用 gh-aw compile --check（GitHub repo）

### 4.3 与各 repo 集成

- 各 `AGENTS.md` Round 结束前：`python scripts/agent_gate.py pre-pr`
- security-release-gate：额外 `gate release`
- Dashboard：读取 gate record 日志聚合

---

## 5. 业务线 × 机制矩阵

| 业务 repo | 主编排 | 专家 Agent | 门禁重点 | 优先参考库 |
|-----------|--------|-----------|---------|-----------|
| 02_novel_generation | Round + spec lean | ai-writing-auditor, continuity | 大纲 Gate、章节 accepted | spec-kit, contains-studio |
| 03_novel_translation | Round + glossary skill | localization, qa | 术语一致性 Eval | agent-skills, BMAD |
| pixel_game_assets | BMAD BMGD（可选） | game-developer | 资产体积、license | BMAD, agent-skills perf |
| anime_video | Planner 步骤 JSON | video pipeline 自建 | 输出 schema、版权 | open-computer-use, gh-aw |
| multi_platform_publish | MCP + Playwright | deployment-engineer | L2 审批、ToS | open-computer-use, subagents |
| 07_audio_asr_tts | 40 Rounds（已有） | Dataset Steward | 数据 never_commit、质量 gate | 07 自身 + agent-skills |
| repo_dashboard | OMC 任务树轻量 | context-manager | 只读聚合 | OneManCompany, AgentHub |
| security-release-gate | gh-aw + /ship | security-engineer | forbidden-commands | gh-aw, AgentHub, agent-skills |
| prompt_playwright_mcp | MCP 集中仓 | browser-testing | L0–L3 审批 | open-computer-use, awesome-cc |

---

## 6. 流程分工（避免三重流程叠加）

| 阶段 | 主机制 | 禁用/可选 |
|------|--------|----------|
| 商业验证 | solo-playbook /solo-analyze | — |
| 新 repo 立项 | ai-dev-os WIZARD fast-path（8 阶段） | 完整 17 阶段仅 flagship repo |
| 产品规划 | BMAD Web Bundle 或 bmad-help（可选） | 与 spec-kit 二选一作「主 spec」 |
| 单 feature / Round | spec-kit lean **或** Round md（内容型） | 不要两套同时默认 |
| 编码纪律 | agent-skills 精简子集（8 skills） | 不全量 23 |
| 发布 | /ship 三评审 + gate release | — |
| GitHub 自动化 | gh-aw 只读 workflow | 业务 repo 各 1–2 个 |
| 运行期 Harness | AgentHub Hook 模板（Cursor 适配版） | 不强制 Electron GUI |

---

## 7. 分阶段落地路线图

### Phase 0 — 文档与门禁（1–2 周，无重型依赖）

- [ ] 发布本分析目录 `docs/reference_analysis/ai_native_company/`
- [ ] 从 07_audio 复制并泛化 `repo_protocol_standard.yaml` 到 lab 模板
- [ ] 实现 `scripts/agent_gate.py` Phase 0（wrap check_repo）
- [ ] 写 `docs/governance/ETHOS.md`（摘 ai-dev-os 15 条 + 中文）
- [ ] `.gitignore` 排除 `reference_lab/ai_native_company/`

### Phase 1 — Meta 层（2–4 周）

- [ ] 创建或指定 **dashboard** reference_lab 子项目：PROJECT_STATE 聚合规范
- [ ] 创建 **security-release-gate** 骨架：gate_checklist.yaml + forbidden-commands 文档
- [ ] 精选 12 个 contains-studio/subagents persona → `docs/talent/manifest.yaml`
- [ ] 安装 agent-skills 精简 plugin + solo-founder-playbook plugin（Boss 机）

### Phase 2 — 业务 repo 对齐（4–8 周）

- [ ] 02_novel、03_translation 添加 AGENTS.md + protocol yaml + specs/ 试点
- [ ] 多平台发布 repo：MCP 能力矩阵 + L0–L3 审批
- [ ] gh-aw 试点 1 个 workflow（security-gate 或 lab 根若用 GitHub）
- [ ] 建立 `docs/registry/INDEX.md`（MCP、skills、外部 pack）

### Phase 3 — 自动化与 Evals（8+ 周）

- [ ] translation/novel/TTS 质量 Eval（unicorn-book 4.4）
- [ ] Dashboard 读 `.tasks/` 或 PROJECT_STATE（AgentHub FileWatcher 思想）
- [ ] studio-patterns.json 聚合跨 repo 复盘
- [ ] OneManCompany 式 task tree 可视化（可选，非像素 UI）

---

## 8. 记忆与资产池

| 层级 | 存储 | 内容 |
|------|------|------|
| 短期 | Cursor rules / 当前 session | 本 Round 上下文 |
| 永久 | `update_log.md`, session-log | 决策、Gate 记录、踩坑 |
| 长期 | `docs/agent_skills/`, `.knowledge/` | 翻译术语、TTS 切分策略、发布 SOP |
| 硬资产 | Dashboard 配置（不进 git） | API 配额、Stripe、域名 |
| 认知资产 | Skill Library symlink | 跨 repo 共享 SKILL.md |

**规则**：Do → Check → Act → Plan 高质量 Round 产出必须沉淀为 skill 或 postmortem（unicorn-book + AgentHub）。

---

## 9. 风险总表与缓解

| 风险 | 来源 | 缓解 |
|------|------|------|
| 流程疲劳 | 15 库叠加 | 上表「流程分工」严格单主机制 |
| Token/API  burn | 多 Agent 并行 | cost-watchdog + asset_pool 配额 |
| Prompt 注入 / 越权 | computer-use, subagents | L0–L3 + forbidden-commands + 最小 tools |
| 参考代码许可证 | 各 upstream | protocol forbidden copy；结构借鉴 |
| 无 git / 无 CI | lab 现状 | 先 agent_gate 本地；再 GitHub + gh-aw |
| 154 subagents 瘫痪 | subagents | 白名单 ≤5/repo |
| NC 书正文商用 | unicorn-book | 只迁移概念，不复制正文 |

---

## 10. 结论与下一步

15 库合奏的 **最小可行 OS** 是：

1. **理论**：unicorn-book（宪法/护栏/资产池）
2. **立项**：ai-dev-os（WIZARD + ETHOS）
3. **交付**：spec-kit lean + 07_audio Round 模型
4. **纪律**：agent-skills 精简 + /ship
5. **治理**：AgentHub Gate + gh-aw 只读 + **agent_gate.py**
6. **编排**：OneManCompany 任务树 + Dashboard 聚合
7. **商业**：solo-playbook + awesome-opc 索引
8. **专家**：subagents 白名单 + contains-studio 模板
9. **工具**：open-computer-use MCP 网关（发布/Playwright）
10. **索引**：awesome-claude-code catalog.csv（后期）

**立即行动**（下一轮治理）：使用 [99_next_governance_prompt.md](./99_next_governance_prompt.md) 执行 Phase 0 任务清单。

**禁止**：在本轮将 reference 代码复制进任何 `src/`；在未验收前启用 penetration-tester 或 live computer-use L3。

---

*生成日期：2026-06-03 | 分析 Agent：ai_native_company 研究任务*
