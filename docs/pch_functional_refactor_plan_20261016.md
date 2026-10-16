# PCH 功能优化与重构执行计划

> 公开脱敏的历史计划/执行入口；动态事实仍以本机唯一 STATE 为准。2026-10-16 所有者后续指令要求每个实际执行轮核查全部待提交内容并正常交付安全候选到 main，取代下文任何延至 G 阶段的交付安排；最终 G 验收仍保留。路径占位值需由执行工作区提供。本文件存在不自动启动任务。

计划版本：1.0.0-20261016。日期：2026-10-16，Asia/Tokyo。所属任务：PCH-REFACTOR-20261014；本次优化增量：PCH-FUNCTIONAL-OPT-20261016。

本计划以“先把功能实现并验证，再由所有者最终审核”为执行目标。现有Python、SQLite、HTTP和浏览器前端继续使用，按8阶段32轮推进，其中29轮处理功能与验收，最后3轮单独整理和交付Git。普通开发由Root按文件分工调用agent完成，不采用每轮三角色审批，不安排中途逐屏人工签收，不把配色、视觉稿或架构重写作为前置。

本次只准备计划、静态目录和执行prompt，产品优化尚未启动，未创建或接续原生Goal、未重启正式服务、未提交或推送。下文是执行阶段要求，不是本规划会话已完成的能力。

## 一 目标和完成边界

最终用户应能完成四项能力：

1. PCH在运行时自动读取已绑定且有资格的项目；不用agent代读，也不用手动刷页面才能看到新采集数据。过期、失败、历史和缺字段各有清楚身份。
2. 从首页找项目，在概览理解用途、产出、角色、当前工作、阻塞恢复条件和下一步；关系、排期、来源均能直接到达。
3. 从项目页面编写普通文本任务，核对目标后发送，看到真实接收、执行、回复、追问、取消及恢复；不要求先有图片批注。
4. 两个owned项目任务并行时，指令、草稿、状态、回应和取消互不串位；其他正在推进的业务不需要暂停。

整体产品接受必须建立在当前候选的必需功能、数据、视觉、桌面与窄屏、console、network、权限和所有者最终审核上。可以先交付“工程工作完成且存在外部待验分支”的候选，但有任何必需FAIL、BLOCKED或UNVERIFIED时，不宣称整体PASS或Goal完成。

不扩展到云项目、removed_local、陌生会话控制、子项目业务重写、媒体/GPU生成、账户迁移、主机网络改动、外网部署或新平台。报告中的两个项目不是故事/角色内容生产任务；本计划只验证Hub能否正确展示和分发，不要求先把作品做到满意。

## 二 依据与当前仓库事实

### 2.1 测试基线

[原报告](testing/pch-user-audit-20261015/TEST_REPORT.md)与用户粘贴附件逐字节一致，SHA256为e36af9dd6b8f8262bb386770b91caba4cbeaeaeba1beb3cef8657d3bad7cb924。[原用例](testing/pch-user-audit-20261015/TEST_CASES.md)的98项产品分母继续保留：PASS 23、FAIL 18、BLOCKED 25、UNVERIFIED 32。G-01至G-10另计，未执行，不属于98分母。

原工程记录107/111、34轮和4项待所有者接受保留。它们不等于产品通过，本次32轮也不追加到111叶任务中伪造新的工程完成率。旧报告23/98只表示那次用例结果，不是产品完成率；本次结果另记，同一ID可保留旧/新候选记录。

报告的实际服务候选C1为27112da10628a0cf03c0f882668ce3986c2010f7750d3a6e0241731040dd4155，声明交付commit为cd8ee0ef8c1b01d9cd14b0f919eac43dcab51af8。本次规划读到的checkout HEAD为8fa0a4e53e00207823e3fb406ee04b94b77141a9，处于旧agent分支且有大量已有改动。两者不是同一身份；执行时必须重新核对，不能把历史清单或HEAD当当前运行内存的完整证明。

### 2.2 当前可复用与真实缺口

| 领域 | 仓库证据 | 本计划判断 |
| --- | --- | --- |
| 采集主链 | connection_sources.SourceResolver、connection_refresh.RefreshLedger、connection_sync、project_service | 增量复用现有来源到SQLite投影，不建第二账本。已有CAS、恢复和原子读取测试。 |
| 自动更新 | systemd_refresh、refresh_schedule、hub.js | 当前调度定义是每日09:00；当前页面未见可见路由定期核对head。日批次和实时更新不能混称。 |
| 定义和质量 | project_service._dto/_quality、connection_view.mjs | 长期用途、产出和角色投影不足；底层已有读取/字段/证据覆盖口径，UI“日常查看信息完整”过宽。 |
| 首页和详情 | hub.js目录默认all，先时间线与同步；项目详情一次拼长页 | 修导航与内容优先级即可先降低找路成本，不先重写框架。 |
| 搜索和返回 | directory.mjs只搜name/project_id，目录路由重建空控件 | 增加已确认别名/用途词及浏览器历史上下文，不按发音自动合并项目。 |
| 关系和排期 | management_store、management.mjs、timeline.mjs、rolling_plan | 已确认管理依赖和建议关系投影分开且阅读口径冲突；日期校验缺字段说明。复用管理CAS与时间语义。 |
| 正式身份 | owner_auth、local_service | 正式owner未配置是外部认证边界；可自主改能力提示和合法入口。不能以配置身份代替执行授权。 |
| 任务现状 | task_service、task_store、task_worker、tasks.js、codex_adapter | 生命周期/控制/幂等基础已有；普通submit仍要求annotation/grant/图像上下文，grant仅特定路径。identity和任务列表的execution_enabled仍硬编码False，服务脚本不启动worker；通用项目任务需要实际开发。 |
| 任务测试 | test_hub_task_runtime、test_hub_task_drafts_http、test_hub_busy_controls | 已有完整preview/storage/grant/worker/mock组件与真实HTTP测试。B03首先是报告测试装配不全，应组合复用成浏览器实例。 |
| 交互基础 | hub.css已有focus、44px动作、窄屏与reduced-motion；common.js已有错误结构 | 复用并补动态验证。不能因为存在CSS规则就算可访问性已接受。 |

src/hub/services/project_profile_service.py只是placeholder，不把文件存在当项目定义能力。代码路径和函数定位是规划时证据，后续实现允许适当移动或合并，验收语义保持。

### 2.3 对九个已确认问题的处理

| 问题 | 实际改进 | 主执行轮 |
| --- | --- | --- |
| F01 用途、产出、角色和恢复条件缺失 | 独立定义投影、来源/缺失说明、概览行动依据 | O1.1、O1.3 |
| F02 首页同步和全量时间线淹没入口 | 搜索/关注项前置，默认简洁列表，详细同步按需查看 | O1.2 |
| F03 重复工程字段与长距离任务入口 | 项目内直接导航，摘要只呈现一次，任务主动作可达 | O1.1、O1.3 |
| F04 后退丢搜索、状态和滚动 | URL/history恢复查询、视图、焦点和位置 | O1.4 |
| F05 错误泛化、反复重试无效 | 认证/权限/不存在/冲突/失联/服务能力分类恢复 | O0.3、O1.4、O4.1 |
| F06 管理关系保存却仍称未确认 | 同版本管理关系阅读投影，方向、反向、跳转及建议层清楚 | O3.1、O3.2 |
| F07 内容不全仍称信息完整 | 读取健康、结构、关键缺值、证据新鲜度分别显示 | O1.1 |
| F08 常用别名无结果 | 仅使用已确认别名和来源词，命中解释及无结果恢复 | O1.2 |
| F09 日期不可保存但无原因 | 字段错误、ARIA关联、保留输入与跨日组合测试 | O3.3 |

F01至F09之外的所有未执行分支仍在范围内，不能只修18个FAIL或只重测原23个PASS。

## 三 体验与数据契约

### 3.1 默认页面行为

首页优先找项目、看需关注项和进入项目；同步健康保留简短摘要和展开入口。列表、树、甘特共享筛选结果，但不默认同时铺开。状态、来源、分组、匹配数和读取资格各自有含义。

项目概览首先回答“是什么、产出什么、扮演什么角色、现在怎样、能做什么”。当前状态可同时包含项目active和局部blocked；缺失负责人/用途/恢复条件应解释缺口，不能把unknown翻成零进度或无阻塞。任务入口靠近标题，最多一个明确导航动作到达；“概览、任务、排期与关系、来源”是功能入口，不强制固定四页或新设计体系。

关系阅读把来源事实、所有者已确认管理关系、未接受提议区分开。所有者保存的管理依赖必须立刻在阅读区出现方向和版本；存在管理边不能推导授予另一个项目写权。来源与管理冲突不覆盖历史，建议不自动升级为事实。

### 3.2 必要数据约束

- 项目身份：稳定project_id、实例/workspace身份、主项目/副本归属、正式名、已确认别名。目录名、技术栈和口述相似不构成身份确认。
- 长期定义：用途、主要产出、多角色；与当前目标、阶段、轮次和执行者分开，每项有来源/管理身份和缺失状态。
- 业务事实：原始数值、单位、分母和范围保留；107/111工程记录不替代作品或项目自己的分母。
- 新鲜度：来源业务事实时间、文件/来源版本、采集观察时间、最后尝试/成功、ledger head和页面版本分别保留。读过文件不表示业务刚进展。
- 管理字段：日期、优先级、管理关系经CAS保存，刷新/再导入不覆盖；计划、滚动估计、实际保持不同身份。
- 普通TaskSpec：project/workspace/storage、request_id、意图、只读/写范围、输入版本、可选图像上下文、新建/继续及精确thread/turn绑定。服务端决定授权，不能由浏览器构造任意grant。
- 能力状态：读取、管理保存、创建任务、继续会话、回应审批、取消和provider可用分别可判定；未知状态不得当空闲。

