# 会话路由与无人值守执行设计
PCH-REBUILD-20261020 / 1.2.0-20261023-package1。静态执行契约；当前 Goal、采用和下一步只看受支持原生工具及原 State 回执。文件生成不恢复 Goal 或切换服务。

## 1.2 源码候选阶段

原任务增加严格的 source_admission：确切实现依赖、输入 hash、owned 路径及开发工作区。受信开发能力与候选证据回调缺失时默认拒绝。plan_source_ready 仅回答源码是否可实现；普通 plan_ready/admit、ROUTES、权限和整项 accepted 继续走原链。

源码证据保存在既有 plan_inputs 的版本化封装中，不创建第二任务队列、普通 tasks 行或新数据库；不占用原任务唯一的真实执行 package。源码 candidate 只能满足明确的源码依赖。UNKNOWN 跨定义版本保留，必须以精确 reconciles 引用及受信核对解决；取消与控制变化继续优先。旧历史、预算及未变验收适用性保留。

普通实现先完成一个功能候选，再做相称检查；旧 engineering_review 标签不自动触发三角色或逐补丁审批。真正高影响边界及平台强制控制继续保留。

以下 1.1 文字的“P3 保持 ROUTES”适用于真实效果与完整验收；源码实现按本节与本版原任务策略准入，不将 P3 改标 bootstrap。当前采用、失败和下一步只存原 State。

## 已存在与尚待建立
已实测本机Codex CLI 0.163.0；独立项目调查使用真实cwd、read-only sandbox、JSONL、output-schema，收到thread.started立即落盘。调查路由、原生ID、提示hash、尝试、起止、退出及usage在本地私有索引。session_id接口未提供时为null。各项目会话编写本地PLAN与TASKS，Root核对原稿并整合跨项目依赖/节点拆分后逐仓串行保存，原稿保留在私有回执；这不等于产品可执行。

现有codex_adapter精确锁0.159.3，不能直接服务本机0.163；TaskService普通契约仍不可用，hub_worker只允许特殊fixture/CSS，SessionService未含exec来源。升级须握手/schema/事件/隔离/中断实测，不能只换版本字符串。当前Web运行冻结5b933…，refresh仍3150…且最近exit2，当前脏树HEAD又不同，先做P1-R1-PCH-BASE。

现有TaskStore/TaskWorker有事件幂等、15秒lease、3秒heartbeat、PID/start_ticks、意图与requires_reconcile；ManagementStore有CAS。容量约束128任务/2048事件/16MiB，P2-R2-PCH-DAG先做32项admission窗口与兼容容量方案。禁止直接将全量长期计划塞入或另开任务DB。

## 路由与启动
route:project_id → 私有清单中的真实root/worktree/branch → adapter版本 → thread_id、session_id → task/attempt/run。首次及每次实质版本漂移均验证pwd、Git或非Git身份、指令、写责和输入。不能用创建会话代替领取/执行，不使用--last，也不假定exec在App会话目录可见。

已验证的调查参数为codex -a never exec --cd <真实根> --sandbox read-only --json --color never --output-schema <真实schema> --output-last-message <私有结果> -；非Git才加skip-git-repo-check。本轮按任务调用配置停用不需要的连接器，未改全局配置或认证。

恢复用同一根与精确thread ID的exec resume；本地实测结果见私有适配证据。执行期workspace-write能力、普通任务grant及Goal控制仍须P2实证。调查权限不自动变成执行权限。

新运行时尚未可用时，Root按本次目标授权与实际项目写责，通过已验证原生项目入口启动P1基线、同步修复、PSH最小策略及P2适配任务。先在现有STATE本任务子条目记bootstrap intent/输入hash/真实ID/回执引用，不新造队列或状态库；项目由各自会话写，跨仓效果、验证和Git串行。TaskStore就绪后一次性按原task/attempt ID导入，核对效果并将bootstrap转为只读投影，不能重派已完成工作。PCH代码由PCH项目会话实现，主协调不代写全部项目。

## 状态与就绪
planned → ready → dispatched → claimed → running → verifying → integrated → done。确有工程风险才有technical reviewing/rework。租约、预算、输入版本、精确写范围与依赖均满足才领取；回执通过schema/版本/实际工程条件、集成和同步才完成。
只有实际原主观节点使用可选decision_requested → proxy_decided分支；无此节点的任务不经过它。不存在waiting_owner、owner_accepted或本人事后回看完成态。历史human记录只保留历史身份。
retry_wait、requires_reconcile、blocked_external、failed、cancelled分别保留原因和恢复条件。一个项目离线/返工只影响真实依赖，不将related_to或管理归属转成执行边。没有ready时普通进程等待事件或next_retry_at，停止模型空轮询。

## 幂等、恢复与控制
先持久effect intent，再启动原生尝试，首次thread ID即记；结果前中断先核对原生turn、文件/产物、提交及远端状态。效果已发生则补回执，确定未发生才有限重试，未知隔离该依赖组。SQLite事务不能保证外部exactly-once，采用幂等与效果核对。
一项目默认一个写者，Root串行跨仓效果。worktree只隔离文件，不隔离端口/DB/GPU。先核对已有任务与重叠文件，保护其他脏树、游戏和GPU所有权；不是看到进程或idle就抢写/杀进程。

用户pause/cancel/改目标是控制，优先于ready选择；目标控制revision在每次派发前验证。P2必须实测本机受支持的原生Goal接口和宿主事件，绑定唯一目标意图，不用私有RPC/修改Codex数据库。宿主不支持某接口时按同一执行契约适配并写明能力，不能编造CLI --goal/--max-tokens旗标。
暂停后新派发0，2秒确认、5秒开始精确中断为设计目标；预算耗尽/用户暂停不能被普通重试恢复，也不新建Goal重置。仅浏览器关闭或无人回复不是取消。原有明确暂停的业务不会因PCH接入恢复。

