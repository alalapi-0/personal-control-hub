# 共用 20 小时管理员窗口

读者：当前媒体目标执行者，以及 LAN File Share / creator_workbench 的原执行者。
这份源代码只准备授权能力。用户已选定 20 小时；实际认证、root 状态和验收事实只写回 `STATE.yaml#media_apps_storage_goal`。项目暂停状态不会因窗口开启而改变。

`python3 -B scripts/admin20/prepare.py` 复用固定哈希的既有 root 后端，减去系统服务操作，生成本机 `Temp/.../admin20-inputs/activate.sh`。用户在自己的终端运行该脚本，输入一次本机 sudo 密码。不要由 Agent 启动认证、传递密码或直接 sudo 本目录的 Python 文件。

认证命令包含完整固定 bootstrap 和字节哈希；它先校验主机、boot、系统命令、封存的 UFW hooks、所有输入，再把固定代码和二进制输入写入新 root 专有命名空间。后端通过 UID1000 的 Unix socket 共用；客户端验证 root 对端及 root 目录。无 root shell、sudoers、保活或任意命令入口。开始与到期由 root 后端记录，72000 秒后拒绝新操作；重启、撤销、时钟回退或代码变化使能力不可复用。已经使用的命名空间不能重新启动或续时。

开启后非交互调用：

```sh
/usr/bin/python3.14 -I /var/lib/linux-admin-window/codex20-20261008-v1/client.py status
/usr/bin/python3.14 -I /var/lib/linux-admin-window/codex20-20261008-v1/client.py revoke
```

| 范围 | 固定能力 |
|---|---|
| LAN 前置包 | libtss2-fapi1t64 4.1.3-6、tpm2-tools 5.7-1build1；本机下载的固定 SHA 字节；仅一次安装，不升级/移除其他包 |
| LAN 凭据前置 | TPM2 非秘密标记的加密、正确解密、错名拒绝、过期拒绝、原生 UID1000 投射；每步只尝试一次 |
| LAN 应用凭据 | 上述五项通过后，仅固定 CA/server 两个名称，TPM2 加密封存及临时 systemd credential 投射；本地应用内存 SDK，CLI 禁用，原项目权限和实际业务验收仍需满足；无自动创建/轮换/删除身份 |
| LAN 常驻 | 仅 alalapi 的 linger；原执行者以普通用户管理选定的 user service；没有 system service 安装操作 |
| LAN 窄防火墙 | 既有 home/private 接口及身份、443/9417；每项必须绑定新鲜 preimage/hooks/candidate 的一次 arm；原项目的网络信任和具体变更验收仍适用 |
| DATA | 固定 UUID 的 /data 状态；仅 root 空挂载点、原 fstab 不变时挂载；不卸载、不隐藏现有文件 |
| Creator | 官方 Electron 44.6.0 固定 SHA 压缩包，安装至全树 root 所有的 `/opt/creator-workbench-electron-44.6.0`，仅该封存 helper 设 4755；保留原项目 node_modules，不使用 `--no-sandbox` |

Electron 字节已核对[官方校验表](https://github.com/electron/electron/releases/download/v44.6.0/SHASUMS256.txt)。项目采用 Electron 的[沙箱机制](https://www.electronjs.org/docs/latest/tutorial/sandbox)，实际应用启动仍须由原项目执行者验证。

`python3 -B scripts/admin20/test_boundary.py` 验证实际组合后的 gate、所有新增操作的身份/期限/参数限制、范围外拒绝、凭据 CLI 阻断及 bootstrap 的哈希/别名/归档拒绝。包安装没有强杀超时，后端没有到期强杀定时器；72000秒后拒绝新请求，已开始的固定事务允许结束。不执行 root 操作；不能声称完成了 20 小时经过时间验收。认证后必须实际查询可信 status，再验证无副作用的范围外拒绝。范围不包括账号登录、手机/Mac 信任确认、浏览器扩展手动加载、未知未来版本、全局 VPN/DNS/路由、重新启动或业务发布。
