# START HERE

这是 personal-control-hub 的当前入口。

## 一句话定位

personal-control-hub 是个人项目 OS 和多项目总控入口：管理项目、想法、任务、日志、优先级和行动建议，通过本地路径接入外部仓库，只读提取主要状态，再生成 profile、snapshot、priority suggestion 和 next actions。Cursor 作为 MCP 宿主，本仓库登记 MCP 能力与审批策略。

## 先读什么

1. `README.md`: 项目定位和启动方式。
2. `project.yaml`: 机器可读身份卡。
3. `AGENTS.md`: Cursor/Codex 的阅读顺序和权限边界（含 MCP 规则）。
4. `docs/01_project_ultimate_goal.md`: 终极目标与明确不做什么。
5. `docs/02_master_roadmap.md`: Restart Phase、Phase 1-4 与 Round 0-17（含 0.5 与 .5 子轮次）。
6. `docs/11_mcp_infrastructure_strategy.md`: MCP 基础设施战略。
7. `docs/12_external_tool_approval_model.md`: L0-L3 审批模型。
8. `docs/14_ui_console_plan.md`: UI/Web Console 路线与页面边界。
9. `docs/15_auto_advance_gate.md`: 自动推进 gate、hard/soft blocker 和 completed/accepted 规则。
10. `docs/16_runtime_environment.md`: 运行环境要求与检查命令。
11. `docs/17_continuous_auto_advance_runner.md`: 持续推进入口脚本说明。
12. `governance/agent_policy.yaml`: 自动权限与必须确认事项。
13. `data/programs/active_programs.yaml`: 当前现实目标矩阵。

## 当前现实背景

工作环境主要在中国大陆，以本地优先、Cursor 优先、Codex 关键执行、Feishu/Lark 后续通知入口为原则。外部项目分散在本地路径，不强制同一平台，不依赖单一云服务。

Cursor 是日常主力项目推进环境与 **MCP 宿主**；Codex 用于更高质量的代码执行、审查、复杂修改和关键轮次推进，且不得绕过 MCP 策略；ChatGPT 用于规划、分析、Prompt 生成和外部讨论。

## 本轮状态

当前是 **Phase 1 / ROUND-1-1: Registry Runtime Validation**。权威状态见 `governance/round_state.yaml` 与 `data/state/current_status.yaml`。

runner 的三个模式默认都只读；gate 结果不授予写入或外部动作权限。当前仍不安装或真实调用 MCP、不写真实 token；Git 交付只能由当前 Root 按上级策略单独执行。

## 最小命令

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
