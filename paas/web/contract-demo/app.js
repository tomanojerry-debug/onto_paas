(() => {
  'use strict';

  const contracts = [
    { id: 'HT-2026-018', name: '设备巡检服务', department: '华东交付部', signed: '2026-09-03', tax: 6, stages: [
      { id: '01', name: '第一期 · 预付款', amount: 8000000 },
      { id: '02', name: '第二期 · 进度款', amount: 4500000 },
      { id: '03', name: '第三期 · 验收款', amount: 2500000 },
    ] },
    { id: 'HT-2026-023', name: '仓配数据看板', department: '数字运营部', signed: '2026-09-07', tax: 6, stages: [
      { id: '01', name: '第一期 · 启动款', amount: 12000000 },
      { id: '02', name: '第二期 · 验收款', amount: 8000000 },
    ] },
    { id: 'HT-2026-031', name: '物料协同项目', department: '华东交付部', signed: '2026-09-12', tax: 13, stages: [
      { id: '01', name: '第一期 · 预付款', amount: 9000000 },
      { id: '02', name: '第二期 · 交付款', amount: 6000000 },
    ] },
  ];
  const invoices = [
    { id: 'FP-2026-0001', contract: 'HT-2026-018', stage: '01', amount: 8000000, tax: 6, date: '2026-09-04' },
    { id: 'FP-2026-0002', contract: 'HT-2026-023', stage: '01', amount: 12000000, tax: 6, date: '2026-09-09' },
    { id: 'FP-2026-0003', contract: 'HT-2026-031', stage: '01', amount: 9000000, tax: 13, date: '2026-09-15' },
    { id: 'FP-2026-0004', contract: 'HT-2026-018', stage: '02', amount: 4500000, tax: 6, date: '2026-09-18' },
  ];
  const receipts = [
    { id: 'SK-2026-0001', invoice: 'FP-2026-0001', amount: 8000000, date: '2026-09-09' },
    { id: 'SK-2026-0002', invoice: 'FP-2026-0003', amount: 6000000, date: '2026-09-22' },
    { id: 'SK-2026-0003', invoice: 'FP-2026-0004', amount: 2000000, date: '2026-09-24' },
  ];
  const statusLabels = { none: '尚未收款', partial: '部分收款', paid: '已收款' };
  const period = '2026-09';
  const periodLabel = '2026 年 9 月';
  const currency = new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY' });
  const percentage = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 });
  const select = (id) => document.getElementById(id);
  const form = select('invoice-form');
  const filters = select('invoice-filters');
  const dialog = select('invoice-entry');
  const workspaceStatus = select('workspace-status');
  const formError = select('invoice-form-error');
  const assistantForm = select('assistant-form');
  const assistantLog = select('assistant-log');
  const fields = ['contract', 'stage', 'amount', 'tax', 'date'].map((name) => form.elements.namedItem(name));
  const [contractField, stageField, amountField, taxField, dateField] = fields;
  let appliedFilters = {};

  function money(cents) {
    return currency.format(cents / 100);
  }

  function total(items) {
    return items.reduce((sum, item) => sum + item.amount, 0);
  }

  function contractById(id) {
    return contracts.find((contract) => contract.id === id);
  }

  function invoiceById(id) {
    return invoices.find((invoice) => invoice.id === id);
  }

  function availableStages(contract) {
    if (!contract) return [];
    return contract.stages.filter((stage) => !invoices.some((invoice) => invoice.contract === contract.id && invoice.stage === stage.id));
  }

  function collected(invoice) {
    return total(receipts.filter((receipt) => receipt.invoice === invoice.id));
  }

  function invoiceStatus(invoice) {
    const paid = collected(invoice);
    return paid === 0 ? 'none' : paid < invoice.amount ? 'partial' : 'paid';
  }

  function element(tag, text, className) {
    const item = document.createElement(tag);
    if (text != null) item.textContent = text;
    if (className) item.className = className;
    return item;
  }

  function row(cells) {
    const item = element('tr');
    for (const cell of cells) {
      const content = element('td');
      if (cell instanceof Node) content.append(cell);
      else content.textContent = cell;
      item.append(content);
    }
    return item;
  }

  function showView(name) {
    document.querySelectorAll('[data-workspace]').forEach((section) => {
      section.hidden = section.dataset.workspace !== name;
    });
    document.querySelectorAll('[data-view]').forEach((button) => {
      const active = button.dataset.view === name;
      button.classList.toggle('active', active);
      if (button.hasAttribute('aria-pressed')) button.setAttribute('aria-pressed', String(active));
      if (button.getAttribute('role') === 'tab') button.setAttribute('aria-selected', active ? 'true' : 'false');
      else if (active) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
    workspaceStatus.textContent = '';
  }

  function matches(invoice) {
    const contract = contractById(invoice.contract);
    const { invoice: number, contract: contractQuery, status, start, end } = appliedFilters;
    return (!number || invoice.id.toLowerCase().includes(number))
      && (!contractQuery || `${contract.id} ${contract.name}`.toLowerCase().includes(contractQuery))
      && (!status || invoiceStatus(invoice) === status)
      && (!start || invoice.date >= start)
      && (!end || invoice.date <= end);
  }

  function renderInvoices() {
    const rows = invoices.filter(matches).sort((left, right) => right.date.localeCompare(left.date) || right.id.localeCompare(left.id));
    const results = select('invoice-rows');
    results.replaceChildren(...rows.map((invoice) => {
      const contract = contractById(invoice.contract);
      const stage = contract.stages.find((item) => item.id === invoice.stage);
      const status = invoiceStatus(invoice);
      return row([invoice.id, contract.name, stage.name, element('span', money(invoice.amount), 'money'),
        percentage.format(invoice.tax) + '%', invoice.date, element('span', statusLabels[status], `invoice-status ${status}`)]);
    }));
    if (!rows.length) {
      const empty = row(['暂无符合条件的开票记录']);
      empty.firstElementChild.colSpan = 7;
      empty.firstElementChild.className = 'empty-row';
      results.append(empty);
    }
    select('invoice-row-count').textContent = rows.length;
  }

  function renderRelated() {
    select('contract-rows').replaceChildren(...contracts.map((contract) => row([
      contract.id, contract.name, contract.department, element('span', money(total(contract.stages)), 'money'),
      element('span', money(total(invoices.filter((invoice) => invoice.contract === contract.id))), 'money'),
    ])));
    select('receipt-rows').replaceChildren(...receipts.map((receipt) => {
      const invoice = invoiceById(receipt.invoice);
      return row([receipt.id, invoice.id, contractById(invoice.contract).name,
        element('span', money(receipt.amount), 'money'), receipt.date]);
    }));
    select('metric-signed').textContent = money(total(contracts.filter((contract) => contract.signed.startsWith(period)).flatMap((contract) => contract.stages)));
    select('metric-invoiced').textContent = money(total(invoices));
  }

  function options(control, placeholder, items) {
    control.replaceChildren(new Option(placeholder, ''), ...items.map(({ label, value }) => new Option(label, value)));
  }

  function clearError() {
    formError.textContent = '';
    formError.hidden = true;
    fields.forEach((field) => field.removeAttribute('aria-invalid'));
  }

  function reportError(field, message) {
    clearError();
    formError.textContent = message;
    formError.hidden = false;
    field.setAttribute('aria-invalid', 'true');
    field.focus();
  }

  function chooseContract() {
    const contract = contractById(contractField.value);
    const eligible = availableStages(contract);
    const placeholder = !contract ? '请先选择合同' : eligible.length ? '请选择付款阶段' : '该合同各阶段已开票';
    options(stageField, placeholder, eligible.map((stage) => ({ label: stage.name + '（' + money(stage.amount) + '）', value: stage.id })));
    stageField.disabled = !eligible.length;
    amountField.value = '';
    taxField.value = contract ? contract.tax : '';
    clearError();
  }

  function chooseStage() {
    const stage = availableStages(contractById(contractField.value)).find((item) => item.id === stageField.value);
    amountField.value = stage ? stageAmount(stage) : '';
    clearError();
  }

  function openInvoice(prefill = false) {
    if (dialog.open) return;
    showView('invoices');
    form.reset();
    options(contractField, '请选择合同', contracts.map((contract) => ({ label: `${contract.id} · ${contract.name}`, value: contract.id })));
    const sample = prefill ? contracts.find((contract) => availableStages(contract).length > 0) : null;
    if (sample) contractField.value = sample.id;
    chooseContract();
    if (sample) {
      stageField.value = availableStages(sample)[0].id;
      chooseStage();
      dateField.value = period + '-26';
    }
    dialog.showModal();
    contractField.focus();
  }

  function validDate(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const date = new Date(value + 'T00:00:00Z');
    return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
  }

  function nextInvoiceId(date) {
    const prefix = 'FP-' + date.slice(0, 4) + '-';
    const last = invoices.filter((invoice) => invoice.id.startsWith(prefix))
      .reduce((highest, invoice) => Math.max(highest, Number(invoice.id.slice(prefix.length))), 0);
    return prefix + String(last + 1).padStart(4, '0');
  }

  function stageAmount(stage) {
    return amountField.type === 'number' ? (stage.amount / 100).toFixed(2) : money(stage.amount);
  }

  function appendMessage(role, message, items = []) {
    const content = element('article', null, `assistant-message ${role}`);
    const author = role === 'user' ? '我' : '经营问答 · 演示计算';
    content.append(element('p', author, 'assistant-message-author'), element('p', message));
    if (items.length) {
      const list = element('ul');
      list.append(...items.map((item) => element('li', item)));
      content.append(list);
    }
    assistantLog.append(content);
    assistantLog.scrollTop = assistantLog.scrollHeight;
  }

  function outstanding(question) {
    const department = contracts.find((contract) => question.includes(contract.department))?.department;
    const unmatched = !department && /(?:销售|市场|交付|运营|财务|采购)[一二三四五六七八九十0-9]*部/.test(question);
    const byDepartment = !department && /部门/.test(question);
    const groups = new Map();
    for (const invoice of invoices) {
      const contract = contractById(invoice.contract);
      if (department && contract.department !== department) continue;
      const balance = invoice.amount - collected(invoice);
      if (balance <= 0) continue;
      const group = byDepartment ? contract.department : contract.name;
      groups.set(group, (groups.get(group) || 0) + balance);
    }
    const items = [...groups.entries()].sort((left, right) => right[1] - left[1])
      .map(([name, amount]) => `${name} · ${money(amount)}`);
    const balance = [...groups.values()].reduce((sum, amount) => sum + amount, 0);
    const scope = department ? `${department}的演示账本` : '全部演示部门';
    const fallback = unmatched ? '演示账本中未匹配到所问部门。' : '';
    return { message: `${fallback}${scope}的已开票未收款合计 ${money(balance)}，按${byDepartment ? '部门' : '合同'}统计。`, items };
  }

  function answer(question) {
    if (/如何|怎样|怎么|流程|规则|为什么不/.test(question)) {
      return { message: '开票时先选合同，再选尚未开票的付款阶段。金额取自该阶段，默认税率可在登记时调整。',
        items: ['补充开票日期和备注后保存。', '同一合同的同一付款阶段只能开票一次。', '收款状态由此预览中对应发票的收款记录计算。'] };
    }
    if (/可(?:以)?开票|待开票|未开票/.test(question)) {
      const available = contracts.map((contract) => ({ name: contract.name, stages: availableStages(contract) })).filter((contract) => contract.stages.length);
      const items = available.map(({ name, stages }) => `${name} · ${stages.map((stage) => `${stage.name} ${money(stage.amount)}`).join('、')}`);
      return { message: available.length ? `当前有 ${available.length} 份演示合同可继续开票。` : '所有演示合同的付款阶段均已开票。', items };
    }
    if (/未收款|没收齐|未收齐|未付清|未回款|未到账|欠款|应收|回款余额|收款余额/.test(question)
        || (/收款|回款/.test(question) && /按合同|按部门/.test(question))) return outstanding(question);
    if (/签约|已签|合同(?:总)?金额/.test(question)) {
      const signed = contracts.filter((contract) => contract.signed.startsWith(period));
      return { message: `${periodLabel}演示账本共有 ${signed.length} 份已签合同，签约金额合计 ${money(total(signed.flatMap((contract) => contract.stages)))}。` };
    }
    if (/开票|发票/.test(question)) {
      const thisPeriod = /本期|本月|9\s*月/.test(question);
      const records = thisPeriod ? invoices.filter((invoice) => invoice.date.startsWith(period)) : invoices;
      return { message: `${thisPeriod ? periodLabel : '当前'}演示账本共有 ${records.length} 笔已开票记录，金额合计 ${money(total(records))}。` };
    }
    if (/收款|回款/.test(question)) return { message: `当前演示账本的已收款合计 ${money(total(receipts))}，来自 ${receipts.length} 笔收款记录。` };
    return { message: '这个演示可以查询签约额、已开票额、可开票合同、按部门或合同统计的未收款金额，以及开票流程。' };
  }

  function ask(question) {
    const query = question.trim();
    if (!query) return;
    appendMessage('user', query);
    const result = answer(query);
    appendMessage('assistant', result.message, result.items);
  }

  amountField.readOnly = true;
  form.noValidate = true;
  select('open-invoice').addEventListener('click', () => openInvoice());
  document.querySelectorAll('[data-view]').forEach((button) => {
    button.addEventListener('click', () => showView(button.dataset.view));
  });
  dialog.querySelectorAll('[data-close-invoice]').forEach((button) => {
    button.addEventListener('click', () => dialog.close());
  });
  contractField.addEventListener('change', chooseContract);
  stageField.addEventListener('change', chooseStage);
  [dateField, taxField].forEach((field) => field.addEventListener('input', clearError));
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const contract = contractById(contractField.value);
    if (!contract) return reportError(contractField, '请选择合同。');
    const stage = contract.stages.find((item) => item.id === stageField.value);
    if (!stage) return reportError(stageField.disabled ? contractField : stageField,
      stageField.disabled ? '该合同当前没有可开票的付款阶段。' : '请选择付款阶段。');
    if (!availableStages(contract).includes(stage)) return reportError(stageField, '此付款阶段已开票，请更换付款阶段。');
    if (amountField.value !== stageAmount(stage)) {
      amountField.value = stageAmount(stage);
      return reportError(amountField, '开票金额已恢复为付款阶段金额，请核对后保存。');
    }
    if (!validDate(dateField.value)) return reportError(dateField, '请选择有效的开票日期。');
    const tax = Number(taxField.value);
    if (!taxField.value || !Number.isFinite(tax) || !taxField.validity.valid || tax < 0 || tax > 100)
      return reportError(taxField, '请输入 0 至 100 之间的开票税率，最多两位小数。');
    const id = nextInvoiceId(dateField.value);
    invoices.push({ id, contract: contract.id, stage: stage.id, amount: stage.amount, tax, date: dateField.value,
      note: form.elements.namedItem('note').value.trim() });
    filters.reset();
    showView('invoices');
    dialog.close();
    renderRelated();
    queueMicrotask(() => { workspaceStatus.textContent = `已保存演示开票 ${id}。`; });
  });

  filters.addEventListener('submit', (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(filters));
    const { start, end } = values;
    if (start && end && start > end) {
      workspaceStatus.textContent = '起始日期应早于或等于结束日期。';
      filters.elements.namedItem('end').focus();
      return;
    }
    const status = statusLabels[values.status] ? values.status : Object.keys(statusLabels).find((key) => statusLabels[key] === values.status);
    appliedFilters = { invoice: values.invoice.trim().toLowerCase(), contract: values.contract.trim().toLowerCase(), status, start, end };
    renderInvoices();
    workspaceStatus.textContent = '';
  });
  filters.addEventListener('reset', () => {
    appliedFilters = {};
    renderInvoices();
    workspaceStatus.textContent = '';
  });
  document.querySelectorAll('[data-reset-filters]').forEach((button) => {
    if (button.type !== 'reset') button.addEventListener('click', () => filters.reset());
  });
  assistantForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const question = assistantForm.elements.namedItem('question');
    ask(question.value);
    question.value = '';
    question.parentElement.update(question);
    question.focus();
  });
  assistantForm.elements.namedItem('question').addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    assistantForm.requestSubmit();
  });
  document.querySelectorAll('[data-question]').forEach((button) => {
    button.addEventListener('click', () => ask(button.dataset.question || button.textContent));
  });

  showView('invoices');
  renderInvoices();
  renderRelated();
  const location = new URLSearchParams(window.location.search);
  if (location.get('open') === 'invoice') {
    requestAnimationFrame(() => requestAnimationFrame(() => openInvoice(location.get('sample') === '1')));
  }
})();
