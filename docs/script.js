// script.js — 竞彩泊松预测看板前端

const DATA_URL = 'data/results.json.gz';
const WEEKDAY_CN = ['周日','周一','周二','周三','周四','周五','周六'];
let allData = null;
let currentSource = 'all';

// ─── 盘口中文转换 ───
function handicapToChinese(h) {
  if (h === null || h === undefined || h === '') return '-';
  h = parseFloat(h);
  if (isNaN(h)) return '-';
  if (h === 0) return '平手';
  const sign = h < 0 ? '-' : '';
  const abs = Math.abs(h);
  const map = {
    0.25: '平/半', 0.5: '半球', 0.75: '半/一',
    1: '一球', 1.25: '一/球半', 1.5: '球半', 1.75: '球半/两',
    2: '两球', 2.25: '两/两半', 2.5: '两球半', 2.75: '两半/三',
    3: '三球', 3.25: '三/三半', 3.5: '三球半', 3.75: '三半/四',
    4: '四球'
  };
  return sign + (map[abs] || abs.toFixed(2));
}

// ─── 赔率变化着色（升绿降红）───
function chgCls(openV, closeV) {
  const o = parseFloat(openV), c = parseFloat(closeV);
  if (isNaN(o) || isNaN(c) || o === c) return '';
  return c > o ? 'ev-pos' : 'ev-neg';
}

