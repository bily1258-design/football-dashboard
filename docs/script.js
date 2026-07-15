var matches=[],stats={};
function fmtTime(t){
  if(!t)return'-';
  var p=t.split(' ');
  if(p.length<2)return t;
  var d=p[0].split('-'),tm=p[1].split(':');
  return d[1]+'/'+d[2]+' '+tm[0]+':'+tm[1];
}
function fmtPct(v){return(v*100).toFixed(1)+'%'}
function dirClass(v){
  if(v=='home')return'dir-home';
  if(v=='draw')return'dir-draw';
  if(v=='away')return'dir-away';
  return'dir-wait';
}
function dirText(v){
  if(v=='home')return'主胜';
  if(v=='draw')return'平局';
  if(v=='away')return'客胜';
  return'—';
}
function renderOdds(comp,hkjcComp){
  if(!comp&&!hkjcComp)return'-';
  var h='<div class="odds-combined">';
  if(comp){
    var open=comp[0],cur=comp[1];
    var div=comp[2];
    var srcTag=comp[3]==='hkjc'?'<div class="oc-source oc-source-hkjc">HKJC</div>':'<div class="oc-source">Pinnacle</div>';
    h+=srcTag+'<div class="oc-line"><span class="oc-label">初</span><span class="oc-open">'+open.join('/')+'</span></div><div class="oc-line"><span class="oc-label">即</span><span class="oc-cur">'+cur.join('/')+'</span></div>';
    if(div!==null&&div!==undefined){
      var cls='oc-pct-flat',sign='';
      if(div>0){cls='oc-pct-up';sign='↑';}else if(div<0){cls='oc-pct-down';sign='↓';}
      h+='<div class="oc-line"><span class="oc-label">歧</span><span class="oc-div '+cls+'">'+sign+Math.abs(div).toFixed(1)+'%</span></div>';
    }else{
      h+='<div class="oc-line"><span class="oc-label">歧</span><span class="oc-div oc-pct-flat">N/A</span></div>';
    }
  }
  if(hkjcComp&&hkjcComp[0]){
    if(comp)h+='<div class="oc-sep-line"></div>';
    var hopen=hkjcComp[0],hcur=hkjcComp[1];
    var hdiv=hkjcComp[2];
    h+='<div class="oc-source oc-source-hkjc">HKJC</div><div class="oc-line"><span class="oc-label">初</span><span class="oc-open">'+hopen.join('/')+'</span></div><div class="oc-line"><span class="oc-label">即</span><span class="oc-cur">'+hcur.join('/')+'</span></div>';
    if(hdiv!==null&&hdiv!==undefined){
      var hcls='oc-pct-flat',hsign='';
      if(hdiv>0){hcls='oc-pct-up';hsign='↑';}else if(hdiv<0){hcls='oc-pct-down';hsign='↓';}
      h+='<div class="oc-line"><span class="oc-label">歧</span><span class="oc-div '+hcls+'">'+hsign+Math.abs(hdiv).toFixed(1)+'%</span></div>';
    }else{
      h+='<div class="oc-line"><span class="oc-label">歧</span><span class="oc-div oc-pct-flat">N/A</span></div>';
    }
  }
  h+='</div>';
  return h;
}
function renderTable(data){
  var tbody=document.getElementById('matchBody');
  tbody.innerHTML='';
  var mlist=data.matches;
  var filtSrc=document.getElementById('sourceFilter').value;
  if(filtSrc!='all')mlist=mlist.filter(function(m){return m.source==filtSrc;});
  var dateFilt=document.getElementById('dateFilter').value;
  if(dateFilt!='all')mlist=mlist.filter(function(m){return m.date==dateFilt;});
  var sortBy=document.getElementById('sortBy').value;
  if(sortBy=='odds'){mlist=mlist.slice().sort(function(a,b){return(b.odds_win||0)-(a.odds_win||0);});}
  mlist.forEach(function(m){
    var hc=m.hit?'hit-'+(m.hit=='yes'||m.hit===true?'yes':'no'):'';
    var tr=document.createElement('tr');
    tr.innerHTML=
      '<td>'+fmtTime(m.match_time)+'</td>'+
      '<td><span class="tag tag-'+m.source+'">'+(m.event||m.source)+'</span></td>'+
      '<td class="team-name">'+m.home_team+'</td>'+
      '<td class="score-cell">'+(m.score||'-')+'</td>'+
      '<td class="team-name">'+m.away_team+'</td>'+
      '<td><span class="'+dirClass(m.prediction)+'">'+dirText(m.prediction)+'</span></td>'+
      '<td class="'+hc+'">'+(m.hit||'')+'</td>'+
      '<td class="odds-cell">'+renderOdds(m.comparison, m.hkjc_comparison)+'</td>'+
      '<td class="odds-cell"><span class="odds-val odds-w">'+fmtPct(m.model_win)+'</span> <span class="odds-val odds-d">'+fmtPct(m.model_draw)+'</span> <span class="odds-val odds-l">'+fmtPct(m.model_loss)+'</span></td>'+
      '<td class="lgbm-cell"><span class="'+dirClass(m.lgbm_prediction)+'">'+dirText(m.lgbm_prediction)+'</span></td>'+
      '<td class="prob-cell">'+fmtPct(m.lgbm_prediction_prob)+'</td>';
    tbody.appendChild(tr);
  });
  document.getElementById('matchCount').textContent = matches.length+' 场';
}
function renderStats(data){
  var bar = document.getElementById('stats-bar');
  bar.innerHTML = '';
  if(!data.daily_stats) return;
  data.daily_stats.forEach(function(d){
    var card = document.createElement('div');
    card.className = 'stat-card';
    card.innerHTML = '<div class="stat-val">'+d.count+'</div><div class="stat-label">'+d.date+'</div>';
    bar.appendChild(card);
  });
}
function loadData(){
  fetch('data/results.json?v='+Date.now())
    .then(function(r){return r.json();})
    .then(function(data){
      document.getElementById('loading').style.display='none';
      document.getElementById('table-wrap').style.display='';
      matches=data.matches;
      stats=data;
      document.getElementById('dateRange').textContent='📅 '+data.date_range;
      document.getElementById('updateTime').textContent='🕐 '+data.generated_at;
      document.getElementById('hitRate').textContent='🎯 '+data.hit_count+'/'+data.total_scored+' ('+fmtPct(data.hit_rate)+')';
      renderStats(data);
      // 填充日期筛选
      var sel=document.getElementById('dateFilter');
      if(data.daily_stats){
        data.daily_stats.forEach(function(d){
          var opt=document.createElement('option');
          opt.value=d.date;
          opt.textContent=d.date+' ('+d.count+'场)';
          sel.appendChild(opt);
        });
      }
      // 填充来源筛选
      var srcSel=document.getElementById('sourceFilter');
      var srcs={};
      data.matches.forEach(function(m){srcs[m.source]=1;});
      Object.keys(srcs).sort().forEach(function(s){
        var opt=document.createElement('option');
        opt.value=s;
        opt.textContent=s=='beidan'?'北单':s=='jingcai'?'竞彩':s=='hkjc'?'HKJC':s;
        srcSel.appendChild(opt);
      });
      applyFilters();
    })
    .catch(function(e){
      document.getElementById('loading').textContent='❌ 加载失败: '+e.message;
    });
}
function applyFilters(){
  var filtSrc=document.getElementById('sourceFilter').value;
  var dateFilt=document.getElementById('dateFilter').value;
  var tbody=document.getElementById('matchBody');
  tbody.innerHTML='';
  var mlist=matches;
  if(filtSrc!='all') mlist=mlist.filter(function(m){return m.source==filtSrc;});
  if(dateFilt!='all') mlist=mlist.filter(function(m){return m.date==dateFilt;});
  var sortBy=document.getElementById('sortBy').value;
  if(sortBy=='odds'){mlist=mlist.slice().sort(function(a,b){return(b.odds_win||0)-(a.odds_win||0);});}
  mlist.forEach(function(m){
    var hc=m.hit?'hit-'+(m.hit=='yes'||m.hit===true?'yes':'no'):'';
    var tr=document.createElement('tr');
    tr.innerHTML=
      '<td>'+fmtTime(m.match_time)+'</td>'+
      '<td><span class="tag tag-'+m.source+'">'+(m.event||m.source)+'</span></td>'+
      '<td class="team-name">'+m.home_team+'</td>'+
      '<td class="score-cell">'+(m.score||'-')+'</td>'+
      '<td class="team-name">'+m.away_team+'</td>'+
      '<td><span class="'+dirClass(m.prediction)+'">'+dirText(m.prediction)+'</span></td>'+
      '<td class="'+hc+'">'+(m.hit||'')+'</td>'+
      '<td class="odds-cell">'+renderOdds(m.comparison, m.hkjc_comparison)+'</td>'+
      '<td class="odds-cell"><span class="odds-val odds-w">'+fmtPct(m.model_win)+'</span> <span class="odds-val odds-d">'+fmtPct(m.model_draw)+'</span> <span class="odds-val odds-l">'+fmtPct(m.model_loss)+'</span></td>'+
      '<td class="lgbm-cell"><span class="'+dirClass(m.lgbm_prediction)+'">'+dirText(m.lgbm_prediction)+'</span></td>'+
      '<td class="prob-cell">'+fmtPct(m.lgbm_prediction_prob)+'</td>';
    tbody.appendChild(tr);
  });
}
loadData();
