"""Actual GLM two-pass extraction and grounded Q&A on fictional SOP text."""
import json
import os
import re
import time
import sys
from pathlib import Path
from . import knowledge
from .provider import status as provider_status
from .server import create_app
from .workspace_lock import WorkspaceLease

DIRECTORY = Path(os.environ.get('PAAS_KNOWLEDGE_VERIFY_DIR', str(Path(__file__).resolve().parents[1] / '.local/verification/model-knowledge'))).resolve()


def _verify_trial(project, answer, jobs):
    docs = {doc['path']: doc for doc in project['documents']}
    hashes = answer.get('source_hashes', {})
    knowledge.validate(project['knowledge_inventory'], project['documents'], skeleton=True)
    models = {kind: project['models'][kind + '.yaml'] for kind in knowledge.KINDS}
    knowledge.validate(models, project['documents'])
    if answer.get('retrieval', {}).get('mode') not in ('scenario', 'concept_only'):
        raise ValueError('试验回答未通过已确认知识模型定位来源')
    citations = answer.get('citations') or []
    if not citations or any(citation['source'] not in docs
                            or citation['quote'] not in docs[citation['source']]['text']
                            or hashes.get(citation['source']) != docs[citation['source']]['sha256']
                            for citation in citations):
        raise ValueError('试验引用未能逐字回读并核对原文摘要')
    returned = answer['answer']
    quotes = '\n'.join(citation['quote'] for citation in citations)
    if ('主管' not in returned or not re.search(r'登记|记录', returned)
            or not re.search(r'(不得|不能|不可|禁止|不允许).{0,12}(扩大|放宽)容差', returned)
            or '登记差异并交主管复核' not in quotes or '不得自行扩大容差' not in quotes):
        raise ValueError('试验回答或其证据遗漏了超差登记、交主管复核或禁止放宽容差')
    expected_model = provider_status()['model']
    calls = [call for job in jobs if job.get('status') == 'succeeded' for call in job.get('calls', [])
             if call.get('status') == 'succeeded' and call.get('output_sha256')]
    required = {'knowledge.inventory', 'knowledge.fill', 'knowledge.locate', 'knowledge.answer'}
    if required - {call['purpose'] for call in calls} or any(call['model'] != expected_model for call in calls):
        raise ValueError('试验缺少配置模型的实际萃取或问答调用记录')
    return {'model': expected_model, 'purposes': [call['purpose'] for call in calls],
            'inventory_counts': {kind: len(knowledge._all_items(project['knowledge_inventory'], kind))
                                 for kind in knowledge.KINDS},
            'retrieval_mode': answer['retrieval']['mode'], 'answer_mode': answer['answer_mode'],
            'verbatim_citations': len(citations), 'fixture_business_rules_verified': True}


def _run_trial():
    root = DIRECTORY
    root.mkdir(parents=True, exist_ok=True)
    client = create_app(root).test_client()
    evidence_file = root / 'trial.json'
    evidence = json.loads(evidence_file.read_text()) if '--resume' in sys.argv and evidence_file.exists() else {'started_at': time.time(), 'jobs': []}
    def save():
        (root / 'trial.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    def call(method, url, data=None):
        response = getattr(client, method)(url, json=data)
        if response.status_code not in (200, 201, 202):
            raise ValueError(str(response.json))
        return response.json
    def run(url, data=None):
        row = call('post', url, data or {})
        while True:
            job = call('get', '/api/v1/jobs/' + row['job_id'])
            if job['status'] in ('succeeded', 'failed', 'cancelled'):
                evidence['jobs'].append(job);save()
                print(job['status'], job.get('error'), flush=True)
                if job['status'] != 'succeeded':
                    raise ValueError(job.get('error'))
                return job['result']
            time.sleep(1)
    try:
        project = (call('get','/api/v1/projects/' + evidence['project_id']) if '--resume' in sys.argv and evidence.get('project_id') else
                   call('post','/api/v1/projects',{'kind':'knowledge','name':'隔离验收 · 样衣尺寸复核SOP','description':'完全虚构的两轮萃取与原文问答验收'}))
        pid = project['id'];evidence['project_id']=pid;save()
        document = '''# 样衣尺寸复核规范（虚构验收资料）
尺寸复核是把客户确认的尺码表与样衣实测值逐项比较的检查过程。
输入为客户确认尺码表和同一件样衣的实测记录。
尺寸复核的步骤是：先检查尺码版本一致；再按同一测量部位比较数值；最后逐项记录差异。
输出为尺寸复核表和待处理差异清单。
尺寸复核表是一份逐项记录测量部位、目标值、实测值和差异的检查工具。
尺寸复核表用于尺寸复核，不代替客户确认的尺码表。
如何复核样衣尺寸：收到样衣后，先执行尺寸复核，再记录结论。
容差由客户确认的尺码表给定，不得自行扩大容差。
尺寸差超出容差时，登记差异并交主管复核，不能直接判定通过。
'''
        if not project.get('documents'):
            call('post',f'/api/v1/projects/{pid}/knowledge/source',{'documents':[{'name':'尺寸复核.md','text':document}]})
        if not project.get('knowledge_inventory'):
            run(f'/api/v1/projects/{pid}/knowledge/extract')
        project=call('get',f'/api/v1/projects/{pid}')
        call('post',f'/api/v1/projects/{pid}/confirm',{'scope':'inventory','revision':project['revision'],'decision':'验收夹具核对原文后确认候选清单'})
        run(f'/api/v1/projects/{pid}/knowledge/extract')
        project=call('get',f'/api/v1/projects/{pid}')
        call('post',f'/api/v1/projects/{pid}/confirm',{'scope':'models','revision':project['revision'],'decision':'验收夹具确认来源及模型'})
        result=run(f'/api/v1/projects/{pid}/knowledge/ask',{'question':'尺寸差超出容差时，下一步该怎样处理？可以自行放宽容差吗？'})
        checks = _verify_trial(project, result, evidence['jobs'])
        evidence.update(success=True, answer=result, checks=checks)
        evidence.pop('error',None)
    except Exception as error:
        evidence.update(success=False,error=str(error));raise
    finally:
        evidence['finished_at']=time.time();save()
    print('KNOWLEDGE CHAIN VERIFIED',root/'trial.json',flush=True)


def main():
    evidence_file = DIRECTORY / 'trial.json'
    if evidence_file.exists():
        if '--resume' not in sys.argv:
            raise SystemExit('已有知识试验记录；用 --resume 续跑，或设置 PAAS_KNOWLEDGE_VERIFY_DIR 为新的独立目录，原记录不会覆盖。')
        if json.loads(evidence_file.read_text()).get('success'):
            print('KNOWLEDGE CHAIN ALREADY VERIFIED', evidence_file)
            return
    with WorkspaceLease(DIRECTORY):
        _run_trial()


if __name__ == '__main__':
    main()
