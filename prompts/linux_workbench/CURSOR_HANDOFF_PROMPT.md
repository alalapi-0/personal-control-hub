# Cursor 接手 Hub 可视化工作台：Goal + Loop

这是所有者给 Cursor 的执行交接。工作目录固定为 `/home/alalapi/Projects/personal-control-hub`。实际实现、运行、验证和交付既定工作台，推进到原定义 M2；不要重新规划后停止，不要缩小目标来宣布完成。Codex 此次只准备交接，不在 Cursor 接手期间继续写同一任务。

## 启动同一个目标

在同一 Cursor Agent 聊天分两次发送，两个入口读取同一文件、同一 STATE，不创建第二个执行任务：

```text
/goal 请完整执行 /home/alalapi/Projects/personal-control-hub/prompts/linux_workbench/CURSOR_HANDOFF_PROMPT.md，从当前真实状态接手并推进到 M2，先交付已有修改，再按每轮提交规则持续实施。
```

```text
/loop 持续推进同一聊天里 CURSOR_HANDOFF_PROMPT.md 定义的已有 Goal：读取 STATE 的唯一下一动作，完成最小合格轮次、验证并交付 GitHub main；到 M2 真实完成或必要人工/权限硬阻塞时停止，不创建重复任务、并行写者或第二个目标。
```

核实当前聊天 `/goal` 和内置 `/loop` 的真实能力、运行标识与状态，报告两者是否实际启用；收到文字不等于启动。原生 Loop 只唤醒同一 Goal，不能与正在执行的轮次重叠；不指定机械间隔，让它依有效事件/完成边界唤醒。停止、硬阻塞、额度耗尽或完成后停止本任务 Loop，避免反复空转；用户未回复不是授权。

本机已有 `~/.cursor/skills/goal-mode/SKILL.md` 与个人 Goal Hook。原生 Goal/Loop 活跃时不叠加个人 Hook，不修改全局 Hook/配置或另建 cron、watcher、调度器。原生能力缺失时先尝试新的 Cursor 聊天；仍缺失就如实说明，保留在当前会话可执行的工作。只有确认无原生继续运行者时，才依现有 Goal Mode Skill 使用其有限继续能力；每次显式激活最多30次自动触发，最后一次仅NEEDS_USER收口，不能冒充原生Goal+Loop或无限运行。

## 接手入口与原验收

先读生效的 Cursor/全局/项目 AGENTS（包括适用的 `~/.codex/AGENTS.md`）、`STATE.yaml` 的 `linux_visual_workbench`，然后按当前轮读取 `docs/linux-workbench/{PLAN,ROUNDS,ACCEPTANCE}.md` 的任务卡、依赖、授权、环境、检查和回退。按必要字段分段读取，不每轮重读全仓或巨大历史。STATE 是唯一执行事实，registry 是项目身份；不另建任务状态机，不修改其他 STATE 条目。首次写入前运行：

```text
python3 scripts/auto_advance_runner.py --mode check --task-id ALL-PROJECTS-CODEX-GOVERNANCE-V1
```

该 runner 只检查，不授予动作权限。候选路径登记在当前任务 candidate_paths。依据 DIRECT/REVIEWED/GOVERNED 选择最低适用风险通道；普通可逆实现自主执行，高影响认证/安全/控制平面按已有合同和独立角色审查，不自审、不模拟独立通过。需要的 Cursor 角色不可用时保留该具体边界，不逐文件询问。

Hub 是现有项目，复用 Python、原生网页、注册/来源账本及设计审核。Linux 唯一后台，Mac/Linux 浏览器共享任务、批注、审批和结果。必须贯通：真实预览或方案 → 整版选择/区域批注 → 修改要求及不改范围 → 发给 Codex 执行 → 正确子项目和明确会话 → 实际检查/差异/新版预览 → 用户验收或继续反馈。资源、项目阶段/轮次/阻塞及授权产物视图必须有真实数据，不能退化为链接页。

