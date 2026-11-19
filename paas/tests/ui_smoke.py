"""Browser wiring checks for the PaaS UI. --fixture never calls a real model.
Run using an environment with playwright installed. --base-url runs read-only route checks.
"""
from __future__ import annotations
import argparse
import json
import re
import threading
import time
from email.parser import BytesParser
from email.policy import default
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs
from playwright.sync_api import sync_playwright, expect

WEB = Path(__file__).resolve().parents[1] / 'web'

ANALYSIS_IMPORT_FILES = (
    'm1_object_model.yaml',
    'm2_behavior_model.yaml',
    'm3_rule_model.yaml',
    'm4_scenario_model.yaml',
    'm_metric_model.yaml',
    'data_mapping.yaml',
)


def fixture_server(*, adaptive_requirements=False):
    projects = {
        'business-built': dict(id='business-built',kind='business',name='设备借用管理',description='由本项目的业务需求与模型构建。',stage=8,revision=9,messages=[],requirements='## 附录C\n当前项目需求文档',requirement_audit={'passed':False,'errors':['业务条件未确认'],'checks':[{'number':1,'item':'范围明确','status':'通过','passed':True,'reason':'第一节已写明范围'},{'number':2,'item':'权限完整','status':'未通过','passed':False,'reason':'岗位访问范围仍需确认'}]},models={'m1-object-model.yaml':{'model_type':'OBJECT','aggregates':[{'id':'AGG-EQUIPMENT','name':'设备','attributes':[{'name':'name','type':'String'}]}]}},build={'output_dir':'/tmp/independent-paas-test/build','version':'1'}),
        'analysis-one': dict(id='analysis-one',kind='analysis',name='借用情况分析',description='按设备统计借用次数。',revision=2,messages=[],models_confirmation={'revision':2},source={'name':'借用数据.db','hash':'0123456789abcdef','schema':{'tables':[{'name':'equipment','columns':[{'name':'id','type':'TEXT','pk':1},{'name':'name','type':'TEXT'}]}]}},requirements={'analysis_goals':[{'title':'设备借用统计'}]},models={
            'm1-object-model.yaml':{'model_type':'OBJECT','entities':[{'id':'ENT-EQUIPMENT','name':'设备'}]},
            'm_metric_model.yaml': {'model_type': 'METRIC', 'metrics': [
                {'id': 'METRIC-COUNT', 'name': '借用次数', 'description': '按设备计数', 'formula': 'COUNT(*)',
                 'unit': '次', 'depends_on_entities': ['ENT-EQUIPMENT'],
                 'sql_template': 'SELECT COUNT(*) FROM equipment'},
                {'id': 'METRIC-TYPES', 'name': '设备种类数', 'description': '统计设备种类', 'unit': '种'},
            ]},
            'data_mapping.yaml':{'entity_mappings':[{'entity_id':'ENT-EQUIPMENT','entity_name':'设备','tables':[{'table':'equipment'}],'field_mappings':[{'ontology_field':'name','ontology_label':'设备名称','db_table':'equipment','db_column':'name'}]}]}}),
        'knowledge-one': dict(id='knowledge-one',kind='knowledge',name='设备借用制度',description='设备借用相关资料。',revision=1,messages=[],models={},documents=[],confirmations=[{'scope':'models','revision':1}],models_confirmation=None)
    }
    built = projects['business-built']
    built['models']['m2-behavior-model.yaml'] = {'model_type':'BEHAVIOR','behaviors':[]}
    built.update(requirements_confirmation={'revision':9}, models_confirmation={'revision':9},
                 prototype={'id':'prototype-1','revision':9,'sha256':'prototype-sha-1','summary':'设备借用页面','pages':[{'name':'设备列表'}],'html':'<h1>历史设备页面</h1>'},
                 prototype_confirmation={'revision':9}, models_prototype_sha256='prototype-sha-1',
                 releases=[{'id':'release-one','version':'1.0.0','url':'/apps/business-built/versions/release-one/','build':{'version':'1'},'created_at':1795000000}],
                 published_release_id='release-one')
    built['build']['url']='/app/business-built/'
    projects['business-prototype'] = dict(id='business-prototype',kind='business',name='原型闭环验证',description='核对原型和独立发布',stage=8,revision=1,messages=[],requirements='## 当前原型需求',models={})
    projects['business-graph'] = dict(id='business-graph',kind='business',name='完整图谱检查',revision=1,models={'m1-object-model.yaml':{'model_type':'OBJECT','entities':[{'id':'NODE-'+str(i),'name':'实体 '+str(i),'related_to':'NODE-'+str((i+1)%135)} for i in range(135)]}},messages=[])
    if adaptive_requirements:
        built.update(models={}, models_confirmation=None, requirement_audit={'passed': True})
        projects['business-prototype'].update(
            requirements_confirmation={'revision': 1, 'prototype_only': True},
            prototype={**built['prototype'], 'id': 'preview-only-prototype', 'revision': 1},
            prototype_confirmation={'revision': 1},
            requirement_audit={'passed': True},
        )
        projects['business-graph'].update(
            requirements='## 未确认原型的需求',
            requirements_confirmation={'revision': 1},
            prototype={**built['prototype'], 'id': 'unconfirmed-prototype', 'revision': 1},
            prototype_confirmation=None,
            requirement_audit={'passed': True},
        )
    jobs={}; requests=[]
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self,*a,**kw): super().__init__(*a,directory=str(WEB),**kw)
        def log_message(self,*a): pass
        def response(self,value,status=200):
            data=json.dumps(value,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def do_GET(self):
            path=urlparse(self.path).path
            if path == '/api/v1/status':
                return self.response({
                    'status': 'ready', 'provider': {'configured': True, 'name': 'GLM', 'model': 'glm-5.3-flash'},
                    'capabilities': {'adaptive_requirements': adaptive_requirements},
                })
            if path=='/api/v1/projects':return self.response({'projects':list(projects.values())})
            if path.startswith('/api/v1/jobs/'):return self.response(jobs[path.rsplit('/',1)[-1]])
            if path.startswith('/api/v1/projects/'):
                project=projects[path.split('/')[4]]
                if path.endswith('/prototype/html'):
                    prototype_id=parse_qs(urlparse(self.path).query).get('prototype_id',[None])[0];prototype=next((item for item in project.get('prototype_history',[])+[project['prototype']] if item.get('id')==prototype_id),project['prototype']);data=prototype['html'].encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(data);return
                return self.response(project)
            if path.startswith(('/apps/','/app/')):
                self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write('<h1>设备借用管理</h1>'.encode());return
            return super().do_GET()
        def mutate(self):
            path = urlparse(self.path).path
            raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            content_type = self.headers.get('Content-Type', '')
            payload = {}
            database_name = None
            database_bytes = None
            if 'application/json' in content_type:
                payload = json.loads(raw or b'{}')
            elif path == '/api/v1/projects' and 'multipart/form-data' in content_type:
                message = BytesParser(policy=default).parsebytes(
                    b'Content-Type: ' + content_type.encode() + b'\r\n\r\n' + raw
                )
                for part in message.iter_parts():
                    name = part.get_param('name', header='content-disposition')
                    if name == 'database':
                        database_name = part.get_filename()
                        database_bytes = part.get_payload(decode=True)
                    else:
                        payload[name] = part.get_payload(decode=True).decode()
            requests.append({'method': self.command, 'path': path, 'payload': payload,
                             'raw_size': len(raw), 'multipart_database': database_name is not None})
            if path == '/api/v1/projects':
                project = dict(payload)
                selection = payload.get('source')
                if database_bytes == b'FAIL_UPLOAD':
                    return self.response({'error': '夹具模拟数据库拒绝'}, 400)
                if database_name or selection:
                    source = projects['analysis-one']['source']
                    source_name = database_name
                    if selection:
                        original = projects[selection['source_project_id']]
                        release_id = selection.get('release_id')
                        release = next((row for row in original.get('releases', []) if row['id'] == release_id), None)
                        if release:
                            source_name = f"{original['name']} · 已发布版本 {release['version']} · 数据快照"
                        elif selection.get('source_kind') == 'candidate' and original.get('build'):
                            source_name = f"{original['name']} · 候选构建 · 数据快照"
                        else:
                            return self.response({'error': '夹具模拟无效的应用版本'}, 400)
                    project['source'] = {**source, 'name': source_name}
                ident = 'new-' + str(len(projects))
                project.update(id=ident, revision=1, stage=0, messages=[], models={})
                projects[ident] = project
                return self.response(project, 201)
            if path.startswith('/api/v1/jobs/') and path.endswith('/cancel'):
                ident=path.split('/')[4];jobs[ident].update(status='cancelled',error='已按用户请求取消');return self.response(jobs[ident])
            bits=path.split('/');p=projects[bits[4]];operation='/'.join(bits[5:])
            if operation=='chat' and payload.get('message')=='FAIL_CHAT':return self.response({'error':'夹具模拟请求拒绝'},409)
            if operation=='prototype' and payload.get('feedback')=='FAIL_PROTOTYPE':return self.response({'error':'夹具模拟原型请求拒绝'},409)
            if operation=='build/acceptance':
                plan=p['build']['acceptance_plan']
                if payload.get('revision')!=p['revision'] or payload.get('candidate_id')!=plan['id'] or set(payload.get('checks', {}))!={'roles'}:
                    return self.response({'error':'验收与候选不一致'},409)
                if payload['checks']['roles'].get('passed') is not True:
                    return self.response({'error':'验收项目未通过'},409)
                acceptance={'candidate_id':plan['id'],'checks':[{'id':'roles','result':payload['checks']['roles']['result']}],
                            'automatic':{'runtime_entry':'fixture'}}
                p['build']['acceptance']=acceptance
                return self.response({'acceptance':acceptance})
            if operation=='releases':
                if payload.get('version')=='FAIL_RELEASE':return self.response({'error':'夹具模拟发布拒绝'},409)
                if payload.get('revision')!=p['revision'] or payload.get('acknowledged') is not True:return self.response({'error':'发布确认失效'},409)
                release={'id':'release-'+str(len(p.get('releases',[]))+1),'version':payload['version'],'build':p['build'],'created_at':time.time()}
                release['url']='/apps/'+p['id']+'/versions/'+release['id']+'/'
                p.setdefault('releases',[]).append(release);p['published_release_id']=release['id'];return self.response({'release':release,'project':p},201)

            if operation=='confirm':
                if payload['scope']=='stage':p['stage']+=1;p['current_stage_result']=None
                else:p[payload['scope']+'_confirmation']={'revision':p['revision']}
                return self.response(p)
            if operation == 'models/import':
                files = payload.get('files', {})
                if set(files) != set(ANALYSIS_IMPORT_FILES) or payload.get('revision') != p['revision']:
                    return self.response({'error': '夹具模拟导入输入不匹配'}, 409)
                if any('FAIL_IMPORT' in text for text in files.values()):
                    return self.response({'error': '夹具模拟五模型导入拒绝'}, 422)
                previous_models = p['models']
                p.update(
                    revision=p['revision'] + 1,
                    model_standard='onto-paas-seven-v1',
                    source_bindings=previous_models['data_mapping.yaml'],
                    models_confirmation=None,
                    model_import={'format': 'onto-dataanalyse-five', 'warnings': ['保留原始字段映射']},
                    model_diagnostics=['示例场景引用尚未校验'],
                    models={
                        'm1-object-model.yaml': {'model_type': 'OBJECT', 'entities': [{'id': 'ENT-EQUIPMENT', 'name': '设备'}]},
                        'm2-behavior-model.yaml': {'model_type': 'BEHAVIOR', 'behaviors': []},
                        'm3-rule-model.yaml': {'model_type': 'RULE', 'rules': []},
                        'm5-actor-model.yaml': {'model_type': 'ACTOR', 'actors': []},
                        'm6-flow-model.yaml': {'model_type': 'FLOW', 'flows': [{
                            'id': 'SCN-COUNT', 'name': '借用次数分析流程', 'flowType': 'ANALYSIS',
                            'startActivity': 'S1', 'keyMetrics': ['METRIC-COUNT'],
                            'activities': [{'activityId': 'S1', 'activityType': 'DATA_COLLECT', 'description': '汇总设备借用记录'}],
                        }]},
                        'm7-report-model.yaml': {'model_type': 'REPORT', 'query_reports': [], 'metrics': previous_models['m_metric_model.yaml']['metrics']},
                        'mu-ui-model.yaml': {'model_type': 'UI', 'screens': []},
                    },
                )
                return self.response(p)
            if operation.startswith('models/'):
                try:value=json.loads(payload['text'])
                except Exception:return self.response({'error':'模型文件不能解析'},422)
                filename = unquote(operation[7:])
                if filename == 'data_mapping.yaml' and p.get('source_bindings'):
                    if value != p['source_bindings']:
                        p.update(source_bindings=value, revision=p['revision'] + 1, models_confirmation=None)
                    return self.response(p)
                if value==p['models'][filename]:return self.response(p)
                p['models'][filename]=value;p['revision']+=1;p['models_confirmation']=None
                if p.get('build'):p.setdefault('build_history',[]).append(p['build'])
                p['build']=None
                return self.response(p)
            p['revision']+=1
            if operation=='prototype':
                if p.get('prototype'):p.setdefault('prototype_history',[]).append(p['prototype'])
                proto_id='prototype-'+str(p['revision'])
                p['prototype']={'id':proto_id,'revision':p['revision'],'requirements_sha256':'fixture-req','summary':'根据当前需求生成的设备原型','pages':[{'name':'设备列表'},{'name':'借用登记'}],'sha256':proto_id,'feedback':payload.get('feedback',''),'html':'<h1 hidden>设备原型</h1><button id="prototype-nav" onclick="document.querySelector(\'h1\').textContent=\'借用登记\'">借用登记</button><script>document.querySelector("h1").hidden=false;</script>'}
                p['prototype_confirmation']=None;p['models_confirmation']=None
                if p.get('build'):p.setdefault('build_history',[]).append(p['build'])
                p['build']=None
            if operation=='models':
                p['models']={'m1-object-model.yaml':{'model_type':'OBJECT','aggregates':[]},'m2-behavior-model.yaml':{'model_type':'BEHAVIOR','behaviors':[]}}
                p['models_prototype_sha256']=p['prototype']['sha256'];p['models_confirmation']=None
            if operation=='build':p['build']={'version':str(p['revision']),'url':'/app/'+p['id']+'/','output_dir':'/tmp/fixture-build',
                'acceptance_plan':{'id':'candidate-'+str(p['revision']),'checks':[{'id':'roles','label':'角色权限与隔离','detail':'记录权限与拒绝结果。'}]}}
            if operation=='source':return self.response(p)
            if operation=='knowledge/source':p['documents']=payload['documents'];return self.response(p)
            if operation=='chat':p['current_stage_result']={'stage':p['stage'],'ready_for_confirmation':True};p['messages']+=[{'role':'user','stage':p['stage'],'content':payload['message']},{'role':'assistant','stage':p['stage'],'content':'已理解设备借用需求，请确认借用人、设备与归还规则。'}]
            if operation=='analysis' and payload.get('question')=='CANCEL_ANALYSIS':
                ident='job-'+str(len(jobs));jobs[ident]={'id':ident,'status':'running','events':['等待取消'],'result':{}};return self.response({'job_id':ident},202)
            if operation=='analysis':p.setdefault('runs',[]).append({'id':'fixture-run-'+str(len(p.get('runs',[]))), 'reasoning_session_id':payload.get('reasoning_session_id'), 'question':payload['question'],'sql':'SELECT name, COUNT(*) AS count FROM equipment GROUP BY name','columns':['name','count'],'rows':[{'name':'相机','count':4},{'name':'麦克风','count':2}],'report':{'summary':'相机借用4次，麦克风借用2次。','findings':['相机次数较多'],'limitations':['只统计本次快照'],'recommendations':[]},'chart':{'type':'bar','x':'name','y':['count']},'source_hash':'0123456789abcdef','model_called':True})
            if operation=='knowledge/extract':
                if not p.get('inventory_confirmation'):p['knowledge_inventory']={'scenarios':[{'id':'借用设备','define':'登记后借用','sources':['raw/借用制度.md']}],'concepts':[],'entities':[]}
                else:p['models']={'scenarios.yaml':{'scenarios':p['knowledge_inventory']['scenarios']},'concepts.yaml':{'concepts':[]},'entities.yaml':{'entities':[]}}
            if operation=='knowledge/ask' and payload.get('question')=='FAIL_KNOWLEDGE':
                ident='job-'+str(len(jobs));jobs[ident]={'id':ident,'status':'failed','events':[],'error':'夹具模拟知识任务失败'};return self.response({'job_id':ident},202)
            if operation=='knowledge/ask':p.setdefault('runs',[]).append({'question':payload['question'],'answer':'借用人须登记后借用。','sources':['raw/借用制度.md']})
            ident = 'job-' + str(len(jobs))
            job = {'id': ident, 'status': 'succeeded', 'events': ['任务已执行'], 'result': {}}
            if operation == 'models':
                job.update(kind='生成本体模型', project_id=p['id'])
            jobs[ident] = job
            return self.response({'job_id': ident}, 202)
        do_POST=mutate;do_PUT=mutate
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    return server,requests


def run(base_url,output,fixture=False,requests=None):
    output.mkdir(parents=True,exist_ok=True);checks=[];errors=[];external=[]
    def record(name,passed,detail=None):checks.append({'case':name,'passed':bool(passed),'detail':detail})
    with sync_playwright() as play:
        browser=play.chromium.launch(channel='chrome',headless=True)
        context=browser.new_context(viewport={'width':1440,'height':1000}, accept_downloads=True)
        def guard(route, *, origin=base_url):
            if route.request.url.startswith(origin + '/'):
                route.continue_()
            else:
                external.append(route.request.url)
                route.abort()
        context.route('**/*',guard);page=context.new_page();page.set_default_timeout(12000);page.on('pageerror',lambda error:errors.append(str(error)))
        def go(route):
            page.goto(base_url+'/#'+route,wait_until='domcontentloaded');page.locator('.live-loading-dot').wait_for(state='detached');page.wait_for_timeout(200)
        def shot(name):page.evaluate('window.scrollTo(0,0)');page.wait_for_timeout(50);page.screenshot(path=str(output/(name+'.png')),full_page=True)
        def click_prototype_action(selector):
            control = page.locator(selector)
            control.scroll_into_view_if_needed()
            expect(control).to_be_in_viewport(ratio=1)
            control.click()
        go('build')
        record('default_application_cards', page.locator('h1').inner_text() == '应用开发')
        record('no_all_apps', '全部应用' not in page.locator('.sidebar').inner_text())
        record('standalone_brand', page.locator('.brand strong').inner_text() == 'Onto PaaS'
               and page.title() == '应用开发 · Onto PaaS')
        projects = page.request.get(base_url + '/api/v1/projects')
        expected_names = sorted(p['name'] for p in projects.json()['projects'] if p['kind'] == 'business')
        actual_names = sorted(page.locator('.live-app-card h2').all_text_contents())
        record('application_cards_match_workspace', projects.ok and actual_names == expected_names, actual_names)
        if fixture:
            record('fixture_only_shows_business_projects', actual_names == sorted(['设备借用管理', '原型闭环验证', '完整图谱检查']))
        elif not expected_names:
            record('empty_workspace_has_create_action', page.locator('.page-heading [data-action="new-project"]').is_visible()
                   and page.get_by_text('暂无应用项目', exact=True).is_visible()
                   and page.locator('.live-overview-count strong').inner_text() == '0')
        record('application_links_use_workspace_origin', page.locator('.live-app-card a').evaluate_all(
            '(links) => links.every(link => link.origin === location.origin)'))
        page_heading = page.locator('main > .page-heading')
        heading_provider = page_heading.locator('.live-heading-provider')
        expect(heading_provider).to_be_visible()
        provider_label = heading_provider.inner_text()
        record('configured_provider_visible', all(label in provider_label for label in ('GLM', 'glm-5.3-flash'))
               if fixture else bool(provider_label))
        record('shared_header_has_no_topbar_or_breadcrumbs',
               page_heading.count() == 1 and page.locator('main .topbar, main .top-right, main .breadcrumbs').count() == 0)
        heading_title_box = page_heading.locator('h1').bounding_box()
        heading_provider_box = heading_provider.bounding_box()
        record('desktop_title_and_provider_are_in_one_header_row',
               heading_title_box['y'] < heading_provider_box['y'] + heading_provider_box['height']
               and heading_provider_box['y'] < heading_title_box['y'] + heading_title_box['height'])
        shot('applications')
        business = page.get_by_role('button', name='应用中心', exact=True)
        analysis = page.get_by_role('button', name='数据分析中心', exact=True)
        knowledge = page.get_by_role('button', name='知识中心', exact=True)
        expect(business).to_have_attribute('aria-expanded', 'true')
        analysis.click()
        knowledge.click()
        for section, heading in [('business', business), ('analysis', analysis), ('knowledge', knowledge)]:
            expect(heading).to_have_attribute('aria-expanded', 'true')
            expect(page.locator(f'#nav-{section}')).to_be_visible()
        business.press('Enter')
        expect(business).to_have_attribute('aria-expanded', 'false')
        expect(page.locator('#nav-business')).to_be_hidden()
        page.locator('#nav-analysis [data-route="resources"]').click()
        expect(page.get_by_role('heading', name='数据库资源', exact=True)).to_be_visible()
        expect(page.locator('#nav-business')).to_be_hidden()
        expect(page.locator('#nav-analysis')).to_be_visible()
        expect(page.locator('#nav-knowledge')).to_be_visible()
        record('sidebar_groups_expand_independently', True)
        knowledge.click()
        analysis.click()
        go('build')
        if fixture:
            card=page.locator('.live-app-card').filter(has_text='设备借用管理');record('actual_application_direct_entry',card.locator('a.primary').get_attribute('href')=='/apps/business-built/versions/release-one/')
            card.locator('[data-action="open-development"]').click();page.locator('.live-build-panel').wait_for();record('configuration_separate_from_enter',page.locator('.live-project-summary h2').inner_text()=='设备借用管理');page.locator('header.page-heading [data-route="build"]').click();page.locator('.live-app-grid').wait_for();record('configuration_returns_to_cards',page.locator('.live-build-panel').count()==0)
            go('interface');page.locator('#project-select').select_option('business-prototype');page.wait_for_timeout(250)
            record('prototype_requires_requirements_confirmation',page.locator('[data-action="generate-prototype"]').is_disabled())
            page.locator('[data-route="requirements"]').last.click();page.locator('[data-scope="requirements"]').click();page.wait_for_timeout(250)
            page.locator('[data-route="interface"]').last.click();page.locator('[data-action="generate-prototype"]').click();page.locator('.live-prototype-frame').wait_for()
            frame=page.frame_locator('iframe.live-prototype-frame');frame.locator('#prototype-nav').click()
            record('prototype_fixture_navigation_runs_inside_sandbox',frame.locator('h1').inner_text()=='借用登记' and page.locator('iframe').get_attribute('sandbox')=='allow-scripts')
            record('models_require_current_prototype_confirmation',page.locator('[data-action="generate-models"]').is_disabled())
            click_prototype_action('[data-action="preview-prototype"]')
            expect(page.locator('.live-preview-modal iframe')).to_be_visible()
            record('prototype_fullscreen_modal', True)
            page.locator('#overlay [data-action="close-modal"]').first.click()
            page.locator('[data-scope="prototype"]').click();page.wait_for_timeout(200);page.locator('[data-action="generate-models"]').click();page.wait_for_timeout(400)
            files_nav = page.locator('.sidebar [data-route="files"]')
            ontology_nav = page.locator('.sidebar [data-route="models"]')
            open_object_file = page.locator(
                '[data-action="open-model-file"][data-file="m1-object-model.yaml"]')
            model_editor = page.locator('#model-editor')
            model_guard = page.get_by_role('dialog', name='保存模型修改后继续')
            confirm_models = page.locator('[data-scope="models"]')
            build_application = page.locator('[data-action="build-application"]')
            files_nav.click()
            expect(open_object_file).to_be_visible()
            expect(model_editor).to_have_count(0)
            record('model_files_starts_with_artifact_inventory',
                   page.get_by_role('row').filter(has=open_object_file).count() == 1)
            open_object_file.click()
            expect(model_editor).to_be_visible()
            record('model_files_is_raw_editor_without_workflow_actions',
                   page_heading.locator('h1').inner_text() == '模型文件'
                   and page.locator('[data-action="save-model"]').is_visible()
                   and page.locator('.live-ontology-tree, [data-action="generate-models"], '
                                    '[data-action="repair-models"], [data-scope="models"], '
                                    '[data-action="build-application"]').count() == 0)
            original = model_editor.input_value()
            model_editor.fill('{broken')
            page.locator('[data-action="select-model"]').nth(1).click()
            model_guard.get_by_role('button', name='保存并继续', exact=True).click()
            expect(model_guard.locator('.live-modal-error')).to_be_visible()
            record('dirty_navigation_failed_save_retains_editor',
                   model_editor.input_value() == '{broken' and model_guard.is_visible())
            model_guard.get_by_role('button', name='继续编辑', exact=True).click()
            model_editor.fill(original.replace('"OBJECT"', '"OBJECT", "description": "补充说明"'))
            page.locator('[data-action="select-model"]').nth(1).click()
            model_guard.get_by_role('button', name='保存并继续', exact=True).click()
            expect(model_editor).to_have_value(re.compile('BEHAVIOR'))
            record('dirty_file_switch_saves_then_continues', 'BEHAVIOR' in model_editor.input_value())

            model_editor.fill('{broken-again')
            ontology_nav.click()
            expect(model_guard).to_be_visible()
            record('dirty_files_guard_ontology_workflow', page.url.endswith('#files')
                   and confirm_models.count() == 0 and build_application.count() == 0)
            model_guard.get_by_role('button', name='继续编辑', exact=True).click()
            expect(page).to_have_url(base_url + '/#files')
            record('dirty_leave_editor_continue_keeps_text', model_editor.input_value() == '{broken-again'
                   and model_guard.count() == 0)
            page.locator('#project-select').select_option('business-built')
            model_guard.get_by_role('button', name='放弃修改并继续', exact=True).click()
            expect(page.locator('#project-select')).to_have_value('business-built')
            expect(model_editor).to_have_count(0)
            open_object_file.click()
            record('dirty_project_switch_discard_choice', model_editor.input_value() != '{broken-again'
                   and model_guard.count() == 0)
            current_file = model_editor.input_value()
            model_editor.fill('{discard-on-close')
            ontology_nav.click()
            model_guard.get_by_role('button', name='放弃修改并继续', exact=True).click()
            expect(page_heading.locator('h1')).to_have_text('业务本体')
            files_nav.click()
            open_object_file.click()
            expect(model_editor).to_have_value(current_file)
            record('dirty_files_can_discard_and_open_ontology', True)
            before = page.locator('.live-revision').inner_text()
            with page.expect_response(lambda response: response.request.method == 'PUT'
                                      and '/models/' in response.url) as unchanged_save:
                page.locator('[data-action="save-model"]').click()
            assert unchanged_save.value.ok
            expect(page.locator('[data-action="save-model"]')).to_be_enabled()
            ontology_nav.click()
            expect(page_heading.locator('h1')).to_have_text('业务本体')
            expect(build_application).to_be_enabled()
            record('unchanged_model_save_keeps_confirmation_and_revision',
                   page.locator('.live-revision').inner_text() == before)

            ontology_tree = page.locator('.live-ontology-tree')
            ontology_search = page.locator('#ontology-search')
            expect(ontology_tree).to_be_visible()
            record('business_ontology_is_structured_workflow',
                   model_editor.count() == 0 and page.locator('[data-action="generate-models"]').is_visible()
                   and confirm_models.is_visible() and build_application.is_visible())
            ontology_search.fill('没有这个业务对象')
            expect(ontology_tree.get_by_role('button', name='设备', exact=False)).to_have_count(0)
            ontology_search.fill('设备')
            device_node = ontology_tree.locator('[data-action="ontology-node"]').filter(has_text='设备')
            device_node.click()
            ontology_detail = page.locator('.live-ontology-detail')
            expect(ontology_detail).to_contain_text('设备')
            expect(ontology_detail).to_contain_text('AGG-EQUIPMENT')
            record('business_ontology_search_and_selection_show_loaded_model', True)
            page.locator('#project-select').select_option('business-prototype')
            expect(confirm_models).to_be_enabled()
            confirm_models.click()
            expect(build_application).to_be_enabled()
            build_application.click()
            expect(build_application).to_be_enabled()
            page.locator('.sidebar [data-route="publish"]').click();record('candidate_preview_separate_from_release',page.locator('.live-release-candidate a').first.get_attribute('href')=='/app/business-prototype/' and page.locator('.live-release-table').count()==0)
            record('publish_waits_for_candidate_acceptance',page.locator('#release-form button[type="submit"]').is_disabled())
            page.locator('#candidate-acceptance-form textarea[name="roles"]').fill('申请人录入，主管负责审批。')
            page.locator('#candidate-acceptance-form input[name="passed-roles"]').check()
            page.locator('#candidate-acceptance-form button[type="submit"]').click()
            expect(page.locator('#release-form button[type="submit"]')).to_be_enabled()
            page.locator('#release-version').fill('FAIL_RELEASE');page.locator('#release-acknowledged').check();page.locator('#release-form button[type="submit"]').click();page.locator('.live-error').wait_for()
            record('release_error_retains_inputs',page.locator('#release-version').input_value()=='FAIL_RELEASE' and page.locator('#release-acknowledged').is_checked())
            page.locator('#release-version').fill('1.0.0');page.locator('#release-form button[type="submit"]').click();page.locator('.live-release-table').wait_for();released_url=page.locator('.live-release-table a').get_attribute('href')
            record('release_api_binds_version_revision_and_ack',any(r['path'].endswith('/releases') and r['payload'].get('version')=='1.0.0' and r['payload'].get('revision') is not None and r['payload'].get('acknowledged') is True for r in requests))
            record('published_version_has_fixed_entry','/versions/' in released_url);shot('published-version')
            page.locator('.sidebar [data-route="interface"]').click()
            click_prototype_action('[data-action="revise-prototype"]')
            page.locator('#prototype-feedback').fill('FAIL_PROTOTYPE');page.locator('#prototype-revision-form button[type="submit"]').click();page.locator('.live-modal-error').wait_for()
            record('prototype_revision_error_retains_feedback',page.locator('#prototype-feedback').input_value()=='FAIL_PROTOTYPE')
            page.locator('#prototype-feedback').fill('调整设备页面布局');page.locator('#prototype-revision-form button[type="submit"]').click();page.wait_for_timeout(400)
            record('prototype_revision_clears_current_confirmation',page.locator('[data-scope="prototype"]').is_enabled() and page.locator('[data-action="generate-models"]').is_disabled())
            click_prototype_action('[data-action="preview-prototype"][data-history]')
            record('historical_prototype_is_sandboxed',page.locator('#overlay iframe').get_attribute('sandbox')=='allow-scripts' and 'prototype_id=' in page.locator('#overlay iframe').get_attribute('src'));history_frame=page.frame_locator('#overlay iframe');record('historical_prototype_first_page_visible',history_frame.locator('h1').is_visible());history_attempts=[]
            for navigation_attempt in range(2):
                page.locator('#overlay iframe').scroll_into_view_if_needed();history_button=history_frame.locator('#prototype-nav');history_button.wait_for(state='visible');expect(history_button).to_be_enabled();history_button.scroll_into_view_if_needed();history_button.click()
                try:
                    expect(history_frame.locator('h1')).to_have_text('借用登记',timeout=2500);history_attempts.append({'attempt':navigation_attempt+1,'passed':True});break
                except AssertionError:
                    history_attempts.append({'attempt':navigation_attempt+1,'passed':False,'text':history_frame.locator('h1').inner_text()});page.screenshot(path=str(output/('historical-navigation-failure-'+str(navigation_attempt+1)+'.png')),full_page=True)
            record('historical_prototype_navigation_works',history_frame.locator('h1').inner_text()=='借用登记',history_attempts);page.locator('#overlay [data-action="close-modal"]').first.click()
            page.locator('.sidebar [data-route="models"]').click();record('old_models_cannot_be_reconfirmed_after_prototype_revision',page.locator('[data-scope="models"]').is_disabled())
            page.locator('.sidebar [data-route="publish"]').click();record('old_release_survives_revision_and_build_history',page.locator('.live-release-table a').get_attribute('href')==released_url and '历史构建记录' in page.locator('main').inner_text())
            page.locator('.sidebar [data-route="build"]').click();record('application_card_uses_latest_release_after_revision',page.locator('.live-app-card').filter(has_text='原型闭环验证').locator('a.primary').get_attribute('href')==released_url)
            go('resources')
            create_analysis = page.locator('header.page-heading button.primary[data-action="new-project"]')
            analysis_picker = page.locator('#project-select')
            analysis_form = page.locator('#new-project-form')
            source_controls = page.locator('#database-file, #source-project-select')
            snapshot = page.locator('.live-source-summary')
            original_analysis = page.request.get(base_url + '/api/v1/projects/analysis-one').json()
            expect(analysis_picker).to_have_value('analysis-one')
            record('existing_analysis_has_one_blue_create_and_read_only_source',
                   create_analysis.inner_text() == '新建分析项目'
                   and page.locator('main [data-action="new-project"]').count() == 1
                   and source_controls.count() == 0 and snapshot.get_by_text('借用数据.db').is_visible())
            analysis_picker.select_option('')
            expect(analysis_picker).to_have_value('')
            record('unselected_analysis_has_one_blue_create_without_source_forms',
                   create_analysis.is_visible() and page.locator('main [data-action="new-project"]').count() == 1
                   and source_controls.count() == 0 and snapshot.count() == 0)
            analysis_picker.select_option('analysis-one')
            expect(snapshot.get_by_text('借用数据.db')).to_be_visible()
            before_create = len(requests)
            create_analysis.click()
            expect(analysis_form.locator('#database-file')).to_be_visible()
            page.keyboard.press('Escape')
            expect(analysis_form).to_have_count(0)
            record('cancel_new_analysis_leaves_selected_source_and_sends_no_write',
                   analysis_picker.input_value() == 'analysis-one' and len(requests) == before_create)

            create_analysis.click()
            analysis_form.locator('#project-name').fill('应用快照分析 QA')
            analysis_form.locator('#project-description').fill('分析已发布版本的设备数据。')
            analysis_form.locator('input[name="source-method"][value="application"]').check()
            source_picker = analysis_form.locator('#source-project-select')
            source_options = source_picker.locator('option').all_text_contents()
            record('published_source_survives_draft_build_invalidation',
                   '原型闭环验证 · 已发布 1.0.0' in source_options)
            record('candidate_sources_explicitly_labelled', any('当前构建候选快照' in label for label in source_options))
            source_picker.select_option(label='原型闭环验证 · 已发布 1.0.0')
            before_create = len(requests)
            analysis_form.locator('button[type="submit"]').click()
            expect(analysis_form).to_have_count(0)
            expect(snapshot.get_by_role('heading', name='原型闭环验证 · 已发布版本 1.0.0 · 数据快照')).to_be_visible()
            writes = requests[before_create:]
            record('published_source_sends_explicit_release_id',
                   len(writes) == 1 and writes[0]['path'] == '/api/v1/projects'
                   and writes[0]['payload']['kind'] == 'analysis'
                   and writes[0]['payload']['source'] == {
                       'source_project_id': 'business-prototype', 'release_id': released_url.split('/')[-2]
                   })
            record('connected_source_selects_new_project',
                   analysis_picker.locator('option:checked').inner_text() == '应用快照分析 QA'
                   and source_controls.count() == 0)

            create_analysis.click()
            analysis_form.locator('#project-name').fill('SQLite 上传分析 QA')
            analysis_form.locator('#project-description').fill('分析上传的设备快照。')
            analysis_form.locator('input[name="source-method"][value="upload"]').check()
            database = analysis_form.locator('#database-file')
            before_projects = page.request.get(base_url + '/api/v1/projects').json()
            database.set_input_files({'name': '应保留.sqlite', 'mimeType': 'application/octet-stream', 'buffer': b'FAIL_UPLOAD'})
            analysis_form.locator('button[type="submit"]').click()
            expect(page.locator('#overlay .live-modal-error')).to_contain_text('夹具模拟数据库拒绝')
            record('upload_error_retains_selected_file', database.evaluate('(e) => e.files[0]?.name') == '应保留.sqlite')
            record('failed_upload_does_not_create_empty_project',
                   page.request.get(base_url + '/api/v1/projects').json() == before_projects)
            database.set_input_files({'name': 'independent.sqlite', 'mimeType': 'application/octet-stream',
                                      'buffer': b'SQLite format 3\x00fixture'})
            before_create = len(requests)
            analysis_form.locator('button[type="submit"]').click()
            expect(analysis_form).to_have_count(0)
            expect(snapshot.get_by_role('heading', name='independent.sqlite')).to_be_visible()
            writes = requests[before_create:]
            record('database_file_preserved_during_render', len(writes) == 1
                   and writes[0]['path'] == '/api/v1/projects' and writes[0]['multipart_database']
                   and writes[0]['payload'] == {'name': 'SQLite 上传分析 QA', 'kind': 'analysis', 'description': '分析上传的设备快照。'})
            record('actual_schema_rendered', 'equipment' in snapshot.inner_text()
                   and analysis_picker.locator('option:checked').inner_text() == 'SQLite 上传分析 QA'
                   and source_controls.count() == 0)
            record('new_sources_leave_original_snapshot_and_models_intact',
                   page.request.get(base_url + '/api/v1/projects/analysis-one').json() == original_analysis)
            analysis_picker.select_option('analysis-one')
            expect(snapshot.get_by_text('借用数据.db')).to_be_visible()
            go('model-graph');page.locator('#project-select').select_option('business-graph');page.locator('.live-graph-node').nth(134).wait_for()
            record('graph_does_not_truncate_at_120',page.locator('.live-graph-node').count()==135 and '135 / 135' in page.locator('.live-graph-status').inner_text())
            graph_svg=page.locator('.live-graph');node=page.locator('.live-graph-node').first;page.mouse.move(0,0);before=node.get_attribute('transform');page.wait_for_timeout(250);record('graph_force_actually_moves',node.get_attribute('transform')!=before)
            record('graph_no_pause_or_resume_button',page.locator('[data-graph-control="pause"]').count()==0 and '暂停浮动' not in page.locator('.live-graph-toolbar').inner_text() and '继续浮动' not in page.locator('.live-graph-toolbar').inner_text())
            node.hover(force=True);before=node.get_attribute('transform');other=page.locator('.live-graph-node').nth(1);other_before=other.get_attribute('transform');page.wait_for_timeout(220);record('graph_hover_stabilizes_only_current_node',node.get_attribute('transform')==before and other.get_attribute('transform')!=other_before);page.mouse.move(0,0)
            before=page.locator('.live-graph-transform').get_attribute('transform');graph_svg.hover();page.mouse.wheel(0,-180);record('graph_wheel_zoom_changes_transform',page.locator('.live-graph-transform').get_attribute('transform')!=before)
            node.dispatch_event('keydown',{'key':'Enter'});record('graph_one_hop_highlight_and_detail',page.locator('.live-graph-node.focused').count()==1 and page.locator('.live-graph-node.dimmed').count()==132 and page.locator('.live-graph-detail').is_visible())
            page.locator('[data-graph-control="clear"]').click();page.locator('[data-graph-control="fit"]').click();before=node.get_attribute('transform');box=node.bounding_box();page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2);page.mouse.down();page.mouse.move(box['x']+box['width']/2+36,box['y']+box['height']/2+20,steps=5);page.mouse.up();record('graph_node_drag_changes_position',node.get_attribute('transform')!=before)
            record('graph_defaults_to_floating',graph_svg.get_attribute('data-layout-state')=='floating');shot('complete-interactive-graph');page.evaluate("window.__graphNodeForStop = document.querySelector('.live-graph-node')")
            go('settings');detached_before=page.evaluate("window.__graphNodeForStop.getAttribute('transform')");page.wait_for_timeout(200);record('graph_stops_after_leaving_page',page.evaluate("window.__graphNodeForStop.getAttribute('transform')")==detached_before);record('four_resource_baselines_are_explicit',page.locator('.live-baseline-grid article').count()==4 and '固定内置基线' in page.locator('.live-resource-baselines').inner_text())
            go('build')
            page.locator('header.page-heading [data-action="new-project"]').click();page.locator('#project-name').fill('舞台设备借用QA');page.locator('#project-description').fill('建立舞台设备借用登记，记录借用人、数量与归还日期。');page.locator('#new-project-form button[type="submit"]').click();page.locator('.live-message.assistant:not(.live-streaming-message)').wait_for();record('new_project_calls_real_chat_api',any('/chat' in r['path'] and r['payload'].get('message','').startswith('建立舞台') for r in requests));page.locator('[data-action="confirm"][data-scope="stage"]').click();page.wait_for_timeout(400);record('stage_confirmation_revision_sent',any(r['path'].endswith('/confirm') and r['payload'].get('stage')==0 and r['payload'].get('revision') is not None for r in requests));shot('requirements-chat');page.locator('#chat-input').fill('FAIL_CHAT');page.locator('#chat-form button[type="submit"]').click();page.locator('.live-error').wait_for();record('chat_request_error_retains_input',page.locator('#chat-input').input_value()=='FAIL_CHAT');go('requirements');page.locator('#project-select').select_option('business-built');page.wait_for_timeout(300);record('actual_requirement_audit_shown','业务条件未确认' in page.locator('.live-requirement-audit').inner_text() and '1 / 2 项通过' in page.locator('.live-requirement-audit').inner_text());
            go('data2');page.locator('#model-editor').wait_for();record('generated_model_files',page.locator('.live-model-list button').count()==3);page.locator('#model-editor').fill('{broken');page.locator('[data-action="save-model"]').click();page.locator('.live-error').wait_for();record('model_save_error_retains_text',page.locator('#model-editor').input_value()=='{broken');page.locator('[data-action="discard-model"]').click();page.wait_for_timeout(200);go('data-mapping');record('field_mapping_rendered','设备名称' in page.locator('main').inner_text() and 'equipment' in page.locator('main').inner_text());go('analytics');page.locator('#analysis-question').fill('按设备统计借用次数');page.locator('#analysis-form button[type="submit"]').click();page.locator('.live-result').wait_for();record('analysis_api_result_rendered','相机借用4次' in page.locator('.live-result').inner_text());record('actual_report_contract_and_chart',page.locator('.live-report-section').count()==2 and page.locator('.live-chart svg rect').count()==2);page.locator('.live-query-evidence summary').click();record('sql_and_source_visible','SELECT name' in page.locator('.live-query-evidence').inner_text() and '0123456789abcdef' in page.locator('.live-query-evidence').inner_text());shot('analysis-report');page.locator('#analysis-question').fill('CANCEL_ANALYSIS');page.locator('#analysis-form button[type="submit"]').click();page.locator('.reasoning-attempt [data-action="cancel-job"]').click();expect(page.locator('.reasoning-attempt .reasoning-failure')).to_have_text('已按用户请求取消');expect(page.locator('#analysis-form button[type="submit"]')).to_be_enabled();record('cancelled_job_is_terminal', '已取消这次分析' in page.locator('.reasoning-attempt').inner_text() and page.locator('.live-job,.live-error,[data-action="cancel-job"]').count()==0 and page.locator('#analysis-form button[type="submit"]').is_enabled());
            go('knowledge');page.locator('#knowledge-files').set_input_files({'name':'借用制度.md','mimeType':'text/markdown','buffer':'# 借用制度\n借用人须登记后借用。'.encode()});page.locator('#knowledge-upload-form button[type="submit"]').click();page.locator('.live-raw-doc').wait_for();page.locator('[data-action="knowledge-extract"]').first.click();page.locator('.live-inventory').wait_for();record('knowledge_inventory_first_pass','借用设备' in page.locator('.live-inventory').inner_text());page.locator('[data-action="confirm"][data-scope="inventory"]').click();page.wait_for_timeout(300);page.locator('[data-action="knowledge-extract"]').first.click();page.wait_for_timeout(800);record('old_confirmation_does_not_confirm_new_models',page.locator('[data-action="confirm"][data-scope="models"]').is_enabled() and page.locator('#knowledge-ask-form button[type="submit"]').is_disabled());page.locator('[data-action="confirm"][data-scope="models"]').click();page.wait_for_timeout(300);page.locator('#knowledge-question').fill('借用设备之前要做什么？');page.locator('#knowledge-ask-form button[type="submit"]').click();page.locator('.live-knowledge-answer').wait_for();record('knowledge_answer_from_api','登记后借用' in page.locator('.live-knowledge-answer').inner_text());shot('knowledge');page.locator('#knowledge-question').fill('FAIL_KNOWLEDGE');page.locator('#knowledge-ask-form button[type="submit"]').click();page.locator('.live-job.failed').wait_for();record('knowledge_job_failure_retains_input',page.locator('#knowledge-question').input_value()=='FAIL_KNOWLEDGE');
            go('data4');record('analysis_survives_reload',page.locator('.live-result').count()==1)

            go('data2')
            input_files = [
                {'name': name, 'mimeType': 'application/yaml', 'buffer': b'version: 1'}
                for name in ANALYSIS_IMPORT_FILES
            ]
            model_upload = page.locator('#analysis-model-files')
            import_button = page.locator('#analysis-model-import-form button[type="submit"]')
            model_upload.set_input_files([dict(input_files[0], buffer=b'FAIL_IMPORT'), *input_files[1:]])
            import_button.click()
            expect(page.get_by_role('alert')).to_contain_text('夹具模拟五模型导入拒绝')
            record('analysis_import_failure_retains_selected_files', model_upload.evaluate('(input) => input.files.length') == 6)
            model_upload.set_input_files(input_files)
            import_button.click()
            expect(page.locator('.live-model-list h2')).to_have_text('统一七模型')
            record('analysis_import_sends_revision_and_six_original_texts', any(
                r['path'].endswith('/models/import')
                and r['payload'].get('revision') is not None
                and set(r['payload'].get('files', {})) == set(ANALYSIS_IMPORT_FILES)
                and set(r['payload']['files'].values()) == {'version: 1'}
                for r in requests
            ))
            record('analysis_import_installs_seven_canonical_model_files',
                   page.locator('.live-model-list button:not([data-model-binding])').count() == 7)
            record('analysis_import_displays_diagnostics_and_keeps_confirmation_disabled',
                   '示例场景引用尚未校验' in page.locator('.live-model-diagnostics').inner_text()
                   and page.locator('[data-scope="models"]').is_disabled())
            page.get_by_text('查看原始导入记录', exact=True).click()
            record('analysis_import_provenance_visible',
                   'onto-dataanalyse-five' in page.locator('.live-analysis-import details[open] code').inner_text())
            shot('unified-model-import')
            page.locator('[data-model-binding]').click()
            editor = page.locator('#model-editor')
            editor.fill(editor.input_value().replace('设备名称', '已编辑设备名称'))
            next_revision = int(page.locator('.live-revision').inner_text().split()[-1]) + 1
            with page.expect_response(lambda response: response.request.method == 'PUT' and
                                      response.url.endswith('/models/data_mapping.yaml')) as receipt:
                page.locator('[data-action="save-model"]').click()
            assert receipt.value.ok
            expect(page.locator('.live-revision')).to_have_text(f'修订 {next_revision}')
            record('analysis_can_save_bindings_separately_from_models', any(
                r['method'] == 'PUT' and r['path'].endswith('/models/data_mapping.yaml')
                and '已编辑设备名称' in r['payload'].get('text', '')
                and r['payload'].get('revision') is not None
                for r in requests
            ))
            page.locator('.sidebar').get_by_text('指标分析', exact=True).click()
            expect(page.locator('main h1')).to_have_text('指标分析')
            record('analysis_metrics_use_canonical_m7',
                   page.locator('.live-metric-card h2').all_text_contents() == ['借用次数', '设备种类数'])
            before = len(requests)
            metric_actions = page.locator('header.page-heading')
            generate_insights = metric_actions.get_by_role('button', name='指标洞察', exact=True)
            generate_insights.click()
            metric_details = page.get_by_role('button', name='查看洞察', exact=True)
            expect(metric_details).to_have_count(2)
            metric_details.first.click()
            insight = page.get_by_role('dialog')
            expect(insight.get_by_role('heading', name='借用次数', exact=True)).to_be_visible()
            expect(insight.get_by_text('模拟洞察', exact=True)).to_be_visible()
            for section in ('计算参考 SQL', '来源表与字段依赖', '空值与异常值处理', '限制与口径边界'):
                expect(insight.get_by_text(section, exact=True)).to_be_visible()
            expect(insight).to_contain_text('SELECT COUNT(*) FROM equipment')
            expect(insight).to_contain_text('equipment.name')
            record('metric_insight_shows_sql_and_saved_field_binding', True)
            page.keyboard.press('Escape')
            expect(insight).to_be_hidden()
            metric_details.last.click()
            expect(insight.get_by_role('heading', name='设备种类数', exact=True)).to_be_visible()
            expect(insight).to_contain_text('待业务确认')
            expect(insight).not_to_contain_text('SELECT COUNT(*) FROM equipment')
            page.keyboard.press('Escape')
            expect(insight).to_be_hidden()
            metric_actions.get_by_role('button', name='导入指标定义', exact=True).click()
            page.locator('#metric-definition-file').set_input_files({
                'name': '业务指标定义.json', 'mimeType': 'application/json',
                'buffer': json.dumps({'metrics': [
                    {'id': 'METRIC-COUNT', 'name': '借用次数', 'filter_rules': ['仅保留已登记设备']},
                    {'id': 'MTR-TXN-001', 'name': 'GMV（有效订单实付额）',
                     'formula_description': '汇总有效订单实付额'},
                ]}, ensure_ascii=False).encode(),
            })
            expect(insight).to_be_hidden()
            expect(page.locator('.live-metric-insight-summary')).to_contain_text('业务指标定义.json')
            expect(metric_details).to_have_count(0)
            generate_insights.click()
            count_card = page.locator('.live-metric-card').filter(has_text='借用次数')
            count_card.get_by_role('button', name='查看洞察', exact=True).click()
            expect(insight).to_contain_text('仅保留已登记设备')
            expect(insight).to_contain_text('SELECT COUNT(*) FROM equipment')
            expect(page.locator('#toast.show')).to_have_count(0)
            shot('metric-insight')
            page.keyboard.press('Escape')
            expect(insight).to_be_hidden()
            record('metric_insights_open_independently_without_writing_project', len(requests) == before)
            gmv_card = page.locator('.live-metric-card').filter(has_text='MTR-TXN-001')
            gmv_card.get_by_role('button', name='查看洞察', exact=True).click()
            insight.get_by_role('button', name='查看场景分析', exact=True).click()
            expect(insight).to_be_hidden()
            expect(page.locator('main h1')).to_have_text('场景分析')
            page.get_by_text('借用次数分析流程', exact=True).click()
            record('analysis_steps_and_metric_display_from_m6', all(
                text in page.locator('.live-analysis-flows').inner_text()
                for text in ('M6 分析流程', '汇总设备借用记录', '借用次数')
            ))
            before = len(requests)
            scenario_demo = page.locator('#scenario-insight-workbench')
            scenario_landing = scenario_demo.locator('[data-scenario-view="landing"]')
            expect(scenario_landing.get_by_role('heading', name='电商分析场景', exact=True)).to_be_visible()
            scenario_landing.get_by_role('button', name='进入', exact=True).click()
            scenario_list = scenario_demo.locator('[data-scenario-view="group"]')
            expect(scenario_list.get_by_role('heading', name='场景清单', exact=True)).to_be_visible()
            scenario_name = 'GMV 异常波动归因分析'
            scenario_row = scenario_list.get_by_role('article').filter(
                has=page.get_by_role('heading', name=scenario_name, exact=True),
            )
            expect(scenario_row).to_contain_text('MTR-TXN-001')
            view_method = scenario_row.get_by_role('button', name='查看', exact=True)
            enter_chat = scenario_row.get_by_role('button', name='进入', exact=True)
            expect(view_method).to_be_disabled()
            expect(enter_chat).to_be_disabled()
            expect(scenario_list.get_by_text('上传方法文档', exact=True)).to_be_visible()
            scenario_list.locator('#scenario-method-upload').set_input_files({
                'name': '渠道波动复盘.md', 'mimeType': 'text/markdown',
                'buffer': '# 渠道波动复盘\n\n1. 核对时间范围\n2. 按渠道拆分指标差额。'.encode(),
            })
            scenario_list.get_by_role('button', name='场景洞察', exact=True).click()
            view_method.click()
            method_dialog = scenario_demo.locator('#scenario-method-dialog')
            expect(method_dialog.get_by_role('heading', name='场景方法洞察', exact=True)).to_be_visible()
            method_review = method_dialog.locator('[data-scenario-reviewed]')
            expect(method_review).to_contain_text('渠道波动复盘')
            expect(method_review).to_contain_text('按渠道拆分指标差额')
            method_dialog.get_by_role('button', name='关闭', exact=True).click()
            expect(method_dialog).to_be_hidden()
            record('scenario_group_methods_can_be_reviewed_before_entering_chat', True)
            enter_chat.click()
            scenario_chat = scenario_demo.locator('[data-scenario-view="chat"]')
            expect(scenario_chat.get_by_role('heading', name=scenario_name, exact=True)).to_be_visible()
            expect(scenario_chat.locator('[data-scenario-metric]')).to_contain_text('GMV（有效订单实付额）')
            conversation_management = scenario_chat.locator('aside[aria-label="场景对话管理"]')
            expect(conversation_management.get_by_role('heading', name='对话管理', exact=True)).to_be_visible()
            session_buttons = conversation_management.locator('button[data-scenario-session]')
            expect(session_buttons).to_have_count(1)
            expect(session_buttons.first).to_be_visible()
            first_session_id = session_buttons.first.get_attribute('data-scenario-session')
            first_session = conversation_management.locator(f'button[data-scenario-session="{first_session_id}"]')
            second_session = session_buttons.first
            expect(first_session).to_have_attribute('aria-current', 'true')
            scenario_input = scenario_chat.locator('#scenario-question')
            scenario_submit = scenario_chat.locator('#scenario-submit')
            scenario_turns = scenario_chat.locator('.scenario-turns')
            initial_suggestion = scenario_input.input_value()
            report_button = scenario_demo.locator('#scenario-report')
            expect(report_button).to_be_disabled()
            first_draft = '第一场对话留一条追问。'
            second_draft = '第二场对话留另一条追问。'
            second_question = '付费渠道一共少了多少钱？'
            scenario_input.fill(first_draft)
            scenario_chat.locator('#scenario-new-chat').click()
            expect(session_buttons).to_have_count(2)
            expect(second_session).to_have_attribute('aria-current', 'true')
            expect(scenario_input).to_have_value(initial_suggestion)
            first_session.click()
            expect(first_session).to_have_attribute('aria-current', 'true')
            expect(scenario_input).to_have_value(first_draft)
            questions = ('本周 GMV 为什么下降？请拆分订单量、客单价和渠道贡献。', '还有哪些渠道口径需要核实？')
            for question in questions:
                scenario_input.fill(question)
                scenario_submit.click()
                expect(scenario_turns.get_by_text(question, exact=True)).to_be_visible()
            scenario_input.fill(first_draft)
            second_session.click()
            expect(scenario_input).to_have_value(initial_suggestion)
            expect(scenario_turns.get_by_text(questions[0], exact=True)).to_have_count(0)
            expect(report_button).to_be_disabled()
            scenario_input.fill(second_question)
            scenario_submit.click()
            expect(scenario_turns.get_by_text(second_question, exact=True)).to_be_visible()
            scenario_input.fill(second_draft)
            first_session.click()
            expect(scenario_input).to_have_value(first_draft)
            expect(scenario_turns.get_by_text(questions[0], exact=True)).to_be_visible()
            expect(scenario_turns.get_by_text(second_question, exact=True)).to_have_count(0)
            second_session.click()
            expect(scenario_input).to_have_value(second_draft)
            expect(scenario_turns.get_by_text(second_question, exact=True)).to_be_visible()
            expect(scenario_turns.locator('[data-scenario-evidence]')).to_contain_text('2,700')
            first_session.click()
            expect(scenario_turns.locator('[data-scenario-evidence]')).to_contain_text('18.3')
            record('scenario_management_switches_independent_drafts_and_results', True)
            sidebar_box, composer_box = conversation_management.bounding_box(), scenario_input.bounding_box()
            record('scenario_management_appears_left_of_composer',
                   sidebar_box['x'] + sidebar_box['width'] <= composer_box['x'])
            shot('scenario-insight-chat')
            with page.expect_download() as report_download:
                report_button.click()
            download = report_download.value
            report = download.path().read_text(encoding='utf-8')
            record('scenario_demo_report_contains_actual_conversation_and_simulated_results',
                   download.suggested_filename.endswith('.md')
                   and all(fragment in report for fragment in (
                       *questions, '渠道波动复盘', 'MTR-TXN-001', '18.3', '模拟',
                   )) and all(fragment not in report for fragment in (second_question, first_draft, second_draft)))
            page.set_viewport_size({'width': 390, 'height': 844})
            expect(conversation_management).to_be_visible()
            expect(first_session).to_be_visible()
            record('scenario_management_fits_narrow_viewport', page.evaluate(
                'document.documentElement.scrollWidth <= innerWidth + 1'))
            shot('scenario-insight-chat-mobile')
            page.set_viewport_size({'width': 1440, 'height': 1000})
            record('scenario_demo_does_not_write_project', len(requests) == before)
            go('data-mapping')
            record('analysis_canonical_source_bindings_visible',
                   '已编辑设备名称' in page.locator('.live-mapping-group').inner_text())
            go('builder')
            record('desktop_demand_exploration_uses_shared_title_size', page_heading.locator('h1').evaluate(
                "(title) => getComputedStyle(title).fontSize === getComputedStyle(title).getPropertyValue('--ui-title-size').trim()"))
            shot('compact-heading-builder')
            page.get_by_role('button', name='新建会话', exact=True).click()
            input_box = page.get_by_role('textbox', name='业务需求')
            record('requirements_new_chat_opens_composer_without_project_form',
                   input_box.is_visible() and page.locator('#new-project-form').count() == 0)
            record('requirements_composer_keeps_workflow_controls_out_of_input',
                   page.locator('#chat-form elastic-textarea textarea#chat-input').count() == 1
                   and all(label not in page.locator('#chat-form').inner_text() for label in ('确认流程', '深度思考')))
            before = len(requests)
            input_box.fill('社区工具借用')
            input_box.press('Enter')
            expect(page.locator('#conversation-messages > .live-message.assistant:not(.live-streaming-message)')).to_be_visible()
            created, chat = requests[before:]
            record('requirements_first_message_creates_then_starts_one_chat',
                   created['path'] == '/api/v1/projects' and
                   created['payload']['description'] == '社区工具借用' and
                   chat['path'].endswith('/chat') and chat['payload']['message'] == '社区工具借用')
            go('analytics')
            record('analysis_composer_stays_below_scrollable_messages_in_view',
                   page.locator('#analysis-form').evaluate(
                       '(form) => form.getBoundingClientRect().bottom <= window.innerHeight'))
            for source_route, source_title in [('data2', '数据本体'), ('knowledge-models', '知识模型')]:
                go(source_route)
                expect(page_heading.locator('h1')).to_have_text(source_title)
                files_nav.click()
                expect(page_heading.locator('h1')).to_have_text('模型文件')
                picker = page.locator('#project-select')
                expect(picker.locator('option[value="business-built"]')).to_have_count(1)
                expect(picker.locator('option[value="analysis-one"], option[value="knowledge-one"]')).to_have_count(0)
                record(f'business_model_files_selects_business_context_from_{source_route}', True)
        routes = ['build', 'builder', 'requirements', 'interface', 'models', 'files', 'model-graph',
                  'resources', 'data1', 'data2', 'data-graph', 'data-mapping', 'metrics', 'data3',
                  'data4', 'analytics', 'knowledge', 'knowledge-models', 'settings', 'publish']
        for route in routes:
            go(route)
            record('route_'+route, page.locator('main h1').count() == 1
                   and page_heading.locator('h1').count() == 1 and heading_provider.count() == 1
                   and page.evaluate("""() => {
                       const styles = selector => getComputedStyle(document.querySelector(selector));
                       const current = styles('.sidebar .nav-item.active');
                       return styles('.sidebar').backgroundColor === 'rgb(255, 255, 255)'
                           && styles('main > .page-heading').backgroundColor === 'rgb(255, 255, 255)'
                           && styles('html').backgroundColor === 'rgb(246, 247, 249)'
                           && styles('main > :is(.content, .conversation-page)').padding === '0px'
                           && current.backgroundColor === 'rgb(23, 35, 50)'
                           && current.color === 'rgb(255, 255, 255)'
                           && current.boxShadow === 'none' && current.borderLeftWidth === '0px';
                   }"""))
            if fixture and route in ('builder', 'analytics'):
                model_labels = page.locator('main').get_by_text(provider_label, exact=True).filter(visible=True)
                record('single_provider_label_'+route, model_labels.count() == 1)
        for width in [390,768]:
            page.set_viewport_size({'width':width,'height':844})
            for route in routes:
                go(route);geometry=page.evaluate('({width:innerWidth,documentWidth:document.documentElement.scrollWidth})');record('responsive_'+str(width)+'_'+route,geometry['documentWidth']<=width+1,geometry)
                if width == 390 and route == 'build':
                    mobile_menu = page_heading.locator('.mobile-menu')
                    expect(mobile_menu).to_be_visible()
                    expect(heading_provider).to_be_visible()
                    expect(mobile_menu).to_have_attribute('aria-controls', 'workspace-sidebar')
                    expect(mobile_menu).to_have_attribute('aria-expanded', 'false')
                    mobile_menu.click()
                    expect(page.locator('.live-shell.menu-open')).to_have_count(1)
                    expect(mobile_menu).to_have_attribute('aria-expanded', 'true')
                    page.locator('.sidebar [data-route="settings"]').click()
                    expect(page_heading.locator('h1')).to_have_text('平台设置')
                    expect(page.locator('.live-shell.menu-open')).to_have_count(0)
                    expect(mobile_menu).to_have_attribute('aria-expanded', 'false')
                    record('mobile_header_menu_opens_navigation_and_closes_after_selection', True)
                if width == 390 and route == 'builder':
                    shot('compact-heading-builder-mobile')
            shot('mobile-'+str(width))
        if fixture:
            registry_server, registry_requests = fixture_server(adaptive_requirements=True)
            registry_url = f'http://127.0.0.1:{registry_server.server_address[1]}'
            try:
                registry_page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                registry_page.route('**/*', lambda route: guard(route, origin=registry_url))
                registry_page.on('pageerror', lambda error: errors.append(str(error)))
                registry_page.add_init_script("""
                    if (window === window.top) {
                        localStorage.setItem('onto-paas-selection-v2',
                            JSON.stringify({business: 'business-prototype'}));
                    }
                """)
                with registry_page.expect_response(registry_url + '/api/v1/projects/business-prototype'):
                    registry_page.goto(registry_url + '/#requirements')
                registry = registry_page.locator('.prd-registry')
                ready_row = registry.get_by_role('row').filter(has_text='设备借用管理')
                preview_row = registry.get_by_role('row').filter(has_text='原型闭环验证')
                unconfirmed_row = registry.get_by_role('row').filter(has_text='完整图谱检查')
                for row in (preview_row, unconfirmed_row):
                    expect(row.get_by_role('button', name='生成本体', exact=True)).to_be_disabled()
                record('registry_model_generation_requires_formal_requirements_and_prototype', True)
                record('registry_replaces_optimize_with_model_generation',
                       registry.get_by_role('button', name='AI 优化', exact=True).count() == 0)
                expect(ready_row.get_by_role('cell', name='未生成', exact=True)).to_be_visible()
                ready_row.get_by_role('button', name='生成本体', exact=True).click()
                expect(ready_row.get_by_role('cell', name='已完成', exact=True)).to_be_visible()
                expect(ready_row.get_by_role('cell', name='已生成 查看本体')).to_be_visible()
                submitted = [(row['method'], row['path'], row['payload']) for row in registry_requests]
                record('registry_generates_selected_row_models_without_optimizing',
                       registry_page.url.endswith('#requirements') and
                       submitted == [('POST', '/api/v1/projects/business-built/models', {})], submitted)
                ready_row.get_by_role('button', name='查看本体', exact=True).click()
                expect(registry_page.locator('#project-select')).to_have_value('business-built')
                expect(registry_page.locator('.live-ontology-tree')).to_be_visible()
                record('registry_opens_the_generated_ontology_for_its_project', True)
                registry_page.locator('.sidebar [data-route="requirements"]').click()

                prototype_job = {'id': 'prototype-row-job', 'project_id': 'business-built',
                                 'kind': '生成或修订界面原型', 'status': 'running'}
                registry_page.route(registry_url + '/api/v1/projects/business-built/prototype',
                                    lambda route: route.fulfill(status=202, json={'job_id': prototype_job['id']}))
                registry_page.route(registry_url + '/api/v1/jobs/prototype-row-job',
                                    lambda route: route.fulfill(json=prototype_job))
                ready_row.get_by_role('button', name='生成原型', exact=True).click()
                status = registry_page.locator('.prd-prototype-status .prd-prototype-job')
                expect(status).to_have_count(1)
                expect(ready_row.locator('.prd-prototype-status')).to_contain_text('正在生成')
                expect(registry_page.locator('.live-job')).to_have_count(0)
                registry_page.locator('.sidebar [data-route="builder"]').click()
                expect(registry_page.locator('.live-job, .prd-prototype-job')).to_have_count(0)
                expect(registry_page.locator('main')).not_to_contain_text('正在处理当前任务。')
                registry_page.locator('.sidebar [data-route="requirements"]').click()
                expect(registry_page.locator('.live-job, .prd-prototype-job')).to_have_count(0)
                expect(ready_row).not_to_contain_text('执行中')
                registry_page.locator('.sidebar [data-route="interface"]').click()
                expect(status).to_contain_text('正在生成')
                record('prototype_job_is_visible_only_in_its_prototype_row', True)
                registry_page.close()
            finally:
                registry_server.shutdown()
                registry_server.server_close()
        record('pageerrors',not errors,errors);record('no_cross_origin_request',not external,external);browser.close()
    result={'mode':'frontend-fixture' if fixture else 'read-only-live','base_url':base_url,'checks':checks,'pageerrors':errors,'cross_origin_requests':external,'failed':[x for x in checks if not x['passed']]};(output/'ui-smoke-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({'checks':len(checks),'failed':result['failed'],'pageerrors':errors},ensure_ascii=False,indent=2));return 1 if result['failed'] else 0

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--fixture',action='store_true');parser.add_argument('--base-url',default='http://127.0.0.1:4320');parser.add_argument('--output',default='.local/verification/paas-ui');args=parser.parse_args();server=None;requests=None
    if args.fixture:server,requests=fixture_server();args.base_url=f'http://127.0.0.1:{server.server_address[1]}'
    try:raise SystemExit(run(args.base_url.rstrip('/'),Path(args.output),args.fixture,requests))
    finally:
        if server:server.shutdown()
