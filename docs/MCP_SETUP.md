# MCP 配置与状态指南

本文档描述当前 Phase 1 / ROUND-1-1 的真实边界。MCP 有四种必须分开的状态：

1. registry 登记候选；
2. `.cursor/mcp.json` 项目配置；
3. Cursor 当前运行时是否已加载且健康；
4. 当前具体动作是否获得授权。

任何一层都不能推出下一层。

## 当前事实

- registry 登记六个候选：chrome-devtools、context7、filesystem、github、playwright、stitch。
- 六个候选的 `enabled_in_project` 均为 false。
- `.cursor/mcp.example.json` 是候选示例，不是启用清单。
- 受保护的 `.cursor/mcp.json` 当前只包含 `filesystem`；Agent 不得覆盖、重建或自动补齐。
- Cursor 运行时可用性未验证。
- 配置存在不授予读、写、安装、登录、发布或其他动作权限。

## 文件角色

| 文件 | 用途 |
|---|---|
| `.cursor/mcp.json` | 用户/宿主提供的受保护项目配置；当前只含 filesystem |
| `.cursor/mcp.example.json` | 六个登记候选的无密钥示例 |
| `data/mcp/mcp_capability_registry.yaml` | 候选登记与默认 disabled 状态 |
| `data/mcp/mcp_approval_policy.yaml` | L0-L3 风险分类；不是授权来源 |
| `.env.example` | 环境变量名称示例，不含真实值 |

## 只读检查

```bash
node scripts/check_mcp_config.js
python3 scripts/check_environment.py --json
python3 hub.py mcp list
python3 hub.py mcp policy
```

这些命令只验证仓库结构与配置声明，不启用 MCP、不检查真实凭据、不证明运行时健康，也不授予调用权限。

## 变更边界

- 不得因示例或 registry 存在而安装、启用或调用 MCP。
- 不得复制示例覆盖已有 `.cursor/mcp.json`。
- 不得读取、打印或写入真实 token、cookie、API key。
- 只有用户当前明确要求配置某个 MCP，且上级策略与宿主权限允许时，才可规划对应变更。
- 任何 L2/L3 真实动作仍需相应显式确认。

## 运行时验证

若用户明确要求验证 Cursor 运行时，由用户在 Cursor Settings → Tools & MCP 查看实际状态。仓库脚本只能报告 `runtime_unverified`；旧审计报告或示例配置不能证明当前运行时状态。

相关文件：`docs/13_cursor_mcp_workspace_setup.md`、`docs/MCP_TROUBLESHOOTING.md`、`.cursor/README.md`。
