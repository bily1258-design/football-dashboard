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
function noText(m){if(!m.beidan_no&&!m.jingcai_no)return '';return (m.beidan_no?'北单'+m.beidan_no:'')+(m.jingcai_no?(m.beidan_no?'<br>':'')+m.jingcai_no:'')}
function bdLine(m){ // 北单让球行: 「过」(胜负过关)优先, 「让」(让球胜平负)兜底; 正=主队受让, 负=主队让球
  var v=m.ahbd_cur_handicap;if(v==null||v==='')v=m.ahbd_open_handicap;
  if(v==null||v==='')return null;
  var f=parseFloat(v);return isNaN(f)?null:f;
}
function bdPick(m){ // 北单「过」盘口方向: 上=主队过关 下=客队过关 走=走盘
  var ph=m.ahbd_home_covers_prob,pa=m.ahbd_away_covers_prob,pp=m.ahbd_push_prob||0;
  if(ph==null||pa==null)return'';
  return ph>pa?(ph>pp?'上':'走'):(pa>pp?'下':'走');
}
function ahDir(m){
  // 口径=北单胜负过关「过」行 (500.com/bjdcsf, 2way 半盘无走盘); 方向取模型赢盘概率(泊松+战绩+排名+对赛)
  // 命中=用比分+北单让球独立判, 不依赖 m.hit (那是比赛预测命中)
  var d=bdPick(m);if(!d)return'';
  var hc=bdLine(m);if(hc==null)return d;
  var sc=m.score?String(m.score).split('-'):null;
  if(sc&&sc.length===2){
    var sh=parseInt(sc[0]),sa=parseInt(sc[1]);
    if(!isNaN(sh)&&!isNaN(sa)){
      var net=sh+hc-sa; // 主队过关 ⟺ 净胜+让球>0 (hc正=主受让)
      if(net===0)return d+'走';
      if(d==='走')return d;
      return d+(((d==='上')===(net>0))?'✔':'✘');
    }
  }
  return d;
}
function sigTier(w){
  // 统一信号 (2026-09-24 起 ai_analysis 写 🟢/🟡/🔴): 兼容历史数据里的 🚩/⚠️
  if(!w) return '';
  if(w.indexOf('🟢')>-1 || w.indexOf('🚩')>-1) return 'strong';
  if(w.indexOf('🟡')>-1) return 'mid';
  if(w.indexOf('🔴')>-1 || w.indexOf('⚠️')>-1) return 'weak';
  return '';
}
var SIG_META={
  strong:['sig-strong','🟢强跟','🟢强跟: LGBM≥53% 且 模型同向≥40% — 实测命中 90.5% (n=63)'],
  mid:['sig-mid','🟡中性','🟡中性: LGBM 40~53% — 实测命中 61.4% (n=2109)'],
  weak:['sig-weak','🔴避雷','🔴避雷: LGBM<40% — 实测命中 41.8% (n=2592); 主推方向赔率<1.5 为硬雷(命中 53.3% vs 隐含 71.5%, n=75)']
};
// 2026-09-24: 三记号合并为 1 个信号, 并复用看板原有「LGBM 前置圆点」作为唯一位点(不再另加徽章)
// 依据: 实测 n=4764 完赛 —— 🟢90.5% / 🟡61.4% / 🔴41.8%; 旧 ⚡ 1108 场 48.3%·ROI-7.0% ≈ 价格, 无独立信息
function renderForm(s){
  if(!s||!s.home_recent||s.home_recent.length===0)return'';
  var hf=s.home_recent, af=s.away_recent;
  function icons(arr){
    return arr.map(function(r){
      var c=r.result;
      return c==='胜'?'🟢':c==='负'?'🔴':c==='平'?'🟡':'⚪';
    }).join('');
  }
  return '<div class="form-icons"><span class="fi-label">主</span>'+icons(hf)+'</div><div class="form-icons"><span class="fi-label">客</span>'+icons(af)+'</div>';
}
function renderSimilarMatches(m){
  if(!m.similar_matches || m.similar_matches.length===0) return '-';
  var items = [];
  var count = Math.min(m.similar_matches.length, 3);
  for(var i=0;i<count;i++){
    var s = m.similar_matches[i];
    var scoreStr = s.score ? ' '+s.score : '';
    items.push(
      '<div class="sim-item">'+
        '<span class="sim-teams">'+s.home_team+' vs '+s.away_team+'</span>'+
        '<span class="sim-score">'+scoreStr+'</span>'+
        '<span class="sim-pct">'+(s.similarity*100).toFixed(0)+'%</span>'+
      '</div>'
    );
  }
  var html = '<div class="sim-list">'+items.join('')+'</div>';
  // 总进球TOP3预测
  if(m.total_goals_top3 && m.total_goals_top3.length>0){
    var goals = [];
    for(var i=0;i<Math.min(m.total_goals_top3.length,3);i++){
      var g = m.total_goals_top3[i];
      goals.push(g.total_goals+'球'+(g.prob*100).toFixed(0)+'%');
    }
    html += '<div class="sim-goals">⚽ '+goals.join(' ')+'</div>';
  }
  return html;
}
function matchTier(m){
  // 统一信号取档: 优先读数据里的 warning(🟢/🟡/🔴, 兼容历史 🚩/⚠️), 缺失时按同一口径现算
  var t=sigTier(m.warning);
  if(t) return t;
  var l=[m.lgbm_win||0,m.lgbm_draw||0,m.lgbm_loss||0];
  var mx=Math.max.apply(null,l), i=l.indexOf(mx);
  var mv=[m.model_win||0,m.model_draw||0,m.model_loss||0][i];
  if(mx>=0.53 && mv>=0.40) return 'strong';
  return mx>=0.40?'mid':'weak';
}
function sigDot(m){
  // 看板唯一位点: LGBM 前置圆点 = 合并后的 1 个信号 (2026-09-24)
  if(m.lgbm_confidence==null)return'';
  var t=matchTier(m); if(!t)return'';
  var color={strong:'#4caf50',mid:'#ffc107',weak:'#f44336'}[t];
  var sm=SIG_META[t];
  return'<span class="conf-dot" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:'+color+';margin-right:4px" title="'+sm[2]+' | LGBM 置信 '+fmtPct(m.lgbm_confidence)+'"></span>';
}

