// Corral service worker — deliberately does NOT cache.
//
// Chromium requires a registered SW with a fetch handler before it will offer
// "Install app". That is the ONLY reason this file exists. It must not become
// a cache: Corral is a live control surface for running agents, and a stale
// shell would show stale panes, stale permission prompts, and a palette that
// refuses to change — a failure mode this project already spent four passes on
// from HTTP caching alone.
//
// So: pass everything straight to the network. If the network is gone, fail
// visibly rather than serving a plausible-looking snapshot of a dead session.
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => { /* network only; no respondWith */ });
