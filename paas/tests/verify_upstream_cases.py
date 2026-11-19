"""Re-run independently checked, pinned upstream commerce and contract cases.

Requires local clones containing the two pinned upstream commits. Defaults to
an isolated Flask test client and a replayed query plan, with real SQLite
execution and independent results from the pinned source database. An explicit
--live-model also exercises a fresh provider-generated S1 query/report.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import sqlite3
import subprocess
import tempfile
import time
import zipfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import yaml

from paas import analysis, business
from paas.app_runtime import BusinessError
from paas.builder import build_application
from paas.ontology import STANDARD, analysis_projection
from paas.server import create_app

COMMERCE_COMMIT = '04a683ec3649d07602f8ca4a3fce581d17a100da'
CONTRACT_COMMIT = '08686470f7af0a9095a000744d69e28c80f74534'
DATABASE = 'backend/data/demo_ecommerce.db'
DATABASE_HASH = '2d5d202b398bf57e015a555d9997a22780b95a155d757e1b0c8fca2a703daf82'
LEGACY_FILES = dict(zip(
    ('m1_object_model.yaml', 'm2_behavior_model.yaml', 'm3_rule_model.yaml',
     'm4_scenario_model.yaml', 'm_metric_model.yaml', 'data_mapping.yaml'),
    ('entities', 'behaviors', 'rules', 'scenarios', 'metrics', 'entity_mappings'),
))
ORDER = 'ENT-ORD-001'
METRIC_IDS = ['MTR-TXN-001', 'MTR-TXN-002', 'MTR-TXN-003']
SCOPE = (
    '公开开源案例验收：使用作者原始快照，验证单表订单聚合。'
    '有效订单排除状态5（取消）和6（退款完成）；GMV按实付金额合计、'
    '有效订单按订单ID计数、客单价用GMV除以有效订单数，金额保留两位小数。'
    '月份和下单渠道仅作为分组维度。'
)
LIVE_QUESTION = (
    '使用t_order全量快照，按gmt_create的月份分组，月份升序。只输出'
    'period、gmv、order_count三列。剔除状态5和6，实付金额之和保留两位小数，'
    '有效订单按order_id去重计数。不连接其他表，不额外筛选时间，不设LIMIT。'
)


def original(repository: Path, commit: str, relative: str) -> bytes:
    return subprocess.check_output(['git', '-C', str(repository), 'show', f'{commit}:{relative}'])


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def request(client, method, path, expected=(200,), **kwargs):
    response = getattr(client, method)(path, base_url='http://localhost', **kwargs)
    if response.status_code not in expected:
        raise AssertionError(f'{method.upper()} {path}: {response.status_code}, {response.get_json()}')
    return response.get_json()


def make_analysis_project(client, name, database):
    created = request(client, 'post', '/api/v1/projects', (201,), json={
        'name': name, 'description': SCOPE, 'kind': 'analysis',
    })
    project_id = created['id']
    route = '/api/v1/projects/' + project_id
    project = request(client, 'post', route + '/source', data={
        'database': (io.BytesIO(database), 'demo_ecommerce.db'),
    }, content_type='multipart/form-data')
    assert project['source']['sha256'] == DATABASE_HASH
    assert len(project['source']['schema']['tables']) == 12
    assert file_hash(Path(project['source']['path']).read_bytes()) == DATABASE_HASH

    # Replay an exact verification scope through the real requirements job.
    requirements = {
        'analysis_goals': [{'id': 'VERIFY-GMV', 'title': '验证作者单表GMV、有效订单数、客单价和渠道聚合口径'}],
        'data_objects': [{'name': '订单', 'hint_tables': ['t_order']}],
        'ai_questions': [], 'focus_areas': ['公开案例单表指标'],
        'metrics': [
            {'name': 'GMV', 'definition': '状态非5、6的订单实付金额合计'},
            {'name': '有效订单数', 'definition': '状态非5、6的订单按订单ID计数'},
            {'name': '客单价', 'definition': 'GMV / 有效订单数'},
        ],
        'time_range': None,
    }

    class RequirementsReplay:
        calls = []

        def __init__(self, **kwargs):
            pass

        def json(self, system, user, *, purpose, **kwargs):
            assert purpose == 'analysis_requirements'
            return requirements

    with patch('paas.jobs.LLM', RequirementsReplay):
        job = wait_for_job(client, request(client, 'post', route + '/requirements', (202,), json={}))
    assert job['status'] == 'succeeded', job.get('error')
    project = request(client, 'get', route)
    project = request(client, 'post', route + '/confirm', json={
        'scope': 'requirements', 'revision': project['revision'],
        'decision': '公开作者案例的离线验收口径，仅适用于独立测试项目',
    })
    return route, project


def import_models(client, route, project, texts):
    request(client, 'post', route + '/models/import', (200, 201), json={
        'revision': project['revision'], 'files': texts,
    })
    return request(client, 'get', route)


def verify_import_artifacts(client, route, project, texts):
    original_files = project['model_import']['files']
    assert set(original_files) == set(texts)
    for filename, text in texts.items():
        entry = original_files[filename]
        response = client.get(route + '/artifacts/' + entry['artifact'], base_url='http://localhost')
        assert response.status_code == 200
        assert response.get_data() == text.encode()
        assert file_hash(response.get_data()) == entry['sha256']
    exported = client.get(route + '/download', base_url='http://localhost')
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.data)) as archive:
        model_paths = {name for name in archive.namelist() if name.startswith('models/')}
        assert model_paths == {'models/' + name for name in business.MODEL_FILES}
        for filename in business.MODEL_FILES:
            assert yaml.safe_load(archive.read('models/' + filename)) == project['models'][filename]
        assert yaml.safe_load(archive.read('source/data_mapping.yaml')) == project['source_bindings']
        assert json.loads(archive.read('provenance.json'))['model_standard'] == STANDARD


def source_subset(legacy):
    """Select the documented single-table scope as a distinct derivative."""
    subset = copy.deepcopy(legacy)
    mapping = next(row for row in subset['data_mapping.yaml']['entity_mappings'] if row['entity_id'] == ORDER)
    columns = {'order_id', 'gmt_create', 'status', 'payment_amount', 'channel'}
    mapping['field_mappings'] = [field for field in mapping['field_mappings'] if field['db_column'] in columns]
    subset['data_mapping.yaml']['entity_mappings'] = [mapping]
    selected_fields = {field['ontology_field'] for field in mapping['field_mappings']}
    entity = next(row for row in subset['m1_object_model.yaml']['entities'] if row['id'] == ORDER)
    entity['attributes'] = [attr for attr in entity['attributes'] if attr['name'] in selected_fields]
    subset['m1_object_model.yaml'].update(entities=[entity], relations=[])
    behavior = next(row for row in subset['m2_behavior_model.yaml']['behaviors'] if row['id'] == 'BHV-ANA-002')
    behavior['appliedRules'] = ['RULE-MTR-001']
    subset['m2_behavior_model.yaml']['behaviors'] = [behavior]
    rule_ids = {'RULE-MTR-001', 'RULE-MTR-002', 'RULE-MTR-003'}
    rules = [rule for rule in subset['m3_rule_model.yaml']['rules'] if rule['id'] in rule_ids]
    for rule in rules:
        rule['reusedBy'] = [ref for ref in rule.get('reusedBy', []) if ref == behavior['id']]
    subset['m3_rule_model.yaml']['rules'] = rules
    metrics = [metric for metric in subset['m_metric_model.yaml']['metrics'] if metric['id'] in METRIC_IDS]
    for metric in metrics:
        metric['rule_refs'] = [ref for ref in metric.get('rule_refs', []) if ref in rule_ids]
    subset['m_metric_model.yaml']['metrics'] = metrics
    scenario = next(row for row in subset['m4_scenario_model.yaml']['scenarios'] if row['id'] == 'SCN-GMV-001')
    scenario['relatedBehaviors'] = [behavior['id']]
    scenario['keyMetrics'] = METRIC_IDS.copy()
    scenario['primaryFlow'] = [step for step in scenario['primaryFlow'] if step['stepId'] in ('S01', 'S03')]
    subset['m4_scenario_model.yaml']['scenarios'] = [scenario]
    return subset


def independent_queries(database_path, files):
    selected = {}
    inventory = []
    with sqlite3.connect(database_path.as_uri() + '?mode=ro&immutable=1', uri=True) as connection:
        connection.execute('PRAGMA query_only=ON')
        connection.row_factory = sqlite3.Row
        for filename, text in files.items():
            document = yaml.safe_load(text)
            for step in document['steps']:
                sql = step.get('fallback_sql')
                if not sql:
                    continue
                rows = [dict(row) for row in connection.execute(sql)]
                record = {'scenario': document['id'], 'step': step['id'], 'row_count': len(rows), 'sql': sql}
                inventory.append(record)
                if filename == 'scenario_gmv.yaml' and step['id'] in ('S1', 'S4', 'S5'):
                    selected[step['id']] = (sql, rows)
        coverage = dict(connection.execute(
            'SELECT min(gmt_create) AS start, max(gmt_create) AS end, count(*) AS orders FROM t_order'
        ).fetchone())
    assert set(selected) == {'S1', 'S4', 'S5'}
    assert all(len(rows) == 12 for _, rows in selected.values())
    assert sum(row['order_count'] for row in selected['S1'][1]) == 13959
    assert sum(Decimal(str(row['gmv'])) for row in selected['S1'][1]) == Decimal('53525655.87')
    return selected, inventory, coverage


def wait_for_job(client, payload, timeout=300):
    deadline = time.monotonic() + timeout
    route = '/api/v1/jobs/' + payload['job_id']
    while time.monotonic() < deadline:
        job = request(client, 'get', route)
        if job['status'] in ('succeeded', 'failed', 'cancelled'):
            return job
        time.sleep(0.15)
    request(client, 'post', route + '/cancel')
    raise AssertionError(f'{payload["job_id"]} was still running after {timeout}s')


def execute(client, route, question):
    return wait_for_job(client, request(client, 'post', route + '/analysis', (202,), json={'question': question}))


def compare_job(job, expected):
    assert job['status'] == 'succeeded', job.get('error')
    result = job['result']
    actual = {tuple(row[column] for column in expected[0]): row for row in result['rows']}
    expected_keys = {tuple(row.values()) for row in expected}
    assert len(result['rows']) == len(actual) == len(expected)
    assert set(actual) == expected_keys, {'actual': result['rows'], 'expected': expected}
    assert result['source_hash'] == DATABASE_HASH and not result['provenance']['truncated']
    return {'job_id': job['id'], 'status': job['status'], 'sql': result['sql'],
            'rows': result['rows'], 'record_count': len(result['rows']), 'provider_calls': job.get('calls', [])}


def run_selected(client, route, selected, inventory, schema):
    results = {}
    metrics_by_step = {'S1': METRIC_IDS[:2], 'S4': METRIC_IDS[:2], 'S5': METRIC_IDS[1:]}
    for step, (sql, expected) in selected.items():
        class Replay:
            calls = []

            def __init__(self, **kwargs):
                pass

            def json(self, system, user, *, purpose, **kwargs):
                if purpose == 'analysis_sql':
                    return {'sql': sql, 'parameters': {}, 'metric_ids': metrics_by_step[step]}
                assert purpose == 'analysis_report'
                return {'summary': '离线验收比较 SQLite 实际执行行和作者参考查询的结果', 'findings': []}

        with patch('paas.jobs.LLM', Replay):
            job = execute(client, route, f'作者示例GMV查询步骤 {step}')
        results[step] = compare_job(job, expected)

    mapped_tables = {table['name'] for table in schema['tables']}
    for step in inventory:
        try:
            analysis._check_sql(step['sql'], schema, mapped_tables)
        except analysis.AnalysisError as exc:
            step.update(guard='rejected_sql', reason=str(exc))
        else:
            if step['row_count'] > analysis.MAX_ROWS:
                step.update(guard='would_exceed_row_budget')
            else:
                step.update(guard='shape_and_row_count_only')
        if step['scenario'] == 'SCN-GMV-001' and step['step'] in results:
            step['guard'] = 'executed_on_confirmed_subset'
    return results


def verify_commerce(client, repository, report, live_model):
    database = original(repository, COMMERCE_COMMIT, DATABASE)
    assert file_hash(database) == DATABASE_HASH
    texts = {file: original(repository, COMMERCE_COMMIT, 'backend/models/default/' + file).decode()
             for file in LEGACY_FILES}
    scenario_files = {filename: original(repository, COMMERCE_COMMIT, 'backend/scenarios/' + filename).decode()
                      for filename in ('scenario_gmv.yaml', 'scenario_anomaly.yaml', 'scenario_churn.yaml')}
    with tempfile.TemporaryDirectory(prefix='onto-author-baseline-') as temporary:
        baseline_path = Path(temporary) / 'author-ecommerce.sqlite'
        baseline_path.write_bytes(database)
        selected, inventory, coverage = independent_queries(baseline_path.resolve(), scenario_files)
    legacy = {name: yaml.safe_load(text) for name, text in texts.items()}
    route, project = make_analysis_project(client, '开源电商 · 作者全量模型导入', database)
    imported = import_models(client, route, project, texts)
    models, bindings = imported['models'], imported['source_bindings']
    verify_import_artifacts(client, route, imported, texts)
    assert set(models) == set(business.MODEL_FILES)
    assert analysis_projection(models, bindings) == legacy
    diagnostics = imported['model_diagnostics']
    diagnosis = json.dumps(diagnostics, ensure_ascii=False)
    assert len(diagnostics) == 2 and 'MTR-MKT-001' in diagnosis and 'MTR-PRD-005' in diagnosis, diagnostics
    refused = request(client, 'post', route + '/confirm', (400, 409, 422), json={
        'scope': 'models', 'revision': imported['revision'],
    })
    assert refused['error']
    assert not request(client, 'get', route)['models_confirmation']
    report['full_import'] = {
        'project_id': imported['id'], 'commit': COMMERCE_COMMIT, 'standard': STANDARD,
        'source_sha256': DATABASE_HASH, 'document_names': list(models),
        'original_file_hashes': {name: file_hash(text.encode()) for name, text in texts.items()},
        'entities': len(models['m1-object-model.yaml']['entities']),
        'behaviors': len(models['m2-behavior-model.yaml']['behaviors']),
        'rules': len(models['m3-rule-model.yaml']['rules']),
        'analysis_flows': len(models['m6-flow-model.yaml']['flows']),
        'metrics': len(models['m7-report-model.yaml']['metrics']),
        'fields_bound': sum(len(row['field_mappings']) for row in bindings['entity_mappings']),
        'round_trip_equal': True, 'original_artifacts_byte_equal': True, 'canonical_export_equal': True,
        'confirmation_blocked': refused['error'], 'diagnostics': diagnostics,
    }
    assert [report['full_import'][key] for key in (
        'entities', 'behaviors', 'rules', 'analysis_flows', 'metrics', 'fields_bound',
    )] == [12, 10, 24, 3, 29, 110]

    route, project = make_analysis_project(client, '开源电商 · 作者GMV步骤验收子集', database)
    subset = source_subset(legacy)
    imported = import_models(client, route, project, {
        name: yaml.safe_dump(doc, sort_keys=False, allow_unicode=True) for name, doc in subset.items()
    })
    assert not imported['model_diagnostics'], imported['model_diagnostics']
    _ = request(client, 'post', route + '/confirm', json={
        'scope': 'models', 'revision': imported['revision'],
        'decision': '仅验收作者单表GMV、订单数、渠道和客单价，核对原样查询；不代表其他场景确认',
    })
    results = run_selected(client, route, selected, inventory, imported['source']['schema'])
    report['selected_verification'] = {
        'project_id': imported['id'], 'source_coverage': coverage,
        'checks': results, 'source_sql_steps': inventory,
        'mode': 'replayed public query plans, real guarded SQLite execution and independent baseline',
        'total_gmv': '53525655.87', 'valid_orders': 13959,
    }
    saved = request(client, 'get', route)
    assert len(saved['runs']) == 3 and file_hash(Path(saved['source']['path']).read_bytes()) == DATABASE_HASH
    if live_model:
        job = execute(client, route, LIVE_QUESTION)
        report['live_gmv_model_run'] = {'id': job['id'], 'status': job['status'], 'calls': job.get('calls', []),
                                        'error': job.get('error'), 'result': job.get('result')}
        report['live_gmv_model_run'].update(compare_job(job, selected['S1'][1]))


def verify_contract(repository, output):
    prefix = 'reference-example/'
    source_files = ('合同管理需求规格说明书-V9.md', '合同管理需求规格说明书-V9-UI原型.html',
                    *business.MODEL_FILES, 'manifest.json')
    materials = {name: original(repository, CONTRACT_COMMIT, prefix + name) for name in source_files}
    requirements = materials[source_files[0]].decode()
    audit = business.validate_requirements(requirements)
    assert audit['passed'] and len(audit['checks']) == 47
    models = {name: yaml.safe_load(materials[name]) for name in business.MODEL_FILES}
    models['manifest.json'] = json.loads(materials['manifest.json'])
    diagnostics = business.validate_models(models)
    target = output / 'contract-native-build'
    try:
        build_application({'id': 'upstream-contract-recheck', 'name': '作者原版合同', 'requirements': requirements,
                           'models': models}, target)
    except BusinessError as exc:
        return {'commit': CONTRACT_COMMIT, 'source_file_hashes': {name: file_hash(data) for name, data in materials.items()},
                'requirements_checks': len(audit['checks']), 'requirements_audit_passed': audit['passed'],
                'model_diagnostics': diagnostics, 'native_build_status': 'rejected',
                'native_build_error': str(exc), 'build_output_created': target.exists()}
    return {'commit': CONTRACT_COMMIT, 'source_file_hashes': {name: file_hash(data) for name, data in materials.items()},
            'requirements_checks': len(audit['checks']), 'requirements_audit_passed': audit['passed'],
            'model_diagnostics': diagnostics, 'native_build_status': 'compiled',
            'build_output_created': target.exists()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commerce-repo', type=Path, required=True)
    parser.add_argument('--contract-repo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, help='Fresh disposable project storage. Defaults to a temporary directory.')
    parser.add_argument('--live-model', action='store_true', help='Submit one additional query/report using the configured provider.')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {}
    try:
        with tempfile.TemporaryDirectory(prefix='onto-upstream-cases-') as temporary:
            data_dir = args.data_dir or Path(temporary)
            if args.data_dir is not None:
                data_dir.mkdir(parents=True, exist_ok=False)
            app = create_app(data_dir)
            try:
                report['contract'] = verify_contract(args.contract_repo, args.output)
                verify_commerce(app.test_client(), args.commerce_repo, report, args.live_model)
            finally:
                app.extensions['paas_jobs'].pool.shutdown()
    except BaseException as exc:
        report['verification_error'] = str(exc)
        raise
    finally:
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
