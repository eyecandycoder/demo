"""Load platform config, taxonomy, ontology and domain packages from YAML."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

import yaml

from .model import (Concept, DerivedMetric, Entity, FactConcept, Level, Measure,
                    Package, Relation, SemanticModel)


class LoadError(Exception):
    pass


def _read(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _entity(name: str, spec: dict, owner_domain: Optional[str] = None) -> Entity:
    try:
        return Entity(
            name=name,
            description=spec.get("description", ""),
            table=spec["table"],
            key=spec["key"],
            levels=[Level(lv["name"], lv["column"], lv.get("label", lv["name"])) for lv in spec["levels"]],
            owner=spec.get("owner", ""),
            owner_domain=owner_domain,
            concept=spec.get("concept", ""),
        )
    except KeyError as exc:
        raise LoadError(f"entity '{name}' is missing field {exc}") from exc


def load_package(path: Path) -> Package:
    raw = _read(path)
    name = raw.get("package")
    if not name:
        raise LoadError(f"{path}: 'package' is required")

    local_entities = {
        en: _entity(en, spec, owner_domain=name)
        for en, spec in (raw.get("local_entities") or {}).items()
    }

    status = raw.get("status", "draft")
    concepts: dict[str, FactConcept] = {}
    for cname, cspec in (raw.get("concepts") or {}).items():
        relations = []
        for r in cspec.get("relations") or []:
            relations.append(Relation(
                from_concept=cname,
                rel=r["rel"],
                to=r["to"],
                key=r["key"],
                level=r.get("level"),
                joinable=r.get("joinable", True),
                crosswalk=r.get("crosswalk"),
            ))
        measures = {}
        for mname, mspec in (cspec.get("measures") or {}).items():
            measures[mname] = Measure(
                name=mname, domain=name, concept=cname,
                expr=mspec["expr"],
                additivity=mspec.get("additivity", "additive"),
                time_agg=mspec.get("time_agg"),
                unit=mspec.get("unit", ""),
                category=mspec.get("category", "General"),
                description=mspec.get("description", ""),
                synonyms=list(mspec.get("synonyms") or []),
                status=status,
            )
        concepts[cname] = FactConcept(
            name=cname, domain=name,
            description=cspec.get("description", ""),
            fact=cspec["fact"],
            grain=list(cspec.get("grain") or []),
            relations=relations,
            measures=measures,
        )

    derived = {}
    for dname, dspec in (raw.get("derived_metrics") or {}).items():
        derived[dname] = DerivedMetric(
            name=dname, domain=name,
            numerator=dspec["numerator"], denominator=dspec["denominator"],
            one_minus=bool(dspec.get("one_minus", False)),
            unit=dspec.get("unit", ""),
            category=dspec.get("category", "General"),
            description=dspec.get("description", ""),
            synonyms=list(dspec.get("synonyms") or []),
            status=status,
        )
        for c in concepts.values():
            if dspec["numerator"] in c.measures:
                derived[dname].concept = c.name

    return Package(
        name=name,
        version=str(raw.get("version", "0.0.0")),
        status=status,
        owner=raw.get("owner", ""),
        group=raw.get("group", "Other"),
        description=raw.get("description", ""),
        concepts=concepts,
        derived=derived,
        local_entities=local_entities,
        business_rules=list(raw.get("business_rules") or []),
        golden_questions=list(raw.get("golden_questions") or []),
        path=str(path),
    )


def discover_packages(domain_dirs: Iterable[str | os.PathLike]) -> list[Path]:
    """Every <dir>/<domain>/package.yaml under the given roots. Adding a folder = adding a domain."""
    found = []
    for root in domain_dirs:
        root = Path(root)
        if not root.exists():
            continue
        for p in sorted(root.glob("*/package.yaml")):
            found.append(p)
    return found


def load_model(project_root: str | os.PathLike, domain_dirs: Optional[Iterable[str]] = None) -> SemanticModel:
    root = Path(project_root)
    platform = _read(root / "config" / "platform.yaml")

    entities = {n: _entity(n, s) for n, s in _read(root / "config" / "taxonomy" / "entities.yaml")["entities"].items()}

    concepts: dict[str, Concept] = {}
    for cname, cspec in _read(root / "config" / "ontology" / "core.yaml")["concepts"].items():
        concepts[cname] = Concept(
            name=cname, entity=cspec.get("entity", ""), description=cspec.get("description", ""),
            is_a=cspec.get("is_a"), condition=cspec.get("condition"),
        )

    dirs = list(domain_dirs) if domain_dirs is not None else [root / "domains"]
    packages: dict[str, Package] = {}
    for path in discover_packages(dirs):
        pkg = load_package(path)
        if pkg.name in packages:
            raise LoadError(f"duplicate package name '{pkg.name}' ({path})")
        packages[pkg.name] = pkg
        for en, ent in pkg.local_entities.items():
            entities.setdefault(en, ent)  # clashes are reported by the validator
            cname = ent.concept or en.title()
            concepts.setdefault(cname, Concept(name=cname, entity=en, description=ent.description, domain=pkg.name))

    model = SemanticModel(platform=platform, entities=entities, concepts=concepts, packages=packages)

    # Resolve each relation's target entity through the ontology (Warehouse -> Location -> location).
    for fc in model.fact_concepts().values():
        for r in fc.relations:
            if r.joinable:
                r.entity = model.entity_of_concept(r.to)
    return model
