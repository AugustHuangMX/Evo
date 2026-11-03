/* Local charts: native SVG, no CDN, names inserted as text only. */
(() => {
  'use strict';
  const root = document.querySelector('[data-analysis]');
  if (root) {
    const {rows, metrics} = JSON.parse(root.querySelector('[data-analysis-json]').textContent);
    const $ = s => root.querySelector(s);
    const ns = 'http://www.w3.org/2000/svg';
    let metric = 'damage', grain = 'match', dimension = 'legend', sort = 'games', ascending = false;
    let trendPoints = [], scatterPoints = [], trendIndex = 0, scatterIndex = 0, trendWidth = 760;
    const fmt = (v, digits = 0) => v == null ? '—' : v.toLocaleString('zh-CN', {maximumFractionDigits: digits, minimumFractionDigits: digits});
    const average = values => values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
    const valid = (data, key) => data.map(r => r[key]).filter(v => v != null);
    function el(tag, text, cls) {
      const n = document.createElement(tag);
      if (text != null) n.textContent = text;
      if (cls) n.className = cls;
      return n;
    }
    function svgNode(svg, tag, attrs, text) {
      const n = document.createElementNS(ns, tag);
      Object.entries(attrs).forEach(([key, value]) => n.setAttribute(key, value));
      if (text != null) n.textContent = text;
      svg.appendChild(n);
      return n;
    }
    function axes(svg, width, height, values, xMax, xLabel) {
      svg.replaceChildren();
      let low = values.reduce((a, b) => Math.min(a, b), 0), high = values.reduce((a, b) => Math.max(a, b), 1);
      if (high === low) high = low + 1;
      const x = v => 54 + v / Math.max(xMax, 1) * (width - 80);
      const y = v => 18 + (high - v) / (high - low) * (height - 58);
      for (let i = 0; i <= 4; i++) {
        const v = low + (high - low) * i / 4;
        svgNode(svg, 'line', {x1: 54, x2: width - 26, y1: y(v), y2: y(v), class: 'analysis-gridline'});
        svgNode(svg, 'text', {x: 46, y: y(v) + 4, 'text-anchor': 'end'}, fmt(v, Math.abs(v) < 10 && v % 1 ? 1 : 0));
      }
      if (low < 0) svgNode(svg, 'line', {x1: 54, x2: width - 26, y1: y(0), y2: y(0), class: 'analysis-zero'});
      svgNode(svg, 'text', {x: 54, y: height - 9}, xLabel[0]);
      svgNode(svg, 'text', {x: width - 26, y: height - 9, 'text-anchor': 'end'}, xLabel[1]);
      return {x, y};
    }
    function empty(svg, width, message) {
      svg.replaceChildren();
      svgNode(svg, 'text', {x: width / 2, y: 110, 'text-anchor': 'middle'}, message);
    }
    function matchLink(r) {
      const link = el('a', '查看这局 →');
      link.href = `/matches?page=${encodeURIComponent(r.page || 1)}&open=${encodeURIComponent(r.id)}#m${encodeURIComponent(r.id)}`;
      return link;
    }
    function detail(target, r) {
      target.replaceChildren(el('span', `${r.when || '时间未识别'} · ${r.legend} · ${r.map} · ${r.mode}${r.review ? ' · 待确认' : ''}`),
        el('strong', `伤害 ${fmt(r.damage)} / 击杀 ${fmt(r.kills)} / 名次 ${fmt(r.placement)} / RP ${fmt(r.rp_delta)}`), matchLink(r));
    }
    function showTrend(index) {
      if (!trendPoints.length) return;
      trendIndex = Math.max(0, Math.min(trendPoints.length - 1, index));
      const p = trendPoints[trendIndex], svg = $('[data-trend]');
      svg.querySelectorAll('.analysis-cursor').forEach(n => n.remove());
      const x = 54 + trendIndex / Math.max(1, trendPoints.length - 1) * (trendWidth - 80);
      svgNode(svg, 'line', {x1: x, x2: x, y1: 18, y2: 190, class: 'analysis-cursor'});
      if (p.row) {
        detail($('[data-detail]'), p.row);
      } else {
        $('[data-detail]').replaceChildren(el('strong', `${p.label} · ${metrics[metric].label}均值 ${fmt(p.value, metrics[metric].digits)}`),
          el('span', `有效 ${p.valid} / ${p.games} 局；按已识别时间的自然日汇总。`));
      }
      svg.setAttribute('aria-label', `表现趋势，${p.label}，${metrics[metric].label} ${fmt(p.value, metrics[metric].digits)}；左右键切换${p.row ? '，回车查看对局' : ''}`);
    }
    function drawTrend() {
      const svg = $('[data-trend]');
      trendWidth = Math.max(300, Math.round(svg.getBoundingClientRect().width));
      svg.setAttribute('viewBox', `0 0 ${trendWidth} 230`);
      if (grain === 'match') trendPoints = rows.map((r, i) => ({value: r[metric], label: `第 ${i + 1} 局`, row: r}));
      else {
        const days = new Map();
        rows.forEach(r => {
          if (!/^\d{4}-\d{2}-\d{2}/.test(r.when)) return;
          const key = r.when.slice(0, 10);
          if (!days.has(key)) days.set(key, []);
          days.get(key).push(r);
        });
        trendPoints = Array.from(days, ([label, games]) => ({label, value: average(valid(games, metric)), valid: valid(games, metric).length, games: games.length}));
      }
      const values = trendPoints.map(p => p.value).filter(v => v != null);
      $('[data-trend-note]').textContent = `${metrics[metric].label} · ${grain === 'match' ? '由早到晚，缺失处断线' : '按有对局的日期等距排列'}`;
      if (!values.length) {
        empty(svg, trendWidth, '没有此指标的有效读数');
        $('[data-detail]').textContent = '更换指标或调整上方筛选条件。';
        return;
      }
      const {x, y} = axes(svg, trendWidth, 230, values, trendPoints.length - 1, [trendPoints[0].label, trendPoints[trendPoints.length - 1].label]);
      let segment = [];
      const flush = () => {
        if (segment.length > 1) svgNode(svg, 'polyline', {points: segment.join(' '), class: 'analysis-line'});
        else if (segment.length) {
          const [cx, cy] = segment[0].split(',');
          svgNode(svg, 'circle', {cx, cy, r: 3, class: 'analysis-point'});
        }
        segment = [];
      };
      trendPoints.forEach((p, i) => { if (p.value == null) flush(); else segment.push(`${x(i)},${y(p.value)}`); });
      flush();
      showTrend(trendPoints.length - 1);
    }
    function showScatter(index) {
      if (!scatterPoints.length) return;
      scatterIndex = Math.max(0, Math.min(scatterPoints.length - 1, index));
      const r = scatterPoints[scatterIndex];
      $('[data-scatter]').querySelectorAll('circle').forEach((n, i) => {
        n.classList.toggle('selected', i === scatterIndex);
        n.setAttribute('r', i === scatterIndex ? 6 : 3.5);
      });
      detail($('[data-scatter-detail]'), r);
      $('[data-scatter]').setAttribute('aria-label', `伤害与击杀散点图，${r.when}，伤害 ${r.damage}，击杀 ${r.kills}；左右键切换，回车查看对局`);
    }
    function drawScatter() {
      const all = rows.filter(r => r.damage != null && r.kills != null);
      scatterPoints = all.length <= 600 ? all : Array.from({length: 600}, (_, i) => all[Math.round(i * (all.length - 1) / 599)]);
      $('[data-scatter-note]').textContent = `有效 ${all.length} / ${rows.length} 局${all.length > 600 ? ' · 等距抽样显示 600 点' : ''}`;
      const svg = $('[data-scatter]');
      const width = Math.max(300, Math.round(svg.getBoundingClientRect().width));
      svg.setAttribute('viewBox', `0 0 ${width} 230`);
      if (!all.length) { empty(svg, width, '伤害或击杀读数不足'); return; }
      const maxDamage = all.reduce((v, r) => Math.max(v, r.damage), 1);
      const {x, y} = axes(svg, width, 230, all.map(r => r.kills), maxDamage, ['0 伤害', `${fmt(maxDamage)} 伤害`]);
      scatterPoints.forEach((r, i) => {
        const circle = svgNode(svg, 'circle', {cx: x(r.damage), cy: y(r.kills), r: 3.5, class: `analysis-point${r.review ? ' review' : ''}`});
        svgNode(circle, 'title', {}, `${r.when} · ${r.legend} · 伤害 ${r.damage} · 击杀 ${r.kills}`);
        circle.addEventListener('pointerenter', () => showScatter(i));
        circle.addEventListener('click', () => showScatter(i));
      });
      showScatter(scatterPoints.length - 1);
    }
    function distribution() {
      const placements = valid(rows, 'placement');
      $('[data-placement-note]').textContent = `有效 ${placements.length} / ${rows.length} 局`;
      const target = $('[data-distribution]'); target.replaceChildren();
      [['冠军', 1, 1], ['第 2–3 名', 2, 3], ['第 4–5 名', 4, 5], ['第 6–10 名', 6, 10], ['第 11 名及以后', 11, Infinity]].forEach(([label, low, high]) => {
        const n = placements.filter(p => p >= low && p <= high).length;
        const line = el('div', null, 'analysis-dist-row');
        const track = el('span', null, 'analysis-track'), bar = el('span');
        bar.style.width = `${placements.length ? n / placements.length * 100 : 0}%`; track.appendChild(bar);
        line.append(el('span', label), track, el('strong', `${n} 局 · ${placements.length ? fmt(n / placements.length * 100) + '%' : '—'}`));
        target.appendChild(line);
      });
    }
    function drawGroups() {
      const groups = new Map();
      rows.forEach(r => { if (!groups.has(r[dimension])) groups.set(r[dimension], []); groups.get(r[dimension]).push(r); });
      const query = $('[data-search]').value.trim().toLocaleLowerCase();
      const data = Array.from(groups, ([name, games]) => {
        const values = valid(games, metric), places = valid(games, 'placement');
        return {name, games: games.length, mean: average(values), valid: values.length,
          win: places.length ? places.filter(p => p === 1).length / places.length : null,
          top5: places.length ? places.filter(p => p <= 5).length / places.length : null};
      }).filter(g => g.name.toLocaleLowerCase().includes(query));
      data.sort((a, b) => {
        if (a[sort] == null) return b[sort] == null ? 0 : 1;
        if (b[sort] == null) return -1;
        const result = sort === 'name' ? a.name.localeCompare(b.name, 'zh-CN') : a[sort] - b[sort];
        return (ascending ? 1 : -1) * result;
      });
      const max = Math.max(1, ...data.map(g => Math.abs(g.mean || 0)));
      const target = $('[data-groups]'); target.replaceChildren();
      data.forEach(g => {
        const tr = el('tr'), mean = el('td'), bar = el('span', null, 'analysis-group-bar');
        bar.style.setProperty('--amount', `${Math.abs(g.mean || 0) / max * 50}%`);
        if (g.mean < 0) bar.classList.add('negative');
        mean.append(el('strong', fmt(g.mean, metrics[metric].digits)), bar);
        tr.append(el('th', g.name), el('td', g.games), mean, el('td', `${g.valid} / ${g.games}`),
          el('td', g.win == null ? '—' : `${fmt(g.win * 100)}%`), el('td', g.top5 == null ? '—' : `${fmt(g.top5 * 100)}%`));
        tr.firstChild.scope = 'row'; target.appendChild(tr);
      });
      if (!data.length) { const tr = el('tr'), td = el('td', '没有匹配的分组'); td.colSpan = 6; tr.appendChild(td); target.appendChild(tr); }
      root.querySelectorAll('[data-sort]').forEach(button => button.closest('th').setAttribute('aria-sort', button.dataset.sort === sort ? (ascending ? 'ascending' : 'descending') : 'none'));
      $('[data-group-note]').textContent = `共 ${data.length} 个分组 · 当前比较${metrics[metric].label} · 点击列标题排序`;
    }
    function render() {
      const values = valid(rows, metric), target = $('[data-summary]'); target.replaceChildren();
      const review = rows.filter(r => r.review).length;
      const summary = [['筛选对局', `${rows.length} 局`], [`平均${metrics[metric].label}`, fmt(average(values), metrics[metric].digits)],
        ['有效读数', `${values.length} / ${rows.length}`], ['待确认', `${review} 局`]];
      summary.forEach(([label, value]) => { const cell = el('div'); cell.append(el('span', label), el('strong', value)); target.appendChild(cell); });
      drawTrend(); drawGroups();
    }
    $('[data-metric]').addEventListener('change', e => { metric = e.target.value; render(); });
    $('[data-grain]').addEventListener('change', e => { grain = e.target.value; drawTrend(); });
    $('[data-group]').addEventListener('change', e => { dimension = e.target.value; $('[data-search]').value = ''; drawGroups(); });
    $('[data-search]').addEventListener('input', drawGroups);
    root.querySelectorAll('[data-sort]').forEach(b => b.addEventListener('click', () => { ascending = sort === b.dataset.sort ? !ascending : b.dataset.sort === 'name'; sort = b.dataset.sort; drawGroups(); }));
    $('[data-trend]').addEventListener('pointermove', e => {
      const rect = e.currentTarget.getBoundingClientRect();
      showTrend(Math.round(((e.clientX - rect.left) / rect.width * trendWidth - 54) / (trendWidth - 80) * (trendPoints.length - 1)));
    });
    [['[data-trend]', () => trendIndex, () => trendPoints, showTrend], ['[data-scatter]', () => scatterIndex, () => scatterPoints, showScatter]].forEach(([selector, index, points, show]) => {
      $(selector).addEventListener('keydown', e => {
        if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) {
          e.preventDefault(); show(e.key === 'Home' ? 0 : e.key === 'End' ? points().length - 1 : index() + (e.key === 'ArrowLeft' ? -1 : 1));
        }
        if (e.key === 'Enter') { const p = points()[index()]; const r = p && (p.row || (selector.includes('scatter') ? p : null)); if (r) matchLink(r).click(); }
      });
    });
    $('[data-export]').disabled = !rows.length;
    $('[data-export]').addEventListener('click', () => {
      const fields = ['when', 'mode', 'map', 'legend', ...Object.keys(metrics), 'review'];
      const labels = ['时间', '模式', '地图', '传奇', ...Object.values(metrics).map(m => m.label), '待确认'];
      const quote = value => { let s = value == null ? '' : String(value); if (/^[\s]*[=+@-]/.test(s) && typeof value !== 'number') s = "'" + s; return '"' + s.replace(/"/g, '""') + '"'; };
      const csv = '\uFEFF' + [labels, ...rows.map(r => fields.map(f => r[f]))].map(row => row.map(quote).join(',')).join('\r\n');
      const url = URL.createObjectURL(new Blob([csv], {type: 'text/csv;charset=utf-8;'}));
      const a = el('a'); a.href = url; a.download = 'Evo-筛选对局.csv'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    render(); distribution(); drawScatter();
    let observedWidth = root.clientWidth;
    new ResizeObserver(() => {
      if (root.clientWidth === observedWidth) return;
      observedWidth = root.clientWidth;
      drawTrend(); drawScatter();
    }).observe(root);
  }

  // Full tables stay usable without JavaScript. Reference totals stay in place.
  document.querySelectorAll('.stats-table:not(.peek-table)').forEach((table, number) => {
    const body = table.tBodies[0];
    if (!body || body.rows.length < 2) return;
    const originals = Array.from(body.rows), rows = originals.filter(r => !r.classList.contains('ref-row'));
    const toolbar = document.createElement('div'); toolbar.className = 'table-tools';
    const label = document.createElement('label'); label.textContent = '查找 '; const input = document.createElement('input');
    input.type = 'search'; input.placeholder = '筛选表格中的名称'; input.id = `table-search-${number}`;
    label.htmlFor = input.id; label.appendChild(input); toolbar.appendChild(label);
    table.parentElement.before(toolbar);
    input.addEventListener('input', () => rows.forEach(r => { r.hidden = !r.cells[0].textContent.toLocaleLowerCase().includes(input.value.trim().toLocaleLowerCase()); }));
    table.querySelectorAll('thead th').forEach((th, column) => {
      if (th.classList.contains('dist-col') || th.classList.contains('spark-col')) return;
      const button = document.createElement('button'); button.type = 'button'; button.textContent = th.textContent.trim();
      th.replaceChildren(button); let ascending = false;
      button.addEventListener('click', () => {
        ascending = !ascending;
        table.querySelectorAll('thead th').forEach(n => n.removeAttribute('aria-sort'));
        th.setAttribute('aria-sort', ascending ? 'ascending' : 'descending');
        const value = row => row.cells[column].textContent.trim();
        rows.sort((a, b) => {
          const va = value(a), vb = value(b);
          if (!va || va === '—') return !vb || vb === '—' ? 0 : 1;
          if (!vb || vb === '—') return -1;
          const na = Number.parseFloat(va.replace(/,/g, '').replace(/−/g, '-')), nb = Number.parseFloat(vb.replace(/,/g, '').replace(/−/g, '-'));
          // Time columns use seconds, so 10:00 sorts after 9:59.
          const time = s => s.split(':').reduce((v, n) => v * 60 + Number(n), 0);
          const result = column && /^\d+:\d{2}$/.test(va) && /^\d+:\d{2}$/.test(vb) ? time(va) - time(vb) :
            column && Number.isFinite(na) && Number.isFinite(nb) ? na - nb : va.localeCompare(vb, 'zh-CN', {numeric: true});
          return result * (ascending ? 1 : -1);
        });
        rows.forEach(r => body.appendChild(r));
      });
    });
  });
})();
