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
      '<tr><td colspan="17" style="text-align:center;color:#f44336">数据加载失败，请刷新重试</td></tr>';
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
    html += `<tr>
<td>${i + 1} ${srcBadge}</td>
<td>${r.league}</td>
<td>${kickoff}</td>
<td>${r.home}</td><td>${r.away}</td>
<td class="${dirClass}">${r.ev_direction || '-'}</td>
<td class="${probDirClass}">${probLabel}</td>
<td>${resultDisplay}</td>
<td>${r.score || '-'}</td>
<td>${oddsStr}</td>
<td>${poissonStr}</td><td>${finalStr}</td>
<td>${evStr}</td><td>${kellyStr}</td>
<td>${pinStr}</td><td>${hkjcStr}</td>

<td title="${r.confidence_index}">${stars}</td>
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

document.addEventListener('DOMContentLoaded', init);
