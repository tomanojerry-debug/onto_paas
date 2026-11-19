"""Exercise an actual model-generated application through its browser UI.

Reads verification project JSON directly and writes labeled fictional records
to the generated application at the specified isolated local address. Browser
requests stay on that origin and the script does not call a model.
"""
from __future__ import annotations
import argparse
import json
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright


def available(behavior, credential, roles, permissions):
    role = roles.get(credential['role_id'], {})
    ids = set(role.get('permissions', []))
    return set(behavior.get('requiredPermissions', [])).issubset(ids) and any(permissions.get(pid, {}).get('targetRef') == behavior['id'] for pid in ids)


def prepare(project):
    models = project['models']
    docs = {m.get('model_type'): m for m in models.values() if isinstance(m, dict)}
    behaviors = docs['BEHAVIOR']['behaviors']
    roles = {r.get('roleId',r.get('id')):r for r in docs['ACTOR']['roles']}
    permissions = {p.get('permissionId',p.get('id')):p for p in docs['ACTOR']['permissions']}
    accounts = project['build']['accounts']
    create = next(b for b in behaviors if b.get('runtime',{}).get('operation') == 'CREATE')
    entity_id = create['ownerEntity']
    submit = next(b for b in behaviors if b['ownerEntity']==entity_id and b.get('runtime',{}).get('operation')=='TRANSITION' and b['runtime'].get('submitterField') and not b['runtime'].get('separationOfDuties'))
    approve = next(b for b in behaviors if b['ownerEntity']==entity_id and b.get('runtime',{}).get('operation')=='TRANSITION' and b['runtime'].get('separationOfDuties'))
    query = next(b for b in behaviors if b['ownerEntity']==entity_id and b.get('runtime',{}).get('operation')=='QUERY')
    requester = next((a for a in accounts if available(create,a,roles,permissions) and available(submit,a,roles,permissions) and available(query,a,roles,permissions)), None)
    manager = next((a for a in accounts if available(approve,a,roles,permissions) and available(query,a,roles,permissions)), None)
    if not requester or not manager:
        raise ValueError('实际模型中未找到同时具备必要操作与查询权限的申请人/主管账号；未写入测试记录')
    return create,submit,approve,query,requester,manager


def field_value(field, marker):
    if field.get('defaultValue') not in (None,''):
        return field['defaultValue']
    kind = field.get('type','String').lower()
    if kind in ('integer','int','decimal','number','money','float','double'):
        return max(field.get('min',2),2)
    if kind in ('boolean','bool'):return True
    if kind=='date':return '2026-11-18'
    if kind in ('datetime','timestamp'):return '2026-11-18T12:00:00+08:00'
    values=field.get('enumValues') or field.get('options')
    if values:
        first=values[0];return first.get('value') if isinstance(first,dict) else first
    if field.get('targetAggregate') or field.get('referenceEntity'):
        raise ValueError('本浏览器验收脚本需独立基础对象，遇到必填引用将停止，不能猜造关联资料')
    return marker if re.search(r'name|title|material|device|label',field['name'],re.I) else '虚构验收资料'


