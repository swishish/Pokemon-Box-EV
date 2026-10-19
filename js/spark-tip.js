/* Shared pok500-style sparkline hover: snap to real history points only. */
(function (global) {
  'use strict';
  var SVGNS = 'http://www.w3.org/2000/svg';
  var W = 320;
  var H = 100;
  var PAD = 8;

  function mk(name, attrs) {
    var el = document.createElementNS(SVGNS, name);
    Object.keys(attrs || {}).forEach(function (k) {
      el.setAttribute(k, attrs[k]);
    });
    return el;
  }

  function ensureCss() {
    if (document.getElementById('spark-tip-css')) return;
    var s = document.createElement('style');
    s.id = 'spark-tip-css';
    s.textContent = [
      '.spark-wrap{position:relative;margin-top:8px;min-width:0;overflow:visible;}',
      '.spark-wrap .spark{width:100%;height:100px;display:block;cursor:crosshair;touch-action:none;}',
      '.spark-wrap .spark.has-vol{height:148px;}',
      '.spark-wrap .spark:focus{outline:2px solid var(--ink,#141414);outline-offset:2px;}',
      '.spark-tip{position:absolute;z-index:5;pointer-events:none;',
      'transform:translate(-50%,calc(-100% - 8px));background:var(--card,#fff);',
      'border:var(--bw,2px) solid var(--ink,#141414);border-radius:8px;padding:6px 10px;',
      'box-shadow:var(--shadow-xs,2px 2px 0 #141414);',
      'white-space:nowrap;}',
      '.spark-tip[hidden]{display:none !important;}',
      '.spark-tip .tt-value{font-weight:800;font-variant-numeric:tabular-nums;',
      'font-size:.95rem;color:var(--text,#141414);line-height:1.2;}',
      '.spark-tip .tt-date{color:var(--muted,#5f5a51);font-size:.72rem;margin-top:2px;}',
      '.spark-tip.is-multi{white-space:normal;min-width:0;max-width:min(18rem,calc(100% - 8px));}',
      '.spark-tip .tt-row{display:flex;align-items:baseline;justify-content:space-between;',
      'gap:8px 12px;font-variant-numeric:tabular-nums;font-size:.78rem;line-height:1.25;}',
      '.spark-tip .tt-row + .tt-row{margin-top:3px;}',
      '.spark-tip .tt-name{display:inline-flex;align-items:center;gap:5px;min-width:0;',
      'color:var(--muted,#5f5a51);font-weight:700;}',
      '.spark-tip .tt-swatch{width:8px;height:8px;border-radius:50%;border:1px solid #141414;',
      'flex-shrink:0;}',
      '.spark-tip .tt-num{font-weight:800;color:var(--text,#141414);}'
    ].join('');
    document.head.appendChild(s);
  }

  function fmtIndex(n) {
    return Number(n).toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });
  }

  function ymdToNum(ymd) {
    var m = String(ymd || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!m) return NaN;
    return Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  }

  function nearestByX(laid, x) {
    var best = 0;
    var bestD = Infinity;
    for (var i = 0; i < laid.length; i++) {
      var d = Math.abs(laid[i].x - x);
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    }
    return best;
  }

  function canHover() {
    return !(window.matchMedia && window.matchMedia('(hover: none)').matches);
  }

  /* Map a client X through the SVG's screen CTM so letterboxing / CSS stretch
     cannot put the tip in a different place than the polyline. */
  function clientToViewBoxX(svg, clientX) {
    var ctm = svg.getScreenCTM && svg.getScreenCTM();
    if (ctm && svg.createSVGPoint) {
      var pt = svg.createSVGPoint();
      pt.x = clientX;
      pt.y = 0;
      return pt.matrixTransform(ctm.inverse()).x;
    }
    var rect = svg.getBoundingClientRect();
    if (!rect.width) return W;
    return ((clientX - rect.left) / rect.width) * W;
  }

  function viewBoxToWrap(svg, wrap, vx, vy) {
    var wrapRect = wrap.getBoundingClientRect();
    var ctm = svg.getScreenCTM && svg.getScreenCTM();
    if (ctm && svg.createSVGPoint) {
      var pt = svg.createSVGPoint();
      pt.x = vx;
      pt.y = vy;
      var screen = pt.matrixTransform(ctm);
      return { x: screen.x - wrapRect.left, y: screen.y - wrapRect.top };
    }
    var svgRect = svg.getBoundingClientRect();
    return {
      x: (svgRect.left - wrapRect.left) + (vx / W) * svgRect.width,
      y: (svgRect.top - wrapRect.top) + (vy / H) * svgRect.height
    };
  }

  function drawMulti(svg, series, opts) {
    ensureCss();
    opts = opts || {};
    if (!svg) return;

    var wrap = svg.closest ? svg.closest('.spark-wrap') : null;
    if (!wrap) wrap = svg.parentNode;
    if (wrap && wrap.classList && !wrap.classList.contains('spark-wrap')) {
      wrap.classList.add('spark-wrap');
    }

    var tip = wrap.querySelector('.spark-tip');
    if (!tip) {
      tip = document.createElement('div');
      tip.className = 'spark-tip';
      tip.hidden = true;
      tip.setAttribute('role', 'status');
      wrap.appendChild(tip);
    }
    tip.classList.add('is-multi');

    if (wrap._spark && typeof wrap._spark.unbind === 'function') {
      wrap._spark.unbind();
    }

    var cleaned = (series || []).map(function (s) {
      var pts = ((s && s.points) || []).filter(function (p) {
        return p && p.date && p.index != null && !Number.isNaN(Number(p.index));
      }).map(function (p) {
        return { date: String(p.date), index: Number(p.index) };
      });
      return {
        id: (s && s.id) || '',
        name: (s && (s.name || s.label)) || '',
        color: (s && s.color) || '#3d6fd9',
        dash: (s && s.dash) || '',
        width: (s && (s.width || s.strokeWidth)) || '2',
        points: pts
      };
    }).filter(function (s) { return s.points.length >= 1; });

    while (svg.firstChild) svg.removeChild(svg.firstChild);

    var state = {
      laid: [],
      dates: [],
      i: -1,
      cross: null,
      dots: [],
      tip: tip,
      svg: svg,
      wrap: wrap
    };

    function hide() {
      if (state.cross) state.cross.setAttribute('opacity', '0');
      state.dots.forEach(function (d) { d.setAttribute('opacity', '0'); });
      if (state.tip) state.tip.hidden = true;
      state.i = -1;
    }

    hide();

    var volPts = ((opts && opts.volume) || []).filter(function (p) {
      return p && p.date && p.volume != null && !Number.isNaN(Number(p.volume));
    }).map(function (p) {
      return { date: String(p.date), volume: Math.max(0, Number(p.volume)) };
    });
    var volByDate = {};
    volPts.forEach(function (p) { volByDate[p.date] = p.volume; });
    var hasVol = volPts.length > 0;
    var chartH = hasVol ? 148 : H;
    var priceBottom = hasVol ? 100 : (chartH - PAD);
    if (hasVol) svg.classList.add('has-vol');
    else svg.classList.remove('has-vol');

    var allVals = [];
    var allDates = [];
    cleaned.forEach(function (s) {
      s.points.forEach(function (p) {
        allVals.push(p.index);
        allDates.push(p.date);
      });
    });
    volPts.forEach(function (p) { allDates.push(p.date); });

    if (cleaned.length === 0 || allVals.length < 2) {
      svg.setAttribute('hidden', 'hidden');
      svg.setAttribute('aria-hidden', 'true');
      svg.removeAttribute('tabindex');
      wrap._spark = { unbind: function () {} };
      return;
    }

    svg.removeAttribute('hidden');
    svg.removeAttribute('aria-hidden');
    svg.setAttribute('tabindex', '0');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', opts.ariaLabel || '三條復古指數走勢，可懸停或點選看日期與指數');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + chartH);
    svg.setAttribute('preserveAspectRatio', 'none');

    var uniqDates = allDates.filter(function (d, i) { return allDates.indexOf(d) === i; });
    uniqDates.sort();
    var t0 = ymdToNum(uniqDates[0]);
    var t1 = ymdToNum(uniqDates[uniqDates.length - 1]);
    var tSpan = (t1 - t0) || 1;
    var min = Math.min.apply(null, allVals);
    var max = Math.max.apply(null, allVals);
    var span = (max - min) || 1;
    var volMax = 0;
    volPts.forEach(function (p) { if (p.volume > volMax) volMax = p.volume; });

    function xOf(date) {
      var t = ymdToNum(date);
      if (Number.isNaN(t)) return PAD;
      return PAD + ((t - t0) / tSpan) * (W - PAD * 2);
    }
    function yOf(val) {
      return PAD + (1 - (val - min) / span) * (priceBottom - PAD);
    }

    var dateIndex = {};
    uniqDates.forEach(function (d, i) { dateIndex[d] = i; });

    if (hasVol) {
      var volTop = 108;
      var volBottom = chartH - PAD;
      var volSpan = volMax || 1;
      var barW = Math.max(0.6, (W - PAD * 2) / Math.max(uniqDates.length, 1) * 0.72);
      uniqDates.forEach(function (d) {
        var v = volByDate[d] || 0;
        if (!(v > 0)) return;
        var bh = (v / volSpan) * (volBottom - volTop);
        svg.appendChild(mk('rect', {
          x: (xOf(d) - barW / 2).toFixed(2),
          y: (volBottom - bh).toFixed(2),
          width: barW.toFixed(2),
          height: bh.toFixed(2),
          fill: opts.volumeColor || '#ffd23f',
          stroke: '#141414',
          'stroke-width': '0.4',
          'vector-effect': 'non-scaling-stroke',
          opacity: '0.92'
        }));
      });
      svg.appendChild(mk('line', {
        x1: String(PAD),
        x2: String(W - PAD),
        y1: String(104),
        y2: String(104),
        stroke: '#e7dfd0',
        'stroke-width': '1',
        'vector-effect': 'non-scaling-stroke'
      }));
    }

    cleaned.forEach(function (s) {
      var laid = s.points.map(function (p) {
        return { date: p.date, index: p.index, x: xOf(p.date), y: yOf(p.index) };
      });
      s.byDate = {};
      laid.forEach(function (p) { s.byDate[p.date] = p; });
      if (laid.length >= 2) {
        var lineAttrs = {
          fill: 'none',
          stroke: s.color,
          'stroke-width': String(s.width || '2'),
          'stroke-linejoin': 'round',
          'stroke-linecap': 'round',
          'vector-effect': 'non-scaling-stroke',
          points: laid.map(function (p) {
            return p.x.toFixed(1) + ',' + p.y.toFixed(1);
          }).join(' ')
        };
        if (s.dash) lineAttrs['stroke-dasharray'] = s.dash;
        svg.appendChild(mk('polyline', lineAttrs));
      }
      var last = laid[laid.length - 1];
      var lastR = Number(s.width) > 0 && Number(s.width) < 1.6 ? '2.4' : '3.2';
      svg.appendChild(mk('circle', {
        cx: last.x.toFixed(1),
        cy: last.y.toFixed(1),
        r: lastR,
        fill: s.color
      }));
    });

    var datesLaid = uniqDates.map(function (d) {
      return { date: d, x: xOf(d) };
    });
    state.dates = datesLaid;

    var cross = mk('line', {
      x1: '0',
      x2: '0',
      y1: String(PAD),
      y2: String(chartH - PAD),
      stroke: '#5f5a51',
      'stroke-width': '1',
      'vector-effect': 'non-scaling-stroke',
      opacity: '0'
    });
    svg.appendChild(cross);
    state.cross = cross;

    var dots = cleaned.map(function (s) {
      var dot = mk('circle', {
        r: '4.5',
        fill: s.color,
        stroke: '#fff',
        'stroke-width': '2',
        opacity: '0'
      });
      svg.appendChild(dot);
      return dot;
    });
    state.dots = dots;

    var hit = mk('rect', {
      x: '0',
      y: '0',
      width: String(W),
      height: String(chartH),
      fill: 'transparent'
    });
    svg.appendChild(hit);

    function show(i) {
      if (i < 0 || i >= datesLaid.length) return;
      var slot = datesLaid[i];
      state.i = i;
      cross.setAttribute('x1', slot.x.toFixed(1));
      cross.setAttribute('x2', slot.x.toFixed(1));
      cross.setAttribute('opacity', '1');
      var rows = '';
      var tipY = chartH;
      cleaned.forEach(function (s, si) {
        var p = s.byDate[slot.date];
        if (p) {
          dots[si].setAttribute('cx', p.x.toFixed(1));
          dots[si].setAttribute('cy', p.y.toFixed(1));
          dots[si].setAttribute('opacity', '1');
          if (p.y < tipY) tipY = p.y;
        } else {
          dots[si].setAttribute('opacity', '0');
        }
        rows +=
          '<div class="tt-row"><span class="tt-name"><span class="tt-swatch" style="background:' +
          s.color + '"></span>' + (s.name || s.id) + '</span><span class="tt-num">' +
          (p ? fmtIndex(p.index) : '—') + '</span></div>';
      });
      if (hasVol) {
        var vol = volByDate[slot.date];
        rows +=
          '<div class="tt-row"><span class="tt-name"><span class="tt-swatch" style="background:' +
          (opts.volumeColor || '#ffd23f') + '"></span>' +
          (opts.volumeLabel || '成交') + '</span><span class="tt-num">' +
          (vol == null ? '—' : Number(vol).toLocaleString('en-US')) + '</span></div>';
      }
      tip.innerHTML =
        '<div class="tt-date">' + slot.date + '</div>' + rows;
      tip.hidden = false;
      var pos = viewBoxToWrap(svg, wrap, slot.x, tipY === chartH ? PAD : tipY);
      var half = tip.offsetWidth / 2 || 40;
      var maxLeft = Math.max(half + 4, wrap.clientWidth - half - 4);
      var left = Math.max(half + 4, Math.min(maxLeft, pos.x));
      var top = Math.max(10, pos.y);
      tip.style.left = left + 'px';
      tip.style.top = top + 'px';
    }

    function clientToIndex(clientX) {
      return nearestByX(datesLaid, clientToViewBoxX(svg, clientX));
    }

    function onPointer(e) {
      if (typeof e.clientX !== 'number') return;
      show(clientToIndex(e.clientX));
    }

    function onLeave() {
      if (canHover()) hide();
    }

    function onDocDown(e) {
      if (!wrap.contains(e.target)) hide();
    }

    function onKey(e) {
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault();
        var cur = state.i < 0 ? datesLaid.length - 1 : state.i;
        var next = e.key === 'ArrowLeft'
          ? Math.max(0, cur - 1)
          : Math.min(datesLaid.length - 1, cur + 1);
        show(next);
      } else if (e.key === 'Escape') {
        hide();
      }
    }

    wrap.addEventListener('pointermove', onPointer);
    wrap.addEventListener('pointerdown', onPointer);
    wrap.addEventListener('pointerleave', onLeave);
    svg.addEventListener('keydown', onKey);
    svg.addEventListener('blur', hide);
    document.addEventListener('pointerdown', onDocDown);

    state.unbind = function () {
      wrap.removeEventListener('pointermove', onPointer);
      wrap.removeEventListener('pointerdown', onPointer);
      wrap.removeEventListener('pointerleave', onLeave);
      svg.removeEventListener('keydown', onKey);
      svg.removeEventListener('blur', hide);
      document.removeEventListener('pointerdown', onDocDown);
    };
    wrap._spark = state;
  }

  function draw(svg, points, opts) {
    ensureCss();
    opts = opts || {};
    if (!svg) return;
    if (opts.series && opts.series.length) {
      drawMulti(svg, opts.series, opts);
      return;
    }

    var wrap = svg.closest ? svg.closest('.spark-wrap') : null;
    if (!wrap) wrap = svg.parentNode;
    if (wrap && wrap.classList && !wrap.classList.contains('spark-wrap')) {
      wrap.classList.add('spark-wrap');
    }

    var tip = wrap.querySelector('.spark-tip');
    if (!tip) {
      tip = document.createElement('div');
      tip.className = 'spark-tip';
      tip.hidden = true;
      tip.setAttribute('role', 'status');
      wrap.appendChild(tip);
    }
    tip.classList.remove('is-multi');

    if (wrap._spark && typeof wrap._spark.unbind === 'function') {
      wrap._spark.unbind();
    }

    var pts = (points || []).filter(function (p) {
      return p && p.date && p.index != null && !Number.isNaN(Number(p.index));
    });

    while (svg.firstChild) svg.removeChild(svg.firstChild);

    var color = opts.color || '#3d6fd9';
    var state = {
      laid: [],
      i: -1,
      cross: null,
      dot: null,
      tip: tip,
      svg: svg,
      wrap: wrap
    };

    function hide() {
      if (state.cross) state.cross.setAttribute('opacity', '0');
      if (state.dot) state.dot.setAttribute('opacity', '0');
      if (state.tip) state.tip.hidden = true;
      state.i = -1;
    }

    hide();

    if (pts.length < 2) {
      svg.setAttribute('hidden', 'hidden');
      svg.setAttribute('aria-hidden', 'true');
      svg.removeAttribute('tabindex');
      wrap._spark = { unbind: function () {} };
      return;
    }

    svg.removeAttribute('hidden');
    svg.removeAttribute('aria-hidden');
    svg.setAttribute('tabindex', '0');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', opts.ariaLabel || '指數走勢，可懸停或點選看日期與指數');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    /* Stretch viewBox to the CSS box. Default "meet" letterboxed the line in
       the middle of a wide 100px-tall chart, so tip left/top (mapped across
       the full CSS width) sat far from the actual stroke. */
    svg.setAttribute('preserveAspectRatio', 'none');

    var vals = pts.map(function (p) { return Number(p.index); });
    var min = Math.min.apply(null, vals);
    var max = Math.max.apply(null, vals);
    var span = (max - min) || 1;
    var laid = pts.map(function (p, i) {
      return {
        date: String(p.date),
        index: Number(p.index),
        x: PAD + (i / (pts.length - 1)) * (W - PAD * 2),
        y: PAD + (1 - (Number(p.index) - min) / span) * (H - PAD * 2)
      };
    });
    state.laid = laid;

    svg.appendChild(mk('polyline', {
      fill: 'none',
      stroke: color,
      'stroke-width': '2',
      'stroke-linejoin': 'round',
      'stroke-linecap': 'round',
      'vector-effect': 'non-scaling-stroke',
      points: laid.map(function (p) {
        return p.x.toFixed(1) + ',' + p.y.toFixed(1);
      }).join(' ')
    }));

    var last = laid[laid.length - 1];
    svg.appendChild(mk('circle', {
      cx: last.x.toFixed(1),
      cy: last.y.toFixed(1),
      r: '3.2',
      fill: color
    }));

    var cross = mk('line', {
      x1: '0',
      x2: '0',
      y1: String(PAD),
      y2: String(H - PAD),
      stroke: '#5f5a51',
      'stroke-width': '1',
      'vector-effect': 'non-scaling-stroke',
      opacity: '0'
    });
    var dot = mk('circle', {
      r: '4.5',
      fill: color,
      stroke: '#fff',
      'stroke-width': '2',
      opacity: '0'
    });
    var hit = mk('rect', {
      x: '0',
      y: '0',
      width: String(W),
      height: String(H),
      fill: 'transparent'
    });
    svg.appendChild(cross);
    svg.appendChild(dot);
    svg.appendChild(hit);
    state.cross = cross;
    state.dot = dot;

    function show(i) {
      if (i < 0 || i >= laid.length) return;
      var p = laid[i];
      state.i = i;
      cross.setAttribute('x1', p.x.toFixed(1));
      cross.setAttribute('x2', p.x.toFixed(1));
      cross.setAttribute('opacity', '1');
      dot.setAttribute('cx', p.x.toFixed(1));
      dot.setAttribute('cy', p.y.toFixed(1));
      dot.setAttribute('opacity', '1');
      tip.innerHTML =
        '<div class="tt-value">' + fmtIndex(p.index) + '</div>' +
        '<div class="tt-date">' + p.date + '</div>';
      tip.hidden = false;
      var pos = viewBoxToWrap(svg, wrap, p.x, p.y);
      var half = tip.offsetWidth / 2 || 40;
      var maxLeft = Math.max(half + 4, wrap.clientWidth - half - 4);
      var left = Math.max(half + 4, Math.min(maxLeft, pos.x));
      var top = Math.max(10, pos.y);
      tip.style.left = left + 'px';
      tip.style.top = top + 'px';
    }

    function clientToIndex(clientX) {
      return nearestByX(laid, clientToViewBoxX(svg, clientX));
    }

    function onPointer(e) {
      if (typeof e.clientX !== 'number') return;
      show(clientToIndex(e.clientX));
    }

    function onLeave() {
      if (canHover()) hide();
    }

    function onDocDown(e) {
      if (!wrap.contains(e.target)) hide();
    }

    function onKey(e) {
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault();
        var cur = state.i < 0 ? laid.length - 1 : state.i;
        var next = e.key === 'ArrowLeft'
          ? Math.max(0, cur - 1)
          : Math.min(laid.length - 1, cur + 1);
        show(next);
      } else if (e.key === 'Escape') {
        hide();
      }
    }

    wrap.addEventListener('pointermove', onPointer);
    wrap.addEventListener('pointerdown', onPointer);
    wrap.addEventListener('pointerleave', onLeave);
    svg.addEventListener('keydown', onKey);
    svg.addEventListener('blur', hide);
    document.addEventListener('pointerdown', onDocDown);

    state.unbind = function () {
      wrap.removeEventListener('pointermove', onPointer);
      wrap.removeEventListener('pointerdown', onPointer);
      wrap.removeEventListener('pointerleave', onLeave);
      svg.removeEventListener('keydown', onKey);
      svg.removeEventListener('blur', hide);
      document.removeEventListener('pointerdown', onDocDown);
    };
    wrap._spark = state;
  }

  global.SparkTip = { draw: draw, drawMulti: drawMulti };
})(window);
