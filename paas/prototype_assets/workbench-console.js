    function resultTable(result) {
      const section = make('section', null, 'assistant-result');
      const title = make('h3', result.title);
      const wrap = make('div', null, 'assistant-table-wrap');
      const table = make('table');
      table.setAttribute('aria-label', result.title);
      const headings = table.createTHead().insertRow();
      result.columns.forEach(label => {
        const cell = make('th', label);
        cell.scope = 'col';
        headings.append(cell);
      });
      const body = table.createTBody();
      result.rows.forEach(values => {
        const row = body.insertRow();
        values.forEach(value => { row.insertCell().textContent = value; });
      });
      if (!result.rows.length) {
        const cell = body.insertRow().insertCell();
        cell.colSpan = result.columns.length;
        cell.textContent = '当前没有符合条件的预览记录';
      }
      wrap.tabIndex = 0;
      wrap.setAttribute('aria-label', result.title + '表格');
      wrap.append(table);
      section.append(title, wrap);
      return section;
    }

    function markdownCells(line) {
      const value = line.trim();
      if (!value.includes('|')) return [];
      return value.replace(/^\||\|$/g, '').split(/(?<!\\)\|/)
        .map(cell => cell.trim().replace(/\\\|/g, '|'));
    }

    function appendModelResponse(target, content) {
      const lines = content.split('\n');
      for (let index = 0; index < lines.length; index += 1) {
        const line = lines[index].trim();
        if (!line) continue;
        const columns = markdownCells(line);
        const separator = markdownCells(lines[index + 1] || '');
        if (columns.length >= 2 && columns.length <= 8 && separator.length === columns.length
            && separator.every(cell => /^:?-{3,}:?$/.test(cell))) {
          const values = [];
          index += 1;
          while (values.length < 30 && index + 1 < lines.length) {
            const row = markdownCells(lines[index + 1]);
            if (row.length !== columns.length) break;
            values.push(row);
            index += 1;
          }
          target.append(resultTable({title: '回答明细', columns, rows: values}));
          continue;
        }
        const heading = /^(?:#{1,4}\s+(.+)|\*\*(.+?)\*\*)$/.exec(line);
        if (heading) {
          target.append(make('h3', heading[1] || heading[2]));
          continue;
        }
        const paragraph = make('p');
        line.split(/(\*\*[^*]+\*\*)/g).forEach((part, position) => {
          paragraph.append(position % 2 ? make('strong', part.slice(2, -2)) : part);
        });
        target.append(paragraph);
      }
    }

    message = function (role, content, items, report) {
      const article = make('article', null, `assistant-message ${role}`);
      article.dataset.role = role;
      const author = role === 'user' ? '我' : '业务助手';
      const bubble = make('div', null, 'assistant-message-bubble');
      const modelAnswer = role === 'assistant' && !items && !report;
      if (modelAnswer) appendModelResponse(bubble, content);
      else bubble.append(make('p', content));
      if (items?.length) {
        const list = make('ul');
        items.forEach(item => list.append(make('li', item)));
        bubble.append(list);
      }
      article.append(make('p', author, 'assistant-message-author'), bubble);
      if (report) article.append(resultTable(report));
      if (role === 'assistant') {
        const source = report ? '当前预览记录' : modelAnswer ? 'GLM · 需求与预览汇总' : '本地预览汇总';
        article.append(make('small', source, 'assistant-message-source'));
      }
      log.append(article);
      log.scrollTop = log.scrollHeight;
    };

    const askAssistant = ask;
    ask = function (question) {
      const query = question.trim().slice(0, 500);
      if (!query || assistantRequest || !log) return false;
      const report = previewReport(query);
      if (!report) return askAssistant(query, true);
      message('user', query);
      message('assistant', report.text, undefined, report);
      return true;
    };

    const setBusy = setAssistantBusy;
    setAssistantBusy = function (active) {
      setBusy(active);
      all(assistant, '[data-question]').forEach(control => { control.disabled = active; });
    };

    const shell = select(document, '.shell');
    const panelControls = all(document, '[data-toggle-panel]');
    panelControls.forEach(control => {
      control.addEventListener('click', () => {
        const panel = control.dataset.togglePanel;
        const attribute = `data-${panel}-collapsed`;
        const collapsed = shell.toggleAttribute(attribute);
        panelControls.filter(button => button.dataset.togglePanel === panel)
          .forEach(button => button.setAttribute('aria-expanded', String(!collapsed)));
        if (collapsed && control.closest('.assistant')) {
          select(document, '.app-bar [data-toggle-panel="assistant"]')?.focus();
        }
      });
    });

    const groups = all(document, '.navigation .nav-group');
    groups.forEach(group => {
      group.open = false;
      group.addEventListener('toggle', () => {
        if (group.open) groups.filter(other => other !== group).forEach(other => { other.open = false; });
      });
    });
