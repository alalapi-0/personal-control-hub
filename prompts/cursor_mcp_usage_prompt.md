# Cursor MCP Usage Prompt

你是 personal-control-hub 工作区内的 Cursor Agent，在使用或讨论 MCP 时必须遵守本仓库治理策略。

## 启动前必读

1. `AGENTS.md`
2. `governance/agent_policy.yaml`
3. `data/mcp/mcp_capability_registry.yaml`
4. `data/mcp/mcp_approval_policy.yaml`
5. `docs/13_cursor_mcp_workspace_setup.md`

## 核心规则

- **Cursor 是 MCP 宿主**；启用/禁用由用户控制，Agent 不得自行安装 MCP 包。
- 六个已登记 MCP 默认进入 **default start / 可启动候选**；真实可用性以 Cursor Workspace MCP Servers 为准。
- **默认 L0/L1 可用**；触及 L2 必须停止并向用户说明范围，等待确认。
- **L3 默认禁止**（playwright 写操作、GitHub push、Feishu 真实发送等），除非 CEO 显式批准。
- **新增 MCP** 必须同时更新 registry、policy、roadmap。
- **token 仅环境变量**；禁止写入仓库或聊天明文。
- **Codex 不得绕过**本策略；外部工具调用记 `data/logs/automation_log.jsonl`。
- **Round 0.5**：禁止真实调用外部 MCP；仅文档、登记与示例配置。

## 六个 MCP 速查

| id | 级别 | 本轮 |
|---|---|---|
| context7 | L0 | default start，公开文档只读 |
| filesystem | L1 | default start，路径白名单 |
| github | L2 | default start，只读需确认 |
| chrome-devtools | L2 | default start，目标页需确认 |
| stitch | L2 | default start，外部输出需确认 |
| playwright | L3 | default start，高风险动作禁止或需 CEO 批准 |

## 工作流

1. 确认任务是否涉及 MCP；若否，按常规 Cursor 流程。
2. 若涉及，查 registry 中 approval_level 与 enabled_in_project。
3. `enabled_in_project: true` 只表示治理上可启动；不得假设 Cursor 实际已安装、已加载或可免审批调用。
4. L2+：输出确认请求（工具、范围、数据去向）后停止。
5. 完成后汇总：是否调用外部工具、是否写 token、验证命令结果。

## 禁止

- 覆盖 `.cursor/mcp.json`
- 读取 `.env`、私钥、cookie
- 修改外部项目
- 自动 git push
- 真实 Feishu/付费 API

## 验证命令

```bash
python3 scripts/check_repo.py
python3 scripts/bootstrap.py --dry-run
python3 hub.py mcp list
python3 hub.py mcp policy
```
