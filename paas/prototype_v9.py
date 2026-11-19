"""Standalone prototype renderer adapted from the vendored MIT V9 UI template.

The original repository is sharptoolbox/ontology-driven-dev, pinned in
upstream/sources.json. Its two-level navigation, workspace tabs, dialog and
drawer patterns are used with the UI-UE guide's form, query and approval layouts.
Only normalized design data enters this renderer. The script is fixed and all
records originate in the current preview session.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
from pathlib import Path


_LICENSE = (Path(__file__).parent / 'upstream/ontology-driven-dev/LICENSE').read_text(encoding='utf-8').strip()
_FORM_LAYOUTS = {'form', 'single_form', 'master_detail_form'}
_LIST_LAYOUTS = {'list', 'list_maintenance', 'query_list'}

# Design tokens and field alignment adapted from V9 UI-UE sections 6, 7 and 13.
_STYLE = """
:root{--primary:#2266e3;--primary-hover:#1a56c0;--ink:#1e293b;--muted:#5b6e8c;--border:#e2edf2;--divider:#e4e7ec;--surface:#fff;--background:#f0f2f6;--nav:#111827;--label:96px;font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink);background:var(--background);font-size:14px}
*{box-sizing:border-box}body{margin:0;min-height:100vh}button,input,select,textarea{font:inherit;color:inherit}button{cursor:pointer}button:disabled{cursor:not-allowed;opacity:.48}[hidden]{display:none!important}:focus-visible{outline:2px solid var(--primary);outline-offset:2px}h1,h2,h3,p{margin:0}h1{font-size:22px;line-height:1.28}h2,h3{font-size:15px}small{font-size:12px}
.shell{display:grid;grid-template-columns:216px minmax(0,1fr);min-height:100vh}.sidebar{background:var(--nav);color:#dce5f1;position:sticky;top:0;align-self:start;height:100vh;overflow:auto;padding:0 12px 16px}.brand{display:flex;align-items:center;gap:10px;min-height:58px;padding:0 10px;color:white;font-weight:650}.mark{width:25px;height:25px;border-radius:7px;background:var(--primary);display:grid;place-items:center}.mark::after{content:"";width:10px;height:10px;border:1.5px solid #fff;border-radius:3px;transform:rotate(45deg)}.navigation{padding-top:12px;border-top:1px solid #263245}.nav-group{margin:0 0 9px}.nav-group summary{cursor:pointer;min-height:32px;padding:7px 10px;color:#b4c2d5;font-weight:600;font-size:13px;overflow-wrap:anywhere}.nav-group summary::marker{color:#91a3be}.nav-link{display:block;width:100%;border:0;border-left:2px solid transparent;background:transparent;color:#dce5f1;text-align:left;border-radius:5px;padding:8px 9px 8px 15px;overflow-wrap:anywhere}.nav-link:hover,.nav-link[aria-current=page]{background:#23334c;color:#fff}.nav-link[aria-current=page]{border-left-color:#79aaff}
.workspace{min-width:0}.workspace-bar{display:flex;justify-content:space-between;gap:12px;align-items:center;min-height:58px;padding:0 24px;background:var(--surface);border-bottom:1px solid var(--divider)}.breadcrumb{font-size:13px;color:var(--muted);overflow-wrap:anywhere}.preview-label{color:var(--muted);font-size:12px;white-space:nowrap}.tabs{display:flex;align-items:flex-end;gap:6px;overflow:auto;padding:7px 24px 0;background:#f8fafd;border-bottom:1px solid var(--divider);min-height:40px}.tab{display:flex;align-items:center;gap:2px;min-width:max-content;border:1px solid transparent;border-radius:6px 6px 0 0}.tab.active{border-color:var(--divider);border-bottom-color:#fff;background:white}.tab button{border:0;background:transparent;padding:6px 8px;color:var(--muted)}.tab.active [data-target]{color:var(--primary);font-weight:650}.tab .close-tab{padding:2px 7px;margin-right:3px;border-radius:4px}.close-tab:hover{background:#e8eff9}
.page{padding:23px 24px 19px;min-width:0}.page-heading{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:18px}.page-heading p{margin-top:5px;color:var(--muted);font-size:13px;overflow-wrap:anywhere}.actions{display:flex;flex-wrap:wrap;justify-content:flex-end;align-items:center;gap:7px}.actions:empty{display:none}.btn{min-height:32px;border:1px solid #cbd5e1;border-radius:40px;background:#fff;padding:5px 14px;font-weight:540;white-space:normal}.btn:hover:not(:disabled){background:#f5f8fc}.btn.primary{background:var(--primary);border-color:var(--primary);color:white}.btn.primary:hover:not(:disabled){background:var(--primary-hover)}.btn.danger{border-color:#e9b6ba;color:#a2353e}.btn.mini{border-radius:6px;font-size:12px;padding:4px 8px;min-height:27px}.card{background:var(--surface);border:1px solid var(--divider);margin-bottom:13px;border-radius:8px;min-width:0}.card-bar{display:flex;justify-content:space-between;gap:14px;align-items:center;border-bottom:1px solid var(--divider);padding:9px 15px;min-height:43px}.card-body{padding:15px}.card-toolbar{margin-top:13px}.page-status{min-height:20px;font-size:13px;color:#234f92;margin:7px 0}.page-status.error{color:#a2353e}.preview-summary{margin:0 24px 20px;color:var(--muted);overflow-wrap:anywhere;font-size:12px}
.fields{display:grid;grid-template-columns:repeat(var(--field-columns,2),minmax(0,1fr));column-gap:14px;row-gap:12px}.fields.three{--field-columns:3}.field{display:grid;grid-template-columns:minmax(0,var(--label)) minmax(0,1fr);align-items:start;gap:8px}.field.long{grid-column:1/-1}.field-label{text-align:right;padding:6px 0;color:var(--muted);overflow-wrap:anywhere}.required{color:#ae313d;margin-right:3px}.field-control{min-width:0}.help{display:block;margin-top:4px;color:var(--muted);overflow-wrap:anywhere}input:not([type=checkbox]),select,textarea{display:block;width:100%;min-width:0;border:1px solid #cfdce7;background:#fff;border-radius:8px;padding:6px 9px;line-height:1.35}input[type=checkbox]{margin:7px 0;accent-color:var(--primary);height:17px;width:17px}textarea{resize:vertical;min-height:64px}input[readonly]{background:#f8fafc}input:focus,select:focus,textarea:focus{border-color:var(--primary);outline:0;box-shadow:0 0 0 3px #2563eb21}.reference{display:flex;gap:5px}.reference input{flex:1}.reference .btn{flex:none;border-radius:7px}.phase{border-radius:12px;padding:2px 8px;color:#22568e;background:#edf5ff;font-size:12px}
.table-wrap{overflow:auto}table{width:100%;min-width:360px;border-collapse:collapse;text-align:left}th,td{padding:8px 12px;border-bottom:1px solid #eef1f5;vertical-align:middle}thead th{color:var(--muted);background:#f8fafc;font-size:12px;font-weight:650;white-space:nowrap}tbody tr:last-child td{border-bottom:0}tbody tr:hover{background:#fafcff}.empty{height:86px;text-align:center;color:var(--muted)}.table-actions{display:flex;justify-content:flex-end;gap:6px;white-space:nowrap}.pager{display:flex;gap:7px;align-items:center;justify-content:flex-end;flex-wrap:wrap;padding:8px 12px;border-top:1px solid #eef1f5;color:var(--muted);font-size:12px}.page-index{background:var(--primary);color:#fff;padding:3px 8px;border-radius:5px}.grid-table td{min-width:120px}.grid-table td:first-child,.grid-table td:last-child{min-width:0}.grid-table input:not([type=checkbox]),.grid-table select,.grid-table textarea{background:transparent;border-color:transparent}.grid-table input:focus,.grid-table select:focus,.grid-table textarea:focus,.grid-table input:hover,.grid-table select:hover,.grid-table textarea:hover{border-color:#cfdce7}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px}.metric{background:white;border:1px solid var(--divider);border-radius:7px;padding:14px;color:var(--muted)}.metric strong{display:block;font-size:24px;color:var(--ink);margin-top:5px}.record-details{display:grid;grid-template-columns:1fr 2fr;gap:8px 15px;margin:0}.record-details dt{color:var(--muted);overflow-wrap:anywhere}.record-details dd{margin:0;overflow-wrap:anywhere}
dialog{border:1px solid var(--divider);border-radius:10px;background:var(--surface);color:var(--ink);padding:0;max-height:calc(100vh - 32px);max-width:calc(100vw - 28px);width:min(620px,96vw);box-shadow:0 16px 48px #11182724;overflow:auto}dialog::backdrop{background:#11182773}.dialog-title{display:flex;justify-content:space-between;align-items:center;gap:12px;border-bottom:1px solid var(--divider);padding:11px 16px}.dialog-body{padding:16px}.dialog-footer{border-top:1px solid var(--divider);padding:10px 16px}.drawer{position:fixed;margin:0 0 0 auto;inset:0 0 0 auto;max-height:none;height:100vh;width:min(465px,96vw);border-radius:0}.options{display:grid;gap:6px}.options button{text-align:left;overflow-wrap:anywhere}.compact-dialog{max-width:min(400px,calc(100vw - 28px))}
@media(max-width:1100px){.fields.three{--field-columns:2}}@media(max-width:760px){.shell{grid-template-columns:155px minmax(0,1fr)}.sidebar{padding-inline:7px}.page,.workspace-bar{padding-inline:14px}.tabs{padding-inline:14px}.fields,.fields.three{--field-columns:1}.page-heading{flex-wrap:wrap}.page-heading .actions{justify-content:flex-start}}@media(max-width:500px){.shell{display:block}.sidebar{position:static;height:auto;padding:4px 8px}.brand{min-height:38px}.navigation{display:flex;gap:7px;overflow:auto;padding:4px 0}.nav-group{min-width:125px;margin:0;padding-bottom:4px}.nav-group summary{padding:3px 5px}.nav-link{padding:4px 6px}.workspace-bar{min-height:42px}.page{padding-block:15px}.preview-label{display:none}}
""".strip()

_SCRIPT = """
(() => {
  const select = (root, selector) => root.querySelector(selector);
  const all = (root, selector) => Array.from(root.querySelectorAll(selector));
  const pages = new Map(all(document, '[data-page]').map(page => [page.dataset.page, page]));
  const tabs = select(document, '.tabs');
  const breadcrumb = select(document, '.breadcrumb');
  const drawer = select(document, '[data-drawer]');
  const references = select(document, '[data-reference-dialog]');
  const deletion = select(document, '[data-delete-dialog]');
  const states = new Map();
  const records = new Map();
  const pageSize = 10;
  let sequence = 0;
  let active;
  let selectedReference;
  let pendingDelete;

  function state(page) {
    if (!states.has(page)) states.set(page, {page: 1, filters: [], editing: null, current: null});
    return states.get(page);
  }

  function rows(page) {
    const source = page.dataset.recordSource || page.dataset.page;
    if (!records.has(source)) records.set(source, []);
    return records.get(source);
  }

  function make(tag, text, className) {
    const element = document.createElement(tag);
    if (text != null) element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  function button(label, event, className = 'btn mini') {
    const item = make('button', label, className);
    item.type = 'button';
    item.dataset.event = event;
    return item;
  }

  function notice(page, text, error = false) {
    const message = select(page, '[data-result]');
    message.textContent = text;
    message.classList.toggle('error', error);
  }

  function open(id) {
    const page = pages.get(id);
    if (!page) return;
    active = id;
    pages.forEach((other, key) => { other.hidden = key !== id; });
    all(document, '.navigation [data-target]').forEach(link => {
      if (link.dataset.target === id) {
        link.setAttribute('aria-current', 'page');
        link.closest('details').open = true;
      } else link.removeAttribute('aria-current');
    });
    const title = select(page, 'h1').textContent;
    breadcrumb.textContent = page.dataset.group + ' / ' + title;
    let tab = all(tabs, '.tab').find(item => item.dataset.tab === id);
    if (!tab) {
      tab = make('div', null, 'tab');
      tab.dataset.tab = id;
      const heading = button(title, 'navigate', '');
      heading.dataset.target = id;
      const close = button('×', 'close-tab', 'close-tab');
      close.setAttribute('aria-label', '关闭 ' + title);
      tab.append(heading, close);
      tabs.append(tab);
    }
    all(tabs, '.tab').forEach(item => {
      const chosen = item === tab;
      item.classList.toggle('active', chosen);
      const heading = select(item, '[data-target]');
      if (chosen) heading.setAttribute('aria-current', 'page'); else heading.removeAttribute('aria-current');
      select(item, '.close-tab').hidden = tabs.childElementCount < 2;
    });
    refreshTable(page);
    refreshDetail(page);
  }

  function controls(container, detail = false) {
    return container ? all(container, '[data-field]').filter(field => detail || !field.closest('[data-detail-row]')) : [];
  }

  function readFields(container, detail = false) {
    return Object.fromEntries(controls(container, detail).map(field => [field.dataset.field,
      field.type === 'checkbox' ? field.checked : field.value.trim()]));
  }

  function writeFields(container, values) {
    controls(container).forEach(field => {
      const value = Object.hasOwn(values, field.dataset.field) ? values[field.dataset.field] : '';
      if (field.type === 'checkbox') field.checked = value === true;
      else field.value = value;
    });
  }

  function valid(form, page) {
    const validation = select(form, '[data-validation]');
    if (validation) validation.textContent = '';
    if (!form.reportValidity()) return false;
    const missing = all(form, '[data-reference-required]').find(field => !field.value);
    if (!missing) return true;
    const message = '请选择必填关联项：' + missing.getAttribute('aria-label');
    if (validation) validation.textContent = message;
    else notice(page, message, true);
    const chooser = select(missing.closest('.reference'), 'button');
    if (!chooser.disabled) chooser.focus();
    return false;
  }

  function detailedRows(page) {
    return all(page, '[data-detail-row]').map(row => readFields(row, true));
  }

  function refreshLines(page) {
    const body = select(page, '[data-lines]');
    if (!body) return;
    const lines = all(body, '[data-detail-row]');
    select(body, '[data-line-empty]').hidden = lines.length > 0;
    lines.forEach((line, index) => { select(line, '[data-line-number]').textContent = index + 1; });
    select(page, '[data-line-count]').textContent = '共 ' + lines.length + ' 项';
  }

  function refreshForm(page) {
    const form = select(page, '[data-main]');
    if (!form) return;
    form.reset();
    all(page, '[data-detail-row]').forEach(row => row.remove());
    state(page).current = null;
    const phase = select(page, '[data-phase]');
    if (phase) phase.hidden = true;
    refreshLines(page);
  }

  function addLine(page) {
    const template = select(page, '[data-line-template]');
    if (!template) return;
    const body = select(page, '[data-lines]');
    if (all(body, '[data-detail-row]').length >= 200) {
      notice(page, '当前预览最多添加 200 项明细。', true);
      return;
    }
    body.append(template.content.cloneNode(true));
    refreshLines(page);
    select(body.lastElementChild, 'input:not([readonly]), select, textarea, button')?.focus();
  }

  function storeRow(page, form, kind, previous) {
    if (kind !== 'draft' && !valid(form, page)) return null;
    const row = previous || {key: ++sequence};
    row.values = readFields(form);
    row.lines = detailedRows(page);
    const approval = Boolean(select(page, '[data-event=draft]') && select(page, '[data-event=submit]'));
    row.phase = kind === 'draft' ? '草稿' : kind === 'submit' ? (approval ? '待审批' : '已提交') : '已保存';
    if (!previous) rows(page).push(row);
    const phase = select(page, '[data-phase]');
    if (phase) { phase.textContent = row.phase; phase.hidden = false; }
    notice(page, (kind === 'draft' ? '' : '已检查填写内容。') + '预览记录：' + row.phase + '。');
    refreshTable(page);
    return row;
  }

  function filterRows(page, reset = false) {
    const form = select(page, '[data-filter]');
    if (!form) return;
    if (reset) form.reset();
    state(page).filters = controls(form).map(field => ({key: field.dataset.field,
      value: field.type === 'checkbox' ? (field.checked ? true : '') : field.value.trim(),
      contains: ['text', 'textarea'].includes(field.type) && !field.readOnly})).filter(item => item.value !== '');
    state(page).page = 1;
    refreshTable(page);
    notice(page, reset ? '已清除查询条件。' : '已查询当前预览记录。');
  }

  function matches(row, filters) {
    return filters.every(({key, value, contains}) => {
      const actual = Object.hasOwn(row.values, key) ? row.values[key] : '';
      return contains ? String(actual).toLocaleLowerCase().includes(value.toLocaleLowerCase()) : actual === value;
    });
  }

  function display(value) {
    return typeof value === 'boolean' ? (value ? '是' : '否') : value || '—';
  }

  function refreshTable(page) {
    const body = select(page, '[data-records]');
    if (!body) return;
    const columns = all(page, '[data-column]');
    const form = select(page, '[data-editor]');
    const data = rows(page).filter(row => matches(row, state(page).filters));
    const numberOfPages = Math.max(1, Math.ceil(data.length / pageSize));
    state(page).page = Math.max(1, Math.min(state(page).page, numberOfPages));
    const current = state(page).page;
    body.replaceChildren();
    if (!data.length) {
      const cell = make('td', page.dataset.layout === 'list' ? '应用构建后显示实际业务记录' : '暂无预览记录', 'empty');
      cell.colSpan = columns.length + 1;
      const empty = make('tr');
      empty.append(cell);
      body.append(empty);
    }
    for (const row of data.slice((current - 1) * pageSize, current * pageSize)) {
      const line = make('tr');
      for (const column of columns) {
        const key = column.dataset.column;
        line.append(make('td', display(Object.hasOwn(row.values, key) ? row.values[key] : '')));
      }
      const cell = make('td');
      const actions = make('div', null, 'table-actions');
      actions.append(button('查看', 'record-view'));
      if (form && page.dataset.editLabel) actions.append(button(page.dataset.editLabel, 'record-edit'));
      if (form && page.dataset.deleteLabel) actions.append(button(page.dataset.deleteLabel, 'record-delete', 'btn mini danger'));
      all(actions, 'button').forEach(action => { action.dataset.key = row.key; });
      cell.append(actions);
      line.append(cell);
      body.append(line);
    }
    select(page, '[data-pager-count]').textContent = '共 ' + data.length + ' 条 / ' + numberOfPages + ' 页';
    select(page, '[data-page-index]').textContent = current;
    select(page, '[data-event=prev]').disabled = current <= 1;
    select(page, '[data-event=next]').disabled = current >= numberOfPages;
  }

  function viewRecord(page, record) {
    select(drawer, 'h2').textContent = select(page, 'h1').textContent + '详情';
    const detail = select(drawer, 'dl');
    detail.replaceChildren();
    const columns = all(page, '[data-column]');
    for (const column of columns) {
      const key = column.dataset.column;
      detail.append(make('dt', column.textContent), make('dd', display(record.values[key])));
    }
    if (record.lines.length) detail.append(make('dt', '明细'), make('dd', record.lines.length + ' 项'));
    const template = select(drawer, '[data-drawer-lines]');
    template.replaceChildren();
    const lineFields = Object.keys(record.lines[0] || {});
    if (lineFields.length) {
      const list = make('ol');
      for (const line of record.lines) list.append(make('li', lineFields.map(key => display(line[key])).join(' · ')));
      template.append(list);
    }
    drawer.showModal();
  }

  function refreshDetail(page) {
    const detail = select(page, '[data-detail-panel]');
    if (!detail) return;
    const record = rows(page).at(-1);
    all(detail, '[data-detail-field]').forEach(field => {
      const key = field.dataset.detailField;
      field.textContent = record ? display(record.values[key]) : '—';
    });
  }

  function editRecord(page, record) {
    const dialog = select(page, '[data-editor]');
    if (!dialog) return;
    const form = select(dialog, 'form');
    form.reset();
    select(form, '[data-validation]').textContent = '';
    if (record) writeFields(form, record.values);
    state(page).editing = record || null;
    select(dialog, 'h2').textContent = record ? '编辑记录' : '新增记录';
    dialog.showModal();
  }

  function selectReference(control) {
    const options = JSON.parse(control.dataset.options);
    const optionList = select(references, '.options');
    optionList.replaceChildren();
    const field = select(control.closest('.reference'), 'input');
    select(references, 'h2').textContent = '选择' + field.getAttribute('aria-label');
    selectedReference = field;
    for (const option of options) {
      const item = button(option, 'reference-pick', 'btn');
      item.dataset.value = option;
      optionList.append(item);
    }
    if (field.value) optionList.append(button('清除选择', 'reference-clear', 'btn'));
    references.showModal();
  }

  function execute(page, control) {
    const event = control.dataset.event;
    const current = state(page);
    const form = select(page, '[data-main]');
    if (event === 'create' && select(page, '[data-editor]')) editRecord(page, null);
    else if (event === 'query') filterRows(page);
    else if (event === 'reset' && select(page, '[data-filter]')) filterRows(page, true);
    else if (['reset', 'cancel', 'create'].includes(event)) { refreshForm(page); notice(page, '已清除本页预览输入。'); }
    else if (event === 'detail-add') addLine(page);
    else if (event === 'detail-remove') { control.closest('[data-detail-row]').remove(); refreshLines(page); }
    else if (event === 'prev' || event === 'next') { current.page += event === 'prev' ? -1 : 1; refreshTable(page); }
    else if (event === 'editor-save') {
      const editor = control.closest('dialog');
      if (storeRow(page, select(editor, 'form'), 'save', current.editing)) editor.close();
    }
    else if (['draft', 'save', 'submit'].includes(event) && form) {
      const row = storeRow(page, form, event, current.current);
      if (row) current.current = row;
    }
    else if (event === 'choose-reference') selectReference(control);
    else if (event.startsWith('record-')) {
      const row = rows(page).find(item => item.key === Number(control.dataset.key));
      if (!row) return;
      if (event === 'record-view') viewRecord(page, row);
      else if (event === 'record-edit') editRecord(page, row);
      else if (event === 'record-delete') { pendingDelete = {page, row}; deletion.showModal(); }
    }
  }

  document.addEventListener('click', input => {
    const control = input.target.closest('button[data-event]');
    if (!control) return;
    const event = control.dataset.event;
    if (event === 'navigate') { open(control.dataset.target); return; }
    if (event === 'close-tab') {
      const tab = control.closest('.tab');
      if (tabs.childElementCount < 2) return;
      const neighbor = tab.previousElementSibling || tab.nextElementSibling;
      const next = active === tab.dataset.tab ? neighbor.dataset.tab : active;
      tab.remove();
      open(next);
      select(tabs, '.tab.active [data-target]')?.focus();
      return;
    }
    if (event === 'close-dialog') { control.closest('dialog').close(); return; }
    if (event === 'reference-pick' || event === 'reference-clear') {
      selectedReference.value = event === 'reference-pick' ? control.dataset.value : '';
      references.close();
      return;
    }
    if (event === 'delete-confirm' && pendingDelete) {
      const {page, row} = pendingDelete;
      const data = rows(page);
      const index = data.indexOf(row);
      if (index >= 0) data.splice(index, 1);
      if (state(page).current === row) refreshForm(page);
      refreshTable(page);
      notice(page, '已删除预览记录。');
      deletion.close();
      return;
    }
    const page = control.closest('[data-page]');
    if (page) execute(page, control);
  });
  document.addEventListener('submit', input => {
    input.preventDefault();
    const page = input.target.closest('[data-page]');
    if (page && input.target.matches('[data-filter]')) filterRows(page);
  });
  deletion.addEventListener('close', () => { pendingDelete = null; });
  references.addEventListener('close', () => { selectedReference = null; });
  if (pages.size) open(pages.keys().next().value);
})();
""".strip()


def content_security_policy():
    """Use the same exact script hash in the standalone meta and server header."""
    script_hash = base64.b64encode(hashlib.sha256(_SCRIPT.encode()).digest()).decode()
    return ("default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-" + script_hash
            + "'; connect-src 'none'; img-src 'none'; font-src 'none'; frame-src 'none';"
              " form-action 'none'; base-uri 'none'")


def _esc(value):
    return html.escape(str(value), quote=True)


def _attribute(name, value):
    return f' {name}="{_esc(value)}"' if value else ''


def _button(label, event, *, classes='btn', target='', description='', extra=''):
    target_attr = _attribute('data-target', target)
    description_attr = _attribute('title', description)
    return (f'<button type="button" class="{classes}" data-event="{event}"{target_attr}'
            f'{description_attr}{extra}>{_esc(label)}</button>')


def _action(action):
    kind = action['kind']
    is_primary = kind in {'create', 'save', 'submit', 'query'}
    return _button(action['label'], kind, classes='btn primary' if is_primary else 'btn',
                   target=action.get('target_page', ''), description=action.get('description', ''),
                   extra=_attribute('data-kind', kind))


def _input(field, *, prefix='', filter_field=False, input_attrs='', chooser_attrs=''):
    field_type = field['type']
    options = field.get('options') or []
    required = field.get('required') and not filter_field
    label = _esc(field['label'])
    name = prefix + '-' + field['id'] if prefix else field['id']
    attrs = f' name="{_esc(name)}" data-field="{_esc(field["id"])}" aria-label="{label}"' + input_attrs
    if field_type == 'reference':
        available = '' if options else ' disabled'
        required_attrs = ' data-reference-required aria-required="true"' if required else ''
        option_attr = _attribute('data-options', json.dumps(options, ensure_ascii=False))
        placeholder = '请选择' if options else '暂无可选关联项'
        return (f'<div class="reference"><input type="text" readonly{attrs}{required_attrs}'
                f' placeholder="{placeholder}">'
                + _button('选择', 'choose-reference', classes='btn mini',
                          extra=option_attr + chooser_attrs + available) + '</div>')
    if required:
        attrs += ' required'
    if field_type == 'textarea':
        return f'<textarea rows="3"{attrs}></textarea>'
    if field_type == 'select':
        entries = ''.join(f'<option value="{_esc(item)}">{_esc(item)}</option>' for item in options)
        placeholder = '全部' if filter_field else '请选择'
        return f'<select{attrs}><option value="">{placeholder}</option>{entries}</select>'
    step = ' step="any"' if field_type == 'number' else ''
    return f'<input type="{_esc(field_type)}"{attrs}{step}>'


def _fields(fields, *, prefix='', compact=False, filter_fields=False):
    controls = []
    for field in fields:
        classes = 'field long' if field['type'] == 'textarea' else 'field'
        marker = '<span class="required" aria-hidden="true">*</span>' if field.get('required') and not filter_fields else ''
        help_text = f'<small class="help">{_esc(field["help"])}</small>' if field.get('help') and not filter_fields else ''
        show = _input(field, prefix=prefix, filter_field=filter_fields)
        controls.append(f'<div class="{classes}"><span class="field-label">{marker}{_esc(field["label"])}</span>'
                        f'<div class="field-control">{show}{help_text}</div></div>')
    classes = 'fields three' if compact else 'fields'
    return f'<div class="{classes}">{"".join(controls)}</div>'


def _card(title, body, *, tools='', padded=True):
    body = f'<div class="card-body">{body}</div>' if padded else body
    return f'<div class="card"><header class="card-bar"><h2>{_esc(title)}</h2>{tools}</header>{body}</div>'


def _grid(page, *, input_renderer=_input):
    fields = page.get('detail_fields') or []
    if not fields:
        return ''
    columns = ''.join(f'<th scope="col">{_esc(field["label"])}</th>' for field in fields)
    cells = ''.join(f'<td>{input_renderer(field)}</td>' for field in fields)
    count = len(fields) + 2
    row = ('<template data-line-template><tr data-detail-row><td data-line-number></td>' + cells
           + '<td>' + _button('删除行', 'detail-remove', classes='btn mini danger') + '</td></tr></template>')
    table = ('<div class="table-wrap"><table class="grid-table"><thead><tr><th scope="col">行</th>' + columns
             + '<th scope="col">操作</th></tr></thead><tbody data-lines>'
               f'<tr data-line-empty><td class="empty" colspan="{count}">暂无明细</td></tr></tbody></table></div>')
    tools = '<div class="actions"><span class="preview-label" data-line-count>共 0 项</span>' + _button('添加行', 'detail-add', classes='btn mini') + '</div>'
    return _card(page.get('detail_title') or '明细', table + row, tools=tools, padded=False)


def _form(page):
    master = page['layout'] == 'master_detail_form'
    phase = '<span class="phase" data-phase hidden></span>'
    fields = _fields(page['fields'], prefix=page['id'], compact=master)
    title = '主表信息' if master else '表单信息'
    return '<form data-main>' + _card(title, fields, tools=phase) + (_grid(page) if master else '') + '</form>'


def _dialog_head(title, title_id):
    return (f'<header class="dialog-title"><h2 id="{_esc(title_id)}">{_esc(title)}</h2>'
            + _button('关闭', 'close-dialog', classes='btn mini') + '</header>')


def _editor(page, *, field_renderer=_fields):
    title_id = 'editor-' + page['id']
    save = next((action['label'] for action in page['actions'] if action['kind'] == 'save'), '保存')
    footer = '<footer class="dialog-footer actions">' + _button('取消', 'close-dialog') + _button(save, 'editor-save', classes='btn primary') + '</footer>'
    return (f'<dialog data-editor aria-labelledby="{_esc(title_id)}">' + _dialog_head('新增记录', title_id)
            + '<form><div class="dialog-body">' + field_renderer(page['fields'])
            + '<p class="page-status error" data-validation role="status"></p></div>' + footer + '</form></dialog>')


def _query(page, maintenance, *, field_renderer=_fields, table_tools='', editor_field_renderer=None):
    actions = {action['kind']: action for action in page['actions']}
    columns = page['columns'] or page['fields']
    controls = field_renderer(page['fields'], prefix=page['id'], compact=True, filter_fields=True)
    buttons = ''.join(_action(actions.get(kind) or {'label': label, 'kind': kind})
                      for kind, label in (('reset', '重置'), ('query', '查询')))
    filters = '<form data-filter>' + controls + '<div class="actions card-toolbar">' + buttons + '</div></form>'
    headers = ''.join(f'<th scope="col" data-column="{_esc(column["id"])}">{_esc(column["label"])}</th>' for column in columns)
    result = ('<div class="table-wrap"><table><thead><tr>' + headers + '<th scope="col">操作</th></tr></thead>'
              '<tbody data-records></tbody></table></div><div class="pager" aria-label="结果分页">'
              '<span data-pager-count></span>' + _button('上一页', 'prev', classes='btn mini')
              + '<span class="page-index" data-page-index aria-current="page">1</span>'
              + _button('下一页', 'next', classes='btn mini') + '</div>')
    return (_card('查询条件', filters) + _card('查询结果', result, padded=False, tools=table_tools)
            + (_editor(page, field_renderer=editor_field_renderer or field_renderer) if maintenance else ''))


def _detail(page):
    fields = page['fields'] or page['columns']
    definitions = ''.join(f'<dt>{_esc(field["label"])}</dt><dd data-detail-field="{_esc(field["id"])}">—</dd>'
                          for field in fields)
    return _card('详情', f'<dl class="record-details" data-detail-panel>{definitions}</dl>')


def _dashboard(page):
    values = ''.join(f'<div class="metric">{_esc(column["label"])}<strong>—</strong></div>' for column in page['columns'])
    return _card('指标', f'<div class="metrics">{values}</div>')


def _section(page):
    layout = page['layout']
    maintenance = layout == 'list_maintenance'
    actions = page['actions']
    if layout in _FORM_LAYOUTS:
        content = _form(page)
        context_actions = {'navigate', 'submit', 'cancel', 'draft', 'save', 'create', 'reset'}
    elif layout in _LIST_LAYOUTS:
        content = _query(page, maintenance)
        context_actions = {'navigate', 'create'} if maintenance else {'navigate'}
    else:
        content = _dashboard(page) if layout == 'dashboard' else _detail(page)
        context_actions = {'navigate'}
    header_actions = ''.join(_action(action) for action in actions if action['kind'] in context_actions)
    opts = _attribute('data-record-source', page.get('record_source'))
    if maintenance:
        for kind in ('edit', 'delete'):
            opts += _attribute(f'data-{kind}-label', next((action['label'] for action in actions if action['kind'] == kind), ''))
    group = page.get('group') or '业务页面'
    return (f'<section class="page" data-page="{_esc(page["id"])}" data-group="{_esc(group)}" '
            f'data-layout="{_esc(layout)}"{opts} hidden>'
            f'<header class="page-heading"><div><h1>{_esc(page["name"])}</h1><p>{_esc(page["purpose"])}</p></div>'
            f'<div class="actions">{header_actions}</div></header>{content}'
            '<p class="page-status" data-result role="status"></p></section>')


def _navigation(pages):
    groups = {}
    for page in pages:
        group = page.get('group') or '业务页面'
        groups.setdefault(group, []).append(_button(page['name'], 'navigate', classes='nav-link', target=page['id']))
    return ''.join(f'<details class="nav-group" open><summary>{_esc(group)}</summary>{"".join(items)}</details>'
                   for group, items in groups.items())


def render(design):
    """Render an already-validated V9 design to one network-free HTML artifact."""
    pages = design['pages']
    csp = _esc(content_security_policy())
    drawer = ('<dialog class="drawer" data-drawer aria-labelledby="detail-title">'
              + _dialog_head('详情', 'detail-title')
              + '<div class="dialog-body"><dl class="record-details"></dl><div data-drawer-lines></div></div></dialog>')
    reference = ('<dialog data-reference-dialog aria-labelledby="reference-title">'
                 + _dialog_head('选择关联项', 'reference-title')
                 + '<div class="dialog-body options"></div></dialog>')
    delete = ('<dialog class="compact-dialog" data-delete-dialog aria-labelledby="delete-title">'
              + _dialog_head('删除记录', 'delete-title')
              + '<div class="dialog-body">删除当前预览记录？</div><footer class="dialog-footer actions">'
              + _button('取消', 'close-dialog') + _button('确认删除', 'delete-confirm', classes='btn danger') + '</footer></dialog>')
    attribution = ('V9: sharptoolbox/ontology-driven-dev; reference-example and UI-UE design guidance.\n\n' + _LICENSE)
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
            f'<title>界面原型</title><!--\n{attribution}\n--><style>{_STYLE}</style></head><body>'
            '<div class="shell"><aside class="sidebar"><div class="brand"><span class="mark" aria-hidden="true"></span>'
            f'界面原型</div><nav class="navigation" aria-label="业务菜单">{_navigation(pages)}</nav></aside>'
            '<div class="workspace"><header class="workspace-bar"><span class="breadcrumb"></span>'
            '<span class="preview-label">当前预览数据仅在此窗口保留</span></header>'
            '<div class="tabs" role="navigation" aria-label="已打开页面"></div><main>' + ''.join(_section(page) for page in pages)
            + f'<p class="preview-summary">{_esc(design["summary"])}</p></main></div></div>'
            + drawer + reference + delete + f'<script>{_SCRIPT}</script></body></html>')
