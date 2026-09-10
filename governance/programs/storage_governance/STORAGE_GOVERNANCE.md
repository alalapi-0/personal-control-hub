# 本机存储治理协议

- **Reader：**执行当前存储批次的 Root；风险审查时的 Governor/Judge；历史调查者只读第 13 节索引后再定位精确证据。
- **Update trigger：**目标、保护边界、authority 模型、风险/审查路由、完成定义或长期存储规则发生变化。
- **Purpose：**提供稳定、比例相称、不会自我循环的存储治理政策。当前状态只在 `STATE.yaml`。

## 1. 权威层级与上下文预算

1. `AGENTS.md` 只负责路由；`STATE.yaml` 是唯一当前状态、authority、blocker 和下一步权威；本文件只负责稳定政策。
2. 当前批次的合同冻结目标、保护边界、授权 effect 和接受条件；当前 evidence index 证明事实。旧合同、旧 Prompt、历史审查或已消费批准都不能产生当前 authority。
3. 已激活续轮先按 router 读取 Hub 参数、清单、adapter、router 与唯一 STATE，再按 next_action 读取当前项目和必要条款；当前 owner 修订及当前合同优先于旧严格存储验收。
4. 禁止默认注入完整历史、完整日志、完整 subagent 对话、全部旧合同或全部旧证据。审查包只包含合同、精确候选 identity/diff、相关条款和可重现的有界证据。
5. 现场事实高于登记状态；出现 drift 时停止 effect，只记录一个 compact drift 和恢复条件。

## 2. 目标与保护边界

当前所有者明确采用“释放内盘空间优先、项目盘外可用其次”的标准，并接受低价值项目历史产物永久丢失及以后修复非核心问题。执行者依据项目归属、路径用途、生成机制、时效、当前入口和 writer 状态作合理判断；无需先证明每份历史数据不存在潜在价值。不可再生、旧引用失效或不能完整重放历史，本身不再构成保护理由或阻塞。

明确属于当前项目且低价值、过时的日志、验证记录、临时文件、构建物、缓存和可替代依赖可以精确批量删除，不必先归档或证明现在可完整重建。有用的大型数据、模型、素材和运行时优先迁移到经既有身份守卫验证的外盘，完成基本复制检查与路径切换后清源。已有外盘目录本身不证明实际使用。主重型入口不静默回落内盘。

冻结项目清单、严格单项目串行和唯一 current-state 不变。Manga 仅为已完成排除记录，不进入其项目或关联路径；Hub 与 StorageGovernance 控制面留内盘，不迁移不清理。所有者授权修改的全局 Codex/Cursor 权限配置不读、不改、不回退。

以下仍不属于普通批量清理：
- 账号凭据、密钥、登录和浏览器/Codex/Cursor 会话、正在使用的数据库及当前有效恢复状态。
- 源码、Git 历史、用户 dirty work、无明确归属或与本项目无关的数据；不能因同处生成目录便一并删除。
- 活动 writer 或正在使用的精确目标。先限定路径处理无占用部分；确有阻塞才登记精确恢复条件。
- 磁盘格式化、分区、加密、所有权/挂载策略、sudo 或批量权限变更。
- 未授权联网下载、Git 发布、外部服务写入及离线视频盘访问。

项目历史文件中的过时合同、恢复命名或敏感性猜测不能自动扩大保护范围；需要用当前用途与精确归属区分，但不得读取或输出真实秘密。

## 3. 数据分类与默认动作

| 类别 | 默认动作 |
|---|---|
| A | 凭据、会话、活动数据库和当前有效恢复状态：排除，不打印、不复制、不清理 |
| B | 源码、Git、dirty work及小型必要配置：留内盘或仅作当前授权的精确路径修复，不混入生成物清理 |
| F | 明确归属项目的低价值生成物、旧验证记录、日志、临时目录、构建物、缓存、可替代依赖：按用途和当前使用情况批量精确删除；可直接丢弃，无恢复保证 |
| G | 仍有使用价值的模型、素材、普通项目数据或运行时：守卫后复制／迁移，检查成功、文件数、逻辑字节及代表样本，切换相关入口后清源 |
| H | 实际归属／权限不明、活动占用或外部能力拒绝：原位保留并记录精确恢复事件 |

