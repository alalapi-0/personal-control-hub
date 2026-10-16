# PCH 功能优化审核包

状态：**with_gaps**。真实 provider 的合成目标读取、追问、取消并行和重开已有证据；正式运行切换、其余实际分支、真实设备及所有者最终接受仍未完成。98 项原始预期及分母全部保留，G10 单独记录。

## 候选与入口

工程候选在已交付代码上修复普通任务回复下方多余的 `null` 文本，截断警告保留；当前结果模块 SHA-256 为 `5e37e5ecca64eb0cfeee03dcb92411bf805e4834c137e5d8a1330d6d3f32892f`。后端执行文件资格与绑定、实时执行阶段及显式只读两任务并行均有各自后续冻结证据；当前固定产品基线为 `150014bf4357a1137839184422932def6ff09767`，私有完整 CLI/配套程序与真实通道证据另行冻结。实际取消并行、只读刷新、列表重开及执行阶段/忙碌意见均有后续证据。另提供冻结代码与既有真实 Hub 数据的独立只读审核入口；最终远端提交及当前本地 URL 以唯一 State 的 git_delivery / review_entry 为准。

独立演示为真实 HTTP/静态资源配合合成来源的只读访客实例，已验证资源哈希、停止和重启；未配置可登录的 owner，没有模型、worker 或 timer。正式 `8766` 服务保持旧版本。演示数据不能证明正式业务或真实 provider 通过。

历史真实数据只读批次固定在 `bd36fc0ec64746f23ced0f6f2a835dfe37d87268`，其 25 个 Web 文件与两个只读后端文件仅按 `6665b0ef04d422513d86b3b4d69435a4948cc571` 的历史字节桥接。最新审核入口改为与固定产品基线 `150014bf4357a1137839184422932def6ff09767` 全部 133 个程序文件一致的独立只读实例，正常通过 HTTP 提供修复后的 CSS；其 74 个 GET、零资源替换、九个宽度及实际 Chrome 200% 已独立复核。当前 URL 以唯一 State 的 review_entry 为准。执行器、claim、Owner 或正式切换不因只读页面证据而通过。正式数据与产品代码通过独立文件系统命名空间只读挂载，所有修改请求在产品分派前拒绝。该入口仅开放静态资源、访客/身份/账本头、项目目录及 A/B 详情读取；没有任务、消息、Owner 登录、provider 或来源刷新。`sync-status` 明确返回受限 503，未调用正常端点的系统服务查询。原合成演示仍单独保留，二者不混用。

可重复隔离启动：使用仓库开发依赖的 Python 3.12 环境执行 `python tests/pch_functional_fixture.py --serve`，按 stdout 的随机回环 URL 打开；stdin `{"action":"stop"}` 正常收尾。普通任务、故障、并行及真实短周期 timer 的完整自动旅程入口见下方证据索引。保留给本次审核的独立实例、精确 PID 和停止/重启命令存放在本地唯一 State。

## 验证与适用边界

- 新增执行文件入口的 Python 3.12.3 固定依赖检查：pytest 1529 + 1704 subtests；unittest 1457；Node 252；其余六项仓库、registry、project-data、schema 检查全部退出 0，四文件代码候选哈希在检查前后相同。上一 Owner 准备候选的 1513 / 1658、1441 及更早核心回归的 1474 / 1609、1417 记录仍保留；本次独立复核结果由唯一 State 的对应候选记录保存。
- 17 组浏览器配置，加任务失败/断连/真实 10 秒 deadline、定义/多角色/工作副本/三方冲突补充，均有真实 HTTP、静态页面和 store 效果。模型是明确的受控替身。适用性按未改变的产品、默认 fixture 和各模式记录，未按“文件存在”授分。
- 三次真实用户级 timer 自动触发均记录新来源→账本→页面，随后移除精确 owned 测试单元；不是模拟时钟。正式自然业务更新仍未验。
- 390×844、1280×800、1440×1050 和 Chrome 实际 200% 缩放有证据；42 次合成画面对比度测量、动态 reduced-motion、键盘和 AX 检查保留其边界。没有把模拟窗口当手机、AX 当真实读屏或布局数字当用户理解。
- 控制台与网络原始负向事件保留：身份拒绝、错误注入、断网、丢回执、CAS、显式重启短断均逐操作归类；不能称原始错误总数为零。17 配置的收尾错误均为零。
- 本轮发现并修复并发账本假损坏：head 多次 SELECT 没有固定快照。真实 WAL 读中提交和校验后提交两项旧代码失败，读事务修复后通过；99 项刷新不再误报 LEDGER_CORRUPT。首个失败、3 秒测试等待超时与过宽标题选择器失败均保留。
- 正式两条候选来源 SHA 与逐字段值仍匹配其旧采集结果；正式 owner 未配置、execution disabled；正式页面与新候选资源不同，未合并为同版本 PASS。

- 后续正式存储只读核验：刷新库 schema 2.0、任务库 schema 2、注册 profile 2、设计库 1.0、工作台 1、管理配置 1.3；任务注册、库内 profile、数据库和物理目录身份一致。六个精确文件读取前后内容与身份稳定，无 SQLite 侧文件；未查询任务或消息正文。这只证明已检查的结构与身份，不代表完整记录、事务、正式重启或可写迁移验收。
- 正式每日刷新最近一次已提交 39 条结果，其中 Hub 自身一条 invalid 导致退出 2，timer 仍等待下一日历触发。失败记录只保存通用错误；对当前旧代码的独立只读复现捕获了自身 State 超过 1 MiB 读取预算。不能把当前复现当成已恢复调度，也不能由通用历史错误反推当时所有细节。
- 后续真实 provider 页面测试确实发送并完成了 1 个合成只读任务，收到实际回复；读取工具在启动配套 `codex-code-mode-host` 前因文件不存在失败，marker 验证未通过。首次零效果的页面刷新失败也保留。配套程序已从同一冻结源码和锁文件构建，V8 产物按上游固定摘要验证；精确程序的 EOF、stdio 握手、JS 固定代理调用、输出和关闭检查通过，新增模型调用及原生线程均为 0。这些无模型局部检查与下述实际页面结果分别记录。
- 所有者明确增加 1 个会话、1 个回合后，完整包实际页面补测已执行：A/B 分别读取正确 marker、线程隔离、A 同线程追问及重开结果页通过。取消 A 的 HTTP 请求返回 200 并持久化标记，但测试脚本立即要求终态，错误地触发收尾，B 先被收尾中断；“取消 A、B 继续完成”失败。现有无模型并行取消测试通过，证实应等待 worker 的异步取消结果。后续只读原生查询确认 A/B 忙碌回合均 interrupted、线程 notLoaded；A 在 Hub 中仍需对账，未篡改旧记录或重新入队。最终桌面/移动回复截图未执行，已有 A 截图只展示结果页上部，不能证明回复在屏内。业务输入和配置未变，精确 adapter、测试浏览器和显示均终止。

