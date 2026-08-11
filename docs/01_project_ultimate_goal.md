# 项目终极目标

personal-control-hub 的终极目标是成为个人项目 OS 和多项目总控入口。

它管理项目、想法、任务、日志、优先级和行动建议；通过本地路径接入外部仓库但不复制；只读取外部项目主要文件；基于 README、AGENTS.md、CLAUDE.md、Cursor rules、repo protocol、roadmap、docs、git log 等判断状态；生成外部项目 profile、snapshot、priority suggestion 和 next actions。

## 项目角色

- 项目控制层: 统一项目入口、状态、路线图和治理规则。
- 索引层: 记录外部项目路径和允许读取的入口文件。
- 计划层: 维护 active programs、任务、next actions 和 program-project links。
- 调度层: 描述每日扫描、每周复盘等计划，但第一阶段不自动执行。
- 通知层: 未来通过 Feishu/Lark 做提醒、消息和移动入口。
- 多项目治理层: 为多个业务 repo 提供只读状态判断、链接提案和风险提示。

## 明确不做

- 不做大模型底座。
- 不做完整 Agent 平台。
- 不做浏览器自动操作平台。
- 不做完整 SaaS。
- 不做大型 dashboard。
- 不做业务 repo monorepo。
- 不做全量扫描。
- 不调用真实 Feishu/Lark API。
- 不调用真实付费模型 API。
- 不存储真实 token。
- 不让模型做最终优先级决策。
- 不直接修改外部项目。
- 不做高风险自动操作。

## MCP 与外部工具治理

personal-control-hub 登记 MCP 能力矩阵与 L0-L3 风险分类，但不替代 Cursor 加载 MCP，也不授予动作权限。六个候选默认 disabled；登记、项目配置、运行时可用和具体动作授权分别判断。新增 MCP 须更新 registry、policy、roadmap；token 仅环境变量。详见 `docs/11_mcp_infrastructure_strategy.md`。

## 现实工作流

Cursor 是日常主力与 MCP 宿主。Codex 是关键执行器，须遵守同一 MCP 策略。ChatGPT 是规划和外部讨论层。personal-control-hub 是三者之间的控制台：把目标、项目状态、MCP 登记、下一步、验证证据和决策记录沉淀为本地文件。

## 成功标准

短期成功是能够登记外部项目、生成可读快照和下一步建议。中期成功是 active programs 与外部项目状态能够互相映射。长期成功是形成个人项目治理循环：计划、执行、扫描、复盘、通知、再计划。