F 不要求备份、逐文件哈希、内容或历史引用图裁定，也不要求立即重建成功。G 不要求第二套恢复副本、全量摘要、原生扩展属性等价或所有历史读者重放；配置修复仅保留必要前像。使用现成外盘副本作为后续修复来源即可。允许适当的项目级配置、环境变量或精确兼容链接；不能整仓／整 home 软链，不能跟随链接扩大删除范围。

## 4. 外盘身份与断盘保护

当前卷名、挂载点、Volume UUID、Container UUID、安全属性和 bootstrap 路径只登记在 `STATE.yaml`；执行前现场复核。

统一守卫必须：

1. 同时验证 Volume UUID、Container UUID、真实挂载点、单一挂载、文件系统、可写性和空间余量；目录存在或卷名相同不能代替身份。
2. 身份通过后才读取外置 map、导出专用变量或创建目标。
3. 外盘缺失、错误、只读、路径漂移或 map 缺失时，以非零码和明确错误退出。
4. 禁止在未挂载的 `/Volumes/...` 普通目录、内置盘或项目仓库创建大数据 fallback。
5. 保护启动、下载、构建、模型加载和产物生成等所有重型入口；重挂正确卷后可无损重试。
6. 负测仅对改变的守卫／路由作代表性检查，复用未变化的既有证据；使用隔离 fixture 或错误 identity，不活动拔盘、强制卸载或破坏生产数据。

## 5. Authority 与批次状态机

`STATE.yaml` 必须为每种 effect 使用显式枚举：`none`、`granted`、`consumed`、`closed`。缺字段等同 `none`。普通项目的动态 disposition、唯一 active project/batch/writer/next action 只在冻结 manifest 与 `STATE.yaml` 中表达，Hub registry 只负责身份和路径。

允许的执行状态：

- `PAUSED`：用户主动暂停；不是技术 blocker，不得自动恢复。
- `WAITING_FOR_USER`：需要新的 owner 决策或 authority；记录一个问题和恢复条件，不周期性复查。
- `READY`：有明确下一步且所需 authority 完整。
- `PREPARING`：只执行已授权的可恢复准备、复制或候选实现。
- `EFFECT_AUTHORIZED`：精确 destructive/significant effect 已通过必要门禁且尚未消费。
- `VERIFYING`：effect 后只做当前批次验证和必要回滚。
- `ACCEPTED`：当前批次结果已接受，authority 已消费/关闭。
- `FAILED_RECOVERABLE`：失败证据保留，可在同一冻结目标和边界内换方法修复。
- `BLOCKED`：同一真实外部阻塞达到全局规则阈值且无独立可执行工作。
- `COMPLETE`：当前 inventory epoch 的完成条件全部满足。

每轮只推进 `STATE.yaml.next_action` 指定的一个 coherent delivery unit。状态为 `PAUSED`、`WAITING_FOR_USER`、`BLOCKED`，或所需 authority 为 `none/consumed/closed` 时，立即停止；不得制造合成 successor、审查 bookkeeping 或自动换批次绕过暂停。

`execution_control` 只有在 native Goal 当前活跃、该 Goal 的精确 Prompt 已登记为 authority source、inventory epoch 已有 immutable project manifest/cutoff/hash，且恰有一个 active project 和一个 active batch/effect set 时才可进入 `READY/PREPARING`。否则必须是 `WAITING_FOR_USER` 或相应终态。

## 6. 比例相称的风险与审查

沿用全局 DIRECT／REVIEWED／GOVERNED 的实质风险边界，但不把每次 unlink 或跨卷复制机械地认定为重大效果。所有者已经接受普通项目低价值历史产物的永久损失。