- 后续取消补测已通过：仅 A 的精确 thread/turn 收到中断，B 独立完成且 cancel=0。原 r3 的提前断言与 B 收尾中断失败完整保留。随后复用同一真实任务库，仅查看、刷新、回列表并点链接重开，TaskStore 身份/内容/行数、业务输入、配置及 native B 的 completed turn 列表均未变；新增模型、线程和中断均为 0。桌面/390×844 实际页面已核对回复可见、无多余 null、无横向溢出，252 项 Node 检查通过。

- 执行阶段/忙碌意见补测通过：A 显式存草稿时没有任务或模型启动，发送后有 queued 回执和精确原生 running 事件；F 在 A 忙碌时持久排队，页面解释未追加到活跃回合。B 新会话在 A 仍运行时完成；A 的实际 provider 回合约 58 秒，之后 F 才核对 completed 父回合、恢复同一 A 会话并启动新回合。三任务均 checks_complete/等待验收，0 中断，原始 DB 只读复核不变，99 个精确测试进程身份全部终止。新增 2 个会话、3 个回合，该历史批次结束时累计 7/11/2。后续显式真实只读并行补测增加 2/2/1，当前累计 9/13/3，仍在同一次 15/18/12 授权内。

- 历史 129 文件批次与真实 Hub 数据的只读审核通过：43 个唯一项目；A/B 实际概览、排期在 1440×1050 和 390×844 浏览器窗口可读、无横向溢出，管理保存保持禁用。该批次 47 个浏览器请求无修改或外部请求、无页面异常；仅有预期的受限 sync-status 503。129 个产品文件和六个正式输入文件未变，无 SQLite 侧文件。首轮脚本误期待旧快照文案而失败，原始证据保留；仅修改私有断言以匹配真实 authority drift，未改产品或数据。
- 两个项目均存在 authority drift：registry authority 一致，代码内置 `record_schema()` 的当前规范与旧账本记录不同；磁盘 schema.json 不是该运行时 authority 输入。用途、产出、角色、别名、主项目和工作副本六类定义缺失，继续显示未知。恢复需在正式写责明确后按当前 resolver 真实读取并追加新记录，保留旧历史；不能替换旧 authority 或关闭 stale 判断，刷新也不会生成不存在的定义。
- 只读实例已实际正常停止并以同一候选重启：旧四个进程身份均终止，新 loopback socket、命名空间、只读挂载及无 Owner 环境重新核对；129 个代码文件、六个输入及 A/B 稳定 DTO 内容一致。浏览器旅程在首实例执行，重启后以同版本/同输入适用性核对，未声称再次跑过 UI。测试浏览器及私有显示均关闭；只保留明确审核入口。该证据不代表正式 8766 切换、原 R 用例整体通过或所有者接受，98 项计数不变。

- 后续正式就绪检查发现真实工程缺口：原 ordinary runtime 与 Web 启动器只接受合成授权，Owner 配置和服务切换本身无法补齐正常业务任务路径。已新增显式 `--ordinary-real-readonly-grant`，复用现有已注册真实只读授权、TaskService、TaskStore 和 TaskWorker；默认关闭、精确输入/存储/登记/有效期检查不变。后续并行补齐使两个同类、不同项目且物理工作区不重叠的真实只读任务可同时执行，并在 SQLite 原子 claim 内限制真实任务总数为两个；同项目、混合类别及不满足原有核验的任务继续隔离。测试两槽模式兼容。此修复的验收及候选身份以唯一 State 为准；受控适配器的合成磁盘检查不代替原生 provider 并行、真实业务或正式激活验收，原 98 项结果不据此晋级。

## F01–F09

| 问题 | 工程结果与证据 | 仍需的实际分支 |
| --- | --- | --- |
| F01 用途/产出/角色/恢复条件 | 已实现来源定义、证据、已知/未知/多角色和行动摘要；navigation.definitions、edge.B02B03B05R03Definitions | 正式缺失内容不能由 Agent 编造，用户理解与业务接受待审 |
| F02 首页找路 | 搜索、项目列表与关注内容前置；navigation.firstScreen/directoryMatrix | 初次真人找路耗时待审 |
| F03 页面层级/术语 | 概览、任务、排期关系、来源分层；保留来源原文并解释状态；navigation.sections、keyboard | 正式业务术语及理解待审 |
| F04 返回上下文 | 搜索/筛选/滚动/后退前进；navigation.history | 正式新运行版本待审核 |
| F05 错误含义/恢复 | 401/403/404/409/500/503/失联/超时及原请求恢复；navigation.errors、ordinary、resilience | live 与正式 owner 通道仍受限 |
| F06 关系层次 | 来源声明、已确认管理关系与提议分别显示；方向/反向/历史/CAS/三方冲突已验 | 正式关系内容及接受待审 |
| F07 完整性误导 | 来源读取与业务字段完整性分开，未知不填零；navigation.definitions、source failure | 正式数据缺口仍真实存在 |
| F08 别名 | 只使用明确确认别名，检索可恢复；navigation.directoryMatrix | 口述项目身份已获所有者确认；正式任务绑定及正式页面检索仍待核验 |
| F09 日期错误 | 字段级原因、关联描述、键盘修正、时区/跨年/DST/未排期；management、keyboard | 实际设备/辅助技术体验待审 |

