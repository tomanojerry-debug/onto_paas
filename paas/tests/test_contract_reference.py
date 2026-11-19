"""Source-faithful business verification against a mounted reference build."""
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from paas.app_runtime import BusinessError, register_runtime_routes
from paas.builder import build_application
from paas.contract_reference import reference_inputs
from paas.store import Store


def exercise_contract(client, mount, accounts):
    """Drive the original 13 smoke stages, plus role and restoration checks."""
    mount = mount.rstrip('/')
    credentials = {item['username']: item['password'] for item in accounts}
    checks = []

    def call(method, path, token=None, payload=None, expected=200):
        headers = {'Authorization': 'Bearer ' + token} if token else {}
        response = client.open(mount + '/api' + path, method=method, headers=headers, json=payload)
        body = response.get_json()
        assert response.status_code == expected, (path, expected, response.status_code, body)
        assert body['success'] is (expected == 200), (path, body)
        return body['data']

    tokens = {user: call('POST', '/auth/login', payload={'username': user, 'password': password})['token']
              for user, password in credentials.items()}
    admin, sales, finance, manager, gm, employee = [tokens[key] for key in ('admin', 'sales', 'finance', 'finmgr', 'gm', 'emp')]
    call('GET', '/contract', expected=401)
    call('GET', '/contract', employee, expected=403)
    checks.append('独立角色登录与未授权读取拒绝')

    def add_master(kind, data):
        assert call('GET', f'/masterdata/{kind}/options', admin) == []
        return call('POST', f'/masterdata/{kind}', admin, data)['id']

    dept_id = add_master('department', {'department_name': '销售部', 'status': '在用'})
    owner_id = add_master('employee', {'employee_name': '示例王五', 'department_id': dept_id, 'status': '在用'})
    prod_id = add_master('product', {'product_name': '软件产品A', 'product_type': 'SOFTWARE', 'status': '在用'})
    cust_id = add_master('customer', {'customer_name': '甲公司', 'customer_type': 'ENTERPRISE', 'status': '在用'})
    checks.append('主数据由角色 API 录入独立空库')

    contract = {
        'contract_name': '测试合同A',
        'product_id': prod_id, 'customer_id': cust_id, 'department_id': dept_id,
        'contract_type': 'PRODUCT', 'sign_date': '2026-08-01', 'owner_id': owner_id,
        'total_amount': 500000, 'purchase_amount': 200000, 'tax_rate': 0.06,
        'stages': [{'stage_name': '预付款', 'pay_ratio': 40}, {'stage_name': '尾款', 'pay_ratio': 60}],
    }
    call('POST', '/contract/draft', employee, contract, expected=403)
    draft = call('POST', '/contract/draft', sales, contract)
    cid = draft['id']
    assert draft['contract_no'].startswith('CON') and len(draft['stages']) == 2
    checks.append('合同草稿与付款阶段')
    submitted = call('POST', f'/contract/{cid}/submit', sales)
    assert submitted['status'] == '待财务经理审批'
    checks.append('合同提交进入财务经理审批')

    def act(biz, record_id, actor, verb='approve'):
        tasks = call('GET', '/workbench/todo', actor)['list']
        task = next(item for item in tasks if item['biz_type'] == biz and item['biz_id'] == record_id)
        call('POST', f"/workbench/todo/{task['id']}/{verb}", actor, {'comment': '验收记录'})

    act('CONTRACT', cid, manager)
    assert call('GET', f'/contract/{cid}', admin)['status'] == '已纳入管理'
    checks.append('小额合同完成单级审批')

    big = call('POST', '/contract/draft', sales, dict(contract, contract_name='大额合同B', total_amount=2000000,
                                                purchase_amount=800000, stages=[
                                                    {'stage_name': '首款', 'pay_ratio': 50},
                                                    {'stage_name': '尾款', 'pay_ratio': 50},
                                                ]))
    big_id = big['id']
    call('POST', f'/contract/{big_id}/submit', sales)
    act('CONTRACT', big_id, manager)
    assert call('GET', f'/contract/{big_id}', admin)['status'] == '待总经理审批'
    act('CONTRACT', big_id, gm)
    assert call('GET', f'/contract/{big_id}', admin)['status'] == '已纳入管理'
    checks.append('大额合同完成财务经理与总经理两级审批')

    rejected = call('POST', '/contract/draft', sales, dict(contract, contract_name='驳回测试合同C'))
    rejected_id = rejected['id']
    call('POST', f'/contract/{rejected_id}/submit', sales)
    act('CONTRACT', rejected_id, manager, 'reject')
    assert call('GET', f'/contract/{rejected_id}', admin)['status'] == '已驳回'
    checks.append('合同审批驳回')

    invoice = {
        'contract_id': cid, 'invoice_amount': 300000, 'invoice_tax_rate': 0.06, 'invoice_date': '2026-08-10',
        'allocations': [
            {'stage_id': '1', 'allocated_amount': 200000},
            {'stage_id': '2', 'allocated_amount': 100000},
        ],
    }
    call('POST', '/invoice/draft', sales, invoice, expected=403)
    drafted_invoice = call('POST', '/invoice/draft', finance, invoice)
    iid = drafted_invoice['id']
    assert drafted_invoice['invoice_no'].startswith('INV')
    assert call('POST', f'/invoice/{iid}/submit', finance)['approval_status'] == '待财务经理审批'
    checks.append('财务员开票与分摊提交')

    act('INVOICE', iid, manager)
    assert call('GET', f'/invoice/{iid}', admin)['approval_status'] == '已批准'
    stages = call('GET', f'/contract/{cid}', admin)['stages']
    assert next(item for item in stages if item['stage_id'] == '1')['invoice_status'] == '已足额开票'
    checks.append('开票批准与合同付款阶段联动')

    receipt = {'contract_id': cid, 'invoice_id': iid, 'receipt_amount': 300000,
               'receipt_time': '2026-08-15 10:00:00', 'receipt_method': 'BANK', 'remark': '全款'}
    receipt_id = call('POST', '/receipt', finance, receipt)['id']
    paid = call('GET', f'/invoice/{iid}', admin)
    assert paid['received_flag'] == 1 and paid['received_amount'] == 300000
    checks.append('收款登记与发票足额标记')

    call('POST', f'/receipt/{receipt_id}/reverse', finance)
    unpaid = call('GET', f'/invoice/{iid}', admin)
    assert unpaid['received_flag'] == 0 and unpaid['received_amount'] == 0
    checks.append('冲销后金额与足额标记回转')

    records = call('GET', f'/contract/{cid}', admin)['approval_records']
    assert records and records[0]['approval_result'] == 'APPROVE'
    checks.append('合同审批留痕')
    execution = call('GET', '/report/execution', admin)
    divisions = call('GET', '/report/dept', admin)
    outstanding = call('GET', '/report/unreceived', admin)
    assert execution['total'] == 3 and len(divisions) == 1 and outstanding['total'] == 1
    checks.append('执行、部门、未收款报表')
    instances = call('GET', '/flow/instances', admin)
    assert instances['total'] == 4
    checks.append('审批流程实例与状态')

    call('POST', '/receipt', finance, receipt)
    actual = call('GET', '/report/execution', admin)
    row = next(item for item in actual['list'] if item['contract_no'] == draft['contract_no'])
    assert row['received_amount'] == 300000 and row['invoiced_amount'] == 300000
    checks.append('重新登记收款并核对只读报表证据')
    return {'checks': checks, 'admin_token': admin, 'contract_id': cid, 'contract_no': draft['contract_no'],
            'invoice_id': iid, 'expected_received_amount': row['received_amount']}


