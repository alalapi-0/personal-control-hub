# MCP 配置与启用指南

本文档说明 personal-control-hub 仓库中六个 Cursor MCP Server 的用途、配置位置与启用步骤。

> 治理策略与审批等级见 `docs/13_cursor_mcp_workspace_setup.md`、`data/mcp/mcp_approval_policy.yaml`。  
> 故障排查见 `docs/MCP_TROUBLESHOOTING.md`。  
> 新仓库复用见 `docs/MCP_REUSE_GUIDE.md`。

## 1. 配置文件位置

| 文件 | 用途 |
|---|---|
| `.cursor/mcp.json` | **项目级** MCP 服务器定义（可提交 git，不含真实密钥） |
| `.cursor/mcp.example.json` | 可复制模板（占位路径） |
| `.env.example` | 环境变量名说明（不含真实值） |
| 用户级 Cursor Settings → MCP | 推荐在此或系统 shell 注入 token |

## 2. 六个 MCP Server 说明

### filesystem（无需 token）

- **用途**：在受控目录内读写文件，辅助治理与文档更新。
- **允许目录**：当前仓库根目录（`.cursor/mcp.json` 中 args 最后一项）。
- **安全**：不得指向 `/`、整个 `$HOME` 或 `.env` 所在敏感路径。

### context7（通常无需 token）

- **用途**：查询第三方库官方文档，减少 API 幻觉。
- **包名**：`@upstash/context7-mcp@latest`
- **可选**：`CONTEXT7_API_KEY`（见 `.env.example`）

### chrome-devtools（无需 token）

- **用途**：Chrome DevTools Protocol 调试——DOM、Network、Console、Performance。
- **包名**：`chrome-devtools-mcp@latest`
- **注意**：与 playwright 可能争用浏览器 profile，见故障排查。

### playwright（无需 token）

- **用途**：浏览器自动化、截图、表单测试、UI 验收。
- **包名**：`@playwright/mcp@latest`
- **注意**：首次运行可能下载浏览器；L3 高风险动作仍须按治理策略审批。

### github（需要 token）

- **用途**：GitHub 仓库、Issue、PR、Commit、搜索等。
- **包名**：`@modelcontextprotocol/server-github`
- **必需环境变量**（二选一，推荐第一个）：
  - `GITHUB_PERSONAL_ACCESS_TOKEN`
  - `GITHUB_TOKEN`
- **配置方式**：
  1. 复制 `.env.example` 为 `.env`（**不要提交 `.env`**）
  2. 填入 token，或
  3. 在 Cursor Settings → Tools & MCP → github server 的环境变量中填入
  4. 将 `.cursor/mcp.json` 中 `<SET_IN_CURSOR_OR_SHELL_ENV>` 替换为实际值，或确保 Cursor 从 shell 继承环境变量

### stitch（需要 token）

- **用途**：Stitch UI 生成、设计资源、screen 相关工具。
- **包名**：`stitch-mcp`（来源：本仓库 `mcp.example.json` / `mcp_servers.example.yaml`）
- **必需环境变量**：`STITCH_API_KEY`
- **配置方式**：同 github，见 `.env.example`

## 3. Token 需求汇总

| Server | 需要 Token | 变量名 |
|---|---|---|
| filesystem | 否 | — |
| context7 | 通常否 | `CONTEXT7_API_KEY`（可选） |
| chrome-devtools | 否 | — |
| playwright | 否 | — |
| github | **是** | `GITHUB_PERSONAL_ACCESS_TOKEN` / `GITHUB_TOKEN` |
| stitch | **是** | `STITCH_API_KEY` |

## 4. 仓库级检查

```bash
node scripts/check_mcp_config.js
# 或
npm run check:mcp
```

## 5. 人工启用步骤（必须）

以下步骤在 **Cursor UI** 中完成，Agent 无法代劳：

```text
1. 打开 Cursor Settings
2. 进入 Tools & MCP
3. 找到当前仓库配置的 MCP server
4. 对 filesystem / playwright / chrome-devtools / context7 / github / stitch 逐一批准
5. 如果 github 或 stitch 需要密钥，先配置对应环境变量或在 Cursor 支持的位置填写
6. 完全退出 Cursor，不只是关闭窗口
7. 重新打开当前仓库
8. 新建普通前台 Agent 对话
9. 不要使用 Multitask 模式测试 MCP
10. 在新对话中让 Agent 检查当前线程暴露的工具列表
```

## 6. 为什么批准后还要重启 Cursor？

Cursor 在启动时加载 MCP 子进程。Settings 中的批准与 `.cursor/mcp.json` 变更，通常需要 **完全退出**（Quit）并重新打开，才会重新 spawn MCP server 进程。仅关闭单个窗口或标签页不够。

## 7. 为什么要新建普通前台 Agent 对话？

已存在的 Agent 线程在创建时绑定当时可用的工具集。配置变更或批准后，**旧线程不会自动刷新 MCP 工具列表**。必须新建一个普通前台 Agent 对话（非 Multitask）才能看到新工具。

## 8. Multitask 模式说明

Multitask 模式下，部分 MCP 工具可能不可用或行为不同。测试 MCP 是否生效时，请使用 **普通前台 Agent 单任务对话**。

## 9. 如何确认当前线程是否暴露了 MCP 工具

在新对话中，可让 Agent：

1. 列出当前可用工具（是否包含 filesystem / playwright / github 等 MCP 专用工具名）
2. 尝试只读操作（如 context7 查文档、filesystem 读 README）

若工具列表中没有 MCP 专用工具：

- 不代表 `.cursor/mcp.json` 一定错误
- 可能是未批准、未重启、未新建线程，或 token 缺失导致 server 启动失败

## 10. 配置层级区分

| 类型 | 说明 |
|---|---|
| 仓库可配置 | `mcp.json`、文档、检查脚本、`.env.example` |
| 用户 UI 批准 | Cursor Settings → Tools & MCP |
| 需重启 Cursor | 批准或改配置后 |
| 需新建对话 | 暴露工具到新线程 |
| 用户自备密钥 | GitHub、Stitch（可选 Context7） |

## 11. 相关文件

- `.cursor/mcp.json` — 实际项目配置
- `.cursor/mcp.example.json` — 模板
- `docs/MCP_AUDIT.md` — 审计记录
- `docs/MCP_TROUBLESHOOTING.md` — 故障排查
- `docs/MCP_REUSE_GUIDE.md` — 新仓库复用
- `docs/13_cursor_mcp_workspace_setup.md` — Round 0.5 治理策略
