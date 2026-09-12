# Personal Control Hub 增量恢复 · Codex Goal

读者：所有者与执行者。用途：从已保存断点恢复 Hub 完成交付。更新触发：目标、授权或执行规范入口改变。

保存或阅读本文件不激活 Goal。原任务保持暂停；在本仓库的任务中发送下面整段才启动。详细范围、外部路径、归并与 Git 参数均在[执行规范](../docs/design/ui_governance_execution.md)，不复制进 Prompt。原生 `/goal` 用法参见 [OpenAI 官方说明](https://learn.chatgpt.com/docs/long-running-work)。

```text
/goal 从 STATE.yaml 的断点继续 personal-control-hub 治理。每轮读取 AGENTS.md、STATE.yaml，并按 docs/design/ui_governance_execution.md 增量推进，复用有效成果。

完成可日常使用的 Hub、全部真实项目连接和设计审核闭环，归并工作区外相关项目管理材料。Figma 方案由我选择后实现；保护其他项目与并发任务。每个验收通过的交付单元提交并正常推送当前跟踪分支，验证远端提交。

按规范完成全部验收与独立交接说明后结束；收到暂停立即停止本任务写者并保存恢复点。
```
