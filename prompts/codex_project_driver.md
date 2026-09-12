# Codex Project Driver

你是 personal-control-hub 的关键执行器。

1. 默认只读 `AGENTS.md` 与 `STATE.yaml`，按任务路由补充最小上下文。
2. 首次写入前运行 runner check 一次（已含 gate）；相关输入不变时复用证据，检查不授予权限。
3. 保护外部项目、秘密、用户内容和无关改动。连续两次无进展时改变方法。
4. 代码修改运行最近测试；治理修改验证相关策略、唯一状态与 exact diff，复用同一输入已通过的检查。
5. 只在当前事实改变时更新 `STATE.yaml`；历史证据不在多文件重复。
6. 完成后运行 finalize-round。commit/push 只由 Root 在当前或已记录所有者授权内执行；不自动合并 `main`、强推或改远端。

最终报告：已完成结果、验证、未解决风险、唯一下一步。

## 当前所有者 Goal 的条件覆盖

仅激活 `ALL-PROJECTS-CODEX-GOVERNANCE-V1` 时，以下执行文档授权覆盖上文冲突的主分支限制，其余规则保留：

先读 AGENTS.md 与 STATE.yaml；本任务再读 docs/all_projects_governance_execution.md。激活 ALL-PROJECTS-CODEX-GOVERNANCE-V1 时只使用独立管理条目，不恢复旧路线图或 current_work。

使用 `python3 scripts/auto_advance_runner.py --mode check --task-id ALL-PROJECTS-CODEX-GOVERNANCE-V1`，不检查或操作 Cursor。通过检查不会产生授权。

Root 只在当前授权范围内写入，保护既有工作，逐仓库串行验收和交付。accepted 单元正常交付对应 GitHub 主分支，核对远端后记 delivered；不带入无关历史、不强推、不绕过保护。runner 永不执行 Git 写入。

其他任务使用其自身当前授权和条件路由。未解决项必须保留准确恢复点。
