# Hub 项目入口

默认读AGENTS.md和STATE.yaml中当前任务的条目，默认上下文≤8192 bytes。STATE保存当前执行事实，registry保存项目身份；不恢复无关任务。

PCH-REFACTOR-20261014（计划1.0.0-20261014）仅在所有者明确启动且STATE.pch_refactor存在时适用。选中该任务时只读STATE.pch_refactor及其中指向的当前轮计划/目录，从首个依赖满足未完成项续接。该任务允许计划内本地页面/图表与必要可逆改动；旧任务“不做UI”不约束该范围。保留P0，不启动旧治理v3，不恢复旧任务，不修改全局Agent规则。子项目默认只读，云项目排除，removed_local禁止探测；生产服务/账本切换须先核对写责与迁移证据。

本任务首次写入前用python3 scripts/auto_advance_runner.py --mode check --task-id PCH-REFACTOR-20261014；候选与动态事实只列STATE.pch_refactor。runner只检查，不授权、不调度。保护已有脏树，只交付精确owned差异；按所有者本次授权、真实可见性和分支保护正常交付main，必要时走PR；禁止强推/改remote/绕保护。DB、真实任务消息、凭据、完整私人路径清单及敏感截图留本地。

当前版本以STATE.all_projects_governance为准。v3计划在docs/all_projects_governance_execution.md和data/roadmap/project_data_v3.yaml；只有所有者明确启动才切换，准备/阅读不启动实施。每轮只读当前阶段/项目，复用未变证据。

来源沿data/registry/external_projects.yaml、docs/12_project_connections.md、data/connections/metric_sources.yaml读取。Hub留本地；独立项目迁移先按V3-01核对存储入口，再进入唯一执行状态。云项目排除，removed_local禁读写。不做图表/UI或无关全盘清理。

保护其他工作、原始数据与凭据，不操作编辑器/账号运行时。首次写入前运行python3 scripts/auto_advance_runner.py --mode check --task-id ALL-PROJECTS-CODEX-GOVERNANCE-V1；候选列STATE.candidate_paths。

普通工作实现→相关检查→差异复核，真实高影响边界才加必要独立审查，不造逐轮报告/收据。跨仓写入/验证/交付串行；依所有者授权正常交付对应main并核对远端，禁强推/绕过保护。STATE保留验收、阻塞、去重轮次和唯一下一步。