### 3.3 自动更新的实现目标

此处明确新增目标：服务运行且源稳定、可读时，每60秒执行一次有界增量检查；可见页面每5秒核对head；正常端到端目标90秒内。沿用用户级systemd与现有同步器，保留每日完整批次，不增加第二调度器。隐藏页面降频，重连/恢复可见先对账，不清空草稿。

这些间隔是可配置的初始工程目标，不是原报告已经约定的SLA或已测性能。执行agent需测至少三次真实短周期调度触发，报告源变化、批次、账本和DOM时间。如果39来源检查耗时或系统负载不满足，应修增量/互斥策略；确需调整间隔则记录实测、原目标、改后目标及原因，不能为了PASS静默放宽。

GET保持只读；定时/手动刷新走原有合法效果路径。重叠批次不叠加，单坏源不拖垮其余来源。被排除的项目不因“检查更新”而被探测。

### 3.4 错误和结果未知

401提示需要合法身份，403解释权限不足，404解释对象不存在并给返回，409保留编辑并给重读/显式重新应用，503区分能力未配置和服务不可用；500、离线、超时保留用户上下文。诊断原文可深入查看，普通反馈不暴露敏感路径。

只读GET可重试；提交回执丢失时先按原request_id核对效果，未知送达不生成新任务。双击、浏览器刷新、重连和worker恢复均不得重复执行同一意图。执行结束、工程检查通过、业务结果合格和所有者接受分开。

## 四 自主执行和人工参与

| 情况 | agent应完成 | 真正需要的外部输入 | 不阻塞的工作 |
| --- | --- | --- | --- |
| 测试实例缺TaskService/worker/grant/调度 | 复用现有fixture装配完整隔离实例并跑浏览器 | 无 | 全部确定性实现和测试 |
| 通用任务缺少文本入口/授权模型 | 实现TaskSpec、服务端准入、队列、页面和恢复 | 仅涉及新的安全边界时按项目政策做必要独立审查 | 其他普通功能和隔离测试 |
| 正式owner未配置 | 做连接入口、能力说明、合成owner全链测试 | 正式密码/令牌由所有者本机输入 | 其余实现、mock链、合法既有provider测试 |
| Actor 4/三十天的口述映射不明 | 稳定ID和合成A/B验证；列待确认别名 | 最终确认真实口述映射 | 通用搜索/展示/任务机制 |
| 来源缺用途/执行者等业务事实 | 有依据的投影/摘要和明确缺口，管理提议不冒充事实 | 仅无法从合法来源推得的业务定义 | 数据契约、所有UI与未知分支 |
| 原生provider无登录/额度 | 完成mock错误/恢复，核对既有合法能力 | 登录/额度若确实不可用 | O4.6隔离、O5、O6和G准备 |
| 自然业务事件或09:00尚未发生 | 受控来源和真实短周期timer验证，择机只读观察正式源 | 不要求用户制造业务事件 | 其余全部工作 |
| 无真实手机/读屏或注销窗口 | 浏览器响应式、缩放、ARIA/键盘、对比度与当前会话调度 | 最终设备/体验审核或以后有设备再验 | 工程交付 |
| 正式服务/账本切换写责不明 | 独立端口完整候选和隔离恢复验证 | 精确切换/迁移授权 | 候选审核、回归、Git准备 |
| 分支保护或外部审批 | 准备精确提交/PR并记录验证结果 | 仓库规则要求的审批 | 本地候选和产品最终审核包 |

无需中途询问普通代码拆分、schema可逆扩展、合成数据、测试装配、错误注入、文案和相关回归。独立agent按文件/职责分工，Root保留STATE、公共共享文件、Git和外部效果。子agent不得自行派生后代；调用次数、评审次数、等待和压缩上下文不增加轮次。

不预设Governor→Repair→Judge三角色链。普通局部实现用DIRECT；难判且影响广的候选才做必要的只读复核；安全权限、凭据、生产迁移或其他明确高影响边界遵守当前项目风险政策。不能通过换名称绕开真实边界，也不能把所有功能都升级成全套治理。

## 五 阶段与依赖

静态机器目录为[data/roadmap/pch_functional_refactor_20261016.yaml](../data/roadmap/pch_functional_refactor_20261016.yaml)，仅定义依赖、步骤、验收和覆盖。32轮是初始执行拆分，不是工期保证、调用次数、预算或产品完成率。

优先完成当前可执行的依赖组；一个分支缺正式身份，不使整个阶段停止。O4.5真实通道与O4.6真实并行依赖合法provider，但O4.6确定性部分、O5、O6.1不依赖live通过。O6.2/O6.4允许记录带缺口结果，绝不把“记录完”当功能通过。硬依赖要求相关可自治代码与确定性检查成立；result_inputs只要求有准确结果记录，可以包含外部未验。整理和审核包使用结果输入，不以等待正式认证、真实日历或生产切换PASS为前置；真实代码/测试失败仍须修复，外部未验仍阻止对应产品PASS。

| 阶段 | 轮数 | 交付能力 |
| --- | --- | --- |
| O0 | 3 | 同候选身份、完整隔离测试实例、真实能力投影 |
| O1 | 4 | 看懂项目、找得到、任务可达、返回与错误恢复 |
| O2 | 4 | 自动采集到页面、原子/并发、排除范围、真实调度 |
| O3 | 4 | 关系口径、编辑与建议、日期甘特、依赖影响与CAS |
| O4 | 6 | 身份能力、通用文本任务、草稿幂等、worker控制、真实链与并行 |
| O5 | 4 | 产物预览、键盘公告、窄屏缩放、异常与负载 |
| O6 | 4 | 全98回归、正式/真实核对、运行候选与恢复、最终审核包 |
| G | 3 | G10项独立整理、精确提交、按授权交付 |

每轮以下列顺序闭环：读当前输入和有效证据→实现最小功能→目标检查与实际浏览器联调→复核精确差异→记录唯一状态→继续下一就绪轮。不要每轮跑全仓检查、产出一份独立轮报或停下来要求人类点头。

以下文件是建议改动入口，允许在相同功能边界内调整，不要求为表中每项创建新模块。

### O0 恢复基线和完整测试装配

#### O0.1 登记新优化增量并复核真实候选

依赖：执行授权与当前入口复核。主要入口：STATE.yaml#pch_refactor；docs/testing/pch-user-audit-20261015/TEST_REPORT.md；scripts/auto_advance_runner.py。

实施：

1. 复核报告哈希、98项原始预期、F01至F09和B01至B03；保留原107/111及34轮历史。
2. 用本次代码文件内容、运行清单、HTTP静态资源和启动身份分别识别checkout、现运行服务、测试实例；不把旧分支HEAD当服务版本。
3. 核对native任务和重叠文件写责，划出新优化owned差异；接续已有有效结果，登记唯一下一步。

通过条件：

- 旧测试报告原文和原结果分布不改；新优化使用独立轮次键，仍归现有pch_refactor。
- 首次产品写入前runner检查有真实退出码；已有脏树和其他任务保持原样。

相关检查：python3 scripts/auto_advance_runner.py --mode check --task-id PCH-REFACTOR-20261014；逐文件哈希/当前服务身份的只读核验，不执行采集或重启。

人工与恢复边界：历史owner业务验收待审只保留原项；不成为本次实现前置。若旧原生Goal仍属旧范围，不覆盖、不擅自complete，先在普通执行模式推进可独立工作并说明原生Goal归属限制。

主覆盖：A-01、N-08、N-09；所有相关分支还需O6复核。

#### O0.2 组装完整的隔离产品和可控任务通道

依赖：O0.1。主要入口：tests/test_hub_task_runtime.py；tests/test_hub_task_drafts_http.py；tests/test_hub_service_integration.py；tests/新增浏览器fixture及旅程。

实施：

1. 复用已有preview、annotation、TaskStore.register_storage、grant、TaskService、TaskWorker和真实HubHTTPServer组合出同代码同静态资源实例。
2. 所有root、SQLite、管理文件、端口、owner、Session/Host适配和worker均显式隔离；只使用合成项目与测试工作区，全部访问设探针。
3. 提供确定性FakeAdapter的延迟、乱序、失联、丢回执、重复事件、失败及取消竞态；mock模式标识清楚。
4. 提取共享装配以防测试漏服务；建立启动、健康、浏览器握手和精确收尾命令。

通过条件：

- 浏览器真实HTTP完成guest/owner握手及已有任务路径；Task/Session/刷新服务不再因fixture遗漏而503。
- 两个合成项目A/B具有独立ID、存储、来源SHA与任务绑定；一实例所有效果均可核对且不访问正式根。
- 实例停止后无owned browser/display/worker/HTTP遗留；原始证据保留本地。

相关检查：python -m unittest discover -s tests -p 'test_hub_task_runtime.py' -q；python -m unittest discover -s tests -p 'test_hub_task_drafts_http.py' -q；新增真实浏览器fixture启动/HTTP/收尾smoke。

人工与恢复边界：合成身份材料由fixture临时内存提供，不打印、不写入证据；不复制真实会话、密码或provider凭据。FakeAdapter通过只计隔离层。

主覆盖：服务装配和新增集成验收，原98项由后续轮统一回归；所有相关分支还需O6复核。

#### O0.3 统一服务装配和可观察能力状态

依赖：O0.2。主要入口：scripts/hub_server.py；src/hub/local_service.py；src/hub/task_service.py；src/hub/web/hub.js；tests/test_hub_server_entry.py。

