# Personal Control Hub：Linux 可视化工作台升级方案

读者：所有者与接手本仓库的 Run Agent。用途：保存阶段 0 的调查、产品合同、架构和接口依据；实现或接口事实改变时定点修订。调查日期：2026-10-03。这里的 **Hub 始终指既有 `personal-control-hub` 项目**。

## 1. 入口、权威与本轮边界

- 实际仓库：`/home/alalapi/Projects/personal-control-hub`；主机 `lab-root`，Ubuntu 24.04.5 LTS。调查基线 HEAD：`8fa0a4e53e00207823e3fb406ee04b94b77141a9`，分支及跟踪分支：`agent/governance-closure-20260812` / `origin/agent/governance-closure-20260812`。这不是一个新平台或平行仓库。
- 当前升级唯一执行状态是根 [STATE.yaml](../../STATE.yaml) 的 `linux_visual_workbench` 条目，稳定任务 ID `HUB-LINUX-VISUAL-WORKBENCH-V1`。其他条目属于其他任务，不恢复、不覆盖。文档中的基线状态只描述本次调查；后续进度、证据、失败和下一步只更新该条目。
- [ROUNDS.md](ROUNDS.md) 是任务卡和旧计划映射；[ACCEPTANCE.md](ACCEPTANCE.md) 是验收定义；[精简执行 Prompt](../../prompts/linux_workbench/EXECUTION_PROMPT.md) 是新会话入口。细节从这些文件按轮读取，不在 Prompt 中复制参数。
- 生效顺序：系统/开发者/当前用户指令 → 实际 `~/.codex/AGENTS.md` → 本仓库 [AGENTS.md](../../AGENTS.md) → 本任务材料 → 相关模块协议与子项目指令。当前用户明确允许本轮规划与后续另行启动的 Hub UI 升级；入口“不做图表/UI”是原数据治理工作的范围，不能抹去本次明确目标。**本轮仍不实施 UI**，也不启动 v3 数据治理。
- 根 `repo_protocol_standard.yaml` 只是旧协议指针；`project.yaml` 管身份和路由；`governance/round_state.yaml`、`data/state/current_status.yaml` 是历史/投影；不新增另一份 current state、STORAGE_MAP 或 Round 调度器。`all_projects_governance.route_version=2` 保持原值。
- 阶段 0 的风险通道为 DIRECT：只读调查、无推理的本机帮助/schema 探针、本仓库规划文件与当前任务状态。没有修改业务代码、外部项目、服务/网络/磁盘/账号配置，没有安装依赖、启动推理、部署、提交或推送。
- 写入归属：只新增本任务四个文档，给 STATE 增加本任务条目并向根 candidate_paths 追加这四个路径。旧 STATE 子树与其余已有文件逐项保留。原生任务列表显示本规划任务；另一 Hub 工作站任务的最近失败轮处理 APFS 问题，属于独立范围；视频项目有 active 任务。本次不控制或向这些任务发消息。
- Git：已有交付授权有任务范围，不能继承旧 Goal 的交付许可。阶段 0 留本地未暂存；后续按当前有效授权交付精确任务单元及现有跟踪分支，不自动合并 main，不强推，不改远端。

## 2. 最终产品合同与里程碑

Linux 运行唯一 Hub 后台、任务控制记录和执行协调；Linux 与真实 Mac 浏览器访问同一服务与同一数据。项目仍在原工作空间，子项目业务状态仍由其原有状态协议负责。Hub 提供主机状态、项目阶段/轮次/阻塞/验收、内容与产物、真实页面与设计审核。

必须保留的主流程：在 Hub 打开真实页面或明确标注的方案 → 整版选择或圈选局部 → 输入“改成什么”和“不改什么” → 服务端绑定项目、页面、版本、视口、截图、选区和请求 → **发送给 Codex 并执行** → 正确 Linux 子项目、明确 thread/turn、有关检查 → 新版预览与差异 → 用户接受或追加意见。浏览模式与标注模式分开；标注不触发业务按钮。截图失败有明确回退；没有源码映射就交给 Codex 核验定位。

