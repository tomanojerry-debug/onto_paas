import itertools
import json
import os
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from paas.provider import LLM, _public_reply_preview, configuration, parse_json
from paas.store import Store


class Stream:
    headers = {'Content-Type': 'text/event-stream'}
    def __init__(self, text, complete=True):
        self.lines = [b'data: ' + json.dumps({'choices':[{'delta':{'content':text},'finish_reason':None}]}).encode() + b'\n']
        if complete:
            self.lines.extend([b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"total_tokens":9}}\n',b'data: [DONE]\n'])
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False
    def __iter__(self):
        return iter(self.lines)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.config={'name':'GLM','model':'glm-5.3-flash','endpoint':'https://open.bigmodel.cn/api/coding/paas/v4/chat/completions','key':'private-key-sentinel'}

    def test_config_ignores_unrelated_api_key_in_environment(self):
        with tempfile.TemporaryDirectory() as temp, patch('paas.provider.PROJECT_ROOT', Path(temp)):
            with patch.dict(os.environ, {'AI_API_KEY': 'unrelated-placeholder'}, clear=True):
                self.assertEqual(configuration()['key'], '')
            (Path(temp) / '.env.paas').write_text('PAAS_API_KEY=local-placeholder\n')
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(configuration()['key'], 'local-placeholder')
            with patch.dict(os.environ, {'PAAS_API_KEY': 'env-placeholder'}, clear=True):
                self.assertEqual(configuration()['key'], 'env-placeholder')

    def test_explicit_profile_loads_only_named_credential(self):
        with tempfile.TemporaryDirectory() as temp, patch('paas.provider.PROJECT_ROOT', Path(temp)):
            config_dir = Path(temp) / 'config'
            config_dir.mkdir()
            profile = config_dir / 'provider.json'
            profile.write_text(json.dumps({'credential_source': 'secrets.env', 'key_name': 'SHARED_KEY'}))
            (config_dir / 'secrets.env').write_text(
                'SHARED_KEY=local-placeholder\nPAAS_MODEL=unrelated-model\nPAAS_BASE_URL=https://not-the-provider.invalid\n')
            with patch.dict(os.environ, {'PAAS_PROVIDER_CONFIG': str(profile)}, clear=True):
                config = configuration()
            self.assertEqual(config['key'], 'local-placeholder')
            self.assertEqual(config['model'], 'glm-5.3-flash')
            self.assertTrue(config['endpoint'].startswith('https://open.bigmodel.cn/'))

    def test_streamed_json_records_usage_without_credentials(self):
        with tempfile.TemporaryDirectory() as temp, patch('paas.provider.configuration',return_value=self.config), patch('paas.provider.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=Stream('{"generated":true}')
            llm=LLM(trace_dir=temp)
            self.assertEqual(llm.json('system','fictional material',purpose='test'),{'generated':True})
            self.assertEqual(llm.calls[0]['usage']['total_tokens'],9)
            request=opener.return_value.open.call_args.args[0]
            payload=json.loads(request.data)
            self.assertTrue(payload['stream'])
            self.assertEqual(payload['model'],'glm-5.3-flash')
            self.assertEqual(payload['thinking'], {'type': 'enabled'})
            self.assertEqual(payload['reasoning_effort'], 'low')
            for file in Path(temp).iterdir():
                self.assertNotIn('private-key-sentinel',file.read_text())

    def test_public_reply_parser_handles_partial_strings_and_prefers_safe_prefix(self):
        cases = [
            ('{"draft":"没有公开回复","reply":"晚到"}', ''),
            ('```json\n{"reply":"有代码围栏', ''),
            ('{"reply":false}', ''),
            ('{"reply":"公开\\', '公开'),
            ('{"reply":"公开\\u4e', '公开'),
            ('{"reply":"公开\\u4e0b', '公开下'),
            ('{"reply":"公开\\ud83d', '公开'),
            ('{"reply":"公开\\ud83d\\ude03', '公开😃'),
            ('{"reply":"公开\\q', ''),
            ('{"reply":"公开\n', ''),
            ('{"reply":"\\"引用\\"\\n公开","draft":"内部"}', '"引用"\n公开'),
            ('{"reply":"公开<th', '公开'),
            ('{"reply":"公开\\u003c/script>脚本"}', '公开'),
            ('{"reply":"' + '字' * 1100, '字' * 1000),
        ]
        for source, expected in cases:
            with self.subTest(source=source[:65]):
                self.assertEqual(_public_reply_preview(source), expected)

    def test_adaptive_reply_progress_tracks_sse_content_before_completion(self):
        with tempfile.TemporaryDirectory() as temp, patch('paas.provider.configuration', return_value=self.config), \
                patch('paas.provider.urllib.request.build_opener') as opener, \
                patch('paas.provider.time.monotonic', side_effect=itertools.count(1)):
            progress_file = Path(temp) / 'progress.json'
            stream = Stream('')

            def chunks():
                pieces = [('{"reply":"请确认', '请确认'), ('\\u4e', '请确认'),
                          ('0b\\n是否范围内', '请确认下\n是否范围内'),
                          ('\\u003cscript>bad","draft":"不要发布"}', '请确认下\n是否范围内')]
                for source, expected in pieces:
                    delta = {'content': source, 'reasoning_content': '私有推理'}
                    yield b'data: ' + json.dumps({'choices': [{'delta': delta}]}).encode() + b'\n'
                    safe = json.loads(progress_file.read_text())
                    self.assertEqual(safe['public_reply'], expected)
                    self.assertNotIn('reasoning_content', safe)
                    self.assertNotIn('draft', safe)
                yield b'data: [DONE]\n'

            stream.lines = chunks()
            opener.return_value.open.return_value = stream
            result = LLM(trace_dir=temp).json('system', 'user', purpose='business.prd.explore')
            self.assertEqual(result['reply'], '请确认下\n是否范围内<script>bad')
            self.assertEqual(result['draft'], '不要发布')

    def test_reply_preview_resets_on_retry_and_is_absent_on_later_purposes(self):
        with tempfile.TemporaryDirectory() as temp, patch('paas.provider.configuration', return_value=self.config), \
                patch('paas.provider.urllib.request.build_opener') as opener:
            progress_file = Path(temp) / 'progress.json'
            retry = 'business.prd.explore.contract-repair.transport-retry'
            first = Stream('{"reply":"旧片段', complete=False)
            second = Stream('{"reply":"新回复"}')
            unrelated = Stream('{"reply":"另一个任务"}')
            attempts = iter([(retry.removesuffix('.transport-retry'), first), (retry, second),
                             ('business.prd.complete', unrelated)])

            def open_attempt(*_args, **_kwargs):
                expected_purpose, stream = next(attempts)
                progress = json.loads(progress_file.read_text())
                self.assertEqual(progress['purpose'], expected_purpose)
                self.assertEqual(progress['characters'], 0)
                self.assertNotIn('public_reply', progress)
                return stream

            opener.return_value.open.side_effect = open_attempt
            llm = LLM(trace_dir=temp)
            llm.json('system', 'user', purpose=retry.removesuffix('.transport-retry'))
            self.assertEqual(json.loads(progress_file.read_text())['public_reply'], '新回复')
            llm.text('system', 'user', purpose='business.prd.complete')
            self.assertNotIn('public_reply', json.loads(progress_file.read_text()))

    def test_job_store_exposes_public_reply_only_for_active_adaptive_discovery(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            project_id, job_id = '1' * 32, '2' * 32
            job_file = store.root / 'jobs' / (job_id + '.json')
            progress_file = store.directory(project_id) / 'jobs' / job_id / 'progress.json'
            progress = {'purpose': 'business.prd.explore', 'characters': 12, 'elapsed_seconds': 1,
                        'public_reply': '公开<危险', 'reasoning_content': '私有推理'}
            job = {'id': job_id, 'project_id': project_id, 'kind': '需求探索', 'status': 'running'}
            store.write(progress_file, progress)
            store.write(job_file, job)
            visible = store.job_get(job_id)['progress']
            self.assertEqual(visible['public_reply'], '公开')
            self.assertNotIn('reasoning_content', visible)
            for fields in ({'kind': '生成需求文档'}, {'status': 'queued'}, {'cancel_requested': True},
                           {'status': 'succeeded'}, {'status': 'failed'}):
                store.write(job_file, job | fields)
                self.assertNotIn('public_reply', store.job_get(job_id).get('progress', {}))
            store.write(job_file, job)
            for purpose in ('business.prd.complete', 'business.prd.explore.format-repair'):
                store.write(progress_file, progress | {'purpose': purpose})
                self.assertNotIn('public_reply', store.job_get(job_id)['progress'])

    def test_requirements_deep_mode_changes_effort_and_preserves_thinking(self):
        with patch('paas.provider.configuration', return_value=self.config), patch('paas.provider.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = Stream('ok')
            llm = LLM()
            llm.reasoning_effort = 'high'
            self.assertEqual(llm.text('s', 'u'), 'ok')
            payload = json.loads(opener.return_value.open.call_args.args[0].data)
            self.assertEqual(payload['thinking'], {'type': 'enabled'})
            self.assertEqual(payload['reasoning_effort'], 'high')
            self.assertEqual(llm.calls[0]['reasoning_effort'], 'high')

    def test_native_transport_retry_is_bounded_and_both_attempts_are_recorded(self):
        with patch('paas.provider.configuration',return_value=self.config),patch('paas.provider.urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect=[urllib.error.URLError('transient'),Stream('ok')]
            llm=LLM()
            self.assertEqual(llm.text('s','u'),'ok')
            self.assertEqual([c['status'] for c in llm.calls],['failed','succeeded'])
            self.assertEqual(opener.return_value.open.call_count,2)

    def test_incomplete_stream_never_publishes_partial_content(self):
        with patch('paas.provider.configuration',return_value=self.config),patch('paas.provider.urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect=[Stream('partial',False),Stream('partial',False)]
            llm=LLM()
            with self.assertRaises(ValueError):
                llm.text('s','u')
            self.assertEqual([c['status'] for c in llm.calls],['failed','failed'])

    def test_authentication_failure_does_not_retry_or_change_provider(self):
        with patch('paas.provider.configuration',return_value=self.config),patch('paas.provider.urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect=urllib.error.HTTPError(self.config['endpoint'],401,'unauthorized',{},None)
            llm=LLM()
            with self.assertRaisesRegex(ValueError,'HTTP 401'):
                llm.text('s','u')
            self.assertEqual(len(llm.calls),1)
            self.assertEqual(llm.calls[0]['model'],'glm-5.3-flash')

    def test_cancelled_job_does_not_start_provider_request(self):
        cancel=threading.Event();cancel.set()
        with patch('paas.provider.configuration',return_value=self.config),patch('paas.provider.urllib.request.build_opener') as opener:
            with self.assertRaisesRegex(ValueError,'取消'):
                LLM(cancel=cancel).text('s','u')
            opener.assert_not_called()

    def test_json_parser_preserves_literal_json_filenames(self):
        self.assertEqual(parse_json('```json\n{"file":"train.jsonl"}\n```'),{'file':'train.jsonl'})

    def test_model_format_repair_can_include_prose_after_a_single_json_block(self):
        payload = '```json\n{"reply":"请确认","ready_for_confirmation":false}\n```\n已修复引号。'
        self.assertEqual(parse_json(payload), {'reply': '请确认', 'ready_for_confirmation': False})


if __name__=='__main__':
    unittest.main()
