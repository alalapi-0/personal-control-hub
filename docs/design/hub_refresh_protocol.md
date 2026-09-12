# Hub 来源与刷新协议 · 1.0

读者：Hub 数据层维护者、所有者与独立审核者。目的：说明来源计划、持久刷新和关系提议的可执行接口。更新触发：格式、来源边界或命令行为改变。当前阶段与唯一下一步只保存在 `STATE.yaml`。

## 来源权威

registry 是唯一项目名单与路径权威。`manifest-v2.json` 保留全部 24 个 ID；旧 v1 清单和记录保持不可变。`source-plan-v1.json` 绑定清单、registry、adapter、已验收的 UI 源码清单与本轮静态取证哈希。`authority-bundle-v1.json` 冻结清单、adapter 和计划，供历史记录离线校验；它不保存第二份可编辑项目路径表。

19 个项目使用 registry 命名的状态入口。Light Novel 只经命名诊断脚本的静态 import 与路径常量读取两个调度控制 JSON；不运行脚本。历史治理 YAML 仅作诊断，调度事实与未知业务状态分开。三个没有当前状态声明的项目只核对已批准的精确入口内容，记录 `EXPLICIT_NO_CURRENT_SOURCE_VERIFIED`，不由介绍或逐项输出状态推断项目进度。Manga 的拒绝发生在任何项目路径解析之前。

来源结果使用 `SOURCE_RESOLVED / EXPLICIT_NO_CURRENT_SOURCE_VERIFIED / BLOCKED_BY_AUTHORITY / SOURCE_UNAVAILABLE / VALIDATION_FAILED`。前两类是来源层成功；后两类是需要修复或重试的中间错误。所有结果的 UI 验证仍为 `UNVERIFIED`，不能因此产生最终连接验收或所有者例外。

读取仅限被绑定的普通文件，限制大小、路径、软链、敏感位置和读取中变化。业务状态、下一步与阻塞只从明确字段提取；无法读取或无字段时保留未知原因，不回写外部项目。

每次访问外部路径前重新核对 Hub 当前 registry、adapter、清单与静态取证绑定；同一请求中也不复用过期权限。来源权威改变、缺失或损坏后，该项目生成无来源观察的失败记录；已提交的其他结果保留。离线历史验证仅用冻结记录，不因此读取当前外部项目。

## 刷新与历史

`connection_refresh.sqlite3` 是 Hub 内追加式刷新事实库。每个请求冻结 ID、项目集合和 authority capsule；每个项目读取后立即独立提交。进程中断后用同一个请求 ID 恢复，只读取尚未提交的项目。更换项目集合或来源权威必须用新请求 ID。不同请求采用 SQLite 事务串行提交；可提供全局 head 的 sequence/hash 做 CAS。

事件以连续 sequence、前序 hash、载荷 hash 和请求/结果引用相互核对。schema 未知、引用损坏、结果与冻结来源不一致均拒绝读入。一次来源失败不会清除其他项目的结果。每个项目分别投影最新尝试与最近成功；失败后保留成功结果引用并标为 stale，没有成功历史时为 unknown。旧 schema 的 snapshot 不被改写来伪装新鲜度。

`history` 与 `rebuild` 用只读 SQLite 连接；数据库不存在时报告错误，不创建空库。重建在内存完成，不依赖外部磁盘、项目路径或缓存文件。历史校验只用冻结 bundle；当前 Hub authority 不可用或变化会单独报告，不删除旧事实。新版本刷新时显式同时提供旧、新 bundle，最后一个为当前来源权威。

一次历史读取在同一 SQLite 读事务内完成 schema、事件、结果、请求完成状态和 head 的核对；并发提交不会混入半份新视图。重建使用该次完整历史快照。真实记录损坏仍会被校验拒绝，正常并发不能被误报为持久损坏。

## 命令

在 Hub 根目录使用现有 Python 3 与 PyYAML，无需新增共享依赖。全局参数放在子命令前。

```sh
python3 scripts/hub_refresh.py validate
python3 scripts/hub_refresh.py refresh --request-id daily-20260905
python3 scripts/hub_refresh.py refresh --request-id hub-20260905 --project personal-control-hub
python3 scripts/hub_refresh.py history --request-id daily-20260905
python3 scripts/hub_refresh.py rebuild
python3 scripts/hub_refresh.py relations
```

可用 `--ledger docs/reports/ui_design_governance/<unit>/refresh.sqlite3` 指定隔离的 Hub 本地库。读取命令只向 stdout 输出 JSON。新刷新使用新请求 ID；重试原请求复用 ID，不产生重复事实。`refresh --expected-sequence N --expected-hash HASH` 的两个字段必须一起提供。

退出码：0 表示命令成功且该刷新请求没有失败结果；2 表示结果已保存，但包含受阻/失败项目或来源校验显示当前权威漂移；1 表示命令、输入、请求冲突或事实库错误。全量请求包含 Manga 时会返回 2；必须核对结构化结果，不能把退出码 2 等同于没有保存。

## 关系与版本

`relation-proposals-v1.json` 的关系需绑定 registry 与已验收源码清单的独立哈希，保存项目 ID、种类、共同任务、差异、不共享内容和来源。它们始终是 proposed。管线关系不赋予视觉家族身份，视觉语言提议也不确认正式 program link、设计选择或实施权限；没有证据的项目关系显示 unknown。

来源、清单、bundle 与关系新版本使用新文件名并保留旧版本。历史库保留原始事件；衍生展示可以删除并重建。数据库损坏时先保留原文件，使用已校验历史或新的隔离库恢复，不覆盖既有事实库，也不通过外部项目改写来修复 Hub 状态。
