# Codex Project Driver

先读 AGENTS.md 与 STATE.yaml；本任务再读 docs/all_projects_governance_execution.md。激活 ALL-PROJECTS-CODEX-GOVERNANCE-V1 时只使用独立管理条目，不恢复旧路线图或 current_work。

使用 `python3 scripts/auto_advance_runner.py --mode check --task-id ALL-PROJECTS-CODEX-GOVERNANCE-V1`，不检查或操作 Cursor。通过检查不会产生授权。

Root 只在当前授权范围内写入，保护既有工作，逐仓库串行验收和交付。accepted 单元正常交付对应 GitHub 主分支，核对远端后记 delivered；不带入无关历史、不强推、不绕过保护。runner 永不执行 Git 写入。

其他任务使用其自身当前授权和条件路由。未解决项必须保留准确恢复点。
