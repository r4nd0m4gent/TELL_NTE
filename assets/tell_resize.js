// Report this page's content height to the host page (deploy/tell_embed.html),
// which resizes its iframe to fit, so nothing is cut off at any screen width.
// Dash loads every file in assets/, so this runs for the dashboard and the
// contribution form alike. Does nothing when the page isn't embedded.
(function () {
    if (window.parent === window) return;
    var last = 0;
    function post() {
        var h = Math.ceil(document.body.getBoundingClientRect().height);
        if (h > 0 && h !== last) {
            last = h;
            window.parent.postMessage({ tellHeight: h }, '*');
        }
    }
    function start() {
        new ResizeObserver(post).observe(document.body);
        post();
    }
    if (document.body) { start(); } else { document.addEventListener('DOMContentLoaded', start); }
})();