class ReferenceContractTests(unittest.TestCase):
    def test_pinned_reference_candidate_runs_original_contract_and_keeps_isolation(self):
        with tempfile.TemporaryDirectory() as base:
            root = Path(base)
            store = Store(root)
            row = store.create('原版销售合同', 'business', '')
            project_id = row['id']
            source = reference_inputs()
            fixture = dict(row, requirements=source['requirements'], models=source['models'],
                           prototype={'html': source['prototype_html'],
                                      'sha256': hashlib.sha256(source['prototype_html'].encode()).hexdigest()},
                           reference_adoption={'format': source['format'], 'commit': source['commit']})
            build_root = store.directory(project_id) / 'builds' / 'source-reference'
            receipt = build_application(fixture, build_root)
            store.mutate(project_id, lambda value: value.update(build=receipt))
            with sqlite3.connect(receipt['database_path']) as connection:
                self.assertEqual([connection.execute(f'SELECT count(*) FROM {name}').fetchone()[0]
                                  for name in receipt['tables']], [0] * len(receipt['tables']))
            mounted = Flask(__name__)
            mounted.register_error_handler(BusinessError, lambda error: ({'message': str(error)}, 400))
            register_runtime_routes(mounted, store)
            mount = '/app/' + project_id
            with mounted.test_client() as client:
                landing = client.get(mount + '/')
                self.assertEqual(landing.status_code, 200)
                self.assertIn(f'<base href="{mount}/">'.encode(), landing.data)
                self.assertEqual(client.post(mount + '/api/auth/login', json={
                    'username': 'admin', 'password': 'admin123'}).get_json()['success'], False)
                summary = exercise_contract(client, mount, receipt['accounts'])
                self.assertEqual(len(summary['checks']), 15)
                second = build_application(fixture, store.directory(project_id) / 'builds' / 'second-build')
                store.mutate(project_id, lambda value: value.update(build=second))
                self.assertEqual(client.get(mount + '/api/report/execution', headers={
                    'Authorization': 'Bearer ' + summary['admin_token']}).status_code, 401)
                source_file = Path(second['app_dir']) / 'web/index.html'
                source_file.write_text('tampered')
                self.assertEqual(client.get(mount + '/').status_code, 400)
            changed = dict(fixture, requirements=fixture['requirements'] + '不同来源')
            with self.assertRaisesRegex(BusinessError, '原始材料'):
                build_application(changed, build_root.with_name('unexpected'))
