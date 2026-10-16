# PCH 本地项目管理重构计划

> 公开脱敏的历史计划/执行入口；动态事实仍以本机唯一 STATE 为准。2026-10-16 所有者后续指令要求每个实际执行轮核查全部待提交内容并正常交付安全候选到 main，取代下文任何延至 G 阶段的交付安排；最终 G 验收仍保留。路径占位值需由执行工作区提供。本文件存在不自动启动任务。

计划版本：`1.0.0-20261014`。任务：`PCH-REFACTOR-20261014`。本轮为 P0，**仅规划，正式执行未启动**。

读者：所有者与收到执行 Prompt 的新执行 agent。用途：从本机已存在的系统增量实施，明确分母、边界及证据。更新触发：范围、来源契约、相关代码版本或验收条件发生实质变化。当前执行事实唯一保存在 `STATE.yaml.pch_refactor`；本文和任务目录不维护另一份当前进度。

相关材料：[任务定义与冻结基线](../data/roadmap/pch_refactor_v1.yaml)、[完整执行 Prompt](../prompts/pch_refactor_goal_mode.md)。附件《PCH_重构规划_原Prompt_20261014.md》是本轮需求参考。当前用户请求是制定计划和执行 Prompt；附件内的推送、启动 Goal、修改生效协议等动作没有在本轮直接执行。

## 1. 目标、边界与第一交付

PCH 先成为独立运行的本地项目管理系统，再提供 Agent 入口。发现、采集、校验、同步、查询、统计、关系编辑、进度展示和预览索引，运行时均不调用大模型。保留系统监控等既有功能，不扩展外网访问、跨设备同步、Facebook、飞书、其他外部渠道或业务项目重写。

第一里程碑 R1.3：至少三个不同类型/状态的真实项目，经程序同步，在本地页面看到当前任务、可信进度或明确缺口、来源及数据时间；PCH 的 Agent/模型适配不可用时仍能操作。之后逐项补接 39 个当前本机登记根，补每日独立调度，再交付任务树、关系、甘特、预览；Agent 集成最后独立验收。

基础验收不是“登记了全部项目”。每项必须对账身份、读资格、来源、核心实际值、字段缺口、新鲜度和证据。缺声明、全部业务字段未知或关键事实缺失，应保留 partial/error/blocked，而不是计为完整接入。未排期和无 UI 是正常状态，不制造日期、页面或百分比。

本轮 owned scope 为新增三份材料和 `STATE.yaml` 中 PREPARED 事实索引/候选路径；lane 为 DIRECT。没有改产品代码、AGENTS 生效规则、数据库、服务、全局配置或外仓，也没有启用 Goal 或执行 Git 提交/推送。正式激活入口、改运行权限、Agent 路由或生产账本迁移，按实际影响使用现有 GOVERNED；普通可逆代码改动使用 DIRECT，复杂验收使用 REVIEWED。不把所有轮次变成治理轮。

## 2. 2026-10-14 本机审计

### 环境与运行事实

