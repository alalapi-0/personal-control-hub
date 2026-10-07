# Linux 工作台验收矩阵

读者：Root Run Agent、适用的独立审查者与所有者。用途：定义产品验收操作和证据，输入/候选/验收合同改变时更新。路线见 [ROUNDS.md](ROUNDS.md)，事实基线见 [PLAN.md](PLAN.md)。**唯一当前结果在 [STATE.yaml](../../STATE.yaml) 的 `linux_visual_workbench.acceptance`；本表状态列只表示阶段0调查时的基线**，后续不另建可编辑完成表。

## 状态与证据合同

- PASS：当前版本、实际适用环境下预期结果和必需审查都有证据。FAIL：实际运行且未符合预期。NOT_RUN：未执行。BLOCKED：具体必需权限/设备/接口/依赖缺失，保存拒绝或缺失证据。DEFERRED：明确未启用的阶段6，不算M1/M2分母。
- UI技能的UNVERIFIED保持原意，在当前矩阵中映射未执行或具体受阻；没有工具不是NOT_APPLICABLE。确有产品依据的指标“不支持”可以作为字段不可用分支PASS，不能伪造实际值或去掉该分支。
- 证据最少包含检查ID、输入/环境、candidate的commit+相关dirty指纹/配置版本、任务/批注/预览/会话关联、操作、预期/实际、真实exit、分类real/mock/dry-run/imported、时间、引用和可复现入口；凭据、cookie和任务无关正文不进入记录。
- 复用已有效证据，相关版本变化才重验；保存至本任务必要共享证据索引及授权存储中的工件，STATE引用。此矩阵不会证明文档中尚未实现的API、fixture或service存在。拟新增测试须在该Round先定位实际模块并登记路径。
- 本轮没有设置延迟/成功率/并发数字。日志/缓存初始容量、低盘阈值和采样TTL由实施轮按存储与测量确定、记录并验证。不能以CPU空闲直接算任务数。视觉阈值固定环境后再定义，不能自动更新baseline使测试通过。

## 阶段0交接验收（P00，产品完成另算）

| 需求 / Round | 环境与前置 | 操作 | 预期 / 证据 | 阶段0基线 |
| --- | --- | --- | --- | --- |
| P00-1 真实仓库/规则/有界事实，0.1 | 当前Linux Hub，必要只读授权 | 核对cwd/HEAD/dirty、实际规则、模块/库/服务/基线 | PLAN现状表/B01–B08有来源、级别与真实失败；不扫描全部项目/会话，不覆盖原有工作 | PASS，调查记录 |
| P00-2 接口/边界/选型，0.2 | 本机Codex及公开官方资料，只有两个只读候选 | help/login status/schema；核对candidate UI类型和active归属 | 主路线/回退、数据/权限/存储、候选与未测试能力都明确；没有推理/外部修改 | PASS，PLAN D1–D5与官方记录 |
| P00-3 可执行交接，0.3 | 以上已完成、文档/STATE属于本任务 | 校验路径/23任务卡/矩阵/Prompt正文、YAML唯一键与旧子树保留 | 下一Round真实为1.1；Prompt允许实际实施至M2、门禁清晰、阶段6未启用；STATE规划完成但产品未开始，当前会话停止 | 验证结果看STATE的planning_acceptance |

阶段0首次runner基线仍是FAIL。当前用户明确授权规划交付，不把文档检查替换成产品gate PASS；1.1修复入口检查，未修前不宣称Run门禁已可用。阶段0验证不需要启动新原生Goal/自动任务。

## 必需网页验收（A01–A13）

