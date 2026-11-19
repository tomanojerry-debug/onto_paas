const sessions = new Map();
const array = (value) => Array.isArray(value) ? value : [];
const clone = (value) => JSON.parse(JSON.stringify(value));
const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const objectModel = (project) => project?.models?.['m1-object-model.yaml'] ||
  project?.models?.['m1_object_model.yaml'] || {};
const entries = (binding) => array(binding?.entity_mappings ?? binding?.mappings);
const entityId = (entry) => entry.entity_id ?? entry.entity ?? entry.id;
const fields = (entry) => array(entry?.field_mappings ?? entry?.fields);
const tableName = (table) => table?.table ?? table?.name;
const attrName = (field) => field.ontology_field ?? field.attribute;
const targetTable = (field) => field.db_table ?? field.table;
const targetColumn = (field) => field.db_column ?? field.column;
const columnName = (column) => typeof column === 'string' ? column : column?.name;
const requiresReview = (field) => field?.mapping_origin !== 'manual' && typeof field?.confidence === 'number' &&
  field.confidence >= 0 && field.confidence < 0.85;

function projectObjects(project, binding) {
  const entities = array(objectModel(project).entities);
  const byId = new Map(entities.map((entity) => [entity.id, entity]));
  for (const entry of entries(binding)) {
    const id = entityId(entry);
    if (id && !byId.has(id)) byId.set(id, { id, name: entry.entity_name ?? id });
  }
  return [...byId.values()];
}

function attributes(entity, entry) {
  if (entity?.attributes?.length) return entity.attributes;
  // Legacy snapshots may have saved declarations but no longer have an M1.
  return fields(entry).map((field) => ({
    name: attrName(field),
    label: field.ontology_label ?? attrName(field),
  }));
}

function readSession(project, binding) {
  const key = project?.id ?? '';
  const revision = project?.revision;
  const identity = JSON.stringify([project?.source?.sha256, project?.source?.schema, objectModel(project), binding]);
  let state = sessions.get(key);
  if (!state) {
    let saved;
    try {
      saved = JSON.parse(sessionStorage.getItem(`onto-paas-mapping-v1:${key}`));
    } catch {}
    state = {
      key, identity, revision: saved?.draft ? saved.revision : revision, baseline: binding,
      draft: saved?.draft || null, entity: saved?.entity || '', table: saved?.table || '',
      field: '', pending: null, history: new Map(), notice: '', saving: false,
      stale: Boolean(saved?.draft && saved.revision !== revision),
    };
    sessions.set(key, state);
  }
  if (state.draft && same(state.draft, binding)) {
    state.draft = null;
    state.identity = identity;
    state.baseline = binding;
    state.revision = revision;
    state.stale = false;
    persist(state);
  } else if (!state.draft) {
    if (state.identity !== identity) state.history.clear();
    Object.assign(state, { identity, baseline: binding, revision, stale: false });
  } else if (state.identity !== identity || state.revision !== revision) {
    state.stale = true;
  }
  return state;
}

function persist(state) {
  if (!state.key) return;
  try {
    const { revision, draft, entity, table } = state;
    sessionStorage.setItem(`onto-paas-mapping-v1:${state.key}`, JSON.stringify({ revision, draft, entity, table }));
  } catch {}
}

function putEntry(state, entry) {
  const document = clone(state.draft || state.baseline);
  const key = Array.isArray(document.mappings) && !Array.isArray(document.entity_mappings) ? 'mappings' : 'entity_mappings';
  document[key] ||= [];
  const index = document[key].findIndex((item) => entityId(item) === state.entity);
  if (index === -1) {
    if (entry) document[key].push(clone(entry));
  } else if (entry) {
    document[key][index] = clone(entry);
  } else {
    document[key].splice(index, 1);
  }
  state.draft = same(document, state.baseline) ? null : document;
  persist(state);
}

function withFields(entry, next) {
  const key = Array.isArray(entry.fields) && !Array.isArray(entry.field_mappings) ? 'fields' : 'field_mappings';
  return { ...entry, [key]: next };
}

