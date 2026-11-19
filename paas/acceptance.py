"""Release acceptance bound to the candidate's models and executable files."""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3

from . import business
from .store import Conflict, digest


# Same exclusions for acceptance, copying and verification of the release.
RELEASE_EXCLUSIONS = ('data', 'logs', 'jobs', 'ai-runs', '__pycache__', 'node_modules',
                      '*.db', '*.sqlite', '*-wal', '*-shm', '.env', '.env.paas',
                      'initial-credentials.json', '.session-secret', 'session-secret')
CONTRACT_AUTHORITY_FIELDS = {
    'sys_user': 'id, username, password, actor_type, department_id, status',
    'sys_role': 'id, code, parent_id, status',
    'sys_permission': 'id, code, target_type, target_ref, data_scope, abac_condition, status',
    'sys_resource': 'id, parent_id, code, permission_code, type, path, component, http_method, status',
    'sys_user_role': 'user_id, role_id',
    'sys_role_permission': 'role_id, permission_id',
    'sys_role_resource': 'role_id, resource_id',
}


def plan(project):
    build = project.get('build') or {}
    if build.get('status') != 'built':
        return None
    models = project['models']
    behaviors = business.model_document(models, 'BEHAVIOR').get('behaviors', [])
    flows = business.model_document(models, 'FLOW').get('flows', [])
    reports = business.model_document(models, 'REPORT').get('query_reports', [])
    rules = business.model_document(models, 'RULE').get('rules', [])
    screens = business.model_document(models, 'UI').get('screens', [])
    checks = [{'id': 'roles', 'label': '角色权限与隔离', 'detail': '验证授权角色可访问，未授权角色被拒绝。'}]
    if screens:
        checks.append({'id': 'ui', 'label': '原型与页面操作',
                       'detail': '逐页核对菜单、表单、控件和对应业务操作。'})
    if any(item.get('behaviorType') != 'QUERY' for item in behaviors):
        checks.append({'id': 'write', 'label': '录入与保存', 'detail': '操作后核对记录值、状态与重新打开后的结果。'})
    if flows:
        for flow in flows:
            checks.append({'id': 'flow:' + flow['id'], 'label': flow.get('name') or flow['id'],
                           'detail': '按模型的角色与路径走通该流程，记录草稿、提交、通过及适用的驳回重提、退回和撤回。'})
    for report in reports:
        checks.append({'id': 'report:' + report['id'], 'label': report.get('name') or report['id'],
                       'detail': '核对筛选、权限和报告结果与实际业务记录一致。'})
    objects = business.model_document(models, 'OBJECT').get('aggregates', [])
    if rules or any(item.get('invariants') or any(field.get('refRules') for field in item.get('attributes', []))
                    for item in objects):
        checks.append({'id': 'rules', 'label': '规则与联动',
                       'detail': '记录一个有效用例和一个违反规则的用例，检查提示、状态及联动结果。'})
    key = digest({key: build.get(key) for key in ('output_dir', 'model_hash', 'models_sha256',
                                                 'requirements_sha256', 'prototype_sha256')})
    return {'id': key, 'checks': checks}


def read_checks(plan_value, submitted):
    if not isinstance(submitted, dict) or set(submitted) != {item['id'] for item in plan_value['checks']}:
        raise ValueError('请逐项提交当前候选的验收结果')
    evidence = []
    for check in plan_value['checks']:
        result = submitted[check['id']]
        if not isinstance(result, dict) or result.get('passed') is not True:
            raise ValueError(f"候选尚未通过「{check['label']}」")
        note = result.get('result')
        if not isinstance(note, str) or not 4 <= len(note.strip()) <= 1200:
            raise ValueError(f"请记录「{check['label']}」的实际用例和结果，4 至 1200 字")
        evidence.append(check | {'result': note.strip(), 'status': 'passed'})
    return evidence


def candidate_paths(build, project_root):
    root = Path(build.get('output_dir', '')).resolve()
    app_dir = Path(build.get('app_dir') or root / 'code-app').resolve()
    database = Path(build.get('database_path', '')).resolve()
    if root == project_root / 'builds' or not root.is_dir() or not root.is_relative_to(project_root / 'builds'):
        raise ValueError('候选构建不在本项目独立构建目录')
    if not app_dir.is_dir() or not app_dir.is_relative_to(root):
        raise ValueError('候选应用目录无效')
    if not database.is_file() or not database.is_relative_to(app_dir):
        raise ValueError('候选业务数据库无效')
    return root, app_dir, database


