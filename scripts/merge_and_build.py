#!/usr/bin/env python3
"""merge_and_build.py — processed → results.json + index.html

读取 data/processed/*.json（优先）或直读 football.db
输出 data/results.json + docs/index.html

职责单一：只管数据聚合 + 页面渲染
"""

import os, sys, json, sqlite3, re, math, argparse
from datetime import datetime, timedelta
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
PROCESSED_DIR = os.path.join(REPO_DIR, "data", "processed")
DATA_DIR = os.path.join(REPO_DIR, "data")
DOCS_DIR = os.path.join(REPO_DIR, "docs")
DB_PATH = os.environ.get('FOOTBALL_DB_PATH',
    os.path.join(REPO_DIR, 'data', 'football.db'))

WEEKDAY_CN = ['周一','周二','周三','周四','周五','周六','周日']


def load_from_processed(max_days=999) -> dict:
    """从 processed 目录加载数据"""
    by_date = {}
    if not os.path.exists(PROCESSED_DIR):
        return by_date
    cutoff = (datetime.now() - timedelta(days=max_days)).strftime('%Y%m%d')
    for fname in sorted(os.listdir(PROCESSED_DIR), reverse=True):
        if not fname.endswith('.json'):
            continue
        date_key = fname.replace('.json', '')
        if len(date_key) == 8 and date_key < cutoff:
            continue
        path = os.path.join(PROCESSED_DIR, fname)
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        d = data.get('date', f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}")
        by_date[d] = data.get('records', [])
    return by_date


