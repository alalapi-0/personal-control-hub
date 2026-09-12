# TC6 管理材料归并验收

Reader: Root、独立 Judge/Governor、后续维护者。Purpose: 证明管理材料和消费者切换，供恢复和审查。Update: 本候选发生实质变化时；Hub 当前进度只读根 STATE.yaml。

三份存储治理材料归入 `governance/programs/storage_governance/`。源目录只保留三个链接，服务于未改动的 STORAGE_MAP 消费者。Hub 根状态只保留管理指针；旧动态状态、暂停文字及历史计量按 baseline.json 原像保留为不可执行证据。当前批次所有 effect 都关闭，字节数缺少本次迁移精确证据时记为 unknown。

`consolidation-inventory.json` 逐项说明归属、来源版本、消费者、去向、保留依据和未解决依赖。`cutover.json` 记录实际链接和工作区精确段落替换；`verify.py` 只读验证内容与不变边界。

默认连接读 v1 与 v2 两套不可变 authority，v2 是当前版本。旧事件和结果逐行保留；唯一新请求只刷新 Hub 与本治理状态，其他业务来源没有重新探测。24 条投影仍可读，旧版本结果明确 stale，Manga 继续 BLOCKED_BY_AUTHORITY。完整业务连接最终验收和 UI/Figma 仍未完成。

`router-delivery.md` 是计划送入 Git 索引的 AGENTS.md 完整 blob，只交付本轮存储治理路由行（含已有必要读取顺序）；工作区另有全局治理 workflow 修改，按 baseline 原像保留，不随本轮提交。候选同时绑定此交付 blob 与实际工作区保存检查。

复现：

- `python3 docs/reports/ui_design_governance/unit-06/verify.py`
- `python3 docs/reports/ui_design_governance/unit-04/readback.py`（原只读 API 脚本直接复用，结果另存本轮）
- `python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_hub*.py'`
- `pytest -q tests/test_control_plane_v2.py`
- `python3 scripts/check_repo.py`、`python3 scripts/check_registry.py`、`python3 scripts/round_consistency_check.py`
- `python3 scripts/hub_refresh.py validate`

恢复只允许核对后修复精确受影响组；不得用本证据回退其他任务。用户暂停时停止本任务写者并保留根 STATE 的当前单元/下一步。