| 对象 | 观察与证据 | 结论及限制 |
| --- | --- | --- |
| checkout / Git | cwd `/path/to/personal-control-hub`；分支 `agent/governance-closure-20260812` 跟踪同名 origin 分支；HEAD `8fa0a4e`。初读有 39 个已跟踪修改、550 个未跟踪文件 | 这些都是本轮前已有内容；不 stage/reset/stash。文件很多不代表当前有一个可辨认的冲突 writer |
| main 绑定 | origin 为 `alalapi-0/personal-control-hub`；origin/HEAD 指向 main；本地 main `e967d46`，缓存 origin/main `90495ac` | 缓存 ref 不是此刻远端值。可见性、保护和远端最新提交本轮未联网核验；没有 Git 交付 |
| 入口 | `project.yaml` 指明 STATE 为 canonical；旧任务由 STATE.all_projects_governance 管理，v3 仍 prepared | 不把 v3 数据治理/存储迁移计划当作本次产品计划，不重启旧目标 |
| 服务 | 用户级 personal-control-hub.service active/running，PID 2808，start_ticks 1422，自 10/08 启动；当前仓 scripts/hub_server.py、loopback 8766 | 只读取；不重启。进程未发布加载代码 hash，不能证明运行模块与当前脏工作树精确相同 |
| 账本 | mode=ro 查询 connection_refresh.sqlite3；schema 2.0；head 454，hash `578e9476bfb8db9476277afeb83277e0795d07b0a1e7ebde8c0180517272fc11`；90 requests、454 events、274 results | 与实际 projects API head 一致。最新事件 10/14 03:04Z 只证明某次读取，不证明每日调度 |
| 领域指标 | metric_projects 26、metric_current 1456、metric_changes 1964；最新 observation 为 10/03 | 指标账本不是当前完整项目状态的证明，不能与任务分母相加 |
| API | GET /api/session 仅匿名内存 cookie；随后 identity/projects/三样本 HTTP 200；limit=2、offset=0/2 均返回两项，total=43 | API 与 SQLite 同源、分页实测正常；不输出 cookie/CSRF，没有 owner 登录或 POST。普通 GET 不采集 |
| identity 缺陷 | /api/identity.ledger_head=null，而 /api/projects.head=454 | local_service.py:349 传空 design_snapshot，project_service.py:362 拒绝，identity 吞错。列入 R1.2；没有修复 |
| 调度 | scheduled_tasks.yaml 全为 disabled 的 prepare/check 占位；用户 timer 列表无 PCH 项目同步 timer | 每日程序同步尚未验证/启用，不把 prompt 生成任务当同步任务 |
| 平台 | Codex CLI 0.159.3，app-server/help 支持 protocol tooling；systemd 259.5；timedatectl 返回 Asia/Tokyo | 用户沟通日期按客户端 Asia/Shanghai；运行调度显示真实主机时区，不能悄悄改主机设置 |
| Native Goal | 当前 create_goal/get_goal/update_goal 工具可用；get_goal 返回 goal=null | 支持接口与实际目标已激活是两回事；本轮没有 create_goal |

### 当前项目集合

登记表实际有 **43 项**。当前 `/path/to/projects` 内的登记根 **39 项**，其中外部 38、Hub 自身 1；39 根及登记状态文件存在，38 个来源声明存在。唯一缺 `hub.connection.yaml` 的当前根是 `desktop-tool`。原始 ID/root 字符串无重复；未对旧越界根做 realpath 探测。

`storage_governance`、`manga-localizer` 是另外两个旧非当前根指针，分别仍用 `~/PycharmProjects`、`/Users`；没有探测这些根，不能声称离线。`game-removed-local`、`manga-removed-local` 两项禁止探测，严格保持排除。历史文档称 17 个云项目排除，但当前 registry 没有它们的 ID 清单；不凭空补身份。

一级目录发现四个未登记候选：`asset-operations-platform`、`creator_workbench`、`personal-control-hub-lwb-delivery`、`stellar-mod-console`。只记录目录名，没有进入内容。交付 checkout 不自动等于独立项目；R2.0 核对身份和读资格，再版本化调整覆盖集合。未确认前不纳入同步分母。

所以本次初始同步分母是 **39 个已确认当前登记根**。registry 的 local_count=41 是处置统计；旧 STATE 的 local=24、README 的 24、workstation inventory 的 32 和领域 source 配置的 23，均不是当前物理根分母。保留这些历史数字及适用时间，不静默改历史。

39 个实际接入单元和 ID 的穷尽集合在机器任务目录 R2.1—R2.7。每项引用 registry，不复制另一份身份/物理路径清单。包括非 Git 工具、共享工具根和 Hub 自身；不因没有 Git 而排除。

### 当前状态质量

只读 current 输出：43 登记，41 非 removed，最新历史 resolved 32，其中 31 stale、1 fresh（lyric-motion）；7 项从未刷新：dance-motion-library、emotion-shot-atlas、film-editing-library、hypothesis-lab、local-toolbox、minecraft-director-workbench、reaper-music-workbench。另有旧 manga invalid、旧 storage missing_declaration、removed 两项。历史 resolved 不能提升为当前业务完整。