// ─── 亚盘赔率弹窗 ───
function openAhModal(record) {
  const modal = document.getElementById('ahModal');
  const title = document.getElementById('ahModalTitle');
  const scoreStr = record.score ? `  ${record.score}` : '';
  title.textContent = `${record.home} vs ${record.away}${scoreStr} — 赔率详情`;
  modal.style.display = 'flex';

  const pin = record.pinnacle || {};
  const pinOpen = pin.open || {};
  const hkjc = record.hkjc || {};
  const william = record.william_1x2 || {};
  const liji1x2Top = ((record.liji || {})['1x2'] || {}).close || {};
  const ms1x2Top = ((record.ms || {})['1x2'] || {}).close || {};

  // ── 1X2对比表（顶部4家一行）──
  let html = '<table class="ah-odds-table">';
  html += '<tr><th class="ah-col-label">盘口</th><th>胜(主)</th><th>平</th><th>负(客)</th></tr>';
  html += `<tr><td class="ah-col-label"><span class="ah-badge ah-badge-pin">平博初盘(Pinnacle初盘)</span></td>`;
  html += `<td>${pinOpen.w ? pinOpen.w : '-'}</td><td>${pinOpen.d ? pinOpen.d : '-'}</td><td>${pinOpen.l ? pinOpen.l : '-'}</td></tr>`;
  html += `<tr><td class="ah-col-label"><span class="ah-badge ah-badge-pin">Pinnacle</span></td>`;
  html += `<td>${pin.w || '-'}</td><td>${pin.d || '-'}</td><td>${pin.l || '-'}</td></tr>`;
  html += `<tr><td class="ah-col-label"><span class="ah-badge ah-badge-bet365">Bet365</span></td>`;
  html += `<td>${hkjc.w || '-'}</td><td>${hkjc.d || '-'}</td><td>${hkjc.l || '-'}</td></tr>`;
  html += `<tr><td class="ah-col-label"><span class="ah-badge ah-badge-will">威廉希尔</span></td>`;
  html += `<td>${william.w || '-'}</td><td>${william.d || '-'}</td><td>${william.l || '-'}</td></tr>`;
  if ((liji1x2Top.w || 0) > 0) {
    html += `<tr><td class="ah-col-label"><span class="ah-badge ah-badge-liji">利记</span></td>`;
    html += `<td>${liji1x2Top.w || '-'}</td><td>${liji1x2Top.d || '-'}</td><td>${liji1x2Top.l || '-'}</td></tr>`;
  }
  if ((ms1x2Top.w || 0) > 0) {
    html += `<tr><td class="ah-col-label"><span class="ah-badge ah-badge-ms">明升</span></td>`;
    html += `<td>${ms1x2Top.w || '-'}</td><td>${ms1x2Top.d || '-'}</td><td>${ms1x2Top.l || '-'}</td></tr>`;
  }
  html += '</table>';

  // ── 亚盘+大小球 统一对比表（5家合并1表） ──
  function ahRow(badge, badgeCls, dAh, dOu) {
    const oAh = (dAh && dAh.open) || {}, cAh = dAh || {};
    const oOu = (dOu && dOu.open) || {}, cOu = dOu || {};
    const hasAh = (cAh.handicap !== null && cAh.handicap !== undefined) ||
                  (oAh.handicap !== null && oAh.handicap !== undefined);
    const hasOu = (cOu.line || 0) > 0 || (oOu.line || 0) > 0;
    if (!hasAh && !hasOu) return '';

    const hasOpenAh = (oAh.handicap !== null && oAh.handicap !== undefined);
    const hasOpenOu = (oOu.line || 0) > 0;
    const hasOpen = hasOpenAh || hasOpenOu;

    function fmtV(openV, closeV) {
      if (closeV != null && closeV !== 0) return closeV;
      if (openV != null && openV !== 0) return openV;
      return '-';
    }
    function fmtH(openH, closeH) {
      const h = (closeH !== null && closeH !== undefined) ? closeH : openH;
      return (h !== null && h !== undefined) ? handicapToChinese(h) : '-';
    }

    let s = '';
    // 初盘行
    if (hasOpen) {
      s += `<tr><td class="ah-col-label">初</td>`;
      s += `<td class="ah-col-label"><span class="ah-badge ${badgeCls}">${badge}</span></td>`;
      if (hasAh) {
        s += `<td>${hasOpenAh ? (oAh.home_w || '-') : '-'}</td>`;
        s += `<td>${hasOpenAh ? handicapToChinese(oAh.handicap) : '-'}</td>`;
        s += `<td>${hasOpenAh ? (oAh.away_w || '-') : '-'}</td>`;
      } else {
        s += '<td>-</td><td>-</td><td>-</td>';
      }
      if (hasOu) {
        s += `<td>${hasOpenOu ? (oOu.over || '-') : '-'}</td>`;
        s += `<td>${hasOpenOu ? (oOu.line || '-') : '-'}</td>`;
        s += `<td>${hasOpenOu ? (oOu.under || '-') : '-'}</td>`;
      } else {
        s += '<td>-</td><td>-</td><td>-</td>';
      }
      s += '</tr>';
    }
    // 即时行
    s += `<tr class="company-end"><td class="ah-col-label">即</td>`;
    s += `<td class="ah-col-label"><span class="ah-badge ${badgeCls}">${badge}</span></td>`;
    if (hasAh) {
      s += `<td>${fmtV(oAh.home_w, cAh.home_w)}</td>`;
      s += `<td>${fmtH(oAh.handicap, cAh.handicap)}</td>`;
      s += `<td>${fmtV(oAh.away_w, cAh.away_w)}</td>`;
    } else {
      s += '<td>-</td><td>-</td><td>-</td>';
    }
    if (hasOu) {
      s += `<td>${fmtV(oOu.over, cOu.over)}</td>`;
      s += `<td>${fmtV(oOu.line, cOu.line)}</td>`;
      s += `<td>${fmtV(oOu.under, cOu.under)}</td>`;
    } else {
      s += '<td>-</td><td>-</td><td>-</td>';
    }
    s += '</tr>';
    return s;
  }

  // 构建统一表
  let ahRows = '';
  // Bet365
  ahRows += ahRow('Bet365', 'ah-badge-bet365', record.hkjc_ah, record.hkjc_ou);
  // Pinnacle
  ahRows += ahRow('Pinnacle', 'ah-badge-pin', record.pin_ah, record.pin_ou);
  // 利记
  const liji = record.liji || {};
  const lijiClose = liji.close || {};
  const lijiOpen = liji.open || {};
  const lijiOu = record.liji_ou || {};
  const lijiOuOpen = lijiOu.open || {};
  const lijiAhObj = (lijiClose.handicap !== null && lijiClose.handicap !== undefined) || (lijiOpen.handicap !== null && lijiOpen.handicap !== undefined) ? {
    handicap: lijiClose.handicap !== null && lijiClose.handicap !== undefined ? lijiClose.handicap : null, home_w: lijiClose.home_w || null, away_w: lijiClose.away_w || null,
    open: (lijiOpen.handicap !== null && lijiOpen.handicap !== undefined) ? { handicap: lijiOpen.handicap, home_w: lijiOpen.home_w, away_w: lijiOpen.away_w } : {}
  } : null;
  const lijiOuObj = (lijiOu.over || 0) > 0 || (lijiOuOpen.over || 0) > 0 ? {
    line: lijiOu.line || null, over: lijiOu.over || null, under: lijiOu.under || null,
    open: lijiOuOpen.over ? { line: lijiOuOpen.line, over: lijiOuOpen.over, under: lijiOuOpen.under } : {}
  } : null;
  ahRows += ahRow('利记', 'ah-badge-liji', lijiAhObj, lijiOuObj);
  // 明升
  const ms = record.ms || {};
  const msClose = ms.close || {};
  const msOpen = ms.open || {};
  const msOu = record.ms_ou || {};
  const msOuOpen = msOu.open || {};
  const msAhObj = (msClose.handicap !== null && msClose.handicap !== undefined) || (msOpen.handicap !== null && msOpen.handicap !== undefined) ? {
    handicap: msClose.handicap !== null && msClose.handicap !== undefined ? msClose.handicap : null, home_w: msClose.home_w || null, away_w: msClose.away_w || null,
    open: (msOpen.handicap !== null && msOpen.handicap !== undefined) ? { handicap: msOpen.handicap, home_w: msOpen.home_w, away_w: msOpen.away_w } : {}
  } : null;
  const msOuObj = (msOu.over || 0) > 0 || (msOuOpen.over || 0) > 0 ? {
    line: msOu.line || null, over: msOu.over || null, under: msOu.under || null,
    open: msOuOpen.over ? { line: msOuOpen.line, over: msOuOpen.over, under: msOuOpen.under } : {}
  } : null;
  ahRows += ahRow('明升', 'ah-badge-ms', msAhObj, msOuObj);
  // 威廉希尔
  const williamAh = record.william_ah || {};
  const williamAhClose = (williamAh.close || (williamAh.handicap !== null && williamAh.handicap !== undefined)) ? williamAh : {};
  const williamAhOpen = (williamAh.open) || {};
  const williamOu = record.william_ou || {};
  const williamOuOpen = williamOu.open || {};
  const williamAhObj = (williamAhClose.handicap !== null && williamAhClose.handicap !== undefined) ||
                       (williamAhOpen.handicap !== null && williamAhOpen.handicap !== undefined) ? {
    handicap: williamAhClose.handicap !== null && williamAhClose.handicap !== undefined ? williamAhClose.handicap : null, home_w: williamAhClose.home_w || null, away_w: williamAhClose.away_w || null,
    open: (williamAhOpen.handicap !== null && williamAhOpen.handicap !== undefined) ? { handicap: williamAhOpen.handicap, home_w: williamAhOpen.home_w, away_w: williamAhOpen.away_w } : {}
  } : null;
  const williamOuObj = (williamOu.over || 0) > 0 || (williamOuOpen.over || 0) > 0 ? {
    line: williamOu.line || null, over: williamOu.over || null, under: williamOu.under || null,
    open: williamOuOpen.over ? { line: williamOuOpen.line, over: williamOuOpen.over, under: williamOuOpen.under } : {}
  } : null;
  ahRows += ahRow('威廉希尔', 'ah-badge-will', williamAhObj, williamOuObj);

  if (ahRows) {
    html += '<table class="ah-odds-table ah-wide">';
    html += '<tr><th></th><th>公司</th><th>主队</th><th>让球</th><th>客队</th><th>大球</th><th>盘口</th><th>小球</th></tr>';
    html += ahRows;
    html += '</table>';
  }

  // ===== HHAD让球 =====
  const hhad = record.hhad || {};
  if (hhad.handicap !== null && hhad.handicap !== undefined) {
    html += '<div class="ah-section-title">竞彩让球(HHAD)</div>';
    html += '<table class="ah-odds-table">';
    html += '<tr><th>让球</th><th>胜</th><th>平</th><th>负</th></tr>';
    html += `<tr><td>${handicapToChinese(hhad.handicap)}</td><td>${hhad.w || '-'}</td><td>${hhad.d || '-'}</td><td>${hhad.l || '-'}</td></tr>`;
    html += '</table>';
  }



  // ===== Pinnacle vs 竞彩 分歧对比（窗口底部，用即时盘） =====
  if (record.odds.w > 0 && pin.w > 0 && (pin.w !== record.odds.w || pin.d !== record.odds.d)) {
    const dW = ((pin.w - record.odds.w) / record.odds.w * 100).toFixed(1);
    const dD = ((pin.d - record.odds.d) / record.odds.d * 100).toFixed(1);
    const dL = ((pin.l - record.odds.l) / record.odds.l * 100).toFixed(1);
    const warn = Math.abs(parseFloat(dW)) > 8 || Math.abs(parseFloat(dD)) > 8 || Math.abs(parseFloat(dL)) > 8;
    html += '<div class="ah-section-title">Pinnacle vs 竞彩 分歧</div>';
    html += `<div class="ah-diff ${warn ? 'ah-diff-warn' : ''}">`;
    html += `胜 <span class="${parseFloat(dW) > 0 ? 'ev-pos' : 'ev-neg'}">${dW}%</span> | `;
    html += `平 <span class="${parseFloat(dD) > 0 ? 'ev-pos' : 'ev-neg'}">${dD}%</span> | `;
    html += `负 <span class="${parseFloat(dL) > 0 ? 'ev-pos' : 'ev-neg'}">${dL}%</span>`;
    if (warn) html += ' <span class="ah-warn-tag">⚠ 分歧大</span>';
    html += '</div>';
  }

  // ===== Pinnacle vs Bet365 分歧对比（用即时盘） =====
  // Pinnacle vs 参考行 分歧（fallback: Bet365 → 威廉希尔 → 利记 → 明升）
  const refChain = [
    { name: 'Bet365', data: hkjc },
    { name: '威廉希尔', data: william },
    { name: '利记', data: liji1x2Top },
    { name: '明升', data: ms1x2Top }
  ];
  const ref = refChain.find(r => (r.data.w || 0) > 0);
  if (ref && pin.w > 0 && (pin.w !== ref.data.w || pin.d !== ref.data.d)) {
    const hW = ((pin.w - ref.data.w) / ref.data.w * 100).toFixed(1);
    const hD = ((pin.d - ref.data.d) / ref.data.d * 100).toFixed(1);
    const hL = ((pin.l - ref.data.l) / ref.data.l * 100).toFixed(1);
    const hwarn = Math.abs(parseFloat(hW)) > 8 || Math.abs(parseFloat(hD)) > 8 || Math.abs(parseFloat(hL)) > 8;
    html += `<div class="ah-section-title">Pinnacle vs ${ref.name} 分歧</div>`;
    html += `<div class="ah-diff ${hwarn ? 'ah-diff-warn' : ''}">`;
    html += `胜 <span class="${parseFloat(hW) > 0 ? 'ev-pos' : 'ev-neg'}">${hW}%</span> | `;
    html += `平 <span class="${parseFloat(hD) > 0 ? 'ev-pos' : 'ev-neg'}">${hD}%</span> | `;
    html += `负 <span class="${parseFloat(hL) > 0 ? 'ev-pos' : 'ev-neg'}">${hL}%</span>`;
    if (hwarn) html += ' <span class="ah-warn-tag">⚠ 分歧大</span>';
    html += '</div>';
  }

  document.getElementById('ahModalContent').innerHTML = html;
}