def release_files(root, *, excluded=()):
    import shutil
    ignore = shutil.ignore_patterns(*RELEASE_EXCLUSIONS)
    files = {}
    def visit(directory):
        entries = sorted(directory.iterdir())
        omitted = ignore(directory, [file.name for file in entries])
        for file in entries:
            if file.is_symlink():
                raise ValueError('候选构建含符号链接，不能验收或发布')
            if file.name in omitted or file in excluded:
                continue
            if file.is_dir():
                visit(file)
            elif file.is_file():
                with file.open('rb') as stream:
                    files[file.relative_to(root).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
            else:
                raise ValueError('候选目录存在无法发布的文件类型')
    visit(root)
    return files


def database_fingerprint(build, database):
    """Bind the candidate to its SQLite schema and effective authority state."""
    try:
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
            db.execute('PRAGMA trusted_schema = OFF')
            db.execute('BEGIN')
            integrity = db.execute('PRAGMA quick_check(1)').fetchone()
            schema = db.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_schema "
                "WHERE substr(name, 1, 7) <> 'sqlite_' ORDER BY type, name"
            ).fetchall()
            tables = {name for kind, name, _, _ in schema if kind == 'table'}
            if integrity != ('ok',) or not build.get('tables') or not set(build['tables']) <= tables:
                raise ValueError('候选 SQLite 完整性或业务表检查失败')
            if build.get('runtime_kind') == 'contract-reference-v9':
                version = db.execute('SELECT model_hash FROM onto_contract_release').fetchall()
                if version != [(build['model_hash'],)]:
                    raise ValueError('候选合同数据库与当前运行模型不匹配')
                authority = {
                    table: sorted(digest(row) for row in db.execute(f'SELECT {columns} FROM {table}'))
                    for table, columns in CONTRACT_AUTHORITY_FIELDS.items()
                }
            else:
                authority = {}
                if 'runtime_migrations' in tables or build.get('migration'):
                    version = db.execute('SELECT version, model_hash FROM runtime_migrations ORDER BY version').fetchall()
                    if not version or any(model_hash != build['model_hash'] for _, model_hash in version):
                        raise ValueError('候选数据库迁移与当前运行模型不匹配')
                    authority['migrations'] = version
    except sqlite3.Error as error:
        raise ValueError('候选 SQLite 无法读取或完整性检查失败') from error
    return digest({'schema': schema, 'authority': authority})


def smoke(app, project, database):
    """Check entry and roles after the caller validates the SQLite fingerprint."""
    build = project['build']
    base_url = 'http://localhost'
    mount = f"/app/{project['id']}/"
    contract = build.get('runtime_kind') == 'contract-reference-v9'
    accounts = build.get('accounts') or []
    if not accounts or app.test_client().get(mount, base_url=base_url).status_code != 200:
        raise ValueError('候选应用入口或初始角色账号不可用')
    role_ids = []
    for account in accounts:
        client = app.test_client()
        credentials = {key: account[key] for key in ('username', 'password')}
        response = client.post(mount + ('api/auth/login' if contract else 'api/login'),
                               base_url=base_url, json=credentials)
        if response.status_code != 200:
            raise ValueError('候选角色登录失败：' + account['role_id'])
        if contract:
            token = (response.json.get('data') or {}).get('token')
            if not token:
                raise ValueError('候选合同角色登录未返回会话：' + account['role_id'])
            principal = client.get(mount + 'api/auth/info', base_url=base_url,
                                   headers={'Authorization': f'Bearer {token}'})
            identity = principal.get_json(silent=True) or {}
            actor = (identity.get('data') or {}).get('user') or {}
            if (principal.status_code != 200 or not identity.get('success')
                    or actor.get('username') != account['username']
                    or account['role_id'] not in actor.get('roles', [])):
                raise ValueError('候选合同角色会话验证失败：' + account['role_id'])
        else:
            metadata = client.get(mount + 'api/meta', base_url=base_url)
            actor = (metadata.json or {}).get('actor') or {}
            if (metadata.status_code != 200 or account['role_id'] not in actor.get('roles', [])
                    or (metadata.json or {}).get('model_hash') != build['model_hash']):
                raise ValueError('候选角色权限或运行模型校验失败：' + account['role_id'])
        role_ids.append(account['role_id'])
    return {'runtime_entry': 'passed', 'sqlite': 'passed', 'roles': sorted(role_ids),
            'business_tables': sorted(build['tables'])}


def require_current(project, files):
    current = plan(project)
    record = project['build'].get('acceptance') or {}
    if (not current or record.get('candidate_id') != current['id']
            or record.get('code_sha256') != digest(files)):
        raise Conflict('请先对当前候选代码和模型完成逐项业务验收')
    return record
