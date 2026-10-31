"""Graph execution and checkpoint tests. Scripted fixtures do not prove LLM reasoning."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from src.agent import Agent
from src.model import ScriptedMockModel
from src.state import RequestStore
from src.tools import Toolset

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = json.loads((ROOT / "data/mock_scenarios.json").read_text(encoding="utf-8"))


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = RequestStore(Path(self.temp.name) / "store")
        self.request = {"request_id": "graph-test", "topic": "虚构新品推广", "budget": 10, "deadline_hours": 8}

    def agent(self, scenario="normal", *, actions=None, fail_once=None, **options):
        model = ScriptedMockModel(SCENARIOS[scenario] if actions is None else actions)
        return Agent(model, Toolset(ROOT / "data", self.store, fail_once), **options)

    @staticmethod
    def nodes(result):
        return [event["node"] for event in result["node_events"]]

    def test_compiled_stategraph_has_expected_conditional_edges(self):
        agent = self.agent()
        self.assertIsInstance(agent.graph, CompiledStateGraph)
        edges = {(edge.source, edge.target) for edge in agent.graph.get_graph().edges}
        self.assertEqual(edges, {
            ("__start__", "prepare"), ("prepare", "decision"), ("prepare", "finalize"),
            ("decision", "execute_tool"), ("decision", "decision"), ("decision", "finalize"),
            ("execute_tool", "decision"), ("execute_tool", "finalize"), ("finalize", "__end__"),
        })

    def test_normal_node_sequence_and_append_reducers(self):
        result = self.agent().run(self.request)
        self.assertEqual(self.nodes(result), ["prepare"] + ["decision", "execute_tool"] * 4 + ["finalize"])
        self.assertEqual(len(result["trace"]), 4)
        self.assertEqual(len(result["steps"]), 4)
        self.assertEqual([step["step"] for step in result["steps"]], [1, 2, 3, 4])
        self.assertEqual(result["metadata"]["engine"], "langgraph")

    def test_missing_input_routes_straight_to_finalize(self):
        request = {k: v for k, v in self.request.items() if k != "budget"}
        result = self.agent().run(request)
        self.assertEqual(self.nodes(result), ["prepare", "finalize"])
        self.assertEqual(result["model_calls"], 0)

    def test_budget_observation_routes_back_to_model_without_selecting_plan(self):
        agent = self.agent("budget_replan")
        result = agent.run(self.request)
        history = list(agent.graph.get_state_history(agent.last_config))
        over_budget = [s for s in history if s.values.get("latest_estimate") and
                       not s.values["latest_estimate"]["budget_ok"] and s.next == ("decision",) and
                       s.values.get("last_observation", {}).get("tool") == "estimate_plan"]
        self.assertTrue(over_budget)
        self.assertEqual(over_budget[0].values["current_plan"]["material_ids"], ["M01"])
        self.assertIsNone(over_budget[0].values["pending_action"])
        self.assertEqual(over_budget[0].values["last_observation"]["tool"], "estimate_plan")
        self.assertEqual(result["current_plan"]["material_ids"], ["M02"])
        # The alternative comes from the fixture's next model action, not an edge.
        self.assertEqual(Agent._route_tool({"status": "running", "latest_estimate": {"budget_ok": False}}), "decision")
        self.assertEqual(Agent._route_tool({"status": "running", "latest_estimate": {"budget_ok": True}}), "decision")

    def test_failed_tool_snapshot_preserves_plan_and_routes_to_decision(self):
        agent = self.agent("tool_retry", fail_once="estimate_plan")
        result = agent.run(self.request)
        history = list(agent.graph.get_state_history(agent.last_config))
        failures = [s for s in history if s.values.get("last_observation") and
                    s.values["last_observation"]["status"] == "failed" and s.next == ("decision",)]
        self.assertEqual(len(failures), 1)
        state = failures[0].values
        self.assertEqual(state["current_plan"]["material_ids"], ["M02"])
        self.assertEqual(state["steps"][1]["status"], "succeeded")
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["status"], "completed")

    def test_plan_change_checkpoint_has_no_stale_estimate(self):
        agent = self.agent("budget_replan")
        agent.run(self.request)
        changed = [s for s in agent.graph.get_state_history(agent.last_config)
                   if s.values.get("plan_version") == 2 and s.next == ("decision",) and
                   s.values.get("last_observation", {}).get("tool") == "generate_script"]
        self.assertTrue(changed)
        self.assertIsNone(changed[0].values["latest_estimate"])

    def test_invalid_action_self_loop_is_bounded_and_not_duplicated(self):
        actions = [{"type": "invalid", "reason": "离线校验测试"}] * 5
        result = self.agent(actions=actions, max_steps=2).run(self.request)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["execution_count"], 2)
        self.assertEqual(result["tool_calls"], 0)
        self.assertEqual(len(result["errors"]), 2)
        self.assertEqual(self.nodes(result), ["prepare", "decision", "decision", "decision", "finalize"])

    def test_last_allowed_action_can_save_before_limit(self):
        result = self.agent(max_steps=4, max_model_calls=4).run(self.request)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["save_result"]["saved"])
        self.assertEqual(result["model_calls"], 4)

    def test_model_stop_routes_to_finalize_without_tools(self):
        result = self.agent(actions=[{"type": "finish", "status": "incomplete", "reason": "离线主动停止"}]).run(self.request)
        self.assertEqual(self.nodes(result), ["prepare", "decision", "finalize"])
        self.assertEqual(result["tool_calls"], 0)

    def test_final_checkpoint_and_history_are_real_snapshots(self):
        agent = self.agent()
        result = agent.run(self.request)
        snapshot = agent.graph.get_state(agent.last_config)
        self.assertEqual(snapshot.next, ())
        self.assertEqual(snapshot.values["status"], "completed")
        self.assertEqual(snapshot.values["current_plan"], result["current_plan"])
        self.assertEqual(snapshot.values["model_calls"], result["model_calls"])
        history = list(agent.graph.get_state_history(agent.last_config))
        pending = [s for s in history if s.next == ("execute_tool",)]
        self.assertEqual(len(pending), 4)
        self.assertEqual({s.values["pending_action"]["name"] for s in pending}, set(Toolset.names))

    def test_run_ids_isolate_checkpoints_and_request_id_still_controls_reuse(self):
        saver = InMemorySaver()
        agent = self.agent(checkpointer=saver)
        first = agent.run(self.request)
        first_config = agent.last_config
        second = agent.run(self.request)
        self.assertNotEqual(first["metadata"]["run_id"], second["metadata"]["run_id"])
        self.assertEqual(second["model_calls"], 0)
        self.assertEqual(self.nodes(second), ["prepare", "finalize"])
        self.assertEqual(agent.graph.get_state(first_config).values["model_calls"], 4)
        self.assertEqual(agent.graph.get_state(agent.last_config).values["model_calls"], 0)

    def test_new_memory_saver_has_no_prior_checkpoints(self):
        first = self.agent()
        first.run(self.request)
        fresh = self.agent(checkpointer=InMemorySaver())
        self.assertEqual(fresh.graph.get_state(first.last_config).values, {})

    def test_recursion_safety_returns_incomplete_with_last_plan(self):
        result = self.agent(recursion_limit=6).run(self.request)
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("图递归上限", result["reason"])
        self.assertIsNotNone(result["current_plan"])
        self.assertFalse(result["metadata"]["resume_supported"])
        self.assertIsNone(result["save_result"])
        unexecuted = [e for e in result["trace"] if e.get("observation", {}).get("status") == "not_executed"]
        self.assertTrue(unexecuted)
        self.assertEqual(unexecuted[0]["action"]["name"], "estimate_plan")


if __name__ == "__main__":
    unittest.main()