// ─── 泊松概率计算 ───
function poissonPMF(k, lambda) {
  if (lambda <= 0) return k === 0 ? 1 : 0;
  let logP = k * Math.log(lambda) - lambda;
  for (let i = 2; i <= k; i++) logP -= Math.log(i);
  return Math.exp(logP);
}

// 计算比分概率分布（home_lambda x away_lambda）
function calcScoreDistribution(homeLambda, awayLambda) {
  const scores = [];
  const MAX_GOALS = 6;
  for (let h = 0; h <= MAX_GOALS; h++) {
    for (let a = 0; a <= MAX_GOALS; a++) {
      const p = poissonPMF(h, homeLambda) * poissonPMF(a, awayLambda);
      if (p > 0.0001) scores.push({ home: h, away: a, prob: p, label: h + '-' + a });
    }
  }
  return scores;
}

// 加权排序: score = α * normalized_poisson + (1-α) * normalized_league_freq
function weightedSort(scores, league, alpha) {
  const freq = (typeof LEAGUE_SCORE_FREQ !== 'undefined' && LEAGUE_SCORE_FREQ[league]) || {};
  const totalFreq = Object.values(freq).reduce((s, v) => s + v, 0) || 1;
  const maxProb = Math.max(...scores.map(s => s.prob)) || 1;
  const maxFreq = Math.max(...Object.values(freq), 1);

  // 计算大2.5球概率
  let over25 = 0;
  scores.forEach(s => { if (s.home + s.away > 2) over25 += s.prob; });

  return scores.map(s => {
    const normPoisson = s.prob / maxProb;
    const leagueCount = freq[s.label] || 0;
    const normFreq = leagueCount / maxFreq;
    const mixed = alpha * normPoisson + (1 - alpha) * normFreq;
    return { ...s, leagueCount, mixed, normPoisson, normFreq };
  }).sort((a, b) => b.mixed - a.mixed);
}

