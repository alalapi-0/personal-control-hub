# MCP 基础设施战略

Round 0.5 文档。定义 personal-control-hub 如何在 Cursor 工作区中登记、治理和默认启动 MCP 候选，而不在本轮真实安装、真实调用外部 MCP 服务或写入密钥。

## 1. 文档目的与范围

本文档说明 MCP（Model Context Protocol）在 personal-control-hub 中的战略定位、登记方式、审批边界和交付物。适用范围为本仓库及 Cursor 宿主环境；不包含外部业务仓库的 MCP 安装。

## 2. 战略定位

personal-control-hub 是治理层与控制台，不是 MCP 运行时。MCP 能力由 Cursor 作为宿主加载；本仓库负责能力矩阵、审批策略、路线图和审计，确保 Agent 不绕过 policy 自行启用高风险工具。

## 3. MCP 在本项目中的角色

- **登记层**：`data/mcp/mcp_capability_registry.yaml` 记录候选 MCP 及允许/禁止范围。
- **策略层**：`data/mcp/mcp_approval_policy.yaml` 定义 L0-L3。
- **路线层**：`data/mcp/mcp_integration_roadmap.yaml` 规划 Round 0.5 及后续轮次。
- **执行层（未来）**：经用户确认后，由 Cursor 加载配置；Codex 须遵守同一策略，不得绕过。

## 4. 宿主环境：Cursor

Cursor 是日常主力与 MCP 宿主。配置示例见 `.cursor/mcp.example.json` 与 `docs/13_cursor_mcp_workspace_setup.md`。用户已有 `.cursor/mcp.json` 时，Agent 不得覆盖，仅报告差异建议。

## 5. 能力矩阵概览

当前登记六个 MCP，全部在本项目治理中标记为 `enabled_in_project: true`。这里的 default start 表示可被 Cursor 工作区启用，不表示 Agent 可免确认执行 L2/L3 动作：

| id | category | approval | 默认可启动 |
|---|---|---|---|
| context7 | documentation_context | L0 | 是 |
| filesystem | local_files | L1 | 是 |
| github | external_vcs | L2 | 是 |
| chrome-devtools | browser_debug | L2 | 是 |
| stitch | ui_generation | L2 | 是 |
| playwright | browser_automation | L3 | 是，动作受限 |

权威数据源：`data/mcp/mcp_capability_registry.yaml`。

## 6. 六个 MCP 总览

### context7

公开库文档上下文，默认 L0，优先在 Phase 1 试点。

### filesystem

受控路径内读写本仓库与扫描输出，L1，须与白名单及外部项目只读协议一致。

### github

Issue/PR/checks 只读查询，L2；写操作归类 L3，默认禁止。

### chrome-devtools

页面与性能诊断，L2，用于浏览器测试复盘。

### stitch

UI 探索草案，L2，不直接写外部业务仓库。

### playwright

E2E 自动化，L3，可登记为默认可启动；生产账号登录、支付、发布、破坏性 UI 操作和无人值守点击链仍禁止或需 CEO 显式批准。

## 7. 审批模型摘要

- **L0**：只读上下文，无需确认。
- **L1**：本仓库低风险写入，记日志。
- **L2**：外部系统/shell 低风险，须人工确认或白名单。
- **L3**：高风险写操作，CEO 显式批准，当前默认禁止。

详见 `docs/12_external_tool_approval_model.md`。

## 8. 数据登记与治理文件

| 文件 | 作用 |
|---|---|
| `data/mcp/mcp_capability_registry.yaml` | 能力矩阵 |
| `data/mcp/mcp_approval_policy.yaml` | L0-L3 策略 |
| `data/mcp/mcp_integration_roadmap.yaml` | 集成路线图 |
| `data/mcp/mcp_servers.example.yaml` | 无 token 示例 |
| `data/integrations/integration_targets.yaml` | 与 Feishu/GitHub 等目标对齐 |
| `data/scheduler/scheduled_tasks.yaml` | MCP 审计与扫描准备任务 |

## 9. 启用策略与优先级

**默认可启动（default start）**：chrome-devtools、context7、filesystem、github、playwright、stitch 均可作为 Cursor 工作区启动候选。

**操作边界**：L0/L1 可默认可用；L2 仍须人工确认范围和数据去向；L3 仍默认禁止具体高风险动作，需 CEO 显式批准。

新增 MCP 必须同时更新 registry、policy、roadmap 三文件。

## 10. 安全与合规边界

Round 0.5 禁止：

- 自行安装 MCP 包。
- 在仓库写入真实 token、secret、cookie、API key。
- 真实调用外部 MCP API。
- 修改外部项目或自动 push。
- 读取 `.env`、私钥、钥匙串。

token 仅通过环境变量注入，示例配置使用 `${VAR_NAME}` 占位。

## 11. Round 0.5 交付物

- 本文档及 `docs/12`、`docs/13`。
- `data/mcp/` 四个 YAML。
- `prompts/mcp_audit_prompt.md`、`prompts/cursor_mcp_usage_prompt.md`。
- `.cursor/README.md`、`.cursor/mcp.example.json`（若不存在则创建）。
- 扩展 `check_repo.py`、`agent_policy.yaml`、`AGENTS.md`。
- 可选 `hub.py mcp list|policy` 只读 CLI。

## 12. 后续 MCP 轮次路线图

| Round | 名称 | 重点 MCP |
|---|---|---|
| 0.5 | Workspace Infrastructure | 全部登记，default start with gates |
| 6 | GitHub Read Pilot | github |
| 6.5 | Context7 Pilot | context7 |
| 7 | Filesystem Whitelist | filesystem |
| 7.5 | Stitch Exploration | stitch |
| 8 | Chrome DevTools Debug | chrome-devtools |
| 8.5 | Browser Test Review | chrome-devtools, playwright |
| 9 | Playwright E2E | playwright (L3) |
| 12 | Production Readiness | 全量复审 |

完整条目见 `data/mcp/mcp_integration_roadmap.yaml`。

## 13. 与 Codex / Cursor 分工

- **Cursor**：MCP 宿主，日常推进，遵守 L0-L3。
- **Codex**：关键执行，不得绕过 MCP policy；外部工具调用同样记日志。
- **personal-control-hub**：登记、审计、调度准备，不替代 Cursor 加载 MCP。

## 14. 审计与日志

- 调度任务 `SCHED-MCP-REGISTRY-AUDIT` 定期对照 registry 与 policy。
- L1+ 操作写入 `data/logs/automation_log.jsonl`。
- 使用 `prompts/mcp_audit_prompt.md` 生成审计草案。

## 15. 验收标准与下一步

**验收**：

- 六个 MCP 登记完整，字段齐全，`enabled_in_project` 均为 true。
- default start 已明确不等于 unlimited access；L2/L3 操作仍受审批策略约束。
- `python3 scripts/check_repo.py` 与 `bootstrap.py --dry-run` 通过。
- 仓库内无真实 token。
- 未真实调用外部 MCP。

**下一步（Round 0.7+）**：

- Round 0.7 建立运行环境检查（`scripts/check_environment.py`）与 auto advance runner；MCP 状态仍为 manual_check_required。
- 用户验收 Round 0.5/0.6/0.7。
- 按需将 `mcp.example.json` 合并到用户级配置（人工），真实启用状态以 Cursor Workspace MCP Servers 为准。
- 启动 GitHub/Context7 只读试点前更新 `round_state.yaml` 与 integration_targets。
