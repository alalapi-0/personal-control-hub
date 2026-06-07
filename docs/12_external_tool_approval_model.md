# 外部工具审批模型（L0-L3）

Round 0.5 文档。定义 Agent 使用 MCP 与其他外部工具时的审批级别、典型场景与停止规则。权威机器可读版本：`data/mcp/mcp_approval_policy.yaml`。

## 模型总览

| 级别 | 名称 | 用户确认 | 日志 | 默认 |
|---|---|---|---|---|
| L0 | 只读上下文 | 否 | 可选 | 允许 |
| L1 | 本仓库低风险写 | 否 | 是 | 允许 |
| L2 | 外部/shell 低风险 | 是 | 是 | 须确认 |
| L3 | 高风险写 | CEO 显式批准 | 是 | **禁止** |

Agent 默认工作在 L0/L1。触及 L2 必须停止并说明；L3 除非 round_state 与用户明确批准，否则不得执行。

Round 0.5 起，六个已登记 MCP 可处于 default start / 默认可启动状态。default start 只代表 Cursor 工作区可以加载该 MCP，不代表 Agent 获得无限访问权；具体工具动作仍按下表的审批等级判断。

## L0：只读上下文

**定义**：获取信息，不改变本仓库或外部系统状态。

**典型场景**：

- Context7 查询 React、FastAPI 等公开文档。
- 读取 `mcp_capability_registry.yaml` 与 approval policy。
- GitHub 只读列出 open issues（启用后、无写操作）。
- Chrome DevTools 只读 DOM 快照（无登录、无提交）。

**Agent 行为**：可继续；建议在回复中注明数据来源。

## L1：本仓库低风险写入

**定义**：修改 personal-control-hub 内文档、YAML、扫描草案、日志，不覆盖用户明确保护的内容。

**典型场景**：

- 更新 `data/project_scans/` 扫描草案。
- 写入 MCP 审计报告到 `docs/reports/`。
- Filesystem MCP 编辑本仓库治理文件（非删除、非大规模迁移）。
- 追加 `automation_log.jsonl`。

**Agent 行为**：完成后写入 `data/logs/automation_log.jsonl`，字段含 mcp_id、approval_level、action_summary。

## L2：外部系统或 Shell 低风险

**定义**：触及外部 API、浏览器会话或本地 shell，风险可控但需人工闸门。

**典型场景**：

- GitHub MCP 拉取 PR diff 与 CI 状态。
- Chrome DevTools 检查需登录的 staging 页面（用户在场）。
- Stitch 生成可分享 UI 草稿链接。
- 对外部注册路径执行只读 `git log`（符合 `docs/05_external_project_protocol.md`）。

**Agent 行为**：

1. 停止执行。
2. 说明工具、范围、数据去向。
3. 等待用户确认或白名单命中。
4. 确认后执行并记日志。

## L3：高风险写操作

**定义**：可改变生产状态、泄露凭据或触发不可轻易回滚的操作。当前 round 默认 **禁止**。

**典型场景**：

- Playwright 自动登录并提交表单。
- GitHub push、merge PR、close issue。
- Feishu/Lark 真实消息发送。
- 修改外部业务仓库代码。
- 远程桌面或系统级控制。
- 批量爬取未授权站点。

**Agent 行为**：

1. 拒绝默认执行。
2. 仅当用户/CEO 显式批准且 governance 允许时，方可列入计划。
3. 必须记完整日志与决策记录。

## MCP 与审批级别映射

| MCP | 默认级别 | 备注 |
|---|---|---|
| context7 | L0 | 文档只读 |
| filesystem | L1 | 路径白名单 |
| github | L2（读）/ L3（写） | 写默认禁 |
| chrome-devtools | L2 | 调试确认 |
| stitch | L2 | UI 探索 |
| playwright | L3 | 可启动但高风险动作仍禁用或需 CEO 批准 |

## 跨 Agent 规则

- **Cursor** 是 MCP 宿主，加载配置由用户控制；仓库中的 default start 是治理建议，不替代 Cursor 实际开关。
- **Codex** 不得绕过本 policy；等效外部动作适用相同级别。
- **新增 MCP** 须更新 registry + policy + roadmap。
- **token** 仅环境变量，禁止入库。

## Round 0.5 特殊约束

- 不得自行安装 MCP。
- 六个已登记 MCP 可默认进入启动候选；不得把 L3 启动状态解释为允许高风险动作。
- 不得真实调用外部 MCP 服务。
- 不得写入真实 token。

## 日志格式建议

追加到 `data/logs/automation_log.jsonl`：

```json
{"timestamp":"ISO8601","agent":"cursor","mcp_id":"github","approval_level":"L2","action_summary":"list open PRs","user_confirmed":true}
```

## 相关文件

- `data/mcp/mcp_approval_policy.yaml`
- `governance/agent_policy.yaml`
- `AGENTS.md`
- `prompts/mcp_audit_prompt.md`
- `docs/16_runtime_environment.md`（MCP 环境为 manual_check_required）
- `scripts/auto_advance_runner.py`（不调用 MCP；L2/L3 仍须 stop）
