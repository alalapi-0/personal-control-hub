# PCH 全项目重构计划入口

计划 PCH-REBUILD-20261020 / 1.0.1-20261020。本轮完成调查与规划；产品实现、原生Goal启动和服务切换留给执行入口。

范围46个管理身份，44份真实项目独立会话调查，2个已合并别名核对包；其中工作区容器不冒充独立业务产品。8阶段、25轮、259项静态任务（含本轮调查/规划），未来实施208项。完整范围/真实根/原生ID仅保存在所有者本地。

读取顺序：[BASELINE](BASELINE.md) → [MASTER_PLAN](MASTER_PLAN.md) → [MACHINE_PLAN](MACHINE_PLAN.json) → [CONTRACTS](CONTRACTS.md) → [ORCHESTRATION](ORCHESTRATION.md) → [STYLE_PROXY](STYLE_PROXY.md) → [COVERAGE_MATRIX](COVERAGE_MATRIX.md) → [ACCEPTANCE_MATRIX](ACCEPTANCE_MATRIX.md) → [REVIEW_REPORT](REVIEW_REPORT.md)。

实际文件、原唯一状态、私有项目索引、适配器与现有/待建运行位置由 [rebuild-index.json](rebuild-index.json) 定位；每个项目有实际本地PLAN/TASKS，已合并根则有标注归并包。私有完整清单、任务全文/路由、报告证据与数据库不公开发布；公开仓库仅同步经核对的非敏感共享契约和本入口，因此从公开clone读取本地私有链接可能不可用。

首个就绪实施任务 **P1-R1-PCH-BASE**，路由 **route:personal-control-hub**；核对运行冻结/交付/当前源码与并行写责后才归并。阶段/轮次不是整阶段屏障，按具体DAG ready领取；普通工程无PSH前置。

[RUN_GOAL_PROMPT](RUN_GOAL_PROMPT.md) 是之后由所有者明确使用的短Goal入口；当前未创建Goal。独立工程审阅适用风险，风格代理仅替代有源码证据的原主观节点。本人事后回看不在任何任务状态、依赖或完成条件内。

静态一致性检查：python3 -B docs/pch-rebuild/validate_plan.py。它验证计划与回执/路径/DAG/分母一致，不运行产品验收。未来运行更新现有STATE本任务子条目和原项目状态/TaskStore，静态文档不另作调度状态源。
