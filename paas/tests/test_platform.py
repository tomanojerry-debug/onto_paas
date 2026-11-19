"""Persisted gates, cancellation, restart and isolated-source HTTP tests."""
import hashlib
import io
import json
import sqlite3
import tempfile
import threading
import time
import types
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock

from paas.app_runtime import AccessDenied
from paas.jobs import Jobs
from paas.server import _inputs_digest, create_app
from paas.store import Conflict, Store, digest
from paas.tests.test_analysis import models_for_test
from paas import analysis as analysis_service
from paas import prototype
from paas.tests.test_business import fixture_models as business_models_for_test


def fixture_design(summary='虚构测试页面'):
    return {'summary': summary, 'pages': [{'id': 'equipment', 'name': '设备借用', 'purpose': '核对设备借用界面',
        'layout': 'list', 'fields': [], 'columns': [{'id': 'deviceName', 'label': '设备名称'}], 'actions': []}]}


class PrototypeStubLLM:
    def __init__(self, *args, **kwargs):
        self.calls = []

    def json(self, system, user, **kwargs):
        self.calls.append({'purpose': kwargs.get('purpose'), 'fixture': True})
        feedback = json.loads(user).get('feedback', '')
        return fixture_design('虚构修订：' + feedback if feedback else '虚构测试页面')


def complete_business_fixture(row, with_models=True):
    """Explicit test-only predecessor evidence; never counted as human/model trials."""
    row['stage'] = 8
    row['stage_summaries'] = {str(i): f'虚构阶段{i}的已确认摘要' for i in range(8)}
    row['confirmations'] = [{'scope': 'stage', 'stage': i, 'summary': row['stage_summaries'][str(i)],
                            'decision': '虚构测试确认', 'revision': row['revision']} for i in range(8)]
    from paas.tests.requirements_fixture import evidence_prd, loan_requirement_models
    from paas.tests.ui_traceability_fixture import loan_design, loan_models

    row['requirements'] = evidence_prd()
    row['requirements_inputs_sha256'] = _inputs_digest(row)
    row['requirements_confirmation'] = {'scope': 'requirements', 'sha256': digest(row['requirements']),
                                         'decision': '虚构测试确认', 'revision': row['revision']}
    model = PrototypeStubLLM()
    model.json = Mock(return_value=loan_design())
    row['prototype'] = prototype.generate(row, model)
    row['prototype_confirmation'] = {'scope': 'prototype', 'sha256': row['prototype']['sha256'],
        'requirements_sha256': digest(row['requirements']), 'decision': '虚构测试确认', 'revision': row['revision']}
    row['confirmations'].extend([row['requirements_confirmation'], row['prototype_confirmation']])
    if with_models:
        row['models'] = loan_models(loan_requirement_models(business_models_for_test()))
        row['models_requirements_sha256'] = digest(row['requirements'])
        row['models_prototype_sha256'] = row['prototype']['sha256']
        row['models_confirmation'] = {'scope': 'models', 'sha256': digest(row['models']),
                                      'decision': '虚构测试确认', 'revision': row['revision']}
        row['confirmations'].append(row['models_confirmation'])


class NoNetworkLLM:
    def __init__(self, *args, **kwargs):
        self.calls = []


