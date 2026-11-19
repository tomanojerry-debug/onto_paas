"""Conversion protects meanings and physical bindings across model formats."""
import unittest

from paas import business, ontology
from paas.tests import test_analysis as fixtures


class AnalysisStandardTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AnalysisTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.legacy = fixtures.models_for_test()

    @property
    def scenario(self):
        return self.legacy["m4_scenario_model.yaml"]["scenarios"][0]

    def add_flow(self):
        self.scenario.update(
            intent="按实际订单汇总",
            actors=["分析师"],
            outputs=["客户结算表"],
            primaryFlow=[
                {"stepId": "read", "type": "DATA_COLLECT", "description": "读取已绑定视图",
                 "relatedEntities": ["E-I"]},
                {"stepId": "aggregate", "type": "STAT_COMPUTE", "description": "按客户汇总",
                 "appliedRules": ["R-1"], "relatedMetrics": ["M-1"]},
            ],
        )

    def diagnostics(self, models, bindings):
        return ontology.analysis_diagnostics(models, bindings, self.fixture.schema)

    def test_round_trip_keeps_sources_and_uses_shared_seven_file_registry(self):
        self.add_flow()
        self.legacy["m_metric_model.yaml"]["metrics"][0].update(
            supported_dimensions=["customer"], sql_template="SELECT sum(amount) FROM invoices",
        )
        self.legacy["data_mapping.yaml"]["source_db"] = {"timezone": "Asia/Shanghai"}
        models, bindings = ontology.analysis_to_seven(self.legacy)

        self.assertIs(business.MODEL_FILES, ontology.MODEL_FILES)
        self.assertEqual(set(models), set(ontology.MODEL_FILES))
        self.assertEqual(self.legacy["m1_object_model.yaml"], models["m1-object-model.yaml"])
        self.assertEqual(self.legacy["m_metric_model.yaml"]["metrics"], models["m7-report-model.yaml"]["metrics"])
        self.assertEqual([], models["m7-report-model.yaml"]["query_reports"])
        flow = models["m6-flow-model.yaml"]["flows"][0]
        self.assertEqual("ANALYSIS", flow["flowType"])
        self.assertEqual(("S-1", "read", ["aggregate"]), (flow["id"], flow["startActivity"], flow["endActivities"]))
        self.assertEqual(["aggregate"], flow["activities"][0]["nextActivities"])
        self.assertEqual("DATA_COLLECT", flow["activities"][0]["activityType"])
        self.assertNotIn("primaryFlow", flow)
        self.assertEqual([], models["m5-actor-model.yaml"]["roles"])
        self.assertEqual([], models["mu-ui-model.yaml"]["screens"])
        self.assertEqual(bindings, self.legacy["data_mapping.yaml"])
        self.assertEqual(self.legacy, ontology.analysis_projection(models, bindings))
        self.assertEqual([], self.diagnostics(models, bindings))

        bindings["source_db"]["timezone"] = "UTC"
        models["m7-report-model.yaml"]["metrics"][0]["supported_dimensions"].clear()
        self.assertEqual("Asia/Shanghai", self.legacy["data_mapping.yaml"]["source_db"]["timezone"])
        self.assertEqual(["customer"], self.legacy["m_metric_model.yaml"]["metrics"][0]["supported_dimensions"])

    def test_legacy_projects_and_absent_or_empty_primary_flow_remain_readable(self):
        self.assertEqual(self.legacy, ontology.analysis_projection(self.legacy))
        for value in (None, []):
            if value is not None:
                self.scenario["primaryFlow"] = value
            with self.subTest(value=value):
                models, bindings = ontology.analysis_to_seven(self.legacy)
                self.assertEqual(self.legacy, ontology.analysis_projection(models, bindings))
                self.assertEqual([], self.diagnostics(models, bindings))

    def test_source_diagnostics_keep_unresolved_references_and_explain_missing_metrics(self):
        self.scenario["keyMetrics"].append("M-UNDECLARED")
        doc = self.legacy["m_metric_model.yaml"]
        doc["metrics"].append({"id": "M-DESCRIPTIVE", "formula_description": "只定义了口径"})
        rule = self.legacy["m3_rule_model.yaml"]
        rule["rules"].append({"id": "R-2", "reusedBy": ["R-1", "B-1"]})
        models, bindings = ontology.analysis_to_seven(self.legacy)

        self.assertEqual(self.legacy, ontology.analysis_projection(models, bindings))
        errors = self.diagnostics(models, bindings)
        self.assertEqual(2, len(errors), errors)
        self.assertTrue(any("M-UNDECLARED" in error for error in errors))
        self.assertTrue(any("M-DESCRIPTIVE" in error for error in errors))

    def test_projection_rejects_mixed_profiles_and_invalid_links(self):
        self.add_flow()
        models, bindings = ontology.analysis_to_seven(self.legacy)
        models["m6-flow-model.yaml"]["flows"][0]["activities"][0]["nextActivities"] = ["unknown"]
        self.assertIn("无法投影分支或循环", self.diagnostics(models, bindings)[0])

        models, bindings = ontology.analysis_to_seven(self.legacy)
        models["m1-object-model.yaml"]["aggregates"] = []
        self.assertIn("同时声明 entities 和业务 aggregates", self.diagnostics(models, bindings)[0])

        models, bindings = ontology.analysis_to_seven(self.legacy)
        models["m5-actor-model.yaml"]["roles"] = [{"roleId": "ANALYST"}]
        self.assertIn("不支持模型中的角色与权限", self.diagnostics(models, bindings)[0])

    def test_structure_rejects_duplicates_and_conflicting_sources(self):
        self.add_flow()
        self.scenario["primaryFlow"][1]["stepId"] = "read"
        with self.assertRaisesRegex(ValueError, "重复 stepId"):
            ontology.analysis_to_seven(self.legacy)
        self.scenario["primaryFlow"][1]["stepId"] = "aggregate"

        models, bindings = ontology.analysis_to_seven(self.legacy)
        models["data_mapping.yaml"] = bindings
        self.assertIn("含额外文件", self.diagnostics(models, bindings)[0])
        models.pop("data_mapping.yaml")
        self.assertIn("缺少有效的数据源绑定", self.diagnostics(models, None)[0])

        missing_mapping = self.legacy.pop("data_mapping.yaml")
        with self.assertRaisesRegex(ValueError, "缺少.*data_mapping.yaml"):
            ontology.analysis_to_seven(self.legacy)
        self.legacy["data_mapping.yaml"] = missing_mapping


if __name__ == "__main__":
    unittest.main()
