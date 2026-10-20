/* Shared JP PSA10 market UI: English labels, overlay defaults, tenor breadth. */
(function (global) {
  'use strict';

  var DEFAULT_PERIOD = '1Y';

  var CJK = /[\u3040-\u30ff\u3400-\u9fff]/g;
  var LATIN = /[A-Za-z]/g;

  function looksLatinName(s) {
    if (s == null || s === '') return false;
    var str = String(s);
    var latin = (str.match(LATIN) || []).length;
    var cjk = (str.match(CJK) || []).length;
    return latin >= 2 && latin > cjk;
  }

  function stripTrailingParens(s) {
    var t = String(s || '').trim();
    while (t.charAt(t.length - 1) === ')' || t.charAt(t.length - 1) === '）') {
      var close = t.charAt(t.length - 1);
      var open = close === ')' ? '(' : '（';
      var depth = 0;
      var i = t.length - 1;
      for (; i >= 0; i--) {
        var ch = t.charAt(i);
        if (ch === close) depth += 1;
        else if (ch === open) {
          depth -= 1;
          if (depth === 0) break;
        }
      }
      if (i <= 0) break;
      t = t.slice(0, i).replace(/\s+$/, '');
    }
    return t;
  }

  function stripDecor(s) {
    var t = String(s || '').replace(/\s*\[[^\]]+\]/g, '');
    t = stripTrailingParens(t);
    return t.replace(/\s+/g, ' ').trim();
  }

  /* SNK EN uses a lone "_" for the write-in trainer on ___'s Pikachu.
     PSA / EN collectors write three underscores. Id keys = catalogId. */
  var DISPLAY_OVERRIDES = {
    '168294': "___'s Pikachu",
    '169185': "___'s Pikachu"
  };

  function overrideKey(row) {
    if (!row) return '';
    return String(row.id || row.catalogId || row.apparelId || '');
  }

  function cleanMangledEn(s) {
    var t = String(s || '').trim();
    t = t.replace(/^[_＿]+[’']s\s+/i, "___'s ");
    t = t.replace(/^[_＿]+の/, "___'s ");
    t = t.replace(/\s*:\s*Old Back\s*\/\s*PROMO\s*$/i, '');
    t = t.replace(/\s*:\s*旧裏\s*\/\s*プロモ\s*$/, '');
    if (/^___'s Pikachu\s*:\s*PROMO$/i.test(t)) t = "___'s Pikachu";
    return t.replace(/\s+/g, ' ').trim();
  }

  function cardLabel(row, lookup) {
    var src = row || {};
    var extra = lookup && src.id != null ? lookup[String(src.id)] : null;
    var override = DISPLAY_OVERRIDES[overrideKey(src)] || (extra && DISPLAY_OVERRIDES[overrideKey(extra)]);
    if (override) return override;
    var candidates = [
      extra && extra.name,
      src.name,
      extra && extra.localizedName,
      src.localizedName,
      extra && extra.nameZh,
      src.nameZh
    ];
    var chosen = '';
    var i;
    for (i = 0; i < candidates.length; i++) {
      if (looksLatinName(candidates[i])) {
        chosen = String(candidates[i]);
        break;
      }
    }
    if (!chosen) {
      for (i = 0; i < candidates.length; i++) {
        if (candidates[i]) {
          chosen = String(candidates[i]);
          break;
        }
      }
    }
    return cleanMangledEn(stripDecor(chosen)) || String((src && src.id) || '');
  }

  function defaultVisible(ids) {
    var out = {};
    (ids || []).forEach(function (id) {
      out[id] = id === 'equal' || id === 'liquidity';
    });
    return out;
  }

  function syncLegendButtons(root, visible) {
    var host = root || (typeof document !== 'undefined' ? document : null);
    if (!host || !host.querySelectorAll) return;
    host.querySelectorAll('.legend-btn').forEach(function (btn) {
      var id = btn.getAttribute('data-series');
      var on = !!(visible && visible[id]);
      btn.classList.toggle('is-on', on);
      btn.classList.toggle('is-off', !on);
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  function syncPeriodButtons(root, period) {
    var host = root || (typeof document !== 'undefined' ? document : null);
    if (!host || !host.querySelectorAll) return;
    host.querySelectorAll('.period-btns .period-btn[data-period]').forEach(function (btn) {
      var on = btn.getAttribute('data-period') === period;
      btn.classList.toggle('is-active', on);
      btn.setAttribute('aria-selected', on ? 'true' : 'false');
    });
  }

  function printsFromCache(cache) {
    var out = {};
    if (!cache || typeof cache !== 'object') return out;
    Object.keys(cache).forEach(function (id) {
      var blob = cache[id] || {};
      var prints = blob.prints || [];
      var rows = [];
      for (var i = 0; i < prints.length; i++) {
        var row = prints[i];
        if (!row) continue;
        var d = row[0];
        var p = Number(row[1]);
        if (d && p > 0) rows.push([String(d), p]);
      }
      rows.sort(function (a, b) {
        return a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0;
      });
      out[id] = rows;
    });
    return out;
  }

  function locfAt(prints, ymd) {
    if (!prints || !prints.length || !ymd) return null;
    var lo = 0;
    var hi = prints.length - 1;
    var idx = -1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      if (prints[mid][0] <= ymd) {
        idx = mid;
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    if (idx < 0) return null;
    var p = Number(prints[idx][1]);
    return p > 0 ? p : null;
  }

  function breadthFromPrints(constituents, printsById, from, to) {
    var advancing = 0;
    var declining = 0;
    var unchanged = 0;
    if (!from || !to || from >= to) {
      return { available: false, advancing: 0, declining: 0, unchanged: 0 };
    }
    (constituents || []).forEach(function (row) {
      if (!row) return;
      var id = String(row.id || '');
      if (!id) return;
      var prints = printsById && printsById[id];
      var p0 = locfAt(prints, from);
      var p1 = locfAt(prints, to);
      if (!(p0 > 0) || !(p1 > 0)) return;
      var changePct = Math.round((p1 / p0 - 1) * 10000) / 100;
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

  function periodBreadthLabel(period, from, to) {
    if (period === 'CUSTOM') {
      return from && to ? (from + ' → ' + to) : '自訂';
    }
    if (period === 'ALL') {
      return from && to ? ('All · ' + from + ' → ' + to) : 'All';
    }
    return period || DEFAULT_PERIOD;
  }

  function breadthNote(period, from, to, br) {
    var label = periodBreadthLabel(period, from, to);
    if (br && br.available) {
      return label + ' 廣度（各成分窗口起迄 LOCF 價：上漲／下跌／持平；刊價日先算成交量柱）';
    }
    return label + ' 廣度需要兩個有 LOCF 價的日期。';
  }

  global.JpPsa10Ui = {
    DEFAULT_PERIOD: DEFAULT_PERIOD,
    looksLatinName: looksLatinName,
    stripDecor: stripDecor,
    cleanMangledEn: cleanMangledEn,
    cardLabel: cardLabel,
    defaultVisible: defaultVisible,
    syncLegendButtons: syncLegendButtons,
    syncPeriodButtons: syncPeriodButtons,
    printsFromCache: printsFromCache,
    locfAt: locfAt,
    breadthFromPrints: breadthFromPrints,
    periodBreadthLabel: periodBreadthLabel,
    breadthNote: breadthNote
  };
})(typeof window !== 'undefined' ? window : globalThis);
