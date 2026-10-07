# 本机应用与媒体存储

当前迁移清单只保存在 `STATE.yaml#media_apps_storage_goal`。本表用于查看应用和磁盘分工，按应用状态或所有者要求更新。

## 磁盘分工与迁移规则

| 磁盘 | 挂载点 | 用途 |
| --- | --- | --- |
| WD-MEDIA | `/run/media/alalapi/WD-MEDIA` | 电影、电视剧等娱乐媒体 |
| DATA | `/data` | 各项目生成的媒体和二进制产出 |

娱乐媒体只移动**目录名明确标明影视作品名称的完整目录**。散放、不明用途和临时媒体保持原位；游戏、MOD、原始输入、模型、账号、运行数据库和现有备份保持原位。常用媒体目录中目前没有找到符合条件的影视目录，Downloads 的三段 `0924` 视频不移动。

项目按功能逐个确认产出目录，整目录搬到 DATA，同时调整实际生成和导出路径。保留原路径的可用性；缺盘或磁盘 UUID 不符时停止写入。活动任务的重叠文件先保留。由 Root 直接执行，只记录迁移清单，不增加审查角色、备份或逐轮报告。相关检查通过后，仅正常提交、推送已经核对的项目改动到 main，不强推、不覆盖他人的未完成工作。

## 请求安装的应用

| 应用 | 本机形式与当前结果 | 来源 |
| --- | --- | --- |
| 百度网盘 | Flathub 社区封装 8.7.0，包来自百度官方；用户级安装并出现原生初始窗口。旧 DEB 4.11.5 启动失败，其应用列表入口已隐藏 | [Flathub](https://flathub.org/zh-Hans/apps/com.baidu.NetDisk)、[官方包与封装配方](https://github.com/flathub/com.baidu.NetDisk/blob/master/com.baidu.NetDisk.yaml) |
| Discord | 官方 DEB 引导器 1.0.161；完整客户端由官方 stable 引导下载成功并已打开原生窗口，返回版本 1.0.160 | [官方下载](https://discord.com/download) |
| 迅雷 | Flathub 社区包 1.0.0.1，用户级安装；已打开原生窗口。安装时的 `lseek` 警告保留，登录和下载未测试 | [Flathub](https://flathub.org/apps/com.xunlei.Thunder) |
| X | 应用列表入口和独立 Chrome 窗口已安装；本机窗口打开了官方登录页 | [官方浏览器支持](https://help.x.com/en/using-x/x-supported-browsers) |
| TikTok | 应用列表入口和独立 Chrome 窗口已安装；本机窗口打开了官方首页 | [官方桌面网页说明](https://newsroom.tiktok.com/new-features-bring-tiktok-magic-to-desktop?lang=en) |

检查窗口已关闭，没有进行账号登录。百度网盘使用 Flatpak 的外层隔离，未修改宿主系统沙箱或权限。两款社区包安装时均出现 `lseek` 警告，安装退出码为 0；后续登录和实际下载尚未测试。

## 补充应用建议

以下为建议，没有额外安装或部署。

| 应用 | 用途 | 本机安排 |
| --- | --- | --- |
| [qBittorrent](https://www.qbittorrent.org/download) | BT 和磁力下载 | 可优先考虑 |
| MPV、VLC | 本地影视播放 | 已有，继续使用 |
| [Jellyfin](https://jellyfin.org/docs/general/quick-start/) | 在电视、手机上查看片库 | 需要时再部署 |
| [HandBrake](https://handbrake.fr/downloads.php) | 视频转码、压缩 | 按需安装 |
| [MediaInfo](https://mediaarea.net/en/MediaInfo/Download/Ubuntu) | 查看编码、音轨、字幕和 HDR | 按需安装；已有 ffprobe |
| [Telegram](https://desktop.telegram.org/) | 群组、频道和跨设备沟通 | 有官方 Linux 桌面版，按需安装 |
| [Signal](https://signal.org/download/) | 私人通讯 | 有 Linux 桌面版，需要手机端，按需安装 |
