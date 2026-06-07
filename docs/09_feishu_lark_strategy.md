# Feishu / Lark Strategy

Feishu/Lark 是未来通知、提醒、消息和移动入口，不是 Round 0 的真实集成对象。

## Round 0 边界

- 不调用真实 Feishu/Lark API。
- 不写 token、secret、webhook、cookie。
- 不真实发消息。
- 不连接真实空间。
- 不写入飞书文档、多维表格或群聊。

## 阶段设计

1. Strategy only: 当前文档和 `data/integrations/integration_targets.yaml`。
2. Mock adapter: 生成本地 JSON/Markdown 消息预览。
3. Manual confirmation: 用户检查消息、目标和权限。
4. Real adapter: 用户明确确认后才接真实 API。

## 配置原则

Feishu/Lark 配置只允许声明：

- `enabled: false`
- `config_source: env`
- `env_keys`
- `write_back_allowed: false`
- `requires_user_confirmation: true`

允许的环境变量名：

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_WEBHOOK_URL`

不要在仓库写入真实值。

## 消息范围

未来可以发送：

- 每日项目扫描摘要。
- 每周项目复盘。
- high-risk confirmation request。
- next actions。
- Codex/Cursor prompt queue 摘要。

第一阶段全部只生成本地预览。
