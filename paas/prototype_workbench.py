"""Three-pane renderer for a validated prototype workbench design."""
from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache, partial
from pathlib import Path

from . import prototype_v9 as v9


ASSETS = Path(__file__).with_name('prototype_assets')
INITIALIZE_PAGES = '  if (pages.size) open(pages.keys().next().value);'
REFRESH_WORKBENCH = '    refreshReferences();\n    refreshMetrics();\n  }'
_esc = v9._esc
_attribute = v9._attribute


@lru_cache(maxsize=6)
def _script(runtime):
    # Share the core table, tab and form logic while keeping the original V9
    # script and its saved artifact hashes intact.
    start, marker, end = v9._SCRIPT.partition(INITIALIZE_PAGES)
    if not marker:
        raise RuntimeError('缺少原型工作区初始化入口')
    filename = f'workbench-v{runtime}.js' if runtime < 3 else 'workbench.js'
    extension = (ASSETS / filename).read_text(encoding='utf-8').strip()
    if runtime >= 4:
        before, refresh, after = extension.rpartition(REFRESH_WORKBENCH)
        if not refresh:
            raise RuntimeError('缺少业务助手初始化入口')
        assets = ['workbench-reports.js', 'workbench-console.js']
        if runtime >= 5:
            assets.append('workbench-polish.js')
        console = '\n'.join((ASSETS / asset).read_text(encoding='utf-8') for asset in assets)
        extension = before + console + '\n' + refresh + after
    return start + extension + '\n' + marker + end


def content_security_policy(*, runtime=3):
    script_hash = base64.b64encode(hashlib.sha256(_script(runtime).encode()).digest()).decode()
    return ("default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-" + script_hash
            + "'; connect-src 'none'; img-src 'none'; font-src 'none'; frame-src 'none';"
              " form-action 'none'; base-uri 'none'")


def _input(field, *, prefix='', filter_field=False, polished=False, control_id='', help_id=''):
    attrs = _attribute('data-reference-page', field.get('reference_page'))
    attrs += _attribute('data-reference-field', field.get('reference_field'))
    attrs += _attribute('data-reference-scope-field', field.get('reference_scope_field'))
    attrs += _attribute('id', control_id) + _attribute('aria-describedby', help_id)
    if polished and filter_field and field['type'] == 'text':
        attrs += ' placeholder="可输入部分内容"'
    chooser = ''
    if polished and field['type'] == 'reference':
        chooser = (_attribute('aria-label', '选择' + field['label'])
                   + ' aria-haspopup="dialog" aria-controls="workbench-reference"')
    return v9._input(field, prefix=prefix, filter_field=filter_field, input_attrs=attrs, chooser_attrs=chooser)


def _fields(fields, *, prefix='', compact=False, filter_fields=False, polished=False):
    controls = []
    for field in fields:
        long = field.get('wide') or field['type'] == 'textarea'
        classes = 'field long' if long else 'field'
        required = field.get('required') and not filter_fields
        marker = '<span class="required" aria-hidden="true">*</span>' if required else ''
        control_id = f'workbench:{prefix}:{"filter:" if filter_fields else ""}{field["id"]}' if polished else ''
        help_text = ''
        help_id = ''
        if field.get('help') and not filter_fields:
            help_id = control_id + ':help' if polished else ''
            help_text = f'<small class="help"{_attribute("id", help_id)}>{_esc(field["help"])}</small>'
        control = _input(field, prefix=prefix, filter_field=filter_fields, polished=polished,
                         control_id=control_id, help_id=help_id)
        tag = 'label' if polished else 'span'
        controls.append(f'<div class="{classes}"><{tag} class="field-label"{_attribute("for", control_id)}>'
                        f'{marker}{_esc(field["label"])}</{tag}>'
                        f'<div class="field-control">{control}{help_text}</div></div>')
    return f'<div class="{"fields three" if compact else "fields"}">{"".join(controls)}</div>'


def _action(action):
    if action['kind'] == 'open_form':
        return v9._button(action['label'], 'open_form', classes='btn primary', target=action['target_page'],
                          description=action.get('description', ''), extra=' data-kind="open_form"')
    return v9._action(action)


