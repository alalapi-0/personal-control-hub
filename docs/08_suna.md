# 08 suna 参考分析报告

## 1. 基本信息

| 项 | 内容 |
|---|---|
| **名称** | Kortix（原 Suna） |
| **URL** | https://github.com/kortix-ai/suna |
| **本地路径** | `reference_lab/ai_native_company/suna/` |
| **类型** | 全栈 monorepo：Next.js + Bun API + CLI + Desktop + 云沙箱 |
| **宣言** | 「公司 = 一个 git 仓库」；`kortix.toml` + `.kortix/opencode/` |
| **Clone 状态** | ✅ 成功 |

## 2. 真正解决的问题

构建 **「AI 公司操作系统」**：Session = 隔离云沙箱 + 独立分支；Change Request 作为合并 main 的唯一受控路径；kortix.toml 统一 triggers、secrets、sandbox；Skills/Agents 即代码；强调可本地端到端验证。

## 3. 关键目录表

| 路径 | 用途 |
|------|------|
| `kortix.toml` | 项目 manifest |
| `.kortix/opencode/` | OpenCode agents/skills/commands/tools |
| `apps/web/`、`apps/api/`、`apps/cli/` | Web/API/CLI |
| `packages/starter/templates/` | 新项目模板 |
| `tests/e2e/end-to-end.md` | 黄金路径单一真相 |
| `tests/security-audit/` | 26+ cloud 扫描 |
| `AGENTS.md` | 强制 E2E 验证 |
| `.deepsec/AGENTS.md` | 安全扫描工作区 |

## 4. 可迁移机制

### 4.1 工作流编排

- kortix.toml triggers → 新 session + prompt；PR bot 双 webhook；session 内 OpenCode 执行。

### 4.2 Agent 角色分工

- opencode/agents/*.md；pr-bot 专责；平台 IAM 角色。

### 4.3 协议/规则

- kortix.toml vs opencode.jsonc **严格分界**；CR 必开才能上 main。

### 4.4 Skills/Commands

- 390+ opencode skills；按需加载 kortix-system skill。

### 4.5 Hooks/CI

- Gate5 脚本族；security-audit 扫描；E2E 为单一真相。

### 4.6 模板/脚手架

- `kortix init` → kortix.toml + `.kortix/` + git。

### 4.7 多仓库治理

- 单 repo = 单公司；你需 **meta-dashboard** 聚合各仓 PROJECT_STATE.md。

## 5. 启发

1. Session 隔离映射为：每 Round 用 git worktree 或分支，失败可弃。
2. CR 门控 = Round 完成 + human review；禁止 agent 直推 main。
3. kortix.toml 的 on_boot 启发 devcontainer 一键起栈。
4. AGENTS.md 要求跑 session-smoke——治理 Round 跑 check_repo.py。
5. Secrets 运行时注入、不进 prompt。
6. tests/e2e/end-to-end.md 的流程 ID 移植为各 repo 的 docs/e2e_flows.md。
7. PR bot webhook 用于出版 repo 触发发布草稿 Agent。
8. security-audit 是 security-release-gate 检查清单来源。
9. Connectors 思路：集中 MCP 注册表在 dashboard repo。
10. protocol yaml 增加 protocol_version 字段。
11. 定义治理 Round 与功能 Round 验证分级。
12. 默认本地 docker compose profile，云依赖可选。
13. agent_gate.py：`gate local` = health + check_repo。
14. 勿全量复制 390 skills。
15. 你应成为多 repo 间的标准作者（repo_protocol_standard.yaml）。

## 6. 协议规则要点

- kortix.toml/.kortix/ = 平台；opencode = Agent。
- session 分支进 main 必须走 Change Request。
- AGENTS.md 要求真实 API + 真沙箱验证。
- Secrets 不进 prompt。

## 7. 治理任务

1. 各 repo 引入双配置：平台配置 + AGENTS.md。
2. 写 change_request_policy.md。
3. 摘 5 条 GOLD 路径落到业务 repo。
4. dashboard 聚合多 repo Round 状态。
5. security-audit 映射为 security-gate checklist。
6. 统一 secret 命名与 never_commit 对齐。
7. OpenCode/Cursor 建最小 agents 集（≤5）。
8. 定义 session_isolation 与 merge_gate 在 protocol yaml。
9. 记录云依赖可选策略。
10. 抽象 CR/E2E ID 思想，非整体搬迁 Kortix 栈。

## 8. 风险

- 体量巨大；单人维护成本高。
- 云绑定 Daytona API；完全离线需大量剥离。
- 验证耗时长。
- Kortix Cloud 与 CLI 生态锁定。

## 9. 结论

**「公司即仓库 + 会话隔离 + CR 合并 + 技能代码化」的最完整产品级参考**。适合 dashboard、多 Agent 并行、出版/开发类仓；对小说/TTS/像素资产应抽象思想（CR、双配置、E2E ID），而非整体搬迁。
