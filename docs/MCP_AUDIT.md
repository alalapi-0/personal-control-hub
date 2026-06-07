# MCP 配置审计报告

> 审计时间：2026-06-07  
> 仓库路径：`/Users/alalapi/PycharmProjects/personal-control-hub`  
> 审计类型：仓库级 MCP 配置治理（Round 配置补全）

## 1. 审计前状态

| 检查项 | 结果 |
|---|---|
| 工作目录 | `/Users/alalapi/PycharmProjects/personal-control-hub` |
| `.cursor/` 目录 | ✅ 存在 |
| `.cursor/mcp.json` | ❌ 不存在（仅有 `mcp.example.json`） |
| `.env` | ❌ 不存在 |
| `.env.example` | ❌ 不存在 |
| `docs/` | ✅ 存在（有 `13_cursor_mcp_workspace_setup.md`，无专用 MCP 治理文档） |
| `scripts/` | ✅ 存在（有 `check_repo.py`，无 MCP JSON 检查脚本） |
| `package.json` | ❌ 不存在 |
| `.gitignore` | ❌ 不存在 |
| `AGENTS.md` | ✅ 存在 |
| `README.md` | ✅ 存在 |
| `PROJECT_STATE.md` | ❌ 不存在 |
| 疑似真实密钥文件 | ✅ 未发现 `.env` 或含 token 的配置 |

## 2. 已有 MCP 相关资产

- `.cursor/mcp.example.json`：六个 server 的示例配置（占位路径与 `${VAR}` 占位符）
- `data/mcp/mcp_servers.example.yaml`：YAML 版示例，含 stitch 命令 `stitch-mcp`
- `data/mcp/mcp_capability_registry.yaml`：六个 MCP 能力登记
- `docs/13_cursor_mcp_workspace_setup.md`：Round 0.5 工作区说明（偏治理策略，非操作手册）
- `prompts/cursor_mcp_usage_prompt.md`：Agent 使用边界

## 3. 已有 / 缺失 Server 对照

| Server | 示例配置中 | 审计前 mcp.json | 需要 Token | 批准 + 重启 |
|---|---|---|---|---|
| filesystem | ✅ | ❌ 缺失 | 否 | 是 |
| playwright | ✅ | ❌ 缺失 | 否 | 是 |
| chrome-devtools | ✅ | ❌ 缺失 | 否 | 是 |
| context7 | ✅ | ❌ 缺失 | 通常否（可选 CONTEXT7_API_KEY） | 是 |
| github | ✅ | ❌ 缺失 | **是**（GITHUB_PERSONAL_ACCESS_TOKEN） | 是 |
| stitch | ✅ | ❌ 缺失 | **是**（STITCH_API_KEY） | 是 |

## 4. stitch 命令来源

已从仓库既有配置确认：

```json
{
  "command": "npx",
  "args": ["-y", "stitch-mcp"],
  "env": { "STITCH_API_KEY": "<SET_IN_CURSOR_OR_SHELL_ENV>" }
}
```

来源：`.cursor/mcp.example.json`、`data/mcp/mcp_servers.example.yaml`。

## 5. 本轮计划修改的文件

| 文件 | 操作 |
|---|---|
| `.cursor/mcp.json` | **新建**（合并自 mcp.example.json，替换为实际仓库路径） |
| `.env.example` | **新建** |
| `.gitignore` | **新建**（含 `.env`、`.cursor/mcp.json.bak` 规则） |
| `package.json` | **新建**（仅含 `check:mcp` 脚本） |
| `scripts/check_mcp_config.js` | **新建** |
| `docs/MCP_AUDIT.md` | **新建**（本文件） |
| `docs/MCP_SETUP.md` | **新建** |
| `docs/MCP_TROUBLESHOOTING.md` | **新建** |
| `docs/MCP_REUSE_GUIDE.md` | **新建** |

## 6. 本轮不修改的内容

- 业务代码（`src/`、`hub.py` 等）
- `.cursor/mcp.example.json`（保留为可复制模板）
- `data/mcp/*.yaml` 治理登记
- `docs/13_cursor_mcp_workspace_setup.md`（保留 Round 0.5 策略文档，新文档与之互补）
- 用户级 Cursor Settings
- 真实 `.env` 或任何 token

## 7. 配置层级说明

| 层级 | 内容 | 谁负责 |
|---|---|---|
| 仓库可配置 | `.cursor/mcp.json`、`.env.example`、检查脚本、文档 | 本轮 Agent / 开发者 |
| Cursor UI 手动批准 | Settings → Tools & MCP 中对每个 server 点批准 | **用户** |
| 需完全退出重启 Cursor | 批准后加载 MCP 子进程 | **用户** |
| 需新建普通前台 Agent 对话 | 新线程才暴露 MCP 工具 | **用户** |
| 需用户提供 Token | GitHub、Stitch（可选 Context7） | **用户** |

## 8. 重要声明

**本轮完成仓库级配置 ≠ 当前 Agent 线程已可用 MCP 工具。**

当前线程可能仍显示 server 存在但 not loaded / needs approval，或 ListMcpResources 为空——这属于 Cursor 运行时加载机制，须用户按 `docs/MCP_SETUP.md` 手动完成批准与重启。
