"""Task state and a single-process, atomic JSON request store."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import operator
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, TypedDict


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def write_json(path: Path, value: Any) -> None:
    """Replace a whole file, so a failed write never leaves half a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         suffix=".tmp", delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def normalize_request(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    request: dict[str, Any] = {}
    allowed = {"request_id", "topic", "budget", "deadline_hours"}
    issues = [f"不支持的输入字段：{key}" for key in raw if key not in allowed]
    for key in ("request_id", "topic"):
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            issues.append(f"请提供非空 {key}")
        else:
            request[key] = value.strip()
    for key in ("budget", "deadline_hours"):
        value = raw.get(key)
        try:
            number = float(value)
            valid = not isinstance(value, bool) and math.isfinite(number)
            valid = valid and (number >= 0 if key == "budget" else number > 0)
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            issues.append(f"请提供有效 {key}（预算非负，截止小时数为正）")
        else:
            request[key] = number
    return request, issues


class RequestConflict(RuntimeError):
    pass


class RequestStore:
    """One hashed file per request. Intended for sequential CLI use, not a server."""
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def path_for(self, request_id: str) -> Path:
        return self.root / (fingerprint(request_id) + ".json")

    def begin(self, request: dict[str, Any]) -> dict[str, Any]:
        path = self.path_for(request["request_id"])
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            if record["request_hash"] != fingerprint(request):
                raise RequestConflict("同一 request_id 已绑定不同输入；请使用新的请求 ID")
            return record
        record = {"request": request, "request_hash": fingerprint(request), "saved_plan": None}
        write_json(path, record)
        return record

    def save(self, request: dict[str, Any], plan: dict[str, Any], estimate: dict[str, Any],
             steps: list[dict[str, Any]]) -> dict[str, Any]:
        record = self.begin(request)
        reused = record["saved_plan"] is not None
        if not reused:
            record["saved_plan"] = {
                "plan_id": "plan-" + fingerprint(request["request_id"])[:16],
                "request": copy.deepcopy(request), "plan": copy.deepcopy(plan),
                "estimate": copy.deepcopy(estimate), "agent_steps": copy.deepcopy(steps),
                "saved_at": utc_now(),
            }
            write_json(self.path_for(request["request_id"]), record)
        return {"saved": True, "status": "reused" if reused else "saved",
                "plan_id": record["saved_plan"]["plan_id"], "duplicate": reused,
                "path": str(self.path_for(request["request_id"]))}


@dataclass
class TaskState:
    request: dict[str, Any]
    status: str = "running"
    reason: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    current_plan: dict[str, Any] | None = None
    plan_version: int = 0
    latest_estimate: dict[str, Any] | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    execution_count: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    save_result: dict[str, Any] | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)

    def public_view(self) -> dict[str, Any]:
        return asdict(self)

    def model_view(self) -> dict[str, Any]:
        value = self.public_view()
        value.pop("trace")
        return value


class GraphState(TypedDict):
    """The authoritative graph state; nodes return deltas, never mutate it."""
    raw_request: dict[str, Any]
    request: dict[str, Any]
    status: str
    reason: str
    current_plan: dict[str, Any] | None
    plan_version: int
    latest_estimate: dict[str, Any] | None
    pending_action: dict[str, Any] | None
    pending_event: dict[str, Any] | None
    last_observation: dict[str, Any] | None
    steps: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[dict[str, Any]], operator.add]
    trace: Annotated[list[dict[str, Any]], operator.add]
    node_events: Annotated[list[dict[str, Any]], operator.add]
    execution_count: int
    model_calls: int
    tool_calls: int
    save_result: dict[str, Any] | None
    run_id: str
    started_at: str
    started_monotonic: float
    metadata: dict[str, Any]


def task_state_from_graph(state: GraphState) -> TaskState:
    """Detached read-only adapter for the original tools and model input schema."""
    return TaskState(**{item.name: copy.deepcopy(state[item.name]) for item in fields(TaskState)})
