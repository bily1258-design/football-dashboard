import json, os
from datetime import datetime, date
import streamlit as st

st.set_page_config(page_title="足球预测看板", layout="wide")

DATA_PATH = os.path.join(os.path.dirname(__file__), 'docs', 'data', 'results.json')

@st.cache_data
def load_data():
    with open(DATA_PATH) as f:
        return json.load(f)['matches']

all_matches = load_data()

# ─── 侧边栏筛选 ───
st.sidebar.header("筛选")

leagues = sorted(set(m.get('event','') for m in all_matches))
sel_leagues = st.sidebar.multiselect("联赛", leagues, default=leagues)

conf_opts = st.sidebar.multiselect("LGBM置信度",
    ['🟢 高', '🟡 中', '🔴 低'], default=['🟢 高', '🟡 中', '🔴 低'])

show_lp = st.sidebar.checkbox("显示低优先级比赛", value=True)
only_value = st.sidebar.checkbox("仅显示有价值(EV>0)", value=False)
only_warn = st.sidebar.checkbox("仅显示有警告", value=False)
search = st.sidebar.text_input("🔍 搜索球队", placeholder="主队/客队名")

# ─── 工具函数 ───
def conf_label(c):
    if c is None: return '🔴 低'
    if c > 0.47: return '🟢 高'
    if c >= 0.40: return '🟡 中'
    return '🔴 低'

def fmt_pct(v):
    if v is None: return ''
    return f"{v*100:.1f}%"

def fmt_val(v):
    if v is None or v == 0: return ''
    return f"{v*100:.1f}%"

def fmt_form(v):
    """格式化近期战绩，保留一位小数"""
    if v is None or v == 0: return ''
    return f"{v:.1f}"

def embed_lgbm_cell(w, d, l, direction):
    """概率嵌入方向单元格：主(52.3%)"""
    if direction is None: return ''
    pct = ''
    if direction == '主' and w: pct = fmt_pct(w)
    elif direction == '平' and d: pct = fmt_pct(d)
    elif direction == '客' and l: pct = fmt_pct(l)
    if pct:
        return f"{direction}({pct})"
    return direction

# ─── 筛选 ───
filtered = []
for m in all_matches:
    if m.get('event','') not in sel_leagues:
        continue
    cl = conf_label(m.get('lgbm_confidence'))
    if cl not in conf_opts:
        continue
    if not show_lp and m.get('low_priority'):
        continue
    if only_value:
        bv = m.get('best_value') or {}
        if not bv.get('ev', 0) > 0:
            continue
    if only_warn and not m.get('warning',''):
        continue
    if search and search.lower() not in m.get('home_team','').lower() and \
               search.lower() not in m.get('away_team','').lower():
        continue
    filtered.append(m)

# ─── 统计 ───
col1, col2, col3, col4 = st.columns(4)
col1.metric("总场次", len(filtered))
g = sum(1 for m in filtered if conf_label(m.get('lgbm_confidence')) == '🟢 高')
y = sum(1 for m in filtered if conf_label(m.get('lgbm_confidence')) == '🟡 中')
r = sum(1 for m in filtered if conf_label(m.get('lgbm_confidence')) == '🔴 低')
col2.metric("🟢高置信", g)
col3.metric("🟡中置信", y)
col4.metric("🔴低置信", r)

st.markdown("---")

