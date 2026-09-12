# Hub connection protocol · 1.0

读者：Hub 数据层维护者、审核者与独立使用 CLI 的所有者。目的：说明当前可执行的命名来源读取、记录校验和证据边界。更新触发：schema、来源选择器、路径权限或命令行为改变。本协议不保存阶段或下一步；当前工作以 `STATE.yaml` 为准。

## 权威与读取

`data/registry/external_projects.yaml` 是唯一名单和路径入口。本单元冻结文件 `data/design_governance/manifest-v1.json` 只绑定 registry 哈希、全部项目 ID 和字段引用，不复制可编辑路径。registry 任意变化拒绝旧清单刷新，显式建立下一 revision；冻结命令拒绝覆盖已有清单。

`connection_adapters.json` 只保存来源角色和确定性字段选择器。业务状态仍属于外部来源；本地投影可重建。Markdown 按明确标题/标签提取，结构化来源用安全解析器；未匹配或来源没提供字段时说明未知原因。来源语法损坏、文件缺失、磁盘离线、权限失败和未授权是不同状态，不回写外部修复。

读取在权限判断后才解析项目路径。仅支持命名的 `current_state_paths`，解析软链后必须落在登记 root 内。通过逐级 no-follow 文件描述符打开解析后的路径，拒绝越界、禁扫目录、秘密文件、非普通文件及超过 1 MiB 的来源。读取中变化返回错误。来源中的命令、hook 和自定义 YAML tag 都不会执行。

三个没有 current-state 入口的记录保持在分母内，显示来源未声明，尚未达到连接最终验收。Manga Localizer 保持禁止访问，尚待所有者范围决定。命名文档只有存储/使用说明时，不声称业务已完成。

## 已实现命令

在 Hub 根目录运行，使用现有 Python 3 与 PyYAML；没有新增共享依赖。入口显式加载 `src`，避免根目录旧 `hub.py` 遮蔽同名包。

```sh
python3 scripts/hub_connections.py schema
python3 scripts/hub_connections.py freeze --revision 1 --output data/design_governance/manifest-v1.json
python3 scripts/hub_connections.py refresh --manifest data/design_governance/manifest-v1.json --project personal-control-hub
python3 scripts/hub_connections.py refresh --manifest data/design_governance/manifest-v1.json
python3 scripts/hub_connections.py validate data/design_governance/manifest-v1.json docs/reports/ui_design_governance/unit-01/connections-v3.json
```

`freeze` 示例仅适用于文件尚未存在时。新版本须改 revision 与文件名，不删除旧清单。`refresh` 默认仅 stdout；显式 `--output` 只能写 Hub 的 `data/design_governance/` 或 `docs/reports/ui_design_governance/`。临时文件同目录原子替换，完成后清理；这些是可重建投影，不是选择/反馈事件存储。事件并发与幂等服务属于后续实现，不能把该输出函数作为用户决定写 API。

退出码：0 表示命令成功且请求的来源均成功读取；2 表示已生成完整结果但有未授权、未声明、不可用或无效来源；1 表示参数/版本/清单或其他命令错误。单项来源错误不会清空其他项目。刷新成功不意味着产品、UI 或全范围接入通过最终验收。

## schema 与引用

当前 schema 的唯一实现是 `hub.connection_records`；`schema` 命令从同一声明导出 `hub-record-validator-v1` 格式的机器可读字段/枚举索引。它不是通用 JSON Schema 方言，不需要额外包。

支持 `scope`、`connection_manifest`、`project_snapshot`、`connection_evidence` 四类记录。顶层和 manifest entry、permission、registry reference、source 嵌套对象都使用精确字段集合。验证唯一 ID、时区时间、哈希、全量范围、缺字段原因和权限。集合校验必须接收由 Hub 本地加载的完整 registry、原文件哈希和当前 adapters；不能只传项目 ID，也不能信任记录自称的权限。逐项核对 access_profile、读取模式、索引和解析后的精确命名路径。禁止读取对象在解析路径前就被拒绝。scope 与 entry 的 evidence reference 必须指向同一 registry 项目。

fresh 必须有来源指纹，`last_success_at` 与 `observed_at` 一致，且无刷新错误。非 fresh 不得携带具体状态、下一步或阻塞列表；字段为 null、normalized status 为 unknown，并逐字段提供未知原因。无来源不等于没有阻塞。invalid 可以保留已读取但无法解析文件的指纹，其余非 fresh 状态不能伪造指纹。状态归一化使用确定性词表；无法识别的原文保留并解释 unknown。具体下一步必须标明 explicit、recommendations、backlog 或 track_milestone，不能同时标为 unknown。

snapshot 与 evidence 的 adapter version、adapter hash 和来源角色必须匹配当前选择器权威。此基础版本的 relations/designs 仅接受空列表；设计引用将在配套集合解析器完成后启用，不接受悬空设计记录。本次所有 1.0 文件仍处首次未验收候选阶段，旧失败候选保留审计用途，不作现行投影导入。

集合校验解析 manifest → snapshot → connection evidence 的精确引用，并核对适配器版本。fresh + UI UNVERIFIED 对应 PENDING/退出码0；fresh + UI PASS 对应 CONNECTED_AND_VERIFIED/0；fresh + UI FAIL 对应 VALIDATION_FAILED/0。其他来源状态对应明确失败/未完成状态、退出码2及 UI UNVERIFIED。授权例外还须匹配 registry 中明确的例外依据。schema 变更需升版本并明确兼容策略，不静默迁移。

接入证据只在真实读取成功且 UI 验证为 PASS 后才允许 `CONNECTED_AND_VERIFIED`。本单元所有 UI 验证均为 UNVERIFIED，成功读取条目保持 PENDING。例外必须有所有者/适用政策依据，不由读取器批准。设计选择、实施授权和发布不由本协议推断。
