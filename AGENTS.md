# AGENTS.md

Hub 的条件上下文入口。默认只读本文件与 `STATE.yaml`，合计不超过 8192 bytes。`STATE.yaml` 是当前管理状态唯一权威；`governance/round_state.yaml` 与 `data/state/current_status.yaml` 只供历史溯源，不恢复旧任务。

## 当前任务路由

- 所有者激活 `ALL-PROJECTS-CODEX-GOVERNANCE-V1` 时，每轮再读 `docs/all_projects_governance_execution.md`，使用 STATE 的 `all_projects_governance` 条目；不占用 `current_work`。
- 本任务只由 Codex 执行，禁止访问或操作 Cursor。首次写入前执行 `python3 scripts/auto_advance_runner.py --mode check --task-id ALL-PROJECTS-CODEX-GOVERNANCE-V1`；仅首次修复检查入口自身冲突可先做最小 bootstrap 修复，立即补验。检查不是授权。
- 项目身份与路径：`data/registry/external_projects.yaml`、`docs/05_external_project_protocol.md`。历史工作分支的接入实现按执行文档验证后逐单元移植，不整分支合并。
- 本轮 Git 检查清单由 STATE 的 `all_projects_governance.candidate_manifest` 指定；缺失或无效时修复当前登记，不读取历史候选代替本轮。
- 权限/Git：`governance/agent_policy.yaml`、`data/gates/auto_advance_policy.yaml`；当前任务授权覆盖冲突的旧限制，其余任务仍默认外部只读。
- 飞书本地准备：`docs/09_feishu_lark_strategy.md`。真实连接保持 disabled。
- 统一项目状态入口：`docs/12_project_connections.md`；`python3 scripts/hub_connections.py current` 只读 Hub 账本，显式 `refresh` 才读取登记来源并更新本地投影。
- 其他工作仅按具体任务读取 `project.yaml`、相关治理文件与路线图条目，不默认读全历史。

## 执行与交付

Root 是本任务状态、控制面和 Git 的唯一写者。跨项目仅并行只读发现，写入、验证与交付按仓库串行。保护既有 dirty 工作、原始素材、凭据和用户决定；不读取或提交真实 .env、token、cookie、私钥，不操作生产或迁移存储。

本任务 accepted 单元依所有者明确授权交付对应远端真实主分支，核对远端祖先关系后才标 delivered。禁止强推、绕过检查/分支保护或带入无关历史。runner 永不暂存、commit、push、生成队列或自动恢复任务。普通任务仍需自身当前授权。

控制面变更由 Governor 冻结合约，Root 注册精确候选，fresh Judge 审查，Governor 决策。同一语义候选只审一次。保留未解决项与唯一下一步，不以局部完成冒充全部完成。
