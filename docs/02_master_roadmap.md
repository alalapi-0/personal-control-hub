# Master Roadmap

当前权威状态只读 `STATE.yaml`。本文是按需路线与历史材料；表格中旧的 `governance/round_state.yaml` / `data/state/current_status.yaml` 路径均按 v2 解释为 `STATE.yaml`，不再形成第二份当前状态。

## Restart Phase（重启阶段）

建立项目身份、治理协议、数据骨架、入口文档、最小验证脚本，以及 **Round 0.5 MCP 工作区基础设施**。

| Round | 名称 | 目标 | 验收线 |
|---|---|---|---|
| 0 | Restart Audit | 审计旧结构并建立重启骨架 | 入口、治理、data、prompts、scripts 存在 |
| **0.5** | **Cursor MCP Workspace Infrastructure** | MCP 能力矩阵、L0-L3 审批、Cursor 配置示例 | 六 MCP 登记、check_repo MCP 检查通过、无真实 token |
| 1 | Core Governance Validation | 更严格协议校验 | 检查脚本覆盖治理与 MCP 文件 |

## Phase 1: Restart Foundation（重启基础）

在 Round 0/0.5 骨架之上，完善治理验证、数据模型与外部项目登记 MVP。本阶段引入 **子轮次（.5）** 用于 MCP 与集成的渐进试点。

| Round | 名称 | 备注 |
|---|---|---|
| 2 | Data Model Foundation | registry/program/task/scheduler/integration schema |
| 3 | External Project Registry MVP | 登记路径与扫描开关 |
| **3.5** | **Context7 Docs Context Pilot** | L0 文档上下文试点（需当前授权） |
| 4 | Project Scan MVP | 只读扫描入口与 git/TODO |
| **4.5** | **MCP Registry Audit Loop** | SCHED-MCP-REGISTRY-AUDIT 与 hub mcp CLI |
| 5 | Project Profile MVP | profile 与 evidence |
| 6 | Project Snapshot MVP | 快照与风险 |
| **6.5** | **GitHub MCP Read Pilot** | L2 只读，写操作仍 L3 禁止 |
| 7 | Program-Project Link MVP | proposal 分层 |
| **7.5** | **Filesystem MCP Whitelist** | L1 路径白名单 |
| 8 | Scheduler Foundation | schedule list、due、prepare |
| **8.5** | **Chrome DevTools Browser Debug** | L2 浏览器诊断 |
| 9 | Feishu/Lark Mock Integration | mock adapter |
| **9.5** | **Stitch UI Exploration** | L2 UI 草案，不写外部 repo |
| 10 | Codex/Cursor Prompt Queue | prompt 队列含 MCP policy |
| **10.5** | **Browser Test Review Scheduler** | SCHED-BROWSER-TEST-REVIEW |
| 12 | MCP Production Readiness Review | 全量 MCP 复审与用户验收 |

## Phase 2: External Project Intelligence

实现外部项目注册、只读扫描、profile、snapshot、TODO/Roadmap/git 摘要和 next actions 草案。MCP（Context7、GitHub 只读）可作为扫描证据补充，须遵守 L0-L2。

## Phase 3: Program Governance Loop

把 active programs 与外部项目链接起来，维护 proposal/confirmed link 分层、优先级建议、每周复盘和决策日志。Round 10/10.5 将 MCP 启用状态纳入 program 复盘。

## Phase 4: Notification and Execution Queue

实现调度准备、Feishu/Lark mock adapter、Codex/Cursor prompt queue 和受控通知。Round 9.5/12 衔接 MCP 与通知/mock 桥接。真实 API、push、外部写入、L3 MCP 必须人工确认。

## MCP 专项路线图

权威机器可读版本：`data/mcp/mcp_integration_roadmap.yaml`。

**登记候选（registry 默认 disabled）**：

- context7（L0）
- filesystem（L1）
- github 只读（L2）
- chrome-devtools（L2）
- stitch（L2）
- playwright（L3，默认 disabled，高风险动作需显式批准）

受保护的当前项目配置只含 filesystem，运行时可用性未验证。登记、配置、运行时和动作授权分别判断。

## Round 0-12 总表（精简）

