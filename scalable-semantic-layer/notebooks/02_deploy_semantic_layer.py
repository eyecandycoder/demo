# Databricks notebook source
# MAGIC %md
# MAGIC # 02 · Deploy the semantic layer
# MAGIC The same pipeline CI runs on every package change:
# MAGIC **static checks → data checks → metric views → registry tables (taxonomy, ontology, linkage) → golden questions.**
# MAGIC
# MAGIC Nothing here names a domain. A new domain is picked up because its folder exists.

# COMMAND ----------
# MAGIC %pip install pyyaml --quiet

# COMMAND ----------
dbutils.widgets.text("domain_dirs", "domains", "Comma-separated package folders")

# COMMAND ----------
import os, sys
ROOT = os.path.abspath("..")
sys.path.insert(0, ROOT)

from semantic_layer import SparkRunner, TableNames, deploy, load_model

dirs = [os.path.join(ROOT, d.strip()) for d in dbutils.widgets.get("domain_dirs").split(",")]
model = load_model(ROOT, dirs)
names = TableNames(model.platform, "databricks")
report = deploy(model, SparkRunner(spark), names)

print(f"packages: {sorted(model.packages)}")
print(f"views:    {report.views}")
print(f"registry: {report.registry_tables}")

# COMMAND ----------
if report.findings:
    display(spark.createDataFrame([(f.severity, f.package, f.check, f.message) for f in report.findings],
                                  ["severity", "package", "check", "message"]))
if report.golden:
    display(spark.createDataFrame([(x["package"], x["question"], x["passed"], x["detail"]) for x in report.golden],
                                  ["package", "question", "passed", "detail"]))

# COMMAND ----------
# Fail the job (and the CI deploy) if anything is wrong. Nothing half-deployed reaches users.
assert report.ok, f"{len(report.errors)} errors, {len(report.golden_failures)} golden failures"

# COMMAND ----------
# MAGIC %md
# MAGIC ### What the agent can now read
# MAGIC Registry tables live in `<catalog>.semantic_registry`:
# MAGIC `taxonomy_nodes`, `metrics`, `hierarchy_members`, `ontology_concepts`, `ontology_relations`,
# MAGIC `linkage_matrix`, `business_rules`, `packages`.

# COMMAND ----------
reg = f"{model.platform['catalog']}.{model.platform['schemas']['registry']}"
display(spark.sql(f"SELECT * FROM {reg}.linkage_matrix"))
