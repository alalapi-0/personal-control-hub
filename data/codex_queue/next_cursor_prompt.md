# 下一轮任务草案：ROUND-1-5

**名称**：External Project Import UX
**执行器**：Cursor

## 开始前

```bash
python scripts/auto_advance_runner.py --mode check
python scripts/agent_gate.py
```

## 目标

设计外部项目导入体验：手动登记、校验提示、缺失字段 warning 和保守默认值。

## 预期输出

- docs/05_external_project_protocol.md
- data/roadmap/round_tasks.yaml

## 验收标准

- 导入字段、默认值、不可读路径处理方式清晰。
- 缺少偏好时使用保守默认，不阻断文档推进。

## Hard Blockers

- 需要批量扫描未知目录。
- 需要覆盖用户登记内容。

## Soft Blockers

- 导入 UX 文案可后续优化。

## 完成后

```bash
python scripts/auto_advance_runner.py --mode finalize-round
```

无硬阻塞时默认继续；软阻塞只记录 warning。
