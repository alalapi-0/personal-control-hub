# MCP 故障排查指南

本文档针对 Cursor 中 MCP Server「配置了但当前线程不可用」的常见问题。

> 先运行 `node scripts/check_mcp_config.js` 确认仓库级配置无误，再按本文排查 Cursor 运行时问题。

## 1. CLI 显示 server 存在，但当前线程不可用

**现象**：Cursor CLI 或 Settings 列表中有 server 名称，但 Agent 对话中没有对应工具。

**原因**：

- 当前 Agent 线程创建于 MCP 批准/重启之前
- Multitask 模式未暴露全部 MCP 工具
- Server 处于 needs approval 状态

**处理**：

1. Settings → Tools & MCP → 逐一 **Approve**
2. **完全退出** Cursor（Cmd+Q / 退出应用）
3. 重新打开仓库
4. **新建**普通前台 Agent 对话
5. 在新对话中验证工具列表

**不能做的**：指望修改 `.cursor/mcp.json` 后当前线程自动获得工具。

## 2. ListMcpResources 返回空

**现象**：Agent 调用 ListMcpResources 得到 "No MCP resources found"。

**原因**：

- 多数 MCP server 不提供 Resource，只提供 Tool——空结果是正常的
- 或 MCP 根本未加载到当前线程

**处理**：

- 不要单独以 Resources 为空判断 MCP 失败
- 检查 Tools 是否包含 MCP 专用工具名（如 `mcp_filesystem_*`、`playwright_*` 等）
- 按 MCP_SETUP.md 完成批准 → 重启 → 新建对话

## 3. Server 显示 not loaded / needs approval

**现象**：Tools & MCP 面板中 server 旁显示 not loaded 或 needs approval。

**处理**：

1. 点击 **Approve** / **Enable**
2. 查看 server 日志（Cursor MCP 面板中的 Error Output）
3. 完全退出并重启 Cursor
4. 新建 Agent 对话

首次 npx 拉包可能较慢，等待 30–60 秒后再判断。

## 4. 配置改了但工具没有出现

**检查清单**：

- [ ] 是否保存了 `.cursor/mcp.json`
- [ ] 是否运行 `node scripts/check_mcp_config.js` 通过
- [ ] 是否在 Tools & MCP 中批准
- [ ] 是否 **完全退出** Cursor（不是只关窗口）
- [ ] 是否 **新建**了 Agent 对话
- [ ] 是否误用 Multitask 模式

## 5. GitHub token 缺失

**现象**：github server 启动失败，日志含 authentication / 401 / token。

**处理**：

1. 在 `.env` 或 shell profile 中设置 `GITHUB_PERSONAL_ACCESS_TOKEN`
2. 或在 Cursor MCP 设置中为 github server 添加环境变量
3. 将 `.cursor/mcp.json` 中 `<SET_IN_CURSOR_OR_SHELL_ENV>` 替换为实际引用方式（若 Cursor 不自动继承 shell env）
4. 重启 Cursor，新建对话

**注意**：不要把真实 token 提交到 git。

## 6. Stitch key 缺失

**现象**：stitch server 启动失败或工具不可用。

**处理**：同 GitHub，配置 `STITCH_API_KEY` 后重启。

若 `stitch-mcp` 包不可用，检查 npm  registry 或查阅 Stitch 官方 MCP 文档更新包名。

## 7. filesystem 允许目录错误

**现象**：filesystem 工具无法读写仓库文件，或权限过大被 Cursor 拒绝。

**检查**：

```bash
node scripts/check_mcp_config.js
```

确认 `.cursor/mcp.json` 中 filesystem args 最后一项为 **当前仓库绝对路径**，且不是 `/` 或 `$HOME`。

**修复**：将路径改为新仓库根目录绝对路径，重启 Cursor。

## 8. playwright / chrome-devtools 冲突

**现象**：两个 browser 相关 MCP 同时启用时，浏览器启动失败或 profile 被占用。

**处理**：

- 同一时间只启用一个 browser MCP 进行测试
- 关闭占用 Chrome profile 的其他进程
- 重启 Cursor 后再试

## 9. 浏览器 profile 被占用

**现象**：chrome-devtools 或 playwright 报 browser already running / profile in use。

**处理**：

1. 关闭多余的 Chrome / Chromium 实例
2. 结束残留的 `chrome-devtools-mcp` 或 playwright 进程
3. 重启 Cursor

## 10. 当前线程没有专用工具

**结论**：这是 **预期行为**，不是配置失败。

仓库中的 `.cursor/mcp.json` 只告诉 Cursor **如何启动** MCP server。是否注入到 **当前** Agent 线程，取决于：

- 批准状态
- Cursor 是否已重启
- 对话是否在新线程中创建
- Token 是否有效

**唯一可靠验证方式**：批准 → 退出 Cursor → 重开 → 新建普通前台 Agent 对话 → 检查工具列表。

## 11. 为什么不能靠仓库文件直接让当前线程生效？

Cursor Agent 线程在创建时绑定可用工具快照。`.cursor/mcp.json` 是静态配置，不会 push 到已运行线程。MCP 子进程也由 Cursor 主进程在启动/批准时 spawn，已打开的线程不会 retroactive 更新。

## 12. npx 首次运行慢或失败

**现象**：server 长时间 loading 或 command not found。

**处理**：

- 确保本机有 Node.js 与 npx
- 检查网络（npx 需下载包）
- 在终端手动测试：`npx -y @playwright/mcp@latest --help`
- 查看 Cursor MCP Error Output

## 13. 快速诊断命令

```bash
# 仓库配置检查
node scripts/check_mcp_config.js

# Python 治理检查（含 MCP 骨架）
python3 scripts/check_repo.py

# Hub MCP 能力简表（只读，非 Cursor 运行时）
python3 hub.py mcp list
```

## 14. 仍无法解决时

收集以下信息：

1. Cursor Settings → Tools & MCP 中各 server 状态截图
2. 失败 server 的 Error Output 日志
3. `node scripts/check_mcp_config.js` 完整输出
4. 是否已完全退出 Cursor 并新建对话

参考 `docs/MCP_SETUP.md` 人工步骤逐项核对。
