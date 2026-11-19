"""Date scopes and complete-result contracts; SQLite fixtures and stubs only."""
import copy
import sqlite3
import unittest

from paas import analysis
from paas.tests import test_analysis as fixtures


class AnalysisContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AnalysisTests()
        self.fixture.setUp()
        self.project = self.fixture.project
        with sqlite3.connect(self.fixture.path) as conn:
            conn.execute('ALTER TABLE invoices ADD COLUMN issued_at TEXT')
            conn.execute("UPDATE invoices SET issued_at=CASE WHEN id<3 THEN '2026-11-15' ELSE '2026-12-01' END")
        self.refresh_source()
        self.project['models']['m1_object_model.yaml']['entities'][1]['attributes'].append({'name': 'issued_at'})
        self.project['models']['data_mapping.yaml']['entity_mappings'][1]['field_mappings'].append({
            'ontology_field': 'issued_at', 'db_table': 'invoices', 'db_column': 'issued_at', 'confidence': 1.0})
        self.project['analysis_requirements']['time_range'] = {
            'table': 'invoices', 'column': 'issued_at', 'start': '2026-11-01',
            'end': '2026-12-01', 'end_exclusive': True, 'format': 'iso_date'}

    def tearDown(self):
        self.fixture.tearDown()

    def refresh_source(self):
        schema = analysis.inspect_database(self.fixture.path)
        self.project['source'].update(schema=schema, sha256=schema['sha256'])

    def run_query(self, sql='SELECT SUM(amount) AS total FROM invoices', question='统计2026年11月结算总额', **extra):
        stub = fixtures.StubLLM(self.fixture.plan(sql, **extra))
        return analysis.run_analysis(self.project, question, stub), stub

    def test_missing_structured_time_scope_refuses_before_model_call(self):
        self.project['analysis_requirements'].pop('time_range')
        for question in ('统计2026年11月结算总额', '统计11月结算总额', '统计十一月结算总额'):
            stub = fixtures.StubLLM()
            with self.assertRaisesRegex(analysis.AnalysisError, '日期|时间'):
                analysis.run_analysis(self.project, question, stub)
            self.assertEqual(stub.calls, [])

    def test_relative_or_conflicting_question_does_not_reuse_another_scope(self):
        for question in ('统计本月结算总额', '统计上个月结算总额', '统计2026年12月结算总额', '统计2026年全年结算总额'):
            with self.subTest(question=question):
                stub = fixtures.StubLLM()
                with self.assertRaisesRegex(analysis.AnalysisError, '日期|时间|范围'):
                    analysis.run_analysis(self.project, question, stub)
                self.assertEqual(stub.calls, [])

    def test_scope_is_server_applied_when_model_omits_where(self):
        result, _ = self.run_query()
        self.assertEqual(result['rows'], [{'total': 30.0}])
        self.assertEqual(result['parameters']['__paas_scope_start'], '2026-11-01')
        self.assertEqual(result['parameters']['__paas_scope_end'], '2026-12-01')
        self.assertIn('issued_at', result['sql'])
        self.assertEqual(result['provenance']['time_scope']['end_exclusive'], True)

    def test_model_or_true_cannot_widen_server_scope(self):
        result, _ = self.run_query('SELECT SUM(i.amount) AS total FROM invoices AS i WHERE i.amount>0 OR 1=1')
        self.assertEqual(result['rows'], [{'total': 30.0}])

    def test_confirmed_snapshot_can_filter_to_an_explicit_month_and_previous_month(self):
        self.project['analysis_requirements']['time_range'] = None
        self.project['models']['m1_object_model.yaml']['entities'][1]['attributes'][-1]['type'] = 'DateTime'
        result, _ = self.run_query(
            "SELECT substr(issued_at,1,7) AS month, sum(amount) AS total FROM invoices "
            "GROUP BY substr(issued_at,1,7) ORDER BY month",
            question="2026–12 GMV骤降了多少",
        )
        self.assertEqual(result['rows'], [
            {'month': '2026-11', 'total': 30.0}, {'month': '2026-12', 'total': 5.0},
        ])
        scope = result['provenance']['time_scope']
        self.assertEqual((scope['start'], scope['end']), ('2026-11-01', '2027-01-01'))
        self.assertEqual(scope['comparison']['baseline_month'], '2026-11')
        self.assertEqual(scope['scope_origin'], 'question')

    def test_common_month_and_day_spellings_resolve_to_the_same_scope(self):
        for version in ('2026.11月', '2026/11', '2026-11月', '２０２６．１１月', '2026年11月'):
            with self.subTest(version=version):
                result, _ = self.run_query(question=f'{version}的结算额')
                self.assertEqual(result['rows'], [{'total': 30.0}])
                self.assertEqual(result['parameters']['__paas_scope_start'], '2026-11-01')
                self.assertEqual(result['parameters']['__paas_scope_end'], '2026-12-01')
        for version in ('2026.11.15', '2026/11/15', '2026年11月15日'):
            with self.subTest(version=version):
                result, _ = self.run_query(question=f'{version}的结算额')
                self.assertEqual(result['parameters']['__paas_scope_start'], '2026-11-15')
                self.assertEqual(result['parameters']['__paas_scope_end'], '2026-11-16')

    def test_explicit_comparison_preserves_direction_and_excludes_intervening_months(self):
        with sqlite3.connect(self.fixture.path) as conn:
            conn.execute("UPDATE invoices SET issued_at='2026-10-15' WHERE id=1")
        self.refresh_source()
        self.project['analysis_requirements']['time_range'].update(start='2026-10-01', end='2027-01-01')
        sql = ("SELECT substr(issued_at,1,7) AS month, sum(amount) AS total FROM invoices "
               "WHERE amount>0 OR 1=1 GROUP BY substr(issued_at,1,7) ORDER BY month")
        result, _ = self.run_query(sql, question="以2026–12月为基准，2026.10月比它高多少")
        self.assertEqual(result['rows'], [
            {'month': '2026-10', 'total': 10.0}, {'month': '2026-12', 'total': 5.0},
        ])
        scope = result['provenance']['time_scope']
        self.assertEqual(scope['comparison'], {
            'baseline_month': '2026-12', 'target_month': '2026-10', 'basis': 'explicit_months',
        })
        self.assertEqual(result['parameters']['__paas_gap_start'], '2026-11-01')
        self.assertEqual(result['parameters']['__paas_gap_end'], '2026-12-01')
        self.project['analysis_requirements']['time_range']['end'] = '2026-12-01'
        with self.assertRaisesRegex(analysis.AnalysisError, '超出已确认'):
            self.run_query(sql, question='以2026.10月为基准，比较2026/12')

    def test_month_range_includes_the_whole_end_month(self):
        self.project['analysis_requirements']['time_range']['end'] = '2027-01-01'
        result, _ = self.run_query(question='2026.11月到2026/12的结算额')
        self.assertEqual(result['rows'], [{'total': 35.0}])
        self.assertNotIn('comparison', result['provenance']['time_scope'])

    def test_ambiguous_months_and_invalid_dotted_dates_do_not_open_the_scope(self):
        for question in ('2026.13月结算额', '2026.11.99结算额', '2026.11-15结算额',
                         '2026.11月，2026.12月的结算额'):
            with self.subTest(question=question):
                with self.assertRaises(analysis.AnalysisError):
                    self.run_query(question=question)
        self.assertIsNone(analysis._question_window('筛选单笔金额不低于2026.11元的结算单'))

    def test_question_may_narrow_confirmed_dates_but_comparison_cannot_widen_them(self):
        result, _ = self.run_query(question='统计 2026-11-15 的结算额')
        self.assertEqual(result['rows'], [{'total': 30.0}])
        self.assertEqual(result['parameters']['__paas_scope_end'], '2026-11-16')
        with self.assertRaisesRegex(analysis.AnalysisError, '超出已确认'):
            self.run_query(question='2026年11月环比下降多少')

    def test_comparison_can_answer_the_known_metric_when_detail_is_unsupported(self):
        self.project['analysis_requirements']['time_range'] = None
        self.project['models']['m1_object_model.yaml']['entities'][1]['attributes'][-1]['type'] = 'DateTime'
        plan = self.fixture.plan(
            "SELECT substr(issued_at,1,7) AS month, sum(amount) AS total FROM invoices "
            "GROUP BY substr(issued_at,1,7) ORDER BY month")

        class MonthComparisonLLM(fixtures.StubLLM):
            def json(self, system, user, *, purpose, max_tokens):
                if purpose == 'analysis_sql':
                    self.calls.append(purpose)
                    return {'error': '缺少货品维度'}
                if purpose == 'analysis_sql.comparison':
                    self.calls.append(purpose)
                    return self.plan
                return super().json(system, user, purpose=purpose, max_tokens=max_tokens)

        llm = MonthComparisonLLM(plan)
        result = analysis.run_analysis(self.project, '2026-12 环比结算额下降是哪个货品影响的', llm)
        self.assertEqual(llm.calls, ['analysis_sql', 'analysis_sql.comparison', 'analysis_report'])
        self.assertEqual([row['total'] for row in result['rows']], [30.0, 5.0])

    def test_confirmed_snapshot_does_not_guess_unmapped_date_type(self):
        self.project['analysis_requirements']['time_range'] = None
        stub = fixtures.StubLLM()
        with self.assertRaisesRegex(analysis.AnalysisError, '明确这次分析使用的日期字段'):
            analysis.run_analysis(self.project, '统计2026-11结算额', stub)
        self.assertEqual(stub.calls, [])

    def test_iso_local_timestamps_respect_confirmed_calendar_day_boundaries(self):
        with sqlite3.connect(self.fixture.path) as conn:
            conn.executemany('UPDATE invoices SET issued_at=? WHERE id=?', (
                ('2026-11-01T00:00:00', 1),
                ('2026-11-30T23:59:59', 2),
                ('2026-12-01T00:00:00', 3),
            ))
        self.refresh_source()
        result, _ = self.run_query()
        self.assertEqual(result['rows'], [{'total': 30.0}])
        self.assertEqual(result['provenance']['time_scope']['storage_formats'], ['iso_local_datetime'])

    def test_timestamp_offsets_and_invalid_times_are_rejected_before_model_call(self):
        for bad_value in ('2026-11-15T10:30:00+01:00', '2026-11-15T25:00:00'):
            with self.subTest(value=bad_value), sqlite3.connect(self.fixture.path) as conn:
                conn.execute('UPDATE invoices SET issued_at=? WHERE id=1', (bad_value,))
                conn.commit()
                self.refresh_source()
                stub = fixtures.StubLLM()
                with self.assertRaisesRegex(analysis.AnalysisError, '日期'):
                    analysis.run_analysis(self.project, '统计2026年11月结算总额', stub)
                self.assertEqual(stub.calls, [])

    def test_quoted_table_and_fk_join_keep_server_scope(self):
        result, _ = self.run_query('SELECT c.name,SUM(i.amount) AS total FROM "invoices" i '
                                   'JOIN customers c ON i.customer_id=c.id GROUP BY c.id,c.name ORDER BY c.id')
        self.assertEqual(result['rows'], [{'name': '客户甲', 'total': 30.0}])

    def test_inclusive_end_is_normalized_and_explicit_iso_question_matches(self):
        scope = self.project['analysis_requirements']['time_range']
        scope.pop('end_exclusive')
        scope.update(end='2026-11-30', end_inclusive=True)
        result, _ = self.run_query(question='统计2026-11-01至2026-11-30结算总额')
        self.assertEqual(result['rows'], [{'total': 30.0}])
        self.assertEqual(result['parameters']['__paas_scope_end'], '2026-12-01')

    def test_ambiguous_invalid_or_unmapped_date_scope_is_refused(self):
        original = copy.deepcopy(self.project['analysis_requirements']['time_range'])
        for change in ({'end_exclusive': False}, {'start': '2026-02-30'}, {'format': 'unix_seconds'},
                       {'column': 'unknown'}, {'table': 'customers', 'column': 'private_note'}):
            with self.subTest(change=change):
                self.project['analysis_requirements']['time_range'] = original | change
                stub = fixtures.StubLLM()
                with self.assertRaises(analysis.AnalysisError):
                    analysis.run_analysis(self.project, '结算总额', stub)
                self.assertEqual(stub.calls, [])

    def test_non_iso_source_values_refuse_instead_of_lexical_guessing(self):
        with sqlite3.connect(self.fixture.path) as conn:
            conn.execute("UPDATE invoices SET issued_at='11/15/2026' WHERE id=1")
        self.refresh_source()
        stub = fixtures.StubLLM()
        with self.assertRaisesRegex(analysis.AnalysisError, 'ISO|日期'):
            analysis.run_analysis(self.project, '统计2026年11月结算总额', stub)
        self.assertEqual(stub.calls, [])

    def test_null_date_coverage_is_recorded(self):
        with sqlite3.connect(self.fixture.path) as conn:
            conn.execute('UPDATE invoices SET issued_at=NULL WHERE id=2')
        self.refresh_source()
        result, _ = self.run_query()
        self.assertEqual(result['rows'], [{'total': 10.0}])
        self.assertEqual(result['provenance']['time_scope']['excluded_null_dates'], 1)

    def test_reserved_scope_parameters_cannot_be_overridden(self):
        with self.assertRaisesRegex(analysis.AnalysisError, '保留|参数'):
            self.run_query(parameters={'__paas_scope_start': '1900-01-01'})

    def test_query_must_read_the_scoped_table(self):
        with self.assertRaisesRegex(analysis.AnalysisError, '日期|范围'):
            self.run_query('SELECT id,name FROM customers')

    def test_model_limit_cannot_hide_more_than_cap(self):
        with sqlite3.connect(self.fixture.path) as conn:
            conn.executemany('INSERT INTO invoices VALUES(?,?,?,?)',
                             [(i, 1, 1, '2026-11-15') for i in range(4, 1004)])
        self.refresh_source()
        with self.assertRaisesRegex(analysis.AnalysisError, '500行'):
            self.run_query('SELECT id,amount FROM invoices ORDER BY id LIMIT 500')

    def test_limit_or_offset_cannot_hide_a_smaller_incomplete_result(self):
        for tail in ('LIMIT 1', 'LIMIT 500 OFFSET 1', 'LIMIT :cap'):
            with self.subTest(tail=tail):
                with self.assertRaisesRegex(analysis.AnalysisError, 'LIMIT|OFFSET|完整'):
                    self.run_query('SELECT id,amount FROM invoices ORDER BY id ' + tail,
                                   parameters={'cap': 1} if ':cap' in tail else {})

    def test_nontruncating_limit_is_accepted_and_marks_completeness(self):
        result, _ = self.run_query('SELECT id,amount FROM invoices ORDER BY id LIMIT 500')
        self.assertEqual(len(result['rows']), 2)
        self.assertFalse(result['provenance']['truncated'])
        self.assertTrue(result['provenance']['complete_result_checked'])


if __name__ == '__main__':
    unittest.main()
