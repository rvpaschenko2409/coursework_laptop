/* Poll summary only; training and sensitive artifacts stay on the server. */
(() => {
  'use strict';
  const root = document.querySelector('[data-model-training-poll]');
  if (!root) return;
  const message = root.querySelector('[data-model-poll-message]');
  let stopped = false;
  async function poll() {
    if (stopped) return;
    try {
      const response = await fetch(root.dataset.statusUrl, { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) throw new Error('status');
      const data = await response.json();
      const active = data.runs.filter(run => ['queued', 'running'].includes(run.status));
      data.runs.forEach(run => {
        const row = Array.from(root.querySelectorAll('[data-run-progress]')).find(node => node.dataset.runProgress === run.id);
        if (row) row.textContent = run.progress + (run.cancel_requested ? ' · запрошена отмена' : '');
      });
      const changed = data.runs.some(run => !['queued', 'running'].includes(run.status) && root.querySelector('[data-run-progress="' + run.id + '"]')?.closest('article')?.querySelector('form[action$="/cancel"]'));
      if (!active.length || changed) { stopped = true; location.reload(); return; }
      message.textContent = 'Статус обновляется автоматически. Можно продолжать работу с проектом.';
    } catch (_) {
      message.textContent = 'Не удалось обновить статус. Проверьте соединение и обновите страницу вручную.';
    }
    if (!stopped) setTimeout(poll, 3000);
  }
  setTimeout(poll, 1500);
  addEventListener('pagehide', () => { stopped = true; }, { once: true });
})();