def load_from_db(db_path: str, max_days=999) -> dict:
    """fallback: 直读DB"""
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # 确保DB有所有新列（兼容旧DB）
    _NEW_COLUMNS = [
        ('pin_ah_handicap', 'REAL'), ('pin_ah_home_water', 'REAL'), ('pin_ah_away_water', 'REAL'),
        ('pin_ou_line', 'REAL'), ('pin_ou_over', 'REAL'), ('pin_ou_under', 'REAL'),
        ('ou_over', 'REAL'), ('ou_line', 'REAL'), ('ou_under', 'REAL'),
        ('ou_open_over', 'REAL'), ('ou_open_line', 'REAL'), ('ou_open_under', 'REAL'),
        ('liji_ou_over', 'REAL'), ('liji_ou_line', 'REAL'), ('liji_ou_under', 'REAL'),
        ('liji_ou_open_over', 'REAL'), ('liji_ou_open_line', 'REAL'), ('liji_ou_open_under', 'REAL'),
        ('ms_ou_over', 'REAL'), ('ms_ou_line', 'REAL'), ('ms_ou_under', 'REAL'),
        ('ms_ou_open_over', 'REAL'), ('ms_ou_open_line', 'REAL'), ('ms_ou_open_under', 'REAL'),
        ('pinnacle_open_w', 'REAL'), ('pinnacle_open_d', 'REAL'), ('pinnacle_open_l', 'REAL'),
    ]
    cur.execute("PRAGMA table_info(poisson_predictions)")
    existing = {row[1] for row in cur.fetchall()}
    for col, ctype in _NEW_COLUMNS:
        if col not in existing:
            cur.execute(f"ALTER TABLE poisson_predictions ADD COLUMN {col} {ctype} DEFAULT 0")
    conn.commit()
    cutoff = (datetime.now() - timedelta(days=max_days)).strftime('%Y-%m-%d') if max_days < 999 else '2000-01-01'
    cur.execute("SELECT id, date, league, home_team, away_team, kickoff_time, \
        prediction, prediction_prob, odds_win, odds_draw, odds_loss, \
        poisson_win, poisson_draw, poisson_loss, final_win, final_draw, final_loss, \
        fusion_win, fusion_draw, fusion_loss, actual_outcome, risk_level, \
        confidence_index, reference_score, best_direction_cn, source, \
        ev_win, ev_draw, ev_loss, kelly_win, kelly_draw, kelly_loss, \
        pinnacle_open_w, pinnacle_open_d, pinnacle_open_l, \
        pinnacle_close_w, pinnacle_close_d, pinnacle_close_l, \
        hkjc_close_w, hkjc_close_d, hkjc_close_l, cold_risk, odds_source, \
        home_lambda, away_lambda, home_ranking, away_ranking, \
        hhad_handicap, hhad_win, hhad_draw, hhad_loss, \
        ah_handicap, ah_home_water, ah_away_water, ah_source, \
        ah_open_handicap, ah_open_home_water, ah_open_away_water, \
        liji_handicap, liji_home_water, liji_away_water, \
        liji_open_handicap, liji_open_home_water, liji_open_away_water, \
        ms_handicap, ms_home_water, ms_away_water, \
        ms_open_handicap, ms_open_home_water, ms_open_away_water, \
        pin_ah_handicap, pin_ah_home_water, pin_ah_away_water, \
        pin_ou_line, pin_ou_over, pin_ou_under, \
        ou_over, ou_line, ou_under, \
        ou_open_over, ou_open_line, ou_open_under, \
        liji_ou_over, liji_ou_line, liji_ou_under, \
        liji_ou_open_over, liji_ou_open_line, liji_ou_open_under, \
        ms_ou_over, ms_ou_line, ms_ou_under, \
        ms_ou_open_over, ms_ou_open_line, ms_ou_open_under \
        FROM poisson_predictions WHERE date >= ? ORDER BY date DESC, kickoff_time, id", (cutoff,))
    by_date = {}
    for r in cur.fetchall():
        d = dict(r)
        date = d['date']
        # 竞彩窗口：凌晨00:00-11:59的比赛归到前一天
        kickoff = d.get('kickoff_time', '')
        if kickoff:
            try:
                kt = datetime.strptime(kickoff, '%Y-%m-%d %H:%M')
                if kt.hour < 12:
                    prev_day = (kt - timedelta(days=1)).strftime('%Y-%m-%d')
                    date = prev_day
                    d['date'] = prev_day
            except:
                pass
        if date not in by_date:
            by_date[date] = []
        # 简化序列化
        outcome = d.get('actual_outcome', '') or ''
        m = re.search(r'(\d+-\d+)', outcome)
        score = m.group(1) if m else ''
        result = '主胜' if '主胜' in outcome else ('客胜' if '客胜' in outcome else ('平局' if '平局' in outcome else ''))
        ev_dir = d.get('best_direction_cn') or d.get('prediction') or ''
        ev_hit = (ev_dir == result) if result else False
        fw = d.get('final_win', 0) or 0
        fd = d.get('final_draw', 0) or 0
        fl = d.get('final_loss', 0) or 0
        prob_dir = '主胜' if fw >= fd and fw >= fl else ('客胜' if fl >= fw and fl >= fd else '平局')
        prob_hit = (prob_dir == result) if result else False
        ci = d.get('confidence_index') or 0
        stars = min(5, max(1, round(ci * 5))) if isinstance(ci, (int, float)) and ci > 0 else 0
        by_date[date].append({
            'id': d['id'], 'date': date, 'league': d.get('league',''),
            'home': d['home_team'], 'away': d['away_team'],
            'kickoff': d.get('kickoff_time',''), 'prediction': d.get('prediction',''),
            'prediction_prob': round(d.get('prediction_prob',0) or 0, 3),
            'odds': {'w': d.get('odds_win',0) or 0, 'd': d.get('odds_draw',0) or 0, 'l': d.get('odds_loss',0) or 0},
            'poisson': {'w': round(d.get('poisson_win',0) or 0, 3), 'd': round(d.get('poisson_draw',0) or 0, 3), 'l': round(d.get('poisson_loss',0) or 0, 3)},
            'final_prob': {'w': round(fw,3), 'd': round(fd,3), 'l': round(fl,3)},
            'fusion_prob': {'w': round(d.get('fusion_win',0) or 0,3), 'd': round(d.get('fusion_draw',0) or 0,3), 'l': round(d.get('fusion_loss',0) or 0,3)},
            'result': result, 'score': score,
            'ev_direction': ev_dir, 'ev_hit': ev_hit,
            'prob_direction': prob_dir, 'prob_hit': prob_hit,
            'fusion_direction': '主胜' if (d.get('fusion_win',0) or 0) >= (d.get('fusion_draw',0) or 0) and (d.get('fusion_win',0) or 0) >= (d.get('fusion_loss',0) or 0) else '客胜' if (d.get('fusion_loss',0) or 0) >= (d.get('fusion_win',0) or 0) else '平局',
            'ev': {'w': round(d.get('ev_win',0) or 0,4), 'd': round(d.get('ev_draw',0) or 0,4), 'l': round(d.get('ev_loss',0) or 0,4)},
            'kelly': {'w': round(d.get('kelly_win',0) or 0,4), 'd': round(d.get('kelly_draw',0) or 0,4), 'l': round(d.get('kelly_loss',0) or 0,4)},
            'pinnacle': {
                'w': d.get('pinnacle_close_w',0) or 0, 'd': d.get('pinnacle_close_d',0) or 0, 'l': d.get('pinnacle_close_l',0) or 0,
                'open': {'w': d.get('pinnacle_open_w',0) or 0, 'd': d.get('pinnacle_open_d',0) or 0, 'l': d.get('pinnacle_open_l',0) or 0},
            },
            'hkjc': {'w': d.get('hkjc_close_w',0) or 0, 'd': d.get('hkjc_close_d',0) or 0, 'l': d.get('hkjc_close_l',0) or 0},
            'risk_level': d.get('risk_level','') or '', 'stars': stars,
            'confidence_index': round(ci,2), 'reference_score': d.get('reference_score','') or '',
            'cold_risk': d.get('cold_risk','') or '', 'source': d.get('source','jingcai'),
            'odds_source': d.get('odds_source','had'),
            'home_lambda': round(d.get('home_lambda',0) or 0,3),
            'away_lambda': round(d.get('away_lambda',0) or 0,3),
            'home_ranking': d.get('home_ranking',0) or 0,
            'away_ranking': d.get('away_ranking',0) or 0,
            'hhad': {
                'handicap': d.get('hhad_handicap', None),
                'w': d.get('hhad_win',0) or 0,
                'd': d.get('hhad_draw',0) or 0,
                'l': d.get('hhad_loss',0) or 0,
            },
            'ah': {
                'handicap': d.get('ah_handicap', None),
                'home_w': d.get('ah_home_water', 0) or 0,
                'away_w': d.get('ah_away_water', 0) or 0,
                'source': d.get('ah_source', '') or '',
                'open': {
                    'handicap': d.get('ah_open_handicap', None),
                    'home_w': d.get('ah_open_home_water', 0) or 0,
                    'away_w': d.get('ah_open_away_water', 0) or 0,
                },
            },
            'liji': {
                'close': {
                    'handicap': d.get('liji_handicap', None),
                    'home_w': d.get('liji_home_water', 0) or 0,
                    'away_w': d.get('liji_away_water', 0) or 0,
                },
                'open': {
                    'handicap': d.get('liji_open_handicap', None),
                    'home_w': d.get('liji_open_home_water', 0) or 0,
                    'away_w': d.get('liji_open_away_water', 0) or 0,
                },
            },
            'ms': {
                'close': {
                    'handicap': d.get('ms_handicap', None),
                    'home_w': d.get('ms_home_water', 0) or 0,
                    'away_w': d.get('ms_away_water', 0) or 0,
                },
                'open': {
                    'handicap': d.get('ms_open_handicap', None),
                    'home_w': d.get('ms_open_home_water', 0) or 0,
                    'away_w': d.get('ms_open_away_water', 0) or 0,
                },
            },
            'pin_ah': {
                'handicap': d.get('pin_ah_handicap', None),
                'home_w': d.get('pin_ah_home_water', 0) or 0,
                'away_w': d.get('pin_ah_away_water', 0) or 0,
            },
            'pin_ou': {
                'line': d.get('pin_ou_line', None),
                'over': d.get('pin_ou_over', 0) or 0,
                'under': d.get('pin_ou_under', 0) or 0,
            },
            'ou': {
                'over': d.get('ou_over', 0) or 0,
                'line': d.get('ou_line', None),
                'under': d.get('ou_under', 0) or 0,
                'open': {
                    'over': d.get('ou_open_over', 0) or 0,
                    'line': d.get('ou_open_line', None),
                    'under': d.get('ou_open_under', 0) or 0,
                },
            },
            'liji_ou': {
                'over': d.get('liji_ou_over', 0) or 0,
                'line': d.get('liji_ou_line', None),
                'under': d.get('liji_ou_under', 0) or 0,
                'open': {
                    'over': d.get('liji_ou_open_over', 0) or 0,
                    'line': d.get('liji_ou_open_line', None),
                    'under': d.get('liji_ou_open_under', 0) or 0,
                },
            },
            'ms_ou': {
                'over': d.get('ms_ou_over', 0) or 0,
                'line': d.get('ms_ou_line', None),
                'under': d.get('ms_ou_under', 0) or 0,
                'open': {
                    'over': d.get('ms_ou_open_over', 0) or 0,
                    'line': d.get('ms_ou_open_line', None),
                    'under': d.get('ms_ou_open_under', 0) or 0,
                },
            },
        })
    conn.close()

    # A3去重+补AH：同 (home, away) 只保留一条，同 source 取 id 更大
    # 保留记录 AH 为空时从同 key 其他记录补
    total_dedup = 0
    total_ah_fixed = 0
    for date in list(by_date.keys()):
        records = by_date[date]
        if len(records) <= 1:
            continue
        groups = {}
        for r in records:
            key = (r['home'], r['away'])
            groups.setdefault(key, []).append(r)
        seen = {}
        for r in records:
            key = (r['home'], r['away'])
            if key not in seen:
                seen[key] = r
            else:
                prev = seen[key]
                if r['source'] == 'jingcai' and prev['source'] != 'jingcai':
                    seen[key] = r
                elif r['source'] == prev['source'] and r['id'] > prev['id']:
                    seen[key] = r
        # 补AH
        ah_fixed = 0
        for key, kept in seen.items():
            ah = kept.get('ah', {})
            if not ah or ah.get('handicap') is None or ah.get('handicap') == 0:
                for r in reversed(groups[key]):
                    rah = r.get('ah', {})
                    if rah and rah.get('handicap') is not None and rah.get('handicap') != 0:
                        kept['ah'] = rah
                        ah_fixed += 1
                        break
        if len(seen) < len(records):
            by_date[date] = list(seen.values())
            total_dedup += len(records) - len(seen)
            total_ah_fixed += ah_fixed
    if total_dedup or total_ah_fixed:
        print(f"  [merge] 去重 {total_dedup} 条, 补AH {total_ah_fixed} 条")

    return by_date


