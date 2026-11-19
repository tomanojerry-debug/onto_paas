const SCENARIO_NAME = 'GMV 异常波动归因分析';
const GROUP_NAME = '电商分析场景';
const TRIGGER_METRIC = 'MTR-TXN-001';
const LIVE_PROMPT = '请比较两个可比完整周期的 GMV，拆分订单量、客单价和渠道贡献，并给出核查建议。';
const EXAMPLE_PROMPT = '样本本期 GMV 为什么下降？请拆分订单量、客单价和渠道贡献。';
const BASELINE_PERIOD = '2025-06-02 至 2025-06-08';
const CURRENT_PERIOD = '2025-06-09 至 2025-06-15';
const THRESHOLD = 0.1;
const MAX_METHOD_BYTES = 128 * 1024;
const DEMO_METHOD_TEXT = `# GMV 异常波动归因分析

## 分析目标
判断 GMV 是否显著变化，拆分订单量、客单价与渠道贡献，明确仍需核对的业务因素。

## 比较口径
基期：${BASELINE_PERIOD}；本期：${CURRENT_PERIOD}。示例中 GMV 是样本订单的模拟成交额，币种为人民币。GMV 变化率的绝对值达到 10% 时触发异常核查。

## 执行步骤
1. 核算 GMV 变化：从同口径模拟记录汇总两期 GMV 和变化率，与 10% 阈值比较。
2. 拆分订单量与客单价：使用 GMV = 订单量 × 客单价，先按基期客单价测算订单量效应，再按本期订单量计算客单价效应。
3. 对照渠道贡献：计算各渠道本期与基期的 GMV 差额，区分拖累与对冲。
4. 标记验证边界：投放、流量、转化与退款等事实需要额外数据，渠道差额只说明变动发生在哪。

## 输出
保留两期汇总、拆分算式、渠道差额、样本行与待核查项，确保计算可以复算。`;

const SAMPLE_ROWS = [
  ['基期', '2025-06-02', '自然流量', 4, 2000],
  ['基期', '2025-06-05', '自然流量', 4, 2000],
  ['基期', '2025-06-03', '付费投放', 5, 2400],
  ['基期', '2025-06-06', '付费投放', 5, 2600],
  ['基期', '2025-06-04', '平台渠道', 3, 1400],
  ['基期', '2025-06-08', '平台渠道', 3, 1600],
  ['本期', '2025-06-09', '自然流量', 4, 2000],
  ['本期', '2025-06-12', '自然流量', 4, 2200],
  ['本期', '2025-06-10', '付费投放', 2, 900],
  ['本期', '2025-06-13', '付费投放', 3, 1400],
  ['本期', '2025-06-11', '平台渠道', 3, 1600],
  ['本期', '2025-06-15', '平台渠道', 3, 1700],
];
const NUMBER = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 });
const WORKSPACES = new Map();
const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[char]);
const amount = (value) => NUMBER.format(value);
const yuan = (value) => '¥' + amount(value);
const signedYuan = (value) => value === 0 ? '¥0' : (value > 0 ? '+' : '−') + yuan(Math.abs(value));
const pct = (value) => (value > 0 ? '+' : value < 0 ? '−' : '') + (Math.abs(value) * 100).toFixed(1) + '%';

function inspectMethod(name, source, builtIn = false) {
  const headings = [];
  const numbered = [];
  for (const line of source.split(/\r?\n/)) {
    const heading = line.match(/^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$/);
    if (heading) headings.push(heading[1]);
    const step = line.match(/^\s*(?:[-*+]\s+)?(?:步骤\s*)?\d+\s*[.、)）:：]\s*(.+)$/);
    if (step) numbered.push(step[1]);
  }
  return { name, source, builtIn, title: headings[0] || name.replace(/\.(?:md|markdown|txt)$/i, ''),
    headings, numbered };
}

const DEMO_METHOD = inspectMethod('GMV 异常波动归因分析.md', DEMO_METHOD_TEXT, true);

function calculateEvidence() {
  const summarize = (rows) => {
    const orders = rows.reduce((sum, row) => sum + row[3], 0);
    const gmv = rows.reduce((sum, row) => sum + row[4], 0);
    return { orders, gmv, aov: orders ? gmv / orders : 0 };
  };
  const baselineRows = SAMPLE_ROWS.filter(([period]) => period === '基期');
  const currentRows = SAMPLE_ROWS.filter(([period]) => period === '本期');
  const baseline = summarize(baselineRows);
  const current = summarize(currentRows);
  const delta = current.gmv - baseline.gmv;
  const rate = delta / baseline.gmv;
  const volumeEffect = (current.orders - baseline.orders) * baseline.aov;
  const aovEffect = current.orders * (current.aov - baseline.aov);
  const channels = [...new Set(SAMPLE_ROWS.map((row) => row[2]))].map((name) => {
    const before = summarize(baselineRows.filter((row) => row[2] === name));
    const after = summarize(currentRows.filter((row) => row[2] === name));
    return { name, before, after, delta: after.gmv - before.gmv };
  }).sort((a, b) => a.delta - b.delta);
  return { baseline, current, delta, rate, volumeEffect, aovEffect, channels };
}

