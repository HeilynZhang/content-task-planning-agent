"""Check existing real-run artifacts locally; never calls a model or rewrites traces."""
import argparse
import json
from pathlib import Path

from src.state import fingerprint, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description="验证已有真实模型运行材料；不会发起API调用")
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    cases = []
    for path in sorted(args.run_dir.glob("*.output.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        name = path.name.removesuffix(".output.json")
        trace_path = args.run_dir / f"{name}.trace.json"
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        metadata = result["metadata"]
        assert metadata["mode"] == "llm" and metadata["api_calls"] > 0, "缓存/Mock不能算新真实模型路径"
        assert len(metadata["response_metadata"]) == metadata["api_calls"], "本批用例必须有每次API响应记录"
        assert metadata == trace["metadata"], "Trace与输出元数据不一致"
        assert result["model_calls"] <= metadata["max_model_calls"]
        assert result["execution_count"] <= metadata["max_steps"]
        saved_verified = False
        if (result.get("save_result") or {}).get("saved"):
            saved = json.loads(Path(result["save_result"]["path"]).read_text(encoding="utf-8"))["saved_plan"]
            assert saved["plan"] == result["current_plan"]
            assert saved["estimate"] == result["latest_estimate"]
            estimate = result["latest_estimate"]
            assert estimate["plan_fingerprint"] == fingerprint(result["current_plan"])
            assert estimate["plan_version"] == result["plan_version"]
            assert estimate["cost_units"] == sum(s["cost_units"] for s in saved["plan"]["business_steps"])
            assert estimate["duration_minutes"] == sum(s["duration_minutes"] for s in saved["plan"]["business_steps"])
            assert estimate["cost_units"] <= result["request"]["budget"]
            assert estimate["duration_minutes"] <= result["request"]["deadline_hours"] * 60
            assert estimate["budget_ok"] and estimate["deadline_ok"] and estimate["constraints_met"]
            saved_verified = True
        assert result["status"] != "completed" or saved_verified
        estimates = [step["result"] for step in result["steps"]
                     if step["tool"] == "estimate_plan" and step["status"] == "succeeded"]
        failures = [step for step in result["steps"] if step["status"] == "failed"]
        cases.append({"case": name, "status": result["status"], "model": metadata["model"],
                      "thinking": metadata["thinking"], "api_calls": metadata["api_calls"],
                      "started_at": metadata["started_at"], "finished_at": metadata["finished_at"],
                      "runtime_seconds": metadata["runtime_seconds"], "saved_verified": saved_verified,
                      "cost_sequence": [e["cost_units"] for e in estimates],
                      "duration_sequence_minutes": [e["duration_minutes"] for e in estimates],
                      "failed_steps": [{"step": s["step"], "tool": s["tool"], "error": s["error"]} for s in failures],
                      "action_sequence": [s["tool"] for s in result["steps"]],
                      "provider_total_tokens": sum(u.get("total_tokens", 0) for u in metadata["provider_usage"]),
                      "trace": trace_path.name, "output": path.name})
    assert cases, "没有找到实际输出文件"
    report = {"mode": "llm", "verification": "offline_artifact_checks_after_real_api_execution",
              "cases": cases, "total_api_calls": sum(c["api_calls"] for c in cases),
              "all_saved_results_verified": all(c["saved_verified"] for c in cases)}
    write_json(args.run_dir / "summary.json", report)
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
