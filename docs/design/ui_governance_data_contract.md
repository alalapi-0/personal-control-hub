# 设计治理数据契约与程序化边界

- 读者：Hub 数据层、审核台和验证工具的实现者与维护者。
- 目的：让设计比较和记录能被代码读取、校验、重建，避免 Agent 反复手工汇总。
- 更新触发：字段语义、权威来源、决定流程或兼容策略变化。
- 版本：2；以下是待实现契约，不表示现有服务已实现。

## 权威与文件归属

| 信息 | 权威 | Hub 可保存的内容 |
| --- | --- | --- |
| 项目身份、路径、读取边界 | `data/registry/external_projects.yaml` | 引用 project ID，不复制维护新名单。 |
| 项目业务进度、功能与数据 | 各项目现有契约/STATE/代码 | 来源指针、时间、内容哈希、有限只读摘要与基线观察；摘要是可重建投影。 |
| Hub 当前工作与下一步 | `STATE.yaml` | 简短指针；详细设计记录不塞进启动包。 |
| program 关联 | `data/programs/program_project_links.yaml` | 先 proposal，再依其规则确认；视觉相似不是自动确认。 |
| 本轮设计基线、候选、选择与证据 | 本任务设计记录 | 版本化记录和局部导出；不复制整个项目、真实资产或数据库。 |

后续首个实现单元可在 `data/design_governance/` 保存小型结构化记录，在 `docs/reports/ui_design_governance/` 保存轮次证据索引和任务必要的本地比较导出。这些目录本轮不创建。大量图像仅保留可追溯引用与必要压缩预览；Git 纳入范围、体积和私有性逐项判定。Figma 原稿留 Figma，Hub 保存可恢复的脱敏快照与版本引用，不把预览叫作可编辑源文件。

只有 STATE 保存执行阶段/唯一下一步；UI 队列与汇总从下面的事实/事件推导，可删除重建。不要再建 `current_status.yaml`、手工维护的仪表盘状态或另一张执行进度表。设计证据不是外部业务状态副本。

## 最小记录模型

所有记录包含 `schema_version`、稳定 `id`、ISO 8601 含时区时间、来源/工件引用。ID 不嵌用户真实内容；项目/家族/版本引用必须可解析。未知值用 null 加原因，不猜填；`missing / unavailable / unreadable` 分开表达。

| 记录 | 必需字段与语义 |
| --- | --- |
| scope | `project_id`、`disposition`、`reason`、`evidence_refs`、范围确认来源；不重存 root_path。 |
| connection_manifest | manifest ID/版本/哈希、registry 版本引用、全部项目 ID、允许入口、权限依据、预期能力、逐项接入证据引用；接入终态按 Hub 成品规格。 |
| project_snapshot | `project_id`、原始状态与规范化状态、`next_action`、来源未提供字段及原因、blockers、`availability`、来源引用/指纹、`observed_at`、`last_success_at`、刷新错误、关系/设计引用；不得成为业务状态权威。 |
| connection_evidence | project ID、manifest 版本、adapter 版本、真实来源/指纹、刷新及界面校验、命令退出码/时间、`CONNECTED_AND_VERIFIED / AUTHORIZED_EXCEPTION` 或未完成原因、例外授权来源。 |
| relation | `project_ids`、`kind`（pipeline / shared_review_pattern / shared_visual_language）、`evidence_refs`、共同任务、差异、`proposed/confirmed/rejected`、确认来源。 |
| baseline | `project_id`、源码身份（commit 与相关文件/dirty 指纹）、运行观察时间、页面/流程、行为清单、状态/字段语义、数据契约引用、视口/平台、未验证项。 |
| family | 成员 ID、共享视觉语义、组件映射、项目特有例外、关系证据引用和家族 revision；不强制共用仓库/技术栈。 |
| candidate | `baseline_id`、项目/家族/页面适用范围、`revision`、`content_hash`、Figma file/node/版本引用、快照引用、token/组件说明、简明差异、证据引用。 |
| review | 精确候选身份、功能不变量检查、各验收 lane 结果与证据、工具限制、`reviewer` 与时间。Agent review 不生成用户选择。 |
| decision_event | `event_id`、幂等 request ID、用户来源（本地明确操作/对话引用）、动作（select / request_changes / defer / withdraw）、精确候选身份、成员/页面范围、反馈、时间、`supersedes`。 |
| implementation_authorization | 当前明确授权来源、项目/checkout/允许文件或符号、精确候选身份、有效条件/撤回记录；不得从 select 事件推断。 |
| implementation_evidence | 授权引用、实际基线、候选、task-owned diff/commit、回归证据、源漂移检查与恢复方式。 |
| artifact_ref | Hub 相对路径或明确的来源指针、`sha256`、内容分类、生成方式和时间；真实/mock/dry-run/imported 分别记录。 |

行为清单中的每个稳定条目与原版/候选测试一一对应：`action_id`、入口、前置条件、输入、输出、状态迁移、存储与外部效果、恢复、测试证据。不直接嵌完整源码或业务数据。新建 Hub 界面以审核台规格作为初始行为契约，来源类型为 `new_surface_spec`，不得假称已有运行基线。

