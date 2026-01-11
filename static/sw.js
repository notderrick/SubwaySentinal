// Service Worker for PWA - Network-first strategy
const CACHE_NAME = 'subwaysentinal-v2';

self.addEventListener('install', event => {
    // Force immediate activation (skip waiting)
    self.skipWaiting();
});

self.addEventListener('activate', event => {
    // Clean up old caches
    event.waitUntil(
        caches.keys().then(cacheNames => {
            return Promise.all(
                cacheNames.filter(name => name !== CACHE_NAME)
                    .map(name => caches.delete(name))
            );
        }).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', event => {
    // Network-first strategy for everything
    // Only fall back to cache if offline
    event.respondWith(
        fetch(event.request)
            .then(response => {
                // Cache successful responses for offline use
                if (response.ok && event.request.method === 'GET') {
                    const responseClone = response.clone();
                    caches.open(CACHE_NAME).then(cache => {
                        cache.put(event.request, responseClone);
                    });
                }
                return response;
            })
            .catch(() => {
                // Only use cache if network fails (offline)
                return caches.match(event.request);
            })
    );
});