产品后端仍主走 Codex App Server；CLI 仅同一适配器内受限回退。Cursor 是接手实施者，不因此把产品 Codex 执行链改成另一系统。沿用合法现有登录和费用边界，不切 API 计费、不购买额度。新建、真实既有空闲会话继续、忙碌追加或安全排队分别实证；不猜 ID，不读写私有会话文件，不启动竞争写者。浏览器断开后任务可对账，不盲重放；预览无执行权限。

M1/M2 与 A01–A13、A08 三子项全部按原文验收。真实 Mac、用户对明确版本的接受、第二授权项目、真实 Linux 旧空闲会话、忙碌处理和适用交付不能由 fixture/文档/测试替代。阶段6飞书仍未启动。

## 第一步：先提交已有修改到 GitHub main

所有者在 2026-10-08 明确要求并授权：Cursor 接手后，先完成一轮现有未提交工作收口、提交并交付 GitHub 主分支；之后每个有实际变更的实施轮次均提交到主分支，再继续下一轮。无需再次询问是否提交。此授权覆盖本任务已验证、归属清楚、可交付的修改；不是全仓任意数据公开许可。

目标是既有 `github.com/alalapi-0/personal-control-hub` 的 `main`。交接时本地分支 `agent/governance-closure-20260812`，HEAD `8fa0a4e53e00207823e3fb406ee04b94b77141a9`，38个tracked文件修改、73个untracked顶层条目、index为空。保存的 origin/main 为 `b2a3e090167678bfeb24c80f72eb7b7cb44a2c78`，相对该旧引用当前分支独有1个commit、远端独有153个；这些是本地快照，接手先核实，不当作最新远端。

先确认精确任务/文件所有权、当前差异与必要依赖，刷新远端后选择安全集成方式。不要直接把这条落后且分叉的分支推 main，不盲目 pull 到巨大 dirty tree，不 reset --hard、强推、改历史或整树 stash。必要时用临时隔离 worktree/`codex/`集成分支把本任务提交合入最新 main；保留原工作区、其他任务及其 dirty，不能擅自带入当前分支唯一旧提交。

仅 stage 审核过的具体文件/片段。STATE/registry/账本等共享文件按本任务实际归属处理；`metric_sources.yaml` 已有其他授权工作的变化，不能归为本任务。保留他人改动，无法确认的部分暂留并说明，继续交付独立可验证部分；不以 `git add -A` 掩盖归属。不得提交凭据、账号/会话运行文件、live SQLite、WAL/SHM、锁、私密日志或未审查的图/数据；必要运行数据留本地，只交付安全可复现摘要/fixture。

对初始交付运行相关检查、复核精确 staged diff、受保护内容与秘密排除，再正常提交、推送/合入 main并核对远端精确 SHA。主分支需要 PR/CI/审批时遵循保护策略，完成正常 PR 路径；不能绕过保护，未合入不得报 main 已交付。GitHub认证只沿用合法本地机制，不输出/复制token或修改全局凭据。初始提交如实标注“工程进展，M1/M2未验收”，不等待 M2 才保存工作。

以后每轮：实施 → 相关验证和修复 → 复核差异 → 更新同一 STATE/INDEX → 提交并交付 main → 核对远端 → 下一合格轮。无代码/文档变更不制造空提交；硬阻塞轮可交付安全的事实更新，不把失败候选宣称产品完成。不重问已批准的提交授权，不操作无关仓库；子项目 Git 授权依其具体范围，不能从 Hub main 授权推定。

## 真实恢复点与视觉反馈

