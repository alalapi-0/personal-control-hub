# personal-control-hub

`personal-control-hub` 是本地优先的个人项目管理与治理控制面。它维护项目名单、当前状态、治理协议、里程碑证据和下一步路由；通过路径索引外部项目，但不复制或默认修改外部项目。

2026-08-29 起，仓库采用 v2 轻量控制面：保留既有 Git 历史、脚本和资料，停止把长协议、完整路线图和历史报告作为每轮默认上下文。

## 当前入口

- Agent 默认启动：`AGENTS.md` + `STATE.yaml`（合计不超过 8KB）
- 长期方向：`NORTH_STAR.md`
- 项目注册表：`data/registry/external_projects.yaml`
- 条件治理协议：`governance/adapters/`
- 人类架构说明：`docs/03_architecture.md`
- 历史路线与报告：按需读取 `docs/02_master_roadmap.md`、`docs/reports/`、`docs/archive/`

## 当前受管项目

项目注册表保存 24 条已冻结身份记录：21 个普通受管项目、`manga-localizer` 已完成排除记录，以及永久留内盘的 `personal-control-hub` 和 `StorageGovernance` 控制面。注册表只保存身份、路径和稳定边界；普通项目的动态 disposition、effect authority 与唯一下一项只在专用 [`governance/programs/storage_governance/STATE.yaml`](governance/programs/storage_governance/STATE.yaml)。

Hub 不从注册表获得业务项目写权限。当前建设范围、设计选择与管理材料归并规则见 [执行规范](docs/design/ui_governance_execution.md)；Hub 成品尚未完成，实际进度只看 `STATE.yaml`。存储管理材料已归并至上述专用目录；旧入口只保留指向同一文件的链接。

## Git 版本控制

既有远端：<https://github.com/alalapi-0/personal-control-hub>。

在当前或已记录的所有者授权有效时，每个 accepted 子项目里程碑或治理轮形成一个作用域明确的 commit，并正常 push 当前跟踪分支。runner 永远只做检查；禁止自动合并 `main`、强推、改远端、提交秘密或把多个无关项目混入同一提交。

## 验证

首次从 Git 检出后运行 `python3 scripts/bootstrap.py`，创建 Git 不保存的空目录；它不会覆盖已有文件。随后运行相关检查：

```bash
python3 scripts/check_repo.py
python3 scripts/check_registry.py
python3 scripts/check_environment.py
python3 scripts/round_consistency_check.py
python3 scripts/agent_gate.py
python3 scripts/auto_advance_runner.py --mode finalize-round
pytest -q
```

`continue` / `warn_and_continue` 只是检查结果，不扩大当前权限。真实飞书、付费 API、外部项目写入、登录、发布、删除和其他高风险动作仍按当前指令与治理门禁处理。
