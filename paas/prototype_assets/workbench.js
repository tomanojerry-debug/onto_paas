  {
    const entryDialogs = new Map(all(document, '[data-entry-dialog]').map(dialog => [
      select(dialog, '[data-entry-page]')?.dataset.page, dialog,
    ]).filter(([id]) => id));
    const assistant = select(document, '.assistant');
    const composer = assistant && select(assistant, '.assistant-compose');
    const assistantInput = composer && select(composer, 'textarea');
    const log = assistant && select(assistant, '.assistant-log');
    const assistantStatus = assistant && select(assistant, '[data-assistant-status]');
    const assistantIdle = assistantStatus?.textContent || '';
    const assistantSubmit = composer && select(composer, 'button[type=submit]');
    const numberFormat = new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 4});
    const referenceInputs = 'input[data-reference-page][data-reference-field]';
    const referenceScopes = new WeakMap();
    let assistantRequest = null;
    let assistantSequence = 0;
    let refreshPending = false;

    function text(value, limit) {
      return String(value ?? '').replace(/\s+/g, ' ').trim().slice(0, limit);
    }

    function pageTitle(page) {
      return text(select(page, 'h1, .dialog-title h2')?.textContent || page.dataset.page, 120);
    }

    function scopeControl(input) {
      const scope = input.dataset.referenceScopeField;
      if (!scope) return null;
      const page = input.closest('[data-page]');
      const form = page && select(page, '[data-main]');
      return controls(form).find(field => field.dataset.field === scope
        && field.dataset.referencePage === input.dataset.referencePage) || null;
    }

    function optionValues(input, scope) {
      const page = pages.get(input.dataset.referencePage);
      const key = input.dataset.referenceField;
      if (!page || !key) return [];
      let entries = rows(page).map(record => record.values);
      if (input.dataset.referenceScopeField) {
        const parentKey = scope?.dataset.referenceField;
        if (!parentKey || !scope.value) return [];
        entries = rows(page).filter(record => Object.hasOwn(record.values, parentKey)
          && String(record.values[parentKey]) === scope.value).flatMap(record => record.lines);
      }
      const values = entries.flatMap(entry => {
        if (!Object.hasOwn(entry, key)) return [];
        const value = entry[key];
        return (typeof value === 'string' || typeof value === 'number') && String(value).trim()
          ? [String(value)] : [];
      });
      return [...new Set(values)];
    }

    function refreshReference(input) {
      const reference = input.closest('.reference');
      const choose = reference && select(reference, 'button[data-event=choose-reference]');
      if (!choose) return;
      const scope = scopeControl(input);
      const choices = optionValues(input, scope);
      choose.dataset.options = JSON.stringify(choices);
      choose.disabled = choices.length === 0;
      input.placeholder = choices.length ? '请选择' : '暂无可选关联项';
      const selectedScope = scope?.value || '';
      const oldScope = referenceScopes.get(input);
      if (input.dataset.referenceScopeField) {
        referenceScopes.set(input, selectedScope);
        if (!selectedScope) input.placeholder = '请先选择' + (scope?.getAttribute('aria-label') || '上游关联项');
      }
      if (input.value && (!choices.includes(input.value) || (oldScope !== undefined && oldScope !== selectedScope))) {
        input.value = '';
      }
    }

    function refreshReferences(root = document) {
      all(root, referenceInputs).forEach(refreshReference);
    }

    function metricSnapshots() {
      if (!assistant) return [];
      return all(assistant, '[data-metric-page]').map(metric => {
        const page = pages.get(metric.dataset.metricPage);
        const field = metric.dataset.metricField;
        const label = text(select(metric, 'dt, .metric-label, small')?.textContent || (page && pageTitle(page)) || '', 120);
        const data = page ? rows(page) : [];
        const usable = field ? data.flatMap(row => {
          if (!Object.hasOwn(row.values, field)) return [];
          const value = row.values[field];
          const raw = typeof value === 'string' || typeof value === 'number' ? String(value).trim() : '';
          const parsed = Number(raw);
          return raw && Number.isFinite(parsed) ? [parsed] : [];
        }) : [];
        const sum = usable.reduce((total, value) => total + value, 0);
        const available = Boolean(page && (!field || (usable.length && Number.isFinite(sum))));
        return {metric, label, field, count: data.length, usable: usable.length, available,
          value: available ? numberFormat.format(field ? sum : data.length) : '—'};
      });
    }

    function refreshMetrics() {
      for (const value of metricSnapshots()) {
        const output = select(value.metric, 'strong');
        if (!output) continue;
        output.textContent = value.value;
        if (value.field) {
          const details = value.available
            ? `已计入 ${value.usable} 条，缺少有效数值 ${value.count - value.usable} 条`
            : '当前没有可汇总的数值';
          output.title = details;
          output.setAttribute('aria-label', `${value.value}。${details}。`);
        }
      }
    }

    function refreshWorkbench() {
      for (const page of pages.values()) {
        refreshTable(page);
        refreshDetail(page);
      }
      refreshReferences();
      refreshMetrics();
    }

    function scheduleRefresh() {
      if (refreshPending) return;
      refreshPending = true;
      queueMicrotask(() => {
        refreshPending = false;
        refreshWorkbench();
      });
    }

    const savePreviewRow = storeRow;
    storeRow = function (...args) {
      const saved = savePreviewRow(...args);
      if (saved) scheduleRefresh();
      return saved;
    };

    function openEntry(id) {
      const dialog = entryDialogs.get(id);
      const page = pages.get(id);
      if (!dialog || !page || dialog.open) return false;
      page.hidden = false;
      refreshForm(page);
      const status = select(page, '[data-result]');
      if (status) notice(page, '');
      const validation = select(page, '[data-validation]');
      if (validation) validation.textContent = '';
      refreshReferences(page);
      dialog.showModal();
      return true;
    }

    function mainAction(page, dialog, kind) {
      if (kind === 'cancel') {
        dialog.close();
        return;
      }
      const form = select(page, '[data-main]');
      if (!form) return;
      refreshReferences(form);
      const previous = state(page).current;
      const saved = storeRow(page, form, kind, previous);
      if (!saved) return;
      state(page).current = saved;
      if (kind !== 'draft') {
        const behind = pages.get(active);
        if (behind && behind !== page && select(behind, '[data-result]')) {
          notice(behind, `已保存「${pageTitle(page)}」的预览记录。`);
        }
        dialog.close();
      }
    }

    for (const [id, dialog] of entryDialogs) {
      dialog.addEventListener('close', () => {
        const page = pages.get(id);
        if (page && !dialog.open) page.hidden = true;
      });
    }

    document.addEventListener('click', event => {
      if (!(event.target instanceof Element)) return;
      const control = event.target.closest('button[data-event]');
      if (!control) return;
      const kind = control.dataset.event;
      const target = control.dataset.target;
      if (kind === 'open_form' || (kind === 'navigate' && entryDialogs.has(target))) {
        event.preventDefault();
        event.stopPropagation();
        openEntry(target);
        return;
      }
      const dialog = control.closest('[data-entry-dialog]');
      if (dialog && ['save', 'submit', 'draft', 'cancel'].includes(kind)) {
        event.preventDefault();
        event.stopPropagation();
        const page = select(dialog, '[data-entry-page]');
        if (page) mainAction(page, dialog, kind);
        return;
      }
      if (kind === 'choose-reference') {
        const input = select(control.closest('.reference'), referenceInputs);
        if (!input) return;
        refreshReference(input);
        if (control.disabled) event.stopPropagation();
      }
    }, true);

    document.addEventListener('focusin', event => {
      if (!(event.target instanceof Element)) return;
      const wrapper = event.target.closest('.reference');
      const input = wrapper && select(wrapper, referenceInputs);
      if (input) refreshReference(input);
    });

    document.addEventListener('change', event => {
      if (!(event.target instanceof Element) || !event.target.matches(referenceInputs)) return;
      const page = event.target.closest('[data-page]');
      if (page) refreshReferences(page);
    });

    document.addEventListener('click', event => {
      if (!(event.target instanceof Element)) return;
      const control = event.target.closest('button[data-event]');
      if (!control) return;
      const kind = control.dataset.event;
      if (kind === 'reference-pick' || kind === 'reference-clear') {
        const page = selectedReference?.closest('[data-page]');
        if (page) refreshReferences(page);
      }
      if (kind === 'delete-confirm') scheduleRefresh();
      if (['detail-add', 'create', 'record-edit'].includes(kind)) {
        const page = control.closest('[data-page]');
        if (page) refreshReferences(page);
      }
    });

    function pageSnapshots() {
      const snapshots = new Map();
      for (const page of pages.values()) {
        const key = page.dataset.recordSource || page.dataset.page;
        const source = pages.get(key) || page;
        if (snapshots.has(key) || !['form', 'single_form', 'master_detail_form', 'list_maintenance'].includes(source.dataset.layout)) continue;
        const data = rows(source);
        const phases = new Map();
        for (const record of data) {
          phases.set(record.phase, (phases.get(record.phase) || 0) + 1);
        }
        const links = all(source, 'button[data-event]').filter(control => (
          ['create', 'edit', 'save', 'draft', 'submit', 'query', 'open_form'].includes(control.dataset.event)
        )).map(control => text(control.textContent, 60));
        snapshots.set(key, {name: pageTitle(source), count: data.length,
          phases: [...phases.entries()].map(([phase, count]) => `${text(phase, 30)} ${count} 条`),
          actions: [...new Set(links)].filter(Boolean).slice(0, 8)});
      }
      return [...snapshots.values()];
    }

    function metricText(value) {
      if (!value.field) return `${value.label}：${value.available ? `${value.value} 条` : '没有可用数据'}`;
      return `${value.label}：${value.available ? value.value : '没有可汇总的数值'}（可用 ${value.usable} 条，缺值 ${value.count - value.usable} 条）`;
    }

    function previewContext() {
      const snapshots = pageSnapshots();
      const metrics = metricSnapshots();
      const names = [...pages.values()].map(pageTitle).filter(Boolean);
      const sourceLines = snapshots.map(({name, count, phases, actions}) => (
        `${name}：${count} 条${phases.length ? '，状态 ' + phases.join('、') : ''}${actions.length ? '，操作 ' + actions.join('、') : ''}`
      ));
      const facts = [
        '范围：仅当前原型标签页的临时录入或明确标注的示例记录。记录数量与指标包含草稿，没有生产库、账号或权限上下文，缺值不能据此推断。',
        '页面：' + names.join('、'),
        '记录：' + (sourceLines.join('\n') || '无已配置录入来源'),
        '指标：' + (metrics.map(metricText).join('\n') || '无已配置指标'),
      ].join('\n').slice(0, 6000);
      return {snapshots, metrics, facts};
    }

    function message(role, content, items = []) {
      const article = make('article', null, `assistant-message ${role}`);
      article.dataset.role = role;
      const author = role === 'user' ? '我' : '业务助手';
      article.append(make('p', author, 'assistant-message-author'), make('p', content));
      if (items.length) {
        const list = make('ul');
        items.forEach(item => list.append(make('li', item)));
        article.append(list);
      }
      log.append(article);
      log.scrollTop = log.scrollHeight;
    }

    function localSummary(question, context, unavailable = false) {
      const total = context.snapshots.reduce((sum, item) => sum + item.count, 0);
      const matchedMetrics = context.metrics.filter(metric => metric.label && question.includes(metric.label));
      const matchedPages = context.snapshots.filter(item => question.includes(item.name));
      const chosenMetrics = matchedMetrics.length ? matchedMetrics : context.metrics;
      const chosenPages = matchedPages.length ? matchedPages : context.snapshots;
      const intro = unavailable ? '模型暂未连接。以下是当前窗口的本地只读统计。' : '以下是当前窗口的本地只读统计。';
      if (/如何|怎么|怎样|流程|步骤|入口|在哪/.test(question)) {
        const actions = chosenPages.filter(item => item.actions.length).slice(0, 6)
          .map(item => `${item.name}：${item.actions.join('、')}`);
        if (actions.length) {
          message('assistant', `${unavailable ? '模型暂未连接。' : ''}以下是当前原型里已配置的页面操作。`, actions);
          return;
        }
      }
      const rowsSummary = chosenPages.slice(0, 6).map(item => `${item.name}：${item.count} 条${item.phases.length ? '（' + item.phases.join('、') + '）' : ''}`);
      const items = [...chosenMetrics.slice(0, 6).map(metricText), ...rowsSummary];
      message('assistant', `${intro}可核对的预览记录共 ${total} 条。`, items);
    }

    function setAssistantBusy(active) {
      if (assistantStatus) assistantStatus.textContent = active ? '正在查询原型记录…' : assistantIdle;
      if (assistantSubmit) assistantSubmit.disabled = active;
      if (log) log.setAttribute('aria-busy', String(active));
    }

    function finishAssistant(answer) {
      const request = assistantRequest;
      if (!request) return;
      clearTimeout(request.timer);
      assistantRequest = null;
      setAssistantBusy(false);
      if (answer) message('assistant', answer);
      else localSummary(request.question, request.context, true);
    }

    function ask(question, freeform = false) {
      const query = question.trim().slice(0, 500);
      if (!query || assistantRequest || !log) return false;
      const context = previewContext();
      message('user', query);
      if (!freeform || window.parent === window) {
        localSummary(query, context, freeform);
        return true;
      }
      const id = `prototype-${Date.now().toString(36)}-${++assistantSequence}`;
      assistantRequest = {id, question: query, context, timer: setTimeout(() => finishAssistant(''), 45000)};
      setAssistantBusy(true);
      window.parent.postMessage({type: 'onto-prototype-assistant:ask', id, question: query, facts: context.facts}, '*');
      return true;
    }

    window.addEventListener('message', event => {
      const response = event.data;
      if (event.source !== window.parent || !assistantRequest || !response
          || response.type !== 'onto-prototype-assistant:answer' || response.id !== assistantRequest.id) return;
      finishAssistant(typeof response.answer === 'string' && !response.error ? response.answer.trim().slice(0, 12000) : '');
    });

    if (assistantInput) {
      if (assistantInput.maxLength < 0 || assistantInput.maxLength > 500) assistantInput.maxLength = 500;
      const askInput = () => { if (ask(assistantInput.value, true)) assistantInput.value = ''; };
      assistantSubmit.addEventListener('click', event => {
        event.preventDefault();
        askInput();
      });
      composer.addEventListener('submit', event => {
        event.preventDefault();
        askInput();
      });
      assistantInput.addEventListener('keydown', event => {
        if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
        event.preventDefault();
        askInput();
      });
      all(assistant, '[data-question]').forEach(control => {
        control.addEventListener('click', () => ask(control.dataset.question || control.textContent));
      });
    }

    refreshReferences();
    refreshMetrics();
  }
