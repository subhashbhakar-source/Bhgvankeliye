// Offline cache. VERSION changes with every build, so phones fetch the new page.
// The other machinery file lives in its own cache (KEEP), which survives updates: that file is
// only downloaded again when its data changes.
const VERSION = 'xdf-parts-dccd492618';
const OTHER = 'other-6020e1d1d966.dat';        // the other machinery data file ('' when that option is off)
const KEEP = 'xdf-other';
const FILES = ['./', './index.html', './manifest.webmanifest', './icon-180.png', './icon-192.png', './icon-512.png'];

// An update that changes the other machinery data must bring the new file with it: a phone that
// already holds an older copy gets the new one first, and the update only completes when it is
// stored. So the new page never starts without its data, and the old copy is removed only after
// that (in activate). A phone that holds no copy yet (a first install) is left to the page, which
// fetches the file itself; fetching it here as well would download it twice.
async function storeOther(){
  if (!OTHER) return;
  const c = await caches.open(KEEP);
  if (await c.match(OTHER)) return;
  if (!(await c.keys()).length) return;
  const resp = await fetch(new Request(OTHER, {cache: 'reload'}));
  if (!resp.ok) throw new Error('HTTP ' + resp.status);
  await c.put(OTHER, resp);
}
self.addEventListener('install', e => {
  // cache: 'reload' skips the browser's HTTP cache, so an update never stores a stale copy of the page
  e.waitUntil(storeOther()
    .then(() => caches.open(VERSION))
    .then(c => c.addAll(FILES.map(f => new Request(f, {cache: 'reload'}))))
    .then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== VERSION && k !== KEEP).map(k => caches.delete(k))))
    .then(async () => {
      if (!OTHER) return caches.delete(KEEP);
      const c = await caches.open(KEEP);
      if (!(await c.match(OTHER))) return;          // keep what is there until the new file has arrived
      for (const k of await c.keys()) if (!k.url.endsWith('/' + OTHER)) await c.delete(k);
    })
    .catch(() => {})
    .then(() => self.clients.claim()));
});
const CORE = FILES.map(f => new URL(f, self.location).href);
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(caches.match(e.request, {ignoreSearch: true}).then(hit => {
    if (hit) return hit;
    return fetch(e.request).then(resp => {
      // a core file missing from the store (the phone cleared it): put it back for offline use
      const url = e.request.url.split('?')[0];
      if (resp.ok && CORE.indexOf(url) >= 0){
        const copy = resp.clone();
        e.waitUntil(caches.open(VERSION).then(c => c.put(url, copy)).catch(() => {}));
      }
      return resp;
    });
  }));
});