- 后续真实只读目录补测：名称与 ID 检索、口述词及清空、状态与来源筛选、四种分组与四种视图的 16 个组合、后退前进及焦点恢复、A/B 项目深链刷新、无效项目子页恢复、纯键盘查找和排期导航均已实际执行。390×844、Chrome 实际 200% 缩放及 reduced-motion 分别记录；新增 142 个页面请求仅含 GET，预期受限 sync-status 503 单列。三次私有验收脚本错误（折叠树链接定位、路由尚在读取便断言焦点、自动化按键未达到真实 200% 缩放）保留，修正后完整旅程通过；产品与六个正式输入未改。此证据不覆盖不存在项目 ID、真实任务深链、真实设备、正式服务或用户理解，98 项结果不据此晋级。
- 实际 200% 截图发现 720 CSS 像素时导航和装饰文案挤成竖排；仅在 701–1000 像素调整品牌弹性、隐藏装饰文案并保持导航文字单行。候选 CSS 在严格只读真实数据页面以明确的浏览器资源替换验证，原服务未改；390、600、700、701、720、900、1000、1001、1440 九个宽度及 Chrome 实际 200% 均无导航折行、重叠或横向溢出。该轮新增 73 个页面请求，其中 3 个 CSS 响应是明示候选替换，其余读取仍来自原只读实例；此证据不冒充正式部署。
- 正常只读浏览实测：61 个采样点覆盖 60.001 秒；登记 1 个服务、20 个已观察浏览器进程及 1 个显示进程。相邻同身份计数的 CPU 增量分别为服务 19.84 秒、浏览器 3.77 秒；采样时点 RSS 求和为服务 98.4–143.8 MiB、浏览器 1263.0–1692.4 MiB。共享页重复计入，不能当独占物理内存；新生/已退出边缘及未确认脱离子树的进程不补零或冒充完整 CPU。55 个 GET 的请求耗时中位 155.361 毫秒、范围 1.114–1608.598 毫秒（responseEnd 相对请求起始）；含 7 次预期受限 503，均单列，无页面异常、失败、未结束、修改或页面外部请求。响应均为 no-store，未拦截资源或强制缓存。只读 wrapper 的日志及采样开销未分离，主机汇总仅作同期背景；不据此判断正式容量、GPU 无争用或性能合格。所有登记测试客户端及显示正常关闭，N-11 整项仍未验。
- 证据版本已分开：原核心/B10 字段是历史快照；后续原生、真实只读及当前完整回归各按精确候选和适用文件引用。原 30/17/51 审查快照不冒充当前 37/10/51；G10 单列。

## 98 项结果

当前结果：**37 PASS、10 BLOCKED、51 UNVERIFIED；0 FAIL、0 NOT_APPLICABLE**。T-04、T-05、T-06、T-07、T-08、T-10、N-03 按其原始 C/C-I 语义补齐；旧 30/17/51 和 35/12/51 记录保留，不为这些条目另加正式 owner 或正式业务对象门槛。每 ID 仅有一个总结果；PASS 只用于该项实际所需的隔离或效果边界已满足的情况。正式/live 未完成的条目保留 BLOCKED/UNVERIFIED。原始 23 PASS / 18 FAIL / 25 BLOCKED / 32 UNVERIFIED 不覆盖、不改写。此计数不是 Goal 完成率。

