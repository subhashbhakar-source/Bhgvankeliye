// Offline cache. VERSION changes with every build, so phones fetch the new page.
// The page itself keeps the other machinery file in its own cache (KEEP), which
// survives updates: that file is only downloaded again when its data changes.
const VERSION = 'xdf-parts-a5502eeddd';
const KEEP = ['xdf-other'];
const FILES = ['./', './index.html', './manifest.webmanifest', './icon-180.png', './icon-192.png', './icon-512.png'];
self.addEventListener('install', e => {
  // cache: 'reload' skips the browser's HTTP cache, so an update never stores a stale copy of the page
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(FILES.map(f => new Request(f, {cache: 'reload'})))).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== VERSION && KEEP.indexOf(k) < 0).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(caches.match(e.request, {ignoreSearch: true}).then(hit => hit || fetch(e.request)));
});
