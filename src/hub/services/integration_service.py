"""Pure local projections. No network client, credential lookup or write-back."""
from __future__ import annotations

import json

from hub.project_service import validate_snapshot

FEISHU_CONFIG = {"enabled": False, "config_source": "env",
                 "env_keys": ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_WEBHOOK_URL"],
                 "write_back_allowed": False, "requires_user_confirmation": True}


def _plain(value: object) -> str:
    if value is None:
        return "unknown"
    if type(value) is str:
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def row_summary(row: dict) -> str:
    latest = row["latest_attempt"]
    record = latest if latest and latest["success"] else row["last_success"]
    lines = [f"{row['name']} ({row['project_id']})",
             f"读取: {latest['disposition'] if latest else 'not_refreshed'}; 新鲜度: {row['freshness']['state']}",
             f"原因: {row['freshness']['reason'] or 'none'}", f"更新标识: {row['update_key']}"]
    if latest:
        lines.append("最新读取证据（包括失败，独立于历史成功）：")
        for key in ("observed_at", "disposition", "errors", "declaration", "sources", "freshness"):
            lines.append(f"latest_attempt.{key}: {_plain(latest[key])}")
    if not record:
        lines.append("业务状态、进度、阻塞及下一步: unknown；尚无有效来源。")
    else:
        if latest is None or not latest["success"]:
            lines.append("以下是单独保留的上次成功历史快照，不能替代上面的最新失败证据。")
        elif row["freshness"]["state"] != "fresh":
            lines.append("以下是历史快照，不能作为当前完成或验收依据。")
        business = record["business"]
        for group in ("current_work", "progress", "blockers", "verification", "delivery"):
            lines.append(f"{group}: {_plain(business[group])}")
        lines.append("validation_entry (display only): " + _plain(record["validation_entry"]))
        lines.append("来源: " + _plain(record["sources"]))
        lines.append("逐字段依据: " + _plain(record["field_provenance"]))
        lines.append("未知字段原因: " + _plain(record["unknown_fields"]))
    return "\n".join(lines)


def markdown_preview(snapshot: dict) -> str:
    validate_snapshot(snapshot)
    lines = ["# 本地项目状态预览", "", "这是 Hub 来源投影；真实飞书连接保持 disabled。", "",
             "覆盖: " + _plain(snapshot["coverage"]), ""]
    for row in snapshot["projects"]:
        lines.extend(["## " + row["project_id"], "", "```text", row_summary(row).replace("```", "'''"), "```", ""])
        # A lossless normalized row accompanies the readable summary. Consumers
        # can verify every field, including failed and historical evidence.
        lines.extend(["完整规范记录（latest_attempt 与 last_success 分开保留）：", "", "```json",
                      json.dumps(row, ensure_ascii=False, sort_keys=True, indent=2), "```", ""])
    return "\n".join(lines)


def feishu_preview(snapshot: dict) -> dict:
    validate_snapshot(snapshot)
    return {"schema_version": "2.0", "kind": "disabled_feishu_preview", "config": dict(FEISHU_CONFIG),
            "coverage": snapshot["coverage"], "write_policy": "preview_only; no credential reads, sends, spaces or write-back",
            "updates": [{"project_id": row["project_id"], "update_key": row["update_key"], "source_record": row,
                         "card": {"config": {"wide_screen_mode": True},
                                  "header": {"title": {"tag": "plain_text", "content": row["name"]}},
                                  "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": row_summary(row)}}]}}
                        for row in snapshot["projects"]]}
