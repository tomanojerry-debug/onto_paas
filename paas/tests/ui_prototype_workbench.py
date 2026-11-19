"""Run a browser regression against the workbench and its mocked model bridge."""
from __future__ import annotations

import argparse
import json
import re
import tempfile
import threading
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from paas import prototype
from paas.server import create_app
from paas.tests.test_platform import complete_business_fixture
from paas.tests.test_prototype_contract import FakeLLM


TITLE = '合同工作台浏览器验收'
ARCHIVE_TITLE = '历史合同工作台浏览器验收'
REFERENCE_TITLE = '原版合同浏览器验收'
SHORTCUTS = ['待审批合同', '本月签订与开票', '未收款开票', '各部门合同统计']
ANSWER = ('### 审批说明\n\n**当前预览**包含用户刚录入的记录。\n\n'
          '| 项目 | 结论 |\n| --- | --- |\n| 数据范围 | 仅当前窗口 |\n\n'
          '保留原文标记 <em>测试文字</em>。')


def field(key, label, kind='text', **extra):
    return {'id': key, 'label': label, 'type': kind, 'required': True, **extra}


def action(key, label, kind, **extra):
    return {'id': key, 'label': label, 'kind': kind, **extra}


def fixture_design():
    contracts = [field('contract-no', '合同编号', help='填写业务合同的唯一编号。'), field('sign-date', '签订日期', 'date'),
                 field('total-amount', '合同总金额', 'number'), field('department-no', '部门编号'),
                 field('contract-type', '合同类型', 'select', options=['销售合同', '服务合同'])]
    invoices = [field('invoice-no', '开票编号'),
                field('contract-no', '合同编号', 'reference', reference_page='contract-entry', reference_field='contract-no'),
                field('invoice-date', '开票日期', 'date'), field('invoice-amount', '开票金额', 'number')]
    receipts = [field('invoice-no', '开票编号', 'reference', reference_page='invoice-entry', reference_field='invoice-no'),
                field('receipt-time', '收款日期', 'date'),
                field('receipt-amount', '收款金额', 'number')]

    def work_area(group, title, key, fields, footer):
        entry = key + '-entry'
        return [
            {'id': key + '-list', 'group': group, 'name': title, 'purpose': '核对当前窗口中的记录',
             'layout': 'query_list', 'record_source': entry,
             'fields': [field(fields[0]['id'], fields[0]['label'])],
             'columns': [{name: item[name] for name in ('id', 'label')} for item in fields],
             'actions': [action('query', '查询', 'query'), action('new', '新增' + title, 'open_form', target_page=entry)]},
            {'id': entry, 'group': group, 'name': title + '登记', 'purpose': '录入验收用的预览记录',
             'layout': 'single_form', 'presentation': 'dialog', 'fields': fields, 'actions': footer},
        ]

    save = [action('save', '保存', 'save'), action('cancel', '取消', 'cancel')]
    approve = [action('draft', '保存草稿', 'draft'), action('submit', '提交审批', 'submit'),
               action('cancel', '取消', 'cancel')]
    pages = [*work_area('合同管理', '合同台账', 'contract', contracts, approve),
             *work_area('开票与收款', '合同开票', 'invoice', invoices, save),
             *work_area('开票与收款', '合同收款', 'receipt', receipts, save),
             {'id': 'department-maintain', 'group': '基础数据', 'name': '部门维护',
              'purpose': '登记部门编号和名称', 'layout': 'single_form',
              'fields': [field('department-no', '部门编号'), field('department-name', '部门名称')], 'actions': save}]
    return {'title': TITLE, 'summary': '合同工作台交互测试', 'pages': pages,
            'assistant': {'suggestions': SHORTCUTS, 'metrics': [
                {'label': '合同签订额', 'page': 'contract-entry', 'field': 'total-amount'},
                {'label': '已开票金额', 'page': 'invoice-entry', 'field': 'invoice-amount'},
            ]}}