def build_daily_stats(by_date):
    stats = {}
    for date, records in by_date.items():
        n = sum(1 for r in records if r.get('result'))
        ev = sum(1 for r in records if r.get('ev_hit'))
        pb = sum(1 for r in records if r.get('prob_hit'))
        stats[date] = {
            'total': len(records), 'with_result': n,
            'ev_hits': ev, 'prob_hits': pb,
            'any_hits': sum(1 for r in records if r.get('ev_hit') or r.get('prob_hit')),
            'ev_rate': round(ev/n*100, 1) if n else 0,
            'prob_rate': round(pb/n*100, 1) if n else 0,
            'any_rate': round(sum(1 for r in records if r.get('ev_hit') or r.get('prob_hit'))/n*100, 1) if n else 0,
        }
    return stats


def build_summary(daily_stats):
    tn = tev = tpb = tany = 0
    for s in daily_stats.values():
        tn += s['with_result']; tev += s['ev_hits']; tpb += s['prob_hits']; tany += s['any_hits']
    return {
        'total_matches': tn, 'ev_hits': tev, 'prob_hits': tpb, 'any_hits': tany,
        'ev_rate': round(tev/tn*100, 1) if tn else 0,
        'prob_rate': round(tpb/tn*100, 1) if tn else 0,
        'any_rate': round(tany/tn*100, 1) if tn else 0,
        'days': len(daily_stats),
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }


