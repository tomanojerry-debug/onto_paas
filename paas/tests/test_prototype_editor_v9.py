"""Versioned UI prototype rendering and editor behavior at the HTTP boundary."""
import base64
from copy import deepcopy
import hashlib
import json
import re
import tempfile
import unittest
from unittest.mock import patch

from paas import prototype
from paas.server import create_app
from paas.store import digest
from paas.tests.test_platform import complete_business_fixture, fixture_design, wait_job
from paas.tests.test_prototype_contract import FakeLLM


LEGACY_FIXTURE_HTML_SHA256 = 'a8773dd1bc24c663cffb995095414316b1e57ad604f7d1deb8c8ce5679b226ac'
V9_FIXTURE_HTML_SHA256 = 'f828ed271e370b08fce31a4d2c4f95b5cfc474ce016bc089f2881b4db5f9b7ae'
WORKBENCH_V1_FIXTURE_HTML_SHA256 = '1728fef8b03f5365e6024c6485f03e54cdaeec1c713135f8159d1fe69ecd9f87'


def legacy_artifact(project):
    design = prototype.validate_design(fixture_design(), template_version=prototype.LEGACY_TEMPLATE)
    rendered = prototype.render(design, template_version=prototype.LEGACY_TEMPLATE)
    requirements_sha = digest(project['requirements'])
    persisted = {'project_id': project['id'], 'requirements_sha256': requirements_sha,
                 'design': design, 'html_sha256': LEGACY_FIXTURE_HTML_SHA256}
    return {'id': 'legacy-prototype', 'project_id': project['id'], 'requirements_sha256': requirements_sha,
            'revision': 1, 'html': rendered, 'sha256': digest(persisted), **design}


def edit_design(extra_column=False):
    fields = [{'id': 'deviceName', 'label': '设备名称', 'type': 'reference', 'required': True}]
    columns = [{'id': 'deviceName', 'label': '设备名称'}]
    if extra_column:
        fields.append({'id': 'returnDate', 'label': '归还日期', 'type': 'date'})
        columns.append({'id': 'returnDate', 'label': '归还日期'})
    return {'summary': '<script>revision-preview-marker</script>', 'pages': [
        {'group': '设备管理', 'id': 'equipment', 'name': '借用登记', 'purpose': '登记设备借用',
         'layout': 'master_detail_form', 'fields': fields, 'detail_title': '设备明细',
         'detail_fields': [{'id': 'quantity', 'label': '数量', 'type': 'number'}],
         'actions': [{'id': 'draft', 'kind': 'draft', 'label': '保存草稿'},
                     {'id': 'submit', 'kind': 'submit', 'label': '提交审批'}]},
        {'group': '设备管理', 'id': 'loans', 'name': '借用查询', 'purpose': '查询设备借用',
         'layout': 'query_list', 'record_source': 'equipment', 'columns': columns,
         'actions': [{'id': 'query', 'kind': 'query', 'label': '查询'}]},
    ]}


def workbench_design():
    design = edit_design(extra_column=True)
    design['title'] = '设备借用工作台'
    form, query = design['pages']
    form['presentation'] = 'dialog'
    form['fields'][0].update(reference_page='catalog', reference_field='deviceName')
    form['fields'].extend([
        {'id': 'fee', 'label': '借用费用', 'type': 'number'},
        {'id': 'remark', 'label': '备注', 'type': 'textarea', 'wide': True},
    ])
    query['actions'].append({'id': 'new', 'kind': 'open_form', 'label': '借用登记', 'target_page': 'equipment'})
    design['pages'] = [query, form, {
        'group': '设备管理', 'id': 'catalog', 'name': '设备目录', 'purpose': '录入可借出设备',
        'layout': 'list_maintenance', 'fields': [{'id': 'deviceName', 'label': '设备名称', 'type': 'text'}],
        'columns': [{'id': 'deviceName', 'label': '设备名称'}],
        'actions': [{'id': 'addDevice', 'kind': 'create', 'label': '新增设备'}],
    }]
    design['assistant'] = {'suggestions': ['如何提交借用审批？'], 'metrics': [
        {'label': '当前借用记录', 'page': 'equipment'},
        {'label': '借用费用合计', 'page': 'equipment', 'field': 'fee'},
    ]}
    return design


