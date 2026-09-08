# 项目状态统一入口

读者：只从 Personal Control Hub 接手的 Agent，以及负责维护项目状态源的 Agent。
更新条件：接入契约、读取命令或错误语义变化时更新。业务事实始终在项目自己的唯一状态源中。

先读 Hub 的 `AGENTS.md`、`STATE.yaml`。运行以下只读命令取得所有登记项：

```sh
python3 scripts/hub_connections.py current
python3 scripts/hub_connections.py current --format markdown
python3 scripts/hub_connections.py current --format feishu
```

上述命令只读 Hub registry 和本地账本，不访问项目根、不执行项目验证命令。
完整输出的 `projects` 包含每个稳定 `project_id`；`coverage` 给出完整分母、现存本地、已移除及当前成功读取数量。
当前登记是 26 条：24 个现存本地项目和 2 个 `removed_local`。17 个云项目已按所有者要求排除。
接入模板本身交付不代表 24 个项目已经完成声明落地；缺少声明的项目保持 `missing_declaration`，后续逐项目治理轮处理。

## 正常刷新与恢复

在当前任务已授权的范围内显式刷新；命令只读项目来源，在本 Hub 写入账本及可重建预览：

```sh
python3 scripts/hub_refresh.py refresh --request-id governance-check-001
python3 scripts/hub_refresh.py validate
```

中断后重用完全相同的 request ID，已经提交的项目不重复读取。重新检查变化的来源时使用新的 request ID。
如只刷新一项，加 `--project-id PROJECT_ID`；输出仍保留全登记覆盖，其他项保留原读取时间。
默认账本 `data/connections/connection_refresh.sqlite3` 和预览 `data/connections/preview/` 不进入 Git。
`validate` 验证账本结构、不可变记录与投影；未初始化会明确返回 `ledger_initialized: false`，不代表接入成功。
SQLite、JSON 或来源错误必须按具体原因修复，不能删除失败项、改成成功或重置旧请求身份。

## 读取语义

每行都保留 `latest_attempt`、`last_success`、`freshness`、`update_key`。

- `latest_attempt` 是最近读取事实，包含明确 disposition 和 errors。失败时 business 全部为 null。
- `last_success` 是最近成功读取的历史快照。新失败、来源授权变更或读取证据过期时，必须将其作为历史资料；不能当作当前通过证据。
- `freshness.state` 是 fresh、stale、unknown 或 removed_local。读取时间和源修改时间分别保留，源文件很久没改不自动等于业务停止。
- `business.current_work` 表达目标、阶段、轮次、状态、completed、accepted、下一步和负责角色。
- `business.progress` 保留完成项、总项、口径或里程碑。没有分母不计算百分比。completed、accepted、delivery.status 是三个独立事实。
- `business.blockers` 表达原因、受影响项、恢复条件、可用入口、是否等待用户。null 是未知；只有来源明确空列表才表示无阻塞。
- `business.verification` 与 `business.delivery` 是项目源声明的验证/交付事实。Hub 不因成功读取就授予验收，也不把源声称的 delivered 当成新做过的 GitHub 复核。
- 每个非 null 业务字段必须有 `field_provenance`，其中包含来源 ID、相对路径对应的 SHA、精确选择器及原值；每个 null 字段必须有 `unknown_fields` 原因。

`missing_declaration`、`missing_source`、`offline`、`permission_denied`、`invalid`、`unsafe_path`、`authority_drift`、`disabled` 都保留项目行。
`removed_local` 只使用登记事实，任何激活标志都不会触发根路径解析、文件读取、写入或重试。
漫画业务状态读取有当前所有者授权的独立路由；存储检查、迁移及清理排除继续保留。
登记中显式 `connection_read_allowed: false` 优先拒绝读取；未设置时才使用 enabled。
`.codex`、`.cursor`、`.ssh` 等保护根及指向它们的别名不能成为项目状态来源；auth、token 等常见凭据文件名在读取前拒绝。

诊断时再按 registry 根路径及结果中的 source 相对路径深入项目；日常交接无需逐个浏览业务仓库。

## 项目声明模板与校验

共享可执行 Schema：`data/connections/schema.json`，由 `hub.connection_records` 的同一验证器导出。
它使用 `hub-typed-validator-v2` 格式；不是声称可交给任意 JSON Schema 引擎的文档。

```sh
python3 scripts/hub_connections.py schema
python3 scripts/hub_connections.py validate-declaration data/connections/hub.connection.template.yaml
```

在被授权的逐项目单元，将模板落地到该项目根下 `hub.connection.yaml`；本模板单元不写入外部项目。
声明只写稳定内容：schema_version、project_id、source_refs、mapping、unknown_fields、validation_entry、max_read_age_seconds。
本机绝对根仅在 Hub registry 管理；声明的唯一 current_state 来源必须与登记的 `current_state_paths` 对应。
现有 YAML/JSON/Markdown 状态文件无需改名。没有权威状态的项目应先在项目内建立最小状态源，由其正常工作流维护。

`source_refs` 当前只接受一个 sole current-state 源。`mapping` 的可用字段和类型由 Schema 列出。
YAML/JSON 使用 `selector: {path: [current_work, status]}`；键为字符串，数组索引为非负整数，不允许执行表达式。
Markdown 使用 `selector: {heading: Current, label: Status}` 精确读取该唯一标题下 `Status: ...` 或 `- Status: ...` 的单行值。
同名标题、重复标签、重复 YAML/JSON 键、YAML alias/anchor、越界路径及类型错误都会失败；不做模糊猜测。
Markdown 的布尔或状态文本必须通过显式 `value_map` 转换，例如 `{"yes": true, "no": false}`，不能按非空字符串猜真值。
未映射字段，以及映射结果为 null 的字段，都使用声明中该字段的 unknown 原因。
`validation_entry` 只展示项目原有验证命令，不由 reader 执行。

数组字段形状也严格校验：milestones 每项含 id、label、completed、accepted；blockers 每项含 code、reason、affected_items、recovery_condition、retry_entry、waiting_for_user。
尚不能依据来源填写的字段保持 null 和明确原因；不要新增假命令、假阻塞或空列表冒充已知。

## 本地飞书准备

JSON、Markdown、飞书样例都从同一个 snapshot 派生，保持 project_id、update_key、来源、失败及新鲜度一致。
Markdown 每项含可读摘要及一份完整规范 JSON 记录，可逐字段还原 latest_attempt 和 last_success；最新失败的指纹与错误不会被历史成功替代。
真实连接始终 `enabled: false`、`write_back_allowed: false`。纯预览不读环境变量、不获取凭据、不创建空间、不发消息。
仅声明 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_WEBHOOK_URL` 三个未来环境变量名。
每个更新以 project_id 为稳定目标，以 update_key 判断内容是否改变；读取时间刷新不会重复产生业务更新，来源、声明或读取处置变化会改变该项标识。
本轮交付的是字段映射和本地演示；真实发送器、目标映射和平台消息大小处理需在明确授权的接入单元另行验证。

回归入口：`python3 -m unittest discover -s tests -p 'test_hub_*.py' -q`。