实施：

1. 复用正式装配入口并允许显式依赖注入，保持代码根、数据根、业务workspace分离。
2. 从实际服务/认证/授权/provider/worker/项目范围推导能力及不可用原因，替换分散硬编码execution_enabled=False。
3. 页面区分未配置owner、guest只读、owner只读、任务执行可用、provider不可用和未知；设置说明只给用户所需操作。

通过条件：

- 合法身份不自动等于执行权限；缺服务与空任务不混淆；失效能力不会残留为可点击执行。
- 默认正式运行不启test_gate、不偷偷启动worker/模型；隔离配置不能泄入正式实例。

相关检查：python -m pytest tests/test_hub_server_entry.py tests/test_hub_owner_auth.py tests/test_hub_sessions.py -q；隔离浏览器核对各能力状态及对应HTTP拒绝。

人工与恢复边界：无人工前置；仅保护本轮精确数据与写责。

主覆盖：服务装配和新增集成验收，原98项由后续轮统一回归；所有相关分支还需O6复核。

### O1 项目理解和日常导航

#### O1.1 补齐项目定义与可信的概览字段

依赖：O0.3。主要入口：src/hub/project_service.py；src/hub/connection_records.py；src/hub/connection_extensions.py；src/hub/web/connection_view.mjs。

实施：

1. 增加用途、主要产出、多角色、正式名/已确认别名、主项目与工作副本的只读投影；优先现有声明/登记/有资格的来源。
2. 长期定义与当前目标、阶段、执行者、阻塞/恢复条件、下一步分开；原始术语提供可读解释和原文入口。
3. 保留字段来源、时间、未知/零/空/不适用差异；把读取成功、结构有效、内容缺口、当前证据覆盖分别呈现。
4. 来源缺事实时列缺口；可由agent写有来源的摘要或明确管理提议，不能伪造业务用途、负责人或已确认别名。

通过条件：

- A/B分别可找到定义字段与缺失说明；active并存局部blocked、0/N、未知N、历史失败等组合表达准确。
- 不再把integration_quality.complete直译为日常查看信息完整；业务分母不被工程107/111替代。

相关检查：python -m pytest tests/test_hub_project_service.py tests/test_pch_source_extensions.py tests/test_hub_field_mapping.py -q；node --test tests/test_hub_connection_view.mjs。

人工与恢复边界：口述三十天/Actor 4映射未确认，使用稳定ID完成通用功能；待确认称呼不自动绑定真实发送对象。

主覆盖：B-01、B-02、B-04、B-05、B-06、B-07、B-09、D-08、V-02、V-05；所有相关分支还需O6复核。

#### O1.2 让首页直接找项目并支持可信别名

依赖：O1.1。主要入口：src/hub/web/hub.js；src/hub/web/directory.mjs；tests/test_hub_directory.mjs。

实施：

1. 默认简洁项目列表，搜索、当前筛选、需关注项目和项目入口前置；同步摘要收起，树/甘特按需进入。
2. 搜索正式名、ID、已确认别名和明确用途关键词；说明命中原因，无结果可清空筛选并查看正式名称。
3. 同一过滤投影用于列表/树/时间线；区分全部登记、读取资格、匹配数、当前页及缺来源。

通过条件：

- 1280×800和1440×1050首屏可找到搜索与项目入口；默认不再展开43项全量甘特长页。
- 103项目×搜索/状态/来源/分组/视图组合无重复遗漏；相似名/多副本不被误合并。

相关检查：node --test tests/test_hub_directory.mjs tests/test_hub_connection_view.mjs；浏览器列表/树/甘特组合及103项分页遍历。

人工与恢复边界：无人工前置；仅保护本轮精确数据与写责。

主覆盖：A-02、A-03、A-04、V-01、V-06；所有相关分支还需O6复核。

#### O1.3 拆解长详情并把任务动作放到可达位置

依赖：O1.1。主要入口：src/hub/web/hub.js；src/hub/web/index.html；src/hub/web/tasks.js。

实施：

1. 提供可直达的概览、任务、排期与关系、来源入口，按现有hash路由增量拆分，不预设重做整个前端。
2. 概览只呈现一次定义、关键状态、阻塞/恢复与下一步；详细字段/内部ID/同步诊断进入来源区域。
3. 标题附近给清晰任务动作；已有任务、会话、产物和预览互相链接并显示归属项目。
4. 兼容现有项目/任务深链接和刷新；信息区数量以功能可达性决定，复用现有组件和样式。

通过条件：

- 任意项目概览到任务输入/状态区最多一个明确导航操作，无需先滚动约6500px。
- 任务、排期关系、来源有独立可复制定位；主导航高亮和所属项目返回路径准确。

相关检查：node --test tests/test_hub_web_logic.mjs tests/test_hub_business_tasks.mjs；浏览器A/B各自完整导航、深链接、刷新回归。

人工与恢复边界：无人工前置；仅保护本轮精确数据与写责。

主覆盖：A-07、V-03、V-04；所有相关分支还需O6复核。

#### O1.4 保留返回上下文并给出可行动错误

依赖：O1.2、O1.3。主要入口：src/hub/web/hub.js；src/hub/web/common.js；src/hub/web/directory.mjs。

实施：

1. 可公开查询写入URL，滚动/焦点写入history.state；还原筛选、分组、视图、展开对象和选中项目。
2. 保留generation迟到请求保护；路由变动取消或忽略旧响应，返回不会误恢复另一对象。
3. 统一401/403/404/409/500/503/离线/超时的用户含义与恢复动作；保留脱敏诊断入口。
4. GET可重读；写入结果未知先按同request_id核对；错误/冲突保留草稿，禁止通用自动重发。

通过条件：

- 搜索筛选→项目→后退还原列表位置和焦点，前进/刷新保持对象；无来路深链接有安全返回。
- 每种错误逐次独立注入并确认命中；对象缺失、缺身份、权限不足、冲突与失联显示不同。

相关检查：node --test tests/test_hub_web_logic.mjs tests/test_hub_directory.mjs；浏览器逐类错误+后退前进+迟到A不覆盖B。

人工与恢复边界：无人工前置；仅保护本轮精确数据与写责。

主覆盖：A-05、A-06、N-04、V-07、E-03、E-04；所有相关分支还需O6复核。

### O2 自动采集与页面更新

#### O2.1 打通来源版本到可见页面的有界更新

依赖：O0.3。主要入口：src/hub/connection_sources.py；src/hub/connection_refresh.py；src/hub/project_service.py；src/hub/web/hub.js。

实施：

1. 规定来源版本、最后尝试、最后成功、业务事实时间、ledger head、页面版本的关联与显示。
2. 合成来源真实写入新值→真实resolver/ledger/API→已打开页面更新；不能只更新时间戳。
3. 复用同步器做有界增量采集；页面可见时观察head并刷新当前投影，隐藏时降频、恢复可见先对账，GET不触发采集。
4. 保留用户草稿/滚动/当前任务；单项目刷新只作用指定资格范围。

通过条件：

- 同一候选取得旧/新来源SHA、ledger sequence、API值和DOM值对应证据；A更新不清空B或其他草稿。
- 断开模型能力后发现、采集、查询、管理、排期和基础展示仍能工作。

相关检查：python -m pytest tests/test_pch_project_api.py tests/test_hub_daily_sync.py tests/test_hub_sync_status.py -q；新增源变化→采集→可见页面端到端测试。

人工与恢复边界：无人工前置；仅保护本轮精确数据与写责。

主覆盖：B-08、D-03、D-04、D-05、N-02；所有相关分支还需O6复核。

#### O2.2 验证并发刷新、原子读取和故障恢复

依赖：O2.1。主要入口：src/hub/connection_refresh.py；src/hub/connection_sources.py；tests/test_hub_refresh_concurrency.py；tests/test_hub_source_atomicity.py。

实施：

1. 复用request恢复、SQLite/CAS和单项目持久化；定义手动×自动、同ID重放、不同ID重叠的合并/排队/忙碌语义。
2. 真实多进程制造同head竞争、锁忙和进程中断，恢复仍使用原请求身份。
3. 覆盖半写YAML、文件/目录替换、权限拒绝、来源读取中更新；一致快照或明确失败，不发布混合字段。
4. 单坏源降级保留历史与失败原因，其他项目可读；恢复只重试有变化或明确可恢复部分。

通过条件：

- 无重复效果、静默CAS覆盖或混合新旧业务状态；每项目结果数量和全局请求结果一致。
- 失败、部分成功和未提交分别呈现，历史成功不冒充当前。

相关检查：python -m pytest tests/test_hub_refresh_concurrency.py tests/test_hub_refresh_recovery.py tests/test_hub_source_atomicity.py -q；真实HTTP双刷新及UI恢复观察。

人工与恢复边界：无人工前置；仅保护本轮精确数据与写责。

主覆盖：D-06、D-07、D-10、D-12、N-05；所有相关分支还需O6复核。

#### O2.3 对账读取资格、分页与全量登记范围

依赖：O1.2、O2.1。主要入口：src/hub/connection_sync.py；src/hub/project_service.py；data/connections/sync_scope.yaml；data/registry/external_projects.yaml。

实施：

1. 以当前登记和读取权限复核原39个确认本地根，保留43登记项等历史基线及变更原因。
2. 优先复用未变来源证据，只读有资格的相关来源；removed_local、云、非当前路径和未授权候选保持排除。
3. 在合成登记中为排除对象设访问探针，证明零解析/读取/启动；不能以没动作日志替代防护测试。
4. 对缺来源/解析错误/部分字段逐项展示实际状态，不为覆盖率自动修子项目或编造完整事实。

