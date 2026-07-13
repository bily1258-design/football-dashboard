#!/usr/bin/env python3
"""
ai_analysis.py — 足彩智能分析系统 v3.0

功能：
1. 读取data/matches_*.json（从500.com zqdc页面抓取）
2. 读取data/football.db（历史数据）
3. 计算：隐含概率 → 泊松λ → 泊松1X2 → 融合概率 → EV
4. 生成：docs/data/results.json + docs/index.html + 前端文件

用法：
  python3 scripts/ai_analysis.py
  python3 scripts/ai_analysis.py --db data/football.db
  python3 scripts/ai_analysis.py --fetch-only   # 只抓取，不分析
"""
import re, json, os, sys, math, sqlite3, glob, logging, hashlib
from datetime import datetime, date
from collections import defaultdict
from typing import Dict, List, Any, Tuple, Optional

# ─── 路径 ──────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(REPO_DIR, "data")
DOCS_DIR = os.path.join(REPO_DIR, "docs")
DB_PATH = os.path.join(DATA_DIR, "football.db")

# ─── 算法常量 ──────────────────────────────────────
SMOOTH_ALPHA = 0.01           # 贝叶斯平滑强度：模型 = (1-α)×隐含 + α/3（极小，仅用于正则化）
HOME_ADJ = 0.01               # 主场调整量：加到模型主胜，从平/负各扣0.003
EV_TANH_SCALE = 0.50          # EV软压缩
MIN_EV_THRESHOLD = 0.03       # 最小EV阈值（3%）

# ─── 日志 ──────────────────────────────────────────
logger = logging.getLogger("ai_analysis")
def setup_logging():
    log_dir = os.path.join(REPO_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"analysis_{datetime.now():%Y%m%d}.log")
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)-8s | %(message)s',
        handlers=[logging.FileHandler(log_file, encoding='utf-8'), logging.StreamHandler(sys.stdout)])
    return logging.getLogger("ai_analysis")

logger = setup_logging()

# ─── 核心数学 ──────────────────────────────────────

def implied_from_odds(odds_w: float, odds_d: float, odds_l: float) -> Tuple[Optional[float], ...]:
    """从赔率反推隐含概率（去抽水归一化）"""
    if odds_w <= 0 or odds_d <= 0 or odds_l <= 0:
        return None, None, None, 0.0
    iw, id_, il = 1.0 / odds_w, 1.0 / odds_d, 1.0 / odds_l
    total = iw + id_ + il
    margin = total - 1.0
    return iw / total, id_ / total, il / total, margin

def calc_ev(fusion_prob: float, implied_prob: float) -> float:
    """概率优势法EV: EV = fusion/implied - 1"""
    if implied_prob <= 0 or fusion_prob <= 0:
        return 0.0
    return fusion_prob / implied_prob - 1

def tanh_compress(ev: float, scale: float = EV_TANH_SCALE) -> float:
    """tanh软压缩"""
    return scale * math.tanh(ev / scale)

def determine_direction(ev_w: float, ev_d: float, ev_l: float) -> Tuple[str, str, float]:
    """确定推荐方向（选EV最大的方向 — 最有价值）
    1. 默认取EV最大的方向
    2. 若中间值EV与最大值EV相差<5% → 改推中间值方向
    """
    items = [('主胜', ev_w, 'home'), ('平局', ev_d, 'draw'), ('客胜', ev_l, 'away')]
    # 按EV降序（最大到最小）
    sorted_items = sorted(items, key=lambda x: x[1], reverse=True)
    max_dir_cn, max_ev, max_dir_en = sorted_items[0]
    mid_dir_cn, mid_ev, mid_dir_en = sorted_items[1]

    # 默认：推最大值方向（最正EV）
    best_dir_cn, best_dir_en = max_dir_cn, max_dir_en

    # 若中间值与最大值相差不到5% → 改推中间值（分岐大时保守）
    if abs(mid_ev - max_ev) < 0.05:
        best_dir_cn, best_dir_en = mid_dir_cn, mid_dir_en

    best_ev = ev_w if best_dir_en == 'home' else ev_d if best_dir_en == 'draw' else ev_l
    return best_dir_cn, best_dir_en, round(best_ev, 4)

