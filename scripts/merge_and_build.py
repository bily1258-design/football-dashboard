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
    os.path.join(REPO_DIR, '..', 'data', 'football.db'))

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
    cutoff = (datetime.now() - timedelta(days=max_days)).strftime('%Y-%m-%d') if max_days < 999 else '2000-01-01'
    cur.execute("SELECT id, date, league, home_team, away_team, kickoff_time, \
        prediction, prediction_prob, odds_win, odds_draw, odds_loss, \
        poisson_win, poisson_draw, poisson_loss, final_win, final_draw, final_loss, \
        fusion_win, fusion_draw, fusion_loss, actual_outcome, risk_level, \
        confidence_index, reference_score, best_direction_cn, source, \
        ev_win, ev_draw, ev_loss, kelly_win, kelly_draw, kelly_loss, \
        pinnacle_close_w, pinnacle_close_d, pinnacle_close_l, \
        hkjc_close_w, hkjc_close_d, hkjc_close_l, cold_risk, odds_source, \
        home_lambda, away_lambda, home_ranking, away_ranking \
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
            'pinnacle': {'w': d.get('pinnacle_close_w',0) or 0, 'd': d.get('pinnacle_close_d',0) or 0, 'l': d.get('pinnacle_close_l',0) or 0},
            'hkjc': {'w': d.get('hkjc_close_w',0) or 0, 'd': d.get('hkjc_close_d',0) or 0, 'l': d.get('hkjc_close_l',0) or 0},
            'risk_level': d.get('risk_level','') or '', 'stars': stars,
            'confidence_index': round(ci,2), 'reference_score': d.get('reference_score','') or '',
            'cold_risk': d.get('cold_risk','') or '', 'source': d.get('source','jingcai'),
            'odds_source': d.get('odds_source','had'),
            'home_lambda': round(d.get('home_lambda',0) or 0,3),
            'away_lambda': round(d.get('away_lambda',0) or 0,3),
            'home_ranking': d.get('home_ranking',0) or 0,
            'away_ranking': d.get('away_ranking',0) or 0,
        })
    conn.close()
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


def generate_index_html(by_date, daily_stats, summary, output_dir=None):
    """生成 index.html（引用外部 style.css + script.js）"""
    if not output_dir:
        output_dir = DOCS_DIR
    os.makedirs(output_dir, exist_ok=True)

    dates = sorted(by_date.keys(), reverse=True)
    today = datetime.now().strftime('%Y-%m-%d')
    default_date = today if today in by_date else (dates[0] if dates else '')

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>竞彩泊松预测看板</title>
<link rel="stylesheet" href="style.css">
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
</div>
<div style="overflow-x:auto">
<table id="matchTable">
<thead><tr>
<th>#</th><th>联赛</th><th>时间</th><th>主队</th><th>客队</th>
<th>推荐</th><th>概率推荐</th><th>赛果</th><th>比分</th>
<th>胜/平/负</th><th>泊松W/D/L</th><th>综合W/D/L</th>
<th>EV</th><th>凯利</th><th>Pinnacle</th><th>HKJC</th>
<th>★</th>
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

    # 优先读processed，fallback读DB
    by_date = load_from_processed()
    if not by_date:
        print(f'⚠️ processed为空，fallback读DB: {db_path}')
        by_date = load_from_db(db_path)
    if not by_date:
        print('[ERROR] 无数据'); sys.exit(1)

    daily_stats = build_daily_stats(by_date)
    summary = build_summary(daily_stats)
    print(f'📊 {len(by_date)}天, {summary["total_matches"]}场已开奖, EV={summary["ev_rate"]}%, 概率={summary["prob_rate"]}%')

    out_base = args.output or REPO_DIR
    # results.json 必须输出到 docs/data/ 下，GitHub Pages 才能访问
    if not args.html_only:
        generate_results_json(by_date, daily_stats, summary, os.path.join(out_base, 'docs', 'data'))
    if not args.json_only:
        generate_index_html(by_date, daily_stats, summary, os.path.join(out_base, 'docs'))

    print('✅ 构建完成')


if __name__ == '__main__':
    main()