// ─── 价值投注 ────────────────────────────────
function renderValueBadge(bv){
  if(!bv||bv.ev<=0)return'<span class="vb-none">无价值</span>';
  var cls=bv.ev>0.5?'vb-hot':bv.ev>0.2?'vb-warm':'vb-cool';
  return'<span class="vb-badge '+cls+'" title="EV '+fmtPct(bv.ev)+' Kelly '+fmtPct(bv.kelly)+'">'+'EV'+fmtPct(bv.ev)+'</span>';
}
function renderValueDetail(m){
  var vbs=m.value_bets||[];
  if(!vbs.length)return'<span class="vb-none">无赔率</span>';
  var html='<div class="vb-detail">';
  vbs.forEach(function(v){
    var cls=v.ev>0?'vb-pos':'vb-neg';
    html+='<div class="vb-item '+cls+'">'+
      '<span class="vb-src">'+v.source+'</span>'+
      '<span class="vb-outcome">'+dirZh(v.outcome)+'</span>'+
      '<span class="vb-odds">'+v.odds.toFixed(2)+'</span>'+
      '<span class="vb-ev">'+fmtPct(v.ev)+'</span>'+
      (v.kelly>0?'<span class="vb-kelly">K'+fmtPct(v.kelly)+'</span>':'')+
      '</div>';
  });
  return html+'</div>';
}

