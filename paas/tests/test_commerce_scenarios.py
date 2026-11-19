"""Regression guards for the separately approved upstream scenario plans."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from paas import analysis, commerce_scenarios as scenarios, upstream_commerce
from paas.store import digest


class CommerceScenarioTests(unittest.TestCase):
    def test_catalog_requires_current_approved_source_and_exact_full_model_bundle(self):
        source_hash = upstream_commerce.AUTHOR_SOURCE_SHA256
        models = {"m6-flow-model.yaml": {"flows": [{"id": "SCN-GMV-001"}]}}
        bindings = {"entity_mappings": []}
        approved_hash = digest({"models": models, "source_bindings": bindings})
        requirements = "Verify original public demo scenarios"
        approved_requirements = digest(requirements)
        project = {
            "kind": "analysis",
            "source": {"sha256": source_hash, "schema": {"sha256": source_hash}},
            "analysis_models_source_sha256": source_hash,
            "models": models,
            "source_bindings": bindings,
            "models_confirmation": {"sha256": approved_hash},
            "requirements": requirements,
            "requirements_confirmation": {"sha256": approved_requirements},
            "models_requirements_sha256": approved_requirements,
        }
        with patch.object(upstream_commerce, "REPAIRED_CANONICAL_SHA256", approved_hash):
            self.assertEqual([row["step_count"] for row in scenarios.available_scenarios(project)], [6, 6, 7])
            for change in (
                {"analysis_models_source_sha256": "different"},
                {"models_confirmation": {}},
                {"models": {}},
                {"requirements": "not yet approved"},
                {"model_diagnostics": ["unresolved metric"]},
            ):
                self.assertEqual(scenarios.available_scenarios({**project, **change}), [])

    def test_intent_exact_trigger_needs_approved_bundle_and_preserves_scoped_queries(self):
        model = Mock()
        self.assertIsNone(scenarios.match_intent({}, "GMV", model))
        with patch.object(scenarios, "_available", return_value=True):
            self.assertEqual(scenarios.match_intent({}, " ＧＭＶ？", model), {
                "scenario_id": "SCN-GMV-001", "confidence": 95, "matched_keywords": ["GMV"], "source": "rule",
            })
            for question in ("2025 年 GMV", "按渠道拆解 GMV", "上个月的客户流失", "销售额是多少"):
                with self.subTest(question=question):
                    self.assertIsNone(scenarios.match_intent({}, question, model))
        model.json.assert_not_called()

    def test_intent_uses_upstream_rules_and_rejects_unproved_or_ambiguous_routes(self):
        model = Mock()
        question = "想了解客户还有没有继续回来"
        response = {"scenario_id": "SCN-CHURN-001", "confidence": 75, "matched_keywords": [], "alternatives": []}
        model.json.return_value = response
        with patch.object(scenarios, "_available", return_value=True):
            self.assertEqual(scenarios.match_intent({}, question, model),
                             {"scenario_id": "SCN-CHURN-001", "confidence": 75, "matched_keywords": [], "source": "model"})
            system, payload = model.json.call_args.args
            self.assertIn("语义相近但未直接命中", system)
            self.assertNotIn(question, system)
            payload = json.loads(payload)
            self.assertEqual(payload["question"], question)
            self.assertEqual({item["id"] for item in payload["available_scenarios"]}, set(scenarios.PLAN_FILES))
            for invalid in (
                {**response, "scenario_id": "UNAPPROVED"},
                {**response, "confidence": 45},
                {**response, "confidence": 95, "matched_keywords": ["客户流失"]},
                {**response, "alternatives": [{"scenario_id": "SCN-ANOMALY-001", "confidence": 72}]},
            ):
                with self.subTest(invalid=invalid):
                    model.json.return_value = invalid
                    self.assertIsNone(scenarios.match_intent({}, question, model))

    def test_source_plan_file_tampering_fails_before_sql(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "scenario_gmv.yaml").write_text("id: SCN-GMV-001\nsteps: []\n")
            with patch.object(scenarios, "ASSET_ROOT", path):
                with self.assertRaisesRegex(analysis.AnalysisError, "摘要"):
                    scenarios._load_plan("SCN-GMV-001")

    def test_sql_write_remains_forbidden_and_the_public_snapshot_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.sqlite"
            connection = sqlite3.connect(path)
            try:
                connection.executescript(
                    "CREATE TABLE t_order (gmt_create TEXT, payment_amount REAL, status INTEGER);"
                    "INSERT INTO t_order VALUES ('2026-02-14', 23.5, 1), ('2026-02-18', 2.5, 5);"
                )
            finally:
                connection.close()
            original = path.read_bytes()
            plan, _ = scenarios._load_plan("SCN-GMV-001")
            allowed = {"t_order": {"gmt_create", "payment_amount", "status"}}
            result = scenarios._run_fixed_query(path.resolve(), plan["id"], plan["steps"][0], allowed, None)
            self.assertEqual(result["rows"], [{"period": "2026-02", "gmv": 23.5, "order_count": 1}])
            write = {"id": "S1", "fallback_sql": "DELETE FROM t_order"}
            with self.assertRaises(analysis.AnalysisError):
                scenarios._run_fixed_query(path.resolve(), plan["id"], write, allowed, None)
            self.assertEqual(path.read_bytes(), original)

    def test_churn_facts_use_separate_people_denominators_and_keep_user_rows_local(self):
        plan, _ = scenarios._load_plan("SCN-CHURN-001")
        result = {
            "S1": {"rows": [
                {"period": "2026-03", "active_users": 70, "new_users": 14},
                {"period": "2026-04", "active_users": 60, "new_users": 9},
                {"period": "2026-05", "active_users": 28, "new_users": 4},
            ]},
            "S2": {"rows": [
                {"bucket": "1) 0-30天", "user_count": 10},
                {"bucket": "4) >180天", "user_count": 2, "avg_monetary": 4500.0},
            ]},
            "S3": {"rows": [{"dimension_name": "注册渠道", "dimension_value": "APP", "user_count": 2}]},
            "S4": {"row_count": 2, "rows": [{"user_id": "never-transmit"}]},
            "S5": {"statistics": {"scored": [{"user_id": "never-transmit"}, {"user_id": "also-private"}],
                                  "segment_counts": {"重要挽留客户": 2}}},
            "S6": {"rows": [
                {"period": "2026-03", "repurchase_rate": 20.0},
                {"period": "2026-04", "repurchase_rate": 25.0, "repurchase_users": 15, "total_users": 60},
                {"period": "2026-05", "repurchase_rate": 5.0},
            ]},
        }

        class Choose:
            def json(self, system, payload, **kwargs):
                self.evidence = json.loads(payload)
                return {"fact_ids": ["rfm_sample", "historical_buyer_money"],
                        "action_ids": ["test_reactivation"]}

        model = Choose()
        report = scenarios._run_report(plan, plan["steps"][-1], result, model)
        self.assertNotIn("user_id", json.dumps(model.evidence))
        self.assertNotIn("never-transmit", json.dumps(model.evidence))
        self.assertIn("已对按历史实付排序的前 2 名", report["summary"])
        self.assertIn("每位曾购用户平均历史累计实付 4,500.00 元", report["findings"][1])
        self.assertIn("分母为该分桶的曾购用户人数", report["findings"][1])
        self.assertNotIn("客单价", " ".join(report["findings"]))
        self.assertNotIn("2026-05", model.evidence["facts"]["buyer_activity"]["text"])
        self.assertNotIn("2026-05", model.evidence["facts"]["repurchase_complete_months"]["text"])
        self.assertTrue({"S4", "S5"} <= set(report["evidence_steps"]))

    def test_report_rejects_unlisted_ids_duplicates_and_model_written_claims(self):
        plan, _ = scenarios._load_plan("SCN-GMV-001")
        facts = {
            "peak_month": {"text": "该月达到峰值。", "steps": ["S1"]},
            "completed_mom": {"text": "同环比按完整月份比较。", "steps": ["S2"]},
        }
        valid = {"fact_ids": ["peak_month", "completed_mom"], "action_ids": ["check_campaign"]}
        with patch.dict(scenarios.REPORT_FACTS, {plan["id"]: lambda results: facts}):
            for choice in (
                {**valid, "summary": "活动造成了增长"},
                {**valid, "fact_ids": ["peak_month", "made_up_ratio"]},
                {**valid, "fact_ids": ["peak_month", "peak_month"]},
                {**valid, "action_ids": ["no_such_action"]},
            ):
                with self.subTest(choice=choice):
                    model = Mock()
                    model.json.return_value = choice
                    with self.assertRaises(analysis.AnalysisError):
                        scenarios._run_report(plan, plan["steps"][-1], {}, model)
