# Data Model

本仓库第一阶段使用本地文件作为数据层，不引入数据库。

## External Project

字段来源：`data/registry/external_projects.yaml`。

- `id`: 稳定 ID。
- `name`: 项目名。
- `root_path`: 本地路径。不要写不存在的真实隐私路径；示例必须 disabled。
- `enabled`: 是否纳入治理。
- `scan_enabled`: 是否允许只读扫描。
- `profile_enabled`: 是否允许生成 profile。
- `summary_enabled`: 是否允许生成 summary。
- `project_type`: 项目类型。
- `priority_link`: 关联 active program 的候选 ID。
- `priority_source`: 人类、规则或 proposal。
- `watch_paths`: 允许关注的入口路径。
- `notes`: 说明。

## Active Program

字段来源：`data/programs/active_programs.yaml`。

- `id`
- `name`
- `status`
- `why_it_matters`
- `desired_outcome`
- `time_horizon`
- `priority_band`
- `owner`
- `related_project_ids`
- `next_review`
- `notes`

## Program-Project Link

字段来源：`data/programs/program_project_links.yaml`。

- `proposals`: LLM 或 Agent 只能写入这里。
- `confirmed_links`: 只有用户确认后才能写入。
- `rejected_or_deferred`: 记录不采纳或延期原因。

## Task and Next Action

`data/tasks/inbox.yaml` 保存未整理输入。`data/tasks/next_actions.yaml` 保存项目和 program 的下一步建议。建议必须带 evidence 或 reason，不允许伪装成最终决策。

## Logs

`data/logs/automation_log.jsonl` 和 `data/logs/project_decision_log.jsonl` 是 append-only 占位。不要写入 token、secret、cookie 或外部 API 响应原文。