const EVIDENCE = calculateEvidence();
const LIMITS = '样本只记录日期、渠道、订单量和成交额。投放预算、流量、转化率、退款、活动变更及埋点状态需要额外资料核查。';
const NEXT_CHECKS = [
  '优先核对付费投放渠道的订单量驱动。对比两期曝光、点击、转化率和投放预算，并校验渠道归属。',
  '按同一口径确认两期退款、取消订单与活动变更，排查数据缺失或埋点变化。',
];
const MODE_NOTE = '模拟分析基于合成样本和固定公式。';

function evidenceSummary({ baseline, current, delta, rate }) {
  const change = Math.abs(rate) >= THRESHOLD ? '达到' : '未达到';
  return `模拟 GMV 由 ${yuan(baseline.gmv)} 降至 ${yuan(current.gmv)}，变化 ${signedYuan(delta)}（${pct(rate)}），${change} 10% 异常核查阈值。`;
}

function effectsSummary({ baseline, current, volumeEffect, aovEffect }) {
  return `订单量由 ${baseline.orders} 减至 ${current.orders} 单，贡献 ${signedYuan(volumeEffect)}。客单价由 ${yuan(baseline.aov)} 升至 ${yuan(current.aov)}，对冲 ${signedYuan(aovEffect)}。`;
}

function channelSummary({ channels }) {
  const [largest, ...others] = channels;
  return `${largest.name}渠道变动 ${signedYuan(largest.delta)}，是样本中最大的 GMV 拖累。${others.map((channel) =>
    `${channel.name} ${signedYuan(channel.delta)}`).join('、')}。渠道差额需要结合流量、转化及投放记录解释。`;
}

function answerFor(question, method) {
  const isChange = /gmv|成交额|变化|下降|上涨|阈值|异常|波动/i.test(question);
  const isVolume = /订单|客单价|均价|量价/.test(question);
  const isChannel = /渠道|投放|自然|平台|流量|转化/.test(question);
  const isMethod = /方法|步骤|公式|计算|怎么算|怎么做/.test(question);
  const general = /归因|原因|为什么|结合|总结|报告|再分析/.test(question);
  const summary = [];
  if (isMethod) {
    summary.push(method.builtIn
      ? '按示例方法依次核对两期 GMV、拆分订单量与客单价、比较渠道差额，再列出需要补充的数据。'
      : `《${method.title}》可在场景洞察中查看。本次计算使用示例方法。`);
  }
  if (isChange || general) summary.push(evidenceSummary(EVIDENCE));
  if (isVolume || general) summary.push(effectsSummary(EVIDENCE));
  if (isChannel || general) summary.push(channelSummary(EVIDENCE));
  if (!summary.length) {
    summary.push('可查询 GMV 变化、异常阈值、订单量、客单价和渠道贡献。', evidenceSummary(EVIDENCE));
  }
  summary.push(LIMITS, ...NEXT_CHECKS);
  return summary;
}

function sectionText(value) {
  if (typeof value === 'string') return value;
  if (!Array.isArray(value)) return '';
  return value.map((section) => {
    if (typeof section === 'string') return section;
    if (!section) return '';
    const rows = (section.rows || []).map(({ label, value: content, source }) =>
      `${label}：${content}${source ? `（来源 ${source}）` : ''}`);
    return [section.title, section.text, ...rows, section.code, section.source && `来源：${section.source}`]
      .filter(Boolean).join('\n');
  }).filter(Boolean).join('\n\n');
}

function projectMetric(project, candidate) {
  if (!project?.id || !candidate?.id || !candidate.name) return null;
  const candidateProject = candidate.projectId ?? candidate.project_id;
  if (candidateProject != null && candidateProject !== project.id) return null;
  return { id: candidate.id, name: candidate.name, sections: sectionText(candidate.sections) };
}

function methodReview(method) {
  if (method.numbered.length) {
    return `<h4>方法步骤</h4><ol class="scenario-method-steps">${method.numbered.map((step) => `<li>${escape(step)}</li>`).join('')}</ol>`;
  }
  const headings = method.headings.length ? `<ol class="scenario-method-outline">${method.headings.map((heading) => `<li>${escape(heading)}</li>`).join('')}</ol>` : '';
  return `<h4>文档提纲</h4>${headings}<p>原文未包含带序号的执行步骤。可以在下方查看原文。</p>`;
}

function renderMetric(metric) {
  if (!metric) return '';
  return `<details class="scenario-metric" data-scenario-metric>
    <summary><span>关联指标洞察</span><strong>${escape(metric.name)}</strong></summary>
    ${metric.sections ? `<div class="scenario-method-source">${escape(metric.sections)}</div>` : ''}
  </details>`;
}

function renderLanding(live) {
  return `<section class="scenario-landing" data-scenario-view="landing">
    <article class="scenario-group-tile">
      <div><h2>${GROUP_NAME}</h2>
        <p>围绕成交指标复核异常波动，拆分量价和渠道影响。</p></div>
      <div class="scenario-tile-actions">
        <span>${live ? '基于当前项目数据' : '合成样本演练'}</span>
        <button type="button" class="ui-button primary" id="scenario-open-group" data-scenario-action="open-group">进入</button>
      </div>
    </article>
  </section>`;
}

