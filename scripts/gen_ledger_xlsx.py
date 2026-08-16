#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「投注簿」xlsx → 手机共享 Documents，供 WPS 打开存云文档。

用法: python3 gen_ledger_xlsx.py
输出: ~/storage/shared/Documents/投注簿_YYYYMMDD.xlsx
内容:
  Sheet1 总览: 状态汇总 + 按信号盈亏 + 按日盈亏 + 未结算大单
  Sheet2 明细: 全部255条(或按 --days N 过滤最近N天), 已结算/未结算着色
"""
import json, os, sys
from datetime import datetime
from collections import defaultdict
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER_PATH = os.path.join(PROJECT_DIR, 'docs', 'data', 'betting_ledger.json')
OUT_DIR = os.path.expanduser('~/storage/shared/Documents')

# 样式
HEADER_FONT = Font(bold=True, color='FFFFFF', size=11)
HEADER_FILL = PatternFill('solid', fgColor='2F5597')
WIN_FILL = PatternFill('solid', fgColor='C6EFCE')   # 中
LOSS_FILL = PatternFill('solid', fgColor='FFC7CE')  # 挂
PEND_FILL = PatternFill('solid', fgColor='FFEB9C')  # 未结算
WEIGHT_FILL = PatternFill('solid', fgColor='F2F2F2')  # 高权重追踪
TITLE_FONT = Font(bold=True, size=14, color='2F5597')
SECTION_FONT = Font(bold=True, size=12, color='404040')
THIN = Side(style='thin', color='BFBFBF')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

SIG_CN = {'value': '价值投注', 'ruleA': '客胜规则A', 'weight': '⚡高权重'}

def fmt_time(ts):
    return ts[:16].replace('T', ' ')

def main():
    days_filter = None
    if '--days' in sys.argv:
        i = sys.argv.index('--days')
        days_filter = int(sys.argv[i + 1])

    with open(LEDGER_PATH, encoding='utf-8') as f:
        recs = json.load(f)

    if days_filter is not None:
        cutoff = (datetime.now().strftime('%Y-%m-%d'))  # 起点=今天往前N天
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days_filter)).strftime('%Y-%m-%d')
        recs = [r for r in recs if r['match_time'][:10] >= cutoff]

    settled = [r for r in recs if r.get('result')]
    open_r = [r for r in recs if not r.get('result')]

    wb = Workbook()

    # ─── Sheet1 总览 ─────────────────────────────
    ws = wb.active
    ws.title = '总览'
    ws.column_dimensions['A'].width = 22
    ws.column_dimensions['B'].width = 16
    ws.column_dimensions['C'].width = 16
    ws.column_dimensions['D'].width = 40

    ws['A1'] = '📒 投注簿总览'
    ws['A1'].font = TITLE_FONT
    ws['A2'] = f'生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M")}  |  覆盖: {recs[0]["match_time"][:10] if recs else "-"} ~ {recs[-1]["match_time"][:10] if recs else "-"}'
    ws['A2'].font = Font(size=10, color='808080')

    row = 4
    ws.cell(row=row, column=1, value='状态汇总').font = SECTION_FONT
    row += 1
    ws.append(['指标', '数值', '', ''])
    ws.cell(row=row - 1, column=1).font = HEADER_FONT
    ws.cell(row=row - 1, column=1).fill = HEADER_FILL
    ws.cell(row=row - 1, column=2).font = HEADER_FONT
    ws.cell(row=row - 1, column=2).fill = HEADER_FILL
    summary = [
        ('总条数', len(recs)),
        ('已结算', len(settled)),
        ('未结算', len(open_r)),
        ('命中', sum(1 for r in settled if r.get('result') == 'win')),
        ('总盈亏', round(sum((r.get('odds', 0) - 1) if r.get('result') == 'win' else (-1 if r.get('result') == 'loss' else 0) for r in settled), 2)),
    ]
    for k, v in summary:
        row += 1
        ws.cell(row=row, column=1, value=k)
        ws.cell(row=row, column=2, value=v)
        for c in (ws.cell(row=row, column=1), ws.cell(row=row, column=2)):
            c.border = BORDER

    # 按信号盈亏
    row += 2
    ws.cell(row=row, column=1, value='按信号盈亏').font = SECTION_FONT
    row += 1
    ws.cell(row=row, column=1, value='信号').font = HEADER_FONT
    ws.cell(row=row, column=1).fill = HEADER_FILL
    ws.cell(row=row, column=2, value='条数').font = HEADER_FONT
    ws.cell(row=row, column=2).fill = HEADER_FILL
    ws.cell(row=row, column=3, value='命中').font = HEADER_FONT
    ws.cell(row=row, column=3).fill = HEADER_FILL
    ws.cell(row=row, column=4, value='盈亏').font = HEADER_FONT
    ws.cell(row=row, column=4).fill = HEADER_FILL
    by_sig = defaultdict(list)
    for r in recs:
        by_sig[r.get('signal', '?')].append(r)
    for sig, items in sorted(by_sig.items(), key=lambda x: -sum((it.get('odds', 0) - 1) if it.get('result') == 'win' else (-1 if it.get('result') == 'loss' else 0) for it in x[1])):
        s = items
        wins = sum(1 for r in s if r.get('result') == 'win')
        pnl = round(sum((r.get('odds', 0) - 1) if r.get('result') == 'win' else (-1 if r.get('result') == 'loss' else 0) for r in s), 2)
        row += 1
        ws.cell(row=row, column=1, value=SIG_CN.get(sig, sig))
        ws.cell(row=row, column=2, value=len(s))
        ws.cell(row=row, column=3, value=wins)
        ws.cell(row=row, column=4, value=pnl)
        for cidx in range(1, 5):
            ws.cell(row=row, column=cidx).border = BORDER

    # 未结算大单（EV>0 的真投注）
    real_open = [r for r in open_r if r.get('ev', 0) > 0]
    if real_open:
        row += 2
        ws.cell(row=row, column=1, value=f'未结算真投注 {len(real_open)} 条 (EV>0)').font = SECTION_FONT
        row += 1
        for c, h in zip('ABCD', ['时间', '比赛', '方向', '赔率/EV']):
            ws.cell(row=row, column=ord(c) - 64, value=h).font = HEADER_FONT
            ws.cell(row=row, column=ord(c) - 64).fill = HEADER_FILL
        for r in sorted(real_open, key=lambda x: x['match_time']):
            row += 1
            ws.cell(row=row, column=1, value=fmt_time(r['match_time']))
            ws.cell(row=row, column=2, value=r.get('teams', ''))
            ws.cell(row=row, column=3, value=r.get('outcome', ''))
            ws.cell(row=row, column=4, value=f"@{r.get('odds', 0)}  EV{r.get('ev', 0):+.2f}")
            for cidx in range(1, 5):
                ws.cell(row=row, column=cidx).border = BORDER

    # ─── Sheet2 明细 ─────────────────────────────
    ws2 = wb.create_sheet('明细')
    headers = ['状态', '日期', '主队', '客队', '信号', '方向', '赔率', 'EV', 'Edge', 'Kelly', '比分', '盈亏']
    ws2.append(headers)
    ws2.column_dimensions['A'].width = 8
    ws2.column_dimensions['B'].width = 12
    ws2.column_dimensions['C'].width = 20
    ws2.column_dimensions['D'].width = 20
    ws2.column_dimensions['E'].width = 12
    ws2.column_dimensions['F'].width = 8
    ws2.column_dimensions['G'].width = 8
    ws2.column_dimensions['H'].width = 8
    ws2.column_dimensions['I'].width = 8
    ws2.column_dimensions['J'].width = 8
    ws2.column_dimensions['K'].width = 8
    ws2.column_dimensions['L'].width = 8
    for c in ws2[1]:
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
        c.border = BORDER
        c.alignment = Alignment(horizontal='center')

    for r in sorted(recs, key=lambda x: x['match_time']):
        res = r.get('result')
        if res == 'win':
            status = '✅中'
            pnl = round(r.get('odds', 0) - 1, 2)
        elif res == 'loss':
            status = '❌挂'
            pnl = -1.0
        else:
            status = '⏳'
            pnl = ''
        sig = r.get('signal', '')
        teams = r.get('teams', '')
        parts = teams.split(' vs ') if ' vs ' in teams else (teams.split('VS ') if 'VS ' in teams else [teams, ''])
        home = parts[0].strip() if parts else ''
        away = parts[1].strip() if len(parts) > 1 else ''
        row_vals = [status, r['match_time'][:10], home, away, SIG_CN.get(sig, sig),
                    r.get('outcome', ''), r.get('odds', 0), r.get('ev', 0),
                    r.get('edge', 0), r.get('kelly', 0), r.get('score', '') or '', pnl]
        ws2.append(row_vals)
        rr = ws2.max_row
        if sig == 'weight':
            fill = WEIGHT_FILL
        elif res == 'win':
            fill = WIN_FILL
        elif res == 'loss':
            fill = LOSS_FILL
        else:
            fill = PEND_FILL
        for cidx in range(1, 13):
            c = ws2.cell(row=rr, column=cidx)
            c.border = BORDER
            c.fill = fill
            if cidx in (2, 5, 6, 7, 8, 9, 10, 11, 12):
                c.alignment = Alignment(horizontal='center')

    # 冻结首行 + 筛选
    ws2.freeze_panes = 'A2'
    ws2.auto_filter.ref = f'A1:L{ws2.max_row}'

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f'投注簿_{datetime.now().strftime("%Y%m%d")}.xlsx')
    wb.save(out_path)
    print(f'✅ 已生成: {out_path}')
    print(f'   明细 {len(recs)} 条 (结算 {len(settled)} / 未结算 {len(open_r)})')

if __name__ == '__main__':
    main()
