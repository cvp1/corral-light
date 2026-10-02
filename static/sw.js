// Service worker that never caches: Chromium needs one with a fetch handler to offer
// "Install app", and a live control surface must not serve stale snapshots.
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => { /* network only; no respondWith */ });
