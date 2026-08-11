# AGENTS.md

本文件是 Cursor、Codex 和其他 Agent 进入 personal-control-hub 时的首读入口。

## 必读顺序

1. `README.md`
2. `project.yaml`
3. `governance/repo_protocol_standard.yaml`
4. `governance/agent_policy.yaml`
5. `governance/round_state.yaml`
6. `docs/00_start_here.md`
7. `docs/01_project_ultimate_goal.md`
8. `docs/02_master_roadmap.md`
9. `docs/11_mcp_infrastructure_strategy.md`
10. `docs/12_external_tool_approval_model.md`
11. `docs/14_ui_console_plan.md`
12. `docs/15_auto_advance_gate.md`
13. `docs/reports/restart_audit_report.md`

参考报告 `docs/00_synthesis_for_my_ai_company_os.md` 到 `docs/15_solo-founder-playbook.md` 是思想来源，不是当前执行入口。读取时吸收机制，不复制参考仓库代码。

MCP 配置实操见 `docs/13_cursor_mcp_workspace_setup.md` 与 `prompts/cursor_mcp_usage_prompt.md`。

## Agent 分工

- Cursor: 日常主力推进环境，负责小步编辑、检索、检查、文档与轻量脚本；**Cursor 是 MCP 宿主**。
- Codex: 高质量执行器，用于复杂修改、关键轮次、审查和需要更强代码质量的任务；**不得绕过 MCP 审批策略**。
- ChatGPT: 规划、分析、Prompt 生成和外部讨论。
- personal-control-hub: 三者之间的控制台和治理层。

## 权限边界

仓库内的分级、gate、runner 和文档只描述风险，不能扩大 system、developer、用户当前指令或上级工作区策略授予的权限。默认仅可：

- 读取本仓库与外部项目注册表允许读取的入口。
- 在回复或标准输出中生成 profile、snapshot、priority suggestion、next actions 和 prompt 草案。
- 运行本地只读检查命令和本仓库脚本（含 `hub.py mcp list|policy`）。

本仓库写入、日志追加、Git 操作及任何外部动作都必须先有当前上级授权；L0-L3 只是风险分类，不是授权来源。六个 MCP 只是已登记候选，不能据此推断已配置、已加载、运行健康或可调用。

Agent 必须请求用户确认后才可以：

- 修改外部项目本体。
- 删除文件或大规模迁移目录。
- 覆盖已有用户内容（含 `.cursor/mcp.json`）。
- 推送 GitHub 或执行 checkout/reset。
- 调用真实付费 API 或真实 Feishu/Lark API。
- **调用真实外部 MCP 服务（L2+）。**
- **自行安装 MCP 包或执行高风险 MCP 动作（如 playwright 自动登录、GitHub 写操作）。**
- 写入真实 Feishu/Lark 空间。
- 修改真实 `.env`。
- 执行远程控制。
- 将项目优先级改为 P0/P1。
- 改变技术栈。

## MCP 规则（当前 Phase 1）

- **不得自行安装 MCP**；不得在本轮真实调用外部 MCP。
- 六个 MCP 已登记但 `enabled_in_project` 均为 false；当前受保护的项目配置只含 `filesystem`，运行时可用性未验证。
- **不得执行高风险 MCP 动作**；playwright 生产账号登录、支付、发布、破坏性 UI 操作等 L3 行为仍默认禁止。
- **新增 MCP** 须同时修改 `data/mcp/mcp_capability_registry.yaml`、`mcp_approval_policy.yaml`、`mcp_integration_roadmap.yaml`。
- **L2/L3 须停止并向用户确认**后再继续。
- 只有当前任务明确授权记录时才可写外部工具日志：`data/logs/automation_log.jsonl`。
- **token 仅环境变量**；禁止写入仓库。
- L0-L3 不授予权限；任何真实调用仍服从当前上级授权，L2/L3 还必须显式确认。
- **Cursor 是 MCP 宿主**；Codex 不绕过策略。

## 安全规则

- 不写 token、secret、cookie、API key。
- 不扫描 `.git`、`node_modules`、`dist`、`build`、`target`、虚拟环境、缓存、日志输出、大型媒体、模型文件、数据集或真实 `.env`。
- 外部项目默认只读；扫描策略以 `docs/05_external_project_protocol.md` 为准。
- LLM 只能提出 priority proposal，不能替用户做最终优先级决策。
- completed 与 accepted 分离：Agent 完成不等于用户验收通过。

## 推进轮 Agent 默认规则（当前 ROUND-1-1）

- 开始前必须运行 `python scripts/auto_advance_runner.py --mode check` 与 `python scripts/agent_gate.py`。
- gate/runner 输出 `continue` 时，只表示检查通过；只能在当前已有授权范围内继续。
- 输出 `warn_and_continue` 时报告 warning，并在当前授权范围内继续。
- 输出 `stop` 时必须停止。
- 没有 hard blocker 不要因偏好不完美而停。
- 可用保守默认值就使用保守默认值。
- 只有 hard blocker 才请求用户。
- 仅在当前任务授权时更新 `governance/round_state.yaml`、`data/state/current_status.yaml` 或日志。
- 涉及代码修改必须运行最小验证。
- 两次连续尝试没有进展时，先诊断并改变方法；只有真实权限、凭据、不可逆外部效果或所有者决策边界才请求用户。

## 持续推进入口

推进轮 Agent 必须使用：

```bash
python scripts/auto_advance_runner.py --mode check
```

开始前检查。

一轮完成后必须使用：

```bash
python scripts/auto_advance_runner.py --mode finalize-round
```

进行只读验证与风险报告；该命令永不暂存、commit 或 push。

如果当前上级授权允许查看下一轮草案，可以运行：

```bash
python scripts/auto_advance_runner.py --mode prepare-next
```

在标准输出中预览下一轮任务 prompt，不写 `data/codex_queue/`，也不自动调用 Codex 或 Cursor。

## 一致性规则

每轮结束前必须保证：

- `round_state` 当前轮次正确
- `current_status` 当前轮次正确
- `master roadmap` 当前轮次存在
- `round_tasks` 当前轮次存在
- 当前授权要求记录时，相关状态与日志才更新
- Git 交付由当前 Root 按上级策略单独完成，runner 不参与

## 本轮验证

完成修改前至少运行：

```bash
python scripts/check_repo.py
python scripts/check_environment.py
python scripts/round_consistency_check.py
python scripts/agent_gate.py
python scripts/auto_advance_runner.py --mode check
python scripts/bootstrap.py --dry-run
python hub.py mcp list
python hub.py mcp policy
```

## 可移植性

本文件与仓库内相对路径共同构成项目入口；不得依赖其他机器或工作区的绝对路径。更高层 system、developer、用户指令以及实际宿主权限始终优先。
