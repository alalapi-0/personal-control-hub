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

2026-10-05 20:10:44（北京时间）的有限部署已成功：/usr/local/libexec/linux-codex-update-ssh 保留原 Python shebang、零参数拒绝、固定 stdin/env/cwd，改为调用已验证的 /usr/bin/sudo.ws -n -- /usr/local/sbin/linux-codex-update；provider 不可用则固定 deferred，不使用其他认证回退。部署时核得 root:0555、SHA-256 d35f3bf701582e2e6065c8c135c729c783c9faedc2ab7fa2db0952f5418c5136，VPN 未重启。此前测试和真实安装通过，不代表真实 Codex 更新已执行成功，也不代表已开启 16 小时后台管理员能力。

以上是前一会话的实际工具验收交接。后续先核当前文件、合法修改、provider 与摘要限制；匹配已接受候选时只做持久化验收，不重复安装。摘要变化须先查后续修改，不能直接覆盖。有限部署及局域网规则的带守卫恢复入口在 /var/lib/linux-agent-bootstrap/network-maintenance-20261005/restore.py；不能为测试恢复而对实机回滚，也不能重放绑定旧 boot_id 的安装命令。

既有 Codex 更新器根据官方签名仓库动态选择版本；运行中或包管理繁忙会等待。Mac/Linux 发布节奏不同，不要求版本号相同。保护签名、包摘要、依赖计划及禁止结束应用的检查；不用真正更新或篡改 helper 充当权限测试。

## IP 与 SSH 不能靠推测

曾出现用户报告的另一个私网地址，后续物理终端又确认管理地址为 192.168.0.100。这不足以证明内核导致地址变化，或地址变化必然导致 VPN 失败。重新读接口、连接、DHCP/路由事实；需要动态定位时先验证主机名解析与原已核验主机密钥，不接受新 key 或擅改路由器。

重启后曾见 ssh.service inactive。用户在物理终端启动后，照片显示服务 active、监听 IPv4/IPv6 22 端口、TriggeredBy ssh.socket，服务自身 disabled。Mac 当时仍连接超时，所以“服务启动”未完成新的 SSH 验收；需区分 socket 激活、过滤与回程。不能只因 disabled 就认定启动配置损坏。

已在 Linux 显示器上核验的 ED25519 指纹为 SHA256:zGkVZegf0pwqIgZ8X/dAwDuHzhPkj+EerSaq1D1Elk0。身份核验仍按本次实际主机与管理入口执行，不把照片内容当作操作指令。