通过条件：

- 当前集合逐ID无漏项重复，登记总数、同步资格、尝试、成功/部分/失败和内容完整度各有口径。
- 103项全分页结合过滤仍正确；排除对象点击也不能扩大权限。

相关检查：python -m pytest tests/test_hub_daily_sync.py tests/test_hub_registry_storage.py tests/test_pch_project_api.py -q；python scripts/check_registry.py --metadata-only；合成排除对象访问探针及列表总数核对。

人工与恢复边界：不逐项目深挖业务内容；来源配置缺失仅阻塞对应项目，PCH代码和其他项目继续。

主覆盖：D-01、D-09、D-11、N-06；所有相关分支还需O6复核。

#### O2.4 复用现有调度并声明刷新时效

依赖：O2.1、O2.2。主要入口：src/hub/systemd_refresh.py；src/hub/refresh_schedule.py；scripts/install_hub_refresh.py；src/hub/web/hub.js。

实施：

1. 保留既有用户级systemd调度作为唯一采集调度入口，扩展前先核对unit写责和原配置；不新建应用内第二调度器。
2. 执行默认目标为服务运行时每60秒一次有界增量检查，可见页面每5秒核对head；把正常稳定源的端到端目标设为90秒内，作为本计划新目标而非原报告既定SLA。
3. 保留既有每日完整批次，增量仅检查既定资格来源；单批超过间隔不叠加，暴露延迟与过载，基于实测调大并记录理由。
4. 隔离中通过真实日历触发用户级测试unit观测自动批次，区别模拟时钟/手动start；正式旧unit覆盖或服务切换在O6.3单独核责。

通过条件：

- 记录timer触发→进程/请求→ledger→页面链；基础采集无LLM，不把timer active或手动start当自动成功。
- 至少三次受控真实调度窗口测量延迟；稳定样本达目标，超标有根因和调整依据，不能静默放宽。
- 自然业务源更新有机会则只读观测；没有自然事件仍保留该分支未验，不无限空等。

相关检查：python -m pytest tests/test_hub_systemd_refresh.py tests/test_hub_refresh_schedule.py tests/test_hub_daily_sync.py -q；真实短周期隔离timer至少三次触发与来源→DOM观测。

人工与恢复边界：用户级systemd/注销持久能力若不可用，仅实际时间/注销分支受限；先完成确定性和当前会话运行链。不得提权、覆盖其他unit或改变登录配置。

主覆盖：D-02；所有相关分支还需O6复核。

### O3 关系排期与数据权威

#### O3.1 统一关系阅读口径并解释归属和方向

依赖：O1.1、O1.3。主要入口：src/hub/project_service.py；src/hub/management_store.py；src/hub/web/hub.js；src/hub/web/management.mjs。

实施：

1. 投影已确认管理关系、来源事实关系和未确认提议，分别保留权威、来源、方向和版本。
2. 保存A依赖B后概览与关系页立即显示同一管理版本，B能看到反向依赖；提议关系仍待确认。
3. 支持多角色、多类型关系、主项目/工作副本归属，跳转使用稳定实例ID并保留返回上下文。

通过条件：

- 不再出现管理已保存而读者区统一称尚未确认；无关系、未知关系、待确认提议不同。
- 任务层级、项目归属和依赖边互不混淆，关系不授予读取/写入/分发权限。

相关检查：python -m pytest tests/test_hub_management_api.py tests/test_hub_management_store.py tests/test_hub_project_service.py -q；node --test tests/test_hub_management.mjs tests/test_hub_directory.mjs；浏览器真实管理保存→反向阅读→跳转返回。

人工与恢复边界：无人工前置；仅保护本轮精确数据与写责。

主覆盖：B-03、R-01、R-02、R-03、R-04、R-05；所有相关分支还需O6复核。

#### O3.2 补齐关系编辑、建议接受和冲突边界

依赖：O3.1。主要入口：src/hub/management_store.py；src/hub/design_service.py；src/hub/web/management.mjs。

实施：

1. 隔离完整新增/修改/撤销关系；建议→明确接受使用现有决策入口，不伪造owner事实。
2. 验证自依赖、循环、重复、失效目标与目标被排除；循环允许警告还是拒绝沿现有契约处理并解释。
3. 覆盖来源/管理/建议三方冲突；保存输入保留，版本冲突可重读与显式重新应用。

通过条件：

- 每类无效关系有可读定位和恢复；失败不损坏旧关系。
- 提议接受不自动执行任务、不修改源业务，权限探针证明无越权读取。

相关检查：python -m pytest tests/test_hub_management_store.py tests/test_hub_design_service.py tests/test_hub_management_api.py -q；真实HTTP两编辑者CAS及浏览器建议接受/撤销链。

人工与恢复边界：无人工前置；仅保护本轮精确数据与写责。

主覆盖：B-10、R-06、R-07、R-08、R-10；所有相关分支还需O6复核。

#### O3.3 完成日期、里程碑和甘特操作

依赖：O3.1。主要入口：src/hub/time_semantics.py；src/hub/rolling_plan.py；src/hub/web/management.mjs；src/hub/web/timeline.mjs。

实施：

1. 日期字段返回具体错误并关联aria-invalid/aria-describedby，修改后可保存，失败不丢输入。
2. 覆盖无日期、仅开始/仅结束、里程碑、跨月/年、Asia/Tokyo跨日、带偏移时间及夏令时输入的规范化边界；不改主机时区。
3. 计划、滚动估计和来源实际明确区分；同版本概览/时间线一致。
4. 甘特缩放×筛选×窄窗支持可读名称/日期/未排期和可达操作，不依赖hover。

通过条件：

- 结束早于开始明确说明原因；未知实际日期不被管理日期或估计填充。
- 实际保存后源进度和源事实不变，空排期项目始终可找到。

相关检查：python -m pytest tests/test_hub_time_semantics.py tests/test_hub_rolling_plan.py tests/test_hub_management_api.py -q；node --test tests/test_hub_management.mjs tests/test_hub_timeline.mjs；浏览器日期组合及甘特操作。

人工与恢复边界：无人工前置；仅保护本轮精确数据与写责。

主覆盖：S-01、S-02、S-03、S-04、S-05、S-06、E-05；所有相关分支还需O6复核。

#### O3.4 呈现依赖影响并验证管理版本恢复

依赖：O3.2、O3.3。主要入口：src/hub/project_service.py；src/hub/metric_management.py；src/hub/web/management.mjs。

实施：

1. 依赖方blocked时显示可追溯的派生影响、路径与来源版本，不覆盖项目原始状态。
2. 依赖变化尚未调日期时提示待复核，不自动移动别人计划、不捏造实际完成。
3. 两个浏览器编辑者保存、来源再导入、刷新、隔离重启后核对管理字段持久化和CAS输入恢复。

通过条件：

- 管理层和源事实、建议层均保留；重启/采集不会覆盖已保存管理配置。
- 无关系循环无限递归，无自动停工/分发/跨项目安排效果。

相关检查：python -m pytest tests/test_metric_management.py tests/test_hub_management_store.py tests/test_hub_rolling_plan.py -q；浏览器双编辑器409→重读→显式应用；隔离重启后重读。

人工与恢复边界：无人工前置；仅保护本轮精确数据与写责。

主覆盖：R-09、S-07、S-08；所有相关分支还需O6复核。

### O4 页面任务和并行闭环

#### O4.1 使任务能力和合法认证路径可使用

依赖：O0.3、O1.3。主要入口：src/hub/owner_auth.py；src/hub/local_service.py；src/hub/web/hub.js；src/hub/task_service.py。

实施：

1. 复用owner/CSRF/项目范围校验，完善连接入口与缺配置说明；基本只读浏览不要求执行身份。
2. 按服务端能力驱动任务创建、继续、控制及provider状态；身份失效或撤权立即阻断新增效果并保留草稿。
3. 完成合成guest、错误身份、owner只读、owner可执行、权限不足、provider无能力的浏览器测试。

通过条件：

- 401/403/503能力不足各自有原因和下一步；前端不能通过按钮/JSON自行获得grant。
- 正式缺owner只阻塞正式写动作；完整隔离任务测试无需人类登录。

相关检查：python -m pytest tests/test_hub_owner_auth.py tests/test_hub_task_controls.py tests/test_hub_task_grants.py -q；浏览器原生认证入口及服务端撤权回归。

人工与恢复边界：不生成、读取或替换正式owner密钥；有既有合法身份可复用其受控入口，否则正式登录留最终审核前的最少事项。修改安全授权语义若超出复用现有边界，先使用项目规定的必要独立审查；不扩大到全项目三角色流程。

主覆盖：A-08、T-14；所有相关分支还需O6复核。

#### O4.2 实现不依赖截图批注的普通项目指令

依赖：O4.1。主要入口：src/hub/task_service.py；src/hub/task_store.py；src/hub/local_service.py；src/hub/web/hub.js；src/hub/web/tasks.js。

实施：

1. 建立普通文本TaskSpec，绑定稳定project_id、workspace/storage、request_id、目标/范围、只读或写入等级、输入版本及新建/继续对象。
2. 图片/批注作为可选上下文，保留现有CSS/story历史可读性，不为普通任务伪造PNG/annotation。
3. 由服务端核对存储、cwd、权限和精确native绑定；通用业务任务不复用仅限fixture/CSS/story的grant伪装。
4. 页面显示接收项目、执行位置的可读名称、任务意图、效果和权限；继续仅选择已验证owned会话。

通过条件：

