"""End-to-end demo on SQLite. No Databricks needed. Run:  python scripts/run_local_demo.py

Walks through every layer, then onboards Manufacturing to prove that a new domain needs
only a package folder and data, with zero code or skill changes.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from demo_data import generator as g                       # noqa: E402
from semantic_layer import SqliteRunner, TableNames, deploy, load_model  # noqa: E402
from semantic_layer.agent_tools import SemanticTools        # noqa: E402
from semantic_layer.cli import _table                       # noqa: E402
from semantic_layer.ontology import Ontology                # noqa: E402


def banner(t):
    print("\n" + "=" * 100 + f"\n{t}\n" + "=" * 100)


def show(out):
    if "error" in out:
        print(json.dumps(out, indent=2))
        return
    print("\n".join(out["explanation"]) + "\n")
    print(_table(out["rows"]))


def main():
    work = Path(tempfile.mkdtemp())
    domains = work / "domains"
    shutil.copytree(ROOT / "domains", domains)          # the 'live' domain folder for this demo

    banner("1. LOAD DEMO DATA (5 domains) AND DEPLOY THE SEMANTIC LAYER")
    model = load_model(ROOT, [domains])
    names = TableNames(model.platform, "sqlite")
    runner = SqliteRunner(str(work / "demo.db"))
    for t, n in g.load(runner, names, g.core_tables()).items():
        print(f"  {t:28} {n:>7} rows")
    rep = deploy(model, runner, names)
    print(f"\nValidation findings: {len(rep.findings)}   Golden questions: "
          f"{sum(x['passed'] for x in rep.golden)}/{len(rep.golden)} passed")
    print(f"Registry tables published: {rep.registry_tables}")
    tools = SemanticTools(model, runner, names)

    banner("2. TAXONOMY: 'What is OTD?'")
    print(json.dumps(tools.find_metric("OTD"), indent=2))

    banner("3. TAXONOMY: 'What is under Snacks?' and 'Break down Snacks order units'")
    print(tools.list_members("product", "product_family", "category", "Snacks"))
    show(tools.run_query({"metrics": ["order_units"], "breakdown": {"of": "product.category", "member": "Snacks"}}))

    banner("4. ONTOLOGY: 'How are Warehouse and Carrier related?'")
    print(json.dumps(tools.explain_relationship("Warehouse", "Carrier"), indent=2))

    banner("5. LINKAGE MATRIX (derived from packages, never hand-written)")
    print(_table(Ontology(model).linkage_rows()))

    banner("6. CROSS-DOMAIN: 'Why did OTD drop in North?' (4 domains, deterministic drill-across)")
    show(tools.run_query({
        "metrics": ["OTD", "forecast accuracy", "on_hand_units", "safety stock", "order units"],
        "by": ["calendar.fiscal_month"],
        "filters": [{"dim": "location.region", "op": "=", "value": "North"}]}))

    banner("7. REFUSALS: the layer says no instead of guessing")
    show(tools.run_query({"metrics": ["tickets", "OTD"], "by": ["location.region"]}))
    show(tools.run_query({"metrics": ["forecast_units"], "by": ["calendar.day"]}))

    banner("8. ONBOARD MANUFACTURING: copy one folder + load its data. No code changes.")
    shutil.copytree(ROOT / "domains_incoming" / "manufacturing", domains / "manufacturing")
    for t, n in g.load(runner, names, g.manufacturing_tables()).items():
        print(f"  {t:28} {n:>7} rows")
    model = load_model(ROOT, [domains])            # same code, one more folder
    rep = deploy(model, runner, names)
    print(f"\nValidation findings: {len(rep.findings)}   Golden questions: "
          f"{sum(x['passed'] for x in rep.golden)}/{len(rep.golden)} passed   "
          f"Packages: {sorted(model.packages)}")
    tools = SemanticTools(model, runner, names)
    print(json.dumps(tools.find_metric("production attainment"), indent=2))

    banner("9. NEW DOMAIN WORKS CROSS-DOMAIN IMMEDIATELY: plant output vs DC stock vs OTD in North")
    show(tools.run_query({
        "metrics": ["plan_attainment", "scrap_rate", "on_hand_units", "OTD"],
        "by": ["calendar.fiscal_month"],
        "filters": [{"dim": "location.region", "op": "=", "value": "North"}]}))

    banner("10. SCRAP RATE BY PRODUCT FAMILY: material_code reaches SKU via the governed crosswalk")
    show(tools.run_query({"metrics": ["scrap_rate"], "by": ["product.category", "product.product_family"]}))

    print(f"\nDemo database: {work / 'demo.db'}")


if __name__ == "__main__":
    main()
