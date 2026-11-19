const labels = {
  aggregates: '聚合根', entities: '子实体', attributes: '属性', valueObjects: '值对象',
  invariants: '不变性', aggregate_associations: '聚合关系', data_dictionaries: '数据字典',
  behaviors: '行为', rules: '规则', actors: '主体', roles: '角色', permissions: '权限',
  flows: '流程', activities: '活动', query_reports: '查询与报表', screens: '界面',
  elements: '控件', actions: '操作', menus: '菜单', parameters: '参数',
  id: '编号', name: '名称', alias: '别名', label: '显示名称', description: '说明', type: '类型',
  required: '必填', unique: '唯一', default: '默认值', defaultValue: '默认值',
  length: '长度', lifecycle: '生命周期', states: '状态', transitions: '状态迁移',
  aggregateType: '聚合类型', ruleType: '规则类型', behaviorType: '行为类型',
  flowType: '流程类型', screenType: '界面类型', model_type: '模型类型', domain: '业务域',
};
const idFields = ['id', 'roleId', 'actorId', 'permissionId', 'activityId', 'screenId', 'elementId', 'actionId'];
const order = ['OBJECT', 'BEHAVIOR', 'RULE', 'ACTOR', 'FLOW', 'REPORT', 'QUERY_REPORT', 'UI'];
const views = new Map();
const field = key => labels[key] || key;
function itemLabel(item, index) {
  const names = [item.name, item.alias, item.label, ...idFields.map(key => item[key])]
    .filter(candidate => typeof candidate === 'string' && candidate.trim());
  return names.find(name => /\p{Script=Han}/u.test(name)) || names[0] || `第 ${index + 1} 项`;
}
const isGroup = value => value && typeof value === 'object' && (!Array.isArray(value) || value.some(
  item => item && typeof item === 'object'));

export function ontologyEditableFields(node) {
  return Object.entries(node || {}).filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value));
}

function valueText(value) {
  if (value === true) return '是';
  if (value === false) return '否';
  if (value == null || value === '') return '—';
  if (Array.isArray(value)) return value.map(valueText).join('、') || '—';
  return String(value);
}