至少八项 metric_sources 仍含旧 Mac/Volumes 路径：manga-localizer、novel-continuation-agent、ai-anime-short-factory、zarathustra-adaptation、storage_governance、universal-player、desktop-jav-tools、desktop-tool。没有读取这些数据根；各自接入轮确认正式来源，禁止全局字符串替换。

| 样本与当前源事实 | 导出/读取→账本→API→页面追踪 | 判断 |
| --- | --- | --- |
| REAPER project.json：P1/RP-P1-R02，blocked，0/6 runtime gates，completed/accepted=false | 2.0 JSON 精确 selector 声明存在；无单独通用 exporter；账本无记录；真实 API unknown/null；页面 connection_view 按 unknown 展示规则处理 | 来源已有而尚未采集。0/6 是业务运行验收，不是计划任务总数；实际浏览器呈现未验 |
| Layered Character project.json：当前权威必须以读取时版本为准；历史 P0 4/4、accepted=false | JSON→SourceResolver 规范路径存在；10/11 历史成功 result/hash 可对照；当前 API authority_drift=true、stale，当前字段 null；UI 规则抑制 stale 当前值、可看历史 | 是权限信息变更后的过期快照，不能恢复成业务完成；本轮未重新同步 |
| desktop-downloads-scripts STATE.yaml：模板本身业务进度未知 | YAML 读取；10/03 历史成功，progress 本来 null；真实 API authority drift 后 unknown/null；页面同样仅显历史 | 读到模板不是完整业务接入，不能算完成 |
| lyric-motion project.json：active/EXECUTION/R01，当前源 2/54，仍列阻塞 | JSON 声明→当前账本唯一 fresh；规范接口/渲染模块已有链路 | active 与局部阻塞可并存；本轮没有重新验 Resolve 或浏览器交互，不用文字反推 runtime |
| desktop-jav-tools README.md：声明 mapping 为空 | Markdown 精确读取入口存在；规范业务字段 unknown，旧 resolved 只是读源成功；UI 保留 unknown | 非 Git 也可见；不能运行 LLM 推断 README 完成率。适合作为第三类缺口样本 |
| film-editing-library project.json：blocked、当前修订 4/5，历史 M1-v2 4/10 保留 | 源已实际只读核对，但账本从未刷新 | 可见新源不等于 Hub 已采集，原分母与版本不能被覆盖 |

前五行给出 JSON/YAML/Markdown 三种来源和三项实际 HTTP 样本；页面这一段是**静态映射追踪**，不是浏览器运行验收。exporter 断点也不是漏跑某个现有通用 exporter：现有主要路径是 Hub 直接读取已声明源，通用 exporter/importer 未证实。R1.3 才完成真实浏览器与关闭 PCH Agent 接口的第一里程碑。

### 复用与改造决定

| 能力 | 当前代码证据 | 决定 |
| --- | --- | --- |
| 来源读取与边界 | connection_sources.py:74/94/107/180：registry/hash、声明、removed 优先拒绝、字段定位/hash | 保留；扩显式有限来源角色与确定性适配，禁止任意 exporter/eval/shell |
| 规范验证 | connection_records.py:15/23/250/299：source 2.0、21 字段、provenance/unknown、计数口径 | 保留兼容；新增字段明确版本。当前 single current_state，不偷读未声明第二权威文件 |
| 同步账本 | connection_refresh.py:173/236/380/441/492：SQLite、request、结果/event hash、CAS、finish | 保留同一账本、事务/幂等/失败历史；不重建 DB，不造第二锁服务 |
| DTO/API | project_service.py:60/84/94/403/423：DTO 1.0、读时 TTL、current 与 last_success | 保留；修 identity，补字段质量、事实时间与完整分页。source_age 当前未用于业务 freshness，不能把重读旧文件说成业务刚更新 |
| 页面 | connection_view.mjs:35/61/75、hub.js:115/169：字段来源、新旧成功区分 | 增量扩展。当前页面请求一次列表，默认 max=100，所以当前43没有证据表明丢页；超过100需完整加载/服务筛选，列为未来容量缺口 |
| 关系 | connection_relations.py:28/62/89：仅 proposed，限定现有三类建议 | 保留建议语义；另加用户管理关系，不能拿 proposal 自动授权/执行 |
| 任务 | task_service.py:95/105/118/120：exact grant、test gate、writer 身份限制；默认 disabled | 保留执行安全边界；业务阶段/轮次/任务树另作只读契约，不混执行队列 |
| 预览 | readonly_preview.py:21/41：固定 study-plan 场景；preview_service.py:47：版本/哈希绑定 | 复用绑定，逐渐一般化清单/状态/按需采集；当前试验不算全项目预览能力 |
| Codex | codex_adapter.py:55/369/405：受限 helper、owned thread resume、能力 echo | 后置真实能力验收；不声称可接管任意 CLI/IDE；基础同步不依赖它 |

