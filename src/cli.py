"""Run with python -m src.cli. Progress goes to stderr; result JSON to stdout."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .agent import Agent
from .model import ModelError, OpenAICompatibleModel, ScriptedMockModel, load_env_file
from .state import RequestStore, write_json
from .tools import Toolset

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="内容任务规划 Agent（离线 Mock 不证明真实模型决策）")
    parser.add_argument("--input", type=Path, help="任务输入 JSON；也可使用单独参数")
    parser.add_argument("--env-file", type=Path, help="显式加载本地 KEY=VALUE 配置；已有环境变量优先")
    parser.add_argument("--topic")
    parser.add_argument("--budget", type=float)
    parser.add_argument("--deadline-hours", type=float)
    parser.add_argument("--request-id")
    parser.add_argument("--mode", choices=("llm", "mock"), default="llm")
    parser.add_argument("--mock-scenario", default="normal")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--store-dir", type=Path, default=ROOT / "data" / "runtime")
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--max-model-calls", type=int)
    parser.add_argument("--fail-tool-once", choices=Toolset.names, help="显式注入一次工具失败，用于验证")
    parser.add_argument("--trace-file", type=Path)
    parser.add_argument("--output-file", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.env_file:
            load_env_file(args.env_file)
        if args.max_steps is None:
            args.max_steps = int(os.getenv("MAX_AGENT_STEPS", "12"))
        if args.max_model_calls is None:
            args.max_model_calls = int(os.getenv("MAX_MODEL_CALLS", "12"))
        if args.max_steps <= 0 or args.max_model_calls <= 0:
            raise ValueError("max-steps 和 max-model-calls 必须为正数")
        raw = json.loads(args.input.read_text(encoding="utf-8-sig")) if args.input else {}
        if not isinstance(raw, dict):
            raise ValueError("输入 JSON 必须是对象")
        for field in ("topic", "budget", "deadline_hours", "request_id"):
            if getattr(args, field) is not None:
                raw[field] = getattr(args, field)
        if args.mode == "mock":
            scenarios = json.loads((args.data_dir / "mock_scenarios.json").read_text(encoding="utf-8"))
            if args.mock_scenario not in scenarios:
                raise ValueError(f"未知 Mock 场景；可选：{', '.join(scenarios)}")
            model = ScriptedMockModel(scenarios[args.mock_scenario], args.mock_scenario)
        else:
            model = OpenAICompatibleModel()
        tools = Toolset(args.data_dir, RequestStore(args.store_dir), args.fail_tool_once)
        result = Agent(model, tools, args.max_steps, args.max_model_calls,
                       progress=lambda message: print(message, file=sys.stderr, flush=True)).run(raw)
        trace = result.pop("trace")
        if args.trace_file:
            write_json(args.trace_file, {"metadata": result["metadata"], "request": raw,
                                         "events": trace, "node_events": result["node_events"]})
            result["trace_file"] = str(args.trace_file)
        else:
            result["trace"] = trace
        if args.output_file:
            write_json(args.output_file, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0 if result["status"] == "completed" else 2
    except (ModelError, ValueError, OSError, KeyError) as exc:
        print(json.dumps({"status": "incomplete", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
