# Databricks notebook source
# MAGIC %md
# MAGIC # 04 · Onboard a new domain (Manufacturing)
# MAGIC The whole onboarding is:
# MAGIC 1. Manufacturing's tables exist in `gold` (loaded below for the demo).
# MAGIC 2. Its `package.yaml` is in a package folder (here we include `domains_incoming`).
# MAGIC 3. The same deploy pipeline runs.
# MAGIC
# MAGIC No changes to the planner, the agent tools, or `skills/core/SKILL.md`.

# COMMAND ----------
# MAGIC %pip install pyyaml --quiet

# COMMAND ----------
import os, sys, json
ROOT = os.path.abspath("..")
sys.path.insert(0, ROOT)

from semantic_layer import SparkRunner, TableNames, deploy, load_model
from semantic_layer.agent_tools import SemanticTools
from demo_data import generator as g

runner = SparkRunner(spark)
model = load_model(ROOT, [os.path.join(ROOT, "domains"), os.path.join(ROOT, "domains_incoming")])
names = TableNames(model.platform, "databricks")

g.load(runner, names, g.manufacturing_tables())
report = deploy(model, runner, names)
assert report.ok, [str(f) for f in report.errors] + report.golden_failures
print("packages now:", sorted(model.packages))

# COMMAND ----------
tools = SemanticTools(model, runner, names)
out = tools.run_query({
    "metrics": ["plan_attainment", "scrap_rate", "on_hand_units", "OTD"],
    "by": ["calendar.fiscal_month"],
    "filters": [{"dim": "location.region", "op": "=", "value": "North"}]})
print("\n".join(out["explanation"]))
display(spark.createDataFrame(out["rows"]))
