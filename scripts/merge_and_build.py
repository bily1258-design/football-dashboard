#!/usr/bin/env python3
"""merge_and_build.py - 读取DB数据 → 生成 results.json + 静态 index.html

职责：
1. 从 football.db 读取所有预测+赛果数据
2. 输出 data/results.json（结构化数据，供前端/API使用）
3. 输出 docs/index.html（静态看板页面）
4. 支持 --json-only / --html-only 参数

数据流：football.db → merge_and_build.py → results.json + index.html
"""

import os
import sys
import json
import sqlite3
import re
import math
import argparse
from datetime import datetime, timedelta
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# DB路径：优先从环境变量取，否则用相对路径
DB_PATH = os.environ.get('FOOTBALL_DB_PATH',
    os.path.join(SCRIPT_DIR, '..', 'data', 'shared_state', 'football.db'))
DATA_DIR = os.path.join(SCRIPT_DIR, '..', 'data')
DOCS_DIR = os.path.join(SCRIPT_DIR, '..', 'docs')

WEEKDAY_CN = ['周一','周二','周三','周四','周五','周六','周日']

# ========== 队名别名映射 ==========
TEAM_ALIAS = {
    '皇马': '皇家马德里', '曼城': '曼彻斯特城', '巴萨': '巴塞罗那',
    '奥维耶多': '皇家奥维耶多', '阿德莱德': '阿德莱德联',
    '中央海岸': '中央海岸水手', '惠灵顿': '惠灵顿凤凰',
    '纽卡素': '纽卡斯尔联', '热刺': '托特纳姆热刺',
    '狼队': '伍尔弗汉普顿', '狐狸城': '莱斯特城',
}


def load_predictions(db_path=None, max_days=999):
    """从DB读取所有预测数据，按日期分组"""
    if not db_path:
        db_path = DB_PATH
    if not os.path.exists(db_path):
        print(f'[ERROR] DB not found: {db_path}')
        return {}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cutoff = (datetime.now() - timedelta(days=max_days)).strftime('%Y-%m-%d') if max_days < 999 else '2000-01-01'

    cur.execute("""
        SELECT id, date, league, home_team, away_team, kickoff_time,
            prediction, prediction_prob,
            odds_win, odds_draw, odds_loss,
            poisson_win, poisson_draw, poisson_loss,
            final_win, final_draw, final_loss,
            fusion_win, fusion_draw, fusion_loss,
            actual_outcome, risk_level, confidence_index, reference_score,
            best_direction_cn, source,
            ev_win, ev_draw, ev_loss,
            kelly_win, kelly_draw, kelly_loss,
            pinnacle_close_w, pinnacle_close_d, pinnacle_close_l,
            hkjc_close_w, hkjc_close_d, hkjc_close_l,
            cold_risk, odds_source,
            home_lambda, away_lambda,
            home_ranking, away_ranking
        FROM poisson_predictions
        WHERE date >= ?
        ORDER BY date DESC, kickoff_time, id
    """, (cutoff,))

    by_date = {}
    for row in cur.fetchall():
        r = dict(row)
        d = r['date']
        if d not in by_date:
            by_date[d] = []
        by_date[d].append(serialize_record(r))
    conn.close()
    return by_date