function downloadYaml(binding, fileName) {
  const inline = (value) => value === null || typeof value !== 'object' || !Object.keys(value).length;
  const write = (value, indent = 0) => {
    const pad = ' '.repeat(indent);
    if (inline(value)) return pad + JSON.stringify(value);
    if (Array.isArray(value)) return value.map((item) => inline(item)
      ? `${pad}- ${JSON.stringify(item)}`
      : `${pad}-\n${write(item, indent + 2)}`).join('\n');
    return Object.entries(value).map(([key, item]) => inline(item)
      ? `${pad}${JSON.stringify(key)}: ${JSON.stringify(item)}`
      : `${pad}${JSON.stringify(key)}:\n${write(item, indent + 2)}`).join('\n');
  };
  const blob = new Blob([write(binding) + '\n'], { type: 'application/yaml' });
  const href = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = href;
  link.download = fileName;
  link.click();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}

function typeWarning(attribute, column) {
  if (!attribute?.type || !column?.type) return false;
  const source = String(attribute.type).toLowerCase();
  const target = String(column.type).toUpperCase();
  if (/dictionary|enum/.test(source)) return !/INT|TEXT|CHAR|CLOB/.test(target);
  if (/integer|^int$|boolean/.test(source)) return !/INT/.test(target);
  if (/decimal|float|money|number/.test(source)) return !/INT|REAL|FLOAT|DOUBLE|NUMERIC|DECIMAL/.test(target);
  if (/string|date|reference|ref$/.test(source)) return !/TEXT|CHAR|CLOB/.test(target);
  return false;
}

const action = (name, label, disabled = false, extra = '') =>
  `<button type="button" data-map-action="${name}" ${disabled ? 'disabled' : ''} ${extra}>${label}</button>`;
const option = (value, label, current) =>
  `<option value="${esc(value)}" ${value === current ? 'selected' : ''}>${esc(label)}</option>`;