| ID | 原始场景及预期 | 原结果 | 当前总结果 | 观察与限制；确定性证据入口 |
| --- | --- | --- | --- | --- |
| A-01 | 从正式首页核对三十天项目及Actor 4名称、用途、已有任务绑定；形成明确A/B映射；歧义被指出，不因发音或目录名猜定 | BLOCKED | BLOCKED | 所有者已确认口述项目身份；正式既有任务绑定、用途及当前运行页面的完整分支仍未验。 证据：所有者当前确认、nav.definitions、u:project_definition.py |
| A-02 | 初次打开首页找A，再找B，不提示搜索/导航位置；找得到且能区分相似名称；记录用时、首击、滚动和错路 | PASS | UNVERIFIED | 已有首屏可达和自动导航证据；未把重复操作计时当初次用户找路与理解耗时。 证据：nav.firstScreen、nav.taskNavigationBefore、resilience.navigationTimings |
| A-03 | 用正式名称、常用名称/关键词检索，再清空；搜索结果可解释；无结果可恢复；不误以为项目消失；别名不支持需记问题 | FAIL | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：nav.directoryMatrix、nav.directoryHistoryWrites |
| A-04 | 切状态、来源、分组、列表/树/甘特后打开项目；当前筛选与选中项目清楚；切视图不丢项目；全部统计与筛选数量不混淆 | UNVERIFIED | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：nav.directoryMatrix、nav.realPagination |
| A-05 | 从A到B再返回列表，用浏览器后退/前进；返回位置/筛选/项目上下文可理解；不反复迷路 | FAIL | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：nav.history、management.confirmedForwardReverse |
| A-06 | 打开项目/任务深链接、刷新，再访问不存在ID；合法链接可恢复对应对象；不存在有可理解返回路径 | FAIL | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：nav.sections、nav.errors、preview.pinnedTaskReopen、ordinary.drafts |
| A-07 | 从项目找任务/会话入口，再找对应产物；不依赖源码或外部提示；导航高亮和返回路径与用户任务一致 | BLOCKED | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：nav.oneClickTasks、nav.taskNavigationBefore、preview.materialReturn、u:sessions.py |
| A-08 | 无身份、合法身份、权限不足分别看同一路径；状态与实际能力一致；不会“已连接”即暗示能执行；认证说明可理解 | BLOCKED | BLOCKED | 正式 owner/正式业务运行分支未就绪；合成及真实 provider 局部证据不能替代完整正式验收。 证据：functional.guestDisabled、functional.ownerEnabled、ordinary.authenticationReasons、ordinary.runtimeAndRevocation |
| B-01 | A/B各自用一句话回答项目用途和主要产出；页面支持回答；无需把技术阶段、路径或任务编号翻译成用途 | FAIL | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：nav.definitions、u:project_definition.py |
| B-02 | 辨认业务项目/Agent/基座/连接器/工作副本等角色；角色可说明、可多重；技术栈标签不代替本质角色 | FAIL | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：u:project_definition.py、responsive.layoutMatrix、edge.B02B03B05R03Definitions |
| B-03 | 比较主项目与分支/工作副本；归属可见，不把同一项目副本重复当独立业务；能进入正确实例 | UNVERIFIED | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：u:project_definition.py、u:relations.mjs、nav.directoryMatrix、edge.B02B03B05R03Definitions |
| B-04 | 找当前目标、阶段、任务和执行者；含义可读，当前与长期定义分开；未知不被虚填 | FAIL | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：nav.definitions、u:connection_view.mjs、u:task_plan_pipeline.py、u:business_tasks.mjs |
| B-05 | 判断项目active但局部blocked的状态；两者可同时解释；不会单一绿色状态掩盖当前阻塞 | UNVERIFIED | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：u:project_definition.py#test_empty_unknown_not_applicable_and_active_blocked_zero_progress、management.confirmedForwardReverse、edge.B02B03B05R03Definitions |
| B-06 | 读业务进度及原始分母；范围、单位和依据清楚；工程完成率不当成作品/业务接受 | PASS | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：nav.definitions、u:business_tasks.mjs、u:connection_view.mjs |
| B-07 | 找阻塞原因、恢复条件和下一步；用户能据此决定行动；技术代码有可读解释；缺项有说明 | FAIL | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：u:connection_view.mjs、edge.D06D07SourceFailures、management.confirmedForwardReverse |
| B-08 | 区分采集时间、来源更新时间、预估和事实；读过旧文件不会被理解为业务刚推进；派生值有身份 | PASS | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：refresh.manualSource、preview.savedCapture、u:connection_view.mjs、u:timeline.mjs |
| B-09 | 多个业务字段未知、部分字段已知；已知信息仍可用；未知/不适用/无内容不同；不给整体“完整可用”误导 | FAIL | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：nav.definitions、responsive.layoutMatrix、u:project_definition.py |
| B-10 | 检查来源/管理/Agent建议相互矛盾的样本；权威层次和冲突可辨；建议不悄悄成为事实或已接受关系 | UNVERIFIED | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：management.proposalAndHistory、u:management_proposals.py、u:relations.mjs、edge.B02B03B05R03Definitions |
| D-01 | 核对A/B合法来源到Hub采集再到页面的关键字段；同一版本值和语义对应；不以读源成功替代字段正确 | PASS | UNVERIFIED | 正式两源 SHA 与逐字段值相同；8766 仍是旧运行候选。 证据：refresh.manualSource、refresh.cycles、u:project_service.py |
| D-02 | 观察现有自动采集的一次实际运行；无需Agent代读即可出现可核对的新数据/采集结果；仅手动刷新不算自动 | UNVERIFIED | UNVERIFIED | 隔离真实 timer 三触发通过；当前正式运行版本与自然批次链分开。 证据：refresh.timer、refresh.cycles、refresh.timerCleanup、u:refresh_timer_fixture.py |
| D-03 | 对A单项目刷新并查看B和全局反馈；请求只作用允许范围；B不被意外启动；反馈区分已接收/完成/失败 | BLOCKED | BLOCKED | 正式 owner/正式业务运行分支未就绪；合成及真实 provider 局部证据不能替代完整正式验收。 证据：refresh.manualSource、refresh.editorPreserved、parallel.sourceIsolation |
| D-04 | 来源自然更新后再次查看/按既有机制刷新；新值实际出现；只改变“读取时间”不算更新成功 | UNVERIFIED | UNVERIFIED | 本次未遇到新的自然源 SHA；受控修改不替代自然更新。 证据：refresh.manualSource、refresh.cycles |
| D-05 | 合法运行配置不具模型执行能力时浏览与采集；边界在副本补测；基础功能可独立工作；无隐式模型/业务进程启动 | UNVERIFIED | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：refresh.manualSource、functional.workerDisabled、functional.providerDisabled |
| D-06 | 来源文件缺失/损坏/不可读；仅受影响项目降级；错误可理解，其他项目仍可浏览 | PASS | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：edge.D06D07SourceFailures、u:source_atomicity.py |
| D-07 | 来源过期或读取失败但有历史成功；当前与历史明确，过期值不被当当前；保留可用线索 | PASS | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：edge.D06D07SourceFailures、u:connection_view.mjs、u:project_service.py |
| D-08 | 字段null、0、空列表及未排期并存；不把0或空列表误写未知；不把未知写0%或“无阻塞” | PASS | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：nav.definitions、management.calendarAndTimeline、u:project_definition.py、u:connection_view.mjs |
| D-09 | 项目超出单页数量、多个筛选组合；不漏读、不重复；总数、匹配数、资格分母有意义 | UNVERIFIED | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：nav.realPagination、nav.directoryMatrix、resilience.emptyVersusUnavailable |
| D-10 | 两个刷新请求重复/重叠；合并、排队或忙碌说明可理解；不产生相互覆盖/重复效果 | BLOCKED | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：refresh.busyRecovery、u:refresh_batch_mutex.py、u:refresh_concurrency.py、u:refresh_recovery.py |
| D-11 | removed/云排除/未授权项目出现在登记中；显示解释但无探测/读取/启动；点击不会扩大范围 | UNVERIFIED | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：edge.D11D12Excluded、u:project_definition.py#test_removed_disabled_cloud_registry_only_projection_never_probes |
| D-12 | 来源在读取期间更新或中间写入不完整；快照一致或明确失败/重试；不发布混合状态，能在后续合法读取恢复 | UNVERIFIED | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：u:source_atomicity.py、u:refresh_concurrency.py |
| R-01 | 从A回答与B有没有已确认关系；关系/无关系/未知不同；不能为了测试造关联 | PASS | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：resilience.emptyVersusUnavailable、management.confirmedForwardReverse、u:management_relations.py |
| R-02 | 阅读归属、依赖、供给/服务等方向；能说清“谁属于谁、谁依赖谁、为什么” | FAIL | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：management.confirmedForwardReverse、u:relations.mjs |
| R-03 | 同一对象有多角色、多条关系；展示不丢角色；业务角色与关系类型不混为一项 | UNVERIFIED | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：responsive.layoutMatrix、u:project_definition.py、u:relations.mjs、edge.B02B03B05R03Definitions |
| R-04 | 点击关联项目再返回；上下文不丢；不落到同名错误工作副本 | FAIL | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：management.confirmedForwardReverse、keyboard.keyboardRelationships |
| R-05 | 在列表、业务树、关系视图间比较；能区分任务层级、项目归属与项目依赖；视图名不误导 | UNVERIFIED | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：nav.directoryMatrix、u:business_tasks.mjs、u:relations.mjs |
| R-06 | 对比提议关系和明确接受关系；未接受提议可识别；不隐式生效或触发任务 | UNVERIFIED | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：management.proposalAndHistory、u:management_proposals.py |
| R-07 | 副本新增、修改、撤销一条管理关系；入口可找到，变化立即可理解；不改源业务事实 | FAIL | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：management.relationsOnlyRetraction、management.proposalAndHistory |
| R-08 | 自依赖、循环、重复关系及失效目标；提示准确、保留输入并给恢复路径；不破坏既有关系 | UNVERIFIED | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：edge.R08Options、edge.R08Malformed、edge.R08CycleSemantics、u:management_relations.py |
| R-09 | 查看依赖方阻塞对项目的影响；影响可追溯，不把推导当原始事实；不自动停其他任务 | UNVERIFIED | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：management.confirmedForwardReverse、u:relations.mjs |
| R-10 | 跨项目关系旁尝试寻找分发/访问含义；用户知道“关联”是否代表可读、可写或任务传递；不能由画线扩大权限 | UNVERIFIED | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：management.proposalAndHistory、u:management_api.py、u:ordinary_api.py |
| S-01 | 回答A/B本周/近期做什么和为什么；可从任务、依赖、日期或明确未排期中作判断 | PASS | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：management.calendarAndTimeline、u:timeline.mjs、u:business_tasks.mjs |
| S-02 | 区分计划、滚动预估、来源实际；名称和视觉可辨；估计不被当承诺/已发生事件 | PASS | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：management.importAndRestart、u:timeline_pipeline.py、u:timeline.mjs |
| S-03 | 打开没有日期/部分日期/里程碑的项目；未排期可见；不填假日期，不把缺少日期误成项目不存在 | UNVERIFIED | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：management.calendarAndTimeline、resilience.emptyVersusUnavailable |
| S-04 | 副本设定/调整日期，再看概览和时间线；同一管理版本一致；反馈明确；不改子项目实际进度 | PASS | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：management.calendarAndTimeline、refresh.saveAfterReconcile |
| S-05 | 结束早于开始、时区/跨日、无效日期；可读校验、时区一致、输入不无故丢失 | UNVERIFIED | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：management.dateRecovery、management.calendarAndTimeline、u:management.mjs |
| S-06 | 甘特缩放/筛选/窄窗查看；项目名、日期、未排期与关键动作仍可理解；不靠悬停才有全部信息 | UNVERIFIED | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：management.calendarAndTimeline、responsive.layoutMatrix |
| S-07 | 依赖改变但计划尚未调整；影响有依据；不未经操作改别人的安排或捏造实际完成时间 | UNVERIFIED | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：management.confirmedForwardReverse、management.relationsOnlyRetraction、u:relations.mjs |
| S-08 | 两个编辑者基于不同版本保存排期；冲突可恢复，不静默覆盖；测试只在隔离副本模拟 | PASS | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：management.twoEditors、parallel.managementIsolation |
| T-01 | A/B分别从项目上下文找“下指令”；不必先有截图/图像批注才能表达普通项目任务；没有可用入口记缺口 | BLOCKED | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：nav.oneClickTasks、ordinary.drafts、keyboard.keyboardDraft |
| T-02 | 编写只读指令，核对项目/新建或继续/任务范围；用户看得懂接收对象与效果；后台绑定吻合；无歧义默认目标 | BLOCKED | BLOCKED | 正式 owner/正式业务运行分支未就绪；合成及真实 provider 局部证据不能替代完整正式验收。 证据：ordinary.drafts、ordinary.idleContinuation、u:ordinary_api.py |
| T-03 | 存草稿、离开、回到同一任务；内容可恢复且明确未发送；不偷偷创建模型任务 | BLOCKED | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：ordinary.unsavedDraft、ordinary.drafts、parallel.activeDraftIsolation |
| T-04 | 经页面真实发送一次有界只读任务；页面请求走真实通道，正确项目/会话真实接收；非后台替代调用 | BLOCKED | PASS | 真实页面POST→TaskStore→正确owned native thread/turn关联及A/B marker通过；原C范围不要求正式业务对象。 证据：native.r3、native.r4、native.reopen；各自适用范围分别记录。 |
| T-05 | 观察发送到回应的各阶段；草稿/接收/排队/执行/完成/失败不混；正在等待有解释 | BLOCKED | PASS | 真实页面显式存草稿且无模型，发送获queued回执，A有精确turn/started与可见执行状态，F有稳定排队解释；三任务完成后仍待用户验收。失败/超时/断线及短暂阶段复用字节未变的I证据。 证据：native.r5、lifecycle.restartQueueRecovery、edge.T12TerminalBranches、u:task_lifecycle.py |
| T-06 | 打开真实回复及对应产物；结果与指令、项目、会话对应；导入/历史结果不冒充本次生成 | BLOCKED | PASS | 真实原始回复、请求/项目/thread/turn一致；固定结果版本与桌面/390可见回复通过。 证据：native.r3、native.r4、native.reopen；各自适用范围分别记录。 |
| T-07 | 对自己任务追问一句并回看历史；继续正确上下文；回应不丢且时间顺序合理 | BLOCKED | PASS | 真实A追问同thread新turn，回复保留且历史可重开；不代替最终所有者接受。 证据：native.r3、native.r4、native.reopen；各自适用范围分别记录。 |
| T-08 | 对自己的忙碌测试任务反馈/新建另一任务；用户理解排队与继续含义；不打断别人的任务，不能向未知writer投递 | BLOCKED | PASS | 忙碌A的意见F持久排队且未注入活跃turn；B新会话在A仍运行时完成；A完成后F核对原生最新回合并复用A会话启动新turn，全程0中断。未知writer拒绝沿用既有I证据；最终人工接受另记。 证据：native.r5、ordinary.busyFeedback、u:ordinary_parallel_claim.py |
| T-09 | 双击发送或同request重试；不重复执行同一意图；冲突/重放反馈可理解 | BLOCKED | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：ordinary.unknownReconciled、parallel.unknownReceiptReconciliation、u:ordinary_api.py、u:ordinary_drafts.py |
| T-10 | 刷新页面、返回列表再开任务；仍能找到指令、进度、回复；不重发、不把掉线当任务失败 | BLOCKED | PASS | 原真实任务只读补验reload→任务列表→点击同一B重开；task/thread/turn/result_version/marker与前后native状态一致，0新任务。 证据：native.r3、native.r4、native.reopen；各自适用范围分别记录。 |
| T-11 | 请求送达后网络断开/回执丢失；结果未知有核对路径，先对账再重试；不生成重复任务 | BLOCKED | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：ordinary.unknownReconciled、parallel.unknownReceiptReconciliation、resilience.taskReceiptUnknown |
| T-12 | 自己测试任务失败、超时、执行者不可用；保留指令和错误摘要，可辨重试条件；失败不包装为完成 | BLOCKED | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：ordinary.runtimeAndRevocation、lifecycle.inputTimeout、edge.T12TerminalBranches |
| T-13 | 取消自己测试任务，与迟到完成竞态；只影响精确任务；取消已请求/已生效/完成明确；B不受影响 | BLOCKED | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：lifecycle.exactCancellation、parallel.cancelIsolation、u:task_runtime.py#test_cancel_committed_after_final_read_before_success_publication、u:task_runtime.py#test_late_cancel_and_wrong_provider_identity_never_publish_success |
| T-14 | 执行门关闭、无合法身份、缺授权；解释真实原因；按钮/状态一致；不改门控、盗用会话或直接SDK补过 | BLOCKED | BLOCKED | 正式 owner/正式业务运行分支未就绪；合成及真实 provider 局部证据不能替代完整正式验收。 证据：ordinary.initialOwnerGate、ordinary.authenticationReasons、ordinary.runtimeAndRevocation、lifecycle.approvalBoundary |
| T-15 | A编辑指令期间切B并等待A迟到响应；草稿/目标/回复不串位；发送前目标明确；B不收到A指令 | BLOCKED | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：parallel.activeDraftIsolation、parallel.lateResponseIsolation、ordinary.unsavedDraft |
| T-16 | 核对“执行结束、工程通过、业务合格、用户接受”；四种状态有区别；无需先把故事/角色作品做满意才评Hub分发 | BLOCKED | BLOCKED | 正式 owner/正式业务运行分支未就绪；合成及真实 provider 局部证据不能替代完整正式验收。 证据：u:task_lifecycle.py、u:task_review.py、u:connection_view.mjs、u:business_tasks.mjs |
| N-01 | 观察一个本来就在推进的任务，同时浏览A/B；其他任务无需暂停；记录精确身份和自然进展，测试不发控制指令 | PASS | UNVERIFIED | 未向未知真实任务发消息或控制；本次没有新的自然任务推进链。 证据：Root 版本/资源/限制核对 |
| N-02 | 自然来源更新与Hub读取交错；来源→采集→显示有版本链；无混合快照；未遇更新不写PASS | UNVERIFIED | BLOCKED | 正式 owner/正式业务运行分支未就绪；合成及真实 provider 局部证据不能替代完整正式验收。 证据：refresh.manualSource、refresh.cycles、u:source_atomicity.py |
| N-03 | 自己的A测试任务与B任务状态同时变化；所选项目、状态和回复隔离；错误不会污染另一项 | BLOCKED | PASS | 真实A/B同时running；精确取消A，B完成正确marker，状态/回复归属独立。 证据：native.r3、native.r4、native.reopen；各自适用范围分别记录。 |
| N-04 | A/B快速切换且请求乱序；旧页面响应不覆盖当前页面；loading/error属于正确项目 | PASS | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：nav.lateResponse、parallel.lateResponseIsolation |
| N-05 | 同时自然刷新与手动刷新；忙碌边界在副本；无双写/错误回退，能理解合并、排队或忙碌 | BLOCKED | BLOCKED | 正式 owner/正式业务运行分支未就绪；合成及真实 provider 局部证据不能替代完整正式验收。 证据：refresh.busyRecovery、refresh.cycles、u:refresh_concurrency.py |
| N-06 | 观察来源业务文件与本次Hub效果归属；采集不写业务源；自然业务写入不误算测试破坏；不要求全树不变 | PASS | BLOCKED | 正式 owner/正式业务运行分支未就绪；合成及真实 provider 局部证据不能替代完整正式验收。 证据：parallel.sourceIsolation、resilience.overlap、u:sources.py、u:source_atomicity.py |
| N-07 | 测试结束只清理自己创建的任务/临时记录；不回滚别人的新进展，不取消未知任务，不删除原始材料 | PASS | PASS | 113 份已登记 fixture PID 全部不存在；浏览器/私有显示已终止，测试单元与子 Agent 已收齐。仅保留明确交付的只读演示 HTTP 服务。 证据：helper.fixtureExit、helper.browserClose、refresh.timerCleanup |
| N-08 | 别的任务在同一仓库修改无关文件；继续独立只读测试；重叠写入只阻塞对应组，不stash/reset/改分支 | UNVERIFIED | UNVERIFIED | 本任务的协调子写者只操作互不重叠文件；未出现另一独立任务对重叠文件写入的真实事件，不能据此完成原 R 用例。 证据：Root 版本/资源/限制核对 |
| N-09 | 测试期间正式代码被正常更新；识别新候选并拆分证据；旧结果保留版本，不为冻结停别的任务 | UNVERIFIED | UNVERIFIED | 正式进程与候选不同且原样保留；未发生正式切换，切换时浏览中的行为未验。 证据：Root 版本/资源/限制核对 |
| N-10 | 测试自己的取消/重试，旁边业务任务继续；控制精确命中自己的任务，其他任务没有收到控制效果 | BLOCKED | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：parallel.cancelIsolation、parallel.explicitRetryIsolation、parallel.followupIsolation、parallel.independentCompletion |
| N-11 | 正常浏览/采集时观察CPU/RSS/请求耗时；无业务生成/GPU争用、无抢占物理桌面；异常有实测依据 | UNVERIFIED | UNVERIFIED | 已补当前真实数据只读实例正常浏览的服务、已观察浏览器子树、显示与请求耗时；共享 RSS、瞬态 CPU、wrapper 开销及正式采集/系统争用仍有限制。原 fixture 证据保留，未设性能 PASS 阈值。 |
| N-12 | 自然事件未发生、身份缺失或局部冲突；对应项UNVERIFIED/BLOCKED；其他覆盖继续，模拟不替实测 | PASS | PASS | 限制、缺失通道和剩余步骤已列入本包，不改分母。 证据：Root 版本/资源/限制核对 |
| V-01 | 不滚动看首页，说出页面用途、项目入口、需要关注的事；首屏优先支持找项目和决策；同步/恢复说明不淹没核心内容 | FAIL | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：nav.firstScreen、responsive.layoutMatrix |
| V-02 | 打开项目首屏回答“是什么、现在怎样、能做什么”；定义、关键状态、主动作有明确顺序，不靠通读工程字段 | FAIL | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：nav.definitions、nav.sections、responsive.layoutMatrix |
| V-03 | 通读完整项目页，标记重复、长段落和无关模块；每块服务明确问题；能辨主次，重复状态不造成矛盾感 | FAIL | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：nav.sections、responsive.layoutMatrix |
| V-04 | 从长页找关系、排期、任务和来源；找路成本可记录；难找时提出基于任务的分层/拆页建议 | FAIL | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：nav.sections、nav.taskNavigationBefore、keyboard.keyboardRelationships、keyboard.keyboardDraft |
| V-05 | 解释内部ID、英文阶段、unknown与技术状态码；正常用户能理解；有必要的技术详情可深入查看；不替用户脑补 | FAIL | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：u:connection_view.mjs、u:relations.mjs、nav.errors、edge.D06D07SourceFailures |
| V-06 | 比较列表/树/甘特中的同一个项目；名称、状态、日期含义一致，视图差异为决策服务 | UNVERIFIED | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：nav.directoryMatrix、management.calendarAndTimeline、u:timeline.mjs |
| V-07 | 等待、空列表、错误、禁用、成功反馈逐一查看；状态清楚、有恢复路径，重要反馈不只靠颜色或瞬时toast | FAIL | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：nav.errors、ordinary.authenticationReasons、resilience.trueEmpty、resilience.emptyVersusUnavailable、keyboard.keyboardAnswerRecovery |
| V-08 | 长项目名、长回复、多关系、很多unknown；不截掉关键对象/操作，不破版；长内容可定位和返回 | UNVERIFIED | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：responsive.longReplies、responsive.layoutMatrix、resilience.longReplyIsolation |
| V-09 | 1440×1050与1280×800观察首屏及滚动；主要任务和反馈可用；大截图不能掩盖小窗的入口缺失 | PASS | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：nav.firstScreen、responsive.layoutMatrix |
| V-10 | 390×844窄窗浏览、导航、表单和长回复；内容有层次、无关键动作丢失；触控目标可用；真实手机另验 | UNVERIFIED | UNVERIFIED | 390 模拟视口通过，实际手机触控未验。 证据：responsive.layoutMatrix、parallel.activeDraftIsolation、preview.materialReturn |
| V-11 | 200%缩放、对比度、焦点/禁用视觉检查；不丢内容/操作，文字可读，状态不只靠颜色；记录检查工具和限制 | UNVERIFIED | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：responsive.layoutMatrix、responsive.contrast、keyboard.keyboardDraft、keyboard.keyboardAnswerRecovery |
| V-12 | 判断方案图/截图预览与当前页面的关系；用户知道是设计、历史截图、导入结果还是当前现场；不可操作图不暗示可控制 | PASS | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：preview.savedCapture、preview.staleMaterial、preview.unversionedMaterial、u:captures.mjs、u:page_inventory.mjs |
| E-01 | 只用键盘完成找项目、查看关系、进入指令和返回；焦点可见、顺序合任务、无陷阱；链接/按钮语义正确 | BLOCKED | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：keyboard.keyboardRelationships、keyboard.keyboardDraft、keyboard.keyboardAnswerRecovery、keyboard.keyboardReturn |
| E-02 | 展开面板/打开关闭对话框/触发错误；焦点进入/返回合理；状态与错误可被辅助技术感知 | UNVERIFIED | UNVERIFIED | 键盘和 AX 证据通过，真实读屏体验未验。 证据：keyboard.ownerDialog、keyboard.quietPolling、keyboard.keyboardAnswerRecovery、keyboard.keyboardRouteError |
| E-03 | 页面等待期间和错误恢复后反复导航；loading不永远悬置；错误可重试；旧请求不恢复错误页面 | PASS | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：nav.errors、nav.lateResponse、parallel.lateResponseIsolation |
| E-04 | 副本模拟401/403/404/409/5xx和超时；各自含义清楚；不泄露敏感内部细节、不把权限问题说成无数据 | FAIL | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：nav.errors、ordinary.authenticationReasons、ordinary.inputConflict、resilience.admittedSave500 |
| E-05 | 提交校验失败或网络失败后再次编辑；输入保留；错误定位；不重复提交，不静默清空 | PASS | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：management.dateRecovery、management.twoEditors、keyboard.keyboardAnswerRecovery、resilience.offlineDraftRecovery、resilience.admittedSave500 |
| E-06 | 浏览器离线/重连边界；可读旧数据标历史；在线恢复可核对；不调整工作站网络/VPN | PASS | PASS | 原始所需隔离/效果边界已按上述实际证据验证；不扩张为正式/live或最终用户接受。 证据：nav.errors、resilience.offlineDraftRecovery |
| E-07 | 打开reduced-motion观察状态变化；非必要动效减少，状态和功能仍完整 | UNVERIFIED | UNVERIFIED | 适用确定性机制已验证；正式 A/B、当前运行版本或用户理解分支尚未完成。 证据：responsive.reducedMotion |
| E-08 | 全程检查console异常和network关键交换；新旧错误分开；请求目标、方法、授权、返回处理匹配实际动作 | UNVERIFIED | BLOCKED | 正式 owner/正式业务运行分支未就绪；合成及真实 provider 局部证据不能替代完整正式验收。 证据：nav.errors、parallel.expectedBrowserErrors、parallel.secondaryNetwork、resilience.runtimeDiagnostics、edge.edgeDiagnostics、helper.console、helper.network |
| E-09 | 空项目/无任务/无关系/无排期/服务未启用；各自说明和下一步正确；入口缺失不写NOT_APPLICABLE | UNVERIFIED | UNVERIFIED | 合成真空目录、无任务/关系/排期及禁用服务通过；正式已有状态在当前候选上的页面分支未复核。 证据：resilience.trueEmpty、resilience.emptyVersusUnavailable、ordinary.runtimeAndRevocation |
| E-10 | 回看测试产生的本地效果和原始证据；不公开DB/消息/凭据/私密路径/敏感截图；不误改/清理其他任务 | PASS | PASS | 只交付审阅过的安全代码/测试/此摘要；原始 State、DB、消息、凭据、路径清单与截图留本地。 证据：u:materials.py、u:preview_policy.py、helper.fixtureExit |

