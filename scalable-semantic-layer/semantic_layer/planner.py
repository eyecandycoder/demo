"""DETERMINISTIC QUERY COMPILER.

The LLM (cookbook / agent) produces only a request that uses registered names:

    {"metrics": ["OTD", "forecast_accuracy"],
     "by": ["location.region", "calendar.fiscal_month"],
     "filters": [{"dim": "location.region", "op": "=", "value": "North"}]}

Everything else is decided here, by rule, never by the LLM:
  * which fact tables and dimension tables are used       (ontology bindings)
  * whether the metrics can be combined, and at what grain (taxonomy levels)
  * how semi-additive and ratio metrics are aggregated     (package definitions)
  * the SQL text                                           (same request -> same SQL)

Cross-domain questions use DRILL-ACROSS: each fact is aggregated separately to the requested
grain (one 'leg' per fact), and only the aggregated legs are joined on the conformed keys.
Facts are never joined to each other, so rows cannot fan out or double count.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .model import DerivedMetric, FactConcept, Measure, Metric, SemanticModel
from .ontology import Ontology
from .runtime import TableNames
from .taxonomy import Taxonomy


class PlanningError(Exception):
    def __init__(self, code: str, message: str, suggestion: str = ""):
        self.code, self.message, self.suggestion = code, message, suggestion
        super().__init__(f"[{code}] {message}" + (f" Suggestion: {suggestion}" if suggestion else ""))


@dataclass
class DimRef:
    entity: str
    level: str

    @property
    def alias(self) -> str:
        return f"{self.entity}_{self.level}"

    def __str__(self) -> str:
        return f"{self.entity}.{self.level}"


@dataclass
class Filter:
    dim: DimRef
    op: str
    value: Any


@dataclass
class Leg:
    name: str
    domain: str
    concept: str
    measures: list[Measure]
    sql: str
    engine: str
    bindings: list[str] = field(default_factory=list)


@dataclass
class Plan:
    metrics: list[Metric]
    dims: list[DimRef]
    filters: list[Filter]
    legs: list[Leg]
    sql: str
    output_columns: list[str]
    explanation: list[str]


_OPS = {"=", "!=", ">", ">=", "<", "<=", "in", "between"}


def _lit(v: Any) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


class Planner:
    def __init__(self, model: SemanticModel, names: TableNames, engine: str = "sql"):
        if engine not in ("sql", "metric_view"):
            raise ValueError("engine must be 'sql' or 'metric_view'")
        self.model, self.names, self.engine = model, names, engine
        self.taxonomy = Taxonomy(model)
        self.ontology = Ontology(model)
        self.facts = model.fact_concepts()

    # ------------------------------------------------------------------ parsing
    def _dim(self, text: str) -> DimRef:
        if "." not in text:
            raise PlanningError("bad_dimension", f"'{text}' must look like entity.level, e.g. location.region")
        entity, level = text.split(".", 1)
        ent = self.model.entities.get(entity)
        if ent is None:
            raise PlanningError("unknown_entity", f"'{entity}' is not in the taxonomy.",
                                f"Known entities: {', '.join(sorted(self.model.entities))}")
        if not ent.has_level(level):
            raise PlanningError("unknown_level", f"'{entity}' has no level '{level}'.",
                                f"Levels: {', '.join(lv.name for lv in ent.levels)}")
        return DimRef(entity, level)

    def _filters(self, raw: list[dict]) -> list[Filter]:
        out = []
        for f in raw or []:
            op = str(f.get("op", "=")).lower()
            if op not in _OPS:
                raise PlanningError("bad_filter", f"operator '{op}' is not allowed")
            out.append(Filter(self._dim(f["dim"]), op, f["value"]))
        return out

    # ----------------------------------------------------------------- planning
    def plan(self, request: dict) -> Plan:
        if not request.get("metrics"):
            raise PlanningError("no_metrics", "The request names no metrics.")

        # 1. Taxonomy: business words -> registered metrics
        metrics: list[Metric] = []
        for term in request["metrics"]:
            m = self.taxonomy.resolve(term)
            if m.qualified not in {x.qualified for x in metrics}:
                metrics.append(m)

        dims = [self._dim(d) for d in request.get("by", [])]
        filters = self._filters(request.get("filters", []))

        # Taxonomy breakdown: 'break down Snacks' = filter Snacks + group by the next level down
        if request.get("breakdown"):
            b = request["breakdown"]
            of = self._dim(b["of"])
            child = self.taxonomy.child_level(of.entity, of.level)
            if child is None:
                raise PlanningError("no_finer_level", f"{of} is already the finest level.")
            filters.append(Filter(of, "=", b["member"]))
            if str(DimRef(of.entity, child)) not in {str(d) for d in dims}:
                dims.append(DimRef(of.entity, child))

        # 2. Base measures needed, grouped per fact concept (one leg per fact)
        per_concept: dict[str, list[Measure]] = {}
        for m in metrics:
            parts = ([self.model.measure(m.domain, m.numerator), self.model.measure(m.domain, m.denominator)]
                     if isinstance(m, DerivedMetric) else [m])
            for p in parts:
                bucket = per_concept.setdefault(p.concept, [])
                if p.name not in {x.name for x in bucket}:
                    bucket.append(p)

        # 3. Ontology + taxonomy check: can every fact reach every dimension at that level?
        self._check_reachability(per_concept, dims, filters)

        # 4. Compile one leg per fact
        legs = []
        for concept, measures in per_concept.items():
            fc = self.facts[concept]
            legs.append(self._compile_leg(fc, measures, dims, filters))

        # 5. Stitch legs on the shared grain
        sql, cols = self._stitch(legs, metrics, dims)

        explanation = self._explain(metrics, dims, legs, filters)
        return Plan(metrics, dims, filters, legs, sql, cols, explanation)

    def _check_reachability(self, per_concept, dims, filters):
        problems = []
        domains = {self.facts[c].domain for c in per_concept}
        for concept in per_concept:
            fc = self.facts[concept]
            for d in [*dims, *(f.dim for f in filters)]:
                ent = self.model.entities[d.entity]
                if not ent.is_conformed and len(domains) > 1:
                    problems.append(("local_dimension_cross_domain",
                                     f"{d} belongs only to '{ent.owner_domain}' and cannot be combined "
                                     f"with other domains."))
                    continue
                r = fc.relation_for_entity(d.entity)
                if r is None:
                    problems.append(("no_common_grain",
                                     f"{fc.name} ({fc.domain}) has no link to '{d.entity}', so it cannot be "
                                     f"shown or filtered by {d}."))
                    continue
                if ent.level_index(d.level) < ent.level_index(r.level or ent.levels[0].name):
                    problems.append(("level_too_fine",
                                     f"{fc.name} ({fc.domain}) is recorded at {d.entity}.{r.level}; "
                                     f"{d} is finer than the data."))
        if problems:
            code = problems[0][0]
            shared = self.ontology.shared_grain(list(per_concept))
            hint = ("These metrics can be combined by: " + ", ".join(f"{e}.{lv} or coarser" for e, lv in shared.items())
                    if shared else "These metrics share no dimension; answer them separately.")
            raise PlanningError(code, " ".join(dict.fromkeys(p[1] for p in problems)), hint)

    # ---------------------------------------------------------------- compiling
    def _where(self, filters: list[Filter], col_of) -> str:
        parts = []
        for f in filters:
            col = col_of(f.dim)
            if f.op == "in":
                parts.append(f"{col} IN ({', '.join(_lit(v) for v in f.value)})")
            elif f.op == "between":
                parts.append(f"{col} BETWEEN {_lit(f.value[0])} AND {_lit(f.value[1])}")
            else:
                parts.append(f"{col} {f.op} {_lit(f.value)}")
        return ("\n  WHERE " + "\n    AND ".join(parts)) if parts else ""

    def _compile_leg(self, fc: FactConcept, measures: list[Measure], dims, filters) -> Leg:
        name = f"leg_{fc.domain}_{fc.name}".lower()
        semi = [m for m in measures if m.additivity == "semi_additive"]
        engine = self.engine
        if engine == "metric_view" and semi:
            engine = "sql"   # metric views hold additive measures; snapshots are compiled here
        if engine == "metric_view":
            return self._compile_leg_metric_view(fc, measures, dims, filters, name)

        joins, bindings, aliases = [], [], {}
        for en in dict.fromkeys([d.entity for d in dims] + [f.dim.entity for f in filters]):
            r = fc.relation_for_entity(en)
            ent = self.model.entities[en]
            a = f"d_{en}"
            aliases[en] = a
            if r.crosswalk:
                cw, x = r.crosswalk, f"x_{en}"
                joins.append(f"  JOIN {self.names.physical(cw['table'])} {x} ON f.{r.key} = {x}.{cw['from_key']}")
                joins.append(f"  JOIN {self.names.physical(ent.table)} {a} ON {x}.{cw['to_key']} = {a}.{ent.key}")
            else:
                joins.append(f"  JOIN {self.names.physical(ent.table)} {a} ON f.{r.key} = {a}.{ent.key}")
            bindings.append(self.ontology.describe_binding(fc.name, en, self.names))

        def col_of(d: DimRef) -> str:
            return f"{aliases[d.entity]}.{self.model.entities[d.entity].level(d.level).column}"

        dim_sel = [f"{col_of(d)} AS {d.alias}" for d in dims]
        dim_grp = [col_of(d) for d in dims]
        src = f"  FROM {self.names.physical(fc.fact)} f\n" + "\n".join(joins)
        where = self._where(filters, col_of)

        if not semi:
            sel = ",\n    ".join(dim_sel + [f"{m.expr} AS {m.name}" for m in measures])
            grp = ("\n  GROUP BY " + ", ".join(dim_grp)) if dim_grp else ""
            sql = f"SELECT\n    {sel}\n{src}{where}{grp}"
        else:
            cal = fc.relation_for_entity("calendar")
            if cal is None:
                raise PlanningError("semi_additive_without_calendar",
                                    f"{fc.name} has snapshot measures but no calendar relation.")
            # step 1: aggregate to grain + snapshot date; step 2: pick last/avg across dates
            inner_sel = ",\n      ".join(dim_sel + [f"f.{cal.key} AS snapshot_key"]
                                         + [f"{m.expr} AS {m.name}" for m in measures])
            inner = (f"SELECT\n      {inner_sel}\n{src}{where}\n  GROUP BY "
                     + ", ".join(dim_grp + [f"f.{cal.key}"]))
            part = ", ".join(d.alias for d in dims)
            over = f"OVER (PARTITION BY {part})" if part else "OVER ()"
            outer = [d.alias for d in dims]
            for m in measures:
                if m.additivity == "semi_additive" and (m.time_agg or "last") == "last":
                    outer.append(f"SUM(CASE WHEN snapshot_key = last_key THEN {m.name} END) AS {m.name}")
                elif m.additivity == "semi_additive" and m.time_agg == "avg":
                    outer.append(f"AVG({m.name}) AS {m.name}")
                else:
                    outer.append(f"SUM({m.name}) AS {m.name}")
            grp = ("\n  GROUP BY " + ", ".join(d.alias for d in dims)) if dims else ""
            sql = (f"SELECT\n    {', '.join(outer)}\n  FROM (\n    SELECT s.*, MAX(snapshot_key) {over} AS last_key"
                   f"\n    FROM (\n    {inner}\n    ) s\n  ) t{grp}")
        return Leg(name, fc.domain, fc.name, measures, sql, "sql", bindings)

    def _compile_leg_metric_view(self, fc, measures, dims, filters, name) -> Leg:
        view = self.names.physical(f"semantic.mv_{fc.domain}_{fc.name.lower()}")
        sel = ",\n    ".join([d.alias for d in dims] + [f"MEASURE({m.name}) AS {m.name}" for m in measures])
        where = self._where(filters, lambda d: d.alias)
        grp = ("\n  GROUP BY " + ", ".join(d.alias for d in dims)) if dims else ""
        sql = f"SELECT\n    {sel}\n  FROM {view}{where}{grp}"
        bindings = [self.ontology.describe_binding(fc.name, en, self.names)
                    for en in dict.fromkeys([d.entity for d in dims] + [f.dim.entity for f in filters])]
        return Leg(name, fc.domain, fc.name, measures, sql, "metric_view", bindings)

    # ---------------------------------------------------------------- stitching
    def _metric_expr(self, m: Metric, leg_of: dict[str, str]) -> str:
        if isinstance(m, DerivedMetric):
            num = self.model.measure(m.domain, m.numerator)
            den = self.model.measure(m.domain, m.denominator)
            leg = leg_of[num.concept]
            ratio = f"CAST({leg}.{num.name} AS DOUBLE) / NULLIF({leg}.{den.name}, 0)"
            return f"1 - {ratio}" if m.one_minus else ratio
        return f"{leg_of[m.concept]}.{m.name}"

    def _stitch(self, legs: list[Leg], metrics: list[Metric], dims: list[DimRef]):
        leg_of = {l.concept: l.name for l in legs}
        names = [m.name for m in metrics]
        out_names = [m.name if names.count(m.name) == 1 else f"{m.domain}_{m.name}" for m in metrics]
        ctes = [f"{l.name} AS (\n  {l.sql.replace(chr(10), chr(10) + '  ')}\n)" for l in legs]
        dcols = [d.alias for d in dims]

        if len(legs) == 1:
            l = legs[0].name
            sel = [f"{l}.{c}" for c in dcols]
            sel += [f"{self._metric_expr(m, leg_of)} AS {o}" for m, o in zip(metrics, out_names)]
            from_ = f"FROM {l}"
            order = f"\nORDER BY {', '.join(f'{l}.{c}' for c in dcols)}" if dcols else ""
        elif dcols:
            union = "\n  UNION\n".join(f"  SELECT {', '.join(dcols)} FROM {l.name}" for l in legs)
            ctes.append(f"grain_keys AS (\n{union}\n)")
            sel = [f"k.{c}" for c in dcols]
            sel += [f"{self._metric_expr(m, leg_of)} AS {o}" for m, o in zip(metrics, out_names)]
            joins = []
            for l in legs:
                cond = " AND ".join(f"k.{c} IS NOT DISTINCT FROM {l.name}.{c}" for c in dcols)
                joins.append(f"LEFT JOIN {l.name} ON {cond}")
            from_ = "FROM grain_keys k\n" + "\n".join(joins)
            order = f"\nORDER BY {', '.join(f'k.{c}' for c in dcols)}"
        else:
            sel = [f"{self._metric_expr(m, leg_of)} AS {o}" for m, o in zip(metrics, out_names)]
            from_ = "FROM " + " CROSS JOIN ".join(l.name for l in legs)
            order = ""

        sql = "WITH " + ",\n".join(ctes) + "\nSELECT\n  " + ",\n  ".join(sel) + "\n" + from_ + order
        return sql, dcols + out_names

    # -------------------------------------------------------------- explaining
    def _explain(self, metrics, dims, legs, filters=()) -> list[str]:
        lines = [f"Metrics: " + ", ".join(f"{m.qualified} [{m.status}]" for m in metrics)]
        lines.append("Grain: " + (", ".join(str(d) for d in dims) or "total"))
        if len(legs) > 1:
            lines.append(f"Drill-across: {len(legs)} legs aggregated separately, then joined on the grain "
                         f"keys. No fact-to-fact joins.")
        for l in legs:
            lines.append(f"Leg {l.name} ({l.engine}): " + ", ".join(m.name for m in l.measures))
            lines.extend(f"    via {b}" for b in l.bindings)
            if any(m.additivity == "semi_additive" for m in l.measures):
                lines.append("    snapshot measures use the last day of each period, never a sum over time")
        # Ontology subtype check: e.g. Location reached as Plant by one leg and Warehouse by another
        refs = list(dims) + [f.dim for f in filters]
        for en in dict.fromkeys(d.entity for d in refs):
            reached = {}
            for l in legs:
                r = self.facts[l.concept].relation_for_entity(en)
                if r:
                    reached.setdefault(r.to, []).append(l.domain)
            if len(reached) > 1:
                ent = self.model.entities[en]
                lvl = next(d.level for d in refs if d.entity == en)
                kinds = "; ".join(f"{k} in {', '.join(v)}" for k, v in reached.items())
                if ent.level_index(lvl) == 0:
                    lines.append(f"Caveat: {en} is reached as different kinds of site ({kinds}); "
                                 f"rows at {en}.{lvl} will not overlap. Use a coarser level such as region.")
                else:
                    lines.append(f"Note: {en} is reached as {kinds}; they are aligned at {en}.{lvl}.")
        drafts = [m.qualified for m in metrics if m.status != "certified"]
        if drafts:
            lines.append("Caveat: not yet certified: " + ", ".join(drafts))
        return lines
