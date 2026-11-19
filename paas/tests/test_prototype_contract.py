"""UI artifact version gates and hostile model-output checks; no network."""
import copy
import base64
import hashlib
import html
import json
import re
import unittest

from paas import prototype
from paas.store import digest


def design():
    return {'summary': '设备借用登记、审批和查询', 'pages': [
        {'id': 'loans', 'name': '借用申请', 'purpose': '查看当前权限下的申请', 'layout': 'list',
         'fields': [], 'columns': [{'id': 'deviceName', 'label': '设备名称'}, {'id': 'status', 'label': '状态'}],
         'actions': [{'id': 'new', 'label': '新建申请', 'kind': 'navigate', 'target_page': 'apply', 'description': '打开申请表'}]},
        {'id': 'apply', 'name': '登记申请', 'purpose': '填写设备和数量', 'layout': 'form',
         'fields': [{'id': 'deviceName', 'label': '设备名称', 'type': 'text', 'required': True},
                    {'id': 'quantity', 'label': '借用数量', 'type': 'number', 'required': True}],
         'columns': [], 'actions': [{'id': 'save', 'label': '保存申请', 'kind': 'submit'},
                                    {'id': 'back', 'label': '返回列表', 'kind': 'navigate', 'target_page': 'loans'}]},
    ]}


class FakeLLM:
    def __init__(self, responses=None):
        self.responses = responses or [design()]
        self.calls = []
        self.inputs = []

    def json(self, system, user, **kwargs):
        self.calls.append({'purpose': kwargs['purpose']})
        self.inputs.append(json.loads(user))
        return copy.deepcopy(self.responses.pop(0))


