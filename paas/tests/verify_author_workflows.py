"""GLM-backed acceptance of the author’s public contract and commerce workflows."""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile

from paas.server import create_app
from paas.tests.verify_upstream_cases import COMMERCE_COMMIT, DATABASE, DATABASE_HASH, LEGACY_FILES, request, wait_for_job
from paas.workspace_lock import WorkspaceLease


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def verify_commerce(app, repo, out):
    proof = {}

    def original(relative):
        return subprocess.check_output(['git', '-C', str(repo), 'show', f'{COMMERCE_COMMIT}:{relative}'])

    def save():
        write(out / 'commerce-live.json', proof)

    client = app.test_client()
    actual_model = request(client, 'get', '/api/v1/status')['provider']
    assert actual_model['configured'] and actual_model['model'] == 'glm-5.3-flash'
    database = original(DATABASE)
    assert sha256(database).hexdigest() == DATABASE_HASH
    spec = original('demo_files/analysis_requirement.md').decode()
    baseline = (
        spec + '\n\n## 上游固定案例验收的输出\n'
        '本次完整执行仓库提交随附场景的固定计划：GMV 分析、经营异常分析、客户流失分析。'
        '各步骤的口径、固定日期、SQL 与统计算法以作者各场景 YAML 为准。'
        '订单快照实际覆盖 2025-06-01 至 2026-05-16，末月为不完整月。'
        'GMV 排除状态 5、6 并按实付金额统计。退款分析沿用作者分别设定的时间归属和退款记录计数。'
        '前 800 名 RFM 样本用于演示画像，禁止当作全量用户。'
        '营销 GMV 贡献只计活动日期范围内由优惠券关联的有效订单，同一活动同一订单计一次。'
        '商品平均评分为统计期内有有效订单的去重动销商品原表评分平均值。'
        '优先行动为建议，因果与 ROI 没证据就列为待核；不改动业务数据。'
    )
    project = request(client, 'post', '/api/v1/projects', (201,), json={
        'name': '开源验收 · 完整电商经营分析', 'kind': 'analysis', 'description': baseline,
    })
    pid = project['id']
    route = '/api/v1/projects/' + pid
    proof.update(project_id=pid, source_commit=COMMERCE_COMMIT, source_sha256=DATABASE_HASH, jobs=[], scenarios=[])
    save()
    project = request(client, 'post', route + '/source', data={'database': (io.BytesIO(database), 'demo_ecommerce.db')},
                      content_type='multipart/form-data')
    raw = {file: original('backend/models/default/' + file).decode('utf-8') for file in LEGACY_FILES}
    project = request(client, 'post', route + '/models/import', json={
        'revision': project['revision'], 'files': raw, 'apply_upstream_metric_repairs': True, 'acknowledged': True,
    })
    assert len(project['models']) == 7 and not project['model_diagnostics']
    assert len(project['models']['m7-report-model.yaml']['metrics']) == 30
    for filename, entry in project['model_import']['files'].items():
        payload = client.get(route + '/artifacts/' + entry['artifact'], base_url='http://localhost')
        assert payload.status_code == 200 and payload.data == raw[filename].encode()
    repairs = project['model_import']['upstream_metric_repairs']
    assert {x['metric_id'] for x in repairs['repairs']} == {'MTR-MKT-001', 'MTR-PRD-005'}
    proof['repairs'] = repairs

    def run_job(endpoint, payload):
        submitted = request(client, 'post', route + endpoint, (202,), json=payload)
        print(endpoint, submitted['job_id'], flush=True)
        terminal = wait_for_job(client, submitted)
        proof['jobs'].append({k: terminal[k] for k in ('id', 'status', 'error', 'calls') if k in terminal})
        save()
        assert terminal['status'] == 'succeeded', terminal.get('error')
        assert terminal['calls'] and all(c['model'] == 'glm-5.3-flash' for c in terminal['calls'])
        return terminal['result']

    run_job('/requirements', {})
    project = request(client, 'get', route)
    assert project['analysis_requirements']['analysis_goals']
    project = request(client, 'post', route + '/confirm', json={
        'revision': project['revision'], 'scope': 'requirements',
        'decision': '仅用于公开案例验收，确认采用作者三个固定场景的完整范围、逐段原版 SQL 与统计口径。',
    })
    project = request(client, 'post', route + '/confirm', json={
        'revision': project['revision'], 'scope': 'models',
        'decision': '公开案例验收。已核对全量七模型、两个有出处的指标修订和原版 SQLite 字段绑定。',
    })
    plans = request(client, 'get', route + '/scenarios')['scenarios']
    assert len(plans) == 3 and sum(plan['step_count'] for plan in plans) == 19
    proof['confirmed_full_bundle'] = True
    with tempfile.TemporaryDirectory(prefix='upstream-commerce-oracle-') as oracle_dir:
        oracle = Path(oracle_dir) / 'original.sqlite'
        oracle.write_bytes(database)
        with sqlite3.connect(oracle.as_uri() + '?mode=ro&immutable=1', uri=True) as connection:
            connection.row_factory = sqlite3.Row
            for plan in plans:
                project = request(client, 'get', route)
                result = run_job('/scenarios/' + plan['id'] + '/runs', {'revision': project['revision']})
                assert result['step_count'] == plan['step_count']
                groups = Counter(step['type'] for step in result['steps'])
                for step in result['steps']:
                    assert step['status'] == 'succeeded'
                    if step['type'] == 'SQL':
                        expected = [dict(row) for row in connection.execute(step['sql'])]
                        assert step['rows'] == expected, (plan['id'], step['id'])
                proof['scenarios'].append({'id': plan['id'], 'name': plan['name'], 'steps': dict(groups),
                    'report': result['report'],
                    'queries': [{'id': step['id'], 'rows': step['row_count'], 'sql_sha256': step['sql_sha256']}
                                for step in result['steps'] if step['type'] == 'SQL'],
                    'plan_sha256': result['provenance']['plan_sha256']})
                print(plan['name'], dict(groups), 'author SQL matched', flush=True)
                save()
    project = request(client, 'get', route)
    assert len(project['runs']) == 3 and project['models_confirmation']
    assert sha256(Path(project['source']['path']).read_bytes()).hexdigest() == DATABASE_HASH
    proof['complete'] = True
    save()