分别呈现三类事实：主机/服务的观测状态，某个业务任务的执行生命周期，项目目标/阶段/Round/验收。Codex turn 状态与用户验收也单独保存。进程活着、提交次数、Agent 回复、文件数均不能证明完成或进度百分比。

- **M1：HUB-LWB-2.4**。一个实际获写入授权的真实网页项目，完成区域批注、一次小型 Codex UI 修改、真实检查、新版预览、差异与人工验收；真实 Mac 提交/查看，Linux 读取同一记录；独立托管后浏览器断连不终止任务，重连不重放；安全负例通过。mock 和双 Linux 浏览器不能代替这些条件。
- **M2：HUB-LWB-5.4**。M1 加安全的设计增强、第二个授权项目隔离、完整项目/产物/资源视图、可访问 Linux 既有空闲会话的明确读取与继续、忙碌会话安全追加或排队、运行恢复与故障回归，并满足验收矩阵全部必需项。未解决既有会话、真实 Mac 或人工验收门禁时只能报告部分完成。
- 阶段 6 飞书默认 DEFERRED，另需明确启动；复用同一任务、身份、权限、审批和审计，不能建立第二套系统。不阻塞 M1/M2。
- 排除：远程桌面/窗口鼠标同步、项目本体迁入 Hub、全库扫描、生产任务重启、自动发布/推送、网络/SSH/VPN/DNS/驱动重构、分区挂载、全局凭据/Agent 配置修改、大规模模型调用、所有项目 UI 重写、全量监控自动调度。

## 3. 现状与差距表

“文档声明”不等于“代码存在”，代码与 schema 不等于本次业务实测。下面每行的事实级别和检查范围必须保留。

