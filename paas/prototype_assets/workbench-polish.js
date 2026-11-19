    const renderMessage = message;
    message = function (role, content, items, report) {
      renderMessage(role, content, items, report);
      const entry = log.lastElementChild;
      entry.setAttribute('aria-label', role === 'user' ? '你的消息' : '助手回复');
      select(entry, '.assistant-message-author')?.remove();
      const source = select(entry, '.assistant-message-source');
      if (source) {
        source.textContent = report ? '预览统计' : items ? '本地汇总' : 'GLM';
      }
      log.scrollTop = log.scrollHeight;
    };

    const openReference = selectReference;
    selectReference = function (control) {
      control.focus();
      openReference(control);
      const options = all(references, '[data-event="reference-pick"]');
      const current = options.find(option => option.dataset.value === selectedReference?.value);
      if (current) current.dataset.current = '';
      (current || options[0])?.focus();
    };
    references.addEventListener('close', event => {
      // A previous Escape can dispatch close after the picker has already reopened.
      if (references.open) event.stopImmediatePropagation();
    }, {capture: true});

    function activateReference(input) {
      const chooser = select(input.closest('.reference'), '[data-event="choose-reference"]');
      if (!chooser.disabled) chooser.click();
    }

    document.addEventListener('click', event => {
      if (event.target.matches?.('.reference input[readonly]')) activateReference(event.target);
    });

    document.addEventListener('keydown', event => {
      const input = event.target;
      if (event.isComposing || !(input instanceof HTMLInputElement)) return;
      if (input.matches('.reference input[readonly]') && [' ', 'Enter'].includes(event.key)) {
        event.preventDefault();
        activateReference(input);
      } else if (event.key === 'Enter' && ['text', 'number'].includes(input.type) && input.closest('[data-filter]')) {
        event.preventDefault();
        filterRows(input.closest('[data-page]'));
      }
    });