## G10 交付核对

| ID | 核对 | 结果依据 |
| --- | --- | --- |
| G-01 | 盘点status、ignore、服务代码身份和实时远端 | 全量 734 待提交项、index0、正式进程与实际远端分别核对。 |
| G-02 | 核对差异对应的native任务、归属与用途 | 本轮子写者已收齐，Root 唯一集成/State/Git；旧未完成/私人组沿原归属保护。 |
| G-03 | 逐文件核对已交付字节，避免重复交付旧投影 | 逐文件哈希比对 main；582 与当前 main 相同，先前相同的另 3 项旧字节已被后续提交取代，不恢复过时投影。 |
| G-04 | 保留旁支独有历史、未提交工作和运行依赖 | 保留 708 个常规文件、25 个未跟随的 symlink 及旁支/正式运行；只有 owned State 子树改变。 |
| G-05 | 私人DB、消息、凭据、路径清单与敏感证据留本地 | 原始 State/DB/消息/凭据/完整路径清单和截图均不入本提交。 |
| G-06 | 精确ignore生成物并验证反例 | 复用精确生成物规则，7 项匹配/源码反例通过；无宽泛 ignore 新增。 |
| G-07 | 组成可独立验证的精确owned提交单元 | 核心 7 文件独立验证/精确 stage，B10 单文件补充和此文档作为最终可审核单元。 |
| G-08 | 精确候选检查、脱敏与差异复核 | 精确哈希、diff --check、固定环境检查、浏览器、独立复核及脱敏；本地证据别名索引可追踪。 |
| G-09 | 当前授权下正常交付并核对远端commit/保护 | 核心 main99ca6 经 API+ls-remote 核实，新 Hub checks 成功；本包所在提交的最终远端/CI 在唯一 State 继续核实。 |
| G-10 | 交付版本、剩余本地项与恢复依赖交接 | 本包区分本地、main、正式旧运行、保留演示和外部缺口，附精确恢复点；最终交付身份以 State 为准。 |

