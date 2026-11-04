const cacheName = 'coursework-offline-calculation-v1';
const assets = new Set([
  '/static/offline_calculation.html',
  '/static/offline_calculation.css',
  '/static/offline_calculation.webmanifest',
  '/static/offline_icon_192.png',
  '/static/offline_icon_512.png',
  '/static/live_preview.js',
  '/static/browser_preview_worker.js',
  '/static/pyodide/numpy-2.0.2-cp312-cp312-pyodide_2024_0_wasm32.whl',
  '/static/pyodide/pandas-2.2.3-cp312-cp312-pyodide_2024_0_wasm32.whl',
  '/static/pyodide/pyodide-lock.json',
  '/static/pyodide/pyodide.asm.js',
  '/static/pyodide/pyodide.asm.wasm',
  '/static/pyodide/pyodide.js',
  '/static/pyodide/python_dateutil-2.9.0.post0-py2.py3-none-any.whl',
  '/static/pyodide/python_stdlib.zip',
  '/static/pyodide/pytz-2024.1-py2.py3-none-any.whl',
  '/static/pyodide/six-1.16.0-py2.py3-none-any.whl',
]);

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(cacheName);
    await cache.addAll([...assets].map(url => new Request(url, {cache: 'reload'})));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) {
      if (key.startsWith('coursework-offline-calculation-') && key !== cacheName) {
        await caches.delete(key);
      }
    }
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin ||
      url.search || !assets.has(url.pathname)) return;
  event.respondWith((async () => {
    const cache = await caches.open(cacheName);
    return await cache.match(event.request) || fetch(event.request);
  })());
});
