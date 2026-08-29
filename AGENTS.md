# AGENTS.md

本文件是 `personal-control-hub` 的条件上下文路由器。目标是用最小上下文启动，再按任务读取唯一必要材料。

## 默认启动包

每次进入仓库只读：

1. `AGENTS.md`
2. `STATE.yaml`

两者合计必须不超过 8192 bytes。不要默认读取 README、完整路线图、历史报告或 `governance/repo_protocol_standard.yaml`。

## 按任务路由

| 任务 | 再读取 |
|---|---|
| 项目登记、名单、路径或状态 | `data/registry/external_projects.yaml`、`docs/05_external_project_protocol.md` |
| 存储治理 | `governance/adapters/storage_governance.yaml`；只有实际执行存储任务时，才按适配器指向外部唯一规范 |
| 产品方向或重大架构决策 | `NORTH_STAR.md`、`project.yaml`、`docs/03_architecture.md`、相关 ADR |
| 权限、Git、外部写入或高风险动作 | `governance/agent_policy.yaml`、`data/gates/auto_advance_policy.yaml` |
| 当前或下一轮执行 | `STATE.yaml`，再用 `rg` 精确定位 `data/roadmap/round_tasks.yaml` 中对应轮次；不要全文载入路线图 |
| MCP 或飞书 | 对应 `data/mcp/`、`docs/11_mcp_infrastructure_strategy.md`、`docs/09_feishu_lark_strategy.md` |
| 历史、审计或迁移溯源 | 只读命中的 `docs/reports/`、`docs/archive/` 或兼容文件 |

## 权威边界

- `STATE.yaml` 是唯一当前状态权威；`governance/round_state.yaml` 与 `data/state/current_status.yaml` 仅为兼容/历史材料。
- `NORTH_STAR.md` 只管长期方向，不存当前进度。
- `data/registry/external_projects.yaml` 是项目名单权威，不复制外部项目内容。
- 外部项目默认只读；本仓库不能自行扩大权限。
- 不读取或提交真实 `.env`、token、secret、cookie、私钥。
- completed 与 accepted 分开；验证通过不等于获得外部动作授权。

## 工作流

1. 读取默认启动包并选择一条任务路由。
2. 用 `rg` / 精确路径收集证据，避免全量扫描和全历史注入。
3. 写入前运行 `python3 scripts/auto_advance_runner.py --mode check` 与 `python3 scripts/agent_gate.py`。
4. 只修改当前授权范围，外部项目保持只读。
5. 运行最近验证；治理轮还要运行仓库检查、状态一致性和测试。
6. 只在状态真实改变时更新 `STATE.yaml`；历史证据写入一份轮次报告，不在多文件重复追加。
7. 获得当前或已记录的所有者授权时，每个 accepted 里程碑/治理轮做一个作用域明确的 commit，并正常 push 当前跟踪分支。不得自动合并 `main`、强推或改远端。

## 精简执行规则

- 同一候选冻结后再做一次独立 Judge 和一次 Governor 决策；内容变化后才重新审查。
- 子 Agent 使用最小任务包，不继承完整历史；控制面写入、Git 交付和外部效果由 Root 负责。
- 小改只跑相关检查；破坏性操作前后才跑完整证据集。
- 不因软警告停止；真实权限、凭据、不可逆外部效果或所有者决策才是阻塞。
