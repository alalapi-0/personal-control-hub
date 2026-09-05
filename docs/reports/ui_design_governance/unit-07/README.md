# TC7 真实运行准备验收

Reader: Root、独立审核者、未来维护者。Purpose: 核对已验收接口对当前真实来源与空设计库的运行结果。Update: 本候选或依赖决定发生实质变化时；当前进度只读根 STATE.yaml。

本轮使用现有接口完成一次冻结24项刷新及同请求重放：20来源解析成功、3明确无当前状态源、Manga按权限拒绝且0路径操作。此前82条事件原封保留，新账本头为108。CLI若用整体失败计退出码，会因Manga返回2；这不是全项目最终验收通过。

生产设计库通过 DesignStore.initialize 初始化为真实 revision1，事实与事件均为空，重开/重试结果一致。没有Figma候选、反馈或所有者决定。锁文件为本机生成的空运行锁，保留原位且不纳入Git。

`execution.py` 是本轮实际执行程序的精确归档，依赖已记录前态，不应作为新刷新命令重新运行；日常调用使用已验收 scripts/hub_refresh.py。`runtime-evidence.json` 保存各项目结果、来源指纹、头、重放及0外部写入计数。当前可安全只读复现：

- `python3 docs/reports/ui_design_governance/unit-07/verify.py`
- `python3 docs/reports/ui_design_governance/unit-04/readback.py`
- `python3 scripts/hub_refresh.py validate`
- `python3 scripts/hub_designs.py validate --store data/design_governance/design-store.json`

`dependencies.json` 保留Figma重连、Manga范围决定及3个无状态源项目例外/新来源决定。20个已解析来源也还需要成品UI链路验收；不得提前将任何项目记录升级为最终连接验收。全部独立运行准备完成后等待这些真实依赖，不另建重复准备单元，不提前编写最终交接完成声明。
