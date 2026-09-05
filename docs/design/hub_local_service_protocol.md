# Hub 本地服务协议 · 1.0 候选

读者：Hub 界面实现者、所有者和独立审核者。目的：说明统一应用接口、来源和本机访问边界。更新触发：接口、读写或访问语义变化。当前执行状态与唯一下一步以 `STATE.yaml` 为准；本协议不表示界面、连接或整个 Goal 已验收。

## 启动与信任边界

在 Hub 根目录运行 `python3 scripts/hub_server.py`，默认地址为 `http://127.0.0.1:8766`。可用 `--port` 更换端口，`0` 让系统分配空闲端口。只接受字面地址 `127.0.0.1`，拒绝 LAN、任意主机名和公共地址。Ctrl-C 关闭监听并等待有界的请求处理结束。启动不读取外部项目、不启动项目业务服务、不建立设计库，也不产生设计选择。

这是当前 OS 用户的单用户本地服务。本机账户和以该身份执行的原生进程处于信任边界内；它不是远程或多用户身份认证系统。网页请求仍须通过精确 Host、Origin、Fetch Metadata 和会话检查。访问许可不由“来自 loopback”这一条件单独推断。

`GET /api/session` 建立只在运行时内存保存的会话，响应设置 `HttpOnly; SameSite=Strict; Path=/api` Cookie，并在 JSON 的 `data.csrf_token` 中返回随机令牌。每个会话单独绑定令牌，最长一小时，重启后失效。令牌不出现在 URL、日志或持久记录中。来自其他 Origin 或跨站 Fetch 的会话申请也被拒绝。

应用查询需要会话 Cookie。所有 POST 还必须带当前服务的精确 Origin、`X-Hub-CSRF` 令牌和 JSON Content-Type；浏览器同源调用产生这些信息。缺失/重复/冲突的安全头、跨站或不支持的帧格式在分派前拒绝。服务不提供 CORS。请求体最多 64 KiB，重复 JSON 字段、非有限数值、未知命令字段和歧义版本均不能静默接收。

## 接口

普通成功响应为 `{"api_version":"1.0","ok":true,"data":...}`。错误为 `{"api_version":"1.0","ok":false,"error":{"code":...,"retryable":...,"outcome":...,"details":...}}`。错误只含固定代码及允许的 ID、版本、哈希或回执；不回传异常堆栈、服务端路径和秘密。已提交但同步/校验未确认的结果保留原提交语义。传输断开或提交后响应无法编码时，调用方用同一请求 ID 与完整命令重试，不应擅自换 ID。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /api/health` | 最小存活信息；不暴露项目事实 |
| `GET /api/session` | 同源会话与运行时防跨站令牌 |
| `GET /api/projects` | 注册项目的确定性列表与筛选 |
| `GET /api/projects/{project_id}` | 由同一 DTO 构造器生成项目详情 |
| `GET /api/designs` | 一次读取的事实、候选、决定投影和历史 |
| `GET /api/artifacts/{artifact_id}?candidate_id=ID&candidate_revision=N` | 精确候选绑定的已登记材料 |
| `GET /api/exports/{request_id}?candidate_id=ID&candidate_revision=N&candidate_hash=HASH&store_revision=N` | 核对绑定后下载已经发布的导出包；不存在时拒绝，不隐式导出 |
| `POST /api/refresh` | 显式、受当前来源权威约束的刷新 |
| `POST /api/designs/decisions` | 所有者明确的选择、修改反馈、暂缓或撤回 |
| `POST /api/designs/exports` | 在服务端指定目录导出精确候选材料 |

没有仓库静态文件挂载、任意文件路径参数、外部 URL 抓取、后台自动刷新或 UI HTML。界面将在所有者选择 Figma 方案后使用这些接口。

## 来源与状态

名单来自 registry；来源使用已验收的 frozen bundle、持久刷新库和关系记录。查询不会读取外部项目。一次项目响应将来源历史和重建投影绑定到相同 ledger head；持续并发时返回明确的重试冲突，不能把不同快照拼成一个结果。列表与详情共用业务字段、来源引用和状态推导。缺失记录保持未知，来源错误和最近成功记录分开；调度器状态不能冒充业务进度。

设计快照只读一次，并保留独立的 store revision。它与 ledger head 共同标识响应使用的两个不可变事实快照，不声称存在跨两个存储的原子事务。设计库尚不存在时明确 unavailable，不创建空库来伪装初始化成功。关系只保留已记录的 proposed/unknown，设计选择和正式项目关联权限不互相替代。

刷新命令必须显式提供 `request_id`、`project_ids` 和 `expected_head`（sequence/hash）。项目集合仅可包含 frozen manifest 中的唯一 ID。相同请求继续使用原集合、权威和身份，已经提交的项目不重读；部分成功仍保留。Manga 拒绝发生在任何项目路径操作之前，HTTP 层不放宽此边界。

## 设计操作与材料

决定命令须完整提供 `request_id`、`event_id`、`created_at`、`expected_revision`、`action`、`candidate`（ID/revision/content_hash）、`scope`、`feedback` 与 `supersedes`。没有默认选择、默认候选或隐式实施授权。新决定要匹配当前候选、相关基线和家族版本；已提交请求的原样重试返回原回执。来源由通过会话和同源保护的调用上下文生成，JSON 中不得提供 trusted_owner 或 source 来替代它。隔离演练始终写 synthetic fixture，真实选择计数不受演练影响。

导出命令须提供 `request_id`、`expected_revision` 与精确 `candidate`。目标文件由服务生成并以请求 ID 绑定，客户端不能提供输出路径。重试须核对已发布包的绑定和材料，不能覆盖既有结果。包内事实和选择语义沿用设计记录协议；选择不代表实施许可。

材料必须由登记 ID 和候选身份查找，并核对绑定、范围、分类与实际字节摘要。读取仅限 Hub 指定材料目录中的有界普通文件，拒绝软链、硬链接、FIFO、穿越和非法路径。可内联的图片须匹配允许的格式；其他内容作为惰性附件下载，配合 nosniff、同源资源策略、禁脚本和 sandbox 响应头。Figma 指针不被下载或伪称本地可编辑原稿。

底层 CLI、应用服务和未来 UI 使用同一设计事实库与刷新 ledger。没有另一份可编辑“当前状态”表，接口或展示可由原始记录重建。验证记录见对应 `unit-04` 证据；程序实现和证据变化后才更新本协议。
