"""Requirement-bound UI prototypes: model plans, deterministic browser code.

The model supplies a bounded page/field/action description. Only this module
renders executable HTML; arbitrary model HTML, scripts and business records are
never accepted. A preview is a design artifact, independent of a built app.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import time
import uuid
from pathlib import Path

from .store import digest

LAYOUTS = ('list', 'detail', 'form', 'dashboard')
FIELD_TYPES = ('text', 'number', 'date', 'select', 'textarea', 'checkbox')
ACTION_KINDS = ('navigate', 'submit', 'query', 'cancel')
LEGACY_TEMPLATE = 'legacy'
V9_TEMPLATE = 'ontology-v9'
WORKBENCH_V1_TEMPLATE = 'ontology-v9-workbench-1'
WORKBENCH_V2_TEMPLATE = 'ontology-v9-workbench-2'
WORKBENCH_V3_TEMPLATE = 'ontology-v9-workbench-3'
WORKBENCH_V4_TEMPLATE = 'ontology-v9-workbench-4'
WORKBENCH_V5_TEMPLATE = 'ontology-v9-workbench-5'
WORKBENCH_V6_TEMPLATE = 'ontology-v9-workbench-6'
WORKBENCH_TEMPLATE = 'ontology-v9-workbench-7'
WORKBENCH_TEMPLATES = (WORKBENCH_V1_TEMPLATE, WORKBENCH_V2_TEMPLATE, WORKBENCH_V3_TEMPLATE,
                       WORKBENCH_V4_TEMPLATE, WORKBENCH_V5_TEMPLATE, WORKBENCH_V6_TEMPLATE,
                       WORKBENCH_TEMPLATE)
V9_LAYOUTS = ('single_form', 'list_maintenance', 'master_detail_form', 'query_list')
V9_ACTIONS = ('draft', 'save', 'create', 'edit', 'delete', 'reset')
UI_REFERENCE = Path(__file__).with_name('upstream') / 'ontology-driven-dev' / 'references' / 'UI-UE界面设计规范.md'
WORKBENCH_SKILL = UI_REFERENCE.parent.parent / 'SKILL.md'
WORKBENCH_PROMPT = Path(__file__).with_name('prototype_assets') / 'workbench-prompt.md'
RECORD_LAYOUTS = {'single_form', 'list_maintenance', 'master_detail_form'}
DIALOG_LAYOUTS = {'single_form', 'master_detail_form', 'form'}
_ID = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,63}')


def _text(value, label, limit=1000, *, optional=False):
    if not isinstance(value, str) or len(value) > limit or ('\x00' in value):
        raise ValueError(label + '必须为长度有效的文本')
    value = value.strip()
    if not value and not optional:
        raise ValueError(label + '不能为空')
    return value


def _id(value, label):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(label + '必须为字母开头的稳定英文标识')
    return value


def _keys(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(label + '包含未支持的结构；请使用页面、字段和操作描述')


def _items(value, label, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(label + '必须为最多' + str(maximum) + '项的列表')
    return value


def _fields(raw_fields, *, v9, workbench=False):
    fields = []
    for field in _items(raw_fields, '字段', 40):
        extra = ('wide', 'reference_page', 'reference_field', 'reference_scope_field') if workbench else ()
        _keys(field, ('id', 'label', 'type', 'required', 'options', 'help') + extra, '字段')
        item = {'id': _id(field.get('id'), '字段ID'),
                'label': _text(field.get('label'), '字段名称', 120),
                'type': field.get('type', 'text'), 'required': field.get('required', False),
                'help': _text(field.get('help', ''), '字段说明', 600, optional=True), 'options': []}
        if item['type'] not in FIELD_TYPES + (('reference',) if v9 else ()) or not isinstance(item['required'], bool):
            raise ValueError('字段类型或必填标记无效')
        item['options'] = [_text(option, '选项', 120) for option in _items(field.get('options', []), '选项', 50)]
        if item['type'] == 'select' and not item['options']:
            raise ValueError('下拉字段需要明确选项')
        if item['type'] not in ('select', 'reference') and item['options']:
            raise ValueError('下拉字段或对象引用字段可以指定选项')
        if 'wide' in field:
            if not isinstance(field['wide'], bool):
                raise ValueError('字段是否占整行须为布尔值')
            item['wide'] = field['wide']
        if any(key in field for key in ('reference_page', 'reference_field', 'reference_scope_field')):
            if item['type'] != 'reference' or item['options']:
                raise ValueError('只有对象引用字段支持关联预览记录')
            item['reference_page'] = _id(field.get('reference_page'), '引用来源界面')
            item['reference_field'] = _id(field.get('reference_field'), '引用显示字段')
            if 'reference_scope_field' in field:
                item['reference_scope_field'] = _id(field['reference_scope_field'], '本表关联对象字段')
        fields.append(item)
    return fields


def _uses_v9(template_version):
    if template_version not in (LEGACY_TEMPLATE, V9_TEMPLATE, *WORKBENCH_TEMPLATES):
        raise ValueError('原型模板版本无效')
    return template_version != LEGACY_TEMPLATE


def validate_design(value, *, template_version=WORKBENCH_TEMPLATE):
    """Normalize the public design schema, rejecting executable/unknown fields."""
    v9 = _uses_v9(template_version)
    workbench = template_version in WORKBENCH_TEMPLATES
    _keys(value, ('summary', 'pages') + (('title', 'assistant') if workbench else ()), '界面描述')
    summary = _text(value.get('summary'), '界面说明', 4000)
    pages = []
    for raw in _items(value.get('pages'), '页面', 24):
        optional = ('group', 'detail_fields', 'detail_title', 'record_source') if v9 else ()
        if workbench:
            optional += ('presentation',)
        _keys(raw, ('id', 'name', 'purpose', 'layout', 'fields', 'columns', 'actions') + optional, '页面')
        page = {
            'id': _id(raw.get('id'), '页面ID'),
            'name': _text(raw.get('name'), '页面名称', 120),
            'purpose': _text(raw.get('purpose'), '页面用途', 1200),
            'layout': raw.get('layout'), 'fields': _fields(raw.get('fields', []), v9=v9, workbench=workbench),
            'columns': [], 'actions': [],
        }
        if v9 and isinstance(page['layout'], str):
            page['layout'] = page['layout'].lower()
        if page['layout'] not in LAYOUTS + (V9_LAYOUTS if v9 else ()):
            raise ValueError('页面布局类型无效')
        if 'group' in raw:
            page['group'] = _text(raw['group'], '一级菜单名称', 120)
        if 'presentation' in raw:
            if raw['presentation'] != 'dialog' or page['layout'] not in DIALOG_LAYOUTS:
                raise ValueError('只有录入表单允许以 dialog 弹窗展示')
            page['presentation'] = 'dialog'
        if raw.get('record_source'):
            if page['layout'] != 'query_list':
                raise ValueError('只有查询列表可关联预览记录来源')
            page['record_source'] = _id(raw['record_source'], '预览记录来源')
        if page['layout'] == 'master_detail_form':
            page['detail_title'] = _text(raw.get('detail_title', '业务明细'), '明细标题', 120)
            page['detail_fields'] = _fields(raw.get('detail_fields', []), v9=True, workbench=workbench)
            if not page['detail_fields']:
                raise ValueError('主从表需要至少一个从表字段')
        elif raw.get('detail_title') or raw.get('detail_fields'):
            raise ValueError('只有主从表布局可设置明细')
        for column in _items(raw.get('columns', []), '结果列', 40):
            _keys(column, ('id', 'label'), '结果列')
            page['columns'].append({'id': _id(column.get('id'), '结果列ID'),
                                    'label': _text(column.get('label'), '结果列名称', 120)})
        for action in _items(raw.get('actions', []), '操作', 20):
            _keys(action, ('id', 'label', 'kind', 'target_page', 'description'), '操作')
            item = {'id': _id(action.get('id'), '操作ID'),
                    'label': _text(action.get('label'), '操作名称', 120),
                    'kind': action.get('kind'), 'target_page': action.get('target_page', ''),
                    'description': _text(action.get('description', ''), '操作说明', 600, optional=True)}
            if item['kind'] not in ACTION_KINDS + (V9_ACTIONS if v9 else ()) + (('open_form',) if workbench else ()):
                raise ValueError('未支持的界面操作类型')
            if item['kind'] in ('navigate', 'open_form'):
                _id(item['target_page'], '导航目标')
            elif item['target_page']:
                raise ValueError('只有导航或打开表单可设置目标页面')
            page['actions'].append(item)
        for key in ('fields', 'columns', 'actions', 'detail_fields'):
            ids = [item['id'] for item in page.get(key, [])]
            if len(ids) != len(set(ids)):
                raise ValueError(page['name'] + '存在重复' + key + '标识')
        pages.append(page)
    ids = [page['id'] for page in pages]
    if not pages or len(ids) != len(set(ids)):
        raise ValueError('至少需要一个页面，且页面ID不得重复')
    record_layouts = RECORD_LAYOUTS | ({'form'} if workbench else set())
    record_pages = {page['id']: page for page in pages if page['layout'] in record_layouts}
    dialog_pages = {page['id'] for page in pages if page.get('presentation') == 'dialog'}
    opened = set()
    for page in pages:
        if page.get('record_source') and page['record_source'] not in record_pages:
            raise ValueError('预览记录来源必须指向本原型的维护界面')
        for action in page['actions']:
            kind, target = action['kind'], action['target_page']
            if kind == 'navigate' and (target not in ids or target in dialog_pages):
                raise ValueError('导航目标必须为可见界面：' + target)
            if kind == 'open_form':
                if target not in dialog_pages or page['id'] in dialog_pages or page['layout'] not in ('list', 'query_list', 'list_maintenance'):
                    raise ValueError('打开表单操作须从列表指向弹窗表单：' + target)
                opened.add(target)
        _validate_references(page, record_pages)
    if opened != dialog_pages:
        raise ValueError('每个弹窗必须有从列表打开的操作')
    design = {'summary': summary, 'pages': pages}
    if 'title' in value:
        design['title'] = _text(value['title'], '原型工作台名称', 120)
    if 'assistant' in value:
        design['assistant'] = _assistant(value['assistant'], record_pages)
    return design


def _validate_references(page, record_pages):
    main_fields = {field['id']: field for field in page['fields']}
    for field in page['fields'] + page.get('detail_fields', []):
        source_id = field.get('reference_page')
        if not source_id:
            continue
        source = record_pages.get(source_id, {})
        scope_id = field.get('reference_scope_field')
        target_fields = source.get('detail_fields' if scope_id else 'fields', [])
        if source_id == page['id'] or field['reference_field'] not in {item['id'] for item in target_fields}:
            raise ValueError('引用控件须指向其他维护界面的真实字段：' + field['label'])
        if scope_id:
            scope = main_fields.get(scope_id, {})
            if (field not in page.get('detail_fields', []) or scope.get('reference_page') != source_id
                    or scope.get('reference_scope_field')):
                raise ValueError('从表引用须按本页主表引用的关联对象筛选：' + field['label'])


def _assistant(raw, record_pages):
    _keys(raw, ('suggestions', 'metrics'), '业务助手')
    suggestions = [_text(item, '助手示例问题', 100) for item in _items(raw.get('suggestions', []), '示例问题', 5)]
    metrics = []
    for item in _items(raw.get('metrics', []), '预览指标', 4):
        _keys(item, ('label', 'page', 'field'), '预览指标')
        source = _id(item.get('page'), '指标来源界面')
        if source not in record_pages:
            raise ValueError('预览指标须引用真实录入界面')
        metric = {'label': _text(item.get('label'), '指标名称', 60), 'page': source}
        if item.get('field'):
            field = _id(item['field'], '指标求和字段')
            if field not in {entry['id'] for entry in record_pages[source]['fields'] if entry['type'] == 'number'}:
                raise ValueError('预览指标求和必须引用数值字段')
            metric['field'] = field
        metrics.append(metric)
    return {'suggestions': suggestions, 'metrics': metrics}


_STYLE = """
*{box-sizing:border-box}body{margin:0;font:14px/1.6 system-ui,-apple-system,sans-serif;color:#26374c;background:#f6f8fb;overflow-wrap:anywhere}button,input,select,textarea{font:inherit}button{cursor:pointer;background:#fff;color:inherit;border:1px solid #dbe2eb;border-radius:6px;padding:8px 13px}button.primary{background:#3478f6;color:#fff;border-color:#3478f6}header{padding:18px 24px;background:#fff;border-bottom:1px solid #e2e7ee;display:flex;gap:14px;align-items:center}.badge{font-size:11px;color:#54749a;background:#edf3fc;padding:3px 9px;border-radius:4px}.shell{display:flex;min-height:calc(100vh - 65px)}nav{width:220px;padding:18px 12px;background:#fff;border-right:1px solid #e2e7ee;flex-shrink:0}nav button{display:block;width:100%;text-align:left;margin-bottom:6px;border-color:transparent}nav button.active{color:#276bd3;background:#eaf2ff}main{padding:28px;min-width:0;flex:1}section[hidden]{display:none}.intro{margin:0 0 20px;color:#7c8a9c;font-size:12px}.panel{background:#fff;border:1px solid #e0e6ee;border-radius:8px;padding:22px}.toolbar{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:20px}h1{font-size:18px;margin:0}h2{font-size:20px;margin:0}p{margin:8px 0;color:#758399}.fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;margin:22px 0}label{display:flex;flex-direction:column;gap:7px;color:#5e6f83}label.checkbox{flex-direction:row;align-items:center}input,select,textarea{width:100%;min-width:0;padding:9px;border:1px solid #d7dfe9;border-radius:5px;background:#fff;color:#27374c}input[type=checkbox]{width:auto}.help{font-size:11px;color:#91a0b0}.actions{display:flex;gap:9px;flex-wrap:wrap}.actions button{max-width:100%;overflow-wrap:anywhere}.table-wrap{overflow:auto;margin-top:22px}table{width:100%;border-collapse:collapse;white-space:nowrap}th,td{text-align:left;padding:12px;border-bottom:1px solid #e8edf3}th{font-size:12px;background:#f8fafc;color:#7c8b9d}.empty{text-align:center;color:#94a1b0;padding:42px}.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-top:20px}.metric{border:1px solid #e5eaf0;border-radius:6px;padding:18px}.metric strong{display:block;font-size:24px;margin-top:8px}.notice{color:#2e6db4;font-size:12px;min-height:22px;margin:15px 0 0}.summary{font-size:12px;color:#8390a2;margin-top:24px}@media(max-width:680px){header{padding:14px 16px}.shell{display:block}nav{width:100%;display:flex;gap:6px;overflow:auto;border-right:0;border-bottom:1px solid #e2e7ee;padding:10px}nav button{white-space:nowrap;width:auto;margin:0}main{padding:16px}.fields{grid-template-columns:1fr}.toolbar{display:block}.toolbar .actions{margin-top:12px}.metrics{grid-template-columns:1fr}}
""".strip()

_SCRIPT = """
(()=>{
const pages=[...document.querySelectorAll('[data-page]')];
function open(id){pages.forEach(p=>p.hidden=p.dataset.page!==id);document.querySelectorAll('nav [data-target]').forEach(b=>b.classList.toggle('active',b.dataset.target===id));}
document.addEventListener('click',event=>{const button=event.target.closest('button');if(!button)return;if(button.dataset.target){open(button.dataset.target);return;}const section=button.closest('[data-page]');if(!section)return;const kind=button.dataset.kind;const form=section.querySelector('form');const message=section.querySelector('[role=status]');if(kind==='cancel'){form?.reset();message.textContent='已清除本页预览输入。';}else if(kind==='submit'){if(form&&!form.reportValidity())return;message.textContent='已检查填写内容。业务提交将在应用构建后启用。';}else if(kind==='query'){message.textContent='已预览筛选条件。业务结果将在应用构建后显示。';}});
document.addEventListener('submit',event=>event.preventDefault());
if(pages.length)open(pages[0].dataset.page);
})();
""".strip()


def content_security_policy(*, template_version=WORKBENCH_TEMPLATE):
    """Shared meta/header policy; callers add their embedding sandbox policy."""
    if template_version in WORKBENCH_TEMPLATES:
        from . import prototype_workbench
        return prototype_workbench.content_security_policy(runtime=WORKBENCH_TEMPLATES.index(template_version) + 1)
    if _uses_v9(template_version):
        from . import prototype_v9
        return prototype_v9.content_security_policy()
    script_hash = base64.b64encode(hashlib.sha256(_SCRIPT.encode()).digest()).decode()
    return ("default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-" + script_hash
            + "'; connect-src 'none'; img-src 'none'; font-src 'none'; frame-src 'none';"
              " form-action 'none'; base-uri 'none'")


def render(design, *, template_version=WORKBENCH_TEMPLATE):
    design = validate_design(design, template_version=template_version)
    if template_version in WORKBENCH_TEMPLATES:
        from . import prototype_workbench
        return prototype_workbench.render(design, runtime=WORKBENCH_TEMPLATES.index(template_version) + 1)
    if _uses_v9(template_version):
        from . import prototype_v9
        return prototype_v9.render(design)
    escape = lambda value: html.escape(str(value), quote=True)
    navigation = ''.join('<button type="button" data-target="' + page['id'] + '">' + escape(page['name']) + '</button>' for page in design['pages'])
    sections = []
    for page in design['pages']:
        fields = []
        for field in page['fields']:
            name = page['id'] + '-' + field['id']
            required = ' required' if field['required'] else ''
            if field['type'] == 'textarea':
                control = '<textarea name="' + name + '" rows="3"' + required + '></textarea>'
            elif field['type'] == 'select':
                control = '<select name="' + name + '"' + required + '><option value="">请选择</option>' + ''.join('<option value="' + escape(option) + '">' + escape(option) + '</option>' for option in field['options']) + '</select>'
            else:
                control = '<input name="' + name + '" type="' + field['type'] + '"' + required + '>'
            label = escape(field['label']) + (' <span aria-label="必填">*</span>' if field['required'] else '')
            help_text = '<small class="help">' + escape(field['help']) + '</small>' if field['help'] else ''
            fields.append('<label' + (' class="checkbox"' if field['type'] == 'checkbox' else '')
                          + '><span class="field-label">' + label + '</span>' + control + help_text + '</label>')
        actions = []
        for action in page['actions']:
            target = ' data-target="' + action['target_page'] + '"' if action['kind'] == 'navigate' else ''
            actions.append('<button type="button" data-kind="' + action['kind'] + '"' + target + (' class="primary"' if action['kind'] == 'submit' else '') + ' title="' + escape(action['description']) + '">' + escape(action['label']) + '</button>')
        if page['layout'] == 'dashboard':
            results = '<div class="metrics">' + ''.join('<div class="metric">' + escape(column['label']) + '<strong>—</strong><small class="help">待应用连接业务数据</small></div>' for column in page['columns']) + '</div>'
        elif page['columns']:
            results = '<div class="table-wrap"><table><thead><tr>' + ''.join('<th>' + escape(column['label']) + '</th>' for column in page['columns']) + '</tr></thead><tbody><tr><td class="empty" colspan="' + str(len(page['columns'])) + '">应用构建后显示实际业务记录</td></tr></tbody></table></div>'
        else:
            results = ''
        sections.append('<section data-page="' + page['id'] + '" hidden><div class="panel"><div class="toolbar"><div><h2>' + escape(page['name']) + '</h2><p>' + escape(page['purpose']) + '</p></div></div><form class="fields">' + ''.join(fields) + '</form><div class="actions">' + ''.join(actions) + '</div>' + results + '<p class="notice" role="status" aria-live="polite"></p></div></section>')
    csp = content_security_policy(template_version=LEGACY_TEMPLATE)
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="' + escape(csp) + '"><title>界面原型</title><style>' + _STYLE + '</style></head><body><header><span class="badge">界面原型</span><h1>页面与操作预览</h1></header><div class="shell"><nav aria-label="原型页面">' + navigation + '</nav><main><p class="intro">用于确认页面与操作，数据录入将在应用构建后启用。</p>' + ''.join(sections) + '<p class="summary">' + escape(design['summary']) + '</p></main></div><script>' + _SCRIPT + '</script></body></html>'


def _design(item):
    design = {key: item.get(key) for key in ('summary', 'pages')}
    if item.get('template_version') in WORKBENCH_TEMPLATES:
        design.update({key: item[key] for key in ('title', 'assistant') if key in item})
    return design


def _identity(project_id, requirements_sha256, design, rendered, template_version):
    content = {'project_id': project_id, 'requirements_sha256': requirements_sha256, 'design': design,
               'html_sha256': hashlib.sha256(rendered.encode()).hexdigest()}
    if template_version != LEGACY_TEMPLATE:
        content['template_version'] = template_version
    return digest(content)


def render_artifact(item):
    template_version = item.get('template_version', LEGACY_TEMPLATE)
    return render(_design(item), template_version=template_version), content_security_policy(template_version=template_version)


def platform_preview(rendered, policy, *, template_version):
    """Style validated HTML for the PaaS viewer without changing artifact identity."""
    directory = Path(__file__).parent
    web = directory / 'web'
    assets = directory / 'prototype_assets'
    shared = (web / 'ui-system.css').read_text(encoding='utf-8')
    styles = (assets / 'platform-preview.css').read_text(encoding='utf-8')
    layout = {LEGACY_TEMPLATE: 'legacy', V9_TEMPLATE: 'v9'}.get(template_version, 'workbench')
    mark = '<span class="ui-brand-mark" aria-hidden="true">O</span>'
    header = ''
    if layout == 'v9':
        header = (f'<header class="app-bar"><div class="app-identity">{mark}<strong>界面原型</strong></div>'
                  '<span class="preview-label">运行预览</span></header>')
    elif layout == 'legacy':
        rendered = rendered.replace('<body><header>', f'<body><header>{mark}', 1)
    else:
        rendered = rendered.replace('<div class="app-identity">', f'<div class="app-identity">{mark}', 1)
        rendered, policy = _elastic_preview_composer(rendered, policy, web, assets)
    rendered = rendered.replace(
        '</head><body>',
        f'<style>{shared}\n{styles}</style></head><body data-ui-preview="{layout}">{header}',
        1,
    )
    return rendered, policy


def _elastic_preview_composer(rendered, policy, web, assets):
    # The preview sandbox grants access to exact script hashes only.
    component = (web / 'elastic-textarea.js').read_text(encoding='utf-8')
    behavior = (assets / 'platform-preview.js').read_text(encoding='utf-8')
    script = component + '\n' + behavior
    license_text = (web / 'elastic-textarea.LICENSE.txt').read_text(encoding='utf-8')
    script_hash = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    viewer_policy = policy.replace('script-src ', f"script-src 'sha256-{script_hash}' ", 1)
    rendered = rendered.replace(html.escape(policy, quote=True), html.escape(viewer_policy, quote=True), 1)
    head, composer, rest = rendered.partition('<form class="assistant-compose" aria-label="向助手提问">')
    field, close, tail = rest.partition('</textarea>')
    if not composer or not close:
        raise RuntimeError('缺少原型助手输入框')
    return (f'{head}{composer}<elastic-textarea>{field}{close}</elastic-textarea>'
            f'{tail.removesuffix("</body></html>")}<!--\n{license_text}\n--><script>{script}</script></body></html>',
            viewer_policy)


def is_current(project):
    value = project.get('prototype')
    if not isinstance(value, dict) or not project.get('requirements'):
        return False
    try:
        template_version = value.get('template_version', LEGACY_TEMPLATE)
        design = validate_design(_design(value), template_version=template_version)
        rendered = render(design, template_version=template_version)
        expected = digest(project['requirements'])
        return (value.get('project_id') == project.get('id')
                and value.get('requirements_sha256') == expected
                and value.get('sha256') == _identity(project.get('id'), expected, design, rendered, template_version)
                and value.get('html') == rendered)
    except (TypeError, ValueError):
        return False


def assert_confirmed(project):
    """Validate the concrete current artifact; callers also guard the SRS gate."""
    record = project.get('prototype_confirmation')
    if (not is_current(project) or not isinstance(record, dict)
            or record.get('sha256') != project['prototype']['sha256']
            or record.get('requirements_sha256') != digest(project['requirements'])):
        raise ValueError('请先确认当前需求版本的界面原型')
    return project['prototype']


def generation_prompt():
    prompt = WORKBENCH_PROMPT.read_text(encoding='utf-8')
    skill = WORKBENCH_SKILL.read_text(encoding='utf-8')
    skill_start = skill.index('### 仅生成界面原型（PaaS）')
    skill_note = skill[skill_start:skill.index('## 四、', skill_start)].strip()
    reference = UI_REFERENCE.read_text(encoding='utf-8')
    sections = []
    for first, after in ((3, 4), (6, 8), (11, 12)):
        start = reference.index(f'## {first}.')
        sections.append(reference[start:reference.index(f'## {after}.', start)].strip())
    return prompt + '\n\n' + skill_note + '\n\n## 项目内的开源界面规范节选\n\n' + '\n\n'.join(sections)


def _validate_generation(project, raw):
    design = validate_design(raw)
    source = (project.get('upstream_import') or {}).get('source') or {}
    if source.get('repository') != 'https://github.com/sharptoolbox/ontology-driven-dev':
        return design
    from .contract_reference import reference_inputs
    if project['requirements'] != reference_inputs()['requirements']:
        return design
    pages = design['pages']
    invoice = next((page for page in pages if re.search('开票|发票', page['name']) and
                    page.get('presentation') == 'dialog' and page['layout'] == 'master_detail_form' and
                    {'draft', 'submit'} <= {action['kind'] for action in page['actions']}), None)
    linked = invoice and any(
        page['layout'] == 'query_list' and page.get('record_source') == invoice['id'] and
        any(action['kind'] == 'open_form' and action['target_page'] == invoice['id'] for action in page['actions'])
        for page in pages)
    if not linked:
        raise ValueError('当前合同 PRD 需要从开票查询同时打开主从表弹窗，查询引用该弹窗记录，弹窗保留开票分摊、保存草稿和提交审批')
    if not any(re.search('付款阶段', field['label']) and field.get('reference_scope_field')
               for field in invoice['detail_fields']):
        raise ValueError('分摊明细的付款阶段应为引用控件，按主表已选对应合同筛选该合同下的付款阶段')
    if not (design.get('assistant') or {}).get('suggestions'):
        raise ValueError('为当前合同工作台配置右侧助手的合同、开票或审批示例问题')
    return design


def _generate_artifact(project, design, llm, before, feedback, prompt):
    history = project.get('prototype_history') or []
    previous = project.get('prototype') or (history[-1] if history else {})
    requirement_sha = digest(project['requirements'])
    rendered = render(design, template_version=WORKBENCH_TEMPLATE)
    return {'id': uuid.uuid4().hex, 'project_id': project['id'],
            'revision': int(previous.get('revision', 0)) + 1,
            'requirements_sha256': requirement_sha, 'template_version': WORKBENCH_TEMPLATE,
            'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
            **design, 'feedback': feedback, 'previous_id': previous.get('id'),
            'created_at': time.time(), 'model_called': len(getattr(llm, 'calls', [])) > before,
            'html': rendered, 'sha256': _identity(project['id'], requirement_sha, design, rendered, WORKBENCH_TEMPLATE)}


def _model_result(llm, system, payload, purpose, repair_purpose, validate):
    budget = 24000
    raw = None
    try:
        raw = llm.json(system, json.dumps(payload, ensure_ascii=False), purpose=purpose, max_tokens=budget)
    except ValueError as error:
        if not any(marker in str(error) for marker in ('输出达到长度上限', '无法解析为 JSON')):
            raise
        reason = str(error)
    else:
        try:
            return validate(raw)
        except ValueError as error:
            reason = str(error)
    repaired = llm.json(
        system + '\n本次重试请逐页检查所有非主从表的明细键、普通入口和弹窗入口的目标、助手列表项数及引用。'
        '按校验错误修复同类问题，保留必要界面与稳定 ID。',
        json.dumps({'input': payload, 'invalid_design': raw, 'validation_error': reason}, ensure_ascii=False),
        purpose=repair_purpose, max_tokens=budget,
    )
    return validate(repaired)


def generate(project, llm, feedback=''):
    if project.get('kind') != 'business':
        raise ValueError('只有业务应用项目可以生成界面原型')
    requirements = project.get('requirements')
    if not isinstance(requirements, str) or not requirements.strip() or len(requirements) > 250000:
        raise ValueError('请先生成并确认有效的需求文档')
    confirmation = project.get('requirements_confirmation') or {}
    requirement_sha = digest(requirements)
    if confirmation.get('sha256') != requirement_sha:
        raise ValueError('请先确认当前版本的需求文档')
    feedback = _text(feedback, '修订意见', 8000, optional=True)
    previous = project.get('prototype')
    if feedback and not isinstance(previous, dict):
        raise ValueError('尚无可修订的界面原型，请先生成')
    if feedback and not is_current(project):
        raise ValueError('原型不再对应当前需求，请先基于当前需求重新生成')
    payload = {'project_name': project.get('name'), 'requirements': requirements, 'feedback': feedback}
    if feedback:
        payload['current_design'] = _design(previous)
    before = len(getattr(llm, 'calls', []))
    prompt = generation_prompt()
    system = prompt + '\n仅返回设计 JSON，不附加说明。'
    purpose = 'interface-prototype.revise' if feedback else 'interface-prototype.generate'
    design = _model_result(llm, system, payload, purpose, 'interface-prototype.schema-repair',
                           lambda raw: _validate_generation(project, raw))
    return _generate_artifact(project, design, llm, before, feedback, prompt)


def edit(project, llm):
    edit_session = project['prototype_edit']
    current = project['prototype']
    working = edit_session.get('candidate') or current
    messages = edit_session['messages']
    latest = messages[-1]['content']
    payload = {'project_name': project.get('name'), 'requirements': project['requirements'],
               'current_design': _design(current), 'working_design': _design(working),
               'conversation': [{'role': item['role'], 'content': item['content']} for item in messages[-12:]]}
    prompt = generation_prompt()
    system = (prompt + '\n在原型编辑对话中，返回 {"reply":"面向用户的说明和改动摘要", "design":null 或上述完整设计}。'
              '用户仅问问题或改动不明确时，先回答或追问，design 为 null。明确请求修改时基于 working_design 输出完整候选，'
              '保持其余页面及先前已采纳的候选修改。说明需要修改业务规则才能实现的请求，避免擅自改变 PRD 的语义。'
              '候选需等待界面确认后才会成为当前版本。')
    before = len(getattr(llm, 'calls', []))
    reply, design = _model_result(llm, system, payload, 'interface-prototype.edit.chat',
                                 'interface-prototype.edit.repair', _validate_edit)
    result = {'reply': reply}
    if design and (design != _design(working) or working.get('template_version') != WORKBENCH_TEMPLATE):
        result['candidate'] = _generate_artifact(project, design, llm, before, latest, prompt)
    return result


def _validate_edit(raw):
    _keys(raw, ('reply', 'design'), '原型修订回复')
    reply = _text(raw.get('reply'), '原型修订说明', 12000)
    design = validate_design(raw['design']) if raw.get('design') is not None else None
    return reply, design