| Round | 名称 | 目标 |
|---|---|---|
| 0 | Restart Audit | 重启骨架 |
| 0.5 | MCP Workspace Infrastructure | MCP 登记与策略 |
| 1 | Core Governance Validation | 协议校验 |
| 2 | Data Model Foundation | 数据 schema |
| 3 | External Project Registry MVP | 外部项目登记 |
| 3.5 | Context7 Pilot | 文档上下文 |
| 4 | Project Scan MVP | 只读扫描 |
| 4.5 | MCP Audit Loop | 审计调度 |
| 5 | Project Profile MVP | profile |
| 6 | Project Snapshot MVP | snapshot |
| 6.5 | GitHub Read Pilot | GitHub 只读 |
| 7 | Program-Project Link MVP | 链接 proposal |
| 7.5 | Filesystem Whitelist | 路径白名单 |
| 8 | Scheduler Foundation | 调度基础 |
| 8.5 | Chrome DevTools Debug | 浏览器调试 |
| 9 | Feishu Mock | mock 集成 |
| 9.5 | Stitch UI | UI 探索 |
| 10 | Prompt Queue | Codex/Cursor 队列 |
| 10.5 | Browser Test Review | 浏览器复盘 |
| 12 | MCP Readiness Review | MCP 投产复审 |

## 路线图原则

- 每一轮都有 spec、plan、tasks、acceptance criteria 和验证证据。
- Agent completed 不等于用户 accepted。
- MCP 候选默认 disabled；L0-L3 只分类风险，任何写入与真实调用须有当前授权。
- 外部项目优先产生 proposal，不直接修改。
- 新增 MCP 须同时更新 registry、policy、roadmap。
- Cursor 是 MCP 宿主；Codex 不得绕过 MCP 策略。
- 现实收益优先：围绕 active programs 评估 MVP、validation、growth 和风险。

## Round 0.6 结构化扩写

本节为 Round 0.6 追加的权威路线扩写，不删除上方既有路线。机器可读版本见 `data/roadmap/round_tasks.yaml`，依赖图见 `data/roadmap/round_dependencies.yaml`。

### Restart Phase：重启与治理期

| round id | name | status | goal | inputs | outputs | acceptance criteria | can_auto_advance | hard_blockers | next_round |
|---|---|---|---|---|---|---|---|---|---|
| ROUND-0 | Restart Audit and Core Skeleton | completed_pending_user_acceptance | 完成项目重启骨架 | README.md; project.yaml; AGENTS.md | docs/reports/restart_audit_report.md; governance/round_state.yaml; scripts/check_repo.py | 核心入口、治理、data、prompts、scripts 存在；check_repo.py 可运行；completed/accepted 分离 | false | 重启核心文件大部分缺失；需要删除历史文件 | ROUND-0-5 |
| ROUND-0-5 | Cursor MCP Workspace Infrastructure | completed_pending_user_acceptance | 纳入六个 MCP，建立能力矩阵与审批策略 | docs/11_mcp_infrastructure_strategy.md; docs/12_external_tool_approval_model.md; docs/13_cursor_mcp_workspace_setup.md | data/mcp/mcp_capability_registry.yaml; data/mcp/mcp_approval_policy.yaml; data/mcp/mcp_integration_roadmap.yaml | 六 MCP 登记；enabled_in_project false；不安装不调用真实 MCP；不写 token | false | 需要启用 L2/L3 MCP 未确认；需要写真实 token | ROUND-0-6 |
| ROUND-0-6 | Roadmap Expansion + UI Console Plan + Auto-Advance Gate | completed_pending_user_acceptance | 扩写路线图、规划 UI、建立自动推进门禁 | docs/02_master_roadmap.md; governance/round_state.yaml | data/roadmap/round_tasks.yaml; data/gates/auto_advance_policy.yaml; scripts/agent_gate.py | master roadmap 已扩写；UI 相关轮次已加入；agent_gate.py 可运行；hard blocker 已定义 | false | 需要删除用户文件；需要真实密钥；需要调用真实外部 API | ROUND-0-7 |
| ROUND-0-7 | Runtime Environment Alignment + Continuous Auto-Advance Runner | completed_pending_user_acceptance | 统一运行环境、环境检查、轮次一致性检查、auto advance runner | governance/round_state.yaml; data/gates/auto_advance_policy.yaml | docs/16_runtime_environment.md; scripts/auto_advance_runner.py; data/runtime/ | 环境检查可运行；runner 支持 check/prepare-next/finalize-round；状态一致 | false | 真实密钥；敏感文件；push 认证失败 | ROUND-0-8 |
| ROUND-0-8 | Runner Dry Run and Failure Simulation | completed_pending_user_acceptance | 测试 runner 各模式、模拟 hard/soft blocker、敏感文件拦截；不真实 push | scripts/auto_advance_runner.py | scripts/runner_dry_run_test.py; data/logs/auto_advance_log.jsonl | check/prepare-next 可运行；拦截已验证；不真实 push | true | 未确认的真实 push | ROUND-0-9 |
| ROUND-0-9 | GitHub Push Workflow Validation | completed_pending_user_acceptance | 用户确认后测试 finalize-round commit/push；push 失败时停止 | scripts/auto_advance_runner.py | data/logs/auto_advance_log.jsonl | push 成功/失败均有记录；日志完整 | false | push 认证失败；merge conflict | ROUND-1 |