- 当前合同范围内、归属清晰的普通本地生成物清理和基本迁移，由 Root 登记精确范围并执行比例检查，不逐微批增加合同、Judge 或 Governor。
- 广泛行为变更按实际影响选择 REVIEWED；新增敏感边界、真实生产数据、live migration、未授权重大外部效果或控制面改版使用 GOVERNED。
- 当前目标／风险取舍的控制面修订需要 fresh Judge 和 Governor；epoch 最终候选保留一次独立终态验收。
- 同项目的普通清理、复制、修复、抽样校验和状态同步不分别触发审查。不为历史归档持续建立专用读取器或增长测试套件。

合同仅在用户目标、保护边界、授权效果或接受条件变化时修改。普通实现细节不重开合同。Judge 有具体问题时 Root 修复；未变化事实不重复审批。

## 7. 非循环执行规则

1. 新 epoch 只做一次有界 project capture：从实际仓库、显式非 Git 开发目录和有项目归属证据的关联路径登记 cutoff、immutable project manifest 和 hash 后冻结队列；项目尺寸为零也不能因此遗漏。后续新发现进入下一 epoch，不能阻止本 epoch 结束；已终态项目无 material drift 不重扫。
2. 任一时刻只能有一个 active project、一个 active batch/effect set、一个 writer 和一个 `next_action`。当前项目必须先进入 `accepted`、`protected`、`ineligible` 或 `deferred_with_fact_blocker_and_exact_recovery_event`，下一续轮才可选择另一项目；不得嵌套批次或并行项目 effect。
3. 每个项目只做一次比例相称的 capability/source/target 前检和一次 effect 后验收；项目内可包含同一闭环所需的精确子范围，但不能借此启动另一个项目。`DIRECT` 不建合同、不派 Judge/Governor。禁止 successor contract、嵌套 preflight、验证的验证或只因状态文字变化重审。
4. 不重跑未变化的失败命令，不重派未变化的候选，不重审未变化的 evidence packet。相同 environment/capability fingerprint 的能力阻塞只探测一次；只有恢复事件或 fingerprint 实质变化时才可再探测。连续两次无进展尝试后，必须先诊断原因并改用实质不同的方法，才能再次尝试。
5. 稳定 blocker 只登记一个事实、恢复条件和 fingerprint，然后把项目置为 `deferred_with_fact_blocker_and_exact_recovery_event`，不周期性轮询。随后可选择冻结队列中最高可释放量、当前可行且风险合适的下一项目；不存在 materially different 可执行动作时进入 `WAITING_FOR_USER`，不得制造合成工作。
6. 状态复述、盘点叙述、计划、合同、审查文本、时间戳/hash 刷新、重复 blocker probe 和报告都不算实现进展；只有数据位置、配置/代码、可重现验证、实际释放字节、authority 或 blocker 的实质变化才算进展。
7. 当前 epoch 的 `authorization_scope.allowed_effects` 由创建 Goal 的精确用户 Prompt 一次登记、跨批次有效；每批精确对象登记只是 Root 的有界一次性执行登记，不等于独立审查，不要求用户重复授权，也不得扩大授权。一次失败只消费实际启动的精确 invocation，不消费整个 epoch authority。
8. 有当前可执行且已授权的 effect 时，一轮不能停在计划、审计或报告；但不得为了“继续”跳过 active project 的终态或并行启动第二项目。
9. 用户说停止或暂停时，立即停止新派发、存储写入、预检、外部/Git effect 和审查空转，并中断准确识别的活动子任务。

## 8. 执行、清源和验证门槛