class PrototypeEditorV9Tests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='paas-prototype-editor-test-')
        self.addCleanup(temp.cleanup)
        app = create_app(temp.name)
        app.config['TESTING'] = True
        self.store = app.extensions['paas_store']
        self.addCleanup(app.extensions['paas_jobs'].pool.shutdown, wait=True)
        self.client = app.test_client()
        row = self.store.create('设备借用原型测试', 'business', '构造的业务需求')
        self.project_id = row['id']
        self.path = '/api/v1/projects/' + self.project_id

    def seed(self, legacy=False):
        def initialize(row):
            complete_business_fixture(row)
            if legacy:
                original = legacy_artifact(row)
                row['prototype'] = original
                row['prototype_confirmation']['sha256'] = original['sha256']
                row['models_prototype_sha256'] = original['sha256']

        return self.store.mutate(self.project_id, initialize)

    def project(self):
        return self.store.get(self.project_id)

    def post_edit(self, action, project, **values):
        payload = {'revision': project['revision'], 'prototype_id': project['prototype']['id'], **values}
        return self.client.post(f'{self.path}/prototype/edit/{action}', json=payload)

    def chat(self, model, message):
        with patch('paas.jobs.LLM', return_value=model):
            response = self.post_edit('chat', self.project(), message=message)
            self.assertEqual(response.status_code, 202, response.json)
            job = wait_job(self.store, response.json['job_id'])
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        return self.project()

    def test_unversioned_legacy_artifact_remains_byte_compatible(self):
        project = self.seed(legacy=True)
        item = project['prototype']
        self.assertNotIn('template_version', item)
        self.assertTrue(prototype.is_current(project))
        self.assertEqual(prototype.assert_confirmed(project), item)

        response = self.client.get(self.path + '/prototype/html')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.headers['X-Prototype-Mode'], 'current')
        self.assertEqual(response.data, item['html'].encode())
        self.assertEqual(hashlib.sha256(response.data).hexdigest(), LEGACY_FIXTURE_HTML_SHA256)
        self.assertNotEqual(prototype.render(fixture_design()).encode(), response.data)

    def test_previous_v9_renderer_remains_byte_compatible(self):
        rendered = prototype.render(fixture_design(), template_version=prototype.V9_TEMPLATE)
        self.assertEqual(hashlib.sha256(rendered.encode()).hexdigest(), V9_FIXTURE_HTML_SHA256)
        self.assertNotIn('AI 业务助手', rendered)

    def test_previous_workbench_renderer_remains_byte_compatible(self):
        design = workbench_design()
        archive = prototype.render(design, template_version=prototype.WORKBENCH_V1_TEMPLATE)
        self.assertEqual(hashlib.sha256(archive.encode()).hexdigest(), WORKBENCH_V1_FIXTURE_HTML_SHA256)
        self.assertNotEqual(archive, prototype.render(design))

    def test_delete_current_prototype_preserves_requirements_and_releases(self):
        self.seed(legacy=True)
        build = {'status': 'built', 'prototype_sha256': self.project()['prototype']['sha256']}
        release = {'id': 'release-kept', 'version': '1.0.0', 'build': dict(build)}

        def prepare(row):
            row.update(build=build, releases=[release], published_release_id=release['id'],
                       prototype_edit={'prototype_id': row['prototype']['id'], 'candidate': row['prototype']})

        before = self.store.mutate(self.project_id, prepare)
        item = before['prototype']
        payload = {'revision': before['revision'], 'prototype_id': item['id']}
        response = self.client.delete(self.path + '/prototype', json=payload)
        self.assertEqual(response.status_code, 200, response.json)
        after = response.json
        self.assertEqual(after['revision'], before['revision'] + 1)
        for key in ('prototype', 'prototype_edit'):
            self.assertNotIn(key, after)
        for key in ('prototype_confirmation', 'models_confirmation', 'models_prototype_sha256', 'build'):
            self.assertIsNone(after[key])
        for key in ('requirements', 'requirements_confirmation', 'models', 'releases', 'published_release_id'):
            self.assertEqual(after[key], before[key])
        self.assertEqual(after['prototype_history'], [item])
        self.assertEqual(after['build_history'], [build])
        self.assertEqual(self.client.get(self.path + '/prototype/html').status_code, 404)
        self.assertEqual(self.client.get(self.path + '/prototype/edit/html').status_code, 404)
        archived = self.client.get(self.path + '/prototype/html', query_string={'prototype_id': item['id']})
        self.assertEqual(archived.headers['X-Prototype-Mode'], 'history')
        self.assertEqual(archived.data, item['html'].encode())

        with patch('paas.jobs.LLM', return_value=FakeLLM([workbench_design()])):
            response = self.client.post(self.path + '/prototype', json={'revision': after['revision']})
            self.assertEqual(response.status_code, 202, response.json)
            job = wait_job(self.store, response.json['job_id'])
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        regenerated = self.project()['prototype']
        self.assertEqual(regenerated['revision'], item['revision'] + 1)
        self.assertEqual(regenerated['previous_id'], item['id'])

    def test_delete_rejects_stale_target_active_job_and_fixed_baselines(self):
        initial = self.seed()
        url = self.path + '/prototype'
        prototype_id = initial['prototype']['id']
        for payload, status in (
            ({'revision': initial['revision']}, 400),
            ({'revision': initial['revision'] - 1, 'prototype_id': prototype_id}, 409),
            ({'revision': initial['revision'], 'prototype_id': 'stale'}, 409),
        ):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.delete(url, json=payload).status_code, status)
                self.assertEqual(self.project(), initial)

        for key, value in (
            ('active_job_id', 'job-running'),
            ('reference_adoption', {'format': 'reference'}),
            ('business_model_import', {'format': 'portable-business-v9'}),
        ):
            with self.subTest(key=key):
                locked = self.store.mutate(self.project_id, lambda row: row.update({key: value}))
                response = self.client.delete(url, json={'revision': locked['revision'], 'prototype_id': prototype_id})
                self.assertEqual(response.status_code, 409, response.json)
                self.assertEqual(self.project(), locked)
                self.store.mutate(self.project_id, lambda row: row.pop(key))

    def test_workbench_renders_linked_dialog_reference_and_assistant(self):
        project = self.seed()
        model = FakeLLM([workbench_design()])
        with patch('paas.jobs.LLM', return_value=model):
            response = self.client.post(self.path + '/prototype', json={'revision': project['revision']})
            self.assertEqual(response.status_code, 202, response.json)
            job = wait_job(self.store, response.json['job_id'])
        self.assertEqual(job['status'], 'succeeded', job.get('error'))
        generated = self.project()['prototype']
        self.assertEqual(generated['template_version'], prototype.WORKBENCH_TEMPLATE)
        response = self.client.get(self.path + '/prototype/html')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.data, generated['html'].encode())
        page = response.get_data(as_text=True)
        menu = re.search(r'<nav class="navigation".*?</nav>', page, re.S).group()
        self.assertIn('借用查询', menu)
        self.assertNotIn('借用登记', menu)
        self.assertRegex(page, r'<button[^>]*data-event="open_form"[^>]*data-target="equipment"[^>]*>借用登记</button>')
        for expected in ('data-record-source="equipment"', 'aria-labelledby="entry-title-equipment"',
                         'data-reference-page="catalog"', 'data-reference-field="deviceName"',
                         'data-metric-page="equipment" data-metric-field="fee"', '保存草稿', '提交审批',
                         '如何提交借用审批？', 'AI 业务助手'):
            self.assertIn(expected, page)

    def test_workbench_rejects_unreachable_dialog_and_invalid_assistant_bindings(self):
        unreachable = workbench_design()
        unreachable['pages'][0]['actions'].pop()
        bad_target = workbench_design()
        bad_target['pages'][0]['actions'][-1]['target_page'] = 'catalog'
        broken_reference = workbench_design()
        broken_reference['pages'][1]['fields'][0]['reference_field'] = 'badName'
        wrong_source = workbench_design()
        wrong_source['pages'][0]['record_source'] = 'loans'
        wrong_metric = workbench_design()
        wrong_metric['assistant']['metrics'][1]['field'] = 'deviceName'
        for design, error in [
            (unreachable, '每个弹窗'), (bad_target, '打开表单'), (broken_reference, '引用控件'),
            (wrong_source, '预览记录来源'), (wrong_metric, '预览指标求和'),
        ]:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                prototype.validate_design(design)

    def test_workbench_scopes_detail_reference_to_selected_main_record(self):
        design = workbench_design()
        _, form, source = design['pages']
        source.update(layout='master_detail_form', detail_title='可借组件',
                      detail_fields=[{'id': 'partName', 'label': '组件名称', 'type': 'text'}],
                      actions=[{'id': 'saveDevice', 'kind': 'save', 'label': '保存设备'}])
        form['detail_fields'].append({
            'id': 'selectedPart', 'label': '借用组件', 'type': 'reference',
            'reference_page': 'catalog', 'reference_field': 'partName',
            'reference_scope_field': 'deviceName',
        })
        normalized = prototype.validate_design(design)
        detail = normalized['pages'][1]['detail_fields'][-1]
        self.assertEqual(detail['reference_field'], 'partName')
        self.assertEqual(detail['reference_scope_field'], 'deviceName')
        self.assertIn('data-reference-page="catalog" data-reference-field="partName" '
                      'data-reference-scope-field="deviceName"', prototype.render(normalized))

        no_scope = deepcopy(design)
        del no_scope['pages'][1]['detail_fields'][-1]['reference_scope_field']
        wrong_scope = deepcopy(design)
        wrong_scope['pages'][1]['detail_fields'][-1]['reference_scope_field'] = 'fee'
        missing_target = deepcopy(design)
        missing_target['pages'][2]['detail_fields'][0]['id'] = 'unrelatedPart'
        for invalid, error in [(no_scope, '真实字段'), (wrong_scope, '主表引用'), (missing_target, '真实字段')]:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                prototype.validate_design(invalid)

    def test_workbench_assistant_reads_preview_context_without_changing_project(self):
        project = self.seed()
        route = self.path + '/prototype/assistant'
        payload = {'prototype_id': project['prototype']['id'], 'question': '如何查看审批？',
                   'facts': '当前预览有一条借用记录。'}
        with patch('paas.server.provider_status', return_value={'configured': False}):
            offline = self.client.post(route, json=payload)
        self.assertEqual(offline.status_code, 409, offline.json)

        with patch('paas.server.provider_status', return_value={'configured': True}), patch('paas.server.LLM') as model:
            model.return_value.text.return_value = '请打开借用记录，核对本次预览中的审批状态。'
            unknown = self.client.post(route, json={**payload, 'prototype_id': '0' * 32})
            self.assertEqual(unknown.status_code, 404, unknown.json)
            model.assert_not_called()
            response = self.client.post(route, json=payload)
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual(response.json, {'source': 'model', 'answer': model.return_value.text.return_value})
            call = model.return_value.text.call_args
            self.assertEqual(call.kwargs['purpose'], 'interface-prototype.assistant')
            self.assertIn('当前预览数值', call.args[0])
            self.assertEqual(json.loads(call.args[1]), {
                'question': payload['question'], 'requirements': project['requirements'],
                'preview_summary': payload['facts'],
            })
        self.assertEqual(self.project(), project)

    def test_chat_asks_questions_updates_sandboxed_candidate_and_applies_current_version(self):
        initial = self.seed(legacy=True)
        initial_prototype = initial['prototype']
        first_design = edit_design()
        second_design = edit_design(extra_column=True)
        model = FakeLLM([
            {'reply': '需要修改哪张表？', 'design': None},
            {'reply': '已生成主从登记和借用查询。', 'design': first_design},
            {'reply': '已补充归还日期。', 'design': second_design},
        ])

        clarified = self.chat(model, '目前可以修改哪些页面？')
        self.assertEqual(clarified['prototype'], initial_prototype)
        self.assertEqual(clarified['prototype_confirmation'], initial['prototype_confirmation'])
        self.assertFalse(clarified['prototype_edit'].get('candidate'))
        self.assertEqual(self.client.get(self.path + '/prototype/edit/html').status_code, 404)
        blocked = self.post_edit('apply', clarified, candidate_id='missing')
        self.assertEqual(blocked.status_code, 409, blocked.json)

        first = self.chat(model, '请调整为主从登记并添加借用查询。')
        first_candidate = first['prototype_edit']['candidate']
        stale_chat = self.post_edit('chat', clarified, message='这是旧页面发送的意见。')
        self.assertEqual(stale_chat.status_code, 409, stale_chat.json)
        second = self.chat(model, '登记和查询再增加归还日期。')
        candidate = second['prototype_edit']['candidate']
        self.assertEqual(candidate['template_version'], prototype.WORKBENCH_TEMPLATE)
        self.assertEqual(candidate['previous_id'], initial_prototype['id'])
        self.assertNotEqual(candidate['id'], first_candidate['id'])
        self.assertEqual(second['prototype'], initial_prototype)
        self.assertEqual(second['prototype_confirmation'], initial['prototype_confirmation'])
        self.assertEqual(model.inputs[-1]['working_design']['pages'], first_candidate['pages'])
        self.assertEqual(model.inputs[-1]['current_design']['pages'], initial_prototype['pages'])
        messages = second['prototype_edit']['messages']
        self.assertEqual([msg['role'] for msg in messages], ['user', 'assistant'] * 3)
        self.assertEqual(model.inputs[-1]['conversation'], [
            {key: msg[key] for key in ('role', 'content')} for msg in messages[:-1]
        ])
        reread = self.client.get(self.path).json
        self.assertEqual(reread['prototype_edit']['messages'], messages)

        candidate_url = self.path + '/prototype/edit/html'
        superseded = self.client.get(candidate_url, query_string={'candidate_id': first_candidate['id']})
        self.assertEqual(superseded.status_code, 409, superseded.json)
        preview = self.client.get(candidate_url, query_string={'candidate_id': candidate['id']})
        self.assertEqual(preview.status_code, 200, preview.json)
        self.assertEqual(preview.headers['X-Prototype-Mode'], 'candidate')
        self.assertEqual(preview.headers['Cache-Control'], 'no-store')
        self.assertEqual(preview.data, candidate['html'].encode())
        page = preview.get_data(as_text=True)
        self.assertIn('归还日期', page)
        self.assertIn('&lt;script&gt;revision-preview-marker&lt;/script&gt;', page)
        self.assertNotIn('<script>revision-preview-marker</script>', page)
        protected_scripts = re.findall(r'<script>(.*?)</script>', page, re.S)
        self.assertEqual(len(protected_scripts), 1)
        script_sha = base64.b64encode(hashlib.sha256(protected_scripts[0].encode()).digest()).decode()
        policy = preview.headers['Content-Security-Policy']
        for directive in ("connect-src 'none'", "form-action 'none'", "frame-ancestors 'self'",
                          'sandbox allow-scripts', f"script-src 'sha256-{script_sha}'"):
            self.assertIn(directive, policy)
        for directive in ("script-src 'unsafe-inline'", 'allow-same-origin', 'allow-forms'):
            self.assertNotIn(directive, policy)

        for revision, candidate_id in [(first['revision'], candidate['id']), (second['revision'], first_candidate['id'])]:
            rejected = self.post_edit('apply', second, revision=revision, candidate_id=candidate_id)
            self.assertEqual(rejected.status_code, 409, rejected.json)
            self.assertEqual(self.project()['prototype']['id'], initial_prototype['id'])
        missing_candidate_id = self.post_edit('apply', second)
        self.assertEqual(missing_candidate_id.status_code, 400, missing_candidate_id.json)
        self.assertEqual(self.project()['prototype']['id'], initial_prototype['id'])
        applied = self.post_edit('apply', second, candidate_id=candidate['id'])
        self.assertEqual(applied.status_code, 200, applied.json)
        self.assertEqual(applied.json['prototype']['id'], candidate['id'])
        self.assertIsNone(applied.json['prototype_confirmation'])
        self.assertEqual(applied.json['requirements_confirmation'], initial['requirements_confirmation'])
        self.assertEqual(applied.json['requirements'], initial['requirements'])
        self.assertEqual(applied.json['models'], initial['models'])
        self.assertIsNone(applied.json['models_confirmation'])
        self.assertIsNone(applied.json['models_prototype_sha256'])
        self.assertEqual(applied.json['prototype_history'], [initial_prototype])
        self.assertEqual(applied.json['prototype_edit'], {'prototype_id': candidate['id'], 'messages': messages})
        self.assertEqual(self.client.get(self.path + '/prototype/edit/html').status_code, 404)
        published = self.client.get(self.path + '/prototype/html')
        self.assertEqual(published.data, preview.data)
        history = self.client.get(self.path + '/prototype/html', query_string={'prototype_id': initial_prototype['id']})
        self.assertEqual(history.headers['X-Prototype-Mode'], 'history')
        self.assertEqual(hashlib.sha256(history.data).hexdigest(), LEGACY_FIXTURE_HTML_SHA256)

        third_design = edit_design(extra_column=True)
        third_design['pages'][1]['name'] = '借用记录查询'
        third = self.chat(FakeLLM([{'reply': '已调整查询标题。', 'design': third_design}]), '把查询改名为借用记录查询。')
        third_candidate = third['prototype_edit']['candidate']
        published_third = self.post_edit('apply', third, candidate_id=third_candidate['id'])
        self.assertEqual(published_third.status_code, 200, published_third.json)
        self.assertEqual([item['id'] for item in published_third.json['prototype_history']],
                         [initial_prototype['id'], candidate['id']])
        workbench_history = self.client.get(self.path + '/prototype/html', query_string={'prototype_id': candidate['id']})
        self.assertEqual(workbench_history.headers['X-Prototype-Mode'], 'history')
        self.assertEqual(workbench_history.data, published.data)

    def test_requirement_revision_removes_candidate_and_blocks_stale_editing(self):
        initial = self.seed()
        self.assertEqual(initial['prototype']['template_version'], prototype.WORKBENCH_TEMPLATE)
        pending = self.chat(FakeLLM([{'reply': '候选已生成。', 'design': edit_design()}]), '调整为主从登记。')
        candidate = pending['prototype_edit']['candidate']
        update = self.client.put(self.path + '/requirements', json={
            'revision': pending['revision'], 'document': pending['requirements'] + '\n\n确认需求新增归还日期。',
        })
        self.assertEqual(update.status_code, 200, update.json)
        revised = self.project()
        self.assertEqual(revised['prototype']['id'], initial['prototype']['id'])
        self.assertNotIn('prototype_edit', revised)
        self.assertIsNone(revised['requirements_confirmation'])
        self.assertFalse(prototype.is_current(revised))
        self.assertEqual(self.client.get(self.path + '/prototype/edit/html').status_code, 404)
        edit = self.post_edit('chat', revised, message='重新编辑旧原型。')
        apply = self.post_edit('apply', revised, candidate_id=candidate['id'])
        self.assertEqual(edit.status_code, 409, edit.json)
        self.assertEqual(apply.status_code, 409, apply.json)

        approval = self.client.post(self.path + '/confirm', json={
            'scope': 'requirements', 'revision': revised['revision'], 'prototype_only': True,
            'decision': '已确认新版需求供原型设计。',
        })
        self.assertEqual(approval.status_code, 200, approval.json)
        obsolete = self.post_edit('chat', self.project(), message='新版需求已确认，继续编辑旧原型。')
        self.assertEqual(obsolete.status_code, 409, obsolete.json)
        self.assertIn('原型或需求版本已变化', obsolete.json['error'])