## 可重复验证入口

下表用于工程复核，最终审核无需所有者重新组织测试。浏览器脚本需要本任务私有 CDP、独立 fixture 和明确的 Python/Playwright 路径，不能指向日常浏览器或正式 8766。

| 证据别名 | 入口或本地证据 | 额外配置与边界 |
| --- | --- | --- |
| functional | `tests/pch_functional_browser.mjs` | 默认 |
| nav | `tests/pch_navigation_browser.mjs` | 默认 |
| refresh | `tests/pch_refresh_browser.mjs` | `PCH_REAL_TIMER=1`，只作用本任务测试单元 |
| management | `tests/pch_management_browser.mjs` | 默认 |
| ordinary / lifecycle | `tests/pch_ordinary_browser.mjs` / `tests/pch_ordinary_lifecycle_browser.mjs` | 明确的受控 provider |
| parallel | `tests/pch_ordinary_parallel_browser.mjs` | 默认及 `PCH_PARALLEL_VIEWPORT=narrow` |
| preview | `tests/pch_preview_versions_browser.mjs` | 默认及 `PCH_PREVIEW_VIEWPORT=narrow` |
| keyboard | `tests/pch_keyboard_browser.mjs` | 键盘/AX，不是真实读屏 |
| responsive | `tests/pch_responsive_browser.mjs` | 默认及 `PCH_RESPONSIVE_MODE=zoom`；真实私有 Chrome 200% |
| resilience | `tests/pch_resilience_browser.mjs` | 默认及 `PCH_RESILIENCE_MODE=empty` |
| edge | `tests/pch_edge_browser.mjs` | `PCH_EDGE_MODE=management/source/runtime/definition` 各自独立运行 |
| helper | `tests/pch_browser_runtime.mjs` | 原始 network、console、fixture 和 browser 收尾证据 |
| native.r3 | 本地唯一 State 指向冻结的真实通道旅程与原始回执 | A/B 读取、A 追问和重开有效；取消脚本误判失败保留 |
| native.r4 | 本地唯一 State 指向取消并行补测与原 TaskStore | 只取消精确 A，B 完成；累计历史效果保留，不重复计算原 PASS |
| native.reopen | 本地只读重装配、浏览器及 native 前后查询 | 真实刷新、回列表并点链接重开；DB 与 native 不变，0 新模型，桌面/窄屏仅为模拟窗口 |
| native.r5 | 本地唯一 State 指向执行阶段/忙碌意见旅程、原始队列及 Root 只读复核 | 草稿无模型、真实执行、F 排队、B 独立完成、F 续接 A；异常短暂分支复用未变版本的 I 证据，人工接受未代办 |
| real.readonly | 本地唯一 State 指向当前候选、命名空间探针、真实数据浏览器及停止/重启证据 | 43 项目录、A/B 概览/排期；正式数据和代码只读，0 模型/正式写入，原 R/H 整项不晋级 |