// ─── 模态框 ───
function openPoissonModal(record) {
  const hl = record.home_lambda || 0, al = record.away_lambda || 0;
  if (hl <= 0 && al <= 0) return;

  const modal = document.getElementById('poissonModal');
  const title = document.getElementById('modalTitle');
  title.textContent = `${record.home} vs ${record.away} — 比分概率分布 (λ=${hl.toFixed(2)}/${al.toFixed(2)})`;
  modal.style.display = 'flex';

  const alphaSlider = document.getElementById('alphaSlider');
  const alphaValue = document.getElementById('alphaValue');

  function render() {
    const alpha = parseInt(alphaSlider.value) / 100;
    alphaValue.textContent = alpha.toFixed(2);
    const scores = calcScoreDistribution(hl, al);
    const sorted = weightedSort(scores, record.league, alpha);
    const top20 = sorted.slice(0, 20);

    // 统计摘要
    let over25 = 0, under25 = 0;
    scores.forEach(s => {
      if (s.home + s.away > 2) over25 += s.prob; else under25 += s.prob;
    });
    const totalGoals = hl + al;

    renderBarChart(top20, record.score, over25, totalGoals);
    if (document.getElementById('showHeatmap').checked) {
      renderHeatmap(sorted, record.score);
    } else {
      document.getElementById('modalHeatmap').innerHTML = '';
    }
  }

  alphaSlider.oninput = render;
  document.getElementById('showHeatmap').onchange = render;
  render();
}

