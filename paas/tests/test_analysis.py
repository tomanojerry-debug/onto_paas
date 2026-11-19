"""Real SQLite schema and execution tests; model stubs never count as model quality."""
import copy
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paas import analysis


def models_for_test():
    return {
        "m1_object_model.yaml": {"model_type": "OBJECT", "version": "1.0", "domain": "咨询结算",
            "entities": [
                {"id": "E-C", "name": "客户", "attributes": [{"name": "id"}, {"name": "name"}]},
                {"id": "E-I", "name": "结算单", "attributes": [
                    {"name": "id"}, {"name": "customer_id", "refEntity": "E-C"}, {"name": "amount"}]}],
            "relations": [{"id": "REL-1", "sourceEntity": "E-I", "targetEntity": "E-C"}]},
        "m3_rule_model.yaml": {"model_type": "RULE", "rules": [{"id": "R-1", "name": "金额口径"}]},
        "m_metric_model.yaml": {"model_type": "METRIC", "metrics": [
            {"id": "M-1", "name": "结算额", "formula_description": "结算单amount求和，不扣减、不排除记录",
             "depends_on_entities": ["E-I"], "rule_refs": ["R-1"]}]},
        "m2_behavior_model.yaml": {"model_type": "BEHAVIOR", "behaviors": [
            {"id": "B-1", "ownerEntity": "E-I", "relatedMetrics": ["M-1"], "appliedRules": ["R-1"]}]},
        "m4_scenario_model.yaml": {"model_type": "SCENARIO", "scenarios": [
            {"id": "S-1", "name": "客户结算汇总", "relatedBehaviors": ["B-1"], "keyMetrics": ["M-1"]}]},
        "data_mapping.yaml": {"version": "1.0", "entity_mappings": [
            {"entity_id": "E-C", "tables": [{"table": "customers", "primary": True}],
             "field_mappings": [{"ontology_field": key, "db_table": "customers", "db_column": key, "confidence": 1.0}
                                for key in ("id", "name")]},
            {"entity_id": "E-I", "tables": [{"table": "invoices", "primary": True}],
             "field_mappings": [{"ontology_field": key, "db_table": "invoices", "db_column": key, "confidence": 1.0}
                                for key in ("id", "customer_id", "amount")]}]},
    }


