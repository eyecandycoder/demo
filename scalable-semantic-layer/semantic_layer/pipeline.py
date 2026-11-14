"""The onboarding / deployment pipeline. CI runs this on every package change.

    validate (static) -> validate (data) -> generate views -> publish registry -> golden questions

Nothing in here mentions a specific domain. That is what makes onboarding a new domain a
YAML change instead of a code change.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .metric_views import all_ddl
from .model import SemanticModel
from .ontology import Ontology
from .planner import Planner, PlanningError
from .runtime import TableNames
from .taxonomy import Taxonomy
from .validate import Finding, data_checks, static_checks


@dataclass
class DeployReport:
    findings: list[Finding] = field(default_factory=list)
    views: list[str] = field(default_factory=list)
    registry_tables: dict[str, int] = field(default_factory=dict)
    golden: list[dict] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "ERROR"]

    @property
    def golden_failures(self) -> list[dict]:
        return [g for g in self.golden if not g["passed"]]

    @property
    def ok(self) -> bool:
        return not self.errors and not self.golden_failures


def registry_rows(model: SemanticModel, runner, names: TableNames) -> dict[str, list[dict]]:
    tax, onto = Taxonomy(model), Ontology(model)
    metrics = []
    for m in model.metrics():
        d = tax.describe(m.qualified)
        metrics.append({"metric": m.qualified, "name": m.name, "domain": m.domain, "concept": m.concept,
                        "kind": m.kind, "category": m.category, "formula": d["formula"], "unit": m.unit,
                        "additivity": getattr(m, "additivity", "derived"), "status": m.status,
                        "owner": d["owner"], "description": m.description, "synonyms": "|".join(m.synonyms),
                        "sliceable_by": "|".join(d["can_be_sliced_by"]),
                        "metric_view": f"mv_{m.domain}_{m.concept.lower()}"})
    packages = [{"package": p.name, "version": p.version, "status": p.status, "owner": p.owner, "domain_group": p.group,
                 "description": p.description, "deployed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
                for p in model.packages.values()]
    rules = [{"package": p.name, "rule_no": str(i + 1), "rule": r}
             for p in model.packages.values() for i, r in enumerate(p.business_rules)]
    return {
        "packages": packages,
        "taxonomy_nodes": tax.nodes(),
        "metrics": metrics,
        "hierarchy_members": tax.hierarchy_member_rows(runner, names),
        "ontology_concepts": onto.concept_rows(),
        "ontology_relations": onto.relation_rows(names),
        "linkage_matrix": onto.linkage_rows(),
        "business_rules": rules,
    }


def run_golden_questions(model: SemanticModel, runner, names: TableNames, execute: bool = True) -> list[dict]:
    planner = Planner(model, names, engine="sql")
    results = []
    for p in model.packages.values():
        for g in p.golden_questions:
            exp = g.get("expect", {})
            res = {"package": p.name, "question": g["question"], "passed": False, "detail": ""}
            try:
                plan = planner.plan(g["request"])
                if "error" in exp:
                    res["detail"] = f"expected refusal '{exp['error']}' but a plan was produced"
                elif "legs" in exp and len(plan.legs) != exp["legs"]:
                    res["detail"] = f"expected {exp['legs']} legs, got {len(plan.legs)}"
                else:
                    rows = runner.query(plan.sql) if execute else []
                    if execute and not rows:
                        res["detail"] = "query returned no rows"
                    else:
                        res["passed"], res["detail"] = True, f"{len(rows)} rows"
            except PlanningError as e:
                if exp.get("error") == e.code:
                    res["passed"], res["detail"] = True, f"correctly refused ({e.code})"
                else:
                    res["detail"] = str(e)
            except Exception as e:  # SQL errors etc.
                res["detail"] = f"{type(e).__name__}: {e}"
            results.append(res)
    return results


def deploy(model: SemanticModel, runner, names: TableNames, create_metric_views: bool | None = None) -> DeployReport:
    rep = DeployReport()
    rep.findings += static_checks(model)
    if rep.errors:
        return rep
    rep.findings += data_checks(model, runner, names)
    if rep.errors:
        return rep

    # Metric views are a Databricks feature; locally we only render the DDL.
    if create_metric_views is None:
        create_metric_views = runner.mode == "databricks"
    for logical, ddl in all_ddl(model, names):
        if create_metric_views:
            runner.execute(ddl)
        rep.views.append(logical)

    for table, rows in registry_rows(model, runner, names).items():
        runner.write_table(names.physical(f"registry.{table}"), rows)
        rep.registry_tables[table] = len(rows)

    rep.golden = run_golden_questions(model, runner, names)
    return rep
