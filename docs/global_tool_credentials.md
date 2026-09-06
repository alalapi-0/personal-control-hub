# 全局工具凭据

供所有者和维护工具配置的 Agent 使用。新增、更换或删除全局工具凭据时，更新 [统一索引](../data/credentials/global_tools.json)。索引是凭据位置与使用方的唯一机器可读登记；密钥值保存在系统钥匙串或既有提供方凭据存储中。

| 凭据 | 存储与使用方 | 验证结果 |
| --- | --- | --- |
| Figma API Key | macOS 钥匙串；Claude 用户级 Figma MCP 和获准的只读脚本 | 已迁移。`/v1/me` 返回 200；Claude 新进程显示 Connected，实际 MCP 握手和两个读取工具枚举通过。 |
| 既有 Stitch API Key | 原 macOS 钥匙串条目；Codex、Cursor 原启动器 | 保持原路径；两个启动器的新进程取用通过。 |
| 既有 GitHub CLI 凭据 | GitHub CLI 管理的凭据存储；Cursor 原启动器 | 保持原路径；新进程取用通过。 |
| Claude 独立 GitHub 凭据 | 独立 macOS 钥匙串条目；Claude GitHub MCP | 已迁移，原明文已移除；迁移前后 `/user` 均为 **401，认证无效**。未改接另一个账户。 |
| Claude 独立 Stitch 凭据 | 独立 macOS 钥匙串条目；Claude Stitch MCP | 已迁移，原明文已移除；握手均返回 200。无凭据对照也成功，因此**认证有效性尚未验证**。 |

盘点覆盖 19 个有效全局工具入口：Codex 7、Cursor 6、Claude 6，以及其直接引用的凭据启动器和实际存在的全局 shell 启动文件。发现的三份明文工具凭据均已迁入钥匙串。Claude 的 GitHub、Stitch 凭据与既有安全路线不同，分别保存。项目业务 `.env`、浏览器会话、私钥和无关账户资料不属于此盘点。

## 钥匙串位置

以下条目的账户均为 `alalapi`。服务名可在 macOS“钥匙串访问”中搜索。

| 索引 ID | 钥匙串服务名 | 运行时变量 |
| --- | --- | --- |
| `figma-api` | `com.openai.codex.mcp.figma-api-key` | `FIGMA_API_KEY` |
| `stitch-api` | `com.openai.codex.mcp.stitch-api-key` | `STITCH_API_KEY` |
| `claude-github-token` | `com.openai.codex.mcp.claude-github-token` | `GITHUB_PERSONAL_ACCESS_TOKEN` |
| `claude-stitch-api` | `com.openai.codex.mcp.claude-stitch-api-key` | `STITCH_API_KEY` |

GitHub CLI 凭据继续由 `gh` 管理。既有 Stitch、GitHub 启动器的路径均在统一索引中登记。

## 检查和使用

在 Hub 根目录检查新迁移条目，只返回取用状态，不返回密钥：

```sh
/usr/bin/python3 scripts/tool_credentials.py check figma-api
/usr/bin/python3 scripts/tool_credentials.py check claude-github-token
/usr/bin/python3 scripts/tool_credentials.py check claude-stitch-api
```

`available: true` 表示能从钥匙串读取，不代表服务端认证通过。Claude 用户级配置 `~/.claude.json` 已接入取用器；原 `~/.claude/settings.json` 中不生效的 Figma 声明已移除。其他获准脚本可通过以下方式读取 Figma 凭据：

```sh
/usr/bin/python3 scripts/tool_credentials.py run figma-api -- /usr/bin/python3 your_figma_script.py
```

脚本在进程内读取 `FIGMA_API_KEY`。取用器仅向直接启动的进程注入环境变量，不经过 shell 拼接；密钥缺失、访问拒绝、钥匙串锁定或空值都会在工具启动前退出。它不会回退到原明文或继承的旧值。

更换凭据时，在“钥匙串访问”中按服务和账户定位条目；之后重新检查取用与相应服务的认证。不要把新值填回 JSON、索引或聊天。Claude GitHub 的失效凭据可以单独更换；要改用当前 GitHub CLI 账户，应先明确账户选择。

## Figma 写入与连接

当前 API Key 对接的 `figma-developer-mcp` 实际提供 `get_figma_data`、`download_figma_images` 两个读取工具。画布写入使用官方远程 Figma MCP 的 OAuth 连接，需要 Full seat；修改已有文件还需编辑权限。[官方画布写入说明](https://developers.figma.com/docs/figma-mcp-server/write-to-canvas/)

Codex 官方 Figma 插件是独立连接：最近界面显示已连接，但本任务调用仍返回 `oauth_token_invalid_grant`。需要从 [Figma 插件连接入口](https://chatgpt.com/plugins/figma) 完成重新授权；现有 API Key 的可用性不能替代这一步。[Codex 配置步骤](https://developers.figma.com/docs/figma-mcp-server/remote-server-installation/#codex)

新进程取用已验证；现有客户端未被重启，是否采用新配置未验证。验证过程中关闭了 Figma 包遥测并使用离线 npm 缓存，未调用设计数据工具或更新依赖。

TC10 还观察到本任务写入范围外的 Codex 全局配置哈希变化：当前内容已保留，七个 MCP 入口核对一致；因没有可靠内容前像，不声称全部旧字段完全一致。Claude 的其他字段、项目映射和 OAuth 状态则通过原始字节重建校验，证明未变。

取用器使用 Apple [Keychain Services](https://developer.apple.com/documentation/security/adding-a-password-to-the-keychain)。独立验收、验证命令和恢复说明见 [TC10 证据入口](reports/ui_design_governance/unit-10/README.md)。
