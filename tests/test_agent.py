"""Executable offline invariant tests; no test here proves real LLM reasoning."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.agent import Agent
from src.model import ModelError, OpenAICompatibleModel, ScriptedMockModel
from src.state import RequestStore, TaskState
from src.tools import ToolError, Toolset

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = json.loads((ROOT / "data/mock_scenarios.json").read_text(encoding="utf-8"))


def action(name, arguments=None):
    return {"type": "tool", "name": name, "arguments": arguments or {}, "reason": "离线测试动作"}


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = RequestStore(Path(self.temp.name) / "store")
        self.request = {"request_id": "test-1", "topic": "新品推广", "budget": 10, "deadline_hours": 8}

    def run_agent(self, scenario="normal", request=None, actions=None, fail_once=None, **limits):
        model = ScriptedMockModel(actions if actions is not None else SCENARIOS[scenario])
        tools = Toolset(ROOT / "data", self.store, fail_once=fail_once)
        return Agent(model, tools, **limits).run(request or self.request)

    def saved_records(self):
        return [json.loads(p.read_text(encoding="utf-8")) for p in self.store.root.glob("*.json")
                if json.loads(p.read_text(encoding="utf-8"))["saved_plan"] is not None]

    def test_normal_saves_real_file_and_business_steps_remain_planned(self):
        result = self.run_agent()
        self.assertEqual(result["status"], "completed")
        saved = self.saved_records()[0]["saved_plan"]
        self.assertEqual(saved["estimate"]["cost_units"], 8)
        self.assertEqual(saved["estimate"]["duration_minutes"], 360)
        self.assertTrue(saved["estimate"]["constraints_met"])
        self.assertEqual(saved["plan"]["material_ids"], ["M02"])
        self.assertTrue(all(s["status"] == "planned" for s in saved["plan"]["business_steps"]))
        self.assertEqual(result["model_calls"], 4)

    def test_missing_budget_or_deadline_never_calls_model_or_saves(self):
        for field in ("budget", "deadline_hours"):
            with self.subTest(field=field):
                request = dict(self.request)
                del request[field]
                result = self.run_agent(request=request)
                self.assertEqual(result["status"], "needs_input")
                self.assertIn(field, result["reason"])
                self.assertEqual(result["model_calls"], 0)
        self.assertFalse(self.saved_records())

    def test_invalid_constraints_do_not_enter_loop(self):
        for value in (float("nan"), float("inf"), -1, True):
            result = self.run_agent(request={**self.request, "budget": value})
            self.assertEqual(result["status"], "needs_input")

    def test_budget_replan_preserves_observations_and_saves_new_estimate(self):
        result = self.run_agent("budget_replan")
        estimates = [s["result"] for s in result["steps"] if s.get("tool") == "estimate_plan"]
        self.assertEqual([e["cost_units"] for e in estimates], [12, 8])
        self.assertFalse(estimates[0]["budget_ok"])
        self.assertTrue(estimates[1]["constraints_met"])
        self.assertEqual(self.saved_records()[0]["saved_plan"]["plan"]["material_ids"], ["M02"])

    def test_tool_failure_retains_script_and_does_not_regenerate_it(self):
        result = self.run_agent("tool_retry", fail_once="estimate_plan")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len([s for s in result["steps"] if s["tool"] == "generate_script"]), 1)
        failed = [s for s in result["steps"] if s["status"] == "failed"]
        self.assertEqual(failed[0]["tool"], "estimate_plan")
        self.assertEqual(result["current_plan"], self.saved_records()[0]["saved_plan"]["plan"])

    def test_same_request_reuses_file_without_model_or_tool_calls(self):
        first = self.run_agent()
        path = self.store.path_for(self.request["request_id"])
        before = path.read_bytes()
        second = self.run_agent()
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["save_result"]["plan_id"], first["save_result"]["plan_id"])
        self.assertTrue(second["save_result"]["duplicate"])
        self.assertEqual((second["model_calls"], second["tool_calls"]), (0, 0))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(self.saved_records()), 1)

    def test_same_id_different_input_conflicts_and_preserves_old_file(self):
        self.run_agent()
        path = self.store.path_for(self.request["request_id"])
        before = path.read_bytes()
        result = self.run_agent(request={**self.request, "budget": 20})
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(path.read_bytes(), before)

    def test_failed_request_also_reserves_request_id(self):
        self.run_agent("save_failure", fail_once="save_plan")
        result = self.run_agent(request={**self.request, "deadline_hours": 9})
        self.assertEqual(result["status"], "conflict")

    def test_save_failure_is_not_success_and_later_rerun_can_save(self):
        result = self.run_agent("save_failure", fail_once="save_plan")
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["save_result"]["saved"])
        self.assertFalse(self.saved_records())
        self.assertIsNotNone(result["current_plan"])
        self.assertEqual(self.run_agent()["status"], "completed")

    def test_actual_disk_write_failure_does_not_claim_saved(self):
        self.store.begin({**self.request, "budget": 10.0, "deadline_hours": 8.0})
        with patch("src.state.write_json", side_effect=OSError("simulated disk full")):
            result = self.run_agent()
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["save_result"]["saved"])
        self.assertFalse(self.saved_records())

    def test_no_feasible_plan_reports_constraint_gaps(self):
        result = self.run_agent("infeasible", request={**self.request, "budget": 0, "deadline_hours": 0.5})
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["latest_estimate"]["budget_shortfall"], 4)
        self.assertEqual(result["latest_estimate"]["time_shortfall_minutes"], 120)
        self.assertFalse(self.saved_records())

    def test_time_overrun_cannot_save_even_when_budget_passes(self):
        result = self.run_agent("time_exceeded", request={**self.request, "deadline_hours": 5})
        self.assertTrue(result["latest_estimate"]["budget_ok"])
        self.assertFalse(result["latest_estimate"]["deadline_ok"])
        self.assertFalse(result["save_result"]["saved"])
        self.assertFalse(self.saved_records())

    def test_time_replan_is_reestimated(self):
        result = self.run_agent("time_replan", request={**self.request, "deadline_hours": 3})
        estimates = [s["result"] for s in result["steps"] if s["tool"] == "estimate_plan"]
        self.assertEqual([e["duration_minutes"] for e in estimates], [360, 150])
        self.assertEqual(result["status"], "completed")

    def test_plan_change_invalidates_previous_estimate(self):
        actions = SCENARIOS["normal"][:3] + [
            action("generate_script", {"material_ids": ["M03"], "format": "quick_post"}),
            action("save_plan")]
        result = self.run_agent(actions=actions)
        self.assertIsNone(result["latest_estimate"])
        self.assertFalse(result["save_result"]["saved"])
        self.assertFalse(self.saved_records())

    def test_save_recomputes_instead_of_trusting_estimate(self):
        tools = Toolset(ROOT / "data", self.store)
        state = TaskState(request={**self.request, "budget": 10.0, "deadline_hours": 8.0})
        state.current_plan = tools.generate_script(state.request, ["M01"], "campaign_video")["plan"]
        state.plan_version = 1
        state.latest_estimate = tools.estimate_plan(state)
        state.latest_estimate["constraints_met"] = True
        with self.assertRaises(ToolError):
            tools.save_plan(state)
        self.assertFalse(self.saved_records())

    def test_step_limit(self):
        result = self.run_agent(max_steps=1, max_model_calls=10)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual((result["execution_count"], result["model_calls"]), (1, 1))
        self.assertIn("步数上限", result["reason"])
        self.assertFalse(self.saved_records())

    def test_model_call_limit(self):
        result = self.run_agent(max_steps=10, max_model_calls=1)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["model_calls"], 1)
        self.assertIn("模型调用次数上限", result["reason"])

    def test_model_cannot_declare_success_without_save(self):
        result = self.run_agent(actions=[{"type": "finish", "status": "completed", "reason": "声称完成"}])
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("没有实际保存", result["reason"])

    def test_invalid_tool_parameters_are_observations_not_crashes(self):
        result = self.run_agent(actions=[action("generate_script", {"material_ids": [], "format": "social_post"}),
                                         action("estimate_plan", {"fake_cost": 0}), action("unknown")])
        self.assertEqual(len(result["errors"]), 3)
        self.assertFalse(self.saved_records())

    def test_model_connection_failure_is_incomplete(self):
        model = ScriptedMockModel([])
        model.next_action = lambda _: (_ for _ in ()).throw(ModelError("连接失败"))
        result = Agent(model, Toolset(ROOT / "data", self.store)).run(self.request)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["model_calls"], 1)

    def test_failed_observation_and_successful_plan_reach_next_model_call(self):
        class RecordingFixture(ScriptedMockModel):
            def __init__(self):
                super().__init__(SCENARIOS["tool_retry"])
                self.contexts = []

            def next_action(self, context):
                self.contexts.append(context)
                return super().next_action(context)

        model = RecordingFixture()
        tools = Toolset(ROOT / "data", self.store, "estimate_plan")
        result = Agent(model, tools).run(self.request)
        fourth_input = model.contexts[3]["state"]
        self.assertEqual(fourth_input["steps"][-1]["status"], "failed")
        self.assertEqual(fourth_input["current_plan"]["material_ids"], ["M02"])
        self.assertEqual(fourth_input["steps"][1]["status"], "succeeded")
        self.assertEqual(result["status"], "completed")

    def test_changed_rules_invalidate_estimate_before_save(self):
        tools = Toolset(ROOT / "data", self.store)
        state = TaskState(request={**self.request, "budget": 10.0, "deadline_hours": 8.0})
        state.current_plan = tools.generate_script(state.request, ["M02"], "social_post")["plan"]
        state.plan_version = 1
        state.latest_estimate = tools.estimate_plan(state)
        tools.rules["review"]["cost_units"] += 1
        with self.assertRaises(ToolError):
            tools.save_plan(state)

    def test_unknown_input_is_not_silently_discarded(self):
        result = self.run_agent(request={**self.request, "must_use_video": True})
        self.assertEqual(result["status"], "needs_input")
        self.assertIn("must_use_video", result["reason"])


class AdapterTests(unittest.TestCase):
    def test_transport_payload_and_action_parsing_with_stubbed_http(self):
        model = OpenAICompatibleModel("https://example.invalid/v1", "test-only-secret", "fixture-model")
        expected = {"type": "tool", "name": "search_materials", "arguments": {"query": "新品"}, "reason": "查询"}
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "choices": [{"message": {"content": json.dumps(expected)}}], "usage": {"total_tokens": 10}}).encode()
        with patch("src.model.urllib.request.urlopen", return_value=response) as transport:
            self.assertEqual(model.next_action({"state": {}}), expected)
        request = transport.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.invalid/v1/chat/completions")
        body = json.loads(request.data)
        self.assertEqual(body["model"], "fixture-model")
        self.assertNotIn("test-only-secret", json.dumps(model.metadata))

    def test_missing_api_configuration_never_silently_uses_mock(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ModelError):
                OpenAICompatibleModel()

    def test_invalid_api_response_is_not_treated_as_an_action(self):
        model = OpenAICompatibleModel("https://example.invalid/v1", "test-key", "fixture-model")
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"choices": []}'
        with patch("src.model.urllib.request.urlopen", return_value=response):
            with self.assertRaises(ModelError):
                model.next_action({"state": {}})


if __name__ == "__main__":
    unittest.main()
