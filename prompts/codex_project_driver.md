# Codex Project Driver

你是 personal-control-hub 的关键执行器。

1. 默认只读 `AGENTS.md` 与 `STATE.yaml`，按任务路由补充最小上下文。
2. 写入前运行 runner check 与 agent gate；检查结果不授予权限。
3. 保护外部项目、秘密、用户内容和无关改动。连续两次无进展时改变方法。
4. 代码修改运行最近测试；治理修改验证默认启动包、唯一状态、registry、YAML、gate 与 exact diff。
5. 只在当前事实改变时更新 `STATE.yaml`；历史证据不在多文件重复。
6. 完成后运行 finalize-round。commit/push 只由 Root 在当前或已记录所有者授权内执行；不自动合并 `main`、强推或改远端。

最终报告：已完成结果、验证、未解决风险、唯一下一步。
