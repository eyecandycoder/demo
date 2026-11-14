"""TAXONOMY LAYER: what exists and how it breaks down.

* Metric tree:     group > domain > category > metric      (from packages)
* Dimension trees: entity > level (finest -> coarsest)     (from config/taxonomy)
* Members:         actual values under each level          (from the dimension tables)

The agent asks the taxonomy 'what is OTD?' or 'what is under Snacks?' instead of having
those answers hard-coded in a skill.
"""
from __future__ import annotations

from typing import Optional

from .model import DerivedMetric, Metric, SemanticModel
from .runtime import TableNames


class TermNotFound(Exception):
    def __init__(self, term: str, suggestions: list[str]):
        self.term, self.suggestions = term, suggestions
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        super().__init__(f"No metric matches '{term}'.{hint}")


class AmbiguousTerm(Exception):
    def __init__(self, term: str, candidates: list[str]):
        self.term, self.candidates = term, candidates
        super().__init__(f"'{term}' matches several metrics: {', '.join(candidates)}. Ask the user which one.")


def _norm(s: str) -> str:
    return " ".join(s.lower().replace("_", " ").replace("-", " ").split())


class Taxonomy:
    def __init__(self, model: SemanticModel):
        self.model = model
        self._index: dict[str, set[str]] = {}
        for m in model.metrics():
            for term in [m.qualified, m.name, *m.synonyms]:
                self._index.setdefault(_norm(term), set()).add(m.qualified)

    # ---- metric lookup -------------------------------------------------------
    def resolve(self, term: str) -> Metric:
        """Deterministic term -> metric. (In production, vector search proposes; this confirms.)"""
        hits = self._index.get(_norm(term), set())
        if len(hits) == 1:
            return self.model.metric(next(iter(hits)))
        if len(hits) > 1:
            raise AmbiguousTerm(term, sorted(hits))
        words = set(_norm(term).split())
        near = sorted({q for k, qs in self._index.items() for q in qs if words & set(k.split())})
        raise TermNotFound(term, near[:5])

    def describe(self, term: str) -> dict:
        m = self.resolve(term)
        pkg = self.model.packages[m.domain]
        out = {
            "metric": m.qualified,
            "description": m.description,
            "unit": m.unit,
            "taxonomy_path": f"{pkg.group} > {m.domain} > {m.category} > {m.name}",
            "owner": pkg.owner,
            "status": m.status,
            "synonyms": m.synonyms,
        }
        if isinstance(m, DerivedMetric):
            out["formula"] = (("1 - " if m.one_minus else "") + f"{m.numerator} / {m.denominator}")
        else:
            out["formula"] = m.expr
        out["can_be_sliced_by"] = self.sliceable_by(m)
        return out

    def sliceable_by(self, m: Metric) -> list[str]:
        concept = self.model.fact_concepts()[m.concept]
        out = []
        for r in concept.relations:
            if not r.joinable or not r.entity:
                continue
            ent = self.model.entities[r.entity]
            start = ent.level_index(r.level) if r.level else 0
            out.extend(f"{ent.name}.{lv.name}" for lv in ent.levels[start:])
        return out

    # ---- hierarchy -----------------------------------------------------------
    def child_level(self, entity: str, level: str) -> Optional[str]:
        ent = self.model.entities[entity]
        i = ent.level_index(level)
        return ent.levels[i - 1].name if i > 0 else None

    def members(self, runner, names: TableNames, entity: str, level: str,
                parent_level: Optional[str] = None, parent_member: Optional[str] = None) -> list[str]:
        ent = self.model.entities[entity]
        col = ent.level(level).column
        sql = f"SELECT DISTINCT {col} AS m FROM {names.physical(ent.table)}"
        if parent_level:
            pcol = ent.level(parent_level).column
            sql += f" WHERE {pcol} = '{str(parent_member).replace(chr(39), chr(39) * 2)}'"
        sql += " ORDER BY 1"
        return [r["m"] for r in runner.query(sql)]

    # ---- registry rows -------------------------------------------------------
    def nodes(self) -> list[dict]:
        rows: list[dict] = []
        seen = set()

        def add(node_id, parent, ntype, name, **kw):
            if node_id in seen:
                return
            seen.add(node_id)
            rows.append({"node_id": node_id, "parent_id": parent or "", "node_type": ntype, "name": name,
                         "domain": kw.get("domain", ""), "description": kw.get("description", ""),
                         "unit": kw.get("unit", ""), "synonyms": "|".join(kw.get("synonyms", [])),
                         "status": kw.get("status", "")})

        add("metrics", None, "root", "Metrics")
        for p in self.model.packages.values():
            g = f"metrics/{p.group}"
            add(g, "metrics", "group", p.group)
            d = f"{g}/{p.name}"
            add(d, g, "domain", p.name, domain=p.name, description=p.description, status=p.status)
            for m in p.all_metrics():
                c = f"{d}/{m.category}"
                add(c, d, "category", m.category, domain=p.name)
                add(f"metric:{m.qualified}", c, "metric", m.qualified, domain=p.name,
                    description=m.description, unit=m.unit, synonyms=m.synonyms, status=m.status)

        add("dimensions", None, "root", "Dimensions")
        for e in self.model.entities.values():
            eid = f"dimensions/{e.name}"
            add(eid, "dimensions", "entity", e.name, domain=e.owner_domain or "", description=e.description)
            parent = eid
            for lv in reversed(e.levels):         # coarsest at the top of the tree
                lid = f"{eid}/{lv.name}"
                add(lid, parent, "level", f"{e.name}.{lv.name}", domain=e.owner_domain or "",
                    description=lv.label)
                parent = lid
        return rows

    def hierarchy_member_rows(self, runner, names: TableNames) -> list[dict]:
        rows = []
        for e in self.model.entities.values():
            if not runner.table_exists(names.physical(e.table)):
                continue
            for i in range(len(e.levels) - 1):
                child, parent = e.levels[i], e.levels[i + 1]
                sql = (f"SELECT DISTINCT {parent.column} AS p, {child.column} AS c "
                       f"FROM {names.physical(e.table)} ORDER BY 1, 2")
                for r in runner.query(sql):
                    rows.append({"entity": e.name, "parent_level": parent.name, "parent_member": str(r["p"]),
                                 "level": child.name, "member": str(r["c"])})
        return rows