def verify_contract(app, out):
    from paas.tests.test_contract_reference import exercise_contract

    client = app.test_client()
    provider = request(client, 'get', '/api/v1/status')['provider']
    assert provider['configured'] and provider['model'] == 'glm-5.3-flash'
    proof = {'complete': False}
    report_file = out / 'contract-live.json'
    write(report_file, proof)
    try:
        project = request(client, 'post', '/api/v1/projects', (201,), json={
            'name': '开源验收 · 原版销售合同执行管理', 'kind': 'business',
            'description': '采用原版 V9 需求、原型、七模型和对应业务运行底座，仅用于公开案例验证。',
        })
        project_id = project['id']
        route = '/api/v1/projects/' + project_id
        proof['project_id'] = project_id
        project = request(client, 'post', route + '/contract-reference', json={
            'revision': project['revision'], 'acknowledged': True,
        })
        assert len(project['requirement_audit']['checks']) == 47 and project['requirement_audit']['passed']
        assert {c['scope'] for c in project['confirmations']} == {'reference', 'requirements', 'prototype', 'models'}
        source_commit = project['reference_adoption']['commit']
        preview = client.get(route + '/prototype/html', base_url='http://localhost')
        assert preview.status_code == 200 and 'sandbox allow-scripts' in preview.headers['Content-Security-Policy']
        assert sha256(preview.data).hexdigest() == project['prototype']['sha256']

        pending = request(client, 'post', route + '/build', (202,), json={})
        job = wait_for_job(client, pending)
        proof['build'] = {key: job[key] for key in ('id', 'status', 'error') if key in job}
        assert job['status'] == 'succeeded', job.get('error')
        project = request(client, 'get', route)
        build = project['build']
        assert build['runtime_kind'] == 'contract-reference-v9' and build['initial_business_rows'] == 0
        accounts = build['accounts']
        candidate_mount = build['preview_url'].rstrip('/')
        exercise = exercise_contract(client, candidate_mount, accounts)
        exercise.pop('admin_token')
        proof['business'] = exercise
        plan = build['acceptance_plan']
        acceptance = request(client, 'post', route + '/build/acceptance', json={
            'revision': project['revision'], 'candidate_id': plan['id'],
            'checks': {item['id']: {'passed': True, 'result': '；'.join(exercise['checks'])} for item in plan['checks']},
        })['acceptance']
        published = request(client, 'post', route + '/releases', (201,), json={
            'version': 'author-v9-validation', 'revision': project['revision'], 'acknowledged': True,
        })
        release = published['release']
        mount = release['url'].rstrip('/')
        proof.update(runtime=build['runtime_kind'], source_commit=source_commit, release_url=release['url'],
                     release_id=release['id'], published_code_sha256=release['code_sha256'])
        assert release['code_sha256'] == acceptance['code_sha256']
        assert client.get(release['url'], base_url='http://localhost').status_code == 200
        admin = next(account for account in accounts if account['role_id'] == 'ROLE-ADMIN')
        token = request(client, 'post', mount + '/api/auth/login', json={
            'username': admin['username'], 'password': admin['password'],
        })['data']['token']
        proof['candidate_acceptance'] = acceptance
        write(report_file, proof)

        def business_state():
            path = Path(release['build']['database_path']).resolve()
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as connection:
                return [connection.execute('SELECT * FROM "' + table + '" ORDER BY rowid').fetchall()
                        for table in release['build']['tables']]

        before = business_state()
        answer = request(client, 'post', mount + '/api/chat', json={
            'question': f"仅报告合同 {exercise['contract_no']} 的已开票金额和已收款金额，以阿拉伯数字和元为单位。",
        }, headers={'Authorization': 'Bearer ' + token})
        assert answer['provider_calls'] and all(item['model'] == 'glm-5.3-flash'
                   and item['status'] == 'succeeded' for item in answer['provider_calls'])
        evidence = next(row for row in answer['evidence']['rows'] if row['contract_no'] == exercise['contract_no'])
        assert round(float(evidence['received_amount']), 2) == exercise['expected_received_amount']
        assert exercise['contract_no'] in answer['answer']
        assert re.search(r'300[ ,，]?000|30\s*万', answer['answer']), answer['answer']
        assert business_state() == before
        with sqlite3.connect(Path(build['database_path']).resolve().as_uri() + '?mode=ro', uri=True) as candidate:
            assert [candidate.execute('SELECT * FROM "' + table + '" ORDER BY rowid').fetchall()
                    for table in build['tables']] == before
        proof.update(glm=answer, model_left_business_rows_unchanged=True, candidate_data_matches_release=True,
                     complete=True)
        print('contract published; checks and live GLM report finished', flush=True)
    except BaseException as error:
        proof['error'] = str(error)
        raise
    finally:
        write(report_file, proof)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commerce-repo', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', choices=('both', 'commerce', 'contract'), default='both')
    args = parser.parse_args()
    run_commerce = args.case != 'contract'
    if run_commerce and not args.commerce_repo:
        parser.error('--commerce-repo is required for the commerce case')
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    if run_commerce and (args.output / 'commerce-live.json').exists():
        parser.error('commerce-live.json already exists; use a fresh output directory')
    if args.case != 'commerce' and (args.output / 'contract-live.json').exists():
        parser.error('contract-live.json already exists; use a fresh output directory')
    workspace = args.output / 'workspace'
    with WorkspaceLease(workspace):
        app = create_app(workspace)
        try:
            if run_commerce:
                verify_commerce(app, args.commerce_repo, args.output)
            if args.case != 'commerce':
                verify_contract(app, args.output)
        finally:
            app.extensions['paas_jobs'].pool.shutdown()


if __name__ == '__main__':
    main()
