# personal-control-hub

`personal-control-hub` 是本地优先的个人项目管理与治理控制面。它维护项目名单、当前状态、治理协议、里程碑证据和下一步路由；通过路径索引外部项目，但不复制或默认修改外部项目。

2026-08-29 起，仓库采用 v2 轻量控制面：保留既有 Git 历史、脚本和资料，停止把长协议、完整路线图和历史报告作为每轮默认上下文。

## 当前入口

- Agent 默认启动：`AGENTS.md` + `STATE.yaml` 中当前任务的条目（默认上下文不超过 8KB）
- 长期方向：`NORTH_STAR.md`
- 项目注册表：`data/registry/external_projects.yaml`
- 条件治理协议：`governance/adapters/`
- 人类架构说明：`docs/03_architecture.md`
- 历史路线与报告：按需读取 `docs/02_master_roadmap.md`、`docs/reports/`、`docs/archive/`

## 日常使用

已有本机服务时打开 <http://127.0.0.1:8766/>。项目目录提供列表、业务树和甘特图，详情展示来源事实、Hub 管理计划与预览；基础读取与刷新无需 Agent 或模型。计划估计、工程核验和本人验收分别保存，未知信息继续显示未知。

从项目代码目录运行时，先把 `PCH_ROOT` 设为实际数据目录的绝对路径。固定代码与数据可以分开；所有命令中的 `--root` 都指向同一数据目录。调试新实例可用空闲端口，避免停止已有服务：

```sh
python scripts/hub_server.py --root "$PCH_ROOT" --host 127.0.0.1 --port 0
```

终端会显示实际本地地址，Ctrl-C 仅停止该手工实例。已有 user service 可用 `systemctl --user status personal-control-hub.service` 查询，用 `journalctl --user -u personal-control-hub.service -n 30` 查看日志。重启前须核对该服务与数据写责。服务只监听本机回环地址；复制代码或打开页面不会初始化任务库或启动业务项目。

管理编辑需要本机所有者身份。身份未配置时页面保持访客只读；凭据由所有者通过受控本机环境／凭据入口处理，不写入仓库或备份。管理计划在 Hub 配置层保存，刷新不会覆盖它，也不把预计时间当实际验收。新项目登记、刷新失败、备份和恢复的具体步骤见 [日常操作与恢复](docs/12_project_connections.md#日常操作与恢复)。

## 当前受管项目

项目身份以当前注册表及 `hub_connections.py --root "$PCH_ROOT" current` 报告为准，固定刷新范围另见 `data/connections/sync_scope.yaml`；公开仓库模板不等于本机私人登记集合。注册表保存身份、路径和稳定边界，业务事实沿各项目的唯一状态读取。存储治理专项的动态 disposition、effect authority 与下一项仍只在其专用 [STATE](governance/programs/storage_governance/STATE.yaml)，不代替 Hub 当前任务的执行状态。

Hub 不从注册表获得业务项目写权限。当前建设范围、设计选择与管理材料归并规则见 [执行规范](docs/design/ui_governance_execution.md)；Hub 成品尚未完成，实际进度只看 `STATE.yaml`。存储管理材料已归并至上述专用目录；旧入口只保留指向同一文件的链接。

## Git 版本控制

既有远端：<https://github.com/alalapi-0/personal-control-hub>。

在当前或已记录的所有者授权有效时，每个 accepted 子项目里程碑或治理轮形成一个作用域明确的 commit，并正常 push 当前跟踪分支。runner 永远只做检查；禁止自动合并 `main`、强推、改远端、提交秘密或把多个无关项目混入同一提交。

## 验证

本地开发与 CI 共同使用 `requirements-dev.txt`，其中包含 `requirements.txt` 的运行依赖。仅运行程序时安装 `requirements.txt`；测试额外使用 pytest。

用与 CI 相同的 Python 3.12 创建隔离环境，不修改全局 Python：

```bash
python3.12 -m venv .venv/pch-dev
. .venv/pch-dev/bin/activate
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -q
python -m pytest tests -q
```

工作流保留 unittest 回归，并执行完整 pytest 集合以覆盖函数式测试；Node 检查使用 Node 24。仓库、状态一致性、登记与模板检查的精确命令见 `.github/workflows/ci.yml`。

本地来源刷新使用 `python scripts/hub_sync.py --root "$PCH_ROOT" discover` 核对固定集合，再用 `python scripts/hub_sync.py --root "$PCH_ROOT" sync --request-id UNIQUE_ID` 显式读取；加 `--project-id ID` 可读取其中一项。固定集合与排除原因在 `data/connections/sync_scope.yaml`，发现不会自动登记项目或扩大读取范围。同请求只续接未提交项目，新请求才重新读取；来源失败保留错误并返回非零码，业务字段不足保留 partial。

快照入口为 `sync --scheduled`，默认每 12 小时（Asia/Tokyo 09:00、21:00），可配置为每 24 小时（09:00）；保留页面手动刷新。有限 oneshot 退出后不保留浏览器、Agent 或模型。普通页面读取上次成功快照，失败、来源时间、年龄和下次计划分别显示，来源本身的有效性规则不变。安装、暂停、恢复及真实到点与受控验证的区别见 [本地周期快照](docs/12_project_connections.md#本地周期快照)。旧 prompt 准备调度保持停用。

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
