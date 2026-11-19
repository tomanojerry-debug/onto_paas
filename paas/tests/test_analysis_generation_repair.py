"""Bounded generation repairs preserve schema checks and the caller's budget."""
import copy
import json
import sqlite3
import unittest

from paas import analysis
from paas.tests import test_analysis as fixtures


class GenerationLLM(fixtures.StubLLM):
    def __init__(self, outcome='valid', corrupt=True, mutate_source=None):
        super().__init__()
        self.outcome, self.corrupt, self.mutate_source = outcome, corrupt, mutate_source
        self.repairs = []

    def json(self, system, user, *, purpose, max_tokens):
        if purpose.startswith('analysis.repair.'):
            self.calls.append(purpose)
            self.repairs.append(json.loads(user))
            rule = copy.deepcopy(fixtures.models_for_test()['m3_rule_model.yaml'])
            if self.outcome == 'invalid':
                rule['rules'][0]['reusedBy'] = ['UNKNOWN-BEHAVIOR']
            if self.outcome == 'new-file':
                return {'replacements': {'unknown.yaml': rule}}
            return {'replacements': {'m3_rule_model.yaml': rule}, 'explanation': '修复不存在的跨文件引用'}
        result = super().json(system, user, purpose=purpose, max_tokens=max_tokens)
        if purpose == 'analysis_model_rule' and self.corrupt:
            result['rules'][0]['reusedBy'] = ['UNKNOWN-BEHAVIOR']
        if purpose == 'analysis_mapping' and self.mutate_source:
            self.mutate_source()
        return result


class AnalysisGenerationRepairTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AnalysisTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_complete_valid_generation_needs_no_repair(self):
        llm = GenerationLLM(corrupt=False)
        result = analysis.generate_models(self.fixture.project, llm)
        self.assertEqual([], analysis.validate_models(result, self.fixture.schema))
        self.assertEqual([], llm.repairs)
        self.assertEqual(6, len(llm.calls))

    def test_cross_file_generation_error_repairs_once_with_real_schema_and_errors(self):
        llm = GenerationLLM()
        result = analysis.generate_models(self.fixture.project, llm)
        self.assertEqual([], analysis.validate_models(result, self.fixture.schema))
        self.assertEqual(1, len(llm.repairs))
        self.assertIn('UNKNOWN-BEHAVIOR', str(llm.repairs[0]['errors']))
        self.assertEqual(self.fixture.schema, llm.repairs[0]['schema'])
        self.assertEqual(self.fixture.project['analysis_requirements'], llm.repairs[0]['requirements'])
        self.assertEqual(7, len(llm.calls))
        self.assertEqual(fixtures.models_for_test()['m_metric_model.yaml'], result['m_metric_model.yaml'])

    def test_repair_is_limited_to_two_attempts_without_sample_substitution(self):
        llm = GenerationLLM(outcome='invalid')
        with self.assertRaisesRegex(ValueError, '修复仍未通过'):
            analysis.generate_models(self.fixture.project, llm)
        self.assertEqual(2, len(llm.repairs))
        self.assertEqual(8, len(llm.calls))

    def test_repair_cannot_inject_new_files(self):
        llm = GenerationLLM(outcome='new-file')
        with self.assertRaisesRegex(ValueError, '未知文件'):
            analysis.generate_models(self.fixture.project, llm)
        self.assertEqual(1, len(llm.repairs))

    def test_changed_source_refuses_repair_before_further_provider_calls(self):
        def change():
            with sqlite3.connect(self.fixture.path) as connection:
                connection.execute('UPDATE invoices SET amount=999 WHERE id=1')
        llm = GenerationLLM(mutate_source=change)
        with self.assertRaisesRegex(ValueError, '快照已变化'):
            analysis.generate_models(self.fixture.project, llm)
        self.assertEqual([], llm.repairs)
        self.assertEqual(6, len(llm.calls))


if __name__ == '__main__':
    unittest.main()
