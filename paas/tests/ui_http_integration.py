"""Exercise real HTTP, prototype rendering and compiled apps with fictional fixtures."""
from __future__ import annotations

import argparse
import json
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from paas.server import create_app
from paas.tests.requirements_fixture import loan_requirement_models
from paas.tests.test_business import fixture_models
from paas.tests.test_platform import complete_business_fixture
from paas.tests.ui_traceability_fixture import loan_design, loan_models
from paas.tests.ui_generated_application import run as test_published_application
from paas.workspace_lock import WorkspaceLease

QUANTITY_HELP = '借用数量至少1件'


class FixtureLLM:
    """Serve plans and model documents only for this test's generation jobs."""

    def __init__(self, **kwargs):
        self.calls = []

    def json(self, system, user, *, purpose, **kwargs):
        self.calls.append({'purpose': purpose, 'fixture': True})
        if purpose in ('interface-prototype.generate', 'interface-prototype.revise'):
            fixture = loan_design()
            if json.loads(user).get('feedback'):
                fixture['pages'][1]['fields'][1]['help'] = QUANTITY_HELP
            return fixture

        models = loan_models(loan_requirement_models(fixture_models()))
        for model in models.values():
            if purpose == 'business.model.' + model['model_type'].lower():
                return model
        raise AssertionError(f'Unexpected fixture generation purpose: {purpose}')


@contextmanager
def test_server(root):
    provider = {'name': 'GLM', 'model': 'fixture', 'configured': True}
    with WorkspaceLease(root), patch('paas.jobs.LLM', FixtureLLM), patch(
        'paas.server.provider_status', return_value=provider
    ), patch('paas.server.LLM', side_effect=AssertionError('Unexpected live model call')):
        app = create_app(root)
        server = make_server('127.0.0.1', 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}', app.extensions['paas_store']
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
            app.extensions['paas_jobs'].pool.shutdown(wait=True)


def request(api, path, status=200, data=None):
    response = api.get(path) if data is None else api.post(path, data=data)
    if response.status != status:
        raise AssertionError(f'{path} returned {response.status}, expected {status}: {response.text()}')
    return response.json()


def wait_for_job(api, submitted):
    path = '/api/v1/jobs/' + submitted['job_id']
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = request(api, path)
        if job['status'] == 'succeeded':
            return job
        if job['status'] not in ('queued', 'running'):
            raise AssertionError(f'{job["kind"]} ended in {job["status"]}: {job.get("error")}')
        time.sleep(0.05)
    raise AssertionError(f'Timed out waiting for {path}')