| ID / 需求 / Round | 测试环境与前置 | 操作与样本 | 预期结果 | 必须保存的证据 / 可定位入口 | 阶段0基线 |
| --- | --- | --- | --- | --- | --- |
| **A01 原功能与旧数据无回归**；1.1、每个相关改动、5.4 | Hub临时fixture + 实際Linux只读原服务；旧库/他人dirty指纹在写前保存 | 查询项目/分页/未知/失败/removed；读取设计候选/决定/过期/工件；隔离库测刷新/反馈/选择/导出；比对旧数据与未改文件 | 原合同/边界保持，既有数据不覆盖；baseline失败得到对应修复或保留未通过；控制面启动条目有界、负例仍拒绝 | PLAN B01–B04/B06/B07；现有test_hub_*、两个mjs；相关preimage/hash/新contract测试；service GET与差异 | FAIL：174项中1项硬编码26；gate超限。读页面局部已可用，未证明完整无回归 |
| **A02 真实Linux与Mac同后台/记录**；1.2、2.4、5.4 | Linux与真实Mac；已有SSH/私网，单所有者认证，登记入口 | Mac提交一个有版本的共享草稿/任务，在Linux读取；Linux追加结果，Mac重连读同ID/revision/后台标识 | 同一TaskStore/设计store/记录，Mac不运行第二Hub后台；Host/Origin/session校验有效；本地不同端口不通过通配绕过 | 两设备浏览器/入口映射、服务身份/ledger head、同request/revision与操作时间；仅Linux模拟不替代 | BLOCKED：本会话没有可操作Mac；已有SSH可用为用户确认，网页未验 |
| **A03 浏览器/访问断连不终止已接收任务**；2.2、2.4、5.3 | 独立Linuxworker与持久任务，短ownedfixture及一次授权真实task | commit后关browser/断SSH隧道，Linux查ownedworker/task；重连接同请求与结果 | received任务按托管规则运行或如实failure，客户端生命周期不杀worker；重连只读取/对账，不重复dispatch | 原request、worker/thread/turn、前后event/副作用计数、实际退出/状态；卡2.2拟新增lifecycle tests，5.3真实断连证据 | NOT_RUN，只有网页service active事实 |
| **A04 主机指标真实/过期/不可用**；1.3、4.3 | 实际CPU/RAM/GPU/service/授权mount；fixture可模拟failure | 与只读工具同窗采样，断collector/unsupported sensor、正常0与stale分支；磁盘来源变化 | source/time/unit/support/TTL可读；失联不置0、旧值标stale、传感器不可用明确；只监控授权路径 | collector命令/exit/采样time、DTO、页面状态、设备/挂载来源；PLAN B08仅一次环境样本 | NOT_RUN：真实GPU/free/df可读，但Hub显示/过期链路未实现 |
| **A05 一个真实授权项目闭环与人工验收**；2.1–2.4（M1） | 真实网页/精确write grant、无重叠writer、实际Codex权限/配额、真实Mac与用户 | 打开→圈选或整版意见→要求/不改范围→Hub提交→正确cwd Codex小改→项目检查→新preview/diff→用户接受或继续 | 真文件差异与真实页面对应；测试/执行/人工状态分开；明确版本的owner acceptance；无mock冒充，未发布默认保持 | annotation/task/grant/root/commit+dirty/thread/turn、真实检查/preview、实际owner决定及source；卡2.3先按子项目AGENTS定位检查 | BLOCKED：候选无本任务写授权、Mac/人工未验；无真实推理 |
| **A06 当前选区/截图/版本/实际改动对应**；2.1、2.3、3.2 | 当前view捕获，版本/hash与坐标定义可验证 | 滚动/缩放/DPR圈选；capture denied；源代码/页面更新后尝试用旧意见；同路由不同viewport/候选 | 图是用户当时看到的状态；框与其坐标一致；失败明确fallback；版本漂移阻止静默应用；元素hint不伪造源码位置 | capture来源/许可、CSS/device坐标/scroll/zoom/DPR、image hash、版本、拒绝日志与代码diff；2.1 capture fixture + 实际授权图 | NOT_RUN；现有candidate版本机制仅可复用 |
| **A07 两真实项目隔离与越权拒绝**；2.2、3.3、5.4 | 两个分别授权真实项目/明确thread和专业预览；负例在isolatedfixture | 顺序运行A/B小任务；同名文件/相似项目名；request伪cwd、path traversal、symlink、错thread/输出/无grant | root/session/annotation/output权限不串；非法/未授权拒绝；removed_local/cloud拒绝在探根前；内部锁不声称管住所有外部writers | 两project真实diff/版本/预览/thread及合法grant；所有负例状态与零side-effect证明，现有source安全tests+新route tests | NOT_RUN，只有旧来源边界相关fixture局部证明 |
| **A08-new 新建任务会话**；2.2、5.4 | Hub自有Adapter、隔离fixture及有效权限 | 显式new→start thread→turn，小输入/图；检查cwd、项目指令marker/事件/usage | 新ID、owner、root/权限/指令加载真实；errors不报success，图片可用或明确限制 | exactthread/turn/协议版本/input类型/事件/检查与usage覆盖；help/schema只是前置 | NOT_RUN |
| **A08-idle 继续Linux既有空闲会话**；5.1、5.4 | 支持接口能访问的真实旧CLI/App/IDE thread，明确授权/idle | 少量list/read、确认root/source/provider，明确bind并resume一次必要反馈 | 原ID/历史延续；new/fork不算resume；错误cwd/不可见来源拒绝；受限来源如实标注 | resume前后同ID/必要history、实际call结果/source/version/auth归属；不读私有DB/全部会话 | BLOCKED：未在Hub执行supported livecall；本机schema与当前Agent read能力不替代 |
| **A08-busy 忙碌会话处理**；5.2、5.4 | 已授权Hub自有active turn或明确外部busy来源 | ownedsteer含expectedTurnId；外部busy不支持控制时queued；错turn、duplicate、等待approval | verifiedsteer或safequeue机制分别标明；同root无第二writer；反馈不丢；approval不扩授权 | expected/actual IDs、协议事件、queue顺序/副作用、真实控制范围；未有supported跨实例机制不能steer另一App | NOT_RUN |
| **A09 幂等、双端、恢复、异常和取消**；2.2、2.4、5.2–5.3 | owned fixture server/worker/库，真实Macrace在允许范围 | 同ID同payload/异payload，两client请求，commit/dispatch前后崩溃，进程异常、cancel race、网络/approvaltimeout | 一次effects、冲突明确、持久记录可对账、unknown/lost不成功；cancel不杀otherworker，不丢/重放任务；状态恢复不冒充计算续算 | event/receipt/diff计数、crash point、exactownedprocess退出/guardprocess仍存活、恢复命令、两端真实记录 | NOT_RUN |
| **A10 执行回复、测试、页面和设计满意度分离**；2.3、3.2、5.4 | exactcandidate、项目实际检查和fixedvisual环境 | 注入fixture “Codex完成但build失败”；页面资源/JS错误；布局有差异；用户needs_changes | task failed/checks_failed与turn complete可并存；真实检查exit与page观察决定技术验收，golden差异单独评审，用户满意不由Agent决定 | actual命令/exit/console/network/截图与baselinehash、human decision、每lane结果；3.2先定位/建立必要visual入口 | NOT_RUN |
| **A11 开发预览/业务运行隔离与精确回退**；1.3、2.3、5.3 | 独立preview/ownedtestworker与业务guard，不触真实长任务 | 在授权dev UI修改/重启preview，另一guard任务继续；预置他人dirty，回退本taskdiff | 不kill生产/其他会话、不共享可写业务数据；回退仅ownedpreimage，其他文件/未提交修改保持 | 进程/服务owner与端口/目录关系、guardliveness/结果、preimage与其他dirtyhash；没有真实长任务许可则用guard验证方法，保留生产未测 | NOT_RUN |
| **A12 认证、授权、无秘密/任意执行、容量可控**；1.2、2.2、3.1、4.2、5.3 | before executeenabled 的实际servergrant/auth边界、isolatedmaliciouspreview与storagefixture | 非owner/跨站/未授权root、恶意URL/shellpayload/消息origin/source/nonce、报告HTML、日志注入、quota/lowdisk/missingmount | 阻止越权/任意shell/SSRF/path写；credential不入frontend/图/日志/Git正文；无公网无保护执行；preview无执行权限；容量/retention只操作owned可再生输出，外置缺失不落内盘 | 负例返回/零effects、审查candidate/hash/独立决定、脱敏scan方法、实际监听、日志cap/guardtests；PLAN B06仅现有Host/Origin部分PASS | NOT_RUN，全执行边界尚未建设 |
| **A13 主流程可用性和UI验收lanes**；2.1、2.4、3.2、5.4 | 现有Hub视觉/组件与实际Linux/Mac浏览器；窄窗/键盘/reducedmotion | 主流程 + loading/empty/error/disabled/stale/waiting/approval/结果；键盘选区/焦点/label/缩放/长文本/窄窗 | 动作可找到、状态真实、标注不触业务、可访问焦点/阅读/反馈、窄窗不丢内容；function/visual/device/console/network/data-isolation独立有据 | UI技能完整rubric与各lane结果、fixedcandidate/环境/真实网络/实际view；本轮仅项目/设计页只读局部观察 | NOT_RUN，完整lanes未验 |