本轮没有足够依据淘汰具体产品模块。淘汰先定位实际调用、所有权与替代验证，确认 superseded 后仅删本任务拥有的死路径；不默认换语言、Web 栈或安装 Backstage。

### 检查与缺口

实际运行并通过：首次 `python3 scripts/auto_advance_runner.py --mode check --task-id ALL-PROJECTS-CODEX-GOVERNANCE-V1`，exit 0，0 hard/soft；只检查，不授新权限。隔离基线 `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests python3 -m unittest test_hub_sources test_hub_refresh test_hub_project_service`，58 passed；`node --test tests/test_hub_connection_view.mjs`，13 passed。Python 使用 TemporaryDirectory；Node 纯内存，没有启动业务项目。另有匿名只读 HTTP 和 mode=ro SQLite 核对。

保留诊断边界：首次 `systemd --version` 因 PATH 无该可执行名失败；通过 `/usr/lib/systemd/systemd --version` 和 `systemctl --version` 确认259.5。首次 API 摘要脚本漏解 data envelope，打印 null；修正本轮读脚本后核实 total43、分页2/2和项目 stale/unknown，不能把初次摘要误读当产品 defect。identity null 为修正脚本后仍复现的独立真实 defect。

未运行：生产刷新、全仓/全部子项目测试、模型/GPU业务、真实浏览器呈现、定时触发、用户接受、Git main 交付、整机重启/注销。测试通过没有填补这些验收层。

## 3. 规则与任务交接

| 当前冲突/依赖 | 正式执行的最小处理 | 继续保护 |
| --- | --- | --- |
| AGENTS 默认 all_projects_governance、v3 需启动、不做 UI | 用户发送本执行 Prompt 后，R1.1 给本 task 增加明确入口与允许 UI 的局部范围；旧 v3 不启动，旧治理任务不恢复 | 子项目只读、云/removed 排除、媒体/凭据/其他任务 |
| README/project.yaml 是 push current tracking branch、禁 automatic main merge；附件目标 main | 当执行 Prompt 由用户明确发送且含 main 授权后，统一本 task 为检查/保护下正常 main 交付；需要 PR 才走 PR，不能把临时分支当main | 禁强推/改origin/绕保护/任意stage；其他task Git规则不改 |
| runner 仅认识两个旧 task ID | R1.1 根在小范围 GOVERNED 候选内扩本task路由并验证；本轮旧runner仅证明旧检查结果 | runner继续只检查，不授Git、业务源或Agent权限 |
| STATE 旧 task 标ACTIVE，可能陈旧 | 核对精确 native thread/status/owned paths和当前文件前像。50最近+全部pinned列表只见本规划chat在PCH，不能据此宣称全局无writer | 不停别的chat、不按进程名推writer；重叠组未解决时先做独立owned工作 |
| 脏树/新旧服务代码身份未知 | 新owned worktree如必要需明确ref及所需本地patch归属；不把不相关脏树自动带入/提交。由精确模块前像处理 | 不reset/stash用户改动；不偷偷重启旧服务或全机 |

本轮仅追加 PREPARED 事实索引和候选材料，保留已有 state subtree；没有把附件的运行/UI授权写成当前生效规则。新会话以用户发送的 Prompt 为当前授权；R1.1 将必要入口最小更新具体化。无需重复读取完整历史或附带旧Mac全局规划。

## 4. 数据契约与唯一权威

