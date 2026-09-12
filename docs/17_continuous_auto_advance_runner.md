# Continuous Auto-Advance Runner

## 文档目的

本文描述 personal-control-hub 的持续推进入口脚本 `scripts/auto_advance_runner.py` 的定位、三种运行模式、与 gate 的关系，以及 soft/hard blocker 处理规则。

## 定位

`auto_advance_runner.py` 不是完整 Agent。它不自己写代码，不负责调用 Codex 或 Cursor。它负责：

1. 运行环境检查（`check_environment.py`）
2. 运行 gate（`agent_gate.py`）
3. 运行轮次一致性检查（`round_consistency_check.py`）
4. 判断是否可以进入下一轮
5. 在标准输出中预览下一轮 prompt 草案（prepare-next）
6. 在本轮 Agent 完成后执行只读验证与风险报告（finalize-round）
7. 硬阻塞出现时停止

当前状态权威：`STATE.yaml`
权威策略：`data/gates/auto_advance_policy.yaml`  
推进 prompt：`prompts/continuous_auto_advance_prompt.md`

## 三种模式

### `--mode check`

执行全部只读验证，不修改 Git。

```bash
python scripts/auto_advance_runner.py --mode check
```

输出：decision、hard_blockers、soft_warnings、current_round、next_round。

### `--mode prepare-next`

1. 先执行 check
2. 无硬阻塞时读取 `round_tasks.yaml` 中的 next_round
3. 在标准输出中分别预览 Codex 与 Cursor prompt；不写队列文件

不调用 Codex/Cursor，不自动执行下一轮。

```bash
python scripts/auto_advance_runner.py --mode prepare-next
```

### `--mode finalize-round`

用于一轮 Agent 修改完成后：

1. check_environment + agent_gate + round_consistency_check
2. check_repo.py（若存在）
3. git status、merge conflict 检查
4. 敏感文件检查
5. 报告工作区变更、冲突和敏感路径；不执行 `git add`、`git commit` 或 `git push`

```bash
python scripts/auto_advance_runner.py --mode finalize-round
```

**注意**：三个模式默认都只读。Git 交付只能由当前 Root 在上级策略与用户当前授权范围内单独执行。

## 决策规则

| 情况 | 行为 |
|---|---|
| 无硬阻塞 | continue |
| 仅软警告 | warn_and_continue |
| 硬阻塞 | stop |
| merge conflict | stop |
| 敏感文件 | stop |

软阻塞只影响检查结果：Node 缺失、MCP 需人工确认、Codex 可用性待确认等会在输出中报告；是否继续仍取决于当前已有上级授权。

硬阻塞必须停止：真实密钥、删除、外部写入、支付、登录、未解决冲突、敏感文件等。

## 敏感文件检查

commit 前阻止：

- `.env`、`.env.*`
- `*.pem`、`*.key`
- `id_rsa`、`id_ed25519`
- `secrets.*`
- 含 OPENAI_API_KEY、FEISHU_APP_SECRET、GITHUB_TOKEN 等密钥赋值内容

## 写入与日志

runner 不写日志、状态或 prompt 文件。`check_environment.py` 也默认只读；其 `--record` 仅在当前任务明确授权记录时使用。gate 的结果不构成写入授权。

## 与 agent_gate 的关系

- 任何推进轮 Agent 开始前必须先运行 gate。
- runner 的 check 模式会调用 gate，不绕过 gate。
- gate 输出 stop 时 runner 也 stop。

## 禁止事项

- 不调用真实 MCP
- 不自动调用 Codex
- 不自动调用 Cursor
- 不自动解决 git conflict
- 不读取 secret 文件内容（仅模式匹配）
- 不修改外部项目

## 推进轮 Agent 工作流

1. `python scripts/auto_advance_runner.py --mode check`
2. decision 为 continue 或 warn_and_continue → 执行当前 round
3. decision 为 stop → 停止并报告
4. 完成 round → `python scripts/auto_advance_runner.py --mode finalize-round` 做只读复核
5. 可选 `prepare-next` 预览下一轮；是否推进仍取决于当前授权与 `STATE.yaml`

详见 `prompts/continuous_auto_advance_prompt.md` 与 `AGENTS.md`。
