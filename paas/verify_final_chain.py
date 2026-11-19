"""Extend the real GLM trial through prototype revision and pinned publication."""
import json
import os
import time
from pathlib import Path
from .server import create_app
from .app_runtime import Runtime
from .business import entities, model_document
from .workspace_lock import WorkspaceLease

ROOT=Path(__file__).resolve().parents[1]
DIRECTORY=Path(os.environ.get('PAAS_VERIFY_DIR', str(ROOT/'.local/verification/model-business'))).resolve()


def _run_trial():
    baseline=json.loads((DIRECTORY/'trial.json').read_text())
    path=DIRECTORY/'final-chain.json'
    evidence=json.loads(path.read_text()) if path.exists() else {'started_at':time.time(),'jobs':[]}
    client=create_app(DIRECTORY).test_client()
    pid=baseline['business_project'];evidence['business_project']=pid
    def save():path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2,default=str))
    def call(method,url,data=None):
        response=getattr(client,method)(url,json=data)
        if response.status_code not in (200,201,202):raise ValueError(f'{response.status_code} {url} {response.json}')
        return response.json
    def get(project_id=pid):return call('get','/api/v1/projects/'+project_id)
    def run(url,data=None):
        result=call('post',url,data or {});print('job',url,result['job_id'],flush=True)
        while True:
            job=call('get','/api/v1/jobs/'+result['job_id'])
            if job['status'] in ('succeeded','failed','cancelled'):
                evidence['jobs'].append(job);save()
                print(job['status'],job.get('error'),flush=True)
                if job['status']!='succeeded':raise ValueError(job.get('error'))
                return job['result']
            time.sleep(1)
    try:
        project=get()
        if not evidence.get('prototype_generated'):
            result=run(f'/api/v1/projects/{pid}/prototype')
            evidence['prototype_generated']={'id':result['id'],'sha256':result['sha256']};save()
        if not evidence.get('prototype_revised'):
            result=run(f'/api/v1/projects/{pid}/prototype',{'feedback':'请把页面和表单的数量字段帮助文字明确为“至少1”，保留全部已确认字段、角色、状态和审批步骤，不新增业务能力。'})
            evidence['prototype_revised']={'id':result['id'],'sha256':result['sha256'],'previous_id':result['previous_id']};save()
        project=get()
        if not project.get('prototype_confirmation'):
            call('post',f'/api/v1/projects/{pid}/confirm',{'scope':'prototype','revision':project['revision'],'decision':'隔离验收夹具核对需求、页面与至少1提示后确认'})
        html=client.get(f'/api/v1/projects/{pid}/prototype/html')
        assert html.status_code==200 and '至少1' in html.get_data(as_text=True)
        assert 'allow-scripts' in html.headers['Content-Security-Policy']
        project=get()
        if project.get('models_prototype_sha256')!=project['prototype']['sha256']:
            run(f'/api/v1/projects/{pid}/models',{'repair':True,'feedback':'依据本次已确认界面原型核对MU的页面、字段帮助与操作引用，并绑定该原型。原需求、角色权限、审批和现有对象字段不变。'})
        project=get()
        if not project.get('models_confirmation'):
            call('post',f'/api/v1/projects/{pid}/confirm',{'scope':'models','revision':project['revision'],'decision':'隔离验收夹具确认与当前原型绑定的模型'})
        project=get()
        if not project.get('build'):
            run(f'/api/v1/projects/{pid}/build')
        project=get()
        rt=Runtime(project['build']['output_dir']);entity=entities(project['models'])[0]
        behaviors=model_document(project['models'],'BEHAVIOR')['behaviors']
        actors=[rt.login(a['username'],a['password']) for a in project['build']['accounts']]
        create=next(b for b in behaviors if b['runtime']['operation']=='CREATE')
        submit=next(b for b in behaviors if b['runtime'].get('targetState')=='待审批')
        approve=next(b for b in behaviors if b['runtime'].get('targetState')=='已批准')
        def principal(behavior):
            return next(actor for actor in actors if rt._grants(behavior,actor))
        applicant=principal(create);manager=principal(approve)
        if not evidence.get('application_records'):
            records=[]
            for material,quantity in [('展架',2),('宣传册',8),('展架',3)]:
                record=rt.create(entity['id'],{'materialName':material,'quantity':quantity},applicant)
                record=rt.act(submit['id'],record['id'],{'_version':record['version']},applicant)
                record=rt.act(approve['id'],record['id'],{'_version':record['version']},manager)
                records.append(record)
            draft=rt.create(entity['id'],{'materialName':'展架','quantity':100},applicant)
            report=model_document(project['models'],'REPORT')['query_reports'][0]
            rows=rt.query(report['id'],{'status':'已批准'},manager)['rows'];assert len(rows)==3,rows
            evidence['application_records']={'approved_ids':[r['id'] for r in records],'draft_id':draft['id'],'query_rows':rows};save()
        if not evidence.get('release'):
            project=get()
            plan=project['build']['acceptance_plan']
            notes = {'roles': '按候选角色登录，以申请人与主管角色执行不同授权行为。',
                     'ui': '确认过的页面和行为引用通过候选构建映射校验。',
                     'write': '已创建并读取四张物料草稿；其中三张成功提交。',
                     'rules': '有效领用数量通过检查，状态前提在实际流程中执行。'}
            checks = {item['id']: {'passed': True, 'result': notes.get(item['id'],
                      '三张申请通过实际审批，按已批准状态查询并核对三条结果。')}
                      for item in plan['checks']}
            evidence['candidate_acceptance']=call('post',f'/api/v1/projects/{pid}/build/acceptance',{
                'revision':project['revision'],'candidate_id':plan['id'],'checks':checks})['acceptance'];save()
            result=call('post',f'/api/v1/projects/{pid}/releases',{'version':'0.1.0','revision':project['revision'],'acknowledged':True})
            evidence['release']=result['release'];save()
        release=evidence['release'];release_url=release['url'];rid=release['id']
        assert client.get(release_url).status_code==200
        credential=next(a for a in project['build']['accounts'] if manager['roles'][0]==a['role_id'])
        call('post',release_url+'api/login',{'username':credential['username'],'password':credential['password']})
        report=model_document(project['models'],'REPORT')['query_reports'][0]
        rows=call('post',release_url+'api/reports/'+report['id'],{'status':'已批准'})['rows'];assert len(rows)==3,rows
        print('PINNED RELEASE VERIFIED',rid,flush=True)
        if not evidence.get('analysis_project'):
            a=call('post','/api/v1/projects',{'kind':'analysis','name':'合并验收 · 新构建物料统计','description':'只统计真实业务表flag=1且status=已批准的申请，按materialName分组，SUM(quantity)为领用数量，COUNT(DISTINCT id)为申请笔数。不限定日期，time_range=null。只用业务表，不包含runtime内部表。'})
            evidence['analysis_project']=a['id'];save()
            call('post',f"/api/v1/projects/{a['id']}/source",{'source_project_id':pid})
        aid=evidence['analysis_project'];a=get(aid)
        if not a.get('requirements'):
            run(f'/api/v1/projects/{aid}/requirements')
        a=get(aid)
        if not a.get('requirements_confirmation'):
            call('post',f'/api/v1/projects/{aid}/confirm',{'scope':'requirements','revision':a['revision']})
        if not a.get('models'):
            run(f'/api/v1/projects/{aid}/models')
        a=get(aid)
        if not a.get('models_confirmation'):
            call('post',f'/api/v1/projects/{aid}/confirm',{'scope':'models','revision':a['revision']})
        result=run(f'/api/v1/projects/{aid}/analysis',{'question':'按materialName分组，汇总flag=1且status=已批准的领用量和申请笔数，列名total_quantity和request_count。全部物料，不限时间，不要截断。'})
        assert sorted(row['total_quantity'] for row in result['rows'])==[5,8],result['rows']
        evidence['analysis']={k:result[k] for k in ('sql','parameters','rows','source_hash','model_called')};save()
        # A draft edit must never alter the independent published version.
        if not evidence.get('draft_independence'):
            project=get()
            call('patch',f'/api/v1/projects/{pid}',{'revision':project['revision'],'description':project['description']+'\n后续草案：调整说明文字，尚未确认。','reopen_stage':0})
            assert get().get('build') is None
            rows=call('post',release_url+'api/reports/'+report['id'],{'status':'已批准'})['rows'];assert len(rows)==3
            package=client.get(f'/api/v1/projects/{pid}/releases/{rid}/download');assert package.status_code==200,package.get_data(as_text=True)
            evidence['draft_independence']={'published_query_rows':len(rows),'source_package_bytes':len(package.data),'url':release_url};save()
        evidence.update(success=True,finished_at=time.time());evidence.pop('error',None);save()
        print('FINAL MERGED CHAIN VERIFIED',path,flush=True)
    except Exception as error:
        evidence.update(success=False,error=str(error),finished_at=time.time());save();raise


def main():
    path = DIRECTORY / 'final-chain.json'
    if path.exists() and json.loads(path.read_text()).get('success'):
        print('FINAL MERGED CHAIN ALREADY VERIFIED', path)
        return
    baseline_path = DIRECTORY / 'trial.json'
    if not baseline_path.exists() or not json.loads(baseline_path.read_text()).get('success'):
        raise SystemExit('先在同一 PAAS_VERIFY_DIR 成功运行 paas.verify_live；原始失败记录保留。')
    with WorkspaceLease(DIRECTORY):
        _run_trial()


if __name__=='__main__':main()