四类分开：业务事实由项目唯一源提供；PCH 管理设置由用户在 Hub 配置存储维护；派生值由可审计程序计算；Agent 建议保持 proposed，明确接受后才成为相应管理决定。import 不覆盖用户配置；管理日期不改实际事实、不启动业务执行。业务回写不是本计划默认权限。

兼容原则：保留 source record 2.0、声明 schema2.0 和 API DTO1.0 的各自版本界限；扩展必需字段时明确升级/读取旧版本办法。复杂任务树可用一个声明内显式只读附加来源角色，或项目已有标准快照，不允许暗中多读文件/创建第二业务状态。现有 exporter metadata 不等于执行许可；默认直接读源，只有无法表达时才审计并登记 exporter 的 exact argv、读写路径、超时、资源和网络权限。无 shell 不是隔离；如需项目写 `.hub/status.json`，必须有该生成物单独授权。

下表是语义目标，不要求机械新增全部同名字段；能由现有契约表达的复用。`可空`=未知允许 null，但必须附 unknown_reason；明确0/空数组/不适用不等于null。F=项目事实（该项目owner更新）；M=Hub用户配置；D=Hub确定性派生；O=Hub采集观察；A=未经接受Agent建议。每个F/O字段保留source_ref、selector、输入hash、观察时间及unknown_reason。

| 字段/语义 | 类型/枚举；可空 | 权威/更新责任 | 公式、兼容与样例 |
| --- | --- | --- | --- |
| project_id/name/type/description | 稳定string；ID不可空，其余可空 | registry/Hub identity owner | `reaper-music-workbench`；根名大小写不决定ID |
| group/checkout_binding/repository | string/list/object；可空 | M或显式F；Hub binding owner | 稳定ID下checkout区分，重复clone不重复计项目 |
| registration/disposition/access | registered/candidate/excluded/legacy；不可空 | registry/Hub | removed禁止读取；登记不是写授权 |
| lifecycle | idea/planning/prototype/development/verification/usable/maintenance/archived；可空 | F | 执行status不拿来代填lifecycle |
| execution_status | not_started/active/paused/blocked/complete/unknown；可空 | F | 兼容既有current_work.status，完整枚举明确value_map |
| current_version/scope/phase/round | string/object；可空 | F | `2.0-math-20261012`、`RP-P1-R02` |
| current_task_id/objective/owner | string；可空 | F | 已计划不等于执行中；owner不推runtime权限 |
| next_action | string/object；可空 | F | 需明确恢复条件，不由Hub LLM编写猜测 |
| blockers/resume_conditions | typed list；可空 | F | `[]`明确无阻塞，null未知；active可有局部blocker |
| task_nodes | [{id,parent,kind,dependencies,status,condition,evidence}]；可空 | F；业务owner | project/stage/round/leaf；与provider execution task分开 |
| plan_version/counting_basis/unit | string；可空 | F | task/video/page不可混加；无basis不算百分比 |
| counts total/implemented/verified/human/not_started/active/blocked | 非负integer；可空 | F经验证或D | 口径一致才计，父子不重复；0/6保留原分母 |
| progress numerator/denominator/ratio | number/object；可空 | D | V/N，N>0且可靠才ratio；N=0“不适用”不算100% |
| baseline_denominator/change_history | versioned list；可空 | F或本任务STATE | 原111→当前N，取消不变完成；旧版本历史保留 |
| runtime_entry/has_ui | constrained ref/boolean；可空 | F | 有端口/进程不代表可用；false非unknown |
| usable_versions/core_capabilities | list[{version,scope,verified,evidence}]；可空 | F验证证据 | 可用v1与开发v2并存；旧测试不证明v2 |
| verification status/at/evidence/version | typed object；可空 | F或独立验证记录 | passed/failed/not_run/pending；证据与输入/code相绑定 |
| human_acceptance/candidate/scope | typed object；可空 | owner明确响应 | human_pending不因模型/测试/静默变通过 |
| page_inventory/id/kind/entry | list；可空 | F显式声明/Hub确定性路由适配 | design/implemented分开；route非页面精确总数 |
| known_page_count/completeness | integer + complete/partial/unknown；可空 | F或D | 不完整只说known，不造总数 |
| preview refs/time/source_version | constrained local refs/object；可空 | O | 1—3代表页；不能绑定版本时unknown_version |
| preview_state | current/historical/not_applicable/not_implemented/unavailable/unknown；不可空 | D/O/F | 脚本无UI≠截图失败≠UI未实现 |
| planned_start/end/milestones | timezone/date typed；可空 | M；用户 | 未排期null，不按mtime/轮次猜天数 |
| actual_start/end | timezone/date typed；可空 | F | 管理调整不能覆盖已发生历史 |
| relations/confirmation/revision | typed edges+unconfigured/independent/confirmed/proposed；可空 | M/用户；A单列 | 所属防环、任务依赖防环；普通相关可网络，项目依赖循环提示 |
| priority/watch/order/layout | typed config + revision；可空 | M；用户 | import保持；拖布局不改关系；CAS版本冲突 |
| fact_updated_at/export_generated_at | timezone timestamp；可空 | F/export声明 | 源mtime仅文件观察；重读旧源不宣称业务更新 |
| last_attempt/last_success/observed_at | timezone timestamp；可空 | O/ledger | 最近失败仍可有历史成功；普通GET不刷新 |
| snapshot_id/schema/source_version | string/hash；可空 | O/F | daily新run identity，同run重试复用identity |
| sync_state/reasons | fresh/stale/offline/error/unknown；不可空 | O/D | 读取TTL、fact age、authority drift各有reason；不能合成业务完成 |
| structure/value/evidence_coverage | counts+field list+denominator；不可空统计对象 | D | 结构合法/值实际已知/证据适用分开，核心字段全null不可complete |
| provenance | per-field {source,selector,hash,observed_at,reason}；不可空 | O | 与已有21字段proof互斥unknown规则兼容；不记录secret raw |
| delivery status/remote/branch/commit/check_at | typed object；可空 | 精确Git复核 | local/branch/main_verified分开；祖先关系证明remote main含commit |
| agent proposal/task/turn/result | typed object；可空 | A/实际native系统 | sent≠accepted≠completed≠verified；不混采集链路 |

