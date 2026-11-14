"""AGENT / COOKBOOK TOOL INTERFACE.

These are the ONLY functions the LLM can call. Each takes and returns plain JSON. The LLM
never sees table names to join or writes SQL: it chooses metrics and dimensions by their
registered names, and the deterministic layer does the rest.

TOOL_SCHEMAS can be passed straight to an LLM function-calling API (Claude tool use,
Databricks Agent Framework / Mosaic AI tools, etc.).
"""
from __future__ import annotations

from typing import Any, Optional

from .model import SemanticModel
from .ontology import Ontology
from .planner import Planner, PlanningError
from .runtime import TableNames
from .taxonomy import AmbiguousTerm, Taxonomy, TermNotFound


class SemanticTools:
    def __init__(self, model: SemanticModel, runner, names: TableNames, engine: str = "sql", row_limit: int = 500):
        self.model, self.runner, self.names, self.row_limit = model, runner, names, row_limit
        self.taxonomy = Taxonomy(model)
        self.ontology = Ontology(model)
        self.planner = Planner(model, names, engine=engine)

    # -- taxonomy ---------------------------------------------------------------
    def find_metric(self, term: str) -> dict:
        """What is OTD? -> definition, owner, taxonomy path, allowed slices."""
        try:
            return self.taxonomy.describe(term)
        except AmbiguousTerm as e:
            return {"error": "ambiguous_term", "candidates": e.candidates,
                    "message": "Ask the user which one they mean."}
        except TermNotFound as e:
            return {"error": "unknown_term", "suggestions": e.suggestions}

    def list_members(self, entity: str, level: str, parent_level: Optional[str] = None,
                     parent_member: Optional[str] = None) -> dict:
        """What is under Snacks? -> list_members('product', 'product_family', 'category', 'Snacks')"""
        try:
            vals = self.taxonomy.members(self.runner, self.names, entity, level, parent_level, parent_member)
            return {"entity": entity, "level": level, "members": vals}
        except KeyError as e:
            return {"error": "unknown_level", "message": str(e)}

    # -- ontology ---------------------------------------------------------------
    def explain_relationship(self, concept_a: str, concept_b: str) -> dict:
        """How are Warehouse and Carrier related?"""
        steps = self.ontology.path(concept_a, concept_b)
        if steps is None:
            return {"related": False, "message": f"No relationship between {concept_a} and {concept_b} is defined."}
        return {"related": True, "path": steps}

    def check_combinable(self, metrics: list[str]) -> dict:
        """Before planning: can these metrics be combined, and by what?"""
        try:
            ms = [self.taxonomy.resolve(t) for t in metrics]
        except (AmbiguousTerm, TermNotFound) as e:
            return {"error": "term", "message": str(e)}
        shared = self.ontology.shared_grain(list(dict.fromkeys(m.concept for m in ms)))
        return {"metrics": [m.qualified for m in ms],
                "combinable_by": {e: f"{lv} or coarser" for e, lv in shared.items()},
                "combinable": bool(shared)}

    def business_rules(self, domains: list[str]) -> dict:
        """Behaviour notes for ONLY the domains a question touches (keeps the prompt small)."""
        return {d: self.model.packages[d].business_rules for d in domains if d in self.model.packages}

    # -- execution --------------------------------------------------------------
    def plan_query(self, request: dict) -> dict:
        try:
            plan = self.planner.plan(request)
        except PlanningError as e:
            return {"error": e.code, "message": e.message, "suggestion": e.suggestion}
        except (AmbiguousTerm, TermNotFound) as e:
            return {"error": "term", "message": str(e)}
        return {"explanation": plan.explanation, "sql": plan.sql, "columns": plan.output_columns,
                "domains": sorted({l.domain for l in plan.legs})}

    def run_query(self, request: dict) -> dict:
        out = self.plan_query(request)
        if "error" in out:
            return out
        rows = self.runner.query(out["sql"])
        out["rows"] = rows[: self.row_limit]
        out["truncated"] = len(rows) > self.row_limit
        return out

    def call(self, name: str, args: dict) -> Any:
        if name not in {t["name"] for t in TOOL_SCHEMAS}:
            return {"error": "unknown_tool", "message": name}
        return getattr(self, name)(**args)


_REQUEST_SCHEMA = {
    "type": "object",
    "properties": {
        "metrics": {"type": "array", "items": {"type": "string"},
                    "description": "Metric names or business terms, e.g. ['OTD', 'forecast_accuracy']"},
        "by": {"type": "array", "items": {"type": "string"},
               "description": "Dimensions as entity.level, e.g. ['location.region', 'calendar.fiscal_month']"},
        "filters": {"type": "array", "items": {"type": "object", "properties": {
            "dim": {"type": "string"}, "op": {"type": "string", "enum": ["=", "!=", ">", ">=", "<", "<=", "in", "between"]},
            "value": {}}, "required": ["dim", "value"]}},
        "breakdown": {"type": "object", "properties": {"of": {"type": "string"}, "member": {"type": "string"}},
                      "description": "Break a member down one level, e.g. {of: product.category, member: Snacks}"},
    },
    "required": ["metrics"],
}

TOOL_SCHEMAS = [
    {"name": "find_metric", "description": "Look up a metric or business term in the taxonomy: definition, owner, formula, allowed dimensions.",
     "input_schema": {"type": "object", "properties": {"term": {"type": "string"}}, "required": ["term"]}},
    {"name": "list_members", "description": "List the members of a hierarchy level, optionally under a parent member.",
     "input_schema": {"type": "object", "properties": {"entity": {"type": "string"}, "level": {"type": "string"},
                      "parent_level": {"type": "string"}, "parent_member": {"type": "string"}},
                      "required": ["entity", "level"]}},
    {"name": "explain_relationship", "description": "Explain how two business concepts are related in the ontology.",
     "input_schema": {"type": "object", "properties": {"concept_a": {"type": "string"}, "concept_b": {"type": "string"}},
                      "required": ["concept_a", "concept_b"]}},
    {"name": "check_combinable", "description": "Check whether metrics from different domains can be combined, and by which dimensions.",
     "input_schema": {"type": "object", "properties": {"metrics": {"type": "array", "items": {"type": "string"}}},
                      "required": ["metrics"]}},
    {"name": "business_rules", "description": "Get behaviour rules for the domains involved in a question.",
     "input_schema": {"type": "object", "properties": {"domains": {"type": "array", "items": {"type": "string"}}},
                      "required": ["domains"]}},
    {"name": "plan_query", "description": "Compile a request into a validated plan and SQL without running it.",
     "input_schema": _REQUEST_SCHEMA},
    {"name": "run_query", "description": "Compile and run a request. Returns rows plus an explanation of how data was joined.",
     "input_schema": _REQUEST_SCHEMA},
]
