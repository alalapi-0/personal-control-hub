# 全局 Agent 治理：目标模式执行规范

- 版本：2026-09-05 / v1。
- 状态：待所有者明确启动阶段 B；本文档的创建、阅读或审核不代表启动治理。
- 读者：未来执行本目标的 Codex Root，以及其按需调用的 Governor、Judge。
- 用途：承载简短目标模式 Prompt 引用的范围、参数、边界和验收要求。
- 更新触发：所有者改变目标或授权，或定向核验发现影响执行的事实变化。不得为了每轮留痕更新本文。

## 1. 目标与权限

在必要授权、凭据保护和结果正确性约束内，提高个人、非生产研发项目的有效交付，减少重复执行、无效审核、上下文消耗和过度留痕。治理对象是 Agent 层，不是业务项目。

仅在所有者明确使用引用本文的执行 Prompt 后，启动阶段 B 并使用可用的原生 Goal 工具。不得再生成下一层 Prompt、创建后台无限循环、定时器或第二套 Goal 状态机。原生 Goal 不扩大权限。

阶段 B 允许在第 3 节白名单内实施必要的本地治理和隔离验证；不授权提交、推送、部署、发布、购买、凭据变更、对外通信、存储迁移或业务数据清理。历史授权和旧路线图不能扩张本任务。系统、开发者及当前所有者的明确边界优先。

## 2. 阶段 A 基线及首轮核验

以下为 2026-09-05 的只读证据，不是对未来会话的运行保证。

| 已读入口 | 已知事实及证据位置 |
| --- | --- |
| `/Users/alalapi/.codex/AGENTS.md`、`AGENT_ARCHITECTURE.md`、`AGENT_PARALLELISM_POLICY.md`、三个 `agents/*.toml` | 已有 DIRECT／REVIEWED／GOVERNED、Root／Governor／Judge／Repair，以及两次无进展后诊断规则。Governor 文件第 19 行仍以修复次数无固定上限表述；缺少统一任务预算。 |
| `/Users/alalapi/.codex/config.toml` 的必要非秘密字段 | 当时是 `gpt-6-astra`、`ultra`、`:danger-full-access`、`never`，同时存在 `sandbox_mode`；子 Agent 并发配置为 3。架构文档的模型及 sandbox 描述已落后。配置差异不等于已验证故障，也不授权改变权限或模型。 |
| Codex 的 `clarify-decisions`、`task-health`、`ui-design-governance` 主文档及强制 rubric | 已有重大选择、健康检查和候选冻结机制；不是额外常驻领导或通用预算账本。`clarify-decisions/agents/openai.yaml` 允许隐式调用。 |
| Cursor 三角色、个人 `three-role-governance`／`goal-mode`、`hooks.json`、791 行 `goal-mode-runtime.py`、两份治理记录 | 个人 Goal Hook 与内置 Goal 静态并存。治理记录第 190 行有状态／哈希更新引发再次审核的记录。Hook 第 239 行的全局锁等待、第 734 行的无效／缺失标记续接存在待复现风险；不宣称已经发生运行故障。 |
| Cursor 内置 `goal`／`loop`／`autopilot`／`create-hook`／`create-subagent` | 内置 Goal 与个人 Hook 的预算及续接契约不同；这些托管 Skill 保持只读。Cursor CLI／IDE 的已读执行模式均为 unrestricted，不修改这些权限。 |
| Hub 的 AGENTS、STATE、治理／审批政策、registry、外部项目协议、驱动 Prompt、直接引用的存储与 UI 治理协议；gate／runner 定向实现 | policy 第 121 行起仍把低风险写入列为需确认；runner 第 140 行自身调用 gate，而 AGENTS 又要求单独调用。policy 与 gate 有绑定，必须作为一致候选处理。Hub 产品任务当时 PAUSED，存在大量既有未提交工作；存储摘要已完成。 |
| `/Users/alalapi/PycharmProjects/AGENTS.md` 及 `.agent/STATE.md` 的治理章节 | 工作区不是共享业务仓库；旧 STATE 混有历史 51 仓任务，不得继承或重开。逐仓历史未完整展开。 |

