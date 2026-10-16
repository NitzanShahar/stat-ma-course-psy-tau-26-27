/* Anonymous course statistics: page views, active time, clicks, and how much of each video/audio was watched.
   Sends rows to the Supabase table "events" (insert only: the site cannot read anything back).
   Fire-and-forget: if anything fails, nothing happens and the page works as usual.
   Not counted: the staff version (it marks its tab in sessionStorage), local copies (file:), and any browser
   that opened a page with ?notrack once (undo with ?track). For a test from another host: ?tracktest. */
(function () {
  try {
    var me = document.currentScript, cfg = me.dataset;
    var root = me.src.replace(/assets\/track\.js.*$/, '');      // the site's root, from this script's own address
    var q = location.search + location.hash;
    var ls = {}, ss = {};
    try { ls = localStorage; ss = sessionStorage; } catch (e) {}

    if (/[?&#]notrack\b/.test(q)) { ls.cv_notrack = '1'; alert('הדפדפן הזה לא ייספר בסטטיסטיקה של האתר.'); }
    if (/[?&#]track\b/.test(q)) { delete ls.cv_notrack; delete ls.cv_test; alert('הדפדפן הזה ייספר שוב.'); }
    if (/[?&#]tracktest\b/.test(q)) ls.cv_test = '1';
    if (cfg.ctx === 'site') delete ss.cv_staff;                  // a public page: this tab is not in the staff version
    var test = ls.cv_test === '1';
    if (ls.cv_notrack === '1' || ss.cv_staff === '1') return;
    if (!test && !/\.github\.io$/.test(location.hostname)) return;

    function rid() { return (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2) + Date.now().toString(36)).replace(/-/g, ''); }
    var visitor = ls.cv_id || (ls.cv_id = rid());
    var session = ss.cv_sid || (ss.cv_sid = rid());
    if (test) visitor = 'test-' + visitor;
    var coarse = matchMedia('(pointer: coarse)').matches;
    var device = !coarse ? 'desktop' : Math.min(screen.width, screen.height) < 600 ? 'mobile' : 'tablet';
    var page = decodeURIComponent(location.href.split(/[?#]/)[0].replace(root, '')) || 'index.html';
    var params = new URLSearchParams(location.search);
    var src = page === 'play.html' ? params.get('src') || '' : '';
    var wm = /week-(\d+)/.exec(page) || /W(\d+)_/.exec(page + ' ' + src);
    var week = cfg.week ? +cfg.week : wm ? +wm[1] : null;

    function cut(s, n) { s = String(s || '').replace(/\s+/g, ' ').trim(); return s ? s.slice(0, n) : null; }
    function send(ev) {
      ev.visitor = visitor; ev.session = session; ev.page = cut(page, 300); ev.device = device; ev.week = week;
      try {
        fetch(cfg.url + '/rest/v1/events', {
          method: 'POST', keepalive: true,
          headers: { apikey: cfg.key, 'Content-Type': 'application/json', Prefer: 'return=minimal' },
          body: JSON.stringify(ev)
        }).catch(function () {});
      } catch (e) {}
    }

    // 1. the visit itself
    send({ type: 'view', label: cut(src ? params.get('t') : document.title, 300), target: cut(src, 500) });

    // 2. active time: counted only while the page is visible and the reader did something in the last 5 minutes
    //    (or a video/audio is playing). Sent when the page is hidden or closed, and every 5 minutes.
    var IDLE = 5 * 60 * 1000, lastInput = Date.now(), active = 0;
    ['mousemove', 'mousedown', 'keydown', 'scroll', 'wheel', 'touchstart'].forEach(function (t) {
      addEventListener(t, function () { lastInput = Date.now(); }, { passive: true, capture: true });
    });
    function media() { return document.querySelector('video, audio'); }
    function playing() { var m = media(); return m && !m.paused && !m.ended; }
    setInterval(function () {
      if (document.visibilityState === 'visible' && (Date.now() - lastInput < IDLE || playing())) active += 5;
      if (active >= 300) flushTime();
    }, 5000);
    function flushTime() { if (active > 0) { send({ type: 'time', seconds: active, target: cut(src, 500) }); active = 0; } }

    // 3. clicks on links in the site's own pages (materials, extra media, other weeks)
    if (cfg.ctx === 'site') {
      document.addEventListener('click', function (e) {
        var a = e.target.closest && e.target.closest('a[href]');
        if (!a) return;
        var h = a.href, m = /play\.html\?src=([^&]+)/.exec(h);
        var target = m ? decodeURIComponent(m[1]) : h.indexOf(root) === 0 ? decodeURIComponent(h.slice(root.length)) : h;
        send({ type: 'click', target: cut(target, 500), label: cut(a.getAttribute('aria-label') || a.textContent, 300) });
      }, true);
    }

    // 4. the player: which seconds of the file were played, remembered across visits in this browser
    var watched = '', sentCount = -1, mkey = 'cv_v:' + src;
    if (src) {
      watched = ls[mkey] || '';
      setInterval(function () {
        var m = media();
        if (!m || m.paused || m.ended || !m.duration) return;
        var s = Math.floor(m.currentTime);
        if (watched.charAt(s) !== '1') {
          while (watched.length < s) watched += '0';
          watched = watched.slice(0, s) + '1' + watched.slice(s + 1);
          ls[mkey] = watched;
        }
      }, 500);
      addEventListener('pause', flushVideo, true);
      addEventListener('ended', flushVideo, true);
    }
    function flushVideo() {
      var m = media(), n = (watched.match(/1/g) || []).length;
      if (!src || !m || !m.duration || n === sentCount) return;
      sentCount = n;
      send({ type: 'video', target: cut(src, 500), label: cut(params.get('t'), 300), seconds: n,
             percent: Math.min(100, Math.round(100 * n / Math.ceil(m.duration))) });
    }

    function leave() { flushTime(); flushVideo(); }
    document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'hidden') leave(); });
    addEventListener('pagehide', leave);
  } catch (e) {}
})();
