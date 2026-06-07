# 11 AgentHub 参考分析报告

## 1. 基本信息

| 项 | 内容 |
|---|---|
| **名称** | AgentHub |
| **URL** | https://github.com/Stanshy/AgentHub |
| **本地路径** | `reference_lab/ai_native_company/AgentHub/` |
| **类型** | Electron 桌面 GUI + Claude Code Harness 工程系统 |
| **规模** | 46 Agent、24 Skill、6 Hook 模板、G0–G6 Gate |
| **License** | MIT |
| **Clone 状态** | ✅ 成功 |

## 2. 真正解决的问题

**让聪明的 AI 有纪律**：Hook 硬拦截 + postmortem 跨项目同步；PreToolUse/PostToolUse/Stop Hook 替代 prompt 祈祷；Markdown 任务文件 + FileWatcher + GUI 实时同步；L1/L2 指挥链 + Kanban + Gate 流水线。

## 3. 关键目录表

| 路径 | 用途 |
|------|------|
| `CLAUDE.md` | Agent 索引地图 |
| `.knowledge/doc-governance.md` | 8 条文件治理规则 |
| `.knowledge/quality-checklist.md` | G0–G6 Gate 检查清单 |
| `.knowledge/company/skill-templates/` | 24 个公司级 Skill |
| `.knowledge/company/hook-templates/` | 6 个 Hook 模板 |
| `agents/definitions/` | 46 个 Agent 角色（9 部门） |
| `electron/services/gate-keeper.ts` | Gate 检查项引擎 |
| `electron/services/file-watcher.ts` | `.tasks/` Markdown → GUI |
| `scripts/install-skills.sh` | 全局安装 Skill |

## 4. 可迁移机制

### 4.1 工作流编排

- Sprint 规划 SOP；任务 dispatch → start → done → approve 链。

### 4.2 Agent 角色分工

- 9 部门 46 Agent；L2 不得越级汇报 Boss。

### 4.3 协议/规则

- 文档即法律；IPC 四方同步；8 条 doc-governance。

### 4.4 Skills/Commands

- sop-plan/execute/review/deploy；task-dispatch/start/done/approve 全局安装。

### 4.5 Hooks/CI

- forbidden-commands：拦截 kill-port、--no-verify、force push main。
- stop-validator：测试/类型未通过 Agent 不能结束。
- gate-keeper G2：LLM 信任边界 CRITICAL。

### 4.6 模板/脚手架

- project-templates：web-app、api-service、library、mobile-app。

### 4.7 多仓库治理

- 全局 Skill + 项目 Hook；FileWatcher 监听 `.tasks/`。

## 5. 启发

1. Boss 只做 Gate 审批——适合小说大纲、翻译 QA、视频发布 Gate。
2. 「好验证器 + 差流程 > 好流程 + 无验证器」——Hook 允许重试。
3. Security Gate 应代码化：G2 LLM 信任边界写入 protocol yaml。
4. 全局 Skill 避免 9 repo 各维护 SOP。
5. postmortem-log 跨 repo 继承保护规则。
6. gate-keeper.ts 的 G2 CRITICAL 项移植到 security-release-gate。
7. forbidden-commands 模板写入 lab Hook 库。
8. Kanban/Gate UI 启发 dashboard repo 前端。
9. harness-audit 七原则健康扫描季度运行。
10. 46 Agent 裁剪到 10–15 个角色。
11. agent_gate.py 可解析 G0–G6 checklist YAML 化。
12. task Markdown 格式标准化到各 repo `.tasks/`。
13. install-skills.sh 模式用于 lab 一次性部署。
14. 强绑定 Claude Code——Cursor 需适配 Hook 格式。
15. 与 ai-dev-os 分工：AgentHub 运行期 Harness；ai-dev-os 立项。

## 6. 协议规则要点

- 文档即法律；code+doc 同 PR。
- G0 决定后续 Gate 组合。
- Hook 禁止 --no-verify、force push main。
- G2：AI 输出不直接用于 SQL/shell/eval。

## 7. 治理任务

1. 移植 G0–G6 checklist 到 docs/governance/gate_checklist.yaml。
2. 部署 forbidden-commands Hook 到各 repo .claude/settings.json。
3. 定义 L1/L2 指挥链 per 业务线。
4. dashboard 监听 .tasks/ 或 PROJECT_STATE.md。
5. 实现 agent_gate.py 对齐 gate-keeper criteria。
6. 建立 postmortem-common.md 跨 repo。
7. 裁剪 46→12 Agent 白名单 manifest。
8. 首次 clone 跑 install-skills 文档化。
9. Windows/Linux 终端差异记录。
10. 禁止 GUI 与 repo work_dir 漂移。

## 8. 风险

- 强绑定 Claude Code CLI。
- Windows 优先开发；Linux/macOS 可能受限。
- 手动指挥链；自动分解派发未实现。
- 46 Agent 过重。

## 9. 结论

**最接近「Dashboard + Security Gate + 多 Agent 公司」的可执行参考**。建议优先迁移 Gate checklist、forbidden-commands Hook、全局 Skill 安装模式、postmortem 跨 repo 继承；与 one-person-unicorn-book 理论配对。
