# TC10 — 全局工具钥匙串与统一索引

读者：所有者、Root、独立审查者。更新触发：本单元的凭据路线、配置证据、验证结果或交付改变。当前结果与后续工作以仓库 STATE.yaml 为准；本页不声明整个 Hub Goal 完成。

所有者选择系统钥匙串存密钥、Hub 存索引并适配现有工具。已将三份原明文凭据迁移到独立 Keychain 条目，登记19个全局工具入口与5条凭据路线。保留原 Codex/Cursor Stitch、GitHub CLI 安全路线。Claude Figma 接入真实用户级 MCP 位置并补充必要的 stdio 参数；新进程发现、握手、读取工具枚举通过。配置中的其他字段、项目映射和 OAuth 状态可在受控内存中重建原始字节并匹配原哈希。

认证与存储分开：Figma API身份验证200；Claude旧GitHub凭据迁移前后401，仍无效；Claude独立Stitch握手200但无凭据对照也200，因此认证未验证。没有替换账户、业务数据调用、Figma写入、客户端重启或依赖更新。官方Codex Figma插件仍报OAuth失效，正在等待所有者从连接入口重新授权。

原Codex配置哈希检查发现外部输入漂移，来源未知；原哈希保留，当前文件未被本任务编辑或回滚。仅确认7个MCP入口和认证字段名一致，并固定观察到的当前哈希；没有可靠内容前像，不声称全部旧设置的语义保持。其余受保护文件严格验证原哈希。

证据：`contract.md` 与 `contract-review.json` 为v2合同及独立激活；`baseline.json` 保留原始基线与外部漂移；`migration.json` 是初次Figma迁移；`claude-registration.json` 为最终Claude切换、脱敏原像和行为证据；`existing-routes.json`、`claude-discovery.json`、`mcp-validation.json` 为消费者验证。最终候选、Judge、Governor与Git交付由Root在验收后登记。

相关复核命令：

```sh
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover -s tests -p test_tool_credentials.py
PYTHONDONTWRITEBYTECODE=1 python3 docs/reports/ui_design_governance/unit-10/verify.py
PYTHONDONTWRITEBYTECODE=1 python3 docs/reports/ui_design_governance/unit-10/mcp_probe.py
python3 scripts/check_repo.py
python3 scripts/round_consistency_check.py
git diff --check
```

`verify.py` 只在内存中读取迁移条目、恢复旧MCP片段以验证原配置哈希，并扫描任务文件及暂存区，不返回值或值哈希；不重复提供方身份请求。`mcp_probe.py` 使用实际配置命令、临时禁遥测与离线npm，仅读取协议能力后关闭自己的进程。钥匙串登录项的旧交互行为曾导致一次受控读取等待；已终止本任务的精确探测进程，并补充进程级禁止可选钥匙串UI。原安全启动器保持不变。

恢复时保留Keychain和取用器，修正具体失效路线；不得把明文放回配置。无当前精确前像时不回滚全局文件。Hub源码路径是Claude启动依赖；若以后移动仓库，应同步三条启动引用并重新验证。仅Hub内非秘密材料进入当前跟踪分支；全局配置、Keychain、其他任务dirty、Figma观察文件和旧交付元数据不进入本单元提交。
