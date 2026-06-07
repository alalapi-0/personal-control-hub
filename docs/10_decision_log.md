# Decision Log

本文件保存人类可读的关键决策摘要。机器可追加日志使用 `data/logs/project_decision_log.jsonl`。

## 2026-06-07: Project Restart Skeleton

- 决策: personal-control-hub 定位为个人项目 OS 和多项目总控入口。
- 决策: 第一阶段采用本地 Markdown、YAML、JSONL 和 Python 标准库。
- 决策: 外部项目默认只读，通过本地路径接入，不复制、不修改。
- 决策: Feishu/Lark 只做策略和 disabled 占位，后续先 mock adapter，再真实 adapter。
- 决策: Cursor 是日常主力，Codex 是关键执行器，ChatGPT 是规划和外部讨论层。
- 决策: LLM 只能提出 priority proposal，用户确认后才进入 confirmed links 或最终优先级。
