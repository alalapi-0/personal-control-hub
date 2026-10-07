"""Isolated browser fixture for UI regression tests.

``FixtureServer()`` is read-only. Tests that explicitly pass
``task_actions=True`` get a task done/undo API whose entire state lives in
memory. This module deliberately does not import the production progress
library and never writes progress, records, terminal state, saves, or
``~/cli-lab``.
"""

from __future__ import annotations

import json
import re
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import NAMESPACE_URL, uuid5


REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_ACTION_PATH = re.compile(r"^/api/tasks/([A-Za-z0-9._-]+)/(done|undo)$")
TASK_RUN_PATH = re.compile(r"^/api/tasks/([A-Za-z0-9._-]+)/run$")
SAVE_LOAD_PATH = re.compile(r"^/api/saves/([A-Za-z0-9._-]+)/load$")
OPERATION_ID = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
TASK_ACTION_TEXT_LIMIT = 500


def _action_id(kind: str, sequence: int) -> str:
    """Return a deterministic canonical UUID unique to this fixture event."""
    return str(uuid5(NAMESPACE_URL, f"learning-ui-fixture:{kind}:{sequence}"))


def _round_id(task_id: str) -> str:
    if re.match(r"^(w\d+-|fin-)", task_id):
        return "round_00"
    if task_id.startswith("r01-"):
        return "round_01"
    if task_id.startswith("r02-"):
        return "round_02"
    if task_id.startswith("r06-"):
        return "round_06"
    if task_id.startswith("vps-"):
        return "vps"
    return "unknown"


class _FixtureHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


