// script.js — 足彩价值投注看板 v4
var allData = null;
var allMatches = [];

function fmtOdds(v){return v>0?v.toFixed(2):'-'}
function fmtPct(v){return (v*100).toFixed(1)+'%'}
function fmtPctSign(v){return v===0?'0%':(v>0?'+':'')+v.toFixed(1)+'%'}
function dirClass(d){return d==='home'?'dir-home':d==='draw'?'dir-draw':d==='away'?'dir-away':'dir-wait'}
function dirText(d){return d==='home'?'主胜':d==='draw'?'平局':d==='away'?'客胜':'观望'}
function renderOdds(c, h){
  var html = '';
  // 平博
  if(c && c.current){
    var o=c.open, cur=c.current, d=c.div_pct;
    var divStr = '';
    for(var i=0;i<3;i++){
      var cls = d[i] < -0.3 ? 'oc-pct-down' : (d[i] > 0.3 ? 'oc-pct-up' : 'oc-pct-flat');
      divStr += '<span class="'+cls+'">'+fmtPctSign(d[i])+'</span>' + (i<2?'<span class="oc-sep">|</span>':'');
    }
    html += '<div class="oc-source">平博</div>'+
      '<div class="oc-line"><span class="oc-label">初</span><span class="oc-open">'+o[0].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-open">'+o[1].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-open">'+o[2].toFixed(2)+'</span></div>'+
      '<div class="oc-line"><span class="oc-label">即</span><span class="oc-cur">'+cur[0].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-cur">'+cur[1].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-cur">'+cur[2].toFixed(2)+'</span></div>'+
      '<div class="oc-line oc-div"><span class="oc-label">分</span>'+divStr+'</div>';
  } else {
    html += '<span class="odds-val odds-w">--</span>';
  }
  // 香港马会
  if(h && h.current){
    var o=h.open, cur=h.current, d=h.div_pct;
    var divStr = '';
    for(var i=0;i<3;i++){
      var cls = d[i] < -0.3 ? 'oc-pct-down' : (d[i] > 0.3 ? 'oc-pct-up' : 'oc-pct-flat');
      divStr += '<span class="'+cls+'">'+fmtPctSign(d[i])+'</span>' + (i<2?'<span class="oc-sep">|</span>':'');
    }
    html += '<div class="oc-sep-line"></div>'+
      '<div class="oc-source oc-source-hkjc">马会</div>'+
      '<div class="oc-line"><span class="oc-label">初</span><span class="oc-open">'+o[0].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-open">'+o[1].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-open">'+o[2].toFixed(2)+'</span></div>'+
      '<div class="oc-line"><span class="oc-label">即</span><span class="oc-cur">'+cur[0].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-cur">'+cur[1].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-cur">'+cur[2].toFixed(2)+'</span></div>'+
      '<div class="oc-line oc-div"><span class="oc-label">分</span>'+divStr+'</div>';
  }
  return '<div class="odds-combined">'+html+'</div>';
}
function fmtTime(t){if(!t)return'';var m=t.match(/^(?:\d{4}-)?(\d{2})-(\d{2})\s+(\S+)$/);return m?m[1]+'/'+m[2]+' '+m[3]:t;}

function applyFilters(){
  var dateVal = document.getElementById('dateFilter').value;
  var srcVal = document.getElementById('sourceFilter').value;
  var sortVal = document.getElementById('sortBy').value;
  var filtered = allMatches.filter(function(m){
    if(dateVal!=='all' && m.date!==dateVal) return false;
    if(srcVal!=='all' && m.source!==srcVal) return false;
    return true;
  });
  if(sortVal==='time') filtered.sort(function(a,b){return a.match_time.localeCompare(b.match_time)});
  else if(sortVal==='odds') filtered.sort(function(a,b){return b.odds_win-a.odds_win});
  renderTable(filtered);
}

function renderTable(matches){
  var tbody = document.getElementById('matchBody');
  tbody.innerHTML = '';
  matches.forEach(function(m){
    var tr = document.createElement('tr');
    var hc=m.hit&&m.hit.indexOf('✓')>-1?'hit-yes':m.hit==='✘'?'hit-no':'';
    tr.innerHTML =
      '<td>'+fmtTime(m.match_time)+'</td>'+
      '<td><span class="tag tag-'+m.source+'">'+(m.event||m.source)+'</span></td>'+
      '<td class="team-name">'+m.home_team+'</td>'+
      '<td class="score-cell">'+(m.score||'-')+'</td>'+
      '<td class="team-name">'+m.away_team+'</td>'+
      '<td><span class="'+dirClass(m.prediction)+'">'+dirText(m.prediction)+'</span></td>'+
      '<td class="'+hc+'">'+(m.hit||'')+'</td>'+
      '<td class="odds-cell">'+renderOdds(m.comparison, m.hkjc_comparison)+'</td>'+
      '<td class="odds-cell"><div>模型: <span class="odds-val odds-w">'+fmtPct(m.model_win)+'</span> <span class="odds-val odds-d">'+fmtPct(m.model_draw)+'</span> <span class="odds-val odds-l">'+fmtPct(m.model_loss)+'</span></div><div style="margin-top:3px">LGBM: <span class="odds-val odds-w">'+fmtPct(m.lgbm_win)+'</span> <span class="odds-val odds-d">'+fmtPct(m.lgbm_draw)+'</span> <span class="odds-val odds-l">'+fmtPct(m.lgbm_loss)+'</span></div></td>';
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
    card.innerHTML = '<div class="stat-val">'+ds.count+'</div><div class="stat-label">'+ds.date+'</div>';
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
    document.getElementById('hitRate').textContent = '🎯 '+data.hit_count+'/'+data.total_scored+' ('+fmtPct(data.hit_rate)+')';
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
