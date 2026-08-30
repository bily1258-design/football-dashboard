// script.js — 足彩价值投注看板 v5
var allData = null;
var allMatches = [];

function fmtOdds(v){return v>0?v.toFixed(2):'-'}
function fmtPct(v){return (v*100).toFixed(1)+'%'}
function fmtPctSign(v){return v===0?'0%':(v>0?'+':'')+v.toFixed(1)+'%'}
function fmtTime(t){if(!t)return'';var m=t.match(/^(?:\d{4}-)?(\d{2})-(\d{2})\s+(\S+)$/);return m?m[1]+'/'+m[2]+' '+m[3]:t;}
function dirClass(d){return d==='home'?'dir-home':d==='draw'?'dir-draw':d==='away'?'dir-away':'dir-wait'}
function dirText(d){return d==='home'?'主胜':d==='draw'?'平局':d==='away'?'客胜':'观望'}
function dirZh(d){return d==='home'?'主':d==='draw'?'平':d==='away'?'客':'?'}
function ahDir(m){
  if(m.ah_home_covers_prob==null)return'';
  var d=m.ah_home_covers_prob>m.ah_away_covers_prob
    ?(m.ah_home_covers_prob>m.ah_push_prob?'上':'走')
    :(m.ah_away_covers_prob>m.ah_push_prob?'下':'走');
  var sc=m.score?m.score.split('-'):null;
  if(sc&&sc.length===2){
    var sh=parseInt(sc[0]),sa=parseInt(sc[1]);
    var hc=m.ah_handicap||m.ah_open_handicap||0;
    if(!isNaN(sh)&&!isNaN(sa)){
      var net=sh+hc-sa;
      var upperWon=hc<=0?net>0:net<0;
      if(d==='上') return d+(upperWon?'✔':(net===0?'走':'✘'));
      return d+(!upperWon?'✔':(net===0?'走':'✘'));
    }
  }
  return d;
}
function renderWarning(w){
  if(!w)return'';
  var h='<span class="warn-badge">';
  if(w.indexOf('🚩')>-1) h+='<span class="warn-trap" title="热门降水+冷门大涨: 可能分歧陷阱">🚩</span>';
  if(w.indexOf('⚠️')>-1) h+='<span class="warn-uncert" title="模型犹豫或LGBM低置信">⚠️</span>';
  return h+'</span>';
}
// ⚡高权重避雷
function renderHighWeight(m){
  if(!m.importance_weight || m.importance_weight<1.14) return'';
  var ts=[m.ts_win||0,m.ts_draw||0,m.ts_loss||0];
  var tsi=ts.indexOf(Math.max.apply(null,ts));
  var dirs=['home','draw','away'];
  var sameDir=(m.model_prediction===dirs[tsi]);
  if(!sameDir) return'';
  return'<span class="hw-warn" title="⚡'+m.importance_weight.toFixed(2)+' 且 模型与TS同向: 历史命中率仅35%">⚠️⚡避雷</span>';
}
function renderForm(s){
  if(!s||!s.home_recent||s.home_recent.length===0)return'';
  var hf=s.home_recent, af=s.away_recent;
  function icons(arr){return arr.map(function(r){var c=r.result;return c==='胜'?'🟢':c==='负'?'🔴':c==='平'?'🟡':'⚪';}).join('');}
  return '<div class="form-icons"><span class="fi-label">主</span>'+icons(hf)+'</div><div class="form-icons"><span class="fi-label">客</span>'+icons(af)+'</div>';
}
// ⚠️ 相似历史 / 总进球数 已禁用 (2026-08-30)
function confDot(c){
  if(c==null)return'';
  var color=c>0.47?'#4caf50':c>=0.40?'#ffc107':'#f44336';
  var label=c>0.47?'高':c>=0.40?'中':'低';
  return'<span class="conf-dot" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:'+color+';margin-right:4px" title="置信度'+fmtPct(c)+'"></span>';
}

function renderValueBadge(bv){
  if(!bv||bv.ev<=0)return'<span class="vb-none">无价值</span>';
  var cls=bv.ev>0.5?'vb-hot':bv.ev>0.2?'vb-warm':'vb-cool';
  return'<span class="vb-badge '+cls+'" title="EV '+fmtPct(bv.ev)+' Kelly '+fmtPct(bv.kelly)+'">'+'EV'+fmtPct(bv.ev)+'</span>';
}