function renderMethodDialog(state, live) {
  if (!state.reviewed) return '';
  const { method, review } = state;
  return `<dialog id="scenario-method-dialog" class="scenario-method-dialog" aria-labelledby="scenario-review-title">
    <header><div><h3 id="scenario-review-title">场景方法洞察</h3><p>${escape(method.name)}</p></div>
      <button type="button" class="ui-button" data-scenario-action="close-method">关闭</button></header>
    <div class="scenario-method-dialog-body">
      <section class="scenario-reviewed" data-scenario-reviewed>
        <h4 class="scenario-method-title">${escape(method.title)}</h4>
        ${live ? renderLiveMethod(review, method) : methodReview(method)}
        <details class="scenario-raw-method" open><summary>查看方法原文</summary><div class="scenario-method-source">${escape(method.source)}</div></details>
        <p class="scenario-boundary">${live
          ? (review.can_run ? '对话按照已审核口径使用只读查询。' : '请调整方法文档，并重新生成洞察后再进入对话。')
          : method.builtIn ? MODE_NOTE : '模拟分析使用内置方法，上传的文档随报告保留。'}</p>
      </section>
    </div>
  </dialog>`;
}

function renderGroup(state, metric, live, methodTemplateUrl, canReview) {
  const busy = Boolean(state.operation);
  const { method, reviewed, error } = state;
  const methodStatus = state.operation?.kind === 'reading' ? '正在读取方法文档…'
    : state.operation?.kind === 'review' ? (live ? '正在理解方法并编排步骤…' : '正在整理方法…')
    : reviewed ? (live && !state.review?.can_run ? '方法待调整 · 可查看未覆盖的步骤' : '方法洞察已生成')
      : '待生成场景洞察';
  const canEnter = reviewed && !busy && (!live || state.review?.can_run);
  return `<section class="scenario-group-view" data-scenario-view="group">
    <header class="scenario-group-head">
      <div><button type="button" class="ui-button ghost scenario-group-back" id="scenario-back-groups" data-scenario-action="back-groups">← 返回场景分组</button>
        <h2>场景清单</h2><p>${GROUP_NAME} · ${live ? '当前项目只读分析' : '合成样本演练'}</p></div>
      <div class="scenario-group-actions">
        ${methodTemplateUrl ? `<a class="ui-button scenario-template-link" href="${escape(methodTemplateUrl)}" download>下载方法模板</a>` : ''}
        <label class="ui-button scenario-upload-control" for="scenario-method-upload">上传方法文档
          <input class="sr-only" type="file" id="scenario-method-upload" accept=".md,.markdown,.txt,text/markdown,text/plain" ${busy ? 'disabled' : ''}>
        </label>
        <button type="button" class="ui-button primary" id="scenario-learn-method" data-scenario-action="review" ${busy || !canReview ? 'disabled' : ''}>场景洞察</button>
      </div>
    </header>
    <div class="scenario-staged-method">
      <div><span>${method.builtIn ? '内置方法' : '方法文档'}</span><strong>${escape(method.name)}</strong></div>
      ${method.builtIn ? '' : `<button type="button" class="ui-button" id="scenario-use-example" data-scenario-action="example" ${busy ? 'disabled' : ''}>使用内置方法</button>`}
    </div>
    ${!canReview ? '<p class="scenario-pending">确认当前项目的数据源、需求和模型，并配置模型服务后，即可生成场景洞察。</p>' : ''}
    ${error ? `<p class="scenario-error" role="alert">${escape(error)}</p>` : ''}
    <article class="scenario-list-row">
      <div class="scenario-list-row-content"><h3>${SCENARIO_NAME}</h3>
        <p>触发指标 <span class="scenario-trigger">${TRIGGER_METRIC}</span>${metric ? ` · ${escape(metric.name)}` : ' · GMV'}
          ${live ? '' : ' · 模拟'}</p><small role="status">${methodStatus}</small></div>
      <div class="scenario-row-actions">
        <button type="button" class="ui-button" id="scenario-view-method" data-scenario-action="view-method" ${reviewed && !busy ? '' : 'disabled'}>查看</button>
        <button type="button" class="ui-button primary" id="scenario-enter-chat" data-scenario-action="enter" ${canEnter ? '' : 'disabled'}>进入</button>
      </div>
    </article>
    ${renderMetric(metric)}
    ${renderMethodDialog(state, live)}
  </section>`;
}

