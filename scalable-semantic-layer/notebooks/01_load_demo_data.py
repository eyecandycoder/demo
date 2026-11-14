# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Load demo data
# MAGIC Writes conformed dimensions and the five core domain facts to the `gold` schema.
# MAGIC In a real project these tables come from your pipelines; the semantic layer only needs them to exist.

# COMMAND ----------
# MAGIC %pip install pyyaml --quiet

# COMMAND ----------
import os, sys
ROOT = os.path.abspath("..")
sys.path.insert(0, ROOT)

from semantic_layer import SparkRunner, TableNames, load_model
from demo_data import generator as g

model = load_model(ROOT)
names = TableNames(model.platform, "databricks")
runner = SparkRunner(spark)

counts = g.load(runner, names, g.core_tables())
display(spark.createDataFrame([(k, v) for k, v in counts.items()], ["table", "rows"]))