媒体迁移交接窗口：原媒体 Codex Goal `01a113b8-2626-7a62-8973-b21daf4c7057` 已 PAUSED，剩余执行交给单独的媒体存储 Cursor Goal，交接入口为 `prompts/workstation/CURSOR_MEDIA_STORAGE_HANDOFF_PROMPT.md`。该接收任务尚未启动，不能称为正在迁移；它接手后独占此前释放的 `docs/reports/linux-workbench` 中现有25张生成截图及相关 capture/fixture 输出路径和引用组，Hub业务Cursor不同时取得该组。该父目录混有 INDEX/文本，不能整体盲搬；`data/workbench`、数据库、输入、账号、依赖、试点业务候选不在媒体目录交接范围。入口包括 `tests/lwb_real_preview_fixture.py`、`data/workbench/csp-css-trial/capture_trial_preview.py`，及 `src/hub/css_trial.py` 的 IMAGE、`src/hub/readonly_preview.py` 的注册预览引用；只交接媒体输出定位/引用部分，不扩大安全守卫或执行语义。

本 Hub 任务继续停止这组截图/capture路径写入。Hub业务Cursor先读 `STATE.linux_visual_workbench.cursor_handoff.media_write_window` 与 `STATE.media_apps_storage_goal.cursor_handoff`，等待媒体存储Cursor完成该具体范围并明确释放，而不是等待已暂停的Codex继续执行。当前Hub25截图仍未迁移；Creator已迁135PNG，但其路径候选仅类型检查通过、未验收未提交，不是本Hub任务提交范围。释放前不捕获、覆盖、移动、stage或改写同组图片/产出源码，其他独立工作可继续；依赖该组的完整提交单元一并等待，不能漏掉必要依赖来凑首轮提交。不要覆盖媒体任务的新路径；完成后增量验证来源、引用、截图对应及受影响候选身份，不能取消路径/哈希/只读守卫或把旧证据冒称当前通过。媒体存储Cursor只更新其STATE条目，Hub业务Cursor只更新本工作台条目。

当前 HUB-LWB-2.3 未闭合；累计去重16个闭合轮（3规划、13技术），M1/M2仍 NOT_RUN；计数和失败历史不因换 Cursor 清零。5次metadata helper、2个原生线程、4次resume、1次模型turn已记账，不能重放。

学习项目 `computer-study-plan` 只获精确 CSS 试点范围；原课程、学习数据、HTML、JS、API均保护。试验副本中只把任务卡边框透明度 .15 改 .24，原业务 CSS 尚未推广；真实新旧桌面/窄屏四图已保留。所有者最新反馈“新版看不出区别”，不是视觉接受。后续在有效授权内选择能一眼比较的小样本、展示改前改后及可见变化，保留已喜欢的整体风格；不能将细微测试当作设计交付，也不能代替用户选版。真实图见 INDEX 的试点记录；不要重建截图后冒充原批注视图。

当前已通过离线审查的精确候选：

- `data/workbench/csp-css-trial/config-precheck-contract-v11-7.json`
- `config-precheck-registration-v11-7-r1.json`，candidate `862215cc3e95df74310e2a913b233e5fd02ef63a0d77fa96a2dc255d8c63ef96`
- evidence `23922c677f72e731cb38f16b9dc2d1594fc80370ee1524603052033615383f22`
- 同目录 `config-precheck-review-v11-7-r1.json`、`config-precheck-governor-decision-v11-7-r1.json`：fresh Judge PASS、Governor仅批准离线准备；65针对性/454Hub检查通过不等于metadata实机通过。

上述路径除首项外均在同一目录。核对现版本/候选及相关保护项，只对变化部分增量验证；之前实机失败和首次认证基线缺口已留证，不以重新命名、新会话或新权限窗口重置一次效果。

首要硬阻塞是官方 SDK 的 metadata 读取可能 OAuth/provider 刷新并非原子更新官方凭据存储。当前明确效果授权未收到，authority/intent/result未创建，不能先运行 CLI/login/helper 再补权限。这不是 Linux sudo 权限问题。先准备可审核提案，只请所有者确认一次该精确效果；获授权后仍需 Governor对同一候选和当前保护项一次激活。样本最多120秒、不续期、不自动重试；20小时窗口不延长该样本期限，不增模型/线程/resume/工具执行/TaskStore/CSS推广权限。

