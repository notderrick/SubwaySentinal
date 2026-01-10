// Service Worker for PWA
const CACHE_NAME = 'subwaysentinal-v1';
const urlsToCache = [
    '/',
    '/index.html'
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => cache.addAll(urlsToCache))
    );
});

self.addEventListener('fetch', event => {
    // Network-first strategy for API calls
    if (event.request.url.includes('realtimerail.nyc')) {
        event.respondWith(
            fetch(event.request).catch(() => caches.match(event.request))
        );
        return;
    }

    // Cache-first for static assets
    event.respondWith(
        caches.match(event.request).then(response => response || fetch(event.request))
    );
});