def serialize_record(r):
    """将DB记录序列化为前端友好的结构"""
    # 解析赛果
    score = ''
    result_label = ''
    if r.get('actual_outcome'):
        m = re.search(r'(\d+-\d+)', r['actual_outcome'])
        if m:
            score = m.group(1)
        if '主胜' in r['actual_outcome']:
            result_label = '主胜'
        elif '客胜' in r['actual_outcome']:
            result_label = '客胜'
        elif '平局' in r['actual_outcome']:
            result_label = '平局'

    # 判断方向是否命中
    ev_dir = r.get('best_direction_cn') or r.get('prediction') or ''
    ev_hit = False
    if result_label and ev_dir:
        ev_hit = (ev_dir == result_label)

    # 综合概率最高方向
    fw, fd, fl = r.get('final_win', 0) or 0, r.get('final_draw', 0) or 0, r.get('final_loss', 0) or 0
    prob_dir = '主胜' if fw >= fd and fw >= fl else ('客胜' if fl >= fw and fl >= fd else '平局')
    prob_hit = (prob_dir == result_label) if result_label else False

    # 融合概率最高方向
    rw, rd, rl = r.get('fusion_win', 0) or 0, r.get('fusion_draw', 0) or 0, r.get('fusion_loss', 0) or 0
    fusion_dir = '主胜' if rw >= rd and rw >= rl else ('客胜' if rl >= rw and rl >= rd else '平局')

    # 信心指数（★星级）
    confidence = r.get('confidence_index') or 0
    if isinstance(confidence, (int, float)) and confidence > 0:
        stars = min(5, max(1, round(confidence * 5)))
    else:
        stars = 0

    # EV最优方向
    ew, ed, el = r.get('ev_win', 0) or 0, r.get('ev_draw', 0) or 0, r.get('ev_loss', 0) or 0
    ev_values = {'主胜': ew, '平局': ed, '客胜': el}
    best_ev_dir = max(ev_values, key=ev_values.get) if any(v > 0 for v in ev_values.values()) else ''

    return {
        'id': r['id'],
        'date': r['date'],
        'league': r.get('league', ''),
        'home': r['home_team'],
        'away': r['away_team'],
        'kickoff': r.get('kickoff_time', ''),
        'prediction': r.get('prediction', ''),
        'prediction_prob': round(r.get('prediction_prob', 0) or 0, 3),
        'odds': {
            'w': r.get('odds_win', 0) or 0,
            'd': r.get('odds_draw', 0) or 0,
            'l': r.get('odds_loss', 0) or 0,
        },
        'poisson': {
            'w': round(r.get('poisson_win', 0) or 0, 3),
            'd': round(r.get('poisson_draw', 0) or 0, 3),
            'l': round(r.get('poisson_loss', 0) or 0, 3),
        },
        'final_prob': {
            'w': round(fw, 3),
            'd': round(fd, 3),
            'l': round(fl, 3),
        },
        'fusion_prob': {
            'w': round(rw, 3),
            'd': round(rd, 3),
            'l': round(rl, 3),
        },
        'result': result_label,
        'score': score,
        'ev_direction': ev_dir,
        'ev_hit': ev_hit,
        'prob_direction': prob_dir,
        'prob_hit': prob_hit,
        'fusion_direction': fusion_dir,
        'best_ev_direction': best_ev_dir,
        'ev': {
            'w': round(ew, 4),
            'd': round(ed, 4),
            'l': round(el, 4),
        },
        'kelly': {
            'w': round(r.get('kelly_win', 0) or 0, 4),
            'd': round(r.get('kelly_draw', 0) or 0, 4),
            'l': round(r.get('kelly_loss', 0) or 0, 4),
        },
        'pinnacle': {
            'w': r.get('pinnacle_close_w', 0) or 0,
            'd': r.get('pinnacle_close_d', 0) or 0,
            'l': r.get('pinnacle_close_l', 0) or 0,
        },
        'hkjc': {
            'w': r.get('hkjc_close_w', 0) or 0,
            'd': r.get('hkjc_close_d', 0) or 0,
            'l': r.get('hkjc_close_l', 0) or 0,
        },
        'risk_level': r.get('risk_level', '') or '',
        'stars': stars,
        'confidence_index': round(r.get('confidence_index', 0) or 0, 2),
        'reference_score': r.get('reference_score', '') or '',
        'cold_risk': r.get('cold_risk', '') or '',
        'source': r.get('source', 'jingcai'),
        'odds_source': r.get('odds_source', 'had'),
        'home_lambda': round(r.get('home_lambda', 0) or 0, 3),
        'away_lambda': round(r.get('away_lambda', 0) or 0, 3),
        'home_ranking': r.get('home_ranking', 0) or 0,
        'away_ranking': r.get('away_ranking', 0) or 0,
    }


def build_daily_stats(by_date):
    """计算每日统计"""
    stats = {}
    for date, records in by_date.items():
        total = len(records)
        with_result = [r for r in records if r.get('result')]
        ev_hits = sum(1 for r in with_result if r.get('ev_hit'))
        prob_hits = sum(1 for r in with_result if r.get('prob_hit'))
        any_hits = sum(1 for r in with_result if r.get('ev_hit') or r.get('prob_hit'))
        n = len(with_result)
        stats[date] = {
            'total': total,
            'with_result': n,
            'ev_hits': ev_hits,
            'prob_hits': prob_hits,
            'any_hits': any_hits,
            'ev_rate': round(ev_hits / n * 100, 1) if n else 0,
            'prob_rate': round(prob_hits / n * 100, 1) if n else 0,
            'any_rate': round(any_hits / n * 100, 1) if n else 0,
        }
    return stats