@contextmanager
def fixture_server(root):
    model = Mock()
    model.calls = []
    model.text.return_value = ANSWER
    status = {'name': 'GLM', 'model': 'offline-fixture', 'configured': True}
    with (patch('paas.server.provider_status', return_value=status),
          patch('paas.server.LLM', return_value=model), patch('paas.jobs.LLM', return_value=model)):
        app = create_app(root)
        store = app.extensions['paas_store']
        for title, template in ((TITLE, prototype.WORKBENCH_TEMPLATE), (ARCHIVE_TITLE, 'ontology-v9-workbench-6')):
            created = store.create(title, 'business', '隔离浏览器验收')

            def seed(project):
                complete_business_fixture(project, with_models=False)
                design = dict(fixture_design(), title=title)
                project['prototype'] = prototype.generate(project, FakeLLM([design]))
                project['prototype_confirmation'] = None

            with patch('paas.prototype.WORKBENCH_TEMPLATE', template):
                store.mutate(created['id'], seed)

        reference = store.create(REFERENCE_TITLE, 'business', '隔离浏览器验收')
        with app.test_client() as client:
            adopted = client.post(f'/api/v1/projects/{reference["id"]}/contract-reference',
                                  json={'revision': reference['revision'], 'acknowledged': True})
            assert adopted.status_code == 200, adopted.json
        server = make_server('127.0.0.1', 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}', model
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
            app.extensions['paas_jobs'].pool.shutdown(wait=True)


def register_observers(context, origin, *, allow_edits=False):
    errors = []
    unapproved = []

    def guard(route):
        request = route.request
        allowed_posts = ('/prototype/assistant',)
        if allow_edits:
            allowed_posts += ('/prototype/edit/chat', '/prototype/edit/apply')
        accepted_post = request.method == 'POST' and request.url.endswith(allowed_posts)
        if request.url.startswith(origin + '/') and (request.method == 'GET' or accepted_post):
            route.continue_()
        else:
            unapproved.append((request.method, request.url))
            route.abort()

    context.route('**/*', guard)
    context.on('page', lambda page: page.on('pageerror', lambda error: errors.append(str(error))))
    return errors, unapproved


def expect_demo_login(scope, requests, *, form_id='prototype-demo-login'):
    form = scope.locator(f'form#{form_id}')
    expect(form).to_be_visible()
    expect(scope.locator('iframe')).to_have_count(0)
    username = form.get_by_label('账号', exact=True)
    password = form.get_by_label('密码', exact=True)
    expect(username).to_have_value('demo')
    expect(password).to_have_value(re.compile(r'.+'))
    expect(password).to_have_attribute('type', 'password')
    assert username.evaluate('(input) => input.readOnly')
    assert password.evaluate('(input) => input.readOnly')
    assert all(method == 'GET' and '/prototype/html' not in url for method, url in requests), requests
    return form.get_by_role('button', name='登录', exact=True)


