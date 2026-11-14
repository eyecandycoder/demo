"""Scalable semantic layer: taxonomy + ontology + deterministic compiler, driven by domain packages."""
from .loader import load_model
from .planner import Planner, PlanningError
from .runtime import SparkRunner, SqliteRunner, TableNames
from .taxonomy import Taxonomy
from .ontology import Ontology
from .pipeline import deploy

__all__ = ["load_model", "Planner", "PlanningError", "SparkRunner", "SqliteRunner", "TableNames",
           "Taxonomy", "Ontology", "deploy"]
