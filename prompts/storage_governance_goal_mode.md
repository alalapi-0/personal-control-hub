# Storage Governance Goal Mode Prompt

- **Reader：**准备启动新一轮本机开发项目存储治理的所有者或 Root。
- **Purpose：**保存唯一可提交给 Codex Goal Mode 的 project-level activation Prompt；静态文件本身不产生 authority。
- **Update trigger：**治理目标、项目清单入口、授权边界、串行规则、每轮读取入口或完成条件发生持久变化。

将以下整段作为一条新消息提交；不要在已激活的续轮中重复提交：

```text
/goal 完成除 manga-localizer 与 personal-control-hub 外全部本机开发项目的逐项目存储治理。先按本提示校正 /Users/alalapi/PycharmProjects/personal-control-hub/data/programs/storage_governance_goal.yaml、Hub 项目清单及其路由的唯一 current-state；以后每轮先读这些入口，由执行 agent 自主决定当前项目、范围、参数、目标路径、迁移方式和验证命令。manga-localizer 仅登记为已完成排除项，不再盘点、验证或改动；personal-control-hub 永驻内盘，不迁移、不清理，只允许本治理所需的记录和管理一致性修复。

从实际项目仓库及关联路径建立并冻结完整清单，严格串行，一个项目闭环后才进入下一个。把每个项目所有经证据判定可安全外置的数据、运行时、依赖、缓存、模型、素材和产物迁移或离线重建到经既有身份守卫验证的 /Volumes/AI_WORK_SSD；切换开发、测试、构建、真实启动和维护入口，禁止内盘静默回退，并逐项目验证前像与回滚、完整性、冷启动、核心行为、重启持久性、错盘/缺盘 fail-closed、内盘残留及清理后状态。已有外盘目录不等于完成。同步记录并修复 Hub 中发现的项目管理问题，保存证据、释放量、阻塞和唯一下一项。

授权参数文件允许的本地迁移、路径与项目修复、验证及通过门禁后的精确清源；其中禁止事项仍不授权。稳定阻塞记录事实与精确恢复条件后继续其他项目。有安全可执行工作时不停在计划或报告；仅当冻结清单逐项实证进入 accepted、protected、ineligible 或带恢复条件的 deferred，且无 pending、active writer 或未消费权限，并核对总释放量、外盘增量、内盘残留和维护方式时结束。
```