每个项目集中处理一个合理的完整效果集合，不按历史合同或日志目录拆成独立治理轮：
1. 确认当前项目和规范化的精确目标、用途、排除项及清前分配字节；检查当前 writer、精确占用和 dirty/Git 交集。
2. 外盘效果紧邻执行先过现有双 UUID 守卫；普通删除只要求目标身份、范围与无活动占用。可以整段已确认的生成目录清理，不要求逐叶哈希门禁。
3. F 类低价值生成物直接精确删除或重建，记录“有意丢弃、无恢复保证”。G 类复制成功后核对文件数、逻辑字节和执行者选定的代表性样本，切换相关当前入口，然后清除精确内盘旧源。
4. 项目闭环时做一次主要入口启动／核心 smoke；确认主要重型路径外置且缺盘不静默回退。只对变更的守卫或路由补相关负测，复用未变化的守卫证据。不强制全量开发／测试／构建矩阵、历史重放或 OS 重启。
5. 核对清后目标缺失或残留、实际释放量、外盘增量、必要维护入口和已知问题。非核心回归单独记后续修复，不撤销真实释放量或阻塞推进。
6. 有用数据基本复制失败时不清对应源；遇到真实权限、占用、归属或外部能力问题仅停受影响范围。已接受的历史损失、无法重建旧日志关系不是 blocker。

“无其他 writer”采用当前路径级现场事实：实际运行且能写入效果集合的进程/agent、目标 cwd/句柄、Git lock/进行中 operation、规范化 dirty 交集。休眠编辑器和旧 worker 元数据不是活动 writer 证据。其他无关 dirty 状态不阻塞整个项目，也不能被本任务回退。不得使用 home、工作区根等宽泛删除目标，不跟随符号链接扩大范围。

## 9. 有界证据与状态写回

- `STATE.yaml` 只保存当前阶段、authority、当前合同/evidence index 指针、关键保护事实、一个 blocker 和一个下一步；不复制旧 round、全部 inode/hash、命令日志或 reviewer 文本。
- 每项目保留一个紧凑目标与结果索引；普通生成目录用规范化根、用途、排除、计数/字节即可，不强制逐叶 manifest。已有证据复用，不因控制文字修正重新证明。
- 只有 material state change、accepted milestone、authority change、rollback/post-effect result 才更新状态。无进展复核不写回。
- accepted milestone 在第 13 节只更新一行；完整证据留在 `/Volumes/AI_WORK_SSD/_governance/evidence/` 的对应批次文件。
- 报告区分 Root 复现结果与 writer/agent 声明；失败证据保留但不自动成为新 authority。

## 10. Hub、Git 与外部 effect

`personal-control-hub` 根状态只保存管理入口；存储治理的 router、唯一执行 STATE 与稳定规范共同归属 `governance/programs/storage_governance/`。2026-09-05 所有者明确授权归并这三份管理材料、更新精确消费者、清除旧可编辑副本并提交/正常推送验收后的 Hub 单元。旧根仅为既有 STORAGE_MAP 读者保留三个文件链接；不得镜像或双写。该授权不恢复已关闭存储权限，不允许业务项目、磁盘或全局配置变更。其他业务项目内容仍不复制到 Hub。
下载、联网、fetch、commit、push、发布、进程终止和任何外部系统写入都使用当前显式 authority；历史授权不得复用。

## 11. 完成条件

完成针对同一个冻结 project manifest，不重建 epoch、不因新发现无限扩张。每个项目及关联项进入 accepted、protected、ineligible 或 deferred_with_fact_blocker_and_exact_recovery_event；无 pending、active project/batch/writer 和未消费效果权限时才能结束。

- 已按价值和用途处理可执行的高容量范围：有用者基本外置，低价值生成物有意丢弃；保护项有明确边界。
- 主重型路径和维护方式记录清楚；项目级主要入口 smoke 结果如实记录，非核心缺陷允许作为以后修复项。
- 凭据／会话／活动数据库、源码、Git、dirty work、Manga、Hub和所有者全局权限配置边界保持。
- 按实际文件系统核对释放量、外盘新增量、残留与必要映射；不要把“已知副本存在”当成运行通过，也不把真实删除量因非核心失败抹去。
- deferred 只基于真实权限／凭据／活动 writer／外部能力拒绝／归属或范围不明，给精确恢复条件；旧引用失效、历史关系未全证或已接受的损失不能单独 deferred。
- 最终只做一次有限 reconciliation 和独立终态验收，不重新引入 TC1 的穷尽内容、回滚与验证矩阵。

