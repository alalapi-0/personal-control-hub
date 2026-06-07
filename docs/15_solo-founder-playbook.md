# 15 solo-founder-playbook 参考分析报告

## 1. 基本信息

| 项 | 内容 |
|---|---|
| **名称** | Solo Founder Playbook |
| **URL** | https://github.com/yayashuxue/solo-founder-playbook |
| **本地路径** | `reference_lab/ai_native_company/solo-founder-playbook/` |
| **类型** | 6 个 Claude/Codex startup skills + 101 Starter Story 访谈数据 |
| **License** | MIT |
| **数据管道** | starterstory-pipeline（URL→transcript→patterns.json） |
| **Clone 状态** | ✅ 成功 |

## 2. 真正解决的问题

创业建议脱离「AI vibes」→ 基于 101 个真实创始人访谈的数据模式：/solo-analyze、/solo-failures、/solo-growth、/solo-playbook、/solo-roast、/solo-patterns。

## 3. 关键目录表

| 路径 | 用途 |
|------|------|
| `README.md` | 安装、6 skills 表、数据摘要 |
| `.claude-plugin/marketplace.json` | Claude Code 插件市场 |
| `knowledge/patterns.json` | 80+ 模式 + 频率 |
| `knowledge/insights.md` | 模式详细描述 |
| `skills/solo-*/SKILL.md` | 6 个 data-driven skills |
| `skills/solo-failures/knowledge/failure-modes.md` | 50+ 失败模式 |
| `install.sh` / `install-codex.sh` | 本地安装 |

## 4. 可迁移机制

### 4.1 工作流编排

- 新 idea → analyze → playbook → roast；stalled → failures；有 MRR → growth。

### 4.2 Agent 角色分工

- 6 skills 均为 advisory；Boss 人类决策。

### 4.3 协议/规则

- 各 SKILL 必须先读 knowledge 再回答；禁止无数据臆测。

### 4.4 Skills/Commands

- Skill=指令框架；Knowledge=SSOT；输出格式固定（Classification/Strengths/Risks/Verdict）。

### 4.5 Hooks/CI

- 无；纯 advisory。

### 4.6 模板/脚手架

- Claude Plugin 分发；knowledge symlink 共享（5 skills 共享 patterns.json）。

### 4.7 多仓库治理

- 9 repo skills symlink 到 monorepo knowledge/；自建 studio-patterns.json 聚合跨 repo 复盘。

## 5. 启发

1. 74.3% 创始人用 AI 为核心工具——多 Agent 策略与数据一致。
2. Organic 3.5:1 paid——novel/anime 优先 SEO + 社区。
3. Top idea origin：个人问题 32/101——小说/翻译/game 源于自己痛点则数据支持。
4. Building without validation 11/101——新 repo 前必须 /solo-analyze。
5. Boss 每周 /solo-failures 自检 stealth 天数/预算/runway。
6. 重大计划前 /solo-roast。
7. patterns.json 频率 count 必须引用——启发 lab 自己的 metrics.json。
8. starterstory-pipeline 可对中文 indie 播客复用。
9. 与 awesome-list 互补：索引 vs 数据顾问。
10. vercel.app/try 零安装 UX 可嵌入 dashboard。
11. 打包 studio skills 为 plugin（novel-QA、translation-glossary）。
12. failure-modes 独立 knowledge 需注意同步策略。
13. 多失败后成功 15/101——9 repo 是小赌注矩阵。
14. 不能替代 AgentHub Gate 或 ai-dev-os WIZARD。
15. playbook 产出 sprint 目标，AgentHub 管执行。

## 6. 协议规则要点

- 必须先读 knowledge 再回答。
- solo-analyze：7 类 idea origin + 4 类 risk + 5 类 validation。
- solo-playbook：realistic timelines（validation 2–4 周，PMF 3–6 月）。
- patterns.json 频率必须引用。

## 7. 治理任务

1. 安装 plugin 到 Boss 工作流。
2. 新 repo/新产品 idea 前 /solo-analyze。
3. 每周 /solo-failures 自检。
4. 增长阶段 /solo-growth。
5. 具体目标 /solo-playbook（首 100 用户、首 TTS 包销售）。
6. 重大计划 /solo-roast。
7. 自建 knowledge/studio-patterns.json 聚合 lab 复盘。
8. 季度重跑或更新 patterns 数据。
9. 与 WIZARD Stage 1–3 配对：流程 vs 该不该做。
10. dashboard 显示 stealth 天数与 runway 指标。

## 8. 风险

- 数据源单一：仅 Starter Story 101 视频；偏英语 SaaS。
- 无工程治理层。
- 静态 JSON 需手动更新。
- game/content/anime 商业模式覆盖弱。
- 建议非执行——不触发 CI/Hook。

## 9. 结论

**商业决策层的数据化顾问**：轻量、可 plugin 安装。Boss 定期跑 failures/roast；新产品 analyze；增长 growth。与 ai-dev-os（立项流程）和 AgentHub（执行 Gate）配对使用。