def run(project_path,base_url,output):
    address = urlsplit(base_url)
    if (address.scheme != 'http' or address.hostname not in ('127.0.0.1', 'localhost', '::1')
            or address.port is None or address.username is not None
            or address.path not in ('', '/') or address.query or address.fragment):
        raise ValueError('请提供带端口的独立本机验收地址')
    base_url = base_url.rstrip('/')
    project=json.loads(project_path.read_text())
    if not project.get('build'):raise ValueError('实际试验项目尚未构建')
    create,submit,approve,query,requester,manager=prepare(project)
    output.mkdir(parents=True,exist_ok=True)
    marker='浏览器验收虚构物料-'+str(int(time.time()))
    release=next((r for r in project.get('releases',[]) if r.get('id')==project.get('published_release_id')),None)
    app_url=base_url.rstrip('/')+(release.get('url') if release and release.get('url') else '/apps/'+project['id']+'/')
    checks=[];errors=[];blocked=[];responses=[];created_id=None
    def record(name,passed,detail=None):checks.append({'case':name,'passed':bool(passed),'detail':detail})
    def save():
        result={'mode':'actual-generated-application-browser-no-new-model-call','project_id':project['id'],'app_url':app_url,'models_sha256':project['build'].get('models_sha256') or project['build'].get('model_hash'),'fictional_record_marker':marker,'record_id':created_id,'checks':checks,'failed':[c for c in checks if not c['passed']],'pageerrors':errors,'blocked_requests':blocked}
        (output/'generated-app-browser-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));return result
    with sync_playwright() as play:
        browser=play.chromium.launch(channel='chrome',headless=True)
        contexts=[]
        def new_page():
            context=browser.new_context(viewport={'width':1440,'height':1000});contexts.append(context)
            def guard(route):
                if not route.request.url.startswith(base_url + '/'):
                    blocked.append(route.request.url);route.abort()
                else:route.continue_()
            context.route('**/*',guard);page=context.new_page();page.set_default_timeout(15000);page.on('pageerror',lambda error:errors.append(str(error)));return page
        def shot(page,name):page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(output/(name+'.png')),full_page=True)
        def login(page,account):
            page.goto(app_url,wait_until='domcontentloaded');page.locator('#login').wait_for();page.locator('#login [name="username"]').fill(account['username']);page.locator('#login [name="password"]').fill(account['password']);page.locator('#login button[type="submit"],#login button.primary').click();page.locator('#logout').wait_for()
        def row(page):return page.locator('#workspace tbody tr').filter(has_text=marker)
        def execute_modal(page,behavior_id,record_id=None,payload=None):
            plan=page.request.get(app_url+'api/meta').json().get('ui_plan')
            if plan and not record_id:
                target=next(s for s in plan['screens'] if any(a.get('behavior_id')==behavior_id for a in s['actions']))
                if target['presentation']=='page' and target['layout']=='SINGLE_FORM':
                    page.locator('[data-screen-route="'+target['id']+'"]').click()
                    form=page.locator('#mu-inline-edit')
                    for name,value in (payload or {}).items():
                        locator=form.locator('[name="'+name+'"]')
                        if locator.evaluate('(e)=>e.tagName')=='SELECT':locator.select_option(str(value))
                        elif locator.evaluate('(e)=>e.type')=='checkbox':locator.set_checked(bool(value))
                        else:locator.fill(str(value))
                    form.locator('[data-create="'+behavior_id+'"]').click()
                    page.locator('#notice').filter(has_text='操作已完成').wait_for()
                    list_screen=next(s for s in plan['screens'] if s['entity_id']==target['entity_id'] and s['layout']=='LIST_MAINTENANCE')
                    page.locator('[data-screen-route="'+list_screen['id']+'"]').click()
                    return
            if record_id:row(page).locator('[data-act="'+behavior_id+'"]').click()
            else:page.locator('[data-create="'+behavior_id+'"]').click()
            page.locator('#edit').wait_for()
            for name,value in (payload or {}).items():
                locator=page.locator('#edit [name="'+name+'"]')
                if locator.evaluate('(e)=>e.tagName')=='SELECT':locator.select_option(str(value))
                else:locator.fill(str(value))
            if plan:
                selector='data-act' if record_id else 'data-create'
                page.locator('#edit ['+selector+'="'+behavior_id+'"]').click()
            else:page.locator('#edit button.primary').click()
        requester_page=new_page();manager_page=None
        try:
            login(requester_page,requester);record('requester_login',True);meta=requester_page.request.get(app_url+'api/meta').json();entity=next(e for e in meta['entities'] if e['id']==create['ownerEntity']);fields={f['name']:f for f in entity['attributes']};payload={name:field_value(fields[name],marker) for name in create['runtime']['editableFields']}
            record('requester_cannot_see_approve_action',not any(b['id']==approve['id'] for b in meta['behaviors']))
            execute_modal(requester_page,create['id'],payload=payload);requester_page.locator('#edit').wait_for(state='detached');row(requester_page).wait_for()
            rows=requester_page.request.get(app_url+'api/entities/'+create['ownerEntity']+'/records').json()['records'];record_data=next(r for r in rows if marker in map(str,r.values()));created_id=record_data['id'];record('actual_browser_create_persisted',bool(created_id),{'record_id':created_id,'version':record_data['version']});shot(requester_page,'01-requester-created')
            denied=requester_page.request.post(app_url+'api/behaviors/'+approve['id'],data=json.dumps({'record_id':created_id,'input':{'_version':record_data['version']}}),headers={'Content-Type':'application/json'});record('wrong_role_approval_rejected',denied.status==403,denied.status)
            execute_modal(requester_page,submit['id'],created_id);requester_page.locator('#edit').wait_for(state='detached');expected_pending=submit['runtime'].get('targetState');requester_page.wait_for_function('(state)=>document.querySelector("#workspace")?.innerText.includes(state)',arg=expected_pending);record('browser_submit_changes_state',expected_pending in row(requester_page).inner_text());shot(requester_page,'02-requester-submitted')
            manager_page=new_page();login(manager_page,manager);manager_meta=manager_page.request.get(app_url+'api/meta').json();record('manager_uses_separate_session',manager_meta['actor']['id']!=meta['actor']['id']);tasks=manager_page.request.get(app_url+'api/tasks').json()['tasks'];record('actual_approval_task_exists',any(t['record_id']==created_id for t in tasks),len(tasks));row(manager_page).wait_for();shot(manager_page,'03-manager-pending')
            execute_modal(manager_page,approve['id'],created_id);manager_page.locator('#edit').wait_for(state='detached');expected_approved=approve['runtime'].get('targetState');manager_page.wait_for_function('(state)=>document.querySelector("#workspace")?.innerText.includes(state)',arg=expected_approved);record('browser_approve_changes_state',expected_approved in row(manager_page).inner_text());shot(manager_page,'04-manager-approved')
            report=next(r for r in manager_meta['reports'] if r['behaviorRef']==query['id'])
            manager_plan=manager_meta.get('ui_plan')
            if manager_plan:
                report_screen=next(s for s in manager_plan['screens'] if s['report_id']==report['id'])
                manager_page.locator('[data-screen-route="'+report_screen['id']+'"]').click()
            else:manager_page.get_by_role('button',name='报表 · '+report['name'],exact=True).click()
            manager_page.locator('#filters').wait_for();manager_page.locator('#results table').wait_for();record('actual_query_contains_created_record',marker in manager_page.locator('#results').inner_text());shot(manager_page,'05-manager-report')
            manager_page.reload(wait_until='domcontentloaded');manager_page.locator('#logout').wait_for()
            if manager_plan:
                list_screen=next(s for s in manager_plan['screens'] if s['entity_id']==create['ownerEntity'] and s['layout']=='LIST_MAINTENANCE')
                manager_page.locator('[data-screen-route="'+list_screen['id']+'"]').click()
            row(manager_page).wait_for();record('record_survives_browser_reload',marker in row(manager_page).inner_text());manager_page.set_viewport_size({'width':390,'height':844});shot(manager_page,'06-mobile-application');geometry=manager_page.evaluate('({width:innerWidth,documentWidth:document.documentElement.scrollWidth})');record('mobile_application_no_page_overflow',geometry['documentWidth']<=geometry['width']+1,geometry)
        except Exception as error:
            record('browser_flow_completed',False,str(error));shot(manager_page or requester_page,'failure-state');raise
        finally:
            record('pageerrors',not errors,errors);record('no_cross_origin_request',not blocked,blocked);result=save();browser.close()
    print(json.dumps({'checks':len(checks),'failed':result['failed'],'project_id':project['id'],'record_id':created_id},ensure_ascii=False,indent=2))
    return 1 if result['failed'] else 0

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base-url',required=True);parser.add_argument('--project-json',required=True);parser.add_argument('--output',default='.local/verification/paas-generated-app-browser');args=parser.parse_args();raise SystemExit(run(Path(args.project_json),args.base_url,Path(args.output)))
