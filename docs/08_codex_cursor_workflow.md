# Codex / Cursor Workflow

personal-control-hub 是 Cursor、Codex 和 ChatGPT 之间的控制台。

## Cursor

Cursor 是日常主力项目推进环境与 **MCP 宿主**，负责：

- 小步编辑。
- 本仓库治理文档维护。
- 本地检查和轻量脚本。
- 外部项目只读扫描。
- 每日 next actions 推进。
- （未来经用户确认）按 `data/mcp/mcp_capability_registry.yaml` 启用 MCP。

使用 MCP 前读 `prompts/cursor_mcp_usage_prompt.md` 与 `docs/12_external_tool_approval_model.md`。Round 0.5 禁止真实调用外部 MCP。

## Codex

Codex 是高质量执行器，负责：

- 复杂代码修改。
- 关键轮次推进。
- 审查、重构和测试补强。
- 需要更高代码质量和更强执行上下文的任务。

Codex 接收 `prompts/codex_project_driver.md` 或后续 prompt queue，不直接读取真实 secret，不直接调用真实付费 API，**不得绕过 MCP L0-L3 策略**，不直接修改外部项目，除非用户明确确认。

## ChatGPT

ChatGPT 用于：

- 规划。
- 分析。
- Prompt 生成。
- 外部讨论和方案比较。

## MCP 分工

| 角色 | MCP 关系 |
|---|---|
| Cursor | 宿主；加载 `.cursor/mcp.json` 或用户设置 |
| Codex | 遵守 policy；外部工具同等审批 |
| personal-control-hub | 登记、审计、调度准备；`hub.py mcp list|policy` |

## 执行分层

- Planner: ChatGPT 或用户，产出目标、约束和验收标准。
- Controller: personal-control-hub，记录项目状态、数据、MCP 登记、链接、调度和决策。
- Daily executor: Cursor。
- Key executor: Codex。
- Human gate: 用户确认 accepted、L2/L3 MCP、高风险动作和最终优先级。

## 完工规则

每次关键轮次必须留下：

- 修改范围。
- 验证命令。
- 结果摘要。
- 未解决问题。
- 是否触碰真实 API、token、外部 MCP、外部项目写入。