读取覆盖：以上规则正文由 Root 或只读子 Agent 阅读；配置只解析必要字段。未读全部业务仓库、源码、数据库、素材、完整会话、凭据和备份树。Manga 在 registry 中明确禁止检查，未进入。其他登记项目只作为根路径、规则入口和状态指针元信息。

尚未确认：Cursor 账户同步主规则正文、现行覆盖关系、原生 Goal 活动状态、具体文件写者、Hook 实际事件行为，以及新会话加载。任务列表只反映可见快照；idle/notLoaded 或空锁目录不能证明没有写者。没有可靠的节省 Token／费用基线。

首轮定向核验：

1. 读适用 AGENTS、全局架构及本轮涉及文件的完整正文和强制引用；按版本／哈希建立本轮基线。未变化内容去重读取，后续只补相关变化，不重新扫描全部仓库。
2. 沿现有索引和引用发现入口；配置按字段脱敏。不得读取环境变量全集、浏览器会话、账号数据库或完整进程命令行。
3. 用现有原生状态及文件归属约定核实本轮写者；保护既有 dirty 工作。无法确认的文件延期，独立范围继续。
4. Cursor 主规则只能通过支持的只读界面定向取得；不可见就保留缺口，不假装已读。依赖该规则的生效结论延期，不阻塞独立 Codex 范围。
5. 改 Hook 前完整读现有测试和直接调用链；运行 Hub 检查前确认环境／一致性检查的读取范围与副作用，不因命令名含 check 就认定无副作用。
6. 核实工作区 STATE 的治理章节和在用归属。只在保护边界确需时补读对应历史，不从历史派生业务任务。
7. 保留简短的已读／排除／缺失／加载关系摘要。未查清的子范围不进入修改。

## 3. 精确写入白名单

白名单是最大允许范围，不是要求全部改动。不存在的现有目标、越过软链后的实际目标或新发现的额外引用，须先核实归属；不得静默扩域。

### Codex

```text
/Users/alalapi/.codex/AGENTS.md
/Users/alalapi/.codex/AGENT_ARCHITECTURE.md
/Users/alalapi/.codex/AGENT_PARALLELISM_POLICY.md
/Users/alalapi/.codex/agents/governor.toml
/Users/alalapi/.codex/agents/judge.toml
/Users/alalapi/.codex/agents/repair.toml
/Users/alalapi/.codex/skills/clarify-decisions/SKILL.md
/Users/alalapi/.codex/skills/clarify-decisions/agents/openai.yaml
/Users/alalapi/.codex/skills/task-health/SKILL.md
```

### Cursor

```text
/Users/alalapi/.cursor/agents/governor.md
/Users/alalapi/.cursor/agents/judge.md
/Users/alalapi/.cursor/agents/repair.md
/Users/alalapi/.cursor/skills/three-role-governance/SKILL.md
/Users/alalapi/.cursor/skills/goal-mode/SKILL.md
/Users/alalapi/.cursor/hooks.json
/Users/alalapi/.cursor/hooks/goal-mode-runtime.py
/Users/alalapi/.cursor/hooks/test_goal_mode_runtime.py
/Users/alalapi/.cursor/CURSOR_GOVERNANCE.md
/Users/alalapi/.cursor/CURSOR_GOVERNANCE_STATE.md
```

### 工作区与 Hub

```text
/Users/alalapi/PycharmProjects/AGENTS.md
/Users/alalapi/PycharmProjects/.agent/STATE.md
/Users/alalapi/PycharmProjects/personal-control-hub/AGENTS.md
/Users/alalapi/PycharmProjects/personal-control-hub/governance/agent_policy.yaml
/Users/alalapi/PycharmProjects/personal-control-hub/data/gates/auto_advance_policy.yaml
/Users/alalapi/PycharmProjects/personal-control-hub/data/mcp/mcp_approval_policy.yaml
/Users/alalapi/PycharmProjects/personal-control-hub/scripts/agent_gate.py
/Users/alalapi/PycharmProjects/personal-control-hub/scripts/auto_advance_runner.py
/Users/alalapi/PycharmProjects/personal-control-hub/prompts/codex_project_driver.md
/Users/alalapi/PycharmProjects/personal-control-hub/prompts/cursor_project_driver.md
```