def _form(page, *, dialog=False, polished=False):
    master = page['layout'] == 'master_detail_form'
    body = _fields(page['fields'], prefix=page['id'], compact=master and not dialog, polished=polished)
    phase = '<span class="phase" data-phase hidden></span>'
    title = '开票信息' if dialog and '开票' in page['name'] else ('主表信息' if master else '表单信息')
    main = body + phase if dialog else v9._card(title, body, tools=phase)
    grid = v9._grid(page, input_renderer=partial(_input, polished=polished)) if master else ''
    return '<form data-main>' + main + grid + '</form>'


def _section_attributes(page):
    options = _attribute('data-record-source', page.get('record_source'))
    if page['layout'] == 'list_maintenance':
        for action in page['actions']:
            if action['kind'] in ('edit', 'delete'):
                options += _attribute(f'data-{action["kind"]}-label', action['label'])
    group = page.get('group') or '业务页面'
    return (f'data-page="{_esc(page["id"])}" data-group="{_esc(group)}" '
            f'data-layout="{_esc(page["layout"])}"{options}')


def _section(page, *, polished=False):
    layout = page['layout']
    actions = page['actions']
    context = {'navigate'}
    if layout in v9._FORM_LAYOUTS:
        body = _form(page, polished=polished)
        context |= {'submit', 'cancel', 'draft', 'save', 'create', 'reset'}
    elif layout in v9._LIST_LAYOUTS:
        table_tools = ''.join(_action(action) for action in actions if action['kind'] in ('open_form', 'create'))
        editor_fields = partial(_fields, prefix=page['id'] + ':editor', polished=True) if polished else None
        body = v9._query(page, layout == 'list_maintenance',
                         field_renderer=partial(_fields, polished=polished), table_tools=table_tools,
                         editor_field_renderer=editor_fields)
    else:
        body = v9._dashboard(page) if layout == 'dashboard' else v9._detail(page)
    header_actions = ''.join(_action(action) for action in actions if action['kind'] in context)
    return (f'<section class="page" {_section_attributes(page)} hidden>'
            f'<header class="page-heading"><div><h1>{_esc(page["name"])}</h1><p>{_esc(page["purpose"])}</p></div>'
            f'<div class="actions">{header_actions}</div></header>{body}'
            '<p class="page-status" data-result role="status"></p></section>')


def _entry(page, *, polished=False):
    title_id = 'entry-title-' + page['id']
    title = _esc(page['name'])
    controls = ''.join(_action(action) for action in page['actions'] if action['kind'] in ('draft', 'save', 'submit', 'cancel', 'reset'))
    return (f'<dialog class="invoice-dialog" data-entry-dialog aria-labelledby="{title_id}">'
            f'<section data-entry-page {_section_attributes(page)} hidden>'
            f'<header class="dialog-title"><h2 id="{title_id}">{title}</h2>'
            + v9._button('关闭', 'close-dialog', classes='btn mini') + '</header>'
            + '<div class="dialog-body">' + _form(page, dialog=True, polished=polished)
            + '<p class="page-status" data-result role="status"></p></div>'
            + f'<footer class="dialog-footer actions">{controls}</footer></section></dialog>')


def _assistant(design):
    options = design.get('assistant') or {}
    metrics = ''.join(f'<section{_attribute("data-metric-page", metric["page"])}'
                      f'{_attribute("data-metric-field", metric.get("field"))}>'
                      f'<small>{_esc(metric["label"])}</small><strong>0</strong></section>'
                      for metric in options.get('metrics', []))
    suggestions = options.get('suggestions') or ['有哪些可操作页面？', '当前有哪些预览记录？']
    shortcuts = ''.join(v9._button(text, 'assistant-question', classes='btn mini',
                                   extra=_attribute('data-question', text)) for text in suggestions)
    return ('<aside class="assistant" aria-label="AI 业务助手">'
            '<header class="assistant-head"><div><h2>AI 业务助手</h2>'
            '<small>只读 · 当前预览</small></div><small data-assistant-status>可提问</small></header>'
            + (f'<div class="assistant-metrics">{metrics}</div>' if metrics else '')
            + '<div class="assistant-log" role="log" aria-label="助手消息" aria-live="polite">'
            '<p>可以询问业务流程，也可以查询本次预览中的记录。</p></div>'
            f'<div class="assistant-questions">{shortcuts}</div>'
            '<form class="assistant-compose" aria-label="向助手提问">'
            '<textarea rows="2" maxlength="500" required aria-label="业务问题" placeholder="输入业务问题…"></textarea>'
            '<div class="actions"><button class="btn primary" type="submit">发送</button></div></form></aside>')


