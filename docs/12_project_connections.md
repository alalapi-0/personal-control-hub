# 项目状态统一入口

读者：只从 Personal Control Hub 接手的 Agent，以及负责维护项目状态源的 Agent。

固定范围的质量报告使用 `python3 scripts/hub_connections.py status`，与本地页面的 `/api/sync-status` 共用 ProjectService 和现有账本。报告逐 ID 保留 complete/partial/failed/stale/unread、核心缺口、来源 SHA、观察时间及相同账本 head；范围与登记 SHA 绑定原始身份集合、当前读取资格、遗漏和范围外条目。历史请求保存的是当时选中的 ID 和 authority hash，不是完整权限快照。最近成功读取、最近失败和 OPEN 请求分别展示；读取成功不代表业务或人类验收。只读 systemd 查询限定两条 refresh unit；查询失败显示不可用。计划时间、LastTrigger 元数据和模拟时钟不能代替 R6.4 真实日历 journal 或退出登录后的恢复验收。

新增非 Git 项目也沿用唯一 `data/registry/external_projects.yaml`。先在 Hub 的 `data/registry/` 下准备**完整单条**登记 YAML，沿用该表的 required_project_fields 和存储约束，明确绝对 root_path、稳定 id、唯一 current_state_paths、`connection_read_allowed`，并保持 `scan_enabled: false`、`external_write_allowed: false`。业务声明不由登记器创建。执行 `sha256sum data/registry/external_projects.yaml` 后，运行 `python3 scripts/hub_connections.py register --entry data/registry/candidate.yaml --expected-registry-sha256 <该SHA>`；默认只检查。审阅单条差异和身份 warning 后，以同一 SHA 加 `--apply` 才写本 Hub 的登记表。此入口不运行项目命令、不启动 Agent、不复制仓库，也不改变固定同步范围；登记不等于执行授权。新项目随后可在目录出现，缺声明仍是 missing_declaration。

迁移路径时提交同一 id 的完整新登记，保留原账本，不改成第二个 id；旧投影因 authority 变化保持 stale，明确重新读取后才更新。不同 ID 的同规范路径或已获读取资格的同物理根会被拒绝；禁读、removed、legacy 条目零探测，不能确认物理身份的 warning 保留为未验证。嵌套根需要先解决身份决定，本入口拒绝直接应用。所有检查及 apply 使用预期登记 SHA、文件身份、现有目录 advisory lock 和原子替换；该锁仅协调遵守此入口的写者，不能替代跨任务文件写责。回退仅恢复本次单条登记的精确前像，且须核对当前 SHA，不能覆盖后来的手工修改或删除账本历史。
更新条件：接入契约、读取命令或错误语义变化时更新。业务事实始终在项目自身的权威源中。现有目录、业务树与甘特沿这些确定性接口展示事实和独立管理计划。

## 日常操作与恢复

