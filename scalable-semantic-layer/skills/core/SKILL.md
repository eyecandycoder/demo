---
name: supply-chain-analytics-core
description: Answer business questions about supply chain data (orders, logistics, planning, service, manufacturing and any domain added later) using the governed semantic layer tools. Use for any metric, KPI, breakdown or cross-domain question.
---

# Supply chain analytics (core skill)

This skill is generic. It never names a specific domain, table or metric formula, so it
does not change when a new domain is onboarded. Domain knowledge lives in the registry
(taxonomy + ontology) and is fetched with the tools below.

## Hard rules

1. Never write SQL and never name tables. Only the `plan_query` / `run_query` tools produce SQL.
2. Use only metric and dimension names returned by the tools. Dimensions are `entity.level`.
3. Never combine metrics the tools say cannot be combined. Explain why and offer the suggestion returned.
4. Never restate a metric formula from memory. Use `find_metric`.
5. If a term is ambiguous, ask the user to choose between the returned candidates.

## Workflow

1. **Understand terms.** For each business term in the question call `find_metric`.
   - "What is X?" questions end here: answer with description, formula, owner and status.
2. **Breakdowns.** "Break down X" or "what is under X": use `list_members`, or a request with
   `breakdown: {of: <entity.level>, member: X}`.
3. **Relationships.** "How is A related to B?": call `explain_relationship`.
4. **Cross-domain.** If metrics come from more than one domain, call `check_combinable` first and
   pick dimensions from `combinable_by`.
5. **Domain behaviour.** Call `business_rules` for only the domains involved and follow them
   (for example default interpretations).
6. **Run.** Call `run_query` with `{metrics, by, filters}`.
7. **Answer.** Lead with the finding in one or two sentences, then a small table.
   Cite each metric by name, mention any caveat lines from the explanation (draft metrics,
   snapshot logic, site-type notes), and never present a correlation across domains as proven cause.

## Request examples

- OTD by region last month:
  `{"metrics": ["OTD"], "by": ["location.region"], "filters": [{"dim": "calendar.fiscal_month", "op": "=", "value": "2026-08"}]}`
- Why did North OTD drop? (diagnostic, several domains):
  `{"metrics": ["OTD", "forecast_accuracy", "on_hand_units", "safety_stock_units"], "by": ["calendar.fiscal_month"], "filters": [{"dim": "location.region", "op": "=", "value": "North"}]}`
- Break down Snacks:
  `{"metrics": ["order_units"], "breakdown": {"of": "product.category", "member": "Snacks"}}`