范围限定：

- 三角色文件只改职责、路由、预算、证据、停止协议；不改模型、readonly、权限或沙盒字段。并行协议仅修直接冲突，不改容量，不作无必要的性能实验。
- hooks.json 只处理已确认的个人 goal-mode 注册，不触碰后来出现的其他 Hook。Hub 脚本只改 Agent 门禁、检查路由和提示生成，保留不自动授予权限、不启动业务、不自动 Git 发布的边界。
- 本任务唯一续接状态是工作区 `.agent/STATE.md` 的一个 `global-agent-governance` 节。它只管本任务，不继承旧 51 仓权限，不替代 Hub、StorageGovernance 或业务状态；其他记录只保留必要本地事实和该节指针。
- 不改 Hub STATE、registry、存储适配器／参数、UI 规格及两个业务 Goal Prompt。指针漂移留为独立后续事项。
- `config.toml`、Cursor `cli-config.json`／`permissions.json`、账户同步规则、托管 Skills、系统 Skills、插件缓存、账号与运行数据库不在写入范围。保留权限、费用、模型、MCP、插件和存储映射设置。
- 不授权业务源码、业务测试、依赖、数据库、素材、模型、部署配置、Git 历史或其他项目文件。旧规范不能扩大这一边界。
- 隔离验证优先内存；确需临时文件，仅可新建并独占 `/Users/alalapi/.codex/tmp/global-agent-governance-check/`，不得覆盖已有同名内容，结束后移除本任务临时产物。持久测试仅限上列现有 Cursor Hook 测试。
- 不默认新增 Skill 或角色；先复用全局入口、clarify-decisions 和 task-health。证据证明仍有必要缺口时，提出一个精确范围扩展，不自行创建。
- 本文作为固定执行参数保持只读；所有者改变授权时再修订，不由执行者自行改白名单或提高预算。

## 4. 准入、减法和角色边界

任务入口短判断一次：真实交付、最低验收、不做项、最小范围、下一步证据。只在新任务、实质扩域、重复失败、预算异常或依赖冲突时重判，不在每个正常小步骤重新规划。

新增机制前依次检查不做、删除、合并、复用、缩小范围是否足够。必要测试保留；偏好性重构和未来能力不能成为当前需求。删除前有界核验用途、引用、在用状态和影响；不能盲删，也不能要求穷尽历史才允许删除。按未来收益与成本决定继续或停止，不以已花 Token、已写路线图为理由继续。

复用 DIRECT／REVIEWED／GOVERNED、TASK_CONTRACT 和已有输出状态，不新增最高领导：Root 管路由、预算、写入归属、证据和控制面修改；Governor 仅为 GOVERNED 确定目标、不做项、边界、预算和验收；执行者在范围内自主推进；Judge 核验预先约定要求及原始证据，偏好不得变成隐性阻塞。Repair 不写控制面。

普通 DIRECT 不走完整三角色，REVIEWED 使用必要独立审核。本次阶段 B 控制面修改按 GOVERNED，由 Root 直接写入。每个实质语义变化的冻结候选进行一次新鲜 Judge 审核和一次 Governor 决策；合同只在目标、保护边界、外部效果或验收变化时修订，不因实现路径变化机械新建。

把受审规则／代码与记录判定的状态元数据分开。追加 PASS、决定和下一步，不使未变化的受审内容重新失效；不要创建包含自身哈希的审核循环。实际语义变更仍需重新登记和验证。必要角色不可用时不得模拟独立批准。

## 5. 去重、失败和预算参数

最小任务记录包括稳定任务 ID、验收、相关输入／代码／配置版本、候选及证据位置、完成状态、失败指纹、累计预算和唯一下一步。

