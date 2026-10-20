# 同步、画布与执行契约
计划 PCH-REBUILD-20261020 / 1.0.1-20261020。本文件为设计契约；代码schema尚需对应创建任务实现。既有来源声明、normalized record、TaskStore与ManagementStore优先兼容，不要求各项目统一技术栈。

## 单一写责
| 数据 | 权威写者 | 其他方职责 |
|---|---|---|
| 项目身份与获准根/字段 | PCH registry；变更须有本次范围或既有授权 | 登记不授予执行或外部效果权限 |
| 项目业务事实、资产、计划进展 | 各项目原唯一状态与原写者 | PCH只读投影；本地TASKS包是静态重构定义，不是第二状态源 |
| 原始偏好、策略、代理决定 | personal-style-hub | 当前明确指令优先；候选推断与本人反馈分开 |
| 手工关系、排期、画布布局 | Hub管理覆盖层 | 不反写来源；CAS与精确字段提交 |
| 任务租约、尝试、效果与回执 | 现有TaskStore/TaskWorker | 主协调派发，项目会话执行；不另建任务数据库 |
| 目标意图/暂停/预算 | 经实测的原生Goal控制或受支持适配入口 | 运行账本只投影控制修订；不能另造竞争Goal状态机 |

## 版本化最小接口
现有hub.connection.yaml与严格读取资格保留。新增接口均是planned_to_create：P1实现ProjectBinding/完成事件，P2实现TaskPackage/Receipt/Attempt，P3实现Graph/Action；P0文档已经给出最小约束，实施不等待整个风格阶段。

所有信封包含schema_version、稳定ID、project_id、source_ref与source_version/hash、fact_time、collected_at、origin、兼容版本。事实时间未知必须保留原因；采集时间不冒充事实时间。UTC落账、Asia/Shanghai显示，保留原时区；无时区不擅自推定。

ProjectBinding用逐项目登记条目、有效访问范围、根身份、字段契约与adapter版本计算binding_hash。整表registry_hash仅作审计。无关A登记变化不使B失效；B路径/权限变动及真正共享安全/契约不兼容必须失效。根目录迁移保留稳定项目ID与旧身份，禁止创建空根来消除离线。

Snapshot分开记录采集健康、来源年龄/适用性、业务字段完整度、业务状态、局部进度/单位/分母、产物可用性及工程结果。五项说明每项有source；代理解释单列；未知配具体缺口与调查任务。不同分母不求和。

Graph对象包括Project、Goal、Phase、Round、Task、Artifact、FileRef、Workflow、Run、Service/Tool、Decision及Review，可映射既有模型。ID由项目/对象类型/原生稳定ID组成，不以卡片位置、同名或路径basename作身份。Artifact修订绑定输入/源码/产物hash、用途、host与受控文件引用；同名多版保持独立。

Relation包含typed endpoints、类型、证据、来源版本、创建时间、origin与修订。managed_by/part_of是管理，depends_on/blocks是明确执行依赖，consumes/produces/derived_from是实际输入输出，references/related_to不阻塞。LLM推断标candidate；运行产出的边绑定run_id；候选接受只写该覆盖项。一般图允许环，只有任务依赖子图严格禁环；管理关系不授予写权限。

ViewLayout单独保存view_id/object_id、x/y/尺寸/折叠/颜色、revision。角色朝向、镜头3D坐标属于业务事实，不能塞进布局。对象可在多个视图出现，仍指向一个权威对象。列表与图读取同一投影；人工布局不更改来源。

ActionDescriptor声明action_id、对象/端口类型、真实handler、host、输入schema、能力/授权、效果类别、幂等与核对方法。打开文件、执行已定义动作、调度任务是三种操作。连线/布局/执行独立；连接任意图片不能假定模型支持；执行创建真实run_id并冻结参数与输入版本。文件路径文本、模拟按钮或空链接不算可用动作。Linux文件在Mac上用已有可信桥接/受控读取，验证设备及权限，不复制登录会话。

