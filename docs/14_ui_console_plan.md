# UI Console Plan

## 文档目的

本文定义 personal-control-hub 的 UI / Web Console 路线：先把信息架构、数据来源、允许操作、禁止操作和测试边界写清楚，再进入静态原型和后续实现。本轮只规划，不直接实现完整 UI。

## 为什么需要 UI 控制台

personal-control-hub 的核心信息分散在 roadmap、round state、external project registry、programs、scheduler、MCP registry、logs 和 gate policy 中。UI 控制台的价值是把这些治理文件转为可快速理解、可人工确认、可追踪证据的工作台。

UI 控制台服务于三类动作：

- 看清当前项目 OS 的状态：当前轮次、下一轮、风险、验证结果、active programs。
- 承接人工确认：hard blocker、P0/P1 变更、外部写入、真实集成、MCP L2/L3。
- 辅助自动推进：显示 agent_gate.py 决策、soft warning、默认继续策略和队列状态。

## UI 优先级

UI 当前不是第一优先级。Restart Phase 和 Phase 1 的重点仍是治理骨架、外部项目只读扫描、profile、snapshot、scheduler 和 gate。UI 必须纳入路线，是因为后续自动推进、人工确认和多项目治理需要稳定的可视化入口。

当前路线约束：

- Round 9-11.5 进入 UI / Web Console 可视化闭环。
- 本轮只写计划、数据契约和验收门禁。
- 不直接实现完整 UI，不接真实外部 API，不写外部项目。

## 页面信息架构

### Dashboard 总览页

显示内容：

- 当前 round、phase、next_round、健康状态。
- 最近验证命令结果。
- hard blocker / soft warning 数量。
- active programs 摘要。
- MCP 登记数量、启用数量和真实调用状态。

数据来源：

- `governance/round_state.yaml`
- `data/state/current_status.yaml`
- `data/gates/auto_advance_policy.yaml`
- `data/logs/automation_log.jsonl`
- `data/programs/active_programs.yaml`

允许操作：

- 查看 gate 摘要。
- 打开对应 roadmap、status、log 文件。
- 生成下一轮建议草案。

禁止操作：

- 直接确认 P0/P1 战略变更。
- 直接触发外部写入、真实 API、git push 或真实 Feishu 发送。
- 读取或展示真实 token、cookie、password。

### Active Programs 页

显示内容：

- 当前 active programs、目标、状态、优先级建议。
- program 与 external project 的 proposal / confirmed link。
- next actions 和复盘备注。

数据来源：

- `data/programs/active_programs.yaml`
- `data/programs/program_project_links.yaml`
- `data/tasks/next_actions.yaml`
- `data/logs/project_decision_log.jsonl`

允许操作：

- 新增或编辑 proposal 草案。
- 查看 linked project snapshot。
- 标记需要用户确认的问题。

禁止操作：

- 让 Agent 直接把 priority suggestion 写成最终决策。
- 未确认时修改 P0/P1。
- 直接修改外部项目。

### External Projects 页

显示内容：

- 外部项目登记列表、本地路径、读取策略、扫描开关。
- 最近扫描状态和 dirty detection 摘要。
- 禁止扫描目录提示。

数据来源：

- `data/registry/external_projects.yaml`
- `data/project_scans/`
- `docs/05_external_project_protocol.md`

允许操作：

- 增加外部项目登记草案。
- 校验路径是否存在。
- 查看只读扫描摘要。

禁止操作：

- 自动全量扫描未知目录。
- 读取 `.env`、密钥、缓存、大型媒体、数据集。
- 修改外部项目本体。

### Project Detail 页

显示内容：

- 单个项目 profile、snapshot、风险、证据、next actions。
- README / roadmap / TODO / git 摘要的证据引用。
- 与 active programs 的关联。

数据来源：

- `data/project_profiles/`
- `data/project_snapshots/`
- `data/project_scans/`
- `data/programs/program_project_links.yaml`

允许操作：

- 查看证据链。
- 生成 profile 或 snapshot 更新草案。
- 创建 next action proposal。

禁止操作：

- 直接修复外部项目代码。
- 自动 checkout/reset 外部仓库。
- 使用真实付费 API 补全信息，除非用户确认。

### Roadmap 页

显示内容：

- Restart Phase、Phase 1-4 的 round 列表。
- 每轮 status、goal、acceptance criteria、can_auto_advance、hard blockers、next_round。
- dependencies requires / unlocks 图。

数据来源：

- `docs/02_master_roadmap.md`
- `data/roadmap/round_tasks.yaml`
- `data/roadmap/round_dependencies.yaml`
- `governance/round_state.yaml`

允许操作：

- 查看 round 详情。
- 生成 roadmap follow-up task 草案。
- 对未完成轮次提出拆分建议。

禁止操作：

- 大改已完成轮次历史。
- 删除已有路线。
- 绕过 acceptance criteria 推进。

### Gate Status 页

显示内容：

- `agent_gate.py` 最近一次决策：continue、warn_and_continue、stop。
- hard blockers、soft warnings、next_round、can_auto_advance。
- gate checklist 状态。

