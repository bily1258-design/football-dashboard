#!/usr/bin/env python3
"""从 docs/data/beidan_*_dashboard.xlsx 生成完整北单看板 HTML。
自动扫描所有期数，每期展示全部赛事（不截断），下拉选择期数，最新期默认选中。
"""
import glob
import os
import re

import openpyxl

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE, 'docs', 'data')
OUT = os.path.join(BASE, 'docs', 'beidan_dashboard.html')


def prob_color(p):
    """概率条颜色：>=40红 30-39橙 20-29蓝 <20灰"""
    if p >= 40:
        return '#c62828'
    if p >= 30:
        return '#e65100'
    if p >= 20:
        return '#1565c0'
    return '#999'


def parse_xlsx(path):
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if len(rows) < 4:
        return []
    matches = []
    for r in rows[3:]:
        if r[0] is None:
            continue
        try:
            no = int(r[0])
        except (TypeError, ValueError):
            continue
        def _pct(v):
            try:
                return float(str(v).replace('%', '').strip())
            except (TypeError, ValueError):
                return 0.0

        # 新布局(≥12列): 6比分 7赛果 8亚盘 9/10/11概率; 旧布局(10列): 6亚盘 7/8/9概率
        if len(r) >= 12:
            score, result = str(r[6] or ''), str(r[7] or '')
            ah, pw, pd, pa = str(r[8] or ''), _pct(r[9]), _pct(r[10]), _pct(r[11])
        else:
            score, result = '', ''
            ah, pw, pd, pa = str(r[6] or ''), _pct(r[7]), _pct(r[8]), _pct(r[9])

        matches.append({
            'no': no,
            'league': str(r[1] or ''),
            'time': str(r[2] or ''),
            'home': str(r[3] or ''),
            'hdcp': str(r[4] or ''),
            'away': str(r[5] or ''),
            'score': score,
            'result': result,
            'ah': ah,
            'pw': pw,
            'pd': pd,
            'pa': pa,
        })
    return matches


def date_range(matches):
    dates = []
    for m in matches:
        mt = m['time'].strip()
        mo = re.match(r'(\d{2})-(\d{2})', mt)
        if mo:
            dates.append((mo.group(1), mo.group(2)))
    if not dates:
        return '—'
    ds = sorted(set(dates))
    if len(ds) == 1:
        return f"{ds[0][0]}/{ds[0][1]}"
    return f"{ds[0][0]}/{ds[0][1]} ～ {ds[-1][0]}/{ds[-1][1]}"


def hdcp_class(h):
    if h.startswith('+'):
        return 'hdcp-pos'
    if h.startswith('-'):
        return 'hdcp-neg'
    return ''


def gen_row(m):
    def bar(p):
        return (f'<div class="prob-bar"><div class="prob-fill" '
                f'style="width:{min(p,100):.0f}%;background:{prob_color(p)}">{p:.0f}%</div></div>')
    return f'''<tr>
  <td>{m['no']}</td>
  <td class="league">{m['league']}</td>
  <td class="time">{m['time']}</td>
  <td>{m['home']}</td>
  <td class="hdcp {hdcp_class(m['hdcp'])}">{m['hdcp']}</td>
  <td>{m['away']}</td>
  <td class="score">{m['score'] or '-'}</td>
  <td class="result">{m['result']}</td>
  <td class="ah-desc">{m['ah']}</td>
  <td>{bar(m['pw'])}</td>
  <td>{bar(m['pd'])}</td>
  <td>{bar(m['pa'])}</td>
</tr>'''


