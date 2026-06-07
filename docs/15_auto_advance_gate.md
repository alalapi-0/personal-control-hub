# Auto-Advance Gate

## 文档目的

本文定义 personal-control-hub 的自动推进门禁：什么时候可以继续，什么时候 warning 后继续，什么时候必须停下并请求用户确认。权威机器可读策略在 `data/gates/auto_advance_policy.yaml`，本地检查脚本为 `scripts/agent_gate.py`。

## 为什么需要 Gate

personal-control-hub 会逐步从文档治理进入外部项目扫描、调度、UI、通知和半自动 Agent Runner。如果没有 gate，Agent 容易因为偏好不完美而停下，也可能在真实外部写入、密钥、登录、删除、发布等高风险场景中继续。Gate 的目标是把两件事分开：

- 无硬阻塞时默认继续推进。
- 触及安全、外部写入、真实密钥、发布、登录、支付、P0/P1 战略变更时必须停止。

## 默认推进原则

- 没有 hard blocker：继续。
- 只有 soft blocker：记录 warning 后继续。
- 缺少可选输入：使用安全默认值继续。
- 缺少必需密钥：停止。
- 需要删除文件、覆盖用户内容或外部写入：停止。
- 需要真实外部 API、真实 MCP L2/L3、登录、支付、发布：停止。
- 需要用户偏好但不影响安全：使用保守默认值继续。

## Hard Blocker

Hard blocker 是不能自动越过的边界。一旦出现，Agent 必须停止并向用户说明原因。

当前 hard blocker 包括：

- 需要真实 API key、真实密码、真实 cookie。
- 写入外部项目。
- 删除文件或覆盖用户内容。
- git push。
- 发布内容。
- 登录账号。
- 支付或购买。
- 改变 P0/P1 战略优先级。
- 测试连续失败两次。
- 路线图冲突无法自动判断。
- MCP L2/L3 未获确认。

## Soft Blocker

Soft blocker 是需要记录但不应该阻断推进的问题。Agent 应使用保守默认值继续，并把 warning 留给后续轮次或用户验收。

当前 soft blocker 包括：

- UI 美术风格未确定。
- 文案可后续优化。
- 缺少真实飞书 webhook。
- 缺少真实 GitHub token。
- LLM 不可用但可用 mock。
- 外部项目暂无更新。
- future round 不够细。
- 文档可读性可优化。

## agent_gate.py 工作方式

`scripts/agent_gate.py` 是本地只读脚本，不写文件、不调用外部 API、不调用真实 MCP、不 git push、不删除文件。

检查内容：

- 核心文件是否存在。
- `data/roadmap/round_tasks.yaml` 是否存在。
- 每个 active/planned round 是否有 `id`、`name`、`status`、`goal`、`acceptance_criteria`、`can_auto_advance`、`hard_blockers`。
- `data/gates/auto_advance_policy.yaml` 是否存在且默认行为合理。
- `data/mcp/mcp_capability_registry.yaml` 是否有明显结构问题。
- 本轮相关 YAML/MD/JSON/PY 文件中是否出现疑似真实 token 字符串；不会扫描 `.git`、`node_modules`、构建目录、虚拟环境或真实 `.env`。
- 指定 `--round ROUND-1` 时，判断该 round 的 `can_auto_advance` 和 next_round。

命令：

```bash
python3 scripts/agent_gate.py
python3 scripts/agent_gate.py --round ROUND-1
python3 scripts/agent_gate.py --json
```

决策：

- 有 hard blocker：`stop`。
- 只有 soft warning：`warn_and_continue`。
- 无问题：`continue`。

## completed vs accepted

`completed` 表示 Agent 完成了任务并提供证据。`accepted` 表示用户或明确 Gate 验收通过。

两者必须分离：

- 没有 accepted 不一定阻止下一轮。
- 文档、配置、mock、只读扫描可以在 completed 后默认继续。
- 安全、外部写入、真实 API、登录、发布、删除、P0/P1 战略变更必须等待 accepted 或用户明确确认。

## 推进轮 Agent 如何使用 Gate 与 Runner

每个推进轮 Agent 开始前必须运行：

```bash
python scripts/auto_advance_runner.py --mode check
python scripts/agent_gate.py
```

一轮完成后（用户确认 push 策略后）：

```bash
python scripts/auto_advance_runner.py --mode finalize-round
```

finalize 成功后可生成下一轮 prompt：

```bash
python scripts/auto_advance_runner.py --mode prepare-next
```

如果是检查特定轮次：

```bash
python scripts/agent_gate.py --round ROUND-1
```

执行规则：

- `continue`：继续当前轮或下一轮。
- `warn_and_continue`：记录 warning，使用保守默认值继续；软阻塞默认继续。
- `stop`：停止，不执行触发阻塞的动作，向用户请求确认。

不要绕过 gate 或 runner。push 失败、merge conflict、敏感文件检测必须 stop。

推进完成后必须更新：

- `governance/round_state.yaml`
- `data/state/current_status.yaml`
- `data/logs/automation_log.jsonl`
- `data/logs/auto_advance_log.jsonl`（runner 执行时自动追加）

涉及代码修改时必须运行最小验证；验证连续失败两次停止。

运行环境与一致性检查：

```bash
python scripts/check_environment.py
python scripts/round_consistency_check.py
```

## 何时自动继续

以下情况可以默认继续：

- 文档扩写、YAML 骨架、mock adapter、只读扫描设计。
- 缺少 UI 美术偏好。
- 缺少非必需 webhook/token，但可以用 mock。
- future round 仍需更细拆分。
- 外部项目暂无更新。
- 文案、可读性、展示顺序有优化空间。

## 何时必须停下

以下情况必须停下：

- 需要真实密钥、密码、cookie。
- 需要登录账号。
- 需要支付、购买或发布。
- 需要删除文件或覆盖用户内容。
- 需要修改外部项目。
- 需要 git push、checkout/reset 等高风险 git 操作。
- 需要真实 Feishu/Lark API 或真实外部写入。
- 需要 MCP L2/L3 但未获确认。
- 需要改变 P0/P1 战略优先级。
- 测试连续失败两次。

## 日志记录

自动推进相关动作写入 `data/logs/automation_log.jsonl`，至少记录：

- timestamp
- round_id
- decision
- hard_blockers
- soft_warnings
- validation
- external_api_called
- external_project_written
- secrets_written

日志默认追加，不删除、不篡改历史。若出现 stop，日志应保留停下原因和下一步建议。

## 后续 CI / GitHub Actions

后续可以把 `scripts/agent_gate.py --json` 接入 CI 或 GitHub Actions，但必须遵守当前边界：

- GitHub 只读检查可以规划，写操作为 L3。
- CI 失败可阻止合并，但不能自动 push 修复。
- 真实 secrets 只来自环境变量或平台 secret store，不写入仓库。
- CI 中的 gate 结果应同步回 status 或日志草案，再由用户验收。