function table(headers, rows, label) {
  return `<div class="scenario-table-scroll"><table aria-label="${escape(label)}">
    <thead><tr>${headers.map((header) => `<th scope="col">${escape(header)}</th>`).join('')}</tr></thead>
    <tbody>${rows.map((row) => `<tr>${row.map((cell, index) => index
      ? `<td>${escape(cell)}</td>` : `<th scope="row">${escape(cell)}</th>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function comparisonRows({ baseline, current }) {
  return [
    ['基期', BASELINE_PERIOD, yuan(baseline.gmv), baseline.orders, yuan(baseline.aov)],
    ['本期', CURRENT_PERIOD, yuan(current.gmv), current.orders, yuan(current.aov)],
  ];
}

function channelRows({ channels }) {
  return channels.map(({ name, before, after, delta }) => [name, yuan(before.gmv), yuan(after.gmv), signedYuan(delta)]);
}

function renderEvidence(evidence) {
  return `<details class="scenario-evidence" data-scenario-evidence open><summary>查看流程与计算证据</summary><ol class="scenario-evidence-flow">
    <li><h4>核对变化</h4>${table(['期间', '日期范围', 'GMV', '订单量', '客单价'], comparisonRows(evidence), '两期 GMV 与量价口径')}
      <p>${escape(evidenceSummary(evidence))}</p></li>
    <li><h4>拆分订单量与客单价</h4><p>${escape(effectsSummary(evidence))}</p>
      <p>量效应 =（本期订单量 − 基期订单量）× 基期客单价。价效应 = 本期订单量 ×（本期客单价 − 基期客单价）。计算使用未四舍五入的客单价。</p>
      <p class="scenario-formula">${signedYuan(evidence.volumeEffect)} ${evidence.aovEffect < 0 ? '−' : '+'} ${yuan(Math.abs(evidence.aovEffect))} = ${signedYuan(evidence.delta)}</p></li>
    <li><h4>对照渠道贡献</h4>${table(['渠道', '基期 GMV', '本期 GMV', 'GMV 差额'], channelRows(evidence), '按渠道对比 GMV')}</li>
    <li><h4>验证边界与核查建议</h4><p>${escape(LIMITS)}</p><ul>${NEXT_CHECKS.map((item) => `<li>${escape(item)}</li>`).join('')}</ul></li>
  </ol>
  <details class="scenario-raw-method"><summary>查看 ${SAMPLE_ROWS.length} 行合成样本</summary>${table(['期间', '日期', '渠道', '订单量', '成交额（元）'], SAMPLE_ROWS, '合成样本记录')}</details></details>`;
}

function renderLiveMethod(review, method) {
  const steps = Array.isArray(review?.steps) ? review.steps : [];
  const limitations = Array.isArray(review?.limitations) ? review.limitations : [];
  const conflicts = Array.isArray(review?.conflicts) ? review.conflicts : [];
  const scope = review?.execution_scope;
  return `${scope ? `<p>比较 ${escape(scope.baseline)} 与 ${escape(scope.target)} · 核查阈值 ${escape(scope.threshold_percent)}%</p>` : ''}
      ${steps.length ? `<h4>编排步骤</h4><ol class="scenario-method-steps">
        ${steps.map((step) => `<li><strong>${escape(step.label || step.id)}</strong>
          ${step.supported === false ? '<span class="scenario-unsupported">暂不支持此步骤</span>' : ''}
          ${step.quote ? `<blockquote>${escape(step.quote)}</blockquote>` : ''}</li>`).join('')}</ol>` : methodReview(method)}
      ${conflicts.length ? `<h4>需调整的口径</h4><ul class="scenario-method-limits">
        ${conflicts.map((item) => `<li>${escape(item.reason)} ${item.quote ? `<q>${escape(item.quote)}</q>` : ''}</li>`).join('')}</ul>` : ''}
      ${limitations.length ? `<h4>方法执行边界</h4><ul class="scenario-method-limits">
        ${limitations.map((item) => `<li>${escape(item)}</li>`).join('')}</ul>` : ''}`;
}

const COLUMN_TITLES = {
  period: '月份', month: '月份', date: '日期', gmv: 'GMV', orders: '订单量', order_count: '订单量',
  aov: '客单价', channel: '渠道', baseline_gmv: '基期 GMV', current_gmv: '本期 GMV',
  baseline_orders: '基期订单量', current_orders: '本期订单量', delta: 'GMV 差额',
  change: '变化额', rate: '变化率', volume: '订单量贡献', price: '客单价贡献',
  threshold_triggered: '达到核查阈值', base_orders: '基期订单量', target_orders: '本期订单量',
  base_gmv: '基期 GMV', target_gmv: '本期 GMV', recorded_amounts: '已记录实付订单数',
  first_order: '最早下单时间', last_order: '最晚下单时间', missing_dates: '缺少日期的订单数',
};
const CURRENCY_COLUMNS = new Set(['gmv', 'aov', 'base_gmv', 'target_gmv', 'delta', 'volume', 'price']);

function recordRows(records, label) {
  if (!Array.isArray(records) || !records.length || !records.every((row) => row && typeof row === 'object')) {
    return '';
  }
  const columns = [...new Set(records.flatMap((row) => Object.keys(row)))];
  const display = (value, column) => {
    if (value == null) return '—';
    if (column === 'rate') return (Number(value) * 100).toFixed(2) + '%';
    if (column === 'threshold_triggered') return value ? '是' : '否';
    if (CURRENCY_COLUMNS.has(column)) return yuan(Number(value));
    if (typeof value === 'number') return amount(value);
    return typeof value === 'object' ? JSON.stringify(value) : value;
  };
  return table(columns.map((column) => COLUMN_TITLES[column] || column),
    records.map((row) => columns.map((column) => display(row[column], column))), label);
}

function reportItem(item) {
  if (typeof item === 'string') return escape(item);
  const value = item?.text || item?.description || item?.summary || item?.label || item?.action || item;
  const reference = item?.evidence_steps?.length ? ` · ${item.evidence_steps.join('、')}` : '';
  return escape((typeof value === 'object' ? JSON.stringify(value) : value ?? '') + reference);
}

function reportItems(title, items) {
  return Array.isArray(items) && items.length ? `<div class="scenario-report-items"><h4>${title}</h4>
    <ul>${items.map((item) => `<li>${reportItem(item)}</li>`).join('')}</ul></div>` : '';
}

function renderLiveEvidence(run, open) {
  const steps = Array.isArray(run.steps) ? run.steps : [];
  const provenance = run.provenance || {};
  const querySteps = steps.map((step) => `<li>
    <h4>${escape(step.description || step.label || step.id)}</h4>
    ${step.status ? `<p>${step.status === 'succeeded' ? '已完成' : escape(step.status)}${step.id ? ' · ' + escape(step.id) : ''}</p>` : ''}
    ${step.sql ? `<details class="scenario-raw-method"><summary>查看 SQL 与参数</summary><pre class="scenario-method-source">${escape(step.sql)}${step.parameters ? '\n\n' + escape(JSON.stringify(step.parameters, null, 2)) : ''}</pre></details>` : ''}
    ${recordRows(step.rows, step.description || step.id)}
    ${step.evidence_steps?.length ? `<p>引用步骤 ${escape(step.evidence_steps.join('、'))}</p>` : ''}
    </li>`).join('');
  const monthly = recordRows(run.monthly, 'GMV 汇总') ||
    (run.monthly ? recordRows(Object.entries(run.monthly).filter(([, value]) => value && typeof value === 'object')
      .map(([period, row]) => ({ period, ...row })), 'GMV 汇总') : '');
  return `<details class="scenario-evidence" data-scenario-evidence ${open ? 'open' : ''}><summary>查看流程与计算证据</summary>
    ${monthly ? `<h4>比较期 GMV</h4>${monthly}` : ''}
    ${run.effects ? `<h4>量价拆分</h4>${recordRows([run.effects], '量价贡献')}` : ''}
    ${run.channels?.length ? `<h4>渠道贡献</h4>${recordRows(run.channels, '渠道贡献')}` : ''}
    ${querySteps ? `<ol class="scenario-evidence-flow">${querySteps}</ol>` : ''}
    ${run.source_hash || Object.keys(provenance).length ? `<details class="scenario-raw-method"><summary>查看来源与版本</summary><pre class="scenario-method-source">${escape(JSON.stringify({
      source_sha256: run.source_hash, models_sha256: run.models_sha256,
      method_sha256: run.method_sha256, ...provenance,
    }, null, 2))}</pre></details>` : ''}
  </details>`;
}

function newConversation(state, live) {
  const id = live ? crypto.randomUUID().replaceAll('-', '') : String(state.nextConversationId++);
  const conversation = { id, createdAt: Date.now(), draft: scenarioPrompt(state, live), turns: [] };
  state.conversations.set(id, conversation);
  state.activeConversationId = id;
  return conversation;
}

function scenarioPrompt(state, live) {
  if (!live) return EXAMPLE_PROMPT;
  const { baseline, target } = state.review?.execution_scope || {};
  return baseline && target
    ? `请比较 ${baseline} 与 ${target} 的 GMV，拆分订单量、客单价和渠道贡献，并给出核查建议。`
    : LIVE_PROMPT;
}

function renderTurn({ question, answer, run }, live, showEvidence) {
  const report = run?.report || {};
  return `<article class="scenario-turn">
    <div class="scenario-user"><strong>你</strong><p>${escape(question)}</p></div>
    <div class="scenario-answer"><h3>${live ? '场景分析结果' : '场景模拟结果'}</h3>
      ${live
        ? `${report.summary ? `<p>${escape(report.summary)}</p>` : ''}
          ${reportItems('主要发现', report.findings)}
          ${reportItems('核查建议', report.recommendations)}
          ${reportItems('证据边界', report.limitations)}${renderLiveEvidence(run, showEvidence)}`
        : `${answer.map((item) => `<p>${escape(item)}</p>`).join('')}${showEvidence ? renderEvidence(EVIDENCE) : ''}`}
    </div>
  </article>`;
}

function renderSessions(state) {
  const busy = Boolean(state.operation);
  const items = [...state.conversations.values()].map((session, index) => {
    const active = session.id === state.activeConversationId;
    const title = session.turns[0]?.question || `新对话 ${index + 1}`;
    const date = Number.isFinite(session.createdAt)
      ? new Date(session.createdAt).toLocaleDateString('zh-CN') : '';
    return `<li><button type="button" class="scenario-session" data-scenario-action="select-session"
      data-scenario-session="${escape(session.id)}" ${active ? 'aria-current="true"' : ''} ${busy ? 'disabled' : ''}>
      <strong title="${escape(title)}">${escape(title)}</strong>
      <small>${session.turns.length} 轮${date ? ` · ${escape(date)}` : ''}</small></button></li>`;
  }).reverse().join('');
  return `<aside class="scenario-sessions" aria-label="场景对话管理">
    <div class="scenario-sessions-head"><h3>对话管理</h3>
      <button type="button" class="ui-button" id="scenario-new-chat" data-scenario-action="new"
        aria-label="新建场景对话" title="新建场景对话" ${busy ? 'disabled' : ''}><span aria-hidden="true">+</span></button>
    </div>
    <ul class="scenario-session-list">${items}</ul>
  </aside>`;
}

function renderChat(project, state, metric, live, canRun, canDownload) {
  const conversation = state.conversations.get(state.activeConversationId);
  const busy = Boolean(state.operation);
  const turns = conversation.turns.map((turn, index) =>
    renderTurn(turn, live, index === conversation.turns.length - 1)).join('');
  const pending = state.operation?.kind === 'run' && state.operation.sessionId === conversation.id
    ? `<article class="scenario-turn"><div class="scenario-user"><strong>你</strong><p>${escape(state.operation.question)}</p></div>
       <p class="scenario-pending" role="status">正在运行只读核查并整理分析结果…</p></article>` : '';
  return `<div class="scenario-chat-view" data-scenario-view="chat">
    <header class="scenario-chat-head">
      <div><h2>${SCENARIO_NAME}</h2><p>${escape(project.name || '当前项目')}${state.method.title === SCENARIO_NAME ? '' : ' · ' + escape(state.method.title)}</p></div>
      <div class="scenario-ready-actions">
        <button type="button" class="ui-button" id="scenario-back" data-scenario-action="back">返回场景清单</button>
        <button type="button" class="ui-button primary" id="scenario-report" data-scenario-action="report" ${turns && canDownload && !busy ? '' : 'disabled'}>生成场景分析报告</button>
      </div>
    </header>
    <div class="scenario-chat-workspace">${renderSessions(state)}
    <div class="scenario-chat-body">
      <p class="scenario-chat-context">${live ? '按项目确认的 GMV 口径进行只读核查。' : MODE_NOTE}</p>
      ${renderMetric(metric)}
      <div class="scenario-turns" aria-live="polite">${turns || pending ? turns + pending : '<p class="scenario-empty-chat">可以从 GMV 的变化原因问起，也可以单独核对阈值、量价拆分或某个渠道。</p>'}</div>
      <div class="scenario-ready-actions">
        <button type="button" class="ui-button" id="scenario-prefill" data-scenario-action="prefill" ${busy ? 'disabled' : ''}>填入示例问题</button>
      </div>
      <form id="scenario-chat-form">
        <label class="sr-only" for="scenario-question">分析问题</label>
        <div class="agent-chat-composer">
          <elastic-textarea class="agent-chat-input">
            <textarea id="scenario-question" name="question" maxlength="2000" rows="1" required placeholder="询问这个场景…" ${busy ? 'disabled' : ''}>${escape(conversation.draft)}</textarea>
          </elastic-textarea>
          <button type="submit" class="ui-button primary" id="scenario-submit" ${canRun && !busy ? '' : 'disabled'}>发送</button>
        </div>
        ${canRun ? '' : '<p class="scenario-pending">确认当前项目的数据源、需求和模型，并配置模型服务后，即可运行场景分析。</p>'}
      </form>
      ${state.error ? `<p class="scenario-error" role="alert">${escape(state.error)}</p>` : ''}
      <p class="scenario-status" role="status">${escape(state.reportStatus)}</p>
    </div>
    </div>
  </div>`;
}

function markdownTable(headers, rows) {
  const line = (row) => `| ${row.map((cell) => String(cell ?? '').replaceAll('|', '\\|')).join(' | ')} |`;
  return [line(headers), line(headers.map(() => '---')), ...rows.map(line)].join('\n');
}

function fenced(text) {
  let longest = 2;
  for (const [run] of text.matchAll(/`+/g)) longest = Math.max(longest, run.length);
  const fence = '`'.repeat(longest + 1);
  return `${fence}text\n${text}\n${fence}`;
}