## 本地证据索引与恢复

本地原始材料只通过唯一 State 关联。下列均为本轮已存在的证据别名，不包含私密载荷：

- `o61-candidate.json`、`o61-final-py312-checks.json`、`o61-evidence-applicability.json`、`o61-diagnostics-classification.json`。
- `o61-matrix-final2-results.json` 及其中 17 份完整浏览器 JSON；`o61-final-edge-runtime`；`o61-definition-three-layers`。
- `o6-final-results-108.json`：98 个唯一 ID，逐项保留原预期、范围、候选、输入、实际观察和证据路径；G10 另表。
- `o62-formal-readonly-preparation.json`、`o62-formal-source-compare.json`、`o62-runtime-identity.json`：正式只读分支及版本差异。
- `o63-review-runtime-checks.json`、`o64-resource-final-audit.json`、`o61-pending-review.json`：入口、停止重启、113 个 fixture 终态与全量待提交项。

## 最少剩余事项

1. 口述项目身份已由所有者明确确认，身份歧义解除；当前候选的真实数据只读入口已可审核 A/B 概览与排期。旧快照规范不匹配及六类定义缺口已明确，现有正式任务绑定仍未核验；项目理解、导航、关系和信息表达的所有者接受仍待完成。
2. 任务内沙箱修复已有 165 项库检查、完整 CLI 自带 helper 的 22 项只读拒绝、设备/PTY/proc、版本/schema 和初始化证据；真实合成目标的读取、追问、精确取消、另一任务继续完成及完整刷新/列表重开分支通过。第一次缺配套程序、r3 取消脚本误判及只读重装配的首次字段错误全部保留，未把失败改成通过。当前累计消耗 7 个线程、11 个回合、2 次本任务中断；执行阶段及忙碌意见分支也已通过。所有者对本任务三个上限各增加 10 次的口述及更正按同一次授权记录为 15/18/12，旧运行的授权和计数不变。其余 C 路径及正式 R/H 分支继续按原预期验证；不消耗额度来重复已通过分支。任务内构建未安装全局 CLI、修改全局配置或扩大目录/写授权；执行文件检查仍有路径检查与执行之间的竞态局限。
3. 正式服务尚未配置 owner。当前候选新增显式 OS 凭据投影模式、只读配置准备命令和人工 TTY 密文录入入口；默认环境模式兼容，OS 模式失败不回退，不自动启用执行。本轮按当前代码与实际服务解释器重新生成只读准备结果，仍为 `ready_to_install: false`；真实 OS 提供者资格验证、可能需要的主机密钥初始化、隐藏录入和正式切换尚未执行，须在具体方案审核后分别处理。操作与边界见日常文档，这与原生沙箱故障分别处理。
4. 当前代码候选沿既有 Hub 自身适配器使用固定 2 MiB 上限，已只读解析约 1.19 MB 的真实 canonical State，读取前后哈希一致；普通来源及声明继续默认 1 MiB。原 1 MiB 失败保留为历史，正式 `8766` 仍运行旧版，尚未用此候选采集或切换账本。边界与适用条件见日常操作文档。
5. 正式服务/账本切换仍需确切写责、完整兼容与恢复依据；本次已核对的结构和身份没有发现版本迁移需求，不能代替正式切换验收，现有 8766 未切换。Web 与定时采集器的代码/schema 版本须共同固定，原 Owner 准备命令只覆盖 Web；真实 executor 授权须另外明确选择，不能以 Owner 配置代替。自然更新、真实手机/读屏及最终所有者接受继续保留未验。

原 107/111 工程记录和 34 轮历史保留。最终用户接受之前，不将 native Goal 标成 complete；报告中的 with_gaps 不代表后台继续执行或暗中部署。
