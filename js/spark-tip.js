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
      '.spark-wrap{position:relative;margin-top:8px;min-width:0;}',
      '.spark-wrap .spark{width:100%;height:100px;display:block;cursor:crosshair;touch-action:none;}',
      '.spark-wrap .spark:focus{outline:2px solid var(--accent,#2563eb);outline-offset:2px;}',
      '.spark-tip{position:absolute;z-index:5;pointer-events:none;',
      'transform:translate(-50%,calc(-100% - 8px));background:var(--card,#fff);',
      'border:1px solid var(--line,#e4e7ec);border-radius:8px;padding:6px 10px;',
      'box-shadow:0 1px 2px rgba(16,24,40,.06),0 1px 3px rgba(16,24,40,.08);',
      'white-space:nowrap;}',
      '.spark-tip[hidden]{display:none !important;}',
      '.spark-tip .tt-value{font-weight:700;font-variant-numeric:tabular-nums;',
      'font-size:.95rem;color:var(--text,#1a1d21);line-height:1.2;}',
      '.spark-tip .tt-date{color:var(--muted,#5c6570);font-size:.72rem;margin-top:2px;}'
    ].join('');
    document.head.appendChild(s);
  }

  function fmtIndex(n) {
    return Number(n).toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });
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

  function draw(svg, points, opts) {
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

    if (wrap._spark && typeof wrap._spark.unbind === 'function') {
      wrap._spark.unbind();
    }

    var pts = (points || []).filter(function (p) {
      return p && p.date && p.index != null && !Number.isNaN(Number(p.index));
    });

    while (svg.firstChild) svg.removeChild(svg.firstChild);

    var color = opts.color || '#2563eb';
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
      stroke: '#98a2b3',
      'stroke-width': '1',
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
      var rect = svg.getBoundingClientRect();
      var left = (p.x / W) * rect.width;
      var half = tip.offsetWidth / 2 || 40;
      left = Math.max(half + 4, Math.min(rect.width - half - 4, left));
      var top = Math.max(10, (p.y / H) * rect.height);
      tip.style.left = left + 'px';
      tip.style.top = top + 'px';
    }

    function clientToIndex(clientX) {
      var rect = svg.getBoundingClientRect();
      if (!rect.width) return laid.length - 1;
      return nearestByX(laid, ((clientX - rect.left) / rect.width) * W);
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

  global.SparkTip = { draw: draw };
})(window);
