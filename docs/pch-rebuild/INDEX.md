# PCH 全项目重构计划入口

PCH-REBUILD-20261020 / 1.2.0-20261023-package1。采用 [1.2 功能路线](REPLAN_20261023.md) 与 [机器增量](PLAN_DELTA_20261023.json)；本快照仅修正本地包版本引用。当前进度与原生Goal实际状态只读STATE的pch_refactor.rebuild_planning_20261020，不把静态文档当运行账本。

范围46管理身份、44份真实项目本地计划及2个已合并别名核对包，8阶段25轮259静态任务、208实施验收不变。历史验收基线12/208；本次不重做BASE或已完成调查，不重置累计480预算。其他项目未变包按definition_revision/hash复用。

读取：[rebuild-index.json](rebuild-index.json) → [本次修订](REPLAN_20261023.md) → [MASTER_PLAN](MASTER_PLAN.md) → [MACHINE_PLAN](MACHINE_PLAN.json) → [CONTRACTS](CONTRACTS.md) → [ORCHESTRATION](ORCHESTRATION.md) → [STYLE_PROXY](STYLE_PROXY.md) → [COVERAGE_MATRIX](COVERAGE_MATRIX.json) → [ACCEPTANCE_MATRIX](ACCEPTANCE_MATRIX.json) → [REVIEW_REPORT](REVIEW_REPORT.md)。调查历史见[BASELINE](BASELINE.md)。真实根、会话、私有证据沿既有private-index解析，不按名称猜路径。

P1-R1-PCH-BASE是历史首派发任务，现已完成；恢复从唯一STATE及实际DAG ready选择。ADAPTER沿原ID回到官方原生接口适配，终止私有Codex隔离扩展；其历史实验不结算产品。BUDGET/CONTROL/RECOVER源码按实际能力推进，不以整项ADAPTER作为无关屏障；明确的 P3/P6 源码策略通过原 TaskStore 的 plan_source_ready 与受信开发能力检查；源码候选记录不创建普通执行任务、不计入 accepted。普通效果与整项验收仍保持原 ROUTES 准入。先沿DAG原ID完成现有TaskStore同ID版本reconcile增量，再采用新定义，不直接覆写DB。所有任务仍须原完整工程、集成、同步及产物验收。

[RUN_GOAL_PROMPT](RUN_GOAL_PROMPT.md)指向本版原生 Goal 入口；不把提示词作为替换 objective 的命令。旧Goal暂停不因本版文件生成自动解除；恢复须核对受支持接口的真实active结果。计划和提示词不证明后台宿主、正式采用、故障恢复或60分钟流转已通过。

完整路径、任务消息、原生ID、数据库和敏感证据留本地；公开仓库只交付获准共享文档。静态校验：`python3 -B docs/pch-rebuild/validate_plan.py`。它验证版本、DAG、路径、原分母及未变包hash，不执行产品验收。

完整计划链接属于所有者本地包，公开clone可能不可用；本版共享文档不包含完整私人路径/会话清单或原始任务消息。