function getBets(){return JSON.parse(localStorage.getItem('football_bets')||'[]')}
function saveBets(b){localStorage.setItem('football_bets',JSON.stringify(b))}
function addBet(m,fid,outcome,odds,stake){
  var bets=getBets();
  bets.push({id:fid+'_'+Date.now(), fid:fid, match:m.home_team+'vs'+m.away_team, time:m.match_time, outcome:outcome, odds:odds, stake:stake, placed:new Date().toISOString(), result:null, profit:0});
  saveBets(bets);renderBetSummary();
}
function removeBet(id){saveBets(getBets().filter(function(b){return b.id!==id}));renderBetSummary();}
function renderBetSummary(){
  var el=document.getElementById('betSummary'),bets=getBets();
  if(!el||bets.length===0){if(el)el.innerHTML='<div class="bs-empty">暂无投注记录</div>';return;}
  var total=bets.length, settled=bets.filter(function(b){return b.result!==null}), wins=settled.filter(function(b){return b.profit>0});
  var totalStake=0,totalProfit=0;
  bets.forEach(function(b){totalStake+=b.stake;totalProfit+=b.profit});
  var roi=totalStake>0?(totalProfit/totalStake*100):0;
  document.getElementById('betSummary').innerHTML=
    '<div class="bs-row"><span class="bs-label">总投注</span><span class="bs-val">'+total+'</span></div>'+
    '<div class="bs-row"><span class="bs-label">已结算</span><span class="bs-val">'+settled.length+' 场 ('+wins.length+' 胜)</span></div>'+
    '<div class="bs-row"><span class="bs-label">总投入</span><span class="bs-val">'+totalStake.toFixed(2)+'</span></div>'+
    '<div class="bs-row"><span class="bs-label">盈亏</span><span class="bs-val '+(totalProfit>=0?'bs-profit':'bs-loss')+'">'+ (totalProfit>=0?'+':'')+totalProfit.toFixed(2)+'</span></div>'+
    '<div class="bs-row"><span class="bs-label">ROI</span><span class="bs-val '+(roi>=0?'bs-profit':'bs-loss')+'">'+ (roi>=0?'+':'')+roi.toFixed(1)+'%</span></div>';
}
function showBetModal(m,bv){
  var modal=document.getElementById('betModal');
  document.getElementById('bmMatch').textContent=m.home_team+' vs '+m.away_team;
  document.getElementById('bmOutcome').textContent=dirZh(bv.outcome);
  document.getElementById('bmOdds').textContent=bv.odds.toFixed(2);
  document.getElementById('bmKelly').textContent=fmtPct(bv.kelly);
  document.getElementById('bmStake').value=(bv.kelly*100).toFixed(1);
  modal.style.display='block';modal.dataset.fid=m.fid;modal.dataset.outcome=bv.outcome;modal.dataset.odds=bv.odds;
}
function closeBetModal(){document.getElementById('betModal').style.display='none';}
function confirmBet(){
  var modal=document.getElementById('betModal');
  var fid=modal.dataset.fid, odds=parseFloat(modal.dataset.odds);
  var stake=parseFloat(document.getElementById('bmStake').value);
  if(!stake||stake<=0){alert('请输入有效注额');return;}
  var m=allMatches.find(function(x){return x.fid==fid});
  if(!m){alert('比赛数据丢失');return;}
  addBet(m,fid,modal.dataset.outcome,odds,stake);closeBetModal();renderBetTable();
}
function renderBetTable(){
  var el=document.getElementById('betTableBody');if(!el)return;
  var bets=getBets().sort(function(a,b){return b.placed.localeCompare(a.placed)});
  el.innerHTML=bets.map(function(b){
    var resultStr=b.result===null?'<span class="bt-pending">待定</span>':(b.result?'<span class="bt-win">赢</span>':'<span class="bt-loss">输</span>');
    var profitStr=b.result===null?'--':(b.profit>=0?'+'+b.profit.toFixed(2):b.profit.toFixed(2));
    return'<tr><td>'+b.time+'</td><td class="team-name">'+b.match+'</td><td>'+dirZh(b.outcome)+'</td><td>'+b.odds.toFixed(2)+'</td><td>'+b.stake.toFixed(1)+'</td><td>'+resultStr+'</td><td>'+profitStr+'</td><td><button class="bt-del" onclick="removeBet(\''+b.id+'\')">✕</button></td></tr>';
  }).join('');
  renderBetSummary();
}

