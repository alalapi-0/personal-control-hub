# Control Plane v2 Upgrade Report

## 文档职责

- 读者：本轮 Judge、Governor、所有者和后续审计者。
- 目的：保存本轮候选、验证和 Git 交付证据；不作为当前状态。
- 更新触发：本轮候选、验证结论或交付结果改变。

## 目标与边界

原地升级现有 personal-control-hub，不创建重复仓库。建立小于 8KB 的默认启动包、唯一当前状态、North Star、StorageGovernance 注册与只读指针适配器。保留旧历史；不删除/移动旧文件，不写外部项目，不调用真实飞书/API，不合并 main、不强推、不改远端。

## Preimage

- branch: `agent/governance-closure-20260812`
- HEAD: `ac9aeef2099fed9c0d3cafc9dcde634505c585b5`
- upstream: `origin/agent/governance-closure-20260812`
- worktree: clean
- origin: existing `alalapi-0/personal-control-hub`
- baseline: repository checks passed; 4 pytest tests passed with 4 pre-existing return-value warnings.

## 候选摘要

- 保留仓库与 Git 历史，替换默认控制面。
- 新增 `STATE.yaml`、`NORTH_STAR.md` 和 StorageGovernance adapter。
- 注册 StorageGovernance 的便携路径与唯一规范指针。
- 将旧状态与长协议明确降级为兼容/历史材料。
- runner/一致性检查改读唯一 `STATE.yaml`；新增默认启动包门禁和回归测试。
- Git 规则限定为 accepted 里程碑/治理轮的单一 scoped commit，正常推送当前跟踪分支，不自动合并 main。

## 验证结果

候选验证完成，等待独立 Judge 与 Governor；审查结论在 Root 的任务证据中登记，避免候选自证或把审查结果写回后触发自引用复审。

- `AGENTS.md` + `STATE.yaml`: 4,648 bytes，低于 8,192-byte 门禁。
- 全部 YAML 解析：PASS。
- `python3 scripts/check_repo.py`: PASS。
- `python3 scripts/check_registry.py`: PASS，1 个项目，0 warning。
- `python3 scripts/check_environment.py`: PASS。
- `python3 scripts/round_consistency_check.py`: PASS，0 warning / 0 blocker。
- `python3 scripts/agent_gate.py`: `warn_and_continue`，0 hard blocker；10 条均为既有未来轮次需人工确认的软警告。
- `python3 scripts/auto_advance_runner.py --mode check`: 同上，检查通过且不授予动作权限。
- `python3 scripts/bootstrap.py --dry-run`: PASS，无写入。
- registry/MCP 只读 CLI：PASS。
- `pytest -q`: 8 passed；4 条 warning 来自既有 `runner_dry_run_test.py` 返回值风格，与本轮无回归。
- `git diff --check`: PASS。

候选 exact diff 与文件摘要在内容冻结后由 Root 单独哈希并提供给独立审查；本报告不嵌入自引用候选哈希。
