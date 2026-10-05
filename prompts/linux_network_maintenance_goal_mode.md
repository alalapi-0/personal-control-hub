# Linux 网络维护目标模式执行 Prompt

这是直接执行任务。单角色完成，不配置 Governor/Judge/Repair 三角色，不生成另一层执行 Prompt，不把可由本机完成的命令交给我手输。

请在这台 Linux 工作站进入原生目标模式，完成 VPN/Codex 的系统更新兼容维护，并安装 Personal Control Hub 的网络维护 Skill。只进入本次网络范围，不继续旧聊天中的磁盘、APFS、格式化、离线合并或系统发行版升级工作。

目标模式使用原生 Goal 能力。已有匹配目标则继续；没有目标时建立本目标。若存在未完成且不匹配的旧目标，先报告具体冲突，保留原状态并等待我决定目标切换，不能伪装完成、覆盖目标或建立第二套 Goal/state/scheduler。独立且已授权的只读诊断与可逆网络维护可继续，无需新增 Goal。复用本次项目唯一权威状态，保留已有有效验收与失败记录。

## 已授权范围与当前事实

只保证 VPN/Codex 兼容后续系统更新，其他软件沿用现有更新方式。不要为让 Mac/Linux 版本数字相等而安装其他渠道版本。当前没有授权全系统升级、改 Mac/VPS/路由器、自动重启、强杀进程或迁移凭据。

用户已自行重启，报告网络恢复；该报告不能替代本 boot 的实际验收。之前网络修复已交付到 /home/alalapi/Projects/linux-agent-bootstrap，commit bf2db998f4c8a55e3cdb298a73892e7e35069968；先检查当前项目与差异，不重新覆盖本地 Agent 的后续工作。SSD 是运行中的根盘，历史核验 UUID 为 9d258b70-f313-4a5d-9cf6-c715c5edca3d；重新核对，不操作分区、EFI、initramfs 或旧 APFS。

用户物理启动 SSH 后，照片已显示 ssh.service active 且监听 IPv4/IPv6 22，TriggeredBy ssh.socket，但 Mac 新连接仍超时。需实际诊断；不要直接关闭防火墙、放开公网 SSH 或盲目 enable/restart。已核验主机指纹是 SHA256:zGkVZegf0pwqIgZ8X/dAwDuHzhPkj+EerSaq1D1Elk0；未知/变化必须停下核验。

最新故障报告：用户自建的“局域共享 / LAN FILE SHARE”使用途中断开，认为 Linux 把局域网流量送入 VPN；照片显示 http://192.168.0.100:9417/，当前监听/出口仍待核对。该归因尚待实际取证。检查现行策略路由、TUN 接管、必要过滤与双向回程，并核对各次代码差异；不能因旧 SSH 通道正常就宣告局域网正常，也不要断言是谁造成。用户要求先完成既有 VPN/Codex 维护、Skill安装和交接任务；此工具仅作为网络回归验收项，不展开工具项目改造，不读取现有上传文件。

## 执行顺序

1. 核对当前主机、root UUID、boot_id、时间、接口、Wi-Fi、主/策略路由、Mac 管理路径、DNS、TUN 与代理实际运行。只读定向找最近变化；不扫描全盘，不读取账号缓存、令牌、密钥环、登录会话、authorized_keys 或完整环境。确认更新已结束及没有重叠网络写入；不把普通项目运行视作冲突，不强杀 apt/dpkg 或 Agent。

2. 从 Personal Control Hub 当前真实仓库读取 .agents/skills/linux-network-maintenance/SKILL.md，按需要读 references/incidents.md。先定位现有 Hub，核对其项目身份、当前 Git 状态和来源；不得在不明目录重新 git init，或覆盖/拉取污染现有工作。Mac 已把这些文件正常交付到 https://github.com/alalapi-0/personal-control-hub 的 main。Linux 若没有 Hub，可在无冲突的用户项目目录从该明确源做普通 clone；认证不足时停在该依赖，不读取其他凭据或借用未知缓存。

