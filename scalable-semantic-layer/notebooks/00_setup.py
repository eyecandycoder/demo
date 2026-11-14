# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · Setup
# MAGIC Creates the catalog and schemas named in `config/platform.yaml`. Run once per workspace.

# COMMAND ----------
# MAGIC %pip install pyyaml --quiet

# COMMAND ----------
import os, sys, yaml
ROOT = os.path.abspath("..")          # repo root when this lives in a Databricks Git folder
sys.path.insert(0, ROOT)
platform = yaml.safe_load(open(f"{ROOT}/config/platform.yaml"))
catalog = platform["catalog"]

spark.sql(f"CREATE CATALOG IF NOT EXISTS {catalog}")
for s in platform["schemas"].values():
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{s}")
print("ready:", catalog, list(platform["schemas"].values()))
