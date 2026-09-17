// Language Teammate service worker (v1) — makes the page installable and
// shows a friendly offline page instead of a blank screen. Everything real
// (voice, photos, translations) needs the internet, so we do NOT cache the
// app itself: the page always loads fresh so updates land instantly.
const OFFLINE_HTML = `<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Language Teammate — offline</title>
<style>body{margin:0;font-family:system-ui,-apple-system,Arial,sans-serif;background:#0f1523;color:#e6ebf5;
display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;padding:24px;box-sizing:border-box}
h1{font-size:22px;margin:0 0 8px}p{color:#aab4c8;line-height:1.5}button{margin-top:16px;background:#1a73e8;color:#fff;
border:none;border-radius:8px;padding:12px 20px;font-size:16px;font-weight:600}</style></head>
<body><div><div style="font-size:44px">📡</div><h1>No internet signal</h1>
<p>Language Teammate needs a connection to translate and teach.<br>Try again when you have a signal.</p>
<button onclick="location.reload()">Try again</button></div></body></html>`;

self.addEventListener('install', function(e) { self.skipWaiting(); });
self.addEventListener('activate', function(e) { e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', function(e) {
  if (e.request.mode !== 'navigate') return;   // only page loads get the offline fallback
  e.respondWith(fetch(e.request).catch(function() {
    return new Response(OFFLINE_HTML, { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
  }));
});