const reportHeading = (text) => String(text).replace(/\s+/g, ' ').replace(/[<>]/g, '').trim();

function reportText(project, state, metric) {
  const { method, activeConversationId, conversations } = state;
  const conversationsText = conversations.get(activeConversationId).turns.map(({ question, answer }, index) =>
    `### 第 ${index + 1} 轮\n\n提问\n\n${fenced(question)}\n\n模拟回答\n\n${answer.map((item) => `- ${reportHeading(item)}`).join('\n')}`).join('\n\n');
  return [
    `# ${SCENARIO_NAME} · 场景分析报告`,
    `项目：${reportHeading(project.name || '当前项目')}\n\n${MODE_NOTE}`,
    `## 当前场景对话\n\n${conversationsText}`,
    `## 分析方法\n\n${reportHeading(method.title)} · 来源 ${reportHeading(method.name)}\n\n${method.builtIn
      ? '示例方法随模拟工作台提供。' : '上传文档用于核对和存档。定量流程使用固定的 GMV 合成样本和公式。'}\n\n${fenced(method.source)}`,
    metric ? `## 关联指标洞察\n\n${fenced(`${metric.name}（${metric.id}）${metric.sections ? '\n' + metric.sections : ''}`)}\n\n指标洞察作为同项目方法参考。以下结果来自合成样本。` : '',
    `## 核对 GMV 变化\n\n${evidenceSummary(EVIDENCE)}\n\n${markdownTable(['期间', '日期范围', 'GMV', '订单量', '客单价'], comparisonRows(EVIDENCE))}`,
    `## 订单量与客单价拆分\n\n${effectsSummary(EVIDENCE)}\n\n量效应 =（本期订单量 − 基期订单量）× 基期客单价。价效应 = 本期订单量 ×（本期客单价 − 基期客单价）。用未四舍五入的客单价计算。`,
    `## 渠道贡献\n\n${channelSummary(EVIDENCE)}\n\n${markdownTable(['渠道', '基期 GMV', '本期 GMV', 'GMV 差额'], channelRows(EVIDENCE))}`,
    `## 验证边界与核查建议\n\n${LIMITS}\n\n${NEXT_CHECKS.map((item) => `- ${item}`).join('\n')}`,
    `## 合成样本记录\n\n${markdownTable(['期间', '日期', '渠道', '订单量', '成交额（元）'], SAMPLE_ROWS)}`,
  ].filter(Boolean).join('\n\n') + '\n';
}

