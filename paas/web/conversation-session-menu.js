const sessionSelector = '.conversation-item[data-action="select-project"]';

export function bindConversationSessionMenu(root, { getActions, onAction }) {
  const menu = document.createElement('div');
  menu.id = 'conversation-session-menu';
  menu.className = 'conversation-session-menu';
  menu.setAttribute('role', 'menu');
  menu.setAttribute('aria-label', '对话操作');

  let session = null;
  let trigger = null;
  let openLayout = null;
  const observer = new MutationObserver(() => {
    if (!session?.isConnected) close();
  });

  function close(restoreFocus = false) {
    const control = trigger || session;
    trigger?.setAttribute('aria-expanded', 'false');
    session = null;
    trigger = null;
    openLayout = null;
    observer.disconnect();
    menu.remove();
    if (restoreFocus && control?.isConnected) control.focus({ preventScroll: true });
  }

  function open(item, { trigger: control = null, clientX, clientY } = {}) {
    if (!root.contains(item) || !item.matches(sessionSelector)) return;
    if (control && control === trigger && item === session) {
      close(true);
      return;
    }
    close();

    const actions = getActions(item);
    if (!actions?.length) return;
    const buttons = actions.map(({ id, label, danger, disabled }) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.tabIndex = -1;
      button.classList.toggle('danger', !!danger);
      button.setAttribute('role', 'menuitem');
      button.textContent = label;
      button.disabled = !!disabled;
      button.addEventListener('click', () => {
        const selectedSession = session;
        close(true);
        onAction(id, selectedSession);
      });
      return button;
    });
    const first = buttons.find((button) => !button.disabled);
    if (!first) return;

    session = item;
    trigger = control;
    trigger?.setAttribute('aria-expanded', 'true');
    menu.replaceChildren(...buttons);
    document.body.append(menu);
    observer.observe(root, { childList: true, subtree: true });

    const viewport = document.documentElement;
    const anchor = (trigger || item).getBoundingClientRect();
    openLayout = {
      viewportWidth: viewport.clientWidth,
      viewportHeight: viewport.clientHeight,
      anchorLeft: anchor.left,
      anchorTop: anchor.top,
    };
    const { width, height } = menu.getBoundingClientRect();
    const gap = 8;
    const left = clientX ?? anchor.left;
    const top = clientY ?? (anchor.bottom + gap + height <= viewport.clientHeight
      ? anchor.bottom + gap : anchor.top - gap - height);
    menu.style.left = `${Math.max(gap, Math.min(left, viewport.clientWidth - width - gap))}px`;
    menu.style.top = `${Math.max(gap, Math.min(top, viewport.clientHeight - height - gap))}px`;
    first.focus({ preventScroll: true });
  }

  root.addEventListener('contextmenu', (event) => {
    const item = event.target.closest(sessionSelector);
    if (!item) return;
    event.preventDefault();
    const options = event.clientX || event.clientY
      ? { clientX: event.clientX, clientY: event.clientY } : undefined;
    open(item, options);
  });

  root.addEventListener('keydown', (event) => {
    if (event.key !== 'ContextMenu' && !(event.shiftKey && event.key === 'F10')) return;
    const item = event.target.closest(sessionSelector);
    if (!item) return;
    event.preventDefault();
    open(item);
  });

  menu.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' || event.key === 'Tab') {
      close(true);
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
      }
      return;
    }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    const buttons = Array.from(menu.querySelectorAll('button:not(:disabled)'));
    const current = buttons.indexOf(document.activeElement);
    let next = current + (event.key === 'ArrowUp' ? -1 : 1);
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = buttons.length - 1;
    event.preventDefault();
    buttons[(next + buttons.length) % buttons.length].focus({ preventScroll: true });
  });

  document.addEventListener('pointerdown', (event) => {
    if (session && !menu.contains(event.target) && !trigger?.contains(event.target)) close();
  }, true);
  document.addEventListener('focusin', (event) => {
    if (session && !menu.contains(event.target) && !trigger?.contains(event.target)) close();
  });
  window.addEventListener('scroll', (event) => {
    const anchor = trigger || session;
    const scroller = event.target;
    if (!anchor || !(scroller === window || scroller === document ||
        (scroller instanceof Element && scroller.contains(anchor)))) return;
    const bounds = anchor.getBoundingClientRect();
    if (bounds.left !== openLayout.anchorLeft || bounds.top !== openLayout.anchorTop) close(true);
  }, true);
  window.addEventListener('resize', () => {
    const viewport = document.documentElement;
    if (openLayout && (viewport.clientWidth !== openLayout.viewportWidth ||
        viewport.clientHeight !== openLayout.viewportHeight)) close(true);
  });

  return { open };
}