var showWarnedOnly = false, showValueOnly = false, showImportantOnly = false;
function toggleWarnFilter(){showWarnedOnly=!showWarnedOnly;document.getElementById('warnToggle').textContent=showWarnedOnly?'⚠️ 仅标记':'⚠️ 全部';document.getElementById('warnToggle').className='warn-filter-btn'+(showWarnedOnly?' active':'');applyFilters();}
function toggleValueFilter(){showValueOnly=!showValueOnly;document.getElementById('valueToggle').textContent=showValueOnly?'💰 仅价值':'💰 全部';document.getElementById('valueToggle').className='warn-filter-btn'+(showValueOnly?' active':'');applyFilters();}
function toggleImportantFilter(){showImportantOnly=!showImportantOnly;document.getElementById('impToggle').textContent=showImportantOnly?'⚡ 仅重要':'⚡ 全部';document.getElementById('impToggle').className='warn-filter-btn'+(showImportantOnly?' active':'');applyFilters();}

function applyFilters(){
  var dateVal=document.getElementById('dateFilter').value, srcVal=document.getElementById('sourceFilter').value, dirVal=document.getElementById('dirFilter').value, sortVal=document.getElementById('sortBy').value;
  var filtered=allMatches.filter(function(m){
    if(dateVal!=='all'&&m.date!==dateVal)return false;
    if(srcVal!=='all'&&m.source.indexOf(srcVal)===-1)return false;
    if(dirVal!=='all'){var comb=(m.model_prediction||'')+'-'+(m.lgbm_prediction||'');if(comb!==dirVal)return false;}
    if(showWarnedOnly&&!m.warning)return false;
    if(showValueOnly&&(!m.best_value||m.best_value.ev<=0.05))return false;
    if(showImportantOnly&&m.low_priority)return false;
    return true;
  });
  if(sortVal==='time')filtered.sort(function(a,b){return a.match_time.localeCompare(b.match_time)});
  else if(sortVal==='odds')filtered.sort(function(a,b){return b.odds_win-a.odds_win});
  renderTable(filtered);
}

