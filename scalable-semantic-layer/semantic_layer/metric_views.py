"""Generate Databricks Metric Views from domain packages.

One metric view per fact concept, named semantic.mv_<domain>_<concept>. Dimensions are
exposed as <entity>_<level> for every level the fact can reach, so BI tools, Genie and the
planner's 'metric_view' engine all see the same governed definitions.

Crosswalk relations (e.g. material_code -> sku_id) are flattened through a generated helper
view so the metric view only needs single-hop joins.

NOTE: Metric View YAML syntax is versioned by Databricks. This targets 'version: 0.1' with
star-schema joins; check the current docs before deploying and adjust METRIC_VIEW_VERSION.
"""
from __future__ import annotations

from .model import DerivedMetric, FactConcept, SemanticModel
from .runtime import TableNames

METRIC_VIEW_VERSION = "0.1"


def helper_view_name(fc: FactConcept, entity: str) -> str:
    return f"semantic.dimx_{fc.domain}_{entity}"


def helper_views(model: SemanticModel, names: TableNames) -> dict[str, str]:
    """CREATE VIEW statements that pre-join crosswalks to their dimension."""
    out = {}
    for fc in model.fact_concepts().values():
        for r in fc.relations:
            if r.joinable and r.crosswalk:
                ent = model.entities[r.entity]
                cw = r.crosswalk
                logical = helper_view_name(fc, r.entity)
                out[logical] = (
                    f"CREATE OR REPLACE VIEW {names.physical(logical)} AS\n"
                    f"SELECT x.{cw['from_key']}, d.*\n"
                    f"FROM {names.physical(cw['table'])} x\n"
                    f"JOIN {names.physical(ent.table)} d ON x.{cw['to_key']} = d.{ent.key}"
                )
    return out


def _q(s: str) -> str:
    return '"' + s.replace('"', '\\"') + '"'


def metric_view_yaml(model: SemanticModel, fc: FactConcept, names: TableNames) -> str:
    # Only fields Databricks accepts: version, source, joins, dimensions, measures, filter, ...
    # (no 'comment' keys). Descriptions live in the registry tables instead.
    lines = [f"version: {METRIC_VIEW_VERSION}", f"source: {names.physical(fc.fact)}", "joins:"]
    dims = []
    for r in fc.relations:
        if not (r.joinable and r.entity):
            continue
        ent = model.entities[r.entity]
        if r.crosswalk:
            src, on = names.physical(helper_view_name(fc, r.entity)), f"source.{r.key} = {r.entity}.{r.crosswalk['from_key']}"
        else:
            src, on = names.physical(ent.table), f"source.{r.key} = {r.entity}.{ent.key}"
        lines += [f"  - name: {r.entity}", f"    source: {src}", f"    on: {on}"]
        start = ent.level_index(r.level) if r.level else 0
        for lv in ent.levels[start:]:
            dims.append((f"{ent.name}_{lv.name}", f"{r.entity}.{lv.column}", f"{lv.label} ({r.rel} {r.to})"))
    lines.append("dimensions:")
    for name, expr, comment in dims:
        lines += [f"  - name: {name}", f"    expr: {expr}"]
    lines.append("measures:")
    pkg = model.packages[fc.domain]
    skipped = []
    for m in fc.measures.values():
        if m.additivity == "semi_additive":
            skipped.append(m.name)
            continue
        lines += [f"  - name: {m.name}", f"    expr: {m.expr}"]
    for d in pkg.derived.values():
        if d.concept != fc.name:
            continue
        num, den = fc.measures[d.numerator], fc.measures[d.denominator]
        if "semi_additive" in (num.additivity, den.additivity):
            skipped.append(d.name)
            continue
        expr = f"{num.expr} / NULLIF({den.expr}, 0)"
        if d.one_minus:
            expr = f"1 - {expr}"
        lines += [f"  - name: {d.name}", f"    expr: {expr}"]
    if skipped:
        lines.insert(2, f"# snapshot measures served by the compiler, not this view: {', '.join(skipped)}")
    return "\n".join(lines)


def metric_view_ddl(model: SemanticModel, fc: FactConcept, names: TableNames) -> str:
    view = names.physical(f"semantic.mv_{fc.domain}_{fc.name.lower()}")
    return (f"CREATE OR REPLACE VIEW {view}\nWITH METRICS\nLANGUAGE YAML\nAS $$\n"
            f"{metric_view_yaml(model, fc, names)}\n$$")


def all_ddl(model: SemanticModel, names: TableNames) -> list[tuple[str, str]]:
    out = list(helper_views(model, names).items())
    for fc in model.fact_concepts().values():
        out.append((f"semantic.mv_{fc.domain}_{fc.name.lower()}", metric_view_ddl(model, fc, names)))
    return out
