// Service worker. It never caches: Chromium needs one with a fetch handler to
// offer "Install app", and a live control surface must not serve stale snapshots.
//
// Push (webpush.py): the ping carries no payload. On one, ask the hub what needs
// you, over this origin and its cookie, and show the newest notice. A device
// that cannot reach the hub still shows something, because a push that shows
// nothing is not allowed (userVisibleOnly) and would cost the subscription.
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => { /* network only; no respondWith */ });

const FALLBACK = { title: 'Corral Light', body: 'An agent needs you.', pane: '' };

async function latestNotice() {
  try {
    const r = await fetch('/api/push/latest', { credentials: 'same-origin', cache: 'no-store' });
    if (!r.ok) return FALLBACK;
    const d = await r.json();
    const n = (d.notices || [])[0];
    return n ? { title: String(n.title || FALLBACK.title).slice(0, 120),
                 body: String(n.body || '').slice(0, 300),
                 pane: String(n.pane || '') } : FALLBACK;
  } catch { return FALLBACK; }
}

self.addEventListener('push', e => {
  e.waitUntil(latestNotice().then(n =>
    self.registration.showNotification(n.title, {
      body: n.body,
      tag: 'corral-' + (n.pane || 'hub'),     // one per pane: a newer one replaces it
      renotify: true,
      data: { pane: n.pane },
      icon: '/static/icon-192.png',
    })));
});

// Open the pane the notice is about: focus an open wall and tell it which
// pane, or open one at /?pane=<id>.
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const pane = (e.notification.data || {}).pane || '';
  const url = pane ? '/?pane=' + encodeURIComponent(pane) : '/';
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const w of wins) {
      if (new URL(w.url).origin !== self.location.origin) continue;
      await w.focus();
      if (pane) w.postMessage({ corralPane: pane });
      return;
    }
    await self.clients.openWindow(url);
  })());
});