REPORT_PROFILE = {
    'contract': {'page': 'contract-entry', 'id': 'contract-no', 'date': 'sign-date',
                 'amount': 'total-amount', 'department': 'department-no'},
    'invoice': {'page': 'invoice-entry', 'id': 'invoice-no', 'contract': 'contract-no',
                'date': 'invoice-date', 'amount': 'invoice-amount'},
    'receipt': {'page': 'receipt-entry', 'invoice': 'invoice-no', 'date': 'receipt-time', 'amount': 'receipt-amount'},
    'department': {'page': 'department-maintain', 'id': 'department-no', 'name': 'department-name'},
}
REPORT_QUESTIONS = ('待审批合同', '本月签订与开票', '未收款开票', '各部门合同统计')


def _report_profile(design):
    pages = {page['id']: page for page in design['pages']}
    for entity in REPORT_PROFILE.values():
        source = pages.get(entity['page'], {})
        fields = {field['id']: field for field in source.get('fields', [])}
        if any(field_id not in fields for key, field_id in entity.items() if key != 'page'):
            return {}
        if ('amount' in entity and fields[entity['amount']]['type'] != 'number'
                or 'date' in entity and fields[entity['date']]['type'] != 'date'):
            return {}
    return REPORT_PROFILE


def _panel_control(panel, label, *, compact=False):
    css = 'assistant-collapse' if compact else 'app-control'
    return (f'<button type="button" class="{css}" data-toggle-panel="{panel}" '
            f'aria-controls="workbench-{panel}" aria-expanded="true">{label}</button>')


def _console_assistant(design, report, *, polished=False):
    options = design.get('assistant') or {}
    metrics = ''.join(f'<section{_attribute("data-metric-page", metric["page"])}'
                      f'{_attribute("data-metric-field", metric.get("field"))}>'
                      f'<small>{_esc(metric["label"])}</small><strong>0</strong></section>'
                      for metric in options.get('metrics', []))
    suggestions = REPORT_QUESTIONS if report else options.get('suggestions') or ['有哪些可操作页面？', '当前有哪些预览记录？']
    shortcuts = ''.join(v9._button(question, 'assistant-question', classes='btn mini',
                                   extra=_attribute('data-question', question)) for question in suggestions)
    configuration = _attribute('data-preview-report', json.dumps(report, ensure_ascii=False, separators=(',', ':'))) if report else ''
    subtitle = '' if polished else '<small>流程与记录查询</small>'
    context = ('<div class="assistant-context"><span>当前窗口的预览记录</span><small class="assistant-scope">只读</small></div>'
               if polished else '<div class="assistant-context"><span>当前预览记录</span>'
               '<small class="assistant-scope">只读</small><small data-assistant-status aria-live="polite">可提问</small></div>')
    welcome = '' if polished else '<p class="assistant-welcome">可查询本次录入的记录，也可以询问需求中的业务流程。</p>'
    activity = '<small data-assistant-status role="status"></small>' if polished else ''
    footer = '' if polished else '<small class="assistant-foot">预览记录仅保留在当前窗口</small>'
    placeholder = '输入问题，Enter 发送' if polished else '输入业务问题，Enter 发送'
    return (f'<aside class="assistant" id="workbench-assistant" aria-label="AI 业务助手"{configuration}>'
            f'<header class="assistant-head"><div><h2>AI 业务助手</h2>{subtitle}</div>'
            + _panel_control('assistant', '收起', compact=True) + '</header>'
            + context + f'<div class="assistant-metrics" hidden>{metrics}</div>'
            f'<div class="assistant-log" role="log" aria-label="助手消息" aria-live="polite">{welcome}</div>{activity}'
            f'<div class="assistant-questions" aria-label="快捷问法">{shortcuts}</div>'
            '<form class="assistant-compose" aria-label="向助手提问">'
            f'<textarea rows="2" maxlength="500" required aria-label="业务问题" placeholder="{placeholder}"></textarea>'
            '<div class="actions"><button class="btn primary" type="submit">发送</button></div></form>'
            + footer + '</aside>')