### Phase 1：本地项目总控可用闭环

| round id | name | status | goal | inputs | outputs | acceptance criteria | can_auto_advance | hard_blockers | next_round |
|---|---|---|---|---|---|---|---|---|---|
| ROUND-1 | External Project Registry MVP | completed_pending_user_acceptance | 建立外部项目登记 MVP | data/registry/external_projects.yaml; docs/05_external_project_protocol.md | data/registry/external_projects.yaml; scripts/check_registry.py | registry 字段稳定；只读策略清晰；不修改外部项目 | true | 需要修改外部项目；需要读取真实 .env 或密钥 | ROUND-1-1 |
| ROUND-1-1 | Registry Runtime Validation | active | 注册后验证路径、watch_paths、profile_enabled 一致性 | data/registry/external_projects.yaml; scripts/check_environment.py | data/registry/external_projects.yaml | 字段与 protocol 一致；不可读路径 warning | true | 修改外部项目；读取 .env | ROUND-1-5 |
| ROUND-1-5 | External Project Import UX | planned | 设计手动导入、校验提示、缺失字段 warning 和保守默认 | data/registry/external_projects.yaml; docs/14_ui_console_plan.md | docs/05_external_project_protocol.md; data/roadmap/round_tasks.yaml | 导入字段、默认值、不可读路径处理清晰 | true | 批量扫描未知目录；覆盖用户登记内容 | ROUND-2 |
| ROUND-2 | Project Scan MVP | planned | 只读扫描允许入口文件、git 摘要、TODO/Roadmap 线索 | data/registry/external_projects.yaml; docs/05_external_project_protocol.md | data/project_scans/; scripts/check_repo.py | 跳过禁止目录和真实 .env；扫描结果可供 profile 使用 | true | 读取密钥文件；写入外部项目 | ROUND-2-1 |
| ROUND-2-1 | Scan Environment Validation | planned | 扫描前检查 filesystem MCP / local read；无 MCP 时 fallback | data/mcp/mcp_capability_registry.yaml; scripts/check_environment.py | docs/05_external_project_protocol.md | 环境检查清晰；不调用真实 MCP | true | 真实 MCP 未确认 | ROUND-2-5 |
| ROUND-2-5 | Project Scan Delta and Dirty Detection | planned | 补充增量扫描和 dirty 状态识别 | data/project_scans/; data/registry/external_projects.yaml | data/project_scans/; data/state/current_status.yaml | 区分首次/增量扫描；dirty 仅作风险提示 | true | checkout/reset 外部项目 | ROUND-3 |
| ROUND-3 | Project Profile MVP | planned | 基于扫描结果生成 profile、证据和风险摘要 | data/project_scans/; data/registry/external_projects.yaml | data/project_profiles/ | profile 含定位、技术栈、状态、风险、证据；LLM 只给建议 | true | 需要真实付费 LLM API | ROUND-3-5 |
| ROUND-3-5 | Context7 Documentation Adapter | planned | 规划 Context7 文档上下文适配器 | data/mcp/mcp_capability_registry.yaml; data/project_profiles/ | docs/11_mcp_infrastructure_strategy.md; prompts/cursor_mcp_usage_prompt.md | 明确 L0/L1 边界；未确认前不真实调用 MCP | false | 需要真实调用外部 MCP 未确认 | ROUND-4 |
| ROUND-4 | Project Snapshot MVP | planned | 将 profile 转为周期性 snapshot 和 next actions | data/project_profiles/; data/tasks/next_actions.yaml | data/project_snapshots/; data/tasks/next_actions.yaml | snapshot 可追踪时间、证据、风险和建议行动 | true | 需要替用户确认优先级 | ROUND-4-5 |
| ROUND-4-5 | GitHub Read Adapter | planned | 规划 GitHub 只读适配器补充 issue/PR/checks 证据 | data/mcp/mcp_capability_registry.yaml; data/project_snapshots/ | data/mcp/mcp_integration_roadmap.yaml; docs/12_external_tool_approval_model.md | GitHub 写操作保持 L3；缺 token 时用本地 git/mock | false | 需要真实 GitHub token；需要 GitHub 写操作 | ROUND-5 |
| ROUND-5 | Program-Project Link MVP | planned | 建立 active programs 与 project snapshot 的 proposal/confirmed 链接 | data/programs/active_programs.yaml; data/project_snapshots/ | data/programs/program_project_links.yaml | Agent 只能创建 proposal；confirmed 需要验收 | true | 替用户做最终战略优先级 | ROUND-5-5 |
| ROUND-5-5 | Priority Review Loop | planned | 建立优先级复盘循环，输出 priority suggestion | data/programs/program_project_links.yaml; data/tasks/next_actions.yaml | data/logs/project_decision_log.jsonl; data/tasks/next_actions.yaml | P0/P1 改变必须确认；普通排序为草案 | true | 改变 P0/P1 战略优先级 | ROUND-6 |
| ROUND-6 | Scheduler Foundation | planned | 建立 schedule、due、dry-run 和确认字段 | data/scheduler/scheduled_tasks.yaml; docs/06_scheduler_design.md | data/scheduler/scheduled_tasks.yaml; src/hub/services/scheduler_service.py | 调度默认不执行外部动作；每项有 target/confirmation/dry-run | true | 无人值守外部写入 | ROUND-6-5 |
| ROUND-6-5 | Auto-Advance Queue MVP | planned | 基于 gate 决策选择下一轮 prompt | data/gates/auto_advance_policy.yaml; data/roadmap/round_tasks.yaml | prompts/auto_advance_agent_prompt.md; data/scheduler/scheduled_tasks.yaml | 无硬阻塞继续；stop 不可绕过 | true | agent_gate.py 输出 stop | ROUND-6-6 |
| ROUND-6-6 | Auto-Advance Queue Runtime Integration | planned | schedule prepare 与 auto_advance_runner 对接；只生成任务包 | data/scheduler/scheduled_tasks.yaml; scripts/auto_advance_runner.py | data/codex_queue/; data/scheduler/scheduled_tasks.yaml | 不自动调用 Codex | true | 自动调用 Codex 未确认 | ROUND-7 |
| ROUND-7 | Codex / Cursor Prompt Queue | planned | 生成受控 prompt 队列，携带 gate 与 MCP policy | prompts/codex_project_driver.md; prompts/cursor_project_driver.md; data/roadmap/round_tasks.yaml | prompts/templates/; data/tasks/next_actions.yaml | prompt 明确 hard/soft blocker；不跳过 gate | true | prompt 要求外部写入未确认 | ROUND-7-5 |
| ROUND-7-5 | Agent Result Intake and Acceptance | planned | 接收 Agent 结果，区分 completed 与 accepted | data/logs/automation_log.jsonl; governance/round_state.yaml | governance/round_state.yaml; data/state/current_status.yaml | completed 不等于 accepted；安全类事项必须等待 | true | 安全/外部写入/P0/P1 变更未 accepted | ROUND-7-6 |
| ROUND-7-6 | Codex/Cursor Execution Result Consistency | planned | 检查执行结果与 roadmap 一致；防止状态未同步 | scripts/round_consistency_check.py; governance/round_state.yaml | data/state/current_status.yaml; data/logs/automation_log.jsonl | round_consistency_check 可检测不同步 | true | 安全/P0-P1 未 accepted | ROUND-8 |
| ROUND-8 | Feishu / Lark Mock Integration | planned | 建立 Feishu/Lark mock adapter | docs/09_feishu_lark_strategy.md; data/integrations/integration_targets.yaml | data/integrations/integration_targets.yaml; data/logs/automation_log.jsonl | mock 消息可读；不调用真实 Feishu/Lark API | true | 真实 Feishu/Lark API；真实 webhook | ROUND-8-5 |
| ROUND-8-5 | Feishu Notification MVP | planned | 规划真实通知 MVP 的边界和日志要求 | data/integrations/integration_targets.yaml; data/gates/auto_advance_policy.yaml | docs/09_feishu_lark_strategy.md; governance/agent_policy.yaml | 真实发送前确认；缺 webhook 停在 mock | false | 真实 webhook；发布或发送真实内容 | ROUND-9 |

