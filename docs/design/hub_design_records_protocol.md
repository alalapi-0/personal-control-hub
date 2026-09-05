# Hub 本地设计记录协议 · 1.0 候选

读者：Hub 数据层、审核界面和维护工具的实现者。目的：说明设计事实与决定事件的实际边界。更新触发：schema、保存、过期或导出语义改变。当前执行状态仍以 `STATE.yaml` 为准；本文件不表示 UI 或 Goal 已验收。

## 数据与身份

设计数据保存于一个 JSON 事实库。`facts` 存不可替换的 baseline、candidate、review 和 artifact_ref；`events` 存追加式决定；`requests` 保存每次已提交操作的幂等回执。历史、当前决定和队列从事实重建，不另存可编辑的当前选择表。

记录和嵌套对象采用精确字段校验。未知版本、字段、枚举或引用拒绝写入。scope 成员必须按 project_id 排序，各成员 pages 也必须按字典序排序；未排序输入在保存前拒绝，不静默改写已提交内容。这样相同成员/页面集合具有唯一范围哈希，跨候选的同范围选择仍须接续 supersedes。原版与候选均通过 artifact_bindings 保存工件 ID 和 SHA-256，摘要进入各自内容哈希。同一候选的 ID、revision、content hash 绑定项目/页面、基线、视觉配置与工件摘要。变化产生新 revision，不能覆盖已有身份。规范化 JSON 哈希不依赖字段排列。

成员项目来自 Hub registry。`design_family` 是带 revision/content hash、明确成员/页面、共享语义和来源证据的不可变设计事实。非空 family 候选绑定精确家族身份；家族变化使旧决定过期，不能自动扩展成员或确认正式 program link。独立项目继续保留自己的基线和材料。合成演练使用独立的 fixture 项目与事实库，不加入真实项目名册。

## 保存与来源

库写入使用 `expected_revision`。获得短暂跨进程文件锁后重新读取、比较版本、追加事实与回执，再同目录原子替换。事实和事件数组必须与提交回执顺序一致，不能通过重排数组改变当前投影。相同 request ID 和相同内容重试返回原回执；内容不同或预期版本冲突明确报错。锁只协调这份 Hub 事实库，不代表锁住其他 Agent、仓库或 Git。

锁文件保持存在以避免锁 inode 被替换后出现两个写者；它是有用途的控制文件，不是备份。事实文件和锁必须为普通文件，事实库上限为 16 MiB。临时文件在完成或失败后清理。原子发布前失败保留旧内容；发布后若目录同步失败，校验已发布字节并报告 `COMMITTED_DURABILITY_UNCONFIRMED`，带路径、摘要及适用的 revision/回执。不能把已经保存的操作报告为普通未提交失败，也不尝试回滚；调用方用原 request ID 重试可解析同一次提交。

选择、需要修改、暂不决定和撤回都是事件。后续同范围事件须指明其替代的当前事件，旧历史保留。真实决定需要调用方单独提供明确的可信用户来源，不能仅信任输入记录中的角色或授权字段。这个库参数不是身份认证系统；未来本地 UI 接口仍须校验 loopback 请求来源并从明确用户操作建立调用上下文。

本单元 CLI 不提供伪造用户选择的批量导入开关。演练事实可为 mock 或 dry-run，但相关基线、候选、家族和材料的分类必须一致。两者仅可进入隔离 synthetic fixture 库；所有演练事件仍为 synthetic，真实选择计数始终为零。设计选择不产生实施授权、代码变化、回滚或发布。

## 过期与材料

基线按项目/页面关联；无关页面的新基线不使其他页面失效。新的来源观察作为新基线记录进入事实库后，候选 revision、适用范围或相关基线变化会使旧决定推导为 stale。历史仍可读，旧决定不会转移到新稿。本单元不自动扫描外部 UI 源码；实际来源观察适配由后续接入能力提供。

本地预览丢失或 Figma 离线不删除历史。导出时必须核对实际工件字节、哈希、分类和适用范围；缺失或过期明确失败。导出有效但未选择的候选时标注未选择，不要求先作决定。

材料 ZIP 包含精确候选、所绑定家族和各项目基线、相关审核/反馈/决定、来源说明，以及校验后的本地基线/候选工件。各基线材料按自身项目/页面验证，候选材料匹配完整候选范围。只收集明确关联的 Figma 引用，不因范围相同混入其他候选的指针。Figma 引用不被称为可编辑的本地原稿。包内材料来自 Hub 任务工件目录，不能从任意外部项目、真实业务素材或秘密路径打包。导出不能覆盖事实库、源工件或已有交付文件；发布后的持久性不确定结果同样须明确报告。

## 调用入口

Python 实现位于 `hub.design_records`、`hub.design_store`、`hub.design_export`；`scripts/hub_designs.py` 避免仓库根部旧 `hub.py` 的包遮蔽。

```sh
python3 scripts/hub_designs.py demo --output-dir docs/reports/ui_design_governance/unit-02/fixture-demo
python3 scripts/hub_designs.py validate --fixture --store docs/reports/ui_design_governance/unit-02/fixture-demo/fixture-store.json
python3 scripts/hub_designs.py history --fixture --store docs/reports/ui_design_governance/unit-02/fixture-demo/fixture-store.json
```

首次演练输出目录必须尚不存在；重启回看使用 read/history，不重新建立另一份事实。普通校验或发布前错误输出结构化 JSON，CLI 退出码为 2。已发布但持久性不确定或发布后校验失败分别保留明确状态、路径和摘要，CLI 退出码为 3，演练也不会吞掉这些结果。导出已提交而临时清理失败时保留已验证的结果并返回清理警告。验收命令、结果与限制登记在对应单元证据中。当前不创建真实用户决定，也未建设产品界面；正式 Hub 使用同一库能力，不能另做一套页面事实。