TaskPackage至少有task_id/lineage_id、plan_revision/hash、phase/round/project、真实repo/worktree/branch与基准、允许模块、输入版本、具体依赖、工程验收/交付/同步条件、风险所需技术审查、预算/超时/重试/幂等键/回滚、回执目标。owner_decision默认false只是静态默认值，不要求普通任务提交“不适用”材料。

Attempt保存attempt_id、lease/fence、原生thread_id/turn_id、可用的session_id、PID/start_ticks、effect intent、预算预留/实际coverage。thread、session、attempt不可互换；适配器未提供session保持null。Receipt记录领取、起止、实际基准与结束版本、改动/产物、检查、集成/同步证据、效果状态、未知、下一动作。相同回执ID同内容重放，异内容冲突；exit0或模型自报完成不能单独结算。

## 同步与冲突
来源事件是正常项目任务完成或获准状态文件变化的提示，不能直接替代来源回读。初始去抖2秒、低频补偿15分钟为设计目标；逐项目去重/退避、原子快照与源版本核对，读取重叠不能混合版本。同步器绝不改源来造传播成功。记录源产生→采集→账本→页面四段真实时间。

所有管理写使用request_id与expected_revision/hash；部分更新仅修改明确字段。关系接受不保存其余草稿，历史载入与真正回退分名，取消不提交。UNKNOWN结果先查询请求/版本再重试。源新版本与覆盖层冲突显式处理，不覆盖新数据。

## 原主观节点决策
DecisionRequest仅在已有原主观节点出现时携带original_node_ref、问题、候选、限制、相关产物版本、策略版本、可逆默认、deadline。返回选择、理由/规则引用、不确定性、具体返工或proxy_accepted、proxy身份、策略与输入有效范围、改判条件。
工程测试/构建/技术审核的结果与重试不发送到风格接口，也不等待它。本人事后回看是独立可选记录；可以形成偏好/后续必要任务，不能成为任务状态、DAG边、阶段门或整体完成条件。
权限、账号、购买、公开发布、用户明确暂停、真实设备/录音/学习练习等事实行为不能由偏好决定伪造。保留历史owner_accepted/human字段身份，迁移未来主观门，不追改旧结论。

## 兼容与回滚
先新增兼容reader/投影与schema版本，完成消费者合同检查后切读。注册账本迁移须核对存储与原writer并使用受支持工具，不能复制DB绕过inode身份。保留历史原字节、有效证据、原分母。回滚只恢复本候选reader/配置/投影；真实外部效果另行核对。JSON Canvas仅可能作交换格式，不能代替业务/执行契约。当前无React构建依赖，先用原生ES/SVG验证；新图依赖必须有试点与成本证据。

## 具体对象信封与兼容窗口

最小结构示例（设计，P1/P2/P3对应任务负责实现严格schema）：

```json
{"schema_version":"1.0","project_id":"stable-id","source_version":{"code":"sha-or-nongit-fingerprint","state":"revision+hash","binding":"permission+declaration-hash"},"object":{"id":"project/type/native-id","type":"Artifact","revision":"v2","source_ref":"controlled:source-id","fact_time":null,"unknown_reason":"source has no timestamp"},"collected_at":"UTC","origin":"source_fact"}
```

事件键使用project_id+source_revision+event_type，投影read-after-change核对相同source_version；partial batch保留每项目结果。跨项目消费者固定producer schema/输入hash，变更后受影响消费者明确stale，reference边只提示。schema major不兼容拒读并给恢复任务；minor新增可选字段保持旧reader，至少覆盖一轮已验证旧消费者回读后才移除旧映射。未知major不猜字段，不让LLM解释成为权威事实。

Receipt中的checks[]各自包含command/steps、exit、scope、candidate_hash、evidence_ref；integration与sync包含相同source revision及四段时间，不以单一passed bool合并。Decision只保存代理身份，不转换成本人授权；retrospective为独立集合，运行DAG不引用其存在或数量。
