"""Atomic local project and job persistence for the platform workspace."""
from __future__ import annotations
import copy
import hashlib
import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from .provider import public_reply_purpose, sanitize_public_reply


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.lock = threading.RLock()
        for kind in ('projects', 'jobs'):
            (self.root / kind).mkdir(exist_ok=True)
        # An interrupted model/build job is never resumed silently after restart.
        for file in (self.root / 'jobs').glob('*.json'):
            row = json.loads(file.read_text())
            if row['status'] in ('queued', 'running'):
                row.update(status='failed', error='服务重启中断了任务，请检查已有产物后重试', finished_at=time.time())
                self.write(file, row)
                try:
                    def release_interrupted(project):
                        if project.get('active_job_id') == row['id']:
                            project.update(active_job_id=None, last_error=row['error'])
                    self.mutate(row['project_id'], release_interrupted, bump=False)
                except (ValueError, FileNotFoundError):
                    pass

    def write(self, file, data):
        file = Path(file)
        file.parent.mkdir(parents=True, exist_ok=True)
        temp = file.with_name(file.name + '.tmp-' + uuid.uuid4().hex)
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        temp.chmod(0o600)
        temp.replace(file)

    def directory(self, project_id):
        if not re.fullmatch(r'[a-f0-9]{32}', project_id):
            raise ValueError('项目标识无效')
        return self.root / 'projects' / project_id

    def create(self, name, kind, description, *, prepare_source=None, initialize=None):
        if kind not in ('business', 'analysis', 'knowledge'):
            raise ValueError('项目类型无效')
        if not name.strip():
            raise ValueError('请填写项目名称')
        row = {'id': uuid.uuid4().hex, 'name': name.strip()[:100], 'kind': kind,
               'description': description.strip()[:30000], 'revision': 1, 'stage': 0,
               'created_at': time.time(), 'updated_at': time.time(), 'messages': [],
               'confirmations': [], 'requirements': '', 'models': {}, 'runs': [],
               'source': None, 'build': None, 'active_job_id': None}
        directory = self.directory(row['id'])
        if prepare_source is None and initialize is None:
            self.write(directory / 'project.json', row)
            return row
        directory.mkdir(mode=0o700)
        try:
            if prepare_source is not None:
                row['source'] = prepare_source(row['id'])
            if initialize is not None:
                initialize(row)
            self.write(directory / 'project.json', row)
        except Exception:
            shutil.rmtree(directory)
            raise
        return row

    def get(self, project_id):
        file = self.directory(project_id) / 'project.json'
        if not file.is_file():
            raise FileNotFoundError('项目不存在')
        return json.loads(file.read_text())

    def list(self):
        with self.lock:
            return sorted([json.loads(p.read_text()) for p in (self.root / 'projects').glob('*/project.json')],
                          key=lambda p: p['updated_at'], reverse=True)

    def mutate(self, project_id, operation, expected_revision=None, bump=True):
        with self.lock:
            row = self.get(project_id)
            if expected_revision is not None and row['revision'] != expected_revision:
                raise Conflict('项目已修改，请刷新后重新确认当前版本')
            operation(row)
            if bump:
                row['revision'] += 1
            row['updated_at'] = time.time()
            self.write(self.directory(project_id) / 'project.json', row)
            return copy.deepcopy(row)

    def job_get(self, job_id):
        if not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise ValueError('任务标识无效')
        file = self.root / 'jobs' / (job_id + '.json')
        if not file.is_file():
            raise FileNotFoundError('任务不存在')
        row = json.loads(file.read_text())
        if row.get('status') in ('queued', 'running'):
            progress = self.directory(row['project_id']) / 'jobs' / job_id / 'progress.json'
            try:
                if progress.is_file() and progress.stat().st_size < 10000:
                    value = json.loads(progress.read_text())
                    if isinstance(value, dict):
                        safe = {key: value[key] for key in ('purpose', 'characters', 'elapsed_seconds', 'model') if key in value}
                        if (isinstance(safe.get('characters'), int) and safe['characters'] >= 0
                                and isinstance(safe.get('elapsed_seconds'), (int, float))):
                            if (row.get('kind') == '需求探索' and row['status'] == 'running'
                                    and not row.get('cancel_requested') and public_reply_purpose(safe.get('purpose'))):
                                reply = sanitize_public_reply(value.get('public_reply'))
                                if reply:
                                    safe['public_reply'] = reply
                            row['progress'] = safe
            except (OSError, ValueError, TypeError):
                # Ignore unreadable progress; it is never part of persisted job state.
                pass
        return row

    def job_update(self, job_id, **fields):
        with self.lock:
            row = self.job_get(job_id)
            row.pop('progress', None)
            row.update(fields)
            self.write(self.root / 'jobs' / (job_id + '.json'), row)
            return row
