"""TC17 authored demonstration content; never loads project or learner data."""

LANE = "linux-foundations"
ROUNDS = [{
    "id": "round_00", "title": "Terminal 初见", "lane": LANE,
    "weeks": [{"title": "示例练习", "tasks": [
        {"id": "w1-fixture-read", "title": "认识终端", "type": "reading", "file": "fixture/terminal.md"},
        {"id": "w1-fixture-practice", "title": "辨认当前位置", "type": "exercise", "file": "fixture/paths.md"},
        {"id": "w1-fixture-check", "title": "复述路径与命令", "type": "test", "file": "fixture/check.md"},
    ]}],
}]
TASK_IDS = tuple(task["id"] for item in ROUNDS for week in item["weeks"] for task in week["tasks"])
PROGRESS = {
    "version": 1, "revision": 0,
    "lanes": {LANE: {"course_id": LANE, "title": "Linux 基础与工程实践", "description": "合成演示"}},
    "tasks": {key: {"done": False, "done_at": None, "lane": LANE} for key in TASK_IDS},
}
FEEDBACK = {key: {
    "task_id": key, "lane": LANE, "done": False, "action_count": 0,
    "last_action_type": None, "last_action_at": None, "feedback_type": "not_started",
    "message": "合成示例，尚未开始。", "next_suggestion": "阅读示例资料。",
} for key in TASK_IDS}
MARKDOWN = {
    "/fixture/terminal.md": "# 认识终端\n\n合成演示资料。终端用于输入命令并查看文字结果。\n\n## 先观察，再行动\n\n- `pwd`：显示当前位置。\n- `ls`：列出当前目录内容。\n- `cd`：切换目录。\n\n本演示仅供阅读；命令执行、记录与存档均停用。\n",
    "/fixture/paths.md": "# 辨认当前位置\n\n合成演示资料。假设当前位置是 `~/practice`。\n\n1. 说明绝对路径与相对路径的区别。\n2. 阅读 `pwd` 的含义。\n3. 用自己的话解释 `..`。\n\n本页不会访问真实目录或执行命令。\n",
    "/fixture/check.md": "# 复述路径与命令\n\n合成自测：\n\n- 哪个命令显示当前位置？\n- 如何表示上一级目录？\n- 为什么执行命令前应先确认当前位置？\n\n答案仅供口头复述；此隔离演示不保存学习记录。\n",
}
for path, title in (
    ("/rounds/round_00/final/command_cheatsheet.md", "Terminal 命令小抄"),
    ("/rounds/round_01/final/command_cheatsheet.md", "文件系统小抄"),
    ("/rounds/round_02/final/command_cheatsheet.md", "Shell 与 Git 小抄"),
    ("/rounds/round_06/final/linux_automation_cheatsheet.md", "Linux 自动化小抄"),
    ("/plans/linux/README.md", "Linux 课程路径"),
):
    MARKDOWN[path] = f"# {title}\n\n合成演示资料，与真实课程文件无关。\n\n先认识终端，再辨认路径，最后复述基础概念。\n\n本演示不会执行命令或修改学习记录。\n"

GET_JSON = {
    "/progress.json": PROGRESS,
    "/api/health": {"ok": True, "synthetic": True, "read_only": True},
    "/api/events": {"version": 1, "events": [], "by_task": {}},
    "/api/feedback": {"version": 1, "feedback": FEEDBACK},
    "/api/saves": {"version": 1, "saves": []},
    "/api/terminal": {"ok": True, "terminal": {"cwd": "", "cwd_display": "~", "allowed": []}},
}
