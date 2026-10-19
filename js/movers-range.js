/* Custom from–to movers: pairwise % from stored price maps. No interpolation, no index rebase. */
(function (global) {
  'use strict';
  var cache = {};
  var TOP = 10;

  function pointsOf(hist, indexId) {
    if (!hist) return [];
    if (indexId && hist.indexes && hist.indexes[indexId]) {
      return hist.indexes[indexId].points || [];
    }
    return hist.points || [];
  }

  function emptyBreadth() {
    return { available: false, advancing: 0, declining: 0, unchanged: 0 };
  }

  function breadthFromPrices(constituents, fromPrices, toPrices) {
    var advancing = 0;
    var declining = 0;
    var unchanged = 0;
    fromPrices = fromPrices || {};
    toPrices = toPrices || {};
    (constituents || []).forEach(function (row) {
      if (!row || row.carried) return;
      var id = String(row.id || '');
      var prior = fromPrices[id];
      var price = toPrices[id];
      if (!id || price == null || !prior || !(Number(prior) > 0) || !(Number(price) > 0)) return;
      var changePct = Math.round((Number(price) / Number(prior) - 1) * 10000) / 100;
      if (changePct > 0) advancing += 1;
      else if (changePct < 0) declining += 1;
      else unchanged += 1;
    });
    var n = advancing + declining + unchanged;
    return {
      available: n > 0,
      advancing: advancing,
      declining: declining,
      unchanged: unchanged
    };
  }

  function empty(reason, from, to) {
    return {
      available: false,
      baselineDate: null,
      endDate: null,
      neededBy: from || null,
      label: '',
      reason: reason,
      gainers: [],
      losers: [],
      breadth: emptyBreadth()
    };
  }

  function windowSnaps(points, from, to) {
    if (!from || !to) return { ok: false, reason: '請輸入起迄日期', from: from || null, to: to || null };
    if (from > to) return { ok: false, reason: '起始日不能晚於結束日', from: from, to: to };
    var windowPts = (points || []).filter(function (p) {
      return p && p.date && p.date >= from && p.date <= to;
    });
    if (windowPts.length < 2) {
      return { ok: false, reason: '此區間少於兩個快照（缺日不補價）', from: from, to: to };
    }
    return {
      ok: true,
      baseline: windowPts[0],
      end: windowPts[windowPts.length - 1],
      fromPrices: windowPts[0].prices || {},
      toPrices: windowPts[windowPts.length - 1].prices || {}
    };
  }

  function pairwiseMoves(constituents, fromPrices, toPrices) {
    var movable = [];
    fromPrices = fromPrices || {};
    toPrices = toPrices || {};
    (constituents || []).forEach(function (row) {
      if (!row || row.carried) return;
      var id = String(row.id || '');
      var prior = fromPrices[id];
      var price = toPrices[id];
      if (!id || price == null || !prior || !(Number(prior) > 0) || !(Number(price) > 0)) return;
      var changePct = Math.round((Number(price) / Number(prior) - 1) * 10000) / 100;
      movable.push({
        id: id,
        name: row.name,
        number: row.number,
        image: row.image,
        setName: row.setName,
        price: Math.round(Number(price) * 100) / 100,
        prevPrice: Math.round(Number(prior) * 100) / 100,
        changePct: changePct
      });
    });
    movable.sort(function (a, b) { return b.changePct - a.changePct; });
    return movable;
  }

  function compute(constituents, points, from, to, topN) {
    topN = topN || TOP;
    var win = windowSnaps(points, from, to);
    if (!win.ok) return empty(win.reason, from, to);
    var movable = pairwiseMoves(constituents, win.fromPrices, win.toPrices);
    var gainers = movable.filter(function (c) { return c.changePct > 0; }).slice(0, topN);
    var losers = movable.filter(function (c) { return c.changePct < 0; }).slice(-topN).reverse();
    return {
      available: true,
      baselineDate: win.baseline.date,
      endDate: win.end.date,
      neededBy: from,
      label: win.baseline.date + ' → ' + win.end.date,
      reason: null,
      gainers: gainers,
      losers: losers,
      breadth: breadthFromPrices(constituents, win.fromPrices, win.toPrices)
    };
  }

  /* Card % minus the same basket's index % over the same from–to window.
     Negative gap = lagged. Missing prices skipped; no interpolation. */
  function lagVsIndex(constituents, points, from, to, indexPct) {
    var win = windowSnaps(points, from, to);
    if (indexPct == null || Number.isNaN(Number(indexPct))) {
      return {
        available: false,
        reason: '尚無指數報酬',
        baselineDate: win.ok ? win.baseline.date : null,
        endDate: win.ok ? win.end.date : null,
        neededBy: from || null,
        indexPct: null,
        rows: []
      };
    }
    if (!win.ok) {
      return {
        available: false,
        reason: win.reason,
        baselineDate: null,
        endDate: null,
        neededBy: from || null,
        indexPct: Number(indexPct),
        rows: []
      };
    }
    var idx = Number(indexPct);
    var rows = pairwiseMoves(constituents, win.fromPrices, win.toPrices).map(function (c) {
      var gap = Math.round((c.changePct - idx) * 100) / 100;
      return {
        id: c.id,
        name: c.name,
        number: c.number,
        image: c.image,
        setName: c.setName,
        price: c.price,
        prevPrice: c.prevPrice,
        changePct: c.changePct,
        gap: gap
      };
    }).filter(function (c) { return c.gap < 0; });
    rows.sort(function (a, b) { return a.gap - b.gap; });
    return {
      available: true,
      baselineDate: win.baseline.date,
      endDate: win.end.date,
      neededBy: from,
      label: win.baseline.date + ' → ' + win.end.date,
      reason: null,
      indexPct: idx,
      rows: rows
    };
  }

  function load(url) {
    if (!cache[url]) {
      cache[url] = fetch(url, { cache: 'no-store' }).then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      });
    }
    return cache[url];
  }

  global.MoversRange = {
    pointsOf: pointsOf,
    compute: compute,
    lagVsIndex: lagVsIndex,
    load: load,
    breadthFromPrices: breadthFromPrices
  };
})(window);