def open_from_registry(context, origin, output=None, *, title=TITLE, reference=False):
    registry = context.new_page()
    registry.goto(origin + '/#interface')
    registry_title = title + ' · 界面原型' if reference else title
    row = registry.get_by_role('row').filter(has=registry.get_by_text(registry_title, exact=True))
    expect(row).to_be_visible()

    requests = []

    def record(request):
        requests.append((request.method, request.url))

    context.on('request', record)
    try:
        with registry.expect_popup() as opened:
            row.get_by_role('link', name='运行', exact=True).click()
        runtime = opened.value
        expect(runtime).to_have_title(re.compile(re.escape(title)))
        iframe = runtime.locator('iframe[title="当前界面原型"]')
        if not reference:
            login = expect_demo_login(runtime, requests)
            if output is not None:
                runtime.screenshot(path=str(output / 'workbench-demo-login.png'))
            login.click()
            expect(iframe.content_frame.locator('.app-bar')).to_contain_text(title)
            expect(runtime.locator('form#prototype-demo-login')).to_have_count(0)
            initial_requests = len(requests)
            runtime.reload(wait_until='domcontentloaded')
            login = expect_demo_login(runtime, requests[initial_requests:])
            login.click()
        else:
            expect(runtime.locator('form#prototype-demo-login')).to_have_count(0)
        expect(iframe).to_be_visible()
        assert all(method == 'GET' for method, _ in requests), requests
    finally:
        context.remove_listener('request', record)

    expect(runtime.locator('main > header')).to_be_hidden()
    expect(runtime.get_by_role('link', name='返回原型列表', exact=True)).to_be_hidden()
    expect(iframe).to_have_attribute('sandbox', 'allow-scripts')
    frame = iframe.content_frame
    if reference:
        expect(frame.locator('#pg-contract-list')).to_have_count(1)
    else:
        top_bar = frame.locator('.app-bar')
        expect(top_bar).to_contain_text(title)
        expect(top_bar.locator('[data-toggle-panel="nav"]')).to_have_count(0)
        expect(top_bar.get_by_role('button', name='菜单', exact=True)).to_have_count(0)
    bounds = iframe.bounding_box()
    viewport = runtime.evaluate('() => ({width: innerWidth, height: innerHeight})')
    for key, expected in {'x': 0, 'y': 0, **viewport}.items():
        assert abs(bounds[key] - expected) <= 1, (bounds, viewport)
    return runtime, frame


def check_editor_preview_scale(page, preview, iframe):
    viewport = preview.locator('.prd-prototype-preview-viewport')
    width, height = page.evaluate('() => [innerWidth, innerHeight]')
    expect(iframe).to_have_js_property('clientWidth', width)
    expect(iframe).to_have_js_property('clientHeight', height)
    assert iframe.content_frame.locator('html').evaluate(
        '(root) => [root.ownerDocument.defaultView.innerWidth, root.ownerDocument.defaultView.innerHeight]'
    ) == [width, height]

    frame, pane = page.wait_for_function('''([iframe, viewport]) => {
        const frame = iframe.getBoundingClientRect();
        const pane = viewport.getBoundingClientRect();
        const scale = Math.min(1, pane.width / innerWidth, pane.height / innerHeight);
        const fitted = Math.abs(frame.width - innerWidth * scale) <= 1 &&
            Math.abs(frame.height - innerHeight * scale) <= 1;
        return fitted && [frame.toJSON(), pane.toJSON()];
    }''', arg=[iframe.element_handle(), viewport.element_handle()], timeout=5000).json_value()
    for coordinate, length in (('x', 'width'), ('y', 'height')):
        assert frame[coordinate] >= pane[coordinate] - 1, (frame, pane)
        assert frame[coordinate] + frame[length] <= pane[coordinate] + pane[length] + 1, (frame, pane)


def click_scaled_preview(page, iframe, control):
    # Map the frame's local coordinates to Chrome's pointer coordinates after scaling.
    x, y = control.evaluate('''(node) => {
        const rect = node.getBoundingClientRect();
        return [(rect.x + rect.width / 2) / innerWidth, (rect.y + rect.height / 2) / innerHeight];
    }''')
    bounds = iframe.bounding_box()
    page.mouse.click(bounds['x'] + x * bounds['width'], bounds['y'] + y * bounds['height'])


