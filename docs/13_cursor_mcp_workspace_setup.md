# Cursor MCP 工作区配置指南

Round 0.5 文档。说明如何在 Cursor 中准备 MCP 工作区配置，而不在本轮真实安装、真实调用服务或写入密钥。

## 1. 原则

- Cursor 是 MCP **宿主**；personal-control-hub 提供登记与策略，不替代 Cursor 设置 UI。
- 配置示例在 `.cursor/mcp.example.json`；**不要**在仓库写入真实 token。
- 若用户已有 `.cursor/mcp.json`，Agent **不得覆盖**，仅报告差异。
- 六个 registry 候选默认 disabled；当前受保护项目配置只含 `filesystem`，不得自动补齐。
- 登记、项目配置、运行时可用和动作授权分别判断；L0-L3 只分类风险。

## 2. 配置层级

1. **项目级**：`<repo>/.cursor/mcp.json`（可选，勿提交 secret）
2. **用户级**：Cursor Settings → MCP（推荐放 token）
3. **示例**：`.cursor/mcp.example.json`（本仓库，占位符）

复制示例时，将 `${VAR}` 改为本机环境变量，勿把值写进 JSON。

真实是否启用以 Cursor Workspace MCP Servers / 用户级 MCP 设置为准；本仓库示例只表达治理建议与无 token 占位。

## 3. 六个 MCP 说明

### context7（优先级：高，L0）

- **用途**：拉取库/框架最新文档，减少 API 幻觉。
- **启用建议**：Phase 1 试点首选；只读，风险低。
- **安全**：不上传私有代码；API key 用 `CONTEXT7_API_KEY` 环境变量。
- **示例命令**：见 `data/mcp/mcp_servers.example.yaml`。

### filesystem（优先级：高，L1）

- **用途**：受控路径读写，辅助治理文件与扫描输出。
- **启用建议**：明确白名单目录后再启用；args 仅指向 personal-control-hub 或注册的外部入口。
- **安全**：禁止指向 `$HOME`、`.env`、`.git`。

### github（优先级：高，L2 只读）

- **用途**：Issue、PR、checks 只读，支撑项目快照。
- **启用建议**：保持 disabled，只有明确需要并获当前授权后配置；token 用 `GITHUB_PERSONAL_ACCESS_TOKEN`。
- **安全**：写操作视为 L3，默认不授予 repo 写权限 token。

### chrome-devtools（优先级：中，L2）

- **用途**：页面结构、网络、性能诊断。
- **启用建议**：保持 disabled；需要浏览器测试复盘时再确认目标 URL 与数据范围。
- **安全**：不自动登录；不操作生产写接口。

### stitch（优先级：低，L2）

- **用途**：UI 草案与探索。
- **启用建议**：保持 disabled；设计探索轮次按 L2 另行授权。
- **安全**：输出限制在本仓库或临时目录；不写外部业务 repo。

### playwright（优先级：低，L3）

- **用途**：E2E 自动化。
- **启用建议**：保持 disabled；任何真实浏览器动作先按 L3 与当前上级授权判断。
- **安全**：禁止无人值守生产操作、自动登录真实账号、支付、发布和破坏性 UI 操作，除非 CEO 显式批准且 governance 允许。

## 4. 若获授权时的评估顺序

1. context7（L0）
2. filesystem（L1，白名单后）
3. github 只读（L2，用户确认）
4. chrome-devtools（L2，测试场景）
5. stitch（L2，可选）
6. playwright（L3，可启动但动作需批准）

与 `data/mcp/mcp_integration_roadmap.yaml` 中 `priority_order` 一致。

## 5. 安全清单

- [ ] 未将真实 token 提交到 git
- [ ] registry、项目配置、Cursor 运行时状态分别记录，未相互推断
- [ ] 新增 MCP 已更新 registry + policy + roadmap
- [ ] L2/L3 操作有用户确认记录
- [ ] 登记或配置没有被解释为免审批或自动执行真实外部动作
- [ ] 外部项目写入仍遵守 `docs/05_external_project_protocol.md`

## 6. 与 personal-control-hub 联动

```bash
python3 scripts/check_repo.py          # 含 MCP 骨架检查
python3 scripts/bootstrap.py --dry-run
python3 hub.py mcp list                # 能力简表（只读）
python3 hub.py mcp policy              # 审批级别简表（只读）
```

## 7. 故障排查（规划）

| 现象 | 检查 |
|---|---|
| MCP 未出现在 Cursor | 用户级 vs 项目级配置路径 |
| 鉴权失败 | 环境变量是否在本机 shell 生效 |
| Agent 越权 | 对照 `mcp_approval_policy.yaml` 停止并确认 |

## 8. 相关文件

- `.cursor/README.md`
- `.cursor/mcp.example.json`
- `data/mcp/mcp_servers.example.yaml`
- `prompts/cursor_mcp_usage_prompt.md`
- `docs/11_mcp_infrastructure_strategy.md`