普通产物“无恢复保证”是当前接受标准，不是失败或未完成。

## 12. 新 Goal 的唯一启动入口

后续新 project epoch 的唯一 activation Prompt 由本机控制面持有：

`/Users/alalapi/PycharmProjects/personal-control-hub/prompts/storage_governance_goal_mode.md`

稳定参数位于 `/Users/alalapi/PycharmProjects/personal-control-hub/data/programs/storage_governance_goal.yaml`，项目身份清单位于 `/Users/alalapi/PycharmProjects/personal-control-hub/data/registry/external_projects.yaml`。本节只提供未激活状态下的单向定位，不包含可提交的 Goal 正文或 effect authority；已激活续轮不得回到本节或 Hub Prompt 重新激活。

Revision 5 Prompt 属于已完成 Goal `01a046b1-06be-70e3-a5d4-0233ebc0093f` 的不可执行历史：SHA-256 `76f71e57ac97d4bf692d43f1c7cf0e50f68b7137c0a9f15434d36cf0daf40651`，`3224` bytes，`13` lines。其 authority 已消费，closed epoch、完成证据与结果仍以 `STATE.yaml` 和第 13 节索引为准；静态路径、旧 Prompt、旧 Goal 或历史批准均不能产生新 authority。

## 13. 不可执行的里程碑索引

本节只用于发现历史证据；不包含当前状态、下一步或 authority。详细记录以原外盘 evidence 路径和 `STATE.yaml.history_ref` 指向的不可执行历史前像按里程碑 ID 定位；本文件不复制终态叙述。

