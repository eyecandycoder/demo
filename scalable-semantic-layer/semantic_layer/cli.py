"""CLI used by CI and by developers.

  python -m semantic_layer.cli validate                       # static checks only (no data needed)
  python -m semantic_layer.cli render-views                   # print generated metric view DDL
  python -m semantic_layer.cli linkage                        # domain linkage matrix
  python -m semantic_layer.cli describe OTD
  python -m semantic_layer.cli plan '{"metrics":["OTD"],"by":["location.region"]}'
  python -m semantic_layer.cli local-deploy --db demo.db      # load demo data + full pipeline on SQLite
  python -m semantic_layer.cli ask --db demo.db '{"metrics":["OTD"],"by":["location.region"]}'

Add --include-incoming to treat domains_incoming/ as onboarded.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent_tools import SemanticTools
from .loader import load_model
from .metric_views import all_ddl
from .ontology import Ontology
from .pipeline import deploy
from .planner import Planner, PlanningError
from .runtime import SqliteRunner, TableNames
from .taxonomy import Taxonomy
from .validate import static_checks

ROOT = Path(__file__).resolve().parent.parent


def _model(args):
    dirs = [ROOT / "domains"] + ([ROOT / "domains_incoming"] if args.include_incoming else [])
    return load_model(ROOT, dirs)


def _table(rows: list[dict]) -> str:
    if not rows:
        return "(no rows)"
    cols = list(rows[0].keys())
    fmt = lambda v: f"{v:.3f}" if isinstance(v, float) else str(v)
    w = {c: max(len(c), *(len(fmt(r[c])) for r in rows)) for c in cols}
    out = ["  ".join(c.ljust(w[c]) for c in cols), "  ".join("-" * w[c] for c in cols)]
    out += ["  ".join(fmt(r[c]).ljust(w[c]) for c in cols) for r in rows]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="semantic_layer")
    ap.add_argument("--include-incoming", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    sub.add_parser("render-views")
    sub.add_parser("linkage")
    d = sub.add_parser("describe"); d.add_argument("term")
    p = sub.add_parser("plan"); p.add_argument("request")
    ld = sub.add_parser("local-deploy"); ld.add_argument("--db", default="demo.db")
    a = sub.add_parser("ask"); a.add_argument("request"); a.add_argument("--db", default="demo.db")
    args = ap.parse_args(argv)

    model = _model(args)
    names = TableNames(model.platform, "databricks")

    if args.cmd == "validate":
        findings = static_checks(model)
        for f in findings:
            print(f)
        errors = [f for f in findings if f.severity == "ERROR"]
        print(f"{len(model.packages)} packages, {len(model.metrics())} metrics, {len(errors)} errors")
        return 1 if errors else 0

    if args.cmd == "render-views":
        for logical, ddl in all_ddl(model, names):
            print(f"-- {logical}\n{ddl};\n")
        return 0

    if args.cmd == "linkage":
        print(_table(Ontology(model).linkage_rows()))
        return 0

    if args.cmd == "describe":
        print(json.dumps(Taxonomy(model).describe(args.term), indent=2))
        return 0

    if args.cmd == "plan":
        try:
            plan = Planner(model, names).plan(json.loads(args.request))
        except PlanningError as e:
            print(e)
            return 2
        print("\n".join(plan.explanation) + "\n\n" + plan.sql)
        return 0

    if args.cmd == "local-deploy":
        from demo_data import generator as g
        Path(args.db).unlink(missing_ok=True)
        local = TableNames(model.platform, "sqlite")
        runner = SqliteRunner(args.db)
        g.load(runner, local, g.core_tables())
        if args.include_incoming:
            g.load(runner, local, g.manufacturing_tables())
        rep = deploy(model, runner, local)
        for f in rep.findings:
            print(f)
        for gq in rep.golden:
            print(f"{'PASS' if gq['passed'] else 'FAIL'}  {gq['package']:16} {gq['question']}  ({gq['detail']})")
        print(f"registry: {rep.registry_tables}")
        return 0 if rep.ok else 1

    if args.cmd == "ask":
        tools = SemanticTools(model, SqliteRunner(args.db), TableNames(model.platform, "sqlite"))
        out = tools.run_query(json.loads(args.request))
        if "error" in out:
            print(json.dumps(out, indent=2))
            return 2
        print("\n".join(out["explanation"]) + "\n")
        print(_table(out["rows"]))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