def check_registry_editor(context, origin, output, *, narrow=False, fullscreen_frame=None):
    registry = context.new_page()
    registry.goto(origin + '/#interface')
    row = registry.get_by_role('row').filter(has=registry.get_by_text(ARCHIVE_TITLE, exact=True))
    edit = row.get_by_role('button', name='编辑', exact=True)
    requests = []

    def record(request):
        requests.append((request.method, request.url))

    context.on('request', record)
    try:
        edit.click()
        dialog = registry.locator('dialog#prototype-editor-dialog')
        expect(dialog).to_be_visible()
        assert dialog.evaluate('(node) => node.matches(":modal")')
        expect(dialog.get_by_role('heading', name='修订界面原型', exact=True)).to_be_focused()
        expect(registry.locator('.prd-prototype-table')).to_be_visible()
        expect(registry).to_have_url(re.compile(r'/#interface$'))

        preview = dialog.locator('.prd-prototype-edit-preview')
        chat = dialog.locator('.prd-prototype-chat')
        expect(preview).to_be_visible()
        expect(chat).to_be_visible()
        expect(dialog.get_by_role('heading', name='当前原型', exact=True)).to_have_count(0)
        expect(dialog.get_by_role('button', name='刷新预览', exact=True)).to_have_count(0)
        header_box = dialog.locator('.prd-prototype-editor-header').bounding_box()
        viewport_box = preview.locator('.prd-prototype-preview-viewport').bounding_box()
        assert abs(viewport_box['y'] - (header_box['y'] + header_box['height'])) <= 1, (viewport_box, header_box)
        preview_box, chat_box = preview.bounding_box(), chat.bounding_box()
        if narrow:
            assert preview_box['y'] < chat_box['y'], (preview_box, chat_box)
            for locator in (registry.locator('html'), dialog):
                assert locator.evaluate('(node) => node.scrollWidth - node.clientWidth') <= 2
        else:
            assert preview_box['x'] < chat_box['x'], (preview_box, chat_box)
            ratio = preview_box['width'] / (preview_box['width'] + chat_box['width'])
            assert .70 <= ratio <= .80, ratio

        login = expect_demo_login(preview, requests, form_id='prototype-editor-demo-login')
        expect(login).to_be_in_viewport(ratio=1)
        suffix = '-mobile' if narrow else ''
        registry.screenshot(path=str(output / f'workbench-editor-login{suffix}.png'))
        login.press('Enter')
        iframe = preview.locator('iframe[title="当前界面原型"]')
        expect(iframe).to_have_attribute('sandbox', 'allow-scripts')
        expect(iframe.content_frame.locator('.app-bar')).to_contain_text(ARCHIVE_TITLE)
        expect(preview.locator('form#prototype-editor-demo-login')).to_have_count(0)
        check_editor_preview_scale(registry, preview, iframe)

        if fullscreen_frame is not None:
            grid_columns = '(node) => getComputedStyle(node).gridTemplateColumns'
            assert iframe.content_frame.locator('.shell').evaluate(grid_columns) == fullscreen_frame.locator(
                '.shell').evaluate(grid_columns)

        if not narrow:
            iframe.evaluate('(frame) => { frame.dataset.mountProof = "keep"; }')
            original_viewport = registry.viewport_size
            registry.set_viewport_size({'width': 1120, 'height': 240})
            check_editor_preview_scale(registry, preview, iframe)
            expect(iframe).to_have_attribute('data-mount-proof', 'keep')
            registry.set_viewport_size(original_viewport)
            check_editor_preview_scale(registry, preview, iframe)
            menu = iframe.content_frame.get_by_role('navigation', name='业务菜单')
            click_scaled_preview(registry, iframe, menu.get_by_text('开票与收款', exact=True))
            invoice = menu.get_by_role('button', name='合同开票', exact=True)
            expect(invoice).to_be_visible()
            click_scaled_preview(registry, iframe, invoice)
            expect(invoice).to_have_attribute('aria-current', 'page')

        composer = chat.get_by_role('textbox', name='修订意见')
        expect(composer).to_be_editable()
        composer.fill('仅校验对话输入焦点')
        expect(chat.get_by_role('button', name='发送', exact=True)).to_be_enabled()
        registry.screenshot(path=str(output / f'workbench-editor-preview{suffix}.png'))
        composer.fill('')
        composer.press('Escape')
        expect(dialog).to_be_hidden()
        expect(edit).to_be_focused()
        assert all(method == 'GET' for method, _ in requests), requests
        if not narrow:
            initial_requests = len(requests)
            edit.press('Enter')
            expect_demo_login(preview, requests[initial_requests:], form_id='prototype-editor-demo-login')
            registry.keyboard.press('Escape')
            expect(dialog).to_be_hidden()
    finally:
        context.remove_listener('request', record)


