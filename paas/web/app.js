import { mountGraph } from './live-graph.js';
import { mountMapping } from './live-mapping.js';
import { mountReasoning } from './live-reasoning.js?v=ui-system';
import { buildMetricInsights, metricDefinitions, parseMetricDefinitions } from './metric-insights.js';
import { mountScenarioInsight } from './live-scenario-insight.js?v=ui-system';
import { bindConversationSessionMenu } from './conversation-session-menu.js';
import { conversationSession, updateConversationSession } from './conversation-session-state.js';
import { mountBusinessOntology, ontologyEditableFields } from './business-ontology.js';
import './elastic-textarea.js';

const root = document.querySelector('#app');
const overlay = document.querySelector('#overlay');
const prototypeEditorDialog = document.querySelector('#prototype-editor-dialog');
const API = '/api/v1';
const PLATFORM_NAME = 'Onto PaaS';
const CHAT_ATTACHMENT_ID = 'chat-attachments';
const CHAT_ATTACHMENT_MAX_BYTES = 120000;
const CHAT_MESSAGE_LIMIT = 30000;
const SEVEN_MODEL_STANDARD = 'onto-paas-seven-v1';
const DATA_BINDING_FILE = 'data_mapping.yaml';
const PROTOTYPE_GENERATION_JOB = '生成或修订界面原型';
const PROTOTYPE_EDIT_JOB = 'AI 修订界面原型';
const CONTRACT_REQUIREMENTS_DIALOGUE = [
  {
    role: 'user',
    content: `帮我梳理一套销售合同执行管理系统的需求。

我们现在是销售和财务各自用表格跟合同，月底要反复核对还有多少能开票、开过的票回了多少钱。系统从销售合同线下签字盖章并生效后开始管，覆盖登记、审批、开票、收款、结清和归档。合同起草、签署、采购合同管理和银行资金处理都不在范围内，也不接外部系统。

销售或财务登记合同编号、名称、客户、产品、部门、责任人、合同类型、签约日期、含税总额、对外采购金额和税率。合同至少有一个付款阶段，每期录编号、名称和付款比例，系统按合同总额计算阶段应付金额，各期比例加起来必须是 100%。合同审批通过后才可以开票。

客户通知付款后，财务登记开票和分摊明细，再提交审批。同一期可以分几次开票，一张票也可以分摊到同一合同的多个阶段。财务核实到账后，按已批准且未作废的开票逐笔登记金额、时间和方式。一张票可以分几次收款，录错了要能冲销并保留原记录。开票、收款状态和合同结清状态要自动更新。

产品、客户、部门和人员在本系统维护。要区分销售、财务、财务经理、总经理和普通员工，普通员工只能查本部门。需要合同台账和详情、执行进度、部门汇总，以及已开票未收款清单，能按客户、部门或日期筛选。合同登记、开票分摊、收款登记、审批待办和报表希望有可点击的原型。`,
  },
  {
    role: 'assistant',
    content: `业务范围和主流程已经明确，可以按“生效合同登记 → 内部审批 → 开票分摊 → 收款 → 结清归档”来整理。还有四项规则会影响审批去向、金额校验和报表口径，请确认建议是否符合你们的实际做法。

- **Q-01 谁审批合同和开票？** 建议合同由财务经理先审，合同总额达到 100 万元（含）再转总经理。开票由财务经理审批，提交人不能审批自己的单据。
- **Q-02 开票分摊怎么校验？** 建议每张票的分摊合计等于开票金额，各期累计有效分摊不超过该期应付金额，开票税率与合同一致。
- **Q-03 合同什么时候算结清？** 建议累计有效收款不能超过对应开票金额。各期足额开票且所有有效开票收齐后自动结清。因冲销或作废不再满足条件时恢复为已纳入管理，已归档的合同除外。
- **Q-04 未收款清单是否包括部分收款？** 建议包括，只统计已批准且未作废的开票。未收金额等于开票金额减去未冲销收款合计。`,
  },
  {
    role: 'user',
    content: `我确认这四条，按下面的口径出需求文档。

- **Q-01** 合同先由财务经理审批，100 万元及以上再交总经理。开票只由财务经理审批，提交人不能审批自己的单据。
- **Q-02** 分摊合计要等于开票金额，各期累计有效分摊不能超出应付金额，开票税率按合同税率。
- **Q-03** 累计有效收款不能超过开票金额。所有付款阶段足额开票、所有有效开票收齐后自动结清。冲销或作废后不再满足条件的，恢复为已纳入管理。已归档的合同不自动恢复。
- **Q-04** 未收款清单包含部分收款的票，只列已批准且未作废的开票，未收金额按开票金额减去未冲销收款合计。`,
  },
  { role: 'assistant', content: '已把你确认的四项规则写进规格说明书。文档包含业务范围、功能规则、审批与状态流程、报表口径及角色权限。' },
];
const PROTOTYPE_WORKBENCH_TEMPLATES = new Set([
  'ontology-v9-workbench-1', 'ontology-v9-workbench-2', 'ontology-v9-workbench-3', 'ontology-v9-workbench-4',
  'ontology-v9-workbench-5', 'ontology-v9-workbench-6', 'ontology-v9-workbench-7',
]);
const ANALYSIS_IMPORT_FILES = [
  'm1_object_model.yaml',
  'm2_behavior_model.yaml',
  'm3_rule_model.yaml',
  'm4_scenario_model.yaml',
  'm_metric_model.yaml',
  DATA_BINDING_FILE,
];
const stageNames = [
  '总体理解',
  '业务对象',
  '功能与规则',
  '跨对象联动',
  '协同与审批',
  '查询统计',
  '角色权限',
  '界面与操作',
];
const routeInfo = {
  build: ['应用开发', 'business'],
  builder: ['需求探索', 'business'],
  requirements: ['需求文档', 'business'],
  interface: ['界面原型', 'business'],
  models: ['业务本体', 'business'],
  files: ['模型文件', 'business'],
  'model-graph': ['知识图谱', 'business'],
  resources: ['数据库资源', 'analysis'],
  data1: ['数据文档', 'analysis'],
  data2: ['数据本体', 'analysis'],
  'data-graph': ['知识图谱', 'analysis'],
  'data-mapping': ['数据映射', 'analysis'],
  metrics: ['指标分析', 'analysis'],
  data3: ['场景分析', 'analysis'],
  data4: ['分析报告', 'analysis'],
  analytics: ['本体推理', 'analysis'],
  knowledge: ['知识库', 'knowledge'],
  'knowledge-models': ['知识模型', 'knowledge'],
  settings: ['平台设置', null],
  publish: ['应用发布', 'business'],
};
const aliases = {
  apps: 'build',
  'requirements-detail': 'requirements',
  'model-detail': 'models',
  'data2-detail': 'data2',
  'analytics-chat': 'analytics',
  'publish-detail': 'publish',
  'knowledge-workspace': 'knowledge',
  architecture: 'settings',
  techbase: 'settings',
  'ui-styles': 'settings',
};
let saved = {};
try {
  saved = JSON.parse(localStorage.getItem('onto-paas-selection-v2') || '{}');
} catch {}
let drafts = {};
try {
  drafts = JSON.parse(sessionStorage.getItem('onto-paas-inputs-v2') || '{}');
} catch {}
const initialPrototypeRun = prototypeRunProject();
const initialRoute = initialPrototypeRun ? 'interface' : normalizeRoute(location.hash.slice(1));
const initialSection = routeGroup(initialRoute);
const state = {
  route: initialRoute,
  selected: saved,
  project: null,
  projects: [],
  provider: null,
  status: null,
  loading: true,
  busy: false,
  error: '',
  activeJob: null,
  scenarioOptions: [],
  menu: false,
  openSections: new Set(initialSection ? [initialSection] : []),
  modelFile: '',
  modelFileOpen: false,
  prototypeDetails: null,
  prototypeEditor: null,
  prototypeRun: initialPrototypeRun,
  enteredPrototype: null,
  projectFilter: '',
  polls: 0,
};
const e = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
const escAttr = e;
const uploadSelections = new Map();
const submittedInputs = new Map();
const scenarioJobWaiters = new Map();
const metricDocuments = new Map();
const metricInsights = new Map();
const prototypeAssistantRequests = new WeakSet();
function normalizeRoute(route) {
  return aliases[route] || (routeInfo[route] ? route : 'build');
}
function prototypeRunProject() {
  const [page, search] = location.hash.slice(1).split('?', 2);
  return page === 'prototype-run' ? new URLSearchParams(search).get('project') : null;
}
function info() {
  return routeInfo[state.route] || routeInfo.build;
}
function kind() {
  return info()[1] || state.project?.kind || 'business';
}
function projectId() {
  return state.project?.id;
}
function url(path) {
  return API + path;
}
function projectUrl(path = '') {
  return `/projects/${encodeURIComponent(projectId())}${path}`;
}
function draftKey(name) {
  return `${projectId() || 'none'}:${name}`;
}
function draft(name, initial = '') {
  return Object.hasOwn(drafts, draftKey(name)) ? drafts[draftKey(name)] : initial;
}
function saveDraft(name, value) {
  drafts[draftKey(name)] = value;
  persistDrafts();
}
function button(label, action, attributes = '', primary = false) {
  return `<button type="button" ${attributes} data-action="${action}"${primary ? ' class="primary"' : ''}>${label}</button>`;
}
function pill(text, tone = '') {
  return `<span class="live-pill ${tone}">${e(text)}</span>`;
}
function icon(name) {
  const d = {
    apps: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    chat: '<path d="M21 11a8 8 0 0 1-8 8H5l-3 3V11a8 8 0 0 1 8-8h3a8 8 0 0 1 8 8Z"/><path d="M7 9h10M7 13h7"/>',
    chart: '<path d="M4 3v17h17M8 16v-5M13 16V6M18 16V9"/>',
    book: '<path d="M12 5C9 2 5 2 2 4v15c4-2 7-1 10 1 3-2 6-3 10-1V4c-4-2-7-1-10 1Z"/><path d="M12 5v15"/>',
    settings:
      '<circle cx="12" cy="12" r="4"/><path d="m12 2 2 3 3-1 1 4 4 2-2 3 1 3-4 1-2 4-3-2-3 1-1-4-4-2 2-3-1-3 4-1Z"/>',
    refresh:
      '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12-1l2 6M4 12l2 6a7 7 0 0 0 12-1"/>',
    file: '<path d="M14 2H5v20h14V7Z"/><path d="M14 2v6h5M8 12h8M8 16h6"/>',
    attach: '<path d="m9 13 6-6a3 3 0 0 1 4 4l-8 8a5 5 0 0 1-7-7l8-8M8 15a1.5 1.5 0 0 0 2 2l7-7"/>',
    pin: '<path d="M9 4h6m-5 0v6l-3 3v2h10v-2l-3-3V4m-2 11v6"/>',
    menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    plus: '<path d="M12 4v16M4 12h16"/>',
    send: '<path d="M12 19V5m-6 6 6-6 6 6"/>',
    stop: '<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" stroke="none"/>',
    arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    eye: '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6Z"/><circle cx="12" cy="12" r="2.5"/>',
    edit: '<path d="M13 5H5v14h14v-8m-2-8 4 4-8 8-5 1 1-5 8-8Z"/>',
    run: '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M9 21h6m-3-4v4m-2-14 5 3.5-5 3.5V7Z"/>',
    trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 14h12l1-14M10 10v6m4-6v6"/>',
  };
  return `<span class="icon"><svg viewBox="0 0 24 24" aria-hidden="true">${d[name] || d.file}</svg></span>`;
}
function routeGroup(route) {
  return routeInfo[route]?.[1] || null;
}
function navItem(route, name) {
  return `<button type="button" class="nav-item sub ${state.route === route ? 'active' : ''}" data-action="nav" data-route="${route}"${state.route === route ? ' aria-current="page"' : ''}>${e(name)}</button>`;
}
function navGroup(key, title, symbol, routes) {
  const isOpen = state.openSections.has(key);
  return `<section class="nav-group"><button type="button" class="nav-item nav-group-heading" data-action="section" data-section="${key}" aria-expanded="${isOpen}" aria-controls="nav-${key}">${icon(symbol)}<span>${title}</span><i aria-hidden="true">${isOpen ? '⌄' : '›'}</i></button><div id="nav-${key}" ${isOpen ? '' : 'hidden'}>${routes.map(([route, label]) => navItem(route, label)).join('')}</div></section>`;
}
function sidebar() {
  return `<aside class="sidebar ui-navigation" id="workspace-sidebar"><div class="brand ui-brand"><span class="ui-brand-mark" aria-hidden="true">O</span><strong>${PLATFORM_NAME}</strong></div><nav class="nav-scroll" aria-label="工作台导航">${navGroup(
    'business',
    '应用中心',
    'apps',
    [
      ['builder', '需求探索'],
      ['requirements', '需求文档'],
      ['interface', '界面原型'],
      ['models', '业务本体'],
      ['files', '模型文件'],
      ['model-graph', '知识图谱'],
      ['build', '应用开发'],
      ['publish', '应用发布'],
    ],
  )}${navGroup('analysis', '数据分析中心', 'chart', [
    ['resources', '数据库资源'],
    ['data1', '数据文档'],
    ['data2', '数据本体'],
    ['data-graph', '知识图谱'],
    ['data-mapping', '数据映射'],
    ['metrics', '指标分析'],
    ['data3', '场景分析'],
    ['data4', '分析报告'],
    ['analytics', '本体推理'],
  ])}${navGroup('knowledge', '知识中心', 'book', [
    ['knowledge', '知识库'],
    ['knowledge-models', '知识模型'],
  ])}<div class="nav-divider"></div><button type="button" class="nav-item${state.route === 'settings' ? ' active' : ''}" data-action="nav" data-route="settings"${state.route === 'settings' ? ' aria-current="page"' : ''}>${icon('settings')}<span>平台设置</span></button></nav></aside>`;
}
function providerLabel() {
  return state.provider?.configured
    ? `${state.provider.name || '模型服务'} · ${state.provider.model || '已配置'}`
    : '模型服务未配置';
}
function heading(actions = '') {
  const [title] = info();
  const provider = e(providerLabel());
  return `<header class="page-heading ui-topbar">
    <button type="button" class="mobile-menu" id="workspace-menu" data-action="menu" aria-label="切换导航" aria-controls="workspace-sidebar" aria-expanded="${state.menu}">${icon('menu')}</button>
    <div class="page-heading-copy"><h1 tabindex="-1">${e(title)}</h1></div>
    ${actions ? `<div class="heading-actions">${actions}</div>` : ''}
    <div class="live-heading-provider" title="${provider}"><span class="live-provider-dot ${state.provider?.configured ? 'ready' : ''}" aria-hidden="true"></span><span>${provider}</span></div>
  </header>${state.error ? `<div class="live-error" role="alert"><strong>操作未完成</strong><span>${e(state.error)}</span>${button('关闭', 'dismiss-error')}</div>` : ''}${jobPanel()}`;
}
function sectionNav(route, label, primary = false) {
  return button(label, 'nav', `data-route="${route}"`, primary);
}
function projectPicker({ showNew = true } = {}) {
  const items = state.projects.filter((p) => p.kind === kind());
  return `<div class="live-project-toolbar"><label for="project-select">当前${kind() === 'business' ? '应用' : kind() === 'analysis' ? '分析' : '知识库'}项目</label><select id="project-select" data-input="project-select" aria-label="选择项目"><option value="">选择项目</option>${items.map((p) => `<option value="${e(p.id)}" ${p.id === projectId() ? 'selected' : ''}>${e(p.name)}</option>`).join('')}</select><span class="live-revision">${state.project ? '修订 ' + e(state.project.revision ?? 0) : ''}</span><div class="live-project-spacer"></div>${showNew ? button(`${icon('plus')}新建`, 'new-project', `data-kind="${kind()}"`) : ''}${button(icon('refresh'), 'refresh', 'aria-label="刷新项目" title="刷新项目"')}</div>`;
}
function emptyProject(type = kind()) {
  return `<div class="panel live-empty"><div class="live-empty-icon">${icon(type === 'business' ? 'apps' : type === 'analysis' ? 'chart' : 'book')}</div><h2>${type === 'business' ? '从一项业务需求开始' : type === 'analysis' ? '连接数据，说明你要分析的问题' : '把业务文档组织成可引用的知识'}</h2><p>${type === 'business' ? '创建项目，用对话梳理对象、规则、流程和权限。' : type === 'analysis' ? '创建分析项目后，上传数据库并确认分析口径。' : '创建知识库，导入原文后执行两轮知识萃取。'}</p>${button('新建' + (type === 'business' ? '应用项目' : type === 'analysis' ? '分析项目' : '知识库'), 'new-project', `data-kind="${type}"`, true)}</div>`;
}
function hasModels(p = state.project) {
  return p?.models && Object.keys(p.models).filter((n) => n !== 'manifest.json').length > 0;
}
function hasBuild(p = state.project) {
  if (p?.build?.stale || p?.build?.invalidated) return false;
  return !!(
    p?.build &&
    (p.build.output_dir ||
      p.build.build_dir ||
      p.build.entry ||
      p.build.url ||
      p.build.app_url ||
      p.build.status === 'succeeded' ||
      p.build.status === 'ready')
  );
}
function latestRelease(p = state.project) {
  const releases = p?.releases || [];
  return releases.find((release) => release.id === p.published_release_id) || releases.at(-1);
}
function candidateUrl(p = state.project) {
  return (
    p?.build?.preview_url ||
    p?.build?.url ||
    p?.build?.app_url ||
    `/app/${encodeURIComponent(p.id)}/`
  );
}
function canGenerateModels(p = state.project) {
  return (
    !!p?.requirements &&
    confirmed('requirements', p) &&
    (p.kind !== 'business' || (!p.requirements_confirmation?.prototype_only && confirmed('prototype', p) &&
      (!!p.prototype || p.prototype_confirmation?.not_applicable === true)))
  );
}
function canConfirmModels() {
  const p = state.project;
  return (
    hasModels() &&
    !p.model_diagnostics?.length &&
    (p.kind !== 'analysis' || p.model_standard !== SEVEN_MODEL_STANDARD ||
      (!!p.source && confirmed('requirements'))) &&
    (p.kind !== 'business' ||
      (confirmed('prototype') && p.models_prototype_sha256 === (p.prototype?.sha256 || null)))
  );
}
function persistDrafts() {
  try {
    sessionStorage.setItem('onto-paas-inputs-v2', JSON.stringify(drafts));
  } catch {}
}
function dirtyModelFiles() {
  return editableModelFiles().filter(
    ([name, model]) =>
      Object.hasOwn(drafts, draftKey('model:' + name)) &&
      drafts[draftKey('model:' + name)] !== JSON.stringify(model, null, 2),
  );
}
function discardModelDraft(name) {
  delete drafts[draftKey('model:' + name)];
  delete drafts[draftKey('model-revision:' + name)];
  persistDrafts();
}
async function saveModelDraft(name = state.modelFile) {
  const currentModel = editableModelFiles().find(([filename]) => filename === name)?.[1];
  const value = draft('model:' + name, JSON.stringify(currentModel, null, 2));
  const revision = Number(draft('model-revision:' + name, state.project.revision));
  const previousRevision = state.project.revision;
  state.project = await api(projectUrl('/models/' + encodeURIComponent(name)), {
    method: 'PUT',
    body: JSON.stringify({ text: value, revision }),
  });
  discardModelDraft(name);
  if (revision === previousRevision) {
    for (const [other] of dirtyModelFiles()) {
      if (draft('model-revision:' + other) === previousRevision)
        saveDraft('model-revision:' + other, state.project.revision);
    }
  }
  await refreshProjects();
}
let pendingModelNavigation = null;
function guardModelDrafts(next) {
  const dirty = dirtyModelFiles();
  if (!dirty.length) return false;
  pendingModelNavigation = next;
  modal(
    '保存模型修改后继续',
    `<p>当前项目有 ${dirty.length} 个模型文件尚未保存。保存并校验成功后继续，或放弃这些本地修改。</p><ul>${dirty.map(([name]) => `<li>${e(name)}</li>`).join('')}</ul><p class="live-subtle">选择继续编辑会保留所有输入。</p>`,
    button('继续编辑', 'close-modal') +
      button('放弃修改并继续', 'discard-model-and-continue') +
      button('保存并继续', 'save-model-and-continue', '', true),
  );
  return true;
}
function syncModelDirtyState() {
  const dirty = dirtyModelFiles().length > 0;
  root.querySelectorAll('[data-requires-saved]').forEach((el) => {
    el.disabled = state.busy || dirty || el.dataset.blocked === 'true';
  });
  const notice = root.querySelector('.live-model-dirty');
  if (notice) notice.hidden = !dirty;
}
function hasRequirements() {
  return !!state.project?.requirements;
}
function confirmed(scope, p = state.project) {
  if (!p) return false;
  for (const field of [`${scope}_confirmation`, `${scope}_confirmed`]) {
    if (Object.hasOwn(p, field)) return !!p[field];
  }
  const c = Array.isArray(p.confirmations) ? p.confirmations : Object.values(p.confirmations || {});
  return c.some(
    (v) =>
      typeof v === 'object' && v.scope === scope && v.revision === p.revision && v.valid !== false,
  );
}
function adaptiveRequirements(project = state.project) {
  return state.status?.capabilities?.adaptive_requirements === true && project?.requirements_workflow !== 'staged';
}
function prdReview(project) {
  return project?.prd_exploration?.prd || project?.prd_exploration || project?.current_stage_result?.prd;
}
function prdSaved(project) {
  const review = prdReview(project);
  return !!project?.requirements && review?.draft === project.requirements &&
    project.requirements_document?.source === 'generated' &&
    project.requirements_document.draft === !review.can_finalize;
}
function canGenerateRequirements(project = state.project) {
  if (adaptiveRequirements() && (project?.reference_adoption || project?.prd_stale)) return false;
  if (adaptiveRequirements() && prdReview(project)) return prdReview(project).can_finalize === true && !prdSaved(project);
  if (adaptiveRequirements() && project?.requirements_workflow === 'adaptive') return false;
  if (adaptiveRequirements() && !state.provider?.configured) return false;
  return (
    Number(project?.stage || 0) >= 8 || project?.stages_complete || project?.requirements_ready
  );
}
function prdGenerationNotice(project) {
  if (jobPending(pageJob())) return '正在处理当前任务。';
  if (canGenerateRequirements(project)) return '';
  if (!project) return '发送业务背景或添加附件，开始澄清需求。';
  if (project.reference_adoption) return '此会话已有原版需求文档，可在需求文档页预览。';
  if (project.prd_stale) return '需求输入已更新，请继续对话，重新核对需求草稿。';
  if (prdSaved(project)) return '当前需求已保存，可在需求文档页查看。';
  if (project.requirements && !prdReview(project)) return '已有需求文档，可在需求文档页预览或继续对话补充。';
  if (!state.provider?.configured) return '配置模型服务后即可继续需求探索。';
  if (prdReview(project)) {
    const unresolved = prdReview(project).questions?.filter((question) => question.status === 'open').length || 0;
    return unresolved ? `还有 ${unresolved} 项待澄清，请在下方补充。` : '请在对话中继续核对需求范围和草稿。';
  }
  return adaptiveRequirements()
    ? '请先发送业务资料，待 Agent 整理出可核对的需求草稿。'
    : `已确认 ${Math.min(Number(project.stage) || 0, 8)} / 8 个阶段，完成剩余阶段即可生成。`;
}
function messageContent(m) {
  return m.content ?? m.text ?? m.reply ?? m.message ?? '';
}
function chatSource(message) {
  const content = messageContent(message);
  const filename = message.attachments?.[0]?.name;
  if (!filename || typeof content !== 'string') return { content, extracted: '' };
  const marker = `附件《${filename}》\n`;
  const index = content.startsWith(marker) ? 0 : content.indexOf(`\n\n${marker}`);
  if (index < 0) return { content, extracted: '' };
  return { content: content.slice(0, index), extracted: content.slice(index).trimStart() };
}
function markdown(raw) {
  if (typeof raw === 'object') raw = JSON.stringify(raw, null, 2);
  const blocks = String(raw || '').split(/(```[\s\S]*?```)/g);
  return blocks
    .map((block) => {
      if (block.startsWith('```'))
        return `<pre class="live-code">${e(block.replace(/^```[^\n]*\n?/, '').replace(/```$/, ''))}</pre>`;
      return markdownLines(block);
    })
    .join('');
}
function markdownCells(line) {
  const delimited = line.trim();
  if (!delimited.startsWith('|') || !delimited.endsWith('|')) return [];
  return delimited.slice(1, -1).split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, '|'));
}
function markdownLines(block) {
  const lines = block.split(/\r?\n/);
  const parts = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const header = markdownCells(line);
    const divider = markdownCells(lines[index + 1] || '');
    if (header.length > 1 && divider.length === header.length && divider.every((cell) => /^:?-{3,}:?$/.test(cell))) {
      const rows = [];
      index += 1;
      while (index + 1 < lines.length) {
        const cells = markdownCells(lines[index + 1]);
        if (cells.length !== header.length) break;
        rows.push(`<tr>${cells.map((cell) => `<td>${inline(cell)}</td>`).join('')}</tr>`);
        index += 1;
      }
      parts.push(`<div class="table-wrap"><table class="live-md-table"><thead><tr>${header.map((cell) => `<th scope="col">${inline(cell)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`);
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      const level = Math.min(4, heading[1].length + 1);
      parts.push(`<h${level}>${inline(heading[2])}</h${level}>`);
    } else if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      parts.push('<hr>');
    } else if (/^\s*[-*]\s/.test(line)) {
      parts.push(`<p class="live-md-bullet">• ${inline(line.replace(/^\s*[-*]\s/, ''))}</p>`);
    } else parts.push(line ? `<p>${inline(line)}</p>` : '<div class="live-md-space"></div>');
  }
  return parts.join('');
}
function inline(text) {
  return e(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}
function analysisRequirementContent(value) {
  let requirement = value;
  if (typeof requirement === 'string') {
    try {
      requirement = JSON.parse(requirement);
    } catch {
      return markdown(value);
    }
  }
  if (!requirement || !Array.isArray(requirement.analysis_goals)) return markdown(value);
  return `<div class="live-analysis-requirements"><h3>分析目标</h3>${requirement.analysis_goals.map((goal) => `<article><strong>${e(goal.title || goal.id)}</strong><p>${e(goal.description || '')}</p></article>`).join('')}${requirement.time_range ? `<p><strong>时间范围：</strong>${e(requirement.time_range)}</p>` : ''}${requirement.data_objects?.length ? `<h3>分析对象</h3><div class="live-analysis-objects">${requirement.data_objects.map((object) => `<span>${e(object.name)}<small>${e((object.hint_tables || []).join(' / '))}</small></span>`).join('')}</div>` : ''}${requirement.metrics?.length ? `<h3>指标与计算口径</h3>${requirement.metrics.map((metric) => `<article><strong>${e(metric.name)}</strong><p>${e(metric.definition || '')}</p></article>`).join('')}` : ''}${requirement.ai_questions?.length ? `<div class="live-analysis-questions"><h3>需要确认的问题</h3><ol>${requirement.ai_questions.map((question) => `<li>${e(question)}</li>`).join('')}</ol><p>请在下方对话中补充答案，再核对更新后的需求。</p></div>` : ''}</div>`;
}
function prdTurn(prd) {
  if (!prd || typeof prd !== 'object') return '';
  const findings = [
    ['业务概况', prd.overview], ['端到端业务流程', prd.process], ['业务域', prd.domains],
    ['业务对象', prd.objects], ['冲突和歧义', prd.conflicts],
  ].filter(([, items]) => items?.length).map(([title, items]) =>
    `<section><h3>${title}</h3><ul>${items.map((item) => `<li>${inline(item)}</li>`).join('')}</ul></section>`,
  ).join('');
  const questions = prd.questions?.length ? `<section class="prd-turn-questions"><h3>澄清记录</h3>
    <div class="table-wrap"><table class="live-md-table"><thead><tr><th scope="col">编号</th><th scope="col">问题</th><th scope="col">影响</th><th scope="col">建议</th><th scope="col">状态</th></tr></thead><tbody>${prd.questions.map((q) => `<tr><th scope="row">${e(q.id)}</th><td>${inline(q.question)}</td><td>${inline(q.impact)}</td><td>${inline(q.recommendation)}${q.reason ? `<small>${inline(q.reason)}</small>` : ''}${q.alternatives?.length ? `<small>其他选项：${q.alternatives.map(inline).join('、')}</small>` : ''}</td><td>${pill(({ open: '待澄清', resolved: '已澄清', deferred: '暂缓' })[q.status] || q.status, q.status === 'resolved' ? 'green' : q.status === 'open' ? 'amber' : '')}${q.answer ? `<small>${inline(q.answer)}</small>` : ''}${q.evidence ? `<small>${inline(q.evidence)}</small>` : ''}</td></tr>`).join('')}</tbody></table></div>
    ${prd.questions.some((q) => q.status === 'open') ? '<p class="live-subtle">可直接按编号回答，也可以补充资料或修改建议。</p>' : ''}</section>` : '';
  const coverage = prd.coverage?.length ? `<details class="prd-turn-coverage"><summary>需求覆盖与依据</summary><ul>${prd.coverage.map((item) => `<li><strong>${e(item.stage_name || stageNames[item.stage] || item.stage)}</strong>${pill(({ covered: '已覆盖', open: '待补充', not_applicable: '本期不涉及' })[item.status] || item.status, item.status === 'covered' ? 'green' : '')}${item.evidence ? `<p>${inline(item.evidence)}</p>` : ''}</li>`).join('')}</ul></details>` : '';
  const document = prd.draft ? `<details class="live-chat-document prd-turn-draft" ${prd.can_finalize ? 'open' : ''}><summary>需求草稿</summary><article class="live-document">${markdown(prd.draft)}</article></details>` : '';
  return `<div class="prd-turn">${findings}${questions}${coverage}${document}</div>`;
}
function chatMessage(p, message, document = '') {
  const user = message.role === 'user';
  const displayName = user ? '你' : message.role === 'system' ? '系统记录' :
    p.kind === 'business' ? '需求 Agent' : PLATFORM_NAME;
  const source = chatSource(message);
  const content = p.kind === 'analysis' && message.role === 'assistant'
    ? analysisRequirementContent(source.content)
    : markdown(source.content);
  const attachments = message.attachments?.length ? `<ul class="prd-message-attachments">${message.attachments.map((file) => `<li>${icon('attach')} ${e(file.name)}</li>`).join('')}</ul>` : '';
  const extracted = source.extracted ? `<details class="prd-attachment-text"><summary>查看附件提取文本</summary>${markdown(source.extracted)}</details>` : '';
  return `<article class="live-message ${user ? 'user' : 'assistant'}"><div class="live-message-role">${message.initial ? '项目需求' : displayName}${message.stage !== undefined && !message.prd ? ` <span>${e(stageNames[message.stage] || '需求整理')}</span>` : ''}</div>${attachments}<div class="live-message-text">${content}${extracted}${message.prd ? prdTurn(message.prd) : ''}${document}</div></article>`;
}
function showsContractRequirementsDialogue(project) {
  const source = project?.upstream_import?.source;
  return project?.kind === 'business' && typeof project.requirements === 'string' && !!project.requirements.trim() &&
    project.requirements_document?.source === 'legacy' &&
    source?.repository === 'https://github.com/sharptoolbox/ontology-driven-dev' &&
    source.source_folder === 'reference-example' &&
    source.commit === '08686470f7af0a9095a000744d69e28c80f74534';
}
function contractRequirementsDocument(project) {
  const document = project.requirements;
  const nextSection = document.indexOf('\n## 1.2 ');
  const preview = nextSection > 0 ? document.slice(0, nextSection) : '';
  const remaining = nextSection > 0 ? document.slice(nextSection + 1) : document;
  return `<section class="live-chat-requirements" aria-label="需求文档">
    ${preview ? `<article class="live-document">${markdown(preview)}</article>` : ''}
    <details>
      <summary>${preview ? '展开其余章节' : e(requirementTitle(project))}</summary>
      <article class="live-document">${markdown(remaining)}</article>
    </details>
  </section>`;
}
function chatHistory(p, messages, demo) {
  if (demo) return [...CONTRACT_REQUIREMENTS_DIALOGUE, ...messages.filter((message) => message.role !== 'system')];
  if (p.description && !messages.some((m) => m.role === 'user' &&
      (chatSource(m).content === p.description || (m.attachments?.length && p.description === `需求附件：${m.attachments.map((file) => file.name).join('、')}`))))
    return [{ role: 'user', content: p.description, initial: true }, ...messages];
  return messages;
}
function isLiveRequirementsReply(job) {
  return state.route === 'builder' && job?.kind === '需求探索' && jobPending(job);
}
function liveRequirementsStatus(job) {
  if (job.cancel_requested) return '正在取消';
  if (job.cancellation_error) return job.cancellation_error;
  if (job.observation_error) return '正在重新连接';
  if (job.status === 'queued') return '等待回复';
  const purpose = job.progress?.purpose || '';
  if (purpose.startsWith('business.requirements.evidence-review')) return '正在核对需求依据';
  if (purpose.startsWith('business.prd.complete')) return '正在编写需求文档';
  return job.publicReply ? '正在回复' : '正在梳理需求';
}
function liveRequirementsReply(job) {
  return `<article class="live-message assistant live-streaming-message" data-job-id="${e(job.id)}">
    <div class="live-message-role">需求 Agent</div>
    <div class="live-reply-activity">
      <span class="live-job-indicator running" aria-hidden="true"></span>
      <span data-reply-status role="status">${liveRequirementsStatus(job)}</span>
    </div>
    <div class="live-message-text live-reply-preview" data-reply-preview ${job.publicReply ? '' : 'hidden'}>${e(job.publicReply)}</div>
  </article>`;
}
function updateLiveRequirementsReply(job) {
  if (!isLiveRequirementsReply(job)) return false;
  const block = root.querySelector('.live-streaming-message');
  if (block?.dataset.jobId !== job.id) return false;
  const scroller = block.closest('.conversation-scroll');
  const following = scroller.scrollHeight - scroller.clientHeight - scroller.scrollTop < 72;
  const status = block.querySelector('[data-reply-status]');
  const statusText = liveRequirementsStatus(job);
  if (status.textContent !== statusText) status.textContent = statusText;
  const preview = block.querySelector('[data-reply-preview]');
  const reply = job.publicReply || '';
  if (preview.textContent !== reply) preview.textContent = reply;
  preview.hidden = !reply;
  const stop = root.querySelector('#chat-form [data-action="cancel-job"]');
  if (stop) {
    const label = job.cancel_requested ? '正在停止回复' : '停止当前回复';
    stop.disabled = Boolean(job.cancel_requested);
    stop.setAttribute('aria-label', label);
    stop.title = label;
  }
  if (following) scroller.scrollTop = scroller.scrollHeight;
  return true;
}
function chatMessages(p, demo) {
  const messages = Array.isArray(p.messages) ? [...p.messages] : [];
  const job = pageJob();
  const submission = submittedInputs.get(job?.id);
  const pending = p.kind === 'business' && job?.kind === '需求探索' && jobPending(job);
  if (pending && (submission?.message || submission?.files?.length) &&
      !messages.slice(submission.messageCount).some((m) => m.role === 'user')) {
    messages.push({ role: 'user', content: submission.message, attachments: submission.adaptive ? submission.files : undefined });
  }
  const history = chatHistory(p, messages, demo).map((message) => chatMessage(p, message,
    demo && message === CONTRACT_REQUIREMENTS_DIALOGUE.at(-1) ? contractRequirementsDocument(p) : '',
  )).join('');
  return history + (isLiveRequirementsReply(job) ? liveRequirementsReply(job) : '');
}
function confirmationCard() {
  const p = state.project;
  if (p.kind === 'knowledge') return '';
  if (p.kind === 'analysis')
    return `<div class="live-confirm-card"><div><strong>确认分析需求与口径</strong><p>核对对象、指标和追问后确认，再生成数据本体。</p></div>${button('确认分析需求', 'confirm', `data-scope="requirements" ${state.busy || !p.requirements ? 'disabled' : ''}`, true)}</div>`;
  if (adaptiveRequirements()) return '';
  const completed = Number(p.stage || 0) >= 8;
  if (completed || (!p.current_stage_result && !p.messages?.some((m) => m.role === 'assistant'))) return '';
  const hasReply = p.current_stage_result
    ? Number(p.current_stage_result.stage) === Number(p.stage || 0) &&
      p.current_stage_result.ready_for_confirmation
    : (p.messages || []).some(
        (m) => m.role === 'assistant' && Number(m.stage) === Number(p.stage || 0),
      );
  return `<div class="live-confirm-card"><div><strong>${e(stageNames[p.stage || 0])}</strong><p>${hasReply ? '核对本轮讨论后，可继续下一轮。' : '请先回答上方的追问。'}</p></div>${button('确认并继续', 'confirm', `data-scope="stage" ${state.busy || !hasReply ? 'disabled' : ''}`, true)}</div>`;
}
function canSendChat(text, files = []) {
  return !state.busy && state.provider?.configured && (text.trim() || files.length);
}
function chatComposer(p, business) {
  const name = business && !p ? 'new-business-chat' : 'chat';
  const value = draft(name);
  const label = business ? '业务需求' : '补充分析目标与口径';
  const placeholder = business ? '描述业务需求…' : '补充分析目标或口径…';
  const job = pageJob();
  const showStop = isLiveRequirementsReply(job);
  const stopLabel = job?.cancel_requested ? '正在停止回复' : '停止当前回复';
  const files = business ? uploadSelections.get(draftKey(CHAT_ATTACHMENT_ID)) || [] : [];
  const attachmentChips = files.length ? `<ul class="live-chat-attachments">${files.map((file, i) =>
    `<li><span>${e(file.name)}</span>${button('×', 'remove-chat-attachment', `data-index="${i}" aria-label="移除 ${e(file.name)}" ${state.busy ? 'disabled' : ''}`)}</li>`,
  ).join('')}</ul>` : '';
  const attach = business ? `<label class="live-chat-attach" title="添加需求附件">
    <input class="sr-only" type="file" id="${CHAT_ATTACHMENT_ID}" aria-label="添加需求附件" accept="${adaptiveRequirements(p) ? '.md,.pdf,.txt' : '.md,.txt'}" multiple ${state.busy ? 'disabled' : ''}>
    ${icon('plus')}
  </label>` : '';
  return `<form class="conversation-composer live-composer" id="chat-form">
    <div class="agent-chat-composer agent-chat-stacked">
      ${attachmentChips}
      <label class="sr-only" for="chat-input">${label}</label>
      <elastic-textarea class="agent-chat-input">
        <textarea id="chat-input" data-draft="${name}" placeholder="${placeholder}" rows="2" maxlength="20000">${e(value)}</textarea>
      </elastic-textarea>
      <div class="agent-chat-toolbar">
        ${attach}
        <button class="primary send-message agent-chat-action" type="submit" aria-label="发送" title="发送" aria-keyshortcuts="Enter" ${showStop ? 'hidden' : 'id="chat-reply-control"'} ${!showStop && canSendChat(value, files) ? '' : 'disabled'}>${icon('send')}</button>
        ${showStop ? button(icon('stop'), 'cancel-job', `id="chat-reply-control" class="primary agent-chat-action" aria-label="${stopLabel}" title="${stopLabel}" ${job.cancel_requested ? 'disabled' : ''}`) : ''}
      </div>
    </div>
    ${state.provider?.configured ? '' : '<a class="agent-chat-status" href="#settings">配置模型服务后即可发送</a>'}
  </form>`;
}
function inChatWorkspace() {
  return state.route === 'builder' || state.route === 'data1';
}
function chatSessions(type) {
  return state.projects.filter((project) => project.kind === type && !conversationSession(project.id).deleted);
}
function chatPage() {
  const business = state.route === 'builder';
  const list = chatSessions(kind()).sort((a, b) =>
    Number(conversationSession(b.id).pinned) - Number(conversationSession(a.id).pinned));
  const p = state.project?.kind === kind() ? state.project : null;
  const demo = showsContractRequirementsDialogue(p);
  const filter = state.projectFilter.toLowerCase();
  const items = list.map((row) => {
    const selected = row.id === p?.id;
    const { pinned, unread } = conversationSession(row.id);
    const status = business ? (canGenerateRequirements(row) ? '待生成文档' : row.requirements ? (row.requirements_document?.draft ? '已保存草稿' : '已有文档') : '探索中') : '分析需求';
    const count = showsContractRequirementsDialogue(row)
      ? CONTRACT_REQUIREMENTS_DIALOGUE.length + (row.messages || []).filter((message) => message.role !== 'system').length
      : row.messages?.length || 0;
    const summary = business ? `${count} 条` : formatDate(row.updated_at || row.created_at);
    return `<button type="button" class="conversation-item ${selected ? 'active' : ''}" id="chat-session-${e(row.id)}" data-action="select-project" data-id="${e(row.id)}" aria-haspopup="menu" aria-keyshortcuts="Shift+F10 ContextMenu" ${selected ? 'aria-current="true"' : ''} ${row.name.toLowerCase().includes(filter) ? '' : 'hidden'}><strong><span class="conversation-name">${e(row.name)}</span>${pinned ? `<span class="conversation-pin" title="已置顶">${icon('pin')}<span class="sr-only">已置顶</span></span>` : ''}${unread ? '<span class="conversation-unread"><span class="sr-only">未读</span></span>' : ''}</strong><span><small>${status}</small><small>${summary}</small></span></button>`;
  });
  if (business) {
    items.unshift(`<button type="button" class="conversation-item ${p ? '' : 'active'}" ${p ? '' : 'aria-current="true"'} data-action="new-conversation" ${'新对话'.includes(filter) ? '' : 'hidden'} ${state.busy ? 'disabled' : ''}><strong>新对话</strong><span><small>探索中</small><small>0 条</small></span></button>`);
  }
  const left = `<aside class="conversation-list">
    <div class="conversation-list-head"><h2>${business ? '对话' : '分析会话'}</h2>${button(icon('plus'), business ? 'new-conversation' : 'new-project', `class="new-chat" data-kind="${kind()}" aria-label="新建会话" title="新建会话" ${business && state.busy ? 'disabled' : ''}`)}</div>
    <input class="conversation-search" data-input="project-search" placeholder="搜索会话" aria-label="搜索会话" value="${e(state.projectFilter)}">
    <div class="conversation-items">${items.join('') || '<p class="live-list-empty">暂无会话</p>'}</div>
  </aside>`;
  const title = p?.name || '新对话';
  const stage = demo ? '示例对话' : business && !isLiveRequirementsReply(pageJob()) ? prdGenerationNotice(p) : '';
  const draftAvailable = !!prdReview(p)?.draft && !p?.prd_stale && !p?.reference_adoption && !prdSaved(p) && !canGenerateRequirements(p);
  const showGenerate = canGenerateRequirements(p) || (!!p && !demo && (!p.requirements || !adaptiveRequirements()));
  const header = `<header class="conversation-header">
    <div><h2 title="${e(title)}">${e(title)}</h2>${stage ? `<p>${e(stage)}</p>` : ''}</div>
    ${business ? `<div class="conversation-header-actions">
      ${p ? button('新对话', 'new-conversation', `class="live-chat-mobile-new" ${state.busy ? 'disabled' : ''}`) : ''}
      ${p?.requirements && !demo ? (adaptiveRequirements() ? button('查看需求文档', 'prd-open-document', state.busy ? 'disabled' : '') : sectionNav('requirements', '查看需求文档')) : ''}
      ${adaptiveRequirements() && draftAvailable ? button('生成需求草稿', 'generate-requirements', `data-save-draft="true" ${state.busy ? 'disabled' : ''}`) : ''}
      ${showGenerate ? button('生成需求文档', 'generate-requirements', `title="${e(prdGenerationNotice(p))}" ${!p || state.busy || !canGenerateRequirements(p) ? 'disabled' : ''}`, canGenerateRequirements(p)) : ''}
    </div>` : ''}
  </header>`;
  const requirementSummary = p?.requirements && !demo
    ? business
      ? `<details class="live-parse-summary live-chat-document"><summary>${p.requirements_document?.draft ? '已保存需求草稿' : '已保存需求文档'}</summary><div class="live-document">${markdown(p.requirements)}</div></details>`
      : `<details class="live-parse-summary"><summary>查看结构化分析需求</summary><div class="live-document">${analysisRequirementContent(p.analysis_requirements || p.requirements)}</div></details>`
    : '';
  const right = !p && !business
    ? `<section class="conversation-workspace">${emptyProject()}</section>`
    : `<section class="conversation-workspace agent-conversation ${business ? 'live-business-chat' : ''}">
      ${header}
      <div class="conversation-scroll" id="conversation-messages">${p ? chatMessages(p, demo) + requirementSummary + confirmationCard() : ''}</div>
      ${chatComposer(p, business)}
    </section>`;
  const actions =
    p?.kind === 'analysis'
      ? sectionNav('resources', '数据来源') +
        button(
          '解析分析需求',
          'generate-requirements',
          state.busy || !p.source ? 'disabled' : '',
          true,
        )
      : '';
  return `${heading(actions)}<div class="conversation-page"><div class="conversation-shell ${business ? 'business-chat-shell' : ''}">${left}${right}</div></div>`;
}
function applicationsPage() {
  const apps = state.projects.filter((p) => p.kind === 'business');
  const releasedCount = apps.filter((project) => latestRelease(project)).length;
  const cards = apps.map((project) => {
    const release = latestRelease(project);
    const built = hasBuild(project);
    const status = release ? '已发布' : built ? '构建候选' : hasModels(project) ? '本体已生成' : '需求探索中';
    const destination = release
      ? `<a class="live-button primary" href="${e(release.url)}" target="_blank" rel="noopener">进入应用 ↗</a>`
      : built ? `<a class="live-button" href="${e(candidateUrl(project))}" target="_blank" rel="noopener">预览候选 ↗</a>` : '';
    return `<article class="live-app-card">
      <div class="live-app-card-top"><h2>${e(project.name)}</h2>${pill(status, release ? 'green' : '')}</div>
      ${project.description ? `<p>${e(project.description)}</p>` : ''}
      <footer>${button('开发配置', 'open-development', `data-id="${e(project.id)}"`)}${destination}</footer>
    </article>`;
  }).join('');
  return `${heading(button('新建应用项目', 'new-project', 'data-kind="business"', true))}
    <div class="content">
      <div class="live-overview">
        <h2>应用项目 <span>${apps.length}</span></h2>
        <div class="live-overview-count"><strong>${releasedCount}</strong><span>已发布应用</span></div>
      </div>
      ${cards ? `<div class="app-grid live-app-grid">${cards}</div>` :
        '<section class="panel live-inline-empty"><h2>暂无应用项目</h2></section>'}
    </div>`;
}
function developmentPage() {
  const p = state.project;
  if (!p) return `${heading()}<div class="content">${emptyProject()}</div>`;
  const referenceAction = state.status?.capabilities?.contract_reference_build &&
    !p.reference_adoption && !hasBuild(p) && !hasModels(p) && !latestRelease(p)
    ? button('采用原版合同样例', 'adopt-contract-reference', state.busy ? 'disabled' : '')
    : '';
  const importAction = !p.requirements && (!hasModels() || p.business_model_import)
    ? button('导入七模型构建', 'import-business-models', state.busy ? 'disabled' : '') : '';
  const release = latestRelease(p);
  const built = hasBuild(p);
  const models = hasModels();
  const buildBlocked = !confirmed('models') || !p.prototype;
  const resources = [
    ['需求文档', hasRequirements(), sectionNav('requirements', '查看需求')],
    ['界面原型', !!p.prototype, p.business_model_import
      ? button('查看并确认界面', 'prd-prototype-review', `data-id="${e(p.id)}"`)
      : sectionNav('interface', '查看原型')],
    ['业务本体', models, sectionNav('models', '查看模型')],
  ].map(([title, ready, action]) => `<section class="panel">
    <header class="panel-head"><h2>${title}</h2>${pill(ready ? '已生成' : '待生成', ready ? 'green' : '')}</header>
    <div class="panel-body">${action}</div>
  </section>`).join('');
  return `${heading(sectionNav('build', '返回应用开发') + referenceAction + importAction)}
    <div class="content">
      ${projectPicker()}
      <section class="live-project-summary">
        <div><h2>${e(p.name)}</h2>${p.description ? `<p>${e(p.description)}</p>` : ''}</div>
        ${release ? `<a class="live-button primary" href="${e(release.url)}" target="_blank" rel="noopener">进入已发布应用 ↗</a>` : ''}
      </section>
      <div class="live-resource-grid">${resources}</div>
      <section class="panel live-build-panel">
        <header class="panel-head"><h2>构建与运行</h2>${pill(built ? '候选可用' : buildBlocked ? '待确认' : '待构建')}</header>
        <div class="panel-body">
          ${buildBlocked && !built ? '<p>确认界面原型和业务本体后，可以构建应用。</p>' : ''}
          <div class="live-action-row">
            ${button('构建应用', 'build-application', `data-requires-saved data-blocked="${buildBlocked}" ${state.busy || buildBlocked || dirtyModelFiles().length ? 'disabled' : ''}`, true)}
            ${models ? button('确认当前本体', 'confirm', `data-scope="models" data-requires-saved data-blocked="${!canConfirmModels()}" ` + (state.busy || dirtyModelFiles().length || !canConfirmModels() ? 'disabled' : '')) : ''}
            ${built ? `<a class="live-button" href="${e(candidateUrl())}" target="_blank" rel="noopener">预览当前候选 ↗</a>${sectionNav('publish', '发布当前候选')}<a class="live-button" href="${url(projectUrl('/download'))}">下载应用开发包 ↓</a>` : ''}
          </div>
          ${p.build ? `<details><summary>构建记录</summary><pre class="live-code">${e(JSON.stringify(p.build, null, 2))}</pre></details>` : ''}
        </div>
      </section>
    </div>`;
}
function requirementAudit(p) {
  const audit = p?.requirement_audit || p?.requirements_audit;
  if (!audit || p.kind !== 'business') return '';
  const checks = Array.isArray(audit.checks) ? audit.checks : [];
  const passed = checks.filter((check) => check.passed).length;
  return `<section class="live-requirement-audit"><div class="live-audit-heading"><div><strong>需求完整性检查</strong></div>${pill(audit.passed ? '结构检查通过' : '需要补充', audit.passed ? 'green' : 'amber')}</div>${audit.errors?.length ? `<ul>${audit.errors.map((error) => `<li>${e(error)}</li>`).join('')}</ul>` : ''}<details ${audit.passed ? '' : 'open'}><summary>${passed} / ${checks.length || 47} 项通过 · 查看依据</summary><div class="table-wrap"><table><thead><tr><th>序号</th><th>自检项</th><th>结果</th><th>依据与理由</th></tr></thead><tbody>${checks.map((check) => `<tr><td>${e(check.number)}</td><td>${e(check.item || '检查项' + check.number)}${check.criterion ? `<small><br>${e(check.criterion)}</small>` : ''}</td><td>${e(check.passed ? check.status : '需复核')}</td><td>${e(check.reason)}${check.diagnostics?.length ? `<ul>${check.diagnostics.map((issue) => `<li>${e(issue)}</li>`).join('')}</ul>` : ''}</td></tr>`).join('')}</tbody></table></div>${audit.document_sha256 ? `<p class="live-subtle">文档摘要 ${e(audit.document_sha256.slice(0, 16))}</p>` : ''}</details></section>`;
}
function canMarkPrototypeOptional(project) {
  const audit = project.requirement_audit || project.requirements_audit;
  const checks = audit?.checks?.slice(42, 47) || [];
  return confirmed('requirements', project) && !project.requirements_confirmation?.prototype_only &&
    !project.prototype_confirmation?.not_applicable && audit?.passed && checks.length === 5 &&
    checks.every((item) => item.status === '不适用');
}
function businessRegistryProjects(test) {
  return state.projects
    .map((project) => project.id === projectId() ? state.project : project)
    .filter((project) => project.kind === 'business' && test(project));
}
function requirementTitle(project) {
  return project.requirements_document?.title || `${project.name} · 需求规格`;
}
function requirementsOutdated(project) {
  return !!(project.prd_stale || (project.prd_inputs_sha256 &&
    project.requirements_inputs_sha256 !== project.prd_inputs_sha256));
}
function prdTaskStatus(project) {
  const job = pageJob('requirements', project);
  if (job && ['生成需求文档', 'AI 优化需求文档', '生成本体模型'].includes(job.kind)) {
    const status = { queued: '等待执行', running: '执行中', succeeded: '已完成', failed: '执行失败', cancelled: '已取消', interrupted: '已中断' }[job.status];
    if (status) return pill(status, job.status === 'succeeded' ? 'green' : 'amber');
  }
  const operation = project.requirements_document?.last_operation;
  if (operation) {
    const label = { generated: '已生成', edited: '已编辑', optimized: '已优化', imported: '已导入' }[operation] || '已完成';
    return pill(label, 'green');
  }
  return '—';
}
function registryModelGenerationBlocker(project) {
  if (project.reference_adoption) return '原版样例使用固定七模型';
  if (requirementsOutdated(project)) return '需求已更新，请先保存最新文档';
  if (project.requirements_document?.draft) return '当前是草稿，请先保存正式需求文档';
  if (!confirmed('requirements', project) || project.requirements_confirmation?.prototype_only)
    return '请先在预览中确认正式需求文档';
  if (!project.prototype_confirmation?.not_applicable && (!project.prototype || prototypeIsStale(project))) return '请先生成当前需求的界面原型';
  if (!confirmed('prototype', project)) return '请先在界面原型页确认当前原型';
  if (!state.provider?.configured) return '请先配置模型服务';
  return '';
}
function requirementsRegistryPage() {
  const items = businessRegistryProjects((project) => project.requirements);
  const rows = items.map((project) => {
    const doc = project.requirements_document;
    const source = { generated: 'AI 生成', imported: '手动上传', legacy: '历史文档' }[doc?.source] || (project.reference_adoption || project.upstream_import ? '原版导入' : '历史文档');
    const id = `data-id="${e(project.id)}"`;
    const editable = !project.reference_adoption && !state.busy;
    const readonlyReason = project.reference_adoption ? '原版样例使用固定需求文档' : '当前任务正在处理';
    const editAttributes = `${id} ${editable ? '' : `disabled title="${readonlyReason}"`}`;
    const modelsConfirmed = confirmed('models', project);
    const model = hasModels(project)
      ? `<div class="prd-row-actions">${pill(modelsConfirmed ? '已确认' : '已生成', modelsConfirmed ? 'green' : '')}${button('查看本体', 'prd-open-ontology', `${id} class="prd-title-button"`)}</div>`
      : '未生成';
    const outOfDate = requirementsOutdated(project);
    const prototypeBlocker = outOfDate ? '需求已更新，请先保存最新文档' :
      !editable ? readonlyReason : !state.provider?.configured ? '请先配置模型服务' : '';
    const prototypeAttributes = `${id} ${prototypeBlocker ? `disabled title="${prototypeBlocker}"` : ''}`;
    const modelBlocker = state.busy ? '当前任务正在处理' : registryModelGenerationBlocker(project);
    const modelAttributes = `${id} ${modelBlocker ? `disabled title="${modelBlocker}"` : ''}`;
    const previewAttributes = `${id} ${state.busy ? 'disabled' : ''}`;
    return `<tr><th scope="row"><button class="prd-title-button" type="button" data-action="prd-preview" ${previewAttributes}>${e(requirementTitle(project))}</button>${doc?.draft ? pill('草稿', 'amber') : ''}${outOfDate ? pill('有新需求', 'amber') : ''}</th><td>${e(source)}</td><td>${e(project.name)}</td><td class="prd-date">${e(formatDate(doc?.updated_at || project.updated_at, true) || '—')}</td><td>${model}</td><td>${prdTaskStatus(project)}</td><td><div class="prd-row-actions">${button('编辑', 'prd-edit', editAttributes)}${button('预览', 'prd-preview', previewAttributes)}${button('生成原型', 'prd-generate-prototype', prototypeAttributes)}${button('生成本体', 'prd-generate-models', modelAttributes)}${button('删除', 'prd-delete', editAttributes + ' data-variant="danger"')}</div></td></tr>`;
  }).join('');
  return `${heading(sectionNav('builder', '返回需求对话') + button('上传需求文档', 'prd-import', state.busy ? 'disabled' : '', true))}<div class="content"><section class="panel prd-registry">${rows ? `<div class="table-wrap"><table><thead><tr><th scope="col">文档标题</th><th scope="col">来源</th><th scope="col">原项目</th><th scope="col">更新时间</th><th scope="col">本体模型</th><th scope="col">生成任务</th><th scope="col">操作</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<div class="panel-body live-inline-empty"><h2>暂无需求文档</h2><p>从需求探索保存文档，或上传现有的 Markdown、PDF 或文本。</p></div>'}</section></div>`;
}
function requirementsPage() {
  if (adaptiveRequirements() && kind() === 'business') return requirementsRegistryPage();
  const p = state.project;
  return `${heading(sectionNav(kind() === 'analysis' ? 'data1' : 'builder', '返回需求对话'))}<div class="content">${projectPicker()}${!p ? emptyProject() : `<section class="panel"><div class="panel-head"><div><h2>${e(p.name)} · 需求规格</h2><small>当前项目修订 ${e(p.revision || 0)}</small></div>${hasRequirements() ? pill(confirmed('requirements') ? '已确认' : '待确认', confirmed('requirements') ? 'green' : '') : pill('待生成')}</div><div class="panel-body">${hasRequirements() ? `${requirementAudit(p)}<article class="live-document">${markdown(p.requirements)}</article>` : `<div class="live-inline-empty"><h3>需求文档尚未生成</h3><p>${p.kind === 'business' ? '完成对话中的逐轮确认，再生成规格说明书。' : '连接数据库并说明分析目标，模型会整理结构化需求。'}</p></div>`}</div><div class="live-panel-footer">${p.kind === 'business' ? button('生成需求文档', 'generate-requirements', `${state.busy ? 'disabled' : ''}`) : ''}${hasRequirements() ? button('确认需求文档', 'confirm', `data-scope="requirements" ${state.busy ? 'disabled' : ''}`) : ''}${canMarkPrototypeOptional(p) ? button('确认原型不适用', 'confirm', 'data-scope="prototype_not_applicable"') : ''}${p.kind === 'business' ? sectionNav(p.prototype_confirmation?.not_applicable ? 'models' : 'interface', p.prototype_confirmation?.not_applicable ? '继续七模型建模' : '预览并确认界面原型 →', true) : button('生成数据本体', 'generate-models', `${state.busy || !canGenerateModels() ? 'disabled' : ''}`, true)}</div></section>`}</div>`;
}
function modelFiles() {
  return Object.entries(state.project?.models || {});
}
function editableModelFiles() {
  const files = modelFiles();
  const bindings = state.project?.source_bindings;
  return bindings ? [...files, [DATA_BINDING_FILE, bindings]] : files;
}
function analysisImportPanel(p) {
  if (p?.kind !== 'analysis') return '';
  const imported = p.model_import?.format === 'onto-dataanalyse-five';
  const warnings = p.model_import?.warnings || [];
  const metricRepairs = p.model_import?.upstream_metric_repairs?.repairs || [];
  const repairOption = state.status?.capabilities?.upstream_commerce_scenarios
    ? '<details class="live-assembly-evidence"><summary>电商样例导入选项</summary><label class="field"><span><input id="apply-original-commerce-repairs" type="checkbox"> 补全营销 GMV 和商品评分的计算字段</span></label></details>'
    : '';
  const sourceFiles = Object.entries(p.model_import?.files || {}).map(([name, source]) => {
    const artifact = source?.artifact;
    const link = artifact
      ? `<a href="${url(projectUrl('/artifacts/' + artifact.split('/').map(encodeURIComponent).join('/')))}" target="_blank" rel="noopener">${e(name)} ↗</a>`
      : e(name);
    return `<li>${link}${source?.sha256 ? ` <code>${e(source.sha256)}</code>` : ''}</li>`;
  }).join('');
  const provenance = imported
    ? `<details class="live-assembly-evidence"><summary>查看原始导入记录</summary><p><code>${e(p.model_import.format)}</code></p>${sourceFiles ? `<ul>${sourceFiles}</ul>` : ''}</details>`
    : '';
  return `<section class="panel live-source-summary live-analysis-import"><div class="panel-head"><h2>导入开源五模型</h2>${imported ? pill('已转换', 'green') : ''}</div><div class="panel-body"><p>同时选择五份模型和一份字段映射。</p>
    ${!p.source ? `<div class="live-stale-notice">先连接 SQLite 数据源。${sectionNav('resources', '连接数据源')}</div>` : ''}
    ${p.source && !confirmed('requirements') ? `<div class="live-stale-notice">现在可以导入并查看。执行查询前请确认分析需求与模型。${sectionNav('data1', '查看分析需求')}</div>` : ''}
    <form id="analysis-model-import-form"><label class="live-upload-zone compact" for="analysis-model-files">${icon('file')}<strong>选择六份 YAML 文件</strong><span>${e(ANALYSIS_IMPORT_FILES.join('、'))}</span><input id="analysis-model-files" type="file" accept=".yaml" multiple required ${!p.source || state.busy ? 'disabled' : ''}></label>${repairOption}<div class="live-action-row"><button type="submit" class="primary" data-requires-saved data-blocked="${!p.source}" ${!p.source || state.busy ? 'disabled' : ''}>转换为统一七模型</button>${hasModels(p) ? '<span class="live-subtle">导入将替换当前本体。</span>' : ''}</div></form>
    ${metricRepairs.length ? `<details class="live-assembly-evidence"><summary>已采用 ${metricRepairs.length} 项原版指标修订</summary><ul>${metricRepairs.map((repair) => `<li><strong>${e(repair.metric_id)} ${e(repair.name)}</strong> ${e(repair.interpretation || repair.reason || '')}</li>`).join('')}</ul></details>` : ''}
    ${warnings.length ? `<details class="live-assembly-evidence"><summary>导入说明 · ${warnings.length} 项</summary><ul>${warnings.map((warning) => `<li>${e(warning)}</li>`).join('')}</ul></details>` : ''}${provenance}</div></section>`;
}
function modelDiagnostics(p) {
  const diagnostics = p?.model_diagnostics || [];
  if (!diagnostics.length) return '';
  return `<section class="panel live-source-summary live-model-diagnostics"><div class="panel-head"><h2>当前模型校验</h2>${pill('待修订', 'amber')}</div><div class="panel-body"><ul>${diagnostics.map((diagnostic) => `<li>${e(diagnostic)}</li>`).join('')}</ul></div></section>`;
}
function modelFileArtifact(project, name) {
  const path = project.model_artifacts?.[name];
  return typeof path === 'string' ? url(projectUrl('/artifacts/' + path.split('/').map(encodeURIComponent).join('/'))) : '';
}
function businessModelFilesPage() {
  const project = state.project;
  const picker = projectPicker({ showNew: false });
  if (!project) return `${heading()}<div class="content">${picker}${emptyProject()}</div>`;
  if (state.modelFileOpen && modelFiles().some(([name]) => name === state.modelFile))
    return modelEditorPage({ businessFiles: true });

  const requirements = project.requirements ? `<tr><td>需求文档</td><td>需求规格</td><th scope="row">${e(requirementTitle(project))}</th><td>${button('查看', 'model-file-requirements', 'aria-label="查看需求规格"')}</td></tr>` : '';
  const files = modelFiles().sort(([left], [right]) => {
    if (left === 'manifest.json') return 1;
    if (right === 'manifest.json') return -1;
    return left.localeCompare(right);
  }).map(([name, model]) => {
    const manifest = name === 'manifest.json';
    const artifact = modelFileArtifact(project, name);
    const attributes = `data-file="${e(name)}" aria-label="查看 ${e(name)}"`;
    const download = artifact ? `<a class="live-button" href="${e(artifact)}" download="${e(name)}" aria-label="下载 ${e(name)}">下载</a>` : '';
    return `<tr><td>${manifest ? '模型清单' : '本体模型'}</td><td>${e(modelTitle(name, model))}</td><th scope="row"><code>${e(name)}</code></th><td><div class="prd-row-actions">${button('查看', 'open-model-file', attributes)}${download}</div></td></tr>`;
  }).join('');
  return `${heading(sectionNav('models', '业务本体'))}<div class="content">${picker}<section class="panel prd-registry live-model-files"><div class="table-wrap"><table><thead><tr><th scope="col">类型</th><th scope="col">模型</th><th scope="col">名称</th><th scope="col">操作</th></tr></thead><tbody>${requirements}${files || '<tr><td colspan="4">当前项目尚未生成本体模型。</td></tr>'}</tbody></table></div></section></div>`;
}
function modelWorkflowActions(p = state.project) {
  if (p?.reference_adoption || p?.business_model_import) return '';
  const blocked = !canGenerateModels(p) || !state.provider?.configured;
  const dirty = dirtyModelFiles().length > 0;
  return button(hasModels(p) ? '重新生成本体' : '生成业务本体', 'generate-models',
    `data-requires-saved data-blocked="${!!blocked}" ${state.busy || dirty || blocked ? 'disabled' : ''}`,
    !hasModels(p));
}
function openOntologyEdit(filename, path, node, title, fieldLabel) {
  const fields = ontologyEditableFields(node).map(([key, value], index) => {
    const name = `field-${index}`;
    const attributes = `name="${name}" id="ontology-edit-${name}"`;
    const control = typeof value === 'boolean'
      ? `<select ${attributes}><option value="true" ${value ? 'selected' : ''}>是</option><option value="false" ${!value ? 'selected' : ''}>否</option></select>`
      : typeof value === 'number'
        ? `<input type="number" step="any" ${attributes} value="${e(value)}" required>`
        : `<textarea ${attributes} rows="${String(value).length > 100 ? 3 : 1}">${e(value)}</textarea>`;
    return `<div class="field"><label for="ontology-edit-${name}">${e(fieldLabel(key))}</label>${control}</div>`;
  }).join('');
  modal(`编辑属性 · ${title}`, `<form id="ontology-edit-form" data-project="${e(projectId())}" data-file="${e(filename)}" data-path="${e(JSON.stringify(path))}" data-revision="${e(state.project.revision)}">${fields}<div class="live-modal-actions">${button('取消', 'close-modal')}<button type="submit" class="primary">保存并校验</button></div></form>`);
}
function businessOntologyPage() {
  const p = state.project;
  const header = heading(sectionNav('files', '模型文件') + (p ? modelWorkflowActions(p) : ''));
  const picker = projectPicker({ showNew: false });
  if (!p) return `${header}<div class="content">${picker}${emptyProject()}</div>`;
  if (!hasModels(p)) {
    const message = canGenerateModels(p) ? '<p>从已确认的需求规格生成七份 V9 本体模型。</p>'
      : `<p>确认需求文档和界面原型后即可生成本体。</p><div class="live-action-row">${sectionNav('requirements', '需求文档')}${sectionNav('interface', '界面原型')}</div>`;
    return `${header}<div class="content">${picker}<section class="panel live-empty"><h2>当前项目尚无业务本体</h2>${message}</section></div>`;
  }
  const confirmedModels = confirmed('models');
  const dirty = dirtyModelFiles().length > 0;
  const confirmBlocked = !canConfirmModels() || confirmedModels;
  const buildBlocked = !confirmedModels || !p.prototype;
  const readOnly = p.reference_adoption || p.business_model_import;
  const repairBlocked = !canGenerateModels(p) || !state.provider?.configured;
  const assistantBody = readOnly ? '<p>当前项目使用固定模型版本。</p>'
    : `<div class="live-ontology-assistant-input"><label for="ontology-repair-feedback">校核重点</label><textarea id="ontology-repair-feedback" data-draft="repair-feedback" rows="4" maxlength="8000" placeholder="例如，复核合同开票规则和财务权限">${e(draft('repair-feedback'))}</textarea><footer>${button('校核并修复', 'repair-models', `data-requires-saved data-blocked="${repairBlocked}" ${state.busy || dirty || repairBlocked ? 'disabled' : ''}`)}</footer></div>`;
  const workspace = `<section class="panel live-ontology-panel"><aside class="live-ontology-nav" aria-label="模型目录"><h2>模型目录</h2><input type="search" id="ontology-search" aria-label="搜索节点" placeholder="搜索名称、编号或字段"><ul class="live-ontology-tree"></ul></aside><section class="live-ontology-detail" aria-label="节点详情"></section><aside class="live-ontology-assistant"><h2>本体助手</h2>${assistantBody}</aside></section>`;
  const footer = `<div class="live-action-row live-model-actions">${pill(confirmedModels ? '已确认' : '待确认', confirmedModels ? 'green' : 'amber')}<span class="live-model-dirty live-stale-notice" ${dirty ? '' : 'hidden'}>请先保存模型文件中的修改。</span>${button('确认当前本体', 'confirm', `data-scope="models" data-requires-saved data-blocked="${confirmBlocked}" ${state.busy || dirty || confirmBlocked ? 'disabled' : ''}`, true)}${button('构建应用', 'build-application', `data-requires-saved data-blocked="${buildBlocked}" ${state.busy || dirty || buildBlocked ? 'disabled' : ''}`)}</div>`;
  return `${header}<div class="content">${picker}${modelDiagnostics(p)}${workspace}${footer}</div>`;
}
function modelEditorPage({ businessFiles = false } = {}) {
  const p = state.project;
  const models = modelFiles();
  const files = editableModelFiles();
  if (!files.some(([name]) => name === state.modelFile)) state.modelFile = files[0]?.[0] || '';
  const file = files.find(([name]) => name === state.modelFile);
  const fileText = file ? JSON.stringify(file[1], null, 2) : '';
  const editorRevision = drafts[draftKey('model-revision:' + state.modelFile)] ?? p?.revision;
  const staleDraft =
    editorRevision !== p?.revision && Object.hasOwn(drafts, draftKey('model:' + state.modelFile));
  const isKnowledge = p?.kind === 'knowledge';
  const unified = p?.model_standard === SEVEN_MODEL_STANDARD;
  const modelActions = businessFiles ? button('返回文件列表', 'close-model-file') :
    button(
      isKnowledge ? '萃取知识模型' : p?.kind === 'analysis' ? '生成数据本体' : '生成业务本体',
      isKnowledge ? 'knowledge-extract' : 'generate-models',
      `data-requires-saved data-blocked="${!isKnowledge && !canGenerateModels()}" ${state.busy || dirtyModelFiles().length || (!isKnowledge && !canGenerateModels()) ? 'disabled' : ''}`,
      true,
    ) +
    (files.length && !isKnowledge && !p?.business_model_import
      ? button(
          '校验并修复',
          'repair-models',
          `data-requires-saved ${state.busy || dirtyModelFiles().length ? 'disabled' : ''}`,
        )
      : '');
  if (!p) return `${heading(modelActions)}<div class="content">${projectPicker()}${emptyProject()}</div>`;
  const importer = analysisImportPanel(p);
  const diagnostics = modelDiagnostics(p);
  if (!file) {
    const empty = `<section class="panel live-empty"><h2>当前项目尚无模型</h2><p>${isKnowledge ? '先导入原文资料，再执行知识萃取。' : '先核对需求，再生成当前项目自己的模型。'}</p>${sectionNav(isKnowledge ? 'knowledge' : p.kind === 'analysis' ? 'data1' : 'builder', '继续准备输入')}</section>`;
    return `${heading(modelActions)}<div class="content">${projectPicker()}${empty}${diagnostics}${importer}</div>`;
  }
  const fileListHeading = unified ? '统一七模型' : models.length + ' 个模型文件';
  const modelChoice = ([name, value], binding = false) =>
    `<button type="button" class="${name === state.modelFile ? 'active' : ''}" data-action="select-model" data-file="${e(name)}"${binding ? ' data-model-binding' : ''}><strong>${binding ? '字段映射' : e(modelTitle(name, value))}</strong><small>${e(name)}</small></button>`;
  const fileList = models.map((model) => modelChoice(model)).join('');
  const bindingChoice = p.source_bindings
    ? `<h3 class="live-model-binding-title">数据源绑定</h3>${modelChoice([DATA_BINDING_FILE, p.source_bindings], true)}`
    : '';
  const bindingSelected = !!p.source_bindings && file[0] === DATA_BINDING_FILE;
  const typeLabel = bindingSelected ? '数据源绑定' : unified ? 'Onto PaaS 七模型' : '当前项目模型';
  const inputHelp = staleDraft
    ? '草稿基于旧版本，请核对后保存。'
    : '修改后需重新确认本体。';
  const readOnly = p.business_model_import || p.reference_adoption || (businessFiles && file[0] === 'manifest.json');
  const artifact = businessFiles ? modelFileArtifact(p, file[0]) : '';
  const source = artifact ? `<a class="live-button" href="${e(artifact)}" target="_blank" rel="noopener">查看原文件</a>` : '';
  const controls = readOnly ? source : `<span class="live-subtle">${inputHelp}</span>${source}${button('放弃本地修改', 'discard-model', state.busy ? 'disabled' : '')}${button('保存并校验', 'save-model', state.busy ? 'disabled' : '', true)}`;
  const editor = `<section class="panel live-model-panel"><aside class="live-model-list"><h2>${fileListHeading}</h2>${fileList}${bindingChoice}</aside><div class="live-model-editor"><div class="live-model-editor-head"><div><h2>${e(modelTitle(file[0], file[1]))}</h2><small>${e(file[0])}</small></div>${pill(businessFiles ? readOnly ? 'JSON' : 'JSON 编辑视图' : typeLabel)}</div><label class="sr-only" for="model-editor">模型文件内容</label><textarea id="model-editor" data-draft="model:${e(state.modelFile)}" data-revision="${e(editorRevision)}" spellcheck="false" ${readOnly ? 'readonly' : ''}>${e(draft('model:' + state.modelFile, fileText))}</textarea>${controls ? `<div class="live-panel-footer">${controls}</div>` : ''}</div></section>`;
  if (businessFiles) return `${heading(modelActions)}<div class="content">${projectPicker({ showNew: false })}${diagnostics}${editor}</div>`;
  const modelNavigation = isKnowledge
    ? sectionNav('knowledge', '回原文问答')
    : p.kind === 'business'
      ? button('构建应用', 'build-application', `data-requires-saved data-blocked="${!confirmed('models') || !p.prototype}" ${state.busy || dirtyModelFiles().length || !confirmed('models') || !p.prototype ? 'disabled' : ''}`)
      : sectionNav('data-mapping', '查看数据源绑定') + sectionNav('analytics', '进入分析') +
        (unified ? `<a class="live-button" href="${url(projectUrl('/download'))}">下载模型和数据源绑定 ↓</a>` : '');
  const footer = `<div class="live-action-row live-model-actions"><span class="live-stale-notice live-model-dirty" ${dirtyModelFiles().length ? '' : 'hidden'}>存在未保存的模型修改，请先保存并校验。</span>${button('确认当前本体', 'confirm', `data-scope="models" data-requires-saved data-blocked="${!canConfirmModels()}" ${state.busy || dirtyModelFiles().length || !canConfirmModels() ? 'disabled' : ''}`, true)}${modelNavigation}</div>`;
  return `${heading(modelActions)}<div class="content">${projectPicker()}${diagnostics}${editor}${footer}${importer}</div>`;
}

function modelTitle(name, value) {
  const titles = {
    OBJECT: 'M1 对象',
    BEHAVIOR: 'M2 行为',
    RULE: 'M3 规则',
    ACTOR: 'M5 主体与权限',
    FLOW: 'M6 流程',
    REPORT: 'M7 查询报表',
    QUERY_REPORT: 'M7 查询报表',
    UI: 'MU 界面',
    SCENARIO: 'M4 场景',
    METRIC: '指标模型',
  };
  if (name === 'manifest.json') return '模型清单';
  if (/mapping/i.test(name)) return '数据映射';
  return titles[value?.model_type] || name.replace(/\.ya?ml$/, '').replaceAll('-', ' ');
}
function graphData() {
  const nodes = new Map();
  function walk(value) {
    if (!value || typeof value !== 'object') return;
    if (typeof value.id === 'string' && !nodes.has(value.id))
      nodes.set(value.id, {
        id: value.id,
        name: value.name || value.label || value.id,
        object: value,
      });
    Object.values(value).forEach((v) => {
      if (typeof v === 'object') Array.isArray(v) ? v.forEach(walk) : walk(v);
    });
  }
  modelFiles().forEach(([, m]) => walk(m));
  const links = new Set();
  for (const node of nodes.values()) {
    function references(v, depth = 0) {
      if (typeof v === 'string' && nodes.has(v)) links.add(node.id + '|' + v);
      if (v && typeof v === 'object' && depth < 8)
        Object.entries(v)
          .filter(([k]) => k !== 'id')
          .forEach(([, x]) => references(x, depth + 1));
    }
    references(node.object);
  }
  return { nodes: [...nodes.values()], edges: [...links].map((s) => s.split('|')) };
}
function graphPage() {
  const graph = graphData();
  const action = state.project?.kind === 'business' && hasModels()
    ? `<a class="live-button" href="${url(projectUrl('/models/callchain'))}" target="_blank" rel="noopener">界面调用链 ↗</a>` : '';
  return `${heading(sectionNav(kind() === 'analysis' ? 'data2' : 'files', '查看模型文件') + action)}<div class="content">${projectPicker()}${!state.project ? emptyProject() : `<section class="panel live-graph-panel"><div class="panel-head"><h2>模型定义与引用</h2><div>${pill(graph.nodes.length + ' 个节点')}${pill(graph.edges.length + ' 条引用')}</div></div>${!graph.nodes.length ? '<div class="empty">生成模型后即可查看图谱。</div>' : `<div class="live-graph-workspace"><div class="live-graph-toolbar"><span class="live-graph-status"></span><div>${['out', 'in', 'fit', 'clear'].map((action, index) => `<button type="button" data-graph-control="${action}" ${action === 'out' ? 'aria-label="缩小图谱"' : action === 'in' ? 'aria-label="放大图谱"' : ''}>${['−', '+', '适应全部', '清除焦点'][index]}</button>`).join('')}<span data-graph-control="scale"></span></div></div><div class="live-graph-viewport" aria-label="本体引用图"><svg class="live-graph" viewBox="0 0 1000 560" data-layout="d3-force" role="img" aria-label="当前模型的全部定义与引用关系"><defs><marker id="live-graph-arrow" markerWidth="6" markerHeight="6" refX="16" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6" fill="#b8c7db"/></marker></defs><g class="live-graph-transform"><g class="live-graph-edges"></g><g class="live-graph-nodes"></g></g></svg></div><aside class="live-graph-detail" hidden></aside><p class="live-graph-help">滚动缩放，拖动调整位置。选择节点可查看直接关联。</p></div>`}</section>`}</div>`;
}

function sourcePage() {
  const p = state.project;
  const schema = p?.source?.schema;
  const tables = Array.isArray(schema?.tables)
    ? schema.tables
    : Object.values(schema?.tables || {});
  const summary = p?.source
    ? `<section class="panel live-source-summary"><div class="panel-head"><div><h2>${e(p.source.name || p.source.filename || '当前数据来源')}</h2></div>${pill(tables.length + ' 张表', 'green')}</div><div class="panel-body">${schemaTable(tables)}${p.source.hash || p.source.sha256 ? `<details class="live-assembly-evidence"><summary>数据源校验摘要</summary><code>${e(p.source.hash || p.source.sha256)}</code></details>` : ''}</div><div class="live-panel-footer">${sectionNav('data1', '说明分析需求 →', true)}</div></section>`
    : `<section class="panel"><div class="empty">${p ? '此项目暂无数据库。' : '选择分析项目以查看数据来源。'}</div></section>`;
  return `${heading(button('新建分析项目', 'new-project', 'data-kind="analysis"', true))}<div class="content">${projectPicker({ showNew: false })}${summary}</div>`;
}
function analysisSourceFields() {
  const sources = state.projects
    .filter((x) => x.kind === 'business')
    .flatMap((project) => [
      ...(project.releases || [])
        .slice()
        .reverse()
        .map((release) => ({
          label: `${project.name} · 已发布 ${release.version}`,
          payload: { source_project_id: project.id, release_id: release.id },
        })),
      ...(hasBuild(project)
        ? [
            {
              label: `${project.name} · 当前构建候选快照`,
              payload: { source_project_id: project.id, source_kind: 'candidate' },
            },
          ]
        : []),
    ]);
  const options = sources.map(({ label, payload }) =>
    `<option value="${e(JSON.stringify(payload))}">${e(label)}</option>`,
  ).join('');
  return `<fieldset class="live-source-methods"><legend>数据来源</legend><div>
    <label><input type="radio" name="source-method" value="upload" checked>上传数据库</label>
    <label><input type="radio" name="source-method" value="application">连接业务应用版本</label>
  </div></fieldset>
  <fieldset class="live-source-fields" data-source-method="upload"><label class="live-upload-zone compact" for="database-file">${icon('file')}<strong>选择 SQLite 数据库</strong><span>.db / .sqlite / .sqlite3</span><input id="database-file" type="file" accept=".db,.sqlite,.sqlite3" required></label></fieldset>
  <fieldset class="live-source-fields" data-source-method="application" hidden disabled><label class="field">业务应用版本<select id="source-project-select" required><option value="">${options ? '请选择版本或构建候选' : '暂无可连接版本'}</option>${options}</select></label></fieldset>`;
}
function schemaTable(tables) {
  return tables
    .map(
      (table) =>
        `<details class="live-schema-table" ${tables.length < 4 ? 'open' : ''}><summary><strong>${e(table.name || table.table)}</strong><span>${(table.columns || []).length} 个字段</span></summary><div class="table-wrap"><table><thead><tr><th>字段</th><th>类型</th><th>约束</th></tr></thead><tbody>${(table.columns || []).map((c) => `<tr><td>${e(c.name)}</td><td>${e(c.type)}</td><td>${c.pk ? '主键 ' : ''}${c.notnull ? '非空' : ''}</td></tr>`).join('')}</tbody></table></div>${table.foreign_keys?.length ? `<p class="live-subtle">关联：${e(table.foreign_keys.map((f) => `${f.from} → ${f.table}.${f.to}`).join('；'))}</p>` : ''}</details>`,
    )
    .join('');
}
function findModel(pattern) {
  return modelFiles().find(([n]) => pattern.test(n))?.[1];
}
function mappingPage() {
  return `${heading(sectionNav('data2', '查看数据本体'))}<div class="content"><div id="mapping-workbench"></div></div>`;
}
function reasoningPage() {
  return `${heading(sectionNav('resources', '数据来源'))}<div class="conversation-page"><div id="reasoning-workbench"></div></div>`;
}
function metricsPage() {
  const project = state.project;
  const metricDocument = metricDocuments.get(project?.id);
  const metrics = metricDefinitions(project, metricDocument?.definitions);
  const insights = currentMetricInsights(project);
  const actions = sectionNav('data3', '场景分析') + (project
    ? button('导入指标定义', 'open-metric-document') +
      button('指标洞察', 'generate-metric-insights', `id="metric-insight-generate" ${metrics.length ? '' : 'disabled'}`, true)
    : '');
  if (!project) return `${heading(actions)}<div class="content">${projectPicker()}${emptyProject()}</div>`;
  const summary = `<div class="live-metric-insight-summary"><div><strong>${metrics.length} 项指标</strong>
    <span>${metricDocument ? e(metricDocument.name) : '当前数据本体'}</span></div>
    ${insights ? `<span role="status">已生成 ${insights.length} 项模拟洞察</span>` : ''}</div>`;
  const cards = metrics.map((metric, index) => {
    const dimensions = metric.supported_dimensions || metric.dimensions;
    return `<article class="panel live-metric-card"><div class="panel-body">
      <h2>${e(metric.name || metric.id)}</h2>${metric.id !== metric.name ? `<code class="live-metric-id">${e(metric.id)}</code>` : ''}
      <p>${e(metric.description || metric.definition || '')}</p>
      <div class="live-metric-formula">${e(metric.formula_description || metric.formula || metric.computation_type || '计算口径待业务确认')}</div>
      <footer>${pill(metric.unit || '未指定单位')}<span>${e(Array.isArray(dimensions) ? dimensions.map((item) => item?.name || item).join(' · ') : dimensions || '')}</span></footer>
      ${insights ? button('查看洞察', 'view-metric-insight', `data-metric-index="${index}"`) : ''}
      <details><summary>指标完整定义</summary><pre class="live-code">${e(JSON.stringify(metric, null, 2))}</pre></details>
    </div></article>`;
  }).join('');
  return `${heading(actions)}<div class="content">${projectPicker()}${summary}${cards
    ? `<div class="live-metric-grid">${cards}</div>`
    : '<section class="panel"><div class="empty">导入业务指标定义，或生成数据本体后从模型读取指标。</div></section>'}</div>`;
}
function metricInsightBasis(project) {
  return JSON.stringify([project.models, project.source_bindings, project.source?.schema,
    metricDocuments.get(project.id)?.definitions]);
}
function currentMetricInsights(project) {
  const snapshot = metricInsights.get(project?.id);
  return snapshot && snapshot.basis === metricInsightBasis(project) ? snapshot.items : null;
}
function openMetricDocument() {
  modal('导入指标定义', `<label class="field" for="metric-definition-file">业务资料
    <input type="file" id="metric-definition-file" accept=".md,.markdown,.txt,.json"></label>
    <p>Markdown 或文本用一级标题写指标名称，在正文注明 <code>指标 ID: MTR-TXN-001</code>，用二级标题分节。多指标可上传包含 <code>metrics</code> 数组的 JSON。</p>
    <p id="metric-document-error" role="alert" class="live-stale-notice" hidden></p>`);
}
async function importMetricDocument(file, input) {
  const id = projectId();
  if (!id || !file) return;
  try {
    if (file.size > 200000) throw new Error('指标定义文件不能超过 200 KB。');
    const bytes = await file.arrayBuffer();
    if (projectId() !== id || !input.isConnected || input.files[0] !== file) return;
    let source;
    try {
      source = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    } catch {
      throw new Error('请使用 UTF-8 编码保存指标定义。');
    }
    const definitions = parseMetricDefinitions(source);
    metricDocuments.set(id, { name: file.name, definitions });
    metricInsights.delete(id);
    closeModal();
    render();
    root.querySelector('#metric-insight-generate')?.focus();
  } catch (error) {
    const message = input.closest('.modal')?.querySelector('#metric-document-error');
    if (!message || input.files[0] !== file) return;
    message.textContent = error.message;
    message.hidden = false;
    input.value = '';
  }
}
function showMetricInsight(index) {
  const insight = currentMetricInsights(state.project)?.[index];
  if (!insight) return;
  const sections = insight.sections.map(({ title, rows, text, code, source }) => `<section class="live-metric-insight-section">
    <h3>${e(title)}</h3>${text ? `<p>${e(text)}</p>` : ''}${rows?.length ? `<dl>${rows.map(({ label, value, source: rowSource }) => `<div><dt>${e(label)}</dt><dd>${e(value)}${rowSource ? `<small>${e(rowSource)}</small>` : ''}</dd></div>`).join('')}</dl>` : ''}
    ${code ? `<pre class="live-code">${e(code)}</pre>` : ''}${source ? `<small>${e(source)}</small>` : ''}</section>`).join('');
  modal(insight.name, `<div class="live-metric-insight-intro"><code>${e(insight.id)}</code>${pill('模拟洞察', 'amber')}</div>${sections}`,
    button('查看场景分析', 'nav', 'data-route="data3"'));
  overlay.querySelector('.modal').classList.add('live-metric-insight-modal');
}
function runs() {
  const p = state.project;
  let values = p?.runs || p?.analyses || p?.reports || [];
  if (!Array.isArray(values)) values = Object.values(values);
  return values
    .filter(
      (x) =>
        x &&
        (x.sql ||
          x.report ||
          x.answer ||
          x.rows ||
          x.result?.report ||
          x.result?.answer ||
          x.result?.sql),
    )
    .map((x) => (x.result ? { ...x, ...x.result } : x));
}
function analysisFlows(p) {
  const flows = (p?.models?.['m6-flow-model.yaml']?.flows || []).filter(
    (flow) => flow.flowType === 'ANALYSIS',
  );
  if (!flows.length) return '';
  const metrics = new Map(
    (p.models['m7-report-model.yaml']?.metrics || []).map((metric) => [metric.id, metric.name]),
  );
  const activityLabels = {
    DATA_COLLECT: '数据采集',
    STAT_COMPUTE: '统计计算',
    AI_REASONING: '分析推理',
    REPORT_ASSEMBLE: '报告汇总',
  };
  const runnable = new Map(state.scenarioOptions.map((scenario) => [scenario.id, scenario]));
  const flowDefinitions = flows.map((flow) => {
    const steps = flow.activities || [];
    const names = (flow.keyMetrics || []).map((id) => metrics.get(id) || id);
    const stepList = steps.map((step) => `<li><strong>${e(step.label || step.name || activityLabels[step.activityType] || step.activityType || step.activityId)}</strong>${step.description ? `<span>${e(step.description)}</span>` : ''}</li>`).join('');
    const plan = runnable.get(flow.id);
    const run = plan
      ? `<div class="live-action-row">${button('执行原版场景', 'run-upstream-scenario', `data-scenario="${e(flow.id)}" ${state.busy || !state.provider?.configured ? 'disabled' : ''}`, true)}<span class="live-subtle">原版计划 ${plan.step_count} 步 · SQL、统计与 GLM 报告</span></div>`
      : '';
    return `<details class="live-analysis-flow"><summary><strong>${e(flow.name || flow.id)}</strong><span>${steps.length} 个模型步骤</span></summary><div class="live-flow-definition">${run}${flow.description ? `<p>${e(flow.description)}</p>` : ''}${names.length ? `<p><strong>关联指标</strong> ${e(names.join('、'))}</p>` : ''}${stepList ? `<ol>${stepList}</ol>` : ''}</div></details>`;
  }).join('');
  return `<section class="panel live-analysis-flows"><div class="panel-head"><h2>M6 分析流程</h2>${pill(flows.length + ' 个流程')}</div><div class="panel-body">${flowDefinitions}</div></section>`;
}
function analysisPage(reportsOnly = false) {
  const p = state.project;
  const history = runs();
  return `${heading(sectionNav('resources', '数据来源'))}<div class="content">${projectPicker()}${
    !p
      ? emptyProject()
      : `${state.route === 'data3' ? '<div id="scenario-insight-workbench"></div>' + analysisFlows(p) : ''}${reportsOnly ? '' : analysisQuestionPanel(p)}<div class="live-analysis-history">${ 
          history
            .slice()
            .reverse()
            .map((r, i) => analysisResult(r, history.length - i))
            .join('') ||
          `<section class="panel"><div class="empty"><h2>暂无分析结果</h2><p>${reportsOnly ? '执行分析后，查询与报告会保存在当前项目中。' : '连接数据库、确认需求并生成数据本体后，可以开始查询。'}</p>${sectionNav(reportsOnly ? 'analytics' : 'data1', reportsOnly ? '开始分析' : '准备分析需求')}</div></section>`
        }</div>`
  }</div>`;
}
function analysisQuestionPanel(project) {
  const question = draft('analysis');
  const needsConfirmation = project.model_standard === SEVEN_MODEL_STANDARD && !confirmed('models');
  const blocked = !project.source || !hasModels() || needsConfirmation;
  const prompt = !project.source ? '请先连接数据库。'
    : !hasModels() ? '请先生成数据本体。'
      : needsConfirmation ? '请先确认数据本体和字段映射。' : '';
  return `<section class="panel live-query-panel">
    <header class="panel-head"><div><h2>自定义查询</h2>${project.source ? `<small>${e(project.source.name || project.source.filename)}</small>` : ''}</div></header>
    <div class="panel-body">
      <form id="analysis-form">
        <label class="sr-only" for="analysis-question">分析问题</label>
        <div class="agent-chat-composer">
          <elastic-textarea class="agent-chat-input"><textarea id="analysis-question" data-draft="analysis" rows="1" placeholder="输入要分析的问题…" maxlength="12000" required>${e(question)}</textarea></elastic-textarea>
          <button type="submit" class="primary send-message" aria-keyshortcuts="Enter" data-blocked="${blocked}" ${blocked || !canSendChat(question) ? 'disabled' : ''}>${jobPending(pageJob()) ? '执行中…' : '发送'}</button>
        </div>
        ${prompt ? `<p class="agent-chat-status">${prompt}</p>` : !state.provider?.configured ? '<a class="agent-chat-status" href="#settings">配置模型服务后即可发送</a>' : ''}
      </form>
    </div>
  </section>`;
}
function analysisResult(r, index) {
  if (r.run_kind === 'upstream-scenario') return upstreamScenarioResult(r);
  const rows = r.rows || r.data?.rows || [];
  const columns = r.columns || (rows[0] && !Array.isArray(rows[0]) ? Object.keys(rows[0]) : []);
  const sql = typeof r.sql === 'string' ? r.sql : JSON.stringify(r.sql || r.queries || [], null, 2);
  const report = r.report || r.answer || r.explanation || r.summary || '';
  return `<article class="panel live-result ${r.stale ? 'live-result-stale' : ''}"><div class="panel-head"><div><h2>${e(r.question || r.title || '分析 ' + index)}</h2><small>${formatDate(r.created_at || r.executed_at || r.timestamp)}</small></div>${pill(r.stale ? '已失效' : rows.length + ' 行', r.stale ? 'amber' : 'green')}</div><div class="panel-body">${r.stale ? `<div class="live-stale-notice">${e(r.stale_reason || '需求、来源或模型已变化；此记录保留为历史，不代表当前版本的分析结果。')}</div>` : ''}${report ? `<div class="live-document">${reportContent(report)}</div>` : ''}${chartSvg(r.chart, rows, columns)}${
    columns.length
      ? `<div class="table-wrap live-result-table"><table><thead><tr>${columns.map((c) => `<th>${e(typeof c === 'object' ? c.name : c)}</th>`).join('')}</tr></thead><tbody>${rows
          .map(
            (row) =>
              `<tr>${columns.map((c, j) => `<td>${e(Array.isArray(row) ? row[j] : row[typeof c === 'object' ? c.name : c])}</td>`).join('')}</tr>`,
          )
          .join('')}</tbody></table></div>`
      : ''
  }<details class="live-query-evidence"><summary>查看 SQL、数据来源与执行依据</summary><pre class="live-code">${e(sql)}</pre>${r.parameters && Object.keys(r.parameters).length ? `<pre class="live-code">${e(JSON.stringify(r.parameters, null, 2))}</pre>` : ''}<dl class="live-evidence"><dt>数据来源摘要</dt><dd>${e(r.source_hash || r.source?.hash || '—')}</dd><dt>行数</dt><dd>${rows.length}</dd></dl>${r.provenance ? `<pre class="live-code">${e(JSON.stringify(r.provenance, null, 2))}</pre>` : ''}${r.warnings?.length ? `<p>${e(r.warnings.join('；'))}</p>` : ''}</details></div></article>`;
}
function upstreamScenarioResult(run) {
  const steps = Array.isArray(run.steps) ? run.steps : [];
  const name = run.scenario_name || run.title || run.scenario_id;
  const sourceHash = run.source_sha256 || run.source_hash;
  const evidence = steps.map((step) => {
    const rows = Array.isArray(step.rows) ? step.rows : [];
    const summary = step.summary || step.result || step.statistics;
    const preview = rows.length ? `<pre class="live-code">${e(JSON.stringify(rows.slice(0, 8), null, 2))}</pre>` : '';
    return `<details><summary>${e(step.id)} · ${e(step.description || step.type)}${Number.isInteger(step.row_count) ? ' · ' + e(step.row_count) + ' 行' : ''}</summary>${step.sql ? `<pre class="live-code">${e(step.sql)}</pre>` : ''}${preview}${summary ? `<pre class="live-code">${e(JSON.stringify(summary, null, 2))}</pre>` : ''}</details>`;
  }).join('');
  return `<article class="panel live-result ${run.stale ? 'live-result-stale' : ''}"><div class="panel-head"><div><h2>${e(name)}</h2><small>${formatDate(run.created_at)}</small></div>${pill(run.stale ? '已失效' : steps.length + ' 个步骤', run.stale ? 'amber' : 'green')}</div><div class="panel-body">${run.stale ? `<p class="live-stale-notice">${e(run.stale_reason)}</p>` : ''}<div class="live-document">${reportContent(run.report)}</div><div class="live-assembly-evidence"><h3>执行依据</h3>${sourceHash ? `<details><summary>数据源校验摘要</summary><code>${e(sourceHash)}</code></details>` : ''}${evidence}</div></div></article>`;
}
function reportContent(report) {
  if (!report || typeof report !== 'object' || !report.summary) return markdown(report);
  return (
    markdown(report.summary) +
    [
      ['findings', '主要发现'],
      ['limitations', '范围与待核查事项'],
      ['recommendations', '建议行动'],
    ]
      .map(([key, title]) =>
        Array.isArray(report[key]) && report[key].length
          ? `<section class="live-report-section"><h3>${title}</h3>${report[key].map((item) => `<p class="live-md-bullet">• ${inline(typeof item === 'string' ? item : JSON.stringify(item))}</p>`).join('')}</section>`
          : '',
      )
      .join('')
  );
}
function chartSvg(chart, rows, columns) {
  if (!rows.length || columns.length < 2 || chart?.type === 'table') return '';
  const keys = columns.map((c) => (typeof c === 'object' ? c.name : c));
  const valueKey =
    (Array.isArray(chart?.y) ? chart.y[0] : chart?.y) ||
    chart?.y_key ||
    keys.find(
      (k, i) => i > 0 && rows.some((r) => Number.isFinite(Number(Array.isArray(r) ? r[i] : r[k]))),
    );
  const index = keys.indexOf(valueKey);
  if (index < 0) return '';
  const data = rows
    .slice(0, 24)
    .map((r) => ({
      label: String(
        Array.isArray(r) ? r[Math.max(0, keys.indexOf(chart?.x))] : r[chart?.x || keys[0]],
      ),
      value: Number(Array.isArray(r) ? r[index] : r[valueKey]),
    }))
    .filter((d) => Number.isFinite(d.value));
  if (!data.length) return '';
  const title = chart?.title || valueKey;
  const caption = e(title) + (rows.length > 24 ? ' · 前24组' : '');
  const w = 760,
    h = 230,
    space = (w - 80) / data.length;
  const colors = ['#4c7fe1', '#71a6e4', '#89bbbe', '#9e94d2', '#c4a97d', '#88b594', '#a7bad9'];
  let plot = '';
  if (chart?.type === 'pie') {
    if (data.some((d) => d.value < 0) || !data.some((d) => d.value > 0)) return '';
    const total = data.reduce((sum, d) => sum + d.value, 0);
    let angle = -Math.PI / 2;
    plot = data
      .map((d, i) => {
        const next = angle + (d.value / total) * Math.PI * 2;
        const x1 = 210 + 85 * Math.cos(angle),
          y1 = 112 + 85 * Math.sin(angle),
          x2 = 210 + 85 * Math.cos(next),
          y2 = 112 + 85 * Math.sin(next);
        const shape =
          d.value === total
            ? `<circle cx="210" cy="112" r="85" fill="${colors[i % colors.length]}"/>`
            : `<path d="M210 112 L${x1} ${y1} A85 85 0 ${next - angle > Math.PI ? 1 : 0} 1 ${x2} ${y2} Z" fill="${colors[i % colors.length]}"/>`;
        angle = next;
        return `<g><title>${e(d.label)}: ${e(d.value)}</title>${shape}${i < 9 ? `<rect x="380" y="${22 + i * 22}" width="9" height="9" rx="2" fill="${colors[i % colors.length]}"/><text x="398" y="${31 + i * 22}" font-size="11" fill="#7088aa">${e(d.label.slice(0, 18))} · ${e(d.value)}</text>` : ''}</g>`;
      })
      .join('');
  } else {
    const min = Math.min(0, ...data.map((d) => d.value));
    const max = Math.max(0, ...data.map((d) => d.value));
    const range = Math.max(1, max - min);
    const y = (value) => 190 - ((value - min) / range) * 165;
    const zero = y(0);
    plot = `<path d="M40 18V190H740M40 ${zero}H740" fill="none" stroke="#d8e0eb"/>`;
    if (chart?.type === 'line')
      plot += `<polyline points="${data.map((d, i) => `${45 + i * space + (space - 14) / 2},${y(d.value)}`).join(' ')}" fill="none" stroke="#4c7fe1" stroke-width="2"/>`;
    plot += data
      .map((d, i) => {
        const x = 45 + i * space;
        const center = x + (space - 14) / 2;
        const valueY = y(d.value);
        const shape =
          chart?.type === 'line'
            ? `<circle cx="${center}" cy="${valueY}" r="3" fill="#4c7fe1"/>`
            : `<rect x="${x}" y="${Math.min(zero, valueY)}" width="${Math.max(6, space - 14)}" height="${Math.abs(valueY - zero)}" rx="2" fill="#4c7fe1"/>`;
        return `<g><title>${e(d.label)}: ${e(d.value)}</title>${shape}<text x="${center}" y="${Math.max(14, valueY - 7)}" text-anchor="middle" font-size="9" fill="#647795">${e(d.value.toLocaleString(undefined, { maximumFractionDigits: 2 }))}</text><text x="${center}" y="211" text-anchor="middle" font-size="8" fill="#7d8ba0">${e(d.label.slice(0, 8))}</text></g>`;
      })
      .join('');
  }
  return `<figure class="live-chart"><figcaption>${caption}</figcaption><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${e(title)} 数据图表">${plot}</svg></figure>`;
}

function documents() {
  const docs =
    state.project?.documents ||
    state.project?.knowledge?.documents ||
    state.project?.source?.documents ||
    [];
  return Array.isArray(docs) ? docs : Object.entries(docs).map(([name, text]) => ({ name, text }));
}
function knowledgeInventory(p) {
  const inventory = p.knowledge_inventory;
  if (!inventory) return '';
  const review = inventory._review;
  const counts = review ? [['scenarios', '场景'], ['concepts', '概念'], ['entities', '实体']]
    .map(([key, title]) => `${title} ${review.before_counts[key]} → ${review.after_counts[key]}`).join(' · ') : '';
  const dedup = review ? `<details ${review.merges.length ? 'open' : ''}><summary>候选合并记录 · ${e(counts)}</summary>${review.merges.length ? `<ul>${review.merges.map((merge) => `<li>${e([...merge.merged_ids, merge.kept_id].join(' + '))} → ${e(merge.kept_id)}。${e(merge.reason)}</li>`).join('')}</ul>` : '<p>本轮没有合并候选。</p>'}</details>` : '';
  return `<section class="live-inventory"><div class="live-inventory-heading"><h3>知识候选清单</h3>${p.inventory_confirmation ? pill('清单已确认', 'green') : button('确认候选清单', 'confirm', `data-scope="inventory" ${state.busy ? 'disabled' : ''}`, true)}</div>${dedup}<div class="live-inventory-grid">${[
    ['scenarios', '场景'],
    ['concepts', '概念'],
    ['entities', '实体'],
  ]
    .map(
      ([key, title]) =>
        `<details ${(inventory[key] || []).length ? 'open' : ''}><summary>${title} · ${(inventory[key] || []).length} 项</summary>${(inventory[key] || []).map((item) => `<article><strong>${e(item.name || item.id)}</strong><p>${e(item.define || item.definition || item.description || '')}</p><small>${e((item.sources || []).map((source) => (typeof source === 'string' ? source : source.path || source.name || JSON.stringify(source))).join(' · '))}</small></article>`).join('')}</details>`,
    )
    .join('')}</div></section>`;
}
function knowledgePage() {
  const p = state.project;
  const headingHtml = heading(button('新建知识库', 'new-project', 'data-kind="knowledge"', true));
  const picker = projectPicker({ showNew: false });
  if (!p) return `${headingHtml}<div class="content">${picker}${emptyProject()}</div>`;
  const docs = documents();
  const answers = (p.answers || p.knowledge_answers || p.qa || []).concat(runs().filter((entry) => entry.answer));
  const models = hasModels();
  const modelsConfirmed = models && confirmed('models');
  const waitingForReview = p.knowledge_inventory && !p.inventory_confirmation;
  const extractLabel = p.knowledge_inventory ? '生成知识模型' : '整理候选清单';
  const question = draft('knowledge-question');
  return `${headingHtml}<div class="content">
    ${picker}
    <section class="panel">
      <header class="panel-head"><h2>原文资料</h2>${pill(docs.length + ' 份文档')}</header>
      <div class="panel-body">
        <form id="knowledge-upload-form">
          <label class="live-upload-zone compact" for="knowledge-files">${icon('file')}<strong>选择 Markdown 或文本资料</strong><input id="knowledge-files" type="file" accept=".md,.txt" multiple required></label>
          <div class="live-action-row">
            <button type="submit" ${state.busy ? 'disabled' : ''}>导入原文</button>
            ${waitingForReview ? '' : button(extractLabel, 'knowledge-extract', state.busy || !docs.length ? 'disabled' : '', true)}
            ${sectionNav('knowledge-models', '查看知识模型')}
            ${models && !modelsConfirmed ? button('确认知识模型', 'confirm', `data-scope="models" ${state.busy ? 'disabled' : ''}`) : ''}
          </div>
        </form>
        ${knowledgeInventory(p)}
        <div class="live-doc-grid">
          ${docs.map((doc, index) => `<details class="live-raw-doc"><summary>${icon('file')}<strong>${e(doc.name || doc.filename || doc.id || '原文 ' + (index + 1))}</strong></summary><div class="live-document">${markdown(doc.text || doc.content || doc.preview || '暂无原文预览。')}</div></details>`).join('')}
        </div>
      </div>
    </section>
    <section class="panel live-knowledge-qa">
      <header class="panel-head"><h2>知识问答</h2>${pill(modelsConfirmed ? '知识模型已确认' : models ? '知识模型待确认' : '待生成模型', modelsConfirmed ? 'green' : '')}</header>
      <div class="panel-body">
        <div class="live-knowledge-answers">
          ${answers.map((a) => `<article class="live-knowledge-answer">
            <h3>${e(a.question || '知识问题')}${a.stale ? pill('已失效', 'amber') : ''}</h3>
            ${a.stale ? `<p class="live-stale-notice">${e(a.stale_reason || '原文或知识模型已变化，此回答仅作为历史记录。')}</p>` : ''}
            <div class="live-document">${markdown(a.answer || a.report || a.content)}</div>
            ${a.sources ? `<details><summary>原文依据</summary><pre class="live-code">${e(JSON.stringify(a.retrieval ? {检索链路: a.retrieval, 原文引证: a.citations || a.sources} : a.citations || a.sources, null, 2))}</pre></details>` : ''}
          </article>`).join('')}
        </div>
        <form id="knowledge-ask-form">
          <label class="sr-only" for="knowledge-question">知识问题</label>
          <div class="agent-chat-composer">
            <elastic-textarea class="agent-chat-input"><textarea id="knowledge-question" data-draft="knowledge-question" rows="1" maxlength="12000" placeholder="根据已导入的原文提出问题…" required>${e(question)}</textarea></elastic-textarea>
            <button type="submit" class="primary send-message" aria-keyshortcuts="Enter" ${modelsConfirmed && canSendChat(question) ? '' : 'disabled'}>发送</button>
          </div>
          ${!modelsConfirmed ? '<p class="agent-chat-status">请先生成并确认知识模型。</p>' : !state.provider?.configured ? '<a class="agent-chat-status" href="#settings">配置模型服务后即可发送</a>' : ''}
        </form>
      </div>
    </section>
  </div>`;
}
function prototypeIsStale(project) {
  if (project.business_model_import) return false;
  const sha = project.requirements_document?.sha256;
  return !project.requirements || requirementsOutdated(project) ||
    !!(sha && project.prototype?.requirements_sha256 && sha !== project.prototype.requirements_sha256);
}
function templatePrototypeEnabled() {
  return state.status?.capabilities?.prototype_template_v9 === true;
}
function prototypeTitle(project) {
  return project.prototype?.title || `${project.name} · 界面原型`;
}
function prototypeRunLink(project, label) {
  const parameters = new URLSearchParams({ project: project.id });
  return `<a class="live-button prd-run-link" href="#prototype-run?${e(parameters)}" target="_blank" rel="noopener" aria-label="运行" title="在独立页面运行">${label}</a>`;
}
function contractDemoLink(project) {
  const source = project?.upstream_import?.source;
  if (PROTOTYPE_WORKBENCH_TEMPLATES.has(project?.prototype?.template_version) ||
      source?.repository !== 'https://github.com/sharptoolbox/ontology-driven-dev' ||
      source?.source_folder !== 'reference-example') return '';
  return '<a class="live-button" href="/contract-demo/" target="_blank" rel="noopener">开票交互演示</a>';
}
function prototypeJobStatus(job) {
  if (!job || job.status === 'succeeded') return '';
  const editing = job.kind === PROTOTYPE_EDIT_JOB;
  const labels = {
    queued: editing ? '等待修订' : '等待生成',
    running: editing ? '正在修订' : '正在生成',
    failed: editing ? '修订失败' : '生成失败',
    cancelled: '已取消',
    interrupted: '已中断',
  };
  const label = job.cancel_requested && jobPending(job) ? '正在取消' : labels[job.status];
  const detail = job.cancellation_error || job.observation_error || job.error;
  return `<div class="prd-prototype-job"><span role="status">${pill(label, 'amber')}</span>${jobControls(job)}${detail ? `<small role="status">${e(detail)}</small>` : ''}</div>`;
}
function prototypeDeletionBlocker(project) {
  if (project.reference_adoption || project.prototype?.reference_original) return '固定参考原型';
  if (project.business_model_import) return '请通过七模型导入更新原型';
  if (project.active_job_id || jobPending(pageJob('interface', project))) return '请等待项目任务结束';
  return '';
}
function prototypeRegistry() {
  const projects = businessRegistryProjects((project) => {
    const job = pageJob('interface', project);
    return project.prototype || (job && job.status !== 'succeeded');
  });
  const template = templatePrototypeEnabled();
  return `<section class="panel prd-registry">${projects.length ? `<div class="table-wrap"><table class="${template ? 'prd-prototype-table' : ''}"><thead><tr><th scope="col">需求文档标题</th><th scope="col">界面原型标题</th><th scope="col">生成时间</th><th scope="col" class="prd-prototype-status">${template ? '生成状态' : '状态'}</th><th scope="col">操作</th></tr></thead><tbody>${projects.map((project) => {
    const prototype = project.prototype;
    const job = pageJob('interface', project);
    const id = `data-id="${e(project.id)}" ${state.busy ? 'disabled' : ''}`;
    const stale = !!prototype && prototypeIsStale(project);
    const revise = !stale && !state.busy && !project.reference_adoption && !project.business_model_import && confirmed('requirements', project) && state.provider?.configured;
    const reviewLabel = stale ? '历史版本' : confirmed('prototype', project) ? '校核记录' : '校核与确认';
    const review = prototype ? button(reviewLabel, 'prd-prototype-review', `${id} class="prd-title-button"`) : '';
    const editAttributes = `${id} ${revise ? '' : 'disabled'} aria-label="编辑" aria-haspopup="dialog" title="${revise ? '编辑' : '编辑需要可用模型和对应当前需求的原型'}"`;
    const deletionBlocker = prototypeDeletionBlocker(project);
    const deleteAttributes = `${id} data-prototype-id="${e(prototype?.id)}" data-revision="${e(project.revision)}" ${deletionBlocker ? 'disabled' : ''} aria-label="删除" aria-haspopup="dialog" title="${e(deletionBlocker || '删除')}"`;
    const actions = prototype ? (
      button(icon('edit'), template ? 'prd-prototype-edit' : 'prd-prototype-revise', editAttributes) +
      prototypeRunLink(project, icon('run')) +
      button(icon('trash'), 'prd-prototype-delete', deleteAttributes)
    ) : '';
    const createdAt = prototype && (prototype.created_at || prototype.generated_at || project.updated_at);
    const demo = contractDemoLink(project);
    return `<tr><td>${e(prototype?.requirements_title || requirementTitle(project))}</td><th scope="row">${e(prototypeTitle(project))}${demo ? `<div class="prd-prototype-demo">${demo}</div>` : ''}</th><td class="prd-date">${e(formatDate(createdAt, true) || '—')}</td><td class="prd-prototype-status">${prototypeJobStatus(job) || review}</td><td>${actions ? `<div class="prd-row-actions prd-prototype-actions">${actions}</div>` : ''}</td></tr>`;
  }).join('')}</tbody></table></div>` : '<div class="panel-body live-inline-empty"><h2>暂无界面原型</h2><p>可在需求文档中发起原型生成。</p></div>'}</section>`;
}
function prototypeEditDraft(project) {
  return `prototype-edit:${project.prototype.id}`;
}
const prototypePreviewObserver = new ResizeObserver(() => {
  const frame = prototypeEditorDialog.querySelector('.prd-prototype-preview-viewport > iframe');
  if (!frame?.offsetWidth || !frame.offsetHeight) return;
  const viewport = frame.parentElement.getBoundingClientRect();
  const scale = Math.min(1, viewport.width / frame.offsetWidth, viewport.height / frame.offsetHeight);
  frame.style.setProperty('--prototype-preview-scale', scale);
});
function closePrototypeEditor(restoreFocus = true) {
  const projectId = state.prototypeEditor?.projectId;
  state.prototypeEditor = null;
  if (!prototypeEditorDialog.open) return;
  prototypePreviewObserver.disconnect();
  prototypeEditorDialog.close();
  prototypeEditorDialog.replaceChildren();
  if (restoreFocus && projectId) {
    const buttons = [...root.querySelectorAll('[data-action="prd-prototype-edit"]:enabled')];
    const opener = buttons.find((item) => item.dataset.id === projectId) ||
      buttons.find((item) => !item.dataset.id);
    opener?.focus({ preventScroll: true });
  }
}
function renderPrototypeEditor() {
  const editor = state.prototypeEditor;
  const project = state.project;
  if (!editor) {
    closePrototypeEditor(false);
    return;
  }
  if (state.route !== 'interface' || state.prototypeRun ||
      editor.projectId !== project?.id || editor.prototypeId !== project.prototype?.id) {
    closePrototypeEditor(false);
    return;
  }
  const prototype = project.prototype;
  const title = prototypeTitle(project);
  const opening = !prototypeEditorDialog.open;
  if (opening) {
    prototypeEditorDialog.innerHTML = `
      <header class="prd-prototype-editor-header">
        <div class="prd-prototype-editor-heading">
          <h2 id="prototype-editor-title" tabindex="-1">修订界面原型</h2>
          <p id="prototype-editor-version"></p>
        </div>
        <div class="prd-prototype-editor-controls">
          <div id="prototype-editor-actions" class="prd-prototype-job-actions"></div>
          ${button('×', 'prd-prototype-editor-close', 'class="prd-prototype-editor-close" aria-label="关闭修订弹窗"')}
        </div>
      </header>
      <p id="prototype-editor-error" class="live-stale-notice" role="alert" hidden></p>
      <div class="prd-prototype-editor-layout">
        <section class="prd-prototype-edit-preview" aria-label="原型预览">
          <div class="prd-prototype-preview-viewport"></div>
        </section>
        <section class="prd-prototype-chat" aria-label="原型编辑对话">
          <div class="prd-prototype-chat-header"><h3>AI 修订对话</h3></div>
          <div class="prd-prototype-chat-messages" id="prototype-edit-messages" role="log" aria-label="原型修订消息"></div>
          <form id="prototype-edit-chat-form" class="prd-prototype-composer">
            <label class="sr-only" for="prototype-edit-input">修订意见</label>
            <div class="agent-chat-composer">
              <elastic-textarea class="agent-chat-input"><textarea id="prototype-edit-input" maxlength="8000" rows="1" placeholder="描述要调整的页面、字段或操作…" required></textarea></elastic-textarea>
              <button type="submit" class="primary send-message" aria-keyshortcuts="Enter">发送</button>
            </div>
            <div id="prototype-editor-chat-status"></div>
          </form>
        </section>
      </div>`;
  }
  const find = (id) => document.getElementById(id);
  const edit = project.prototype_edit?.prototype_id === prototype.id ? project.prototype_edit : null;
  const messages = Array.isArray(edit?.messages) ? [...edit.messages] : [];
  const job = pageJob('interface', project);
  const submission = submittedInputs.get(job?.id)?.prototypeEdit;
  const pending = job?.kind === PROTOTYPE_EDIT_JOB && jobPending(job);
  if (pending && submission && messages.length === submission.messageCount) messages.push({ role: 'user', content: submission.message });
  const history = messages.length ? messages.map((message) => {
    const user = message.role === 'user';
    return `<article class="live-message ${user ? 'user' : 'assistant'}"><div class="live-message-role">${user ? '你' : '原型 Agent'}${message.created_at ? `<span>${e(formatDate(message.created_at))}</span>` : ''}</div><div class="live-message-text">${markdown(messageContent(message))}</div></article>`;
  }).join('') : '<p class="prd-prototype-edit-intro">告诉 AI 想调整哪一页、哪个字段或操作。候选原型会显示在左侧，核对后可应用为新版本。</p>';
  const candidate = edit?.candidate;
  const draftName = prototypeEditDraft(project);
  const value = draft(draftName);
  const available = !prototypeIsStale(project) && !project.reference_adoption && confirmed('requirements', project);
  find('prototype-editor-version').textContent = `${title} · 修订 ${prototype.revision}`;
  const actions = prototypeJobStatus(job) +
    (candidate ? button('应用为新版本', 'prd-prototype-apply', !available || state.busy ? 'disabled' : '', true) : '');
  if (editor.actions !== actions) {
    find('prototype-editor-actions').innerHTML = actions;
    editor.actions = actions;
  }
  const error = find('prototype-editor-error');
  if (error.textContent !== state.error) error.textContent = state.error;
  error.hidden = !state.error;
  const log = find('prototype-edit-messages');
  if (editor.history !== history) {
    const scroll = log.scrollTop;
    log.innerHTML = history;
    log.scrollTop = scroll;
    editor.history = history;
  }
  const input = find('prototype-edit-input');
  input.dataset.draft = draftName;
  const draftChanged = input.value !== value;
  if (draftChanged) input.value = value;
  input.disabled = !available || !state.provider?.configured;
  input.readOnly = state.busy;
  const send = input.form.querySelector('.send-message');
  send.disabled = input.disabled || !canSendChat(value);
  send.textContent = pending ? '修订中…' : '发送';
  const status = !available ? '<p class="agent-chat-status">请先确认当前需求并生成对应原型。</p>'
    : !state.provider?.configured ? '<a class="agent-chat-status" href="#settings">配置模型服务后即可发送</a>' : '';
  if (editor.status !== status) {
    find('prototype-editor-chat-status').innerHTML = status;
    editor.status = status;
  }
  const showLogin = PROTOTYPE_WORKBENCH_TEMPLATES.has(prototype.template_version) && !editor.entered;
  const previewKey = showLogin ? 'login' : candidate ? `candidate:${candidate.id}` : `current:${prototype.id}`;
  // Keep the live preview mounted while the revision job is polled.
  if (editor.previewKey !== previewKey) {
    const viewport = prototypeEditorDialog.querySelector('.prd-prototype-preview-viewport');
    prototypePreviewObserver.disconnect();
    if (showLogin) viewport.innerHTML = prototypeDemoLogin(title, 'prototype-editor-demo-login');
    else if (candidate) {
      const params = new URLSearchParams({ candidate_id: candidate.id, appearance: 'paas' });
      viewport.innerHTML = `<iframe title="候选界面原型" sandbox="allow-scripts" referrerpolicy="no-referrer"${prototypeAssistantAttributes(candidate)} src="${url(projectUrl('/prototype/edit/html'))}?${e(params)}"></iframe>`;
    } else viewport.innerHTML = prototypeFrame(prototype);
    const frame = viewport.querySelector('iframe');
    if (frame) {
      prototypePreviewObserver.observe(viewport);
      prototypePreviewObserver.observe(frame);
    }
    editor.previewKey = previewKey;
  }
  if (opening) {
    prototypeEditorDialog.showModal();
    find('prototype-editor-title').focus({ preventScroll: true });
  }
  if (opening || draftChanged)
    input.closest('elastic-textarea').update(input);
  if (state.scrollPrototypeEditEnd) log.scrollTop = log.scrollHeight;
  state.scrollPrototypeEditEnd = false;
}
function prototypeDemoLogin(title, formId = 'prototype-demo-login') {
  return `<section class="prd-prototype-login"><form id="${formId}" class="prd-prototype-login-card" aria-labelledby="${formId}-title" autocomplete="off">
    <h2 id="${formId}-title">${e(title)}</h2>
    <p class="prd-prototype-login-note">原型演示 · 账号已预填</p>
    <label>账号<input type="text" value="demo" autocomplete="off" readonly></label>
    <label>密码<input type="password" value="preview-demo" autocomplete="off" readonly></label>
    <button type="submit" class="primary">登录</button>
  </form></section>`;
}
function prototypeRunPage() {
  const p = state.project;
  const title = p?.prototype ? prototypeTitle(p) : '运行界面原型';
  document.title = title + ' · ' + PLATFORM_NAME;
  const artifact = p?.prototype;
  const showLogin = PROTOTYPE_WORKBENCH_TEMPLATES.has(artifact?.template_version) &&
    state.enteredPrototype !== artifact.id;
  let content = `<p role="${state.error ? 'alert' : 'status'}">${e(state.error || (state.loading ? '正在加载界面原型…' : '该项目暂无界面原型。'))}</p>`;
  if (artifact) content = showLogin ? prototypeDemoLogin(title) : prototypeFrame(artifact);
  return `<main class="prd-prototype-run" aria-label="${e(title)}">${content}</main>`;
}
function interfacePage() {
  const p = state.project;
  const prototype = p?.prototype;
  const ready = !!p?.business_model_import || (!!p?.requirements && confirmed('requirements'));
  const registry = adaptiveRequirements();
  const template = templatePrototypeEnabled();
  const taskStatus = registry ? '' : prototypeJobStatus(pageJob());
  const stale = registry && prototype && prototypeIsStale(p);
  const top = heading(sectionNav('requirements', '需求文档') + sectionNav('models', '业务本体'));
  const details = !p ? emptyProject() : `${!ready ? `<div class="live-stale-notice">请先确认当前需求文档，再生成界面原型。${sectionNav('requirements', '核对并确认需求')}</div>` : ''}${prototype ? `<section class="panel live-prototype-panel" id="prd-prototype-detail"><div class="panel-head"><div><h2 id="prd-prototype-review-title" tabindex="-1">${e(prototypeTitle(p))}</h2><small>修订 ${e(prototype.revision)}</small></div>${taskStatus || (stale ? pill('历史版本') : pill(confirmed('prototype') ? '已确认' : '待确认', confirmed('prototype') ? 'green' : 'amber'))}</div><div class="panel-body live-prototype-intro">${prototype.summary ? `<div class="live-document">${markdown(prototype.summary)}</div>` : ''}${prototype.pages?.length ? `<div class="live-prototype-pages">${prototype.pages.map((page) => `<span>${e(typeof page === 'string' ? page : page.name || page.title || page.id)}</span>`).join('')}</div>` : ''}${stale ? '<p class="live-subtle">需求已更新，请重新生成原型。</p>' : ''}</div>${prototypeFrame(prototype)}<div class="live-panel-footer">${template ? prototypeRunLink(p, '全屏运行') : button('全屏预览', 'preview-prototype')}${button(template ? '编辑原型' : '提出修订', template ? 'prd-prototype-edit' : 'revise-prototype', state.busy || stale || !ready || p.reference_adoption || p.business_model_import || !state.provider?.configured ? 'disabled' : '')}${button(confirmed('prototype') ? '原型已确认' : '确认当前原型', 'confirm', `data-scope="prototype" ${state.busy || stale || !ready || confirmed('prototype') ? 'disabled' : ''}`, true)}${button('生成业务本体', 'generate-models', state.busy || !canGenerateModels() ? 'disabled' : '')}</div></section>` : `<section class="panel live-empty"><div class="live-empty-icon">${icon('apps')}</div><h2>从已确认需求生成界面原型</h2><p>确认原型后即可生成业务本体。</p><div class="prd-prototype-job-actions">${button('生成界面原型', 'generate-prototype', state.busy || !ready || !state.provider?.configured ? 'disabled' : '', true)}${taskStatus}</div></section>`}${prototypeHistory(p)}`;
  if (registry) return `${top}<div class="content prd-prototype-workspace">${prototypeRegistry()}${state.prototypeDetails === p?.id && p ? details : ''}</div>`;
  return `${top}<div class="content">${projectPicker()}${details}</div>`;
}
function prototypeAssistantAttributes(prototype) {
  if (!PROTOTYPE_WORKBENCH_TEMPLATES.has(prototype?.template_version)) return '';
  return ` data-prototype-assistant="${e(prototype.id)}" data-project-id="${e(prototype.project_id)}"`;
}
function prototypeFrame(prototype, historical = false) {
  const parameters = new URLSearchParams({ version: prototype.sha256 || prototype.id || '', appearance: 'paas' });
  if (historical) parameters.set('prototype_id', prototype.id);
  return `<iframe class="live-application-frame live-prototype-frame" title="${historical ? '历史' : '当前'}界面原型" sandbox="allow-scripts" referrerpolicy="no-referrer"${prototypeAssistantAttributes(prototype)} src="${url(projectUrl('/prototype/html'))}?${e(parameters)}"></iframe>`;
}
function prototypeHistory(p) {
  const history = p.prototype_history || [];
  if (!history.length) return '';
  return `<section class="panel live-source-summary"><div class="panel-head"><h2>原型修订记录</h2>${pill(history.length + ' 条历史')}</div><div class="panel-body live-history-list">${history
    .slice()
    .reverse()
    .map(
      (prototype, index) =>
        `<article><div><strong>原型修订 ${e(prototype.revision)}</strong><small>${formatDate(prototype.created_at)}</small>${prototype.feedback || prototype.summary ? `<p>${e(prototype.feedback || prototype.summary)}</p>` : ''}</div>${button('查看历史原型', 'preview-prototype', `data-history="${history.length - index - 1}"`)}</article>`,
    )
    .join('')}</div></section>`;
}
function previewPrototype(index) {
  const historical = index !== undefined;
  const prototype = historical
    ? state.project.prototype_history?.[Number(index)]
    : state.project.prototype;
  if (!prototype) return;
  modal(
    `${historical ? '历史原型' : '当前原型'} · 修订 ${prototype.revision}`,
    `${historical ? '<p class="live-stale-notice">此原型仅供历史回看，不代表当前确认版本。</p>' : ''}${prototypeFrame(prototype, historical)}`,
  );
  overlay.querySelector('.modal').classList.add('live-preview-modal');
}
function candidateAcceptance(build) {
  const plan = build.acceptance_plan;
  if (!plan) return '';
  const record = build.acceptance;
  const key = (check) => `acceptance-${plan.id}-${check.id}`;
  const checks = plan.checks.map((check) => {
    const saved = record?.checks?.find((item) => item.id === check.id)?.result || '';
    const checked = draft(`${key(check)}-passed`, saved ? 'true' : 'false') === 'true';
    return `<fieldset><legend>${e(check.label)}</legend><label class="field">实际用例和结果<small>${e(check.detail)}</small><textarea name="${e(check.id)}" data-draft="${e(key(check))}" minlength="4" maxlength="1200" rows="2" required>${e(draft(key(check)) || saved)}</textarea></label><label class="live-acknowledgement"><input type="checkbox" name="passed-${e(check.id)}" data-draft="${e(key(check))}-passed" ${checked ? 'checked' : ''} required>此项已通过</label></fieldset>`;
  }).join('');
  return `<form id="candidate-acceptance-form" class="live-project-form" data-candidate-id="${e(plan.id)}"><div class="panel-head"><h3>候选验收记录</h3>${record?.candidate_id === plan.id ? pill('已记录', 'green') : pill('待验收', 'amber')}</div>${checks}<button type="submit" ${state.busy || dirtyModelFiles().length ? 'disabled' : ''}>校验候选并保存验收</button></form>`;
}
function publishPage() {
  const p = state.project;
  if (!p)
    return `${heading(sectionNav('build', '应用开发'))}<div class="content">${projectPicker()}${emptyProject()}</div>`;
  const releases = p.releases || [];
  const history = p.build_history || [];
  const waitingAcceptance = !!p.build?.acceptance_plan && p.build.acceptance?.candidate_id !== p.build.acceptance_plan.id;
  return `${heading(sectionNav('build', '应用开发'))}<div class="content">${projectPicker()}<section class="panel live-release-candidate"><div class="panel-head"><div><h2>当前构建候选</h2><small>${e(p.name)} · 项目修订 ${e(p.revision)}</small></div>${pill(hasBuild() ? '等待发布' : '待构建', hasBuild() ? 'amber' : '')}</div><div class="panel-body">${hasBuild() ? `<p>先在候选应用中检查页面、权限和业务操作，再确认发布独立版本。</p><div class="live-action-row"><a class="live-button" href="${e(candidateUrl())}" target="_blank" rel="noopener">预览当前候选 ↗</a><a class="live-button" href="${url(projectUrl('/download'))}">下载当前开发包 ↓</a></div>${candidateAcceptance(p.build)}<form id="release-form"><label class="field">发布版本名称<input id="release-version" data-draft="release-version" maxlength="100" value="${e(draft('release-version'))}" placeholder="例如 1.0.0" required></label><label class="live-acknowledgement"><input id="release-acknowledged" data-draft="release-acknowledged" type="checkbox" ${draft('release-acknowledged') === 'true' ? 'checked' : ''} required>我已检查候选版本，确认发布。</label><div class="live-action-row"><button type="submit" class="primary" data-requires-saved data-blocked="${waitingAcceptance}" ${state.busy || dirtyModelFiles().length || waitingAcceptance ? 'disabled' : ''}>发布版本</button></div></form>` : `<div class="live-inline-empty"><h3>当前没有可发布的构建</h3><p>确认原型和业务本体后，构建可发布版本。</p>${button('打开开发配置', 'open-development', `data-id="${e(p.id)}"`)}</div>`}</div></section><section class="panel live-source-summary"><div class="panel-head"><h2>已发布版本</h2>${pill(releases.length + ' 个版本', releases.length ? 'green' : '')}</div>${
    releases.length
      ? `<div class="table-wrap"><table class="live-release-table"><thead><tr><th>版本</th><th>发布时间</th><th>构建依据</th><th>操作</th></tr></thead><tbody>${releases
          .slice()
          .reverse()
          .map(
            (release) =>
              `<tr><td><strong>${e(release.version)}</strong>${latestRelease(p)?.id === release.id ? pill('当前入口', 'green') : ''}</td><td>${formatDate(release.created_at)}</td><td>${e(release.build?.version || release.build?.id || release.id)}<details><summary>版本记录</summary><pre class="live-code">${e(JSON.stringify(release, null, 2))}</pre></details></td><td><a class="live-button primary" href="${e(release.url)}" target="_blank" rel="noopener">进入应用 ↗</a></td></tr>`,
          )
          .join('')}</tbody></table></div>`
      : '<div class="empty">暂无已发布版本。</div>'
  }</section>${
    history.length
      ? `<section class="panel live-source-summary"><div class="panel-head"><h2>历史构建记录</h2>${pill(history.length + ' 条')}</div><div class="panel-body live-history-list">${history
          .slice()
          .reverse()
          .map(
            (build) =>
              `<details><summary>构建 ${e(build.version || build.id || '历史候选')} · ${formatDate(build.created_at)}</summary><pre class="live-code">${e(JSON.stringify(build, null, 2))}</pre></details>`,
          )
          .join('')}</div></section>`
      : ''
  }</div>`;
}
function settingsPage() {
  const s = state.status || {};
  return `${heading(button('重新检查', 'refresh-status'))}<div class="content"><section class="panel"><div class="panel-head"><h2>模型与运行服务</h2>${pill(state.provider?.configured ? '已配置' : '待配置', state.provider?.configured ? 'green' : 'amber')}</div><div class="panel-body"><dl class="live-settings"><dt>模型服务</dt><dd>${e(state.provider?.name || '—')}</dd><dt>当前模型</dt><dd>${e(state.provider?.model || '—')}</dd><dt>凭据</dt><dd>${state.provider?.configured ? '由服务端环境配置提供' : '请在本机服务端配置模型 API 凭据'}</dd><dt>项目与任务</dt><dd>本机持久化保存，可刷新后继续查看</dd><dt>运行状态</dt><dd>${e(s.status || s.version || '运行中')}</dd></dl><p class="live-subtle">调用模型时会将当前任务所需的需求、模型或查询结果发送给已配置的服务商。</p></div></section>${resourceBaselines()}<section class="panel live-source-summary"><div class="panel-head"><h2>开源资源与采用范围</h2></div><div class="panel-body"><div class="live-reference-grid">${[
    ['ontology-driven-dev', '需求确认、七模型与应用构建流程'],
    ['ontology_modeling_framework', 'V9 建模规范与一致性要求'],
    ['Onto-Model', '模型浏览、维护和引用方法'],
    ['Onto-DataAnalyse', '数据建模、字段映射与分析执行'],
    ['LLM-knowledgeBase', '两轮知识萃取与来源约束问答'],
  ]
    .map(
      ([name, desc]) =>
        `<a href="https://github.com/sharptoolbox/${name}" target="_blank" rel="noopener"><strong>${name} ↗</strong><span>${desc}</span></a>`,
    )
    .join('')}</div></div></section></div>`;
}
function resourceBaselines() {
  const source =
    'https://github.com/sharptoolbox/ontology-driven-dev/blob/08686470f7af0a9095a000744d69e28c80f74534/';
  const resources = [
    [
      '需求与模型规范',
      'V9 需求分析与建模指南',
      'references/AI需求探索与确认提示词V9.0.md',
    ],
    ['技术底座', '应用构建与运行', 'techbase/README.md'],
    [
      '界面规范',
      '界面原型与操作规范',
      'references/UI-UE界面设计规范.md',
    ],
    ['工作技能', '项目开发与验收流程', 'SKILL.md'],
  ];
  const p = state.project;
  return `<section class="panel live-source-summary live-resource-baselines"><div class="panel-head"><div><h2>四类内置资源</h2><small>固定内置基线</small></div></div><div class="panel-body"><div class="live-baseline-grid">${resources.map(([title, description, path]) => `<article><h3>${e(title)}</h3><p>${e(description)}</p><a href="${source + path.split('/').map(encodeURIComponent).join('/')}" target="_blank" rel="noopener">查看说明 ↗</a></article>`).join('')}</div><details class="live-assembly-evidence"><summary>项目构建详情${p ? ' · ' + e(p.name) : ''}</summary>${p ? `<p>模型文件 ${modelFiles().length} 个 · ${hasBuild() ? '当前构建已生成' : '当前没有构建候选'}</p>${p.models?.['manifest.json'] ? `<pre class="live-code">${e(JSON.stringify(p.models['manifest.json'], null, 2))}</pre><a class="live-button" href="${url(projectUrl('/artifacts/manifest.json'))}" target="_blank" rel="noopener">查看当前模型清单 ↗</a>` : ''}${p.build ? `<pre class="live-code">${e(JSON.stringify({ source: p.build.source, upstream_registry_counts: p.build.upstream_registry_counts, version: p.build.version, prototype_sha256: p.build.prototype_sha256 }, null, 2))}</pre><a class="live-button" href="${url(projectUrl('/download'))}">下载项目源码包 ↓</a>` : ''}` : '<p>选择项目后查看构建详情。</p>'}</details></div></section>`;
}
function isReasoningJob(job) {
  return !!(job?.reasoning_session_id || submittedInputs.get(job?.id)?.reasoning);
}
function jobPending(job) {
  return job?.status === 'queued' || job?.status === 'running';
}
function jobPage(job, project) {
  switch (job.kind) {
    case '需求探索':
      return project.kind === 'analysis' ? 'data1' : 'builder';
    case '生成需求文档':
      return project.kind === 'analysis' ? 'data1' : 'requirements';
    case 'AI 优化需求文档':
      return 'requirements';
    case PROTOTYPE_GENERATION_JOB:
    case PROTOTYPE_EDIT_JOB:
      return 'interface';
    case '生成本体模型':
    case 'AI校核修复本体':
      if (job.kind === '生成本体模型' && job.page === 'requirements' && adaptiveRequirements()) return 'requirements';
      return project.kind === 'analysis' ? 'data2' : 'models';
    case '构建应用与数据库':
      return 'build';
    case '只读查询与分析报告':
    case 'GMV 场景洞察':
    case 'GMV 场景分析':
    case '运行原版电商场景':
      return 'data3';
    case '第一轮知识清单':
    case '第二轮知识填充':
      return job.page === 'knowledge-models' ? 'knowledge-models' : 'knowledge';
    case '原文知识问答':
      return 'knowledge';
    default:
      return job.page;
  }
}
function pageJob(page = state.route, project = state.project) {
  const job = state.activeJob;
  return job && project && job.project_id === project.id && !isReasoningJob(job) && jobPage(job, project) === page ? job : null;
}
function jobControls(job) {
  return jobPending(job)
    ? button(job.cancel_requested ? '正在取消' : '取消任务', 'cancel-job', job.cancel_requested ? 'disabled' : '')
    : button('收起', 'dismiss-job', 'aria-label="收起任务状态"');
}
function jobPanel() {
  const job = pageJob();
  if (!job || job.status === 'succeeded' || state.route === 'interface' || isLiveRequirementsReply(job)) return '';
  const failed = job.status === 'failed';
  const labels = { queued: '等待执行', running: '正在执行', failed: '任务未完成', cancelled: '已取消', interrupted: '已中断' };
  const label = job.cancel_requested && jobPending(job) ? '正在取消' : labels[job.status];
  const detail = job.cancellation_error || job.observation_error || job.error;
  return `<aside class="live-job ${failed ? 'failed' : ''}" role="${failed ? 'alert' : 'status'}"><div><span class="live-job-indicator ${jobPending(job) ? 'running' : ''}"></span><strong>${label}</strong><span>${e(job.kind || '')}</span></div>${detail ? `<p>${e(detail)}</p>` : ''}${jobControls(job)}</aside>`;
}
function body() {
  if (state.loading)
    return `${heading()}<div class="content"><section class="panel live-empty"><span class="live-loading-dot"></span><h2>正在载入工作空间</h2></section></div>`;
  if (state.route === 'build')
    return state.developmentOpen ? developmentPage() : applicationsPage();
  if (['builder', 'data1'].includes(state.route)) return chatPage();
  if (state.route === 'requirements') return requirementsPage();
  if (state.route === 'models') return businessOntologyPage();
  if (state.route === 'files') return businessModelFilesPage();
  if (['data2', 'knowledge-models'].includes(state.route)) return modelEditorPage();
  if (['model-graph', 'data-graph'].includes(state.route)) return graphPage();
  if (state.route === 'resources') return sourcePage();
  if (state.route === 'data-mapping') return mappingPage();
  if (state.route === 'metrics') return metricsPage();
  if (state.route === 'analytics') return reasoningPage();
  if (['data3', 'data4'].includes(state.route)) return analysisPage(state.route === 'data4');
  if (state.route === 'knowledge') return knowledgePage();
  if (state.route === 'interface') return interfacePage();
  if (state.route === 'publish') return publishPage();
  return settingsPage();
}
let disposeWorkspace = () => {};
const pageHeadingObserver = new ResizeObserver(([{ target, borderBoxSize }]) => {
  target.closest('.live-shell').style.setProperty('--page-heading-height', `${borderBoxSize[0].blockSize}px`);
});
function render() {
  pageHeadingObserver.disconnect();
  disposeWorkspace();
  disposeWorkspace = () => {};
  if (state.prototypeRun) {
    closePrototypeEditor(false);
    root.innerHTML = prototypeRunPage();
    return;
  }
  const focus = document.activeElement;
  const focusId = focus?.id;
  const start = focus?.selectionStart,
    end = focus?.selectionEnd;
  const scroll = root.querySelector('#conversation-messages')?.scrollTop;
  root.innerHTML = `<div class="shell live-shell ${state.menu ? 'menu-open' : ''} ${['builder', 'data1', 'analytics'].includes(state.route) ? 'conversation-layout' : ''}">${sidebar()}${state.menu ? '<button class="nav-scrim" type="button" data-action="menu" tabindex="-1" aria-hidden="true"></button>' : ''}<main class="main">${body()}</main></div>`;
  pageHeadingObserver.observe(root.querySelector('.main > .page-heading'), { box: 'border-box' });
  mountRouteWorkspace();
  if (focusId) {
    const next = document.getElementById(focusId);
    if (next && next.tagName === focus.tagName) {
      next.focus({ preventScroll: true });
      try {
        next.setSelectionRange(start, end);
      } catch {}
    }
  }
  if (scroll !== undefined && root.querySelector('#conversation-messages'))
    root.querySelector('#conversation-messages').scrollTop = scroll;
  if (state.scrollChatEnd) {
    const messages = root.querySelector('#conversation-messages');
    const responses = messages?.querySelectorAll(':scope > .live-message.assistant');
    const readyDraft = responses?.[responses.length - 1]?.querySelector('.prd-turn-draft[open] > summary');
    if (readyDraft) {
      messages.scrollTop += readyDraft.getBoundingClientRect().top - messages.getBoundingClientRect().top;
    } else if (messages) messages.scrollTop = messages.scrollHeight;
    state.scrollChatEnd = false;
  }
  renderPrototypeEditor();
  root.querySelectorAll('input[type="file"]').forEach((input) => {
    const selected = uploadSelections.get(draftKey(input.id));
    if (selected?.length) {
      const transfer = new DataTransfer();
      selected.forEach((file) => transfer.items.add(file));
      input.files = transfer.files;
    }
  });
  syncModelDirtyState();
  document.title = info()[0] + ' · ' + PLATFORM_NAME;
}
function mountRouteWorkspace() {
  const ontology = root.querySelector('.live-ontology-panel');
  if (ontology) {
    const project = state.project;
    mountBusinessOntology(ontology, {
      project, escape: e, titleFor: modelTitle, onEdit: openOntologyEdit,
      editable: !state.busy && !project.reference_adoption && !project.business_model_import && !dirtyModelFiles().length,
    });
    return;
  }
  const graph = root.querySelector('.live-graph-workspace');
  if (graph) {
    disposeWorkspace = mountGraph(graph, graphData(), `${projectId()}:${state.route}`);
    return;
  }
  const mapping = root.querySelector('#mapping-workbench');
  const reasoning = root.querySelector('#reasoning-workbench');
  const scenario = root.querySelector('#scenario-insight-workbench');
  if (!mapping && !reasoning && !scenario) return;
  const project = state.project?.kind === 'analysis' ? state.project : null;
  if (scenario) {
    const insight = currentMetricInsights(project)?.find(({ id }) => id === 'MTR-TXN-001');
    const scoped = project?.model_import?.upstream_metric_repairs && state.status?.capabilities?.gmv_attribution;
    disposeWorkspace = mountScenarioInsight(scenario, { project,
      metricInsight: insight ? { ...insight, projectId: project.id } : null,
      liveAvailable: !!scoped,
      realReady: !!(scoped && confirmed('models') && confirmed('requirements') &&
        !project.model_diagnostics?.length && state.provider?.configured),
      methodTemplateUrl: scoped ? url('/gmv-attribution/method-template') : undefined,
      onReviewMethod: scoped ? ({ name, text, builtIn }) => launchGmvJob(project.id, '/method', {
        name, text, built_in: builtIn,
      }) : undefined,
      onRun: scoped ? ({ question, sessionId }) => launchGmvJob(project.id, '/runs', {
        question, session_id: sessionId, method_sha256: state.project?.gmv_method?.sha256,
      }) : undefined,
      onDownload: scoped ? async ({ sessionId }) => {
        const markdown = await api(`/projects/${encodeURIComponent(project.id)}/gmv-attribution/sessions/${encodeURIComponent(sessionId)}/report`);
        const objectUrl = URL.createObjectURL(new Blob([markdown], { type: 'text/markdown;charset=utf-8' }));
        const link = document.createElement('a');
        link.href = objectUrl;
        link.download = 'GMV异常波动归因分析-场景分析报告.md';
        document.body.append(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      } : undefined,
    });
    return;
  }
  const projects = state.projects.filter((item) => item.kind === 'analysis');
  const onSelectProject = (id) => action(() => loadProject(id));
  if (mapping) {
    const binding = project?.source_bindings || project?.models?.[DATA_BINDING_FILE] || findModel(/mapping/);
    const editable = project?.source_bindings || project?.models?.[DATA_BINDING_FILE];
    disposeWorkspace = mountMapping(mapping, {
      project, binding, projects, onSelectProject, busy: state.busy,
      onSave: editable ? (candidate, revision) => saveVisualMapping(project.id, candidate, revision) : null,
    });
    return;
  }
  const submitted = submittedInputs.get(state.activeJob?.id)?.reasoning;
  const job = state.activeJob && { ...state.activeJob, ...submitted };
  disposeWorkspace = mountReasoning(reasoning, {
    project, projects, onSelectProject, busy: state.busy, job,
    canAsk: !!(project?.source && hasModels(project) && confirmed('models') &&
      !project.model_diagnostics?.length && state.provider?.configured),
    onAsk: ({ question, sessionId }) => launchReasoning(project.id, question, sessionId),
    renderResult: analysisResult,
  });
}
async function launchGmvJob(id, operation, payload) {
  if (state.busy || projectId() !== id) throw new Error('请等待当前项目的任务结束。');
  state.busy = true;
  try {
    const job = await send(`/projects/${encodeURIComponent(id)}/gmv-attribution${operation}`, {
      ...payload, revision: state.project.revision,
    });
    return new Promise((resolve, reject) => {
      scenarioJobWaiters.set(job.job_id, { resolve, reject });
      startPoll(job.job_id, { project_id: id, page: 'data3' });
    });
  } catch (error) {
    state.busy = false;
    throw error;
  }
}
async function saveVisualMapping(id, binding, revision) {
  if (state.busy || state.project?.id !== id) throw new Error('请在当前数据本体中保存映射。');
  const project = await api(`/projects/${encodeURIComponent(id)}/models/${DATA_BINDING_FILE}`, {
    method: 'PUT',
    body: JSON.stringify({ text: JSON.stringify(binding), revision, allow_incomplete_mapping: true }),
  });
  state.projects = state.projects.map((item) => item.id === id ? project : item);
  if (state.project?.id === id) {
    state.project = project;
    state.scenarioOptions = [];
  }
  return project;
}
async function launchReasoning(id, question, sessionId) {
  if (state.busy || state.project?.id !== id) throw new Error('请在当前数据本体中发送问题。');
  state.busy = true;
  state.error = '';
  try {
    const result = await launch(`/projects/${encodeURIComponent(id)}/analysis`, {
      question, reasoning_session_id: sessionId,
    }, { reasoning: { project_id: id, question, reasoning_session_id: sessionId } });
    render();
    return result;
  } catch (error) {
    state.busy = false;
    throw error;
  }
}
function formatDate(value, includeYear = false) {
  if (!value) return '';
  const d = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value);
  return Number.isNaN(d.valueOf())
    ? String(value)
    : d.toLocaleString('zh-CN', {
        ...(includeYear ? { year: 'numeric' } : {}),
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      });
}
async function api(path, options = {}) {
  const response = await fetch(url(path), {
    credentials: 'same-origin',
    ...options,
    headers: {
      ...(options.body && !(options.body instanceof FormData)
        ? { 'Content-Type': 'application/json' }
        : {}),
      ...options.headers,
    },
  });
  const type = response.headers.get('content-type') || '';
  let value = type.includes('json') ? await response.json() : await response.text();
  if (!response.ok)
    throw new Error(
      typeof value === 'string'
        ? value
        : value.error?.message || value.error || value.message || `请求失败 (${response.status})`,
    );
  return value;
}
function send(path, payload = {}) {
  return api(path, { method: 'POST', body: JSON.stringify(payload) });
}
function listValue(result) {
  return Array.isArray(result) ? result : result.projects || result.items || [];
}
async function refreshProjects() {
  state.projects = listValue(await api('/projects'));
}
async function refreshScenarioOptions() {
  state.scenarioOptions = state.project?.kind === 'analysis' &&
    state.project.model_import?.upstream_metric_repairs && confirmed('models')
    ? (await api(projectUrl('/scenarios'))).scenarios
    : [];
}
function rememberProject(project) {
  state.selected[project.kind] = project.id;
  try {
    localStorage.setItem('onto-paas-selection-v2', JSON.stringify(state.selected));
  } catch {}
}
async function loadProject(id) {
  state.scenarioOptions = [];
  if (id !== projectId()) {
    state.modelFile = '';
    state.modelFileOpen = false;
  }
  if (!id) {
    state.project = null;
    return;
  }
  state.project = await api(`/projects/${encodeURIComponent(id)}`);
  await refreshScenarioOptions();
  rememberProject(state.project);
  const pending = state.project.active_job_id || state.project.active_job;
  const pendingId = typeof pending === 'object' ? pending?.id : pending;
  if (pendingId && !jobPending(state.activeJob) && state.activeJob?.id !== pendingId)
    startPoll(pendingId, { project_id: id });
}
async function chooseForKind(type) {
  const candidates = inChatWorkspace() ? chatSessions(type) : state.projects.filter((p) => p.kind === type);
  const id = candidates.find((p) => p.id === state.selected[type])?.id || candidates[0]?.id;
  await loadProject(id);
}
async function refresh() {
  await refreshProjects();
  if (projectId()) await loadProject(projectId());
  else await chooseForKind(kind());
  render();
}
async function go(route, skipGuard = false) {
  route = normalizeRoute(route);
  if (!skipGuard && route !== state.route && guardModelDrafts(() => go(route, true))) {
    history.replaceState(null, '', '#' + state.route);
    return;
  }
  state.route = route;
  closePrototypeEditor(false);
  const section = routeGroup(route);
  if (section) state.openSections.add(section);
  state.menu = false;
  state.error = '';
  if (route !== 'build') state.developmentOpen = false;
  if (route !== 'files') state.modelFileOpen = false;
  closeModal();
  location.hash = route;
  const expected = routeInfo[route]?.[1];
  if (expected && (state.project?.kind !== expected || (inChatWorkspace() && conversationSession(projectId()).deleted)))
    await chooseForKind(expected);
  render();
}
function showError(error) {
  state.error = error instanceof Error ? error.message : String(error);
  render();
  const modalBody = overlay.querySelector('.modal-body');
  if (modalBody) {
    modalBody.querySelector('.live-modal-error')?.remove();
    modalBody.insertAdjacentHTML(
      'afterbegin',
      `<p class="live-stale-notice live-modal-error" role="alert">${e(state.error)}</p>`,
    );
  }
}
async function action(fn, { renderPending = true } = {}) {
  if (state.busy) return;
  state.busy = true;
  state.error = '';
  if (renderPending) render();
  try {
    await fn();
  } catch (error) {
    showError(error);
  } finally {
    if (!jobPending(state.activeJob))
      state.busy = false;
    render();
  }
}
let pollTimer;
function startPoll(id, context = {}) {
  if (!id) return;
  clearTimeout(pollTimer);
  state.activeJob = { project_id: projectId(), ...context, id, status: 'queued', events: [] };
  state.busy = true;
  pollJob(id);
}
async function pollJob(id) {
  try {
    const j = await api(`/jobs/${encodeURIComponent(id)}`);
    if (!state.activeJob || state.activeJob.id !== id) return;
    const { project_id, kind, page, cancellation_error } = state.activeJob;
    const submitted = submittedInputs.get(id);
    const job = { project_id, kind, page, ...j, id, ...submitted?.reasoning };
    job.publicReply = typeof j.progress?.public_reply === 'string' ? j.progress.public_reply : '';
    if (cancellation_error && jobPending(j) && !j.cancel_requested)
      job.cancellation_error = cancellation_error;
    if (['succeeded', 'failed', 'cancelled', 'interrupted'].includes(j.status)) {
      if (j.status === 'succeeded' && submitted && drafts[submitted.key] === submitted.value) {
        delete drafts[submitted.key];
        persistDrafts();
      }
      if (j.status === 'succeeded' && submitted?.files?.length &&
          uploadSelections.get(submitted.attachmentKey) === submitted.files)
        uploadSelections.delete(submitted.attachmentKey);
      if (job.kind === '需求探索' || submitted?.message) state.scrollChatEnd = true;
      if (submitted?.prototypeEdit) state.scrollPrototypeEditEnd = true;
      await refreshProjects();
      if (state.activeJob?.id === id && job.project_id === projectId()) await loadProject(projectId());
      submittedInputs.delete(id);
      if (state.activeJob?.id !== id) return;
      state.activeJob = job;
      state.busy = false;
      const waiter = scenarioJobWaiters.get(id);
      if (waiter) {
        scenarioJobWaiters.delete(id);
        if (j.status === 'succeeded') waiter.resolve(j.result);
        else waiter.reject(new Error(j.error || '场景任务未完成。'));
      }
      render();
      return;
    }
    state.activeJob = job;
    if (!updateLiveRequirementsReply(job)) render();
    pollTimer = setTimeout(() => pollJob(id), isLiveRequirementsReply(job) ? 650 : 1400);
  } catch {
    if (state.activeJob?.id !== id) return;
    state.activeJob.observation_error = '暂时无法同步任务状态，正在重试。';
    if (!updateLiveRequirementsReply(state.activeJob)) render();
    pollTimer = setTimeout(() => pollJob(id), 2500);
  }
}
async function launch(path, payload = {}, input = null) {
  const [, project, operation] = /^\/projects\/([^/]+)(\/.*)$/.exec(path);
  const jobKind = {
    '/chat': '需求探索',
    '/requirements': '生成需求文档',
    '/prototype': PROTOTYPE_GENERATION_JOB,
    '/prototype/edit/chat': PROTOTYPE_EDIT_JOB,
    '/models': payload.repair ? 'AI校核修复本体' : '生成本体模型',
  }[operation];
  const context = { project_id: decodeURIComponent(project), page: state.route, kind: jobKind };
  const result = payload instanceof FormData
    ? await api(path, { method: 'POST', body: payload })
    : await send(path, payload);
  const id = result.job_id || result.id;
  if (id) {
    if (input) submittedInputs.set(id, { ...input, key: input.key || (input.name ? draftKey(input.name) : null) });
    startPoll(id, context);
  } else {
    await refresh();
  }
  return result;
}
function toast(text) {
  const el = document.getElementById('toast');
  el.textContent = text;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 3000);
}
let previousFocus;
function modal(title, content, footer = '') {
  previousFocus = document.activeElement;
  overlay.innerHTML = `<div class="modal-backdrop"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><header class="modal-header"><h2 id="modal-title">${e(title)}</h2>${button('×', 'close-modal', 'class="modal-close" aria-label="关闭"')}</header><div class="modal-body">${content}</div>${footer ? `<footer class="modal-footer">${footer}</footer>` : ''}</section></div>`;
  overlay.querySelector('input,textarea,button')?.focus();
}
function closeModal() {
  if (overlay.querySelector('#new-project-form[aria-busy="true"]')) return;
  overlay.innerHTML = '';
  pendingModelNavigation = null;
  previousFocus?.focus?.({ preventScroll: true });
}
function shareConversation(project) {
  const messages = chatHistory(project, project.messages || [], showsContractRequirementsDialogue(project));
  const transcript = messages.filter(({ role }) => role === 'user' || role === 'assistant')
    .map((message) => {
      const { content } = chatSource(message);
      const body = typeof content === 'string' ? content.trim() : JSON.stringify(content ?? '', null, 2);
      const attachments = message.attachments?.map(({ name }) => name).join('、');
      return [`## ${message.role === 'user' ? '用户' : 'Agent'}`, body,
        ...(attachments ? [`附件 ${attachments}`] : [])].filter(Boolean).join('\n\n');
    });
  modal('分享对话', `<label class="field">可复制的对话<textarea id="conversation-share-text" rows="12" readonly>${e([`# ${project.name}`, ...transcript].join('\n\n'))}</textarea></label>`,
    button('关闭', 'close-modal') + button('复制对话', 'copy-conversation', '', true));
}
async function copyConversation() {
  const field = document.getElementById('conversation-share-text');
  if (!field) return;
  try {
    await navigator.clipboard.writeText(field.value);
    toast('对话已复制');
  } catch {
    field.focus();
    field.select();
    toast('对话已选中，可使用复制快捷键');
  }
}
bindConversationSessionMenu(root, {
  getActions(item) {
    const { pinned, unread } = conversationSession(item.dataset.id);
    return [
      { id: 'pin', label: pinned ? '取消置顶' : '置顶' },
      { id: 'unread', label: unread ? '标记已读' : '标记未读' },
      { id: 'share', label: '分享' },
      { id: 'delete', label: '删除对话', danger: true, disabled: state.busy },
    ];
  },
  onAction(name, item) {
    const id = item.dataset.id;
    const project = state.project?.id === id ? state.project : state.projects.find((candidate) => candidate.id === id);
    if (!project) return;
    const { pinned, unread } = conversationSession(id);
    if (name === 'share') shareConversation(project);
    else if (name === 'delete') {
      modal('删除对话', `<p>从此浏览器的会话列表移除「${e(project.name)}」。需求文档、原型和本体仍可在项目中查看。</p>`,
        button('取消', 'close-modal') + button('删除对话', 'delete-conversation', `data-id="${e(id)}" data-variant="danger"`, true));
    } else {
      updateConversationSession(id, name === 'pin' ? { pinned: !pinned } : { unread: !unread });
      render();
    }
  },
});
function prdPreview(project, generate = false) {
  const attrs = `data-id="${e(project.id)}" data-revision="${e(project.revision)}"`;
  const alreadyConfirmed = confirmed('requirements', project);
  const canReview = !project.reference_adoption && !requirementsOutdated(project);
  const canApprove = canReview && !alreadyConfirmed;
  const onlyPrototype = project.requirements_confirmation?.prototype_only;
  const audit = project.requirement_audit || project.requirements_audit;
  const canApproveFormally = canReview && !generate && (!alreadyConfirmed || onlyPrototype) &&
    audit?.passed && !project.requirements_document?.draft && (project.requirements_document || Number(project.stage) >= 8);
  const skipPrototype = canMarkPrototypeOptional(project);
  const approval = canApprove
    ? button(generate ? '确认需求并生成原型' : '确认需求用于原型', generate ? 'prd-prototype-confirm' : 'prd-confirm', attrs, true)
    : '';
  const formal = canApproveFormally ? button('确认需求文档', 'prd-confirm', `${attrs} data-formal="true"`, true) : '';
  const status = alreadyConfirmed ? pill(onlyPrototype ? '已确认用于原型' : '已确认', 'green') : pill('待核对', 'amber');
  const notice = requirementsOutdated(project)
    ? '<p class="live-stale-notice">需求探索已有新内容，请先保存最新文档。</p>'
    : generate ? '<p class="live-stale-notice">请核对当前需求。确认后，AI 会根据这份文档生成界面原型。</p>' : '';
  modal(requirementTitle(project), `<div class="prd-document-preview"><div class="prd-preview-meta">${status}<span>${e(project.name)}</span></div>${notice}<article class="live-document">${markdown(project.requirements)}</article>${requirementAudit(project)}</div>`, button('关闭', 'close-modal') + (formal || approval) + (skipPrototype ? button('确认原型不适用', 'prd-confirm-no-prototype', attrs) : ''));
  overlay.querySelector('.modal').classList.add('prd-document-modal');
}
function openPrototypeRevision() {
  modal(
    '修订界面原型',
    `<form id="prototype-revision-form"><p>根据当前需求与修订意见生成新原型。原型更新后需要重新确认。</p><label class="field">修订意见<textarea id="prototype-feedback" data-draft="prototype-feedback" rows="6" maxlength="8000" placeholder="说明希望调整的页面、字段、操作或布局。" required>${e(draft('prototype-feedback'))}</textarea></label><div class="live-modal-actions">${button('继续查看原型', 'close-modal')}<button type="submit" class="primary">生成修订原型</button></div></form>`,
  );
}
function openRequirementsImport() {
  const choices = businessRegistryProjects((project) => !project.reference_adoption)
    .map((project) => `<option value="${e(project.id)}" data-revision="${e(project.revision)}">${e(project.name)}${project.requirements ? '（替换当前需求）' : ''}</option>`)
    .join('');
  modal('上传需求文档', `<form id="prd-import-form"><label class="field">需求文档<input name="document" type="file" accept=".md,.pdf,.txt" required></label><label class="field">文档标题（可选）<input name="title" maxlength="100" placeholder="默认使用文件名"></label><label class="field">归属项目<select name="project_id"><option value="">创建新项目</option>${choices}</select></label><p class="live-subtle">对应已有项目时，当前需求将被替换，需要重新核对原型和本体。</p><div class="live-modal-actions">${button('取消', 'close-modal')}<button type="submit" class="primary">上传</button></div></form>`);
}
async function startRegistryPrototype() {
  state.prototypeDetails = templatePrototypeEnabled() ? null : projectId();
  await go('interface');
  await launch(projectUrl('/prototype'), { revision: state.project.revision });
}
async function generateProjectModels() {
  if (dirtyModelFiles().length)
    throw new Error('存在未保存的模型修改，请先保存并校验，或放弃本地修改。');
  if (!canGenerateModels())
    throw new Error(
      state.project.kind === 'business'
        ? '请先确认当前需求文档和界面原型。'
        : '请先确认当前需求文档。',
    );
  await launch(projectUrl('/models'));
}
async function registryAction(name, target) {
  if (!adaptiveRequirements()) return;
  if (name === 'prd-prototype-editor-close') {
    closePrototypeEditor();
    return;
  }
  if (name === 'prd-import') {
    if (!guardModelDrafts(openRequirementsImport)) openRequirementsImport();
    return;
  }
  const id = target.dataset.id || projectId();
  if (!id) return;
  const selectAndOpen = () => action(async () => {
    if (id !== projectId()) await loadProject(id);
    const project = state.project;
    if (name === 'prd-open-document') {
      await go('requirements');
      if (project.requirements) prdPreview(project);
      return;
    }
    if (name === 'prd-open-ontology') {
      await go('models');
      return;
    }
    if (name === 'prd-prototype-review') {
      showPrototypeReview(project);
      return;
    }
    if (name === 'prd-prototype-delete') {
      const { prototypeId, revision } = target.dataset;
      if (project.prototype?.id !== prototypeId || project.revision !== Number(revision))
        throw new Error('原型已更新，请刷新后再删除。');
      const blocker = prototypeDeletionBlocker(project);
      if (blocker) throw new Error(blocker);
      modal('删除界面原型',
        `<p>删除《${e(prototypeTitle(project))}》？此操作也会撤销相关确认和待发布构建。</p>`,
        button('取消', 'close-modal') + button('删除原型', 'prd-prototype-delete-confirm',
          `data-id="${e(id)}" data-prototype-id="${e(prototypeId)}" data-revision="${e(revision)}" data-variant="danger"`, true));
      return;
    }
    if (name === 'prd-prototype-delete-confirm') {
      state.project = await api(projectUrl('/prototype'), {
        method: 'DELETE',
        body: JSON.stringify({ revision: Number(target.dataset.revision), prototype_id: target.dataset.prototypeId }),
      });
      if (pageJob('interface', project)) state.activeJob = null;
      state.prototypeDetails = null;
      closeModal();
      await refreshProjects();
      toast('原型已删除');
      return;
    }
    if (name === 'prd-prototype-edit') {
      if (!project.prototype || prototypeIsStale(project) || project.reference_adoption) return;
      state.prototypeDetails = null;
      state.prototypeEditor = { projectId: project.id, prototypeId: project.prototype.id };
      state.scrollPrototypeEditEnd = true;
      render();
      return;
    }
    if (name === 'prd-prototype-apply') {
      const editor = state.prototypeEditor;
      const prototypeId = editor?.prototypeId;
      const candidate = project.prototype_edit?.candidate;
      if (!prototypeId || prototypeId !== project.prototype?.id || !candidate?.id) return;
      target.disabled = true;
      target.setAttribute('aria-busy', 'true');
      const previousDraft = draftKey(prototypeEditDraft(project));
      state.project = await send(projectUrl('/prototype/edit/apply'), {
        revision: project.revision,
        prototype_id: prototypeId,
        candidate_id: candidate.id,
      });
      if (drafts[previousDraft]) drafts[draftKey(prototypeEditDraft(state.project))] = drafts[previousDraft];
      delete drafts[previousDraft];
      persistDrafts();
      if (state.prototypeEditor === editor) {
        editor.prototypeId = state.project.prototype.id;
        state.scrollPrototypeEditEnd = true;
      }
      await refreshProjects();
      toast(`已应用为原型修订 ${state.project.prototype.revision}`);
      return;
    }
    if (name === 'prd-prototype-revise') {
      state.prototypeDetails = project.id;
      openPrototypeRevision();
      return;
    }
    if (!project.requirements) throw new Error('该项目没有需求文档。');
    if (name === 'prd-preview') {
      prdPreview(project);
      return;
    }
    if (project.reference_adoption) throw new Error('原版样例使用固定需求文档。');
    const revision = e(project.revision);
    if (name === 'prd-edit') {
      modal('编辑需求文档', `<form id="prd-edit-form" data-revision="${revision}"><label class="field">文档标题<input name="title" required maxlength="100" value="${e(requirementTitle(project))}"></label><label class="field">文档正文<textarea name="document" class="prd-edit-text" rows="18" required>${e(project.requirements)}</textarea></label><div class="live-modal-actions">${button('取消', 'close-modal')}<button type="submit" class="primary">保存</button></div></form>`);
      overlay.querySelector('.modal').classList.add('prd-document-modal');
      return;
    }
    if (name === 'prd-generate-models') {
      const blocker = registryModelGenerationBlocker(project);
      if (blocker) throw new Error(blocker);
      await generateProjectModels();
      return;
    }
    if (name === 'prd-delete') {
      modal('删除需求文档', `<p>删除《${e(requirementTitle(project))}》后，将从需求文档列表移除。当前原型和本体需要基于新的需求重新生成。</p>`, button('取消', 'close-modal') + button('删除文档', 'prd-delete-confirm', `data-id="${e(project.id)}" data-revision="${revision}" data-variant="danger"`, true));
      return;
    }
    if (name === 'prd-delete-confirm') {
      await api(projectUrl('/requirements'), { method: 'DELETE', body: JSON.stringify({ revision: Number(target.dataset.revision) }) });
      closeModal();
      await refresh();
      toast('需求文档已删除');
      return;
    }
    if (name === 'prd-generate-prototype') {
      if (confirmed('requirements')) await startRegistryPrototype();
      else prdPreview(project, true);
      return;
    }
    if (name === 'prd-confirm' || name === 'prd-prototype-confirm') {
      state.project = await send(projectUrl('/confirm'), {
        scope: 'requirements', revision: Number(target.dataset.revision), prototype_only: target.dataset.formal !== 'true',
      });
      await refreshProjects();
      closeModal();
      if (name === 'prd-prototype-confirm') await startRegistryPrototype();
      else toast(target.dataset.formal === 'true' ? '需求文档已确认' : '已确认需求文档用于原型');
    }
    if (name === 'prd-confirm-no-prototype') {
      state.project = await send(projectUrl('/confirm'), {
        scope: 'prototype_not_applicable', revision: Number(target.dataset.revision),
        decision: '用户确认当前需求无需生成界面原型',
      });
      await refreshProjects();
      closeModal();
      toast('已确认此需求无需界面原型');
    }
  }, { renderPending: name !== 'prd-prototype-apply' });
  if (id !== projectId() && guardModelDrafts(selectAndOpen)) return;
  await selectAndOpen();
}
function showPrototypeReview(project) {
  state.prototypeDetails = project.id;
  render();
  root.querySelector('#prd-prototype-review-title')?.focus();
}
function newProject(type) {
  const label = type === 'business' ? '应用项目' : type === 'analysis' ? '分析项目' : '知识库';
  modal(
    '新建' + label,
    `<form id="new-project-form" data-kind="${type}"><label class="field">${label}名称<input id="project-name" name="name" maxlength="100" placeholder="输入项目名称" required></label><label class="field">${type === 'business' ? '原始业务需求' : type === 'analysis' ? '分析目标' : '资料范围与用途'}<textarea id="project-description" name="description" rows="5" maxlength="20000" placeholder="${type === 'business' ? '说明想管理的业务、使用人和当前问题。' : type === 'analysis' ? '说明要分析的问题、时间范围和关注指标。' : '说明需要整理哪些资料，以及希望回答哪些问题。'}" required></textarea></label>${type === 'analysis' ? analysisSourceFields() : ''}<div class="live-modal-actions">${button('取消', 'close-modal')}<button type="submit" class="primary">${type === 'business' ? '创建并开始探索' : '创建' + label}</button></div></form>`,
  );
}
root.addEventListener('input', (ev) => {
  const t = ev.target;
  if (t.dataset.draft) {
    if (
      t.id === 'model-editor' &&
      !Object.hasOwn(drafts, draftKey('model-revision:' + state.modelFile))
    )
      saveDraft('model-revision:' + state.modelFile, Number(t.dataset.revision));
    saveDraft(t.dataset.draft, t.type === 'checkbox' ? String(t.checked) : t.value);
    if (t.id === 'model-editor') syncModelDirtyState();
    if (t.id === 'chat-input')
      t.form.querySelector('.send-message').disabled =
        !canSendChat(t.value, uploadSelections.get(draftKey(CHAT_ATTACHMENT_ID)));
    if (t.id === 'knowledge-question')
      t.form.querySelector('.send-message').disabled = !hasModels() || !confirmed('models') || !canSendChat(t.value);
    if (t.id === 'analysis-question' && state.route === 'data3') {
      const send = t.form.querySelector('.send-message');
      send.disabled = send.dataset.blocked === 'true' || !canSendChat(t.value);
    }
  }
  if (t.dataset.input === 'project-search') {
    state.projectFilter = t.value;
    const items = root.querySelectorAll('.conversation-item');
    items.forEach((el) => {
      const label = el.querySelector('.conversation-name') || el.querySelector('strong');
      el.hidden = !label.textContent.toLowerCase().includes(t.value.toLowerCase());
    });
  }
});
root.addEventListener('change', (ev) => {
  if (ev.target.id === CHAT_ATTACHMENT_ID) {
    const key = draftKey(CHAT_ATTACHMENT_ID);
    const files = Array.from(ev.target.files || []);
    uploadSelections.set(key, files);
    render();
    return;
  }
  if (ev.target.type === 'file')
    uploadSelections.set(draftKey(ev.target.id), Array.from(ev.target.files || []));
  if (ev.target.dataset.draft)
    saveDraft(
      ev.target.dataset.draft,
      ev.target.type === 'checkbox' ? String(ev.target.checked) : ev.target.value,
    );
  if (ev.target.dataset.input === 'project-select') {
    const id = ev.target.value;
    const next = () => action(async () => loadProject(id));
    ev.target.value = projectId() || '';
    if (!guardModelDrafts(next)) next();
  }
});
async function onClick(event) {
  const target = event.target.closest('[data-action]');
  if (!target) return;
  const a = target.dataset.action;
  if (a === 'close-modal') {
    closeModal();
    return;
  }
  if (a === 'copy-conversation') {
    await copyConversation();
    return;
  }
  if (a === 'delete-conversation') {
    const id = target.dataset.id;
    await action(async () => {
      closeModal();
      updateConversationSession(id, { deleted: true, pinned: false, unread: false });
      if (inChatWorkspace() && id === projectId()) await chooseForKind(kind());
      toast('对话已从列表移除');
    }, { renderPending: false });
    return;
  }
  if (a.startsWith('prd-')) {
    await registryAction(a, target);
    return;
  }
  if (a === 'open-metric-document') {
    openMetricDocument();
    return;
  }
  if (a === 'generate-metric-insights' && state.project) {
    const project = state.project;
    const items = buildMetricInsights(project, metricDocuments.get(project.id)?.definitions);
    metricInsights.set(project.id, { basis: metricInsightBasis(project), items });
    render();
    root.querySelector('#metric-insight-generate')?.focus();
    return;
  }
  if (a === 'view-metric-insight') {
    showMetricInsight(Number(target.dataset.metricIndex));
    return;
  }
  if (a === 'save-model-and-continue' || a === 'discard-model-and-continue') {
    const next = pendingModelNavigation;
    let ready = false;
    await action(async () => {
      for (const [name] of dirtyModelFiles()) {
        if (a === 'save-model-and-continue') await saveModelDraft(name);
        else discardModelDraft(name);
      }
      ready = true;
    });
    if (ready) {
      closeModal();
      await next?.();
    }
    return;
  }
  if (a === 'cancel-job') {
    const job = state.activeJob;
    if (!jobPending(job)) return;
    try {
      await send('/jobs/' + encodeURIComponent(job.id) + '/cancel');
      if (state.activeJob?.id !== job.id || !jobPending(state.activeJob)) return;
      state.activeJob = { ...state.activeJob, cancel_requested: true, cancellation_error: '', publicReply: '' };
      render();
    } catch (err) {
      if (state.activeJob?.id === job.id && jobPending(state.activeJob)) {
        state.activeJob.cancellation_error = '暂时无法取消任务。' + err.message;
        render();
      }
    }
    return;
  }
  if (a === 'menu') {
    state.menu = !state.menu;
    render();
    return;
  }
  if (a === 'section') {
    const section = target.dataset.section;
    const isOpen = !state.openSections.has(section);
    if (isOpen) state.openSections.add(section);
    else state.openSections.delete(section);
    target.setAttribute('aria-expanded', String(isOpen));
    target.querySelector('i').textContent = isOpen ? '⌄' : '›';
    document.getElementById(target.getAttribute('aria-controls')).hidden = !isOpen;
    return;
  }
  if (a === 'nav') {
    if (normalizeRoute(target.dataset.route) === 'build') state.developmentOpen = false;
    try {
      await go(target.dataset.route);
    } catch (err) {
      showError(err);
    }
    return;
  }
  if (a === 'new-project') {
    const next = () => newProject(target.dataset.kind || kind());
    if (!guardModelDrafts(next)) next();
    return;
  }
  if (a === 'import-business-models') {
    modal('从七模型直接构建', `<form id="business-model-import-form"><p>选择七份模型 YAML 和 manifest.json。导入后先预览并确认模型中的界面，再确认模型和构建。</p><label class="field">可移植七模型文件<input type="file" name="models" accept=".yaml,.yml,.json" multiple required></label><button type="submit" class="primary">检查并导入</button></form>`);
    return;
  }
  if (a === 'new-conversation') {
    if (state.busy) return;
    state.project = null;
    state.error = '';
    state.activeJob = null;
    state.scrollChatEnd = false;
    render();
    document.getElementById('chat-input')?.focus();
    return;
  }
  if (a === 'remove-chat-attachment') {
    if (state.busy) return;
    const key = draftKey(CHAT_ATTACHMENT_ID);
    const remaining = (uploadSelections.get(key) || []).filter((_, i) => i !== Number(target.dataset.index));
    uploadSelections.set(key, remaining);
    render();
    document.getElementById(CHAT_ATTACHMENT_ID)?.focus();
    return;
  }
  if (a === 'preview-prototype') {
    previewPrototype(target.dataset.history);
    return;
  }
  if (a === 'revise-prototype') {
    openPrototypeRevision();
    return;
  }
  if (a === 'adopt-contract-reference') {
    modal(
      '采用原版销售合同样例',
      `<form id="contract-reference-form"><p>此样例会导入销售合同的需求、原型和七模型，构建时创建独立数据库。</p><label class="field"><span><input name="acknowledged" type="checkbox" required> 使用此样例</span></label><div class="live-modal-actions">${button('返回', 'close-modal')}<button type="submit" class="primary">确认采用</button></div></form>`,
    );
    return;
  }
  if (a === 'repair-models') {
    if (state.busy) return;
    modal(
      'AI校核修复当前模型',
      `<form id="model-repair-form"><p>修复通过校验后会更新当前模型，需要重新审阅、确认模型并构建。</p><label class="field">校核重点与修复意见（可选）<textarea id="repair-feedback" data-draft="repair-feedback" rows="5" maxlength="8000" placeholder="例如：结合需求复核角色授权与规则，保留已确认的业务范围。">${e(draft('repair-feedback'))}</textarea></label><div class="live-modal-actions">${button('返回模型', 'close-modal')}<button type="submit" class="primary">执行AI校核修复</button></div></form>`,
    );
    return;
  }

  if (a === 'dismiss-error') {
    state.error = '';
    render();
    return;
  }
  if (a === 'dismiss-job') {
    state.activeJob = null;
    render();
    return;
  }
  if (a === 'open-model-file' || a === 'close-model-file') {
    const next = () => {
      state.modelFileOpen = a === 'open-model-file';
      state.modelFile = target.dataset.file || '';
      render();
      root.querySelector(state.modelFileOpen ? '#model-editor' : '[data-action="open-model-file"]')?.focus();
    };
    if (!guardModelDrafts(next)) next();
    return;
  }
  if (a === 'model-file-requirements') {
    if (!guardModelDrafts(() => prdPreview(state.project))) prdPreview(state.project);
    return;
  }
  if (a === 'select-model') {
    const next = () => {
      state.modelFile = target.dataset.file;
      render();
    };
    if (target.dataset.file !== state.modelFile && guardModelDrafts(next)) return;
    next();
    return;
  }
  if (['select-project', 'open-development', 'refresh'].includes(a)) {
    const next = () =>
      action(async () => {
        if (a === 'refresh') await refresh();
        else {
          await loadProject(target.dataset.id);
          if (a === 'select-project') updateConversationSession(target.dataset.id, { unread: false });
          if (a === 'open-development') {
            state.developmentOpen = true;
            await go('build', true);
          }
        }
      });
    if (guardModelDrafts(next)) return;
    await next();
    return;
  }
  await action(async () => {
    if (a === 'refresh-status') {
      state.status = await api('/status');
      state.provider = state.status.provider;
      return;
    }
    if (!projectId()) throw new Error('请先选择或创建项目。');
    if (
      (a === 'build-application' ||
        (a === 'confirm' && target.dataset.scope === 'models')) &&
      dirtyModelFiles().length
    )
      throw new Error('存在未保存的模型修改，请先保存并校验，或放弃本地修改。');
    if (a === 'confirm') {
      if (target.dataset.scope === 'models' && !canConfirmModels())
        throw new Error('请完成当前需求与模型校验。业务项目还需确认对应原型。');
      const confirmation = await send(projectUrl('/confirm'), {
        scope: target.dataset.scope,
        revision: state.project.revision,
        stage: state.project.stage,
      });
      if (confirmation.job_id) startPoll(confirmation.job_id);
      else state.project = confirmation;
      await refreshScenarioOptions();
      await refreshProjects();
      toast('确认已保存');
      if (
        target.dataset.scope === 'stage' &&
        state.project.stage < 8 &&
        !confirmation.job_id &&
        state.provider?.configured
      ) {
        await launch(projectUrl('/chat'), {
          message: '已确认上一轮，请继续讨论：' + stageNames[state.project.stage] + '。',
        });
      }
      return;
    }
    if (a === 'run-upstream-scenario') {
      await launch(projectUrl('/scenarios/' + encodeURIComponent(target.dataset.scenario) + '/runs'), {
        revision: state.project.revision,
      });
      return;
    }
    if (a === 'generate-requirements') {
      const review = adaptiveRequirements() && prdReview(state.project);
      if (review) {
        const saveDraft = target.dataset.saveDraft === 'true';
        if (!review.draft || state.project.prd_stale || (!saveDraft && !review.can_finalize)) throw new Error(prdGenerationNotice(state.project));
        state.project = await send(projectUrl('/requirements'), {
          revision: state.project.revision,
          ...(saveDraft ? { save_draft: true } : {}),
        });
        await refreshProjects();
        toast(saveDraft ? '需求草稿已保存到需求文档' : '已生成需求文档');
      } else await launch(projectUrl('/requirements'));
      return;
    }
    if (a === 'generate-models') {
      await generateProjectModels();
      return;
    }
    if (a === 'build-application') {
      if (!confirmed('models')) throw new Error('请先确认当前本体。');
      await launch(projectUrl('/build'));
      return;
    }
    if (a === 'discard-model') {
      discardModelDraft(state.modelFile);
      const selected = state.modelFile;
      await loadProject(projectId());
      state.modelFile = selected;
      return;
    }
    if (a === 'save-model') {
      await saveModelDraft();
      toast('模型已保存并校验');
      return;
    }
    if (a === 'generate-prototype') {
      if (!confirmed('requirements')) throw new Error('请先确认当前需求文档。');
      await launch(projectUrl('/prototype'), { revision: state.project.revision });
      return;
    }
    if (a === 'knowledge-extract') {
      await launch(projectUrl('/knowledge/extract'));
      return;
    }
  });
}
root.addEventListener('click', onClick);
overlay.addEventListener('click', onClick);
prototypeEditorDialog.addEventListener('click', onClick);
prototypeEditorDialog.addEventListener('cancel', (event) => {
  event.preventDefault();
  closePrototypeEditor();
});
async function chatWithAttachments(text, files) {
  if (files.some((file) => !/\.(md|txt)$/i.test(file.name)))
    throw new Error('需求附件支持 Markdown 和纯文本文件。');
  if (files.reduce((size, file) => size + file.size, 0) > CHAT_ATTACHMENT_MAX_BYTES)
    throw new Error('需求附件合计不能超过 120 KB。');
  const attachments = await Promise.all(files.map(async (file) => {
    let content;
    try {
      content = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer());
    } catch {
      throw new Error(`无法读取 UTF-8 附件：${file.name}`);
    }
    if (!content.trim()) throw new Error(`附件没有正文：${file.name}`);
    return `附件《${file.name}》\n${content}`;
  }));
  const message = [text, ...attachments].filter(Boolean).join('\n\n');
  if (Array.from(message).length > CHAT_MESSAGE_LIMIT)
    throw new Error('消息和附件正文合计不能超过 30000 字符。');
  return message;
}
async function submitChat(form) {
  const input = form.querySelector('#chat-input');
  const value = input.value;
  const text = value.trim();
  const business = state.route === 'builder';
  const adaptive = business && adaptiveRequirements();
  let attachmentKey = draftKey(CHAT_ATTACHMENT_ID);
  const files = business ? uploadSelections.get(attachmentKey) || [] : [];
  if (!canSendChat(text, files)) return;
  let key = draftKey(input.dataset.draft);
  let project = state.project?.kind === kind() ? state.project : null;
  const newBusinessChat = business && !project;
  await action(async () => {
    if (adaptive && files.some((file) => !/\.(md|pdf|txt)$/i.test(file.name)))
      throw new Error('需求附件支持 Markdown、PDF 和纯文本文件。');
    const message = adaptive ? text : await chatWithAttachments(text, files);
    if (newBusinessChat) {
      const firstLine = message.split(/\r?\n/, 1)[0].trim() || files[0]?.name;
      const title = Array.from(firstLine);
      const name = title.length > 32 ? title.slice(0, 31).join('') + '…' : firstLine;
      const description = message || `需求附件：${files.map((file) => file.name).join('、')}`;
      project = await send('/projects', { kind: 'business', name, description });
      const projectKey = `${project.id}:chat`;
      drafts[projectKey] = drafts[key] ?? value;
      delete drafts[key];
      persistDrafts();
      key = projectKey;
      if (files.length) {
        const nextKey = `${project.id}:${CHAT_ATTACHMENT_ID}`;
        uploadSelections.set(nextKey, files);
        uploadSelections.delete(attachmentKey);
        attachmentKey = nextKey;
      }
      rememberProject(project);
      state.projects = [project, ...state.projects];
      if (state.route === 'builder' && !state.project) {
        state.project = project;
        state.projectFilter = '';
      }
    }
    if (!project) throw new Error('请先选择项目。');
    state.scrollChatEnd = true;
    let payload = { message };
    if (business) payload.workflow = adaptive ? 'adaptive' : 'staged';
    if (adaptive) {
      if (files.length) {
        const formData = new FormData();
        for (const [name, entry] of Object.entries(payload)) formData.append(name, String(entry));
        for (const file of files) formData.append('attachments', file);
        payload = formData;
      }
    }
    await launch(`/projects/${encodeURIComponent(project.id)}/chat`, payload, {
      key, value, attachmentKey, files, adaptive, project: project.id, message, messageCount: project.messages?.length || 0,
    });
  });
}
async function submit(event) {
  const form = event.target;
  if (event.defaultPrevented || !(form instanceof HTMLFormElement)) return;
  event.preventDefault();
  if (form.id === 'prototype-demo-login') {
    const artifact = state.project?.prototype;
    if (!state.prototypeRun || !PROTOTYPE_WORKBENCH_TEMPLATES.has(artifact?.template_version)) return;
    state.enteredPrototype = artifact.id;
    render();
    root.querySelector('iframe')?.focus();
    return;
  }
  if (form.id === 'prototype-editor-demo-login') {
    if (!state.prototypeEditor) return;
    state.prototypeEditor.entered = true;
    renderPrototypeEditor();
    prototypeEditorDialog.querySelector('iframe')?.focus();
    return;
  }
  if (form.id === 'chat-form') {
    await submitChat(form);
    return;
  }
  const submittedFiles = Array.from(form.querySelector('input[type="file"]')?.files || []);
  if (form.id === 'prd-import-form') {
    const body = new FormData(form);
    const revision = form.elements.project_id.selectedOptions[0]?.dataset.revision;
    if (revision !== undefined) body.set('revision', revision);
    await action(async () => {
      state.project = await api('/requirements/import', { method: 'POST', body });
      rememberProject(state.project);
      await refreshProjects();
      closeModal();
      toast('需求文档已上传');
    });
    return;
  }
  if (form.id === 'new-project-form') {
    const type = form.dataset.kind;
    const name = form.querySelector('[name=name]').value.trim();
    const description = form.querySelector('[name=description]').value.trim();
    if (!name || !description) return;
    await action(async () => {
      const details = { kind: type, name, description };
      if (type === 'analysis') {
        const createButton = form.querySelector('button[type="submit"]');
        createButton.disabled = true;
        form.setAttribute('aria-busy', 'true');
        try {
          if (form.elements['source-method'].value === 'upload') {
            if (!submittedFiles[0]) throw new Error('请选择 SQLite 数据库。');
            const body = new FormData();
            for (const [field, value] of Object.entries(details)) body.append(field, value);
            body.append('database', submittedFiles[0]);
            state.project = await api('/projects', { method: 'POST', body });
          } else {
            const sourceVersion = form.querySelector('#source-project-select').value;
            if (!sourceVersion) throw new Error('请选择业务应用版本。');
            state.project = await send('/projects', { ...details, source: JSON.parse(sourceVersion) });
          }
        } finally {
          createButton.disabled = false;
          form.removeAttribute('aria-busy');
        }
        state.projects = [state.project, ...state.projects];
        rememberProject(state.project);
      } else {
        state.project = await send('/projects', details);
        state.selected[type] = state.project.id;
        await refreshProjects();
      }
      closeModal();
      state.developmentOpen = false;
      await go(type === 'business' ? 'builder' : type === 'analysis' ? 'resources' : 'knowledge');
      if (type === 'business' && state.provider?.configured)
        await launch(projectUrl('/chat'), { message: description, ...(adaptiveRequirements() ? { workflow: 'adaptive' } : {}) });
    });
    return;
  }
  await action(async () => {
    if (!projectId()) throw new Error('请先选择项目。');
    if (form.id === 'business-model-import-form') {
      const selected = Array.from(form.elements.models.files);
      if (selected.length !== 8 || new Set(selected.map((file) => file.name)).size !== 8)
        throw new Error('请选择七份业务模型和 manifest.json。');
      if (selected.some((file) => file.size > 512 * 1024)) throw new Error('单个模型文件不能超过 512 KiB。');
      const files = Object.fromEntries(await Promise.all(selected.map(async (file) => [file.name, await file.text()])));
      state.project = await send(projectUrl('/models/import'), { revision: state.project.revision, files });
      await refreshProjects();
      closeModal();
      showPrototypeReview(state.project);
      return;
    }
    if (form.id === 'prd-edit-form') {
      const title = form.elements.title.value.trim();
      const document = form.elements.document.value;
      state.project = await api(projectUrl('/requirements'), {
        method: 'PUT',
        body: JSON.stringify({ title, document, revision: Number(form.dataset.revision) }),
      });
      await refreshProjects();
      closeModal();
      toast('需求文档已保存');
      return;
    }
    if (form.id === 'prototype-edit-chat-form') {
      const input = form.querySelector('#prototype-edit-input');
      const project = state.project;
      const prototypeId = state.prototypeEditor?.prototypeId;
      const message = input.value.trim();
      if (!message || !prototypeId || project.prototype?.id !== prototypeId) return;
      const edit = project.prototype_edit?.prototype_id === prototypeId ? project.prototype_edit : null;
      state.scrollPrototypeEditEnd = true;
      await launch(projectUrl('/prototype/edit/chat'), {
        revision: project.revision,
        prototype_id: prototypeId,
        message,
      }, {
        name: input.dataset.draft,
        value: input.value,
        prototypeEdit: { prototypeId, message, messageCount: edit?.messages?.length || 0 },
      });
      return;
    }
    if (form.id === 'prototype-revision-form') {
      const feedback = form.querySelector('#prototype-feedback').value.trim();
      if (!feedback) return;
      await launch(
        projectUrl('/prototype'),
        { feedback, revision: state.project.revision },
        { name: 'prototype-feedback', value: feedback },
      );
      closeModal();
      return;
    }
    if (form.id === 'candidate-acceptance-form') {
      if (dirtyModelFiles().length) throw new Error('请先保存并校验模型修改。');
      const plan = state.project.build.acceptance_plan;
      const values = new FormData(form);
      await send(projectUrl('/build/acceptance'), {
        revision: state.project.revision,
        candidate_id: form.dataset.candidateId,
        checks: Object.fromEntries(plan.checks.map(({ id }) => [id, { passed: values.has(`passed-${id}`), result: values.get(id) }])),
      });
      form.querySelectorAll('[data-draft]').forEach((field) => delete drafts[draftKey(field.dataset.draft)]);
      persistDrafts();
      await refresh();
      toast('候选已通过入口、角色和数据库检查，验收记录已保存');
      return;
    }
    if (form.id === 'release-form') {
      if (dirtyModelFiles().length) throw new Error('请先保存并校验模型修改，再发布当前构建。');
      const version = form.querySelector('#release-version').value.trim();
      const acknowledged = form.querySelector('#release-acknowledged').checked;
      if (!version || !acknowledged) return;
      await send(projectUrl('/releases'), {
        version,
        revision: state.project.revision,
        acknowledged: true,
      });
      delete drafts[draftKey('release-version')];
      delete drafts[draftKey('release-acknowledged')];
      persistDrafts();
      await refresh();
      toast('应用版本已发布');
      return;
    }
    if (form.id === 'ontology-edit-form') {
      const { project, file, path, revision } = form.dataset;
      if (project !== projectId() || Number(revision) !== state.project.revision)
        throw new Error('本体已更新，请重新打开属性编辑。');
      const document = structuredClone(state.project.models[file]);
      const node = JSON.parse(path).reduce((value, key) => value?.[key], document);
      if (!node) throw new Error('此节点已更新，请重新选择。');
      ontologyEditableFields(node).forEach(([key, value], index) => {
        const changed = form.elements.namedItem(`field-${index}`).value;
        node[key] = typeof value === 'boolean' ? changed === 'true' : typeof value === 'number' ? Number(changed) : changed;
      });
      state.project = await api(projectUrl('/models/' + encodeURIComponent(file)), {
        method: 'PUT', body: JSON.stringify({ revision: Number(revision), text: JSON.stringify(document) }),
      });
      closeModal();
      await refreshProjects();
      toast('模型属性已保存');
      return;
    }
    if (form.id === 'model-repair-form') {
      if (dirtyModelFiles().length) throw new Error('请先保存并校验模型修改，再执行校核修复。');
      const feedback = form.querySelector('#repair-feedback').value.trim();
      await launch(
        projectUrl('/models'),
        { repair: true, feedback },
        { name: 'repair-feedback', value: feedback },
      );
      closeModal();
      return;
    }
    if (form.id === 'analysis-form') {
      const question = document.getElementById('analysis-question').value.trim();
      if (!question) return;
      await launch(projectUrl('/analysis'), { question });
      return;
    }
    if (form.id === 'contract-reference-form') {
      const accepted = form.querySelector('[name=acknowledged]').checked;
      if (!accepted) return;
      state.project = await send(projectUrl('/contract-reference'), {
        revision: state.project.revision, acknowledged: true,
      });
      closeModal();
      await refreshProjects();
      toast('原版合同基线已确认，可构建应用');
      return;
    }
    if (form.id === 'analysis-model-import-form') {
      if (dirtyModelFiles().length)
        throw new Error('请先保存并校验模型修改，或放弃本地修改。');
      const names = new Set(submittedFiles.map((file) => file.name));
      if (submittedFiles.length !== ANALYSIS_IMPORT_FILES.length ||
          !ANALYSIS_IMPORT_FILES.every((name) => names.has(name)))
        throw new Error('请选择匹配文件名的五份原始模型和一份字段映射。');
      const files = Object.fromEntries(await Promise.all(
        submittedFiles.map(async (file) => [file.name, await file.text()]),
      ));
      const repairInputs = form.querySelector('#apply-original-commerce-repairs')?.checked
        ? { apply_upstream_metric_repairs: true, acknowledged: true }
        : {};
      state.project = await send(projectUrl('/models/import'), {
        files,
        revision: state.project.revision,
        ...repairInputs,
      });
      await refreshScenarioOptions();
      uploadSelections.delete(draftKey('analysis-model-files'));
      await refreshProjects();
      toast(state.project.model_diagnostics?.length ? '已转换为七模型，等待校验问题修订' : '已转换为统一七模型');
      return;
    }
    if (form.id === 'knowledge-upload-form') {
      const files = submittedFiles;
      if (!files.length) return;
      const documents = await Promise.all(
        files.map(async (f) => ({ name: f.name, text: await f.text() })),
      );
      state.project = await send(projectUrl('/knowledge/source'), { documents });
      uploadSelections.delete(draftKey('knowledge-files'));
      toast('原文已导入');
      return;
    }
    if (form.id === 'knowledge-ask-form') {
      const question = document.getElementById('knowledge-question').value.trim();
      if (!question) return;
      await launch(
        projectUrl('/knowledge/ask'),
        { question },
        { name: 'knowledge-question', value: question },
      );
      return;
    }
  });
}
root.addEventListener('submit', submit);
overlay.addEventListener('submit', submit);
prototypeEditorDialog.addEventListener('submit', submit);
prototypeEditorDialog.addEventListener('input', (event) => {
  const input = event.target;
  if (input.id !== 'prototype-edit-input') return;
  saveDraft(input.dataset.draft, input.value);
  input.form.querySelector('.send-message').disabled = input.disabled || !canSendChat(input.value);
});
overlay.addEventListener('change', (event) => {
  const input = event.target;
  if (input.id === 'metric-definition-file') {
    importMetricDocument(input.files[0], input);
    return;
  }
  if (input.name !== 'source-method') return;
  for (const fields of input.form.querySelectorAll('[data-source-method]')) {
    const active = fields.dataset.sourceMethod === input.value;
    fields.hidden = fields.disabled = !active;
  }
});
overlay.addEventListener('input', (event) => {
  if (event.target.dataset.draft) saveDraft(event.target.dataset.draft, event.target.value);
});
function submitChatWithEnter(event) {
  if (
    (['chat-input', 'prototype-edit-input', 'knowledge-question'].includes(event.target.id) ||
      (event.target.id === 'analysis-question' && state.route === 'data3')) &&
    event.key === 'Enter' &&
    !event.shiftKey &&
    !event.isComposing &&
    event.keyCode !== 229
  ) {
    event.preventDefault();
    const sendButton = event.target.form.querySelector('.send-message');
    if (!sendButton.disabled) event.target.form.requestSubmit(sendButton);
  }
}
root.addEventListener('keydown', submitChatWithEnter);
prototypeEditorDialog.addEventListener('keydown', submitChatWithEnter);
document.addEventListener('keydown', (event) => {
  if (prototypeEditorDialog.open) return;
  if (event.key === 'Escape') {
    if (overlay.firstChild) closeModal();
    else if (state.menu) {
      state.menu = false;
      render();
      document.getElementById('workspace-menu')?.focus();
    }
  }
  if (event.key === 'Tab' && overlay.firstChild) {
    const all = Array.from(overlay.querySelectorAll('button,input,textarea,select,a[href]')).filter(
      (el) => !el.matches(':disabled') && !el.closest('[hidden]'),
    );
    if (event.shiftKey && document.activeElement === all[0]) {
      event.preventDefault();
      all.at(-1)?.focus();
    } else if (!event.shiftKey && document.activeElement === all.at(-1)) {
      event.preventDefault();
      all[0]?.focus();
    }
  }
});
window.addEventListener('hashchange', () => {
  const run = prototypeRunProject();
  if (run !== state.prototypeRun) {
    state.prototypeRun = run;
    state.enteredPrototype = null;
    state.project = null;
    state.loading = true;
    state.error = '';
    state.route = run ? 'interface' : normalizeRoute(location.hash.slice(1));
    boot();
    return;
  }
  const route = normalizeRoute(location.hash.slice(1));
  if (route !== state.route) go(route).catch(showError);
});
window.addEventListener('message', async (event) => {
  if (event.origin !== 'null' || event.data?.type !== 'onto-prototype-assistant:ask') return;
  const { id, question, facts } = event.data;
  if (typeof id !== 'string' || id.length > 100 || typeof question !== 'string' || question.length > 500 ||
      typeof facts !== 'string' || facts.length > 6000) return;
  const frame = [...document.querySelectorAll('iframe[data-prototype-assistant]')].find((item) => item.contentWindow === event.source);
  if (!frame || prototypeAssistantRequests.has(frame)) return;
  const sender = frame.contentWindow;
  const respond = (result) => sender.postMessage({ type: 'onto-prototype-assistant:answer', id, ...result }, '*');
  prototypeAssistantRequests.add(frame);
  try {
    const project = encodeURIComponent(frame.dataset.projectId);
    const result = await api(`/projects/${project}/prototype/assistant`, {
      method: 'POST',
      body: JSON.stringify({ prototype_id: frame.dataset.prototypeAssistant, question, facts }),
      signal: AbortSignal.timeout(40000),
    });
    if (typeof result.answer === 'string') respond({ answer: result.answer });
    else respond({ error: true });
  } catch {
    respond({ error: true });
  } finally {
    prototypeAssistantRequests.delete(frame);
  }
});
window.addEventListener('beforeunload', (event) => {
  if (dirtyModelFiles().length) {
    event.preventDefault();
    event.returnValue = '';
  }
});
async function boot() {
  render();
  if (state.prototypeRun) {
    const id = state.prototypeRun;
    try {
      const project = await api(`/projects/${encodeURIComponent(id)}`);
      if (state.prototypeRun === id) state.project = project;
    } catch (error) {
      if (state.prototypeRun === id) state.error = error.message;
    } finally {
      if (state.prototypeRun === id) {
        state.loading = false;
        render();
      }
    }
    return;
  }
  try {
    const [status, projects] = await Promise.all([api('/status'), api('/projects')]);
    state.status = status;
    state.provider = status.provider;
    state.projects = listValue(projects);
    await chooseForKind(kind());
  } catch (err) {
    state.error = err.message;
  } finally {
    state.loading = false;
    render();
  }
}
if (!state.prototypeRun && location.hash.slice(1) !== state.route) history.replaceState(null, '', '#' + state.route);
boot();