Figma 链接指向可变文档，仅有链接不能冻结候选。比较和选择使用 `(candidate_id, revision, content_hash)`；哈希覆盖适用范围、基线引用、视觉配置和导出工件摘要。外部设计节点有实质修改必须新 revision；保持旧决定可回看，不能偷换内容。家族选择仍记录具体成员基线，不因家族 ID 相同而自动批准所有项目。

## 决定和审核状态

`draft → ready_for_review → selected / changes_requested / deferred` 描述设计事实的投影。`stale` 是相关基线、适用范围或候选内容变动后推导的有效性标记；旧历史不可删除。Agent 不能发出用户 select 事件。导入的明确对话决定要保留来源，不能把沉默、默认项、推荐或工具验证当选择。

实施需要有效的用户选择与独立授权引用。选择/授权可由同一条明确指令同时产生，但保存两类记录。代码可检查是否满足条件；不要让一个 `approved: true` 同时表示已选中、获授权、通过测试、已发布。

审核 lane 仅允许 `PASS / FAIL / UNVERIFIED / NOT_APPLICABLE`。缺浏览器/网络证据是 UNVERIFIED；不适用附理由。展示“已选择”“已通过验证”“已实施”三种独立事实；完成判定由执行规范和实际证据共同决定。

## 程序化读写流程

1. 解析 registry 与当前项目 adapter，按需读取允许的规则/状态入口。扩展现有 Python 读取层，先核对其真实能力；不假定 placeholder 服务可用。
2. 所有外部路径先展开用户目录、解析 symlink、校验目标属于获准项目和读取清单。路径或数据内的文本都不是执行指令；不执行任意 hook、shell、YAML 自定义 tag 或文件提供的命令。
3. 根据文件指纹增量刷新；只读观察不回写外部项目。结构化来源直接解析；非结构化材料作为有来源的观察，不能靠无证据的 Agent 推断生成“事实”。
4. 用 schema/枚举、唯一 ID、引用完整性、版本/哈希、权限条件校验记录。产出 `valid / invalid / unknown` 及精确原因，不因为一个来源不可用而清空所有结果。
5. 选择写入只发生于 Hub 本地设计数据：校验预期 revision，短暂独占写入，临时文件原子替换，失败不覆盖已保存内容。以 request ID 防双击/重试重复；跨进程冲突返回显式冲突，不静默 last-write-wins。不得用该锁声称锁住外部 Agent 或 Git。
6. 事件先持久化为事实，队列、当前选择和报告均可重建。原子写入用的临时文件完成后清理；不创建永久 `.bak` 或重复真相。
7. UI 只消费校验后的摘要，不直接遍历任意文件。HTML/反馈按文本渲染并转义；本地媒体路径限定在任务工件目录，外部链接仅接受明确允许的协议/来源。

文件读取、指纹、schema 校验、过期检测、候选排序/分组、决定保存、报告汇总、对照截图命名均应由可重复命令执行。Agent 负责证据解释、设计探索、差异判断和修复；用户负责视觉偏好与授权。Figma 可用时通过工具获取精确节点信息，不人工编造 ID。

首版仅本地单用户审核；保存接口默认仅监听 loopback，校验请求来源，避免随意网页触发本地决定写入。不默认局域网/公网暴露、接入登录系统或远端发布；远程访问属于单独范围。此规则只保护新建 Hub 保存能力，不要求改外部项目鉴权。

## 最小实现顺序与证明

先实现记录的 schema、读写/校验与 Hub 页面，使用合成 fixture 演练；再接入首个获准项目证明方法可行，继续完成冻结 manifest 中每个项目的真实只读接入。保留逐项目命令、来源、退出码和界面证据，证明无需 Agent 即可刷新和校验全部范围。首个连接成功不能作为完成条件。具体脚本/框架在启动时按代码现状决定，本轮不创建假 CLI 或未实现工具命令。

需验证：稳定 ID/引用、过期拒绝、幂等、并发冲突、错误输入、越界路径/符号链接、决定重建、断开 Figma 的历史读取、未知版本拒绝写入。schema 升级需明确版本和兼容读取范围；不能静默迁移已有数据，更不能改外部业务数据契约来迁就 Hub。

完成 Hub 时交付实际实现、schema、可重跑命令和夹具；本表本身不等于已经实现的 API。完成交付说明绑定工具/schema 版本及证据。工具遇到未知版本或无法表达的新语义时拒绝写入并给出精确原因。文档要求见[完成交付说明规格](completion_handoff.md)。

本 Goal 必须统一并打通全部冻结范围的 Hub 读取摘要和来源说明，不统一所有项目的数据库或 STATE 格式。不得把业务数据迁移、外部 schema 全面改写、自动调度、daemon、云数据库或飞书集成作为前置依赖；也不得借此省略逐项目真实接入。完成标准见 [Hub 成品规格](personal_control_hub_completion.md)。