def check_editor_candidate(browser, origin, model, output):
    updated_title = '候选合同台账验收'
    question = '请将标题改为候选合同台账验收，保留审批和开票流程。'
    allow_completion = threading.Event()

    def revise(*args, **kwargs):
        model.calls.append(kwargs['purpose'])
        assert allow_completion.wait(20), '浏览器没有完成待任务状态检查'
        return {'reply': '已更新原型标题。', 'design': dict(fixture_design(), title=updated_title)}

    model.json.side_effect = revise
    context = browser.new_context(viewport={'width': 1440, 'height': 960})
    errors, unapproved = register_observers(context, origin, allow_edits=True)
    try:
        page = context.new_page()
        page.goto(origin + '/#interface')
        row = page.get_by_role('row').filter(has=page.get_by_text(ARCHIVE_TITLE, exact=True))
        row.get_by_role('button', name='编辑', exact=True).click()
        dialog = page.locator('dialog#prototype-editor-dialog')
        dialog.locator('#prototype-editor-demo-login').get_by_role('button', name='登录', exact=True).click()
        current = dialog.locator('iframe[title="当前界面原型"]')
        expect(current.content_frame.locator('.app-bar')).to_contain_text(ARCHIVE_TITLE)
        version = dialog.locator('#prototype-editor-version').inner_text()
        initial_revision = int(version.rsplit(' ', 1)[-1])
        current.evaluate('(frame) => { frame.dataset.mountProof = "keep"; }')

        dialog.get_by_role('textbox', name='修订意见').fill(question)
        with page.expect_response(lambda response: response.url.endswith('/prototype/edit/chat')) as started:
            dialog.get_by_role('button', name='发送', exact=True).click()
        assert started.value.status == 202
        job_id = started.value.json()['job_id']
        with page.expect_response(lambda response: response.url.endswith('/jobs/' + job_id)):
            expect(dialog.get_by_role('log')).to_contain_text(question)
        expect(current).to_have_attribute('data-mount-proof', 'keep')

        allow_completion.set()
        candidate = dialog.locator('iframe[title="候选界面原型"]')
        expect(candidate.content_frame.locator('.app-bar')).to_contain_text(updated_title, timeout=15000)
        preview = dialog.locator('.prd-prototype-edit-preview')
        check_editor_preview_scale(page, preview, candidate)
        expect(dialog.get_by_role('log')).to_contain_text('已更新原型标题。')
        page.screenshot(path=str(output / 'workbench-editor-candidate.png'))

        with page.expect_response(lambda response: response.url.endswith('/prototype/edit/apply')) as applied:
            dialog.get_by_role('button', name='应用为新版本', exact=True).click()
        assert applied.value.ok and applied.value.json()['prototype']['revision'] == initial_revision + 1
        expect(current.content_frame.locator('.app-bar')).to_contain_text(updated_title)
        expect(current).to_have_attribute('src', re.compile(r'/prototype/html\?'))
        check_editor_preview_scale(page, preview, current)
        expect(candidate).to_have_count(0)
        expect(dialog.locator('#prototype-editor-demo-login')).to_have_count(0)
        expect(page.get_by_role('row').filter(has=page.get_by_text(updated_title, exact=True))).to_be_visible()
        assert model.calls == ['interface-prototype.edit.chat']
        assert not errors and not unapproved, (errors, unapproved)
        page.screenshot(path=str(output / 'workbench-editor-applied.png'))
    finally:
        allow_completion.set()
        context.close()


