# Scalable semantic layer for supply chain analytics (reference implementation)

A working demo of a metadata-driven semantic layer on Databricks with **taxonomy** and **ontology** layers
and a **deterministic query compiler**. A new domain is onboarded by adding one `package.yaml` folder:
no planner, agent, skill or SQL changes.

The demo ships five domains (orders, logistics, demand planning, supply planning, service) and proves
scalability by onboarding a sixth (manufacturing) that uses different product codes.

## The idea in one picture

```
User question
     │
     ▼
LLM agent / cookbook  ── reads skills/core/SKILL.md (generic, never edited per domain)
     │  only emits: {"metrics": [...], "by": ["entity.level"], "filters": [...]}
     ▼
Agent tools (semantic_layer/agent_tools.py)
     │
     ├── TAXONOMY   what exists, how it breaks down      "What is OTD?"  "What is under Snacks?"
     ├── ONTOLOGY   how things relate, bound to keys     "How are Warehouse and Carrier related?"
     └── COMPILER   deterministic SQL, drill-across      same request -> same SQL, or a refusal
     ▼
Gold tables (conformed dimensions + domain facts) and generated Databricks Metric Views
     ▲
     │ generated + validated by CI from
Domain packages  domains/<domain>/package.yaml   (what Context Studio would author)
```

| Your team's term | In this repo |
| --- | --- |
| Context Studio | Where `package.yaml` files are authored and reviewed (here: a Git folder + CI) |
| Domain package | `domains/<domain>/package.yaml` |
| Taxonomy | `config/taxonomy/entities.yaml` + metric categories in packages → `semantic_registry.taxonomy_nodes`, `metrics`, `hierarchy_members` |
| Ontology | `config/ontology/core.yaml` + `relations` in packages → `semantic_registry.ontology_concepts`, `ontology_relations` |
| Relationship rules | `relations` (each bound to a key and tested) |
| Business rules | `business_rules` in each package (behaviour only, never formulas) |
| Cookbook | The agent using `skills/core/SKILL.md` + `TOOL_SCHEMAS` |
| Metric layer | Generated Metric Views `semantic.mv_<domain>_<concept>` |

## Rules the design enforces

1. **The LLM never joins.** It picks registered metric and dimension names; the compiler decides tables, joins and SQL.
2. **Facts never join to facts.** Domains meet only through conformed entities (product, location, customer, channel, calendar).
3. **Drill-across.** Each fact is aggregated separately to the requested grain, then legs are joined on the grain keys. No fan-out.
4. **Every ontology edge is bound and tested.** Key match rate ≥ 98%, unique grain, 1:1 crosswalks, or the deploy fails.
5. **Refuse, don't guess.** Missing link, too-fine level, or a domain-only dimension used cross-domain → an error with a suggestion.
6. **Definitions live in one place.** Formulas are in measures; business rules containing formulas fail validation.

## Repo layout

```
config/
  platform.yaml              catalog, schemas, quality gates
  taxonomy/entities.yaml     conformed entities and their hierarchies (finest -> coarsest)
  ontology/core.yaml         core concepts incl. subtypes (Warehouse is_a Location)
domains/<domain>/package.yaml     live domain packages
domains_incoming/manufacturing/   a new domain waiting to be onboarded
semantic_layer/
  loader.py        YAML -> model (discovers every */package.yaml)
  taxonomy.py      term resolution, metric tree, hierarchy members, breakdowns
  ontology.py      concept graph, bound relations, paths, linkage matrix
  planner.py       deterministic compiler: request -> drill-across SQL
  metric_views.py  generates Databricks Metric View DDL
  validate.py      static + data quality gate
  pipeline.py      validate -> generate -> publish registry -> golden questions
  agent_tools.py   the only interface the LLM gets, with tool schemas
  runtime.py       Spark (Databricks) and SQLite (local/CI) runners
skills/core/SKILL.md     generic agent instructions
demo_data/generator.py   deterministic synthetic data with a planted North-region disruption
notebooks/00..05         Databricks notebooks
tests/                   guarantees: no fan-out, snapshot logic, ratios, refusals, validator
```

## Run it locally (2 minutes, no Databricks)

```bash
pip install -r requirements.txt
python scripts/run_local_demo.py            # full walkthrough incl. onboarding manufacturing
python -m unittest discover -s tests -v     # 13 tests
python -m semantic_layer.cli describe OTD
python -m semantic_layer.cli plan '{"metrics":["OTD","forecast accuracy"],"by":["location.region"]}'
python -m semantic_layer.cli --include-incoming render-views   # Databricks Metric View DDL
```

## Run it on Databricks

1. Clone the repo into a **Databricks Git folder**.
2. Set `catalog` in `config/platform.yaml` (you need permission to create schemas in it).
3. Run notebooks in order: `00_setup` → `01_load_demo_data` → `02_deploy_semantic_layer` → `03_ask_questions` → `04_onboard_manufacturing`.
4. Optional: `databricks bundle deploy` to create the `deploy-semantic-layer` job; the GitHub workflow runs it on merge to `main`.

Check before production:
- **Metric View syntax** is versioned by Databricks. The generator targets `version: 0.1` with star-schema joins
  (`METRIC_VIEW_VERSION` in `metric_views.py`). Confirm against current docs.
- The planner's default `sql` engine queries gold tables directly and works without Metric Views.
  The `metric_view` engine queries the generated views (additive measures only; snapshot measures fall back to `sql`).
- Run queries **as the asking user** so Unity Catalog row filters and column masks apply.

## Onboarding a new domain

1. Land the domain's facts in `gold` (your normal pipelines).
2. Map its keys to conformed entities. If it uses its own codes, add a crosswalk table (like `xref_material_sku`).
3. Write `domains/<new>/package.yaml`: concepts, relations (with key + level), measures, derived metrics,
   business rules, at least 3 golden questions. Start with `status: draft`.
4. Open a pull request. CI validates, tests joins on data, generates views, runs every golden question across all domains.
5. Merge → the job deploys. The agent can now answer the domain alone **and combined with every linked domain**.
6. Owner reviews answers, then sets `status: certified`.

Nothing else changes. The linkage matrix updates itself.

## How it scales

| Growth | Why it holds |
| --- | --- |
| More domains | Relations are declared once per domain against conformed entities, not per domain pair (N, not N²). |
| Bigger prompts | The agent fetches only matching metrics and the rules of involved domains. |
| More data | Each leg aggregates before joining; add pre-aggregated gold tables or materialized views for hot grains. |
| More teams | Each team owns its package; the platform owns entities, the contract and CI. |
| Drift | Golden questions run on every change; ambiguous terms and formulas in prose are blocked. |

## Next steps for a production build

- Term resolution via Databricks Vector Search over `semantic_registry.metrics` (the taxonomy confirms the final match).
- Role-playing dimensions (e.g. order date vs requested date) via named relations.
- SCD Type 2 conformed dimensions for "as of" hierarchies.
- Cross-domain derived metrics (e.g. days of cover = inventory / demand) declared at platform level.
- Plan and result caching keyed on request + data freshness.
- Authoring UI (Context Studio) that writes `package.yaml` and opens the pull request.
