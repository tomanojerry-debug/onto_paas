const viewStates = new Map();
const pendingStatuses = new Set(['queued', 'running']);
const terminalFailures = new Set(['failed', 'cancelled', 'interrupted']);
const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[char]);
const sessionPattern = /^[a-f0-9]{32}$/i;

function newSession() {
  return crypto.randomUUID().replaceAll('-', '');
}

function titleOf(run) {
  return run.question || run.scenario_name || run.title || run.scenario_id || '已保存的分析';
}

function shorten(text, limit = 94) {
  const characters = Array.from(String(text || '').replace(/\s+/g, ' ').trim());
  return characters.length > limit ? characters.slice(0, limit - 1).join('') + '…' : characters.join('');
}

function getRuns(project) {
  const records = project?.runs || project?.analyses || project?.reports || [];
  return (Array.isArray(records) ? records : Object.values(records))
    .filter((record) => record && typeof record === 'object')
    .map((record) => record.result ? { ...record, ...record.result } : record)
    .filter((run) => run.run_kind !== 'gmv-attribution')
    .filter((run) => run.question || run.sql || run.report || run.answer || run.steps || run.rows);
}

function getSessions(runs) {
  const sessions = new Map();
  runs.forEach((run, index) => {
    const sessionId = run.reasoning_session_id || null;
    const key = sessionId || `history-${run.id || index}`;
    if (!sessions.has(key)) {
      sessions.set(key, { key, sessionId, title: titleOf(run), entries: [], lastIndex: 0 });
    }
    const session = sessions.get(key);
    session.entries.push({ run, index });
    session.lastIndex = index;
  });
  return [...sessions.values()].sort((left, right) => right.lastIndex - left.lastIndex);
}

function rowCount(run) {
  if (Array.isArray(run.rows)) return run.rows.length;
  if (Array.isArray(run.data?.rows)) return run.data.rows.length;
  return Number.isInteger(run.provenance?.row_count) ? run.provenance.row_count : null;
}

function asCode(value) {
  if (value === null || value === undefined || value === '') return '';
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return `<pre class="live-code">${escape(text)}</pre>`;
}

function paragraph(value) {
  return `<p>${escape(value)}</p>`;
}

function reportSummary(report) {
  if (typeof report === 'string') return shorten(report);
  return shorten(report?.summary || '');
}

function flowNodes(run) {
  const isScenario = run.run_kind === 'upstream-scenario';
  const nodes = [{
    label: isScenario ? '分析场景' : '用户问题',
    summary: titleOf(run),
    state: '输入',
    detail: paragraph(titleOf(run)),
  }];
  if (isScenario) {
    for (const step of run.steps || []) {
      const titles = { SQL: '数据查询', STAT: '统计计算', AI_REASONING: '报告整理' };
      const summary = step.description || step.id;
      const count = Number.isInteger(step.row_count) ? `${step.row_count} 行` : '';
      const inputs = Array.isArray(step.evidence_steps) ? step.evidence_steps.join('、') : '';
      nodes.push({
        label: titles[step.type] || step.type || '场景步骤',
        summary,
        state: count || (step.status === 'succeeded' ? '完成' : ''),
        detail: paragraph(summary) + asCode(step.sql) + asCode(step.statistics || step.summary || step.result) +
          (inputs ? paragraph('证据步骤 · ' + inputs) : ''),
      });
    }
  } else {
    const tables = Array.isArray(run.provenance?.tables) ? run.provenance.tables : [];
    const contextIds = Array.isArray(run.provenance?.context_run_ids) ? run.provenance.context_run_ids : [];
    if (tables.length || contextIds.length) {
      const columns = Array.isArray(run.provenance?.columns) ? run.provenance.columns : [];
      nodes.push({
        label: '查询依据', summary: tables.join('、') || '使用同会话的查询记录',
        state: tables.length ? `${tables.length} 张表` : `${contextIds.length} 条前文`,
        detail: (tables.length ? paragraph(tables.join('、')) : '') + asCode(columns.length ? columns : null) +
          (contextIds.length ? paragraph(`此次分析读取了 ${contextIds.length} 条同会话查询记录`) + asCode(contextIds) : ''),
      });
    }
    if (run.sql) {
      const count = rowCount(run);
      const time = run.provenance?.query_duration_ms;
      const parameters = run.parameters && Object.keys(run.parameters).length ? asCode(run.parameters) : '';
      nodes.push({
        label: '执行只读查询', summary: typeof run.sql === 'string' ? run.sql : '已记录 SQL',
        state: count === null ? '已执行' : `${count} 行`,
        detail: (Number.isFinite(time) ? paragraph(`查询用时 ${time} 毫秒`) : '') + asCode(run.sql) + parameters,
      });
    }
  }
  const report = run.report || run.answer || run.explanation || run.summary;
  if (report) nodes.push({ label: '分析结论', summary: reportSummary(report) || '查看报告', state: '完成', result: true });
  return nodes;
}

