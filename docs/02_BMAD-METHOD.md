# 02 BMAD-METHOD 参考分析报告

## 1. 基本信息

| 项 | 内容 |
|---|---|
| **名称** | BMad Method (BMM) |
| **URL** | https://github.com/bmad-code-org/BMAD-METHOD |
| **本地路径** | `reference_lab/ai_native_company/BMAD-METHOD/` |
| **类型** | Node CLI 安装器 + Skills/Workflow 模块生态 + 文档站 |
| **License** | MIT |
| **版本** | `package.json` 6.8.0 |
| **Clone 状态** | ✅ 成功 |

## 2. 真正解决的问题

**规模自适应的 AI 敏捷交付**：从 bugfix 到企业系统，用「专家 Agent 人格 + 结构化 workflow」引导人类思考，而非 AI 替你想。解决单人/小团队在用 AI 写软件时 **缺少产品—架构—实现—测试的阶段制品与导航**。

V6 强调：Skills 架构、非交互安装、Web Bundles（Gemini/ChatGPT 做规划、IDE 做实现）、模块扩展（BMB/TEA/BMGD/CIS）。

## 3. 关键目录表

| 路径 | 用途 |
|------|------|
| `tools/installer/` | `npx bmad-method install` 核心 |
| `src/core-skills/` | 核心 skills（bmad-help、party-mode、review） |
| `src/bmm-skills/` | BMM 模块：Agent roster、workflow skills |
| `web-bundles/` | Gemini/ChatGPT 可导入 planning skills |
| `docs/reference/agents.md` | 默认 Agent 与 trigger 表 |
| `website/` | Astro 文档站 |
| `test/` | 安装通道、skill 校验、对抗性审查 |
| `.github/workflows/quality.yaml` | 与 `npm run quality` 对齐 |
| `AGENTS.md` | Conventional Commits、push 前 quality |
| `bmad-modules.yaml` | 模块清单 |

## 4. 可迁移机制

### 4.1 工作流编排

- **34+ workflows**（BMM），通过 Agent menu **trigger 代码** 启动（PRD、DS、CA 等）。
- **`bmad-help` skill**：读 `_bmad/_config/bmad-help.csv` + 产物路径模糊匹配，推荐下一步。
- **Party Mode**：多 Agent 人格同屏协作。

### 4.2 Agent 角色分工

| Agent | 典型 triggers |
|-------|----------------|
| Analyst (Mary) | BP, MR, DR, TR, CB |
| PM (John) | PRD, CE, IR, CC |
| Architect (Winston) | CA, IR |
| Developer (Amelia) | DS, QD, QA, CR, SP |
| UX (Sally) | CU |
| Tech Writer (Paige) | DP, WD, MG |

安装后落在项目 `_bmad/` 下。

### 4.3 协议/规则

- Conventional Commits；push 前 `npm ci && npm run quality`。
- `npm run validate:skills`；required=true 的 catalog 行是硬门禁。
- 每个 skill 建议 **fresh context window**。

### 4.4 Skills/Commands

- V6 以 Skills 为一等公民；Web bundles 做订阅制 Web LLM 规划。
- 非交互：`npx bmad-method install --modules bmm --tools claude-code --yes --set ...`。

### 4.5 Hooks/CI

- quality.yaml：Prettier、ESLint、markdownlint、validate:skills。
- husky + lint-staged；对抗性审查测试。

### 4.6 模板/脚手架

- Installer 创建 `_bmad/` 与 `customize.toml` per agent。
- 外部模块：Game Dev、Test Architect、Creative Intelligence。

### 4.7 多仓库治理

- 按项目 `npx bmad-method install`；Web bundle + IDE 拆分控制 token 成本。
- `module-help.csv` / file-refs CSV 用于跨 workflow 引用完整性校验。

## 5. 启发

1. 在 dashboard repo 维护 **`bmad-help.csv` 等价物**：列 lab 各 repo、Round、前置 skill、产出路径。
2. 将 novel/translation migration plan 映射为 **phase 1-analysis → 2-planning → 4-implementation**。
3. **Web bundle 策略**：ChatGPT/Gemini 做长篇大纲与 PRD，Cursor 只做 repo 内实现。
4. 为 audiobook TTS 定义虚拟 Agent「Dataset Steward」（custom agent 思路）。
5. `security-release-gate` 对接 TEA 模块：风险驱动测试与发布门禁。
6. 用 **Implementation Readiness (IR)** 作为多 repo 合并前检查名。
7. `repo_protocol_standard.yaml` 增加 **`required: true` 字段** 对齐 bmad-help 硬门禁。
8. **Party Mode** 用于「出版 + 翻译 + 法务」多角色评审，需限制 token。
9. `scripts/agent_gate.py` 可解析 catalog 的 **outputs 模式** 检测 Round 是否完成。
10. 像素/视频 repo 可装 BMGD 模块作参考，勿引入 Unity 全流程。
11. 复制 **validate:skills** 思路：对自有 `skills/*/SKILL.md` frontmatter 做 CI。
12. `AGENTS.md` 写清：**fresh context per Round**。
13. Dashboard 读取各 repo `planning-artifacts/` 与 `implementation-artifacts/`。
14. 非交互 install 适合 CI 初始化新 repo 骨架。
15. 只 pin 官方 bmm + 自研 custom module，避免模块版本漂移。

## 6. 协议规则要点

- Agent = Skill；用 **menu-code** 调用。
- Workflow trigger 对话式 trigger 不要省略参数。
- 规划产物路径由 install 时变量解析，不要手写散落路径。
- `communication_language` / `document_output_language` 应设为中文。

## 7. 治理任务

1. 定义 lab 是否采用 `_bmad/` 或仅吸收 CSV help 模式。
2. 为 8+ 子 repo 建统一 **phase 命名** 与产物目录。
3. 选 1 个工具型 repo 完整 `npx bmad-method install` pilot。
4. 将 `ROADMAP_40_ROUNDS` 条目录入 help catalog。
5. Web bundle 仅用于产品/内容规划，禁止直接生成可提交代码。
6. 建立 custom module「Lab Governance」：Round 规则、数据禁提交。
7. 每季度 bmad-method 升级策略（@next vs stable）。
8. 与 Spec Kit 分工：BMM 管产品敏捷；Spec Kit 管单 feature 技术 spec。
9. 记录 adversarial-review 测试用例供 security-gate repo。
10. 文档输出语言统一中文。

## 8. 风险

- 安装器复杂度高，多模块/多 IDE 易路径冲突。
- 34+ workflow 对 solo founder **认知负载大**。
- Web bundle 与 IDE 双轨可能造成制品不同步。
- Node 20+ / Python 3.10+ / uv 前置要求。
- 游戏/企业模块易过度安装。

## 9. 结论

BMAD 是 lab 的 **「产品—敏捷—多角色规划 OS」** 最佳参考：统一 phase、help 导航与 Web+IDE 成本拆分。与 Spec Kit（技术 SDD）和 agent-skills（工程纪律）组成三层：BMM 定「做什么」，Spec Kit 定「这一 feature 怎么落地」，agent-skills 定「怎么写得好」。建议 dashboard/help catalog 先落地，再选单 repo 完整安装。
