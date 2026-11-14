"""Validation = the CI quality gate for a domain package.

Static checks need only the YAML. Data checks run SQL against the tables and prove that every
ontology relation is a real, safe join (keys match, grain is unique, crosswalks are 1:1).
A package that fails any ERROR is not deployed.
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import DerivedMetric, SemanticModel
from .runtime import TableNames

VALID_ADDITIVITY = {"additive", "semi_additive"}
VALID_TIME_AGG = {None, "last", "avg"}


@dataclass
class Finding:
    severity: str   # ERROR | WARN
    package: str
    check: str
    message: str

    def __str__(self) -> str:
        return f"{self.severity:5} {self.package:16} {self.check:24} {self.message}"


def static_checks(model: SemanticModel) -> list[Finding]:
    out: list[Finding] = []
    gates = model.platform.get("quality_gates", {})
    forbidden = model.platform.get("forbidden_in_business_rules", [])

    def err(pkg, check, msg):
        out.append(Finding("ERROR", pkg, check, msg))

    def warn(pkg, check, msg):
        out.append(Finding("WARN", pkg, check, msg))

    # names must be globally unique so the taxonomy can resolve them deterministically
    seen_concepts: dict[str, str] = {}
    entity_owner: dict[str, str] = {n: "(conformed)" for n, e in model.entities.items() if e.is_conformed}

    # one business term must mean one metric across ALL packages
    term_users: dict[str, set[str]] = {}
    for m in model.metrics():
        for term in {m.name, *m.synonyms}:
            term_users.setdefault(" ".join(term.lower().replace("_", " ").replace("-", " ").split()), set()).add(m.qualified)
    for term, users in sorted(term_users.items()):
        if len(users) > 1:
            for pkg in sorted({u.split(".")[0] for u in users}):
                err(pkg, "ambiguous_term", f"'{term}' is used by {', '.join(sorted(users))}")

    for p in model.packages.values():
        for en, ent in p.local_entities.items():
            if en in entity_owner and entity_owner[en] != p.name:
                err(p.name, "entity_name_clash",
                    f"local entity '{en}' already defined by {entity_owner[en]}; promote it to a conformed entity instead")
            entity_owner.setdefault(en, p.name)

        for cname, fc in p.concepts.items():
            if cname in model.concepts or cname in seen_concepts:
                err(p.name, "concept_name_clash", f"concept '{cname}' already exists")
            seen_concepts[cname] = p.name
            if not fc.grain:
                err(p.name, "grain_missing", f"{cname} declares no grain")

            per_entity: dict[str, str] = {}
            for r in fc.relations:
                if not r.joinable:
                    if r.to not in model.fact_concepts():
                        warn(p.name, "unknown_fact_link", f"{cname} -{r.rel}-> {r.to}: target is not a known fact concept")
                    continue
                if r.entity is None:
                    err(p.name, "unknown_concept", f"{cname} -{r.rel}-> {r.to}: '{r.to}' is not in the ontology")
                    continue
                ent = model.entities[r.entity]
                if r.level and not ent.has_level(r.level):
                    err(p.name, "unknown_level", f"{cname} -{r.rel}-> {r.to}: entity '{r.entity}' has no level '{r.level}'")
                if r.entity in per_entity:
                    err(p.name, "ambiguous_relation",
                        f"{cname} links to '{r.entity}' twice ({per_entity[r.entity]}, {r.rel}); keep one so joins stay deterministic")
                per_entity[r.entity] = r.rel
                if r.crosswalk and not {"table", "from_key", "to_key"} <= set(r.crosswalk):
                    err(p.name, "crosswalk_incomplete", f"{cname} -{r.rel}-> {r.to}: crosswalk needs table, from_key, to_key")
            if not any(model.entities[e].is_conformed for e in fc.joinable_entities()):
                err(p.name, "no_conformed_entity", f"{cname} links to no conformed entity, so it can never be combined")

            for m in fc.measures.values():
                if m.additivity not in VALID_ADDITIVITY:
                    err(p.name, "bad_additivity", f"{m.name}: '{m.additivity}' (non-additive metrics must be derived_metrics)")
                if m.time_agg not in VALID_TIME_AGG:
                    err(p.name, "bad_time_agg", f"{m.name}: '{m.time_agg}'")
                if m.additivity == "semi_additive" and fc.relation_for_entity("calendar") is None:
                    err(p.name, "snapshot_without_calendar", f"{m.name} is semi-additive but {cname} has no calendar relation")
                if not m.description:
                    err(p.name, "description_missing", f"{m.name} has no description")

        for d in p.derived.values():
            concepts = {c.name for c in p.concepts.values() if d.numerator in c.measures} | \
                       {c.name for c in p.concepts.values() if d.denominator in c.measures}
            if len(concepts) != 1 or not d.concept:
                err(p.name, "derived_refs", f"{d.name}: numerator and denominator must be measures of one concept")
            if not d.description:
                err(p.name, "description_missing", f"{d.name} has no description")


        for rule in p.business_rules:
            for bad in forbidden:
                if bad.lower() in rule.lower():
                    err(p.name, "definition_in_rules",
                        f"business rule contains '{bad.strip()}'; metric definitions belong in measures, not prose")

        need = gates.get("min_golden_questions_certified", 3)
        if p.status == "certified" and len(p.golden_questions) < need:
            err(p.name, "golden_questions", f"certified packages need >= {need} golden questions")
        elif len(p.golden_questions) < need:
            warn(p.name, "golden_questions", f"only {len(p.golden_questions)} golden questions")
        if not p.owner:
            err(p.name, "owner_missing", "package has no owner")
    return out


def data_checks(model: SemanticModel, runner, names: TableNames) -> list[Finding]:
    out: list[Finding] = []
    min_match = model.platform.get("quality_gates", {}).get("min_key_match_rate", 0.98)

    for e in model.entities.values():
        t = names.physical(e.table)
        if not runner.table_exists(t):
            out.append(Finding("ERROR", e.owner_domain or "(conformed)", "table_missing", t))
            continue
        dup = runner.query(f"SELECT COUNT(*) AS n FROM (SELECT {e.key} FROM {t} GROUP BY {e.key} HAVING COUNT(*) > 1) z")[0]["n"]
        if dup:
            out.append(Finding("ERROR", e.owner_domain or "(conformed)", "dimension_key_not_unique", f"{t}.{e.key}: {dup} duplicates"))

    for fc in model.fact_concepts().values():
        t = names.physical(fc.fact)
        if not runner.table_exists(t):
            out.append(Finding("ERROR", fc.domain, "table_missing", t))
            continue
        g = ", ".join(fc.grain)
        dup = runner.query(f"SELECT COUNT(*) AS n FROM (SELECT {g} FROM {t} GROUP BY {g} HAVING COUNT(*) > 1) z")[0]["n"]
        if dup:
            out.append(Finding("ERROR", fc.domain, "grain_not_unique", f"{fc.name}: {dup} duplicate grain rows on ({g})"))

        for r in fc.relations:
            if not (r.joinable and r.entity):
                continue
            ent = model.entities[r.entity]
            dim = names.physical(ent.table)
            concept = model.concepts.get(r.to)
            cond = f" AND d.{concept.condition}" if concept and concept.condition else ""
            if r.crosswalk:
                cw = r.crosswalk
                xt = names.physical(cw["table"])
                d2 = runner.query(f"SELECT COUNT(*) AS n FROM (SELECT {cw['from_key']} FROM {xt} "
                                  f"GROUP BY {cw['from_key']} HAVING COUNT(*) > 1) z")[0]["n"]
                if d2:
                    out.append(Finding("ERROR", fc.domain, "crosswalk_not_unique", f"{xt}.{cw['from_key']} maps to several targets"))
                sql = (f"SELECT COUNT(*) AS total, SUM(CASE WHEN d.{ent.key} IS NULL THEN 0 ELSE 1 END) AS matched "
                       f"FROM {t} f LEFT JOIN {xt} x ON f.{r.key} = x.{cw['from_key']} "
                       f"LEFT JOIN {dim} d ON x.{cw['to_key']} = d.{ent.key}")
            else:
                sql = (f"SELECT COUNT(*) AS total, SUM(CASE WHEN d.{ent.key} IS NULL THEN 0 ELSE 1 END) AS matched "
                       f"FROM {t} f LEFT JOIN {dim} d ON f.{r.key} = d.{ent.key}{cond}")
            row = runner.query(sql)[0]
            total, matched = row["total"] or 0, row["matched"] or 0
            rate = (matched / total) if total else 1.0
            sev = "ERROR" if rate < min_match else None
            if sev:
                out.append(Finding(sev, fc.domain, "key_match_rate",
                                   f"{fc.name} -{r.rel}-> {r.to}: {rate:.1%} of rows match (need {min_match:.0%})"))
    return out