- 从项目上下文直接编写普通只读任务，无需截图；篡改project/thread/cwd/grant被服务端拒绝。
- 未确认Actor 4等口述名称绝不成为发送目标；陌生会话只读展示。

相关检查：新增普通文本TaskSpec/API/浏览器测试；python -m pytest tests/test_hub_task_runtime.py tests/test_hub_native_business.py tests/test_hub_task_drafts_http.py -q。

人工与恢复边界：无人工前置；仅保护本轮精确数据与写责。

主覆盖：T-01、T-02；所有相关分支还需O6复核。

#### O4.3 保存草稿并实现幂等发送和重连

依赖：O4.2。主要入口：src/hub/task_store.py；src/hub/task_service.py；src/hub/web/tasks.js；src/hub/web/hub.js。

实施：

1. 草稿按项目/owner/目标会话隔离，离开返回/刷新/重启可恢复，未发送不创建模型任务。
2. 双击、同request重放、回执丢失、浏览器超时和网络重连先查原request状态；禁止新ID盲目重发。
3. 回复/事件严格绑定task/project/thread/turn，长回复和产物不串位；历史导入结果标来源。

通过条件：

- A草稿切B再返回不丢不串，改目标需明确重新绑定；同意图只有一次排队/执行。
- 重开任务可见原指令、状态、回应和历史，离线不被误写任务失败。

相关检查：python -m pytest tests/test_hub_task_drafts.py tests/test_hub_task_drafts_http.py tests/test_hub_task_recovery.py -q；node --test tests/test_hub_web_logic.mjs tests/test_hub_approval_ui.mjs；浏览器丢回执/重连/刷新/双击故障注入。

人工与恢复边界：无人工前置；仅保护本轮精确数据与写责。

主覆盖：T-03、T-09、T-10、T-11；所有相关分支还需O6复核。

#### O4.4 装配worker并完成控制和结果生命周期

依赖：O4.2、O4.3。主要入口：scripts/hub_worker.py；src/hub/task_worker.py；src/hub/codex_adapter.py；src/hub/task_lifecycle.py；src/hub/task_approvals.py。

实施：

1. 复用TaskWorker/Adapter装配普通任务队列，启动和退出均有精确owned身份；正式默认不因读页启动模型。
2. 解释草稿、接收、排队、发送待确认、运行、等待输入/审批、检查完成、待验收、失败、取消和待对账。
3. 完成追问、忙碌排队、另一新任务、精确取消与取消×迟到完成；结果审阅不自动代owner接受。
4. 租约/fence/撤权/重复控制按现有契约处理，禁止把未知状态标空闲或成功。

通过条件：

- mock确定性任务经页面HTTP→真实队列/worker→回复页面完整通过，控制效果仅命中确切task/thread/turn。
- 取消失败/超时可核对，重连不重复执行，worker异常可恢复且不抢占未知writer。

相关检查：python -m pytest tests/test_hub_task_runtime.py tests/test_hub_busy_controls.py tests/test_hub_task_controls.py tests/test_hub_task_lifecycle.py -q；python -m pytest tests/test_hub_task_review.py tests/test_hub_task_approvals.py tests/test_hub_approval_transport.py -q；node --test tests/test_hub_approval_ui.mjs。

人工与恢复边界：无人工前置；仅保护本轮精确数据与写责。

主覆盖：T-05、T-08、T-12、T-16；所有相关分支还需O6复核。

#### O4.5 跑通两条有界真实页面任务链

依赖：O4.4。主要入口：src/hub/codex_adapter.py；src/hub/writer_handoff.py；tests/真实provider smoke入口；本地忽略证据。

实施：

1. 已有合法provider能力时，为两个合成项目创建全新owned原生测试会话/工作区，经真实页面分别发送只读检查并取得实际回复。
2. 核对浏览器请求、TaskService请求ID、native thread/turn、worker事件、页面结果；不直接SDK或后台调用替代页面发送。
3. 各自追问、重开和历史回看；对一个owned可控忙碌测试执行精确取消并核对后续回应处理。
4. 真实指令限读取本任务合成文件并返回摘要，不改业务项目、不生成媒体、不扩大外部效果。

通过条件：

- A、B各自有同候选页面→真实native执行→真实回复→页面的完整对应证据。
- 真实任务与mock模式分别统计；已有业务候选/旧截图/旧结果不能替代本次回复。

相关检查：明确标为live的有界浏览器smoke；核对两条native身份和任务效果；现有adapter配置/协议/能力检查；失败记录真实错误码与零/未知提交效果。

人工与恢复边界：provider登录/额度/认证不可用只阻塞live分支；不得更换账号/模型绕过限制或复制会话。继续O4.6隔离部分、O5/O6自动化，保留真实闭环未验。原业务A/B口述映射仅阻塞对应正式对象发送，不阻塞两个合成项目的真实provider测试。

主覆盖：T-04、T-06、T-07；所有相关分支还需O6复核。

#### O4.6 证明双项目并行和控制互不干扰

依赖：O4.4。主要入口：src/hub/task_store.py；src/hub/task_worker.py；src/hub/web/hub.js；src/hub/web/tasks.js；tests/浏览器并行旅程。

实施：

1. 两条owned任务同时变化：A接收/运行/取消，B持续回应；检查队列、store、native和DOM各层身份。
2. 切项目、草稿编辑、迟到GET/回复、自动采集、管理保存和控制同时发生；分别注入乱序与CAS。
3. mock完整覆盖确定性竞态，live部分在O4.5可用后补真实并行；不控制其他原本运行任务。

通过条件：

- 取消/重试/追问A不影响B，来源与管理变更不串到另一项目；迟到回应不会恢复旧页面。
- 真实并行分支必须同时有两条live owned任务；单GET隔离或mock结果不代替。

相关检查：python -m pytest tests/test_hub_busy_controls.py tests/test_hub_task_runtime.py tests/test_hub_refresh_concurrency.py -q；双浏览器/双任务可控竞态；O4.5可用后live并行证据。

人工与恢复边界：外部在推进的项目只作合法只读观察，零控制；无live能力时隔离分支先完成，native并行保留待验。

主覆盖：T-13、T-15、N-03、N-10；所有相关分支还需O6复核。

### O5 产物预览和交互完整性

#### O5.1 核对产物预览的版本和返回路径

依赖：O1.3、O4.3。主要入口：src/hub/material_service.py；src/hub/preview_service.py；src/hub/page_inventory.py；src/hub/web/materials.js；src/hub/web/captures.mjs。

实施：

1. 任务回复、产物、截图、页面清单使用稳定归属和版本链接；返回原项目/任务不丢上下文。
2. 区分当前可操作页面、保存截图、导入历史和预览缺失，展示时间/来源和适用范围。
3. 验证路径解析、失效文件、无预览、长名与多产物；测试不产生正式业务媒体。

通过条件：

- 用户能从本轮任务找到本轮结果；历史截图不被标为当前可操作页面。
- 仅访问当前允许数据根，无任意路径/软链越界和敏感绝对路径展示。

相关检查：python -m pytest tests/test_hub_materials.py tests/test_hub_preview_policy.py tests/test_hub_page_inventory.py -q；node --test tests/test_hub_page_inventory.mjs tests/test_hub_captures.mjs。

人工与恢复边界：无人工前置；仅保护本轮精确数据与写责。

主覆盖：V-12；所有相关分支还需O6复核。

#### O5.2 补全键盘操作、焦点和动态公告

依赖：O1.4、O3.3、O4.4。主要入口：src/hub/web/index.html；src/hub/web/hub.js；src/hub/web/tasks.js；src/hub/web/management.mjs；src/hub/web/hub.css。

实施：

1. 纯键盘完成找项目→关系→任务草稿→发送/回应→返回；复用现有skip link和focus-visible。
2. 对话框/分区/错误后焦点进入与返回明确，无陷阱；语义名称、标签、aria状态和错误关联正确。
3. 加载、接收、完成、错误、冲突等动态反馈可在可访问树中感知，避免轮询反复抢焦点。

通过条件：

- 自动可访问树+键盘轨迹覆盖完整已认证任务链；不只检查Tab outline。
- 无色彩唯一编码，禁用操作有可理解原因，输入失败仍可编辑。

相关检查：浏览器键盘旅程、可访问树和自动规则检查；复用相关Node交互测试；最终候选完整UI rubric。

人工与恢复边界：真实读屏体验没有可用设备/能力时保留最终人工审核项；自动ARIA检测不冒称真实读屏通过。

主覆盖：E-01、E-02；所有相关分支还需O6复核。

#### O5.3 验证窄屏、缩放、对比度和减少动画

依赖：O1.4、O3.3、O4.4。主要入口：src/hub/web/hub.css；src/hub/web/hub.js；src/hub/web/tasks.js；src/hub/web/timeline.mjs。

实施：

1. 复用现有样式和44px动作目标，验证390×844、1280×800、1440×1050与实际200%缩放。
2. 组合长名、长回复、多角色、多关系、大量未知、已认证表单和错误，关键动作无丢失/遮挡/文档级横向溢出。
3. 计算实际前景/背景对比度；本计划新设普通文字4.5:1、大字及关键非文本控件3:1的检查目标，按实际合成背景计算。
4. reduced-motion下触发真实任务状态变化与错误恢复，信息、焦点及操作保留。

通过条件：

- A/B都覆盖窄屏与200%操作；按钮可达且内容可读，不仅静态截图。
- 对比度有数值和元素定位；动态减少动画有对照证据，不声称全面WCAG认证。

相关检查：浏览器视口/真实缩放/对比度计算/动态motion测试；完整检查console、network及页面渲染。

人工与恢复边界：真实手机触控若不可用仅保留该设备接受未验；不影响浏览器响应式实现和自动化完成。