// ─── 投注追踪器 (localStorage) ──────────────
function getBets(){return JSON.parse(localStorage.getItem('football_bets')||'[]')}
function saveBets(b){localStorage.setItem('football_bets',JSON.stringify(b))}
function addBet(m,fid,outcome,odds,stake){
  var bets=getBets();
  bets.push({
    id:fid+'_'+Date.now(), fid:fid, match:m.home_team+'vs'+m.away_team,
    time:m.match_time, outcome:outcome, odds:odds, stake:stake,
    placed:new Date().toISOString(), result:null, profit:0
  });
  saveBets(bets);
  renderBetSummary();
}
function removeBet(id){
  var bets=getBets().filter(function(b){return b.id!==id});
  saveBets(bets);
  renderBetSummary();
}
function renderBetSummary(){
  var el=document.getElementById('betSummary');
  if(!el)return;
  var bets=getBets();
  var total=bets.length;
  var settled=bets.filter(function(b){return b.result!==null});
  var wins=settled.filter(function(b){return b.profit>0});
  var totalStake=0,totalProfit=0;
  bets.forEach(function(b){totalStake+=b.stake;totalProfit+=b.profit});
  var roi=totalStake>0?(totalProfit/totalStake*100):0;
  if(total===0){
    el.innerHTML='<div class="bs-empty">暂无投注记录</div>';
    return;
  }
  el.innerHTML=
    '<div class="bs-row"><span class="bs-label">总投注</span><span class="bs-val">'+total+'</span></div>'+
    '<div class="bs-row"><span class="bs-label">已结算</span><span class="bs-val">'+settled.length+' (胜'+wins.length+')</span></div>'+
    '<div class="bs-row"><span class="bs-label">总投入</span><span class="bs-val">'+totalStake.toFixed(2)+'</span></div>'+
    '<div class="bs-row"><span class="bs-label">盈亏</span><span class="bs-val '+(totalProfit>=0?'bs-profit':'bs-loss')+'\">'+(totalProfit>=0?'+':'')+totalProfit.toFixed(2)+'</span></div>'+
    '<div class="bs-row"><span class="bs-label">ROI</span><span class="bs-val '+(roi>=0?'bs-profit':'bs-loss')+'\">'+(roi>=0?'+':'')+roi.toFixed(1)+'%</span></div>';
}
function showBetModal(m,bv){
  var outcome=bv.outcome;
  var odds=bv.odds;
  var kelly=bv.kelly;
  var modal=document.getElementById('betModal');
  document.getElementById('bmMatch').textContent=m.home_team+' vs '+m.away_team;
  document.getElementById('bmOutcome').textContent=dirZh(outcome);
  document.getElementById('bmOdds').textContent=odds.toFixed(2);
  document.getElementById('bmKelly').textContent=fmtPct(kelly);
  document.getElementById('bmStake').value=(kelly*100).toFixed(1); // 预设Kelly
  modal.style.display='block';
  modal.dataset.fid=m.fid;
  modal.dataset.outcome=outcome;
  modal.dataset.odds=odds;
}
function closeBetModal(){
  document.getElementById('betModal').style.display='none';
}
function confirmBet(){
  var modal=document.getElementById('betModal');
  var fid=modal.dataset.fid;
  var outcome=modal.dataset.outcome;
  var odds=parseFloat(modal.dataset.odds);
  var stake=parseFloat(document.getElementById('bmStake').value);
  if(!stake||stake<=0){alert('请输入有效注额');return;}
  var m=allMatches.find(function(x){return x.fid==fid});
  if(!m){alert('比赛数据丢失');return;}
  addBet(m,fid,outcome,odds,stake);
  closeBetModal();
  renderBetTable();
}
function renderBetTable(){
  var el=document.getElementById('betTableBody');
  if(!el)return;
  var bets=getBets().sort(function(a,b){return b.placed.localeCompare(a.placed)});
  el.innerHTML=bets.map(function(b){
    var resultStr=b.result===null?'<span class="bt-pending">待定</span>':(b.result?'<span class="bt-win">赢</span>':'<span class="bt-loss">输</span>');
    var profitStr=b.result===null?'--':(b.profit>=0?'+'+b.profit.toFixed(2):b.profit.toFixed(2));
    return '<tr>'+
      '<td>'+b.time+'</td>'+
      '<td class="team-name">'+b.match+'</td>'+
      '<td>'+dirZh(b.outcome)+'</td>'+
      '<td>'+b.odds.toFixed(2)+'</td>'+
      '<td>'+b.stake.toFixed(1)+'</td>'+
      '<td>'+resultStr+'</td>'+
      '<td>'+profitStr+'</td>'+
      '<td><button class="bt-del" onclick="removeBet(\''+b.id+'\')">✕</button></td>'+
      '</tr>';
  }).join('');
  renderBetSummary();
}

