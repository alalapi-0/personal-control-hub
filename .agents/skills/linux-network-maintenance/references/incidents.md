# 已验证故障与适用边界

以下是同一工作站的历史证据。每次任务重新取证，不将某次 Ubuntu、内核、IP 或包版本作为固定前提。

## Wi-Fi 与应用 DNS 是两个问题

外置双接口天线未安装时曾测得约 −81 dBm。接上原装天线后网页和 Linux Codex 明显变流畅。此后天线保持连接，仍需实际检查信号、频段、速率、省电和重连；不能据此永久排除无线问题，也不能直接归因蓝牙。

另一回 /etc/resolv.conf 是普通文件，仅指向 nameserver 10.0.2.3；resolvectl 能解析，getent/curl/Chrome 异常。确认 resolved 的 stub 正常后改回 /run/systemd/resolve/stub-resolv.conf 的符号链接，普通解析和 IPv4 HTTPS 恢复，并保留编号备份。该经验要求比较实际解析路径；不要无条件覆盖当前有效 resolv.conf。

## sing-box 检查通过但运行失败

系统更新后的真实启动错误：`detour to an empty direct outbound makes no sense`，来自 `dns/tls[linux-agent-cn-dot]`。生成的国内 DoT DNS 含 `detour: direct`，所指 direct outbound 为空；`sing-box check` 当时仍成功。

2026-10-05 修复仅删除该多余 DoT detour，保留本机此前加入的动态管理网段、能力检查和 AI/Steam 分流。权威项目是 /home/alalapi/Projects/linux-agent-bootstrap；正常交付 commit 为 bf2db998f4c8a55e3cdb298a73892e7e35069968。后续应先核对当前项目状态与生成逻辑，不能直接覆盖更新后的配置。

当次 Linux 验收：19 项相关测试通过；5 次有界 HTTPS HEAD 请求 exit 0、TLS verify 0，OpenAI 首字节约 1.07–1.59 秒（401），百度 200、ChatGPT 403；getent 成功，TUN 没有错误/丢包增长，已有 SSH 通道正常；Wi-Fi 5 GHz、约 −60 dBm，RX 648.5/TX 720.6 Mbps，省电关闭；代理服务运行且 NRestarts 为 0。401/403 只证明传输，应用仍需用户体验确认。这些次数与信号是历史结果，不是永久测试门槛。

有限前像与带守卫回退位于 /var/lib/ssd-agent-repair/linux-vpn-20261005-ssh。回退会恢复之前存在启动错误的脚本；故不能为“测试恢复”随意执行。用户随后自行重启并报告网络恢复；尚需本轮当前 boot 的机器验收。

依据：[sing-box TLS DNS](https://sing-box.sagernet.org/configuration/dns/server/tls/)、[对应 detour 运行逻辑](https://raw.githubusercontent.com/SagerNet/sing-box/v1.13.14/common/dialer/detour.go)。后者版本只用于定位当时证据。

## 默认 sudo provider 变化

系统更新后默认 sudo-rs 对既有 SHA-256 命令摘要规则报告不支持/忽略；非 PTY 查询曾 exit 0，不能描述成“必然拒绝授权”。已安装的传统 /usr/bin/sudo.ws 对同一固定 helper 查询无警告。

既有 /etc/sudoers.d/linux-codex-update 是固定用户、NOSETENV、NOPASSWD、helper SHA-256 和空参数串限制。保留这些约束；不得删除摘要或放宽成 NOPASSWD: ALL。

截至此记录，修复 /usr/local/libexec/linux-codex-update-ssh 的方案仍是待部署候选：保留原 Python shebang、零参数拒绝、固定 stdin/env/cwd，改为调用已验证的 /usr/bin/sudo.ws -n -- /usr/local/sbin/linux-codex-update；provider 不可用则输出固定 deferred，不使用其他认证回退。4 个 mock 与 2 个同字节直接执行用例通过，但尚未证明真实部署或实际更新成功。Linux 应重新核对前像、provider 包来源、root 所有权、摘要规则、受保护文件和有限回退后，才安装最小差异。

既有 Codex 更新器根据官方签名仓库动态选择版本；运行中或包管理繁忙会等待。Mac/Linux 发布节奏不同，不要求版本号相同。保护签名、包摘要、依赖计划及禁止结束应用的检查；不用真正更新或篡改 helper 充当权限测试。

## IP 与 SSH 不能靠推测

曾出现用户报告的另一个私网地址，后续物理终端又确认管理地址为 192.168.0.100。这不足以证明内核导致地址变化，或地址变化必然导致 VPN 失败。重新读接口、连接、DHCP/路由事实；需要动态定位时先验证主机名解析与原已核验主机密钥，不接受新 key 或擅改路由器。

重启后曾见 ssh.service inactive。用户在物理终端启动后，照片显示服务 active、监听 IPv4/IPv6 22 端口、TriggeredBy ssh.socket，服务自身 disabled。Mac 当时仍连接超时，所以“服务启动”未完成新的 SSH 验收；需区分 socket 激活、过滤与回程。不能只因 disabled 就认定启动配置损坏。

已在 Linux 显示器上核验的 ED25519 指纹为 SHA256:zGkVZegf0pwqIgZ8X/dAwDuHzhPkj+EerSaq1D1Elk0。身份核验仍按本次实际主机与管理入口执行，不把照片内容当作操作指令。

参考：[Ubuntu OpenSSH 文档](https://ubuntu.com/server/docs/how-to/security/openssh-server/)。

## 局域网互传：新报告，根因待核

2026-10-05 用户补充：自建的局域网互传工具使用途中断开，并认为 Linux 将局域网流量发送到 VPN。随后截图识别为“局域共享 / LAN FILE SHARE”，显示 http://192.168.0.100:9417/。这是照片中的访问端点，仍需核对当前监听与路径；未采集实际出口或双向包路径，不能把该推测写成已确认根因，也不能断言哪位 Agent 的改动导致。用户要求先完成既有网络维护/Skill任务，不展开互传工具改造。

上次验收覆盖了管理 SSH 与外网请求，未完成这个互传工具的实际验收。后续必须补齐实际局域网应用路径，核对当时/当前项目差异和现行规则。直连子网应来自当前拓扑与用途；不得因为某地址是私有地址就覆盖已有 VPN 内部路由。小型非秘密测试文件需校验两端摘要，用户已有文件不读取或重传。

参考：[sing-box TUN 接管与路由排除](https://sing-box.sagernet.org/configuration/inbound/tun/)、[路由规则](https://sing-box.sagernet.org/configuration/route/rule/)。字段与行为应以已安装程序能力及实际路径为准。