## 预算与服务
设计：累计最多480次应用层原生推理派发，包含本计划已发生调查与失败；不是provider内部模型调用次数或token硬限。lineage跨改名/新会话/重分解累计。CLI可见token usage按覆盖记录，Root/协作工具不可观察部分记unknown，不宣称完整总成本。若用户设原生预算，按真实支持接口及覆盖保守预留，不能靠外部线程隐藏用量。
初始1个效果写者，最多2个只读执行会话，GPU重任务最多1且须实际可用；本轮独立只读调查峰值3，已有CLI PSS点样本约137–180MiB/进程，不是全进程树峰值或资源上限证明。生产并发先做代表批次。每尝试最长15分钟为设计上限，超时先核对效果。
相同版本/失败方法不盲重试；证实瞬态仅再试一次；两次无进展必须诊断/换可逆方法。重试退避30/120/600秒、最长15分钟后等恢复事件。技术修复与节点返工沿原问题累计，预算耗尽保存精确恢复点而非完成。

未来扩展scripts/hub_worker.py的--run-ready/--status/--drain及personal-control-hub-worker.service；均为planned_to_create，创建任务P2-R2-PCH-RUNTIME，采用任务P7-R3-PCH-DEPLOY。当前仅Web/refresh受管服务与linger=yes已核实，没有通用worker部署证明。
用户systemd worker Restart=on-failure，关页面不拥有其生命周期；宿主休眠/关机期间不运行，恢复先reconcile。服务升级先停止新派发、持久化、核对writer，再由受管机制恢复。服务重启程序，不盲建模型任务。账号失效保留真实拒绝，由本人本地处理凭据；不复制auth、不触发GUI登录。
健康/启动/停止命令由上述任务实际实现并绑定schema与service身份。当前执行入口是原生项目会话的bootstrap，不把不存在的后台命令写成已能运行。

## 官方接口依据

原生Goal objective上限与goal/get、goal/set、goal/clear见[App Server Goals](https://learn.chatgpt.com/docs/app-server#manage-a-thread-goal)；非交互JSONL与thread.started/usage见[Codex非交互执行](https://developers.openai.com/codex/noninteractive)。公开接口文档与本机能力分别核对：本轮get_goal实测无活动Goal，CLI只读调查可用；执行期控制仍由P2适配任务实证。

## 首次派发与能力准入

MACHINE_PLAN.admission_policy是现有TaskStore导入器P2-R2-PCH-DAG待实现的选择规则。BASE priority=0，独立PSH-POLICY=10，其他=100；同优先级按稳定task ID。完成的历史节点只能依赖已完成节点。BASE是唯一首派发最高优先级，PSH-POLICY保留独立ready，不构成全部工程前置。新运行时前bootstrap白名单是机器清单中P1/P2任务；其他任务即使业务依赖ready，也须P2-R1-PCH-ROUTES完成的真实执行能力。此准入复用原STATE/TaskStore，不创建新队列或风格门。

恢复探测turn.completed可能沿同一线程累计；原始resume快照不能直接与原调查快照相加。私有索引分别记录前后快照和有条件派生增量；已知原生应用派发45次，Root/协作/平台内部用量覆盖未知。

## 原生能力边界与本次恢复

1.1.0终止私有Codex源码/二进制路线及尚未启动的v16竞态工作，保留全部原失败、消耗与窄证据，不安装或采用。ADAPTER沿原task ID仅实现PCH到官方原生接口的协议、事件、身份、中断与精确恢复。当前实际版本重新核对，不能把私有扩展称为官方能力。

领取前分别核对授权范围、所需能力和实际原生权限。只读调查、已有获准任务工作区写入及精确叶写分别记录；不以任务提示或事后diff冒充OS执行边界。没有适用授权/能力的任务保持真实unsupported/blocked，不扩大权限、不改平台安全控制。账号问题仍由本人本机处理。

沿现有STATE bootstrap保留已有P1/P2范围，P3继续要求原ROUTES真实能力，不新增授权。先沿DAG原ID核对同ID计划修订冲突；现有import_plan明确返回PLAN_CONFLICT_REQUIRES_RECONCILE。在原TaskStore实现/验证受支持reconcile增量后才能采用本版六项变化，保留旧定义、已验收适用版本、尝试与回执；不直接写DB、不换plan_id/重置历史。该增量未通过不能宣称新版正式准入。BUDGET/CONTROL/RECOVER按机器修订依赖推进源码/fixture工作，真实原生集成仍逐项满足才done。ROUTES、真实任务、RUNTIME、正式采用、故障恢复和60分钟两次跨轮次要求全部保留；不创建替代Goal控制或第二调度器。

原生Goal暂停/恢复只用当前受支持入口，先核对效果。当前工具可get/create及pause/complete/blocked，未暴露resume或objective edit；不能通过修改本地DB、私有RPC、向自身注入消息或create新Goal绕过暂停。已暂停时由用户在原生界面使用官方/goal入口恢复/修订，再以get_goal核实active；不能在此之前声称重启成功。

每次尝试≤15分钟、累计480、保留20次闭环/历史未知预留；具体已用数只读STATE并继续累计。本次重规划、调查、技术审阅也计数。无ready由已有确定性宿主等事件；宿主尚未就绪时保存唯一恢复点，不用模型空转。