### Phase 2：UI / Web Console 可视化闭环

| round id | name | status | goal | inputs | outputs | acceptance criteria | can_auto_advance | hard_blockers | next_round |
|---|---|---|---|---|---|---|---|---|---|
| ROUND-9 | UI Information Architecture | planned | 定义 UI 信息架构、页面列表、导航和安全边界 | docs/14_ui_console_plan.md; data/roadmap/round_tasks.yaml | docs/14_ui_console_plan.md | Dashboard、Programs、Projects、Roadmap、Gate、MCP、Scheduler、Logs、Settings 边界明确 | true | 要求本轮实现完整 UI | ROUND-9-5 |
| ROUND-9-5 | UI Data Contract | planned | 定义 UI 读取 YAML/JSON/JSONL 数据契约 | data/state/current_status.yaml; data/roadmap/round_tasks.yaml; data/gates/auto_advance_policy.yaml | docs/14_ui_console_plan.md; docs/04_data_model.md | 每页有数据来源、允许操作、禁止操作；UI 不写外部项目 | true | UI 需要读取真实密钥 | ROUND-10 |
| ROUND-10 | Static UI Prototype | planned | 创建静态 UI 原型或线框，不接真实外部 API | docs/14_ui_console_plan.md; data/state/current_status.yaml | docs/14_ui_console_plan.md; prompts/templates/ | 静态原型覆盖核心页面；写操作保持 mock/proposal | true | 需要生产级完整 UI | ROUND-10-5 |
| ROUND-10-5 | Stitch UI Concept Round | planned | 规划 Stitch UI 概念草图边界 | data/mcp/mcp_capability_registry.yaml; docs/14_ui_console_plan.md | data/mcp/mcp_integration_roadmap.yaml; docs/14_ui_console_plan.md | Stitch 为 L2；不写外部业务仓库 UI 代码 | false | MCP L2/L3 未确认；上传敏感截图 | ROUND-11 |
| ROUND-11 | Browser Test Adapter Planning | planned | 规划 Chrome DevTools 与 Playwright UI 验收边界 | data/mcp/mcp_capability_registry.yaml; data/gates/gate_checklist.yaml | docs/14_ui_console_plan.md; data/mcp/mcp_integration_roadmap.yaml | DevTools 只读诊断；Playwright 属 L3；不自动登录真实账号 | true | 自动登录账号；Playwright L3 未确认 | ROUND-11-5 |
| ROUND-11-5 | UI Acceptance Gate | planned | 定义 UI 验收门禁 | docs/14_ui_console_plan.md; data/gates/gate_checklist.yaml | data/gates/gate_checklist.yaml; docs/15_auto_advance_gate.md | checklist 可验收；真实外部写入、登录、发布必须 stop | true | UI 需要真实外部写入 | ROUND-11-6 |
| ROUND-11-6 | UI Runtime Smoke Test | planned | UI 页面出现后 Playwright/Chrome DevTools 本地 smoke test；不登录 | docs/14_ui_console_plan.md; data/mcp/mcp_capability_registry.yaml | docs/14_ui_console_plan.md; data/logs/automation_log.jsonl | smoke test 计划清晰；Playwright L3 未确认不执行 | false | 自动登录；Playwright L3 未确认 | ROUND-12 |