function renderTurn(entry, position, renderResult) {
  const { run, index } = entry;
  const nodes = flowNodes(run);
  const scenario = run.run_kind === 'upstream-scenario';
  const count = rowCount(run);
  const flowLabel = scenario ? `${(run.steps || []).length} 个分析步骤`
    : run.sql ? '只读 SQL' + (count === null ? '' : ` · ${count} 行`) : '已保存的分析';
  const buttons = nodes.map((node, nodeIndex) => `<button type="button" class="reasoning-node" data-reasoning-anchor="${position}-${node.result ? 'result' : nodeIndex}">
    <strong>${escape(node.label)}</strong><span>${escape(shorten(node.summary))}</span>
    ${node.state ? `<small>${escape(node.state)}</small>` : ''}</button>`).join('');
  const details = nodes.map((node, nodeIndex) => node.result ? '' : `<details class="reasoning-evidence" data-reasoning-evidence="${position}-${nodeIndex}">
    <summary>${escape(node.label)}${node.state ? `<small>${escape(node.state)}</small>` : ''}</summary>${node.detail}</details>`).join('');
  return `<article class="reasoning-turn" data-run-index="${index}">
    <div class="reasoning-user"><small>${scenario ? '分析场景' : '你'}</small>${paragraph(titleOf(run))}</div>
    <div class="reasoning-agent">推理助手</div>
    <details class="reasoning-flow" open><summary><strong>执行流程</strong><small>${escape(flowLabel)}</small></summary>
      <div class="reasoning-diagram" aria-label="流程图">${buttons}</div>
      ${nodes.length > 1 ? '<p class="reasoning-flow-help">点击节点查看执行明细</p>' : ''}
    </details>
    <div class="reasoning-evidence-list">${details}</div>
    <section class="reasoning-result" tabindex="-1" data-reasoning-evidence="${position}-result">${renderResult(run, index + 1)}</section>
  </article>`;
}

function renderAttempt(attempt, progressOpen) {
  const failed = terminalFailures.has(attempt.status);
  const pending = pendingStatuses.has(attempt.status);
  const statusText = {
    submitting: '正在提交分析…',
    queued: '分析已进入队列…',
    running: attempt.progress?.purpose?.startsWith('analysis_report')
      ? '正在整理查询结果…'
      : '正在执行只读分析…',
    succeeded: '分析已完成，正在同步结果…',
    failed: '这次分析未完成',
    cancelled: '已取消这次分析',
    interrupted: '这次分析已中断',
  }[attempt.status];
  const details = (Array.isArray(attempt.events) ? attempt.events : []).map((event) =>
    typeof event === 'string' ? event : event?.message || event?.phase || '',
  ).filter(Boolean);
  if (Number.isFinite(attempt.progress?.elapsed_seconds))
    details.push(`本阶段已用 ${Math.round(attempt.progress.elapsed_seconds)} 秒`);
  const cancellation = pending && attempt.id
    ? `<button type="button" id="reasoning-cancel-job" data-action="cancel-job" ${attempt.cancel_requested ? 'disabled' : ''}>${attempt.cancel_requested ? '正在取消…' : '取消任务'}</button>`
    : '';
  const explanation = failed ? attempt.error : attempt.cancellation_error || attempt.observation_error;
  return `<article class="reasoning-turn reasoning-attempt">
    <div class="reasoning-user"><small>你</small>${paragraph(attempt.question)}</div>
    <div class="reasoning-agent">推理助手</div>
    <div class="reasoning-task ${failed ? 'reasoning-task-ended' : ''}">
      <div class="reasoning-task-header">
        <span role="status">${escape(attempt.cancel_requested && pending ? '正在等待分析停止…' : statusText)}</span>
        ${cancellation}
      </div>
      ${explanation ? `<p class="reasoning-failure" role="${attempt.status === 'cancelled' ? 'status' : 'alert'}">${escape(explanation)}</p>` : ''}
      ${details.length ? `<details data-reasoning-job-details="${escape(attempt.id)}" ${progressOpen ? 'open' : ''}>
        <summary id="reasoning-progress">执行进度</summary>
        <ul>${details.map((detail) => `<li>${escape(detail)}</li>`).join('')}</ul>
      </details>` : ''}
    </div>
  </article>`;
}