## 提前预检与独立20小时窗口

所有者已选择20小时，Cursor接手时先进行相关阻塞预检、准备认证/授权动作，再进入长执行阶段；不要重复问时长。检查实际缺少的是操作授权、平台审批、GitHub登录、官方SDK效果、root能力，还是Mac/人工验收；分别处理，不能用sudo解决全部阻塞，也不能以窗口开启计通过。

确实需要管理员操作时读取 `~/.codex/skills/linux-admin-window/SKILL.md` 及它的 backend-qualification/本机事件参考，先复用可信后端已经有效且覆盖具体操作的能力。没有覆盖能力时，在具体必要操作的权限/治理范围内准备合格受限后端；确认支持固定20小时、硬到期拒绝、最小固定命令/参数/资源、可信只读status与撤销。没有合格后端不能虚构开启命令；普通Hub工作无需root，继续普通工作。20小时选择不授权永久sudoers、任意root shell或安全绕过。

仅在后端和精确命令已核实、必要授权完备后，给所有者一个单独本机Terminal中可直接执行的命令，由所有者自行输入一次密码，提前执行/验证；不要先让人认证再发现后端不支持。Agent不启动索密GUI/pkexec/askpass，不接收密码。所有者保持该Terminal打开；Cursor随后必须从其真正执行环境通过非交互status/精确能力验证可用性、跨TTY适用范围、boot ID、版本、硬到期及撤销状态，并保存无秘密的事实到同一STATE。到期、撤销、重启或状态不明立即关闭相应提权路径，不续期。

Terminal开着或一次 `sudo -v` 不证明固定20小时/跨TTY能力；禁止sudo保活循环、全局免密或常驻root shell。20小时是经验证授权能力的寿命，不是任务、费用或Goal/Loop的无限预算，不保证网络/桌面/订阅不会中断。当前尚未验证合格20小时后端；本交接未开启窗口。

## 持续实施和收口

保护原数据、他人工作、现有服务和账号运行时；跨仓写入/验证/交付串行。先小样本再真实闭环；开放执行前认证、服务端项目授权、路径/命令边界、版本、幂等、审计和托管均需通过。两次同态失败没有新证据就诊断/换法，不重复本轮全仓扫描或审查已不变内容；先核实未知效果再重试。

仅必要权限/凭据/破坏性效果/不可绕过的真实验收依赖是硬阻塞，先完成独立授权工作；需要人时一次只给一个必要动作及预期回传。真实进展、正在轮询的确切活任务、无进展分开记录；没有活句柄的“等授权”不是verified wait。原生Goal被blocked后在Cursor接手视为新blocked审计，不改历史状态、不伪造Codex仍在执行。

每个既有收口点用 `~/.codex/skills/goal-progress/SKILL.md`：报告实际变更、测试/证据、当前阶段/轮次、已交付远端SHA、遗留和唯一下一步；实现/实机/人工验收/交付分开，测试/修复/审查不另计新轮，异质验收比例不伪造。保留历史累计预算及覆盖范围，按Cursor实际额度预留验证/收口空间；到期限/额度保存恢复点，停止本任务继续机制，不承诺会话结束后后台运行。

仅原M2全部必需验收、真实Mac/两项目/旧会话/用户接受及适用main交付确实完成时关闭本任务Goal和Loop。核验不全就报告partial，不能把工程提交当M2。

能力依据：Cursor官方 [Goal与Loop配合](https://cursor.com/docs/agent/overview#goals-with-goal)、[内置Loop](https://cursor.com/changelog/shared-canvases)。本机安装包读到Cursor3.23.12、tmux存在；CLI `agent`/`cursor-agent`不在当前PATH。安装/文档/Hook文件存在均不是当前聊天实际激活证明，不据此安装或改全局配置。
