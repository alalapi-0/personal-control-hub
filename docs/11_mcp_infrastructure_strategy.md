# MCP 基础设施战略

Round 0.5 历史设计文档，已按当前 Phase 1 现实校正：登记候选不等于项目配置、运行时可用或动作授权。

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

当前登记六个 MCP，registry 中均为 `enabled_in_project: false`。受保护的项目配置当前只含 `filesystem`，实际运行时可用性未验证：

| id | category | approval | registry enabled |
|---|---|---|---|
| context7 | documentation_context | L0 | false |
| filesystem | local_files | L1 | false（但当前项目配置存在） |
| github | external_vcs | L2 | false |
| chrome-devtools | browser_debug | L2 | false |
| stitch | ui_generation | L2 | false |
| playwright | browser_automation | L3 | false |

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

E2E 自动化候选，L3，默认 disabled；生产账号登录、支付、发布、破坏性 UI 操作和无人值守点击链仍禁止或需显式批准。

## 7. 审批模型摘要

- **L0**：只读上下文风险级别；仍服从当前上级授权。
- **L1**：本仓库低风险写入；需要当前任务授权，获准记录时才记日志。
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

**默认状态**：六个 registry 候选均 disabled；`.cursor/mcp.example.json` 只是候选示例。

**四层边界**：登记、项目配置、运行时可用和动作授权分别判断；任何层都不能推出下一层。

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
| 0.5 | Workspace Infrastructure | 全部登记，默认 disabled |
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
- **Codex**：关键执行，不得绕过 MCP policy；外部工具动作与仓库日志写入分别需要当前授权。
- **personal-control-hub**：登记、审计、调度准备，不替代 Cursor 加载 MCP。

## 14. 审计与日志

- 调度任务 `SCHED-MCP-REGISTRY-AUDIT` 定期对照 registry 与 policy。
- L1+ 是风险分类；只有当前任务另行授权记录时才写 `data/logs/automation_log.jsonl`。
- 使用 `prompts/mcp_audit_prompt.md` 生成审计草案。

## 15. 验收标准与下一步

**验收**：

- 六个 MCP 登记完整，字段齐全，`enabled_in_project` 均为 false。
- 四层状态分离；L0-L3 不被解释为授权。
- `python3 scripts/check_repo.py` 与 `bootstrap.py --dry-run` 通过。
- 仓库内无真实 token。
- 未真实调用外部 MCP。

**下一步（Round 0.7+）**：

- Round 0.7 建立运行环境检查（`scripts/check_environment.py`）与 auto advance runner；MCP 状态仍为 manual_check_required。
- 用户验收 Round 0.5/0.6/0.7。
- 按需将 `mcp.example.json` 合并到用户级配置（人工），真实启用状态以 Cursor Workspace MCP Servers 为准。
- 启动 GitHub/Context7 只读试点前更新 `round_state.yaml` 与 integration_targets。