先在代码目录选择现有数据根 `PCH_ROOT`。日常入口见 [README](../README.md#日常使用)；固定代码启动时仍须显式传 `--root`。以下操作共用当前注册表与原账本，不调用模型、不启动业务项目。

1. 查询：`python scripts/hub_connections.py --root "$PCH_ROOT" status` 查看完整质量／失败／恢复状态；`current` 查看目录，`history --request-id ID` 核对某次原始结果，`validate` 验证完整账本，`rebuild` 只读重建投影。这些详细输出可能包含私人来源，只在本地保存。
2. 刷新：先 `python scripts/hub_sync.py --root "$PCH_ROOT" discover`，再用新请求 ID 执行 `sync --request-id ID`；原请求 ID 只续接未提交项。失败或 partial 保留原分母与历史；修正来源后用新 ID 重读受影响项目，不删除失败或伪造完整字段。
3. 管理计划：所有者在页面编辑优先级、关系、日期及滚动估计，保存的是 Hub 管理层。导入只更新来源事实。预估到期与本人实际接受分别记录；没有本人回执时仍待验收。
4. 新项目：按本文开头准备完整单条登记，使用 `register` 和预期 registry SHA 先检查，再在准确写责下显式 `--apply`。它不会生成业务声明、自动加入固定刷新集合或授予业务执行权。
5. 日志：`journalctl --user -u personal-control-hub-refresh.service -n 30` 与 `systemctl --user status personal-control-hub-refresh.timer` 分别核对执行和调度。退出码 2 需要查看原请求错误；timer active、service 启动或 HTTP 200 均不等于全部业务成功。注销／漏跑边界见 [本地每日刷新](#本地每日刷新)。

数据备份前先核对并停止该备份范围的写者，保持刷新、任务、管理编辑和采集静止；不要停止其他人的任务。SQLite 使用标准库的 `Connection.backup`，同时保存匹配版本的注册文件、管理配置、唯一 STATE 与已保存图片。下面片段只接受全新的私有备份目录，不读取浏览器会话或环境凭据；它本身不会建立独占写责。代码及未提交工作树成果另行保留。

```sh
python - "$PCH_ROOT" "$NEW_BACKUP_DIR" <<'PY'
from pathlib import Path, PurePosixPath
import hashlib, json, shutil, sqlite3, sys
root, target = map(Path, sys.argv[1:])
if not root.is_absolute() or '..' in root.parts:
    raise RuntimeError('Select an absolute data root')
def local_path(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts:
        return None
    current = Path(root.anchor)
    for part in (*root.parts[1:], *path.parts):
        current /= part
        if current.is_symlink():
            return None
    return current
target.mkdir(mode=0o700)
names = ['STATE.yaml', 'hub.connection.yaml', 'data/registry/external_projects.yaml',
 'data/connections/sync_scope.yaml', 'data/connections/management_config.yaml',
 'data/connections/connection_refresh.sqlite3', 'data/design_governance/design-store.json',
 'data/design_governance/connection_refresh.sqlite3', 'data/workbench/tasks.sqlite3',
 'data/workbench/storage-registration.json', 'data/workbench/workbench-store.json']
names += [str(p.relative_to(root)) for p in (root/'data/previews').rglob('*') if p.is_file()]
exclusions = []
design = local_path('data/design_governance/design-store.json')
if design is None:
    raise RuntimeError('Inspect data-root aliases before backup')
if design.is_file():
    for fact in json.loads(design.read_text()).get('facts', []):
        location = fact.get('location', {})
        if fact.get('kind') == 'artifact_ref' and location.get('kind') == 'hub_relative':
            name = location['value']
            path = local_path(name)
            if path is not None and path.is_file():
                names.append(name)
            else:
                exclusions.append({'id': fact['id'], 'reason': 'nonlocal_alias_or_missing'})
manifest = {}
for name in sorted(set(names)):
    source, dest = local_path(name), target/name
    if source is None:
        raise RuntimeError('Inspect symbolic link before backup')
    if not source.exists():
        continue
    dest.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix == '.sqlite3':
        with sqlite3.connect(source.as_uri()+'?mode=ro', uri=True) as src, sqlite3.connect(dest) as dst:
            src.backup(dst)
    else:
        shutil.copyfile(source, dest)
    dest.chmod(0o600)
    manifest[name] = hashlib.sha256(dest.read_bytes()).hexdigest()
(target/'backup-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
(target/'backup-exclusions.json').write_text(json.dumps(exclusions, indent=2)+'\n')
PY
```

只在新的、独占恢复目录中按清单核对 SHA 后复制这些文件，并用同一已验证代码版本启动 `hub_server.py --root "$RESTORED_PCH_ROOT" --host 127.0.0.1 --port 0`。核对完整账本 head／history、管理值、任务状态及保存图片；外部资产引用不代表资产字节已备份，业务项目原始资料仍由各项目保管。任务库的物理身份需要下节的显式离线恢复，不能靠新建空库解决。恢复演练通过之前不要覆盖当前运行目录；回退仅针对本次拥有且当前 SHA 未变化的文件。

## 本地每日刷新

当前单状态来源刷新复用 `SourceResolver → RefreshLedger → ProjectService/API → 项目页面`，不执行下面旧数值指标入口的 exporter。`data/connections/sync_scope.yaml` 是静态资格集合：固定 39 个身份及显式排除，读取前再核对当前登记权限；缺登记、撤销权限、离线、缺声明不会缩小分母。`discover` 只核对这份集合与 Hub 登记元数据，不扫描目录或自动注册新候选。

来源读取使用 no-follow 描述符，核对文件身份、类型、链接数、大小、mtime/ctime，以及目录和文件名仍绑定到已打开对象。规范化结束前复读同一允许来源，核对内容版本与 mtime，再核对映射声明；期间变化归为当次失败。旧成功保留为历史，其他项目继续采集。业务文件不加锁；这是有版本证据的读取快照，不是跨文件写事务或持续实时观察。

普通来源和 `hub.connection.yaml` 声明的单文件读取上限为 1 MiB。既有 Hub 自身适配器仅在登记 ID 为 `personal-control-hub`、登记规范根等于当前读取器根、来源路径恰为 `STATE.yaml` 且格式为 `yaml` 时使用固定 2 MiB 上限；仍要求 `metadata.authority: canonical`，并对首次读取和规范化后的复读应用同一上限及身份核对。外部同名项目、其他路径或格式继续使用 1 MiB；超限保留失败及历史成功，不自动提高上限。

```sh
python scripts/hub_sync.py --root "$PCH_ROOT" discover
python scripts/hub_sync.py --root "$PCH_ROOT" sync --request-id UNIQUE_ID
python scripts/hub_sync.py --root "$PCH_ROOT" sync --request-id UNIQUE_ID --project-id PROJECT_ID
python scripts/hub_sync.py --root "$PCH_ROOT" sync --daily
```

同请求 ID 复试只处理未提交项；已记录的失败也保留，重读需显式新 ID。每日请求 ID 按 Asia/Tokyo 日期稳定生成；资格或来源规则变化导致身份冲突时保留原请求，用历史入口核对后另起明确的新请求，不自动覆盖或换号。输出只有请求状态、数量、错误代码和账本版本，不把业务正文写入 journal；失败返回 2。原详细历史查询仍用 `hub_connections.py history --request-id ID`，只在本地查看。

页面的完整同步统计来自只读 `/api/sync-status`，在与完整分页目录同一账本版本时才启用刷新。完整/partial/失败/过期/未读取五类保留固定集合分母；登记及排除单列，搜索不会改变范围。未登记目录的发现数量保持未知。旧成功仅显示为历史；核心字段缺口单列，技术完成与用户接受互不替代。

每日运行使用已验证版本的独立代码与项目专用 Python 3.12 venv。依赖仍只安装同一 `requirements.txt`；把固定版本的 `src/hub`、`scripts/hub_sync.py` 和运行依赖放在项目已忽略的 `.venv/pch-refresh/` 内，显式 `--root` 指向现有主项目。不要复制 registry、STATE、DB 或凭据到运行副本，不把临时目录作为持久 timer 入口，不覆盖主项目未提交代码。生成新 unit 时指定这些绝对路径：

```sh
python scripts/install_hub_refresh.py --root "$PCH_ROOT" --code-root "$FIXED_CODE_ROOT" --python "$REFRESH_PYTHON" --unit-dir "$HOME/.config/systemd/user"
systemd-analyze --user verify "$HOME/.config/systemd/user/personal-control-hub-refresh.service" "$HOME/.config/systemd/user/personal-control-hub-refresh.timer"
systemd-analyze calendar '*-*-* 09:00:00 Asia/Tokyo'
systemctl --user daemon-reload
systemctl --user enable --now personal-control-hub-refresh.timer
```

`--time HH:MM --timezone Area/Location` 可调整生成的日程；不改主机时区或 linger。生成器拒绝覆盖已有 unit，更新前需核对精确前像及写责。只暂停新 timer：`systemctl --user stop personal-control-hub-refresh.timer`；恢复用 `start`，停用用 `disable --now`。已开始的 oneshot 与现有 Hub 生产服务分开；中断后账本 OPEN 请求可用同 ID 续接。手动启动 service 可验证执行链，但不能证明真实到点。`Persistent=true` 只在 user manager 再次可用时补跑；注销期间持续运行、实际下一次定时 journal 都须真实验证，安装文件不构成这些验收。

## 离线备份中的任务库恢复

先停止恢复副本的全部使用者，并保留一致性备份。任务库与外部注册文件共同绑定原物理目录和文件身份，因此直接复制后保持 blocked 是预期行为；普通服务启动、HTTP、授权登记和 worker 都不会自动接管它。只有确认独占的离线副本才能显式执行：

```sh
python scripts/restore_task_storage.py --root "$RESTORED_PCH_ROOT" \
  --database-sha256 DATABASE_SHA256 --registration-sha256 REGISTRATION_SHA256 \
  --confirm-offline
```

两个 SHA 必须分别来自该副本 `data/workbench/tasks.sqlite3` 和 `storage-registration.json` 的精确字节。操作只接受已知 schema 2、无 SQLite sidecar、完整有效哈希以及全为终态的任务；健康存储、排队／运行／未知状态和不安全文件均拒绝。`--confirm-offline` 是调用者对已完成停用与写责核对的确认，本身不能证明没有其他进程。已有 `requires_reconcile`、provider 标识、任务、授权、事件和自增序号全部保留，仅显式重绑存储 profile 与文件身份；它不重建空库、不改变授权范围，也不启动任务。

数据库和注册文件没有跨文件原子事务。发布先写不可用的 pending 注册，再替换已验证的暂存库，最后写 ready 注册。中断或结果未知时先检查实际状态；pending 保持 blocked，新进程不自动补完。只有对照保留备份、核实精确当前文件与独占写责后，才可恢复本次拥有的副本并重新显式操作；不要删除注册文件、静默重新初始化、盲目重试旧 SHA 或覆盖后来的改动。原始备份及源项目保持不变；其他工作台数据和图片仍需各自的一致性备份与恢复核对。

## 程序指标入口（v2 数据前置）

```sh
python3 scripts/hub_connections.py metrics schema
python3 scripts/hub_connections.py metrics collect --request-id UNIQUE_ID
python3 scripts/hub_connections.py metrics summary
python3 scripts/hub_connections.py metrics summary --after 10 --limit 10
python3 scripts/hub_connections.py metrics query --project-id manga-localizer --metric-id review_count --stage issues
python3 scripts/hub_connections.py metrics aggregate --project-id manga-localizer --metric-id review_count
python3 scripts/hub_connections.py metrics management
python3 scripts/hub_connections.py metrics reviews --axis review_stage --view current
# reviews 按 review_stage/rework_count/submission_attempt/candidate_revision 单轴查询；current 与 history 不混计，无历史时 coverage.history_status=empty。
python3 scripts/hub_connections.py metrics changes --project-id light-novel --since 2026-09-01T00:00:00Z
python3 scripts/hub_connections.py metrics issues --project-id manga-localizer --limit 5
python3 scripts/hub_connections.py metrics validate
python3 scripts/hub_connections.py metrics feishu
python3 scripts/check_v3_end_to_end.py
python3 scripts/check_v3_end_to_end.py --handoff
python3 scripts/check_v3_closure.py
python3 scripts/check_v3_closure.py --verify-remote
# 收口核对远端 main SHA、无Git/移除处置、Hub 死路径与恢复点；不访问受保护项目根，不把历史 delivery-inventory 当当前权威。
# V3-08 余项在条件变化前保持分母，goal_complete 不得为真。
```

Hub 数据启动与显式手动同步使用既有根 CLI：

```sh
python3 hub.py start --project-id light-novel
python3 hub.py sync --request-id UNIQUE_ID --project-id light-novel
```

两条命令都先输出一行 `phase=cache` 的 Hub 本地投影并立即 flush，再输出一行 `phase=sync` 的结果。同步只读取登记声明中的安全相对 Python `metric_export.entry`，通过无 shell、无继承环境、固定参数和单项目超时的子进程运行；不执行 `validation_entry`，也不调用 Agent、模型、MCP、生成器、测试、Git、provider、scheduler、业务队列或审批入口。Hub 在导出前后复核 registry、项目根、声明和脚本身份，只把固定 `.hub/status.json` 交给导入器。重放同一请求会复用已提交的项目收据，不再次运行其导出器。

同步集合先按实际项目 ID 与最大合法 receipt 预检完整结果的 8KiB 上限；放不下时须拆分项目列表，并在创建账本或访问项目根前失败。每次未提交项目单元在访问项目根前先写入 Hub 本地 attempt；成功快照、项目 request/receipt 与 attempt 成功状态在同一 SQLite 事务提交。离线、超时、绑定变化、无效快照及未声明/禁用处置只结束各自 attempt，不阻塞集合中的其他项目；持久化故障单独记为 `sync_attempt_store_failed`，不伪装成快照无效。进程中断留下的 running attempt 会在该项目下一次真实尝试时准确结束为 `sync_interrupted`；同请求中已有 receipt 的项目在此之前即复用，不创建新 attempt。升级前遗留的同项目 request 只有在没有任何 receipt 时才允许于成功事务内重绑定新快照身份，已提交身份不可改写。

缓存项目行明确返回 `latest_attempt`、`last_success` 与 `view_role`。最近尝试失败但存在旧成功时，旧值保留为 `historical` 且 freshness 为 `sync_failed`，不能显示为当前成功；首次失败则为 `unavailable`。`MetricStore.sync_status` 和分页 `sync_attempts` 保留精确开始/结束时间、错误、snapshot ID/schema、导出器版本、源版本及快照观察时间；受 8KiB 限制的启动摘要只返回源版本集合的确定性身份与数量，不复制完整版本映射。超长但有效的 ISO 时间在账本保留原文，缓存中规范化为语义等价的微秒 ISO 表示，避免单行阻塞分页。

`collect`只读登记项目的明确元数据与本地Git状态，在原连接SQLite账本的metric表中写入投影。加`--project-id`可只采集一项。原请求ID重放使用已提交结果；输入/采集器版本变化需新ID。云项目排除；removed_local在解析或探测根路径之前结束。查询、校验、摘要和飞书映射均无需模型、不运行项目检查、不触发业务任务、不联网。

所有输出上限8KiB。摘要始终保留全部登记分母、处置、数值/未知及错误计数，项目页默认10项；指标/问题页默认10项且按字节自动缩小。用next_cursor作为下页的--after，不能把第一页当作全部明细。

`data/connections/metric_sources.yaml`保存来源选择。当前漫画读取真实review/project SQLite只读事务，按当前items而非历史artifact累加；轻小说读取章节清单、导出清单和生产批次metadata。治理completed不能成为业务完成指标。未接领域源的项目仍有Git观察及明确缺口；历史验证不代表当前代码验收。当前登记项全部有处置不等于全部业务接入完成。

指标含稳定project_id/metric_id、数值或null、unit、dimensions、observed_at、business_at（未知为null）、source_ref、相关输入指纹source_version、quality/reason及counting_basis。独立字段失败保留其他有效指标。总数、待审、失败、排队和版本各有口径；运行链active不等于活跃worker。真实连续阻塞起点无证据时年龄为null，不用首次采集时间冒充。

项目普通 Python 脚本通过 `hub.metric_export.export_metric_snapshot` 调用显式只读统计函数，并原子写入唯一标准快照 `.hub/status.json`。v2 快照同时携带经 `validate_result` 验证的 21 字段管理投影和数值指标，两者绑定同一 `snapshot_id`、`observed_at` 与 `source_versions`。Hub 侧仅通过 `hub.metric_import.import_metric_snapshot_file` 读取该快照并写入现有指标 ledger；导入模块不加载领域适配器，也不接受业务源路径。

批量入口 `hub.metric_import.import_metric_snapshots` 冻结项目绑定和快照根；每个项目独立事务提交，已有 receipt 的项目恢复时不再读取。无效/离线/超时快照只形成当次安全错误，其余项目继续；同一快照跨请求只更新观察 receipt，不增加指标变化历史。

`MetricStore.project_snapshot` 在一个 SQLite 读事务中返回该快照的管理投影和分页业务指标；没有标准快照身份的旧直采记录不能冒充这一视图。变化按project_id、metric_id、unit、dimensions和口径分别查询；页、章、任务与测试不混加。delta只来自同一指标实际观察，历史不足为null；`change_per_second`与`throughput`恒为null，净变化不冒充吞吐。`metrics aggregate`按单位/口径/窗口分组，比例保留分子分母，百分位不平均，未知不改写成0，成本只来自遥测。默认Git指标仅观察本地tracking refs并明确remote_verified=false；显式启用`--remote-git`或`--github-ci`后，共享只读客户端才按`metric_sources.yaml`仓库绑定查询GitHub。同仓请求在有效期内复用并以ETag重验，分页受限、权限不足或限流时结果保持unknown，同时保留已验证页数、已观察run数和provider报告总数。Actions统计绑定采集时的本地HEAD，口径是该候选的全部workflow runs，不声称required checks齐全、脏工作区已测试或已完成新的GitHub交付复核。

账本只新增语义变化，重复刷新更新观察时间；失败前的成功仍在历史中保留原时间。当前登记撤销读取或改变绑定后，查询将旧值标为历史，不能作为当前成功。`metrics prune --before ISO_TIMESTAMP`是显式本地历史保留操作，保留当前值及计算变化所需前驱；不会删除项目原始数据。飞书输出固定enabled=false、write_back_allowed=false，仅本地映射。

以下为当前确定性单状态连接接口，与数值指标共享登记和账本。

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

## 日期、里程碑和未排期视图

完整项目目录和项目详情展示独立的 Hub 管理计划、来源实际日期及来源阶段轨道。列表先完整分页并核对同一账本 head，再做筛选；没有日期、读取失败、过期或被移除的项目仍有身份和缺失原因。阶段没有自己的来源日期时进入未排期轨道，不继承项目计划，也不按阶段序号、轮次、文件 mtime 或观察时间估计工期。日、周、月视图采用有界窗口，窗口外日期保留文字和“定位日期”；原生筛选、日期输入、按钮和详情链接提供键盘与触摸入口。

来源可显式选择 optional extension 1.2 的 `fact.task_timeline`，沿唯一源的字面 selector、实际 SHA 与观察时间证明导入。它保存实际开始／结束、实际里程碑和引用当前计划 task_id 的日期，保持 task_plan 1.0 的计数和验收独立。日期点明确区分日历日期与带 UTC 偏移的时间戳，命名时区可空且不会使用主机时区补齐。日历日期按自身日历标注；时间戳在 UTC 日期轴定位并保留原文和声明时区。不同粒度／不可比较日历只显示分别标记，不推算连续时长；单侧日期不冒充完整持续条。未声明、null、旧版本、过期及历史事实保持各自含义。

管理配置 1.2 在旧七字段覆盖层之外新增 `timezone` 和计划 `milestones`。旧 1.0／1.1 配置、旧覆盖层及完整历史原文保持可读，显式保存新版覆盖层才升级；导入和查询不自动迁移。计划里程碑的稳定 ID、名称和可空日历日期由所有者编辑，null 未配置与显式空列表不同。管理操作只改本 Hub 计划与优先级，不改实际事实、来源文件、运行状态或业务完成率，也不启动任务。

管理配置 1.3 的 `rolling_plan` 独立保存滚动估计：预估基准日、复核日、预计完成区间、依据与信心、前置条件、下一可验收增量以及自动／本人验收建议窗口。预计区间不是固定截止日期，也不是项目实际工期。持续型项目或条件未满足时，最终完成区间可空，先安排下一增量或复核。普通日期／优先级编辑保留已有滚动计划；旧七／九字段配置、历史及请求哈希原样兼容。

预估可按明确的东京时区管理；主机时区不改变。来源的启动、工程收尾、更新和验收时间按其原范围展示，不用文件 mtime、首个 Git 提交、轮次或“距更新时间”代替开工／停工时长。自动或工程审查不自动等于本人验收；没有带范围与版本依据的实际验收时间保持未知。估计值只写 Hub 管理配置，导入不会覆盖它，也不改来源事实或启动业务任务；计算与展示不依赖模型。

项目目录在完整分页后统一筛选、分组并投影为列表、来源业务树与甘特；每页及每行须具有相同账本 head 和管理配置可用性、revision、config_hash、source_sha256。版本混合或缺页时拒绝发布部分目录，可重新读取。排除项与固定范围统计不随筛选缩减。身份暂不可用只停用管理编辑，已读取来源仍可查看并可重新核对身份；真实空目录与筛选无匹配分别提示。

来源可选扩展 1.3 的 `page_inventory` 分开声明实施与设计页面域，沿既有来源选择器、字面值／SHA／观察时间证明和不可变账本进入项目 DTO 与页面。每域页面计数明确为完整、已知子集或未知：`known_page_count` 是所列页面单元数，只有完整清单才可称全部总数；动态路由、弹窗与标签如何计数须写入依据，不能从路由数量自动推算全部页面。代表预览至多 3 个，数量不限制页面清单规模。

每个预览引用精确绑定项目、页面域、页面 ID、源／构建哈希与可空采集时间；已知版本不一致标历史，必要版本缺失保持无法绑定。当前绑定仅表示来源元数据一致，图片字节、真实运行和本人验收仍独立。`GET /api/page-inventory` 返回完整登记目录与同一账本 head 的只读清单，不读取图片、不依赖设计记录、不触发采集或 Agent；项目详情显示同源信息，旧来源没有声明时显示未知。

预览引用不授予采集权限。受信 Hub 所有者须另给项目与精确 loopback URL／本地图片路径允许清单；默认没有捕获地址，重定向一律拒绝，凭据／会话字段不进入政策或仓库。图片读取限定 `data/previews/`，逐层 no-follow、正规单链接文件、体积／哈希及读取前后身份校验；仅本地保留。索引查询不触发采集；独立截图 worker 与已有桌面图片导入见下文，各候选的真实运行／图像验收仍看唯一 STATE。来源 1.3 与 Hub 管理配置 1.3 是两个独立契约。

## Hub 管理配置

项目详情页的“Hub 管理配置”沿用唯一 `data/connections/management_config.yaml`，独立保存优先级、字面日历日期、关注、显示顺序、布局及所有者确认的归属/依赖/关联/独立关系。来源事实、业务完成率和原关系建议保留各自含义；“未配置”不会推断为独立，独立项目仍可依赖其他项目。归属循环拒绝保存，项目依赖循环显示警告；来源任务的阻塞依赖仍按任务契约防环。拖动预览只改布局草稿，不自动保存或改关系。

查询和来源导入不创建、升级或写入管理配置。缺配置时明确不可编辑；现有 1.0 配置可读，保存旧覆盖层升级为 1.1，保存含时区／里程碑的新覆盖层升级为 1.2，保留 freshness、axes、dimensions、watch 等既有设置。只有现有所有者认证和 CSRF 检查通过才能保存或撤回，并在提交前重验权限和当前登记范围。保存使用版本与语义 SHA 的 CAS、原子替换及可重放的请求标识；过期版本拒绝更新并保留草稿，显式重新读取后才能提交新请求。提交结果未知时用同一请求确认，已保存但视图读取失败时明确显示提交版本。

管理配置保留单文件完整版本历史，重启后读取相同值。新引用只允许当前有读取资格的本地登记项目；已失去资格的旧设置不由导入删除。该入口不探测子项目目录，不启动 Agent，不写业务来源，也不授予执行权限。页面使用键盘或触摸的原生输入控件；布局也提供可键盘输入的坐标。

## 正常刷新与恢复

在当前任务已授权的范围内显式刷新；命令只读项目来源，在本 Hub 写入账本及可重建预览：

```sh
python3 scripts/hub_refresh.py refresh --request-id governance-check-001
python3 scripts/hub_refresh.py validate
```

中断后重用完全相同的 request ID，已经提交的项目不重复读取。重新检查变化的来源时使用新的 request ID。
刷新退出码依据该请求的不可变结果；后续成功不会让旧失败请求重放变成成功。仅包含已移除项目的请求正常结束，不产生重试。刷新后会重新核对当前登记权威；权威不可用或预览写入失败时，返回固定错误码及已提交请求收据，不把已提交事实丢失或伪装成当前预览成功。
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

## 现有服务入口

`hub.project_service.ProjectService` 为现有本地 HTTP/UI 消费者提供 `list_projects`、`get_project` 和显式 `refresh`。主分支提供服务模块；原工作区的 HTTP/UI 历史是否已完成安装验证，以 `STATE.yaml` 的本任务条目和交付收据为准。

服务保持原查询、分页、错误码和设计快照接口。`source.source_record` 保存完整规范来源记录，`operational.last_success` 单独保存历史成功，`business.fields` 提供全部21个类型化业务字段。业务摘要不把过期或失败证据当作当前成功。移除状态在 `freshness.local_presence` 与 `freshness.state` 明确表达。设计快照按 project_id 过滤；旧关系或设计数据不能授予项目业务状态权威。

服务读取不创建账本或访问项目根。显式刷新要求完整 request_id、project_ids 与 expected_head；冲突和部分提交带稳定错误码及可恢复请求收据。新 Hub2 账本不回退到旧设计治理账本或旧 authority bundle。

登记文件损坏或暂时不可用时，公共 CLI 仍可核对已有账本：

```sh
python3 scripts/hub_refresh.py history
python3 scripts/hub_refresh.py rebuild
```

这两个命令只读现有 Hub2 账本，不创建数据库、不访问项目根。结果是历史审计记录或重建的历史投影；当前登记权威不可用时会明确显示，历史成功不能冒充当前项目状态。账本缺失或损坏返回可操作错误，不能通过初始化新库或回退旧设计账本隐藏故障。日常项目状态仍从 `current` 与显式刷新取得。

`declared.enabled` 保留原登记值；页面的读取资格使用服务输出的明确布尔值 `declared.connection_read_allowed`，与当前来源解析权限一致。历史 `legacy_hub_connection_exception` 不得提升为当前 `hub_connection_exception`，也不能遮蔽最新失败。漫画的 `enabled: false` 是原登记事实，不取消本任务明确允许的业务读取。

`src/hub/web/connection_view.mjs` 提供现有页面使用的 `attention`、`freshness`、`refreshable` 三个纯函数：已移除项目明确显示“已从本地移除”且不能刷新；最新失败保持可见，即使存在历史成功或例外。回归命令为 `node --test tests/test_hub_connection_view.mjs`。

## Owner 安全配置准备

默认认证继续读取服务进程中的 `HUB_OWNER_TOKEN`。显式启动参数 `--owner-auth=systemd-credential` 改为读取 OS 通过 `CREDENTIALS_DIRECTORY` 投影的固定名称 `pch-owner-token`，缺失或损坏时不回退到环境凭据。目录和文件逐层 no-follow，文件必须私有、属于当前 UID、正规单链接且不超过 2048 bytes；读取前后核对版本和路径绑定，文本须为 32–512 个可打印字符，不自动裁剪。投影轮换、丢失或损坏会使旧 Owner 会话在后续请求中失效；不据此宣称所有进行中的业务操作均可即时撤销。认证配置不启用任务 executor 或模型。

先对明确的候选代码和既有数据根生成配置草稿：

```sh
python3 scripts/hub_owner_credential.py prepare --code-root "$PCH_CODE_ROOT" --root "$PCH_DATA_ROOT" --python "$PCH_PYTHON" --host 127.0.0.1 --port 8766
```

`prepare` 只读并返回代码 SHA 和 systemd drop-in，不访问凭据、不调用加解密提供者、不写入或安装配置、不重启服务。它保留数据根、host 和 port，明确使用含新启动参数的代码路径，不能直接给旧生产脚本追加新参数。输出保持 `ready_to_install: false`：正式应用前仍需审核精确代码、服务写责、账本迁移和回退。drop-in 使用 `LoadCredentialEncrypted`，并取消继承 `HUB_OWNER_TOKEN`。

实际录入只能在上述方案审核后，由所有者在本机交互 TTY 显式运行 `python3 scripts/hub_owner_credential.py register --allow-host-key-initialization`。该参数还要求输入明确的主机提供者确认；host 加密可能初始化 OS 全局密钥，`--user` 和 `--no-ask-password` 不保证免初始化。入口先用合成值完成内存加解密资格验证，再隐藏输入两次 Owner proof。明文只经 stdin 内存传递给固定 `systemd-creds`，不进入参数、环境或临时文件；只保存私有目录 0700 下的密文文件 0600，已有文件或链接均拒绝覆盖。非 TTY、缺确认、提供者失败、隐藏输入不可用或两次不一致均失败关闭，不尝试 sudo、GUI 认证、null key、TPM 或其他提供者回退。

当前交付只证明代码、合成提供者和合成 HTTP/浏览器行为。真实 OS 加解密资格验证、可能需要的主机密钥初始化、真实 Owner 录入以及正式服务切换分别保留未验；Agent 未执行这些操作。配置命令不会安装 unit、重启 Hub 或开启任务执行。

## 显式普通只读执行器

默认 Web 启动和 Owner 登录均不启用任务执行。普通文本任务可显式选择已有的服务端授权：测试授权继续使用 `--ordinary-readonly-grant ID`；真实只读授权使用 `--ordinary-real-readonly-grant ID`。两个选项互斥，真实模式不能同时启动 readonly preview。入口不创建授权、初始化存储或接管其他 owner 已 claim 的任务；必须先核对既有任务库/profile、登记资格、准确输入清单与哈希、授权版本/有效期，以及该授权下已准入的队列与运行状态。

真实模式只接受明确列出的 `classification=real`、`effect=read_only` 注册授权；无效、混合类别、过期、来源登记或输入变化在 provider 就绪查询前拒绝。启动后仍逐次核对授权、物理存储、输入和原生绑定，不允许浏览器提交任意 grant。worker capability 报告真实模式；真实任务保持原 TaskStore 的全局串行规则，测试模式的两槽例外不扩展到真实项目。

准备正式方案时，Web 与采集器必须固定同一份已验证代码及静态资源，并单独核对解释器、数据根和单元前像。仅升级 Web/collector 不会自动选择真实授权。显式执行器启动会处理该精确授权下已经合法准入的 queued 任务，因此须先核对这些任务的归属与效果授权；未知原生状态或 `requires_reconcile` 不自动重派。停止仅取消本 runtime 已 claim 的精确任务，保留未启动队列和其他 owner；再次启动使用新 runtime，并重新验证全部前提。

此入口补齐产品装配能力，不构成真实业务执行授权。实际输入注册、模型回合、Owner 本机录入及正式服务切换仍分别需要对应授权；隔离数据和受控 adapter 的验证不能替代真实业务/provider 或本人验收。

## 仓库检查与本机可用性

```sh
python3 scripts/check_registry.py
python3 scripts/check_registry.py --metadata-only
```

默认本机检查保留 authority 路径存在性、watch_paths 覆盖及存储保护约束。两条已移除记录在任何文件探测前停止；漫画只允许当前所有者登记的唯一业务状态路由，存储检查仍不访问漫画根。

`--metadata-only` 用于无法拥有本机项目盘的 CI：仍验证全部登记、路由和存储契约，并明确输出 `path_availability_checked: false`。它不声称本机路径可用；路径缺失拒绝由临时样例回归覆盖，真实来源结果由显式刷新验证。

治理 runner 从STATE.all_projects_governance.candidate_paths读取当前文件范围，无需外部候选报告。缺失或越界时失败；作者/编辑器名称不改变范围，选定范围不产生动作授权。

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
同名标题、重复标签、重复 YAML/JSON 键、外部来源的 YAML alias/anchor、越界路径及类型错误都会失败；不做模糊猜测。
Markdown 业务字段的布尔或状态文本可通过显式 `value_map` 转换，例如 `{"yes": true, "no": false}`，不能按非空字符串猜真值。来源已明确写出小写字面量 `true` / `false` 时，也可使用 `selector: {heading: 当前应用界面审计, boolean_label: has_ui}`，返回真实布尔值，适用于 `has_ui` 等扩展事实。此选择器复用唯一标题、唯一标签和围栏过滤；大小写变体、数字、yes/no、引号、强调或附加说明均被拒绝。原有 `label` 仍返回原文字，不自动转换。
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

## Hub 拥有的确定性映射与来源绑定

来源项目保持只读时，可以在它的既有 registry 条目中明确配置 `hub_field_mapping`。它使用现有声明的 mapping 规则格式，整体替换字段选择，不合并旧规则；未映射或遇到明确 null 子树的字段仍保持 unknown。缺键、错误的非 null 类型、越界索引与歧义仍失败，false、0、空列表不被当成缺值。不得在映射中填写业务事实或推断完成率。

可选 `hub_source_ref` 使用现有 source_ref 结构，只绑定一个明确登记的相对来源路径及格式。它必须保持来源声明原有的 id 和 role；路径必须已列入该项目的 `current_state_paths`，并通过现有根资格、保护路径、无跟随文件读取和类型检查。不自动跟随 README 链接，不扩大权限、根、来源数量、exporter、验证命令或 TTL。

以下仅展示既有登记条目的局部配置，示例字段必须来自实际当前源：

```yaml
current_state_paths: [STATE.json]
hub_source_ref:
  id: state
  path: STATE.json
  format: json
  role: current_state
hub_field_mapping:
  current_work.status:
    source_ref: state
    selector: {path: [status]}
    value_map: {active: active, blocked: blocked}
```

原 `hub.connection.yaml` 仍先校验并保留准确哈希，读取后再次核对；规范记录的 field_provenance 保存实际 selector、raw_value 和 value_map。完整 registry 哈希绑定 Hub 映射/来源配置，配置变化需要重新同步，不能用旧成功冒充当前。坏映射只使对应项目刷新失败，其余项目仍可处理；移除或禁读项目仍在任何根探测之前拒绝。

不同范围的工程验收、创作决定、用户接受和运行门禁分别保留。不能把历史工程 12/12、运行测试或规划总量改称当前创作业务分母；来源没有当前分母时保持未知。

Markdown 来源还可明确选择唯一标题下的两列表格单元或编号项：`selector: {heading: Snapshot, table_label: Phase}`、`selector: {heading: Next, ordered_item: 1}`。表格必须是明确的两列结构，目标标签或编号只能出现一次；读取值保留原 Markdown 文本。代码围栏、标题范围外和嵌套/续行内容不提供目标事实，缺失、歧义与不支持的结构会失败。既有 `heading + label` 与 JSON/YAML typed path 保持兼容。

状态枚举转换仍须显式 `value_map`，保存原值与转换规则；等待人类审核不会变成审核通过。Markdown 表格或列表读取不生成日期、分母、用户接受或业务事实，也不执行来源中的命令、链接和代码。

## 来源声明的业务任务树

可选扩展 `1.1` 在来源 `2.0` 和 DTO `1.0` 上添加 `fact.task_plan`；原扩展 `1.0` 与没有扩展的声明继续有效。声明的 `extensions` 包含 `schema_version`、`fact_mapping` 和 `unknown_fields`。每条映射只接受既有唯一来源的 `source_ref` 与字面 `selector`，例如 `{path: [task_plan]}`，不执行命令、读取其他文件或生成来源事实。缺少明确计划时保留未知；后台执行任务不能替代业务计划。

`task_plan` 的独立版本 `1.0` 包含计划版本、单位、计数依据、目录与输入摘要，项目→阶段→轮次→叶子节点，以及叶子的 I/V/H、验收条件版本和证据引用。只计业务叶子；父节点、39 项接入总量与 PCH 的 111 项重构总量互不替换。重复 ID、孤儿、错误层级、父链或依赖循环都会使该项目读取失败，其他项目可继续。已验证需要当前计划和条件版本、已实现及非空证据；用户验收需要已验证。未知 I/V/H 不转换为 false 或通过；未知总量不从节点数补算，零总量的比例不适用。

版本历史明确列出新增、移除、拆分、取消或修订前后的叶子 ID、对应关系、原因与证据。取消保留在分母且不能计 V/H；移除须有版本变化记录。最新失败或来源过期后，历史成功树仅可在标有“历史快照”的区域回看，不能计作当前验收。读取时间、来源 SHA、选择路径和适用版本与原始账本记录一同保留。

PCH 自身由唯一 `STATE.yaml.pch_refactor` 的正常写者，在同一次状态更新中机械生成 `business_task_view`。冻结任务目录由该写者核对；读取器只检查已经读取的 State 输入摘要，不另读目录，不写状态、不产生第二台账。修改 task_results 或当前进度而未更新投影时，读取失败，不复用旧树。

### 手动采集实际预览（可选）

来源页面清单是项目事实；已保存截图是本地采集观察，两个入口分别展示。普通查询、每日同步和无模型模式均不会启动浏览器或业务服务。截图也不代表本人验收。

需要新网页截图时，在项目隔离环境安装 `requirements-preview.txt`，再显式运行 `python scripts/hub_preview.py --root /path/to/hub capture --request /path/to/owner-request.json`。可用 `--executable-path` 指定已安装的本地 Chromium；使用新的无登录上下文，保留浏览器沙箱，不接管用户浏览器。请求必须提供当前已声明的实施页面 ID、来源／构建版本、来源凭证 hash、精确 loopback URL 与资源 GET 白名单、视口、最长 30 秒超时、过期时长和刷新原因。请求格式见 `validate_capture_request`；来源字段不能授予采集权限。已有图片优先使用 `import-existing --manifest ... --policy ...`，只读取所有者明确列入白名单的 Hub 内图片，不搜索或启动子项目。

采集入口只适用于所有者信任的本地应用。每个 HTTP 资源按精确地址拦截，拒绝跳转、写请求、WebSocket、弹窗、下载和认证头。仅当前全新上下文的同源匿名 GET（PCH 为 `/api/guest`，旧 `/api/session` 接口保留）可建立临时访客 cookie；不导入已有会话、不执行登录，不向其他来源发送 cookie。可选的 `version_url` 必须在相同白名单中，返回精确对象 `{schema_version: "1.0", project_id, source_sha256, build_sha256}`；前后均匹配才认定运行版本已核对。PCH 自身的 `/api/preview-runtime` 只报告实际服务打包页面文件的内容 hash，不返回路径或凭据。其他应用没有这个证据时保留版本未知。

图片和不可变请求收据保存在本地 `data/previews/`。相同请求 ID 不重复执行，不同内容复用 ID 会被拒绝；未过期且版本相同的成功截图可复用。明确手动刷新可重新采集，超时或失败保留旧图并标为历史。未知页面不推算为零；只有当前来源明确 `has_ui: false` 才标记不适用，完整实施清单明确为零则单独显示尚无实施页面。历史桌面报告时间不替代实际截图时间。


完整服务可从固定代码目录读取已有 Hub 数据目录：`python scripts/hub_server.py --root /absolute/hub-data-root --host 127.0.0.1 --port 8766`。未传 `--root` 时仍使用该代码 checkout 的目录；静态页面与导入模块始终来自代码目录。根目录必须已存在且不得含符号链接或 `..`，选择目录不会初始化数据、重新注册任务存储、恢复浏览器会话或启动 Agent/项目预览。已有数据的备份恢复须另行核对存储身份；文件副本不自动获得原任务账本的物理身份。