数据来源：

- `scripts/agent_gate.py`
- `data/gates/auto_advance_policy.yaml`
- `data/gates/gate_checklist.yaml`
- `data/logs/automation_log.jsonl`

允许操作：

- 本地运行 gate 检查。
- 查看阻塞原因和建议。
- 将 soft warning 记录为 follow-up task。

禁止操作：

- 忽略 stop 决策继续推进。
- 把 soft warning 升级为必须停下，除非触及安全边界。
- 在 UI 中输入真实密钥。

### MCP Registry 页

显示内容：

- 六个候选 MCP：chrome-devtools、context7、filesystem、github、playwright、stitch。
- approval level、enabled_in_project、allowed_scope、forbidden_scope、planned_round。
- L0-L3 审批规则。

数据来源：

- `data/mcp/mcp_capability_registry.yaml`
- `data/mcp/mcp_approval_policy.yaml`
- `data/mcp/mcp_integration_roadmap.yaml`
- `docs/11_mcp_infrastructure_strategy.md`
- `docs/12_external_tool_approval_model.md`

允许操作：

- 查看登记状态。
- 生成 MCP 审计草案。
- 准备用户确认说明。

禁止操作：

- 自动安装 MCP。
- 未确认调用 L2/L3 MCP。
- 写入真实 token 或覆盖 `.cursor/mcp.json`。

### Scheduler 页

显示内容：

- scheduled tasks 列表、schedule、action_type、target、confirmation、dry-run。
- auto advance gate check 与 roadmap validation 任务。

数据来源：

- `data/scheduler/scheduled_tasks.yaml`
- `docs/06_scheduler_design.md`
- `data/gates/auto_advance_policy.yaml`

允许操作：

- 查看任务是否启用。
- 生成 dry-run prompt。
- 手动准备 gate check。

禁止操作：

- 默认无人值守执行外部 API。
- 默认真实发送通知。
- 默认运行高风险浏览器自动化。

### Logs 页

显示内容：

- automation_log、project_decision_log。
- 每轮完成证据、验证命令、warning、stop 原因。
- 外部 API、外部项目写入、secrets_written 标记。

数据来源：

- `data/logs/automation_log.jsonl`
- `data/logs/project_decision_log.jsonl`

允许操作：

- 查看日志。
- 过滤 round、decision、warning。
- 生成 postmortem 草案。

禁止操作：

- 篡改历史日志。
- 删除日志。
- 隐藏验证失败。

### Settings 页

显示内容：

- 当前 agent policy、auto advance policy、MCP policy、文件角色。
- 默认推进行为、hard/soft blocker 列表。
- completed vs accepted 规则。

数据来源：

- `governance/agent_policy.yaml`
- `governance/file_role_map.yaml`
- `data/gates/auto_advance_policy.yaml`
- `data/mcp/mcp_approval_policy.yaml`

允许操作：

- 查看策略。
- 生成策略变更草案。
- 标记需要用户确认的设置项。

禁止操作：

- 直接写真实 `.env`。
- 直接改变技术栈。
- 未确认改变 P0/P1 战略优先级。

## UI 相关 Round 列表

- `ROUND-9`: UI Information Architecture。
- `ROUND-9-5`: UI Data Contract。
- `ROUND-10`: Static UI Prototype。
- `ROUND-10-5`: Stitch UI Concept Round。
- `ROUND-11`: Browser Test Adapter Planning。
- `ROUND-11-5`: UI Acceptance Gate。

## MCP 与 UI 测试关系

MCP 不替代 UI 本身，也不替代用户确认。MCP 只作为设计、诊断、测试和上下文增强工具纳入治理。

- Context7：可为 UI 技术栈或组件库提供公开文档上下文，默认 L0。
- Stitch：可辅助 UI 概念草图，属于 L2；未确认前不真实调用，不写外部项目。
- Chrome DevTools：可用于页面结构、网络和性能诊断，属于 L2；需要确认后使用。
- Playwright：端到端自动化，属于 L3；默认禁止，不能自动登录真实账号或执行高风险点击链。
- GitHub：只读证据可服务 UI 展示，写操作仍为 L3 禁止。
- Filesystem：只在白名单路径内读写本仓库治理文件，不能读取密钥或写外部项目。

## Stitch / Playwright / Chrome DevTools 使用边界

Stitch：

- 允许：生成 UI 草案、线框、组件概念说明，输出到本仓库文档或明确临时 mock。
- 禁止：直接写外部业务仓库 UI 代码、上传敏感截图、绕过审批提交 PR。

Chrome DevTools：

- 允许：在用户确认后检查本地或测试页面的 DOM、网络、性能和截图。
- 禁止：自动登录、填写凭据、修改生产页面状态、下载隐私数据。

Playwright：

- 允许：在明确测试环境和用户批准后执行受控 E2E。
- 禁止：生产环境自动操作、真实账号自动登录、无人值守高风险点击链、绕过 L3 审批。