# ─── 数据加载 ──────────────────────────────────────

def load_raw_matches() -> List[Dict]:
    """从 data/matches_*.json 加载原始比赛数据"""
    all_ms = []
    files = sorted(glob.glob(os.path.join(DATA_DIR, "matches_*.json")))
    if not files:
        logger.warning("未找到 data/matches_*.json 文件")
        return []
    for fp in files:
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                data = json.load(f)
            ms = data.get('matches', data if isinstance(data, list) else [])
            if isinstance(ms, list):
                all_ms.extend(ms)
                logger.debug(f"  {os.path.basename(fp)}: {len(ms)} 场")
        except Exception as e:
            logger.warning(f"读取 {fp} 失败: {e}")
    # 去重 (按 home+away+date)
    seen = set()
    deduped = []
    for m in all_ms:
        key = (m.get('home_team',''), m.get('away_team',''), m.get('date',''))
        if key not in seen:
            seen.add(key)
            deduped.append(m)
    logger.info(f"原始数据 {len(all_ms)} 场 → 去重后 {len(deduped)} 场")
    return deduped

def load_db_matches(db_path: str = DB_PATH, days: int = 14) -> List[Dict]:
    """从数据库加载已有预测（用于合并）"""
    if not os.path.exists(db_path):
        logger.warning(f"数据库不存在: {db_path}")
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT date, home_team, away_team, kickoff_time, league,
                   odds_win, odds_draw, odds_loss,
                   poisson_win, poisson_draw, poisson_loss,
                   final_win, final_draw, final_loss,
                   ev_win, ev_draw, ev_loss,
                   prediction, prediction_prob,
                   home_lambda, away_lambda,
                   source, match_id
            FROM poisson_predictions
            WHERE date >= date('now', ?)
            ORDER BY date, kickoff_time
        """, (f'-{days} days',))
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        logger.info(f"数据库加载 {len(rows)} 条历史预测")
        return rows
    except Exception as e:
        logger.warning(f"数据库读取失败: {e}")
        return []

# ─── 分析引擎 ──────────────────────────────────────

def analyze_matches(matches: List[Dict]) -> List[Dict]:
    """对比赛列表执行EV/泊松分析"""
    results = []
    skipped = 0

    for m in matches:
        # 赔率源：平博
        ow = float(m.get('odds_pinnacle_win', 0) or 0)
        od = float(m.get('odds_pinnacle_draw', 0) or 0)
        ol = float(m.get('odds_pinnacle_loss', 0) or 0)
        odds_source = 'pinnacle'

        if not (ow > 1 and od > 1 and ol > 1):
            skipped += 1
            continue

        # 1. 隐含概率（去抽水）
        imp_w, imp_d, imp_l, margin = implied_from_odds(ow, od, ol)
        if imp_w is None:
            skipped += 1
            continue

        # 2. 贝叶斯平滑隐含概率 + 主场修正（微量）
        sw = imp_w * (1 - SMOOTH_ALPHA) + SMOOTH_ALPHA / 3 + HOME_ADJ
        sd = imp_d * (1 - SMOOTH_ALPHA) + SMOOTH_ALPHA / 3 - HOME_ADJ * 0.3
        sl = imp_l * (1 - SMOOTH_ALPHA) + SMOOTH_ALPHA / 3 - HOME_ADJ * 0.3
        st = sw + sd + sl
        model_w, model_d, model_l = sw/st, sd/st, sl/st

        # 3. EV（模型概率 vs 市场隐含概率）
        ev_w = calc_ev(model_w, imp_w)
        ev_d = calc_ev(model_d, imp_d)
        ev_l = calc_ev(model_l, imp_l)
        ev_w_c = tanh_compress(ev_w)
        ev_d_c = tanh_compress(ev_d)
        ev_l_c = tanh_compress(ev_l)

        # 4. 推荐方向
        dir_cn, dir_en, best_ev = determine_direction(ev_w_c, ev_d_c, ev_l_c)

        # 5. 最大概率方向
        max_prob_val = max(model_w, model_d, model_l)

        # 比分 → 实际结果方向
        score_raw = m.get('score', '')
        score_parts = score_raw.split('-')
        if len(score_parts) == 2 and score_parts[0].strip().isdigit() and score_parts[1].strip().isdigit():
            sh, sa = int(score_parts[0]), int(score_parts[1])
            actual = 'home' if sh > sa else ('draw' if sh == sa else 'away')
        else:
            actual = ''
        hit = '✅' if actual and actual == dir_en else ('❌' if actual else '')

        # 8. 赔率对比数据（平博开盘 vs 平博即时）
        comparison = {}
        if ow > 1 and od > 1 and ol > 1:
            iw, id_, il, _ = implied_from_odds(ow, od, ol) or (None, None, None, 0)
            if iw:
                ew = tanh_compress(calc_ev(model_w, iw))
                ed = tanh_compress(calc_ev(model_d, id_))
                el = tanh_compress(calc_ev(model_l, il))
                dc, _, _ = determine_direction(ew, ed, el)
                entry = {
                    'odds': [round(ow,2), round(od,2), round(ol,2)],
                    'ev': [round(ew,4), round(ed,4), round(el,4)],
                    'dir': dc,
                }
                op_w = float(m.get('odds_pinnacle_open_win', 0) or 0)
                op_d = float(m.get('odds_pinnacle_open_draw', 0) or 0)
                op_l = float(m.get('odds_pinnacle_open_loss', 0) or 0)
                if op_w > 1 and op_d > 1 and op_l > 1:
                    entry['open'] = [round(op_w,2), round(op_d,2), round(op_l,2)]
                comparison['pinnacle'] = entry

        results.append({
            'date': m.get('date', ''),
            'match_time': m.get('match_time', ''),
            'event': m.get('event', ''),
            'home_team': m.get('home_team', ''),
            'away_team': m.get('away_team', ''),
            'score': score_raw,
            'hit': hit,
            'source': m.get('source', 'beidan'),
            'odds_source': odds_source,
            'odds_win': round(ow, 2),
            'odds_draw': round(od, 2),
            'odds_loss': round(ol, 2),
            'margin': round(margin, 4),
            'implied_win': round(imp_w, 4),
            'implied_draw': round(imp_d, 4),
            'implied_loss': round(imp_l, 4),
            'model_win': round(model_w, 4),
            'model_draw': round(model_d, 4),
            'model_loss': round(model_l, 4),
            'ev_win': round(ev_w_c, 4),
            'ev_draw': round(ev_d_c, 4),
            'ev_loss': round(ev_l_c, 4),
            'prediction': dir_en,
            'prediction_cn': dir_cn,
            'prediction_prob': round(max_prob_val, 4),
            'comparison': comparison,
        })

    logger.info(f"分析完成: {len(results)} 场 (跳过 {skipped} 场无赔率)")
    return results

# ─── 前端生成 ──────────────────────────────────────

def generate_frontend(results: List[Dict]):
    """生成docs/目录下的前端文件"""
    os.makedirs(DOCS_DIR, exist_ok=True)
    os.makedirs(os.path.join(DOCS_DIR, 'data'), exist_ok=True)

    # 按日期分组
    by_date = defaultdict(list)
    for r in results:
        by_date[r['date']].append(r)

    dates = sorted(by_date.keys(), reverse=True)
    all_counts = sum(len(v) for v in by_date.values())

    # 构建输出JSON
    output = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total_matches': all_counts,
        'date_range': f"{min(dates)} ~ {max(dates)}" if dates else '无数据',
        'daily_stats': [],
        'matches': results,
    }
    for d in dates:
        ms = by_date[d]
        ev_pos = sum(1 for m in ms if m['ev_win'] > 0 or m['ev_draw'] > 0 or m['ev_loss'] > 0)
        output['daily_stats'].append({
            'date': d,
            'count': len(ms),
            'positive_ev': ev_pos,
        })

    # 写入 results.json
    rp = os.path.join(DOCS_DIR, 'data', 'results.json')
    with open(rp, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    logger.info(f"结果 → {rp} ({all_counts} 场)")

    # ─── index.html ──────────────────────────
    html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>足彩价值投注看板</title>
<link rel="stylesheet" href="style.css?v=20260713v2">
</head>
<body>
<div class="container">
  <header>
    <h1>⚽ 足彩价值投注看板</h1>
    <div class="meta">
      <span id="updateTime">加载中...</span>
      <span id="matchCount">—</span>
      <span id="dateRange">—</span>
    </div>
    <div class="controls">
      <select id="dateFilter" onchange="applyFilters()">
        <option value="all">全部日期</option>
      </select>
      <select id="sourceFilter" onchange="applyFilters()">
        <option value="all">全部来源</option>

        <option value="jingcai">竞彩</option>
      </select>
      <select id="sortBy" onchange="applyFilters()">
        <option value="ev_desc">按EV ↓</option>
        <option value="ev_asc">按EV ↑</option>
        <option value="time">按时间</option>
        <option value="odds">按赔率</option>
      </select>
      <button id="refreshBtn" onclick="location.reload()">🔄 刷新</button>
    </div>
  </header>
  <div id="stats-bar"></div>
  <div id="loading">加载中...</div>
  <div id="table-wrap" style="display:none">
    <table id="matchTable">
      <thead>
        <tr>
          <th data-sort="time">时间</th>
          <th data-sort="league">赛事</th>
          <th>主队</th>
          <th>客队</th>
          <th data-sort="odds">赔率(W/D/L)</th>
          <th>平博初盘vs即时</th>
          <th data-sort="model">模型(W/D/L)</th>
          <th data-sort="ev">EV(W/D/L)</th>
          <th data-sort="direction">方向</th>
          <th>比分</th>
          <th>命中</th>
        </tr>
      </thead>
      <tbody id="matchBody"></tbody>
    </table>
  </div>
</div>
<script src="script.js?v=20260713v4"></script>
</body>
</html>'''
    with open(os.path.join(DOCS_DIR, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html)

    # ─── style.css ──────────────────────────
    css = '''*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#0f1923;color:#e0e6ed;min-height:100vh}
.container{max-width:1400px;margin:0 auto;padding:16px}
header{background:linear-gradient(135deg,#1a2a3a,#0d1b2a);border-radius:12px;padding:20px 24px;margin-bottom:16px;border:1px solid #2a3a4a}
h1{font-size:22px;color:#66b8ff;margin-bottom:8px}
.meta{display:flex;gap:20px;font-size:13px;color:#8899aa;flex-wrap:wrap}
.meta span{background:#1a2a3a;padding:4px 12px;border-radius:6px}
.controls{display:flex;gap:10px;margin-top:10px;flex-wrap:wrap}
.controls select,.controls button{background:#1a2a3a;color:#e0e6ed;border:1px solid #2a3a4a;border-radius:6px;padding:6px 12px;font-size:13px;cursor:pointer}
.controls button{background:#2563eb;border-color:#2563eb;color:#fff;font-weight:600}
.controls button:hover{background:#1d4ed8}
#stats-bar{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap}
.stat-card{background:#1a2a3a;border:1px solid #2a3a4a;border-radius:8px;padding:8px 14px;text-align:center;min-width:80px}
.stat-card .stat-val{font-size:18px;font-weight:700;color:#66b8ff}
.stat-card .stat-label{font-size:11px;color:#667788;margin-top:2px}
#loading{text-align:center;padding:40px;color:#667788;font-size:16px}
#table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
#table-wrap::before{content:'← 左右滑动查看更多 →';display:block;text-align:center;font-size:11px;color:#556677;padding:4px 0}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:#1a2a3a;padding:8px 10px;text-align:left;font-weight:600;color:#8899aa;border-bottom:2px solid #2a3a4a;cursor:pointer;white-space:nowrap;user-select:none}
th:hover{color:#66b8ff}
td{padding:8px 10px;border-bottom:1px solid #1a2a3a;white-space:nowrap}
tr:hover{background:#1a2a3a}
.team-name{font-weight:600;color:#e0e6ed}
.vs{color:#667788;padding:0 4px}
.odds-cell{font-variant-numeric:tabular-nums}
.odds-val{display:inline-block;min-width:48px;text-align:center;padding:1px 4px;border-radius:3px;font-size:12px}
.odds-w{color:#4ade80}
.odds-d{color:#fbbf24}
.odds-l{color:#f87171}
.ev-pos{color:#4ade80;font-weight:600}
.ev-neg{color:#f87171}
.ev-zero{color:#667788}
.dir-home{color:#4ade80;font-weight:700}
.dir-draw{color:#fbbf24;font-weight:700}
.dir-away{color:#f87171;font-weight:700}
.dir-wait{color:#667788}
.tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:11px;background:#2a3a4a;color:#8899aa}
.tag-beidan{background:#1e3a5f;color:#60a5fa}
.tag-jingcai{background:#3a2a1e;color:#fbbf24}
.odds-source-tag{display:inline-block;padding:0 4px;border-radius:3px;font-size:10px;margin-right:4px;font-weight:700}
.tag-pinnacle{background:#1a3a2a;color:#4ade80}
.cmp-cell{font-size:11px;line-height:1.6;position:relative;text-align:center;min-width:70px}
.cmp-hint-wrap{cursor:pointer;position:relative;display:inline-block}
.cmp-hint{display:inline-block;font-size:10px;font-weight:700;padding:2px 8px;border-radius:4px;cursor:pointer;transition:all .15s}
.cmp-hint:hover{filter:brightness(1.3)}
.cmp-hint-agree{background:#0a2a1a;color:#4ade80;border:1px solid #1a4a2a}
.cmp-hint-conflict{background:#2a1a0a;color:#fbbf24;border:1px solid #4a3a1a}
.cmp-hint-wait{background:#1a1a2a;color:#667788;border:1px solid #2a2a3a}
.cmp-popup{display:none;position:absolute;top:100%;left:50%;transform:translateX(-50%);z-index:100;background:#0d1b2a;border:1px solid #2a4a6a;border-radius:8px;padding:8px 12px;white-space:nowrap;min-width:130px;box-shadow:0 8px 24px rgba(0,0,0,0.6);margin-top:4px}
.cmp-hint-wrap:hover .cmp-popup{display:block}
.cmp-pop-row{font-size:11px;padding:2px 0;color:#c0c8d0;border-bottom:1px solid #1a2a3a}
.cmp-pop-row:last-child{border-bottom:none}
.cmp-pop-row .cmp-na{color:#556677}
.cmp-pop-source{display:inline-block;width:28px;color:#60a5fa;font-weight:600}
.cmp-pop-latest{font-weight:500;color:#e0e8f0}
.cmp-pop-open{font-size:10px;color:#7a8a9a;padding-left:28px;border-bottom:1px solid #1a2a3a}
.cmp-divider{border-top:1px solid #2a4a6a;margin:4px 0}
.cmp-div-title{font-size:10px;color:#8899aa;text-align:center;padding:2px 0}
.cmp-div-sep{color:#445566;margin:0 2px}
.cmp-div-val{font-weight:500;color:#e0e8f0}
.cmp-div-pin{color:#4ade80}
.cmp-div-bet{color:#a78bfa}
.cmp-div-higher{color:#fbbf24;font-weight:700}
.cmp-div-lower{color:#7a8a9a}
.cmp-div-diff{font-size:9px;margin-left:2px;color:#8899aa}
.lambda-cell{font-size:12px;color:#8899aa}
.hit-yes{color:#4ade80;font-weight:700;font-size:1.1em;text-align:center}
.hit-no{color:#f87171;font-weight:700;font-size:1.1em;text-align:center}
.score-cell{text-align:center;font-weight:500}
.high-ev{animation:glow 2s ease-in-out infinite}
@keyframes glow{0%,100%{opacity:1}50%{opacity:0.6}}
@media(max-width:768px){
  .container{padding:8px}
  table{font-size:11px}
  th,td{padding:5px 4px}
  .meta{flex-direction:column;gap:6px}
}'''
    with open(os.path.join(DOCS_DIR, 'style.css'), 'w', encoding='utf-8') as f:
        f.write(css)

    # ─── script.js ──────────────────────────
    js = '''// script.js — 足彩价值投注看板
var allData = null;
var allMatches = [];

function fmtOdds(v){return v>0?v.toFixed(2):'-'}
function fmtPct(v){return (v*100).toFixed(1)+'%'}
function fmtEv(v){return v===0?'0%':(v>0?'+':'')+(v*100).toFixed(1)+'%'}
function evClass(v){return v>0.03?'ev-pos':v<-0.03?'ev-neg':'ev-zero'}
function dirClass(d){return d==='home'?'dir-home':d==='draw'?'dir-draw':d==='away'?'dir-away':'dir-wait'}
function dirText(d){return d==='home'?'主胜':d==='draw'?'平局':d==='away'?'客胜':'观望'}
function srcLabel(s){return '平博'}
function srcEvClass(v){return v>0.03?'ev-pos':v<-0.03?'ev-neg':'ev-zero'}
function renderCmp(c){
  if(!c) return '<span class="cmp-na">--</span>';
  var d = c.pinnacle;
  if(!d) return '<span class="cmp-na">--</span>';
  var hint = '', hintClass = '';
  var dirDot = '';
  if(d.dir==='主胜') dirDot='👑H';
  else if(d.dir==='平局') dirDot='⚖️D';
  else if(d.dir==='客胜') dirDot='👑A';
  hint = dirDot || '◻️';
  hintClass = dirDot ? 'cmp-hint-agree' : 'cmp-hint-wait';
  var popup = '';
  popup+= '<div class="cmp-pop-row"><span class="cmp-pop-source">即时</span> <span class="cmp-pop-latest">'+d.odds[0].toFixed(2)+'/'+d.odds[1].toFixed(2)+'/'+d.odds[2].toFixed(2)+'</span> '+dirDot+'</div>';
  if(d.open){
    popup+= '<div class="cmp-pop-row cmp-pop-open"><span class="cmp-pop-source">初盘</span> '+d.open[0].toFixed(2)+'/'+d.open[1].toFixed(2)+'/'+d.open[2].toFixed(2)+'</div>';
    var oLabels = ['胜','平','负'];
    popup+= '<div class="cmp-divider"></div><div class="cmp-div-title">初盘→即时变化</div>';
    for(var di=0;di<3;di++){
      var curVal = d.odds[di], openVal = d.open[di];
      var diff = curVal - openVal;
      var diffNote = diff > 0.01 ? '↑' : (diff < -0.01 ? '↓' : '=');
      var higherCls = diff > 0 ? ' cmp-div-higher' : (diff < 0 ? ' cmp-div-lower' : '');
      popup+= '<div class="cmp-pop-row"><span class="cmp-pop-source">'+oLabels[di]+'</span> <span class="cmp-div-val cmp-div-open">'+openVal.toFixed(2)+'</span><span class="cmp-div-sep">→</span><span class="cmp-div-val'+higherCls+'">'+curVal.toFixed(2)+'</span><span class="cmp-div-diff">'+diffNote+'</span></div>';
    }
  }
  return '<div class="cmp-hint-wrap"><span class="cmp-hint '+hintClass+'">'+hint+'</span><div class="cmp-popup">'+popup+'</div></div>';
}
function fmtTime(t){return t?t.replace(/^\\d{2}-/,''):''}

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
    var hc=m.hit==='\u2705'?'hit-yes':m.hit==='\u274c'?'hit-no':'';
    tr.innerHTML =
      '<td>'+fmtTime(m.match_time)+'</td>'+
      '<td><span class="tag tag-'+m.source+'">'+(m.event||m.source)+'</span></td>'+
      '<td class="team-name">'+m.home_team+'</td>'+
      '<td class="team-name">'+m.away_team+'</td>'+
      '<td class="odds-cell"><span class="odds-source-tag tag-'+m.odds_source+'">平博</span><span class="odds-val odds-w">'+fmtOdds(m.odds_win)+'</span> <span class="odds-val odds-d">'+fmtOdds(m.odds_draw)+'</span> <span class="odds-val odds-l">'+fmtOdds(m.odds_loss)+'</span></td>'+
      '<td class="odds-cell cmp-cell">'+renderCmp(m.comparison)+'</td>'+
      '<td class="odds-cell"><span class="odds-val odds-w">'+fmtPct(m.model_win)+'</span> <span class="odds-val odds-d">'+fmtPct(m.model_draw)+'</span> <span class="odds-val odds-l">'+fmtPct(m.model_loss)+'</span></td>'+
      '<td class="odds-cell"><span class="odds-val '+evClass(m.ev_win)+'">'+fmtEv(m.ev_win)+'</span> <span class="odds-val '+evClass(m.ev_draw)+'">'+fmtEv(m.ev_draw)+'</span> <span class="odds-val '+evClass(m.ev_loss)+'">'+fmtEv(m.ev_loss)+'</span></td>'+
      '<td><span class="'+dirClass(m.prediction)+'">'+dirText(m.prediction)+'</span></td>'+
      '<td class="score-cell">'+(m.score||'-')+'</td>'+
      '<td class="'+hc+'">'+(m.hit||'')+'</td>';
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
'''
    with open(os.path.join(DOCS_DIR, 'script.js'), 'w', encoding='utf-8') as f:
        f.write(js)

    logger.info(f"前端文件 → {DOCS_DIR}/")


# ─── 主流程 ──────────────────────────────────────

def main():
    logger.info("=" * 60)
    logger.info("🏟️  足彩智能分析系统 v3.0")
    logger.info(f"⏰  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    # 1. 加载原始数据
    logger.info("\n📥 [1/3] 加载原始数据...")
    matches = load_raw_matches()
    if not matches:
        logger.error("无原始数据，退出")
        return

    # 2. 分析
    logger.info(f"\n🔬 [2/3] EV/泊松分析 ({len(matches)} 场)...")
    results = analyze_matches(matches)

    if not results:
        logger.warning("分析后无有效结果!")
        return

    # 3. 生成前端
    logger.info(f"\n📄 [3/3] 生成前端 ({len(results)} 场)...")
    generate_frontend(results)

    # 统计
    pos_ev = sum(1 for r in results if r['ev_win'] > 0.03 or r['ev_draw'] > 0.03 or r['ev_loss'] > 0.03)
    logger.info(f"\n✅ 完成! {len(results)} 场, 正EV {pos_ev} 场")
    logger.info(f"   结果: docs/data/results.json")
    logger.info(f"   看板: docs/index.html")

if __name__ == '__main__':
    main()
