# Hub 项目入口

默认读AGENTS.md和STATE.yaml，合计≤8192 bytes。STATE保存当前执行事实，registry保存项目身份；不恢复无关任务。

当前版本以STATE.all_projects_governance为准。v3计划在docs/all_projects_governance_execution.md和data/roadmap/project_data_v3.yaml；只有所有者明确启动才切换，准备/阅读不启动实施。每轮只读当前阶段/项目，复用未变证据。

来源沿data/registry/external_projects.yaml、docs/12_project_connections.md、data/connections/metric_sources.yaml读取。Hub留本地；独立项目迁移先按V3-01核对存储入口，再进入唯一执行状态。云项目排除，removed_local禁读写。不做图表/UI或无关全盘清理。

保护其他工作、原始数据与凭据，不操作编辑器/账号运行时。首次写入前运行python3 scripts/auto_advance_runner.py --mode check --task-id ALL-PROJECTS-CODEX-GOVERNANCE-V1；候选列STATE.candidate_paths。

普通工作实现→相关检查→差异复核，真实高影响边界才加必要独立审查，不造逐轮报告/收据。跨仓写入/验证/交付串行；依所有者授权正常交付对应main并核对远端，禁强推/绕过保护。STATE保留验收、阻塞、去重轮次和唯一下一步。