def main():
    xlsx_files = sorted(glob.glob(os.path.join(DATA_DIR, 'beidan_*_dashboard.xlsx')))
    if not xlsx_files:
        print('❌ 未找到任何 beidan_*_dashboard.xlsx')
        return 1

    periods = []  # (expect, matches)
    for f in xlsx_files:
        expect = os.path.basename(f).replace('beidan_', '').replace('_dashboard.xlsx', '')
        ms = parse_xlsx(f)
        if ms:
            periods.append((expect, ms))
    if not periods:
        print('❌ 所有 xlsx 均为空')
        return 1

    periods.sort(key=lambda x: x[0])
    latest = periods[-1][0]

    # ---- 下拉 ----
    opts = ['      <option value="all">📋 全部期数</option>']
    for expect, ms in periods:
        sel = ' selected' if expect == latest else ''
        opts.append(f'      <option value="{expect}"{sel}>第{expect}期 ({date_range(ms)})</option>')
    select_html = '\n'.join(opts)

    # ---- 摘要卡 ----
    cards = []
    for expect, ms in periods:
        leagues = {}
        for m in ms:
            leagues[m['league']] = leagues.get(m['league'], 0) + 1
        league_tags = ''.join(
            f'<span class="league-tag">{lg}({cnt})</span>'
            for lg, cnt in sorted(leagues.items(), key=lambda x: -x[1])
        )
        active = ' active' if expect == latest else ''
        cards.append(f'''<div class="card period-group{active}" data-period="{expect}">
  <h2>第{expect}期 <span style="font-size:13px;color:#999;font-weight:400">({date_range(ms)})</span></h2>
  <div class="stat"><span class="label">比赛场次</span><span class="value">{len(ms)}</span></div>
  <div class="stat"><span class="label">联赛分布</span><span class="value" style="font-size:13px">{len(leagues)}个联赛</span></div>
  <div style="margin-top:8px">{league_tags}</div>
  <div style="margin-top:10px;text-align:center">
    <a href="https://github.com/bily1258-design/football-odds-api/raw/main/beidan_{expect}_dashboard.xlsx" style="display:inline-block;background:#283593;color:white;text-decoration:none;padding:6px 20px;border-radius:6px;font-size:13px">📥 下载Excel</a>
  </div>
</div>''')

    # ---- 表格 ----
    tables = []
    for expect, ms in periods:
        active = ' active' if expect == latest else ''
        rows = '\n'.join(gen_row(m) for m in ms)
        tables.append(f'''<div class="table-wrap period-group{active}" data-period="{expect}">
  <h3>第{expect}期 比赛列表 ({len(ms)}场) · 皇冠历史相同亚盘概率</h3>
  <div style="overflow-x:auto">
  <table>
    <thead><tr>
      <th>#</th><th>联赛</th><th>时间</th><th>主队</th><th>让球</th><th>客队</th>
      <th>比分</th><th>赛果</th><th>亚盘</th><th>主胜%<br><span class="sub-hdr">亚盘</span></th><th>平%<br><span class="sub-hdr">亚盘</span></th><th>客负%<br><span class="sub-hdr">亚盘</span></th>
    </tr></thead>
    <tbody>{rows}
</tbody>
  </table>
  </div>
  <p class="match-count">共{len(ms)}场 · <a href="https://github.com/bily1258-design/football-odds-api/raw/main/beidan_{expect}_dashboard.xlsx" style="color:#283593">下载Excel</a></p>
</div>''')

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>北单看板 - 第{latest}期</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,'Microsoft YaHei',sans-serif; background:#f5f7fa; color:#333; }}
.header {{ background:linear-gradient(135deg,#1a237e,#283593); color:white; padding:24px 20px; text-align:center; }}
.header h1 {{ font-size:22px; margin-bottom:4px; }}
.header p {{ font-size:13px; opacity:.8; }}
.header select {{ margin-top:10px; padding:8px 16px; border-radius:6px; border:none; font-size:14px; background:#3949ab; color:#fff; cursor:pointer; outline:none; }}
.header select option {{ background:#283593; color:#fff; }}
.container {{ max-width:1400px; margin:0 auto; padding:16px; }}
.summary-cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:12px; margin-bottom:20px; }}
.card {{ background:white; border-radius:10px; padding:16px; box-shadow:0 2px 8px rgba(0,0,0,.08); }}
.card h2 {{ font-size:16px; color:#1a237e; margin-bottom:8px; }}
.card .stat {{ display:flex; justify-content:space-between; padding:4px 0; font-size:14px; border-bottom:1px solid #eee; }}
.card .stat:last-child {{ border-bottom:none; }}
.card .label {{ color:#666; }}
.card .value {{ font-weight:600; }}
.league-tag {{ display:inline-block; background:#e8eaf6; color:#283593; border-radius:12px; padding:2px 10px; font-size:12px; margin:2px; }}
.table-wrap {{ background:white; border-radius:10px; overflow:hidden; box-shadow:0 2px 8px rgba(0,0,0,.08); margin-bottom:20px; }}
.table-wrap h3 {{ background:#283593; color:white; padding:10px 16px; font-size:14px; }}
table {{ width:100%; border-collapse:collapse; font-size:12px; }}
th {{ background:#e8eaf6; color:#283593; padding:6px 4px; text-align:center; font-weight:600; position:sticky; top:0; white-space:nowrap; }}
td {{ padding:5px 4px; text-align:center; border-bottom:1px solid #f0f0f0; }}
tr:nth-child(even) td {{ background:#fafafa; }}
.league {{ color:#666; font-size:11px; }}
.time {{ color:#888; font-size:11px; font-family:monospace; }}
.hdcp {{ font-weight:600; }}
.hdcp-neg {{ color:#2e7d32; }}
.hdcp-pos {{ color:#c62828; }}
.prob-bar {{ display:inline-block; width:60px; height:14px; background:#eee; border-radius:7px; vertical-align:middle; position:relative; overflow:hidden; }}
.prob-fill {{ height:100%; border-radius:7px; line-height:14px; font-size:9px; color:#fff; text-align:center; }}
.ah-desc {{ color:#555; font-size:11px; }}
.score {{ font-family:monospace; font-weight:600; color:#333; font-size:13px; }}
.result {{ font-weight:600; color:#1565c0; }}
.sub-hdr {{ font-weight:400; font-size:10px; color:#78909c; }}
.footer {{ text-align:center; padding:20px; color:#999; font-size:12px; }}
.period-group {{ display:none; }}
.period-group.active {{ display:block; }}
.match-count {{ padding:10px; text-align:center; color:#999; font-size:13px; }}
</style>
</head>
<body>
<div class="header">
  <h1>⚽ 北京单场看板</h1>
  <p id="updateInfo">数据更新：{latest}期 · 共{sum(len(ms) for _, ms in periods)}场</p>
  <p style="margin-top:6px;font-size:12px;opacity:.7">数据来源：500.com 皇冠公司历史相同亚盘</p>
  <div style="margin-top:10px">
    <select id="periodSelect" onchange="switchPeriod()">
{select_html}
    </select>
  </div>
</div>
<div class="container">

<div class="summary-cards" id="summaryCards">
{chr(10).join(cards)}
</div>

{chr(10).join(tables)}

</div>
<div class="footer">
  <p>⚡ 自动生成 · 概率数据来自500.com 皇冠历史相同亚盘</p>
</div>

<script>
function switchPeriod() {{
  var val = document.getElementById('periodSelect').value;
  var groups = document.querySelectorAll('.period-group');
  groups.forEach(function(el) {{
    if (val === 'all') {{
      el.classList.add('active');
    }} else {{
      if (el.getAttribute('data-period') === val) {{
        el.classList.add('active');
      }} else {{
        el.classList.remove('active');
      }}
    }}
  }});
}}
</script>
</body>
</html>
'''
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'✅ 已生成 {OUT}')
    print(f'   期数: {", ".join(e for e, _ in periods)} (最新: {latest})')
    print(f'   总场次: {sum(len(ms) for _, ms in periods)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
