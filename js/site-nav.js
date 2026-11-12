/* Single source for the public top bar. Pages only mount [data-site-nav]. */
(function () {
  var ITEMS = [
    {
      href: './calc.html',
      label: '開盒 EV',
      title: '這盒落地價，開出來大概回幾多？',
      match: ['calc.html']
    },
    {
      href: './chase-fit.html',
      label: 'Chase 公允值',
      title: '這張 PSA10，相對模型貴還是便宜？',
      match: ['chase-fit.html']
    },
    {
      href: './market-jp-psa10-bench.html',
      label: '日版 PSA10',
      title: '日版 PSA10：綜合、復古、日版 SM＋劍盾、現行，以及較輕的特典／日版 Tag Team',
      match: ['market-jp-psa10-bench.html']
    },
    {
      href: './market-en.html',
      label: '英文市況',
      title: '英文裸卡 TCGPlayer Top 500，以及英文 DP–SM 中世代 Top 50',
      match: ['market-en.html']
    },
    {
      href: './market-vintage.html',
      label: '英文 Base',
      title: '英文 Base 四種印刷的指數，以及人口',
      match: ['market-vintage.html']
    }
  ];

  function pageName() {
    var path = (location.pathname || '').split('/').pop() || 'index.html';
    if (!path || path.slice(-1) === '/') return 'index.html';
    return path;
  }

  function fill(nav) {
    var here = pageName();
    nav.innerHTML = ITEMS.map(function (item) {
      var on = item.match.indexOf(here) !== -1;
      return '<a class="nav-btn' + (on ? ' is-current' : '') + '" href="' + item.href + '"' +
        (on ? ' aria-current="page"' : '') +
        ' title="' + item.title + '">' + item.label + '</a>';
    }).join('');
  }

  var nav = document.querySelector('[data-site-nav]');
  if (nav) fill(nav);
})();
