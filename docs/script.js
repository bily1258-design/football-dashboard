// script.js — 足彩价值投注看板
var allData = null;
var allMatches = [];

function fmtOdds(v){return v>0?v.toFixed(2):'-'}
function fmtPct(v){return (v*100).toFixed(1)+'%'}
function fmtEv(v){return v===0?'0%':(v>0?'+':'')+(v*100).toFixed(1)+'%'}
function evClass(v){return v>0.03?'ev-pos':v<-0.03?'ev-neg':'ev-zero'}
function dirClass(d){return d==='home'?'dir-home':d==='draw'?'dir-draw':d==='away'?'dir-away':'dir-wait'}
function dirText(d){return d==='home'?'主胜':d==='draw'?'平局':d==='away'?'客胜':'观望'}
function fmtTime(t){return t?t.replace(/^\d{2}-/,''):''}

function applyFilters(){
  var dateVal = document.getElementById('dateFilter').value;
  var srcVal = document.getElementById('sourceFilter').value;
  var sortVal = document.getElementById('sortBy').value;
  var filtered = allMatches.filter(function(m){
    if(dateVal!=='all' && m.date!==dateVal) return false;
    if(srcVal!=='all' && m.source!==srcVal) return false;
    return true;
  });
  if(sortVal==='ev_desc') filtered.sort(function(a,b){return Math.max(b.ev_win,b.ev_draw,b.ev_loss)-Math.max(a.ev_win,a.ev_draw,a.ev_loss)});
  else if(sortVal==='ev_asc') filtered.sort(function(a,b){return Math.max(a.ev_win,a.ev_draw,a.ev_loss)-Math.max(b.ev_win,b.ev_draw,b.ev_loss)});
  else if(sortVal==='time') filtered.sort(function(a,b){return a.match_time.localeCompare(b.match_time)});
  else if(sortVal==='odds') filtered.sort(function(a,b){return b.odds_win-a.odds_win});
  renderTable(filtered);
}

function renderTable(matches){
  var tbody = document.getElementById('matchBody');
  tbody.innerHTML = '';
  matches.forEach(function(m){
    var maxEv = Math.max(m.ev_win,m.ev_draw,m.ev_loss);
    var tr = document.createElement('tr');
    if(maxEv>0.08) tr.style.background='rgba(74,222,128,0.05)';
    var hc=m.hit==='✅'?'hit-yes':m.hit==='❌'?'hit-no':'';
    tr.innerHTML =
      '<td>'+fmtTime(m.match_time)+'</td>'+
      '<td><span class="tag tag-'+m.source+'">'+(m.event||m.source)+'</span></td>'+
      '<td class="team-name">'+m.home_team+'</td>'+
      '<td class="team-name">'+m.away_team+'</td>'+
      '<td class="score-cell">'+(m.score||'-')+'</td>'+
      '<td class="'+hc+'">'+(m.hit||'')+'</td>'+
      '<td class="odds-cell"><span class="odds-val odds-w">'+fmtOdds(m.odds_win)+'</span> <span class="odds-val odds-d">'+fmtOdds(m.odds_draw)+'</span> <span class="odds-val odds-l">'+fmtOdds(m.odds_loss)+'</span></td>'+
      '<td class="odds-cell"><span class="odds-val odds-w">'+fmtPct(m.poisson_win)+'</span> <span class="odds-val odds-d">'+fmtPct(m.poisson_draw)+'</span> <span class="odds-val odds-l">'+fmtPct(m.poisson_loss)+'</span></td>'+
      '<td class="odds-cell"><span class="odds-val odds-w">'+fmtPct(m.fusion_win)+'</span> <span class="odds-val odds-d">'+fmtPct(m.fusion_draw)+'</span> <span class="odds-val odds-l">'+fmtPct(m.fusion_loss)+'</span></td>'+
      '<td class="odds-cell"><span class="odds-val '+evClass(m.ev_win)+'">'+fmtEv(m.ev_win)+'</span> <span class="odds-val '+evClass(m.ev_draw)+'">'+fmtEv(m.ev_draw)+'</span> <span class="odds-val '+evClass(m.ev_loss)+'">'+fmtEv(m.ev_loss)+'</span></td>'+
      '<td><span class="'+dirClass(m.prediction)+'">'+dirText(m.prediction)+'</span></td>'+
      '<td class="lambda-cell">'+m.home_lambda+' / '+m.away_lambda+'</td>';
    tbody.appendChild(tr);
  });
  document.getElementById('matchCount').textContent = matches.length+' 场';
}

function renderStats(data){
  var bar = document.getElementById('stats-bar');
  bar.innerHTML = '';
  if(!data.daily_stats) return;
  data.daily_stats.forEach(function(ds){
    var card = document.createElement('div');
    card.className = 'stat-card';
    card.innerHTML = '<div class="stat-val">'+ds.count+'</div><div class="stat-label">'+ds.date+'<br>EV+ '+ds.positive_ev+'</div>';
    bar.appendChild(card);
  });
}

// 加载
fetch('data/results.json?v='+Date.now())
  .then(function(r){return r.json()})
  .then(function(data){
    allData = data;
    allMatches = data.matches || [];
    document.getElementById('loading').style.display='none';
    document.getElementById('table-wrap').style.display='block';
    document.getElementById('updateTime').textContent = '🕐 '+data.generated_at;
    document.getElementById('dateRange').textContent = data.date_range;
    document.getElementById('matchCount').textContent = data.total_matches+' 场';
    // 填充日期过滤
    var sel = document.getElementById('dateFilter');
    (data.daily_stats||[]).forEach(function(ds){
      var opt = document.createElement('option');
      opt.value = ds.date; opt.textContent = ds.date+' ('+ds.count+')';
      sel.appendChild(opt);
    });
    renderStats(data);
    applyFilters();
  })
  .catch(function(err){
    document.getElementById('loading').innerHTML = '❌ 数据加载失败，请刷新重试<br><small>'+err.message+'</small>';
  });