核心实际值门槛：身份/读资格必须已确认；执行状态及当前阶段/工作至少能可靠解释；存在可追溯的下一步/阻塞或明确不适用；同步/观察时间可查；若源声明任务分母则原口径可追溯，否则显示unknown与补接需求。每个项目列实际value/evidence覆盖和重要缺口。未提供足够业务事实时保留partial，不要求Hub编造值或擅自让项目迁移栈。

## 5. 运行架构与迁移

继续用现有Python、SQLite、CLI/API和Web模块：项目唯一元数据/结构化状态/已有测试证据 → 显式只读适配或声明快照 → validation → 同一Hub本地投影/历史 → ProjectService → API/CLI/UI。PCH用户管理设置在既有store/同一DB的独立管理域，import不写它。没有第二Goal scheduler、独立进度store或大平台。

发现只在确认授权根、浅层目录进行，跳过models、media、cache、dependencies、backups、受保护根与越界link。新增/移动按稳定ID和checkout绑定审查；默认不全盘，不执行项目validation_entry。缺声明保留可见；选择器可靠的标准Markdown可以确定性适配，任意自然语言不交给LLM。

每日默认主机09:00 Asia/Tokyo，可配置并明确显示；不是本轮设置。优先已有可靠调度，否则systemd259用户timer/service，OnCalendar+Persistent处理未触发期间补跑；不配置WakeSystem来抢占睡眠。用户manager注销生命周期/linger必须实际验证；不得未经授权enable-linger或假设注销后永远在线。逻辑模拟、手动service start、真实timer事件三种证据分别计。

幂等/CAS/原子发布沿现有ledger。每日新运行ID；同一次失败复试用原ID。源读取前后版本一致才发布，事务失败保留原成功头；单项目错误隔离。超时/读量/并发限制以本机实际测量确定，预览独立低频worker。查询不启动采集、测试、截图、业务项目、模型或GPU。

