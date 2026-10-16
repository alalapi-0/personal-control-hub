# PCH 功能优化目标模式执行 Prompt

> 公开脱敏的历史计划/执行入口；动态事实仍以本机唯一 STATE 为准。2026-10-16 所有者后续指令要求每个实际执行轮核查全部待提交内容并正常交付安全候选到 main，取代下文任何延至 G 阶段的交付安排；最终 G 验收仍保留。路径占位值需由执行工作区提供。本文件存在不自动启动任务。

这份正文只有在所有者将其作为执行请求发送给执行agent后才构成执行授权。本次计划编制未启用它。可直接复制以下整段正文，或让执行agent完整读取本文件。

我授权你在 /path/to/personal-control-hub 启用并核实原生目标模式，执行PCH用户测试后的功能优化与重构，持续推进所有已授权且可由agent完成的实现、配置、联调、测试和结果整理。先实现功能，普通开发不使用固定的Governor、Repair、Judge三角色链，不以中途人工逐轮审批、Figma稿、配色方案或先做满意业务作品作为前置。最终成果由我审核。

所属任务：PCH-REFACTOR-20261014。
优化增量：PCH-FUNCTIONAL-OPT-20261016。
计划版本：1.0.0-20261016。
唯一动态状态：STATE.yaml.pch_refactor.user_test_optimization_20261016。

先读适用AGENTS.md和上述当前状态的摘要，再按当前轮按需读取：

/path/to/personal-control-hub/docs/pch_functional_refactor_plan_20261016.md
/path/to/personal-control-hub/data/roadmap/pch_functional_refactor_20261016.yaml
/path/to/personal-control-hub/docs/testing/pch-user-audit-20261015/TEST_CASES.md
/path/to/personal-control-hub/docs/testing/pch-user-audit-20261015/TEST_REPORT.md

默认启动上下文不超过8192 bytes；先读阶段摘要和当前轮，报告按当前ID展开，不能每轮加载完整历史。完整计划和目录是本次功能范围；其中未完成项不能因未读而漏做。不要启动旧all_projects_governance v3，不恢复旧R5.4故事生产，不改全局Agent规则。保留P0、原107/111工程记录、34轮历史和原T轮98结果，新优化另记候选与分支结果。

先get_goal核对实际归属；已有本次同目标则复用，无目标才create_goal，再get_goal确认。若其他旧目标仍存在，不能覆盖或伪称完成；说明归属限制，先继续本次已授权独立工程，等待真正需要的原生目标处置。工具不可用则普通持续执行并如实说明，不建立替代调度器、Hook或循环，不自行填写token预算。全部约定验收及我的最终接受成立后才complete；原生blocked/paused严格按当时工具语义办理。我的直接stop/pause/cancel立即停止本任务效果并精确中断本任务后代，不操作其他任务。

首次写入前运行：
python3 scripts/auto_advance_runner.py --mode check --task-id PCH-REFACTOR-20261014
runner只检查、不授新权。读Git已有脏树、相关native任务与具体文件写责；Root拥有STATE、共享写入、Git和外部效果。先选一仓和当前owned文件，不stash/reset、不改remote、不清理未归属文件。当前checkout HEAD、已交付commit、正式8766服务与测试候选分别核对；不要用HEAD替代运行版本。

我授权本计划范围内PCH代码、schema可逆扩展、页面、测试、合成配置、文档和唯一状态的本地可逆改动；允许完整隔离测试实例、任务私有浏览器/显示、合成数据根和测试用户级调度单元。业务子项目仅在既有登记与读取资格内只读；云项目排除，removed_local禁止探测，非当前根和新候选不默认纳入。不得写业务源、重做业务内容、占用GPU/用户前台、改主机网络、访问凭据、复制会话或启动陌生任务。正式服务/账本迁移仍需核对写责与迁移证据，本Prompt默认授权独立候选实例，不暗授生产切换。

从O0.1开始；已有有效执行记录时从首个未完成且依赖满足分支续接，复用不变证据。8阶段32轮中O0至O6共29轮功能与验收，G独立3轮。先组装完整fixture，随后推进项目理解导航、自动采集、关系排期、普通文本任务、并行、产物和交互完整性，再全量回归；就绪的独立组可以先行，不把整个阶段串在正式owner登录或口述别名确认后。静态目录中的dependencies要求相关可自治实现/确定性检查，result_inputs只要求准确结果记录；正式调度/身份/切换外部未验不阻止O6.2、O6.4和G准备，但仍阻止对应产品PASS。

复用SourceResolver、RefreshLedger、ProjectService、现有SQLite、HTTP、systemd、TaskStore、TaskService、TaskWorker、Codex adapter及原生任务控制，不建第二数据库、状态机、调度器或新前端栈。发现、采集、查询、统计、关系/排期和基础页面运行时不得依赖LLM或agent。

O0.2必须把现有test_hub_task_runtime、drafts_http、busy_controls、service_integration等组件组成真实程序+真实静态资源的完整隔离浏览器实例：Project/Design/Preview/Task/Session/必要Host适配、独立SQLite/管理/store/root/port、合成owner、精确storage/grant和可控worker。不要再次把TaskService漏装后将T-01至T-16全部卡在B03。FakeAdapter、固定时钟、合成身份清楚标为隔离替代；不能用它们证明正式owner或真实provider通过。

