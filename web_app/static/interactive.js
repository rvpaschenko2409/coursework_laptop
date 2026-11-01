/* Local presentation only: no requests, model calculations, credentials or stored project data. */
(() => {
  'use strict';
  const key = 'coursework-theme-v1';
  const root = document.documentElement;
  const validTheme = value => value === 'light' || value === 'dark';
  function applyTheme(theme) {
    root.dataset.bsTheme = validTheme(theme) ? theme : 'dark';
    const light = root.dataset.bsTheme === 'light';
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = light ? '#f4f6fc' : '#0c1220';
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.setAttribute('aria-pressed', String(light));
      button.title = light ? 'Светлая тема включена. Нажмите для тёмной.' : 'Включить светлую тему';
    });
  }
  let saved = 'dark';
  try { const value = localStorage.getItem(key); if (validTheme(value)) saved = value; } catch (_) {}
  applyTheme(saved);
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) applyTheme(validTheme(event.newValue) ? event.newValue : 'dark');
  });
  const normalize = value => String(value).normalize('NFC').toLocaleLowerCase('ru-RU').replace(/ё/g, 'е').trim();

  function filters(form) {
    const items = [...document.querySelectorAll('[data-laptop-id]')];
    const cards = items.filter(item => item.tagName === 'ARTICLE');
    const search = form.querySelector('[name=search]');
    const price = form.querySelector('[name=max_price]');
    const memory = form.querySelector('[name=min_ram]');
    const storage = form.querySelector('[name=storage]');
    const status = form.querySelector('[data-filter-status]');
    const empty = document.querySelector('[data-filter-empty]');
    function update() {
      if (!price.checkValidity()) {
        status.textContent = 'Максимальная цена: введите число от 1 до 1 000 000 000 без дробной части.';
        price.setAttribute('aria-invalid', 'true');
        return;
      }
      price.removeAttribute('aria-invalid');
      const words = normalize(search.value).split(/\s+/).filter(Boolean);
      const maximum = price.value === '' ? null : price.valueAsNumber;
      const minimum = memory.value === '' ? null : Number(memory.value);
      const medium = storage.value;
      const shown = new Set();
      for (const item of items) {
        const rawPrice = item.dataset.price;
        const rawMemory = item.dataset.ram;
        const matches = words.every(word => normalize(item.dataset.search).includes(word)) &&
          (maximum === null || rawPrice !== '' && Number(rawPrice) <= maximum) &&
          (minimum === null || rawMemory !== '' && Number(rawMemory) >= minimum) &&
          (medium === '' || item.dataset.storage === medium);
        item.hidden = !matches;
        if (matches) shown.add(item.dataset.laptopId);
      }
      status.textContent = `Показано ноутбуков: ${shown.size} из ${cards.length}.`;
      if (empty) empty.hidden = shown.size !== 0;
    }
    form.addEventListener('submit', event => event.preventDefault());
    form.addEventListener('input', update);
    form.addEventListener('change', update);
    form.querySelector('[data-filter-reset]').addEventListener('click', () => { form.reset(); update(); search.focus(); });
    form.hidden = false;
    update();
  }

  function bars(host) {
    const data = JSON.parse(host.dataset.points);
    const maximum = Number(host.dataset.maximum);
    if (!Array.isArray(data) || !data.length || !Number.isFinite(maximum) || maximum <= 0 ||
        data.some(p => typeof p.label !== 'string' || typeof p.series !== 'string' || !Number.isFinite(p.value) || p.value < 0 || p.value > maximum)) return;
    const series = [...new Set(data.map(p => p.series))];
    const controls = document.createElement('div');controls.className = 'chart-series';controls.setAttribute('role', 'group');controls.setAttribute('aria-label', 'Ряды диаграммы');
    if (series.length === 1) controls.hidden = true;
    const note = document.createElement('p');note.className = 'chart-tooltip';note.setAttribute('role', 'status');note.setAttribute('aria-live', 'polite');
    const instruction = 'Наведите курсор, коснитесь полосы или выберите её клавишей Tab, чтобы увидеть подробности.';
    note.textContent = instruction;
    const list = document.createElement('div');list.className = 'live-bars';
    const groupMap = new Map();const buttons = [];
    const number = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 6});
    data.forEach(point => {
      let group = groupMap.get(point.label);
      if (!group) {
        group = document.createElement('section');group.className = 'live-bar-group';
        const title = document.createElement('p');title.className = 'live-bar-label';title.textContent = point.label;
        group.append(title);groupMap.set(point.label, group);list.append(group);
      }
      const button = document.createElement('button');button.type = 'button';button.className = 'live-bar-point';
      button.dataset.series = point.series;
      button.setAttribute('aria-label', `${point.label}. ${point.series}: ${number.format(point.value)}. ${point.detail || ''}`);
      const caption = document.createElement('span');caption.className = 'live-bar-caption';caption.textContent = point.series;
      const meter = document.createElement('meter');meter.min = 0;meter.max = maximum;meter.value = point.value;
      meter.setAttribute('aria-hidden', 'true');meter.className = series.indexOf(point.series) % 2 ? 'series-two' : 'series-one';
      const value = document.createElement('span');value.className = 'live-bar-value';value.textContent = number.format(point.value);
      button.append(caption, meter, value);group.append(button);buttons.push(button);
      const show = () => { note.textContent = `${point.label}. ${point.series}: ${String(point.value)}. ${point.detail || ''}`; };
      button.addEventListener('pointerenter', show);button.addEventListener('focus', show);button.addEventListener('click', show);
      button.addEventListener('keydown', event => {
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
        const visible = buttons.filter(b => !b.hidden);const position = visible.indexOf(button);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? visible.length - 1 : (position + (event.key === 'ArrowDown' ? 1 : -1) + visible.length) % visible.length;
        event.preventDefault();visible[next].focus();
      });
    });
    if (series.length > 1) {
      series.forEach(name => {
        const toggle = document.createElement('button');toggle.type = 'button';toggle.className = 'btn btn-outline-secondary';
        toggle.textContent = name;toggle.setAttribute('aria-pressed', 'true');
        toggle.addEventListener('click', () => {
          const active = [...controls.querySelectorAll('button[aria-pressed=true]')];
          if (toggle.getAttribute('aria-pressed') === 'true' && active.length === 1) return;
          const enabled = toggle.getAttribute('aria-pressed') !== 'true';toggle.setAttribute('aria-pressed', String(enabled));
          buttons.filter(b => b.dataset.series === name).forEach(b => { b.hidden = !enabled; });
          note.textContent = `${name}: ${enabled ? 'показан' : 'скрыт'}. Скрытие ряда не меняет расчёт.`;
        });controls.append(toggle);
      });
    }
    host.append(controls, note, list);host.hidden = false;
    const original = host.nextElementSibling;
    if (original && original.classList.contains('chart-link')) {
      const details = document.createElement('details');details.className = 'chart-original';
      const summary = document.createElement('summary');summary.textContent = 'Исходное изображение диаграммы';
      original.before(details);details.append(summary, original);
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.hidden = false;
      button.addEventListener('click', () => {
        const theme = root.dataset.bsTheme === 'dark' ? 'light' : 'dark';applyTheme(theme);
        try { localStorage.setItem(key, theme); } catch (_) {}
      });
    });applyTheme(root.dataset.bsTheme);
    document.querySelectorAll('[data-laptop-filters]').forEach(form => { try { filters(form); } catch (_) { form.hidden = true; } });
    document.querySelectorAll('[data-interactive-bars]').forEach(host => { try { bars(host); } catch (_) { host.hidden = true; } });
  });
})();
