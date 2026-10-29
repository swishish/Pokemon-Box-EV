/* Snapshot-date badge. Reads dates the pages already store.
   Never invents a timestamp: a missing or unreadable date stays a fallback. */
(function (root, factory) {
  var api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.FreshBadge = api;
})(typeof window !== 'undefined' ? window : null, function () {
  var HOME_SOURCES = [
    { url: './data.json', field: 'as_of', label: '開盒', staleAfter: 8, cmp: 'gte' },
    { url: './data/market-jp-psa10-bench/latest.json', field: 'asOfDate', label: 'JP 基準', staleAfter: 8, cmp: 'gt' },
    { url: './data/chase-fit/latest.json', field: 'data_date', label: 'Chase', staleAfter: 7, cmp: 'gt' }
  ];

  function ymdFromRaw(raw) {
    if (raw == null || raw === '') return null;
    var m = String(raw).trim().match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!m) return null;
    var y = Number(m[1]);
    var mo = Number(m[2]);
    var d = Number(m[3]);
    var dt = new Date(y, mo - 1, d);
    if (dt.getFullYear() !== y || dt.getMonth() !== mo - 1 || dt.getDate() !== d) return null;
    return m[1] + '-' + m[2] + '-' + m[3];
  }

  function ageDays(ymd, today) {
    var m = String(ymd || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return null;
    var now = today || new Date();
    var a = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    var b = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
    return Math.floor((b - a) / 86400000);
  }

  function isStale(age, staleAfter, cmp) {
    if (staleAfter == null || age == null || age < 0) return false;
    if (cmp === 'gte') return age >= staleAfter;
    return age > staleAfter;
  }

  function pickField(prefix, field) {
    if (!prefix || !field) return null;
    var escaped = String(field).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    var re = new RegExp('"' + escaped + '"\\s*:\\s*"([^"]*)"');
    var m = String(prefix).match(re);
    return m ? m[1] : null;
  }

  function describe(opts) {
    opts = opts || {};
    var label = opts.label || '快照';
    var ymd = ymdFromRaw(opts.raw);
    if (!ymd) {
      return { state: 'missing', text: label + ' · 沒有記錄快照日期' };
    }
    var age = ageDays(ymd, opts.today);
    var stale = isStale(age, opts.staleAfter, opts.cmp);
    if (opts.compact) {
      if (stale) return { state: 'stale', text: label + ' ' + ymd + ' · 已過 ' + age + ' 日' };
      return { state: 'fresh', text: label + ' ' + ymd };
    }
    if (stale) return { state: 'stale', text: label + ' ' + ymd + ' · 已過 ' + age + ' 日 · 非即時' };
    return { state: 'fresh', text: label + ' ' + ymd + ' · 非即時' };
  }

  function fillBody(body, parts) {
    while (body.firstChild) body.removeChild(body.firstChild);
    parts.forEach(function (part, i) {
      if (i) {
        var sep = document.createElement('span');
        sep.className = 'fresh-sep';
        sep.setAttribute('aria-hidden', 'true');
        sep.textContent = '·';
        body.appendChild(sep);
      }
      var span = document.createElement('span');
      span.className = 'fresh-item is-' + part.state;
      span.textContent = part.text;
      body.appendChild(span);
    });
  }

  function paint(el, parts, opts) {
    if (!el) return;
    opts = opts || {};
    var kicker = el.querySelector('.fresh-kicker');
    var body = el.querySelector('.fresh-body');
    if (!body) return;
    var allMissing = parts.every(function (p) { return p.state === 'missing'; });
    var anyStale = parts.some(function (p) { return p.state === 'stale'; });
    var state = allMissing ? 'missing' : (anyStale ? 'stale' : 'fresh');
    el.classList.remove('is-fresh', 'is-stale', 'is-missing');
    el.classList.add('is-' + state);
    if (kicker) {
      kicker.textContent = (opts.compact && !allMissing) ? '靜態快照 · 非即時' : '靜態快照';
    }
    fillBody(body, parts);
    var title = allMissing
      ? '此頁沒有可用的快照日期，不會顯示估計日期。'
      : '已保存的快照日期，不是即時行情。';
    el.title = title;
    el.setAttribute('aria-label', title + ' ' + parts.map(function (p) { return p.text; }).join('，'));
  }

  function show(opts) {
    opts = opts || {};
    var el = opts.el || (typeof document !== 'undefined' ? document.getElementById('freshBadge') : null);
    paint(el, [describe(opts)], { compact: false });
  }

  function readPrefix(url, maxBytes) {
    var limit = maxBytes || 2048;
    return fetch(url, { cache: 'no-store' }).then(function (res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      if (!res.body || typeof res.body.getReader !== 'function') {
        return res.text().then(function (text) { return text.slice(0, limit); });
      }
      var reader = res.body.getReader();
      var dec = new TextDecoder('utf-8');
      var buf = '';
      function pump() {
        return reader.read().then(function (step) {
          if (step.value) buf += dec.decode(step.value, { stream: !step.done });
          if (step.done || buf.length >= limit) return buf.slice(0, limit);
          return pump();
        });
      }
      return pump().then(function (text) {
        try { reader.cancel(); } catch (e) { /* already closed */ }
        return text;
      });
    });
  }

  function mountHome(el) {
    var node = el || (typeof document !== 'undefined' ? document.getElementById('freshBadge') : null);
    if (!node || typeof fetch !== 'function') return Promise.resolve();
    return Promise.all(HOME_SOURCES.map(function (src) {
      return readPrefix(src.url, 2048).then(function (prefix) {
        return describe({
          label: src.label,
          raw: pickField(prefix, src.field),
          staleAfter: src.staleAfter,
          cmp: src.cmp,
          compact: true
        });
      }).catch(function () {
        return describe({ label: src.label, raw: null, compact: true });
      });
    })).then(function (parts) {
      paint(node, parts, { compact: true });
    });
  }

  function selfTest() {
    function assert(cond, msg) {
      if (!cond) throw new Error(msg);
    }
    assert(ymdFromRaw('2026-10-28 15:18 Asia/Shanghai') === '2026-10-28', 'as_of prefix');
    assert(ymdFromRaw('2026-10-28') === '2026-10-28', 'plain ymd');
    assert(ymdFromRaw('') === null, 'empty');
    assert(ymdFromRaw('not-a-date') === null, 'garbage');
    assert(ymdFromRaw('2026-02-31') === null, 'impossible day');
    assert(pickField('{\n  "as_of": "2026-10-28 15:18 Asia/Shanghai",\n', 'as_of') === '2026-10-28 15:18 Asia/Shanghai', 'pick as_of');
    assert(pickField('{"generated":"x"}', 'asOfDate') === null, 'missing field');
    var blob = '"as_of": "2026-10-28 15:18 Asia/Shanghai",' + new Array(3000).join('x') + '"as_of": "1999-01-01"';
    assert(pickField(blob.slice(0, 2048), 'as_of').indexOf('2026-10-28') === 0, 'first date only');
    var today = new Date(2026, 9, 29);
    var fresh = describe({ label: '開盒快照', raw: '2026-10-28 15:18 Asia/Shanghai', staleAfter: 8, cmp: 'gte', today: today });
    assert(fresh.state === 'fresh' && fresh.text.indexOf('2026-10-28') >= 0 && fresh.text.indexOf('非即時') >= 0, 'fresh calc ' + fresh.text);
    var edge = describe({ label: '開盒快照', raw: '2026-10-21', staleAfter: 8, cmp: 'gte', today: today });
    assert(edge.state === 'stale' && edge.text.indexOf('8') >= 0, 'gte 8 ' + edge.text);
    var notYet = describe({ label: 'SNK PSA10 快照', raw: '2026-10-21', staleAfter: 8, cmp: 'gt', today: today });
    assert(notYet.state === 'fresh', 'gt 8 still fresh at 8');
    var chase = describe({ label: 'Chase', raw: '2026-10-16', staleAfter: 7, cmp: 'gt', today: today, compact: true });
    assert(chase.state === 'stale' && chase.text.indexOf('13') >= 0 && chase.text.indexOf('非即時') < 0, 'compact stale ' + chase.text);
    var miss = describe({ label: '開盒快照', raw: '', staleAfter: 8, cmp: 'gte' });
    assert(miss.state === 'missing' && miss.text.indexOf('沒有記錄') >= 0 && miss.text.indexOf('2026') < 0, 'no invented date');
    return 'ok';
  }

  return {
    ymdFromRaw: ymdFromRaw,
    ageDays: ageDays,
    pickField: pickField,
    describe: describe,
    show: show,
    mountHome: mountHome,
    readPrefix: readPrefix,
    selfTest: selfTest
  };
});
