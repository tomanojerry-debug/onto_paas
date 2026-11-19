// Render the compiler's role-filtered MU plan. All records and mutations still use
// the authenticated runtime APIs, including business rules and row-level checks.
let modelViewRevision = 0;
let activeModelScreen = null;
let modelRows = [];
let modelDialog = null;

const modelScreen = id => meta.ui_plan.screens.find(screen => screen.id === id);
const modelBehavior = id => meta.behaviors.find(behavior => behavior.id === id);
const modelMenus = () => meta.ui_plan.menus.flatMap(group => group.items);
const modelFirst = () => modelMenus()[0]?.screen_id;

function modelRoute() {
  const value = /^#screen\/(.+)$/.exec(location.hash)?.[1];
  if (!value) return modelFirst();
  try {
    const id = decodeURIComponent(value);
    return modelMenus().some(item => item.screen_id === id) ? id : modelFirst();
  } catch {
    return modelFirst();
  }
}

async function renderModelApp() {
  $('#nav').innerHTML = meta.ui_plan.menus.map(group =>
    '<section class="mu-menu-group" data-menu-group="' + esc(group.id) + '"><h3>' + esc(group.name) + '</h3>' +
    group.items.map(item => '<button type="button" data-menu-id="' + esc(item.id) + '" data-screen-route="' +
      esc(item.screen_id) + '">' + esc(item.name) + '</button>').join('') + '</section>'
  ).join('') + (meta.ui_plan.show_tasks ? '<button type="button" data-mu-tasks>审批待办</button>' : '');
  mountAssistant();
  const first = modelRoute();
  if (first) {
    history.replaceState(null, '', '#screen/' + encodeURIComponent(first));
    await openModelScreen(first, true);
  }
  else $('#workspace').innerHTML = '<p class="empty">当前角色没有可用界面。</p>';
}