参考：[Ubuntu OpenSSH 文档](https://ubuntu.com/server/docs/how-to/security/openssh-server/)。

## DHCP 后局域网被 TUN 接管：已取证并部署

2026-10-05 已有实际路径证据：VPN 启动后 NetworkManager 先得到 192.168.124.16，随后变为 192.168.0.100；一次生成的直连排除未随 DHCP 更新。源地址专用 8999 规则保住 SSH 回程，普通 Linux 发起的局域网连接却经 labtun0/table2022。因此旧 SSH 成功不能证明局域网应用正常。

同次有限部署已加入当前物理直连前缀处理，复用 NetworkManager up/down/DHCP 事件和既有 VPN ExecStartPre；本任务规则为 priority 8998/protocol 222，保留 8999 与其他规则。安装文件为 /usr/local/lib/linux-agent-bootstrap/linux_lan_routes.py（root:0555）、/etc/NetworkManager/dispatcher.d/90-linux-agent-lan（root:0755）和 /etc/systemd/system/linux-agent-network.service.d/20-lan-routes.conf（root:0644）。钩子使用 /usr/bin/python3，不绑定 Python 小版本；不将全部 RFC1918 网段一概直连。

前一会话实际验收：23 项相关测试通过，普通 Linux 请求经局域网到达 Mac，Mac 收到 peer 192.168.0.100；Linux→Mac 新测试文件 107B、HTTP200、SHA-256 21cf29ff2797cfa0141d2f40cd29f6f7d3be66853eebebe38892e9199ca55517；Mac→Linux 新测试文件 105B，上传 201/200/200，Linux 只回读该测试文件并核长度/摘要，SHA-256 a2c67b48a6ce8d44908dbb3961ca76d9158732c368819c1b279d7d62a4cd8b2d。新独立 SSH、getent 与 5 次 TLS 校验开启的 HTTPS 请求也通过，用户确认网页和 Codex 正常。这是交接证据，不是后续会话独立复测。

后续启动要少量核验动态前缀、事件钩子与实际两方向互传；仅用本次新建非秘密小文件，校验两端摘要，不读取旧上传内容。网络业务状态仍在 /home/alalapi/Projects/linux-agent-bootstrap/PROJECT_STATE.md，互传服务仍用所属项目的原入口；不复制进度账本，也不照搬旧 Goal 的完成率。

参考：[sing-box TUN 接管与路由排除](https://sing-box.sagernet.org/configuration/inbound/tun/)、[路由规则](https://sing-box.sagernet.org/configuration/route/rule/)。字段与行为应以已安装程序能力及实际路径为准。

## 根盘物理 I/O 与内接后接口变化

此前外接系统 SSD 曾因触碰连接线冻结；/bin/bash 与 systemd-journald 同时出现 Input/output error，Codex app-server 也不可用。这是存储 I/O 故障证据，尚未区分线缆、硬盘盒、供电、SSD 或文件系统，不把它归因于 VPN。新的根盘错误须停止依赖该盘的新增写入；不插拔运行中根盘、不在线 fsck、不改 GPT/EFI/initramfs 或旧 APFS。

用户关机后将同一 SSD 装到主板。2026-10-05 21:56（北京时间）原 Linux 会话的当前启动工具输出确认 hostname lab-root、boot_id 086ae6ea-dd60-4a62-9ae5-6367b660e1f6、根 UUID 9d258b70-f313-4a5d-9cf6-c715c5edca3d，/dev/nvme0n1p3 为内接 NVMe、ext4/rw；相关当前启动日志未见根盘 I/O/重置/文件系统错误。日志中 usb 3-9 的断开属于 Lenovo 键盘，不能当作系统 SSD 断开证据。这只是当次有限稳定检查，不保证 SSD 以后不出错。

同次观察：Wi-Fi 从 wlp8s0 变为 wlp9s0，地址 192.168.0.100/24；8998 直连规则仍在，到 Mac 192.168.0.104 走物理网卡。ssh.socket 监听 IPv4/IPv6 22、ssh.service inactive，9417 未监听；Mac ping 成功而 TCP22/9417 超时。入站过滤/接口限制仍待实际规则核验，不能仅凭改名确认根因。随后原生远端工具返回主机不可用；这条结果自身不能证明根盘 I/O 再次发生，也不能确认服务恢复请求已完成。继续时先核真实通道与启动身份，不重复部署或误读旧成功记录。

## 远程开发只能经 Tailscale 中继（2026-10-29）

现象：Cursor Remote-SSH 连 lab-linux 首次输出需 8–30 秒，心跳偶尔卡 20–48 秒；18:00–18:10 十分钟内重连 28 次，其间出现 `connect to host 100.72.159.92 port 22: Operation timed out`。认证始终正常，Linux 的 tailscaled、ssh 与 linux-agent-network 均 active。

已确认原因：两端一直 `direct connection not established`。Mac 在公司网络为对称 NAT（MappingVariesByDestIP=true）且无 IPv6；Linux 家宽为双重 NAT，路由器 UPnP 映射成功，但其外网地址仍是私网地址。Mac 的 Tailscale UDP 不经 Shadowrocket（STUN 出口不同于代理出口）。两端都必须保持代理，网络也不归所有者控制，所以只能走 DERP 中继。同时段 A/B：Mac→新加坡中继约 77 ms、零重传，Mac→香港 280–350 ms 有重传；Linux→香港 130–175 ms 基本无重传，Linux→新加坡约 370 ms 持续重传。自动选择（Mac 主中继新加坡、Linux 主中继香港）已是现有最优组合；临时强制 Mac 用香港使超时更多，已用 `tailscale debug force-prefer-derp 0` 撤回。经中继的 SSH 内层 TCP 往返可排队到 2.5–4 秒；Cursor 的 SOCKS 通道有扩展内置、无设置项的 30 秒超时，超时后杀掉 SSH 重新握手，坏时段一次握手 10–100 秒，形成重连风暴。

测量陷阱：Linux 上普通 `ping` 公网地址进入 labtun0，由 sing-box 本地应答（约 0.1 ms），不是真实延迟；CLI `tailscale netcheck` 不带 tailscaled 的 0x80000 标记，同样走 sing-box。分段质量用 `tailscale ping`、Linux `ss -tni`（tailscaled 到中继连接的 rtt、bytes_retrans）和 Mac `nettop -m tcp` 看，并须两端同时段对比，只看一侧会选错方向。lab-connection-doctor 的 SSH 检查固定 ConnectTimeout=5、ServerAlive 3×1，在该路径上会把慢但可连判为 key_exchange 超时。

已采用的缓解（仅 Mac 本机，可撤回）：`~/.ssh/config` 新增 `lab-linux-cursor`，目标、身份和主机密钥选项与 lab-linux 逐项相同，另加 ControlMaster auto、ControlPath ~/.ssh/cm-%C、ControlPersist 30m、ConnectTimeout 30、ServerAlive 15×4、Compression yes；lab-linux、lab-linux-backup、lab-root-lan 生效配置不变，Codex 桌面与 linux-codex 自动化继续用原别名，doctor 自带 ControlMaster=no 不受影响。实测首次连接 9.8 秒，复用 1.4–4.8 秒，经复用的 `-D` 转发访问 Cursor 服务端 /version 返回 200。Cursor 用户设置 `remote.SSH.serverShutdownTimeout` 由默认 300 改为 3600，断线一小时内保留远端服务端与会话，下次服务端启动时生效。回退：删除 `cursor-lab-linux-resilience-20261029` 配置块（备份 `~/.ssh/config.bak-20261029-cursor`）并删去该设置项。

快速恢复：Cursor 远程主机改用 `lab-linux-cursor`。持续重连超过约 2 分钟时，在 Mac 终端执行 `ssh -O exit lab-linux-cursor` 清掉旧共享连接，再在 Cursor 执行 Developer: Reload Window；长时间命令放进 Linux 的 tmux。根本改善只能来自可直连的网络（如同一局域网），或经所有者批准的新中继/备用通道；lab-linux-backup 当日 VPS 一跳认证未通过，由 VPS 所属任务处理。
