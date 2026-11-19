"""Build the pinned V9 contract example with its matching upstream services.

The reference model set and the author's runnable example are separate artifacts.
The design is only accepted unchanged, and arbitrary uploaded code is never run.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
from functools import lru_cache
from pathlib import Path

import yaml

from .app_runtime import BusinessError, now
from .business import MODEL_FILES

COMMIT = '08686470f7af0a9095a000744d69e28c80f74534'
FORMAT = 'ontology-driven-dev-contract-v9'
SOURCE_MANIFEST_SHA256 = '1c0e1f0d45764189d489bb966ee95bc0af129e41abf4e766d0ad189603007e22'
WEB_MANIFEST_SHA256 = '4d1b660560854c43da7c0e4e221b26cc68571bfa02e34b66ea91e72c98d0c264'
RUNTIME_KIND = 'contract-reference-v9'
ASSETS = Path(__file__).with_name('upstream') / 'contract_reference'
DESIGN = 'reference-example'
AUTHOR_APP = 'code-app-example'
REQUIREMENTS = f'{DESIGN}/合同管理需求规格说明书-V9.md'
PROTOTYPE = f'{DESIGN}/合同管理需求规格说明书-V9-UI原型.html'
TABLES = ['product', 'customer', 'department', 'employee', 'contract', 'contract_stage',
          'invoice', 'invoice_allocation', 'receipt', 'approval_record']
MODULES = 'api|config|db|engine|ontology|services|utils'


def _verified_manifest(name, expected):
    contents = (ASSETS / name).read_bytes()
    if hashlib.sha256(contents).hexdigest() != expected:
        raise BusinessError('合同参考案例清单摘要不一致：' + name)
    return json.loads(contents)


@lru_cache(maxsize=1)
def _sources():
    manifest = _verified_manifest('source-sha256.json', SOURCE_MANIFEST_SHA256)
    if manifest['commit'] != COMMIT:
        raise BusinessError('合同参考案例来源提交不一致')
    return manifest['files']


def _read_original(path):
    asset = ASSETS / path
    data = asset.read_bytes()
    if hashlib.sha256(data).hexdigest() != _sources()[path]:
        raise BusinessError('合同参考原始材料摘要不一致：' + path)
    return data


@lru_cache(maxsize=1)
def _reference_inputs():
    models = {name: yaml.safe_load(_read_original(f'{DESIGN}/{name}')) for name in MODEL_FILES}
    models['manifest.json'] = json.loads(_read_original(f'{DESIGN}/manifest.json'))
    return {'requirements': _read_original(REQUIREMENTS).decode(),
            'models': models, 'prototype_html': _read_original(PROTOTYPE).decode(),
            'commit': COMMIT, 'format': FORMAT}


def reference_inputs():
    return copy.deepcopy(_reference_inputs())


def is_reference_project(project):
    marker = project.get('reference_adoption') or {}
    if not isinstance(marker, dict) or marker.get('commit') != COMMIT or marker.get('format') != FORMAT:
        return False
    expected = _reference_inputs()
    prototype = project.get('prototype') or {}
    return (project.get('requirements') == expected['requirements']
            and project.get('models') == expected['models']
            and prototype.get('html') == expected['prototype_html']
            and prototype.get('sha256') == hashlib.sha256(expected['prototype_html'].encode()).hexdigest())


def _namespace_python(source, depth):
    # Only the known upstream files call these modules with absolute imports.
    dot = '.' * depth
    source = re.sub(rf'^(\s*)from ({MODULES})([.\w]*) import ',
                    lambda match: f'{match[1]}from {dot}{match[2]}{match[3]} import ', source, flags=re.M)
    return re.sub(r'^(\s*)import db\s*$', rf'\1from {dot} import db', source, flags=re.M)


def _copy_runtime_assets(code_app):
    upstream = code_app / 'upstream-techbase'
    backend = upstream / 'contract_backend'
    runtime_models = upstream / 'contract_runtime_models'
    for relative in _sources():
        if relative.startswith(f'{AUTHOR_APP}/models/'):
            target = runtime_models / Path(relative).name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_read_original(relative))
        if not relative.startswith(f'{AUTHOR_APP}/backend/'):
            continue
        source = Path(relative).relative_to(f'{AUTHOR_APP}/backend')
        # Host wrapper supplies initialization, database config, route mounting and GLM.
        if source.as_posix() in {'app.py', 'config.yaml', 'config/settings.py', 'requirements.txt'}:
            continue
        data = _read_original(relative)
        target = backend / source
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix == '.py':
            code = data.decode()
            if source.as_posix() == 'seed.py':
                # Keep author role, permission, menu and workflow mappings; use only our fresh-account bootstrap.
                code = code[:code.index('def ensure_seed(')] + code[code.index('def _seed_roles_permissions('):]
                code = code[:code.index('def _seed_users(')] + code[code.index('def _ensure_flow_definition('):]
                code = code.replace('from utils.codegen import generate_code\n', '')
                code = code.replace('from utils.security import hash_password\n', '')
            target.write_text(_namespace_python(code, len(source.parts)))
        elif source.as_posix() == 'schema.sql':
            target.write_bytes(data)
    (backend / '__init__.py').write_text('')
    (backend / 'config/settings.py').write_text(
        "config = {}\n"
        "def get(key, default=None):\n"
        "    return config.get(key, default)\n"
        "def resolve_models_dir():\n"
        "    return config['models_dir']\n")
    # Copy the prebuilt, licensed frontend. There is no Node dependency when building a case.
    bundle = ASSETS / 'web'
    hashes = _verified_manifest('web-sha256.json', WEB_MANIFEST_SHA256)
    for name, expected in hashes.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise BusinessError('合同前端清单路径无效')
        data = (bundle / relative).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise BusinessError('合同前端产物摘要不一致')
        target = code_app / 'web' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (code_app / 'UPSTREAM-LICENSE').write_bytes(_read_original('LICENSE'))
    return backend


def build_reference_application(project, output_dir, model_hash):
    if not is_reference_project(project):
        raise BusinessError('合同参考案例的原始材料已变化，不能沿用专用构建配置')
    from .contract_runtime import initialize_database

    output_dir = Path(output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise BusinessError('构建目录已存在内容，请使用新的不可变版本目录')
    code_app = output_dir / 'code-app'
    code_app.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        output_dir.chmod(0o700)
        backend = _copy_runtime_assets(code_app)
        for name in (*MODEL_FILES, 'manifest.json'):
            target = code_app / 'models' / name
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(_read_original(f'{DESIGN}/{name}'))
        migrations = code_app / 'migrations'
        migrations.mkdir()
        shutil.copy2(backend / 'schema.sql', migrations / '001_initial.sql')
        shutil.copy2(Path(__file__).with_name('contract_runtime.py'), code_app / 'runtime.py')
        shutil.copy2(Path(__file__).with_name('provider.py'), code_app / 'provider.py')
        (code_app / 'server.py').write_text(
            'from pathlib import Path\nfrom runtime import create_standalone_app\n'
            'app = create_standalone_app(Path(__file__).resolve().parent)\n'
            "if __name__ == '__main__':\n    app.run(host='127.0.0.1', port=5400, debug=False)\n")
        (code_app / 'requirements.txt').write_text('Flask==3.1.3\nPyYAML==6.0.3\nPyJWT==2.8.0\nsimpleeval==0.9.13\n')
        (code_app / 'README.md').write_text(
            '# 销售合同执行管理 · 作者固定案例\n\n'
            '基于人月 ontology-driven-dev 的 V9 设计版与同一提交的 code-app-example 服务和页面。\n'
            f'来源提交：{COMMIT}。原设计保存在 models，作者运行模型在 upstream-techbase。\n'
            '本构建仅支持该固定案例，不是通用的 V9 兼容层。付款阶段和开票分摊的 localId 由作者主从表定位。'
            '写入权限来自作者运行版 M5 的实际操作权限，重新提交走作者驳回后编辑与提交入口，'
            '作废、归档和总经理审批使用作者 MU 页面及审批待办。\n'
            '安装 requirements.txt 后执行 python server.py。首次运行生成独立空业务库和随机角色凭据，'
            '初始凭据保存在 data/initial-credentials.json。\n'
            '助手使用平台 GLM 只读汇总；可用 PAAS_API_KEY 配置独立运行。\n')
        prototype = project['prototype']
        spec = {'runtime_kind': RUNTIME_KIND, 'source': {'commit': COMMIT, 'format': FORMAT},
                'model_hash': model_hash, 'name': project['name'], 'prototype_sha256': prototype['sha256']}
        (code_app / 'runtime.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2))
        accounts = initialize_database(code_app)
        receipt = {'status': 'built', 'runtime_kind': RUNTIME_KIND, 'output_dir': str(output_dir),
                   'app_dir': str(code_app), 'database_path': str(code_app / 'data/application.db'),
                   'model_hash': model_hash, 'prototype_sha256': prototype['sha256'], 'tables': TABLES,
                   'app_url': f"/apps/{project.get('id')}/", 'accounts': accounts, 'built_at': now(),
                   'source': spec['source'], 'migration': '001_initial.sql', 'initial_business_rows': 0}
        (output_dir / 'initial-credentials.json').write_text(json.dumps(accounts, ensure_ascii=False, indent=2))
        (output_dir / 'initial-credentials.json').chmod(0o600)
        receipt['artifact_sha256'] = {
            str(file.relative_to(output_dir)): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in code_app.rglob('*') if file.is_file() and not any(
                component in {'data', '__pycache__'} for component in file.relative_to(code_app).parts)
        }
        (output_dir / 'build-manifest.json').write_text(
            json.dumps({key: value for key, value in receipt.items() if key != 'accounts'}, ensure_ascii=False, indent=2))
        return receipt
    except Exception:
        if code_app.exists():
            shutil.rmtree(code_app)
        (output_dir / 'initial-credentials.json').unlink(missing_ok=True)
        (output_dir / 'build-manifest.json').unlink(missing_ok=True)
        raise