def open_menu(frame, group, page):
    category = frame.locator('.navigation details').filter(has=frame.locator('summary', has_text=group))
    entry = category.get_by_role('button', name=page, exact=True)
    if not category.evaluate('(element) => element.open'):
        category.locator('summary').click()
    entry.click()
    expect(entry).to_have_attribute('aria-current', 'page')
    return category


def check_layout(frame, runtime, output):
    navigation = frame.locator('.sidebar')
    workspace = frame.locator('.workspace')
    assistant = frame.get_by_role('complementary', name='AI 业务助手')
    initial_width = workspace.bounding_box()['width']
    for pane in (navigation, workspace, assistant):
        expect(pane).to_be_visible()
    assert navigation.bounding_box()['x'] < workspace.bounding_box()['x'] < assistant.bounding_box()['x']
    expect(frame.locator('.assistant-context')).to_be_visible()
    runtime.screenshot(path=str(output / 'workbench-three-panes.png'))

    toggle = frame.locator('.app-bar [data-toggle-panel="assistant"]')
    expect(toggle).to_have_attribute('aria-expanded', 'true')
    toggle.press('Enter')
    expect(toggle).to_have_attribute('aria-expanded', 'false')
    expect(assistant).to_be_hidden()
    expect(navigation).to_be_visible()
    assert workspace.bounding_box()['width'] > initial_width
    runtime.screenshot(path=str(output / 'workbench-assistant-collapsed.png'))
    toggle.press('Enter')
    expect(toggle).to_have_attribute('aria-expanded', 'true')
    expect(assistant).to_be_visible()
    expect(navigation).to_be_visible()

    contract_group = open_menu(frame, '合同管理', '合同台账')
    invoice_group = open_menu(frame, '开票与收款', '合同开票')
    assert not contract_group.evaluate('(element) => element.open')
    assert invoice_group.evaluate('(element) => element.open')


def choose_record(frame, entry, label, value):
    reference = entry.get_by_role('textbox', name=label, exact=True)
    expect(reference).to_have_attribute('readonly', '')
    chooser = entry.get_by_role('button', name='选择' + label, exact=True)
    reference.press('Tab')
    expect(chooser).to_be_focused()
    reference.press('Enter')
    choices = frame.get_by_role('dialog', name='选择' + label, exact=True)
    expect(choices).to_be_visible()
    option = choices.get_by_role('button', name=value, exact=True)
    option.press('Escape')
    expect(choices).to_be_hidden()
    expect(chooser).to_be_focused()

    chooser.press('Enter')
    option = choices.get_by_role('button', name=value, exact=True)
    option.press('Enter')
    expect(choices).to_be_hidden()
    expect(reference).to_have_value(value)
    expect(chooser).to_be_focused()


def check_contract_form(dialog):
    contract = dialog.get_by_role('textbox', name='合同编号', exact=True)
    expect(contract).to_have_accessible_description('填写业务合同的唯一编号。')
    dialog.locator('label').filter(has_text='合同编号').click()
    expect(contract).to_be_focused()
    date_input = dialog.get_by_label('签订日期', exact=True)
    expect(date_input).to_have_attribute('type', 'date')
    contract.press('Tab')
    expect(date_input).to_be_focused()
    expect(dialog.get_by_role('spinbutton', name='合同总金额', exact=True)).to_be_visible()


def add_record(frame, title, values, commit, *, select_values=None, reference_values=None):
    trigger = frame.get_by_role('button', name='新增' + title, exact=True)
    trigger.press('Enter')
    dialog = frame.get_by_role('dialog', name=title + '登记', exact=True)
    expect(dialog).to_be_visible()
    if title == '合同台账':
        check_contract_form(dialog)
    for label, value in values.items():
        dialog.get_by_label(label, exact=True).fill(value)
    for label, value in (select_values or {}).items():
        dialog.get_by_role('combobox', name=label, exact=True).select_option(value)
    for label, value in (reference_values or {}).items():
        choose_record(frame, dialog, label, value)
    dialog.get_by_role('button', name=commit, exact=True).press('Enter')
    expect(dialog).to_be_hidden()
    expect(trigger).to_be_focused()