迁移步骤：确认唯一writer与owned数据→生产只读preimage/适用版本→一致性SQLite备份到授权本地位置→副本演练schema兼容/旧记录重建/配置保持→仅实际授权窗口切换→核对head/数据/行为→保留恢复点。无确切preimage且有并发更改不能声称rollback；不能为测试破坏真实source。先增量兼容、验证再删除确证owned superseded路径；不清理用户/别的task数据。

## 6. 阶段、轮次和验收

**冻结111个等权叶任务；32个计划轮次（P0三轮已作规划，未来29轮）。** 产品任务为108；P0三个规划任务不冒充功能完成。机器目录逐任务写稳定ID、依赖、owned scope、改造、联调、测试、预期结果、证据、恢复和计数口径。阶段/轮次不重复计分。实际执行允许一个轮次分多次推进，但不能因此复制任务或重置失败/预算。

| 阶段 | 轮次 | 叶任务数 | 可见交付 / 关键门槛 |
| --- | --- | ---: | --- |
| P0 审计/规划 | R0.1—R0.3，3轮 | 3 | 本文、定义目录、执行Prompt，未开始产品 |
| P1 最小真实闭环 | R1.1—R1.3，3轮 | 9 | 入口/契约→适配/ledger/API修复→3真实项目页面与无Agent闭环 |
| P2 全量/日同步 | R2.0身份6项；R2.1—R2.7接入39项；R2.8—R2.10同步9项，11轮 | 54 | 39单元分7批；旧路径/4候选资格单列；每日独立同步/错误隔离/对账 |
| P3 管理视图 | R3.1—R3.4，4轮 | 12 | 任务树→关系/管理配置→甘特/未排期→日常可访问性 |
| P4 代表预览 | R4.1—R4.3，3轮 | 9 | 清单/版本→按需受控采集→适用性39对账与真实预览 |
| P5 Agent入口 | R5.1—R5.4，4轮 | 12 | 真实能力→确定性路由/队列→并发/恢复/审批→1个精确授权子项目闭环 |
| P6 整体验收 | R6.1—R6.4，4轮 | 12 | 全量/离线→重启/恢复→资源/说明/安全main交付→真实timer/用户接受/收口 |
| 总计 | 32轮 | 111 | 计划轮次不是保证工期；未知输入/真实时间/人类接受可能增加实际迭代 |

每日服务建设不依赖39个业务源全部补齐；单项blocked时继续独立项目与调度。P6基础验收可在P5等待子项目授权时继续；整体验收仍保留Agent端到端待授权任务，不能宣布全部完成。R5.4明确等用户选项目/文件/要求/验收，不自行给业务项目分发改动。普通PCH实现和已授权Git不逐轮重复索权。

验收矩阵：

| 层次 | 必需证据 | 不足以替代 |
| --- | --- | --- |
| 结构/兼容 | schema/types/provenance、DAG/计数、旧格式回归 | 文件存在、定义齐全 |
| 真实接入 | 每项源版本→校验→ledger→API→页面同ID/值，明确缺口 | 样本或fixture代全量、读源成功代值完整 |
| 页面功能 | 浏览器数据/交互/持久化/console/network、窄屏/键盘，具体candidate | 截图代CRUD保存、HTTP200代渲染 |
| 同步 | 无Agent/模型/外网、并发/坏源隔离、人工刷新与真实调度区分 | prompt placeholder、timer文件或模拟clock |
| 配置保持 | 修改priority/date/relations→导入→刷新/重启保留且业务源不变 | 单次API响应 |
| 预览 | 真实page/source版本、适用性与历史标识 | 设计稿代实现、route数量代全部页面 |
| Agent | exact native身份/请求/修改/测试/结果/审核/状态预览闭环 | 排队/已发消息/模型宣称完成 |
| Git | 当轮授权、可见性/保护、精确owned stage、commit、远端main包含commit | 本地commit或临时分支push |
| 人工/时间 | 具体candidate/scope的明确回应、真实timer journal | 静默/测试/手动start |

UI轮次适用 ui-design-governance：复用当前设计与组件，加载/空/错/历史/disabled状态明确；键盘语义/focus、响应式、reduced-motion；接受时读取该skill完整rubric。需要设计文件时Figma为design source，不在本轮另做视觉稿。真实截图/桌面采集在任务私有环境；没有能力就标未验。

