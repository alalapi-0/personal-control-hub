# 09 open-computer-use 参考分析报告

## 1. 基本信息

| 项 | 内容 |
|---|---|
| **名称** | Open Computer Use / Coasty |
| **URL** | https://github.com/coasty-ai/open-computer-use |
| **本地路径** | `reference_lab/ai_native_company/open-computer-use/` |
| **类型** | Next.js + FastAPI + Supabase + VM 多 Agent + Electron |
| **License** | Apache 2.0 |
| **MCP** | `@coasty/mcp`（24 tools + 2 prompts，4 模式审批） |
| **Clone 状态** | ✅ 成功 |

## 2. 真正解决的问题

给 AI **真实计算机控制能力**（浏览器、终端、桌面、文件），通过 Planner 分解任务 → 专用 Agent 顺序执行；云 VM 或本地 Electron 双路径；MCP 统一出口；4 级审批模式与 post-deploy 安全测试降低失控风险。

## 3. 关键目录表

| 路径 | 用途 |
|------|------|
| `app/`、`backend/app/` | 前端与 FastAPI multi_agent_executor |
| `electron/` | 本地 overlay、local-executor |
| `mcp/` | MCP server 包 |
| `CLAUDE.md` | 全栈架构、Agent 类型 |
| `tests/post_deploy/` | 部署后安全与 E2E |
| `scripts/deploy_readiness.py` | 发布就绪检查 |

## 4. 可迁移机制

### 4.1 工作流编排

- Planner → browser/terminal/desktop 顺序执行；SSE 流式回前端。

### 4.2 Agent 角色分工

- 四类 Agent + Electron local-executor 50+ 命令。

### 4.3 协议/规则

- CLAUDE.md 作架构真相；MCP 审批 4 模式；AGENTS.md 应写清禁止未审批 destructive shell。

### 4.4 Skills/Commands

- 能力在 backend tools + MCP tools；MCP 网关 repo 应维护工具能力表。

### 4.5 Hooks/CI

- Vitest + post_deploy pytest；security-release-gate 可抄 authz/webhook/IDOR 主题。

### 4.6 模板/脚手架

- `.env.oss.example` 一键 OSS。

### 4.7 多仓库治理

- computer-use 能力集中在 1 个 MCP 服务仓，业务仓只消费。

## 5. 启发

1. MCP 作为统一电脑接口——维护能力矩阵（对照 mcp/README.md）。
2. 4 模式审批写入 `repo_protocol_standard.yaml` 的 `automation_approval_levels`。
3. tests/post_deploy/ 是 security-gate 用例库种子。
4. Planner 串行子任务适合多平台发布流水线。
5. WebSocket ws-bridge 用于 dashboard 实时任务状态。
6. 记录 per-round LLM/ASR cost 到 PROJECT_STATE.md。
7. agent_gate.py 设 gate security-posture 跑 post_deploy 子集。
8. 本地数据仓禁止连接 live VM。
9. Playwright 与 MCP 分工：浏览器 vs 系统级。
10. destructive 操作前必须 human_approve 标记。
11. 长 pipeline 需显式 DAG 设计。
12. 单变量 LAB_API_KEY 降低新人成本。
13. macOS 权限要求写入 AGENTS.md。
14. 与 OneManCompany task tree 交叉参考多 Agent 树。
15. 工具层与 TTS/小说内容流水线解耦。

## 6. 协议规则要点

- MCP 24 工具需显式审批策略。
- 多 Agent 顺序执行；上下文用前序摘要传递。
- post_deploy 为发布门槛。
- Electron 需 Screen Recording + Accessibility 权限。

## 7. 治理任务

1. 新建或强化 MCP 集中仓，文档化工具与审批级别。
2. 从 post_deploy 选 10 个测试主题写入 security-gate v1 checklist。
3. protocol yaml 增加 computer_automation 节。
4. 多平台发布 repo 实现 Planner 式步骤 JSON。
5. agent_gate.py --profile oss 检查 .env.example。
6. 本地数据仓仅 local profile。
7. 审计 multi_agent_executor 并行限制。
8. 禁止 computer-use Agent 直接碰 TTS 原始音轨。
9. 记录 Electron vs 无头 CI 差异。
10. 自动化发帖遵守平台 ToS。

## 8. 风险

- 攻击面大：桌面/终端控制 prompt 注入后果严重。
- 双栈维护成本高。
- 云成本与合规风险。
- 与业务流水线重叠风险。

## 9. 结论

**浏览器/桌面/终端自动化 + MCP 网关 + 安全 post-deploy 测试** 的首选参考。最适合多平台发布、Playwright、MCP、security-release-gate；与小说/TTS 内容流水线保持工具层解耦。
