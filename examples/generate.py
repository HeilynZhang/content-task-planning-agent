"""Generate evidence by invoking the real CLI. Actions are offline fixtures only."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.state import write_json

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="通过 CLI 生成实际离线运行证据，不生成真实 LLM 证据")
    parser.add_argument("--output-dir", type=Path, required=True, help="使用尚不存在的目录，防止覆盖旧证据")
    args = parser.parse_args()
    out = args.output_dir.resolve()
    if out.exists():
        raise SystemExit("输出目录已存在，请使用新目录以保留旧执行记录")
    out.mkdir(parents=True)
    store = out / "store"
    base = {"topic": "虚构新品推广", "budget": 10, "deadline_hours": 8}
    # These are test inputs and expectations, never prerecorded execution traces.
    cases = [
        ("normal", {**base, "request_id": "example-normal"}, "normal", [], "completed"),
        ("missing_budget", {"topic": base["topic"], "deadline_hours": 8, "request_id": "missing-budget"}, "normal", [], "needs_input"),
        ("missing_deadline", {"topic": base["topic"], "budget": 10, "request_id": "missing-deadline"}, "normal", [], "needs_input"),
        ("budget_replan", {**base, "request_id": "example-budget"}, "budget_replan", [], "completed"),
        ("tool_failure", {**base, "request_id": "example-tool-failure"}, "tool_retry", ["--fail-tool-once", "estimate_plan"], "completed"),
        ("duplicate", {**base, "request_id": "example-normal"}, "normal", [], "completed"),
        ("conflict", {**base, "budget": 11, "request_id": "example-normal"}, "normal", [], "conflict"),
        ("infeasible", {**base, "budget": 0, "deadline_hours": 0.5, "request_id": "example-infeasible"}, "infeasible", [], "incomplete"),
        ("time_exceeded", {**base, "deadline_hours": 5, "request_id": "example-time"}, "time_exceeded", [], "incomplete"),
        ("time_replan", {**base, "deadline_hours": 3, "request_id": "example-time-replan"}, "time_replan", [], "completed"),
        ("save_failure", {**base, "request_id": "example-save-failure"}, "save_failure", ["--fail-tool-once", "save_plan"], "incomplete"),
        ("step_limit", {**base, "request_id": "example-step-limit"}, "normal", ["--max-steps", "1"], "incomplete"),
        ("model_limit", {**base, "request_id": "example-model-limit"}, "normal", ["--max-model-calls", "1"], "incomplete"),
    ]
    summary = {"generated_at": datetime.now(timezone.utc).isoformat(), "mode": "mock",
               "real_llm_verified": False, "cases": []}
    normal_bytes = None
    for name, request, scenario, extra, expected in cases:
        print(f"[examples] 执行 {name}，预期状态 {expected}", flush=True)
        input_path, output_path = out / f"{name}.input.json", out / f"{name}.output.json"
        trace_path = out / f"{name}.trace.json"
        write_json(input_path, request)
        command = [sys.executable, "-m", "src.cli", "--input", str(input_path), "--mode", "mock",
                   "--mock-scenario", scenario, "--store-dir", str(store),
                   "--trace-file", str(trace_path), "--output-file", str(output_path), *extra]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                                   env={**os.environ, "PYTHONUTF8": "1"})
        (out / f"{name}.console.log").write_text(completed.stderr, encoding="utf-8")
        if not output_path.exists():
            raise RuntimeError(f"CLI 未生成输出：{name} {completed.stderr}")
        output = json.loads(output_path.read_text(encoding="utf-8"))
        if output["metadata"]["engine"] != "langgraph":
            raise AssertionError("样例必须由 LangGraph 实际执行生成")
        summary.update(engine="langgraph", langgraph_version=output["metadata"]["langgraph_version"])
        if output["status"] != expected or completed.returncode != (0 if expected == "completed" else 2):
            raise AssertionError(f"{name} 实际状态/退出码不符合预期")
        saved_count = sum(json.loads(p.read_text(encoding="utf-8"))["saved_plan"] is not None for p in store.glob("*.json"))
        saved_path = Path(output["save_result"]["path"]) if (output.get("save_result") or {}).get("saved") else None
        if name == "normal":
            normal_bytes = saved_path.read_bytes()
        if name in ("duplicate", "conflict"):
            normal_path = next(p for p in store.glob("*.json")
                               if json.loads(p.read_text(encoding="utf-8"))["request"]["request_id"] == "example-normal")
            if normal_path.read_bytes() != normal_bytes or output["model_calls"] != 0:
                raise AssertionError("重复/冲突请求改变原记录或调用了模型")
        summary["cases"].append({"name": name, "status": output["status"], "exit_code": completed.returncode,
                                 "model_calls": output["model_calls"], "saved_records_after_case": saved_count,
                                 "run_id": output["metadata"]["run_id"],
                                 "node_sequence": [event["node"] for event in output["node_events"]],
                                 "command": ["python", *command[1:]],
                                 "input": input_path.name, "output": output_path.name, "trace": trace_path.name})
    write_json(out / "summary.json", summary)
    print(f"已生成 {len(cases)} 组实际离线记录：{out}")


if __name__ == "__main__":
    main()