function renderBarChart(top20, actualScore, over25, totalGoals) {
  const container = document.getElementById('modalBarChart');
  const maxMixed = top20[0]?.mixed || 1;

  // 摘要行
  let html = `<div class="modal-summary">
    <span>期望总进球: <b>${totalGoals.toFixed(2)}</b></span>
    <span>大2.5球: <b style="color:#4caf50">${(over25*100).toFixed(1)}%</b></span>
    <span>小2.5球: <b style="color:#ff9800">${((1-over25)*100).toFixed(1)}%</b></span>
  </div>`;

  html += '<div class="bar-list">';
  top20.forEach((s, i) => {
    const pct = (s.mixed / maxMixed * 100).toFixed(1);
    const isHit = s.label === actualScore;
    const cls = isHit ? 'bar-hit' : '';
    const poissonPct = (s.prob * 100).toFixed(2);
    const freqPct = s.leagueCount > 0 ? `${s.leagueCount}次` : '-';
    const isHome = s.home > s.away;
    const isDraw = s.home === s.away;
    const resultCls = isDraw ? 'score-draw' : (isHome ? 'score-home' : 'score-away');
    html += `<div class="bar-row ${cls} ${resultCls}">
      <span class="bar-rank">${i + 1}</span>
      <span class="bar-label">${s.label}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div>
      <span class="bar-poisson">P:${poissonPct}%</span>
      <span class="bar-freq">L:${freqPct}</span>
      <span class="bar-mixed">${(s.mixed * 100).toFixed(1)}%</span>
      ${isHit ? '<span class="bar-actual">✔ 实际</span>' : ''}
    </div>`;
  });
  html += '</div>';
  container.innerHTML = html;
}

function renderHeatmap(sorted, actualScore) {
  const container = document.getElementById('modalHeatmap');
  const SIZE = 4;
  const grid = {};
  sorted.forEach(s => {
    if (s.home <= SIZE && s.away <= SIZE) grid[s.home + ',' + s.away] = s;
  });
  const maxProb = Math.max(...Object.values(grid).map(s => s.prob)) || 1;

  let html = '<div class="heatmap-title">比分热力图 (0-4球)</div>';
  html += '<table class="heatmap-table"><tr><th></th>';
  for (let a = 0; a <= SIZE; a++) html += `<th>${a}</th>`;
  html += '</tr>';
  for (let h = 0; h <= SIZE; h++) {
    html += `<tr><th>${h}</th>`;
    for (let a = 0; a <= SIZE; a++) {
      const s = grid[h + ',' + a];
      const prob = s ? (s.prob * 100).toFixed(1) : '0.0';
      const intensity = s ? Math.min(1, s.prob / maxProb) : 0;
      const bg = intensity > 0 ? `rgba(88,166,255,${intensity * 0.8})` : 'transparent';
      const isHit = s && s.label === actualScore;
      const border = isHit ? 'border:2px solid #4caf50;' : '';
      html += `<td style="background:${bg};${border}" title="P=${prob}%">${prob}%</td>`;
    }
    html += '</tr>';
  }
  html += '</table>';
  html += '<div class="heatmap-axis">← 客队进球 | 主队进球 ↓</div>';
  container.innerHTML = html;
}

// ─── 初始化 ───
async function init() {
  try {
    const resp = await fetch(DATA_URL);
    if (DATA_URL.endsWith('.gz')) {
      const blob = await resp.blob();
      if (typeof DecompressionStream !== 'undefined') {
        const ds = new DecompressionStream('gzip');
        const stream = blob.stream().pipeThrough(ds);
        const decompressed = await new Response(stream).blob();
        allData = JSON.parse(await decompressed.text());
      } else {
        // 降级: 用 pako.js
        const buf = await blob.arrayBuffer();
        const pako = await import('https://cdnjs.cloudflare.com/ajax/libs/pako/2.1.0/pako.min.js');
        const text = pako.ungzip(new Uint8Array(buf), { to: 'string' });
        allData = JSON.parse(text);
      }
    } else {
      allData = await resp.json();
    }
    renderDateSelector();
    loadDate();
    renderDailyStats();
    renderLeagueStats();
    bindEvents();
  } catch(e) {
    console.error('数据加载失败:', e);
    document.getElementById('matchBody').innerHTML =
      '<tr><td colspan="16" style="text-align:center;color:#f44336">数据加载失败，请刷新重试</td></tr>';
  }
}

// ─── 日期选择器 ───
function renderDateSelector() {
  const sel = document.getElementById('dateSelect');
  const dates = allData.dates || [];
  const today = new Date().toISOString().slice(0, 10);
  const defaultDate = dates.includes(today) ? today : (dates[0] || '');
  sel.innerHTML = dates.map(d => {
    const wd = WEEKDAY_CN[new Date(d).getDay()];
    const selected = d === defaultDate ? ' selected' : '';
    return `<option value="${d}"${selected}>${d} ${wd}</option>`;
  }).join('');
}