3. 核对本 boot 的 VPN 生成与真实启动。上次实际错误是国内 DoT DNS 的 detour 指向空 direct outbound；已删除这一多余项。语法通过不代表可运行。确认既有动态管理网段和能力检查仍有效，保留 AI/Steam 分流。只修当前有证据的回归；不硬编码发行版/内核/软件版本或旧局域网网段，不重建整个网络方案。

4. 诊断并恢复 Mac 的新 SSH 连接和实际局域网互传。核对当前直连前缀、真实对端、工具监听地址/端口、发现/名称解析、策略规则、TUN 规则优先级与双向回程。根据监听、必要过滤规则、实际出口和回程证据，一次只修一个确认的问题；不要把所有私有地址一概直连，因为既有 VPN 可能有内部私有网段。涉及重连、代理/网络重启、路由或防火墙修改，先说明断线风险并准备、核验独立本机回退；没有可核验回退时等我在物理终端配合。不用现有旧连接或普通 ip route get 证明应用路径正常，不用 service active 替代连接测试。


5. 完成待部署的 Codex 受限更新入口兼容修复：先检查 /usr/local/libexec/linux-codex-update-ssh、/usr/local/sbin/linux-codex-update、/etc/sudoers.d/linux-codex-update 的当前代码/权限及规则。核实默认 provider 对摘要规则的实际行为、已安装传统 /usr/bin/sudo.ws 的包来源和能力。若旧入口仍固定调用不支持摘要的默认 sudo，则只改入口为固定 sudo.ws 调用，保留 shebang、拒绝额外参数、DEVNULL stdin、固定最小 env 和 cwd。不存在可用 provider 时固定 deferred，不安装/改全局 sudo，不回退密码或其他 key。

   保留 sudoers 的用户、NOSETENV、helper 摘要、零参数与 NOPASSWD 限制；保留 helper、Mac 检查器、专用密钥、钥匙串、自动检查日程和包仓库。修改前保存有限前像并核验不依赖当前 SSH 的本机恢复方法。用不执行真实更新器的 mock/直接执行测试及只读非交互权限查询验证；不得为测试篡改真实 helper，无法安全验证的负例明确记未验证。

6. 将 Hub 的这个 Skill 安装到 Linux Codex 实际使用的用户级 Skill 目录。先从本机配置或原生 skills/list 确认目录及现有同名项；不要假定所有版本都使用同一路径。仅复制 SKILL.md 与 references/incidents.md 的非秘密内容，canonical 源留在 Hub。同名已有不同内容则先比较并保护其修改；不可覆盖其他技能、全局规则或配置。校验 frontmatter、引用和内容。用实际 skills/list 验证可发现；如果当前进程未刷新，只报告“文件已安装，当前发现未验证”，等自然新会话验证，不重启/退出客户端或声称热加载成功。Skill 本身不创建执行器、服务或定时任务。

7. 验收普通 getent DNS、TLS 验证开启的 HTTPS、少量重复请求、必要代理路径、实际 Wi-Fi 状态、服务/生成逻辑，以及 Mac 新 SSH 连接和实际局域网互传。互传使用工具实际双向路径、发现/连接及小型非秘密测试文件两端摘要校验；不读取原传输内容，不要求大文件压力测试。区分 curl 累计时间与各阶段耗时。401/403 只算传输结果；让我集中刷新一个原慢网页并尝试一次 Codex 操作，必要时合并一项互传确认。保留现有官方签名索引、包摘要、架构/依赖计划检查；Codex/app-server/相关 Agent 运行或包管理锁忙时等待，不结束进程，也不真正启动更新器充当权限测试。

## 交付与结束条件

更新每个实际所属项目的同一权威状态，跨项目修改与 Git 交付串行；只提交本任务拥有并验证的差异，保护并发修改，正常非 force 推送对应授权 main，核对远端 commit。不创建重复治理状态或后台监控，不自动改自动更新日程。

最终中文说明：确认的原因和证据；文件/配置改动；修改前后实际结果；回退；仍需我完成的最少操作；Skill 是已写入、可发现还是行为已验证；是否具备手动重启验收条件。原因不明和未验证项要具体说明。已有有效网络修复不要重复做；同一方法两次无新信息就换有依据的方法。

持续完成当前会话内能够安全执行的工作。不得自动重启；当前修复/验收通过后，由我决定是否重启以及后续验收。
