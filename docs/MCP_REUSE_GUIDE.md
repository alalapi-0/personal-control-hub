# MCP 配置新仓库复用指南

将 personal-control-hub 的 MCP 治理配置迁移到其他 Cursor 仓库时的操作说明。

## 1. 建议复制的文件

| 文件 | 说明 |
|---|---|
| `.cursor/mcp.json` | **需修改 filesystem 路径** |
| `.cursor/mcp.example.json` | 可选，作为模板 |
| `.env.example` | 环境变量名说明 |
| `scripts/check_mcp_config.js` | 配置检查脚本 |
| `package.json` | 至少保留 `scripts.check:mcp`（可合并到已有 package.json） |
| `docs/MCP_SETUP.md` | 启用指南 |
| `docs/MCP_TROUBLESHOOTING.md` | 故障排查 |
| `docs/MCP_REUSE_GUIDE.md` | 本文件 |
| `.gitignore` 中相关规则 | `.env`、`.cursor/mcp.json.bak` |

## 2. 不要直接复制的文件

| 文件 | 原因 |
|---|---|
| `.env` | 含真实密钥，且不应跨仓库共享 |
| `.cursor/mcp.json.bak` | 本地备份，不入 git |
| 含真实 token 的 `mcp.json` | 安全风险 |

## 3. 重新生成 filesystem 允许目录

**不要**直接复制旧仓库的绝对路径。

方法一：手动编辑 `.cursor/mcp.json`

```json
"args": [
  "-y",
  "@modelcontextprotocol/server-filesystem",
  "/新仓库/绝对/路径"
]
```

方法二：在新仓库根目录运行检查脚本，若路径不一致会给出警告：

```bash
node scripts/check_mcp_config.js
```

方法三：从 `mcp.example.json` 复制后替换占位符。

## 4. 检查 `.cursor/mcp.json`

```bash
cd /path/to/new-repo
node scripts/check_mcp_config.js
```

确保六个 server 齐全、JSON 有效、filesystem 路径正确、无真实 token 明文。

## 5. 运行 npm 检查

```bash
npm run check:mcp
# 或
node scripts/check_mcp_config.js
```

## 6. 在 Cursor 里批准 MCP

每个新仓库打开后：

1. Cursor Settings → Tools & MCP
2. 逐一 Approve：filesystem、playwright、chrome-devtools、context7、github、stitch
3. **完全退出** Cursor
4. 重新打开 **新仓库**
5. **新建**普通前台 Agent 对话

项目级 `mcp.json` 不会自动跳过批准步骤。

## 7. GitHub 和 Stitch token 处理

1. 在新机器或新仓库创建 `.env`（从 `.env.example` 复制）
2. 填入 `GITHUB_PERSONAL_ACCESS_TOKEN`、`STITCH_API_KEY`
3. **不要**把 `.env` 提交到 git
4. 或在 Cursor 用户级 MCP 设置中配置环境变量
5. 没有 token 时，github / stitch 可能启动失败，但不阻塞其他 MCP

## 8. 为什么不要把真实 `.env` 提交

- Git 历史永久保留，泄露后难以撤销
- 协作仓库中 token 会被所有 clone 者看到
- 本仓库 `.gitignore` 已排除 `.env` 和 `.env.*`（保留 `.env.example`）

## 9. 为什么新仓库需要重新打开 Cursor

Cursor 按工作区加载 `.cursor/mcp.json`。切换仓库或首次 clone 后，应：

- 打开新仓库文件夹
- 批准 MCP
- 完全退出并重启 Cursor

否则可能仍使用旧工作区的 MCP 缓存状态。

## 10. 如何判断当前线程是否真的暴露了 MCP 工具

在 **新建的普通前台 Agent 对话** 中：

1. 询问 Agent 当前可用工具列表
2. 尝试 MCP 只读操作（如 filesystem 读文件、context7 查文档）

以下情况 **不代表配置失败**：

- 旧对话中没有 MCP 工具
- ListMcpResources 为空
- CLI 显示 server 但 not loaded

须完成：批准 → 退出 Cursor → 重开 → 新建对话。

## 11. 与 personal-control-hub 治理文件的关系

若新仓库也要接入 personal-control-hub 治理体系， additionally 参考：

- `data/mcp/mcp_capability_registry.yaml`
- `docs/13_cursor_mcp_workspace_setup.md`
- `prompts/cursor_mcp_usage_prompt.md`

纯 MCP 工具启用只需本文档 + `MCP_SETUP.md` 即可。

## 12. 最小复用清单（Quick Start）

```bash
# 1. 复制文件到新仓库
cp .cursor/mcp.json /path/to/new-repo/.cursor/
cp .env.example scripts/check_mcp_config.js /path/to/new-repo/...
cp docs/MCP_*.md /path/to/new-repo/docs/

# 2. 修改 filesystem 路径
# 编辑 new-repo/.cursor/mcp.json

# 3. 检查
cd /path/to/new-repo && node scripts/check_mcp_config.js

# 4. Cursor UI：批准 → 退出 → 重开 → 新建对话
```
