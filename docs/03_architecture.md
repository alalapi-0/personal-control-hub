# Architecture

## 文档职责

- 读者：处理系统边界、数据权威或新适配器的开发者与 Agent。
- 目的：描述当前 v2 分层，不保存进度。
- 更新触发：权威层、数据流或集成边界改变。

## v2 分层

1. 启动层：`AGENTS.md` + `STATE.yaml`，完整默认包不超过 8KB。
2. 方向层：`NORTH_STAR.md` 与 `project.yaml`，只在重大决策时读取。
3. 注册层：`data/registry/external_projects.yaml` 保存项目 ID、便携路径和权威指针。
4. 适配层：`governance/adapters/` 为各子项目定义条件路由和写入边界，不复制子项目规范。
5. 策略层：`governance/agent_policy.yaml`、`data/gates/` 和按任务协议。
6. 证据层：`docs/reports/`、Git commit 和外部项目自身证据；历史不进入默认上下文。
7. 服务层：`src/hub/` 与 `scripts/` 提供注册、检查、投影和未来 UI/集成能力。

## 权威与数据流

- 当前状态只写 `STATE.yaml`。
- 项目名单只写 registry。
- 长期方向只写 North Star。
- 旧 `governance/round_state.yaml`、`data/state/current_status.yaml` 与长协议保留为兼容/历史材料。
- 执行子项目任务时：默认包 → registry/adapter → 子项目唯一规范 → 最小证据 → 状态/报告 → accepted Git 交付。

## 安全边界

外部项目默认只读；hub 不取得子项目删除或写入权限。飞书、MCP、GitHub 与浏览器工具都是受控适配器，登记或配置不等于可用性或动作授权。秘密不入库，runner 不做 Git 写入。
