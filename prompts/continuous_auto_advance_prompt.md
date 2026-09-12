# Continuous Auto-Advance Prompt

默认上下文只有 `AGENTS.md` 与 `STATE.yaml`。按 `AGENTS.md` 路由到当前任务材料，不读取完整路线图、长协议或历史报告。

开始：

```bash
python3 scripts/auto_advance_runner.py --mode check
python3 scripts/agent_gate.py
```

`continue` / `warn_and_continue` 仅是检查结果；`stop` 阻断相应动作。真实凭据、未授权外部写入/删除/发布/登录/支付、P0/P1 决策、未授权 Git 与 MCP L2/L3 是边界。

完成：

```bash
python3 scripts/check_environment.py
python3 scripts/round_consistency_check.py
python3 scripts/check_registry.py
python3 scripts/check_repo.py
python3 scripts/auto_advance_runner.py --mode finalize-round
```

只更新 `STATE.yaml` 中真实改变的当前事实；runner 不 commit/push。Git 交付由 Root 按有效所有者授权单独执行。