### Phase 3：半自动项目管理闭环

| round id | name | status | goal | inputs | outputs | acceptance criteria | can_auto_advance | hard_blockers | next_round |
|---|---|---|---|---|---|---|---|---|---|
| ROUND-12 | Daily Project Scan | planned | 建立每日扫描准备流程，默认 dry-run | data/scheduler/scheduled_tasks.yaml; data/registry/external_projects.yaml | data/project_scans/; data/logs/automation_log.jsonl | 可生成准备材料；不写外部项目 | true | 无人值守外部写入 | ROUND-12-5 |
| ROUND-12-5 | Daily Feishu Summary | planned | 生成每日 Feishu 摘要草案 | data/project_snapshots/; data/integrations/integration_targets.yaml | data/logs/automation_log.jsonl; docs/09_feishu_lark_strategy.md | 缺 webhook 使用 mock；真实发送前 stop | false | 要求真实发送但缺 webhook；发布内容 | ROUND-13 |
| ROUND-13 | Weekly Review Loop | planned | 汇总 programs、snapshots、next actions 为复盘草案 | data/programs/active_programs.yaml; data/project_snapshots/; data/tasks/next_actions.yaml | data/logs/project_decision_log.jsonl; data/tasks/next_actions.yaml | 复盘区分 proposal/confirmed；战略变更等待确认 | true | 改变 P0/P1 战略优先级 | ROUND-13-5 |
| ROUND-13-5 | Human Approval Queue | planned | 建立人类确认队列，避免软阻塞打断推进 | data/gates/auto_advance_policy.yaml; data/tasks/next_actions.yaml | data/tasks/next_actions.yaml; data/state/current_status.yaml | hard blocker 入队；soft blocker 在回复中报告；任何推进仍需当前授权 | true | approval queue 要求绕过用户确认 | ROUND-14 |
| ROUND-14 | Auto-Advance Agent Runner | planned | 实现受控自动推进 Runner | prompts/auto_advance_agent_prompt.md; scripts/agent_gate.py; data/roadmap/round_tasks.yaml | data/logs/automation_log.jsonl; governance/round_state.yaml | Runner 不绕过 gate；两次无进展后诊断并改变方法；不执行外部高风险动作 | false | 未授权真实外部写入 | ROUND-14-5 |
| ROUND-14-5 | Auto-Advance Postmortem | planned | 复盘自动推进误判、停下原因和安全增强 | data/logs/automation_log.jsonl; governance/round_state.yaml | docs/reports/auto_advance_postmortem.md; data/gates/auto_advance_policy.yaml | 记录 stop/warn/continue 样例；不吞失败 | true | 篡改历史日志 | ROUND-15 |

