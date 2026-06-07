# personal-control-hub

personal-control-hub 是个人项目 OS 和多项目总控入口。

GitHub: https://github.com/alalapi-0/personal-control-hub它负责管理项目、想法、任务、日志、优先级和行动建议，通过本地路径索引外部仓库，但不复制、不默认修改外部项目本体。

本仓库定位为项目控制层、索引层、计划层、调度层、通知层和多项目治理层。它不是大模型底座、完整 Agent 平台、浏览器自动操作平台、SaaS dashboard、业务仓库 monorepo 或全量扫描系统。

## 当前启动方式

1. 先读 `docs/00_start_here.md`、`project.yaml`、`AGENTS.md`。
2. 用 `data/registry/external_projects.yaml` 登记外部项目，只写本地路径和只读扫描策略。
3. 用 `data/programs/active_programs.yaml` 维护现实目标与 active programs。
4. 用 `scripts/check_repo.py` 验证核心骨架是否完整。
5. 用 `scripts/agent_gate.py` 检查自动推进 gate。
6. 用 `scripts/check_environment.py` 检查运行环境。
7. 用 `scripts/auto_advance_runner.py --mode check` 检查是否可持续推进。
8. 用 `scripts/bootstrap.py --dry-run` 查看缺失目录和占位文件，不覆盖已有内容。

```bash
python scripts/check_repo.py
python scripts/check_environment.py
python scripts/agent_gate.py
python scripts/auto_advance_runner.py --mode check
python scripts/bootstrap.py --dry-run
python hub.py mcp list
python hub.py mcp policy
```

如果根目录后续出现历史 `bootstrap.py`，保留为兼容入口；本轮新增的标准入口是 `scripts/bootstrap.py`。

## 工作环境与工具分工

当前现实环境以中国大陆本地工作为主：本地优先、Cursor 优先、Codex 关键执行、Feishu/Lark 后续通知入口。外部项目可以分散在不同本地路径，不强制同一平台，不依赖单一云服务。

- Cursor 是日常主力项目推进环境，用于常规编辑、检索、任务推进和本仓库的日常治理。
- Codex 用于更高质量的代码执行、审查、复杂修改和关键轮次推进。
- ChatGPT 用于规划、分析、Prompt 生成和外部讨论。
- 第三方 Agent 工具暂非主力，只作为后续可接入对象。
- Feishu/Lark 本轮只做策略和占位，不调用真实 API，不写 token，不真实发消息。

## 本轮边界

- 不删除历史文件。
- 不调用真实 Feishu/Lark API。
- 不调用真实付费 LLM API。
- 不写入 token、secret、cookie、API key。
- 不修改外部项目本体。
- 不自动 git push、checkout、reset。

## Roadmap 与自动推进（Round 0.7）

当前轮次：**Round 0.7 — Runtime Environment Alignment + Continuous Auto-Advance Runner**。统一运行环境、环境检查脚本、轮次一致性检查与持续推进入口 `scripts/auto_advance_runner.py`。

Round 0.6 把路线图扩写为 Restart Phase、Phase 1 本地项目总控闭环、Phase 2 UI/Web Console、Phase 3 半自动项目管理、Phase 4 高级自动化研究期。机器可读路线位于 `data/roadmap/round_tasks.yaml`，依赖图位于 `data/roadmap/round_dependencies.yaml`。

自动推进 gate 位于 `scripts/agent_gate.py` 与 `data/gates/auto_advance_policy.yaml`。持续推进入口位于 `scripts/auto_advance_runner.py`（支持 `check`、`prepare-next`、`finalize-round`）。运行环境见 `docs/16_runtime_environment.md`，runner 说明见 `docs/17_continuous_auto_advance_runner.md`。

默认规则：无 hard blocker 继续；只有 soft blocker 时 warning 后继续；需要真实密钥、删除、外部写入、登录、支付、发布、P0/P1 战略变更或 MCP L2/L3 未确认时停止。`finalize-round` 在 push 失败、merge conflict 或敏感文件检测时必须停止。

UI/Web Console 当前不是第一优先级，但已纳入 `docs/14_ui_console_plan.md` 和 Round 9-11.5 路线。

## MCP 工作区（Round 0.5）

Cursor 是 MCP 宿主；本仓库登记能力矩阵与 L0-L3 审批策略，**本轮不安装、不调用真实 MCP**。

- 战略：`docs/11_mcp_infrastructure_strategy.md`
- 审批：`docs/12_external_tool_approval_model.md`
- 配置：`docs/13_cursor_mcp_workspace_setup.md`、`.cursor/mcp.example.json`
- 数据：`data/mcp/mcp_capability_registry.yaml` 等四个 YAML
- 只读 CLI：`python3 hub.py mcp list`、`python3 hub.py mcp policy`

六个 MCP（chrome-devtools、context7、filesystem、github、playwright、stitch）均已登记，`enabled_in_project` 默认 true，含义是 default start / 可被 Cursor 工作区启用；L2/L3 具体动作仍必须按审批策略确认。

## 主要目录

- `governance/`: Agent 策略、Round 状态、文件角色和 repo protocol。
- `docs/`: 人类可读总纲、路线图、架构、协议、MCP 策略、报告和归档说明。
- `data/`: 外部项目注册表、active programs、任务、调度、集成、**MCP 登记**、状态和日志。
- `data/mcp/`: MCP 能力矩阵、审批策略、路线图与示例服务器配置。
- `data/roadmap/`: 机器可读 round tasks 与依赖关系。
- `data/gates/`: 自动推进策略与 gate checklist。
- `data/runtime/`: 运行环境 requirements、toolchain 状态与验证命令。
- `prompts/`: Codex/Cursor 驱动提示词、MCP 审计、持续推进 prompt。
- `.cursor/`: Cursor MCP 示例配置（不覆盖用户已有 `mcp.json`）。
- `scripts/`: 骨架检查、环境检查、轮次一致性检查、auto advance runner 与启动辅助脚本。
- `src/hub/`: hub 服务与只读 MCP CLI。
- `tests/`: 后续自动化测试占位。

## 当前最小可执行路径

Round 0 完成治理骨架；**Round 0.5** 完成 MCP 登记与策略。Round 1-3 再建立外部项目注册、扫描和 profile/snapshot。任何外部写入、真实 MCP 调用、真实飞书调用、付费模型调用、远程控制和 GitHub push 都必须人工确认后才允许进入后续轮次。