A08总体只有new/idle/busy三项分别达到要求才PASS；busy允许已验证安全排队而不强求无法控制的steer，但必须保留区别与真实限制。某种既有来源不可访问不允许伪造“全部会话已关联”。核心既有Linux会话继续受阻时M2部分完成。

## 门禁与里程碑归并

1. **执行开关门禁**：1.1技术检查/严格负例 + 1.2所有者认证/来源 + 2.2项目grant/cwd/命令/幂等/外部冲突/托管/审计通过，适用独立治理完成，才可在许可项目开放。没有真实项目授权可继续fixture，不能对真实项目dispatch。
2. **M1**：A01相关原功能回归、A02/A03/A05/A06、A08-new及A09/A10/A11/A12的单项目必需子项、A13首条路径通过；实际用户接受具体结果。A07/高级资源/全部旧会话是M2增量，不能说M1就证明它们。
3. **M2**：A01–A13全部必需结果与A08三个子项，当前candidate与真实Linux/Mac/两项目证据、人工验收及适用Git交付齐全。部分旧能力的基线PASS只能作回归证据，不能替新能力计完成。矩阵异质，不预设按文件/卡数量的产品完成百分比。
4. **阶段6**：DEFERRED；另行启动后按照6.1–6.3保存channel身份/重复事件、真实机器人、实际手机入口、有限审批/审计的独立结果。没有通知授权不发消息；不影响当前网页验收分母。

## 当前阻塞与一项人工动作原则

- 当前规划可在不操作Mac、不写子项目、不调用真实推理的条件下完成；没有需要用户现在提供凭据的阻塞。
- Run的首个1.1是已有检查实现问题，应自主修复并完成适用审查，不询问是否删旧STATE或提高限额。
- 准备好接入提案且真正需要外部效果时，只请求一次指定项目/版本/文件边界/允许检查的授权；本轮没有把候选默认授权。确认后不逐文件重问。
- 实机Mac无法自动操作时，每次只给一个动作及预期回传，例如“在Mac打开登记Hub入口，保存指定测试草稿，回传页面记录ID”。不能要求重新说明产品目标，不能把用户沉默计PASS。
- 最终UI接受需要真实用户选择具体版本；测试不能代替。若所有独立授权工作已完成而仍受阻，STATE保存当前证据和唯一恢复动作，原生Goal状态按届时真实工具规则处理，不能把unfinished标complete。
