# Cursor 工作区 MCP 说明

本目录存放 MCP 示例和受保护的项目配置。

## 文件

- `mcp.example.json`：无真实 token 的 MCP 服务器示例。
- `mcp.json`：用户/宿主提供的受保护项目配置；当前仅含 `filesystem`，Agent 不得覆盖或补齐。

## 规则

1. 权威登记见 `data/mcp/mcp_capability_registry.yaml`。
2. 审批策略见 `data/mcp/mcp_approval_policy.yaml`。
3. Agent **不得覆盖**已有 `mcp.json`。
4. 六个 MCP 是 registry/example 候选且默认 disabled；登记不等于项目配置、运行时可用或调用授权。
5. 详细说明见 `docs/13_cursor_mcp_workspace_setup.md`。

## 示例使用边界

```bash
# 仅由用户在明确需要时人工复制并填入环境变量
cp mcp.example.json mcp.json
# 编辑 mcp.json，确保 token 来自环境变量而非明文
```