// ─── 比赛表格 ───
function loadDate() {
  if (!allData) return;
  const sel = document.getElementById('dateSelect').value;
  const showResulted = document.getElementById('showResulted').checked;
  const showPending = document.getElementById('showPending').checked;
  const records = allData.matches[sel] || [];
  const tbody = document.getElementById('matchBody');
  let html = '';

  records.forEach((r, i) => {
    const hasResult = !!r.result;
    if (hasResult && !showResulted) return;
    if (!hasResult && !showPending) return;
    // 按来源过滤
    if (currentSource !== 'all' && r.source !== currentSource) return;

    const dirClass = r.ev_hit ? 'hit' : (hasResult ? 'miss' : 'pending');
    const resultDisplay = hasResult
      ? `${r.result} <span class="${r.ev_hit ? 'hit' : 'miss'}">E${r.ev_hit ? '✔' : '✘'}</span><span class="${r.prob_hit ? 'hit' : 'miss'}">P${r.prob_hit ? '✔' : '✘'}</span>`
      : '待定';
    const srcBadge = r.source === 'beidan'
      ? '<span class="badge badge-bd">北单</span>'
      : '<span class="badge badge-jc">竞彩</span>';
    const tierColors = {high:'#4caf50', medium:'#ff9800', low:'#8b949e', very_low:'#484f58'};
    const tierLabels = {high:'高', medium:'中', low:'低', very_low:'低'};
    const tier = r.confidence_tier || 'very_low';
    const tierBadge = `<span style="display:inline-block;padding:1px 5px;border-radius:3px;font-size:10px;color:#fff;background:${tierColors[tier]||'#484f58'};margin-left:3px">${tierLabels[tier]||'低'}</span>`;
    const evW = r.ev.w, evD = r.ev.d, evL = r.ev.l;
    const evCls = v => v > 0 ? 'ev-pos' : 'ev-neg';
    // 亚盘列优先显示Pinnacle让球，其次利记，再次威廉希尔，再次Bet365
    const pinAh = r.pin_ah || {};
    const lijiAh = (r.liji || {}).close || {};
    let ahDisplayVal = '-';
    let ahClickable = false;
    if (pinAh.handicap !== null && pinAh.handicap !== undefined) {
      ahDisplayVal = handicapToChinese(pinAh.handicap);
      ahClickable = true;
    } else if (lijiAh.handicap !== null && lijiAh.handicap !== undefined) {
      ahDisplayVal = handicapToChinese(lijiAh.handicap);
      ahClickable = true;
    } else {
      const williamAh = (r.william_ah || {});
      const williamAhHc = williamAh.handicap;
      if (williamAhHc !== null && williamAhHc !== undefined) {
        ahDisplayVal = handicapToChinese(williamAhHc);
        ahClickable = true;
      } else {
        const hkjcAh = (r.hkjc_ah || {}).close || {};
        if (hkjcAh.handicap !== null && hkjcAh.handicap !== undefined) {
          ahDisplayVal = handicapToChinese(hkjcAh.handicap);
          ahClickable = true;
        }
      }
    }
    const ahCell = ahClickable
      ? `<td class="ah-clickable" data-date="${sel}" data-idx="${i}">${ahDisplayVal}</td>`
      : `<td>${ahDisplayVal}</td>`;
    const poissonStr = `${r.poisson.w}/${r.poisson.d}/${r.poisson.l}`;
    const finalStr = `${r.final_prob.w}/${r.final_prob.d}/${r.final_prob.l}`;
    const evStr = `<span class="${evCls(evW)}">${evW.toFixed(2)}</span>/<span class="${evCls(evD)}">${evD.toFixed(2)}</span>/<span class="${evCls(evL)}">${evL.toFixed(2)}</span>`;
    const kellyStr = `${r.kelly.w}/${r.kelly.d}/${r.kelly.l}`;
    const kickoff = (!r.kickoff || r.kickoff === '待定') ? '-' : r.kickoff.substring(11, 16);
    const probPct = (r.prediction_prob * 100).toFixed(1) + '%';
    const probLabel = r.prob_direction ? `${r.prob_direction} ${probPct}` : probPct;
    const probDirClass = r.prob_hit ? 'hit' : (hasResult ? 'miss' : 'pending');
    const hasPoisson = r.poisson && (r.poisson.w > 0 || r.poisson.d > 0 || r.poisson.l > 0);
    const poissonCell = hasPoisson
      ? `<td class="poisson-clickable" data-date="${sel}" data-idx="${i}">${poissonStr}</td>`
      : `<td>${poissonStr}</td>`;

    html += `<tr>
<td>${i + 1} ${srcBadge}${tierBadge}</td>
<td>${r.source === 'beidan' ? '北单' : '竞彩'}${r.league}</td>
<td>${kickoff}</td>
<td>${r.home}</td>${ahCell}<td>${r.away}</td>
<td class="${dirClass}">${r.ev_direction || '-'}</td>
<td class="${probDirClass}">${probLabel}</td>
<td>${resultDisplay}</td>
<td>${r.score || '-'}</td>
${poissonCell}<td>${finalStr}</td>
<td>${evStr}</td><td>${kellyStr}</td>
</tr>`;
  });

  tbody.innerHTML = html || '<tr><td colspan="13" style="text-align:center;color:#8b949e">无数据</td></tr>';
}