class PrototypeContractTests(unittest.TestCase):
    def setUp(self):
        self.project = {'id': 'independent-project', 'name': '设备借用', 'kind': 'business',
                        'requirements': '员工登记设备借用申请，由主管审核。'}
        self.project['requirements_confirmation'] = {'sha256': digest(self.project['requirements'])}

    def test_generated_design_is_bound_to_exact_confirmed_requirement(self):
        llm = FakeLLM()
        result = prototype.generate(self.project, llm)
        self.project['prototype'] = result
        self.assertTrue(prototype.is_current(self.project))
        self.assertTrue(result['model_called'])
        self.assertEqual('interface-prototype.generate', llm.calls[0]['purpose'])
        self.assertIn('员工登记设备借用', llm.inputs[0]['requirements'])
        self.assertEqual(1, result['revision'])
        self.assertIn('应用构建后显示实际业务记录', result['html'])
        self.assertNotIn('示例客户', result['html'])

    def test_unconfirmed_or_changed_requirement_does_not_call_model(self):
        for confirmation in (None, {'sha256': 'old'}):
            self.project['requirements_confirmation'] = confirmation
            llm = FakeLLM()
            with self.assertRaisesRegex(ValueError, '确认当前版本'):
                prototype.generate(self.project, llm)
            self.assertEqual([], llm.calls)

    def test_missing_requirement_and_wrong_project_kind_do_not_call_model(self):
        projects = [dict(self.project, requirements=value) for value in (None, '', '  ')]
        projects.extend(dict(self.project, kind=kind) for kind in ('analysis', 'knowledge'))
        for project in projects:
            with self.subTest(project=project):
                llm = FakeLLM()
                with self.assertRaises(ValueError):
                    prototype.generate(project, llm)
                self.assertEqual([], llm.calls)

    def test_revision_receives_current_design_and_retains_parent_identity(self):
        old = prototype.generate(self.project, FakeLLM())
        self.project['prototype'] = old
        updated = design()
        updated['pages'][1]['fields'].append({'id': 'returnDate', 'label': '预计归还日期', 'type': 'date'})
        llm = FakeLLM([updated])
        new = prototype.generate(self.project, llm, '申请表增加预计归还日期')
        self.assertEqual(old['id'], new['previous_id'])
        self.assertEqual(2, new['revision'])
        self.assertNotEqual(old['sha256'], new['sha256'])
        self.assertEqual(old['pages'], llm.inputs[0]['current_design']['pages'])
        self.assertIn('预计归还日期', new['html'])
        self.assertNotIn('预计归还日期', old['html'])

    def test_stale_preview_or_other_project_confirmation_does_not_unlock(self):
        self.project['prototype'] = prototype.generate(self.project, FakeLLM())
        self.project['prototype_confirmation'] = {'sha256': self.project['prototype']['sha256'],
                                                  'requirements_sha256': digest(self.project['requirements'])}
        self.assertEqual(self.project['prototype'], prototype.assert_confirmed(self.project))
        self.project['requirements'] += '新增登记归还日期。'
        self.assertFalse(prototype.is_current(self.project))
        with self.assertRaisesRegex(ValueError, '界面原型'):
            prototype.assert_confirmed(self.project)
        llm = FakeLLM()
        self.project['requirements_confirmation'] = {'sha256': digest(self.project['requirements'])}
        with self.assertRaisesRegex(ValueError, '重新生成'):
            prototype.generate(self.project, llm, '修改表单')
        self.assertEqual([], llm.calls)

    def test_tampered_html_and_fields_invalidate_existing_confirmation(self):
        value = prototype.generate(self.project, FakeLLM())
        self.project['prototype'] = value
        self.project['prototype_confirmation'] = {'sha256': value['sha256'], 'requirements_sha256': value['requirements_sha256']}
        for field in ('html', 'pages'):
            modified = copy.deepcopy(value)
            if field == 'html':
                modified['html'] += '<script>alert(1)</script>'
            else:
                modified['pages'][0]['name'] = '未经确认的新页面'
            self.project['prototype'] = modified
            with self.assertRaises(ValueError):
                prototype.assert_confirmed(self.project)

    def test_identical_requirement_does_not_allow_cross_project_artifact_reuse(self):
        self.project['prototype'] = prototype.generate(self.project, FakeLLM())
        self.project['prototype_confirmation'] = {'sha256': self.project['prototype']['sha256'],
                                                  'requirements_sha256': digest(self.project['requirements'])}
        another = copy.deepcopy(self.project)
        another['id'] = 'another-independent-project'
        self.assertFalse(prototype.is_current(another))
        with self.assertRaises(ValueError):
            prototype.assert_confirmed(another)

    def test_model_code_and_business_rows_are_rejected(self):
        for name, value in [('html', '<script>fetch("https://evil.invalid")</script>'), ('script', 'alert(1)'), ('rows', [{'name': '假记录'}])]:
            invalid = design()
            invalid['pages'][0][name] = value
            with self.assertRaisesRegex(ValueError, '未支持的结构'):
                prototype.validate_design(invalid)

    def test_model_text_is_escaped_and_only_fixed_script_is_executable(self):
        value = design()
        value['summary'] = '<script>fetch("https://evil.invalid")</script>'
        value['pages'][0]['name'] = '<img src="https://evil.invalid/x" onerror="alert(1)">'
        result = prototype.render(value)
        self.assertNotIn('<img', result)
        self.assertIn('&lt;script&gt;', result)
        self.assertEqual(1, result.count('<script>'))
        self.assertIn('connect-src &#x27;none&#x27;', result)
        self.assertIn('script-src &#x27;sha256-', result)
        self.assertIn('form-action &#x27;none&#x27;', result)

    def test_shared_csp_matches_exact_script_and_meta_policy(self):
        result = prototype.render(design())
        policy = prototype.content_security_policy()
        meta = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', result).group(1)
        script = re.search(r'<script>(.*?)</script>', result, re.S).group(1)
        encoded_hash = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        self.assertEqual(policy, html.unescape(meta))
        self.assertIn("script-src 'sha256-" + encoded_hash + "'", policy)
        self.assertNotIn('sandbox', policy)
        self.assertNotIn('frame-ancestors', policy)
        self.assertNotIn("script-src 'unsafe-inline'", policy)

    def test_missing_navigation_duplicate_ids_and_invalid_field_schema_are_rejected(self):
        values = []
        value = design(); value['pages'][0]['actions'][0]['target_page'] = 'unknown'; values.append(value)
        value = design(); value['pages'].append(copy.deepcopy(value['pages'][0])); values.append(value)
        value = design(); value['pages'][1]['fields'][0]['required'] = 'false'; values.append(value)
        value = design(); value['pages'][1]['fields'][0]['type'] = 'select'; values.append(value)
        for value in values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    prototype.validate_design(value)

    def test_structural_failure_gets_one_bounded_repair_and_no_sample_fallback(self):
        invalid = {'summary': '缺少页面', 'pages': []}
        llm = FakeLLM([invalid, design()])
        result = prototype.generate(self.project, llm)
        self.assertEqual(2, len(llm.calls))
        self.assertEqual('interface-prototype.schema-repair', llm.calls[-1]['purpose'])
        self.assertEqual('借用申请', result['pages'][0]['name'])
        llm = FakeLLM([invalid, invalid])
        with self.assertRaises(ValueError):
            prototype.generate(self.project, llm)
        self.assertEqual(2, len(llm.calls))


if __name__ == '__main__':
    unittest.main()
