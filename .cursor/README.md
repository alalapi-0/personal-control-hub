# Cursor 工作区 MCP 说明

本目录存放 **示例** MCP 配置，供 personal-control-hub Round 0.5 使用。

## 文件

- `mcp.example.json`：无真实 token 的 MCP 服务器示例。
- 用户实际配置可使用项目级 `mcp.json` 或 Cursor 用户设置；**勿将含 secret 的 mcp.json 提交到 git**。

## 规则

1. 权威登记见 `data/mcp/mcp_capability_registry.yaml`。
2. 审批策略见 `data/mcp/mcp_approval_policy.yaml`。
3. Agent **不得覆盖**已有 `mcp.json`。
4. Round 0.5 六个已登记 MCP 默认可启动；L2/L3 具体动作仍须按审批策略确认，且不得覆盖真实 `mcp.json`。
5. 详细说明见 `docs/13_cursor_mcp_workspace_setup.md`。

## 复制示例

```bash
# 仅当不存在 mcp.json 时，人工复制并填入环境变量
cp mcp.example.json mcp.json
# 编辑 mcp.json，确保 token 来自环境变量而非明文
```