// ─── 过滤器 ───────────────────────────────
var showWarnedOnly = false;
var showValueOnly = false;
var showImportantOnly = false;
function toggleWarnFilter(){
  showWarnedOnly = !showWarnedOnly;
  document.getElementById('warnToggle').textContent = showWarnedOnly?'🔴 仅避雷':'🔴 全部';
  document.getElementById('warnToggle').className = 'warn-filter-btn'+(showWarnedOnly?' active':'');
  applyFilters();
}
function toggleValueFilter(){
  showValueOnly = !showValueOnly;
  document.getElementById('valueToggle').textContent = showValueOnly?'💰 仅价值':'💰 全部';
  document.getElementById('valueToggle').className = 'warn-filter-btn'+(showValueOnly?' active':'');
  applyFilters();
}
function toggleImportantFilter(){
  showImportantOnly = !showImportantOnly;
  document.getElementById('impToggle').textContent = showImportantOnly?'🟢 仅强跟':'🟢 全部';
  document.getElementById('impToggle').className = 'warn-filter-btn'+(showImportantOnly?' active':'');
  applyFilters();
}

function applyFilters(){
  var dateVal = document.getElementById('dateFilter').value;
  var srcVal = document.getElementById('sourceFilter').value;
  var dirVal = document.getElementById('dirFilter').value;
  var sortVal = document.getElementById('sortBy').value;
  var filtered = allMatches.filter(function(m){
    if(dateVal!=='all' && m.date!==dateVal) return false;
    if(srcVal!=='all' && m.source.indexOf(srcVal)===-1) return false;
    if(dirVal!=='all'){
      var comb = (m.model_prediction||'')+'-'+(m.lgbm_prediction||'');
      if(comb!==dirVal) return false;
    }
    if(showWarnedOnly && matchTier(m)!=='weak') return false;
    if(showValueOnly && (!m.best_value||m.best_value.ev<=0.05)) return false;
    if(showImportantOnly && matchTier(m)!=='strong') return false;
    return true;
  });
  if(sortVal==='time') filtered.sort(function(a,b){return a.match_time.localeCompare(b.match_time)});
  else if(sortVal==='odds') filtered.sort(function(a,b){return b.odds_win-a.odds_win});
  renderTable(filtered);
}