### Phase 4：高级自动化研究期

| round id | name | status | goal | inputs | outputs | acceptance criteria | can_auto_advance | hard_blockers | next_round |
|---|---|---|---|---|---|---|---|---|---|
| ROUND-15 | Remote Entry Environment Planning | planned | 研究远程入口环境方案，不执行远程控制 | docs/01_project_ultimate_goal.md; governance/agent_policy.yaml | docs/02_master_roadmap.md; docs/reports/remote_entry_environment_plan.md | 风险、权限、确认点清晰；不真实远程控制 | false | 执行远程控制 | ROUND-16 |
| ROUND-16 | Desktop Control Research | planned | 研究桌面控制边界、风险和替代方案 | docs/12_external_tool_approval_model.md; data/gates/auto_advance_policy.yaml | docs/reports/desktop_control_research.md | 高风险自动点击/登录/系统控制默认禁止；实验须确认 | false | 执行远程控制；登录账号 | ROUND-17 |
| ROUND-17 | Full Personal Project OS Review | planned | 复盘完整个人项目 OS 闭环并决定下一阶段优先级 | docs/02_master_roadmap.md; data/state/current_status.yaml; data/logs/project_decision_log.jsonl | docs/reports/full_personal_project_os_review.md; docs/02_master_roadmap.md | 覆盖治理、项目状态、自动推进、安全、UI、通知；P0/P1 用户确认 | false | 改变 P0/P1 战略优先级 | null |

## Round 0.7 结构化扩写

### Restart Round 0.7：Runtime Environment Alignment + Continuous Auto-Advance Runner

**状态：历史完成记录（非当前 active）**

目标：

- 统一运行环境
- 创建环境检查脚本 `scripts/check_environment.py`
- 创建轮次一致性检查脚本 `scripts/round_consistency_check.py`
- 创建自动推进 runner `scripts/auto_advance_runner.py`
- 当时曾支持 finalize-round Git 流程；当前 runner 已收紧为只读验证
- 确保无硬阻塞时默认继续

关键输出：`docs/16_runtime_environment.md`、`docs/17_continuous_auto_advance_runner.md`、`data/runtime/`、`prompts/continuous_auto_advance_prompt.md`

### Restart Round 0.8：Runner Dry Run and Failure Simulation

测试 `--mode check`、`prepare-next`；模拟 hard blocker 与 soft warning；测试敏感文件拦截；不真实 push。

### Restart Round 0.9：GitHub Push Workflow Validation

在用户确认后测试 finalize-round；验证 commit/push；验证 push 失败时停止；验证日志写入。

### Round 1.0：External Project Registry MVP

继续原 Round 1（ROUND-1）。

兼容说明：原 ROUND-0-7「Gate Dry Run and Roadmap Validation」目标已并入 ROUND-0-8。
