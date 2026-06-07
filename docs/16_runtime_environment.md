# Runtime Environment

## 1. 文档目的

本文统一 personal-control-hub 的运行环境要求、检查方式与当前阶段边界。机器可读版本见 `data/runtime/environment_requirements.yaml`，检查结果见 `data/runtime/toolchain_status.yaml`，验证命令见 `data/runtime/validation_commands.yaml`。

## 2. 当前项目运行环境目标

- 本地优先、Cursor 优先、Codex 关键执行。
- Python 脚本作为 gate、环境检查、轮次一致性检查和 auto advance runner 的入口。
- Git 用于轮次完成后的 commit/push，但 push 失败必须停止。
- Node/npm 当前可选，为未来 UI、Playwright 和部分 MCP 预留。
- MCP 只登记与治理，脚本不强行启用、不调用真实 MCP。
- 不写真实 token，不自动安装未知依赖。

## 3. Python 环境要求

- 推荐使用项目根目录 `.venv`。
- 不强制全局安装。
- 脚本入口优先使用 `python scripts/...`（或 `python3 scripts/...`）。
- 当前依赖尽量保持标准库 + PyYAML；不自动安装未知包。
- 最低版本：Python 3.10；推荐 3.11+。

检查命令：

```bash
python scripts/check_environment.py
python scripts/check_environment.py --json
```

## 4. Node 环境要求

- 当前阶段仅作为未来 UI / Playwright / 部分 MCP 工具可能需要的环境。
- 不在 Round 0.7 强制初始化前端项目。
- 不强制安装 npm 包。
- Node/npm 缺失时只产生 soft warning，不阻断文档与脚本推进。

## 5. Git 环境要求

必须能运行：

- `git status`
- `git add`
- `git commit`
- `git push`

规则：

- `auto_advance_runner.py --mode finalize-round` 在 push 失败时必须停止。
- 不自动解决 merge conflict。
- 未配置 remote 时 push 会失败并停止。
- 当前工作区若不在 git 仓库中，环境检查会产生 warning。

## 6. Cursor 环境要求

- Cursor 是主要 MCP 宿主环境与日常开发环境。
- 当前已知 MCP（登记在 registry，不在脚本中强行启用）：
  - chrome-devtools
  - context7
  - filesystem
  - github
  - playwright
  - stitch
- MCP 启用状态需在 Cursor Workspace MCP Servers 中人工确认。
- 配置参考：`.cursor/mcp.example.json`、`docs/13_cursor_mcp_workspace_setup.md`。

## 7. Codex 环境要求

- Codex 用于关键轮次推进与高质量代码执行。
- 本项目生成 Codex prompt（如 `data/codex_queue/next_round_prompt.md`）。
- 不默认自动调用 Codex。
- 若后续要自动调用 Codex，需要单独 round 和用户审批。

## 8. MCP 环境要求

- MCP 能力登记在 `data/mcp/mcp_capability_registry.yaml`。
- 审批策略在 `data/mcp/mcp_approval_policy.yaml`。
- L0/L1 可默认可用；L2/L3 必须在具体动作前停止确认。
- `check_environment.py` 只检查配置文件是否存在，不调用真实 MCP。
- `auto_advance_runner.py` 不调用 MCP。

## 9. Feishu / Lark 集成环境要求

- 当前只做配置占位与策略文档。
- 不写真实 webhook。
- 不真实发送消息。
- 后续可作为通知和移动端入口（见 `docs/09_feishu_lark_strategy.md`）。

## 10. GitHub 同步环境要求

- 轮次完成后由 `auto_advance_runner.py --mode finalize-round` 在验证通过后执行 commit/push。
- push 失败、认证失败、merge conflict、敏感文件检测时必须停止。
- Round 0.9 将专门验证 push 工作流；Round 0.7 不自动执行真实 push（除非用户明确确认）。

## 11. 不允许写入仓库的敏感信息

- `.env`、`.env.*`
- `*.pem`、`*.key`
- `id_rsa`、`id_ed25519`
- `secrets.*`
- 含 OPENAI_API_KEY、FEISHU_APP_SECRET、GITHUB_TOKEN 等密钥赋值内容

## 12. 环境检查命令

```bash
python scripts/check_environment.py
python scripts/round_consistency_check.py
python scripts/agent_gate.py
python scripts/auto_advance_runner.py --mode check
python scripts/check_repo.py
```

## 13. 常见问题

**Q: Python 版本够但 check 报 version_too_low？**  
A: 确认 `python --version` 输出为 3.10+。

**Q: Git 可用但报 warning_not_in_repo？**  
A: 当前目录未初始化 git；finalize-round 无法 commit/push。

**Q: Node 未安装是否阻断？**  
A: 否，只产生 soft warning。

**Q: Cursor MCP 如何确认？**  
A: 在 Cursor Settings → MCP 中查看；本仓库脚本不代为启用。

## 14. 当前阶段不做的事情

- 不自动安装 MCP 或 npm 包。
- 不调用真实外部 API 或 MCP。
- 不写真实 token。
- 不修改外部项目本体。
- 不在 Round 0.7 自动 push（除非用户明确确认）。