- 证据仍有效的任务不重开。换会话、角色或计划不是重做理由；相关版本变化、新失败或明确新需求只重开受影响部分。
- 外部效果结果不明时先核对是否已发生，不盲目重发造成重复发布、创建或计费；本任务不授权这些效果。
- 失败指纹包含目标／检查、相关版本、失败类别和已试方法。同一状态、同类失败、无新证据时不重复同一动作。
- 有短暂故障证据最多额外重试一次；缺前置条件则解决该条件，需求冲突固定边界，确定性缺陷提出新假设及定向修复。缺环境不自动引出业务重构。
- 两次无实质进展后必须诊断或改变方法。新根因证据、缩小失败面、有效候选变化或新验收结果才算进展；重复叙述和计划不算。
- 互相等待时缩小任务、固定接口、合并不可分任务或回到已有裁决者，不增加协调层。重复问题先修局部规则／回归，确认跨项目共性才提升全局，同时淘汰过期规则。

| 参数 | 上限与含义 |
| --- | --- |
| 修复尝试 | 仅作累计审计事件，不设固定尝试次数上限，不按角色、合同或会话清零；两次无进展后改变方法。是否继续取决于新证据、可行路径和剩余统一预算，不以尝试次数本身停止。 |
| 工具调用总预算 | 整个任务最多 120 次实际工具调用，父子 Agent 合计；编排器内各实际调用分别计数，不重复计编排外壳。只读发现、实现、验证、诊断及审核均计入。Root 派发时从剩余额度中预留子任务额度，收回其调用计数；计数不可观测时保守扣满预留额并标明，不伪装精确用量。至少预留 10 次用于验收与收束，不把余额用尽后再启动新候选。 |
| 子 Agent 调度 | 整个任务累计最多 12 次启动或重新派发，包括发现、规划、诊断、审核和失败派发。给已有运行 Agent 补充同一工作的证据不算新派发；启动新工作算。 |
| 同一短暂故障 | 有证据才允许 1 次额外重试；确定性重复失败不享有该额度。 |
| Token／费用 | 有覆盖范围明确的真实遥测才记录；不可见就标不可用，以以上可核验上限替代。不从账户百分比、历史数据库或墙钟伪造消耗。已有更紧原生预算优先，不自动提高。 |

所有规划、执行、审核、重试、诊断和子 Agent 工作都受同一任务预算约束，改名不清零。这些是上限，不是要用完的轮次。诊断也须有一个可改变决定或产生证据的出口。task-health 只按其已证实触发条件作一次只读检查，不建新监控或计数台账。

## 6. 数据处置与最小留存

秘密保护、正确性、历史留存、备份和审批频率分别决策。在已确认个人非生产实验范围，按项目、目录或类别一次定义并持续适用：

1. 必须保护：凭据、账号／权限配置、所有者明确重要的原始资料／资产、外部承诺。
2. 维持运行必需：有效配置、当前任务状态和其他 Agent 在用文件，可以授权更新，不能误当废物清理。
3. 默认可处置：确认属于本人可处置且未受保护的生成物、测试输出、缓存、临时文件、旧草稿、重复报告和历史中间版本。

第三类在未来有明确操作范围的执行任务中允许覆盖、移动、重建、删除，不默认逐文件审批、每步备份或永久留存；不确定时只核验该项来源、属主、用途和在用状态，不把整个工作区升级最高风险。实验产物允许失败尝试、暂时不完整和重建，只对声称完成的结果承担验证责任。

本次只修改上述政策，不执行业务清理。Agent 自身也只在白名单内确认用途和引用后删减，不按 log/cache/backup 文件名批删，不动平台会话与运行数据库。

只留当前状态、关键版本、必要验收、未解决阻塞和有效故障结论；复用现有版本管理、可重建性和短摘要，不造备份树、每轮报告和多份进度。重大修改保留必要的恢复依据，不要求所有中间态全部可回滚。调试信息限定事件／数量，解决后收敛。不得伪造通过、隐瞒失败、删除未解决证据或承诺控制平台日志。

## 7. 并发、发布与生效

确认 Codex／Cursor 真实加载与覆盖关系，不假定共享记忆或立即热更新。优先用现有登记／归属约定；缺少时只在唯一状态中记 Root 身份、精确文件集、基线版本，不搭锁服务。约定不能排除不可见写者；归属不能建立时不写该文件。