def build_fixture(api, store, check):
    project = request(api, '/api/v1/projects', 201, {
        'name': '虚构设备借用 HTTP 验收',
        'kind': 'business',
        'description': '申请人登记设备与数量，主管审批。',
    })
    project_id = project['id']
    path = '/api/v1/projects/' + project_id

    def seed_requirements(row):
        complete_business_fixture(row, with_models=False)
        row['prototype'] = None
        row['prototype_confirmation'] = None
        row['confirmations'] = [c for c in row['confirmations'] if c['scope'] != 'prototype']

    project = store.mutate(project_id, seed_requirements)
    original = wait_for_job(api, request(api, path + '/prototype', 202, {
        'revision': project['revision'],
    }))['result']
    project = request(api, path)
    revised = wait_for_job(api, request(api, path + '/prototype', 202, {
        'revision': project['revision'],
        'feedback': '补充已确认的数量填写说明',
    }))['result']
    check('fixture_prototype_revision_linked', revised['previous_id'] == original['id'])
    project = request(api, path)
    request(api, path + '/confirm', 200, {
        'scope': 'prototype', 'revision': project['revision'], 'prototype_id': revised['id'],
    })
    models = wait_for_job(api, request(api, path + '/models', 202, {}))
    check('seven_models_generated_from_fixture', len(models['result']['manifest.json']['model_files']) == 7
          and len(models['calls']) == 7 and all(call.get('fixture') for call in models['calls']))
    project = request(api, path)
    request(api, path + '/confirm', 200, {'scope': 'models', 'revision': project['revision']})
    build = wait_for_job(api, request(api, path + '/build', 202, {}))
    check('real_candidate_compiled_without_model', build['result']['status'] == 'built'
          and not build['model_called'])
    project = request(api, path)
    attempted = request(api, path + '/releases', 409, {
        'version': '1.0.0', 'revision': project['revision'], 'acknowledged': True,
    })
    check('publication_requires_candidate_acceptance', '验收' in attempted['error'])
    mount = project['build']['preview_url'] + 'api/'
    accounts = {account['role_id']: {key: account[key] for key in ('username', 'password')}
                for account in project['build']['accounts']}
    request(api, mount + 'login', 200, accounts['ROLE-REQUESTER'])
    request(api, mount + 'entities/AGG-LOAN/records', 400,
            {'deviceName': '候选验收验证规则', 'quantity': 0})
    created = request(api, mount + 'entities/AGG-LOAN/records', 201,
                      {'deviceName': '候选验收验证审批', 'quantity': 2})['record']
    request(api, mount + 'behaviors/Loan_Approve', 403,
            {'record_id': created['id'], 'input': {'_version': created['version']}})
    submitted = request(api, mount + 'behaviors/Loan_Submit', 200,
                        {'record_id': created['id'], 'input': {'_version': created['version']}})['record']
    request(api, mount + 'login', 200, accounts['ROLE-MANAGER'])
    approved = request(api, mount + 'behaviors/Loan_Approve', 200,
                       {'record_id': submitted['id'], 'input': {'_version': submitted['version']}})['record']
    report = request(api, mount + 'reports/REP-LOAN', 200, {'status': '已批准'})
    check('candidate_operation_chain_precedes_release', created['status'] == '草稿'
          and submitted['status'] == '待审批' and approved['status'] == '已批准'
          and '候选验收验证审批' in json.dumps(report['rows'], ensure_ascii=False))
    audit = project['build']['acceptance_plan']
    notes = {'roles': '申请角色被拒绝审批，主管登录后可审批。', 'ui': '候选按当前确认原型映射页面并通过构建校验。',
             'write': '保存设备和数量并读取新建草稿状态。', 'flow:FLOW-LOAN': '申请人保存并提交，主管审批后状态为已批准。',
             'flow:FLOW-LOAN-LIFECYCLE': '核对登记、草稿、提交及最终审核结果贯通。',
             'report:REP-LOAN': '过滤已批准设备借用单并核对本次记录。', 'rules': '数量为零被拒绝，数量为二通过。'}
    accepted = request(api, path + '/build/acceptance', 200, {
        'revision': project['revision'], 'candidate_id': audit['id'],
        'checks': {item['id']: {'passed': True, 'result': notes[item['id']]} for item in audit['checks']},
    })['acceptance']
    check('candidate_acceptance_checks_real_roles_and_database', accepted['automatic']['sqlite'] == 'passed'
          and set(accepted['automatic']['roles']) == set(accounts))
    publication = request(api, path + '/releases', 201, {
        'version': '1.0.0', 'revision': project['revision'], 'acknowledged': True,
    })
    check('fixed_release_published', publication['release']['status'] == 'published'
          and publication['release']['code_sha256'] == accepted['code_sha256']
          and publication['project']['published_release_id'] == publication['release']['id'])
    return publication['project']


