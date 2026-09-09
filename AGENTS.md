# Hub 项目入口

默认读取本文件与STATE.yaml（合计≤8192 bytes）；只使用当前任务条目，不恢复其他任务。以项目事实为治理对象，执行者/编辑器不决定范围。

- 本任务：docs/all_projects_governance_execution.md v2；状态all_projects_governance。先程序采集/汇总，再模型分析；本阶段不生成图表或改造UI。未变证据复用，不读全历史。
- 名单/状态：data/registry/external_projects.yaml、docs/12_project_connections.md。业务事实在项目权威源，Hub保存可重建投影。
- 存储：先读data/programs/storage_governance_goal.yaml，再沿适配器进入唯一执行状态；本任务不迁移或清理存储。
- 其他任务按明确范围路由；NORTH_STAR管方向，STATE管当前管理状态。

只修改明确归属文件，保护其他工作与原始数据。不读真实凭据，不操作编辑器/账号运行时。removed_local不读写，云项目排除。

普通工作：实现→相关检查→一次差异复核。真正高影响/安全边界变化才加独立审查；不默认生成逐轮合同、报告或收据。检查不授权。

本任务首次写入前运行 python3 scripts/auto_advance_runner.py --mode check --task-id ALL-PROJECTS-CODEX-GOVERNANCE-V1，输入未变复用。候选直接列STATE.candidate_paths，不要求额外清单文档。串行完成每仓库写入、验证和交付。

按所有者授权交付对应仓库真实主分支，核对远端包含关系；禁止强推、改远端或绕过保护。无变化不造commit，交付元数据不引发收据循环。用STATE一条记录保存恢复点。
