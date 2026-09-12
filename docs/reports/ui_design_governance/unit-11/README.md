# TC11 — 恢复 Codex 本机 Figma 入口

读者：所有者及后续 Root。更新触发：本机配置修复或验收改变。Hub 当前状态仍只由 STATE.yaml 管理。

已查明账号端插件 installed/user_enabled 均为 true，操作权限为 Allow all actions，但 Codex 本机 `apps.connector_68df038e0ba48191908c8434991bbac2.enabled` 为 false；本新回合工具目录没有 Figma。官方配置说明确认该布尔值控制特定 app/connector 的启用：[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)。

Root 只把 false 改成 true。反向替换唯一布尔 token 后复现修改前的整个文件 SHA，证明其他字节未变；文件权限保持0600。未重装插件、改权限、改Key、重连OAuth或重启任何客户端。修复文件不代表当前回合已热加载；下一新回合的工具目录和官方 whoami 是独立待验证事项。

`baseline.json` 保存本轮新前像哈希、无秘密结构、源文件模式和运行时前态；它不改写 TC10 历史观察。`validation.json` 保存精确写入结果，`verify.py` 检查布尔差异、TOML、原像重建、其他配置和既有 Hub 成果。复核命令：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 docs/reports/ui_design_governance/unit-11/verify.py
python3 scripts/check_repo.py
python3 scripts/round_consistency_check.py
git diff --check
```

仅验收后的 Hub 证据与 STATE 进入跟踪分支；本机配置不进入 Git。保留已启用状态，运行时未加载或OAuth失败不能作为再次禁用或重置配置的理由。全Hub/Figma选稿与产品验收仍未完成。
