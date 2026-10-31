"""Synthetic business tools. No publication or real business execution occurs."""
from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from .state import RequestStore, TaskState, fingerprint


class ToolError(RuntimeError):
    pass


class Toolset:
    names = ("search_materials", "generate_script", "estimate_plan", "save_plan")

    def __init__(self, data_dir: str | Path, store: RequestStore, fail_once: str | None = None):
        self.store = store
        self.fail_once = fail_once
        self._failed = False
        root = Path(data_dir)
        catalog = json.loads((root / "materials.json").read_text(encoding="utf-8"))
        self.materials = {item["id"]: item for item in catalog["materials"]}
        self.rules = json.loads((root / "rules.json").read_text(encoding="utf-8"))

    def context(self) -> dict[str, Any]:
        return {"formats": self.rules["formats"], "review": self.rules["review"],
                "accounting": "预算只计最终业务方案；耗时为业务步骤串行总和。Agent 调用免费，不计真实 API 费用。"}

    def execute(self, name: str, arguments: dict[str, Any], state: TaskState) -> dict[str, Any]:
        if name not in self.names:
            raise ToolError(f"未知工具：{name}")
        if self.fail_once == name and not self._failed:
            self._failed = True
            raise ToolError(f"注入的单次工具故障：{name}")
        handlers = {
            "search_materials": lambda: self.search_materials(**arguments),
            "generate_script": lambda: self.generate_script(state.request, **arguments),
            "estimate_plan": lambda: self.estimate_plan(state, **arguments),
            "save_plan": lambda: self.save_plan(state, **arguments),
        }
        return handlers[name]()

    def search_materials(self, query: str, exclude_ids: list[str] | None = None) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            raise ToolError("query 必须是非空字符串")
        if exclude_ids is not None and (not isinstance(exclude_ids, list) or
                                       any(not isinstance(mid, str) for mid in exclude_ids)):
            raise ToolError("exclude_ids 必须是字符串列表")
        terms = query.lower().split()
        excluded = set(exclude_ids or [])
        matches = []
        for item in self.materials.values():
            text = " ".join([item["title"], item["description"], *item["tags"]]).lower()
            if item["id"] not in excluded and any(term in text for term in terms):
                matches.append(copy.deepcopy(item))
        return {"materials": matches, "count": len(matches),
                "note": "本地关键词检索；空结果不等于所有查询都无资料，可改写查询。"}

    def _select(self, material_ids: list[str], format: str) -> list[dict[str, Any]]:
        if (not isinstance(material_ids, list) or not material_ids or
                any(not isinstance(mid, str) for mid in material_ids)):
            raise ToolError("material_ids 必须是非空字符串列表")
        if len(set(material_ids)) != len(material_ids):
            raise ToolError("素材 ID 不能重复")
        if not isinstance(format, str) or format not in self.rules["formats"]:
            raise ToolError("未知内容形式；请查看可用 formats")
        selected = []
        for mid in material_ids:
            if mid not in self.materials:
                raise ToolError(f"未知素材：{mid}")
            item = self.materials[mid]
            if format not in item["formats"]:
                raise ToolError(f"素材 {mid} 不支持内容形式 {format}")
            selected.append(item)
        return selected

    def _business_steps(self, selected: list[dict[str, Any]], format: str) -> list[dict[str, Any]]:
        production = self.rules["formats"][format]
        review = self.rules["review"]
        return [
            {"id": "prepare", "name": "素材准备", "depends_on": [], "status": "planned",
             "deliverable": "核对素材与来源", "cost_units": sum(m["cost_units"] for m in selected),
             "duration_minutes": sum(m["preparation_minutes"] for m in selected)},
            {"id": "produce", "name": production["name"], "depends_on": ["prepare"], "status": "planned",
             "deliverable": "按脚本制作内容", "cost_units": production["cost_units"],
             "duration_minutes": production["duration_minutes"]},
            {"id": "review", "name": "审核整理", "depends_on": ["produce"], "status": "planned",
             "deliverable": "可交付内容包", "cost_units": review["cost_units"],
             "duration_minutes": review["duration_minutes"]},
        ]

    def generate_script(self, request: dict[str, Any], material_ids: list[str], format: str) -> dict[str, Any]:
        selected = self._select(material_ids, format)
        sections = [{"text": fact, "source_material_id": item["id"]}
                    for item in selected for fact in item["facts"]]
        plan = {"topic": request["topic"], "constraints": {"budget": request["budget"],
                "deadline_hours": request["deadline_hours"]}, "material_ids": list(material_ids),
                "materials": copy.deepcopy(selected), "format": format,
                "script": {"title": f"{request['topic']}：新品功能介绍", "sections": sections,
                           "closing": "了解新品功能，选择适合自己的内容制作方式。",
                           "generation_mode": "local_synthetic_template"},
                "business_steps": self._business_steps(selected, format)}
        return {"plan": plan, "note": "生成的是规划草稿，业务步骤尚未实际执行。"}

    def estimate_plan(self, state: TaskState) -> dict[str, Any]:
        plan = state.current_plan
        if not plan:
            raise ToolError("请先生成方案")
        selected = self._select(plan["material_ids"], plan["format"])
        expected_steps = self._business_steps(selected, plan["format"])
        if plan["business_steps"] != expected_steps:
            raise ToolError("业务步骤与当前费用规则不一致，请重新生成方案")
        expected_sections = [{"text": fact, "source_material_id": item["id"]}
                             for item in selected for fact in item["facts"]]
        if plan["script"]["sections"] != expected_sections:
            raise ToolError("脚本事实与素材来源不一致")
        cost = sum(step["cost_units"] for step in expected_steps)
        minutes = sum(step["duration_minutes"] for step in expected_steps)
        budget_gap = max(Decimal(cost) - Decimal(str(state.request["budget"])), Decimal(0))
        time_gap = max(Decimal(minutes) - Decimal(str(state.request["deadline_hours"])) * 60, Decimal(0))
        return {"plan_version": state.plan_version, "plan_fingerprint": fingerprint(plan),
                "cost_units": cost, "duration_minutes": minutes, "estimated_hours": minutes / 60,
                "budget_ok": budget_gap == 0, "deadline_ok": time_gap == 0,
                "constraints_met": budget_gap == 0 and time_gap == 0,
                "budget_shortfall": float(budget_gap), "time_shortfall_minutes": float(time_gap),
                "business_steps": copy.deepcopy(expected_steps)}

    def save_plan(self, state: TaskState) -> dict[str, Any]:
        estimate = state.latest_estimate
        if not estimate or estimate["plan_version"] != state.plan_version:
            raise ToolError("拒绝保存：修改方案后必须重新估算")
        verified = self.estimate_plan(state)
        if estimate != verified:
            raise ToolError("拒绝保存：方案或规则已变化，旧估算无效")
        if not verified["constraints_met"]:
            raise ToolError(f"拒绝保存：预算或时间未满足；费用缺口 {verified['budget_shortfall']}，时间缺口 {verified['time_shortfall_minutes']} 分钟")
        return self.store.save(state.request, state.current_plan, verified, state.steps)
