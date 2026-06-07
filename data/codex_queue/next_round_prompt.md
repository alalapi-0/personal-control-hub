# 下一轮任务草案：ROUND-1

**名称**：External Project Registry MVP
**执行器**：Codex

## 开始前

```bash
python scripts/auto_advance_runner.py --mode check
python scripts/agent_gate.py
```

## 目标

建立外部项目登记 MVP，只记录路径、读取策略、扫描开关和安全边界。

## 预期输出

- data/registry/external_projects.yaml
- data/logs/automation_log.jsonl

## 验收标准

- 外部项目 registry 字段稳定。
- 只读策略、禁止目录、写入禁止项清晰。
- 不修改外部项目本体。

## Hard Blockers

- 需要修改外部项目。
- 需要读取真实 .env 或密钥。

## Soft Blockers

- 外部项目暂无更新。
- 部分项目路径待用户补充。

## 完成后

```bash
python scripts/auto_advance_runner.py --mode finalize-round
```

无硬阻塞时默认继续；软阻塞只记录 warning。
