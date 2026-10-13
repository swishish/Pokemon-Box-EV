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

  function empty(reason, from, to) {
    return {
      available: false,
      baselineDate: null,
      endDate: null,
      neededBy: from || null,
      label: '',
      reason: reason,
      gainers: [],
      losers: []
    };
  }

  function compute(constituents, points, from, to, topN) {
    topN = topN || TOP;
    if (!from || !to) return empty('請輸入起迄日期', from, to);
    if (from > to) return empty('起始日不能晚於結束日', from, to);
    var windowPts = (points || []).filter(function (p) {
      return p && p.date && p.date >= from && p.date <= to;
    });
    if (windowPts.length < 2) {
      return empty('此區間少於兩個快照（缺日不補價）', from, to);
    }
    var baseline = windowPts[0];
    var end = windowPts[windowPts.length - 1];
    var fromPrices = baseline.prices || {};
    var toPrices = end.prices || {};
    var movable = [];
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
    var gainers = movable.filter(function (c) { return c.changePct > 0; }).slice(0, topN);
    var losers = movable.filter(function (c) { return c.changePct < 0; }).slice(-topN).reverse();
    return {
      available: true,
      baselineDate: baseline.date,
      endDate: end.date,
      neededBy: from,
      label: baseline.date + ' → ' + end.date,
      reason: null,
      gainers: gainers,
      losers: losers
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
    load: load
  };
})(window);
