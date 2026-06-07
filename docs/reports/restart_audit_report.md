# Restart Audit Report

日期：2026-06-07

## 审计结论

本仓库在本轮开始时不是 Git 仓库，`git status --short`、`git branch --show-current`、`git log --oneline -n 20` 和 `git remote -v` 组合命令返回 `fatal: not a git repository`。本轮没有执行 push、checkout、reset，也没有删除历史文件。

本轮开始时，仓库主要包含 16 份参考报告和 `governance/repo_protocol_standard.yaml`。本轮已补齐 personal-control-hub 的重启骨架：入口文档、治理层、data 骨架、prompts、scripts、src/hub 服务占位和审计报告。

## 当前已有文件概览

本轮开始时观察到的文件：

- `docs/00_synthesis_for_my_ai_company_os.md`
- `docs/01_spec-kit.md`
- `docs/02_BMAD-METHOD.md`
- `docs/03_agent-skills.md`
- `docs/04_awesome-claude-code.md`
- `docs/05_awesome-claude-code-subagents.md`
- `docs/06_contains-studio-agents.md`
- `docs/07_gh-aw.md`
- `docs/08_suna.md`
- `docs/09_open-computer-use.md`
- `docs/10_OneManCompany.md`
- `docs/11_AgentHub.md`
- `docs/12_awesome-one-person-company.md`
- `docs/13_one-person-unicorn-book.md`
- `docs/14_ai-dev-operating-system.md`
- `docs/15_solo-founder-playbook.md`
- `governance/repo_protocol_standard.yaml`

本轮新增或更新的核心文件见最终执行摘要。

## 缺失文件与本轮处理

本轮开始时缺失以下关键入口和骨架，本轮已创建：

- `README.md`
- `AGENTS.md`
- `project.yaml`
- `repo_protocol_standard.yaml`
- `governance/round_state.yaml`
- `governance/agent_policy.yaml`
- `governance/file_role_map.yaml`
- `docs/00_start_here.md`
- `docs/01_project_ultimate_goal.md`
- `docs/02_master_roadmap.md`
- `docs/03_architecture.md`
- `docs/04_data_model.md`
- `docs/05_external_project_protocol.md`
- `docs/06_scheduler_design.md`
- `docs/07_integration_strategy.md`
- `docs/08_codex_cursor_workflow.md`
- `docs/09_feishu_lark_strategy.md`
- `docs/10_decision_log.md`
- `data/registry/external_projects.yaml`
- `data/programs/active_programs.yaml`
- `data/programs/program_project_links.yaml`
- `data/tasks/inbox.yaml`
- `data/tasks/next_actions.yaml`
- `data/tasks/next_program_actions.yaml`
- `data/scheduler/scheduled_tasks.yaml`
- `data/integrations/integration_targets.yaml`
- `data/state/current_status.yaml`
- `data/logs/automation_log.jsonl`
- `data/logs/project_decision_log.jsonl`
- `prompts/codex_project_driver.md`
- `prompts/cursor_project_driver.md`
- `scripts/bootstrap.py`
- `scripts/check_repo.py`
- `src/hub/*`

## 重复或可能过时文档

以下文档是重要参考报告，但不应作为当前执行入口：

- `docs/00_synthesis_for_my_ai_company_os.md`
- `docs/01_spec-kit.md`
- `docs/02_BMAD-METHOD.md`
- `docs/03_agent-skills.md`
- `docs/04_awesome-claude-code.md`
- `docs/05_awesome-claude-code-subagents.md`
- `docs/06_contains-studio-agents.md`
- `docs/07_gh-aw.md`
- `docs/08_suna.md`
- `docs/09_open-computer-use.md`
- `docs/10_OneManCompany.md`
- `docs/11_AgentHub.md`
- `docs/12_awesome-one-person-company.md`
- `docs/13_one-person-unicorn-book.md`
- `docs/14_ai-dev-operating-system.md`
- `docs/15_solo-founder-playbook.md`

建议后续用户确认后，将这些参考报告移动到 `docs/archive/reference_reports/` 或建立 `docs/reference_index.md`。本轮没有移动或删除它们。

## 协议兼容说明

`governance/repo_protocol_standard.yaml` 已存在且内容很长，包含可迁移规则，也包含旧项目特定条目。本轮没有重写该文件，只在末尾追加 `personal_control_hub_restart_overlay`，并新增根目录 `repo_protocol_standard.yaml` 作为兼容指针。后续如需清理旧项目特定条目，应先获得用户确认并做迁移报告。

## 当前最小可执行路径

1. 读取 `docs/00_start_here.md`、`AGENTS.md`、`project.yaml`。
2. 在 `data/registry/external_projects.yaml` 中由用户确认外部项目路径。
3. 用 `docs/05_external_project_protocol.md` 控制只读扫描范围。
4. 用 `data/programs/active_programs.yaml` 维护现实目标。
5. 用 `data/programs/program_project_links.yaml` 提出 proposal，用户确认后才写 confirmed links。
6. 用 `data/scheduler/scheduled_tasks.yaml` 准备每日扫描和每周复盘，不自动执行。
7. 用 `scripts/check_repo.py` 和 `scripts/bootstrap.py --dry-run` 验证骨架。

## 安全声明

本轮没有调用真实 Feishu/Lark API，没有调用真实付费 LLM API，没有写入 token、secret、cookie 或 API key，没有修改外部项目，没有删除历史文档。
