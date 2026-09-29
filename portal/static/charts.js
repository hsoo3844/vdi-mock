// 의존성 없는 SVG 차트: 시계열 라인/영역 차트(hover 툴팁) + 링 게이지
const SERIES_COLORS = ['#6366f1', '#06b6d4', '#f59e0b', '#ec4899', '#10b981', '#8b5cf6', '#ef4444', '#3b82f6'];

const fmt = {
  pct: v => `${v === 0 ? 0 : v.toFixed(v < 10 ? 1 : 0)}%`,
  bytes: v => { const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0; while (Math.abs(v) >= 1024 && i < 4) { v /= 1024; i++; } return `${v.toFixed(v < 10 && i ? 1 : 0)} ${u[i]}`; },
  bps: v => fmt.bytes(v) + '/s',
  cores: v => v < 1 ? `${Math.round(v * 1000)}m` : `${v.toFixed(2)}`,
  num: v => Number.isInteger(v) ? String(v) : v.toFixed(2),
  sec: v => v < 1 ? `${Math.round(v * 1000)}ms` : `${v.toFixed(1)}s`,
  rps: v => `${v.toFixed(2)}/s`,
};
const clock = (t, withSec = false) => {
  const d = new Date(t * 1000), p = n => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}${withSec ? ':' + p(d.getSeconds()) : ''}`;
};

function niceMax(v) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v)), n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

/**
 * lineChart(el, series, opts)
 *  series: [{ name, points: [[unixSec, value], ...], color? }]
 *  opts: { format: fn, max?: number, area?: bool, height?: number }
 */
function lineChart(el, series, opts = {}) {
  const format = opts.format || fmt.num;
  const W = Math.max(el.clientWidth, 280), H = opts.height || 190, P = { l: 44, r: 12, t: 12, b: 24 };
  const all = series.flatMap(s => s.points);
  if (!all.length) { el.innerHTML = `<div class="chart-empty">데이터 수집 중… (15초 간격)</div>`; return; }
  const xs = all.map(p => p[0]), x0 = Math.min(...xs), x1 = Math.max(...xs, x0 + 1);
  const yMax = opts.max ?? niceMax(Math.max(...all.map(p => p[1])) * 1.1);
  const X = t => P.l + (t - x0) / (x1 - x0) * (W - P.l - P.r);
  const Y = v => P.t + (1 - Math.min(v, yMax) / yMax) * (H - P.t - P.b);
  const grid = [0, .25, .5, .75, 1].map(f => {
    const y = Y(yMax * f);
    return `<line x1="${P.l}" x2="${W - P.r}" y1="${y}" y2="${y}" class="grid"/><text x="${P.l - 8}" y="${y + 4}" class="axis" text-anchor="end">${format(yMax * f)}</text>`;
  }).join('');
  const short = x1 - x0 < 300;  // 5분 미만 구간은 초까지 표시해 눈금 중복 방지
  const ticks = [0, .5, 1].map(f => { const t = x0 + (x1 - x0) * f; return `<text x="${X(t)}" y="${H - 6}" class="axis" text-anchor="${f === 0 ? 'start' : f === 1 ? 'end' : 'middle'}">${clock(t, short)}</text>`; }).join('');
  const colored = series.map((s, i) => ({ ...s, color: s.color || SERIES_COLORS[i % SERIES_COLORS.length] }));
  const paths = colored.map((s, i) => {
    if (!s.points.length) return '';
    const d = s.points.map((p, j) => `${j ? 'L' : 'M'}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join('');
    const gid = `g${Math.random().toString(36).slice(2, 8)}`;
    const area = opts.area !== false ? `<defs><linearGradient id="${gid}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${s.color}" stop-opacity=".28"/><stop offset="1" stop-color="${s.color}" stop-opacity="0"/></linearGradient></defs>
      <path d="${d}L${X(s.points.at(-1)[0])},${Y(0)}L${X(s.points[0][0])},${Y(0)}Z" fill="url(#${gid})"/>` : '';
    return `${area}<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
  }).join('');
  const legend = colored.length > 1 || opts.legend ? `<div class="legend">${colored.map(s => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join('')}</div>` : '';
  el.innerHTML = `${legend}<div class="chart-box"><svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img">${grid}${ticks}${paths}
    <line class="cursor" y1="${P.t}" y2="${H - P.b}" visibility="hidden"/></svg><div class="tip" hidden></div></div>`;

  const svg = $('svg', el), tip = $('.tip', el), cursor = $('.cursor', el);
  svg.addEventListener('mousemove', e => {
    const r = svg.getBoundingClientRect(), mx = (e.clientX - r.left) * (W / r.width);
    const t = x0 + (mx - P.l) / (W - P.l - P.r) * (x1 - x0);
    const rows = colored.map(s => {
      if (!s.points.length) return null;
      const p = s.points.reduce((a, b) => Math.abs(b[0] - t) < Math.abs(a[0] - t) ? b : a);
      return { s, p };
    }).filter(Boolean);
    if (!rows.length) return;
    const px = X(rows[0].p[0]);
    cursor.setAttribute('x1', px); cursor.setAttribute('x2', px); cursor.setAttribute('visibility', 'visible');
    tip.hidden = false;
    tip.innerHTML = `<b>${clock(rows[0].p[0])}</b>` + rows.sort((a, b) => b.p[1] - a.p[1])
      .map(({ s, p }) => `<div><i style="background:${s.color}"></i>${esc(s.name)}<span>${format(p[1])}</span></div>`).join('');
    const left = px / W * r.width;
    tip.style.left = `${Math.min(Math.max(left + 12, 0), r.width - tip.offsetWidth - 4)}px`;
  });
  svg.addEventListener('mouseleave', () => { tip.hidden = true; cursor.setAttribute('visibility', 'hidden'); });
}

/** ringGauge(pct, color) → SVG 문자열 */
function ringGauge(pct, color) {
  const r = 30, c = 2 * Math.PI * r, v = Math.max(0, Math.min(100, pct || 0));
  const tone = v >= 85 ? 'var(--err)' : v >= 65 ? 'var(--warn)' : color;
  return `<svg viewBox="0 0 80 80" width="80" height="80" class="ring">
    <circle cx="40" cy="40" r="${r}" fill="none" stroke="var(--surface-3)" stroke-width="8"/>
    <circle cx="40" cy="40" r="${r}" fill="none" stroke="${tone}" stroke-width="8" stroke-linecap="round"
      stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - v / 100)}" transform="rotate(-90 40 40)" style="transition:stroke-dashoffset .6s ease"/>
    <text x="40" y="45" text-anchor="middle" class="ring-text">${Math.round(v)}%</text></svg>`;
}

// Prometheus 응답 → 차트 series
const promSeries = (res, label) => (res?.data?.result || []).map(r => ({
  name: typeof label === 'function' ? label(r.metric) : (r.metric[label] || label),
  points: (r.values || [r.value]).filter(Boolean).map(([t, v]) => [Number(t), Number(v)]).filter(p => Number.isFinite(p[1])),
}));
const promScalar = res => { const v = res?.data?.result?.[0]?.value?.[1]; return v == null ? null : Number(v); };
