"""In-memory model of the semantic layer.

Everything here is loaded from YAML (config/ + domains/). Nothing is hand-coded per domain:
adding a domain adds objects to these structures, it never adds code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Union


@dataclass
class Level:
    name: str
    column: str
    label: str = ""


@dataclass
class Entity:
    """A dimension in the taxonomy: conformed (shared) or local to one domain."""
    name: str
    description: str
    table: str                      # logical name, e.g. gold.dim_product
    key: str
    levels: list[Level]             # finest -> coarsest
    owner: str = ""
    owner_domain: Optional[str] = None   # None = conformed; otherwise the domain that owns it
    concept: str = ""               # ontology concept name that represents this entity

    def level(self, name: str) -> Level:
        for lv in self.levels:
            if lv.name == name:
                return lv
        raise KeyError(name)

    def level_index(self, name: str) -> int:
        for i, lv in enumerate(self.levels):
            if lv.name == name:
                return i
        raise KeyError(name)

    def has_level(self, name: str) -> bool:
        return any(lv.name == name for lv in self.levels)

    @property
    def is_conformed(self) -> bool:
        return self.owner_domain is None


@dataclass
class Concept:
    """An ontology concept that maps onto an entity (Product, Warehouse, Carrier ...)."""
    name: str
    entity: str
    description: str = ""
    is_a: Optional[str] = None
    condition: Optional[str] = None   # e.g. location_type = 'DC' for Warehouse
    domain: Optional[str] = None      # set for local concepts


@dataclass
class Relation:
    """An ontology edge from a fact concept, BOUND to a physical key."""
    from_concept: str
    rel: str
    to: str
    key: str
    level: Optional[str] = None
    joinable: bool = True
    crosswalk: Optional[dict] = None  # {table, from_key, to_key}
    entity: Optional[str] = None      # resolved target entity (None for non-joinable fact links)


@dataclass
class Measure:
    name: str
    domain: str
    concept: str
    expr: str
    additivity: str = "additive"      # additive | semi_additive
    time_agg: Optional[str] = None    # last | avg (semi_additive only)
    unit: str = ""
    category: str = "General"
    description: str = ""
    synonyms: list[str] = field(default_factory=list)
    status: str = "draft"

    kind = "measure"

    @property
    def qualified(self) -> str:
        return f"{self.domain}.{self.name}"


@dataclass
class DerivedMetric:
    name: str
    domain: str
    numerator: str
    denominator: str
    one_minus: bool = False
    unit: str = ""
    category: str = "General"
    description: str = ""
    synonyms: list[str] = field(default_factory=list)
    status: str = "draft"
    concept: str = ""                 # filled in by the loader (concept of the numerator)

    kind = "derived"

    @property
    def qualified(self) -> str:
        return f"{self.domain}.{self.name}"


Metric = Union[Measure, DerivedMetric]


@dataclass
class FactConcept:
    """A business event backed by a fact table (Order, Shipment ...)."""
    name: str
    domain: str
    description: str
    fact: str
    grain: list[str]
    relations: list[Relation]
    measures: dict[str, Measure]

    def relation_for_entity(self, entity: str) -> Optional[Relation]:
        for r in self.relations:
            if r.joinable and r.entity == entity:
                return r
        return None

    def joinable_entities(self) -> list[str]:
        return [r.entity for r in self.relations if r.joinable and r.entity]


@dataclass
class Package:
    name: str
    version: str
    status: str
    owner: str
    group: str
    description: str
    concepts: dict[str, FactConcept]
    derived: dict[str, DerivedMetric]
    local_entities: dict[str, Entity]
    business_rules: list[str]
    golden_questions: list[dict]
    path: str = ""

    def all_metrics(self) -> list[Metric]:
        out: list[Metric] = []
        for c in self.concepts.values():
            out.extend(c.measures.values())
        out.extend(self.derived.values())
        return out


@dataclass
class SemanticModel:
    platform: dict
    entities: dict[str, Entity]          # conformed + local
    concepts: dict[str, Concept]         # entity-backed ontology concepts (core + local)
    packages: dict[str, Package]

    # ---- lookups -------------------------------------------------------------
    def fact_concepts(self) -> dict[str, FactConcept]:
        out = {}
        for p in self.packages.values():
            out.update(p.concepts)
        return out

    def metrics(self) -> list[Metric]:
        out: list[Metric] = []
        for p in self.packages.values():
            out.extend(p.all_metrics())
        return out

    def metric(self, qualified: str) -> Metric:
        for m in self.metrics():
            if m.qualified == qualified:
                return m
        raise KeyError(qualified)

    def measure(self, domain: str, name: str) -> Measure:
        for c in self.packages[domain].concepts.values():
            if name in c.measures:
                return c.measures[name]
        raise KeyError(f"{domain}.{name}")

    def entity_of_concept(self, concept: str) -> Optional[str]:
        c = self.concepts.get(concept)
        while c is not None:
            if c.entity:
                return c.entity
            c = self.concepts.get(c.is_a) if c.is_a else None
        return None
