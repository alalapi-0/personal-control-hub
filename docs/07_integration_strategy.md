# Integration Strategy

personal-control-hub 的集成策略是先本地、后 mock、再真实 adapter。MCP 集成遵循同一闸门，登记在 `data/mcp/` 并与 `integration_targets.yaml` 对齐。

## 集成阶段

1. Local files: YAML、Markdown、JSONL 作为唯一事实来源。
2. Mock adapters: 为 Feishu/Lark、Codex/Cursor prompt queue、MCP 审计等准备输出格式。
3. Confirmed adapters: 用户确认后才接真实 API 或执行 L2/L3 MCP 动作。

## 第一批集成目标

| id | 说明 | enabled | status |
|---|---|---|---|
| feishu_lark_notifications | 通知 | false | planned |
| feishu_lark_interactive | 交互卡片 | false | planned |
| github_lightweight_sync / github_read_mcp | GitHub 只读 | false | planned |
| browser_test_integration | 浏览器测试复盘 | false | planned |
| codex_prompt_queue | Codex 队列 | false | planned |
| cursor_prompt_queue | Cursor 队列 | false | planned |
| context7_docs | Context7 文档 | false | planned |
| stitch_ui_exploration | Stitch UI | false | planned |

权威列表：`data/integrations/integration_targets.yaml`。

## MCP 与集成关系

- **Cursor** 加载 MCP；本仓库 `data/mcp/mcp_capability_registry.yaml` 登记能力与审批级别。
- **Context7、GitHub 只读** 规划为近期试点（L0/L2），当前仅登记且默认 disabled，真实调用另行授权。
- **Playwright** 归类 L3，与 browser_test_integration 联动，默认 disabled；自动登录、支付、发布和破坏性 UI 操作仍禁止或需显式批准。
- 调度任务 `SCHED-MCP-REGISTRY-AUDIT`、`SCHED-DAILY-PROJECT-SCAN-PREP`、`SCHED-BROWSER-TEST-REVIEW` 准备集成与 MCP 审计材料，不自动执行。

## 配置原则

- token、secret、cookie、API key 不进入仓库。
- 集成配置只写 `config_source: env` 和环境变量名。
- 真实写入必须 `requires_user_confirmation: true`。
- `write_back_allowed` 默认 false。
- 新增 MCP 须同时更新 registry、policy、roadmap 与相关 integration target。