def check_prototype_and_form(browser, base_url, project, output, check):
    errors, external, writes = [], [], []
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})

    def guard(route):
        request = route.request
        if not request.url.startswith(base_url + '/'):
            external.append(request.url)
            route.abort()
            return
        if request.method != 'GET':
            writes.append(request.method)
        route.continue_()

    context.route('**/*', guard)
    page = context.new_page()
    page.set_default_timeout(12000)
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(base_url + '/#interface', wait_until='domcontentloaded')
        row = page.get_by_role('row').filter(has=page.locator(
            f'[data-action="prd-prototype-delete"][data-id="{project["id"]}"]'))
        run_link = row.get_by_role('link', name='运行', exact=True)
        expect(run_link).to_be_visible()
        labels = row.locator('.prd-prototype-actions > *').evaluate_all(
            'items => items.map(item => item.getAttribute("aria-label"))')
        check('prototype_registry_actions_in_order', labels == ['编辑', '运行', '删除'], labels)
        with page.expect_popup() as opened:
            run_link.click()
        runtime = opened.value
        try:
            runtime.get_by_role('button', name='登录', exact=True).click()
            expect(runtime.frame_locator('iframe[title="当前界面原型"]').locator(
                'section[data-page="loans"]:not([hidden])')).to_be_visible()
            check('prototype_runs_in_standalone_page', '#prototype-run?' in runtime.url)
        finally:
            runtime.close()
        row.get_by_role('button', name='校核记录', exact=True).click()
        current_selector = 'iframe[title="当前界面原型"]'
        current = page.frame_locator(current_selector)
        current.locator('section[data-page="loans"]:not([hidden])').wait_for()
        check('actual_current_iframe_first_page_visible',
              current.locator('section[data-page]:not([hidden])').count() == 1)
        check('actual_current_iframe_sandbox', page.locator(current_selector).get_attribute('sandbox') == 'allow-scripts')
        current.locator('nav [data-target="apply"]').click()
        form = current.locator('section[data-page="apply"]:not([hidden])')
        form.wait_for()
        check('actual_current_navigation_works', form.is_visible())
        draft = form.locator('[data-kind="draft"]')
        notice = form.locator('[role="status"]')
        check('actual_iframe_exposes_required_field_constraints',
              form.locator('input:invalid').count() == 2)
        form.locator('[name="apply-deviceName"]').fill('虚构验收设备')
        form.locator('[name="apply-quantity"]').fill('2')
        draft.click()
        notice.filter(has_text='预览记录：草稿').wait_for()
        check('actual_iframe_valid_draft_preview_works', QUANTITY_HELP in form.inner_text())
        page.screenshot(path=str(output / 'current-prototype.png'), full_page=True)

        history_button = page.get_by_role('button', name='查看历史原型', exact=True)
        history_button.scroll_into_view_if_needed()
        expect(history_button).to_be_in_viewport(ratio=1)
        history_button.click()
        historical_selector = 'iframe[title="历史界面原型"]'
        history = page.frame_locator(historical_selector)
        try:
            history.locator('section[data-page="loans"]:not([hidden])').wait_for()
            check('actual_historical_first_page_visible', history.locator('section[data-page]:not([hidden])').count() == 1)
            history.locator('nav [data-target="apply"]').click()
            old_form = history.locator('section[data-page="apply"]:not([hidden])')
            old_form.wait_for()
            check('actual_historical_navigation_and_original_content', QUANTITY_HELP not in old_form.inner_text())
            iframe_url = urlparse(page.locator(historical_selector).get_attribute('src'))
            check('historical_http_uses_project_bound_id',
                  iframe_url.path == f'/api/v1/projects/{project["id"]}/prototype/html'
                  and parse_qs(iframe_url.query).get('prototype_id') == [project['prototype_history'][0]['id']])
            page.screenshot(path=str(output / 'historical-prototype.png'), full_page=True)
        except Exception:
            check('history_modal_controls', False, {
                'index': history_button.get_attribute('data-history'),
                'modal': page.locator('#modal-title').all_text_contents(),
                'frames': page.locator('iframe').evaluate_all('(entries) => entries.map(item => [item.title, item.getAttribute("src")])'),
            })
            page.screenshot(path=str(output / 'history-failure.png'), full_page=True)
        check('prototype_preview_never_writes_business_or_models', not writes, writes.copy())

        release = project['releases'][0]
        credential = next(account for account in project['build']['accounts'] if account['role_id'] == 'ROLE-REQUESTER')
        page.goto(base_url + release['url'], wait_until='domcontentloaded')
        page.locator('#login [name="username"]').fill(credential['username'])
        page.locator('#login [name="password"]').fill(credential['password'])
        page.locator('#login button.primary').click()
        page.locator('[data-menu-group="loan-group"] [data-screen-route="screen-apply"]').click()
        help_text = page.locator('#mu-inline-edit label').filter(has=page.locator('input[name="quantity"]')).locator('.field-help')
        help_text.filter(has_text=QUANTITY_HELP).wait_for()
        check('actual_compiled_form_shows_confirmed_minimum_hint', QUANTITY_HELP in help_text.inner_text())
        check('actual_navigation_and_control_bound_to_mu', page.locator('#workspace').get_attribute('data-prototype-page-id') == 'apply'
              and page.locator('#mu-inline-edit input[name="quantity"]').get_attribute('data-element-id') == 'input-quantity'
              and page.locator('#mu-inline-edit [data-mu-action="save"]').get_attribute('data-create') == 'Loan_Create')
        page.locator('[data-screen-route="screen-report"]').click()
        page.locator('#filters select[name="status"]').wait_for()
        check('actual_query_scope_has_m7_bound_filter', page.locator('#filters select[name="status"]').get_attribute('data-element-id') == 'input-status')
        page.screenshot(path=str(output / 'compiled-form-help.png'), full_page=True)

        callchain = page.goto(base_url + '/api/v1/projects/' + project['id'] + '/models/callchain', wait_until='domcontentloaded')
        check('actual_callchain_is_read_only_html', callchain.ok and 'connect-src' in callchain.headers['content-security-policy'])
        page.locator('#expand').click()
        page.locator('[data-key="action:screen:screen-apply:action-save"]').click()
        page.locator('#detail [data-ref="behavior:Loan_Create"]').first.click()
        detail = page.locator('#detail')
        check('actual_callchain_quick_links_follow_current_selected_models',
              'RULE-QTY' in detail.inner_text() and 'PERM-CREATE' in detail.inner_text()
              and 'AGG-LOAN' in detail.inner_text() and 'AGG-CONTRACT' not in page.locator('#tree').inner_text())
        page.screenshot(path=str(output / 'current-model-callchain.png'), full_page=True)
    finally:
        check('pageerrors', not errors, errors)
        check('no_external_requests', not external, external)
        context.close()


