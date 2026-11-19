from __future__ import annotations
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from .provider import LLM, TASK_TIMEOUT_SECONDS
from .store import Conflict


class Jobs:
    def __init__(self, store):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='paas')
        self.cancels = {}

    def submit(self, project_id, kind, work, commit, expected_revision=None, *, reasoning_turn=None):
        with self.store.lock:
            project = self.store.get(project_id)
            if expected_revision is not None and project['revision'] != expected_revision:
                raise Conflict('项目已修改，请刷新后重新执行任务')
            if project.get('active_job_id'):
                raise Conflict('当前项目已有执行中的任务，请等待或取消')
            job_id = uuid.uuid4().hex
            row = {'id': job_id, 'project_id': project_id, 'kind': kind,
                   'revision': project['revision'], 'status': 'queued',
                   'created_at': time.time(), 'events': [], 'result': None, 'error': None}
            if reasoning_turn:
                row.update(reasoning_session_id=reasoning_turn['reasoning_session_id'],
                           question=reasoning_turn['question'])
            self.store.write(self.store.root / 'jobs' / (job_id + '.json'), row)
            self.store.mutate(project_id, lambda p: p.update(active_job_id=job_id), bump=False)
            cancel = threading.Event()
            self.cancels[job_id] = cancel
            self.pool.submit(self._run, row, project, work, commit, cancel)
            return {'job_id': job_id, 'status': 'queued'}

    def _run(self, row, project, work, commit, cancel):
        job_id, project_id = row['id'], row['project_id']
        trace_dir = self.store.directory(project_id) / 'jobs' / job_id
        deadline = time.monotonic() + TASK_TIMEOUT_SECONDS
        llm = None
        try:
            if cancel.is_set():
                raise ValueError('任务已取消，未执行任务')
            llm = LLM(trace_dir=trace_dir, cancel=cancel)
            self.store.job_update(job_id, status='running', started_at=time.time(),
                                  events=[{'message': '正在执行 ' + row['kind'], 'at': time.time()}])
            result = work(project, llm)
            def apply(current):
                if cancel.is_set():
                    raise ValueError('任务已取消，未提交产物')
                if time.monotonic() >= deadline:
                    raise ValueError('本次任务已达到时间预算，未提交产物')
                if current.get('active_job_id') != job_id:
                    raise Conflict('任务已失效，未覆盖当前项目')
                commit(current, result)
                if time.monotonic() >= deadline:
                    raise ValueError('本次任务已达到时间预算，未提交产物')
                current['active_job_id'] = None
            # Cancellation and successful publication share one lock. A request
            # cannot be acknowledged as cancelled while its result is committed.
            with self.store.lock:
                self.store.mutate(project_id, apply, expected_revision=row['revision'])
                self.store.job_update(job_id, status='succeeded', result=result, calls=llm.calls,
                                      finished_at=time.time(), model_called=bool(llm.calls),
                                      events=[{'message': '执行完成，产物已持久保存', 'at': time.time()}])
        except Exception as error:
            trace_dir.mkdir(parents=True, exist_ok=True)
            trace_file = trace_dir / 'error.txt'
            trace_file.write_text(traceback.format_exc())
            trace_file.chmod(0o600)
            calls = llm.calls if llm else []
            def release(current):
                if current.get('active_job_id') == job_id:
                    current['active_job_id'] = None
                    current['last_error'] = str(error)[:3000]
            with self.store.lock:
                try:
                    self.store.mutate(project_id, release, bump=False)
                except FileNotFoundError:
                    pass
                # A terminal job must never remain the project's active job
                # when the UI observes it and immediately retries.
                self.store.job_update(job_id, status='cancelled' if cancel.is_set() else 'failed',
                                      error=str(error)[:3000], calls=calls,
                                      model_called=bool(calls), finished_at=time.time())
        finally:
            self.cancels.pop(job_id, None)

    def cancel(self, job_id):
        with self.store.lock:
            row = self.store.job_get(job_id)
            if row['status'] not in ('queued', 'running'):
                return row
            cancel = self.cancels.get(job_id)
            if cancel:
                cancel.set()
            self.store.job_update(job_id, cancel_requested=True)
            return self.store.job_get(job_id)