def enter_preview_records(frame):
    today = date.today().isoformat()
    open_menu(frame, '基础数据', '部门维护')
    department = frame.locator('[data-page="department-maintain"]')
    department.get_by_label('部门编号', exact=True).fill('DEPT-01')
    department.get_by_label('部门名称', exact=True).fill('设备销售部')
    department.get_by_role('button', name='保存', exact=True).click()
    expect(department.locator('[data-result]')).to_contain_text('已保存')
    open_menu(frame, '合同管理', '合同台账')
    add_record(frame, '合同台账', {'合同编号': 'HT-BROWSER-01', '签订日期': today, '合同总金额': '1000',
                                 '部门编号': 'DEPT-01'}, '提交审批', select_values={'合同类型': '销售合同'})
    open_menu(frame, '开票与收款', '合同开票')
    add_record(frame, '合同开票', {'开票编号': 'FP-BROWSER-01', '开票日期': today, '开票金额': '700'},
               '保存', reference_values={'合同编号': 'HT-BROWSER-01'})
    open_menu(frame, '开票与收款', '合同收款')
    add_record(frame, '合同收款', {'收款日期': today, '收款金额': '300'},
               '保存', reference_values={'开票编号': 'FP-BROWSER-01'})
    receipt_page = frame.locator('[data-page="receipt-list"]')
    search = receipt_page.get_by_role('textbox', name='开票编号', exact=True)
    search.fill('FP-NOT-PRESENT')
    search.press('Enter')
    expect(receipt_page.get_by_role('table')).not_to_contain_text('FP-BROWSER-01')
    receipt_page.get_by_role('button', name='重置', exact=True).press('Enter')
    expect(receipt_page.get_by_role('table')).to_contain_text('FP-BROWSER-01')


def check_composer(frame):
    compose = frame.get_by_role('form', name='向助手提问')
    question = compose.get_by_role('textbox', name='业务问题')
    send = compose.get_by_role('button', name='发送', exact=True)
    expect(compose).to_be_visible()
    question.fill('检查键盘焦点')
    question.press('Tab')
    expect(send).to_be_focused()
    question.fill('')
    start_height = question.bounding_box()['height']
    question.fill('第一行\n第二行\n第三行\n第四行\n第五行')
    assert question.bounding_box()['height'] > start_height
    question.fill('')
    assert abs(question.bounding_box()['height'] - start_height) <= 1
    assert compose.evaluate('''form => {
        const outer = form.getBoundingClientRect();
        const textbox = form.querySelector('textarea');
        const controls = [textbox, form.querySelector('button[type=submit]')];
        return parseFloat(getComputedStyle(form).borderTopWidth) > 0
            && parseFloat(getComputedStyle(textbox).borderTopWidth) === 0
            && controls.every(control => {
                const rect = control.getBoundingClientRect();
                return rect.left >= outer.left && rect.right <= outer.right
                    && rect.top >= outer.top && rect.bottom <= outer.bottom;
            });
    }'''), 'The textarea and submit control should share one visible composer frame'


