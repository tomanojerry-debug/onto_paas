(() => {
  const composer = document.querySelector('.assistant-compose');
  const field = composer.querySelector('textarea');

  // The original send handlers clear the field programmatically after sending.
  for (const eventType of ['click', 'submit', 'keydown']) {
    composer.addEventListener(eventType, () => {
      if (!field.value) field.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }
})();