function downloadReport(project, state, metric) {
  const blob = new Blob([reportText(project, state, metric)], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'GMV异常波动归因分析-场景分析报告.md';
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function useMethod(state, method, staged = true) {
  if (state.method.source !== method.source || state.method.name !== method.name ||
      state.method.builtIn !== method.builtIn) {
    state.method = method;
    state.reviewed = false;
    state.review = null;
    state.methodStaged = staged;
    state.activeConversationId = null;
    state.conversations.clear();
    state.reportStatus = '';
  }
}

function useLiveReview(state, review) {
  if (!review?.sha256 || typeof review.text !== 'string' || !review.name) return false;
  useMethod(state, inspectMethod(review.name, review.text, review.built_in), false);
  state.review = review;
  state.reviewed = true;
  state.methodStaged = false;
  return true;
}

function mergeLiveRun(state, run, sessionId) {
  if (!run?.report || !run.question || !sessionId ||
      (run.method_sha256 && run.method_sha256 !== state.review?.sha256)) return false;
  if (!state.conversations.has(sessionId)) {
    const createdAt = Number.isFinite(run.created_at) ? run.created_at * 1000 : undefined;
    state.conversations.set(sessionId, { id: sessionId, createdAt, draft: '', turns: [] });
  }
  const conversation = state.conversations.get(sessionId);
  if (run.id && conversation.turns.some((turn) => turn.run.id === run.id)) return true;
  const provisional = conversation.turns.findIndex(({ run: stored }) => !stored.id && stored.question === run.question);
  if (provisional >= 0) {
    if (run.id) conversation.turns[provisional] = { question: run.question, run };
  } else if (run.id || !conversation.turns.some(({ run: stored }) => stored.question === run.question)) {
    conversation.turns.push({ question: run.question, run });
  }
  return true;
}

function readProjectRuns(state, runs) {
  if (!state.reviewed || !Array.isArray(runs)) return;
  for (const run of runs) {
    if (run?.run_kind === 'gmv-attribution' && !run.stale && run.reasoning_session_id) {
      mergeLiveRun(state, run, run.reasoning_session_id);
    }
  }
}

function currentReview(project, review) {
  const bindings = [
    [project.source?.sha256, review.source_hash],
    [project.models_confirmation?.sha256, review.model_hash],
    [project.requirements_confirmation?.sha256, review.requirements_hash],
  ];
  return bindings.every(([current, reviewed]) => !current || !reviewed || current === reviewed);
}

const errorText = (error) => error instanceof Error ? error.message : String(error || '操作未完成。');

export function mountScenarioInsight(container, {
  project, metricInsight, liveAvailable = false, realReady = liveAvailable, methodTemplateUrl,
  onReviewMethod, onRun, onDownload,
} = {}) {
  if (!project?.id) {
    container.innerHTML = '<section class="scenario-insight"><p>选择一个数据分析项目后，可进入场景分组。</p></section>';
    return () => container.replaceChildren();
  }

  const live = liveAvailable;
  const builtInMethod = live ? inspectMethod('GMV异常波动归因方法.md', '', true) : DEMO_METHOD;
  if (!WORKSPACES.has(project.id) || WORKSPACES.get(project.id).live !== live) {
    WORKSPACES.set(project.id, {
      live, method: builtInMethod, reviewed: false, review: null, methodStaged: false,
      view: 'landing', operation: null, error: '', reportStatus: '', nextConversationId: 1,
      activeConversationId: null, conversations: new Map(), redraw: null,
    });
  }

  const state = WORKSPACES.get(project.id);
  if (live) {
    const review = project.gmv_method || project.gmv_method_review;
    if (!state.methodStaged && review) {
      if (currentReview(project, review)) useLiveReview(state, review);
      else {
        useMethod(state, inspectMethod(review.name, review.text, review.built_in), false);
        state.reviewed = false;
        state.review = null;
        state.activeConversationId = null;
        state.conversations.clear();
      }
    }
    readProjectRuns(state, project.runs);
  }
  const metric = projectMetric(project, metricInsight);
  const listeners = new AbortController();
  const activeConversation = () => state.conversations.get(state.activeConversationId);
  const canReview = !live || (realReady && Boolean(onReviewMethod));
  const canRun = () => !live || (realReady && Boolean(onRun) && state.review?.can_run);
  const canDownload = !live || Boolean(onDownload);

  function paint(focusSelector, latest = false) {
    if (state.view === 'chat' && !activeConversation()) state.view = 'group';
    const sidebarScroll = container.querySelector('.scenario-session-list')?.scrollTop;
    const views = {
      landing: () => renderLanding(live),
      group: () => renderGroup(state, metric, live, methodTemplateUrl, canReview),
      chat: () => renderChat(project, state, metric, live, canRun(), canDownload),
    };
    container.innerHTML = `<section class="scenario-insight" aria-label="场景分析工作台">${views[state.view]()}</section>`;
    const sidebar = container.querySelector('.scenario-session-list');
    if (sidebar && sidebarScroll !== undefined) sidebar.scrollTop = sidebarScroll;
    if (latest) {
      const turns = container.querySelector('.scenario-turns');
      const current = turns?.lastElementChild;
      if (current) turns.scrollTop = current.getBoundingClientRect().top - turns.getBoundingClientRect().top;
    }
    if (focusSelector) container.querySelector(focusSelector)?.focus({ preventScroll: true });
  }

  state.redraw = paint;

  async function operate(operation, task, success, focusSelector) {
    if (state.operation) return;
    state.operation = operation;
    state.error = '';
    state.reportStatus = '';
    state.redraw?.(undefined, operation.kind === 'run');
    try {
      success(await task());
    } catch (error) {
      state.error = errorText(error);
    } finally {
      state.operation = null;
      state.redraw?.(focusSelector, operation.kind === 'run');
    }
  }

  function upload(file) {
    if (!file) return;
    return operate({ kind: 'reading' }, async () => {
      if (!/\.(?:md|markdown|txt)$/i.test(file.name)) throw new Error('请选择 Markdown 或 TXT 方法文档。');
      if (file.size > MAX_METHOD_BYTES) throw new Error('方法文档超过 128 KB。');
      const bytes = await file.arrayBuffer();
      let source;
      try {
        source = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
      } catch {
        throw new Error('请选择使用 UTF-8 编码的文本文档。');
      }
      if (!source.trim()) throw new Error('方法文档为空。');
      return inspectMethod(file.name, source);
    }, (method) => useMethod(state, method), '#scenario-learn-method');
  }

  function reviewMethod() {
    if (!live) {
      state.reviewed = true;
      paint('#scenario-view-method');
      return;
    }
    if (!canReview) return;
    const { name, source, builtIn } = state.method;
    return operate({ kind: 'review' }, () => onReviewMethod({ name, text: source, builtIn }), (result) => {
      if (!useLiveReview(state, result) && !state.reviewed) {
        throw new Error('方法洞察返回后缺少可核对的分析步骤。');
      }
      readProjectRuns(state, project.runs);
    }, '#scenario-view-method');
  }

  function exportReport() {
    if (!activeConversation()?.turns.length || !canDownload) return;
    if (!live) {
      downloadReport(project, state, metric);
      state.reportStatus = '已生成场景分析报告。';
      container.querySelector('.scenario-status').textContent = state.reportStatus;
      return;
    }
    return operate({ kind: 'download' }, () => onDownload({ sessionId: state.activeConversationId }), () => {
      state.reportStatus = '已生成场景分析报告。';
    }, '#scenario-report');
  }

  function submitQuestion(question) {
    const conversation = activeConversation();
    if (!live) {
      conversation.turns.push({ question, answer: answerFor(question, state.method) });
      conversation.draft = '';
      state.reportStatus = '';
      paint('#scenario-question', true);
      return;
    }
    const sessionId = conversation.id;
    return operate({ kind: 'run', question, sessionId }, () => onRun({ question, sessionId }), (result) => {
      if (!mergeLiveRun(state, { ...result, question: result?.question || question }, sessionId) &&
          !conversation.turns.some((turn) => turn.question === question)) {
        throw new Error('场景分析返回后缺少实际结果，请检查任务记录。');
      }
      conversation.draft = '';
    }, '#scenario-question');
  }

  const listen = (name, handler) => container.addEventListener(name, handler, { signal: listeners.signal });

  listen('change', (event) => {
    if (event.target.id === 'scenario-method-upload') {
      event.stopPropagation();
      void upload(event.target.files[0]);
    }
  });

  listen('input', (event) => {
    if (event.target.id !== 'scenario-question') return;
    activeConversation().draft = event.target.value;
  });

  listen('click', (event) => {
    const control = event.target.closest('[data-scenario-action]');
    if (!control || control.disabled) return;
    switch (control.dataset.scenarioAction) {
      case 'open-group':
        state.view = 'group';
        paint('#scenario-learn-method');
        break;
      case 'back-groups':
        state.view = 'landing';
        paint('#scenario-open-group');
        break;
      case 'example':
        if (state.operation) return;
        useMethod(state, builtInMethod);
        state.error = '';
        paint('#scenario-learn-method');
        break;
      case 'review':
        void reviewMethod();
        break;
      case 'view-method':
        container.querySelector('#scenario-method-dialog')?.showModal();
        break;
      case 'close-method':
        container.querySelector('#scenario-method-dialog')?.close();
        break;
      case 'enter':
        if (!state.reviewed || state.operation || (live && !state.review?.can_run)) return;
        if (!activeConversation()) {
          state.activeConversationId = Array.from(state.conversations.keys()).at(-1) || null;
          if (!activeConversation()) newConversation(state, live);
        }
        state.view = 'chat';
        state.reportStatus = '';
        state.error = '';
        paint('#scenario-question', true);
        break;
      case 'back':
        state.view = 'group';
        paint('#scenario-enter-chat');
        break;
      case 'new':
        newConversation(state, live);
        state.reportStatus = '';
        state.error = '';
        paint('#scenario-question');
        container.querySelector('.scenario-session-list').scrollTop = 0;
        break;
      case 'select-session': {
        const id = control.dataset.scenarioSession;
        if (id === state.activeConversationId || !state.conversations.has(id)) return;
        state.activeConversationId = id;
        state.reportStatus = '';
        state.error = '';
        paint('[data-scenario-session][aria-current="true"]', true);
        break;
      }
      case 'prefill': {
        const field = container.querySelector('#scenario-question');
        field.value = scenarioPrompt(state, live);
        field.dispatchEvent(new Event('input', { bubbles: true }));
        field.focus();
        break;
      }
      case 'report':
        void exportReport();
        break;
    }
  });

  listen('submit', (event) => {
    if (event.target.id !== 'scenario-chat-form') return;
    event.preventDefault();
    const field = event.target.elements.question;
    const question = field.value.trim();
    if (!question || question.length > 2000 || !state.reviewed || !canRun() || state.operation) {
      field.focus();
      return;
    }
    void submitQuestion(question);
  });

  paint(undefined, state.view === 'chat');
  return () => {
    listeners.abort();
    if (state.redraw === paint) state.redraw = null;
    container.replaceChildren();
  };
}