function renderTable(matches){
  var tbody=document.getElementById('matchBody');tbody.innerHTML='';var CHUNK=150,idx=0;
  function renderRow(m){
    var tr=document.createElement('tr');if(m.low_priority)tr.className='lp-row';
    var hc=m.hit&&m.hit.indexOf('✓')>-1?'hit-yes':m.hit==='✘'?'hit-no':'';
    var tsRow='';
    if(m.ts_win!=null){
      tsRow='<div style="margin-top:3px;border-top:1px dashed #888;padding-top:2px;font-size:12px"><span style="color:#999">TS: </span><span class="odds-val odds-w">'+fmtPct(m.ts_win)+'</span> <span class="odds-val odds-d">'+fmtPct(m.ts_draw)+'</span> <span class="odds-val odds-l">'+fmtPct(m.ts_loss)+'</span></div>';
    }
    var bv=m.best_value;
    var vbHtml=bv&&bv.ev>0.05?'<span class="vb-badge '+(bv.ev>0.5?'vb-hot':bv.ev>0.2?'vb-warm':'vb-cool')+'" data-fid="'+m.fid+'" title="EV '+fmtPct(bv.ev)+' Kelly '+fmtPct(bv.kelly)+'">💰 '+fmtPct(bv.ev)+'</span>':'<span class="vb-none">-</span>';
    tr.innerHTML=
      '<td>'+fmtTime(m.match_time)+'</td>' +
      '<td><span class="tag tag-'+m.source+'">'+(m.event||m.source)+'</span></td>' +
      '<td class="team-name">'+m.home_team+'</td>' +
      '<td class="score-cell"><span>'+(m.score||(m.postponed?'推迟':'-'))+'</span></td>' +
      '<td class="team-name">'+m.away_team+'</td>' +
      '<td><span class="'+dirClass(m.lgbm_prediction)+'">'+dirText(m.lgbm_prediction)+'</span><span style="font-size:11px;color:#999">'+dirText(m.model_prediction)+(m.ah_home_covers_prob!=null?' <span class="ah-pred-inline">('+m.ah_home_covers_prob>m.ah_away_covers_prob?(m.ah_home_covers_prob>m.ah_push_prob?'上':'走'):(m.ah_away_covers_prob>m.ah_push_prob?'下':'走') +')</span>':'')+'</span><span class="weight-badge" title="权重 '+m.importance_weight.toFixed(2)+'">⚡'+m.importance_weight.toFixed(2)+'</span>'+renderHighWeight(m)+renderWarning(m.warning)+'<br>'+vbHtml+'</td>' +
      '<td class="'+hc+'">'+(m.hit||'')+(ahDir(m)?' <span class="ah-hit-dir">'+ahDir(m)+'</span>':'')+'</td>' +
      '<td class="odds-cell">'+renderOdds(m.comparison, m.pin_comparison, m)+'</td>' +
      '<td class="odds-cell" style="font-size:12px"><div>模型: <span class="odds-val odds-w">'+fmtPct(m.model_win)+'</span> <span class="odds-val odds-d">'+fmtPct(m.model_draw)+'</span> <span class="odds-val odds-l">'+fmtPct(m.model_loss)+'</span></div><div style="margin-top:3px">'+confDot(m.lgbm_confidence)+'LGBM: <span class="odds-val odds-w">'+fmtPct(m.lgbm_win)+'</span> <span class="odds-val odds-d">'+fmtPct(m.lgbm_draw)+'</span> <span class="odds-val odds-l">'+fmtPct(m.lgbm_loss)+'</span><div style="margin-top:2px"><span class="oc-label" style="margin-right:4px">分</span><span class="'+((m.model_win-m.lgbm_win)<-0.003?'oc-pct-down':(m.model_win-m.lgbm_win)>0.003?'oc-pct-up':'oc-pct-flat')+'">'+fmtPctSign((m.model_win-m.lgbm_win)*100)+'</span> <span class="'+((m.model_draw-m.lgbm_draw)<-0.003?'oc-pct-down':(m.model_draw-m.lgbm_draw)>0.003?'oc-pct-up':'oc-pct-flat')+'">'+fmtPctSign((m.model_draw-m.lgbm_draw)*100)+'</span> <span class="'+((m.model_loss-m.lgbm_loss)<-0.003?'oc-pct-down':(m.model_loss-m.lgbm_loss)>0.003?'oc-pct-up':'oc-pct-flat')+'">'+fmtPctSign((m.model_loss-m.lgbm_loss)*100)+'</span></div>'+tsRow+'</div></td>';
    var vbb=tr.querySelector('.vb-badge');if(vbb){(function(match){vbb.onclick=function(){showBetModal(match,match.best_value);}})(m);}
    tbody.appendChild(tr);
  }
  function nextChunk(){var end=Math.min(idx+CHUNK,matches.length);for(;idx<end;idx++)renderRow(matches[idx]);if(idx<matches.length)requestAnimationFrame(nextChunk);else document.getElementById('matchCount').textContent=matches.length+' 场';}
  nextChunk();
}

function renderStats(data){
  var bar=document.getElementById('stats-bar');bar.innerHTML='';
  if(!data.daily_stats)return;
  data.daily_stats.forEach(function(ds){var card=document.createElement('div');card.className='stat-card';card.innerHTML='<div class="stat-val">'+ds.count+'</div><div class="stat-label">'+ds.date+'</div>';bar.appendChild(card);});
}