| 调查项 | 当前事实与证据 | 复用 / 保留 | 缺口与对应轮次 |
| --- | --- | --- | --- |
| 前后端、启动 | 代码：`scripts/hub_server.py`、`src/hub/local_service.py`、`src/hub/web/{index.html,hub.js,designs.js,common.js,hub.css}`；Python 标准库 HTTP + 原生 JS/CSS。实测用户服务 active/enabled，回环 8766，根页/health 200 | 保留技术栈、当前项目/设计导航及已选视觉；不引入大型前端重建 | 当前服务只托管网页；后台任务执行器不存在。1.1、2.2、5.3 |
| 状态与数据库 | 代码及存在性：`data/connections/connection_refresh.sqlite3`，来源/刷新账本；`data/design_governance/design-store.json` 为真实设计库；`MetricStore(RefreshLedger)` 复用 SQLite 能力，不能当主机采集器 | 来源账本、原子保存、版本、幂等回执；不迁移现有库或复制当前业务事实 | 新任务记录无实现；可增加必要的 Hub 自有任务表，2.1–2.2；兼容原 schema，不复用业务指标表冒充任务 |
| 项目注册、路径与读取 | 代码：`connections.py`、`connection_sources.py`、`services/project_registry_service.py`；实测 metadata-only 校验 34 条/31 enabled，真实 GET 项目返回 34 条 | registry 只管身份/稳定路径，服务端解析 cwd。保留 `connection_read_allowed`、保护根、removed_local 拒绝及唯一来源 | README 的 24、旧 STATE 的 32 与真实 34 不一致，不能据此减掉项目；本任务只记录差异，1.1 修相关测试，4.1 核对展示 |
| profile / scan / summary | 文档 `docs/05_external_project_protocol.md` 定义三个资格开关及 watch_paths；校验器检查字段与覆盖。`project_scan_service.py`、`project_profile_service.py` 是 placeholder，不能称为 watcher/profile engine | 保留原字段语义；`enabled` 不等于读权限，更不等于写授权；`scan_enabled=false` 不自动否定明确的命名来源读取 | 不新增全仓扫描器。snapshot 用现有账本投影；按相关来源指纹检测变更，4.1 |
| 阶段、轮次、验收摘要 | `ProjectService` 读取 Hub 账本，区分 latest_attempt、last_success、fresh/stale/unknown；GET 不刷新外部根；业务字段保留来源及未知原因 | `hub.connection.yaml` / 共享 typed validator / 21 个业务字段与 `metric_sources.yaml` 适配器 | 多个实际项目仍“来源未提供状态”；不从文件或进程猜进度，4.1 |
| 内容和设计审核 | 代码：`DesignService/DesignStore/design_records/design_export`，immutable baseline/candidate/artifact、expected_revision、用户决定与导出；实测设计 API revision 15 / real，12 facts、2 history；只读页面有 Hub 与学习项目候选 | 版本、来源分类、过期、反馈、选择与导出；已有 A/B/C 或其他候选保留，不代选 | 页面是已绑定工件/链接审核；没有任意真实应用 iframe/区域截图/DOM 桥接/媒体浏览。1.3、2.1、3.1–3.2、4.2 |
| 审批、任务、日志、通知 | `scheduler_service.py` placeholder；`auto_advance_runner.py` 是只读检查/Prompt 预览，不执行 Agent；设计库的队列是决定投影；飞书是 disabled 本地投影 | 原 runner、真实/fixture 分类、已有来源审计及错误码 | 没有业务 TaskStore、独立 worker、Codex 事件/审批回传；2.2、5.2–5.3。队列目录/Prompt 不是运行中的 worker |
| Codex/ Cursor 接口 | 本机 `/usr/local/bin/codex`，`codex-cli 0.159.3`；`codex login status` 为 Logged in using ChatGPT。exec/resume/app-server 帮助及无推理 schema 导出成功。当前 Agent 的原生 list/read 可读任务摘要 | 使用既有合法认证，不读 auth 文件、不切换 API 计费。当前工具可见性仅证明本会话能力 | Hub 无执行 Adapter。schema 证明字段存在，不证明 thread 可访问/继续/忙碌注入/审批可用。2.2、5.1–5.2 |
| 双端入口与安全 | `HubHTTPServer` 仅 literal 127.0.0.1；Host/Origin 精确匹配；cookie/CSRF、体积限制、CSP。实测错误 Host/跨源 Origin 均 403。`/api/session` 目前只创建 loopback 浏览器会话，不是独立用户登录系统 | SSH/已有私网是访问保护层；保留浏览器来源边界，不直接开放 Codex 端口 | Mac 实机不可操作；端口不同的 SSH 转发会触发 Host 403。执行入口前还需明确所有者认证与服务端授权；1.2、2.2 |
| Linux、存储与监控 | 实测 Python 3.12.3、Node v24.21.0、SSH socket 22；Hub/Temp/Artifacts/Runtimes/Caches 当前在同一 ext4 `/`。`nvidia-smi` 可读 RTX 5070 Ti / 595.91.07、16303 MiB VRAM、温度及 encode/decode utilization；内存约 60 GiB，无 swap；该次磁盘余量约 479 GiB | 采集优先 `/proc`、可读 `/sys`、已有 nvidia-smi；零值是该次采样，不证明媒体能力/并发余量 | Hub 中未找到主机采集器。CPU 温度/I/O 未实测；外置存储映射未准入，不能假定 /Volumes 可用。1.3、4.3、5.3 |
| 基线与 UI 取证 | 见第 7 节。实机浏览器只读项目/设计页；控制台此次无 error/warn。源码里的纯逻辑 Node 测试可用 | `tests/test_hub_*.py`、两个 `.mjs` 测试、隔离临时 fixture、现有 docs/reports | 没有核验 Hub 的完整自动化视觉/故障套件；没有真实 Mac、写入操作或人工选稿验收。1.1、2.4、3.2、5.4 |

