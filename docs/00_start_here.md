# Start Here

本文件是人类导航页，不是 Agent 默认启动包。

## 最短路径

- 当前状态：`STATE.yaml`（唯一当前状态权威）
- 长期目标：`NORTH_STAR.md`
- Agent 路由：`AGENTS.md`
- 项目名单：`data/registry/external_projects.yaml`
- 存储治理适配器：`governance/adapters/storage_governance.yaml`

需要历史轮次时再定向读取 `docs/02_master_roadmap.md`、`governance/round_state.yaml` 和 `docs/reports/`。不要把它们作为每次启动输入。

## 当前事实

仓库正在原地升级为 v2 轻量控制面；首个受管项目为 StorageGovernance。飞书仍是未来入口，不调用真实 API。外部项目默认只读，任何删除或写入必须由对应项目的当前规范和用户权限授权。

## 验证入口

```bash
python3 scripts/auto_advance_runner.py --mode check
python3 scripts/check_repo.py
python3 scripts/check_registry.py
python3 scripts/round_consistency_check.py
pytest -q
```
