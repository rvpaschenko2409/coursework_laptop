(() => {
  const projectPrefix = 'coursework.offline-project.v1.';
  async function prepareOfflinePage() {
    const registration = await navigator.serviceWorker.register('/static/offline_calculation_sw.js');
    await registration.update();
    const deadline = Date.now() + 60000;
    while (registration.active?.state !== 'activated' || registration.installing || registration.waiting ||
        !(await caches.match('/static/offline_calculation.html', {cacheName: 'coursework-offline-calculation-criteria-v2'}))) {
      if (Date.now() >= deadline || (!registration.installing && !registration.waiting && !registration.active)) {
        throw new Error('Offline assets unavailable');
      }
      await new Promise(resolve => setTimeout(resolve, 200));
    }
  }

  const projectBox = document.querySelector('[data-save-project]');
  if (projectBox && 'serviceWorker' in navigator) {
    projectBox.hidden = false;
    const button = projectBox.querySelector('button');
    const notice = projectBox.querySelector('[data-offline-project-status]');
    button.addEventListener('click', async () => {
      button.disabled = true;
      notice.textContent = 'Готовим офлайн-копию проекта…';
      try {
        const response = await fetch(projectBox.dataset.saveProject, {cache: 'no-store'});
        const input = await response.json();
        if (!response.ok) throw new Error(typeof input.detail === 'string' ? input.detail : 'Проект недоступен.');
        await prepareOfflinePage();
        localStorage.setItem(projectPrefix + input.project_id, JSON.stringify({input, savedAt: Date.now()}));
        projectBox.querySelector('a').href = '/static/offline_calculation.html?project=' + input.project_id;
        notice.textContent = 'Проект сохранён. Теперь его можно открыть при выключенном сервере.';
      } catch (error) {
        notice.textContent = 'Не удалось сохранить проект. ' + error.message;
      } finally {
        button.disabled = false;
      }
    });
  }

  const draftPrefix = 'coursework.offline-draft.v1.';

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
        if (delta < 0) cell.classList.add('rank-gained');
        if (delta > 0) cell.classList.add('rank-lost');
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
    const mode = offline && (saved.input.project_id || form.elements.merec?.checked) ? 'MEREC рассчитан заново · ' : '';
    status.textContent = (local ? 'В браузере · ' : 'На сервере · ') + mode + summary.join('. ') + '. Компромисс VIKOR · ' +
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
    if (offline) rows.replaceChildren();
    status.textContent = 'Пересчитываем…';
    timer = setTimeout(async () => {
      controller = new AbortController();
      const signal = controller.signal;
      const body = new URLSearchParams(new FormData(form));
      const scenario = [body.get('criterion'), Number(body.get('change')), Number(body.get('v'))];
      if (offline && form.elements.merec?.checked) scenario.push(true);
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
        if (revision === generation) {
          status.textContent = rows.childElementCount
            ? 'Пересчёт не выполнен. В таблице остался предыдущий результат.'
            : 'Пересчёт не выполнен. Измените параметр или нажмите «Сбросить».';
        }
      }
    }, 250);
  }
  function initializeSync(projectId, revision) {
    const box = document.querySelector('[data-offline-sync]');
    const key = draftPrefix + projectId;
    const save = box.querySelector('[data-save-draft]');
    const send = box.querySelector('[data-send-draft]');
    const notice = box.querySelector('[data-sync-status]');
    const link = box.querySelector('[data-sync-result]');
    const {criterion, change, v} = form.elements;
    box.hidden = false;

    if (localStorage.getItem(key)) {
      send.disabled = false;
      notice.textContent = 'Есть сохранённый черновик.';
    }

    save.addEventListener('click', () => {
      try {
        localStorage.setItem(key, JSON.stringify({
          revision, criterion: criterion.value, change: change.value, v: v.value,
        }));
        send.disabled = false;
        link.hidden = true;
        notice.textContent = 'Черновик сохранён в браузере.';
      } catch {
        notice.textContent = 'Не удалось сохранить черновик в браузере.';
      }
    });

    send.addEventListener('click', async () => {
      const stored = localStorage.getItem(key);
      if (!stored) return;
      send.disabled = true;
      save.disabled = true;
      link.hidden = true;
      notice.textContent = 'Отправляем черновик…';
      try {
        const tokenResponse = await fetch('/api/auth/csrf', {cache: 'no-store', signal: AbortSignal.timeout(10000)});
        if (!tokenResponse.ok) throw new Error('Не удалось получить доступ к серверу.');
        const {csrf_token: csrf} = await tokenResponse.json();
        const response = await fetch('/api/projects/' + projectId + '/offline/sync', {
          method: 'POST',
          body: new URLSearchParams({...JSON.parse(stored), csrf}),
          signal: AbortSignal.timeout(30000),
        });
        if (response.status === 409) {
          notice.textContent = 'Версия проекта на сервере изменилась. Черновик остался в браузере.';
          return;
        }
        if (response.status === 401) throw new Error('Войдите в систему и повторите отправку.');
        if (response.status === 403) throw new Error('Проверьте права аналитика и повторите отправку.');
        const result = await response.json();
        if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Сервер отклонил расчёт.');
        if (localStorage.getItem(key) === stored) localStorage.removeItem(key);
        link.href = '/projects/' + projectId + '/calculations/' + result.result_ids.marcos;
        link.hidden = false;
        notice.textContent = result.created ? 'Расчёт сохранён на сервере.' : 'Этот расчёт уже есть на сервере.';
      } catch (error) {
        notice.textContent = error instanceof TypeError || error.name === 'TimeoutError'
          ? 'Сервер недоступен. Черновик остался в браузере.'
          : error.message + ' Черновик остался в браузере.';
      } finally {
        save.disabled = false;
        send.disabled = !localStorage.getItem(key);
      }
    });
  }

  function initializeOfflinePage() {
    const info = document.querySelector('[data-offline-info]');
    const remove = document.querySelector('[data-delete-offline]');
    const list = document.querySelector('[data-offline-project-list]');
    const matrixBox = document.querySelector('[data-offline-project-matrix]');
    const id = new URLSearchParams(location.search).get('project');
    const projectKey = id ? projectPrefix + id : null;
    const activeKey = projectKey || storageKey;
    try {
      if (list) {
        const addLink = (label, url) => {
          const link = document.createElement('a');
          link.textContent = label;
          link.href = url;
          const item = document.createElement('p');
          item.append(link);
          list.append(item);
        };
        for (const key of Object.keys(localStorage).filter(key => key.startsWith(projectPrefix)).sort()) {
          const record = JSON.parse(localStorage.getItem(key));
          if (record?.input) addLink(record.input.project_title, '?project=' + record.input.project_id);
        }
        if (localStorage.getItem(storageKey)) addLink('Открыть копию прежнего расчёта', location.pathname);
      }
      saved = JSON.parse(localStorage.getItem(activeKey));
      if (saved && projectKey) {
        initializeSync(saved.input.project_id, saved.input.revision);
        saved.criteria = saved.input.criteria;
        saved.originalV = 0.5;
        saved.scenario = [saved.criteria[0][0], 0, 0.5];
        const table = document.createElement('table');
        table.createCaption().textContent = 'Сохранённые оценки ноутбуков';
        const headers = new Map(saved.criteria);
        const head = table.createTHead().insertRow();
        for (const name of ['Ноутбук', ...saved.input.matrix.columns.map(code => headers.get(code))]) {
          const cell = document.createElement('th');
          cell.scope = 'col';
          cell.textContent = name;
          head.append(cell);
        }
        const body = table.createTBody();
        saved.input.matrix.index.forEach((code, index) => {
          const row = body.insertRow();
          row.insertCell().textContent = code + ' · ' + saved.input.alternatives[code];
          for (const value of saved.input.matrix.data[index]) row.insertCell().textContent = format.format(value);
        });
        matrixBox.classList.add('table-responsive');
        matrixBox.append(table);
        const details = document.createElement('p');
        details.textContent = 'Бюджет: ' + (saved.input.budget == null ? 'не задан' : format.format(saved.input.budget) + ' ₽') +
          (saved.input.excluded.length ? ' · Исключены: ' + saved.input.excluded.join(', ') : '');
        matrixBox.prepend(details);
        for (const limitation of saved.input.limitations) {
          const notice = document.createElement('p');
          notice.textContent = limitation;
          matrixBox.prepend(notice);
        }
      }
      if (!saved) {
        info.textContent = 'Выберите сохранённый проект выше или сохраните его на странице проекта при запущенном сервере.';
        return false;
      }
      const {criterion, change, v} = form.elements;
      for (const [code, label] of saved.criteria) criterion.add(new Option(label, code));
      v.defaultValue = saved.originalV;
      [criterion.value, change.value, v.value] = saved.scenario;
      const label = projectKey ? 'Проект № ' + id + ' · версия ' + saved.input.revision :
        'Копия расчёта № ' + new URL(saved.path, location.origin).pathname.split('/').pop();
      info.textContent = label + ' · сохранена ' +
        new Date(saved.savedAt).toLocaleString('ru-RU') + '. Изменения ползунков действуют в этой вкладке.';
      if (saved.input.project_title) info.textContent = saved.input.project_title + ' · ' + info.textContent;
      const supportsMerec = Boolean(saved.input.sources?.['merec.py']);
      const merecOption = panel.querySelector('[data-offline-merec-option]');
      const merecHelp = panel.querySelector('[data-offline-merec-help]');
      if (merecOption && merecHelp) {
        merecOption.hidden = !supportsMerec || Boolean(projectKey);
        merecHelp.hidden = !supportsMerec || Boolean(projectKey);
        if (!supportsMerec) info.textContent += ' Сохраните копию заново, чтобы включить расчёт весов MEREC.';
      }
      remove.hidden = false;
      remove.addEventListener('click', () => {
        try {
          localStorage.removeItem(activeKey);
          matrixBox?.replaceChildren();
          if (projectKey) {
            localStorage.removeItem(draftPrefix + id);
            location.replace(location.pathname);
          }
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