export function mountMapping(container, { project, binding, projects = [], onSelectProject, onSave, onDownload, busy = false } = {}) {
  const state = readSession(project, binding);
  const objects = projectObjects(project, binding);
  const tables = array(project?.source?.schema?.tables);
  const realSchema = tables.length > 0;
  const tablesByName = new Map(tables.map((table) => [tableName(table), table]));
  const editable = () => !busy && !state.saving && !state.stale && Boolean(onSave && binding &&
    realSchema && objects.find((entity) => entity.id === state.entity)?.attributes?.length);
  const documentBinding = () => state.draft || state.baseline;
  const currentEntry = () => entries(documentBinding()).find((entry) => entityId(entry) === state.entity);
  const history = () => {
    if (!state.history.has(state.entity)) state.history.set(state.entity, { undo: [], redo: [] });
    return state.history.get(state.entity);
  };
  const selectionFocus = () => state.field ? `[data-map-source="${CSS.escape(state.field)}"]` :
    state.pending?.kind === 'target' ? `[data-map-target="${CSS.escape(state.pending.name)}"]` : '#mapping-object';
  let disposed = false;
  let search = '';
  let drag = null;
  let pointerRelease = null;
  let boardObserver = null;
  let viewportScroll = [0, 0];

  function currentView() {
    if (!objects.some((entity) => entity.id === state.entity)) state.entity = objects[0]?.id || '';
    const entity = objects.find((item) => item.id === state.entity);
    const entry = currentEntry();
    const declared = array(entry?.tables);
    const entryFields = fields(entry);
    const declarationTables = [...new Set([...declared.map(tableName), ...entryFields.map(targetTable)].filter(Boolean))];
    const tableNames = [...new Set([...declarationTables, ...tablesByName.keys()])];
    if (!tableNames.includes(state.table)) state.table = declared.find((table) => table.primary)?.table ?? tableNames[0] ?? '';
    const table = tablesByName.get(state.table);
    const tableFields = entryFields.filter((field) => targetTable(field) === state.table);
    const columns = table ? array(table.columns) :
      [...new Set(tableFields.map(targetColumn).filter(Boolean))].map((name) => ({ name }));
    return { entity, declared, declarationTables, tableNames, table, tableFields, columns,
      attributes: attributes(entity, entry), entryFields };
  }

  function draw() {
    const board = container.querySelector('.mapping-board');
    const svg = board?.querySelector('.mapping-links');
    if (!svg) return;
    const box = board.getBoundingClientRect();
    if (!box.width || !box.height) return;
    const position = (element) => {
      const bounds = element.getBoundingClientRect();
      return [bounds.left + bounds.width / 2 - box.left, bounds.top + bounds.height / 2 - box.top];
    };
    const curve = (source, target) => {
      const mid = (source[0] + target[0]) / 2;
      return `M ${source[0]} ${source[1]} C ${mid} ${source[1] - 3}, ${mid} ${target[1] + 3}, ${target[0]} ${target[1]}`;
    };
    const sources = new Map([...board.querySelectorAll('[data-map-source]')].map((item) => [item.dataset.mapSource, item]));
    const targets = new Map([...board.querySelectorAll('[data-map-target]')].map((item) => [item.dataset.mapTarget, item]));
    const view = currentView();
    const lines = view.tableFields.map((field) => {
      const attr = attrName(field);
      const column = targetColumn(field);
      const start = sources.get(attr);
      const end = targets.get(column);
      if (!start || !end) return '';
      const d = curve(position(start), position(end));
      const key = view.table?.primary_key?.includes(column);
      return `<path class="mapping-link ${attr === state.field ? 'selected' : ''} ${key ? 'key' : ''}" d="${d}"></path>
        <path class="mapping-link-hit" d="${d}" tabindex="0" role="button" data-map-line="${esc(attr)}" aria-label="${esc(attr)} 映射到 ${esc(column)}"></path>`;
    });
    if (drag?.moved) {
      const start = position(drag.control);
      const end = [drag.x - box.left, drag.y - box.top];
      lines.push(`<path class="mapping-link drafting" d="${curve(start, end)}"></path>`);
    }
    svg.setAttribute('viewBox', `0 0 ${box.width} ${box.height}`);
    svg.innerHTML = lines.join('');
  }

  function render(focus) {
    if (disposed) return;
    boardObserver?.disconnect();
    const viewport = container.querySelector('.mapping-viewport');
    if (viewport) viewportScroll = [viewport.scrollLeft, viewport.scrollTop];
    const view = currentView();
    const { entity, declared, declarationTables, tableNames, table, tableFields, columns, attributes: attrs, entryFields } = view;
    const projectList = projects.filter((item) => item.kind === 'analysis').map((item) => {
      const name = item.name || item.id;
      const model = objectModel(item);
      const domain = typeof model.domain === 'string' ? model.domain : model.domain?.name;
      const objectCount = projectObjects(item, item.source_bindings || item.models?.['data_mapping.yaml']).length;
      const tableCount = array(item.source?.schema?.tables).length;
      return `<button type="button" class="mapping-project ${item.id === project?.id ? 'active' : ''}" data-map-project="${esc(item.id)}" data-search="${esc([name, domain].filter(Boolean).join(' ').toLowerCase())}" ${busy || state.saving ? 'disabled' : ''} ${item.id === project?.id ? 'aria-current="true"' : ''}>
        <strong>${esc(name)}</strong><span>${objectCount} 个对象 · ${tableCount ? `${tableCount} 张 SQLite 表` : '未连接数据源'}</span>
      </button>`;
    });
    const sidebar = `<aside class="mapping-list"><div class="mapping-list-heading"><h2>数据本体</h2><span>${projectList.length} 个项目</span></div>
      <label class="sr-only" for="mapping-project-search">搜索数据本体</label>
      <input id="mapping-project-search" type="search" placeholder="搜索名称或领域" value="${esc(search)}">
      <div class="mapping-projects">${projectList.join('') || '<p>暂无分析项目</p>'}<p class="mapping-search-empty" hidden>未找到匹配的数据本体</p></div>
    </aside>`;
    if (!project || !binding || !objects.length) {
      container.innerHTML = `<div class="mapping-shell">${sidebar}<section class="mapping-empty"><h2>${!project ? '选择一个分析项目' : '当前项目没有可查看的字段映射'}</h2><p>连接数据源并生成数据本体后，即可查看对象与数据库表的对应关系。</p></section></div>`;
      filterProjects();
      return;
    }

    const selected = entryFields.find((field) => attrName(field) === state.field);
    const reviewItems = entries(documentBinding()).flatMap((entry) =>
      fields(entry).filter(requiresReview).map((field) => ({ id: entityId(entry), field })));
    const mappedAttrs = new Set(entryFields.map(attrName));
    const mappedCols = new Set(tableFields.map(targetColumn));
    const canEdit = editable();
    const duplicateTargets = new Set();
    const seenTargets = new Set();
    const warnings = [];
    for (const field of entryFields) {
      const target = `${targetTable(field)}.${targetColumn(field)}`;
      if (seenTargets.has(target)) duplicateTargets.add(target);
      seenTargets.add(target);
      const column = array(tablesByName.get(targetTable(field))?.columns).find((item) => columnName(item) === targetColumn(field));
      if (typeWarning(attrs.find((attribute) => attribute.name === attrName(field)), column))
        warnings.push(`${attrName(field)} 与 ${target} 的字段类型待核对`);
    }
    for (const target of duplicateTargets) warnings.push(`多个属性指向 ${target}`);
    const unmappedAttrs = attrs.filter((attribute) => !mappedAttrs.has(attribute.name)).length;
    const unmappedCols = columns.filter((column) => !mappedCols.has(columnName(column))).length;
    const invalid = project?.model_diagnostics?.length;
    const role = (name) => {
      const declaration = declared.find((item) => tableName(item) === name);
      return declaration ? declaration.primary ? '主表' : '关联表' : '未绑定';
    };
    const tableOptions = tableNames.map((name) => option(name, `${name}（${role(name)}）`, state.table)).join('');
    const attrRows = attrs.map((attribute) => {
      const name = attribute.name;
      const field = entryFields.find((item) => attrName(item) === name);
      const mappedHere = field && targetTable(field) === state.table;
      const note = field && !mappedHere ? `已连 ${targetTable(field)}` : '';
      return `<div class="mapping-field ${mappedHere ? 'mapped' : ''} ${name === state.field ? 'selected' : ''}">
        <div><strong>${esc(attribute.label || name)}</strong><small>${esc(name)}${note ? ` · ${esc(note)}` : ''}</small></div>
        <span class="mapping-field-type">${attribute.required ? '<b>必填</b>' : ''}${esc(attribute.type)}</span>
        <button type="button" class="mapping-port" data-map-source="${esc(name)}" aria-label="属性 ${esc(name)} 的连接点" title="${esc(name)}" ${!canEdit && !field ? 'disabled' : ''}></button>
      </div>`;
    }).join('');
    const columnRows = columns.map((column) => {
      const name = columnName(column);
      const pk = column.pk || table?.primary_key?.includes(name);
      const selectedTarget = selected && targetTable(selected) === state.table && targetColumn(selected) === name;
      return `<div class="mapping-field ${mappedCols.has(name) ? 'mapped' : ''} ${selectedTarget ? 'selected' : ''}">
        <button type="button" class="mapping-port" data-map-target="${esc(name)}" aria-label="字段 ${esc(name)} 的连接点" title="${esc(name)}" ${!canEdit ? 'disabled' : ''}></button>
        <div><strong>${pk ? '<b>PK</b>' : ''}${esc(name)}</strong><small>${table ? column.notnull || pk ? '非空' : '可空' : '映射声明'}</small></div>
        <span class="mapping-field-type">${esc(column.type)}</span>
      </div>`;
    }).join('');
    const scopeNotice = !realSchema ? '没有已核对的数据表。可查看或导出已有映射。' :
      !table && state.table ? '请选择实际数据表以重新连接。' :
      '点击或拖动连接点建立映射。选中连线可修改或删除。';
    const notice = state.stale ? '数据本体、数据库结构或映射版本已更新。当前草稿可导出，请载入最新版本后再编辑。' : state.notice;
    const status = state.draft ? '有未保存修订' : !realSchema ? '只读' :
      unmappedAttrs || invalid ? '已保存 · 待校验' : warnings.length || reviewItems.length ? '已保存 · 待核对' : '已保存 · 校验正常';
    container.innerHTML = `<div class="mapping-shell">${sidebar}
      <section class="mapping-workspace live-mapping-group">
        <div class="mapping-selectors">
          <label for="mapping-object">业务对象 <select id="mapping-object" ${busy || state.saving ? 'disabled' : ''}>${objects.map((item) => option(item.id, `${item.name || item.id}（${item.id}）`, state.entity)).join('')}</select></label>
          <label for="mapping-table">数据表 <select id="mapping-table" ${!tableNames.length || busy || state.saving ? 'disabled' : ''}>${tableOptions}</select></label>
          <div class="mapping-history">${action('undo', '撤销', !canEdit || !history().undo.length)}${action('redo', '重做', !canEdit || !history().redo.length)}</div>
        </div>
        <div class="mapping-tools">
          <div class="mapping-selection">${selected ? `<strong>${esc(state.field)}</strong><span>→ ${esc(targetTable(selected))}.${esc(targetColumn(selected))}</span>` : state.pending ? `<strong>${esc(state.pending.name)}</strong><span>选择另一端连接点</span>` : '<span>选择字段或连线</span>'}</div>
          <div class="mapping-tools-actions">
            ${action('edit', '手工映射', !canEdit || !state.field)}
            ${requiresReview(selected) ? action('review', '确认此映射', !canEdit) : ''}
            ${action('delete', '删除连线', !canEdit || !selected)}
            ${action('download', '导出映射')}
            ${project?.model_import?.files?.['data_mapping.yaml']?.artifact ? `<a class="ui-button" data-map-original href="/api/v1/projects/${encodeURIComponent(project.id)}/artifacts/${encodeURIComponent(project.model_import.files['data_mapping.yaml'].artifact)}" download="data_mapping.original.yaml">下载原始映射</a>` : ''}
            ${state.stale ? action('reset', '载入最新映射') : action('save', state.saving ? '正在保存…' : '保存映射', !canEdit || !state.draft, 'class="primary"')}
          </div>
        </div>
        ${notice ? `<p class="mapping-notice" role="status">${esc(notice)}</p>` : ''}
        <div class="mapping-viewport">
          <div class="mapping-board">
            <svg class="mapping-links" aria-label="属性与数据库字段的连线"></svg>
            <section class="mapping-card mapping-ontology"><header><h3>${esc(entity?.name || state.entity)}</h3><span>本体对象</span></header><p class="mapping-card-meta">${attrs.length} 个属性 · ${tableFields.length} 条当前表映射</p>${attrRows || '<p class="mapping-card-meta">无属性声明</p>'}</section>
            <section class="mapping-card mapping-physical"><header><h3>${esc(state.table || '未选数据表')}</h3><span>${esc(state.table ? role(state.table) : '')}</span></header><p class="mapping-card-meta">${table?.primary_key?.length ? `主键 ${esc(table.primary_key.join('、'))}` : table ? '数据库物理表' : '映射声明'} · ${mappedCols.size}/${columns.length} 已映射</p>${columnRows || '<p class="mapping-card-meta">无可显示字段</p>'}</section>
          </div>
        </div>
        <div class="mapping-status"><span>映射表 <strong>${declarationTables.length}</strong></span><span>已映射 <strong>${mappedAttrs.size}</strong></span><span>对象未映射 <strong>${unmappedAttrs}</strong></span>${table ? `<span>表字段未映射 <strong>${unmappedCols}</strong></span>` : ''}<strong class="${state.draft || unmappedAttrs || invalid || warnings.length || reviewItems.length ? 'review' : 'normal'}">${status}</strong></div>
        <p class="mapping-help">${esc(scopeNotice)}${!declarationTables.includes(state.table) && table ? ' 向此表连线会将它加入当前对象的映射。' : ''}</p>
        ${warnings.length ? `<details class="mapping-warnings"><summary>${warnings.length} 项映射待核对</summary><ul>${warnings.map((warning) => `<li>${esc(warning)}</li>`).join('')}</ul></details>` : ''}
        ${reviewItems.length ? `<details class="mapping-warnings" open><summary>${reviewItems.length} 项低置信度映射待复核</summary><p>打开字段，核对业务含义后确认对应关系，或修改目标字段。完成后保存映射。</p><ul>${reviewItems.map(({ id, field }) => {
          const score = (field.confidence * 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
          return `<li><button type="button" data-map-review-entity="${esc(id)}" data-map-review-field="${esc(attrName(field))}">${esc(id)} · ${esc(attrName(field))} → ${esc(targetTable(field))}.${esc(targetColumn(field))}（${score}%）</button>${typeof field.note === 'string' && field.note ? `<span> ${esc(field.note)}</span>` : ''}</li>`;
        }).join('')}</ul></details>` : ''}
      </section></div>`;
    filterProjects();
    const nextViewport = container.querySelector('.mapping-viewport');
    [nextViewport.scrollLeft, nextViewport.scrollTop] = viewportScroll;
    draw();
    boardObserver = new ResizeObserver(() => { if (!disposed) draw(); });
    boardObserver.observe(container.querySelector('.mapping-board'));
    if (focus) container.querySelector(focus)?.focus({ preventScroll: true });
  }

  function filterProjects() {
    const query = search.trim().toLowerCase();
    const items = [...container.querySelectorAll('.mapping-project')];
    items.forEach((item) => { item.hidden = !item.dataset.search.includes(query); });
    const empty = container.querySelector('.mapping-search-empty');
    if (empty) empty.hidden = !items.length || items.some((item) => !item.hidden);
  }

  function changeEntry(next) {
    const previous = currentEntry();
    if (same(previous, next)) return false;
    const changes = history();
    changes.undo.push(previous ? clone(previous) : null);
    if (changes.undo.length > 40) changes.undo.shift();
    changes.redo = [];
    state.notice = '';
    putEntry(state, next);
    return true;
  }

  function connect(attributeName, table, column, conversion) {
    if (!editable()) return;
    const view = currentView();
    const attribute = view.attributes.find((item) => item.name === attributeName);
    if (!attribute || !array(tablesByName.get(table)?.columns).some((item) => columnName(item) === column)) {
      state.notice = '请选择当前数据本体的属性和真实数据表字段。';
      render();
      return;
    }
    const original = currentEntry();
    const entry = original || { entity_id: state.entity, entity_name: view.entity.name || state.entity, tables: [], field_mappings: [] };
    const index = view.entryFields.findIndex((field) => attrName(field) === attributeName);
    const previous = index < 0 ? null : view.entryFields[index];
    const moved = !previous || targetTable(previous) !== table || targetColumn(previous) !== column;
    const nextField = { ...previous };
    if (moved) {
      Object.assign(nextField, { ontology_field: attributeName, ontology_label: attribute.label || attributeName,
        db_table: table, db_column: column, mapping_origin: 'manual' });
      delete nextField.attribute;
      delete nextField.table;
      delete nextField.column;
      delete nextField.confidence;
      delete nextField.value_mapping;
      delete nextField.db_expression;
      delete nextField.note;
    }
    if (conversion !== undefined) {
      if (Object.keys(conversion).length) nextField.value_mapping = conversion;
      else delete nextField.value_mapping;
    }
    const next = [...view.entryFields];
    next.splice(index < 0 ? next.length : index, index < 0 ? 0 : 1, nextField);
    const tableEntries = array(entry.tables);
    const updated = withFields(tableEntries.some((item) => tableName(item) === table) ? entry :
      { ...entry, tables: [...tableEntries, { table, primary: !tableEntries.length }] }, next);
    state.field = attributeName;
    state.pending = null;
    state.table = table;
    const changed = changeEntry(updated);
    if (changed && moved && previous?.value_mapping && conversion === undefined) {
      state.notice = '目标字段已更换，原取值转换已移除。可通过手工映射重新填写。';
    }
    render(selectionFocus());
  }

  function openEditor() {
    const view = currentView();
    const field = view.entryFields.find((item) => attrName(item) === state.field);
    const editTable = (field && tablesByName.has(targetTable(field)) ? targetTable(field) : '') ||
      (tablesByName.has(state.table) ? state.table : '') || tablesByName.keys().next().value;
    const editColumn = field && targetTable(field) === editTable ? targetColumn(field) : '';
    const columnOptions = (table, current) => array(tablesByName.get(table)?.columns)
      .map((item) => option(columnName(item), columnName(item), current)).join('');
    const dialog = document.createElement('dialog');
    dialog.className = 'mapping-dialog';
    dialog.setAttribute('aria-labelledby', 'mapping-dialog-title');
    const conversionValue = field?.value_mapping ? JSON.stringify(field.value_mapping, null, 2) : '';
    dialog.innerHTML = `<form class="mapping-edit-form"><header><h3 id="mapping-dialog-title">手工映射</h3><p>${esc(state.field)}</p></header>
      <label for="mapping-edit-table">目标表<select id="mapping-edit-table">${[...tablesByName.keys()].map((name) => option(name, name, editTable)).join('')}</select></label>
      <label for="mapping-edit-column">目标字段<select id="mapping-edit-column" required>${columnOptions(editTable, editColumn)}</select></label>
      <label for="mapping-conversion">取值转换<textarea id="mapping-conversion" rows="4" placeholder='{"原始值":"业务值"}'>${esc(conversionValue)}</textarea></label>
      <p class="mapping-help">选填，使用 JSON 对象定义取值对应关系。</p>
      <p class="mapping-dialog-error" role="alert"></p>
      <div class="mapping-tools-actions">${action('cancel-edit', '取消')}<button type="submit" class="primary">保存映射草稿</button></div></form>`;
    container.append(dialog);
    const tableInput = dialog.querySelector('#mapping-edit-table');
    const columnInput = dialog.querySelector('#mapping-edit-column');
    const valueInput = dialog.querySelector('#mapping-conversion');
    const error = dialog.querySelector('.mapping-dialog-error');
    tableInput.addEventListener('change', () => {
      columnInput.innerHTML = columnOptions(tableInput.value, '');
      valueInput.value = '';
    });
    columnInput.addEventListener('change', () => {
      if (targetTable(field || {}) !== tableInput.value || targetColumn(field || {}) !== columnInput.value)
        valueInput.value = '';
    });
    dialog.querySelector('form').addEventListener('submit', (event) => {
      event.preventDefault();
      event.stopPropagation();
      let conversion;
      try {
        conversion = valueInput.value.trim() ? JSON.parse(valueInput.value) : {};
        if (!conversion || typeof conversion !== 'object' || Array.isArray(conversion) ||
            Object.values(conversion).some((value) => value !== null && typeof value === 'object'))
          throw new TypeError('非标量转换');
      } catch {
        error.textContent = '请填写 JSON 对象，每个原始值对应一个字符串、布尔值、数字或 null。';
        return;
      }
      dialog.close();
      connect(state.field, tableInput.value, columnInput.value, conversion);
    });
    dialog.addEventListener('close', () => dialog.remove(), { once: true });
    dialog.showModal();
  }

  async function save() {
    if (!editable() || !state.draft) return;
    const snapshot = clone(state.draft);
    state.saving = true;
    state.notice = '正在保存映射…';
    render();
    try {
      const updated = await onSave(snapshot, state.revision);
      if (updated) {
        const returned = updated.source_bindings || updated.models?.['data_mapping.yaml'] || snapshot;
        project = updated;
        binding = returned;
        state.baseline = returned;
        state.revision = updated.revision;
      } else {
        state.baseline = snapshot;
      }
      state.draft = null;
      state.stale = false;
      state.notice = updated?.model_diagnostics?.length
        ? '映射已保存。补齐待校验的字段后再确认数据本体。'
        : '映射已保存。重新确认当前数据本体后即可用于分析。';
      persist(state);
    } catch (error) {
      state.notice = error instanceof Error ? error.message : String(error);
    } finally {
      state.saving = false;
      state.redraw?.('[data-map-action="download"]');
    }
  }

  function applyAction(name) {
    if (name === 'cancel-edit') { container.querySelector('dialog')?.close(); return; }
    if (name === 'download') {
      const fileName = 'data_mapping.yaml';
      const complete = clone(documentBinding());
      if (onDownload) onDownload(complete, fileName);
      else downloadYaml(complete, fileName);
      return;
    }
    if (name === 'reset') {
      Object.assign(state, { baseline: binding, revision: project.revision, draft: null, stale: false,
        notice: '', field: '', pending: null });
      state.history.clear();
      persist(state);
      render('#mapping-object');
      return;
    }
    if (!editable()) return;
    if (name === 'save') { void save(); return; }
    if (name === 'edit') { openEditor(); return; }
    if (name === 'review') {
      const entry = currentEntry();
      const selected = fields(entry).find((field) => attrName(field) === state.field);
      if (requiresReview(selected)) {
        const { confidence, ...confirmed } = selected;
        const updated = { ...confirmed, mapping_origin: 'manual', suggested_confidence: confidence };
        changeEntry(withFields(entry, fields(entry).map((field) => field === selected ? updated : field)));
        state.notice = '已确认这条映射。请保存映射后继续。';
      }
    } else if (name === 'delete') {
      const entry = currentEntry();
      if (entry && state.field) changeEntry(withFields(entry, fields(entry).filter((field) => attrName(field) !== state.field)));
    } else if (name === 'undo' || name === 'redo') {
      const changes = history();
      if (!changes[name].length) return;
      const current = currentEntry();
      changes[name === 'undo' ? 'redo' : 'undo'].push(current ? clone(current) : null);
      state.notice = '';
      putEntry(state, changes[name].pop());
    }
    state.pending = null;
    render(selectionFocus());
  }

  function click(event) {
    const target = event.target.closest('[data-map-project],[data-map-action],[data-map-line],[data-map-source],[data-map-target],[data-map-review-field]');
    if (!target || target.disabled) return;
    const { mapProject, mapAction, mapLine, mapSource, mapTarget, mapReviewEntity, mapReviewField } = target.dataset;
    if (event.detail && (mapSource || mapTarget) && pointerRelease &&
        performance.now() < pointerRelease.until &&
        Math.hypot(event.clientX - pointerRelease.x, event.clientY - pointerRelease.y) < 3) return;
    if (mapProject) {
      if (mapProject !== project?.id && onSelectProject) {
        state.pending = null;
        Promise.resolve(onSelectProject(mapProject)).catch((error) => {
          state.notice = error instanceof Error ? error.message : String(error);
          render();
        });
      }
      return;
    }
    if (mapAction) { applyAction(mapAction); return; }
    if (mapReviewField) {
      state.entity = mapReviewEntity;
      const selected = fields(currentEntry()).find((field) => attrName(field) === mapReviewField);
      if (!selected) return;
      state.table = targetTable(selected);
      state.field = mapReviewField;
      state.pending = null;
      persist(state);
      render(editable() ? '[data-map-action="review"]' : selectionFocus());
      return;
    }
    if (mapLine) {
      state.field = mapLine;
      state.pending = null;
      render(selectionFocus());
    } else if (mapSource) {
      state.field = mapSource;
      if (editable() && state.pending?.kind === 'target') {
        connect(mapSource, state.table, state.pending.name);
      } else {
        state.pending = editable() ? { kind: 'source', name: mapSource } : null;
        render(selectionFocus());
      }
    } else if (mapTarget && editable()) {
      if (state.pending?.kind === 'source') connect(state.pending.name, state.table, mapTarget);
      else {
        state.pending = { kind: 'target', name: mapTarget };
        state.field = '';
        render(selectionFocus());
      }
    }
  }

  function change(event) {
    const { id, value } = event.target;
    if (id === 'mapping-object') {
      state.entity = value;
      state.table = '';
      viewportScroll = [0, 0];
      const viewport = container.querySelector('.mapping-viewport');
      if (viewport) [viewport.scrollLeft, viewport.scrollTop] = viewportScroll;
    } else if (id === 'mapping-table') state.table = value;
    else return;
    state.field = '';
    state.pending = null;
    state.notice = '';
    persist(state);
    render(`#${id}`);
  }

  function keydown(event) {
    const fieldControl = event.target.matches('input, textarea, select, [contenteditable="true"]');
    if (container.querySelector('dialog[open]')) return;
    if (event.target.matches('[data-map-line]') && ['Enter', ' '].includes(event.key)) {
      event.preventDefault();
      event.target.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      return;
    }
    const command = (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z';
    if (command && !fieldControl) {
      event.preventDefault();
      applyAction(event.shiftKey ? 'redo' : 'undo');
    } else if (event.key === 'Delete' && !fieldControl && state.field) {
      event.preventDefault();
      applyAction('delete');
    } else if (event.key === 'Escape' && (state.pending || state.field)) {
      state.pending = null;
      state.field = '';
      render('#mapping-object');
    }
  }

  function pointerdown(event) {
    if (event.button !== 0 || !editable()) return;
    const control = event.target.closest('[data-map-source],[data-map-target]');
    if (!control || control.disabled) return;
    drag = { control, pointerId: event.pointerId, x: event.clientX, y: event.clientY, moved: false };
  }

  function pointermove(event) {
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (!drag.moved && Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 5) return;
    if (event.cancelable) event.preventDefault();
    Object.assign(drag, { x: event.clientX, y: event.clientY, moved: true });
    draw();
  }

  function pointerup(event) {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const previous = drag;
    drag = null;
    if (!previous.moved) return;
    pointerRelease = { x: event.clientX, y: event.clientY, until: performance.now() + 100 };
    const endpoint = document.elementFromPoint(event.clientX, event.clientY)?.closest('[data-map-source],[data-map-target]');
    const source = previous.control.dataset.mapSource || endpoint?.dataset.mapSource;
    const target = previous.control.dataset.mapTarget || endpoint?.dataset.mapTarget;
    if (endpoint && container.contains(endpoint) && source && target) connect(source, state.table, target);
    else draw();
  }

  function pointercancel() { drag = null; draw(); }
  function input(event) {
    if (event.target.id === 'mapping-project-search') {
      search = event.target.value;
      filterProjects();
    }
  }

  container.addEventListener('click', click);
  container.addEventListener('change', change);
  container.addEventListener('input', input);
  container.addEventListener('keydown', keydown);
  container.addEventListener('pointerdown', pointerdown);
  window.addEventListener('pointermove', pointermove, { passive: false });
  window.addEventListener('pointerup', pointerup);
  window.addEventListener('pointercancel', pointercancel);
  state.redraw = render;
  render();
  return () => {
    disposed = true;
    drag = null;
    state.pending = null;
    if (state.redraw === render) state.redraw = null;
    boardObserver?.disconnect();
    container.removeEventListener('click', click);
    container.removeEventListener('change', change);
    container.removeEventListener('input', input);
    container.removeEventListener('keydown', keydown);
    container.removeEventListener('pointerdown', pointerdown);
    window.removeEventListener('pointermove', pointermove);
    window.removeEventListener('pointerup', pointerup);
    window.removeEventListener('pointercancel', pointercancel);
  };
}