def wait_job(store, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        row = store.job_get(job_id)
        if row['status'] not in ('queued', 'running'):
            return row
        time.sleep(0.005)
    raise AssertionError('Local test job did not finish')


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='paas-platform-jobs-')
        self.store = Store(self.temp.name)
        self.project = self.store.create('测试项目', 'analysis', '测试')
        self.jobs = Jobs(self.store)
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        self.jobs.pool.shutdown(wait=True)
        self.temp.cleanup()

    def test_cancelled_work_cannot_publish_and_releases_project(self):
        started = threading.Event()
        def work(project, llm):
            started.set()
            self.release.wait(3)
            return {'should_not_publish': True}
        with patch('paas.jobs.LLM', NoNetworkLLM):
            job = self.jobs.submit(self.project['id'], '取消测试', work, lambda p, result: p.update(models=result))
            self.assertTrue(started.wait(2))
            result = self.jobs.cancel(job['job_id'])
            self.assertTrue(result['cancel_requested'])
            self.release.set()
            terminal = wait_job(self.store, job['job_id'])
        self.assertEqual(terminal['status'], 'cancelled')
        current = self.store.get(self.project['id'])
        self.assertEqual(current['models'], {})
        self.assertIsNone(current['active_job_id'])

    def test_provider_initialization_failure_is_terminal_and_unlocks_project(self):
        with patch('paas.jobs.LLM', side_effect=ValueError('invalid provider configuration')):
            job = self.jobs.submit(self.project['id'], '初始化失败', lambda p, llm: None, lambda p, result: None)
            row = wait_job(self.store, job['job_id'])
        self.assertEqual(row['status'], 'failed')
        self.assertFalse(row['model_called'])
        self.assertIsNone(self.store.get(self.project['id'])['active_job_id'])

    def test_stale_input_revision_cannot_be_submitted(self):
        self.store.mutate(self.project['id'], lambda p: p.update(description='变化'))
        with self.assertRaises(Conflict):
            self.jobs.submit(self.project['id'], '旧任务', lambda p, llm: None, lambda p, r: None,
                             expected_revision=self.project['revision'])

    def test_changed_project_while_worker_runs_rejects_result(self):
        started = threading.Event()
        def work(project, llm):
            started.set()
            self.release.wait(3)
            return 'late'
        with patch('paas.jobs.LLM', NoNetworkLLM):
            job = self.jobs.submit(self.project['id'], '迟到结果', work, lambda p, r: p.update(requirements=r))
            self.assertTrue(started.wait(2))
            self.store.mutate(self.project['id'], lambda p: p.update(description='更新版本'))
            self.release.set()
            row = wait_job(self.store, job['job_id'])
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(self.store.get(self.project['id'])['requirements'], '')

    def test_restart_marks_only_its_interrupted_job_failed(self):
        old = uuid.uuid4().hex
        another = uuid.uuid4().hex
        self.store.write(self.store.root / 'jobs' / (old + '.json'),
                         {'id': old, 'project_id': self.project['id'], 'status': 'running'})
        self.store.mutate(self.project['id'], lambda p: p.update(active_job_id=another), bump=False)
        reopened = Store(self.temp.name)
        self.assertEqual(reopened.job_get(old)['status'], 'failed')
        self.assertEqual(reopened.get(self.project['id'])['active_job_id'], another)

    def test_progress_is_filtered_read_only_and_partial_writes_are_ignored(self):
        job_id = uuid.uuid4().hex
        job_file = self.store.root / 'jobs' / (job_id + '.json')
        self.store.write(job_file, {'id': job_id, 'project_id': self.project['id'], 'status': 'running'})
        path = self.store.directory(self.project['id']) / 'jobs' / job_id / 'progress.json'
        path.parent.mkdir(parents=True)
        path.write_text('{"purpose":"generation","characters":123,"elapsed_seconds":4,"model":"fixture","raw_prompt":"secret"}')
        progress = self.store.job_get(job_id)['progress']
        self.assertEqual(progress['characters'], 123)
        self.assertNotIn('raw_prompt', progress)
        self.store.job_update(job_id, cancel_requested=True)
        self.assertNotIn('progress', job_file.read_text())
        path.write_text('{"characters":')
        self.assertNotIn('progress', self.store.job_get(job_id))


class PlatformHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='paas-platform-http-')
        self.app = create_app(self.temp.name)
        self.app.config.update(TESTING=True)
        self.store = self.app.extensions['paas_store']
        self.jobs = self.app.extensions['paas_jobs']
        self.client = self.app.test_client()
        self.base_url = 'http://127.0.0.1'

    def tearDown(self):
        self.jobs.pool.shutdown(wait=True)
        self.temp.cleanup()

    def post(self, path, payload):
        return self.client.post(path, json=payload, base_url=self.base_url)

    def seed_candidate(self):
        project = self.store.create('虚构发布测试', 'business', '设备借用测试')
        output = self.store.directory(project['id']) / 'builds/fixture'
        app_dir = output / 'code-app'
        (app_dir / 'data').mkdir(parents=True)
        (app_dir / 'web').mkdir()
        (app_dir / 'models').mkdir()
        (app_dir / 'migrations').mkdir()
        (app_dir / 'runtime.json').write_text(json.dumps({'model_hash': 'fixture-compiled-hash'}))
        (app_dir / 'runtime.py').write_text('# immutable fixture runtime\n')
        (app_dir / 'server.py').write_text('# fixture server\n')
        (app_dir / 'web/index.html').write_text('<h1>Published fixture</h1>')
        (app_dir / 'accounts.json').write_text('[{"password_hash":"private-fixture-hash"}]')
        database = app_dir / 'data/application.db'
        with sqlite3.connect(database) as conn:
            conn.executescript("CREATE TABLE t_records(id TEXT PRIMARY KEY,amount INTEGER);INSERT INTO t_records VALUES('first',35);")
        def seed(row):
            complete_business_fixture(row)
            row['build'] = {'status': 'built', 'model_hash': 'fixture-compiled-hash',
                'models_sha256': digest(row['models']), 'requirements_sha256': digest(row['requirements']),
                'prototype_sha256': row['prototype']['sha256'], 'source_revision': row['revision'],
                'output_dir': str(output), 'app_dir': str(app_dir), 'database_path': str(database),
                'url': '/app/' + row['id'] + '/', 'tables': ['t_records'],
                'accounts': [{'username': 'fixture', 'password': 'private-fixture-password'}]}
        return self.store.mutate(project['id'], seed)

    def accept_fixture(self, row, *, stub_runtime=True):
        prefix = '/api/v1/projects/' + row['id']
        plan = self.client.get(prefix, base_url=self.base_url).json['build']['acceptance_plan']
        payload = {'revision': row['revision'], 'candidate_id': plan['id'],
                   'checks': {check['id']: {'passed': True, 'result': '隔离发布测试设备借用核对记录'}
                              for check in plan['checks']}}
        if not stub_runtime:
            return self.post(prefix + '/build/acceptance', payload)
        with patch('paas.acceptance.smoke', return_value={'runtime_entry': 'fixture'}):
            response = self.post(prefix + '/build/acceptance', payload)
        self.assertEqual(response.status_code, 200, response.json)
        return response

    def test_local_origin_and_host_are_enforced(self):
        response = self.client.post('/api/v1/projects', json={'name': 'X'},
                                    headers={'Origin': 'https://outside.example'}, base_url=self.base_url)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get('/api/v1/projects', base_url='http://outside.example').status_code, 403)

    def test_persistent_private_session_key_and_access_denied_status(self):
        secret = Path(self.temp.name) / '.session-secret'
        self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
        second = create_app(self.temp.name)
        try:
            self.assertEqual(self.app.secret_key, second.secret_key)
        finally:
            second.extensions['paas_jobs'].pool.shutdown(wait=True)
        @self.app.get('/permission-denied-test')
        def denied():
            raise AccessDenied('无此权限')
        self.assertEqual(self.client.get('/permission-denied-test', base_url=self.base_url).status_code, 403)

    def test_stage_requires_current_result_true_boolean_and_current_revision(self):
        project = self.store.create('设备业务', 'business', '设备借还')
        path = '/api/v1/projects/' + project['id'] + '/confirm'
        self.assertEqual(self.post(path, {'scope': 'stage', 'stage': 0, 'revision': 1}).status_code, 409)
        row = self.store.mutate(project['id'], lambda p: p.update(current_stage_result={
            'stage': 0, 'ready_for_confirmation': 'false', 'stage_summary': '未完成'}))
        self.assertEqual(self.post(path, {'scope': 'stage', 'stage': 0, 'revision': row['revision']}).status_code, 409)
        row = self.store.mutate(project['id'], lambda p: p['current_stage_result'].update(ready_for_confirmation=True))
        self.assertEqual(self.post(path, {'scope': 'stage', 'stage': 0, 'revision': row['revision'] - 1}).status_code, 409)
        accepted = self.post(path, {'scope': 'stage', 'stage': 0, 'revision': row['revision']})
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json['stage'], 1)
        self.assertIsNone(accepted.json['current_stage_result'])

    def test_reopen_invalidates_following_summaries_requirement_and_build(self):
        project = self.store.create('设备业务', 'business', '设备借还')
        self.store.mutate(project['id'], lambda p: p.update(stage=8,
            stage_summaries={str(i): str(i) for i in range(8)}, requirements='旧文档',
            requirements_confirmation={'sha256': digest('旧文档')}, models_confirmation={'sha256': 'old'},
            build={'output_dir': 'old-build'}, confirmations=[{'scope': 'stage', 'stage': i} for i in range(8)]))
        row = self.store.get(project['id'])
        response = self.client.patch('/api/v1/projects/' + project['id'], json={'reopen_stage': 3, 'revision': row['revision']}, base_url=self.base_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json['stage_summaries']), {'0', '1', '2'})
        self.assertEqual(response.json['requirements'], '')
        self.assertIsNone(response.json['requirements_confirmation'])
        self.assertIsNone(response.json['models_confirmation'])
        self.assertIsNone(response.json['build'])

    def test_project_kinds_cannot_bypass_workflow_gates(self):
        business = self.store.create('业务', 'business', '说明')
        knowledge = self.store.create('知识', 'knowledge', '说明')
        cases = [(business['id'], '/knowledge/extract'), (business['id'], '/analysis'),
                 (knowledge['id'], '/requirements'), (knowledge['id'], '/models')]
        for project_id, suffix in cases:
            with self.subTest(suffix=suffix):
                self.assertEqual(self.post('/api/v1/projects/' + project_id + suffix, {'question': 'x'}).status_code, 400)

    def test_create_analysis_project_with_upload_only_publishes_valid_source(self):
        existing = self.store.create('已有分析', 'analysis', '说明')
        database = Path(self.temp.name) / 'equipment.sqlite'
        with sqlite3.connect(database) as conn:
            conn.execute('CREATE TABLE equipment(id INTEGER PRIMARY KEY, name TEXT)')

        def create(content):
            return self.client.post('/api/v1/projects', base_url=self.base_url,
                data={'name': '新分析', 'kind': 'analysis', 'description': '设备分析',
                      'database': (io.BytesIO(content), 'equipment.sqlite')})

        failed = create(b'invalid')
        self.assertEqual(failed.status_code, 400, failed.json)
        self.assertEqual(list((self.store.root / 'projects').iterdir()), [self.store.directory(existing['id'])])

        created = create(database.read_bytes())
        self.assertEqual(created.status_code, 201, created.json)
        source = created.json['source']
        self.assertEqual(source['source_kind'], 'upload')
        self.assertEqual([table['name'] for table in source['schema']['tables']], ['equipment'])
        self.assertEqual(self.store.get(existing['id']), existing)

    def test_app_source_snapshot_excludes_all_runtime_and_identity_tables(self):
        business = self.store.create('构建业务', 'business', '说明')
        directory = self.store.directory(business['id']) / 'builds/test'
        directory.mkdir(parents=True)
        database = directory / 'application.db'
        with sqlite3.connect(database) as conn:
            conn.executescript("CREATE TABLE t_records(id TEXT PRIMARY KEY,name TEXT);"
                               "INSERT INTO t_records VALUES('1','业务记录');"
                               "CREATE TABLE runtime_chat(actor_id TEXT,content TEXT);"
                               "INSERT INTO runtime_chat VALUES('actor-secret','private-chat');"
                               "CREATE TABLE _paas_users(password_hash TEXT);"
                               "INSERT INTO _paas_users VALUES('secret-password-hash');")
        self.store.mutate(business['id'], lambda p: p.update(build={'database_path': str(database), 'tables': ['t_records']}))
        analysis = self.store.create('分析', 'analysis', '说明')
        response = self.post('/api/v1/projects/' + analysis['id'] + '/source', {'source_project_id': business['id']})
        self.assertEqual(response.status_code, 200, response.json)
        snapshot = Path(response.json['source']['path'])
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o400)
        self.assertEqual([t['name'] for t in response.json['source']['schema']['tables']], ['t_records'])
        with sqlite3.connect(snapshot) as conn:
            self.assertEqual(conn.execute('SELECT name FROM t_records').fetchall(), [('业务记录',)])
            self.assertEqual(conn.execute("SELECT name FROM sqlite_schema WHERE type='table'").fetchall(), [('t_records',)])
        self.assertNotIn(b'secret-password-hash', snapshot.read_bytes())
        self.assertNotIn(b'private-chat', snapshot.read_bytes())

    def test_analysis_message_is_text_and_result_keeps_question(self):
        project = self.store.create('分析', 'analysis', '结算分析')
        def seed(row):
            row['requirements'] = '已确认分析口径'
            row['requirements_inputs_sha256'] = _inputs_digest(row)
            row['requirements_confirmation'] = {'sha256': digest(row['requirements'])}
            row['models'] = models_for_test()
            row['models_requirements_sha256'] = digest(row['requirements'])
            row['models_confirmation'] = {'sha256': digest(row['models'])}
        self.store.mutate(project['id'], seed)
        with patch('paas.jobs.LLM', NoNetworkLLM), patch('paas.server.analysis.run_analysis', return_value={'report': {'summary': '结果35'}, 'rows': []}):
            response = self.post('/api/v1/projects/' + project['id'] + '/analysis', {'question': '总额是多少'})
            self.assertEqual(response.status_code, 202, response.json)
            self.assertEqual(wait_job(self.store, response.json['job_id'])['status'], 'succeeded')
        current = self.store.get(project['id'])
        self.assertIsInstance(current['messages'][-1]['content'], str)
        self.assertEqual(current['runs'][-1]['question'], '总额是多少')

    def test_source_export_never_contains_accounts_data_secrets_or_runtime_logs(self):
        project = self.store.create('导出业务', 'business', '说明')
        output = self.store.directory(project['id']) / 'builds/source-export'
        code_app = output / 'code-app'
        allowed = {'runtime.py': 'print("runtime")', 'server.py': 'print("server")',
                   'runtime.json': '{"name":"example"}', 'web/index.html': '<h1>App</h1>',
                   'models/m1-object-model.yaml': 'model_type: OBJECT',
                   'migrations/001_initial.sql': 'CREATE TABLE t_records(id TEXT);',
                   'upstream-techbase/backend/app.py': 'print("upstream")'}
        for name, content in allowed.items():
            path = code_app / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        private = ['accounts.json', '.env', '.session-secret', 'data/application.db',
                   'data/ai-runs/report.json', 'logs/report.txt', 'notes.json',
                   'upstream-techbase/backend/data/private.json', 'upstream-techbase/.env']
        for name in private:
            path = code_app / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('do-not-export-sensitive-value')
        (output / 'initial-credentials.json').write_text('do-not-export-sensitive-value')
        self.store.mutate(project['id'], lambda p: p.update(build={'output_dir': str(output)}))
        response = self.client.get('/api/v1/projects/' + project['id'] + '/download', base_url=self.base_url)
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            names = set(archive.namelist())
            self.assertTrue({'application/code-app/' + name for name in allowed}.issubset(names))
            self.assertFalse(any('accounts.json' in name or 'data/' in name or '.env' in name for name in names))
            self.assertNotIn(b'do-not-export-sensitive-value', b''.join(archive.read(name) for name in names))

    def test_unknown_model_filename_cannot_write_artifacts_or_enter_zip(self):
        project = self.store.create('文件名边界', 'business', '说明')
        def seed(row):
            complete_business_fixture(row, with_models=False)
        self.store.mutate(project['id'], seed)
        with patch('paas.jobs.LLM', NoNetworkLLM), patch('paas.server.business.generate_models', return_value={'../../escape.txt': {'danger': True}}):
            response = self.post('/api/v1/projects/' + project['id'] + '/models', {})
            self.assertEqual(response.status_code, 202, response.json)
            job = wait_job(self.store, response.json['job_id'])
            self.assertEqual(job['status'], 'failed')
            self.assertIn('文件名', job['error'])
        self.assertFalse(list(Path(self.temp.name).rglob('escape.txt')))
        self.store.mutate(project['id'], lambda row: row.update(models={'../../escape.txt': {}}))
        response = self.client.get('/api/v1/projects/' + project['id'] + '/download', base_url=self.base_url)
        self.assertEqual(response.status_code, 400)

    def test_repair_endpoint_uses_feedback_and_requires_fresh_model_review(self):
        project = self.store.create('修复分析', 'analysis', '结算分析')
        source = self.store.directory(project['id']) / 'source.sqlite'
        with sqlite3.connect(source) as conn:
            conn.executescript('CREATE TABLE customers(id INTEGER PRIMARY KEY,name TEXT);'
                               'CREATE TABLE invoices(id INTEGER PRIMARY KEY,customer_id INTEGER REFERENCES customers(id),amount REAL);')
        schema = analysis_service.inspect_database(source)
        def seed(row):
            row.update(source={'schema': schema, 'path': str(source), 'sha256': schema['sha256']},
                       requirements='已确认口径', analysis_requirements={'metrics': []}, models=models_for_test(),
                       build={'output_dir': 'old-build'}, runs=[{'id': 'past'}])
            row['requirements_inputs_sha256'] = _inputs_digest(row)
            row['requirements_confirmation'] = {'sha256': digest(row['requirements'])}
            row['models_requirements_sha256'] = digest(row['requirements'])
            row['models_confirmation'] = {'sha256': digest(row['models'])}
        self.store.mutate(project['id'], seed)
        fixed = models_for_test()
        fixed['m_metric_model.yaml']['metrics'][0]['name'] = '修订后的结算额'
        module = types.ModuleType('paas.model_repair')
        module.analysis_repair = Mock(return_value=fixed)
        module.business_repair = Mock(side_effect=AssertionError('wrong repair kind'))
        with patch.dict('sys.modules', {'paas.model_repair': module}), patch('paas.jobs.LLM', NoNetworkLLM):
            response = self.post('/api/v1/projects/' + project['id'] + '/models', {'repair': True, 'feedback': '修复指标命名'})
            self.assertEqual(response.status_code, 202, response.json)
            self.assertEqual(wait_job(self.store, response.json['job_id'])['status'], 'succeeded')
        self.assertEqual(module.analysis_repair.call_args.args[2], '修复指标命名')
        current = self.store.get(project['id'])
        self.assertEqual(current['requirements'], '已确认口径')
        self.assertEqual(current['models']['m7-report-model.yaml']['metrics'][0]['name'], '修订后的结算额')
        self.assertIsNone(current['models_confirmation'])
        self.assertIsNone(current['build'])
        self.assertTrue(current['runs'][0]['stale'])
        self.assertEqual(self.post('/api/v1/projects/' + project['id'] + '/analysis', {'question': '总额'}).status_code, 409)

    def test_reopen_cannot_skip_unconfirmed_stages(self):
        project = self.store.create('不准跳级', 'business', '设备')
        response = self.client.patch('/api/v1/projects/' + project['id'],
            json={'reopen_stage': 7, 'revision': project['revision']}, base_url=self.base_url)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.store.get(project['id'])['stage'], 0)
        self.store.mutate(project['id'], lambda row: row.update(stage=8, stage_summaries={}, confirmations=[]))
        response = self.post('/api/v1/projects/' + project['id'] + '/requirements', {})
        self.assertEqual(response.status_code, 409)
        self.assertIn('完整人工确认', response.json['error'])

    def test_prototype_is_a_separate_human_gate_and_html_is_sandboxed(self):
        project = self.store.create('原型门禁', 'business', '设备')
        def seed(row):
            complete_business_fixture(row, with_models=False)
            row.pop('prototype')
            row.pop('prototype_confirmation')
        row = self.store.mutate(project['id'], seed)
        prefix = '/api/v1/projects/' + project['id']
        self.assertEqual(self.post(prefix + '/models', {}).status_code, 409)
        with patch('paas.jobs.LLM', PrototypeStubLLM):
            response = self.post(prefix + '/prototype', {'revision': row['revision']})
            self.assertEqual(response.status_code, 202, response.json)
            self.assertEqual(wait_job(self.store, response.json['job_id'])['status'], 'succeeded')
        current = self.store.get(project['id'])
        self.assertIsNone(current['prototype_confirmation'])
        self.assertEqual(self.post(prefix + '/models', {}).status_code, 409)
        response = self.client.get(prefix + '/prototype/html', base_url=self.base_url)
        self.assertEqual(response.status_code, 200)
        policy = response.headers['Content-Security-Policy']
        self.assertIn('sandbox allow-scripts', policy)
        self.assertIn("script-src 'sha256-", policy)
        self.assertNotIn('allow-same-origin', policy)
        self.assertEqual(self.post(prefix + '/confirm', {'scope': 'prototype', 'revision': row['revision']}).status_code, 409)
        self.assertEqual(self.post(prefix + '/confirm', {'scope': 'prototype', 'revision': current['revision']}).status_code, 200)

    def test_prototype_revision_requires_regeneration_or_repair_before_model_confirmation(self):
        from paas.tests.ui_traceability_fixture import loan_design
        current = self.seed_candidate()
        prefix = '/api/v1/projects/' + current['id']
        original_sha = current['prototype']['sha256']
        design = loan_design()
        design['summary'] = '虚构修订：修订页面说明'
        model = PrototypeStubLLM()
        model.json = Mock(return_value=design)
        with patch('paas.jobs.LLM', return_value=model):
            response = self.post(prefix + '/prototype', {'revision': current['revision'], 'feedback': '修订页面说明'})
            self.assertEqual(response.status_code, 202, response.json)
            self.assertEqual(wait_job(self.store, response.json['job_id'])['status'], 'succeeded')
        changed = self.store.get(current['id'])
        self.assertEqual(changed['prototype_history'][0]['sha256'], original_sha)
        self.assertNotEqual(changed['prototype']['sha256'], original_sha)
        self.assertIsNone(changed['models_prototype_sha256'])
        self.assertIsNone(changed['models_confirmation'])
        self.assertIsNone(changed['build'])
        confirmed = self.post(prefix + '/confirm', {'scope': 'prototype', 'revision': changed['revision']})
        self.assertEqual(confirmed.status_code, 200, confirmed.json)
        self.assertEqual(self.post(prefix + '/confirm', {'scope': 'models', 'revision': changed['revision']}).status_code, 409)
        module = types.ModuleType('paas.model_repair')
        module.business_repair = Mock(return_value=current['models'])
        module.analysis_repair = Mock(side_effect=AssertionError('wrong kind'))
        with patch.dict('sys.modules', {'paas.model_repair': module}), patch('paas.jobs.LLM', NoNetworkLLM):
            response = self.post(prefix + '/models', {'repair': True, 'feedback': '按修订原型校核'})
            self.assertEqual(response.status_code, 202, response.json)
            self.assertEqual(wait_job(self.store, response.json['job_id'])['status'], 'succeeded')
        repaired = self.store.get(current['id'])
        self.assertEqual(repaired['models_prototype_sha256'], repaired['prototype']['sha256'])
        self.assertIsNone(repaired['models_confirmation'])
        self.assertEqual(self.post(prefix + '/confirm', {'scope': 'models', 'revision': repaired['revision']}).status_code, 200)

    def test_unchanged_model_save_keeps_revision_confirmation_and_candidate_build(self):
        row = self.seed_candidate()
        filename = 'm1-object-model.yaml'
        response = self.client.put('/api/v1/projects/' + row['id'] + '/models/' + filename,
            json={'revision': row['revision'], 'text': json.dumps(row['models'][filename])}, base_url=self.base_url)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['revision'], row['revision'])
        self.assertEqual(response.json['models_confirmation'], row['models_confirmation'])
        self.assertEqual(response.json['build'], row['build'])

    def test_release_freezes_baseline_and_database_and_survives_draft_changes(self):
        row = self.seed_candidate()
        prefix = '/api/v1/projects/' + row['id']
        self.assertEqual(self.post(prefix + '/releases', {'revision': row['revision'], 'version': '1.0.0'}).status_code, 400)
        denied = self.post(prefix + '/releases', {'revision': row['revision'], 'version': '1.0.0', 'acknowledged': True})
        self.assertEqual(denied.status_code, 409, denied.json)
        self.assertIn('验收', denied.json['error'])
        self.assertNotEqual(self.accept_fixture(row, stub_runtime=False).status_code, 200)
        accepted = self.accept_fixture(row).json['acceptance']
        response = self.post(prefix + '/releases', {'revision': row['revision'], 'version': '1.0.0', 'acknowledged': True})
        self.assertEqual(response.status_code, 201, response.json)
        release = response.json['release']
        current = response.json['project']
        self.assertEqual(current['build']['status'], 'built')
        self.assertEqual(release['status'], 'published')
        self.assertEqual(current['published_release_id'], release['id'])
        self.assertNotEqual(release['build']['output_dir'], row['build']['output_dir'])
        self.assertNotIn('accounts', release['build'])
        self.assertEqual(release['build']['acceptance'], accepted)
        self.assertEqual(release['code_sha256'], accepted['code_sha256'])
        duplicate = self.post(prefix + '/releases', {'revision': current['revision'], 'version': '1.0.0', 'acknowledged': True})
        self.assertEqual(duplicate.status_code, 409)
        original_html = Path(row['build']['app_dir']) / 'web/index.html'
        original_html.write_text('<h1>Changed candidate</h1>')
        with sqlite3.connect(row['build']['database_path']) as conn:
            conn.execute("INSERT INTO t_records VALUES('second',100)")
        with sqlite3.connect(release['build']['database_path']) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM t_records').fetchone()[0], 1)
        edited = self.client.patch(prefix, json={'revision': current['revision'], 'description': '新的业务草案', 'reopen_stage': 0}, base_url=self.base_url)
        self.assertEqual(edited.status_code, 200)
        self.assertIsNone(edited.json['build'])
        self.assertIsNone(edited.json['prototype_confirmation'])
        listed = self.client.get(prefix + '/releases', base_url=self.base_url)
        self.assertEqual(listed.json['releases'][0]['id'], release['id'])
        exported = self.client.get(prefix + '/releases/' + release['id'] + '/download', base_url=self.base_url)
        self.assertEqual(exported.status_code, 200, exported.json if exported.is_json else '')
        with zipfile.ZipFile(io.BytesIO(exported.data)) as archive:
            self.assertEqual(archive.read('requirements.md').decode(), row['requirements'])
            self.assertEqual(archive.read('application/code-app/web/index.html'), b'<h1>Published fixture</h1>')
            self.assertEqual(archive.read('prototype.html').decode(), row['prototype']['html'])
            self.assertIn('approved-baseline.json', archive.namelist())
            self.assertIn('release-acceptance.json', archive.namelist())
            self.assertFalse(any('accounts.json' in name or '/data/' in name for name in archive.namelist()))
        (Path(release['build']['app_dir']) / 'web/index.html').unlink()
        missing = self.client.get(prefix + '/releases/' + release['id'] + '/download', base_url=self.base_url)
        self.assertEqual(missing.status_code, 409, missing.json)

    def test_release_rejects_stale_build_and_stale_revision(self):
        row = self.seed_candidate()
        prefix = '/api/v1/projects/' + row['id'] + '/releases'
        self.assertEqual(self.post(prefix, {'revision': row['revision'] - 1, 'version': '1.0.0', 'acknowledged': True}).status_code, 409)
        row = self.store.mutate(row['id'], lambda p: p['build'].update(prototype_sha256='stale'))
        response = self.post(prefix, {'revision': row['revision'], 'version': '1.0.0', 'acknowledged': True})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.store.get(row['id']).get('releases'))

    def test_release_rejects_code_changed_after_candidate_acceptance(self):
        row = self.seed_candidate()
        self.accept_fixture(row)
        (Path(row['build']['app_dir']) / 'web/index.html').write_text('<h1>Changed reviewed code</h1>')
        response = self.post('/api/v1/projects/' + row['id'] + '/releases', {
            'revision': row['revision'], 'version': '1.0.0', 'acknowledged': True,
        })
        self.assertEqual(response.status_code, 409, response.json)
        self.assertIn('验收', response.json['error'])

    def test_portable_seven_models_build_without_a_requirements_document(self):
        from paas.ui_contract import bind_portable_design
        source = self.seed_candidate()
        models = source['models']
        bind_portable_design(models, source['prototype'])
        models.setdefault('manifest.json', {})['model_files'] = list(business_models_for_test())
        files = {filename: json.dumps(value, ensure_ascii=False) for filename, value in models.items()}
        target = self.store.create('从七模型直接构建', 'business', '')
        path = '/api/v1/projects/' + target['id']
        imported = self.post(path + '/models/import', {'revision': target['revision'], 'files': files})
        self.assertEqual(imported.status_code, 201, imported.json)
        row = imported.json
        self.assertFalse(row['requirements'])
        self.assertIsNone(row['prototype_confirmation'])
        self.assertEqual(self.post(path + '/build', {}).status_code, 409)
        confirmed = self.post(path + '/confirm', {'scope': 'prototype', 'revision': row['revision'],
                                                  'prototype_id': row['prototype']['id']})
        self.assertEqual(confirmed.status_code, 200, confirmed.json)
        confirmed = self.post(path + '/confirm', {'scope': 'models', 'revision': confirmed.json['revision']})
        self.assertEqual(confirmed.status_code, 200, confirmed.json)
        build = self.post(path + '/build', {})
        self.assertEqual(build.status_code, 202, build.json)
        completed = wait_job(self.store, build.json['job_id'])
        self.assertEqual(completed['status'], 'succeeded', completed.get('error'))
        self.assertTrue(completed['result']['ui_traceability'])

    def test_cli_claims_workspace_before_store_factory_and_releases_on_exit(self):
        from paas import server
        from paas.workspace_lock import WorkspaceLease
        fake_app = Mock()
        def factory(root):
            with self.assertRaisesRegex(RuntimeError, '已有运行中的服务'):
                with WorkspaceLease(root):
                    pass
            return fake_app
        with patch('sys.argv', ['paas.server', '--data-dir', self.temp.name, '--port', '4320']), patch('paas.server.create_app', side_effect=factory):
            server.main()
        fake_app.run.assert_called_once_with(host='127.0.0.1', port=4320, threaded=True, debug=False)
        with WorkspaceLease(self.temp.name):
            pass

    def test_published_release_is_default_analysis_source_after_draft_build_is_cleared(self):
        row = self.seed_candidate()
        self.accept_fixture(row)
        source_prefix = '/api/v1/projects/' + row['id']
        publication = self.post(source_prefix + '/releases', {
            'version': '1.0.0', 'revision': row['revision'], 'acknowledged': True})
        self.assertEqual(publication.status_code, 201, publication.json)
        release = publication.json['release']
        with sqlite3.connect(row['build']['database_path']) as conn:
            conn.execute('UPDATE t_records SET amount=999')
        target = self.store.create('来源选择测试', 'analysis', '比较正式版本与候选数据')
        target_prefix = '/api/v1/projects/' + target['id'] + '/source'
        def connect(**selection):
            response = self.post(target_prefix, {'source_project_id': row['id'], **selection})
            self.assertEqual(response.status_code, 200, response.json)
            with sqlite3.connect(response.json['source']['path']) as conn:
                amount = conn.execute('SELECT amount FROM t_records').fetchone()[0]
            return response.json['source'], amount
        selected, amount = connect()
        self.assertEqual(amount, 35)
        self.assertEqual(selected['source_kind'], 'release')
        self.assertEqual(selected['release_id'], release['id'])
        self.assertEqual(selected['version'], '1.0.0')
        self.assertEqual(selected['source_project_id'], row['id'])
        self.assertIn('已发布版本', selected['name'])
        candidate, amount = connect(source_kind='candidate')
        self.assertEqual(amount, 999)
        self.assertEqual(candidate['source_kind'], 'candidate')
        self.assertIsNone(candidate['release_id'])
        selected, amount = connect(release_id=release['id'])
        self.assertEqual(amount, 35)
        creation = {'name': '发布快照分析', 'kind': 'analysis',
                    'source': {'source_project_id': row['id'], 'release_id': release['id']}}
        created = self.post('/api/v1/projects', creation)
        self.assertEqual(created.status_code, 201, created.json)
        self.assertEqual(created.json['source']['release_id'], release['id'])
        with sqlite3.connect(created.json['source']['path']) as conn:
            self.assertEqual(conn.execute('SELECT amount FROM t_records').fetchone()[0], 35)
        before = self.store.list()
        creation['source']['release_id'] = '0' * 32
        self.assertEqual(self.post('/api/v1/projects', creation).status_code, 404)
        self.assertEqual(self.store.list(), before)
        updated = self.client.patch(source_prefix, json={'description': '新草案', 'reopen_stage': 0,
            'revision': publication.json['project']['revision']}, base_url=self.base_url)
        self.assertEqual(updated.status_code, 200)
        self.assertIsNone(updated.json['build'])
        selected, amount = connect()
        self.assertEqual(amount, 35)
        self.assertEqual(selected['release_id'], release['id'])
        self.assertEqual(self.post(target_prefix, {'source_project_id': row['id'], 'source_kind': 'candidate'}).status_code, 400)
        self.assertEqual(self.post(target_prefix, {'source_project_id': row['id'], 'release_id': '0' * 32}).status_code, 404)

    def test_history_prototype_uses_safe_renderer_without_confirming_current_artifact(self):
        row = self.seed_candidate()
        prefix = '/api/v1/projects/' + row['id']
        previous = row['prototype']
        with patch('paas.jobs.LLM', PrototypeStubLLM):
            response = self.post(prefix + '/prototype', {'feedback': '修订页面', 'revision': row['revision']})
            self.assertEqual(response.status_code, 202, response.json)
            self.assertEqual(wait_job(self.store, response.json['job_id'])['status'], 'succeeded')
        current = self.store.mutate(row['id'], lambda p: p['prototype_history'][0].update(html='<script>evil-history-marker</script>'))
        response = self.client.get(prefix + '/prototype/html?prototype_id=' + previous['id'], base_url=self.base_url)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.data.decode(), prototype.render({key: previous[key] for key in ('summary', 'pages')}))
        self.assertNotIn(b'evil-history-marker', response.data)
        self.assertIn('sandbox allow-scripts', response.headers['Content-Security-Policy'])
        self.assertIsNone(self.store.get(row['id'])['prototype_confirmation'])
        rejected = self.post(prefix + '/confirm', {'scope': 'prototype', 'prototype_id': previous['id'], 'revision': current['revision']})
        self.assertEqual(rejected.status_code, 409)
        self.store.mutate(row['id'], lambda p: p['prototype_history'][0].update(project_id='0' * 32))
        self.assertEqual(self.client.get(prefix + '/prototype/html?prototype_id=' + previous['id'], base_url=self.base_url).status_code, 400)


if __name__ == '__main__':
    unittest.main()
