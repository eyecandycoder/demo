# Databricks notebook source
# MAGIC %md
# MAGIC # 03 · Ask questions through the agent tools
# MAGIC These are exactly the calls an LLM agent / cookbook makes. The LLM only fills in the request;
# MAGIC the deterministic layer chooses tables, joins and SQL.

# COMMAND ----------
# MAGIC %pip install pyyaml --quiet

# COMMAND ----------
import os, sys, json
ROOT = os.path.abspath("..")
sys.path.insert(0, ROOT)

from semantic_layer import SparkRunner, TableNames, load_model
from semantic_layer.agent_tools import SemanticTools

dbutils.widgets.dropdown("engine", "sql", ["sql", "metric_view"], "Execution engine")
model = load_model(ROOT, [os.path.join(ROOT, "domains")])
tools = SemanticTools(model, SparkRunner(spark), TableNames(model.platform, "databricks"),
                      engine=dbutils.widgets.get("engine"))

def show(out):
    if "error" in out:
        print(json.dumps(out, indent=2)); return
    print("\n".join(out["explanation"]))
    if out["rows"]:
        display(spark.createDataFrame(out["rows"]))

# COMMAND ----------
# Taxonomy: what is OTD?
print(json.dumps(tools.find_metric("OTD"), indent=2))

# COMMAND ----------
# Ontology: how are Warehouse and Carrier related?
print(tools.explain_relationship("Warehouse", "Carrier"))

# COMMAND ----------
# Taxonomy breakdown
show(tools.run_query({"metrics": ["order_units"], "breakdown": {"of": "product.category", "member": "Snacks"}}))

# COMMAND ----------
# Cross-domain: why did North OTD drop?
show(tools.run_query({
    "metrics": ["OTD", "forecast accuracy", "on_hand_units", "safety stock", "order units"],
    "by": ["calendar.fiscal_month"],
    "filters": [{"dim": "location.region", "op": "=", "value": "North"}]}))

# COMMAND ----------
# Refusal instead of a guess
show(tools.run_query({"metrics": ["tickets", "OTD"], "by": ["location.region"]}))