def build_summary(daily_stats):
    """全局准确率汇总"""
    total_ev = total_prob = total_any = total_n = 0
    league_stats = defaultdict(lambda: {'total': 0, 'ev_hit': 0, 'prob_hit': 0})
    for date, s in sorted(daily_stats.items()):
        total_n += s['with_result']
        total_ev += s['ev_hits']
        total_prob += s['prob_hits']
        total_any += s['any_hits']
    return {
        'total_matches': total_n,
        'ev_hits': total_ev,
        'prob_hits': total_prob,
        'any_hits': total_any,
        'ev_rate': round(total_ev / total_n * 100, 1) if total_n else 0,
        'prob_rate': round(total_prob / total_n * 100, 1) if total_n else 0,
        'any_rate': round(total_any / total_n * 100, 1) if total_n else 0,
        'days': len(daily_stats),
        'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }


def generate_results_json(by_date, daily_stats, summary, output_dir=None):
    """生成 results.json"""
    if not output_dir:
        output_dir = os.path.join(SCRIPT_DIR, '..', 'data')
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
    """生成静态 index.html 看板"""
    if not output_dir:
        output_dir = os.path.join(SCRIPT_DIR, '..', 'docs')
    os.makedirs(output_dir, exist_ok=True)

    dates = sorted(by_date.keys(), reverse=True)
    today = datetime.now().strftime('%Y-%m-%d')
    default_date = today if today in by_date else (dates[0] if dates else '')

    # 构建日期选项
    date_options = '\n'.join(
        f'<option value="{d}"{" selected" if d == default_date else ""}>{d} {WEEKDAY_CN[datetime.strptime(d,"%Y-%m-%d").weekday()]}</option>'
        for d in dates
    )

    # 构建每日统计行
    daily_rows = ''
    for d in dates:
        s = daily_stats.get(d, {})
        n = s.get('with_result', 0)
        color_ev = '#4caf50' if s.get('ev_rate', 0) >= 55 else '#f44336' if s.get('ev_rate', 0) < 40 else '#ff9800'
        color_prob = '#4caf50' if s.get('prob_rate', 0) >= 60 else '#f44336' if s.get('prob_rate', 0) < 45 else '#ff9800'
        daily_rows += f'''<tr>
<td>{d}</td><td>{s.get('total',0)}</td><td>{n}</td>
<td style="color:{color_ev}">{s.get('ev_rate',0)}% ({s.get('ev_hits',0)}/{n})</td>
<td style="color:{color_prob}">{s.get('prob_rate',0)}% ({s.get('prob_hits',0)}/{n})</td>
</tr>\n'''

    # 全局汇总
    sm = summary
    color_sev = '#4caf50' if sm['ev_rate'] >= 55 else '#f44336' if sm['ev_rate'] < 40 else '#ff9800'
    color_sprob = '#4caf50' if sm['prob_rate'] >= 60 else '#f44336' if sm['prob_rate'] < 45 else '#ff9800'

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>竞彩泊松预测看板</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; background:#0d1117; color:#c9d1d9; padding:16px; }}
.header {{ text-align:center; padding:20px 0; border-bottom:1px solid #30363d; margin-bottom:20px; }}
.header h1 {{ color:#58a6ff; font-size:24px; margin-bottom:8px; }}
.header .sub {{ color:#8b949e; font-size:13px; }}
.summary-cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin-bottom:20px; }}
.card {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:16px; text-align:center; }}
.card .label {{ font-size:12px; color:#8b949e; margin-bottom:4px; }}
.card .value {{ font-size:28px; font-weight:bold; }}
.card .value.green {{ color:#4caf50; }}
.card .value.red {{ color:#f44336; }}
.card .value.orange {{ color:#ff9800; }}
.card .value.blue {{ color:#58a6ff; }}
.controls {{ display:flex; gap:12px; align-items:center; margin-bottom:16px; flex-wrap:wrap; }}
.controls select {{ background:#161b22; color:#c9d1d9; border:1px solid #30363d; padding:8px 12px; border-radius:6px; font-size:14px; }}
.controls label {{ color:#8b949e; font-size:13px; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th {{ background:#161b22; color:#58a6ff; padding:10px 8px; text-align:left; border-bottom:2px solid #30363d; position:sticky; top:0; }}
td {{ padding:8px; border-bottom:1px solid #21262d; }}
tr:hover {{ background:#161b22; }}
.hit {{ color:#4caf50; font-weight:bold; }}
.miss {{ color:#f44336; }}
.pending {{ color:#8b949e; font-style:italic; }}
.ev-pos {{ color:#4caf50; }}
.ev-neg {{ color:#f44336; }}
.tabs {{ display:flex; gap:4px; margin-bottom:16px; }}
.tab {{ padding:8px 16px; background:#161b22; border:1px solid #30363d; border-radius:6px 6px 0 0; cursor:pointer; color:#8b949e; font-size:13px; }}
.tab.active {{ background:#0d1117; color:#58a6ff; border-bottom:2px solid #58a6ff; }}
.tab-content {{ display:none; }}
.tab-content.active {{ display:block; }}
.daily-stats {{ max-height:300px; overflow-y:auto; }}
.badge {{ display:inline-block; padding:2px 6px; border-radius:4px; font-size:11px; }}
.badge-jc {{ background:#1a3a5c; color:#58a6ff; }}
.badge-bd {{ background:#3a1a5c; color:#bc8cff; }}
.badge-risk-high {{ background:#3a1a1a; color:#f44336; }}
.badge-risk-mid {{ background:#3a2a1a; color:#ff9800; }}
.badge-risk-low {{ background:#1a3a1a; color:#4caf50; }}
.odds-source {{ font-size:10px; color:#8b949e; margin-left:4px; }}
.section {{ margin-bottom:24px; }}
.section-title {{ color:#58a6ff; font-size:16px; margin-bottom:12px; padding-bottom:6px; border-bottom:1px solid #30363d; }}
</style>
</head>
<body>
<div class="header">
<h1>⚽ 竞彩泊松预测看板</h1>
<div class="sub">更新于 {sm['last_updated']} | 共 {sm['total_matches']} 场已开奖 | {sm['days']} 天数据</div>
</div>

<div class="summary-cards">
<div class="card"><div class="label">EV方向命中率</div><div class="value" style="color:{color_sev}">{sm['ev_rate']}%</div><div class="label">{sm['ev_hits']}/{sm['total_matches']}</div></div>
<div class="card"><div class="label">概率最高命中率</div><div class="value" style="color:{color_sprob}">{sm['prob_rate']}%</div><div class="label">{sm['prob_hits']}/{sm['total_matches']}</div></div>
<div class="card"><div class="label">任一命中</div><div class="value blue">{sm['any_rate']}%</div><div class="label">{sm['any_hits']}/{sm['total_matches']}</div></div>
<div class="card"><div class="label">总天数</div><div class="value blue">{sm['days']}</div></div>
</div>

<div class="tabs">
<div class="tab active" onclick="switchTab('matches')">📊 比赛数据</div>
<div class="tab" onclick="switchTab('daily')">📈 每日统计</div>
</div>

<div id="tab-matches" class="tab-content active">
<div class="controls">
<label>日期：</label>
<select id="dateSelect" onchange="loadDate()">{date_options}</select>
<label><input type="checkbox" id="showResulted" checked onchange="loadDate()"> 已开奖</label>
<label><input type="checkbox" id="showPending" checked onchange="loadDate()"> 待开奖</label>
</div>
<div style="overflow-x:auto">
<table id="matchTable">
<thead><tr>
<th>编号</th><th>联赛</th><th>时间</th><th>主队</th><th>客队</th>
<th>推荐方向</th><th>概率</th><th>赛果</th><th>比分</th>
<th>胜赔</th><th>平赔</th><th>负赔</th>
<th>泊松W/D/L</th><th>综合W/D/L</th>
<th>EV(W/D/L)</th><th>凯利W/D/L</th>
<th>Pinnacle</th><th>风险</th><th>★</th>
</tr></thead>
<tbody id="matchBody"></tbody>
</table>
</div>
</div>

<div id="tab-daily" class="tab-content">
<div class="section-title">每日统计</div>
<div class="daily-stats" style="overflow-x:auto">
<table>
<thead><tr><th>日期</th><th>总场次</th><th>已开奖</th><th>EV命中率</th><th>概率命中率</th></tr></thead>
<tbody>{daily_rows}</tbody>
</table>
</div>
</div>

<script>
const DATA_URL = 'data/results.json';
let allData = null;

async function loadData() {{
    try {{
        const resp = await fetch(DATA_URL);
        allData = await resp.json();
        loadDate();
    }} catch(e) {{
        console.error('Failed to load data:', e);
        document.getElementById('matchBody').innerHTML = '<tr><td colspan="19" style="text-align:center;color:#f44336">数据加载失败，请刷新重试</td></tr>';
    }}
}}

function loadDate() {{
    if (!allData) return;
    const sel = document.getElementById('dateSelect').value;
    const showResulted = document.getElementById('showResulted').checked;
    const showPending = document.getElementById('showPending').checked;
    const records = allData.matches[sel] || [];
    const tbody = document.getElementById('matchBody');
    let html = '';
    records.forEach((r, i) => {{
        const hasResult = !!r.result;
        if (hasResult && !showResulted) return;
        if (!hasResult && !showPending) return;
        const dirClass = r.ev_hit ? 'hit' : (hasResult ? 'miss' : 'pending');
        const resultClass = r.ev_hit ? 'hit' : (hasResult ? 'miss' : 'pending');
        const srcBadge = r.source === 'beidan' ? '<span class="badge badge-bd">北单</span>' : '<span class="badge badge-jc">竞彩</span>';
        const riskBadge = r.risk_level ? `<span class="badge badge-risk-${{r.risk_level==='高'?'high':r.risk_level==='中'?'mid':'low'}}">${{r.risk_level}}</span>` : '';
        const stars = '★'.repeat(r.stars) + '☆'.repeat(5-r.stars);
        const evW = r.ev.w, evD = r.ev.d, evL = r.ev.l;
        const evClass = v => v > 0 ? 'ev-pos' : 'ev-neg';
        const pinStr = r.pinnacle.w > 0 ? `${{r.pinnacle.w}}/${{r.pinnacle.d}}/${{r.pinnacle.l}}` : '-';
        const probDir = r.prob_direction;
        const fusionDir = r.fusion_direction;

        html += `<tr>
<td>${{i+1}} ${{srcBadge}}</td>
<td>${{r.league}}</td>
<td>${{r.kickoff ? r.kickoff.substring(11,16) : ''}}</td>
<td>${{r.home}}</td><td>${{r.away}}</td>
<td class="${{dirClass}}">${{r.ev_direction || '-'}}</td>
<td>${{(r.prediction_prob*100).toFixed(1)}}%</td>
<td class="${{resultClass}}">${{r.result || '待定'}}</td>
<td>${{r.score || '-'}}</td>
<td>${{r.odds.w||'-'}}</td><td>${{r.odds.d||'-'}}</td><td>${{r.odds.l||'-'}}</td>
<td>${{r.poisson.w}}/${{r.poisson.d}}/${{r.poisson.l}}</td>
<td>${{r.final_prob.w}}/${{r.final_prob.d}}/${{r.final_prob.l}}</td>
<td><span class="${{evClass(evW)}}">${{evW.toFixed(2)}}</span>/<span class="${{evClass(evD)}}">${{evD.toFixed(2)}}</span>/<span class="${{evClass(evL)}}">${{evL.toFixed(2)}}</span></td>
<td>${{r.kelly.w}}/${{r.kelly.d}}/${{r.kelly.l}}</td>
<td>${{pinStr}}</td>
<td>${{riskBadge}}</td>
<td title="${{r.confidence_index}}">${{stars}}</td>
</tr>`;
    }});
    tbody.innerHTML = html || '<tr><td colspan="19" style="text-align:center;color:#8b949e">无数据</td></tr>';
}}

function switchTab(name) {{
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    event.target.classList.add('active');
    document.getElementById('tab-'+name).classList.add('active');
}}

document.addEventListener('DOMContentLoaded', loadData);
</script>
</body>
</html>'''

    path = os.path.join(output_dir, 'index.html')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'✅ index.html → {path}')
    return path


def main():
    parser = argparse.ArgumentParser(description='合并DB数据生成看板')
    parser.add_argument('--json-only', action='store_true', help='仅生成 results.json')
    parser.add_argument('--html-only', action='store_true', help='仅生成 index.html')
    parser.add_argument('--db', type=str, help='数据库路径')
    parser.add_argument('--output', type=str, help='输出目录（默认 football-dashboard/ 根目录）')
    args = parser.parse_args()

    db_path = args.db or DB_PATH
    print(f'📖 读取DB: {db_path}')

    by_date = load_predictions(db_path)
    if not by_date:
        print('[ERROR] 无数据')
        sys.exit(1)

    daily_stats = build_daily_stats(by_date)
    summary = build_summary(daily_stats)
    print(f'📊 {len(by_date)} 天, {summary["total_matches"]} 场已开奖, EV={summary["ev_rate"]}%, 概率={summary["prob_rate"]}%')

    if not args.html_only:
        generate_results_json(by_date, daily_stats, summary, 
            os.path.join(args.output, 'data') if args.output else None)

    if not args.json_only:
        generate_index_html(by_date, daily_stats, summary,
            os.path.join(args.output, 'docs') if args.output else None)

    print('✅ 构建完成')


if __name__ == '__main__':
    main()
