# Hub 选稿页交付说明 · 2026-09-06

本次成品为 Hub C 排版 / P5「暮紫杏光」柔和材质 v3，以及设计选择页。页面版本提交为 `9dba9d8bee1f83d2f278af6ea3b24e91710bcca2`，范围记录提交为 `0bbe0314ef34bac2966343af6129ff0243e65b6b`；两者已正常推送并核对远端。最终说明的验收与交付记录在 [final/](ui_design_governance/final/)。

所有者已[明确调整范围](ui_design_governance/unit-17/owner-decision.json)：学习计划保留原版，本轮只完成 Hub 选稿页。其他项目的设计与实施由所有者以后自行安排，可能使用 Cursor；本任务不启动、配置、操作或派发 Cursor 工作。

## 打开与使用

当前入口：[Hub 设计选择](http://127.0.0.1:63078/#designs)。

设计列表 → 项目候选 → 原版/候选对比 → 填写反馈 → 记录决定 → 展开历史 → 生成校验导出。桌面双栏对比，手机切换原版/候选；可切换桌面/手机预览、放大图片及打开 Figma 来源。选择设计与代码实施授权分开，导出包包含版本、范围、历史和校验后的材料。

学习计划的「安静任务台」已记录为 **暂缓、未选用**，反馈包含所有者原话及 Root 代为记录说明；外部原版保留。本模型没有“保留原版”专用动作，因此使用现有 defer 表达不采用候选，没有伪造一次原版/候选选择。[决定与回执](ui_design_governance/unit-17/decision-receipt.json)可追溯。

## 运行与校验

在仓库根目录执行：

```sh
python3 -B scripts/hub_server.py --host 127.0.0.1 --port 63078
```

前台运行时 Ctrl-C 只停止该实例。当前实例由本任务启动，监听本机63078；端口被占用时先确认是否是这个实例，不要停止无关进程。默认端口也可使用8766。已验证 Python3.14.5、PyYAML6.0.3，无前端构建步骤；测试使用现有 pytest 命令。

只读校验及历史入口：

```sh
python3 scripts/hub_refresh.py validate
python3 -B scripts/hub_designs.py history --store data/design_governance/design-store.json
python3 scripts/check_repo.py
python3 scripts/round_consistency_check.py
```

以后需要更新获准项目的摘要时，可用页面刷新或 `python3 scripts/hub_refresh.py refresh --request-id <唯一请求ID> --project <项目ID>`。同一操作失败或结果不明时使用原请求ID核对/重试；本轮收尾没有再次刷新任何外部项目。来源离线或变化时保留历史及明确未知/过期状态，不用旧结果冒充当前状态。

## 当前数据与验收索引

唯一当前状态是 [STATE.yaml](../../STATE.yaml)，唯一项目名册是 [external_projects.yaml](../../data/registry/external_projects.yaml)。管理连接采用 manifest-v5、authority-bundle-v4 / source-plan-v4，schema1.0，SQLite刷新账本保持 head130。24项管理覆盖为20项当前权威读取和4项获准例外；详见 [TC16逐项结果](ui_design_governance/unit-16/refresh-validation.json)。Manga保持可见例外与禁读边界，其他例外不被伪装为成功实时读取。

当前 UI 范围由 [scope-resolution-v1.json](../../data/design_governance/scope-resolution-v1.json)及TC18验收确定：13项有自有UI，8项无自有产品UI，3项受保护。这是范围证据，不是后续改版任务清单；manifest-v5保留历史启动时的范围值。

[设计存储](../../data/design_governance/design-store.json) revision15：12个事实、2个决定。原Hub C/P5选择保留；新增1个学习计划defer。所有既有事实、决定和请求前缀均保持，重复请求不增加事件。Hub的[Figma选稿来源](https://www.figma.com/design/AjYCtyxV5mmXNqWPtJQRQ4?node-id=91-2723)对应TC14，实际柔和材质v3以TC15已验收代码为准。学习计划[Figma对比稿](https://www.figma.com/design/qHIMgQnOulj5TEEYk9Yaod?node-id=13-13)仅保留为未采用的设计材料。

| 验收内容 | 权威证据 |
|---|---|
| Figma连接身份验证 | [TC12验收](ui_design_governance/unit-12/governor-v1.json) |
| Hub可编辑方案、选稿及实现 | [TC13](ui_design_governance/unit-13/governor.json)、[TC14](ui_design_governance/unit-14/governor.json)、[TC15](ui_design_governance/unit-15/governor.json) |
| 全量管理连接 | [TC16验收](ui_design_governance/unit-16/governor.json) |
| 选稿页、保留原版决定、重启与导出 | [TC17 v3验收](ui_design_governance/unit-17/closure-governor.json)、[验证](ui_design_governance/unit-17/closure-validation.json) |
| 24项UI范围 | [TC18验收](ui_design_governance/unit-18/governor.json) |
| 管理材料归并与原位保留依据 | [TC6清单](ui_design_governance/unit-06/consolidation-inventory.json) |

TC6已归并确认属于Hub的管理材料，存储治理唯一执行STATE位于 `governance/programs/storage_governance/STATE.yaml`；有实际消费者的旧入口、独立工具与宿主配置按清单原位保留。本轮没有再次迁移这些材料。

## 导出、验证与恢复

[学习计划材料ZIP](ui_design_governance/service/exports/real/ui-export-3aec346e-2bed-4e59-bb26-a2744fd070b3.zip)包含4张脱敏预览、Figma引用与决定历史；SHA256为 `313bb3ec4b5bcbe4e96e6dac7349bf90eab23b7292c0da7f5440af8a3f6f621e`。它是 **unselected** 导出，selection为null，real_selection和implementation_authority均为false，不是应实施的新设计。

本次验证：187项Python测试及140项子测试、10项Node测试通过；页面桌面/手机、键盘焦点、减弱动效、重启、幂等与下载校验通过。最终浏览器运行没有console错误、失败响应或外部请求。保留10项既有路线图软警告和Node模块类型提示；旧三个authority bundle的registry漂移属于历史，新bundle-v4匹配。未修改外部业务代码、读取Manga或操作Cursor。

数据恢复前先停止自己拥有的Hub实例，保留当前设计存储和刷新SQLite的一致副本，再按明确的已验收版本恢复，避免手工修改事件、哈希或删除历史。设计决定通过后续事件表达变化，代码修复保持任务范围。Git交付分支为 `origin/agent/governance-closure-20260812`，没有合并main或强推。旧TC18 store14保护断言是授权后继事件之前的历史验证；当前store15用 [verify_closure.py](ui_design_governance/unit-17/verify_closure.py)验证授权增量。

既存的其他任务dirty文件保持原样，不属于这次提交。TC17隔离学习页面服务已停止，Hub页面继续可用。后续其他项目设计不属于本轮未完成项。
