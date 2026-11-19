    function previewReport(question) {
      const query = String(question || '').replace(/\s+/g, '').trim();
      let config;
      try {
        config = JSON.parse(assistant?.dataset.previewReport || 'null');
      } catch {
        return null;
      }
      if (!config) return null;

      const currency = new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 0});
      const currentMonth = /本月|当月|这个月|本期/.test(query);
      const day = new Date();
      const year = String(day.getFullYear()).padStart(4, '0');
      const month = String(day.getMonth() + 1).padStart(2, '0');
      const monthStart = `${year}-${month}-01`;
      const today = `${year}-${month}-${String(day.getDate()).padStart(2, '0')}`;
      const monthLabel = `${year} 年 ${day.getMonth() + 1} 月截至 ${day.getDate()} 日`;

      function source(type, required, include = () => true) {
        const mapping = config[type];
        const page = mapping && pages.get(mapping.page);
        const keys = required.map(key => mapping?.[key]);
        if (!page || keys.some(key => typeof key !== 'string' || !key) || new Set(keys).size !== keys.length) return null;
        return {mapping, data: rows(page).filter(row => row.values && include(row))};
      }

      function value(row, key) {
        const item = key && Object.hasOwn(row.values, key) ? row.values[key] : null;
        return typeof item === 'string' || typeof item === 'number' ? String(item).trim() : '';
      }

      function date(row, key) {
        const raw = value(row, key);
        if (!/^\d{4}-\d{2}-\d{2}$/.test(raw)) return '';
        const parsed = new Date(raw + 'T00:00:00Z');
        return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === raw ? raw : '';
      }

      function inMonth(when) {
        return when >= monthStart && when <= today;
      }

      function cents(row, key) {
        const found = /^(\d+)(?:\.(\d{1,2}))?$/.exec(value(row, key));
        if (!found) return null;
        const parsed = Number(found[1]) * 100 + Number((found[2] || '').padEnd(2, '0'));
        return Number.isSafeInteger(parsed) ? parsed : null;
      }

      function add(left, right) {
        if (left === null || right === null || !Number.isSafeInteger(left + right)) return null;
        return left + right;
      }

      function money(amount) {
        if (amount === null) return '—';
        return `¥${currency.format(Math.floor(amount / 100))}.${String(amount % 100).padStart(2, '0')}`;
      }

      function unique(records, key) {
        const map = new Map();
        const duplicate = new Set();
        for (const record of records) {
          const id = value(record, key);
          if (!id || duplicate.has(id)) continue;
          if (map.has(id)) {
            map.delete(id);
            duplicate.add(id);
          } else map.set(id, record);
        }
        return map;
      }

      const recorded = row => row.phase !== '草稿';
      const reconciled = row => recorded(row) && row.phase !== '待审批';
      const departments = source('department', ['id', 'name'], recorded);
      const departmentNames = departments ? unique(departments.data, departments.mapping.id) : new Map();
      const departmentName = key => (departmentNames.has(key) ? value(departmentNames.get(key), departments.mapping.name) : '') || key;
      const contractScope = source('contract', ['id', 'department'], recorded);
      const namedDepartments = new Set([...departmentNames].map(([key]) => departmentName(key)));
      for (const row of contractScope?.data || []) {
        const department = value(row, contractScope.mapping.department);
        if (department) namedDepartments.add(departmentName(department));
      }
      const scopeName = [...namedDepartments].sort((a, b) => b.length - a.length).find(name => name && query.includes(name));
      const unmatchedScope = !scopeName && /(?:销售|市场|交付|运营|财务|采购|商务|研发)[一二三四五六七八九十\d]*部/.test(query);
      const matchesDepartment = (row, key) => !scopeName || departmentName(value(row, key)) === scopeName;
      const scopeText = scopeName ? `，范围为${scopeName}` : '';
      const exclusions = count => count ? `另有 ${count} 条记录由于标识、日期、部门或金额缺失或冲突而未计入。` : '';

      function byDepartment() {
        const title = '部门合同统计';
        const columns = ['部门编号', '部门名称', '合同数', '合同金额', '开票金额'];
        const base = source('contract', ['id', 'department', 'amount', ...(currentMonth ? ['date'] : [])], recorded);
        if (!base) return null;
        if (unmatchedScope) return {title, text: '当前预览中未匹配到所问部门。', columns, rows: []};
        const {mapping, data} = base;
        const contracts = unique(data, mapping.id);
        const invoice = source('invoice', ['id', 'contract', 'amount', ...(currentMonth ? ['date'] : [])], recorded);
        const linked = invoice && invoice.mapping.page !== mapping.page ? invoice : null;
        const groups = new Map();
        let unassigned = data.length - contracts.size;
        let missingContractAmounts = 0;
        let missingInvoiceAmounts = 0;

        function groupFor(department) {
          if (!groups.has(department)) groups.set(department, {count: 0, contract: 0, invoice: 0, hasContract: false, hasInvoice: false});
          return groups.get(department);
        }

        for (const record of contracts.values()) {
          if (!matchesDepartment(record, mapping.department)) continue;
          if (currentMonth) {
            const signed = date(record, mapping.date);
            if (!signed) unassigned++;
            if (!inMonth(signed)) continue;
          }
          const department = value(record, mapping.department);
          if (!department) { unassigned++; continue; }
          const group = groupFor(department);
          const amount = cents(record, mapping.amount);
          group.count++;
          if (amount === null) { missingContractAmounts++; continue; }
          group.hasContract = true;
          group.contract = add(group.contract, amount);
        }
        if (linked) {
          const invoices = unique(linked.data, linked.mapping.id);
          unassigned += linked.data.length - invoices.size;
          for (const record of invoices.values()) {
            const contract = contracts.get(value(record, linked.mapping.contract));
            const department = contract && value(contract, mapping.department);
            if (!department) { unassigned++; continue; }
            if (!matchesDepartment(contract, mapping.department)) continue;
            if (currentMonth) {
              const issued = date(record, linked.mapping.date);
              if (!issued) unassigned++;
              if (!inMonth(issued)) continue;
            }
            const group = groupFor(department);
            const amount = cents(record, linked.mapping.amount);
            if (amount === null) { missingInvoiceAmounts++; continue; }
            group.hasInvoice = true;
            group.invoice = add(group.invoice, amount);
          }
        }
        const results = [...groups].sort(([a], [b]) => a.localeCompare(b, 'zh-CN'))
          .map(([department, group]) => [department,
            departmentNames.has(department) ? value(departmentNames.get(department), departments.mapping.name) || '—' : '—',
            String(group.count), money(group.hasContract ? group.contract : null), money(group.hasInvoice ? group.invoice : null)]);
        const period = currentMonth ? `${monthLabel}，合同按签订日期、开票按开票日期筛选。` : '';
        const summary = results.length ? `当前预览覆盖 ${results.length} 个部门${scopeText}。` : '当前预览没有符合条件的部门统计记录。';
        const amountNotes = [
          missingContractAmounts ? `${missingContractAmounts} 条合同缺少有效金额` : '',
          missingInvoiceAmounts ? `${missingInvoiceAmounts} 条开票缺少有效金额` : '',
        ].filter(Boolean);
        const missingAmounts = amountNotes.length ? `${amountNotes.join('，')}，未计入金额合计。` : '';
        const missingJoin = linked ? '' : '未配置可用的开票关联，开票金额无法核对。';
        return {title, text: `${summary}${period}统计非草稿登记，包含待审批预览记录。${exclusions(unassigned)}${missingAmounts}${missingJoin}`, columns, rows: results};
      }

      function pendingContracts() {
        const columns = ['合同编号', '部门', '签订日期', '合同金额', '状态'];
        const base = source('contract', ['id', ...(currentMonth ? ['date'] : []), ...(scopeName ? ['department'] : [])], recorded);
        if (!base) return null;
        const title = '待审批合同';
        if (unmatchedScope) return {title, text: '当前预览中未匹配到所问部门。', columns, rows: []};
        const {mapping, data} = base;
        const contracts = unique(data, mapping.id);
        const results = [...contracts].filter(([, record]) => record.phase === '待审批'
          && matchesDepartment(record, mapping.department) && (!currentMonth || inMonth(date(record, mapping.date))))
          .map(([id, record]) => [id, departmentName(value(record, mapping.department)) || '—', date(record, mapping.date) || '—',
            money(cents(record, mapping.amount)), '待审批']);
        const period = currentMonth ? `按${monthLabel}的签订日期筛选。` : '';
        const summary = results.length ? `当前预览有 ${results.length} 条待审批合同${scopeText}。` : '当前预览没有符合条件的待审批合同。';
        return {title, text: `${summary}${period}只依据预览记录的提交状态，未按账号或审批权限筛选。${exclusions(data.length - contracts.size)}`, columns, rows: results};
      }

      function monthlyTotals(wantContract, wantInvoice) {
        const columns = ['项目', '有效金额记录数', '金额'];
        const contract = wantContract && source('contract', ['id', 'date', 'amount', ...(scopeName ? ['department'] : [])], recorded);
        const invoice = wantInvoice && source('invoice', ['id', 'date', 'amount', ...(scopeName ? ['contract'] : [])], recorded);
        if ((wantContract && !contract) || (wantInvoice && !invoice)
            || (contract && invoice && contract.mapping.page === invoice.mapping.page) || (wantInvoice && scopeName && !contractScope)) return null;
        const title = wantContract && wantInvoice ? '本月合同签订额和开票额' : wantContract ? '本月合同签订额' : '本月开票额';
        if (unmatchedScope) return {title, text: '当前预览中未匹配到所问部门。', columns, rows: []};
        const scopedContracts = scopeName && contractScope ? unique(contractScope.data, contractScope.mapping.id) : new Map();
        let missing = 0;
        const results = [];
        const sources = [[contract, '合同签订金额'], [invoice, '开票记录金额']];
        for (const [ledger, label] of sources) {
          if (!ledger) continue;
          const {mapping, data} = ledger;
          const distinct = unique(data, mapping.id);
          missing += data.length - distinct.size;
          let amount = 0;
          let count = 0;
          for (const record of distinct.values()) {
            if (scopeName && ledger === invoice) {
              const parent = scopedContracts.get(value(record, mapping.contract));
              if (!parent) { missing++; continue; }
              if (!matchesDepartment(parent, contractScope.mapping.department)) continue;
            } else if (!matchesDepartment(record, mapping.department)) continue;
            const when = date(record, mapping.date);
            if (!when) missing++;
            if (!inMonth(when)) continue;
            const value = cents(record, mapping.amount);
            if (value === null) { missing++; continue; }
            amount = add(amount, value);
            count++;
          }
          if (count) results.push([label, String(count), money(amount)]);
        }
        const summary = results.length ? `${monthLabel}的当前预览统计${scopeText}。` : `${monthLabel}，当前预览没有符合条件且可统计的记录。`;
        return {title, text: `${summary}按各自登记日期统计非草稿，包含预览待审批记录。${exclusions(missing)}`, columns, rows: results};
      }

      function unreconciledInvoices() {
        const columns = ['开票编号', '合同编号', '开票金额', '可核对收款', '可核对余额'];
        const titles = '未收款开票';
        const invoice = source('invoice', ['id', 'contract', 'amount', ...(currentMonth ? ['date'] : [])], reconciled);
        const receipt = source('receipt', ['invoice', 'amount'], reconciled);
        if (!invoice || !receipt || invoice.mapping.page === receipt.mapping.page || (scopeName && !contractScope)) return null;
        if (unmatchedScope) return {title: titles, text: '当前预览中未匹配到所问部门。', columns, rows: []};
        const invoices = unique(invoice.data, invoice.mapping.id);
        const contracts = scopeName ? unique(contractScope.data, contractScope.mapping.id) : new Map();
        const received = new Map();
        let unmatched = 0;
        for (const record of receipt.data) {
          const invoiceId = value(record, receipt.mapping.invoice);
          if (!invoices.has(invoiceId)) { unmatched++; continue; }
          received.set(invoiceId, add(received.has(invoiceId) ? received.get(invoiceId) : 0, cents(record, receipt.mapping.amount)));
        }
        let incomplete = invoice.data.length - invoices.size;
        let noReceipt = 0;
        let balance = 0;
        const results = [];
        for (const [id, record] of invoices) {
          const contract = value(record, invoice.mapping.contract);
          if (!contract) { incomplete++; continue; }
          if (scopeName) {
            const parent = contracts.get(contract);
            if (!parent) { incomplete++; continue; }
            if (!matchesDepartment(parent, contractScope.mapping.department)) continue;
          }
          if (currentMonth) {
            const issued = date(record, invoice.mapping.date);
            if (!issued) incomplete++;
            if (!inMonth(issued)) continue;
          }
          if (!received.has(id)) { noReceipt++; continue; }
          const paid = received.get(id);
          const amount = cents(record, invoice.mapping.amount);
          if (paid === null || amount === null || paid > amount) { incomplete++; continue; }
          if (amount > paid) {
            balance = add(balance, amount - paid);
            results.push([id, contract, money(amount), money(paid), money(amount - paid)]);
          }
        }
        const summary = results.length ? `当前预览里有 ${results.length} 张开票存在可核对余额${scopeText}，合计 ${money(balance)}。`
          : '当前预览没有可核对为仍有余额的开票记录。';
        const period = currentMonth ? `按${monthLabel}的开票日期筛选。` : '';
        const unknown = noReceipt ? `${noReceipt} 张开票缺少关联收款记录，收款状态暂无法核对。` : '';
        const unlinked = unmatched ? `${unmatched} 条收款记录未能关联到唯一开票。` : '';
        return {title: titles, text: `${summary}${period}${unknown}${unlinked}${exclusions(incomplete)}仅计算当前预览中可匹配的登记记录，草稿和待审批记录不参与。`, columns, rows: results};
      }

      if (/怎么|如何|怎样|录入流程|操作步骤|哪儿录入|为什么|走势|同比|环比|去年|今年|上月/.test(query)) return null;
      if (/未收款|未回款|没收齐|未收齐|未付清|未到账|应收未收/.test(query)) return unreconciledInvoices();
      if (/待(?:我)?审批|待审核|待审合同|等待审批|审批中|审批待办/.test(query)) return pendingContracts();
      if (/部门.*(?:合同|签约|签订)|合同.*(?:按|各)部门|各部门.*合同/.test(query)) return byDepartment();
      const wantContract = /签订|签约|已签/.test(query);
      const wantInvoice = /开票|发票/.test(query);
      if (currentMonth && (wantContract || wantInvoice)) return monthlyTotals(wantContract, wantInvoice);
      return null;
    }