主覆盖：V-08、V-09、V-10、V-11、E-07；所有相关分支还需O6复核。

#### O5.4 覆盖空态、错误、离线恢复和运行负载

依赖：O0.2、O2.2、O4.4。主要入口：src/hub/web/common.js；src/hub/web/hub.js；src/hub/web/tasks.js；tests/故障与性能场景。

实施：

1. 覆盖空项目/无任务/无关系/无排期/服务未启用，和未认证/不可读/失联的差异。
2. 逐类HTTP错误、网络超时、浏览器context离线/重连、保存500、任务回执未知；状态有期限与恢复动作。
3. 采集首屏、103项目分页、长回复、双任务和刷新重叠时耗时、CPU/RSS、请求数与轮询频率；使用软件渲染不占GPU业务。
4. console/pageerror与关键network逐旅程采证，既有基线和故障注入单列，发现回归才扩大测试。

通过条件：

- 无永久loading、无限重试、重复提交和离线数据伪当前；错误恢复后草稿与路由保持。
- 给出可复现负载样本和前后对比，持续增长/重叠请求/严重导航延迟有修复；单次4.4ms样本不当容量结论。

相关检查：浏览器故障矩阵和最终DOM/请求确认；可控负载采样，报告样本条件及范围；不做无依据全系统基准。

人工与恢复边界：无人工前置；仅保护本轮精确数据与写责。

主覆盖：N-11、E-06、E-08、E-09；所有相关分支还需O6复核。

### O6 同候选全面回归和最终审核包

#### O6.1 同候选重跑全部98项的确定性分支

依赖：O1.4、O2.2、O2.3、O3.4、O4.4、O5.1、O5.2、O5.3、O5.4。主要入口：docs/testing/pch-user-audit-20261015/TEST_CASES.md；本次本地证据；STATE.yaml#pch_refactor.user_test_optimization_20261016。

实施：

1. 冻结ready候选的语义文件清单/哈希、配置和数据版本；不是暂停其他业务。
2. 逐项执行原98用例全部适用隔离分支，包括A/B、过滤组合、边界、错误和双任务；保留原始预期不降级。
3. 功能、视觉、桌面/移动、console、network、数据隔离分别记结果；每ID仅计一次总结果并保留分支记录。
4. 失败则回到相关实现做实质修复，重新冻结新候选，只复用未受影响且身份有效的证据。

通过条件：

- 98个ID无缺失/重复；每项有步骤、输入/候选/效果身份、观察、证据与结论。
- 旧PASS也按新候选适用性复核；fixture不再以B03缺装配跳过整组T。

相关检查：原98项全量浏览器/HTTP/服务联动回归；完整适用Python+Node检查，采用当前CI精确入口。

人工与恢复边界：本轮不依赖O4.5/live正式认证通过即可执行隔离部分；总体PASS仍需要全部必需分支成立。

主覆盖：服务装配和新增集成验收，原98项由后续轮统一回归；所有相关分支还需O6复核。

#### O6.2 复核正式只读数据和可用真实链

依赖：O6.1。非阻塞证据输入：O2.4的调度结果或明确限制，未通过真实时间分支不阻止正式只读核对。主要入口：运行服务清单；本次本地R/H/C证据；STATE.yaml#pch_refactor.user_test_optimization_20261016。

实施：

1. 正式环境重新登记候选，与隔离候选不同则分别报告，不合并为同版本通过。
2. 对实际允许登记项目复核源字段→采集→API→页面，观察既有自动批次和自然更新窗口，保留partial/失败。
3. 具备当前合法能力时完成正式owner操作及O4.5/O4.6的live链；不操作未知真实业务会话。
4. 缺身份、口述映射、自然事件、provider或真实设备时仅列对应阻塞及恢复动作，继续结果整理。

通过条件：

- 正式R、合法H/C与I分栏；新自然源SHA有无被采集显示明确，不把受控fixture升级为自然业务验收。
- 两条live任务、真实provider和正式业务A/B是不同分支，不互相替代。

相关检查：正式只读版本/字段对账；可用身份下有界任务链；原生thread/turn/请求与当前DOM对应核验。

人工与恢复边界：没有自然事件或正式身份时不无限等待，不停止独立工程；相应原测试分支保留BLOCKED/UNVERIFIED并交最终审核。

主覆盖：N-01；所有相关分支还需O6复核。

#### O6.3 核对运行候选、切换边界和恢复

依赖：O6.1。主要入口：scripts/hub_server.py；既有运行清单；本地候选和恢复记录。

实施：

1. 核对代码候选、HTTP资源、数据schema和实际进程；防止checkout改好但日常服务仍旧版。
2. 优先启动独立端口的完整候选供审核，保留现有8766服务。
3. 只有核对写责、inflight请求、账本兼容、原配置与恢复入口后才安排既有服务切换；唯一数据写责未知则不切。
4. 隔离验证停止/重启、旧数据读取、任务恢复和可逆退回；正式迁移需真实生产边界授权，不能由计划暗授。

通过条件：

- 最终审核入口明确对应所测候选；本机文件变化不被宣称已进入正式运行。
- 恢复过程不丢业务数据和其他任务；新候选只读/隔离预览可在不切服务时交付。

相关检查：服务身份/HTTP资源哈希对账，隔离重启与数据兼容检查；当前UI候选完整rubric结果随审核入口提供。

人工与恢复边界：本执行Prompt默认授权独立测试实例，不默认授权生产服务/账本迁移；真实切换受原项目写责与迁移边界约束。仅此动作受限时交付独立候选并继续G。

主覆盖：服务装配和新增集成验收，原98项由后续轮统一回归；所有相关分支还需O6复核。

#### O6.4 交付最终审核包并保留准确结论

依赖：O6.1。结果输入：O6.2、O6.3的当前结果记录，允许明确的外部BLOCKED/UNVERIFIED，不要求先完成生产切换。主要入口：本计划验收汇总；STATE.yaml#pch_refactor.user_test_optimization_20261016。

实施：

1. 给出当前可打开入口/启动命令、候选、主要变化、代表性旅程和全部98项结果，列F01至F09关闭证据。
2. 分别报告代码/隔离、真实通道、正式运行、Git和所有者接受；保留原107/111与旧4项待接受。
3. 把口述映射、正式登录、真实手机/读屏等确实剩余项合成最少审核清单；不要求用户重复实现、查源码或组织测试。
4. 收尾所有owned agent/browser/display/worker/测试unit；不关闭日常服务/其他任务。

通过条件：

- 用户一次审核可看到成品、操作和限制；未验分支不隐藏或移出98分母。
- 工程可交付可为with_gaps，但产品整体PASS/Goal complete必须全部约定验收含所有者最终接受成立。

相关检查：108覆盖对象、历史分母、证据链接与资源归属的最终核验。

人工与恢复边界：本轮是最终人工审核入口，不是自动把用户未回复当接受；等待审核不能阻塞先完成的工程工作或G准备。

主覆盖：N-07、N-12；所有相关分支还需O6复核。

### G 独立整理与授权交付

#### G.1 独立核对差异归属与私人资料

依赖：O6.1。结果输入：O6.3的运行身份/写责记录；生产切换受阻不阻止安全本地Git准备。主要入口：本仓Git工作树；精确ignore规则；本次owned候选。

实施：

1. 按G01至G05逐组核对status、原生任务/文件归属、已交付字节、旁支独有历史和运行checkout依赖。
2. 把旧checkout对已交付main的投影与真正新差异分开；不为status归零删除他人文件。
3. DB/真实任务消息/凭据/完整私人路径清单/敏感截图和原始证据留本地；无归属项保留。

通过条件：

- 产生精确可提交单元与本地保留项，且不把未跟踪文件当废物。
- 原服务依赖和已有manual edits完好，归属不明只阻塞对应提交组。

相关检查：G-01至G-05逐项只读对账；按精确文件与已交付commit比较。

人工与恢复边界：无人工前置；仅保护本轮精确数据与写责。

主覆盖：E-10、G-01、G-02、G-03、G-04、G-05；所有相关分支还需O6复核。

#### G.2 组成可审阅提交并验证精确候选

依赖：G.1。主要入口：本次owned安全代码和测试；必要精确ignore规则。

实施：

1. G06只对已确认生成物加精确ignore并测反例；已跟踪项单独处理，禁止宽泛忽略藏业务源码。
2. 按独立可验证功能组构造提交候选，不借整理大范围refactor。
3. G07/G08复核精确差异、必要测试、隐私与baseline；无授权时只准备候选，不stage或commit。

通过条件：

- 每个候选可解释、可验证、可回退；包含所有运行必需安全源码，私人数据不入公开候选。
- 不覆盖/搬运/借用其他仓交付，未归属差异仍留原处。

相关检查：git diff --check -- 精确owned路径；当前CI适用检查；精确暂存内容复核。

人工与恢复边界：此轮准备工作可自治。实际stage/commit/push只在执行会话有明确Git交付授权时进行；本次规划无此效果。

主覆盖：G-06、G-07、G-08；所有相关分支还需O6复核。

#### G.3 按授权正常交付并核对远端

依赖：G.2。主要入口：授权分支/main或PR；本地唯一状态的delivery字段。

实施：

1. G09实时核对origin、可见性、main和保护；只stage验证过的owned unit，正常提交和推送。
2. 保护要求PR则创建并attach，遵守检查/审批；无权限或待外部审批只记录远端交付阻塞。
3. G10核对远端包含精确commit与日常运行版本的关系；不声称main永远等于本地HEAD。
4. 保存剩余本地项去向、恢复依赖和唯一下一步；不为了清树reset/stash/强推/改remote。

