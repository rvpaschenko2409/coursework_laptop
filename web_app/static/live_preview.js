(() => {
  const panel = document.querySelector('[data-live-preview]');
  if (!panel) return;
  const form = panel.querySelector('form');
  const status = panel.querySelector('[data-preview-status]');
  const rows = panel.querySelector('[data-preview-rows]');
  const format = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 4});
  const offline = panel.hasAttribute('data-offline-panel');
  const storageKey = 'coursework.offline-calculation.v1';
  const saveButton = panel.querySelector('[data-save-offline]');
  let verifiedInput;
  let saved;
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
      if (offline) {
        await askWorker({input: saved.input});
        return true;
      }
      const [response, reference] = await Promise.all([
        fetch(form.action, {cache: 'no-store'}),
        serverPreview(body, AbortSignal.timeout(15000)),
      ]);
      if (!response.ok) throw new Error('Snapshot unavailable');
      const input = await response.json();
      await askWorker({input});
      const result = await askWorker({scenario});
      if (!sameResult(result, reference)) throw new Error('Ranking mismatch');
      verifiedInput = input;
      if (saveButton) saveButton.disabled = false;
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
        if (!local) {
          if (offline) throw new Error('Offline worker unavailable');
          data = await serverPreview(body, signal);
        }
        if (revision === generation) show(data, local);
      } catch {
        if (revision === generation) status.textContent = 'Пересчёт не выполнен. Измените параметр или нажмите «Сбросить».';
      }
    }, 250);
  }
  function initializeOfflinePage() {
    const info = document.querySelector('[data-offline-info]');
    const remove = document.querySelector('[data-delete-offline]');
    try {
      saved = JSON.parse(localStorage.getItem(storageKey));
      if (!saved) {
        info.textContent = 'Сохранённой копии нет. Откройте исходный расчёт при запущенном сервере и сохраните его.';
        return false;
      }
      const {criterion, change, v} = form.elements;
      for (const [code, label] of saved.criteria) criterion.add(new Option(label, code));
      v.defaultValue = saved.originalV;
      [criterion.value, change.value, v.value] = saved.scenario;
      const source = new URL(saved.path, location.origin);
      const id = source.pathname.split('/').pop();
      info.textContent = 'Копия расчёта № ' + id + ' · сохранена ' +
        new Date(saved.savedAt).toLocaleString('ru-RU') + '. Изменения ползунков действуют в этой вкладке.';
      remove.hidden = false;
      remove.addEventListener('click', () => {
        try {
          localStorage.removeItem(storageKey);
          clearTimeout(timer);
          generation++;
          worker?.terminate();
          panel.hidden = true;
          remove.hidden = true;
          info.textContent = 'Сохранённая копия удалена.';
        } catch {
          info.textContent = 'Браузер не смог удалить копию.';
        }
      });
      return true;
    } catch {
      info.textContent = 'Не удалось прочитать сохранённую копию.';
      return false;
    }
  }

  async function prepareOfflinePage() {
    const registration = await navigator.serviceWorker.register('/static/offline_calculation_sw.js');
    const deadline = Date.now() + 60000;
    while (registration.active?.state !== 'activated') {
      if (Date.now() >= deadline || (!registration.installing && !registration.waiting && !registration.active)) {
        throw new Error('Offline assets unavailable');
      }
      await new Promise(resolve => setTimeout(resolve, 200));
    }
  }

  if (offline && !initializeOfflinePage()) return;
  if (saveButton && 'serviceWorker' in navigator) {
    const controls = panel.querySelector('[data-offline-controls]');
    const notice = controls.querySelector('[data-offline-status]');
    controls.hidden = false;
    saveButton.addEventListener('click', async () => {
      saveButton.disabled = true;
      notice.textContent = 'Готовим офлайн-страницу…';
      const {criterion, change, v} = form.elements;
      const copy = {
        input: verifiedInput,
        criteria: Array.from(criterion.options, option => [option.value, option.text]),
        scenario: [criterion.value, change.value, v.value],
        originalV: v.defaultValue,
        path: location.pathname,
        savedAt: Date.now(),
      };
      try {
        await prepareOfflinePage();
        localStorage.setItem(storageKey, JSON.stringify(copy));
        notice.textContent = 'Копия сохранена. Теперь её можно открыть при выключенном сервере.';
      } catch {
        notice.textContent = 'Копию не удалось сохранить. Проверьте, что сервер запущен, и повторите.';
      } finally {
        saveButton.disabled = false;
      }
    });
  }

  form.addEventListener('submit', event => event.preventDefault());
  form.addEventListener('input', schedule);
  form.addEventListener('reset', () => setTimeout(schedule, 0));
  panel.addEventListener('toggle', schedule);
  panel.hidden = false;
  if (offline) schedule();
})();
