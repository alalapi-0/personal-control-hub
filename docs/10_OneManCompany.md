# 10 OneManCompany 参考分析报告

## 1. 基本信息

| 项 | 内容 |
|---|---|
| **名称** | OneManCompany |
| **URL** | https://github.com/1mancompany/OneManCompany |
| **本地路径** | `reference_lab/ai_native_company/OneManCompany/` |
| **类型** | Agent OS（Python 后端 + 像素办公室前端 + Talent Market） |
| **启动** | `npx @1mancompany/onemancompany` |
| **Clone 状态** | ✅ 成功 |

## 2. 真正解决的问题

在 Agent 框架之上建 **「公司模拟 OS」**：唯一人类 CEO；EA/HR/COO/CSO 等为内置 AI 高管；任务树 + 状态机；会议、绩效、雇佣/解雇；Vessel + Talent Market 分离；`company/` 下 Markdown SOP 可被 workflow_engine 解析执行。

## 3. 关键目录表

| 路径 | 用途 |
|------|------|
| `src/onemancompany/core/vessel.py` | 核心执行容器 |
| `src/onemancompany/core/task_tree.py` | 层级任务分解 |
| `src/onemancompany/core/workflow_engine.py` | 解析 company workflows |
| `company/human_resource/` | 员工 profile、skills、hooks |
| `company/operations/sops/` | 任务生命周期、派单、验收 SOP |
| `company/shared_prompts/` | 跨角色工程/风险规范 |
| `scripts/import_github_talent.py` | 从 GitHub 仓导入 talent（读 AGENTS.md） |
| `tests/unit/core/` | vessel、workflow 单测 |
| `CLAUDE.md` | Skill 路由表（/ship、/qa 等） |

## 4. 可迁移机制

### 4.1 工作流编排

- Markdown workflow（Flow ID、Phase、Depends on）+ workflow_engine；任务树父子派发/验收。

### 4.2 Agent 角色分工

- 组织图 CEO→EA→HR/COO/CSO→工程师；Talent Market 雇佣。

### 4.3 协议/规则

- task_lifecycle_states.md、workflow_schema.md、code_engineering_standards.md 应并入 protocol yaml。

### 4.4 Skills/Commands

- 每员工 skills/*/SKILL.md；hooks/pre-tool.sh；CLAUDE.md 强制 skill 路由。

### 4.5 Hooks/CI

- hooks/pre-tool.sh；CI 强制版本 bump；171+ unit tests。

### 4.6 模板/脚手架

- profile_template.yaml、talent manifest.json。

### 4.7 多仓库治理

- import_github_talent.py 跨 repo 拉 AGENTS.md；dashboard 仓作轻量 OMC 只编排。

## 5. 启发

1. **completed vs accepted** 两阶段验收映射 Round「Agent 自称完成」与「你验收通过」。
2. dispatch_child / accept_child 用于小说「章节子任务」树形依赖。
3. task_dispatch_and_acceptance_sop.md 提炼进 repo_protocol_standard.yaml。
4. Talent Market：优秀 prompt/员工包版本化。
5. self-improving-agent hooks 接近 AgentHub 级硬约束。
6. workflow_schema 的 Phase/Depends on 升级 ROADMAP 为机器可读 workflow。
7. Vessel 抽象：执行引擎与人格包分离。
8. import_github_talent 检测 AGENTS.md——dashboard 索引全 lab 合规性。
9. CI 版本递增 → protocol_version 递增。
10. 像素 UI 非必需；要 task tree 可视化接 PROJECT_STATE.md。
11. CLAUDE.md「先 invoke skill 再回答」写入全局 AGENTS.md。
12. remote_protocol 外部 Agent 作编外员工。
13. 会议机制用于跨 repo 协调，需控制 token。
14. agent_gate.py：gate task-tree 校验状态机合法迁移。
15. 编排层与执行层分 repo。

## 6. 协议规则要点

- 任务状态机：仅 accepted/finished 解锁下游。
- Workflow 必有 Flow ID、Owner、Phase Goal。
- 员工包：profile.yaml + manifest.json + skills + hooks。
- PR CI：pyproject.toml 版本必须大于 main。

## 7. 治理任务

1. lab 根或 dashboard 建轻量 company/ 目录：EA/COO/QA + SOP。
2. 将 task_lifecycle_states 写入 protocol yaml 的 round_states 节。
3. 各 repo PROJECT_STATE.md 增加任务树链接。
4. 从 code_engineering_standards 摘 10 条进 AGENTS.md。
5. agent_gate.py：check version + workflow md schema + check_repo。
6. docs/talent/ 存放经过验证的 Agent 包。
7. 定义编外员工 remote_worker 接口。
8. 每 10 Round 跑 retrospective workflow。
9. import_github_talent 改为扫描 9 个 repo AGENTS.md。
10. 避免 OMC 全栈并入 TTS/小说仓。

## 8. 风险

- 复杂度高；单人易过载。
- 依赖外部 Agent 质量不稳定。
- 会议/多 Agent token 成本高。
- 与 gh-aw/Kortix 功能重叠。

## 9. 结论

**一人公司组织层与任务治理** 的最贴近愿景参考：任务树、验收状态机、Markdown workflow、员工 skill 包、hooks、Talent 导入。应作为 dashboard/meta-orchestration 层，与 protocol yaml、AGENTS.md、agent_gate.py 结合形成「CEO + EA 指挥九条产品线」轻量实现。