function renderContext(project, session, projects, busy) {
  const source = project?.source;
  const sourceName = source?.name || source?.filename || '尚未连接数据库';
  const tables = source?.schema?.tables;
  const tableCount = Array.isArray(tables) ? tables.length : null;
  const analysisProjects = projects.filter((item) => item.kind === 'analysis');
  const options = analysisProjects.map((item) => `<option value="${escape(item.id)}" ${item.id === project?.id ? 'selected' : ''}>${escape(item.name)}</option>`).join('');
  const selector = `<select aria-label="选择数据本体" data-reasoning-project ${busy || !analysisProjects.length ? 'disabled' : ''}>
    ${project ? '' : '<option value="">选择数据本体</option>'}${options}</select>`;
  const modelCount = Object.keys(project?.models || {}).filter((name) => /^m[125367u]/.test(name)).length;
  const metricCount = project?.models?.['m7-report-model.yaml']?.metrics?.length;
  const lastRun = session?.entries.at(-1)?.run;
  const contextCount = Array.isArray(lastRun?.provenance?.context_run_ids) ? lastRun.provenance.context_run_ids.length : null;
  const summary = `<dl><dt>数据本体</dt><dd>${escape(project?.name || '尚未选择')}</dd>
    <dt>数据源</dt><dd>${escape(sourceName)}</dd>
    ${tableCount === null ? '' : `<dt>表</dt><dd>${tableCount} 张</dd>`}
    <dt>本体模型</dt><dd>${modelCount} 个模型文件</dd>
    ${Number.isInteger(metricCount) ? `<dt>指标</dt><dd>${metricCount} 项</dd>` : ''}
    <dt>会话记录</dt><dd>${session?.entries.length || 0} 次分析</dd>
    ${contextCount === null ? '' : `<dt>最近一次分析</dt><dd>读取 ${contextCount} 条会话前文</dd>`}</dl>`;
  return `<header class="reasoning-header"><div class="reasoning-title"><h2 title="${escape(session?.title || '新对话')}">${escape(session?.title || '新对话')}</h2>
    <p>${escape(project?.name || '本体推理')} · ${escape(sourceName)}</p></div>
    <div class="reasoning-controls">${selector}
      <details class="reasoning-context"><summary>分析依据</summary>${summary}</details></div></header>`;
}

