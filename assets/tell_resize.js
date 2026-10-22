// When a page embeds the dashboard in an iframe, report the dashboard's content
// height so the host page can resize its iframe to fit.
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
