# Auto-Advance Gate

## 文档目的

本文定义 personal-control-hub 的推进检查门禁：哪些条件通过、哪些产生 warning、哪些必须阻断。gate 只报告检查结果，不授予推进、写入、日志、Git 或外部动作权限。权威机器可读策略在 `data/gates/auto_advance_policy.yaml`，本地检查脚本为 `scripts/agent_gate.py`。

## 为什么需要 Gate

personal-control-hub 会逐步从文档治理进入外部项目扫描、调度、UI、通知和半自动 Agent Runner。如果没有 gate，Agent 容易因为偏好不完美而停下，也可能在真实外部写入、密钥、登录、删除、发布等高风险场景中继续。Gate 的目标是把两件事分开：

- 无硬阻塞时检查通过；是否推进仍取决于当前已有上级授权。
- 触及安全、外部写入、真实密钥、发布、登录、支付、P0/P1 战略变更时必须停止。

## 默认推进原则

- 没有 hard blocker：检查通过，不授予动作权限。
- 只有 soft blocker：在回复中报告 warning，并只在当前已有授权范围内继续。
- 缺少可选输入：检查可通过；获授权执行时使用安全默认值。
- 缺少必需密钥：停止。
- 需要删除文件、覆盖用户内容或外部写入：停止。
- 需要真实外部 API、真实 MCP L2/L3、登录、支付、发布：停止。
- 需要用户偏好但不影响安全：检查可通过；获授权执行时使用保守默认值。

## Hard Blocker

Hard blocker 是不能自动越过的边界。一旦出现，Agent 必须停止并向用户说明原因。

当前 hard blocker 包括：

- 需要真实 API key、真实密码、真实 cookie。
- 写入外部项目。
- 删除文件或覆盖用户内容。
- 未获当前或已记录所有者授权的 git push。
- 发布内容。
- 登录账号。
- 支付或购买。
- 改变 P0/P1 战略优先级。
- 连续两次无进展后仍未诊断或改变方法。
- 路线图冲突无法自动判断。
- MCP L2/L3 未获确认。

## Soft Blocker

Soft blocker 是应在输出中报告但不使检查失败的问题。它不授予推进或日志权限；Agent 只有在当前已有授权范围内才可使用保守默认值继续。

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
- completed 后仍只能在当前已有上级授权范围内继续。
- 安全、外部写入、真实 API、登录、发布、删除、P0/P1 战略变更必须等待 accepted 或用户明确确认。

## 推进轮 Agent 如何使用 Gate 与 Runner

每个推进轮 Agent 开始前必须运行：

```bash
python scripts/auto_advance_runner.py --mode check
python scripts/agent_gate.py
```

一轮完成后做只读复核：

```bash
python scripts/auto_advance_runner.py --mode finalize-round
```

当前上级授权允许查看下一轮草案时可做只读预览：

```bash
python scripts/auto_advance_runner.py --mode prepare-next
```

如果是检查特定轮次：

```bash
python scripts/agent_gate.py --round ROUND-1
```

执行规则：

- `continue`：检查通过；不授予动作权限。
- `warn_and_continue`：检查通过但有 warning；在输出中报告，不授予动作权限。
- `stop`：停止，不执行触发阻塞的动作，向用户请求确认。

不要绕过 gate 或 runner。push 失败、merge conflict、敏感文件检测必须 stop。

只有当前任务明确授权且当前事实改变时才更新：

- `STATE.yaml`（唯一当前状态权威）
- `data/logs/automation_log.jsonl`
- `data/logs/auto_advance_log.jsonl`（runner 默认不追加）

涉及代码修改时必须运行最小验证；连续两次无进展时先诊断并改变方法。

运行环境与一致性检查：

```bash
python scripts/check_environment.py
python scripts/round_consistency_check.py
```

## 检查通过但仍需当前授权的情况

以下情况本身不构成 hard blocker，但不授予推进权限：

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
- 需要未获当前或已记录所有者授权的 git push，或 checkout/reset 等未授权 Git 操作。
- 需要真实 Feishu/Lark API 或真实外部写入。
- 需要 MCP L2/L3 但未获确认。
- 需要改变 P0/P1 战略优先级。
- 连续两次无进展后仍未诊断或改变方法。

## 日志记录

只有当前任务明确授权记录时，推进相关动作才写入 `data/logs/automation_log.jsonl`，至少记录：

- timestamp
- round_id
- decision
- hard_blockers
- soft_warnings
- validation
- external_api_called
- external_project_written
- secrets_written

日志默认不写；获授权记录时只能追加，不删除、不篡改历史。若出现 stop，只在授权包含记录时保存停下原因和下一步建议。

## 后续 CI / GitHub Actions

后续可以把 `scripts/agent_gate.py --json` 接入 CI 或 GitHub Actions，但必须遵守当前边界：

- GitHub 只读检查可以规划，写操作为 L3。
- CI 失败可阻止合并，但不能自动 push 修复。
- 真实 secrets 只来自环境变量或平台 secret store，不写入仓库。
- CI 中的 gate 结果应同步回 status 或日志草案，再由用户验收。
