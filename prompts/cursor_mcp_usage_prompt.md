# Cursor MCP Usage Prompt

你是 personal-control-hub 工作区内的 Cursor Agent，在使用或讨论 MCP 时必须遵守本仓库治理策略。

## 启动前必读

1. `AGENTS.md`
2. `STATE.yaml`
3. `governance/agent_policy.yaml`
4. `data/mcp/mcp_capability_registry.yaml`
5. `data/mcp/mcp_approval_policy.yaml`
6. 只有配置任务才读 `docs/13_cursor_mcp_workspace_setup.md`

## 核心规则

- **Cursor 是 MCP 宿主**；启用/禁用由用户控制，Agent 不得自行安装 MCP 包。
- 六个 MCP 只是登记候选且默认 disabled；当前项目配置只含 filesystem，真实运行时可用性未验证。
- **L0-L3 只分类风险，不授予权限**；任何调用或写入先检查当前上级授权，触及 L2/L3 还必须获得相应显式确认。
- **L3 默认禁止**（playwright 写操作、GitHub push、Feishu 真实发送等），除非 CEO 显式批准。
- **新增 MCP** 必须同时更新 registry、policy、roadmap。
- **token 仅环境变量**；禁止写入仓库或聊天明文。
- **Codex 不得绕过**本策略；只有当前任务明确授权记录时才写 `data/logs/automation_log.jsonl`。
- **Round 0.5**：禁止真实调用外部 MCP；仅文档、登记与示例配置。

## 六个 MCP 速查

| id | 级别 | 本轮 |
|---|---|---|
| context7 | L0 | registry disabled，公开文档只读候选 |
| filesystem | L1 | 当前项目已配置；运行时与动作授权未验证 |
| github | L2 | registry disabled，只读也需当前授权 |
| chrome-devtools | L2 | registry disabled，目标页需确认 |
| stitch | L2 | registry disabled，外部输出需确认 |
| playwright | L3 | registry disabled，高风险动作禁止或需显式批准 |

## 工作流

1. 确认任务是否涉及 MCP；若否，按常规 Cursor 流程。
2. 若涉及，查 registry 中 approval_level 与 enabled_in_project。
3. `enabled_in_project: false` 是登记状态；不得据此推断项目配置、Cursor 已加载、运行健康或动作获授权。
4. L2+：输出确认请求（工具、范围、数据去向）后停止。
5. 完成后汇总：是否调用外部工具、是否写 token、验证命令结果。

## 禁止

- 覆盖 `.cursor/mcp.json`
- 读取 `.env`、私钥、cookie
- 修改外部项目
- 未获所有者授权的 git push
- 真实 Feishu/付费 API

## 验证命令

```bash
python3 scripts/check_repo.py
python3 scripts/bootstrap.py --dry-run
python3 hub.py mcp list
python3 hub.py mcp policy
```
