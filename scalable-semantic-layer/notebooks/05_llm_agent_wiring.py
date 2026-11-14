# Databricks notebook source
# MAGIC %md
# MAGIC # 05 · Wiring an LLM agent (cookbook) to the tools
# MAGIC The LLM gets `skills/core/SKILL.md` as instructions and `TOOL_SCHEMAS` as its tools.
# MAGIC Any tool-calling LLM works (Claude via Databricks Foundation Model APIs or the Anthropic API,
# MAGIC Mosaic AI Agent Framework, etc.). The loop below is provider-neutral: plug your client into `call_llm`.

# COMMAND ----------
# MAGIC %pip install pyyaml --quiet

# COMMAND ----------
import os, sys, json
ROOT = os.path.abspath("..")
sys.path.insert(0, ROOT)

from semantic_layer import SparkRunner, TableNames, load_model
from semantic_layer.agent_tools import SemanticTools, TOOL_SCHEMAS

model = load_model(ROOT)
tools = SemanticTools(model, SparkRunner(spark), TableNames(model.platform, "databricks"))
SYSTEM_PROMPT = open(f"{ROOT}/skills/core/SKILL.md").read()

def call_llm(system, messages, tools_schema):
    """Replace with your provider. Must return either
       {"tool": name, "args": {...}}  or  {"answer": "text"}."""
    raise NotImplementedError

def agent(question, max_steps=8):
    messages = [{"role": "user", "content": question}]
    for _ in range(max_steps):
        step = call_llm(SYSTEM_PROMPT, messages, TOOL_SCHEMAS)
        if "answer" in step:
            return step["answer"]
        result = tools.call(step["tool"], step["args"])          # deterministic layer does the work
        messages.append({"role": "assistant", "content": json.dumps(step)})
        messages.append({"role": "user", "content": json.dumps(result, default=str)[:20000]})
    return "Stopped: too many steps."

# COMMAND ----------
# What the LLM would do for "Why did OTD drop in North in August?" (shown without an LLM):
for name, args in [
    ("find_metric", {"term": "OTD"}),
    ("check_combinable", {"metrics": ["OTD", "forecast accuracy", "on_hand_units"]}),
    ("business_rules", {"domains": ["logistics", "demand_planning", "supply_planning"]}),
]:
    print(name, "->", json.dumps(tools.call(name, args), indent=2)[:800], "\n")