既有 dirty 工作保留，不覆盖、不自动归属。本轮未查清的依赖组整体延期，例如 Hub policy 与绑定 gate，不能发布半套互相矛盾规则；独立范围继续。跨仓只有读取可并行，写入和验证按范围串行。不终止他人 Agent、全局重启或操纵平台锁文件。

Cursor 原生 Goal、个人 goal-mode 和 loop 要有明确互斥和适用条件。先验证当前入口，再决定保留、缩小或退出个人 Hook；不得双重续接同一任务。退出前确认无在用依赖，不清除运行状态。对锁等待、标记和事件 generation 假设先隔离复现，避免按猜测改实现。

发布分别标记已写入、加载已验证、行为已验证。需要新会话时，只做本任务最小无业务副作用验证；无法验证就写待新会话生效，不宣称已有 Agent 全部采用。

## 8. 最少阶段与验收

只细化下一步，后续保留三个里程碑：必要事实／归属／合同；按依赖最小一致修改；定向验证／独立审核／收敛。已有机制满足目标就保留，不为形式统一重写全部文件。

优先配置解析、规则样例回放、现有治理测试和合成 fixture。不跑全部业务测试，不真实删用户数据。能机械落实的限制优先用已有 Hook／校验器；文档提醒明确为软约束，不新建监控平台。

| 验收情形 | 必须证明的行为 |
| --- | --- |
| 授权低风险实验操作 | 无无意义逐次审批和全量备份；必要结果验证保留。 |
| 无新证据的同类失败 | 停止盲试，诊断仍计预算；缺环境不扩为业务重构。 |
| 已完成任务换会话／角色 | 版本与证据有效时不重开。 |
| 审核新增偏好 | 不扩大验收；真实缺陷仍能阻止错误通过。 |
| 同文件存在其他写者 | 不覆盖其工作，独立范围能继续。 |
| 凭据／在用状态／重要资产 | 保护仍成立，业务清理不越界，减少日志不隐瞒失败。 |
| 只更新审核状态元数据 | 不形成自触发的再次审核循环。 |
| 修改了 Cursor 续接机制 | 验证单入口、停止／完成／无效标记、超时与跨会话隔离。 |
| 修改了 Hub | policy/gate 一致、不重复检查，不把 PAUSED 或旧授权当成恢复许可。 |

区分文本检查、隔离回放、入口加载和实际行为证据；未验证不记通过。有少量现成可比记录时比较重复次数、有效交付、人工介入和可观察消耗，无基线不虚构节省比例。

最终状态：入口采用准入／减法机制，无新增最高决策层；已确认重复规则、检查和留痕得到有证据的删除／合并／降级；完成保护、失败、预算、职责裁决及交接有出口；实验数据政策与必要资产边界兼容；加载、并发归属和续接条件清楚；必要验证及独立判断完成，不依赖重扫所有仓库。

## 9. 续接、停止与恢复

每轮只在唯一状态节记录问题、范围、实际变化、证据、累计预算、继续与否和唯一下一步。交接传目标、差异、证据入口和阻塞，保留审核者核验原始材料的入口。

恢复时读该节及相关文件版本，增量复核，不重做仍有效诊断。其他项目旧暂停／完成状态不随本任务恢复。

真实权限／凭据缺口、无法安全合并的并发修改、保护边界冲突或预算上限，只停止受影响动作；独立工作先完成。交付精确续接条件，不自动升权、扩预算或重启其他客户端。预算耗尽不是完成，也不伪装成权限阻塞。

发现候选违反边界或造成已验证回归时，停止其发布；优先定向前向修复，必要时只恢复本任务拥有且已登记的精确前像。恢复前复核当前版本及他人改动，不整目录回滚。无可靠前像时保留候选、说明缺口，不捏造已恢复。

用户明确停止时立即停止新派发和写入，以精确身份收束本任务自己的子 Agent，不影响其他顶层任务。原生 Goal 状态按工具实际能力和语义更新，不伪造暂停接口，不把未完成记 complete。

最终只交付变化、删减理由、证据、生效范围和缺口；全部验收后结束 Goal，部分延期明确未完成及下一步。达到目标即停止，不继续制造治理任务。推进优先不跳过正确性；保护必要资产不等于保存所有历史。
