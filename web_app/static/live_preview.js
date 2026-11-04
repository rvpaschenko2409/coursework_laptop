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
  let browser;
  let worker;
  let messageId = 0;

  function askWorker(payload) {
    const id = ++messageId;
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => finish(null, true), 30000);
      function finish(result, failed) {
        clearTimeout(timeout);
        worker.removeEventListener('message', receive);
        if (failed) reject(new Error('Browser calculation unavailable'));
        else resolve(result);
      }
      function receive({data}) {
        if (data.id === id) finish(data.result, data.error);
      }
      worker.addEventListener('message', receive);
      worker.postMessage({id, ...payload});
    });
  }

  async function serverPreview(body, signal) {
    const response = await fetch(form.action, {method: 'POST', body, signal});
    if (!response.ok) throw new Error('Preview failed');
    return response.json();
  }

  function sameResult(local, server) {
    const ranks = (result, method) => result[method]
      .map(row => [row.alternative_code, row.rank]).sort();
    return ['marcos', 'vikor'].every(method =>
      JSON.stringify(ranks(local, method)) === JSON.stringify(ranks(server, method))
    ) && JSON.stringify(local.conditions.compromise_set) ===
      JSON.stringify(server.conditions.compromise_set);
  }

  async function startBrowser(scenario, body) {
    try {
      worker = new Worker(panel.dataset.worker);
      const [response, reference] = await Promise.all([
        fetch(form.action, {cache: 'no-store'}),
        serverPreview(body, AbortSignal.timeout(15000)),
      ]);
      if (!response.ok) throw new Error('Snapshot unavailable');
      await askWorker({input: await response.json()});
      const result = await askWorker({scenario});
      if (!sameResult(result, reference)) throw new Error('Ranking mismatch');
      return true;
    } catch {
      worker?.terminate();
      return false;
    }
  }

  function show(data, local) {
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
    status.textContent = (local ? 'В браузере · ' : 'На сервере · ') + summary.join('. ') + '. Компромисс VIKOR · ' +
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
      const signal = controller.signal;
      const body = new URLSearchParams(new FormData(form));
      const scenario = [body.get('criterion'), Number(body.get('change')), Number(body.get('v'))];
      try {
        browser ??= startBrowser(scenario, body);
        let local = await browser;
        if (revision !== generation) return;
        let data;
        if (local) {
          try {
            data = await askWorker({scenario});
          } catch {
            browser = Promise.resolve(false);
            local = false;
          }
        }
        if (!local) data = await serverPreview(body, signal);
        if (revision === generation) show(data, local);
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