def check_prototype_deletion(browser, base_url, project, check):
    page = browser.new_page()
    try:
        project_id = project['id']
        path = f'/api/v1/projects/{project_id}'
        page.goto(base_url + '/#interface', wait_until='domcontentloaded')
        delete = page.locator(f'[data-action="prd-prototype-delete"][data-id="{project_id}"]')
        delete.click()
        page.get_by_role('button', name='取消', exact=True).click()
        expect(delete).to_be_visible()
        check('cancelling_prototype_deletion_keeps_current', page.request.get(
            base_url + path).json()['prototype']['id'] == project['prototype']['id'])

        delete.click()
        with page.expect_response(lambda response: response.url == base_url + path + '/prototype'
                                  and response.request.method == 'DELETE') as deleted:
            page.get_by_role('button', name='删除原型', exact=True).click()
        response = deleted.value
        check('prototype_deletion_targets_exact_visible_revision', response.ok
              and response.request.post_data_json == {'revision': project['revision'],
                                                      'prototype_id': project['prototype']['id']})
        expect(delete).to_have_count(0)
        expect(page.get_by_text('原型已删除', exact=True)).to_be_visible()
        updated = response.json()
        check('prototype_deletion_preserves_requirements_and_release', updated['requirements'] == project['requirements']
              and updated['published_release_id'] == project['published_release_id']
              and page.request.get(base_url + project['releases'][0]['url']).ok)
    finally:
        page.close()


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    checks = []

    def check(case, value, detail=None):
        checks.append({'case': case, 'passed': bool(value), 'detail': detail})

    try:
        with tempfile.TemporaryDirectory(prefix='onto-paas-http-') as directory:
            with test_server(Path(directory)) as (base_url, store):
                with sync_playwright() as play:
                    api = play.request.new_context(base_url=base_url, timeout=12000)
                    try:
                        project = build_fixture(api, store, check)
                    finally:
                        api.dispose()
                    browser = play.chromium.launch(channel='chrome', headless=True)
                    try:
                        check_prototype_and_form(browser, base_url, project, output, check)
                    finally:
                        browser.close()
                project_file = store.directory(project['id']) / 'project.json'
                release_output = output / 'published-application'
                check('real_published_application_role_flow', test_published_application(
                    project_file, base_url, release_output
                ) == 0)
                with sync_playwright() as play:
                    browser = play.chromium.launch(channel='chrome', headless=True)
                    try:
                        check_prototype_deletion(browser, base_url, project, check)
                    finally:
                        browser.close()
    except (Exception, SystemExit) as error:
        check('integration_completed', False, str(error))
        raise
    finally:
        result = {'mode': 'real-http-fictional-fixtures-no-live-model', 'checks': checks,
                  'failed': [item for item in checks if not item['passed']]}
        (output / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps({'checks': len(checks), 'failed': result['failed']}, ensure_ascii=False, indent=2))
    return bool(result['failed'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('.local/verification/http-integration'))
    args = parser.parse_args()
    return run(args.output)


if __name__ == '__main__':
    raise SystemExit(main())