通过条件：

- 授权交付的准确远端commit或PR可核查；本地、分支、main、运行版本分别报告。
- G10覆盖结论与98产品测试分开；提交/CI成功不替代用户产品接受。

相关检查：实时远端commit/祖先关系与保护检查；创建PR后attach_artifact；G-09、G-10证据及最终资源/差异复核。

人工与恢复边界：最终执行Prompt若被用户发送则包含精确owned安全单元的正常Git交付授权；本规划阶段不commit/push。外部保护/审批不可绕过，仅阻塞交付分支。

主覆盖：G-09、G-10；所有相关分支还需O6复核。

## 六 验证方法与证据要求

### 6.1 三类环境分别记结论

I使用真实Hub程序、静态资源和完整服务，数据根/存储/owner/端口独立，mock控制器明确替代native协议；证明确定性产品行为。L使用同一产品页面和真实provider、精确owned native会话，业务数据仍为本任务合成工作区；证明真实执行通道。R/H/C使用正式环境的只读、合法单次采集、实际合法指令路径；证明正式身份与真实业务对象适用性。

三类不能互相补过。正式代码候选与I/L候选不同，结果分开。普通单元测试、后台直接调用、静态截图或历史产物都不能替代浏览器端到端路径。对本轮未变化且身份有效的底层单元证据可以复用；新的UI候选仍需完整旅程。

### 6.2 复用的仓库检查

Python以当前项目约定的3.12隔离环境为准，Node以当前CI约定为准，不为本计划擅自升级依赖或安装全局工具。每轮先跑其列出的精确模块。最终候选执行当前CI适用全套：

- python -m unittest discover -s tests -q
- python -m pytest tests -q
- node --test tests/test_hub_connection_view.mjs tests/test_hub_business_tasks.mjs tests/test_hub_management.mjs tests/test_hub_timeline.mjs tests/test_hub_directory.mjs tests/test_hub_page_inventory.mjs tests/test_hub_captures.mjs tests/test_hub_approval_ui.mjs tests/test_hub_web_logic.mjs
- 当前CI的repo/schema/registry检查，使用真实适用task ID。旧治理task的检查不授予启动旧任务或覆盖其状态的权限。
- 新增浏览器fixture和旅程入口在O0.2确定实际可运行命令，并写入同一状态的checks；上文“新增测试”不是现已存在的虚构命令。

装配、任务并行、源原子读取、版本冲突和效果未知采用有意义的集成检查；不要写只镜像实现的测试。全量检查出现既有baseline失败，单列相关版本、归属和退出码；只修本次相关缺陷，不借机清理全仓。一个候选完整通过后，不在无新变化时重复跑同样全量检查。

### 6.3 浏览器和运行证据

Linux GUI使用任务私有显示/浏览器profile，核对PID、start_ticks、窗口和端口归属；不抢用户前台、不启动GPU业务。需要界面设计文件时使用Figma；本计划不把设计文件作为功能开发前置。

每个必需用例记录候选/数据/权限/效果身份、A/B分支、真实步骤、预期与实际、DOM或请求证据、结果和恢复点。浏览器超时、未等待稳定DOM、截图裁切等工具失败先辨别，不立即判产品缺陷。错误注入只在本任务context或合成实例，禁止更改工作站网络来模拟离线。

动态测试至少覆盖：两项目、两编辑者、103条分页、长回复、多角色/多关系、unknown/0/空、旧成功+新失败、跨日与里程碑、认证撤销、乱序、丢回执、重复提交、取消×完成、源半写和排除访问探针。避免盲目全组合爆炸；固定各分支必须覆盖，交互组合选择有明确风险依据，并列出未覆盖组合。

UI完整接受读取ui-design-governance的完整rubric，对功能、视觉、桌面/窄屏、console、network分别结论；截图只证明画面。自动可访问性不等于真实读屏，模拟视口不等于真实手机触控。

### 6.4 最终旅程

原J1：从首页找两个项目，理解定义/角色/状态/下一步并往返。原J2：从项目编写→页面真实发送→接收/运行→实际回复→追问→重开。原J3：两条owned任务并行，旁边原有业务继续自然推进；GET隔离、刷新冲突和任务控制分别验证。

为补齐日常管理，本计划新增J4：关系新增/接受/撤销、方向跳转、排期/依赖影响、CAS与离线恢复。J4是新增旅程，不冒称原报告已有。旅程是检验路径，不能代替下一节逐项98覆盖。

## 七 全部测试的逐项追踪

下表的“原结果”冻结自2026-10-15报告；“执行轮”是改造/补证主责，不是本次已经执行的结论。每项完整原始预期还保存在静态YAML目录中。O6.1对所有ID重跑隔离适用分支，O6.2复核正式/自然/live适用分支，未执行项不移出分母。