# ─── 表格 ───
if filtered:
    html_parts = ['<table style="width:100%;border-collapse:collapse;font-size:13px">']
    html_parts.append('<thead><tr style="background:#f0f2f6;position:sticky;top:0">')
    headers = ['时间','联赛','主队','排名','主队近况','客队','排名','客队近况',
               '比分','LGBM方向','置信度','权重','EV','价值方向','警告']
    for h in headers:
        html_parts.append(f'<th style="padding:6px 8px;text-align:left;white-space:nowrap">{h}</th>')
    html_parts.append('</tr></thead><tbody>')

    for m in filtered:
        opacity = 'opacity:0.55' if m.get('low_priority') else ''

        hr = m.get('home_rank', 0) or ''
        ar = m.get('away_rank', 0) or ''
        hfp = fmt_form(m.get('home_form_pts'))
        afp = fmt_form(m.get('away_form_pts'))
        bv = m.get('best_value') or {}

        lgbm_cell = embed_lgbm_cell(
            m.get('lgbm_win'), m.get('lgbm_draw'), m.get('lgbm_loss'),
            m.get('lgbm_prediction_cn')
        )

        weight = m.get('importance_weight', 1.0)
        w_str = f'⚡{weight:.2f}' if weight > 1.0 else f'{weight:.2f}'

        html_parts.append(f'<tr style="{opacity}">')
        vals = [
            m.get('match_time','')[:16],
            m.get('event',''),
            m.get('home_team',''),
            f'#{hr}' if hr else '',
            hfp,
            m.get('away_team',''),
            f'#{ar}' if ar else '',
            afp,
            m.get('score',''),
            lgbm_cell,
            conf_label(m.get('lgbm_confidence')),
            w_str,
            fmt_val(bv.get('ev')),
            bv.get('outcome',''),
            m.get('warning',''),
        ]
        for v in vals:
            html_parts.append(f'<td style="padding:4px 8px;white-space:nowrap">{v}</td>')
        html_parts.append('</tr>')

    html_parts.append('</tbody></table>')
    st.markdown(''.join(html_parts), unsafe_allow_html=True)
else:
    st.info("无匹配比赛")

# ─── 详情 ───
st.markdown("---")
st.subheader("📋 比赛详情")
if filtered:
    labels = [f"{m.get('match_time','')[:10]} {m.get('home_team','')} vs {m.get('away_team','')}"
              for m in filtered]
    sel_idx = st.selectbox("选择", range(len(filtered)), format_func=lambda i: labels[i])
    m = filtered[sel_idx]
    bv = m.get('best_value') or {}

    w_pct = fmt_pct(m.get('lgbm_win'))
    d_pct = fmt_pct(m.get('lgbm_draw'))
    l_pct = fmt_pct(m.get('lgbm_loss'))

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**主队信息**")
        st.json({
            '球队': m.get('home_team'),
            '联赛排名': m.get('home_rank', 0) or '无',
            '近期战绩(场均积分)': f"{fmt_form(m.get('home_form_pts')) or '无'} 分",
            '近期战绩(场均净胜球)': f"{fmt_form(m.get('home_form_gd')) or '无'}",
            '联赛': m.get('event'),
        }, expanded=True)
    with col_b:
        st.markdown("**客队信息**")
        st.json({
            '球队': m.get('away_team'),
            '联赛排名': m.get('away_rank', 0) or '无',
            '近期战绩(场均积分)': f"{fmt_form(m.get('away_form_pts')) or '无'} 分",
            '近期战绩(场均净胜球)': f"{fmt_form(m.get('away_form_gd')) or '无'}",
            '联赛': m.get('event'),
        }, expanded=True)

    st.markdown("**预测详情**")
    st.json({
        '比分': m.get('score'),
        '赔率': {'主': m.get('odds_win'), '平': m.get('odds_draw'), '客': m.get('odds_loss')},
        'LGBM方向': m.get('lgbm_prediction_cn'),
        'LGBM概率': f"{lgbm_cell_text if (lgbm_cell_text:=embed_lgbm_cell(m.get('lgbm_win'), m.get('lgbm_draw'), m.get('lgbm_loss'), m.get('lgbm_prediction_cn'))) else '无'}",
        'LGBM完整': f"主{w_pct} 平{d_pct} 客{l_pct}",
        'LGBM置信度': f"{m.get('lgbm_confidence',0)*100:.1f}%",
        '模型方向': m.get('model_prediction_cn',''),
        '权重': m.get('importance_weight'),
        '低优先级': m.get('low_priority'),
        'EV': fmt_val(bv.get('ev')),
        'Kelly': fmt_val(bv.get('kelly')),
        'Kelly加权': fmt_val(bv.get('kelly_weighted')),
        '价值方向': bv.get('outcome'),
        '价值来源': bv.get('source'),
        '警告': m.get('warning'),
        '命中': m.get('hit'),
    }, expanded=True)

# ─── 底部版本 ───
st.markdown("---")
st.caption(f"🔄 数据更新: {datetime.now().strftime('%Y-%m-%d %H:%M')} | LGBM v7 (排名+近期战绩)")
