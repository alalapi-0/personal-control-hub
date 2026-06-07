# Architecture

personal-control-hub 采用轻量本地文件架构，先用 Markdown、YAML、JSONL 和 Python 标准库完成可审计骨架。

## 分层

- Entry: `README.md`、`AGENTS.md`、`docs/00_start_here.md`。
- Governance: `governance/*.yaml` 约束权限、Round、文件角色和协议。
- Registry: `data/registry/external_projects.yaml` 记录外部项目。
- Programs: `data/programs/*.yaml` 记录现实目标和项目链接。
- Tasks: `data/tasks/*.yaml` 记录 inbox 和 next actions。
- Scheduler: `data/scheduler/scheduled_tasks.yaml` 记录计划任务，但不自动执行。
- Integrations: `data/integrations/integration_targets.yaml` 记录未来 Feishu/Lark、GitHub、Browser test、Codex/Cursor queue、Context7、Stitch 等集成，占位禁用。
- MCP: `data/mcp/` 记录能力矩阵、L0-L3 审批、集成路线图与示例服务器配置；Cursor 为宿主。
- Cursor: `.cursor/mcp.example.json` 提供无 token 示例；不覆盖用户 `mcp.json`。
- Services: `src/hub/services/` 实现 registry、MCP 只读查询、scan、profile、link、scheduler、integration 和 sync（渐进）。
- Scripts: `scripts/check_repo.py` 和 `scripts/bootstrap.py` 提供最小验证与补骨架能力。

## 数据流

1. 用户登记 external project。
2. 扫描器只读入口文件和有限目录。
3. profile 服务生成项目身份、状态和证据。
4. snapshot 服务生成当前状态与风险。
5. program link 服务提出 proposals。
6. 用户确认后 proposals 才进入 confirmed links。
7. scheduler 只准备 prompt 或提醒，不默认执行真实外部动作。

## 安全边界

第一阶段没有数据库、RAG、向量库、真实模型调用、真实 Feishu 调用、**真实 MCP 调用**、外部仓库写入和远程控制。MCP 默认 L0/L1；L2/L3 须确认。所有高风险动作都必须先进入 proposal 或 pending_confirmation 状态。