class StubLLM:
    def __init__(self, plan=None, report=None):
        self.plan = plan
        self.report = report if report is not None else {"evidence_ids": ["R1"]}
        self.calls = []

    def json(self, system, user, *, purpose, max_tokens):
        self.calls.append(purpose)
        if purpose == "analysis_requirements":
            return {"analysis_goals": [{"id": "G1", "title": "客户结算汇总"}],
                    "data_objects": [{"name": "结算单", "hint_tables": ["invoices"]}],
                    "metrics": [{"name": "结算额", "definition": "所有结算单amount求和", "depends_on_tables": ["invoices"]}],
                    "ai_questions": ["按开单日还是到账日统计？"], "time_range": "待确认", "focus_areas": []}
        if purpose == "analysis_sql":
            return self.plan
        if purpose == "analysis_report":
            return self.report
        if purpose == "analysis_mapping":
            return models_for_test()["data_mapping.yaml"]
        kind = purpose.removeprefix("analysis_model_").upper()
        return next(copy.deepcopy(model) for model in models_for_test().values() if model.get("model_type") == kind)


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="paas-analysis-")
        self.path = Path(self.directory.name) / "consulting.sqlite"
        with sqlite3.connect(self.path) as conn:
            conn.executescript("""
                CREATE TABLE customers(id INTEGER PRIMARY KEY,name TEXT NOT NULL,private_note TEXT);
                CREATE TABLE invoices(id INTEGER PRIMARY KEY,customer_id INTEGER NOT NULL,
                    amount REAL NOT NULL,FOREIGN KEY(customer_id) REFERENCES customers(id));
                INSERT INTO customers VALUES(1,'客户甲','非映射字段'),(2,'客户乙','非映射字段');
                INSERT INTO invoices VALUES(1,1,10),(2,1,20),(3,2,5);
            """)
        self.schema = analysis.inspect_database(self.path)
        self.project = {"description": "按客户汇总结算额，所有记录参与，不使用电商指标",
                        "source": {"path": str(self.path), "schema": self.schema, "sha256": self.schema["sha256"]},
                        "analysis_requirements": {"metrics": [{"name": "结算额", "definition": "全部amount合计"}]},
                        "models": models_for_test()}

    def tearDown(self):
        self.directory.cleanup()

    def plan(self, sql, **extra):
        return {"sql": sql, "parameters": {}, "metric_ids": ["M-1"], "chart": {"type": "table"}, **extra}

    def test_inspects_actual_schema_and_keys_without_business_rows(self):
        self.assertEqual({t["name"] for t in self.schema["tables"]}, {"customers", "invoices"})
        invoices = next(t for t in self.schema["tables"] if t["name"] == "invoices")
        self.assertEqual(invoices["primary_key"], ["id"])
        self.assertEqual(invoices["foreign_keys"][0]["table"], "customers")
        self.assertNotIn("客户甲", str(self.schema))
        self.assertEqual(self.schema["sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_rejects_non_sqlite_symlink_and_oversize(self):
        text = Path(self.directory.name) / "not.sqlite"
        text.write_text("not a database")
        link = Path(self.directory.name) / "alias.sqlite"
        link.symlink_to(self.path)
        for path in (text, link):
            with self.assertRaises(analysis.AnalysisError):
                analysis.inspect_database(path)
        with patch.object(analysis, "MAX_DATABASE_BYTES", 10):
            with self.assertRaises(analysis.AnalysisError):
                analysis.inspect_database(self.path)

    def test_project_source_directory_bounds_file_access(self):
        source_root = self.path.resolve().parent / "sources"
        source_root.mkdir()
        snapshot = source_root / "attached.sqlite"
        snapshot.write_bytes(self.path.read_bytes())
        self.assertEqual(analysis.inspect_database(snapshot, source_root=source_root), self.schema)
        llm = StubLLM()
        with self.assertRaisesRegex(analysis.AnalysisError, "当前分析项目的快照目录"):
            analysis.run_analysis(self.project, "结算总额", llm, source_root=source_root)
        self.assertEqual(llm.calls, [])

    def test_rejects_virtual_tables(self):
        virtual = Path(self.directory.name) / "virtual.sqlite"
        with sqlite3.connect(virtual) as conn:
            try:
                conn.execute("CREATE VIRTUAL TABLE search USING fts5(body)")
            except sqlite3.OperationalError:
                self.skipTest("SQLite build has no FTS5")
        with self.assertRaisesRegex(analysis.AnalysisError, "虚拟表"):
            analysis.inspect_database(virtual)

    def test_requirement_parser_preserves_unconfirmed_questions(self):
        result = analysis.analyze_requirements(self.project, StubLLM())
        self.assertEqual(result["ai_questions"], ["按开单日还是到账日统计？"])
        self.assertEqual(result["data_objects"][0]["hint_tables"], ["invoices"])

    def test_generates_five_models_and_mapping_for_non_ecommerce_source(self):
        llm = StubLLM()
        models = analysis.generate_models(self.project, llm)
        self.assertEqual(len(models), 6)
        self.assertEqual(analysis.validate_models(models, self.schema), [])
        self.assertEqual(llm.calls, ["analysis_model_object", "analysis_model_rule", "analysis_model_metric",
                                     "analysis_model_behavior", "analysis_model_scenario", "analysis_mapping"])

    def test_unknown_field_and_cross_reference_are_rejected(self):
        models = models_for_test()
        models["data_mapping.yaml"]["entity_mappings"][0]["field_mappings"][0]["db_column"] = "invented"
        models["m2_behavior_model.yaml"]["behaviors"][0]["relatedMetrics"] = ["M-DOES-NOT-EXIST"]
        errors = analysis.validate_models(models, self.schema)
        self.assertTrue(any("invented" in error for error in errors))
        self.assertTrue(any("M-DOES-NOT-EXIST" in error for error in errors))

    def test_missing_entity_attribute_mapping_is_rejected(self):
        models = models_for_test()
        models["data_mapping.yaml"]["entity_mappings"][1]["field_mappings"].pop()
        self.assertTrue(any("属性未映射" in error for error in analysis.validate_models(models, self.schema)))

    def test_valid_field_with_low_confidence_requires_human_review(self):
        models = models_for_test()
        binding = models["data_mapping.yaml"]
        mapping = binding["entity_mappings"][1]["field_mappings"][-1]
        mapping.update(db_column="id", confidence=0.01, note="金额与编号字段语义不一致")
        self.assertEqual(analysis.validate_models(models, self.schema), [])
        self.assertEqual(analysis.mapping_review_items(binding), [
            {"entity_id": "E-I", "ontology_field": "amount", "db_table": "invoices", "db_column": "id",
             "confidence": 0.01, "note": "金额与编号字段语义不一致"},
        ])
        mapping.update(db_column="amount", mapping_origin="manual")
        self.assertEqual(analysis.mapping_review_items(binding), [])

    def test_real_join_aggregate_statistics_and_provenance_preserve_source(self):
        original = self.path.read_bytes()
        plan = self.plan("SELECT c.name AS customer,SUM(i.amount) AS total FROM invoices i "
                         "JOIN customers c ON i.customer_id=c.id GROUP BY c.id,c.name ORDER BY c.id",
                         chart={"type": "bar", "x": "customer", "y": ["total"]})
        llm = StubLLM(plan)
        result = analysis.run_analysis(self.project, "按客户汇总结算额", llm)
        self.assertEqual(result["rows"], [{"customer": "客户甲", "total": 30.0}, {"customer": "客户乙", "total": 5.0}])
        self.assertEqual(result["statistics"]["numeric_columns"]["total"]["sum"], 35)
        self.assertEqual(result["provenance"]["tables"], ["customers", "invoices"])
        self.assertFalse(result["provenance"]["truncated"])
        self.assertTrue(result["model_called"])
        self.assertEqual(llm.calls, ["analysis_sql", "analysis_report"])
        self.assertEqual(original, self.path.read_bytes())

    def test_report_discards_narrative_that_contradicts_executed_query(self):
        plan = self.plan("SELECT SUM(amount) AS total FROM invoices")
        for rejected in ({"summary": "结算总额 999999。"}, {"evidence_ids": ["R999999"]},
                         {"evidence_ids": ["R1"], "recommendations": ["结算总额 999999。"]}):
            with self.subTest(rejected=rejected):
                result = analysis.run_analysis(self.project, "结算总额", StubLLM(plan, rejected))
                self.assertEqual(result["rows"], [{"total": 35.0}])
                self.assertNotIn("999999", str(result["report"]))
                self.assertIn("total = 35.0", result["report"]["findings"][0])
                self.assertEqual(result["report"]["verification"], {
                    "mode": "verified_preview", "evidence": [{"id": "R1", "row": 1, "columns": ["total"]}],
                })

    def test_report_renders_selected_evidence_from_actual_rows_and_statistics(self):
        plan = self.plan("SELECT amount FROM invoices ORDER BY id")
        result = analysis.run_analysis(self.project, "汇总结算额",
                                       StubLLM(plan, {"evidence_ids": ["R3", "S1"]}))
        self.assertEqual(result["report"]["verification"]["mode"], "verified_selection")
        self.assertIn("amount = 5.0", result["report"]["findings"][0])
        self.assertIn("最大 20.0", result["report"]["findings"][1])
        self.assertEqual([item["id"] for item in result["report"]["verification"]["evidence"]], ["R3", "S1"])

    def test_named_parameters_are_bound_and_literal_content_cannot_write(self):
        malicious_value = "客户甲'; DROP TABLE invoices;--"
        llm = StubLLM(self.plan("SELECT id FROM customers WHERE name=:customer",
                                parameters={"customer": malicious_value}))
        result = analysis.run_analysis(self.project, "找指定客户", llm)
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["report"]["verification"]["mode"], "empty_result")
        self.assertEqual(llm.calls, ["analysis_sql"])
        self.assertEqual({t["name"] for t in analysis.inspect_database(self.path)["tables"]}, {"customers", "invoices"})

    def test_blocks_writes_system_tables_extensions_unmapped_fields_and_unverified_joins(self):
        forbidden = [
            "DELETE FROM invoices", "SELECT id FROM invoices; DELETE FROM invoices",
            "SELECT name FROM sqlite_schema", "PRAGMA database_list",
            "SELECT load_extension('x') FROM invoices", "SELECT randomblob(1000) FROM invoices",
            "SELECT private_note FROM customers", "SELECT id FROM invoices UNION SELECT id FROM customers",
            "SELECT i.id FROM invoices i,customers c",
            "SELECT i.id FROM invoices i JOIN customers c ON i.id=c.id",
            "SELECT i.id FROM invoices i JOIN customers c ON (i.customer_id=c.id)=0",
            "SELECT i.id FROM invoices i JOIN customers c ON i.customer_id=c.id OR 1=1",
        ]
        original = self.path.read_bytes()
        for sql in forbidden:
            with self.subTest(sql=sql):
                llm = StubLLM(self.plan(sql))
                with self.assertRaises(analysis.AnalysisError):
                    analysis.run_analysis(self.project, "安全测试", llm)
                self.assertNotIn("analysis_report", llm.calls)
        self.assertEqual(original, self.path.read_bytes())

    def test_rejects_stale_source_before_model_call(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT INTO invoices VALUES(4,1,99)")
        llm = StubLLM(self.plan("SELECT SUM(amount) AS total FROM invoices"))
        with self.assertRaisesRegex(analysis.AnalysisError, "版本已变化"):
            analysis.run_analysis(self.project, "总额", llm)
        self.assertEqual(llm.calls, [])

    def test_fk_join_cannot_silently_duplicate_parent_aggregation(self):
        for expression in ("SUM(c.id)", '"sum"(c.id)', "SUM(DISTINCT i.amount)", "COUNT(*)"):
            with self.subTest(expression=expression):
                with self.assertRaises(analysis.AnalysisError):
                    analysis.run_analysis(self.project, "客户统计", StubLLM(self.plan(
                        f"SELECT {expression} AS result FROM invoices i JOIN customers c ON i.customer_id=c.id")))
        result = analysis.run_analysis(self.project, "客户去重数", StubLLM(self.plan(
            "SELECT COUNT(DISTINCT c.id) AS customers FROM invoices i JOIN customers c ON i.customer_id=c.id")))
        self.assertEqual(result["rows"], [{"customers": 2}])

    def test_row_cap_refuses_incomplete_result_and_progress_budget_stops_query(self):
        with sqlite3.connect(self.path) as conn:
            conn.executemany("INSERT INTO invoices VALUES(?,?,?)", [(i,1,1) for i in range(4,1004)])
        self.project["source"]["schema"] = analysis.inspect_database(self.path)
        self.project["source"]["sha256"] = self.project["source"]["schema"]["sha256"]
        with self.assertRaisesRegex(analysis.AnalysisError, "500行"):
            analysis.run_analysis(self.project, "全部明细", StubLLM(self.plan("SELECT id,amount FROM invoices")))
        with patch.object(analysis, "MAX_QUERY_STEPS", 0):
            with self.assertRaisesRegex(analysis.AnalysisError, "interrupted"):
                analysis.run_analysis(self.project, "总额", StubLLM(self.plan("SELECT SUM(amount) AS total FROM invoices")))

    def test_composite_foreign_key_must_be_complete(self):
        schema = {"tables": [
            {"name": "parent", "columns": [], "primary_key": ["a", "b"], "unique_keys": [["a", "b"]], "foreign_keys": []},
            {"name": "child", "columns": [], "foreign_keys": [
                {"id": 0, "seq": 0, "table": "parent", "from": "x", "to": "a"},
                {"id": 0, "seq": 1, "table": "parent", "from": "y", "to": "b"}]}]}
        with self.assertRaisesRegex(analysis.AnalysisError, "完整Schema外键"):
            analysis._check_sql("SELECT c.x FROM child c JOIN parent p ON c.x=p.a", schema, {"child", "parent"})
        self.assertTrue(analysis._check_sql(
            "SELECT c.x FROM child c JOIN parent p ON c.x=p.a AND c.y=p.b", schema, {"child", "parent"}))


if __name__ == "__main__":
    unittest.main()
