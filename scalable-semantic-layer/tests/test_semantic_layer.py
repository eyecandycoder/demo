"""Run:  python -m unittest discover -s tests -v"""
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from demo_data import generator as g  # noqa: E402
from semantic_layer import Planner, PlanningError, SqliteRunner, TableNames, deploy, load_model  # noqa: E402
from semantic_layer.agent_tools import SemanticTools  # noqa: E402
from semantic_layer.metric_views import all_ddl  # noqa: E402
from semantic_layer.validate import static_checks  # noqa: E402


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = load_model(ROOT, [ROOT / "domains", ROOT / "domains_incoming"])
        cls.names = TableNames(cls.model.platform, "sqlite")
        cls.runner = SqliteRunner()
        g.load(cls.runner, cls.names, g.core_tables())
        g.load(cls.runner, cls.names, g.manufacturing_tables())
        cls.planner = Planner(cls.model, cls.names)

    def run_req(self, req):
        return self.runner.query(self.planner.plan(req).sql)


class TestPipeline(Base):
    def test_deploy_is_clean_and_golden_questions_pass(self):
        rep = deploy(self.model, self.runner, self.names)
        self.assertEqual([str(f) for f in rep.errors], [])
        self.assertEqual(rep.golden_failures, [])
        self.assertEqual(len(rep.golden), 18)

    def test_metric_view_ddl_generated_for_every_fact(self):
        views = [v for v, _ in all_ddl(self.model, TableNames(self.model.platform))]
        self.assertIn("semantic.mv_logistics_shipment", views)
        self.assertIn("semantic.mv_manufacturing_productionrun", views)
        self.assertIn("semantic.dimx_manufacturing_product", views)  # crosswalk helper


class TestCompilerGuarantees(Base):
    def test_same_request_same_sql(self):
        req = {"metrics": ["OTD", "forecast_accuracy"], "by": ["location.region", "calendar.fiscal_month"]}
        self.assertEqual(self.planner.plan(req).sql, self.planner.plan(req).sql)

    def test_no_fan_out_in_drill_across(self):
        """Totals in a 3-domain answer equal each domain's own total."""
        alone = self.run_req({"metrics": ["order_units"], "by": ["location.region"]})
        combo = self.run_req({"metrics": ["order_units", "shipments", "forecast_units"], "by": ["location.region"]})
        self.assertEqual({r["location_region"]: r["order_units"] for r in alone},
                         {r["location_region"]: r["order_units"] for r in combo})
        raw = self.runner.query("SELECT SUM(order_qty) AS n FROM gold__fact_order_line")[0]["n"]
        self.assertEqual(sum(r["order_units"] for r in combo), raw)

    def test_snapshot_uses_last_day_not_sum(self):
        rows = self.run_req({"metrics": ["on_hand_units"], "by": ["calendar.fiscal_month"]})
        aug = next(r for r in rows if r["calendar_fiscal_month"] == "2026-08")
        direct = self.runner.query(
            "SELECT SUM(on_hand_units) AS n FROM gold__fact_inventory_daily WHERE snapshot_date_key = 20260831")[0]["n"]
        self.assertEqual(aug["on_hand_units"], direct)

    def test_ratio_recomputed_not_averaged(self):
        row = self.run_req({"metrics": ["OTD"]})[0]
        raw = self.runner.query("SELECT SUM(on_time_flag) * 1.0 / COUNT(*) AS r FROM gold__fact_shipment")[0]["r"]
        self.assertAlmostEqual(row["otd_pct"], raw)

    def test_crosswalk_reaches_product(self):
        rows = self.run_req({"metrics": ["good_units"], "by": ["product.business_unit"]})
        raw = self.runner.query("SELECT SUM(good_qty) AS n FROM gold__fact_production")[0]["n"]
        self.assertEqual(sum(r["good_units"] for r in rows), raw)

    def test_breakdown_uses_taxonomy(self):
        plan = self.planner.plan({"metrics": ["order_units"], "breakdown": {"of": "product.category", "member": "Snacks"}})
        self.assertEqual([str(d) for d in plan.dims], ["product.product_family"])


class TestRefusals(Base):
    def assertRefused(self, req, code):
        with self.assertRaises(PlanningError) as cm:
            self.planner.plan(req)
        self.assertEqual(cm.exception.code, code)

    def test_missing_link(self):
        self.assertRefused({"metrics": ["tickets"], "by": ["location.region"]}, "no_common_grain")

    def test_level_too_fine(self):
        self.assertRefused({"metrics": ["forecast_units"], "by": ["calendar.day"]}, "level_too_fine")

    def test_local_dimension_cross_domain(self):
        self.assertRefused({"metrics": ["OTD", "scrap_rate"], "by": ["carrier.carrier"]}, "local_dimension_cross_domain")

    def test_ambiguous_or_unknown_term_returns_error_not_guess(self):
        tools = SemanticTools(self.model, self.runner, self.names)
        self.assertEqual(tools.find_metric("happiness index")["error"], "unknown_term")


class TestValidatorBlocksBadPackages(unittest.TestCase):
    def _model_with(self, yaml_text):
        work = Path(tempfile.mkdtemp())
        shutil.copytree(ROOT / "domains", work / "domains")
        (work / "domains" / "bad").mkdir()
        (work / "domains" / "bad" / "package.yaml").write_text(textwrap.dedent(yaml_text))
        return load_model(ROOT, [work / "domains"])

    def test_bad_package_is_rejected(self):
        model = self._model_with("""
            package: bad
            status: certified
            owner: someone@demo-co.com
            concepts:
              Thing:
                description: x
                fact: gold.fact_thing
                grain: [id]
                relations:
                  - {rel: in_place, to: Galaxy, key: galaxy_id}
                measures:
                  late_count: {expr: COUNT(*), additivity: sometimes, description: x, synonyms: [OTD]}
            business_rules:
              - OTD is SUM(on_time) / COUNT(*)
        """)
        checks = {f.check for f in static_checks(model) if f.package == "bad" and f.severity == "ERROR"}
        self.assertTrue({"unknown_concept", "bad_additivity", "ambiguous_term", "definition_in_rules",
                         "golden_questions", "no_conformed_entity"} <= checks, checks)


if __name__ == "__main__":
    unittest.main()