// ─── 每日统计 ───
function renderDailyStats() {
  const tbody = document.getElementById('dailyBody');
  const dates = allData.dates || [];
  let html = '';
  for (const d of dates) {
    const s = allData.daily_stats[d] || {};
    const n = s.with_result || 0;
    const colorEv = s.ev_rate >= 55 ? '#4caf50' : s.ev_rate < 40 ? '#f44336' : '#ff9800';
    const colorPb = s.prob_rate >= 60 ? '#4caf50' : s.prob_rate < 45 ? '#f44336' : '#ff9800';
    const colorAny = s.any_rate >= 65 ? '#4caf50' : s.any_rate < 50 ? '#f44336' : '#ff9800';
    html += `<tr>
<td>${d}</td><td>${s.total || 0}</td><td>${n}</td>
<td style="color:${colorEv}">${s.ev_rate || 0}% (${s.ev_hits || 0}/${n})</td>
<td style="color:${colorPb}">${s.prob_rate || 0}% (${s.prob_hits || 0}/${n})</td>
<td style="color:${colorAny}">${s.any_rate || 0}%</td>
</tr>`;
  }
  tbody.innerHTML = html;
}

// ─── 联赛统计 ───
function renderLeagueStats() {
  const leagueMap = {};
  for (const [date, records] of Object.entries(allData.matches)) {
    for (const r of records) {
      const lg = r.league || '未知';
      if (!leagueMap[lg]) leagueMap[lg] = { total: 0, ev_hit: 0, prob_hit: 0, with_result: 0 };
      leagueMap[lg].total++;
      if (r.result) {
        leagueMap[lg].with_result++;
        if (r.ev_hit) leagueMap[lg].ev_hit++;
        if (r.prob_hit) leagueMap[lg].prob_hit++;
      }
    }
  }
  const sorted = Object.entries(leagueMap).sort((a, b) => b[1].with_result - a[1].with_result);
  const tbody = document.getElementById('leagueBody');
  let html = '';
  for (const [lg, s] of sorted) {
    const n = s.with_result;
    const evRate = n ? (s.ev_hit / n * 100).toFixed(1) : '-';
    const pbRate = n ? (s.prob_hit / n * 100).toFixed(1) : '-';
    html += `<tr><td>${lg}</td><td>${s.total}</td><td>${s.ev_hit}</td><td>${s.prob_hit}</td><td>${evRate}%</td><td>${pbRate}%</td></tr>`;
  }
  tbody.innerHTML = html;
}

// ─── 事件绑定 ───
function bindEvents() {
  // 标签切换
  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      const name = tab.dataset.tab;
      document.getElementById('tab-' + name).classList.add('active');
    });
  });

  // 日期/筛选
  document.getElementById('dateSelect').addEventListener('change', loadDate);
  document.getElementById('showResulted').addEventListener('change', loadDate);
  document.getElementById('showPending').addEventListener('change', loadDate);

  // 来源筛选按钮
  document.querySelectorAll('.source-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.source-tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentSource = btn.dataset.source;
      loadDate();
    });
  });

  // 泊松列点击：事件委托
  document.getElementById('matchBody').addEventListener('click', (e) => {
    const cell = e.target.closest('.poisson-clickable');
    if (!cell || !allData) return;
    const date = cell.dataset.date;
    const idx = parseInt(cell.dataset.idx, 10);
    const records = allData.matches[date];
    if (records && records[idx]) openPoissonModal(records[idx]);
  });

  // 亚盘列点击：事件委托
  document.getElementById('matchBody').addEventListener('click', (e) => {
    const cell = e.target.closest('.ah-clickable');
    if (!cell || !allData) return;
    const date = cell.dataset.date;
    const idx = parseInt(cell.dataset.idx, 10);
    const records = allData.matches[date];
    if (records && records[idx]) openAhModal(records[idx]);
  });

  // 亚盘模态框关闭
  document.getElementById('ahModalClose').addEventListener('click', () => {
    document.getElementById('ahModal').style.display = 'none';
  });
  document.getElementById('ahModal').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) e.target.style.display = 'none';
  });

  // 模态框关闭
  document.getElementById('modalClose').addEventListener('click', () => {
    document.getElementById('poissonModal').style.display = 'none';
  });
  document.getElementById('poissonModal').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) e.target.style.display = 'none';
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') document.getElementById('poissonModal').style.display = 'none';
  });
}