export function mountBusinessOntology(panel, { project, escape, titleFor, onEdit, editable }) {
  const tree = panel.querySelector('.live-ontology-tree');
  const detail = panel.querySelector('.live-ontology-detail');
  const search = panel.querySelector('#ontology-search');
  const nodes = new Map();
  const view = views.get(project.id) || { selected: null, expanded: new Set(), query: '' };
  views.set(project.id, view);

  function nodeFor(value, filename, path, label, parent) {
    const key = JSON.stringify([filename, ...path]);
    const id = !Array.isArray(value) && (idFields.map(name => value?.[name]).find(id => typeof id === 'string') || '');
    const node = { key, id, value, filename, path, label, parent, children: [] };
    nodes.set(key, node);
    node.children = Object.entries(value || {}).filter(([, item]) => isGroup(item)).map(([name, item]) => {
      const childLabel = Array.isArray(value)
        ? itemLabel(item, Number(name))
        : field(name);
      return nodeFor(item, filename, [...path, name], childLabel, node);
    });
    return node;
  }

  const roots = Object.entries(project.models).filter(([name, value]) => name !== 'manifest.json' && isGroup(value))
    .sort(([, a], [, b]) => {
      const rank = model => order.includes(model.model_type) ? order.indexOf(model.model_type) : order.length;
      return rank(a) - rank(b);
    })
    .map(([filename, value]) => nodeFor(value, filename, [], titleFor(filename, value), null));
  if (!roots.length) return;
  let selected = nodes.get(view.selected);
  if (!selected) {
    const groups = roots[0].children;
    const group = groups.find(node => ['aggregates', 'entities'].includes(node.path[0])) || groups[0];
    selected = group?.children[0] || group || roots[0];
    view.selected = selected.key;
    for (let parent = selected.parent; parent; parent = parent.parent) view.expanded.add(parent.key);
  }

  function nodeButton(node, showId = false) {
    return `<button type="button" class="live-ontology-node" data-action="ontology-node" data-node="${escape(node.key)}" aria-current="${node.key === view.selected}"><span>${escape(node.label)}</span>${showId && node.id && node.id !== node.label ? `<small>${escape(node.id)}</small>` : ''}</button>`;
  }
  function branch(node, query) {
    const scalars = Object.entries(node.value).filter(([, value]) => !isGroup(value));
    const searchable = [node.label, node.id, ...scalars.flatMap(([key, value]) => [key, valueText(value)])];
    const match = query && searchable.some(text => String(text).toLocaleLowerCase().includes(query));
    const children = node.children.map(child => branch(child, match ? '' : query)).join('');
    if (query && !match && !children) return '';
    const button = nodeButton(node, true);
    return children
      ? `<li><details data-branch="${escape(node.key)}" ${query || view.expanded.has(node.key) ? 'open' : ''}><summary aria-label="${escape(node.label)}">${button}</summary><ul>${children}</ul></details></li>`
      : `<li>${button}</li>`;
  }
  function renderTree() {
    const query = search.value.trim().toLocaleLowerCase();
    tree.innerHTML = roots.map(node => branch(node, query)).join('') || '<li class="live-subtle">未找到匹配节点</li>';
  }
  function renderDetail() {
    const fields = Object.entries(selected.value).filter(([, value]) => !isGroup(value));
    const trail = [];
    for (let parent = selected.parent; parent; parent = parent.parent) trail.unshift(parent.label);
    const rows = selected.children.map(child => `<tr><th scope="row">${nodeButton(child)}</th><td>${escape(child.id || '—')}</td></tr>`).join('');
    const properties = fields.map(([key, value]) => `<div><dt>${escape(field(key))}</dt><dd>${escape(valueText(value))}</dd></div>`).join('');
    const canEdit = editable && !Array.isArray(selected.value) && ontologyEditableFields(selected.value).length > 0;
    detail.innerHTML = `<header><div><p class="live-ontology-breadcrumb">${escape(trail.join(' / ') || selected.filename)}</p><h2 id="ontology-node-title" tabindex="-1">${escape(selected.label)}</h2></div>${canEdit ? '<button type="button" data-ontology-edit>编辑属性</button>' : ''}</header>
      ${properties ? `<section aria-label="基本信息"><h3>基本信息</h3><dl class="live-ontology-fields">${properties}</dl></section>` : ''}
      ${rows ? `<section class="live-ontology-children"><h3>下级定义</h3><div class="table-wrap"><table><thead><tr><th scope="col">名称</th><th scope="col">编号</th></tr></thead><tbody>${rows}</tbody></table></div></section>` : ''}
      ${!properties && !rows ? '<p>此节点暂无定义。</p>' : ''}`;
  }
  tree.addEventListener('click', event => {
    const disclosure = event.target.closest('summary');
    if (!disclosure || view.query.trim()) return;
    const branch = disclosure.parentElement;
    if (event.target.closest('[data-node]')) return;
    if (branch.open) view.expanded.delete(branch.dataset.branch);
    else view.expanded.add(branch.dataset.branch);
  });
  panel.addEventListener('click', event => {
    if (event.target.closest('[data-ontology-edit]')) {
      onEdit(selected.filename, selected.path, selected.value, selected.label, field);
      return;
    }
    const node = nodes.get(event.target.closest('[data-node]')?.dataset.node);
    if (!node) return;
    event.preventDefault();
    const fromDetail = detail.contains(event.target);
    selected = node;
    view.selected = node.key;
    const branch = tree.querySelector(`[data-branch="${CSS.escape(node.key)}"]`);
    if (branch && !branch.open) {
      branch.open = true;
      view.expanded.add(node.key);
    }
    tree.querySelector('[aria-current="true"]')?.setAttribute('aria-current', 'false');
    for (const item of tree.querySelectorAll('[data-node]')) {
      if (item.dataset.node === node.key) {
        item.setAttribute('aria-current', 'true');
        for (let parent = item.parentElement; parent && parent !== tree; parent = parent.parentElement) {
          if (parent.tagName === 'DETAILS') {
            parent.open = true;
            view.expanded.add(parent.dataset.branch);
          }
        }
        if (fromDetail) item.scrollIntoView({ block: 'nearest' });
        break;
      }
    }
    renderDetail();
    if (fromDetail) detail.querySelector('h2').focus();
  });
  search.value = view.query;
  search.addEventListener('input', () => {
    view.query = search.value;
    renderTree();
  });
  renderTree();
  renderDetail();
}
