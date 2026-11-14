"""ONTOLOGY LAYER: how business concepts relate.

Every joinable relation is BOUND to a physical key and tested by the validator, so the
ontology is not just a diagram: it is the deterministic join layer. The planner may only
join along these edges. Fact-to-fact relations (Shipment fulfills Order) are recorded for
explanation but are never joined; facts meet only through shared (conformed) entities.
"""
from __future__ import annotations

from collections import deque
from itertools import combinations
from typing import Optional

from .model import SemanticModel


class Ontology:
    def __init__(self, model: SemanticModel):
        self.model = model
        self.edges: list[dict] = []
        for fc in model.fact_concepts().values():
            for r in fc.relations:
                self.edges.append({"from": fc.name, "rel": r.rel, "to": r.to, "domain": fc.domain,
                                   "joinable": r.joinable, "relation": r, "fact": fc})
        for c in model.concepts.values():
            if c.is_a:
                self.edges.append({"from": c.name, "rel": "is_a", "to": c.is_a, "domain": c.domain or "",
                                   "joinable": False, "relation": None, "fact": None})

    # ---- explanation ---------------------------------------------------------
    def path(self, a: str, b: str) -> Optional[list[str]]:
        """Shortest relationship path between two concepts, as readable steps."""
        adj: dict[str, list[tuple[str, str]]] = {}
        for e in self.edges:
            adj.setdefault(e["from"], []).append((e["to"], f"{e['from']} -{e['rel']}-> {e['to']}"))
            adj.setdefault(e["to"], []).append((e["from"], f"{e['from']} -{e['rel']}-> {e['to']}"))
        seen, q = {a}, deque([(a, [])])
        while q:
            node, steps = q.popleft()
            if node == b:
                return steps
            for nxt, label in adj.get(node, []):
                if nxt not in seen:
                    seen.add(nxt)
                    q.append((nxt, steps + [label]))
        return None

    def describe_binding(self, fact_concept: str, entity: str, names) -> str:
        fc = self.model.fact_concepts()[fact_concept]
        r = fc.relation_for_entity(entity)
        ent = self.model.entities[entity]
        if r.crosswalk:
            cw = r.crosswalk
            return (f"{fc.name} -{r.rel}-> {r.to}: {names.physical(fc.fact)}.{r.key} = "
                    f"{names.physical(cw['table'])}.{cw['from_key']}, "
                    f"{cw['to_key']} = {names.physical(ent.table)}.{ent.key}")
        return (f"{fc.name} -{r.rel}-> {r.to}: {names.physical(fc.fact)}.{r.key} = "
                f"{names.physical(ent.table)}.{ent.key}")

    # ---- linkage -------------------------------------------------------------
    def shared_grain(self, concepts: list[str]) -> dict[str, str]:
        """Entities every concept carries, at the finest level all of them support."""
        fcs = [self.model.fact_concepts()[c] for c in concepts]
        common = set(fcs[0].joinable_entities())
        for fc in fcs[1:]:
            common &= set(fc.joinable_entities())
        out = {}
        for en in sorted(common):
            ent = self.model.entities[en]
            if not ent.is_conformed and len({fc.domain for fc in fcs}) > 1:
                continue  # local entities never link domains
            idx = max(ent.level_index(fc.relation_for_entity(en).level or ent.levels[0].name) for fc in fcs)
            out[en] = ent.levels[idx].name
        return out

    def linkage_rows(self) -> list[dict]:
        """Domain x domain matrix, derived (never hand-written) from each package's relations."""
        rows = []
        fcs = list(self.model.fact_concepts().values())
        for a, b in combinations(fcs, 2):
            if a.domain == b.domain:
                continue
            shared = self.shared_grain([a.name, b.name])
            rows.append({
                "domain_a": a.domain, "concept_a": a.name, "domain_b": b.domain, "concept_b": b.name,
                "shared": ", ".join(f"{e} (>= {lv})" for e, lv in shared.items()) or "none",
                "linkable": "yes" if any(e != "calendar" for e in shared) else
                            ("time only" if shared else "no"),
            })
        return rows

    # ---- registry rows -------------------------------------------------------
    def concept_rows(self) -> list[dict]:
        rows = []
        for c in self.model.concepts.values():
            ent = self.model.entity_of_concept(c.name)
            rows.append({"concept": c.name, "kind": "subtype" if c.is_a else "entity",
                         "is_a": c.is_a or "", "entity": ent or "", "condition": c.condition or "",
                         "domain": c.domain or "", "description": c.description})
        for fc in self.model.fact_concepts().values():
            rows.append({"concept": fc.name, "kind": "fact", "is_a": "", "entity": "",
                         "condition": "", "domain": fc.domain, "description": fc.description})
        return rows

    def relation_rows(self, names) -> list[dict]:
        rows = []
        for e in self.edges:
            r = e["relation"]
            if r is None:
                rows.append({"from_concept": e["from"], "relation": "is_a", "to_concept": e["to"],
                             "domain": e["domain"], "joinable": "no", "level": "", "binding": "subtype"})
                continue
            binding = (self.describe_binding(e["fact"].name, r.entity, names) if r.joinable
                       else "fact-to-fact link: explanation only, never joined")
            rows.append({"from_concept": e["from"], "relation": e["rel"], "to_concept": e["to"],
                         "domain": e["domain"], "joinable": "yes" if r.joinable else "no",
                         "level": r.level or "", "binding": binding})
        return rows
