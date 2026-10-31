"""LangGraph controls execution; the model selects business actions."""
from __future__ import annotations

import copy
import time
from importlib.metadata import version
from typing import Any, Callable, Literal
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from .model import Model, ModelError, TOOL_SPEC
from .state import (GraphState, RequestConflict, TaskState, normalize_request,
                    task_state_from_graph, utc_now)
from .tools import ToolError, Toolset


class Agent:
    node_names = ("prepare", "decision", "execute_tool", "finalize")

    def __init__(self, model: Model, tools: Toolset, max_steps: int = 12,
                 max_model_calls: int = 12, progress: Callable[[str], None] | None = None,
                 *, checkpointer: InMemorySaver | None = None, recursion_limit: int | None = None):
        if max_steps <= 0 or max_model_calls <= 0:
            raise ValueError("执行上限必须为正数")
        self.model = model
        self.tools = tools
        self.max_steps = max_steps
        self.max_model_calls = max_model_calls
        self.progress = progress or (lambda message: None)
        self.checkpointer = checkpointer if checkpointer is not None else InMemorySaver()
        # Each action needs at most decision + tool, plus prepare/guard/finalize.
        self.recursion_limit = recursion_limit if recursion_limit is not None else 2 * max_steps + len(self.node_names) + 1
        if self.recursion_limit <= 0:
            raise ValueError("图递归上限必须为正数")
        self.last_config: dict[str, Any] | None = None
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(GraphState)
        builder.add_node("prepare", self._prepare)
        builder.add_node("decision", self._decision)
        builder.add_node("execute_tool", self._execute_tool)
        builder.add_node("finalize", self._finalize)
        builder.add_edge(START, "prepare")
        builder.add_conditional_edges("prepare", self._route_prepare)
        builder.add_conditional_edges("decision", self._route_decision)
        builder.add_conditional_edges("execute_tool", self._route_tool)
        builder.add_edge("finalize", END)
        return builder.compile(checkpointer=self.checkpointer)

    @staticmethod
    def _route_prepare(state: GraphState) -> Literal["decision", "finalize"]:
        return "decision" if state["status"] == "running" else "finalize"

    @staticmethod
    def _route_decision(state: GraphState) -> Literal["execute_tool", "decision", "finalize"]:
        if state["status"] != "running":
            return "finalize"
        return "execute_tool" if state["pending_action"] is not None else "decision"

    @staticmethod
    def _route_tool(state: GraphState) -> Literal["decision", "finalize"]:
        # Budget/time observations never select a replacement plan here.
        return "decision" if state["status"] == "running" else "finalize"

    def _node_event(self, name: str) -> dict[str, Any]:
        self.progress(f"图节点：{name}")
        return {"node": name, "timestamp": utc_now()}

    def _prepare(self, state: GraphState) -> dict[str, Any]:
        update: dict[str, Any] = {"node_events": [self._node_event("prepare")]}
        request, issues = normalize_request(state["raw_request"])
        update["request"] = request
        if issues:
            update.update(status="needs_input", reason="；".join(issues),
                          trace=[{"timestamp": utc_now(), "event": "input_validation", "issues": issues}])
            return update
        try:
            existing = self.tools.store.begin(request)
            if existing["saved_plan"]:
                saved = existing["saved_plan"]
                save_result = {"saved": True, "status": "reused", "duplicate": True,
                               "plan_id": saved["plan_id"],
                               "path": str(self.tools.store.path_for(request["request_id"]))}
                update.update(current_plan=saved["plan"], latest_estimate=saved["estimate"],
                              plan_version=saved["estimate"]["plan_version"], steps=saved["agent_steps"],
                              save_result=save_result, status="completed",
                              reason="复用已保存方案；本次未调用模型或业务工具",
                              trace=[{"timestamp": utc_now(), "event": "idempotent_reuse", "result": save_result}])
        except RequestConflict as exc:
            update.update(status="conflict", reason=str(exc),
                          trace=[{"timestamp": utc_now(), "event": "request_conflict", "error": str(exc)}])
        except (OSError, ValueError, KeyError) as exc:
            reason = f"无法读取或登记请求：{exc}"
            update.update(status="incomplete", reason=reason,
                          trace=[{"timestamp": utc_now(), "event": "store_error", "error": reason}])
        return update

    @staticmethod
    def _failure(step: int, action: Any, message: str, event: dict[str, Any]) -> dict[str, Any]:
        error = {"step": step, "tool": action.get("name") if isinstance(action, dict) else None,
                 "arguments": copy.deepcopy(action.get("arguments")) if isinstance(action, dict) else None,
                 "status": "failed", "error": message}
        event = {**event, "observation": error, "result_summary": {"status": "failed", "error": message}}
        update = {"errors": [copy.deepcopy(error)], "steps": [copy.deepcopy(error)],
                  "trace": [event], "last_observation": error,
                  "pending_action": None, "pending_event": None}
        if error["tool"] == "save_plan":
            update["save_result"] = {"saved": False, "status": "failed", "reason": message}
        return update

    def _decision(self, state: GraphState) -> dict[str, Any]:
        update: dict[str, Any] = {"node_events": [self._node_event("decision")],
                                  "pending_action": None, "pending_event": None}
        if state["execution_count"] >= self.max_steps or state["model_calls"] >= self.max_model_calls:
            reason = "达到 Agent 执行步数上限" if state["execution_count"] >= self.max_steps else "达到模型调用次数上限"
            update.update(status="incomplete", reason=reason,
                          trace=[{"timestamp": utc_now(), "event": "limit_reached", "reason": reason}])
            return update
        calls = state["model_calls"] + 1
        update["model_calls"] = calls
        self.progress(f"模型调用 {calls}/{self.max_model_calls}，等待下一步动作…")
        event = {"timestamp": utc_now(), "step": state["execution_count"] + 1,
                 "event": "model_action", "decision_node": "decision"}
        model_view = task_state_from_graph(state).model_view()
        model_view["model_calls"] = calls
        model_view["last_observation"] = copy.deepcopy(state["last_observation"])
        try:
            action = self.model.next_action({"state": model_view, "tools": TOOL_SPEC,
                                            "business_context": self.tools.context(),
                                            "remaining_steps": self.max_steps - state["execution_count"],
                                            "remaining_model_calls": self.max_model_calls - calls})
        except ModelError as exc:
            update.update(status="incomplete", reason=str(exc),
                          errors=[{"kind": "model_error", "message": str(exc)}],
                          trace=[{**event, "event": "model_error", "error": str(exc)}])
            return update
        step = state["execution_count"] + 1
        update["execution_count"] = step
        event["action"] = copy.deepcopy(action)
        try:
            if not isinstance(action, dict):
                raise ToolError("模型动作必须是对象")
            if not isinstance(action.get("reason"), str) or not action["reason"].strip():
                raise ToolError("模型动作缺少简要依据 reason")
            if action.get("type") == "finish":
                if action.get("status") not in ("completed", "incomplete"):
                    raise ToolError("结束动作 status 必须为 completed 或 incomplete")
                status = "completed" if state["save_result"] and state["save_result"].get("saved") else "incomplete"
                reason = action["reason"]
                if action["status"] == "completed" and status != "completed":
                    reason = "模型宣称完成，但没有实际保存成功；" + reason
                update.update(status=status, reason=reason,
                              trace=[{**event, "observation": {"status": status, "reason": reason}}])
                return update
            if action.get("type") != "tool":
                raise ToolError("动作 type 必须为 tool 或 finish")
            if not isinstance(action.get("name"), str) or not isinstance(action.get("arguments"), dict):
                raise ToolError("工具名必须是字符串，arguments 必须是对象")
            update.update(pending_action=copy.deepcopy(action), pending_event=event)
        except ToolError as exc:
            update.update(self._failure(step, action, str(exc), event))
        return update

    def _execute_tool(self, state: GraphState) -> dict[str, Any]:
        update: dict[str, Any] = {"node_events": [self._node_event("execute_tool")],
                                  "tool_calls": state["tool_calls"] + 1,
                                  "pending_action": None, "pending_event": None}
        action = state["pending_action"]
        name, arguments = action["name"], action["arguments"]
        event = {**state["pending_event"], "tool_node": "execute_tool"}
        self.progress(f"步骤 {state['execution_count']}/{self.max_steps}：执行 {name}")
        try:
            observation = self.tools.execute(name, arguments, task_state_from_graph(state))
            if name == "generate_script":
                update.update(current_plan=copy.deepcopy(observation["plan"]),
                              plan_version=state["plan_version"] + 1, latest_estimate=None, save_result=None)
                summary = {"plan_version": update["plan_version"], "material_ids": observation["plan"]["material_ids"],
                           "format": observation["plan"]["format"], "estimate_invalidated": True}
            elif name == "estimate_plan":
                update["latest_estimate"] = copy.deepcopy(observation)
                summary = {k: observation[k] for k in ("cost_units", "duration_minutes", "budget_ok", "deadline_ok",
                                                       "budget_shortfall", "time_shortfall_minutes")}
            elif name == "save_plan":
                update["save_result"] = copy.deepcopy(observation)
                if observation.get("saved"):
                    update.update(status="completed", reason="方案实际保存成功；业务步骤仍待执行")
                summary = copy.deepcopy(observation)
            else:
                summary = {"count": observation["count"], "material_ids": [m["id"] for m in observation["materials"]]}
            record = {"step": state["execution_count"], "tool": name, "arguments": copy.deepcopy(arguments),
                      "status": "succeeded", "result": copy.deepcopy(observation)}
            update.update(steps=[record], last_observation=copy.deepcopy(record),
                          trace=[{**event, "observation": copy.deepcopy(record), "result_summary": summary}])
            self.progress(f"{name} 成功")
        except (ToolError, TypeError, ValueError, OSError, RequestConflict) as exc:
            update.update(self._failure(state["execution_count"], action, str(exc), event))
            self.progress(f"动作失败：{exc}；保留已有成功结果，交给模型决定下一步")
        return update

    def _metadata(self, state: GraphState) -> dict[str, Any]:
        return {**copy.deepcopy(self.model.metadata), "started_at": state["started_at"], "finished_at": utc_now(),
                "runtime_seconds": round(time.monotonic() - state["started_monotonic"], 4),
                "max_steps": self.max_steps, "max_model_calls": self.max_model_calls,
                "engine": "langgraph", "langgraph_version": version("langgraph"),
                "run_id": state["run_id"], "checkpointer": type(self.checkpointer).__name__,
                "checkpoint_scope": "process_memory_only", "resume_supported": False}

    @staticmethod
    def _final_reason(state: GraphState) -> str:
        reason = state["reason"]
        if state["status"] == "incomplete" and state["latest_estimate"] and not state["latest_estimate"]["constraints_met"]:
            e = state["latest_estimate"]
            reason += f"；当前方案费用缺口 {e['budget_shortfall']}，时间缺口 {e['time_shortfall_minutes']} 分钟"
        return reason

    def _finalize(self, state: GraphState) -> dict[str, Any]:
        return {"reason": self._final_reason(state), "metadata": self._metadata(state),
                "node_events": [self._node_event("finalize")]}

    def run(self, raw_request: dict[str, Any]) -> dict[str, Any]:
        run_id = str(uuid4())
        initial = {**TaskState(request={}).public_view(), "raw_request": copy.deepcopy(raw_request),
                   "pending_action": None, "pending_event": None, "last_observation": None,
                   "node_events": [], "run_id": run_id, "started_at": utc_now(),
                   "started_monotonic": time.monotonic(), "metadata": {}}
        self.last_config = {"configurable": {"thread_id": run_id}, "recursion_limit": self.recursion_limit}
        try:
            # Graph execution, not a Python while loop, drives all node transitions.
            final = self.graph.invoke(initial, config=self.last_config)
        except GraphRecursionError:
            final = copy.deepcopy(self.graph.get_state(self.last_config).values)
            final.update(status="incomplete", reason="达到 LangGraph 图递归上限")
            if final.get("pending_event"):
                # Preserve an actual model action whose tool has not run yet.
                observation = {"status": "not_executed", "reason": "图递归上限阻止工具执行"}
                final["trace"] = final.get("trace", []) + [
                    {**final["pending_event"], "observation": observation, "result_summary": observation}]
            final["trace"] = final.get("trace", []) + [{"timestamp": utc_now(), "event": "graph_recursion_limit"}]
            final["reason"] = self._final_reason(final)
            final["metadata"] = self._metadata(final)
        output = task_state_from_graph(final).public_view()
        output.update(metadata=final["metadata"], node_events=final["node_events"],
                      last_observation=final["last_observation"])
        return output
