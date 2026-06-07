# External Project Protocol

外部项目默认只读。personal-control-hub 只通过本地路径索引外部仓库，不复制，不直接修改，不自动提交，不推送。

## 默认允许读取

默认只读以下入口文件和规则：

- `README.md`
- `README.txt`
- `AGENTS.md`
- `CLAUDE.md`
- `PROJECT_STATE.md`
- `project.yaml`
- `repo_protocol_standard.yaml`
- `docs/00_start_here.md`
- `docs/README.md`
- `docs/ROADMAP.md`
- `docs/roadmap.md`
- `docs/architecture.md`
- `docs/decision_log.md`
- `.cursor/rules`
- `.cursor/rules/*`
- `.github/workflows/*.yml`
- `.github/workflows/*.yaml`

## 有限扫描目录

允许有限扫描：

- `docs/`
- `governance/`
- `prompts/`
- `skills/`
- `.cursor/`
- `.github/workflows/`

## 禁止扫描

禁止扫描：

- `.git/`
- `node_modules/`
- `dist/`
- `build/`
- `target/`
- `__pycache__/`
- `.venv/`
- `venv/`
- `.idea/`
- `.cache/`
- `cache/`
- `logs/`
- `outputs/`
- `tmp/`
- 大型媒体文件
- 模型文件
- 数据集
- 真实 `.env`

## 大文件规则

单文件大于 1MB 时不全文读取，只记录标题、前若干行、文件大小和路径。确需全文读取时，必须写明 `reason`，并在报告中标出。

## 读取顺序

1. 入口文件。
2. 用关键词搜索少量命中。
3. 读取少量命中文件。

不默认向量库、RAG 或全量 Markdown 扫描。

## Git/GitHub 状态判断

只做设计和占位，可读取：

```bash
git log --oneline -n 20
git status --short
git branch --show-current
git remote -v
```

输出内容包括最近 commit 摘要、当前分支、未提交变更、近期活跃、TODO/FIXME/Roadmap 和建议下一步。禁止 push、checkout、reset。

## 输出边界

扫描结果可以生成 profile、snapshot、priority suggestion 和 next actions。LLM 只能提出 proposal，用户确认后才进入 confirmed link 或优先级变更。
