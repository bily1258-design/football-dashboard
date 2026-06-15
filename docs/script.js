// script.js — 竞彩泊松预测看板前端

const DATA_URL = 'data/results.json';
const WEEKDAY_CN = ['周日','周一','周二','周三','周四','周五','周六'];
let allData = null;

// ─── 初始化 ───
async function init() {
  try {
    const resp = await fetch(DATA_URL);
    allData = await resp.json();
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

    const dirClass = r.ev_hit ? 'hit' : (hasResult ? 'miss' : 'pending');
    const probHitClass = r.prob_hit ? 'hit' : (hasResult ? 'miss' : 'pending');
    const resultDisplay = hasResult
      ? `${r.result} <span class="${r.ev_hit ? 'hit' : 'miss'}">E${r.ev_hit ? '✔' : '✘'}</span><span class="${r.prob_hit ? 'hit' : 'miss'}">P${r.prob_hit ? '✔' : '✘'}</span>`
      : '待定';
    const srcBadge = r.source === 'beidan'
      ? '<span class="badge badge-bd">北单</span>'
      : '<span class="badge badge-jc">竞彩</span>';
    const evW = r.ev.w, evD = r.ev.d, evL = r.ev.l;
    const evCls = v => v > 0 ? 'ev-pos' : 'ev-neg';
    const pinStr = r.pinnacle.w > 0
      ? `${r.pinnacle.w}/${r.pinnacle.d}/${r.pinnacle.l}`
      : '-';
    const hkjcStr = r.hkjc.w > 0
      ? `${r.hkjc.w}/${r.hkjc.d}/${r.hkjc.l}`
      : '-';
    const ah = r.ah || {};
    const ahStr = (ah.handicap !== null && ah.handicap !== undefined && ah.handicap !== 0)
      ? `${ah.handicap > 0 ? '+' : ''}${ah.handicap} (${ah.home_w}/${ah.away_w})`
      : '-';
    const oddsStr = `${r.odds.w}/${r.odds.d}/${r.odds.l}`;
    const poissonStr = `${r.poisson.w}/${r.poisson.d}/${r.poisson.l}`;
    const finalStr = `${r.final_prob.w}/${r.final_prob.d}/${r.final_prob.l}`;
    const evStr = `<span class="${evCls(evW)}">${evW.toFixed(2)}</span>/<span class="${evCls(evD)}">${evD.toFixed(2)}</span>/<span class="${evCls(evL)}">${evL.toFixed(2)}</span>`;
    const kellyStr = `${r.kelly.w}/${r.kelly.d}/${r.kelly.l}`;
    const kickoff = r.kickoff ? r.kickoff.substring(11, 16) : '';
    const probPct = (r.prediction_prob * 100).toFixed(1) + '%';
    const probLabel = r.prob_direction ? `${r.prob_direction} ${probPct}` : probPct;
    const probDirClass = r.prob_direction === '主胜' ? 'hit' : (r.prob_direction === '客胜' ? 'miss' : 'draw');

    html += `<tr>
<td>${i + 1} ${srcBadge}</td>
<td>${r.league}</td>
<td>${kickoff}</td>
<td>${r.home}</td><td>${ahStr}</td><td>${r.away}</td>
<td class="${dirClass}">${r.ev_direction || '-'}</td>
<td class="${probDirClass}">${probLabel}</td>
<td>${resultDisplay}</td>
<td>${r.score || '-'}</td>
<td>${oddsStr}</td>
<td>${poissonStr}</td><td>${finalStr}</td>
<td>${evStr}</td><td>${kellyStr}</td>
<td>${pinStr}</td><td>${hkjcStr}</td>
</tr>`;
  });

  tbody.innerHTML = html || '<tr><td colspan="17" style="text-align:center;color:#8b949e">无数据</td></tr>';
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
}

// ─── Excel 下载 ───
function downloadExcel() {
  if (!allData) return;
  const sel = document.getElementById('dateSelect').value;
  const showResulted = document.getElementById('showResulted').checked;
  const showPending = document.getElementById('showPending').checked;
  const records = allData.matches[sel] || [];

  // 表头
  const headers = ['编号','联赛','开赛时间','主队','亚盘盘口','亚盘主水','亚盘客水','亚盘来源','客队','来源','推荐方向','概率推荐','赛果','比分',
    '胜赔','平赔','负赔','泊松W','泊松D','泊松L','综合W','综合D','综合L',
    'EV_W','EV_D','EV_L','凯利W','凯利D','凯利L',
    'Pinnacle_W','Pinnacle_D','Pinnacle_L','HKJC_W','HKJC_D','HKJC_L',
    'HHAD盘口','HHAD胜','HHAD平','HHAD负'];

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
      r.hkjc.w, r.hkjc.d, r.hkjc.l,
      r.hhad ? r.hhad.handicap : '', r.hhad ? r.hhad.w : 0, r.hhad ? r.hhad.d : 0, r.hhad ? r.hhad.l : 0
    ]);
  });

  const wb = XLSX.utils.book_new();
  const ws = XLSX.utils.aoa_to_sheet(rows);

  // 列宽
  ws['!cols'] = headers.map((h, i) => {
    if (i <= 1 || i === 4) return {wch: 14};
    if (h.includes('推荐') || h === '赛果') return {wch: 10};
    return {wch: 10};
  });

  XLSX.utils.book_append_sheet(wb, ws, '比赛数据');

  // 每日统计 sheet
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