class FixtureHandler(SimpleHTTPRequestHandler):
    server_version = "LearningUIFixture/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(REPO_ROOT), **kwargs)

    @property
    def fixture(self) -> dict:
        return self.server.fixture  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args) -> None:
        return

    def _record(self, method: str) -> None:
        with self.fixture["lock"]:
            self.fixture["requests"].append((method, urlparse(self.path).path))

    def _json(self, status: HTTPStatus, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # A planned action timeout intentionally leaves the handler alive
            # after Chromium has aborted its request.
            return

    def _progress_payload(self) -> dict[str, Any]:
        with self.fixture["lock"]:
            return {
                "version": self.fixture["progress"]["version"],
                "revision": self.fixture["revision"],
                "lanes": deepcopy(self.fixture["progress"]["lanes"]),
                "tasks": deepcopy(self.fixture["tasks"]),
            }

    def _events_payload(self) -> dict[str, Any]:
        with self.fixture["lock"]:
            events = deepcopy(self.fixture["events"])
        by_task: dict[str, list[dict[str, Any]]] = {}
        for event in reversed(events):
            by_task.setdefault(event["task_id"], []).append(event)
        return {"version": 1, "events": events, "by_task": by_task}

    def _feedback_payload(self) -> dict[str, Any]:
        with self.fixture["lock"]:
            feedback = deepcopy(self.fixture["feedback"])
        return {"version": 1, "feedback": feedback}

    @staticmethod
    def _apply_response_plan(payload: dict[str, Any], plan: dict[str, Any] | None) -> dict[str, Any]:
        wire_payload = deepcopy(payload)
        mutator = (plan or {}).get("response_mutator")
        if callable(mutator):
            mutator(wire_payload)
        for key, value in (plan or {}).get("response_overrides", {}).items():
            wire_payload[key] = deepcopy(value)
        return wire_payload

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        self._record("GET")
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            self._json(HTTPStatus.OK, {"ok": True, "service": "ui_fixture"})
            return
        if path == "/api/events":
            payload = self._events_payload() if self.fixture["task_actions"] else {
                "version": 1,
                "events": [],
                "by_task": {},
            }
            self._json(HTTPStatus.OK, payload)
            return
        if path == "/api/feedback":
            self._json(HTTPStatus.OK, self._feedback_payload())
            return
        if path == "/api/saves":
            self._json(HTTPStatus.OK, {"version": 1, "saves": []})
            return
        if path == "/api/terminal":
            requested_cwd = parse_qs(parsed.query).get("cwd", [""])[0]
            cwd_display = requested_cwd or "~"
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "terminal": {
                        "cwd": requested_cwd,
                        "cwd_display": cwd_display,
                        "allowed": ["pwd", "ls", "find"],
                    },
                },
            )
            return
        if path == "/progress.json":
            delay = float(self.fixture.get("progress_delay", 0))
            if delay > 0:
                time.sleep(delay)
            if self.fixture["task_actions"]:
                payload = self._progress_payload()
                with self.fixture["lock"]:
                    plan = self.fixture["progress_plan"].pop(0) if self.fixture["progress_plan"] else None
                self._json(HTTPStatus.OK, self._apply_response_plan(payload, plan))
                return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        self._record("POST")
        length = int(self.headers.get("Content-Length") or 0)
        raw_body = self.rfile.read(length) if length else b"{}"
        if not self.fixture["task_actions"]:
            self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"ok": False, "error": "fixture_is_read_only"})
            return

        path = urlparse(self.path).path
        match = TASK_ACTION_PATH.fullmatch(path)
        run_match = TASK_RUN_PATH.fullmatch(path)
        save_match = SAVE_LOAD_PATH.fullmatch(path)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_json"})
            return
        if not isinstance(payload, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_payload"})
            return

        if run_match:
            self._task_run(run_match.group(1), payload)
            return
        if save_match:
            self._load_save(save_match.group(1))
            return
        if not match:
            self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"ok": False, "error": "fixture_task_actions_only"})
            return

        task_id, action = match.group(1), match.group(2)
        operation_id = payload.get("operation_id", "")
        note = payload.get("note", "")
        evidence_path = payload.get("evidence_path", "")
        if not isinstance(operation_id, str):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_operation_id_type"})
            return
        if not operation_id:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "missing_operation_id"})
            return
        if not OPERATION_ID.fullmatch(operation_id):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_operation_id"})
            return
        if not isinstance(note, str) or not isinstance(evidence_path, str):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_task_action_text"})
            return
        if len(note) > TASK_ACTION_TEXT_LIMIT or len(evidence_path) > TASK_ACTION_TEXT_LIMIT:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "task_action_text_too_long"})
            return
        with self.fixture["lock"]:
            if task_id not in self.fixture["tasks"]:
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"unknown task: {task_id}"})
                return
            fingerprint = (task_id, action, note, evidence_path)
            existing = self.fixture["operations"].get(operation_id)
            if existing is not None and existing["fingerprint"] != fingerprint:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "operation_id_conflict"})
                return
        with self.fixture["lock"]:
            self.fixture["post_count"] += 1
            self.fixture["posts"].append({
                "task_id": task_id,
                "action": action,
                "payload": deepcopy(payload),
            })
            plan = self.fixture["action_plan"].pop(0) if self.fixture["action_plan"] else {"kind": "success"}

        delay = float(plan.get("delay", 0))
        if plan.get("kind") == "timeout" and delay <= 0:
            delay = float(self.fixture["timeout_delay"])
        if delay > 0:
            time.sleep(delay)
        if plan.get("kind") == "http_error":
            status = HTTPStatus(int(plan.get("status", HTTPStatus.SERVICE_UNAVAILABLE)))
            response_body = plan["response_body"] if "response_body" in plan else {
                "ok": False,
                "error": str(plan.get("error", "fixture_planned_error")),
            }
            self._json(status, response_body)
            return
        if plan.get("kind") == "timeout":
            # No mutation: this models a request that never reached a commit.
            # Sleeping beyond the UI deadline makes Chromium abort the socket.
            self._json(HTTPStatus.GATEWAY_TIMEOUT, {"ok": False, "error": "fixture_planned_timeout"})
            return
        self._task_action(task_id, action == "undo", payload, plan, fingerprint)

    def _task_action(
        self,
        task_id: str,
        undo: bool,
        payload: dict[str, Any],
        plan: dict[str, Any] | None,
        fingerprint: tuple[str, str, str, str],
    ) -> None:
        operation_id = payload["operation_id"]
        note = payload.get("note", "")
        evidence_path = payload.get("evidence_path", "")
        with self.fixture["lock"]:
            tasks = self.fixture["tasks"]
            replay = self.fixture["operations"].get(operation_id)
            if replay is not None:
                if replay["fingerprint"] != fingerprint:
                    response = (HTTPStatus.BAD_REQUEST, {"ok": False, "error": "operation_id_conflict"})
                else:
                    ledger_event = replay["response"]["event"]
                    current_matches = [
                        event for event in self.fixture["events"]
                        if event.get("action_id") == ledger_event.get("action_id")
                    ]
                    if len(current_matches) != 1 or current_matches[0] != ledger_event:
                        response = (
                            HTTPStatus.BAD_REQUEST,
                            {"ok": False, "error": "operation_event_not_in_current_history"},
                        )
                    else:
                        replay_payload = deepcopy(replay["response"])
                        replay_payload["result"] = "replayed_operation"
                        replay_payload["message"] = f"replayed:{task_id}"
                        response = (HTTPStatus.OK, replay_payload)
            else:
                task = tasks[task_id]
                was_done = bool(task.get("done"))
                now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                if undo:
                    result = "ok" if was_done else "noop_already_undone"
                    task["done"] = False
                    task["done_at"] = None
                    action_type = "undo_done"
                else:
                    result = "noop_already_done" if was_done else "ok"
                    task["done"] = True
                    task["done_at"] = task.get("done_at") or now
                    action_type = "mark_done"
                self.fixture["revision"] += 1
                self.fixture["event_sequence"] += 1
                action_id = _action_id("task-action", self.fixture["event_sequence"])
                event = {
                    "action_id": action_id,
                    "operation_id": operation_id,
                    "timestamp": now,
                    "task_id": task_id,
                    "round_id": _round_id(task_id),
                    "lane": task.get("lane", "linux-foundations"),
                    "action_type": action_type,
                    "result": result,
                    "note": note,
                    "evidence_path": evidence_path,
                }
                self.fixture["events"].append(event)
                self.fixture["feedback"] = self.fixture["build_feedback"](tasks, self.fixture["events"])
                result_payload = {
                    "ok": True,
                    "task_id": task_id,
                    "done": bool(task["done"]),
                    "done_at": task.get("done_at"),
                    "lane": task.get("lane", "linux-foundations"),
                    "result": result,
                    "message": (
                        f"already_undone:{task_id}" if result == "noop_already_undone"
                        else f"undone:{task_id}" if undo
                        else f"already_done:{task_id}" if result == "noop_already_done"
                        else f"done:{task_id}:{now}"
                    ),
                    "action_id": action_id,
                    "event": deepcopy(event),
                    "operation_id": operation_id,
                    "revision": self.fixture["revision"],
                    "total": len(tasks),
                    "done_count": sum(1 for item in tasks.values() if item.get("done")),
                    "tasks": deepcopy(tasks),
                    "lanes": deepcopy(self.fixture["progress"]["lanes"]),
                    "feedback": deepcopy(self.fixture["feedback"]),
                }
                self.fixture["operations"][operation_id] = {
                    "fingerprint": fingerprint,
                    "response": deepcopy(result_payload),
                }
                wire_payload = self._apply_response_plan(result_payload, plan)
                response = (HTTPStatus.OK, wire_payload)
        self._json(*response)

    def _task_run(self, task_id: str, payload: dict[str, Any]) -> None:
        if set(payload) != {"operation_id"}:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_run_payload"})
            return
        operation_id = payload.get("operation_id")
        if not isinstance(operation_id, str):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_operation_id_type"})
            return
        if not operation_id:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "missing_operation_id"})
            return
        if not OPERATION_ID.fullmatch(operation_id):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_operation_id"})
            return

        with self.fixture["lock"]:
            if task_id not in self.fixture["tasks"]:
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"unknown task: {task_id}"})
                return
            fingerprint = (task_id, "run")
            existing = self.fixture["run_operations"].get(operation_id)
            if existing is not None and existing["fingerprint"] != fingerprint:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "operation_id_conflict"})
                return
            self.fixture["run_post_count"] += 1
            self.fixture["run_posts"].append({"task_id": task_id, "payload": deepcopy(payload)})
            plan = self.fixture["run_plan"].pop(0) if self.fixture["run_plan"] else {"kind": "success"}
        delay = float(plan.get("delay", 0))
        if delay > 0:
            time.sleep(delay)
        if plan.get("kind") == "http_error":
            status = HTTPStatus(int(plan.get("status", HTTPStatus.SERVICE_UNAVAILABLE)))
            response_body = plan["response_body"] if "response_body" in plan else {
                "ok": False,
                "error": str(plan.get("error", "fixture_planned_run_error")),
            }
            self._json(status, response_body)
            return

        with self.fixture["lock"]:
            existing = self.fixture["run_operations"].get(operation_id)
            if existing is not None:
                if existing["fingerprint"] != fingerprint:
                    self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "operation_id_conflict"})
                    return
                ledger_event = existing["execution"]["event"]
                current_matches = [
                    event for event in self.fixture["events"]
                    if event.get("action_id") == ledger_event.get("action_id")
                ]
                if len(current_matches) != 1 or current_matches[0] != ledger_event:
                    response = (
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": "run_operation_event_not_in_current_history"},
                    )
                else:
                    execution = deepcopy(existing["execution"])
                    execution["replayed_operation"] = True
                    execution["revision"] = self.fixture["revision"]
                    execution["total"] = len(self.fixture["tasks"])
                    execution["done_count"] = sum(
                        1 for item in self.fixture["tasks"].values() if item.get("done")
                    )
                    canonical = {
                        "ok": True,
                        "execution": execution,
                        "tasks": deepcopy(self.fixture["tasks"]),
                        "lanes": deepcopy(self.fixture["progress"]["lanes"]),
                        "feedback": deepcopy(self.fixture["feedback"]),
                        "revision": self.fixture["revision"],
                    }
                    response = (HTTPStatus.OK, self._apply_response_plan(canonical, plan))
            else:
                self.fixture["run_execution_count"] += 1
                task = self.fixture["tasks"][task_id]
                result = str(plan.get("result") or (
                    "failed" if plan.get("kind") == "failed"
                    else "timeout" if plan.get("kind") == "timeout"
                    else "ok"
                ))
                if result not in {"ok", "failed", "timeout"}:
                    response = (HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_fixture_run_result"})
                    self._json(*response)
                    return
                timed_out = result == "timeout"
                returncode = 0 if result == "ok" else None if timed_out else int(plan.get("returncode", 1))
                verification_reached = bool(plan.get("verification_reached", result == "ok"))
                completion_applied = result == "ok" and verification_reached and not bool(task.get("done"))
                now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                if completion_applied:
                    task["done"] = True
                    task["done_at"] = now
                self.fixture["revision"] += 1
                self.fixture["event_sequence"] += 1
                action_id = _action_id("task-run", self.fixture["event_sequence"])
                script_path = "fixture/in-memory.sh"
                sandbox_path = "fixture://in-memory"
                stdout = str(plan.get("stdout", "fixture run completed without executing a script"))
                stderr = str(plan.get("stderr", "" if result == "ok" else "fixture run did not complete"))
                duration_ms = int(plan.get("duration_ms", 1))
                run_event = {
                    "action_id": action_id,
                    "operation_id": operation_id,
                    "task_id": task_id,
                    "round_id": _round_id(task_id),
                    "lane": task.get("lane", "linux-foundations"),
                    "action_type": "run_exercise",
                    "timestamp": now,
                    "result": result,
                    "note": f"Web UI 执行练习脚本：{script_path}；结果：{result}",
                    "evidence_path": sandbox_path,
                    "details": {
                        "script_path": script_path,
                        "sandbox_path": sandbox_path,
                        "returncode": returncode,
                        "timed_out": timed_out,
                        "duration_ms": duration_ms,
                        "stdout_excerpt": stdout,
                        "stderr_excerpt": stderr,
                        "verification_reached": verification_reached,
                        "completion_applied": completion_applied,
                    },
                }
                self.fixture["events"].append(run_event)
                self.fixture["feedback"] = self.fixture["build_feedback"](
                    self.fixture["tasks"], self.fixture["events"]
                )
                total = len(self.fixture["tasks"])
                done_count = sum(1 for item in self.fixture["tasks"].values() if item.get("done"))
                execution = {
                    "task_id": task_id,
                    "action_id": action_id,
                    "event": deepcopy(run_event),
                    "result": result,
                    "execution_ok": result == "ok",
                    "timed_out": timed_out,
                    "duration_ms": duration_ms,
                    "returncode": returncode,
                    "script_path": script_path,
                    "sandbox_path": sandbox_path,
                    "stdout": stdout,
                    "stderr": stderr,
                    "verification_reached": verification_reached,
                    "completion_applied": completion_applied,
                    "operation_id": operation_id,
                    "replayed_operation": False,
                    "revision": self.fixture["revision"],
                    "total": total,
                    "done_count": done_count,
                }
                self.fixture["run_operations"][operation_id] = {
                    "fingerprint": fingerprint,
                    "execution": deepcopy(execution),
                }
                canonical = {
                    "ok": True,
                    "execution": execution,
                    "tasks": deepcopy(self.fixture["tasks"]),
                    "lanes": deepcopy(self.fixture["progress"]["lanes"]),
                    "feedback": deepcopy(self.fixture["feedback"]),
                    "revision": self.fixture["revision"],
                }
                response = (HTTPStatus.OK, self._apply_response_plan(canonical, plan))
        self._json(*response)

    def _load_save(self, save_id: str) -> None:
        with self.fixture["lock"]:
            self.fixture["save_load_post_count"] += 1
            plan = self.fixture["save_load_plan"].pop(0) if self.fixture["save_load_plan"] else {"kind": "success"}
            if plan.get("kind") == "restore_uncompleted_history":
                task_id = str(plan.get("task_id", ""))
                if task_id not in self.fixture["tasks"]:
                    self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_restore_task"})
                    return
                self.fixture["tasks"][task_id]["done"] = False
                self.fixture["tasks"][task_id]["done_at"] = None
                self.fixture["events"] = [
                    event for event in self.fixture["events"]
                    if event.get("task_id") != task_id
                ]
                self.fixture["revision"] += 1
                self.fixture["feedback"] = self.fixture["build_feedback"](
                    self.fixture["tasks"], self.fixture["events"]
                )
            snapshot = {
                "tasks": deepcopy(self.fixture["tasks"]),
                "lanes": deepcopy(self.fixture["progress"]["lanes"]),
                "feedback": deepcopy(self.fixture["feedback"]),
                "revision": self.fixture["revision"],
            }
            canonical = {
                "ok": True,
                "loaded": {
                    "save_id": save_id,
                    "label": "隔离内存存档",
                    "personal": {},
                    **deepcopy(snapshot),
                },
                **snapshot,
                "saves": [],
            }
            response = (HTTPStatus.OK, self._apply_response_plan(canonical, plan))
        self._json(*response)

    def _reject_other_write(self) -> None:
        self._record(self.command)
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"ok": False, "error": "fixture_post_only"})

    do_PUT = _reject_other_write
    do_PATCH = _reject_other_write
    do_DELETE = _reject_other_write