fetch('data/results_light.json', {cache:'no-cache'})
  .then(function(r){return r.json()})
  .then(function(data){
    allData=data;allMatches=data.matches||[];
    var valueCount=allMatches.filter(function(m){return m.best_value&&m.best_value.ev>0.05}).length;
    document.getElementById('loading').style.display='none';document.getElementById('table-wrap').style.display='block';
    document.getElementById('updateTime').textContent='🕐 '+data.generated_at;
    document.getElementById('dateRange').textContent=data.date_range;
    document.getElementById('matchCount').textContent=data.total_matches+' 场';
    document.getElementById('hitRate').textContent='🎯 '+data.hit_count+'/'+data.total_scored+' ('+fmtPct(data.hit_rate)+')';
    document.getElementById('valueStats').textContent='💰 价值: '+valueCount+' 场';
    var sel=document.getElementById('dateFilter'),recent=(data.daily_stats||[]).slice(0,14);
    recent.forEach(function(ds){var opt=document.createElement('option');opt.value=ds.date;opt.textContent=ds.date+' ('+ds.count+')';sel.appendChild(opt);});
    var today=new Date().toISOString().slice(0,10);
    if(sel.querySelector('option[value="'+today+'"]'))sel.value=today;else if(recent.length>0)sel.value=recent[0].date;
    renderStats(data);renderBetSummary();renderBetTable();
    var bets=getBets();if(bets.length>0)document.getElementById('betTracker').style.display='block';applyFilters();
  })
  .catch(function(err){document.getElementById('loading').innerHTML='❌ 数据加载失败，请刷新重试<br><small>'+err.message+'</small>';});

function renderOdds(c,p,m){
  var html='';
  if(c&&c.current){
    var o=c.open,cur=c.current,d=c.div_pct;
    var srcLabel=c.source==='pinnacle'?'平博':'马会';
    var divStr='';
    for(var i=0;i<3;i++){
      var cls=d[i]<-0.3?'oc-pct-down':(d[i]>0.3?'oc-pct-up':'oc-pct-flat');
      divStr+='<span class="'+cls+'">'+fmtPctSign(d[i])+'</span>'+(i<2?'<span class="oc-sep">|</span>':'');
    }
    html+='<div class="oc-source oc-source-hkjc">'+srcLabel+'</div><div class="oc-line"><span class="oc-label">初</span><span class="oc-open">'+o[0].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-open">'+o[1].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-open">'+o[2].toFixed(2)+'</span></div><div class="oc-line"><span class="oc-label">即</span><span class="oc-cur">'+cur[0].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-cur">'+cur[1].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-cur">'+cur[2].toFixed(2)+'</span></div><div class="oc-line oc-div"><span class="oc-label">分</span>'+divStr+'</div>';
  }else{html+='<span class="odds-val odds-w">--</span>';}
  if(p&&p.current){
    var o=p.open,cur=p.current,d=p.div_pct;
    var srcLabel=p.source==='pinnacle'?'平博':'马会';
    var divStr='';
    for(var i=0;i<3;i++){
      var cls=d[i]<-0.3?'oc-pct-down':(d[i]>0.3?'oc-pct-up':'oc-pct-flat');
      divStr+='<span class="'+cls+'">'+fmtPctSign(d[i])+'</span>'+(i<2?'<span class="oc-sep">|</span>':'');
    }
    html+='<div class="oc-sep-line"></div><div class="oc-source">'+srcLabel+'</div><div class="oc-line"><span class="oc-label">初</span><span class="oc-open">'+o[0].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-open">'+o[1].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-open">'+o[2].toFixed(2)+'</span></div><div class="oc-line"><span class="oc-label">即</span><span class="oc-cur">'+cur[0].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-cur">'+cur[1].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-cur">'+cur[2].toFixed(2)+'</span></div><div class="oc-line oc-div"><span class="oc-label">分</span>'+divStr+'</div>';
  }
  if(m&&m.ah_home!=null){
    html+='<div class="oc-sep-line"></div><div class="oc-line"><span class="oc-label">亚初</span><span class="oc-open">'+m.ah_open_home.toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-open">'+(m.ah_open_handicap_text||m.ah_open_handicap.toFixed(2))+'</span><span class="oc-sep">/</span><span class="oc-open">'+m.ah_open_away.toFixed(2)+'</span></div><div class="oc-line"><span class="oc-label">亚即</span><span class="oc-cur">'+m.ah_home.toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-cur">'+(m.ah_handicap_text||m.ah_handicap.toFixed(2))+'</span><span class="oc-sep">/</span><span class="oc-cur">'+m.ah_away.toFixed(2)+'</span></div>';
  }
  return'<div class="odds-combined">'+html+'</div>';
}