function renderTable(matches){
  var tbody = document.getElementById('matchBody');
  tbody.innerHTML = '';
  // 分批渲染: 每帧最多 CHUNK 行, 避免一次性插入千行 DOM 阻塞主线程
  var CHUNK = 150;
  var idx = 0;
  function renderRow(m){
    var tr = document.createElement('tr');
    if(m.low_priority) tr.className = 'lp-row';
    var hc=m.hit&&m.hit.indexOf('✓')>-1?'hit-yes':m.hit==='✘'?'hit-no':'';
    // 命中列显示: 去掉来源前缀(L/M·) → 上行=方向命中, 下行=「过」盘口命中
    var hitTxt=(m.hit||'').replace(/^[LM]+·/,'');
    var ahTxt=ahDir(m);
    var tsRow = '';
    if(m.ts_win != null){
      tsRow = '<div style="margin-top:3px;border-top:1px dashed #888;padding-top:2px;font-size:12px">'+
        '<span style="color:#999">TS: </span>'+
        '<span class="odds-val odds-w">'+fmtPct(m.ts_win)+'</span> '+
        '<span class="odds-val odds-d">'+fmtPct(m.ts_draw)+'</span> '+
        '<span class="odds-val odds-l">'+fmtPct(m.ts_loss)+'</span></div>';
    }
    var bv = m.best_value;
    var vbHtml = bv && bv.ev > 0.05 ?
      '<span class="vb-badge '+(bv.ev>0.5?'vb-hot':bv.ev>0.2?'vb-warm':'vb-cool')+
      '" data-fid="'+m.fid+'" title="EV '+fmtPct(bv.ev)+' Kelly '+fmtPct(bv.kelly)+'">'+
      '💰 '+fmtPct(bv.ev)+'</span>' :
      '<span class="vb-none">-</span>';
    tr.innerHTML =
      '<td>'+fmtTime(m.match_time)+'</td>'+
      '<td><span class="tag tag-'+m.source+'">'+(m.event||m.source)+'</span></td>'+
      '<td class="no-cell">'+noText(m)+'</td>'+
      '<td class="team-name">'+m.home_team+'</td>'+
      '<td class="score-cell"><span>'+(m.score||(m.postponed?'推迟':'-'))+'</span></td>'+
      '<td class="team-name">'+m.away_team+'</td>'+
      '<td class="sim-cell">'+renderSimilarMatches(m)+'</td>'+
      '<td><span class="'+dirClass(m.lgbm_prediction)+'">'+dirText(m.lgbm_prediction)+'</span> <span style="font-size:11px;color:#999">'+dirText(m.model_prediction)+(bdPick(m)?' <span class="ah-pred-inline">('+bdPick(m)+')</span>':'')+'</span><br><span class="weight-badge" title="权重 '+m.importance_weight.toFixed(2)+'">⚡'+m.importance_weight.toFixed(2)+'</span><br>'+vbHtml+'</td>'+

      '<td class="'+hc+'">'+(hitTxt?hitTxt+'<br>':'')+(ahTxt?'<span class="ah-hit-dir">'+ahTxt+'</span>':'')+'</td>'+
      '<td class="odds-cell">'+renderOdds(m.comparison, m.pin_comparison, m)+'</td>'+
      '<td class="odds-cell" style="font-size:12px"><div>模型: <span class="odds-val odds-w">'+fmtPct(m.model_win)+'</span> <span class="odds-val odds-d">'+fmtPct(m.model_draw)+'</span> <span class="odds-val odds-l">'+fmtPct(m.model_loss)+'</span></div>'+
        '<div style="margin-top:3px">'+sigDot(m)+'LGBM: <span class="odds-val odds-w">'+fmtPct(m.lgbm_win)+'</span> <span class="odds-val odds-d">'+fmtPct(m.lgbm_draw)+'</span> <span class="odds-val odds-l">'+fmtPct(m.lgbm_loss)+'</span>'+
          '<div style="margin-top:2px"><span class="oc-label" style="margin-right:3px">分</span>'+
            '<span class="'+((m.model_win-m.lgbm_win)<-0.003?'oc-pct-down':(m.model_win-m.lgbm_win)>0.003?'oc-pct-up':'oc-pct-flat')+'">'+fmtPctSign((m.model_win-m.lgbm_win)*100)+'</span> '+
            '<span class="'+((m.model_draw-m.lgbm_draw)<-0.003?'oc-pct-down':(m.model_draw-m.lgbm_draw)>0.003?'oc-pct-up':'oc-pct-flat')+'">'+fmtPctSign((m.model_draw-m.lgbm_draw)*100)+'</span> '+
            '<span class="'+((m.model_loss-m.lgbm_loss)<-0.003?'oc-pct-down':(m.model_loss-m.lgbm_loss)>0.003?'oc-pct-up':'oc-pct-flat')+'">'+fmtPctSign((m.model_loss-m.lgbm_loss)*100)+'</span>'+
          '</div>'+
        tsRow+
        '</div></td>'
    ;
    // 绑定点击事件 - 价值标签
    var vbb = tr.querySelector('.vb-badge');
    if(vbb){
      (function(match){
        vbb.onclick=function(){showBetModal(match,match.best_value);};
      })(m);
    }
    tbody.appendChild(tr);
  }
  function nextChunk(){
    var end = Math.min(idx+CHUNK, matches.length);
    for(; idx<end; idx++) renderRow(matches[idx]);
    if(idx < matches.length) requestAnimationFrame(nextChunk);
    else document.getElementById('matchCount').textContent = matches.length+' 场';
  }
  nextChunk();
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

// 加载 (results_light.json: 精简版, 12.7MB→1.6MB; cache:'no-cache' 走 ETag 条件请求, 数据未变 304 秒回)
fetch('data/results_light.json', {cache:'no-cache'})
  .then(function(r){return r.json()})
  .then(function(data){
    allData = data;
    allMatches = data.matches || [];

    // 价值投注统计
    var valueCount = allMatches.filter(function(m){return m.best_value&&m.best_value.ev>0.05}).length;

    document.getElementById('loading').style.display='none';
    document.getElementById('table-wrap').style.display='block';
    document.getElementById('updateTime').textContent = '🕐 '+data.generated_at;
    document.getElementById('dateRange').textContent = data.date_range;
    document.getElementById('matchCount').textContent = data.total_matches+' 场';
    document.getElementById('hitRate').textContent = '🎯 '+data.hit_count+'/'+data.total_scored+' ('+fmtPct(data.hit_rate)+')';
    document.getElementById('valueStats').textContent = '💰 价值: '+valueCount+' 场';

    // 填充日期过滤 — 只列最近 14 天, 减少下拉选项与误选全量
    var sel = document.getElementById('dateFilter');
    var recent = (data.daily_stats||[]).slice(0,14);
    recent.forEach(function(ds){
      var opt = document.createElement('option');
      opt.value = ds.date; opt.textContent = ds.date+' ('+ds.count+')';
      sel.appendChild(opt);
    });
    var today = new Date().toISOString().slice(0,10);
    if (sel.querySelector('option[value="'+today+'"]')) {
      sel.value = today;
    } else if (recent.length>0) {
      // 今天无比赛: 默认选最近有数据的日期 (而非 all 全量渲染)
      sel.value = recent[0].date;
    }
    renderStats(data);
    renderBetSummary();
    renderBetTable();
    // 有投注记录时显示追踪器
    var bets=getBets();
    if(bets.length>0) document.getElementById('betTracker').style.display='block';
    applyFilters();
  })
  .catch(function(err){
    document.getElementById('loading').innerHTML = '❌ 数据加载失败，请刷新重试<br><small>'+err.message+'</small>';
  });

function fmtHcp(h){ // 盘口: 带符号, 去尾零  (+1 / -0.5 / 0)
  if(h === null || h === undefined || h === '') return '';
  var v = Number(h);
  if(isNaN(v)) return String(h);
  var a = Math.abs(v);
  var s = (Math.abs(a - Math.round(a)) < 0.001) ? String(Math.round(a)) : a.toFixed(2).replace(/0$/, '');
  return (v > 0 ? '+' : (v < 0 ? '-' : '')) + s;
}
function spTxt(w){ return (w == null) ? '—' : (w + 1).toFixed(2); } // 水位 -> SP(赔率)
function renderOdds(c, p, m){
  var html = '';
  // 固定顺序: 平博在前, 马会在后
  if(c && c.current && p && p.current && c.source === 'hkjc' && p.source === 'pinnacle'){ var _t = c; c = p; p = _t; }
  // 主赔率（动态标源）
  if(c && c.current){
    var o=c.open, cur=c.current, d=c.div_pct;
    var srcLabel = c.source === 'pinnacle' ? '平博' : '马会';
    var divStr = '';
    for(var i=0;i<3;i++){
      var cls = d[i] < -0.3 ? 'oc-pct-down' : (d[i] > 0.3 ? 'oc-pct-up' : 'oc-pct-flat');
      divStr += '<span class="'+cls+'">'+fmtPctSign(d[i])+'</span>' + (i<2?'<span class="oc-sep">|</span>':'');
    }
    html += '<div class="oc-source oc-source-hkjc">'+srcLabel+'</div>'+
      '<div class="oc-line"><span class="oc-label">初</span><span class="oc-open">'+o[0].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-open">'+o[1].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-open">'+o[2].toFixed(2)+'</span></div>'+
      '<div class="oc-line"><span class="oc-label">即</span><span class="oc-cur">'+cur[0].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-cur">'+cur[1].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-cur">'+cur[2].toFixed(2)+'</span></div>'+
      '<div class="oc-line oc-div"><span class="oc-label">分</span>'+divStr+'</div>';
  } else {
    html += '<span class="odds-val odds-w">--</span>';
  }
  // 备查列（另一家公司）
  if(p && p.current){
    var o=p.open, cur=p.current, d=p.div_pct;
    var srcLabel = p.source === 'pinnacle' ? '平博' : '马会';
    var divStr = '';
    for(var i=0;i<3;i++){
      var cls = d[i] < -0.3 ? 'oc-pct-down' : (d[i] > 0.3 ? 'oc-pct-up' : 'oc-pct-flat');
      divStr += '<span class="'+cls+'">'+fmtPctSign(d[i])+'</span>' + (i<2?'<span class="oc-sep">|</span>':'');
    }
    html += '<div class="oc-sep-line"></div>'+
      '<div class="oc-source">'+srcLabel+'</div>'+
      '<div class="oc-line"><span class="oc-label">初</span><span class="oc-open">'+o[0].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-open">'+o[1].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-open">'+o[2].toFixed(2)+'</span></div>'+
      '<div class="oc-line"><span class="oc-label">即</span><span class="oc-cur">'+cur[0].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-cur">'+cur[1].toFixed(2)+'</span><span class="oc-sep">/</span><span class="oc-cur">'+cur[2].toFixed(2)+'</span></div>'+
      '<div class="oc-line oc-div"><span class="oc-label">分</span>'+divStr+'</div>';
  }
  // 亚盘: 500.com 北单数据 —— 让=让球胜平负(bjdc), 过=胜负过关(bjdcsf)
  // (真盘 ah_* 已去掉: 无北单数据时该场不显示亚盘行, 不兜底)
  if(m && (m.ahbd_cur_home != null || m.ahbd_open_home != null)){
    var L = '';
    if(m.ahbd_open_home != null){
      L += '<div class="oc-line"><span class="oc-label oc-bd-rang" title="北单 让球胜平负">让</span><span class="oc-bd-rang">'+fmtHcp(m.ahbd_open_handicap)+'</span><span class="oc-open"> '+spTxt(m.ahbd_open_home)+'/'+spTxt(m.ahbd_open_push)+'/'+spTxt(m.ahbd_open_away)+'</span></div>';
    }
    if(m.ahbd_cur_home != null){
      L += '<div class="oc-line"><span class="oc-label oc-bd-guo" title="北单 胜负过关">过</span><span class="oc-bd-guo">'+fmtHcp(m.ahbd_cur_handicap)+'</span><span class="oc-cur"> '+spTxt(m.ahbd_cur_home)+'/'+spTxt(m.ahbd_cur_away)+'</span></div>';
    }
    if(L) html += '<div class="oc-sep-line"></div>' + L;
  }
  // 亚盘预测 — 已移至推荐列
  return '<div class="odds-combined">'+html+'</div>';
}