| ID | 原结果 | 具体场景 | 执行轮 |
| --- | --- | --- | --- |
| A-01 | BLOCKED | 从正式首页核对三十天项目及Actor 4名称、用途、已有任务绑定 | O0.1 |
| A-02 | PASS | 初次打开首页找A，再找B，不提示搜索/导航位置 | O1.2 |
| A-03 | FAIL | 用正式名称、常用名称/关键词检索，再清空 | O1.2 |
| A-04 | UNVERIFIED | 切状态、来源、分组、列表/树/甘特后打开项目 | O1.2 |
| A-05 | FAIL | 从A到B再返回列表，用浏览器后退/前进 | O1.4 |
| A-06 | FAIL | 打开项目/任务深链接、刷新，再访问不存在ID | O1.4 |
| A-07 | BLOCKED | 从项目找任务/会话入口，再找对应产物 | O1.3 |
| A-08 | BLOCKED | 无身份、合法身份、权限不足分别看同一路径 | O4.1 |
| B-01 | FAIL | A/B各自用一句话回答项目用途和主要产出 | O1.1 |
| B-02 | FAIL | 辨认业务项目/Agent/基座/连接器/工作副本等角色 | O1.1 |
| B-03 | UNVERIFIED | 比较主项目与分支/工作副本 | O3.1 |
| B-04 | FAIL | 找当前目标、阶段、任务和执行者 | O1.1 |
| B-05 | UNVERIFIED | 判断项目active但局部blocked的状态 | O1.1 |
| B-06 | PASS | 读业务进度及原始分母 | O1.1 |
| B-07 | FAIL | 找阻塞原因、恢复条件和下一步 | O1.1 |
| B-08 | PASS | 区分采集时间、来源更新时间、预估和事实 | O2.1 |
| B-09 | FAIL | 多个业务字段未知、部分字段已知 | O1.1 |
| B-10 | UNVERIFIED | 检查来源/管理/Agent建议相互矛盾的样本 | O3.2 |
| D-01 | PASS | 核对A/B合法来源到Hub采集再到页面的关键字段 | O2.3 |
| D-02 | UNVERIFIED | 观察现有自动采集的一次实际运行 | O2.4 |
| D-03 | BLOCKED | 对A单项目刷新并查看B和全局反馈 | O2.1 |
| D-04 | UNVERIFIED | 来源自然更新后再次查看/按既有机制刷新 | O2.1 |
| D-05 | UNVERIFIED | 合法运行配置不具模型执行能力时浏览与采集；边界在副本补测 | O2.1 |
| D-06 | PASS | 来源文件缺失/损坏/不可读 | O2.2 |
| D-07 | PASS | 来源过期或读取失败但有历史成功 | O2.2 |
| D-08 | PASS | 字段null、0、空列表及未排期并存 | O1.1 |
| D-09 | UNVERIFIED | 项目超出单页数量、多个筛选组合 | O2.3 |
| D-10 | BLOCKED | 两个刷新请求重复/重叠 | O2.2 |
| D-11 | UNVERIFIED | removed/云排除/未授权项目出现在登记中 | O2.3 |
| D-12 | UNVERIFIED | 来源在读取期间更新或中间写入不完整 | O2.2 |
| R-01 | PASS | 从A回答与B有没有已确认关系 | O3.1 |
| R-02 | FAIL | 阅读归属、依赖、供给/服务等方向 | O3.1 |
| R-03 | UNVERIFIED | 同一对象有多角色、多条关系 | O3.1 |
| R-04 | FAIL | 点击关联项目再返回 | O3.1 |
| R-05 | UNVERIFIED | 在列表、业务树、关系视图间比较 | O3.1 |
| R-06 | UNVERIFIED | 对比提议关系和明确接受关系 | O3.2 |
| R-07 | FAIL | 副本新增、修改、撤销一条管理关系 | O3.2 |
| R-08 | UNVERIFIED | 自依赖、循环、重复关系及失效目标 | O3.2 |
| R-09 | UNVERIFIED | 查看依赖方阻塞对项目的影响 | O3.4 |
| R-10 | UNVERIFIED | 跨项目关系旁尝试寻找分发/访问含义 | O3.2 |
| S-01 | PASS | 回答A/B本周/近期做什么和为什么 | O3.3 |
| S-02 | PASS | 区分计划、滚动预估、来源实际 | O3.3 |
| S-03 | UNVERIFIED | 打开没有日期/部分日期/里程碑的项目 | O3.3 |
| S-04 | PASS | 副本设定/调整日期，再看概览和时间线 | O3.3 |
| S-05 | UNVERIFIED | 结束早于开始、时区/跨日、无效日期 | O3.3 |
| S-06 | UNVERIFIED | 甘特缩放/筛选/窄窗查看 | O3.3 |
| S-07 | UNVERIFIED | 依赖改变但计划尚未调整 | O3.4 |
| S-08 | PASS | 两个编辑者基于不同版本保存排期 | O3.4 |
| T-01 | BLOCKED | A/B分别从项目上下文找“下指令” | O4.2 |
| T-02 | BLOCKED | 编写只读指令，核对项目/新建或继续/任务范围 | O4.2 |
| T-03 | BLOCKED | 存草稿、离开、回到同一任务 | O4.3 |
| T-04 | BLOCKED | 经页面真实发送一次有界只读任务 | O4.5 |
| T-05 | BLOCKED | 观察发送到回应的各阶段 | O4.4 |
| T-06 | BLOCKED | 打开真实回复及对应产物 | O4.5 |
| T-07 | BLOCKED | 对自己任务追问一句并回看历史 | O4.5 |
| T-08 | BLOCKED | 对自己的忙碌测试任务反馈/新建另一任务 | O4.4 |
| T-09 | BLOCKED | 双击发送或同request重试 | O4.3 |
| T-10 | BLOCKED | 刷新页面、返回列表再开任务 | O4.3 |
| T-11 | BLOCKED | 请求送达后网络断开/回执丢失 | O4.3 |
| T-12 | BLOCKED | 自己测试任务失败、超时、执行者不可用 | O4.4 |
| T-13 | BLOCKED | 取消自己测试任务，与迟到完成竞态 | O4.6 |
| T-14 | BLOCKED | 执行门关闭、无合法身份、缺授权 | O4.1 |
| T-15 | BLOCKED | A编辑指令期间切B并等待A迟到响应 | O4.6 |
| T-16 | BLOCKED | 核对“执行结束、工程通过、业务合格、用户接受” | O4.4 |
| N-01 | PASS | 观察一个本来就在推进的任务，同时浏览A/B | O6.2 |
| N-02 | UNVERIFIED | 自然来源更新与Hub读取交错 | O2.1 |
| N-03 | BLOCKED | 自己的A测试任务与B任务状态同时变化 | O4.6 |
| N-04 | PASS | A/B快速切换且请求乱序 | O1.4 |
| N-05 | BLOCKED | 同时自然刷新与手动刷新；忙碌边界在副本 | O2.2 |
| N-06 | PASS | 观察来源业务文件与本次Hub效果归属 | O2.3 |
| N-07 | PASS | 测试结束只清理自己创建的任务/临时记录 | O6.4 |
| N-08 | UNVERIFIED | 别的任务在同一仓库修改无关文件 | O0.1 |
| N-09 | UNVERIFIED | 测试期间正式代码被正常更新 | O0.1 |
| N-10 | BLOCKED | 测试自己的取消/重试，旁边业务任务继续 | O4.6 |
| N-11 | UNVERIFIED | 正常浏览/采集时观察CPU/RSS/请求耗时 | O5.4 |
| N-12 | PASS | 自然事件未发生、身份缺失或局部冲突 | O6.4 |
| V-01 | FAIL | 不滚动看首页，说出页面用途、项目入口、需要关注的事 | O1.2 |
| V-02 | FAIL | 打开项目首屏回答“是什么、现在怎样、能做什么” | O1.1 |
| V-03 | FAIL | 通读完整项目页，标记重复、长段落和无关模块 | O1.3 |
| V-04 | FAIL | 从长页找关系、排期、任务和来源 | O1.3 |
| V-05 | FAIL | 解释内部ID、英文阶段、unknown与技术状态码 | O1.1 |
| V-06 | UNVERIFIED | 比较列表/树/甘特中的同一个项目 | O1.2 |
| V-07 | FAIL | 等待、空列表、错误、禁用、成功反馈逐一查看 | O1.4 |
| V-08 | UNVERIFIED | 长项目名、长回复、多关系、很多unknown | O5.3 |
| V-09 | PASS | 1440×1050与1280×800观察首屏及滚动 | O5.3 |
| V-10 | UNVERIFIED | 390×844窄窗浏览、导航、表单和长回复 | O5.3 |
| V-11 | UNVERIFIED | 200%缩放、对比度、焦点/禁用视觉检查 | O5.3 |
| V-12 | PASS | 判断方案图/截图预览与当前页面的关系 | O5.1 |
| E-01 | BLOCKED | 只用键盘完成找项目、查看关系、进入指令和返回 | O5.2 |
| E-02 | UNVERIFIED | 展开面板/打开关闭对话框/触发错误 | O5.2 |
| E-03 | PASS | 页面等待期间和错误恢复后反复导航 | O1.4 |
| E-04 | FAIL | 副本模拟401/403/404/409/5xx和超时 | O1.4 |
| E-05 | PASS | 提交校验失败或网络失败后再次编辑 | O3.3 |
| E-06 | PASS | 浏览器离线/重连边界 | O5.4 |
| E-07 | UNVERIFIED | 打开reduced-motion观察状态变化 | O5.3 |
| E-08 | UNVERIFIED | 全程检查console异常和network关键交换 | O5.4 |
| E-09 | UNVERIFIED | 空项目/无任务/无关系/无排期/服务未启用 | O5.4 |
| E-10 | PASS | 回看测试产生的本地效果和原始证据 | G.1 |

G的10项单独验收，不纳入98产品通过率：

| ID | 交付验收 | 执行轮 |
| --- | --- | --- |
| G-01 | 盘点status、ignore、服务代码身份和实时远端 | G.1 |
| G-02 | 核对差异对应的native任务、归属与用途 | G.1 |
| G-03 | 逐文件核对已交付字节，避免重复交付旧投影 | G.1 |
| G-04 | 保留旁支独有历史、未提交工作和运行依赖 | G.1 |
| G-05 | 私人DB、消息、凭据、路径清单与敏感证据留本地 | G.1 |
| G-06 | 精确ignore生成物并验证反例 | G.2 |
| G-07 | 组成可独立验证的精确owned提交单元 | G.2 |
| G-08 | 精确候选检查、脱敏与差异复核 | G.2 |
| G-09 | 当前授权下正常交付并核对远端commit/保护 | G.3 |
| G-10 | 交付版本、剩余本地项与恢复依赖交接 | G.3 |

## 八 唯一状态、恢复和收口

动态事实只保存在STATE.yaml.pch_refactor.user_test_optimization_20261016，仍是现有STATE内的增量，原task ID、原工程progress、旧T报告和all_projects_governance不改写。静态YAML不保存另一份current status或运行队列；不建立新scheduler、hook、loop或预算账本。

执行时记录当前轮、owned paths、输入/代码/配置版本、round_results、当前98项分支结果、证据指针、失败指纹、真实可见预算、delivery和唯一next_action。可以复用现有pch_refactor.task_results的索引模式，不复制一套冲突的项目状态。缺round result表示尚未开始，写完代码、测过和最终接受分开。

轮次使用PCH-FUNCTIONAL-OPT-20261016/Ox.y的去重键；一个逻辑轮跨会话、工具失败、修复或复核继续累计，不通过重启、改名、派agent重置失败和预算。仅当执行/范围/有效证据实质改变才在既有closing checkpoint按goal-progress记轮。调用、讨论、状态查询和等待不是新轮。

没有指定token/cost总预算，不臆造预算或费用。若执行会话有原生预算，所有父子agent、工具、诊断、验证和收口共同计入；派出前留验证与收口余量。未知遥测不当零或无限。等价无进展失败两次后改变方法或定位前置，适用时按task-health处理，不能靠增配额或新会话重置同一故障。

失败指纹至少包含目标/检查、相关版本、失败类别、已尝试方法和效果是否已提交。未知外部效果先核对再重试；回退只针对owned且无并发变化的精确前像。局部依赖受阻继续下一就绪组。全部独立工作结束仍有外部缺口时，交付可审核候选和精确恢复动作，不伪造complete、不声称后台继续。

原生Goal只有执行会话收到用户明确启动请求后才操作：get_goal先核对归属；同一目标复用，无目标才create_goal；其他目标不覆盖。当前工具不可用就普通持续执行并说明，禁止假装启用。complete仅在全部约定验收和最终审核成立后；blocked、paused严格按当时原生工具规则，不能因等一次用户回复就自行pause。

G轮独立安排于功能候选形成后。用户发送附带的执行prompt才形成其中明确的Git交付授权；本规划会话不stage/commit/push。精确owned安全单元正常交付现有授权main或PR，遵守可见性、CI和保护，创建PR立即attach。STATE、DB、真实任务消息、完整私人路径清单、凭据和敏感截图留本地；公开文档需逐项脱敏，不能把本机计划事实批量提交。

最终报告提供：候选入口/启动命令、代码与实际服务版本、F01至F09修复、98项当前结果、10项G结果、I/L/正式证据区别、未验和最少用户事项、资源收尾、Git准确状态和唯一下一步。用户只在最后审核成果；没有最终明确接受，产品接受仍pending。

## 九 直接执行入口和参考

完整执行prompt见[prompts/pch_functional_refactor_goal_mode_20261016.md](../prompts/pch_functional_refactor_goal_mode_20261016.md)。执行从O0.1开始；已有本增量有效记录则从首个未完成且依赖满足的分支续接。旧R5.4业务生产与旧治理v3不自动恢复。

依据：[原测试计划](testing/pch-user-audit-20261015/TEST_PLAN.md)、[原98用例和G定义](testing/pch-user-audit-20261015/TEST_CASES.md)、[原报告](testing/pch-user-audit-20261015/TEST_REPORT.md)、[旧工程计划](pch_refactor_plan.md)、当前实际代码与CI。有关Codex子agent的职责划分、收集结果和并行写冲突控制参照[官方Subagents文档](https://learn.chatgpt.com/docs/agent-configuration/subagents)；具体原生Goal能力以执行会话可用工具和真实返回为准。