当前普通任务路径仍绑annotation/grant/图片，execution_enabled有硬编码，脚本没有自动装配worker。必须实际实现不依赖图片的普通文本TaskSpec、合法服务端准入、能力投影、页面草稿/发送/回复/追问/取消/恢复。不能只把owner配置好或把按钮改成可点击就称通用分发完成。图片上下文可选，旧story/CSS能力保留兼容；客户端不能自行伪造授权或借test_gate开启正式执行。涉及新安全边界的部分采用项目当前要求的最小必要独立审查，不把所有普通开发升级成三角色工作流。

我明确授权为本任务创建两个全新owned原生测试会话与合成工作区，在已有合法provider/认证能力下，经产品页面各发送有界只读检查、接收真实回复、追问、重开；对本任务owned可控忙碌测试做精确取消并验证另一任务继续。这些测试只读本任务合成文件并返回摘要，不向既有未知业务会话发消息，不让原有业务停工，不生成媒体或修改子项目。浏览器请求、request_id、task/project/thread/turn、worker事件和DOM必须对应；后台SDK调用不代替页面链。

基础采集按计划新增目标实现：服务运行时每60秒有界增量检查，可见页面每5秒核对head，稳定可读源正常端到端目标90秒内；沿用现有用户级systemd与同步器，GET不采集，保留每日完整批次。至少三次真实短周期调度触发观测；模拟时钟和手动start不冒称真实自动调度。实测无法满足先修增量/互斥和负载，调整须保留原目标与依据。被排除对象不因检查更新而被探测。

每轮按计划具体步骤实现→目标检查→浏览器联调→精确差异复核→更新同一状态→继续下一就绪轮；无需重复问已授权普通实现。用explorer做有界只读发现、worker做互不重叠文件，告知不是唯一工作者；Root整合复现关键结果并收齐或精确中断全部后代。子agent不自行派生。共享文件、STATE、跨仓效果和Git串行；不干扰独立顶层任务。缺角色配置只阻塞真正要求该角色的边界，不能伪造独立审核。

读取ui-design-governance及接受rubric，复用现有样式/组件，先解决主路径可达、可读、错误和恢复。浏览器覆盖390×844、1280×800、1440×1050、真实200%缩放、键盘/焦点、量化对比度和动态reduced-motion。功能、视觉、桌面/窄屏、console、network分别采证。自动ARIA不当真实读屏，模拟视口不当真实手机，截图不当保存或执行效果。GUI使用任务私有显示并核对进程/窗口归属，错误注入只作用合成实例或本任务browser context。

原98产品ID一个不少，保留各A/B/隔离/正式/live分支及原始预期；G10另计。每个ID总结果只有一条，分支未完成不能偷写PASS；无工具/权限为BLOCKED或UNVERIFIED，不是NOT_APPLICABLE。F01至F09逐项关闭，原PASS也复核当前候选适用性。代码/确定性隔离、真实provider通道、正式业务对象、实际服务运行、Git交付和所有者接受分开，不把原107/111、23/98或32轮写成产品完成率。

缺正式owner时不要索取密码/令牌、创建替代正式身份或借用户会话；先完成合成身份全链、普通任务实现和其余测试。口述三十天/Actor 4不明时用稳定ID及合成A/B实现机制，只阻塞对应正式对象发送。provider无登录/额度只阻塞live，天然业务事件未出现只保留自然分支未验；不无限等待、不让这些条件卡住其他工程。真正剩余身份/映射/设备/审批事项合并到最终审核包，只有实际需要授权且无法继续依赖组时才提出最少明确事项。

每轮保留输入/代码/配置版本、candidate/evidence、implementation、verification、human_acceptance、blocker和delivery，失败保留原分母和效果状态。两次等价无进展后诊断或改变方法；未知送达先查原request，不重复触发。所有agent/工具/诊断/验证共用实际预算，派出前留验证与收口余量；未知遥测不算零或无限，不伪造Token/cost。只在已有closing checkpoint使用goal-progress核算去重轮次，task-health仅在真实触发时用，不新增报告体系或预算账本。

功能候选和完整回归形成后进入独立G轮，不逐功能轮整理旧脏树。我授权G轮将本计划精确owned且已验证、已脱敏的安全代码/测试/必要文档正常交付现有授权GitHub main；保护要求PR则正常建PR、attach并遵守检查/审批。先实时核对origin/可见性/保护及其他改动；只精确stage本任务单元，禁止git add -A、force-push、改remote、破坏性历史重写、reset/stash他人工作或绕保护。STATE、DB、真实消息、凭据、完整私人路径清单、敏感截图与原始本地证据不上传；本机计划需脱敏后才进入公开候选。核对远端包含准确commit，不把本地commit或分支push当main同步，生产运行版本另核。

最终交给我可打开的当前候选和一份结果包：入口/启动命令、候选身份、变化、F01至F09关闭证据、全部98结果、G10结果、真实通道/正式/隔离区别、未验/最少需我确认事项、Git准确状态、资源收尾和唯一下一步。先自主完成所有不需要我参与的工作；我最后审核成品。没有我的最终明确接受不伪造产品PASS/Goal complete；存在外部边界时如实交付with_gaps并保留恢复点，不声称后台继续。