export function mountReasoning(container, {
  project, projects = [], busy = false, job = null, canAsk = false,
  onSelectProject, onAsk, renderResult,
}) {
  const projectKey = project?.id || 'unbound';
  if (!viewStates.has(projectKey)) viewStates.set(projectKey, {
    drafts: new Map(), selected: null, fresh: newSession(), search: '', listOpen: false, scrollTop: 0,
  });
  const state = viewStates.get(projectKey);
  const sessions = getSessions(getRuns(project));
  const relatedJob = job?.reasoning_session_id && job.question &&
    (!job.project_id || job.project_id === project?.id) ? job : null;
  const submission = state.submission;
  if (submission) {
    const committed = sessions.find(({ key }) => key === submission.key)?.entries
      .slice(submission.count).some(({ run }) => run.question === submission.question);
    if (committed) {
      if (state.drafts.get(submission.key) === submission.value) state.drafts.delete(submission.key);
      state.submission = null;
      if (state.fresh === submission.key) state.fresh = newSession();
    } else if (relatedJob?.reasoning_session_id === submission.key && relatedJob.id !== submission.priorJobId &&
               terminalFailures.has(relatedJob.status)) {
      state.submission = null;
    }
  }
  if (relatedJob && !state.selectedByUser) state.selected = relatedJob.reasoning_session_id;
  if (!state.selected) state.selected = sessions[0]?.key || state.fresh;
  let disposed = false;
  const listeners = new AbortController();
  const listen = (name, handler) => container.addEventListener(name, handler, { signal: listeners.signal });
  const activeSession = () => sessions.find(({ key }) => key === state.selected);
  const currentValue = () => state.drafts.get(state.selected) || '';
  function attemptFor(key) {
    const currentJob = relatedJob?.reasoning_session_id === key ? relatedJob : null;
    if (state.submission?.submitting && state.submission.key === key &&
        (!currentJob || currentJob.id === state.submission.priorJobId))
      return { question: state.submission.question, status: 'submitting' };
    if (state.localFailure?.key === key) return state.localFailure;
    if (!currentJob) return null;
    return currentJob.status !== 'succeeded' || currentJob.observation_error ? currentJob : null;
  }
  const blocked = () => busy || pendingStatuses.has(relatedJob?.status) || !!state.submission?.submitting;

  function render(scrollEnd = false) {
    if (disposed) return;
    const session = activeSession();
    const attempt = attemptFor(state.selected);
    const latestDraft = { key: state.fresh, title: attemptFor(state.fresh)?.question || '新对话', entries: [] };
    const saved = sessions.some(({ key }) => key === latestDraft.key) ? sessions : [latestDraft, ...sessions];
    const visibleSessions = [...saved];
    for (const key of new Set([state.selected, relatedJob?.reasoning_session_id, state.localFailure?.key])) {
      if (key && !visibleSessions.some((session) => session.key === key))
        visibleSessions.splice(1, 0, { key, title: attemptFor(key)?.question || '新对话', entries: [] });
    }
    const list = visibleSessions.map((item) => {
      const count = item.entries.reduce((sum, { run }) => sum + (run.report || run.answer ? 2 : 1), 0);
      const active = attemptFor(item.key);
      const status = active ? terminalFailures.has(active.status) ? '未完成' : '推理中' : item.entries.length ? '已保存' : '新对话';
      return `<button type="button" class="reasoning-list-item" data-reasoning-session="${escape(item.key)}" ${item.key === state.selected ? 'aria-current="true"' : ''} ${item.title.toLowerCase().includes(state.search.toLowerCase()) ? '' : 'hidden'}>
        <strong title="${escape(item.title)}">${escape(item.title)}</strong><span>${status}<small>${count + (active ? 1 : 0)} 条</small></span></button>`;
    }).join('');
    const entries = session?.entries || [];
    container.innerHTML = `<div class="reasoning-shell${state.listOpen ? ' reasoning-list-open' : ''}">
      <aside class="reasoning-list" aria-label="推理对话"><div class="reasoning-list-head"><h2>对话管理</h2>
        <button class="ui-button reasoning-new" type="button" data-reasoning-new aria-label="新建推理对话" ${blocked() ? 'disabled' : ''}>+</button></div>
        <label class="sr-only" for="reasoning-search">搜索推理对话</label><input id="reasoning-search" type="search" placeholder="搜索对话" value="${escape(state.search)}">
        <div class="reasoning-sessions">${list}</div></aside>
      <div class="reasoning-workspace">
        <div class="reasoning-mobile-actions"><button type="button" data-reasoning-list aria-expanded="${state.listOpen}">对话列表</button>
          <button type="button" data-reasoning-new ${blocked() ? 'disabled' : ''}>新对话</button></div>
        ${renderContext(project, session || (attempt ? { title: attempt.question, entries: [] } : null), projects, blocked())}
        <div class="reasoning-scroll" aria-label="推理消息">
          ${entries.map((entry, position) => renderTurn(entry, position, renderResult)).join('')}
          ${attempt ? renderAttempt(attempt, !!attempt.id && state.progressOpenFor === attempt.id) : ''}
          ${!entries.length && !attempt ? `<div class="reasoning-empty"><h3>${project ? '从一个数据问题开始' : '选择数据本体后开始推理'}</h3>
            <p>${canAsk ? '可以提问对象关系、指标口径或业务数据。' : '先连接数据库并确认数据本体，再执行分析。'}</p></div>` : ''}
        </div>
        <form class="reasoning-composer" id="analysis-form">
          <label class="sr-only" for="analysis-question">分析问题</label>
          <div class="agent-chat-composer">
            <elastic-textarea class="agent-chat-input">
              <textarea id="analysis-question" maxlength="12000" rows="1" placeholder="询问数据对象、映射关系或指标…">${escape(currentValue())}</textarea>
            </elastic-textarea>
            <button type="submit" class="ui-button primary" aria-keyshortcuts="Enter" ${blocked() || !canAsk || !currentValue().trim() ? 'disabled' : ''}>${attempt && blocked() ? '分析中…' : '发送'}</button>
          </div>
        </form>
      </div>
    </div>`;
    const scroll = container.querySelector('.reasoning-scroll');
    scroll.scrollTop = scrollEnd ? scroll.scrollHeight : state.scrollTop;
  }

  function select(key) {
    state.selected = key;
    state.selectedByUser = true;
    state.listOpen = false;
    state.scrollTop = 0;
    render(!!attemptFor(key));
  }

  listen('click', (event) => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.hasAttribute('data-reasoning-new')) {
      const fresh = state.fresh;
      if (attemptFor(fresh) || sessions.some(({ key }) => key === fresh)) state.fresh = newSession();
      select(state.fresh);
      container.querySelector('#analysis-question').focus();
    } else if (button.hasAttribute('data-reasoning-session')) {
      select(button.dataset.reasoningSession);
    } else if (button.hasAttribute('data-reasoning-list')) {
      state.listOpen = !state.listOpen;
      render();
      container.querySelector('[data-reasoning-list]').focus();
    } else if (button.hasAttribute('data-reasoning-anchor')) {
      const evidence = container.querySelector(`[data-reasoning-evidence="${button.dataset.reasoningAnchor}"]`);
      if (!evidence) return;
      if (evidence.tagName === 'DETAILS') evidence.open = true;
      const focus = evidence.querySelector('summary') || evidence;
      focus.focus({ preventScroll: true });
      evidence.scrollIntoView({ block: 'nearest' });
    }
  });

  listen('input', (event) => {
    if (event.target.id === 'analysis-question') {
      state.drafts.set(state.selected, event.target.value);
      event.target.form.querySelector('button[type="submit"]').disabled = !event.target.value.trim() || blocked() || !canAsk;
    } else if (event.target.id === 'reasoning-search') {
      state.search = event.target.value;
      const filter = state.search.toLowerCase();
      for (const item of container.querySelectorAll('[data-reasoning-session]')) {
        item.hidden = !item.querySelector('strong').textContent.toLowerCase().includes(filter);
      }
    }
  });

  listen('change', (event) => {
    if (event.target.hasAttribute('data-reasoning-project')) {
      const id = event.target.value;
      if (id && id !== project?.id) onSelectProject(id);
    }
  });

  listen('keydown', (event) => {
    if (event.target.id === 'analysis-question' && event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!blocked() && canAsk) event.target.form.requestSubmit();
    }
  });

  listen('submit', async (event) => {
    if (event.target.id !== 'analysis-form') return;
    event.preventDefault();
    event.stopPropagation();
    const value = event.target.querySelector('#analysis-question').value;
    const question = value.trim();
    if (!question || blocked() || !canAsk) return;
    const session = activeSession();
    const key = session?.sessionId || (sessionPattern.test(state.selected) ? state.selected : state.fresh);
    state.selected = key;
    state.selectedByUser = true;
    state.drafts.set(key, value);
    state.localFailure = null;
    const request = { key, question, value, count: session?.sessionId ? session.entries.length : 0,
      priorJobId: relatedJob?.id, submitting: true };
    state.submission = request;
    render(true);
    try {
      const response = await onAsk({ question, sessionId: key });
      if (response === false && state.submission === request) state.submission = null;
    } catch (error) {
      if (state.submission === request) state.submission = null;
      state.localFailure = { key, question, status: 'failed', error: error instanceof Error ? error.message : String(error) };
    } finally {
      request.submitting = false;
      render(true);
    }
  });

  container.addEventListener('scroll', (event) => {
    if (event.target.classList?.contains('reasoning-scroll')) state.scrollTop = event.target.scrollTop;
  }, { capture: true, signal: listeners.signal });
  container.addEventListener('toggle', (event) => {
    const id = event.target.dataset?.reasoningJobDetails;
    if (id) state.progressOpenFor = event.target.open ? id : null;
  }, { capture: true, signal: listeners.signal });
  const attempt = attemptFor(state.selected);
  const attemptState = attempt ? `${attempt.id || attempt.question}:${attempt.status}` : null;
  render(!!attemptState && state.attemptState !== attemptState);
  state.attemptState = attemptState;
  return () => {
    disposed = true;
    listeners.abort();
  };
}
