(() => {
  const panel = document.querySelector('[data-live-preview]');
  if (!panel) return;
  const form = panel.querySelector('form');
  const status = panel.querySelector('[data-preview-status]');
  const rows = panel.querySelector('[data-preview-rows]');
  const format = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 4});
  let timer;
  let controller;
  let generation = 0;

  function show(data) {
    const vikor = new Map(data.vikor.map(row => [row.alternative_code, row.rank]));
    const fragment = document.createDocumentFragment();
    for (const item of data.marcos) {
      const code = item.alternative_code;
      const row = document.createElement('tr');
      const label = document.createElement('th');
      label.scope = 'row';
      label.textContent = code;
      row.append(label);
      for (const [method, rank] of [['marcos', item.rank], ['vikor', vikor.get(code)]]) {
        const cell = document.createElement('td');
        const delta = data.changes[method].rank_deltas[code];
        const movement = delta < 0 ? '↑ ' + -delta : delta > 0 ? '↓ ' + delta : 'без изменений';
        cell.textContent = rank + ' · ' + movement;
        const bar = document.createElement('meter');
        bar.className = 'd-block w-100';
        bar.min = 0;
        bar.max = data.marcos.length;
        bar.value = rank;
        bar.setAttribute('aria-hidden', 'true');
        cell.append(bar);
        row.append(cell);
      }
      fragment.append(row);
    }
    rows.replaceChildren(fragment);
    const summary = ['marcos', 'vikor'].map(method =>
      method.toUpperCase() + ' · ' + data.changes[method].leaders.join(', ') +
      (data.changes[method].leaders_changed ? ' (лидер изменился)' : '')
    );
    const selected = form.elements.criterion.value;
    status.textContent = summary.join('. ') + '. Компромисс VIKOR · ' +
      data.conditions.compromise_set.join(', ') + '. Вес выбранного критерия · ' +
      format.format(data.weights[selected]) + '.';
  }

  function schedule() {
    clearTimeout(timer);
    controller?.abort();
    const revision = ++generation;
    if (!panel.open) return;
    for (const output of form.querySelectorAll('output')) {
      output.value = format.format(Number(document.getElementById(output.htmlFor.value).value));
    }
    rows.replaceChildren();
    status.textContent = 'Пересчитываем…';
    timer = setTimeout(async () => {
      controller = new AbortController();
      try {
        const response = await fetch(form.action, {
          method: 'POST',
          body: new URLSearchParams(new FormData(form)),
          signal: controller.signal,
        });
        if (!response.ok) throw new Error('Preview failed');
        const data = await response.json();
        if (revision === generation) show(data);
      } catch {
        if (revision === generation) status.textContent = 'Пересчёт не выполнен. Измените параметр или нажмите «Сбросить».';
      }
    }, 250);
  }
  form.addEventListener('submit', event => event.preventDefault());
  form.addEventListener('input', schedule);
  form.addEventListener('reset', () => setTimeout(schedule, 0));
  panel.addEventListener('toggle', schedule);
  panel.hidden = false;
})();
