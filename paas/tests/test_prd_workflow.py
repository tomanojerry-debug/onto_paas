"""Requirements exploration and managed document regressions over Flask, offline."""
import io
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from paas.server import create_app
from paas.tests.requirements_fixture import evidence_prd, loan_requirement_models
from paas.tests.test_business import fixture_models
from paas.tests.test_platform import wait_job
from paas.tests.ui_traceability_fixture import loan_design, loan_models


def text_pdf():
    stream = b'BT /F1 12 Tf 20 30 Td (A loan is closed upon return.) Tj ET'
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        (b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] '
         b'/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>'),
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length %d >>\nstream\n%s\nendstream' % (len(stream), stream),
    ]
    document = bytearray(b'%PDF-1.4\n')
    offsets = []
    for index, body in enumerate(objects, 1):
        offsets.append(len(document))
        document += b'%d 0 obj\n%s\nendobj\n' % (index, body)
    xref = len(document)
    document += b'xref\n0 6\n0000000000 65535 f \n'
    for offset in offsets:
        document += b'%010d 00000 n \n' % offset
    document += b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % xref
    return io.BytesIO(document)


def model_round(draft, coverage, questions):
    return {
        'reply': '已梳理从登记到归还的设备借用流程。', 'draft': draft,
        'overview': ['为员工提供设备借用登记。'], 'process': ['员工提交借用申请，归还后关闭。'],
        'domains': ['设备管理'], 'objects': ['设备、借用单'], 'conflicts': [], 'questions': questions,
        'coverage': [{'stage': index, 'status': 'covered', 'evidence': quote}
                     for index, quote in enumerate(coverage)],
    }


def full_prd(facts):
    return evidence_prd('。'.join(facts) + '。')


class PRDWorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='paas-prd-test-')
        self.addCleanup(temp.cleanup)
        app = create_app(temp.name)
        app.config['TESTING'] = True
        self.store = app.extensions['paas_store']
        self.client = app.test_client()
        self.model = Mock(calls=[], reasoning_effort='low')
        model_patch = patch('paas.jobs.LLM', return_value=self.model)
        model_patch.start()
        self.addCleanup(model_patch.stop)
        self.addCleanup(app.extensions['paas_jobs'].pool.shutdown, wait=True)
        response = self.client.post('/api/v1/projects', json={'name': '设备借用', 'kind': 'business'})
        self.assertEqual(response.status_code, 201, response.json)
        self.route = '/api/v1/projects/' + response.json['id']

    def post(self, suffix, payload):
        return self.client.post(self.route + suffix, json=payload)

    def project(self):
        return self.client.get(self.route).json

    def finish(self, response, status='succeeded'):
        self.assertEqual(response.status_code, 202, response.json)
        job = wait_job(self.store, response.json['job_id'])
        self.assertEqual(job['status'], status, job.get('error'))
        return job

    def import_markdown(self, document, *, new_project=False):
        data = {'document': (io.BytesIO(document.encode()), 'loan-requirements.md')}
        path = '/api/v1/requirements/import' if new_project else self.route + '/requirements/import'
        if not new_project:
            data['revision'] = str(self.project()['revision'])
        response = self.client.post(path, data=data, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 201 if new_project else 200, response.json)
        return response.json

    def test_model_ellipsis_evidence_cannot_close_coverage_or_questions(self):
        source = '范围仅限展会器材借用。器材先登记，申请人提交后由主管审批。'
        exact = '范围仅限展会器材借用。'
        spliced = '器材先登记…由主管审批。'
        result = model_round('# 需求草稿', [], [{
            'question': '最终审批由谁办理？', 'answer': '主管', 'evidence': spliced, 'status': 'resolved',
        }])
        result['coverage'] = [
            {'stage': 0, 'status': 'covered', 'evidence': exact},
            {'stage': 4, 'status': 'covered', 'evidence': spliced},
        ]
        self.model.json.return_value = result
        self.finish(self.post('/chat', {'message': source, 'workflow': 'adaptive'}))
        review = self.project()['prd_exploration']
        self.assertEqual(review['coverage'][0]['evidence'], exact)
        self.assertEqual(review['coverage'][4]['status'], 'open')
        self.assertEqual(review['coverage'][4]['evidence'], '')
        self.assertEqual(review['questions'][0]['status'], 'open')
        self.assertFalse(review['can_finalize'])

    def test_multiround_attachments_pending_save_final_save_and_stale_inputs(self):
        starting_facts = ['为员工提供设备借用登记', '借用单引用设备并登记数量', 'A loan is closed upon return.']
        more_facts = ['归还后释放设备库存', '借用单必须由主管审批', '按设备查询当前借用单',
                      '员工只可查看本人的借用单', '界面提供申请表和借用列表']
        draft = '# 设备借用需求草稿\n\n员工提交借用申请，借用单引用设备并登记数量。'
        final_draft = full_prd(starting_facts)
        question = {'question': '借用申请是否需要审批？', 'impact': '影响借用单状态和审批功能',
                    'recommendation': '由主管审批', 'reason': '便于分配设备', 'alternatives': ['无需审批'],
                    'status': 'open'}
        answer = 'Q-01 借用单必须由主管审批'
        accepted = {'id': 'Q-01', 'status': 'resolved', 'answer': '主管审批', 'evidence': answer}
        self.model.json.side_effect = [
            ValueError('模型输出达到长度上限，未接受截断产物'),
            model_round(draft, starting_facts, [question]),
            model_round(final_draft + '\n\n申请表提交按钮名称[待确认]。', starting_facts + more_facts, [accepted]),
            model_round(final_draft, starting_facts + more_facts, []),
            ValueError('模型连接中断'),
        ]
        response = self.client.post(self.route + '/chat', data={
            'message': starting_facts[0], 'workflow': 'adaptive', 'attachments': [
                (io.BytesIO(('# 对象\n' + starting_facts[1]).encode()), 'objects.md'),
                (text_pdf(), 'returns.pdf'),
            ],
        }, content_type='multipart/form-data')
        self.finish(response)
        first = self.project()
        self.assertFalse(first['prd_stale'])
        self.assertEqual([a['name'] for a in first['messages'][0]['attachments']], ['objects.md', 'returns.pdf'])
        self.assertEqual(first['messages'][0]['attachments'][1]['pages'], 1)
        primary_call, retry = self.model.json.call_args_list
        self.assertEqual(primary_call.args[1], retry.args[1])
        self.assertLess(len(retry.args[0]), len(primary_call.args[0]))
        evidence = json.loads(retry.args[1])['latest_user_message']
        for fact in starting_facts:
            self.assertIn(fact, evidence)
        initial_review = first['messages'][-1]['prd']
        self.assertFalse(initial_review['can_finalize'])
        self.assertEqual(initial_review['questions'][0]['id'], 'Q-01')
        self.assertIn('[待确认] Q-01', initial_review['draft'])

        blocked = self.post('/requirements', {'revision': first['revision']})
        self.assertEqual(blocked.status_code, 409, blocked.json)
        saved = self.post('/requirements', {'revision': first['revision'], 'save_draft': True})
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual(self.project()['requirements'], initial_review['draft'])
        self.assertTrue(self.project()['requirements_document']['draft'])
        self.assertEqual(self.post('/confirm', {'scope': 'requirements',
                                               'revision': saved.json['revision']}).status_code, 409)

        self.finish(self.post('/chat', {'workflow': 'adaptive', 'message': '。'.join([answer, *more_facts])}))
        second = self.project()
        self.assertFalse(second['messages'][-1]['prd']['can_finalize'])
        self.assertEqual(self.post('/requirements', {'revision': second['revision']}).status_code, 409)
        self.finish(self.post('/chat', {'workflow': 'adaptive', 'message': '申请表提交按钮名称定为提交申请。'}))
        third = self.project()
        review = third['messages'][-1]['prd']
        self.assertTrue(review['can_finalize'])
        self.assertEqual(review['questions'][0]['id'], 'Q-01')
        self.assertEqual(review['questions'][0]['status'], 'resolved')
        self.assertEqual(review['questions'][0]['evidence'], answer)
        self.assertEqual(self.post('/requirements', {'revision': first['revision']}).status_code, 409)
        saved = self.post('/requirements', {'revision': third['revision']})
        self.assertEqual(saved.status_code, 200, saved.json)
        final = self.project()
        self.assertEqual(final['requirements'], final_draft)
        self.assertEqual(final['requirements'], final['messages'][-1]['prd']['draft'])
        self.assertFalse(final['requirements_document']['draft'])
        self.assertEqual(self.model.json.call_count, 4)
        self.model.text.assert_not_called()

        self.finish(self.post('/chat', {'workflow': 'adaptive', 'message': '需要改为运营审批。'}), 'failed')
        stale = self.project()
        self.assertTrue(stale['prd_stale'])
        self.assertEqual(stale['requirements'], final_draft)
        self.assertEqual(self.post('/requirements', {'revision': stale['revision']}).status_code, 409)
        self.assertEqual(self.post('/requirements', {'revision': stale['revision'], 'save_draft': True}).status_code, 409)

    def test_short_complete_review_expands_inside_chat_and_preserves_draft_on_failure(self):
        facts = ['提供员工设备借用', '借用单登记设备和数量', '归还后关闭借用单', '归还后释放设备库存',
                 '借用需主管审批', '按设备查询借用单', '员工只能查看本人借用单', '提供申请表和借用列表']
        message = '。'.join(facts)
        compact = '# 设备借用需求草稿\n\n' + message + '。'
        final = full_prd(facts)
        self.model.json.return_value = model_round(compact, facts, [])
        self.model.text.side_effect = [ValueError('模型输出达到长度上限，未接受截断产物'), '# 缺少章节的输出', final]
        self.finish(self.post('/chat', {'message': message, 'workflow': 'adaptive'}))
        project = self.project()
        response = project['messages'][-1]
        self.assertFalse(response['prd']['can_finalize'])
        self.assertEqual(response['prd']['questions'], [])
        self.assertEqual(response['prd']['draft'], compact)
        self.assertIn('重新生成完整规格', response['content'])
        self.assertEqual(self.post('/requirements', {'revision': project['revision']}).status_code, 409)
        self.assertEqual(self.post('/requirements', {'revision': project['revision'], 'save_draft': True}).status_code, 200)

        first, recovery = self.model.text.call_args_list
        self.assertEqual(first.args[1], recovery.args[1])
        context = json.loads(first.args[1])
        self.assertEqual(context['latest_user_message'], message)
        self.assertEqual([row['evidence'] for row in context['current_review']['coverage']], facts)
        self.assertEqual(recovery.kwargs['purpose'], 'business.prd.complete.retry')
        self.finish(self.post('/chat', {'message': '重新生成完整规格', 'workflow': 'adaptive'}))
        project = self.project()
        self.assertTrue(project['messages'][-1]['prd']['can_finalize'])
        self.assertEqual(project['messages'][-1]['prd']['draft'], final)
        self.assertEqual(self.post('/requirements', {'revision': project['revision']}).status_code, 200)
        self.assertEqual(self.project()['requirements'], final)

    def test_complete_retry_receives_previous_candidate_and_exact_audit_failures(self):
        facts = ['提供员工设备借用', '登记设备数量', '填写后保存申请', '只有独立借用对象',
                 '由主管审批', '按设备查询借用', '员工查询自己借用', '提供设备申请页面']
        final = full_prd(facts)
        malformed = final.replace('| 通过 |', '| 未通过 |', 1)
        self.model.json.return_value = model_round('# 需求草稿', facts, [])
        self.model.text.side_effect = [malformed, final]
        self.finish(self.post('/chat', {'message': '。'.join(facts), 'workflow': 'adaptive'}))
        retry = self.model.text.call_args_list[1]
        context = json.loads(retry.args[1])
        self.assertEqual(context['previous_candidate'], malformed)
        self.assertEqual(context['failed_audit_items'][0]['number'], 1)
        self.assertIn('自检结果尚未通过', context['failed_audit_items'][0]['diagnostics'])
        self.assertIn('各自检项可引用的章节编号', retry.args[0])
        self.assertTrue(self.project()['prd_exploration']['can_finalize'])

    def test_upload_edit_preview_optimize_and_delete_keeps_import_provenance(self):
        original = '# 设备借用原稿\n\n主管审批人[待确认]。'
        uploaded = self.import_markdown(original, new_project=True)
        self.route = '/api/v1/projects/' + uploaded['id']
        metadata = uploaded['requirements_document']
        self.assertEqual(metadata['title'], '设备借用原稿')
        self.assertEqual(metadata['source'], 'imported')
        self.assertEqual(metadata['imported_file']['name'], 'loan-requirements.md')
        listed = self.client.get('/api/v1/projects').json['projects']
        self.assertIn(uploaded['id'], [row['id'] for row in listed if row.get('requirements_document')])
        with self.client.get(self.route + '/artifacts/' + uploaded['requirements_artifact']) as preview:
            self.assertEqual(preview.get_data(as_text=True), original)

        edited = original + '\n\n借用人登记设备数量。'
        saved = self.client.put(self.route + '/requirements', json={
            'revision': uploaded['revision'], 'title': '设备借用规格', 'document': edited,
        })
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual(saved.json['requirements_document']['last_operation'], 'edited')
        self.assertEqual(self.post('/requirements/optimize', {'revision': uploaded['revision']}).status_code, 409)
        self.model.text.return_value = '# 设备借用规格\n\n借用人登记设备数量。\n\n## 附录 C 七模型建模输入基线'
        self.finish(self.post('/requirements/optimize', {'revision': saved.json['revision'], 'feedback': '整理业务对象'}))
        optimized = self.project()
        source_sent = json.loads(self.model.text.call_args.args[1])
        self.assertEqual(source_sent['source_document'], edited)
        self.assertEqual(source_sent['review_feedback'], '整理业务对象')
        self.assertEqual(optimized['requirements_document']['title'], '设备借用规格')
        self.assertEqual(optimized['requirements_document']['last_operation'], 'optimized')
        self.assertEqual(optimized['requirements_document']['source'], 'imported')
        self.assertEqual(optimized['requirements_document']['imported_file'], metadata['imported_file'])
        self.assertIn('主管审批人[待确认]', optimized['requirements'])
        self.assertFalse(optimized['requirement_audit']['passed'])
        self.assertIsNone(optimized['requirements_confirmation'])
        self.assertEqual(self.post('/confirm', {'scope': 'requirements',
                                               'revision': optimized['revision']}).status_code, 409)
        deleted = self.client.delete(self.route + '/requirements', json={'revision': optimized['revision']})
        self.assertEqual(deleted.status_code, 200, deleted.json)
        self.assertFalse(self.project().get('requirements_document'))
        self.assertEqual(self.project()['requirements'], '')

    def test_staged_workflow_requires_explicit_confirmations_and_rejects_mode_switch(self):
        self.model.json.return_value = {
            'reply': '业务范围仅设备借用，请核对。', 'stage_summary': '范围已梳理',
            'ready_for_confirmation': True, 'questions': [],
        }
        self.finish(self.post('/chat', {'workflow': 'staged', 'message': '办理员工设备借用'}))
        project = self.project()
        self.assertEqual(project['requirements_workflow'], 'staged')
        self.assertEqual(project['stage'], 0)
        self.assertEqual(self.post('/requirements', {}).status_code, 409)
        self.assertEqual(self.post('/chat', {'workflow': 'adaptive', 'message': '请求切换'}).status_code, 409)
        decision = self.post('/confirm', {'scope': 'stage', 'stage': 0, 'revision': project['revision']})
        self.assertEqual(decision.status_code, 200, decision.json)
        self.assertEqual(decision.json['stage'], 1)
        self.assertEqual(decision.json['stage_summaries']['0'], '范围已梳理')
        self.assertEqual(self.post('/confirm', {'scope': 'stage', 'stage': 1,
                                               'revision': decision.json['revision']}).status_code, 409)
        self.assertIn('business.explore.0', self.model.json.call_args.kwargs['purpose'])

    def test_optimizer_does_not_restore_explicitly_closed_appendix_marker(self):
        document = evidence_prd()
        with_marker = document.replace(
            '无遗留问题，已完成业务范围确认。',
            '无遗留问题，已完成业务范围确认。附录 B 无 `[待确认]` 条目。',
            1,
        )
        imported = self.import_markdown(with_marker)
        self.assertTrue(imported['requirement_audit']['passed'])
        self.model.text.return_value = document
        self.finish(self.post('/requirements/optimize', {'revision': imported['revision']}))
        result = self.project()
        self.assertEqual(result['requirements'], document)
        self.assertTrue(result['requirement_audit']['passed'])

    def test_confirmed_prototype_not_applicable_allows_models_without_ui_trace_refs(self):
        uploaded = self.import_markdown(evidence_prd(prototype_not_applicable=True))
        self.assertTrue(uploaded['requirement_audit']['passed'], uploaded['requirement_audit']['errors'])
        decision = self.post('/confirm', {'scope': 'requirements', 'revision': uploaded['revision']})
        self.assertEqual(decision.status_code, 200, decision.json)
        self.assertEqual(self.post('/models', {}).status_code, 409)
        decision = self.post('/confirm', {'scope': 'prototype_not_applicable', 'revision': decision.json['revision']})
        self.assertEqual(decision.status_code, 200, decision.json)
        self.assertTrue(decision.json['prototype_confirmation']['not_applicable'])
        models = loan_requirement_models(fixture_models())
        models['mu-ui-model.yaml']['screens'][0].pop('requirementRefs')
        self.model.json.side_effect = lambda system, user, **options: next(
            item for item in models.values() if options['purpose'] == 'business.model.' + item['model_type'].lower())
        self.finish(self.post('/models', {}))
        project = self.project()
        self.assertIsNone(project['models_prototype_sha256'])
        self.assertIsNone(project.get('prototype'))
        confirmed = self.post('/confirm', {'scope': 'models', 'revision': project['revision']})
        self.assertEqual(confirmed.status_code, 200, confirmed.json)

    def test_preview_approval_does_not_authorize_model_generation(self):
        specification = evidence_prd()
        uploaded = self.import_markdown(specification)
        self.assertTrue(uploaded['requirement_audit']['passed'])
        self.assertEqual(self.post('/prototype', {'revision': uploaded['revision']}).status_code, 409)
        confirmed = self.post('/confirm', {'scope': 'requirements', 'revision': uploaded['revision'],
                                           'prototype_only': True})
        self.assertEqual(confirmed.status_code, 200, confirmed.json)
        self.assertTrue(confirmed.json['requirements_confirmation']['prototype_only'])
        self.model.json.return_value = loan_design()
        self.finish(self.post('/prototype', {'revision': confirmed.json['revision']}))
        project = self.project()
        self.assertEqual(project['prototype']['requirements_sha256'], project['requirements_document']['sha256'])
        html = self.client.get(self.route + '/prototype/html')
        self.assertEqual(html.status_code, 200)
        self.assertIn('设备名称', html.get_data(as_text=True))
        confirmed = self.post('/confirm', {'scope': 'prototype', 'revision': project['revision']})
        self.assertEqual(confirmed.status_code, 200, confirmed.json)
        blocked = self.post('/models', {})
        self.assertEqual(blocked.status_code, 409, blocked.json)
        self.assertEqual(self.model.json.call_count, 1)

        formal = self.post('/confirm', {'scope': 'requirements', 'revision': confirmed.json['revision']})
        self.assertEqual(formal.status_code, 200, formal.json)
        self.assertFalse(formal.json['requirements_confirmation'].get('prototype_only'))
        with patch('paas.business.generate_models', return_value=loan_models(loan_requirement_models(fixture_models()))):
            self.finish(self.post('/models', {}))
        self.assertTrue(self.project()['models'])

