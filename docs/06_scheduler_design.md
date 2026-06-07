# Scheduler Design

第一阶段的 scheduler 只是本地计划表和 prompt 准备器，不自动执行任何外部动作。

## 目标

- 记录每日项目扫描。
- 记录每周项目复盘。
- 生成待执行的 Codex/Cursor prompt 草案。
- 输出 due list 和 prepare list。

## 非目标

- 不自动执行 Codex。
- 不调用真实 Feishu/Lark。
- 不 push GitHub。
- 不远程控制。
- 不修改外部项目。

## 第一阶段命令设计

未来可以实现：

```bash
python -m hub.cli schedule list
python -m hub.cli schedule due
python -m hub.cli schedule prepare
```

当前只创建 `data/scheduler/scheduled_tasks.yaml` 占位，并由 `scripts/check_repo.py` 检查存在。

## 调度项结构

每个调度项至少包含：

- `id`
- `name`
- `enabled`
- `schedule`
- `action_type`
- `target`
- `dry_run_default`
- `requires_user_confirmation`
- `notes`