def build_league_score_freq(db_path: str) -> dict:
    """从DB统计每个联赛的历史比分频率（用于泊松排序加权）"""
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""SELECT league, actual_outcome FROM poisson_predictions
        WHERE home_lambda > 0 AND away_lambda > 0
        AND actual_outcome IS NOT NULL AND actual_outcome != ''""")
    league_scores = defaultdict(lambda: defaultdict(int))
    for league, outcome in cur.fetchall():
        m = re.search(r'(\d+-\d+)', outcome or '')
        if not m:
            continue
        league_scores[league][m.group(1)] += 1
    conn.close()
    return {lg: dict(scores) for lg, scores in league_scores.items()}


def generate_results_json(by_date, daily_stats, summary, output_dir=None):
    if not output_dir:
        output_dir = DATA_DIR
    os.makedirs(output_dir, exist_ok=True)
    dates = sorted(by_date.keys(), reverse=True)
    output = {
        'meta': {
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'total_dates': len(dates),
            'total_matches': sum(len(v) for v in by_date.values()),
            'date_range': {'from': dates[-1] if dates else '', 'to': dates[0] if dates else ''},
        },
        'summary': summary,
        'daily_stats': daily_stats,
        'dates': dates,
        'matches': by_date,
    }
    path = os.path.join(output_dir, 'results.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f'✅ results.json → {path} ({os.path.getsize(path)//1024}KB)')
    return path


def generate_index_html(by_date, daily_stats, summary, league_score_freq=None, output_dir=None):
    """生成 index.html（引用外部 style.css + script.js）"""
    if not output_dir:
        output_dir = DOCS_DIR
    os.makedirs(output_dir, exist_ok=True)

    dates = sorted(by_date.keys(), reverse=True)
    today = datetime.now().strftime('%Y-%m-%d')
    default_date = today if today in by_date else (dates[0] if dates else '')
    league_freq_json = json.dumps(league_score_freq or {}, ensure_ascii=False)

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>竞彩泊松预测看板</title>
<link rel="stylesheet" href="style.css">
<script src="https://cdn.sheetjs.com/xlsx-0.20.3/package/dist/xlsx.full.min.js"></script>
</head>
<body>
<div class="header">
<h1>⚽ 竞彩泊松预测看板</h1>
<div class="sub">更新于 {summary['last_updated']} | 共 {summary['total_matches']} 场已开奖 | {summary['days']} 天数据</div>
</div>

<div class="summary-cards">
<div class="card"><div class="label">EV方向命中率</div><div class="value" style="color:{'#4caf50' if summary['ev_rate']>=55 else '#f44336' if summary['ev_rate']<40 else '#ff9800'}">{summary['ev_rate']}%</div><div class="label">{summary['ev_hits']}/{summary['total_matches']}</div></div>
<div class="card"><div class="label">概率最高命中率</div><div class="value" style="color:{'#4caf50' if summary['prob_rate']>=60 else '#f44336' if summary['prob_rate']<45 else '#ff9800'}">{summary['prob_rate']}%</div><div class="label">{summary['prob_hits']}/{summary['total_matches']}</div></div>
<div class="card"><div class="label">任一命中</div><div class="value blue">{summary['any_rate']}%</div><div class="label">{summary['any_hits']}/{summary['total_matches']}</div></div>
<div class="card"><div class="label">总天数</div><div class="value blue">{summary['days']}</div></div>
</div>

<div class="tabs">
<div class="tab active" data-tab="matches">📊 比赛数据</div>
<div class="tab" data-tab="daily">📈 每日统计</div>
<div class="tab" data-tab="leagues">🏆 联赛统计</div>
</div>

<div id="tab-matches" class="tab-content active">
<div class="controls">
<label>日期：</label>
<select id="dateSelect"></select>
<label><input type="checkbox" id="showResulted" checked> 已开奖</label>
<label><input type="checkbox" id="showPending" checked> 待开奖</label>
<button id="btnExcel" onclick="downloadExcel()" style="margin-left:auto;padding:4px 12px;background:#238636;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:13px">📥 下载Excel</button>
</div>
<div style="overflow-x:auto">
<table id="matchTable">
<thead><tr>
<th>#</th><th>联赛</th><th>时间</th><th>主队</th><th>亚盘</th><th>客队</th>
<th>推荐</th><th>概率推荐</th><th>赛果</th><th>比分</th>
<th>泊松W/D/L</th><th>综合W/D/L</th>
<th>EV</th><th>凯利</th>

</tr></thead>
<tbody id="matchBody"></tbody>
</table>
</div>
</div>

<div id="tab-daily" class="tab-content">
<div class="section-title">每日统计</div>
<div style="overflow-x:auto">
<table id="dailyTable">
<thead><tr><th>日期</th><th>总场次</th><th>已开奖</th><th>EV命中率</th><th>概率命中率</th><th>任一命中率</th></tr></thead>
<tbody id="dailyBody"></tbody>
</table>
</div>
</div>

<div id="tab-leagues" class="tab-content">
<div class="section-title">联赛统计</div>
<div style="overflow-x:auto">
<table id="leagueTable">
<thead><tr><th>联赛</th><th>总场次</th><th>EV命中</th><th>概率命中</th><th>EV率</th><th>概率率</th></tr></thead>
<tbody id="leagueBody"></tbody>
</table>
</div>
</div>


<!-- 泊松比分分布模态框 -->
<div id="poissonModal" class="modal-overlay" style="display:none">
<div class="modal-box">
<div class="modal-header">
  <span id="modalTitle">比分概率分布</span>
  <span class="modal-close" id="modalClose">&times;</span>
</div>
<div class="modal-controls">
  <label>加权 α: <input type="range" id="alphaSlider" min="0" max="100" value="50" style="width:120px;vertical-align:middle">
  <span id="alphaValue">0.50</span></label>
  <label><input type="checkbox" id="showHeatmap" checked> 热力图</label>
</div>
<div id="modalBarChart" class="modal-bars"></div>
<div id="modalHeatmap" class="modal-heatmap-wrap"></div>
</div>
</div>

<!-- 亚盘赔率详情模态框 -->
<div id="ahModal" class="modal-overlay" style="display:none">
<div class="modal-box ah-modal-box">
<div class="modal-header">
  <span id="ahModalTitle">赔率详情</span>
  <span class="modal-close" id="ahModalClose">&times;</span>
</div>
<div id="ahModalContent"></div>
</div>
</div>
<script>const LEAGUE_SCORE_FREQ = {league_freq_json};</script>
<script src="script.js"></script>
</body>
</html>'''

    path = os.path.join(output_dir, 'index.html')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'✅ index.html → {path}')
    return path


def main():
    parser = argparse.ArgumentParser(description='processed → results.json + index.html')
    parser.add_argument('--json-only', action='store_true')
    parser.add_argument('--html-only', action='store_true')
    parser.add_argument('--db', type=str, default=None)
    parser.add_argument('--output', type=str, default=None)
    args = parser.parse_args()

    db_path = args.db or DB_PATH

    # 优先读processed，再用DB补充缺失日期和新增字段
    by_date = load_from_processed()
    if os.path.exists(db_path):
        db_data = load_from_db(db_path)
        if db_data:
            missing = {k: v for k, v in db_data.items() if k not in by_date}
            if missing:
                by_date.update(missing)
                print(f'📌 DB补充{len(missing)}天缺失数据: {sorted(missing.keys())}')
            # DB中同日期记录数更多时也更新（processed可能缺新插入的场次）
            # 同时用DB的新字段（pin_ah/ou等）补充processed旧记录
            _NEW_KEYS = {'pin_ah', 'pin_ou', 'ou', 'liji_ou', 'ms_ou'}
            n_merged = 0
            for d_key, db_records in db_data.items():
                if d_key in by_date:
                    if len(db_records) > len(by_date[d_key]):
                        by_date[d_key] = db_records
                    else:
                        # 用DB的新字段补充processed旧记录
                        db_by_id = {r['id']: r for r in db_records}
                        for proc_rec in by_date[d_key]:
                            db_rec = db_by_id.get(proc_rec.get('id'))
                            if db_rec:
                                for k in _NEW_KEYS:
                                    if k not in proc_rec and k in db_rec:
                                        proc_rec[k] = db_rec[k]
                                        n_merged += 1
            if n_merged:
                print(f'📌 DB补充{n_merged}条记录的新字段')
    if not by_date:
        print('[ERROR] 无数据'); sys.exit(1)

    daily_stats = build_daily_stats(by_date)
    summary = build_summary(daily_stats)

    # 联赛比分频率（用于前端加权排序）
    league_score_freq = build_league_score_freq(db_path)
    n_leagues = len(league_score_freq)
    n_scores = sum(len(v) for v in league_score_freq.values())
    print(f'📊 {len(by_date)}天, {summary["total_matches"]}场已开奖, EV={summary["ev_rate"]}%, 概率={summary["prob_rate"]}%')
    print(f'   联赛比分频率: {n_leagues}个联赛, {n_scores}个比分记录')

    out_base = args.output or REPO_DIR
    # results.json 必须输出到 docs/data/ 下，GitHub Pages 才能访问
    if not args.html_only:
        generate_results_json(by_date, daily_stats, summary, os.path.join(out_base, 'docs', 'data'))
    if not args.json_only:
        generate_index_html(by_date, daily_stats, summary, league_score_freq, os.path.join(out_base, 'docs'))

    print('✅ 构建完成')


if __name__ == '__main__':
    main()
