"""Mount the pinned author's V9 contract runtime in one isolated build or release."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import uuid
from contextlib import closing
from pathlib import Path


class BusinessError(ValueError):
    pass


class AccessDenied(PermissionError):
    pass


_runtime_lock = threading.RLock()
_packages = {}
_apps = {}
_PROFILE = 'contract-reference-v9'
_ROLES = {
    'ROLE-ADMIN': 'admin', 'ROLE-SALES': 'sales', 'ROLE-FINANCE': 'finance',
    'ROLE-FINANCE-MANAGER': 'finmgr', 'ROLE-GENERAL-MANAGER': 'gm', 'ROLE-EMPLOYEE': 'emp',
}
_BLUEPRINTS = ('auth', 'meta', 'users', 'roles', 'permissions', 'resources', 'flow',
               'workbench', 'master_data', 'contract', 'invoice', 'receipt', 'report')
_REPORTS = {
    '/api/report/execution': 'PERM-CONTRACT-QUERY-EXECUTION',
    '/api/report/dept': 'PERM-CONTRACT-QUERY-DEPT-SUMMARY',
    '/api/report/unreceived': 'PERM-CONTRACT-QUERY-UNRECEIVED',
}


def _auth_secret(path):
    if path.is_symlink():
        raise BusinessError('合同运行时密钥路径不可为链接')
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        secret = path.read_text()
    else:
        with os.fdopen(descriptor, 'w') as stream:
            secret = secrets.token_hex(32)
            stream.write(secret)
    if not re.fullmatch('[0-9a-f]{64}', secret):
        raise BusinessError('合同运行时密钥无效')
    return secret


def _prepare_paths(directory):
    data = directory / 'data'
    if data.is_symlink():
        raise BusinessError('合同运行数据目录不可为链接')
    data.mkdir(mode=0o700, exist_ok=True)
    database = data / 'application.db'
    if database.is_symlink():
        raise BusinessError('合同数据库路径不可为链接')
    return data, database


def _load_package(directory):
    package_root = directory / 'upstream-techbase/contract_backend'
    namespace = '_onto_contract_' + hashlib.sha256(str(directory).encode()).hexdigest()
    with _runtime_lock:
        existing = _packages.get(namespace)
        if existing:
            return existing
        data, database = _prepare_paths(directory)
        descriptor = importlib.util.spec_from_file_location(
            namespace, package_root / '__init__.py', submodule_search_locations=[str(package_root)])
        if not descriptor or not descriptor.loader:
            raise BusinessError('无法加载合同执行包')
        package = importlib.util.module_from_spec(descriptor)
        sys.modules[namespace] = package
        try:
            descriptor.loader.exec_module(package)
            settings = importlib.import_module(namespace + '.config.settings')
            settings.config = {
                'models_dir': str(directory / 'upstream-techbase/contract_runtime_models'),
                'auth.jwt_secret': _auth_secret(data / 'contract-auth-secret'),
                'auth.jwt_expires_hours': 2,
            }
            db = importlib.import_module(namespace + '.db')
            db.init_db_path(str(database))
            registry = importlib.import_module(namespace + '.ontology.registry')
            registry.load_ontology()
        except (ImportError, OSError, ValueError) as error:
            for name in list(sys.modules):
                if name == namespace or name.startswith(namespace + '.'):
                    sys.modules.pop(name, None)
            raise BusinessError('合同执行包无法初始化，请确认运行依赖：' + str(error)) from None
        _packages[namespace] = (namespace, db, registry)
        return _packages[namespace]


def initialize_database(directory):
    """Initialize fresh runtime tables, roles and random users. Seed no business data."""
    directory = Path(directory).resolve()
    _, database = _prepare_paths(directory)
    if database.exists():
        raise BusinessError('合同应用初始化必须使用空库')
    namespace, db, registry = _load_package(directory)
    seed = importlib.import_module(namespace + '.seed')
    security = importlib.import_module(namespace + '.utils.security')
    ddl = (directory / 'migrations/001_initial.sql').read_text()
    connection = db.connect()
    try:
        connection.executescript(ddl)
        connection.execute('BEGIN')
        seed._seed_roles_permissions(connection)
        seed._seed_resources(connection)
        accounts = []
        for role_code, username in _ROLES.items():
            role = registry.registry['roles'][role_code]
            part = db.query_one('SELECT id FROM sys_role WHERE code=?', (role_code,), connection)
            password = secrets.token_urlsafe(24)
            user_id = db.execute(
                "INSERT INTO sys_user (username,password,real_name,actor_type,status) VALUES (?,?,?,'HUMAN',1)",
                (username, security.hash_password(password), role['name']), connection)[0]
            db.execute('INSERT INTO sys_user_role (user_id,role_id) VALUES (?,?)', (user_id, part['id']), connection)
            accounts.append({'username': username, 'password': password, 'role': role['name'], 'role_id': role_code})
        seed._ensure_flow_definition(connection)
        connection.execute('CREATE TABLE onto_contract_release (model_hash TEXT NOT NULL)')
        model_hash = json.loads((directory / 'runtime.json').read_text())['model_hash']
        connection.execute('INSERT INTO onto_contract_release (model_hash) VALUES (?)', (model_hash,))
        connection.commit()
        os.chmod(database, 0o600)
        return accounts
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _runtime_app(directory):
    from flask import Flask, Response, g, jsonify, request, send_from_directory
    from werkzeug.datastructures import ImmutableMultiDict
    from werkzeug.exceptions import HTTPException

    namespace, db, _ = _load_package(directory)
    app = Flask(namespace, static_folder=None)
    app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024
    auth = importlib.import_module(namespace + '.services.auth_service')
    security = importlib.import_module(namespace + '.utils.security')
    reports = importlib.import_module(namespace + '.services.report_service')
    contracts = importlib.import_module(namespace + '.services.contract_service')
    write_lock = threading.RLock()

    for name in _BLUEPRINTS:
        app.register_blueprint(importlib.import_module(namespace + '.api.' + name).bp)

    def fail(message, status):
        return jsonify(success=False, message=message, data=None, errorCode='CONTRACT_RUNTIME'), status

    def scope(code, contract_id=None):
        if '*' in g.grants or code in g.grants:
            return None
        department = g.user['department_id']
        if code + '-OWN-DEPT' not in g.grants or department is None:
            raise AccessDenied('当前角色没有此报表或记录权限')
        if contract_id is not None:
            actual = db.query_one('SELECT department_id FROM contract WHERE id=? AND flag=1', (contract_id,))
            if not actual or actual['department_id'] != department:
                raise AccessDenied('当前角色只可查看本部门合同')
        return department

    def narrow_department(department):
        if department is not None:
            if request.args.get('department_id') not in (None, '', str(department)):
                raise AccessDenied('当前角色只可查看本部门合同')
            query = [(k, v) for k, v in request.args.items(multi=True) if k != 'department_id']
            request.args = ImmutableMultiDict([*query, ('department_id', str(department))])

    def verify_permission(code):
        if '*' not in g.grants and code not in g.grants:
            raise AccessDenied('当前角色无权执行该操作')

    @app.before_request
    def guard_api():
        if not request.path.startswith('/api/') or request.path == '/api/auth/login':
            return None
        header = request.headers.get('Authorization', '')
        try:
            payload = security.decode_token(header[7:]) if header.startswith('Bearer ') else {}
            g.user = auth.get_user(int(payload['sub']))
        except Exception:
            return fail('登录已失效，请重新登录', 401)
        if not g.user or g.user['status'] != 1:
            return fail('账号不存在或已停用', 401)
        g.grants = auth.get_permission_codes(g.user['id'])
        path = request.path
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            write_lock.acquire()
            g.contract_write_lock = True
        if path in _REPORTS:
            narrow_department(scope(_REPORTS[path]))
        elif path == '/api/contract/options':
            department = scope('PERM-CONTRACT-QUERY-LIST')
            if department is not None:
                rows = contracts.list_contracts(1, 100, {'status': '已纳入管理', 'department_id': department})
                return jsonify(success=True, data=[
                    {'id': row['id'], 'no': row['contract_no'], 'name': row['contract_name'],
                     'total_amount': row['total_amount']} for row in rows['list']], message='ok')
        elif path == '/api/contract' and request.method == 'GET':
            narrow_department(scope('PERM-CONTRACT-QUERY-LIST'))
        elif request.method == 'GET' and (match := re.fullmatch(r'/api/contract/(\d+)', path)):
            scope('PERM-CONTRACT-QUERY-DETAIL', int(match[1]))
        elif request.method == 'GET' and path.startswith(('/api/invoice', '/api/receipt')):
            if scope('PERM-CONTRACT-QUERY-DETAIL') is not None:
                raise AccessDenied('此明细入口仅对全范围合同查询角色开放')
        if path.startswith(('/api/users', '/api/roles', '/api/permissions', '/api/resources')):
            verify_permission('system:manage')
        if path.startswith('/api/flow'):
            verify_permission('flow:manage')
            if path.startswith('/api/flow/definitions') and request.method not in ('GET', 'HEAD'):
                raise AccessDenied('固定案例流程定义与来源模型绑定')
        if match := re.fullmatch(r'/api/(contract|invoice)(?:/(\d+))?/(draft|submit|withdraw)', path):
            domain, record_id, action = match.groups()
            prefix = 'PERM-' + domain.upper() + '-'
            verify_permission(prefix + ('SAVE-DRAFT' if action == 'draft' else 'SUBMIT'))
            if record_id and '*' not in g.grants:
                status = 'status' if domain == 'contract' else 'approval_status'
                row = db.query_one(f'SELECT created_by,{status} FROM {domain} WHERE id=? AND flag=1', (int(record_id),))
                if not row or row['created_by'] != g.user['id']:
                    raise AccessDenied('只有当前单据的发起人可修改草稿、提交或撤回')
                if row[status] == '已驳回':
                    verify_permission(prefix + 'RESUBMIT')
        if match := re.fullmatch(r'/api/workbench/todo/(\d+)/(approve|reject|return)', path):
            task = db.query_one(
                'SELECT i.creator_id FROM flow_task t JOIN flow_instance i ON t.instance_id=i.id WHERE t.id=?',
                (int(match[1]),))
            if task and task['creator_id'] == g.user['id']:
                raise AccessDenied('禁止审批本人提交的业务单据')
        return None

    @app.teardown_request
    def unlock_request(_error):
        if getattr(g, 'contract_write_lock', False):
            g.contract_write_lock = False
            write_lock.release()

    @app.errorhandler(AccessDenied)
    def denied(error):
        return fail(str(error), 403)

    @app.errorhandler(ValueError)
    def bad_input(error):
        return fail(str(error), 400)

    @app.errorhandler(HTTPException)
    def http_error(error):
        return fail(error.description, error.code)

    @app.after_request
    def response_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/api/ai/config')
    def platform_model_config():
        # Credential ownership stays with the platform provider. The reference app is read-only.
        return jsonify(success=True, message='由 Onto PaaS 管理 GLM', data={
            'base_url': '', 'api_key_masked': '', 'model_id': 'glm-5.3-flash',
            'max_tokens': 1500, 'configured': bool(request.environ.get('onto.contract_llm_factory'))})

    def answer():
        body = request.get_json(silent=True) or {}
        question = body.get('question') or body.get('message')
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            raise BusinessError('请提供有效的合同报表问题')
        factory = request.environ.get('onto.contract_llm_factory')
        if not factory:
            raise BusinessError('此运行环境尚未配置 GLM')
        department = scope('PERM-CONTRACT-QUERY-EXECUTION')
        based_on = reports.execution_analysis(1, 20, {'department_id': department})
        keep = ('contract_no', 'contract_name', 'status', 'total_amount', 'invoiced_amount', 'received_amount')
        data = {'report': '合同执行情况', 'total': based_on['total'],
                'rows': [{key: row[key] for key in keep if key in row} for row in based_on['list']]}
        llm = factory(directory / 'data/ai-runs' / uuid.uuid4().hex)
        response = llm.text(
            '你是只读合同报表助手。仅基于给出的真实合同执行报表回答。'
            '金额以元计，引用合同编号与数值，区分已开票、已收款和合同金额；'
            '超出报表就说明证据不足。切勿声称执行审批、开票、收款或其他写入。',
            json.dumps({'question': question, 'evidence': data}, ensure_ascii=False),
            purpose='contract_reference.execution_report', max_tokens=1500)
        return {'answer': response, 'evidence': data, 'provider_calls': [
            {'purpose': item.get('purpose'), 'model': item.get('model'), 'status': item.get('status')}
            for item in llm.calls]}

    @app.post('/api/chat')
    def contract_chat():
        return jsonify(answer())

    @app.post('/api/ai/chat')
    def browser_chat():
        result = answer()
        delta = json.dumps({'content': result['answer']}, ensure_ascii=False)
        return Response(f'event: delta\ndata: {delta}\n\nevent: message_end\ndata: {{}}\n\n',
                        mimetype='text/event-stream')

    @app.get('/', defaults={'path': ''})
    @app.get('/<path:path>')
    def frontend(path):
        if path.startswith('api/'):
            return fail('接口不存在', 404)
        if path and (directory / 'web' / path).is_file():
            return send_from_directory(directory / 'web', path)
        mount = request.script_root.rstrip('/') + '/'
        if not re.fullmatch(r'/(?:[A-Za-z0-9_-]+/)*', mount):
            return fail('应用入口路径无效', 400)
        html = (directory / 'web/index.html').read_text().replace('__ONTO_APP_MOUNT__', mount)
        return Response(html, mimetype='text/html')

    return app


class Runtime:
    def __init__(self, build_root):
        directory = Path(build_root).resolve()
        self.directory = directory / 'code-app' if (directory / 'code-app').is_dir() else directory
        self.spec = json.loads((self.directory / 'runtime.json').read_text())
        if self.spec.get('runtime_kind') != _PROFILE:
            raise BusinessError('合同运行模型类型不符')
        _, database = _prepare_paths(self.directory)
        if not database.is_file():
            raise BusinessError('合同运行数据库不存在')
        try:
            with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
                hashes = connection.execute('SELECT model_hash FROM onto_contract_release').fetchall()
        except sqlite3.Error:
            raise BusinessError('无法核对合同数据库的固定模型摘要') from None
        if hashes != [(self.spec['model_hash'],)]:
            raise BusinessError('合同数据库与固定模型摘要不匹配')

    def handle(self, environ, mount, suffix, llm_factory):
        from flask import Response
        with _runtime_lock:
            app = _apps.get(self.directory)
            if app is None:
                app = _runtime_app(self.directory)
                _apps[self.directory] = app
        adjusted = dict(environ, SCRIPT_NAME=mount, PATH_INFO='/' + suffix)
        adjusted['onto.contract_llm_factory'] = llm_factory
        return Response.from_app(app, adjusted)


def create_standalone_app(directory):
    from flask import Flask, request
    directory = Path(directory).resolve()
    _, database = _prepare_paths(directory)
    if not database.exists():
        accounts = initialize_database(directory)
        path = directory / 'data/initial-credentials.json'
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
            json.dump(accounts, stream, ensure_ascii=False, indent=2)
        print('初始角色凭据：' + str(path), flush=True)
    runtime = Runtime(directory)
    app = Flask(__name__, static_folder=None)
    from provider import LLM

    @app.route('/', defaults={'path': ''}, methods=['GET', 'POST', 'PUT', 'DELETE'])
    @app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
    def dispatch(path):
        return runtime.handle(request.environ, '', path, lambda trace: LLM(trace_dir=trace, max_calls=1))
    return app