## 7. 唯一台账、持续推进与Git交付

任务定义在 `data/roadmap/pch_refactor_v1.yaml`，动态结果只在 `STATE.yaml.pch_refactor.task_results[id]`。默认缺result为not_started。每任务分维记录 implementation、verification、human_acceptance、blocker、delivery，并保存input/code/contract版本、evidence refs、失败指纹与resume action。全局当前V/N从这一个state生成，不手写第二个会变动的表。

V只加当前有效verification=passed的叶任务；implemented、pending、human_pending、blocked、cancelled、main_verified分别报告。P0计3/111，产品计0/108；未来真实复用能力经本task验收可以加V，但必须说明既有复用，不能算本轮新建。计划版本变化保留111→当前N与ID映射/原因，不能减分母提高完成率。项目coverage分母39→当前确认集另记；业务项目自己的4/5、0/6、2/54又是第三类口径。

每轮节奏：复核相关输入→实施/配置→联调→必要测试→精确差异复核→更新唯一state→按当轮授权交付main→对话汇报→有可行下一步继续。汇报包含计划版本/轮次/IDs、实际可見效果、V/N与增量、pending/human/blocked、覆盖/值证据质量/新鲜度/失败、真实检查与未运行、local commit/remote main复核、唯一下一步。调用/Agent数量不计轮次；goal-progress只在已有closing checkpoint统计去重round keys，无新调度器。

附件的main交付要求已保留在执行Prompt，**仅用户发送明确授权的执行Prompt后生效**。执行前核对当前分支/dirty ownership、远端可见性与保护、fresh main；只精确stage该任务拥有的安全unit。允许正常main提交时正常交付；必须PR时走检查和必要审批，完成创建即attach该PR。不同仓mutation/validation/state/Git/external effects串行。禁止git add -A/force/reset/stash他人内容/改remote/绕保护。失败实现留隔离分支；仅diagnostic文档交付不代表功能PASS。

本机DB、实际任务消息、凭据、完整私人根清单、敏感截图不进入公开仓。即便private也只提交必要脱敏材料。远端已有后续commit用祖先关系证明包含本轮unit；不要求HEAD永远相等。回执在本地state/后续合法交付归档，不为把SHA写回同commit无限补交。runner始终只检查，不获得Git写权限。

真实阻塞：缺结构化业务输入、owner权限/凭据、生产迁移交接、main保护/审批、真实timer/注销未观测、人类接受、R5.4未授权。先完成独立可做项，保存原分母/失败/版本/精确恢复点。没有可推进项时遵守当时Native Goal工具的实际blocked规则；不虚构权限拒绝、pause接口或完成。用户直接stop/pause立即停止本task dispatch/effects并按精确身份interrupt全部live descendants；其余owner任务保护。

## 8. 官方与本机参考

本机man `systemd.timer(5)`已核实：Persistent只对OnCalendar生效，timer重新激活会补未激活期间应发生的触发；受RandomizedDelay影响，文件设置本身不证明运行。与客户端时区分开。

[Backstage关系文档](https://backstage.io/docs/features/software-catalog/well-known-relations/)仅用于关系语义参考；文档明确展示所有权关系不是运行时授权。未安装Backstage。[Playwright截图文档](https://playwright.dev/docs/screenshots)用于P4受控页面/局部截图方法，截图不证明交互和业务接受。[GitHub保护分支](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)要求按实际保护/检查/审核流程交付，授权不绕过规则。

[Codex官方App Server API](https://learn.chatgpt.com/docs/app-server#api-overview)已检索并读取，列thread/goal/set/get/clear。当前Desktop还实际暴露create_goal/get_goal/update_goal；只核实可用及goal=null，本轮不调用create_goal、不创建automations。未来执行首步再次核对工具/版本，不假设通用`/loop`、Goal Hook或IDE接管接口。

规划交付到此结束。下一轮仅在所有者发送执行Prompt后从R1.1首个未完成、依赖满足任务开始；不自动进入P1。
