# 13 one-person-unicorn-book 参考分析报告

## 1. 基本信息

| 项 | 内容 |
|---|---|
| **名称** | 《一人独角兽》 |
| **URL** | https://github.com/easychen/one-person-unicorn-book |
| **在线** | https://opu.ft07.com |
| **本地路径** | `reference_lab/ai_native_company/one-person-unicorn-book/` |
| **类型** | mdBook 双语理论书（cn/、en/） |
| **License** | CC-BY-NC-SA（非商业） |
| **Clone 状态** | ✅ 成功 |

## 2. 真正解决的问题

在缺乏先例下用逻辑推演回答：瓶颈从时间迁移到注意力；如何管理概率性硅基员工（记忆、PDCA、资源配额）；如何治理自动化组织（宪法 + 护栏）；如何做资产池而非项目思维；企业健忘症 → Skill Library。

## 3. 关键目录表

| 路径 | 用途 |
|------|------|
| `cn/src/SUMMARY.md` | 全书目录（5 章 18 节） |
| `cn/src/chapter1/` | 首席智能官、人类领地 |
| `cn/src/chapter2/` | 生产力、Token vs Payroll |
| `cn/src/chapter3/` | 硅基员工生理学：记忆、PDCA、配额 |
| `cn/src/chapter4/` | 组织治理：宪法、护栏、Evals |
| `cn/src/chapter5/` | 资产架构：资产池、应用矩阵 |
| `.github/workflows/deploy.yml` | mdbook 自动发布 |

## 4. 可迁移机制

### 4.1 工作流编排

- 分形 PDCA：Agent/团队/公司多层循环。

### 4.2 Agent 角色分工

- 人类保留愿景、品味、执行力；其余交给 Agent（首席智能官模型）。

### 4.3 协议/规则

- 宪法式 AI + 输入/输出护栏双轨；深度防御瑞士奶酪模型。

### 4.4 Skills/Commands

- 认知资产 / Skill Library：Do→Check→Act→Plan 转 handle_*.skill。

### 4.5 Hooks/CI

- 护栏层 = 确定性规则引擎、schema 校验、PII 脱敏（工程层需自建）。

### 4.6 模板/脚手架

- 宪法原则示例：绝对诚实、极致简约、主动帮助。

### 4.7 多仓库治理

- 资产池三分法：硬资产 + 软资产 + 认知资产；9 repo 共享支付/域名/API 预算。

## 5. 启发

1. 「文化即代码」：出版调性、翻译风格写入 constitution 原则。
2. 护栏一票否决：TTS/视频发布用输出端 schema + 敏感词拦截。
3. Token vs Payroll：API 配额是硬资产——dashboard cost-watchdog。
4. 记忆三层：session-log=永久；.knowledge=长期；AGENTS.md=短期工作面。
5. KPI → Evals：translation QA、TTS 质量、novel 连贯性用 LLM-as-Judge。
6. 应用矩阵：novel+translation+game+video 是资产池上的调用组合。
7. 失败案例自动知识萃取对齐 AgentHub postmortem。
8. security-release-gate 实现 4.2 节护栏。
9. 资源配额 API Key 绑定预算/频率/报警。
10. 批判性阅读：逻辑推演需 red-team 验证。
11. NC 许可限制直接商用书中正文。
12. 与 AgentHub 配对：书讲治理哲学，AgentHub 给 Harness。
13. protocol yaml 增加 asset_pool 节。
14. 各 repo 写 3–5 条宪法原则 per Agent 角色。
15. 每季度资产池盘点可复用 skill/channel。

## 6. 协议规则要点

- 宪法 + 输入护栏 + 输出护栏 = 深度防御。
- 输入：防 prompt injection、PII 脱敏、业务边界拒绝。
- 输出：JSON schema、事实 API 核对、敏感词拦截。
- 资源配额：API Key 非敞开水龙头。
- CC-BY-NC-SA：商业产品直接复制正文受限。

## 7. 治理任务

1. 为每个 Agent 角色写 3–5 条宪法原则。
2. security-gate 部署输入/输出护栏。
3. 高质量产出 → Eval → 更新 Skill Library。
4. 每周审查 API 配额/Token burn。
5. 每季度资产池盘点。
6. dashboard 实现硬资产健康度视图。
7. 各 repo symlink 共享认知 skill（translation-glossary 等）。
8. 书中护栏思想写入 repo_protocol_standard.yaml 的 guardrails 节。
9. 禁止把 NC 正文整段复制进商业 repo。
10. 与 solo-playbook 商业验证层配对。

## 8. 风险

- 理论超前实践；全自动化商业闭环仍在特定场景。
- 非商业许可限制。
- 无代码参考；需 AgentHub/gh-aw 补工程层。
- 概率性 Agent 必须 Hook/Gate 补强。

## 9. 结论

**五库中最契合长期架构的「为什么」**：多 repo 多 Agent 公司应围绕资产池 + 宪法/护栏 + 记忆/Skill 库 + Evals 设计。Security Gate 实现护栏；Dashboard 实现资源配额；各业务 repo 向认知资产层沉淀 Skill。