| 里程碑 | 结果 | accepted release 增量 | 主要证据 |
|---|---|---:|---|
| B001 | 建立控制面、bootstrap 与 UUID 守卫 | 0 | 外盘治理根与 B001 现场记录 |
| B002 | `audio_clone` 外置运行时/cache/app，旧 F 清源 | 5,791,752,192 B | `B002_FINAL_EVIDENCE.md` |
| B003 | `audio_clone` 模型 G 迁移、验证、清源 | 3,209,007,104 B | `B003_FINAL_EVIDENCE.md` |
| B004 | `universal-player` 外置构建路由，`.build` 清源 | 853,790,720 B | `B004_FINAL_EVIDENCE.md` |
| B005 | `ai-music-foundry` fail closed，等待锁定 wheel authority | 0 | `B005_AI_MUSIC_FOUNDRY_SCOPE.md` |
| B006/B008 | `pixel-world-asset-forge` 外置与最终清源接受 | 277,823,488 B | `B006_POST_CLEAN_EVIDENCE_V3.md` |
| B007 | 建立 compact router/state 与父 Hub 基线 | 0 | 本地控制面与 Hub accepted commit |
| B009 | Hub 同步闭环；全局 Xcode DerivedData 记 H | 0 | Hub milestone 与当前 deferred state |
| B010 | `zarathustra-adaptation` 外置运行时与 `.venv` 清源 | 29,106,176 B | `B010_POST_CLEAN_EVIDENCE.md` |
| B011 | `computer_study_plan` 外置 UI runtime 与 `.venv-ui` 清源 | 157,114,368 B | `B011_POST_CLEAN_EVIDENCE.md` |
| B013 | 清理 ShipIt 未引用的旧 Cursor 更新副本，保留当前更新与应用状态 | 1,263,030,272 B | `evidence_root` 下 B013 evidence index |
| B014 | 清理 npm content-addressed cache，保留 `_npx` 与配置边界 | 158,597,120 B | `evidence_root` 下 B014 evidence index |
| B015 | 清理无句柄的旧 npx CLI 与禁用 Stitch 执行树，保留服务器型子树 | 737,005,568 B | `evidence_root` 下 B015 evidence index |
| B017 | 清理 `universal-player` 旧 DerivedData 的 10 个可再生子树，保留签名 Products 与唯一测试结果 | 823,209,984 B | `evidence_root` 下 B017 evidence index |
| B018 | 清理截断且已被更新主运行时取代的 Codex runtime 安装暂存 | 124,567,552 B | `evidence_root` 下 B018 evidence index |
| B019 | 清理 `light_novel` 已提交事务的 539 个 canonical 临时副本，保护未提交事务和全部权威文件 | 3,969,110,016 B | `evidence_root` 下 B019 evidence index |
| B020 | 清理 3 个陈旧 pytest 会话目录和 `pytest-current`，保留空临时根 | 180,310,016 B | `evidence_root` 下 B020 evidence index |
| SGR1-010 | `light_novel` 章节审校数据外置、双 UUID 守卫入口切换并清除内盘源 | 1,069,686,784 B | `storage_restart_2026_08_31_r1` 下 SGR1-010 terminal evidence |
| SGR1-013 | Claude Code 旧版本清理并外置已校验回滚版；活动版本因注册入口保留内盘 | 677,244,928 B | `storage_restart_2026_08_31_r1` 下 SGR1-013 terminal evidence |
| SGR1-014 | Whisky Wine 7.7 外置离线恢复副本验真并清除无活动 caller 的内盘孤立运行时；Bottle 保持内盘且未改动 | 885,608,448 B | `SGR1_014_EFFECT_EVIDENCE_aaa962b19ba3b547dc17448e36ed791863456cc3ff276edb6ebdabe5cce1d7ae.yaml` |
| SGR1-015 | `manga-localizer` ignored macOS 构建包外置、修复 bundle-relative backend 路由并清除内盘构建副本；已安装 app 与 B012 staging 未改动 | 855,523,328 B | `SGR1_015_EFFECT_EVIDENCE_e028a71c69879e4c959756257a10834bd68e4c0fed092de4b886b7c9619056d2.yaml` |
| SGR1-016 | 清理旧版 `com.netease.163music` 无调用者的可再生在线播放缓存；当前 `com.netease.cloudmusic` 与其余容器数据未改动 | 804,495,360 B | `SGR1_016_EFFECT_EVIDENCE_8e5f4ebf4f76ac11c7de5ec14cc367ceb7a0aa83b5f0c55543207c2594874b1e.yaml` |
| SGR1-017 | Cursor Agent 三个版本化运行时外置，双 UUID 守卫真实入口切换并清除内盘 `versions` 副本；凭据、会话、logs 与 `worker.lock` 保持内盘 | 611,848,192 B | `SGR1_017_EFFECT_EVIDENCE_0f0772bafbb217bd5998fbb160de3aa21c4f9731a72e4f0dbb1b938c47ea4dd0.yaml` |
| SGR1-018 | `light_novel/workspace/review` 审核报告外置，所有已发现读写入口经双 UUID 守卫切换；清除 1,636 个内盘报告文件，仅保留 Git 路由说明 | 598,007,808 B | `SGR1_018_TERMINAL_CANDIDATE_R2_7bcf0f7e2013d3393a043e45d3273cc42fc56287a247b9a3fd986b7b4d8e52fd.txt` |
| SGR1-020 | `manga-localizer` 五组模型迁至双 UUID 守卫的外盘 bundle，dev/app/setup 真实入口 fail closed；清除内盘模型源且普通 setup 不再重建内盘副本 | 521,441,280 B | `SGR1_020_R2_EVIDENCE_5203a4afc35c86c829ff28d0cb7352b53336ae072ba90c95ca6186374776dfa8.txt` |
| SGR1-021 | `manga-localizer` 旧 `.model-staging` 与已验收外盘模型 bundle 完全一致，确认为真实打包流程不再使用的 F 暂存副本并清源；未创建冗余 cache 副本 | 521,433,088 B | `SGR1_021_EVIDENCE_887905b237055a3d5aa671ff485c2a8ea0f17292dc43df8a8a0249c4dd6f7e73.txt` |
| SGR1-023 | 清除 `~/.codex/plugins/.plugin-appserver` 内与已安装 ChatGPT.app 逐字节一致的两个签名运行时副本；活动 Chrome/plugin cache、配置、会话与 primary runtime 保持原位 | 291,971,072 B | `SGR1_023_TERMINAL_EVIDENCE_171bcf42067498aaebf648b7d02442db4cc697cc930c0e54923c3005e8cede5c.txt` |
| SGR1-025 | `manga-localizer` 锁定 Python runtime 迁至双 UUID 守卫的外盘，dev/app/setup/test/package 入口无内盘 runtime 回退；清除旧 `.venv` 大树并保留 4 KiB fail-closed 哨兵 | 335,630,336 B | `SGR1_025_TERMINAL_EVIDENCE_R3_7cdc07007152b47199531b901ec342a93a6df94f614b1b1b15c62e80a05bf38f.txt` |
| SGR1-029 | 确认 JetBrains 日志树无句柄、无 IDE writer 且 live allocation 与 cutoff 一致；同盘隔离验证后精确清除全部可再生日志，不触及配置、缓存、项目或 IDE 状态 | 260,079,616 B | `SGR1_029_TERMINAL_EVIDENCE_27db6d6f43f9e3a083458aecab4caec1b971124084fca25d69c7d854e0af9be8.txt` |
| SGR1-030 | 清除 Thunder 已分类且无调用者的可再生日志、事件与缩略图缓存；保留下载、凭据、数据库及应用状态 | 50,982,912 B | `SGR1_030_TERMINAL_EVIDENCE_6dcb354878a815c6165ac47ef98861cb84fb006fbd8834fc85d3e8c08575ff92.txt` |
| SGR1-035 | `manga-localizer` 前端依赖迁至双 UUID 守卫的外盘 runtime，真实 dev/build/test 入口切换并清除内盘 `node_modules` 负载，仅保留 0-block 登记链接 | 171,663,360 B | `SGR1_035_TERMINAL_1455a35a15e4220889b8e2d80f2a3a8423fa6399d333a48fd35312e802e4cbb8.txt` |
| SGR1-036 | uv 管理的 CPython 3.11/3.12 与 tool 数据迁至双 UUID 守卫的外盘 runtime；七组既有入口保留兼容并清除内盘运行时负载，仅留 12 KiB 守卫拓扑 | 162,631,680 B | `SGR1_036_TERMINAL_ef80081309b007f7d2208574b14089ab1e057e940668838a6bba9f459957809d.txt` |
| SGR1-037 | 清除 Codex 无句柄、无会话/数据库内容且与真实插件安装面分离的 plugin-sync 临时仓库与 marketplace 暂存；配置、会话、已安装插件 cache 和运行入口保持原位 | 168,603,648 B | `SGR1_037_TERMINAL_b3da4ff6bfd0e1374917c63b7dbff7374f220c4c623dba4fde3c43992b97b35d.txt` |
| SGR1-039 | `manga-localizer` 真实入口已使用双 UUID 守卫外盘模型 bundle 后，清除项目目录内 3 个逐字节一致的模型副本；私有配置、数据库和 OCR 状态未改动 | 112,926,720 B | `SGR1_039_TERMINAL_EVIDENCE_8d4b43a6456bb5c30b6e7b0436e54f70c01a082cf03886f5f29cac6239a28df9.txt` |
| SGR1-043 | 无已安装或运行 PyCharm caller 时，将 4 个孤立运行组件目录迁至已验真外盘恢复根并清除内盘源；IDE 设置、workspace、任务历史与内部数据库全部保留 | 100,622,336 B | `SGR1_043_TERMINAL_EVIDENCE_R2_8d43404b781bd44477c9bbaf1464928ff4a03c5a69971cc29efcc1fe8af92bba.txt` |
| SGR1-045 | EasyOCR 两个官方模型迁至双 UUID 守卫的外盘模型根，官方路径与 login/interactive CLI 入口实跑通过并清除内盘默认模型根 | 100,483,072 B | `SGR1_045_TERMINAL_EVIDENCE_8be8873ae84ef1030368f20bbd9fdae4bc1df89e742f1c891e5d50da24b472b9.txt` |
| SGR1-047 | `spacy_pkuseg` 官方模型根迁至已验真外盘；保留已签名 Audio Clone 双 UUID 守卫入口，以细粒度默认路径链接完成离线真测并清除内盘模型负载 | 94,769,152 B | `SGR1_047_TERMINAL_EVIDENCE_4ccee9b9f6e8e0d731b1fac6b0a33232d7808f812b0d24f77cb6f56de0d06d76.txt` |
| SGR1-049 | `light_novel/workspace/indexes` 三类可再生索引迁至双 UUID 守卫的外盘项目数据根；七处生产读写入口统一 fail closed，真实离线生成与读取通过后清除内盘索引负载 | 83,165,184 B | `SGR1_049_TERMINAL_EVIDENCE_76192ac29bbd03ae0c5dc81cc23f334c6dfb33f1666e35c19018e58c1172d73e.txt` |
| SGR1-051 | 清除 Codex Desktop 8 月 14–27 日无句柄、无消息/令牌负载且早于冻结 cutoff 的 14 组历史诊断日志；当前线程、活动日志和 8 月 28–31 日现场保持原位 | 57,094,144 B | `SGR1_051_TERMINAL_EVIDENCE_168e7fdaf7cc86b9965259f8516052587217aed1a9363b8f05dd82ba23dece71.txt` |
| `storage_restart_2026_08_31_r1` closure | 冻结的 54 项全部终态：22 accepted、18 deferred、13 protected、1 accepted_excluded；本 epoch 释放 8,535,912,448 B，新增外盘分配 5,805,977,600 B，revision 5 authority 已消费 | 0（终局汇总，不重复计数） | `EPOCH_CLOSURE_REPORT_176c0387d4c5890dec2d4a25a49cfac8283ced45fd5c21fcaef06d1ec7675ebf.txt` |
| SGR2-001 | `manga-localizer` 真实 installed app 经双 UUID 门禁使用外置重型运行时；持久 clean-source 路由、fail-closed、rollback、精确 cleanup 与 `origin/main` 验收通过 | 848,822,272 B | `SGR2_001_TC2_TERMINAL_CANDIDATE.yaml` |
| SGR2-041-F | `light_novel` 精确清除 14 个无句柄 Python bytecode cache 与一个 pytest cache；私有生产数据、24 个 dirty 路径和既有外盘路由保持不变，剩余 root 按事实 blocker deferred | 5,718,016 B | `SGR2_041_TERMINAL_DEFERRED_WITH_CACHE_CLEANUP.yaml` |
| SGR2-043-F | `ai-anime-short-factory` 精确清除 7 个无句柄 Python bytecode cache、pytest cache 与 `test-results`；51 个 dirty 路径、媒体/metadata、治理证据和离线媒体 authority 均未改动，运行时 remainder deferred | 28,037,120 B | `SGR2_043_TERMINAL_DEFERRED_WITH_CACHE_CLEANUP.yaml` |
| `storage_restart_2026_09_01_r2` closure | 冻结的 45 项全部终态：1 accepted、29 deferred、14 protected、1 ineligible；本 epoch 真实释放 882,577,408 B，新增外盘分配 851,976,192 B，全部 Git、网络与存储 effect authority 已消费并释放 writer | 0（终局汇总，不重复计数） | `EPOCH_CLOSURE_REPORT.yaml` |

累计 accepted release：`26,992,914,432` B。当前状态只读 `STATE.yaml`。