class FixtureServer:
    def __init__(
        self,
        *,
        progress_delay: float = 0,
        task_actions: bool = False,
        action_plan: list[dict[str, Any] | str] | None = None,
        run_plan: list[dict[str, Any] | str] | None = None,
        progress_plan: list[dict[str, Any]] | None = None,
        save_load_plan: list[dict[str, Any]] | None = None,
        timeout_delay: float = 10.5,
    ):
        progress = json.loads((REPO_ROOT / "progress.json").read_text(encoding="utf-8"))
        def normalize_plan(raw_plan: list[dict[str, Any] | str] | None) -> list[dict[str, Any]]:
            normalized = []
            for raw_step in raw_plan or []:
                step = {"kind": raw_step} if isinstance(raw_step, str) else dict(raw_step)
                if step.get("kind") == "delayed_success":
                    step["kind"] = "success"
                    step.setdefault("delay", 0.35)
                elif step.get("kind") == "error":
                    step["kind"] = "http_error"
                normalized.append(step)
            return normalized

        def build_feedback(tasks: dict[str, dict], events: list[dict]) -> dict[str, dict]:
            feedback = {}
            for task_id, task in tasks.items():
                task_events = [event for event in events if event["task_id"] == task_id]
                last = task_events[-1] if task_events else None
                done = bool(task.get("done"))
                feedback[task_id] = {
                    "task_id": task_id,
                    "lane": "linux-foundations",
                    "done": done,
                    "action_count": len(task_events),
                    "last_action_type": last["action_type"] if last else None,
                    "last_action_at": last["timestamp"] if last else None,
                    "feedback_type": "completed" if done else "not_started",
                    "message": "本次学习记录已保存在隔离测试内存中。" if done else "尚未开始。",
                    "next_suggestion": "继续下一项已注册 Linux 任务。" if done else "准备好后开始学习。",
                }
            return feedback

        normalized_plan = normalize_plan(action_plan)
        normalized_run_plan = normalize_plan(run_plan)
        tasks = deepcopy(progress["tasks"])
        self.state = {
            "progress_delay": progress_delay,
            "requests": [],
            "task_actions": task_actions,
            "action_plan": normalized_plan,
            "run_plan": normalized_run_plan,
            "progress_plan": list(progress_plan or []),
            "save_load_plan": list(save_load_plan or []),
            "timeout_delay": timeout_delay,
            "progress": progress,
            "tasks": tasks,
            "revision": max(0, int(progress.get("revision") or 0)),
            "events": [],
            "event_sequence": 0,
            "feedback": build_feedback(tasks, []),
            "build_feedback": build_feedback,
            "operations": {},
            "run_operations": {},
            "posts": [],
            "post_count": 0,
            "run_posts": [],
            "run_post_count": 0,
            "run_execution_count": 0,
            "save_load_post_count": 0,
            "lock": threading.RLock(),
        }
        self.httpd = _FixtureHTTPServer(("127.0.0.1", 0), FixtureHandler)
        self.httpd.fixture = self.state
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> "FixtureServer":
        self.thread.start()
        return self

    def __exit__(self, *_exc_info) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)