### 有界调查停止点与试点

只读 Hub 相关实现和注册数据，再只读两个候选的登记入口；没有遍历业务代码/素材/全部会话。两条 `game-removed-local`、`manga-removed-local` 不访问根；云任务排除。Manga 的旧失败及存储排除保持原状，不在本任务探查修复。

| 候选 | 已核验与限制 | 决定 |
| --- | --- | --- |
| `computer-study-plan` | 注册根 `/home/alalapi/Projects/computer-study-plan`；静态网页 + Python `scripts/progress_server.py`，默认 8777/progress.html；AGENTS/README/状态声明已有隔离 `tests/ui/`，本轮未启动该服务或运行子项目检查；当前端口清单中未监听 8777 | 首选**候选**，不是已选试点。小型布局修改可用真实网页闭环；实施前按其 AGENTS 读取必需入口、检查现有改动、确认精确授权及无重叠写者 |
| `linux-video-workbench` | 注册根 `/home/alalapi/Projects/linux-video-workbench`；原生 Python/PySide6 目标，HTML prototypes 是 mock 方案；状态为 LVW-MVP-20261003、R8 推进中，原生列表也显示 active | 最贴近典型场景但当前不适合并发写入；只展示方案/业务状态，不把原生 UI 冒充网页，不为集成改成另一套生产前端；需要可用网页/设计适配器及新的具体写入授权后再评估 |

这两个登记项 `external_write_allowed=false`。本轮只列候选，授权项目集合为空。当前视频项目任务和业务 worker 不因 Hub 开发而停止。

## 4. 架构决定

### D1：Linux 唯一后台与最少生命周期拆分

保留 Python/原生网页、registry、SQLite 刷新账本和 JSON 设计库。增加后端项目预览适配器、Codex Adapter、必要 TaskStore、一个独立 Linux 执行器生命周期；先由现有用户服务体系托管，不把每个模块拆成服务。监控是有采样时间的采集逻辑，与网页请求/Agent 任务分离。网页关闭或 SSH 隧道断开只影响客户端；任务接受后持久化，独立 worker 接管。配置/Prompt 本身不能托管工作。

既有库不被覆盖、重建或静默迁移。新任务存储复用现有 SQLite 技术，但有自己的明确表/兼容升级，不能把 source-refresh request 或 design decision 当 Codex job。TaskStore 是运行产品数据；STATE 是工程推进状态，二者不竞争。

### D2：入口、认证与信任边界

| 层 | 当前 / 规划关系 | 验证门禁 |
| --- | --- | --- |
| Linux Hub 本地 | 实测 `http://127.0.0.1:8766/`，同源 API | 保留 literal host、Origin、session/CSRF 及 CSP；执行开关默认关闭 |
| Mac Hub | 复用既有 SSH 连接，转发到 Linux Hub；优先使 Mac 回环地址及端口与后端匹配 | Mac 访问同一真实服务/账本并提交可读记录；只模拟转发请求不是实机 PASS；若本地端口冲突，用明确 allowlist 的受控入口映射，禁止通配 Host/Origin |
| 项目预览 | 注册 project/preview ID 对应独立预览来源；浏览器不能提供任意 URL/cwd | 逐项核对静态资源、路由、API、WS/SSE、CSP/X-Frame-Options、登录；不做任意 URL 代理；Mac 的子预览也须独立转发/登记入口 |
| 执行后端 | 只有 Hub 后端调用 Codex，stdio 不公开监听端口 | SSH 身份保护访问之外，执行 API 验证所有者会话及精确项目 grant；无 grant 拒绝；不能以匿名 session 发行证明执行认证完成 |

