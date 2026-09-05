# StorageGovernance 路由

- **Reader：**进入本目录范围的 Root、Explorer、Judge、Governor。
- **Update trigger：**权威层级、项目清单入口、启动路由、暂停/恢复语义或审查路由发生持久变化。
- **Purpose：**把存储治理路由到 Hub 稳定入口、完整项目清单、Hub 内本目录唯一 current-state、稳定规范和当前项目证据。

## 权威与每轮固定入口

1. 已激活 Goal 的每一轮必须依次读取：
   1. `/Users/alalapi/PycharmProjects/personal-control-hub/data/programs/storage_governance_goal.yaml`
   2. `/Users/alalapi/PycharmProjects/personal-control-hub/data/registry/external_projects.yaml`
   3. `/Users/alalapi/PycharmProjects/personal-control-hub/governance/adapters/storage_governance.yaml`
   4. 本文件
   5. `STATE.yaml`
   6. `STATE.yaml.next_action` 指定项目的 `AGENTS.md`、唯一 current-state 与必要入口（若存在）
2. `STATE.yaml` 是唯一执行 current-state、authority、blocker、当前项目和 `next_action` 权威。
3. Hub `STATE.yaml` 只保存 Hub 自身状态、registry/manifest 指针与小型摘要；不得复制项目动态状态或成为第二执行状态。
4. `STORAGE_GOVERNANCE.md` 只承载稳定政策和不可执行里程碑索引；项目 manifest 与 evidence 证明现场事实。
5. 参数、registry、Prompt、旧合同、旧审查和历史文字都不能产生或扩大当前 authority。
6. 现场与 `STATE.yaml` 冲突时停止 effect，登记一个 compact drift 与精确恢复条件。

## 条件路由

- Goal 未激活且用户需要启动入口：只可定位 Hub 的 `prompts/storage_governance_goal_mode.md`；不得自动提交或执行。
- 已激活执行：按固定入口读取后，仅加载当前项目规则、当前合同、当前项目 evidence index 和规范相关条款；禁止全历史注入。
- 项目清单或路径问题：以 Hub registry 为身份清单，以冻结 project manifest 为本 epoch 范围，以本 `STATE.yaml` 为动态 disposition 权威。
- 历史、争议或恢复：先用规范里程碑索引定位精确旧证据；旧证据只能作为 baseline，不能授权新 effect。
- 协议或架构改版：可完整读取规范，但候选与审查包仍只含相关条款、精确 diff/hash 和有界证据。

## 串行执行与审查

- 任一时刻只能有一个 active project、一个 active batch/effect set、一个 writer 和一个 `next_action`。
- 当前项目必须先进入 `accepted`、`protected`、`ineligible` 或 `deferred_with_fact_blocker_and_exact_recovery_event`，才可选择下一项目。
- 跨仓库只读发现可并行；fetch、写入、验证、迁移、清源、状态更新和 Git effect 严格按一个项目串行。
- `manga-localizer` 仅保留已完成排除记录；不得进入其项目树、关联应用、运行时、模型或验证路径。
- `personal-control-hub` 永驻内盘，不迁移、不清理、不递归治理；只允许本 Goal 所需的 registry、参数、路由、compact 状态和直接管理一致性修复。
- `PAUSED`、`STOPPED`、`WAITING_FOR_USER`、`BLOCKED`、authority 为 `none/consumed/closed` 或 `next_action.requires_user: true` 时不得执行 effect。
- 风险沿用全局 `DIRECT` / `REVIEWED` / `GOVERNED`；本 Goal 使用 `STATE.yaml.task_contract` 指向的单一当前能力合同；当前所有者“释放优先”修订优先于旧严格条款。普通已授权生成物清理／基本迁移只由 Root 登记精确范围并检查，不逐微批派 Judge/Governor；控制面改版与最终验收按当前合同审查。

## 写回与边界

- 只在 material state change、项目终态、authority change、rollback/post-effect result 时更新 `STATE.yaml`；无进展复核不写回。
- 项目详细事实写入外盘当前 epoch evidence；`STATE.yaml` 只保留指针、计量摘要和一个下一步。
- Hub 管理同步只限当前用户已授权的本治理记录与一致性修复；不得迁移或清理 Hub，也不得从 registry 获得项目 effect authority。
- 存储/业务的下载、Git、发布、磁盘管理、凭据/会话/活动数据库及离线视频盘效果仍须当前独立授权。2026-09-05 Hub Goal 只授权三份管理材料归并、精确消费者切换与验收后的 Hub 分支提交/推送，不恢复存储执行。
- 唯一材料根为 `governance/programs/storage_governance/`；旧 `~/Documents/StorageGovernance/` 只保留指向这三份文件的链接，供现有 STORAGE_MAP 入口使用。不得写出第二套副本。
