"""Task deadlines and cancellation at provider and publication boundaries."""
import json
import tempfile
import threading
import time
import unittest
import urllib.error
from unittest.mock import Mock, patch

from paas.jobs import Jobs
from paas.provider import LLM, TASK_TIMEOUT_SECONDS
from paas.store import Store


CONFIG = {'name': 'GLM', 'model': 'fixture',
          'endpoint': 'https://open.bigmodel.cn/fake', 'key': 'fake-key'}


class Response:
    def __init__(self, streaming=False, on_read=None, on_exit=None):
        self.headers = {'Content-Type': 'text/event-stream' if streaming else 'application/json'}
        self.on_read = on_read or (lambda: None)
        self.on_exit = on_exit or (lambda: None)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.on_exit()
        return False

    def read(self):
        self.on_read()
        return json.dumps({'choices': [{'message': {'content': 'complete answer'},
                                       'finish_reason': 'stop'}]}).encode()

    def __iter__(self):
        return iter([
            b'data: {"choices":[{"delta":{"content":"complete answer"},"finish_reason":null}]}\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n',
            b'data: [DONE]\n',
        ])


class ProviderDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.clock = 0

    def expire(self):
        self.clock = TASK_TIMEOUT_SECONDS

    def test_nonstream_response_read_cannot_publish_after_task_deadline(self):
        with patch('paas.provider.configuration', return_value=CONFIG), \
                patch('paas.provider.time.monotonic', side_effect=lambda: self.clock), \
                patch('paas.provider.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = Response(on_read=self.expire)
            llm = LLM()
            with self.assertRaisesRegex(ValueError, '时间预算'):
                llm.text('system fixture', 'question fixture')
        self.assertEqual(opener.return_value.open.call_count, 1)
        self.assertEqual(llm.calls[0]['status'], 'failed')
        self.assertNotIn('output_sha256', llm.calls[0])

    def test_stream_cleanup_cannot_publish_after_task_deadline(self):
        with patch('paas.provider.configuration', return_value=CONFIG), \
                patch('paas.provider.time.monotonic', side_effect=lambda: self.clock), \
                patch('paas.provider.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = Response(streaming=True, on_exit=self.expire)
            llm = LLM()
            with self.assertRaisesRegex(ValueError, '时间预算'):
                llm.text('system fixture', 'question fixture')
        self.assertEqual(llm.calls[0]['status'], 'failed')
        self.assertNotIn('output_sha256', llm.calls[0])

    def test_response_cleanup_cancellation_rejects_both_response_formats(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                cancel = threading.Event()
                with patch('paas.provider.configuration', return_value=CONFIG), \
                        patch('paas.provider.urllib.request.build_opener') as opener:
                    opener.return_value.open.return_value = Response(streaming=streaming, on_exit=cancel.set)
                    llm = LLM(cancel=cancel)
                    with self.assertRaisesRegex(ValueError, '取消'):
                        llm.text('system fixture', 'question fixture')
                self.assertEqual(opener.return_value.open.call_count, 1)
                self.assertEqual(llm.calls[0]['status'], 'failed')

    def test_request_timeout_uses_remaining_task_budget(self):
        with patch('paas.provider.configuration', return_value=CONFIG), \
                patch('paas.provider.time.monotonic', side_effect=lambda: self.clock), \
                patch('paas.provider.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = Response()
            llm = LLM()
            self.clock = TASK_TIMEOUT_SECONDS - 17
            self.assertEqual(llm.text('system fixture', 'question fixture'), 'complete answer')
        self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'], 17)
        self.assertEqual(llm.calls[0]['status'], 'succeeded')

    def test_transport_failure_at_deadline_cannot_start_retry(self):
        def transport(*args, **kwargs):
            self.expire()
            raise urllib.error.URLError('fixture connection failure')
        with patch('paas.provider.configuration', return_value=CONFIG), \
                patch('paas.provider.time.monotonic', side_effect=lambda: self.clock), \
                patch('paas.provider.urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = transport
            llm = LLM()
            with self.assertRaisesRegex(ValueError, '时间预算'):
                llm.text('system fixture', 'question fixture')
        self.assertEqual(opener.return_value.open.call_count, 1)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(llm.calls[0]['status'], 'failed')

    def test_json_parsing_at_deadline_cannot_return_or_start_format_repair(self):
        def parse(text):
            self.expire()
            return {'complete': True}
        with patch('paas.provider.configuration', return_value=CONFIG), \
                patch('paas.provider.time.monotonic', side_effect=lambda: self.clock), \
                patch('paas.provider.urllib.request.build_opener') as opener, \
                patch('paas.provider.parse_json', side_effect=parse):
            opener.return_value.open.return_value = Response()
            llm = LLM()
            with self.assertRaisesRegex(ValueError, '时间预算'):
                llm.json('system fixture', 'question fixture')
        self.assertEqual(opener.return_value.open.call_count, 1)


class NoNetworkLLM:
    def __init__(self, *args, **kwargs):
        self.calls = []


class JobDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='paas-deadline-')
        self.store = Store(self.temp.name)
        self.project = self.store.create('Deadline fixture', 'analysis', 'Fictional')
        self.jobs = Jobs(self.store)
        self.clock = 0

    def tearDown(self):
        self.jobs.pool.shutdown(wait=True)
        self.temp.cleanup()

    def wait(self, job_id):
        for _ in range(500):
            row = self.store.job_get(job_id)
            if row['status'] not in ('queued', 'running'):
                return row
            time.sleep(0.002)
        self.fail('Local fixture job did not finish')

    def test_work_exceeding_deadline_is_not_committed_and_releases_project(self):
        def work(project, llm):
            self.clock = TASK_TIMEOUT_SECONDS
            return 'late artifact'
        commit = Mock(side_effect=lambda project, result: project.update(requirements=result))
        with patch('paas.jobs.LLM', NoNetworkLLM), \
                patch('paas.jobs.time.monotonic', side_effect=lambda: self.clock):
            job = self.jobs.submit(self.project['id'], 'deadline fixture', work, commit)
            terminal = self.wait(job['job_id'])
        commit.assert_not_called()
        self.assertEqual(terminal['status'], 'failed')
        self.assertIn('时间预算', terminal['error'])
        current = self.store.get(self.project['id'])
        self.assertEqual(current['requirements'], '')
        self.assertIsNone(current['active_job_id'])
        self.assertEqual(current['revision'], self.project['revision'])

    def test_commit_processing_exceeding_deadline_cannot_save_project(self):
        def commit(project, result):
            project['requirements'] = result
            self.clock = TASK_TIMEOUT_SECONDS
        with patch('paas.jobs.LLM', NoNetworkLLM), \
                patch('paas.jobs.time.monotonic', side_effect=lambda: self.clock):
            job = self.jobs.submit(self.project['id'], 'late commit fixture', lambda p, llm: 'late artifact', commit)
            terminal = self.wait(job['job_id'])
        self.assertEqual(terminal['status'], 'failed')
        self.assertIn('时间预算', terminal['error'])
        current = self.store.get(self.project['id'])
        self.assertEqual(current['requirements'], '')
        self.assertIsNone(current['active_job_id'])
        self.assertEqual(current['revision'], self.project['revision'])

    def test_terminal_failure_is_written_only_after_project_unlock(self):
        observed_active_jobs = []
        original_update = self.store.job_update
        def update(job_id, **fields):
            if fields.get('status') == 'failed':
                observed_active_jobs.append(self.store.get(self.project['id'])['active_job_id'])
            return original_update(job_id, **fields)
        def fail(project, llm):
            raise ValueError('fixture failure')
        with patch('paas.jobs.LLM', NoNetworkLLM), \
                patch.object(self.store, 'job_update', side_effect=update):
            job = self.jobs.submit(self.project['id'], 'failure ordering fixture', fail, Mock())
            terminal = self.wait(job['job_id'])
        self.assertEqual(terminal['status'], 'failed')
        self.assertEqual(observed_active_jobs, [None])


if __name__ == '__main__':
    unittest.main()
