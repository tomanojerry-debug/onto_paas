"""Actual-provider integration trial on a new, explicitly fictional project.

Run with the platform environment. Artifacts stay in a separate verification
store, and confirmations below are test-fixture decisions, not user approvals.
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path

from .server import create_app
from .app_runtime import Runtime
from .business import entities, model_document
from .provider import LLM
from .workspace_lock import WorkspaceLease

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = Path(os.environ.get('PAAS_VERIFY_DIR', str(ROOT / '.local/verification/model-business'))).resolve()
DESCRIPTION = '''这是隔离验收用虚构需求：展会物料领用审批。只管理“物料领用申请”一个聚合，英文别名MaterialRequest；
用户输入materialName（物料名称，必填文本）和quantity（领用数量，必填整数且至少1），系统保存status（草稿、待审批、已批准）
和submitterId（提交人）。系统自动编号，不另外引入必填业务编号。两角色：申请人可保存草稿、提交、查看本人申请，主管可审批并查看所有。
创建初始草稿；草稿提交后为待审批并自动记录提交人；主管批准后为已批准。主管与提交人必须不是同一人。
不需要驳回分支，本次版本只有单级批准。批准之后不再修改；没有跨对象联动、不管理库存、不发消息、不对接外部系统。
只有一张查询报表：可选按status筛选，列出materialName、quantity、status，主管查全部，申请人查自己。
需要一个最简菜单与申请表、审批操作、查询列表，按作者MU建模；先确认可操作HTML原型，再生成七模型。
只使用本段的业务范围；企业规则均为本虚构验收夹具明确给定。开发采用作者七模型，字段严格按上述英文名，
M2必须含CREATE草稿、TRANSITION提交、TRANSITION批准、QUERY查询，不能增加空泛业务或不可执行条件。'''


def _run_trial():
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    app = create_app(DIRECTORY)
    client = app.test_client()
    existing = DIRECTORY / 'trial.json'
    evidence = json.loads(existing.read_text()) if '--resume' in sys.argv and existing.exists() else {'started_at': time.time(), 'provider': client.get('/api/v1/status').json['provider'], 'steps': []}
    def save():
        (DIRECTORY / 'trial.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str))
    def call(method, url, data=None, expected=(200, 201, 202)):
        response = getattr(client, method)(url, json=data)
        if response.status_code not in expected:
            raise RuntimeError(f'{method} {url}: {response.status_code} {response.json}')
        return response.json
    def run(url, data=None):
        submitted = call('post', url, data or {})
        jid = submitted['job_id']
        print('job', jid, url, flush=True)
        while True:
            row = call('get', '/api/v1/jobs/' + jid)
            if row['status'] in ('failed', 'succeeded', 'cancelled'):
                evidence['steps'].append({'url': url, 'job': row})
                save()
                print(row['status'], row.get('error'), 'calls', len(row.get('calls', [])), flush=True)
                if row['status'] != 'succeeded':
                    raise RuntimeError(row.get('error'))
                return row['result']
            time.sleep(1)
    try:
        p = (call('get','/api/v1/projects/' + evidence['business_project']) if '--resume' in sys.argv and evidence.get('business_project')
             else call('post', '/api/v1/projects', {'kind': 'business', 'name': '隔离验收 · 展会物料领用', 'description': DESCRIPTION}))
        pid = p['id']; evidence['business_project'] = pid; save()
        for stage in range(p['stage'],8):
            message = DESCRIPTION if stage == 0 else f'进入{stage}阶段。前文需求的所有规则均已由本虚构验收夹具明确给出。请只整理本阶段内容及建议，若无信息缺口，ready_for_confirmation=true，等待我单独确认。'
            run(f'/api/v1/projects/{pid}/chat', {'message': message})
            p = call('get', '/api/v1/projects/' + pid)
            if not p['current_stage_result'].get('ready_for_confirmation'):
                raise RuntimeError('当前虚构需求仍有待澄清项：' + json.dumps(p['current_stage_result'], ensure_ascii=False))
            p = call('post', f'/api/v1/projects/{pid}/confirm', {'scope': 'stage', 'stage': stage, 'revision': p['revision'], 'decision': '隔离测试夹具明确确认本阶段建议，仅适用虚构验收'})
        if not p.get('requirements'):
            run(f'/api/v1/projects/{pid}/requirements')
        p = call('get', '/api/v1/projects/' + pid)
        if not p.get('requirements_confirmation'):
            call('post', f'/api/v1/projects/{pid}/confirm', {'scope': 'requirements', 'revision': p['revision'], 'decision': '确认虚构验收规格'})
        p = call('get', '/api/v1/projects/' + pid)
        if not p.get('prototype'):
            run(f'/api/v1/projects/{pid}/prototype')
        p = call('get', '/api/v1/projects/' + pid)
        if not p.get('prototype_confirmation'):
            call('post', f'/api/v1/projects/{pid}/confirm', {'scope': 'prototype', 'revision': p['revision'], 'decision': '验收夹具核对当前需求与原型后确认'})
        p = call('get', '/api/v1/projects/' + pid)
        if '--repair' in sys.argv:
            run(f'/api/v1/projects/{pid}/models', {'repair': True, 'feedback': '查询行为同时要求OWN与ALL两个权限，但两个角色分别只授其一，实际无法查询。保留两角色各自的数据范围、同一查询报表及全部已确认业务，修复互相矛盾的权限门槛，不全局放宽权限。'})
        elif not p.get('models'):
            run(f'/api/v1/projects/{pid}/models')
        p = call('get', '/api/v1/projects/' + pid)
        if not p.get('models_confirmation'):
            call('post', f'/api/v1/projects/{pid}/confirm', {'scope': 'models', 'revision': p['revision'], 'decision': '确认本次真实模型生成结果'})
        if not p.get('build'):
            run(f'/api/v1/projects/{pid}/build')
        p = call('get', '/api/v1/projects/' + pid)
        rt = Runtime(p['build']['output_dir'])
        entity = entities(p['models'])[0]
        behaviors = model_document(p['models'], 'BEHAVIOR')['behaviors']
        accounts = p['build']['accounts']
        actors = [rt.login(account['username'], account['password']) for account in accounts]
        def principal_for(behavior):
            for actor in actors:
                try:
                    rt._authorize(behavior, actor)
                    return actor
                except Exception:
                    pass
            # Compile model roles identify the public runtime actor contract.
            roles = model_document(p['models'], 'ACTOR')['roles']
            perms = set(behavior.get('requiredPermissions', []))
            for account, actor in zip(accounts, actors):
                role = next(r for r in roles if r.get('roleId',r.get('id')) == account['role_id'])
                if perms.issubset(set(role.get('permissions', []))):
                    return actor
            raise RuntimeError('No role for behavior ' + behavior['id'])
        create = next(b for b in behaviors if b.get('runtime', {}).get('operation') == 'CREATE')
        transitions = [b for b in behaviors if b.get('runtime', {}).get('operation') == 'TRANSITION']
        submit = next(b for b in transitions if b['runtime'].get('targetState') == '待审批')
        approve = next(b for b in transitions if b['runtime'].get('targetState') == '已批准')
        applicant = principal_for(create); manager = principal_for(approve)
        records = []
        for name, quantity in [('展架', 2), ('宣传册', 8), ('展架', 3)]:
            row = rt.create(entity['id'], {'materialName': name, 'quantity': quantity}, applicant)
            row = rt.act(submit['id'], row['id'], {'_version': row['version']}, applicant)
            row = rt.act(approve['id'], row['id'], {'_version': row['version']}, manager)
            assert row['status'] == '已批准'
            records.append(row)
        report = model_document(p['models'], 'REPORT')['query_reports'][0]
        result = Runtime(p['build']['output_dir']).query(report['id'], {'status': '已批准'}, manager)
        assert len(result['rows']) == 3, result
        draft = rt.create(entity['id'], {'materialName': '展架', 'quantity': 100}, applicant)
        assert draft['status'] == '草稿'
        evidence['application'] = {'entity': entity['id'], 'created': len(records), 'excluded_draft_quantity':100, 'query_rows': result['rows'], 'model_hash':p['build']['model_hash']}
        save()
        print('real generated app create-submit-approve-query succeeded', flush=True)
        assistant = LLM(trace_dir=Path(p['build']['app_dir'])/'data/ai-runs/verification-assistant',max_calls=8)
        answer = rt.chat('请查询状态为已批准的物料领用申请，列出真实名称、数量和状态。',manager,assistant,entity['id'])
        assert answer.get('query') and len(answer['query']['rows'])==3,answer
        evidence['application_ai']={'result':answer,'calls':assistant.calls};save()

        analysis_desc = '分析这个物料领用应用的真实业务表：只统计flag=1且status为已批准的申请，以记录id去重。按materialName分组，SUM(quantity)为领用数量、COUNT(DISTINCT id)为申请笔数。不限定时间。只用单张业务表，不分析runtime_内部表。'
        a = call('post', '/api/v1/projects', {'kind':'analysis','name':'隔离验收 · 物料领用统计','description':analysis_desc})
        aid=a['id'];evidence['analysis_project']=aid;save()
        call('post', f'/api/v1/projects/{aid}/source', {'source_project_id':pid})
        run(f'/api/v1/projects/{aid}/requirements')
        a=call('get',f'/api/v1/projects/{aid}')
        call('post',f'/api/v1/projects/{aid}/confirm',{'scope':'requirements','revision':a['revision']})
        run(f'/api/v1/projects/{aid}/models')
        a=call('get',f'/api/v1/projects/{aid}')
        call('post',f'/api/v1/projects/{aid}/confirm',{'scope':'models','revision':a['revision']})
        result=run(f'/api/v1/projects/{aid}/analysis',{'question':'按物料汇总已批准且flag=1的领用数量和申请笔数，按物料名称排序。领用数量结果列使用total_quantity，申请笔数使用request_count。'})
        assert sorted(float(row.get('total_quantity',row.get('quantity',next((v for k,v in row.items() if 'quantity' in k.lower() or '数量' in k),-1)))) for row in result['rows']) == [5.0,8.0],result['rows']
        evidence['analysis']={k:result[k] for k in ('sql','rows','source_hash','model_called')}
        evidence['success']=True
        evidence.pop('error',None)
    except Exception as error:
        evidence['success']=False;evidence['error']=str(error);save()
        print('TRIAL FAILED',str(error),flush=True)
        raise
    finally:
        evidence['finished_at']=time.time();save()
    print('FULL CHAIN VERIFIED',DIRECTORY/'trial.json',flush=True)


def main():
    evidence_file = DIRECTORY / 'trial.json'
    if evidence_file.exists():
        if '--resume' not in sys.argv:
            raise SystemExit('已有试验记录；用 --resume 续跑，或设置 PAAS_VERIFY_DIR 为新的独立目录，原记录不会覆盖。')
        if json.loads(evidence_file.read_text()).get('success'):
            print('FULL CHAIN ALREADY VERIFIED', evidence_file)
            return
    with WorkspaceLease(DIRECTORY):
        _run_trial()


if __name__ == '__main__':
    main()