// ─── Excel 下载 ───
function downloadExcel() {
  if (!allData) return;
  const sel = document.getElementById('dateSelect').value;
  const showResulted = document.getElementById('showResulted').checked;
  const showPending = document.getElementById('showPending').checked;
  const records = allData.matches[sel] || [];

  const headers = ['编号','联赛','开赛时间','主队','亚盘盘口','亚盘主水','亚盘客水','亚盘来源','客队','来源','推荐方向','概率推荐','赛果','比分',
    '胜赔','平赔','负赔','泊松W','泊松D','泊松L','综合W','综合D','综合L',
    'EV_W','EV_D','EV_L','凯利W','凯利D','凯利L',
    'Pinnacle即时_W','Pinnacle即时_D','Pinnacle即时_L','Pinnacle初_W','Pinnacle初_D','Pinnacle初_L','Bet365_W','Bet365_D','Bet365_L',
    'HHAD盘口','HHAD胜','HHAD平','HHAD负',
    'Pinnacle_AH_让球','Pinnacle_AH_主水','Pinnacle_AH_客水',
    'Pinnacle_OU_盘口','Pinnacle_OU_大球','Pinnacle_OU_小球',
    '百家OU_大球','百家OU_盘口','百家OU_小球',
    '利记OU_大球','利记OU_盘口','利记OU_小球',
    '明升OU_大球','明升OU_盘口','明升OU_小球'];

  const rows = [headers];
  records.forEach((r, i) => {
    const hasResult = !!r.result;
    if (hasResult && !showResulted) return;
    if (!hasResult && !showPending) return;
    rows.push([
      i + 1, r.league, r.kickoff || '', r.home,
      r.ah ? r.ah.handicap : '', r.ah ? r.ah.home_w : 0, r.ah ? r.ah.away_w : 0, r.ah ? r.ah.source : '',
      r.away, r.source || '',
      r.ev_direction || '', r.prob_direction ? `${r.prob_direction} ${(r.prediction_prob*100).toFixed(1)}%` : '',
      r.result || '', r.score || '',
      r.odds.w, r.odds.d, r.odds.l,
      r.poisson.w, r.poisson.d, r.poisson.l,
      r.final_prob.w, r.final_prob.d, r.final_prob.l,
      r.ev.w, r.ev.d, r.ev.l,
      r.kelly.w, r.kelly.d, r.kelly.l,
      r.pinnacle.w, r.pinnacle.d, r.pinnacle.l,
      (r.pinnacle.open && r.pinnacle.open.w) || 0, (r.pinnacle.open && r.pinnacle.open.d) || 0, (r.pinnacle.open && r.pinnacle.open.l) || 0,
      r.hkjc.w, r.hkjc.d, r.hkjc.l,
      r.hhad ? r.hhad.handicap : '', r.hhad ? r.hhad.w : 0, r.hhad ? r.hhad.d : 0, r.hhad ? r.hhad.l : 0,
      r.pin_ah ? r.pin_ah.handicap : '', r.pin_ah ? r.pin_ah.home_w : 0, r.pin_ah ? r.pin_ah.away_w : 0,
      r.pin_ou ? r.pin_ou.line : '', r.pin_ou ? r.pin_ou.over : 0, r.pin_ou ? r.pin_ou.under : 0,
      r.ou ? r.ou.over : 0, r.ou ? r.ou.line : '', r.ou ? r.ou.under : 0,
      r.liji_ou ? r.liji_ou.over : 0, r.liji_ou ? r.liji_ou.line : '', r.liji_ou ? r.liji_ou.under : 0,
      r.ms_ou ? r.ms_ou.over : 0, r.ms_ou ? r.ms_ou.line : '', r.ms_ou ? r.ms_ou.under : 0
    ]);
  });

  const wb = XLSX.utils.book_new();
  const ws = XLSX.utils.aoa_to_sheet(rows);

  ws['!cols'] = headers.map((h, i) => {
    if (i <= 1 || i === 4) return {wch: 14};
    if (h.includes('推荐') || h === '赛果') return {wch: 10};
    return {wch: 10};
  });

  XLSX.utils.book_append_sheet(wb, ws, '比赛数据');

  const dailyHeaders = ['日期','总场次','已开奖','EV命中率%','EV命中数','概率命中率%','概率命中数','任一命中率%'];
  const dailyRows = [dailyHeaders];
  for (const d of allData.dates || []) {
    const s = allData.daily_stats[d] || {};
    const n = s.with_result || 0;
    dailyRows.push([d, s.total || 0, n, s.ev_rate || 0, s.ev_hits || 0, s.prob_rate || 0, s.prob_hits || 0, s.any_rate || 0]);
  }
  const ws2 = XLSX.utils.aoa_to_sheet(dailyRows);
  XLSX.utils.book_append_sheet(wb, ws2, '每日统计');

  XLSX.writeFile(wb, `竞彩预测_${sel}.xlsx`);
}

document.addEventListener('DOMContentLoaded', init);