def _console_header(title, *, show_nav_toggle):
    nav_toggle = _panel_control('nav', '菜单') if show_nav_toggle else ''
    return ('<header class="app-bar"><div class="app-identity">'
            + nav_toggle + f'<strong>{title}</strong></div>'
            '<div class="app-actions"><span class="preview-label">运行预览</span>'
            + _panel_control('assistant', '助手') + '</div></header>')


def render(design, *, runtime=3):
    title = _esc(design.get('title') or '界面原型 · 业务工作台')
    pages = [page for page in design['pages'] if page.get('presentation') != 'dialog']
    entries = [page for page in design['pages'] if page.get('presentation') == 'dialog']
    console = runtime >= 4
    polished = runtime >= 5
    report = _report_profile(design) if console else {}
    if report:
        pages = [dict(page, group='开票与收款') if page.get('group') in ('开票管理', '收款管理') else page
                 for page in pages]
    csp = _esc(content_security_policy(runtime=runtime))
    css = v9._STYLE + '\n' + (ASSETS / 'workbench.css').read_text(encoding='utf-8')
    if runtime >= 3:
        css += '\n' + (ASSETS / 'workbench-dialog.css').read_text(encoding='utf-8')
    if console:
        css += '\n' + (ASSETS / 'workbench-console.css').read_text(encoding='utf-8')
    if polished:
        css += '\n' + (ASSETS / 'workbench-polish.css').read_text(encoding='utf-8')
    header = _console_header(title, show_nav_toggle=runtime < 6) if console else (
        '<header class="app-bar"><div class="app-identity"><span class="mark" aria-hidden="true"></span>'
        f'<strong>{title}</strong></div><span class="preview-label">交互原型 · 预览记录临时保存</span></header>')
    sidebar = ('<aside class="sidebar" id="workbench-nav"><h2 class="sidebar-heading">业务功能</h2>' if console
               else '<aside class="sidebar">')
    assistant = _console_assistant(design, report, polished=polished) if console else _assistant(design)
    reference_id = ' id="workbench-reference"' if polished else ''
    drawers = ('<dialog class="drawer" data-drawer aria-labelledby="detail-title">'
               + v9._dialog_head('详情', 'detail-title')
               + '<div class="dialog-body"><dl class="record-details"></dl><div data-drawer-lines></div></div></dialog>'
               + f'<dialog{reference_id} data-reference-dialog aria-labelledby="reference-title">'
               + v9._dialog_head('选择关联项', 'reference-title')
               + '<div class="dialog-body options"></div></dialog>'
               + '<dialog class="compact-dialog" data-delete-dialog aria-labelledby="delete-title">'
               + v9._dialog_head('删除记录', 'delete-title')
               + '<div class="dialog-body">删除当前预览记录？</div><footer class="dialog-footer actions">'
               + v9._button('取消', 'close-dialog')
               + v9._button('确认删除', 'delete-confirm', classes='btn danger') + '</footer></dialog>')
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta http-equiv="Content-Security-Policy" content="{csp}"><title>{title}</title>'
            f'<!--\n界面组件参考 sharptoolbox/ontology-driven-dev MIT V9 规范。\n{v9._LICENSE}\n--><style>{css}</style></head><body>'
            + header + '<div class="shell">' + sidebar + '<nav class="navigation" aria-label="业务菜单">'
            + v9._navigation(pages) + '</nav></aside>'
            '<div class="workspace"><header class="workspace-bar"><span class="breadcrumb"></span></header>'
            '<div class="tabs" role="navigation" aria-label="已打开页面"></div><main>'
            + ''.join(_section(page, polished=polished) for page in pages)
            + f'<p class="preview-summary">{_esc(design["summary"])}</p></main></div>'
            + assistant + '</div>' + ''.join(_entry(page, polished=polished) for page in entries)
            + drawers + f'<script>{_script(runtime)}</script></body></html>')
