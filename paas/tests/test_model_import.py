"""Exercise the legacy import and unified model lifecycle through local HTTP."""
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import yaml

from paas import ontology
from paas.server import _inputs_digest, _models_digest, create_app
from paas.store import digest
from paas.tests import test_analysis as fixtures
from paas.tests.test_platform import wait_job


class AnalysisModelImportHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='paas-analysis-model-import-')
        self.addCleanup(self.temp.cleanup)
        self.fixture = fixtures.AnalysisTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.app = create_app(self.temp.name)
        self.addCleanup(self.app.extensions['paas_jobs'].pool.shutdown, wait=True)
        self.store = self.app.extensions['paas_store']
        self.client = self.app.test_client()
        created = self.store.create('授权测试账单', 'analysis', '只统计结算快照')
        self.pid = created['id']
        self.route = '/api/v1/projects/' + self.pid
        source = self.client.post(self.route + '/source', data={
            'database': (io.BytesIO(self.fixture.path.read_bytes()), 'fixture.sqlite'),
        })
        self.assertEqual(200, source.status_code, source.json)
        self.legacy = fixtures.models_for_test()

    def get(self):
        return self.store.get(self.pid)

    def import_request(self, *, files=None, revision=None):
        files = files if files is not None else self.encode(self.legacy)
        revision = revision if revision is not None else self.get()['revision']
        return self.client.post(self.route + '/models/import', json={
            'files': files, 'revision': revision,
        })

    @staticmethod
    def encode(models):
        return {name: yaml.safe_dump(value, allow_unicode=True) for name, value in models.items()}

    def approve_requirements(self):
        def stage(row):
            row['analysis_requirements'] = self.fixture.project['analysis_requirements']
            row['requirements'] = json.dumps(row['analysis_requirements'], ensure_ascii=False)
            row['requirements_inputs_sha256'] = _inputs_digest(row)
        self.store.mutate(self.pid, stage)
        response = self.confirm('requirements')
        self.assertEqual(200, response.status_code, response.json)
        return response.json

    def confirm(self, scope):
        return self.client.post(self.route + '/confirm', json={
            'scope': scope, 'revision': self.get()['revision'], 'decision': '测试夹具确认',
        })

    def test_import_keeps_original_artifacts_and_diagnostics_but_blocks_execution(self):
        self.approve_requirements()
        self.legacy['m4_scenario_model.yaml']['scenarios'][0]['keyMetrics'].append('ABSENT')
        raw = self.encode(self.legacy)
        response = self.import_request(files=raw)
        self.assertEqual(200, response.status_code, response.json)
        draft = response.json
        self.assertEqual(ontology.STANDARD, draft['model_standard'])
        self.assertEqual(self.legacy, ontology.analysis_projection(draft['models'], draft['source_bindings']))
        self.assertIsNone(draft['models_confirmation'])
        self.assertIn('ABSENT', draft['model_diagnostics'][0])
        provenance = draft['model_import']
        self.assertEqual(draft['source']['sha256'], provenance['source_sha256'])
        self.assertEqual(draft['model_diagnostics'], provenance['diagnostics_at_import'])
        for name, origin in provenance['files'].items():
            with self.client.get(self.route + '/artifacts/' + origin['artifact']) as material:
                self.assertEqual(200, material.status_code)
                data = material.get_data()
            self.assertEqual(raw[name].encode(), data)
            self.assertEqual(hashlib.sha256(data).hexdigest(), origin['sha256'])

        self.assertEqual(400, self.confirm('models').status_code)
        query = self.client.post(self.route + '/analysis', json={'question': '总额'})
        self.assertEqual(409, query.status_code)
        self.assertEqual([], list((self.store.root / 'jobs').iterdir()))

    def test_malformed_and_stale_import_leave_previous_provenance_intact(self):
        import_response = self.import_request()
        self.assertEqual(200, import_response.status_code, import_response.json)
        before = self.get()
        import_path = self.store.directory(self.pid) / 'imports'
        directory_snapshot = {path: path.read_bytes() for path in import_path.rglob('*.yaml')}
        self.assertEqual(6, len(directory_snapshot))

        malformed = self.encode(self.legacy)
        malformed['m1_object_model.yaml'] = 'model_type: OBJECT\nmodel_type: OBJECT\n'
        self.assertEqual(400, self.import_request(files=malformed).status_code)
        self.assertEqual(409, self.import_request(revision=before['revision'] - 1).status_code)
        self.assertEqual(before, self.get())
        self.assertEqual(directory_snapshot, {path: path.read_bytes() for path in import_path.rglob('*.yaml')})

        # The same material at the current revision is idempotent.
        self.assertEqual(200, self.import_request().status_code)
        self.assertEqual(before, self.get())

    def test_confirmed_import_executes_and_mapping_edit_retires_old_confirmation(self):
        self.approve_requirements()
        imported = self.import_request()
        self.assertEqual(200, imported.status_code, imported.json)
        approved = self.confirm('models')
        self.assertEqual(200, approved.status_code, approved.json)
        self.assertEqual(_models_digest(approved.json), approved.json['models_confirmation']['sha256'])
        plan = self.fixture.plan('SELECT sum(amount) AS total FROM invoices')
        with patch('paas.jobs.LLM', return_value=fixtures.StubLLM(plan)):
            submission = self.client.post(self.route + '/analysis', json={'question': '整个快照的结算总额'})
            self.assertEqual(202, submission.status_code, submission.json)
            finished = wait_job(self.store, submission.json['job_id'])
        self.assertEqual('succeeded', finished['status'], finished.get('error'))
        self.assertEqual([{'total': 35.0}], finished['result']['rows'])
        before = self.get()
        self.assertEqual(ontology.STANDARD, before['runs'][0]['model_standard'])
        self.assertEqual(before['models_confirmation']['sha256'], before['runs'][0]['models_sha256'])

        archive = self.client.get(self.route + '/download')
        self.assertEqual(200, archive.status_code)
        with zipfile.ZipFile(io.BytesIO(archive.data)) as package:
            model_paths = {name for name in package.namelist() if name.startswith('models/')}
            self.assertEqual({'models/' + name for name in ontology.MODEL_FILES}, model_paths)
            mapping = yaml.safe_load(package.read('source/data_mapping.yaml'))
            self.assertEqual(before['source_bindings'], mapping)

        binding = before['source_bindings']
        binding['entity_mappings'][0]['field_mappings'][0]['confidence'] = 0.9
        edited = self.client.put(self.route + '/models/data_mapping.yaml', json={
            'revision': before['revision'], 'text': yaml.safe_dump(binding, allow_unicode=True),
        })
        self.assertEqual(200, edited.status_code, edited.json)
        self.assertNotEqual(_models_digest(approved.json), _models_digest(edited.json))
        self.assertIsNone(edited.json['models_confirmation'])
        self.assertTrue(edited.json['runs'][0]['stale'])
        self.assertEqual(409, self.client.post(self.route + '/analysis', json={'question': '总额'}).status_code)

    def test_visual_mapping_can_save_unbound_fields_but_cannot_confirm_or_use_them(self):
        self.approve_requirements()
        self.assertEqual(200, self.import_request().status_code)
        self.assertEqual(200, self.confirm('models').status_code)
        before = self.get()
        binding = before['source_bindings']
        fields = binding['entity_mappings'][0]['field_mappings']
        removed = fields.pop()

        def save(*, visual):
            return self.client.put(self.route + '/models/data_mapping.yaml', json={
                'revision': self.get()['revision'], 'text': yaml.safe_dump(binding),
                'allow_incomplete_mapping': visual,
            })

        self.assertEqual(400, save(visual=False).status_code)
        saved = save(visual=True)
        self.assertEqual(200, saved.status_code, saved.json)
        self.assertIn('属性未映射', saved.json['model_diagnostics'][0])
        self.assertIsNone(self.get()['models_confirmation'])
        self.assertEqual(400, self.confirm('models').status_code)
        self.assertEqual(409, self.client.post(self.route + '/analysis', json={'question': '总额'}).status_code)
        removed['db_column'] = '不存在的字段'
        fields.append(removed)
        self.assertEqual(400, save(visual=True).status_code)
        removed.update(db_column=removed['ontology_field'], mapping_origin='manual')
        removed.pop('confidence')
        corrected = save(visual=True)
        self.assertEqual(200, corrected.status_code, corrected.json)
        self.assertEqual([], corrected.json['model_diagnostics'])
        self.assertEqual(200, self.confirm('models').status_code)

    def test_reasoning_followup_only_uses_current_context_from_the_same_session(self):
        self.approve_requirements()
        self.assertEqual(200, self.import_request().status_code)
        self.assertEqual(200, self.confirm('models').status_code)
        plan = self.fixture.plan('SELECT sum(amount) AS total FROM invoices')
        prompts = []

        class RecordingLLM(fixtures.StubLLM):
            def json(self, system, user, *, purpose, max_tokens):
                if purpose == 'analysis_sql':
                    prompts.append(json.loads(user))
                return super().json(system, user, purpose=purpose, max_tokens=max_tokens)

        for session_id, question in (('first-session', '整个快照的结算总额'),
                                     ('first-session', '再算一次结算总额'),
                                     ('separate', '另一会话的结算总额')):
            with patch('paas.jobs.LLM', return_value=RecordingLLM(plan)):
                submission = self.client.post(self.route + '/analysis', json={
                    'question': question, 'reasoning_session_id': session_id,
                })
                self.assertEqual(202, submission.status_code, submission.json)
                finished = wait_job(self.store, submission.json['job_id'])
            self.assertEqual('succeeded', finished['status'], finished.get('error'))

        project = self.get()
        first, followup, separate = project['runs']
        self.assertEqual(first['reasoning_session_id'], followup['reasoning_session_id'])
        self.assertNotIn('prior_turns', prompts[0])
        self.assertEqual([first['question']], [row['question'] for row in prompts[1]['prior_turns']])
        self.assertNotIn('prior_turns', prompts[2])
        self.assertEqual([first['id']], followup['provenance']['context_run_ids'])
        self.assertEqual([], separate['provenance']['context_run_ids'])
        self.assertEqual([], project['messages'])

    def test_legacy_startup_migration_keeps_equivalent_confirmation_and_history(self):
        self.approve_requirements()
        old_model_hash = digest(self.legacy)
        historical = {'question': '旧查询', 'rows': [{'total': 35.0}], 'stale': False,
                      'source_hash': self.get()['source']['sha256'], 'models_sha256': old_model_hash}
        old_artifact = 'artifacts/revision-legacy/m_metric_model.yaml'
        old_file = self.store.directory(self.pid) / old_artifact
        old_file.parent.mkdir(parents=True)
        old_file.write_text(yaml.safe_dump(self.legacy['m_metric_model.yaml']), encoding='utf-8')
        before_bytes = old_file.read_bytes()

        def seed(project):
            project.update(models=self.legacy, models_requirements_sha256=digest(project['requirements']),
                           runs=[historical], model_artifacts={'m_metric_model.yaml': old_artifact})
            project['models_confirmation'] = {'scope': 'models', 'sha256': old_model_hash,
                                               'decision': '保留原来的确认决定', 'revision': project['revision']}
        before = self.store.mutate(self.pid, seed)

        restarted = create_app(self.temp.name)
        self.addCleanup(restarted.extensions['paas_jobs'].pool.shutdown, wait=True)
        migrated = self.get()
        self.assertEqual(before['revision'] + 1, migrated['revision'])
        self.assertEqual(ontology.STANDARD, migrated['model_standard'])
        self.assertEqual(self.legacy, ontology.analysis_projection(migrated['models'], migrated['source_bindings']))
        self.assertEqual([historical], migrated['runs'])
        self.assertEqual(before_bytes, old_file.read_bytes())
        self.assertEqual(before['models_confirmation']['decision'], migrated['models_confirmation']['decision'])
        self.assertEqual(_models_digest(migrated), migrated['models_confirmation']['sha256'])
        self.assertEqual(old_model_hash, migrated['model_migration']['previous_sha256'])
        self.assertEqual(before['model_artifacts'], migrated['model_migration']['previous_artifacts'])

        restarted_again = create_app(self.temp.name)
        self.addCleanup(restarted_again.extensions['paas_jobs'].pool.shutdown, wait=True)
        self.assertEqual(migrated, self.get())


if __name__ == '__main__':
    unittest.main()