async function openModelScreen(id, fromHash = false) {
  const screen = modelScreen(id);
  if (!screen || screen.presentation === 'dialog') return;
  const route = '#screen/' + encodeURIComponent(id);
  if (!fromHash && location.hash !== route) history.pushState(null, '', route);
  const token = ++modelViewRevision;
  activeModelScreen = screen;
  modelRows = [];
  selected = screen.entity_id;
  currentReport = screen.report_id;
  closeModelDialog();
  for (const link of document.querySelectorAll('[data-screen-route]')) {
    const selected = link.dataset.screenRoute === id;
    link.classList.toggle('active', selected);
    if (selected) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  $('#notice').textContent = '';
  $('#workspace').dataset.screenId = screen.id;
  $('#workspace').dataset.prototypePageId = screen.prototype_page_id;
  $('#workspace').innerHTML = '<p class="quiet" role="status">载入' + esc(screen.name) + '…</p>';
  try {
    if (screen.report_id) {
      await showModelReport(screen, token);
    } else {
      const data = screen.can_read
        ? await api('entities/' + encodeURIComponent(screen.entity_id) + '/records') : {records: []};
      if (token !== modelViewRevision) return;
      modelRows = data.records;
      if (screen.layout === 'SINGLE_FORM') {
        const form = await modelRecordForm(screen, {}, screen.actions, false);
        if (token === modelViewRevision) $('#workspace').innerHTML = form;
      } else if (screen.layout === 'DETAIL') {
        showModelDetails(screen);
      } else {
        showModelList(screen);
      }
    }
  } catch (error) {
    if (token === modelViewRevision) {
      $('#workspace').innerHTML = '<section class="panel"><h2>' + esc(screen.name) +
        '</h2><p class="error">' + esc(error.message) + '</p></section>';
    }
  }
}

function modelActionButton(action, row = null, submitForm = '') {
  const behavior = modelBehavior(action.behavior_id);
  const operation = behavior?.runtime.operation;
  const mode = operation === 'CREATE' ? ' data-create="' + esc(behavior.id) + '"'
    : operation && operation !== 'QUERY' ? ' data-act="' + esc(behavior.id) + '"' : '';
  const form = submitForm ? ' type="submit" form="' + submitForm + '"' : ' type="button"';
  const record = row ? ' data-record="' + esc(row.id) + '"' : '';
  const title = action.description ? ' title="' + esc(action.description) + '"' : '';
  return '<button' + form + mode + record + title + ' class="' +
    (['query', 'create', 'draft', 'submit', 'save'].includes(action.kind) ? 'primary' : '') +
    '" data-mu-action="' + esc(action.id) + '" data-action-id="' + esc(action.action_id) + '">' +
    esc(action.label) + '</button>';
}

function actionMatchesRecord(action, row) {
  const behavior = modelBehavior(action.behavior_id);
  if (!behavior || !['UPDATE', 'TRANSITION'].includes(behavior.runtime.operation)) return false;
  const rule = behavior.runtime;
  return (action.scope !== 'OWN' || row.created_by === meta.actor.id) &&
    (!rule.allowedStates?.length || rule.allowedStates.includes(row[rule.stateField || 'status']));
}

function showModelList(screen) {
  const buttons = screen.actions.filter(action => ['navigate', 'open_form'].includes(action.kind) ||
    modelBehavior(action.behavior_id)?.runtime.operation === 'CREATE');
  const columns = screen.columns.length ? screen.columns : screen.fields;
  const rows = modelRows.map(row => {
    const actions = screen.actions.filter(action => actionMatchesRecord(action, row));
    const values = columns.map(column => '<td>' + esc(row[column.name]) + '</td>');
    return '<tr>' + values.join('') + '<td><div class="actions">' +
      actions.map(action => modelActionButton(action, row)).join('') + '</div></td></tr>';
  });
  const list = screen.can_read && columns.length
    ? '<div class="scroll"><table><thead><tr>' + columns.map(column => '<th>' + esc(column.label) +
      '</th>').join('') + '<th>操作</th></tr></thead><tbody>' + rows.join('') + '</tbody></table></div>' +
      (rows.length ? '' : '<p class="empty">暂无可见记录。</p>') : '';
  $('#workspace').innerHTML = '<section class="panel"><div class="toolbar"><h2>' + esc(screen.name) +
    '</h2><div class="actions">' + buttons.map(action => modelActionButton(action)).join('') +
    '</div></div>' + list + '</section>';
}

function showModelDetails(screen) {
  const columns = screen.columns.length ? screen.columns : screen.fields;
  const details = modelRows.map(row => {
    const pairs = columns.map(column => '<dt>' + esc(column.label) + '</dt><dd>' +
      esc(row[column.name]) + '</dd>').join('');
    const actions = screen.actions.filter(action => actionMatchesRecord(action, row));
    return '<details class="mu-detail"><summary>' + esc(row[columns[0].name] || row.code) +
      '</summary><dl>' + pairs + '</dl><div class="actions">' +
      actions.map(action => modelActionButton(action, row)).join('') + '</div></details>';
  });
  const navigation = screen.actions.filter(action => action.kind === 'navigate');
  $('#workspace').innerHTML = '<section class="panel"><div class="toolbar"><h2>' + esc(screen.name) +
    '</h2><div class="actions">' + navigation.map(action => modelActionButton(action)).join('') + '</div></div>' +
    (details.join('') || '<p class="empty">暂无可见记录。</p>') + '</section>';
}

const modelType = {date: 'date', number: 'number', checkbox: 'checkbox'};

function modelInput(field, value) {
  const common = ' name="' + esc(field.name) + '" data-element-id="' + esc(field.element_id) + '"' +
    (field.required ? ' required' : '');
  if (field.control === 'select') {
    return '<select' + common + '><option value="">请选择</option>' + field.options.map(option => {
      const entry = typeof option === 'object' ? option : {value: option, label: option};
      return '<option value="' + esc(entry.value) + '"' +
        (String(entry.value) === String(value) ? ' selected' : '') + '>' + esc(entry.label) + '</option>';
    }).join('') + '</select>';
  }
  if (field.control === 'textarea') return '<textarea' + common + '>' + esc(value) + '</textarea>';
  if (field.control === 'reference') {
    return '<span class="mu-reference"><input' + common + ' type="hidden" value="' + esc(value) +
      '"><output data-reference-label="' + esc(field.id) + '">' + esc(value || '请选择') +
      '</output><button type="button" data-mu-reference="' + esc(field.id) + '">选择</button></span>';
  }
  return '<input' + common + ' type="' + (modelType[field.control] || 'text') + '"' +
    (field.control === 'number' ? ' step="any"' : '') +
    (field.control === 'checkbox' ? (value && value !== 'false' ? ' checked' : '') : ' value="' + esc(value) + '"') + '>';
}

function modelFields(fields, values = {}, editable = null) {
  return fields.map(field => {
    const value = values[field.name] ?? field.default ?? '';
    const readonly = editable && !editable.has(field.name);
    const control = readonly
      ? '<output class="mu-readonly" data-readonly-field="' + esc(field.name) + '" data-element-id="' +
        esc(field.element_id) + '">' + esc(value === '' ? '—' : value) + '</output>'
      : modelInput(field, value);
    return '<label' + (field.wide ? ' class="mu-wide"' : '') + '><span>' +
      esc(field.label) + (field.required && !readonly ? ' *' : '') + '</span>' + control +
      (field.help.length ? '<small class="quiet field-help">' + esc(field.help.join('；')) + '</small>' : '') +
      '</label>';
  }).join('');
}

function modelRecordForm(screen, record, actions, dialog = true, suggested = {}) {
  const hasRecord = Boolean(record.id);
  const applicable = actions.filter(action => {
    const behavior = modelBehavior(action.behavior_id);
    return behavior ? (hasRecord ? actionMatchesRecord(action, record) : behavior.runtime.operation === 'CREATE') :
      ['cancel', 'navigate', 'reset'].includes(action.kind);
  });
  const editable = new Set(applicable.flatMap(action => modelBehavior(action.behavior_id)?.runtime.editableFields || []));
  const showRecord = hasRecord && !dialog && screen.can_read;
  const fields = screen.fields.filter(field => showRecord || editable.has(field.name));
  const id = dialog ? 'edit' : 'mu-inline-edit';
  const buttons = applicable.map(action =>
    modelActionButton(action, null, action.behavior_id ? id : '')).join('');
  if (!buttons && !fields.length) {
    return '<section class="panel"><h2>' + esc(screen.name) + '</h2><p class="empty">当前角色没有可用操作。</p></section>';
  }
  return '<form class="panel mu-form" id="' + id + '" data-mu-form data-screen-id="' + esc(screen.id) +
    '" data-record-id="' + esc(record.id || '') + '"><h2>' + esc(screen.name) +
    '</h2><div class="fields">' + modelFields(fields, {...record, ...suggested}, editable) + '</div>' +
    '<div class="error" data-mu-form-error role="alert"></div><footer class="actions">' +
    (dialog ? '<button type="button" data-mu-close>关闭</button>' : '') + buttons + '</footer></form>';
}

function closeModelDialog() {
  $('#modal').textContent = '';
  modelDialog = null;
}

function showModelDialog(screen, record = {}, actions = screen.actions, suggested = {}) {
  modelDialog = {screen, record, actions, suggested};
  $('#modal').innerHTML = '<div class="modal" role="dialog" aria-modal="true" aria-label="' +
    esc(screen.name) + '">' + modelRecordForm(screen, record, actions, true, suggested) + '</div>';
  $('#modal').querySelector('input:not([type=hidden]), textarea, select, button')?.focus();
}

async function showModelReport(screen, token) {
  const filters = '<form id="filters" data-mu-report data-screen-id="' + esc(screen.id) +
    '"><div class="fields mu-query-fields">' + modelFields(screen.fields) + '</div><div class="actions">' +
    screen.actions.map(action => modelActionButton(action, null, action.kind === 'query' ? 'filters' : '')).join('') +
    '</div></form>';
  if (token !== modelViewRevision) return;
  $('#workspace').innerHTML = '<section class="panel"><h2>' + esc(screen.name) + '</h2>' + filters +
    '<div id="results" aria-live="polite"></div></section>';
  const form = $('#filters');
  if (form.checkValidity() && screen.fields.every(field => field.control !== 'reference' || !field.required)) {
    await queryModelReport(form, screen);
  }
}

function modelFormValues(form, fields) {
  const payload = {};
  for (const field of fields) {
    const input = form.elements.namedItem(field.name);
    if (!input) continue;
    const value = input.value;
    if (field.required && field.control === 'reference' && !value) throw Error('请选择' + field.label);
    payload[field.name] = field.control === 'checkbox' ? input.checked
      : field.control === 'number' && value !== '' ? Number(value) : value;
  }
  return payload;
}

async function queryModelReport(form, screen, page = 1) {
  const results = $('#results');
  try {
    const filters = Object.fromEntries(Object.entries(modelFormValues(form, screen.fields)).filter(([, value]) => value !== ''));
    const data = await api('reports/' + encodeURIComponent(screen.report_id), {filters, page});
    if (!results?.isConnected) return;
    results.innerHTML = '<div class="scroll"><table><thead><tr>' +
      screen.columns.map(column => '<th>' + esc(column.label) + '</th>').join('') +
      '</tr></thead><tbody>' + data.rows.map(row => '<tr>' +
        screen.columns.map(column => '<td>' + esc(row[column.name]) + '</td>').join('') + '</tr>').join('') +
      '</tbody></table></div><div class="toolbar mu-result-footer"><span class="quiet">共 ' + esc(data.total) +
      ' 行 · 第 ' + esc(data.page) + ' 页</span><div class="actions">' +
      '<button type="button" id="report-prev" data-mu-page="' + (data.page - 1) + '"' +
      (data.page <= 1 ? ' disabled' : '') + '>上一页</button>' +
      '<button type="button" id="report-next" data-mu-page="' + (data.page + 1) + '"' +
      (!data.has_next ? ' disabled' : '') + '>下一页</button></div></div>';
  } catch (error) {
    if (results?.isConnected) results.innerHTML = '<p class="error">' + esc(error.message) + '</p>';
  }
}

async function runModelCommand(form, screen, action, record) {
  const behavior = modelBehavior(action.behavior_id);
  const error = form.querySelector('[data-mu-form-error]');
  const button = Array.from(form.querySelectorAll('[data-mu-action]')).find(item => item.dataset.muAction === action.id);
  error.textContent = '';
  if (button) button.disabled = true;
  try {
    const payload = modelFormValues(form, screen.fields.filter(field => behavior.runtime.editableFields.includes(field.name)));
    if (record?.id) payload._version = record.version;
    const {record: updated} = await api('behaviors/' + encodeURIComponent(behavior.id), {
      record_id: record?.id || null, input: payload,
    });
    const canEdit = screen.actions.some(item => actionMatchesRecord(item, updated));
    if (screen.layout === 'SINGLE_FORM' && canEdit && !record?.id) {
      if (modelDialog) showModelDialog(screen, updated);
      else {
        modelRows = [updated, ...modelRows.filter(row => row.id !== updated.id)];
        $('#workspace').innerHTML = modelRecordForm(screen, updated, screen.actions, false);
      }
      $('#notice').textContent = '已保存，可以继续办理。';
    } else {
      closeModelDialog();
      if (activeModelScreen) await openModelScreen(activeModelScreen.id, true);
      $('#notice').textContent = '操作已完成。';
    }
  } catch (failure) {
    error.textContent = failure.message;
  } finally {
    if (button) button.disabled = false;
  }
}

async function chooseModelReference(form, field) {
  const reference = field.reference;
  const marker = form.querySelector('[data-reference-label="' + CSS.escape(field.id) + '"]');
  if (!marker || !reference) return;
  const old = form.querySelector('[data-mu-picker]');
  old?.remove();
  const panel = document.createElement('section');
  panel.className = 'mu-picker panel';
  panel.dataset.muPicker = field.id;
  panel.setAttribute('aria-label', field.label);
  panel.innerHTML = '<p class="quiet" role="status">正在读取可见记录…</p>';
  marker.closest('label').after(panel);
  try {
    const data = await api('entities/' + encodeURIComponent(reference.entity_id) + '/records');
    panel.innerHTML = '<p>选择' + esc(field.label) + '</p>' +
      (data.records.length ? data.records.map(row => '<button type="button" data-mu-pick="' +
        esc(row[reference.value]) + '" data-label="' + esc(row[reference.label] || row.code) + '">' +
        esc(row[reference.label] || row.code) + '</button>').join('') : '<p class="quiet">没有可选记录。</p>') +
      '<button type="button" data-mu-unpick>清空</button><button type="button" data-mu-picker-close>关闭</button>';
  } catch (error) {
    panel.textContent = error.message;
  }
}

async function showModelTasks() {
  const token = ++modelViewRevision;
  closeModelDialog();
  const data = await api('tasks');
  if (token !== modelViewRevision) return;
  $('#workspace').innerHTML = '<section class="panel"><h2>审批待办</h2>' +
    data.tasks.map(item => '<div class="toolbar"><span>' + esc(item.task.name) +
      ' · ' + esc(item.record_id.slice(0, 8)) + '</span><button type="button" data-task-screen="' +
      esc(item.entity_id) + '">打开业务记录</button></div>').join('') +
    (data.tasks.length ? '' : '<p class="empty">当前角色暂无待办。</p>') + '</section>';
}

function modelOpenEntity(entityId) {
  const screen = meta.ui_plan.screens.find(item => item.entity_id === entityId && item.layout === 'LIST_MAINTENANCE')
    || meta.ui_plan.screens.find(item => item.entity_id === entityId && item.presentation !== 'dialog');
  if (!screen) throw Error('当前角色没有该对象的已配置页面。');
  return openModelScreen(screen.id);
}

function modelOpenReport(reportId) {
  const screen = meta.ui_plan.screens.find(item => item.report_id === reportId && item.presentation !== 'dialog');
  if (!screen) throw Error('当前角色没有该报表的已配置页面。');
  return openModelScreen(screen.id);
}

async function modelAssistantAction(behaviorId, recordId, suggested) {
  const screen = meta.ui_plan.screens.find(item => item.actions.some(action => action.behavior_id === behaviorId));
  if (!screen) throw Error('当前角色没有这个行为的可用页面。');
  const action = screen.actions.find(item => item.behavior_id === behaviorId);
  let record = {};
  if (recordId) {
    const {records} = await api('entities/' + encodeURIComponent(screen.entity_id) + '/records');
    record = records.find(row => row.id === recordId);
    if (!record || !actionMatchesRecord(action, record)) throw Error('当前记录不可操作，请刷新后核对。');
  }
  showModelDialog(screen, record, [action], suggested);
}

async function modelClick(event) {
  if (!meta?.ui_plan) return;
  const button = event.target.closest('button');
  if (!button) return;
  const {dataset} = button;
  if (dataset.screenRoute) return openModelScreen(dataset.screenRoute);
  if ('muTasks' in dataset) return showModelTasks();
  if (dataset.taskScreen) return modelOpenEntity(dataset.taskScreen);
  if (dataset.muPage && activeModelScreen?.report_id) return queryModelReport($('#filters'), activeModelScreen, Number(dataset.muPage));
  if ('muClose' in dataset) return closeModelDialog();
  const form = button.closest('form');
  const screen = modelScreen(form?.dataset.screenId) || modelDialog?.screen || activeModelScreen;
  if (dataset.muReference) return chooseModelReference(form, screen.fields.find(field => field.id === dataset.muReference));
  const picker = button.closest('[data-mu-picker]');
  if (picker) {
    if ('muPick' in dataset || 'muUnpick' in dataset) {
      const field = screen.fields.find(item => item.id === picker.dataset.muPicker);
      form.elements.namedItem(field.name).value = dataset.muPick || '';
      form.querySelector('[data-reference-label="' + CSS.escape(field.id) + '"]').textContent = dataset.label || '请选择';
    }
    picker.remove();
    return;
  }
  const action = (modelDialog?.actions || screen?.actions || []).find(item => item.id === dataset.muAction);
  if (!action || button.type === 'submit') return;
  if (action.kind === 'navigate') return openModelScreen(action.target_screen_id);
  if (action.kind === 'open_form') return showModelDialog(modelScreen(action.target_screen_id));
  if (action.kind === 'cancel' && modelDialog) return closeModelDialog();
  if (action.kind === 'cancel' || action.kind === 'reset') return form?.reset();
  if (action.behavior_id) {
    const record = modelRows.find(row => row.id === dataset.record);
    showModelDialog(screen, record, [action]);
  }
}

document.addEventListener('click', event => modelClick(event).catch(err));
document.addEventListener('submit', event => {
  if (!meta?.ui_plan) return;
  const form = event.target;
  if (form.hasAttribute('data-mu-report')) {
    event.preventDefault();
    queryModelReport(form, modelScreen(form.dataset.screenId));
  } else if (form.hasAttribute('data-mu-form')) {
    event.preventDefault();
    const screen = modelScreen(form.dataset.screenId);
    const action = (modelDialog?.actions || screen.actions).find(item => item.id === event.submitter?.dataset.muAction);
    if (action?.behavior_id) {
      const record = modelDialog?.record || modelRows.find(row => row.id === form.dataset.recordId);
      runModelCommand(form, screen, action, record);
    }
  }
});
window.addEventListener('hashchange', () => {
  if (meta?.ui_plan && modelRoute()) openModelScreen(modelRoute(), true).catch(err);
});