预览与控制界面分源。任意项目 HTML/报告不能进入高权限同源；先用图片/静态工件安全审核和区域标注，增强版桥接仅处理受控、版本绑定的只读消息。iframe、popup、浏览模式和标注模式按项目适配；不同时开启同源 DOM 权限与任意不可信脚本。跨源读取受浏览器[同源策略](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Same-origin_policy)限制；桥接按 [postMessage](https://developer.mozilla.org/en-US/docs/Web/API/Window/postMessage) 校验 origin、source window、nonce、结构和版本，不能使用通配目标或消息中执行指令。静态 HTML 报告使用 sandbox/下载/文本，媒体按需取流。

### D3：Codex 主路线与退路

没有已实现可复用的 Hub Codex Adapter。**主路线：后端封装官方 App Server stdio**，由 Linux worker 控制自己的连接，使用明确 thread/turn 标识和事件。能力发现/版本适配限制在 Adapter 内，浏览器不接触协议或凭据。官方称 App Server/WS 实验性，不能承诺生产支持；本地个人工作台先在隔离小样本验收再开放受控执行。SDK 不另建一条执行系统。

必要退路是同一 Adapter/TaskStore 内的 `codex exec --json` 与**明确 UUID** 的 `exec resume`，只能提供它已测能力：没有实时审批/steer 验证时采用已预授权操作、等待/排队；不能用 CLI 新 thread 冒充原会话继续。主路线不通过时，保留失败证据并切到受限闭环，既有会话需求仍留在 5.1，M2 不能被降格为纯新任务。

当前 Agent 的 `mcp__codex_app` task 工具可用于本会话只读确认归属，但不能假定外部 Hub HTTP 进程天然拥有这些连接器。禁止读取/改写 Codex 私有数据库、会话文件，或用模拟键盘控制已有窗口。禁止 `--last`、项目名猜 ID、设置绕过规则/权限的 flag。已有账号沿用 ChatGPT 登录；不修改登录、不切为 API key，不承诺订阅不限额。

新 Hub 自有 thread、旧空闲 CLI/App/IDE thread、其他进程中的忙碌 thread 分别验收。跨 App Server 实例不假定可 steer；未找到受支持的原运行实例控制机制时排队，等待精确会话 idle 后再受支持 resume。后台外部写者无法确认时，不产生第二个写者，显示阻塞及原因。

### D4：版本、截图、权限和单写入者

截图来自用户当时所见：优先浏览器明确许可的当前 tab 捕获；可控预览桥接可返回经过核验的当前视图截图。需要补充截图时只提供一个必要动作。后台重新打开默认页的截图只能叫“重建参考”，不能绑定成原选区。记录 CSS/device pixels、viewport、DPR、scroll、zoom、矩形、来源版本和截图摘要。截图失败回退到无图批注并标记，不自动上传整页/表单；截图送 Codex 前限于用户许可的选区/脱敏内容。

服务器解析 registry 的规范根/预览登记，检查不存在 symlink 越权/路径穿越；客户端只带 ID/版本。保存任务 request ID 与规范内容哈希：同 ID 同内容返回原任务，同 ID 异内容冲突；结果不确定先对账。项目/checkout 默认单写入者，以实际任务身份及具体文件重叠核对外部 Codex/Cursor，不把内部 lease 当成约束外部进程的万能锁。未知外部执行状态是一项真实限制。

registry 的默认只读策略继续有效。具体用户授权记录须绑定 project ID、规范根、允许修改范围、禁止项、有效任务、费用/权限条件和来源；新 TaskStore 的 grant 引用验证过的所有者授权，不由模型、截图或浏览器 payload 自授权，也不把 registered/enabled 升为写权限。授权检查与预览边界属高影响实现，执行时按 GOVERNED 进入必要独立审查；本规划不改变控制面。

### D5：资源、可靠性与存储

采样保存来源、时间、单位、支持情况、过期阈值；失联保存 unavailable/stale 而不是 0。CPU/cores、available memory/swap、GPU/VRAM/encode/decode、温度、授权文件系统余量/I/O、明确登记的关键服务逐项验证；不支持字段显示不可用。并发建议只使用已测任务画像的内存/显存/磁盘与计算瓶颈，未测就显示未知。

本机共享入口见已有 `docs/workstation/linux_workstation_manual_zh.md`：Projects、ProjectData、Artifacts、Runtimes、Caches；**这些入口当前并不证明外置盘**。1.1/5.3 按选定路径实际 `findmnt` 核对来源；外置 profile 缺失/UUID 不符拒绝写，不回退内盘。Hub 核心状态继续内盘保护位置。截图/日志/媒体容量和保留期在执行轮给出有依据的初始上限、低盘阈值与测量证据，不能无限增长或清理其他任务数据。不挂载 APFS，不探查原盘。

持久任务在 dispatch 前提交意图，之后绑定 exact thread/turn/worker。恢复先核对存活 owner 与已有请求，再续接/标记 lost 或 requires_reconcile；不得重发副作用。中断仅是中断，不是回滚或断点续算。开发预览独立进程、测试数据与端口，不能重启业务 worker。取消只作用于本任务确证的运行标识，回退仅用任务拥有的精确 preimage，保留他人修改。

## 5. 非生效契约样例

以下是领域结构样例，**不是现有 API/schema、不是可运行配置，也不包含默认授权**。2.1/2.2 按本机 schema/现有 validators 确定最终契约并实施测试，不能把示例原样写进生产库。

```yaml
annotation:
  project_id: registry_identity
  preview_id: registered_preview_identity
  kind: region_or_whole_design
  source_version: exact_code_and_dirty_fingerprint
  page: registered_route
  design_ref: optional_exact_candidate_revision
  capture: {status: not_captured, artifact_ref: null, digest: null}
  viewport: recorded_current_view
  coordinate_system: css_pixels_with_scroll_zoom_and_dpr
  target: region_or_element_hint
  requested_change: explicit_user_text
  preserve_scope: explicit_user_text
  task_ref: null
task_request:
  request_id: stable_user_operation_identity
  annotation_ref: exact_annotation_version
  project_grant_ref: null
  mode: new_or_resume_or_busy_feedback
  thread_ref: null
  expected_turn_ref: null
```

服务端推导根路径、actor/授权、权限、已允许的检查与预算，不接收浏览器 shell/cwd。事件至少关联 task/request、project/checkout、annotation/version、thread/turn、时间、事件去重标识、来源和失败类别。授权/版本变化不静默替换已有记录。

| 事实类别 | 建议状态及映射 | 不能推断 |
| --- | --- | --- |
| Hub 业务任务 | draft → queued → running；可 waiting_input / waiting_approval / validating / failed / cancelled / lost / requires_reconcile；checks_complete 后等待用户 | turn completed 不自动把任务标 accepted；服务存活不证明 running |
| Codex thread/turn | 保存实际协议的 thread status、turn ID/status、通知与错误；started/completed/interrupted 只映射该回合 | CLI/App 的记录可见不证明当前控制权；stream EOF 不证明成功 |
| 人工验收 | not_requested / pending / accepted / needs_changes / deferred，绑定实际版本与决定人 | 测试通过不等于用户满意；取消不是拒绝全部方案 |
| 发布 | 未授权时 not_authorized，独立记录 | 人工接受 UI 不自动授予发布、推送或重启生产 |

## 6. 官方接口记录与采用决定

查询日期均为 2026-10-03。OpenAI Docs 连接器按官方来源检索并读取下列页；获取内容中的索引/交叉链接已采用 `learn.chatgpt.com/docs/...`，原起点仍是 developers URL；连接器未提供 HTTP 重定向链，因此不伪造重定向状态。执行时只复核当前使用的部分及本机版本差异。

| 官方来源 | 已读取结论 / 本机证据 | 采用与限制 |
| --- | --- | --- |
| [App Server](https://developers.openai.com/codex/app-server/) | initialize/initialized，thread start/resume/list/read，turn start/steer/interrupt，通知/审批；本机 0.159.3 帮助与默认生成的 314 个 JSON schema 检出这些 methods。`ThreadResumeParams` 必需 threadId；`TurnSteerParams` 必需 threadId/expectedTurnId/input；`TurnInterruptParams` 必需 threadId/turnId；v2 bundle 含 localImage | 后端 stdio 主线，封装实验性变化；本轮未建立运行连接，未发 thread/start/resume/turn，不证明现有会话访问或图片执行成功 |
| [非交互执行](https://developers.openai.com/codex/noninteractive/) | 官方 exec JSONL 与 resume；本机 help 存在 image、json、明确 SESSION_ID | 同一 Adapter 内受限回退；不得用 last/新会话冒充 continue，审批/忙碌控制未验证 |
| [认证](https://developers.openai.com/codex/auth/) | 官方区分 ChatGPT 订阅登录与 API 用量计费；本机仅执行 login status 得到 ChatGPT | 沿用合法认证，费用与账户能力需真实检查，不读取凭据；没有无限配额结论 |
| [同源策略](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Same-origin_policy)、[postMessage](https://developer.mozilla.org/en-US/docs/Web/API/Window/postMessage) | 不同端口是不同来源；跨来源消息要验证目标和发送者 | 分源预览及安全桥接；不关闭浏览器安全/CSP/全局鉴权 |
| [SSH 手册](https://man.openbsd.org/ssh) | 本地转发可通过既有 SSH 访问远端回环服务；本机 SSH socket active | 复用连接，实际 Mac/预览转发未验收；不重建 SSH 或网络 |
| [Glances API](https://glances.readthedocs.io/en/latest/api/restful.html) | 阅读到 4.5.7 文档入口；当前未探测安装/采集端点 | 候选而非依赖，先复用本机采集；未安装、不开放网络 API，不以 Glances 替代 Hub |
| [Playwright 视觉比较](https://playwright.dev/docs/test-snapshots) | 快照依赖环境与可比较基线 | 3.2 固定浏览器/字体/视口/fixture；不通过更新基线掩盖差异；功能、视觉与人工满意度分开 |

本机 schema 输出有摘要定位：ThreadStart `e9c6d3cc…a9428`，ThreadResume `c818e26d…e093527`，TurnStart `2dfcf687…daf05a`，TurnSteer `857e7a2b…68ec78b`。完整 schema 来自临时目录并随探针清理，不复制大包作为新协议权威。后续用同版本生成器定向复核最终使用字段，真实握手/推理另在执行轮测试。

## 7. 本轮验证与保留失败

| ID / 命令或操作 | 状态、退出与实际范围 | 后续 |
| --- | --- | --- |
| B01 `python3 scripts/auto_advance_runner.py --mode check --task-id ALL-PROJECTS-CODEX-GOVERNANCE-V1` | FAIL / exit 1；agent_gate 整文件启动包 161051 bytes > 8192。YAML 未报解析错误。原因在 `agent_gate._check_default_boot` 使用整文件 stat，和“只读当前任务条目”不一致 | 本轮按用户明示的规划写入范围保存材料，不把 gate 改为 PASS、不执行产品/交付。1.1 修读取与检查的一致性，保留严格限额和 unknown-task 拒绝 |
| B02 `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_hub_*.py' -q` | FAIL / exit 1；174 tests，173 PASS、1 FAIL，3.624s。失败 `test_hub_registry_storage.RegistryStorageTests.test_metadata_only_is_explicit_and_never_probes_local_roots`：34 != 26。使用临时 Fixture/Spy/回环 HTTP；不启动 Codex、不写正式库 | 1.1 改为源自实际 registry 的基数检查并保留“零探测根”断言，不硬编码新的 34 |
| B03 `node --test tests/test_hub_web_logic.mjs tests/test_hub_connection_view.mjs` | PASS / exit 0；17/17。Node 报已有 MODULE_TYPELESS_PACKAGE_JSON 警告 | 相关 JS 变更时回归，不为本警告额外改 package 模式 |
| B04 `python3 scripts/check_registry.py --metadata-only` | PASS / exit 0；34、31 enabled、零 blocker/warning，path availability false | 不证明所有项目路径可用；不运行全体本机路径扫描 |
| B05 Python/Node/Codex help、login status、schema 导出 | PASS / 相关调用 exit 0；314 schemas、所需 methods/必需字段存在；临时 schema 随探针清理 | 本轮无真实推理、运行权限/项目指令加载验收、live thread 控制；2.2/5.1 |
| B06 实际 HTTP 只读与恶意来源 GET | `/`、health、session、projects、designs 各 200；projects 34；designs real/revision15/facts12/history2；错误 Host 与跨源 Origin 各 403。session/cookie/token 不打印、不留凭据 | 未 POST 刷新/决定/导出；既有 API 安全局部证明，不证明未来执行 API |
| B07 实际浏览器只读 | 项目页 34 条、2 来源需关注；设计页 Hub/学习项目各一个已决定候选，截图仅观察，未更新截图基线/保存真实业务截图；本次 console error/warn 为空 | 有可读 UI，未执行功能修改/人工决定、网络全量或 Mac 验收 |
| B08 用户服务/SSH/文件系统/GPU 只读 | service active/running/enabled；SSH 22；ext4 内盘与有限采样如现状表 | 进程状态不证明重启恢复、长时采集、媒体编解码、并发或 Mac 可达 |
| B09 `npm run check:mcp` 与完整 pytest/UI 故障套件 | NOT_RUN；MCP 检查的旧记录缺 `.cursor/mcp.json`，本轮没有重跑或配置；不依赖它证明 Hub/Codex 主路线 | 若选用该依赖才定位检查；不伪造项目 MCP 配置 |

两个失败指纹为：`B01/HEAD8fa0a4+dirtySTATE/boot_packet_oversize/whole_file_stat`、`B02/registry34/hardcoded_count26/unittest174`。不修业务内容来躲测试，不删除旧状态或降低门禁限额。未知版本/相关输入变更时只重验受影响部分。

## 8. 阶段授权、预算与交接

当前只完成阶段 0。新执行 Agent 收到本项目精简 Prompt 后，才进入 Hub 的阶段 1–5 实际工程；这一未来启动授权不替代外部项目具体写权限、所有者验收、真实 Mac 操作或高影响审查。阶段 6 保持未启用。

没有为本任务指定数值预算；不继承 STATE 中别的 Goal 巨额历史 tokens、auto_followups、quota。执行时核对本会话真实预算/限额；仅按可获得 telemetry 记录覆盖、token/用量，未知写未知；不擅自增加 quota，不启动无界付费调用。预留一次相关验证与收口所需空间，不够就保存同一 STATE 的下一动作。Goal/Loop Skill 与 runner 不等于后台调度器，也不承诺会话结束后继续。

Run 循环：当前任务 STATE → 依赖合格的最小任务卡 → 建立归属/按风险通道和检查 → 实施 → 定向验证/必要修复 → 证据与差异 → 同一状态和索引 → 当前授权下精确 Git 交付 → 下一合格轮。相同失败两次无新证据即诊断/换法；结果不确定先对账。支线软阻塞可转独立授权轮次，必需项不能跳过计 PASS。当前全局要求的 GOVERNED/REVIEWED 角色使用真实独立只读角色；不能加载就保存该边界，不模拟审批。

终止：M2 必需项真实通过并完成适用交付；所有合格支线耗尽而硬依赖缺失；用户停止；或预算不足以验证/收口。阶段 0 当前会话在交接一致性检查后停止，**不进入 1.1 实施**。