def check_assistant(frame, runtime, model, output):
    shortcuts = frame.locator('.assistant-questions')
    log = frame.get_by_role('log', name='助手消息')
    assistant = frame.get_by_role('complementary', name='AI 业务助手')
    expect(assistant.locator('.assistant-context')).to_have_text(re.compile(r'^当前窗口的预览记录\s*只读$'))
    expect(log).to_be_empty()
    expect(assistant.get_by_role('status')).to_be_empty()
    expect(assistant.locator('.assistant-head small, .assistant-foot')).to_have_count(0)
    check_composer(frame)
    checks = [
        ('待审批合同', 'HT-BROWSER-01'),
        ('本月签订与开票', '700'),
        ('未收款开票', 'FP-BROWSER-01'),
        ('各部门合同统计', '设备销售部'),
    ]
    for question, value in checks:
        shortcuts.get_by_role('button', name=question, exact=True).click()
        result = log.locator('.assistant-result').last
        expect(result.get_by_role('heading')).to_be_visible()
        expect(result.locator('.assistant-table-wrap > table')).to_contain_text(value)
        expect(log.locator('.assistant-message-source').last).to_have_text('预览统计')
        if question == '未收款开票':
            expect(result.locator('table')).to_contain_text('400')
    model.text.assert_not_called()
    runtime.screenshot(path=str(output / 'workbench-shortcuts.png'))

    question = '请说明当前预览如何开票。'
    field = frame.get_by_role('textbox', name='业务问题')
    field.fill(question)
    field.press('Enter')
    reply = log.locator('[data-role="assistant"]').last
    expect(reply.get_by_role('heading', name='审批说明')).to_be_visible()
    expect(reply.locator('strong')).to_have_text('当前预览')
    expect(reply.get_by_role('table')).to_contain_text('仅当前窗口')
    expect(reply).to_contain_text('<em>测试文字</em>')
    expect(reply.locator('em')).to_have_count(0)
    expect(reply.locator('.assistant-message-source')).to_have_text('GLM')
    expect(log.locator('.assistant-message-author')).to_have_count(0)
    model.text.assert_called_once()
    call = model.text.call_args
    assert call.kwargs['purpose'] == 'interface-prototype.assistant'
    context = json.loads(call.args[1])
    assert context['question'] == question
    assert '合同开票登记' in context['preview_summary'], context['preview_summary']
    runtime.screenshot(path=str(output / 'workbench-model-bridge.png'))


def check_mobile(browser, origin, output):
    context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
    errors, unapproved = register_observers(context, origin)
    try:
        runtime, frame = open_from_registry(context, origin)
        expect(frame.get_by_role('heading', name='合同台账')).to_be_visible()
        expect(frame.get_by_role('navigation', name='业务菜单')).to_be_visible()
        open_menu(frame, '开票与收款', '合同开票')
        expect(frame.get_by_role('heading', name='合同开票', exact=True)).to_be_visible()
        open_menu(frame, '合同管理', '合同台账')
        expect(frame.get_by_role('heading', name='合同台账', exact=True)).to_be_visible()
        assert runtime.locator('html').evaluate('(node) => node.scrollWidth - node.clientWidth') <= 2
        assert frame.locator('html').evaluate('(node) => node.scrollWidth - node.clientWidth') <= 2
        check_composer(frame)
        runtime.screenshot(path=str(output / 'workbench-mobile.png'))
        check_registry_editor(context, origin, output, narrow=True)
        assert not errors and not unapproved, (errors, unapproved)
    finally:
        context.close()


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='prototype-workbench-browser-') as root, fixture_server(root) as (origin, model):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                context = browser.new_context(viewport={'width': 1440, 'height': 960})
                errors, unapproved = register_observers(context, origin)
                try:
                    runtime, frame = open_from_registry(context, origin, output)
                    check_layout(frame, runtime, output)
                    enter_preview_records(frame)
                    check_assistant(frame, runtime, model, output)
                    _, archive_frame = open_from_registry(context, origin, title=ARCHIVE_TITLE)
                    open_from_registry(context, origin, title=REFERENCE_TITLE, reference=True)
                    check_registry_editor(context, origin, output, fullscreen_frame=archive_frame)
                except AssertionError as error:
                    if errors or unapproved:
                        error.add_note(f'Browser errors: {errors}\nUnexpected requests: {unapproved}')
                    raise
                assert not errors and not unapproved, (errors, unapproved)
                check_mobile(browser, origin, output)
                check_editor_candidate(browser, origin, model, output)
            finally:
                browser.close()
    print(f'Workbench browser checks passed. Evidence: {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output)
