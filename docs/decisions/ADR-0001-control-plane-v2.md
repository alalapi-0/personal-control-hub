# ADR-0001: 原地升级为轻量控制面

- 状态：accepted by owner direction；implementation pending governed review
- 日期：2026-08-29
- 读者：修改项目权威层、上下文路由、项目注册或集成边界的人与 Agent。
- 目的：记录为何保留仓库并逻辑重启，而不是删除重建。
- 更新触发：本决策被正式替代；实现细节变化不更新本 ADR。

## 决策

保留 `personal-control-hub` 的 Git 历史、远端、可用脚本、registry 服务和安全门禁，在原仓库内建立 v2 轻量控制面：`AGENTS.md` 负责条件路由，`STATE.yaml` 是唯一当前状态，`NORTH_STAR.md` 负责长期方向，registry + adapters 管理子项目指针。

旧长协议、旧 round state、旧 status 与大路线图保留为历史/兼容材料，但不进入默认上下文，也不与当前状态竞争。

## 理由

只读评估显示仓库 113 个受控文件、约 3.8MB，核心检查通过、测试通过、远端与历史完整。可复用资产真实存在；问题是文档控制面冗余和默认注入过重，而不是代码或数据不可恢复。删除重建会丢失有价值历史并制造新的迁移风险。

## 后果

- 默认启动成本显著下降，深入材料仍可按路由发现。
- StorageGovernance 成为首个登记项目；hub 只保存便携路径和权威指针。
- accepted 里程碑/治理轮在有效所有者授权下形成一个 scoped commit/push。
- 飞书保留为未来适配器，不成为当前事实源或本轮实现。
