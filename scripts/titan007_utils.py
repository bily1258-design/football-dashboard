#!/usr/bin/env python3
"""
titan007_utils.py — titan007.com 数据源公用工具模块
替代500.com作为赔率+H2H+战绩数据源

接口：
  - get_match_list(date)       → [sid, league, home_team, away_team]
  - get_odds_history(sid, cid) → [{open, latest}] 赔率变化历史
  - get_analysis_data(sid)     → {h2h, home_form, away_form, eOdds, hOdds}
  - sid_to_oddsid(sid)         → 1555xxxxx 赔率页面ID
"""
import re, json, time, urllib.request
from datetime import datetime

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
}
BF_BASE = 'https://bf.titan007.com'
OP_BASE = 'https://op1.titan007.com'
ANALYSIS_BASE = 'https://zq.titan007.com'

# cid映射：titan007 公司ID
# 验证：432=香港马会(HKJC), 177=平博(Pinnacle)
CID_PINNACLE = '177'  # 平博
CID_HKJC = '432'      # 香港马会

# 其他常用公司ID（来自OddsHistory页面验证）
CID_WILLIAM_HILL = '18'
CID_BET365 = '3'

def f_float(v):
    """安全转float，空字符串返回None"""
    if v and v.strip():
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def fetch_1x2d_odds(sid):
    """从1x2d.titan007.com/{sid}.js获取全公司赔率
    
    返回 {cid: {name, init_w/d/l, curr_w/d/l, init_rr, curr_rr}} 或 None
    
    CID验证: '432'=香港马会(HKJC), '177'=平博(Pinnacle)
    """
    ts = int(time.time() * 1000)
    url = f'https://1x2d.titan007.com/{sid}.js?r=007{ts}'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.230 Mobile Safari/537.36',
        'Referer': f'https://1x2.titan007.com/oddslist/{sid}.htm',
    })
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        text = resp.read().decode('utf-8-sig')
    except Exception as e:
        return None

    game_match = re.search(r'var game=Array\(([\s\S]*?)\);', text)
    if not game_match:
        return None

    raw = game_match.group(1)
    companies = re.findall(r'"([^"]*)"', raw)

    odds = {}
    for entry in companies:
        fields = entry.split('|')
        if len(fields) < 17:
            continue
        cid = fields[0]
        try:
            odds[cid] = {
                'name': fields[2],
                'init_w': f_float(fields[3]),
                'init_d': f_float(fields[4]),
                'init_l': f_float(fields[5]),
                'init_rr': f_float(fields[9]),
                'curr_w': f_float(fields[10]),
                'curr_d': f_float(fields[11]),
                'curr_l': f_float(fields[12]),
                'curr_rr': f_float(fields[16]),
            }
        except (ValueError, IndexError):
            pass
    return odds
CID_INTERWETTEN = '1'

ODDSID_OFFSET = 152595753  # sid + offset = oddsHistory ID


def decode_gbk(raw):
    """智能解码"""
    for enc in ('utf-8', 'gbk', 'gb18030', 'big5', 'gb2312'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode('utf-8', errors='replace')


def fetch_url(url, encoding=None, timeout=15):
    """通用抓取"""
    req = urllib.request.Request(url, headers=HEADERS)
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    if encoding:
        return raw.decode(encoding, errors='replace')
    return decode_gbk(raw)


def sid_to_oddsid(sid):
    """计算OddsHistory页面ID: id = sid + 152595753"""
    return str(int(sid) + ODDSID_OFFSET)


# ═══════════ 1. 比赛日程 ═══════════

def get_match_list(date_str):
    """
    从titan007获取指定日期的所有比赛
    返回 [{sid, league, home_team, away_team}]
    date_str格式: 'YYYY-MM-DD' 或 'YYYYMMDD'
    """
    # 格式化日期
    if '-' in date_str:
        d = date_str
    elif len(date_str) == 8:
        d = f'{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}'
    else:
        d = date_str

    url = f'{BF_BASE}/CommonInterface.ashx?type=2&date={d}'
    text = fetch_url(url)
    if not text.strip():
        return []

    matches = []
    for item in text.split(','):
        parts = item.strip().split('^')
        if len(parts) >= 4:
            matches.append({
                'sid': parts[0],
                'league': parts[1],
                'home_team': parts[2],
                'away_team': parts[3],
            })
    # 英→中翻译
    translate_match_list(matches)
    return matches


# ═══════════ 2. 赔率历史 ═══════════

def _clean_odds_val(v):
    """清理赔率值"""
    try:
        return float(v.strip().replace('--', ''))
    except (ValueError, AttributeError):
        return 0.0


def parse_odds_table(html):
    """
    从OddsHistory.aspx的HTML表格提取赔率
    
    返回:
    {
        'open': {'win': x, 'draw': x, 'loss': x},
        'latest': {'win': x, 'draw': x, 'loss': x},
        'history': [{'time': ..., 'win': x, 'draw': x, 'loss': x}, ...],
        'company_name': str,
        'changes': int,   # 赔率变化次数
    }
    无数据或解析失败返回 None
    """
    # 检查是否有数据：找公司名行
    # 页面结构：<span class='f1'><font color='red'>香港马会</font>标准走势</span>
    # 或 <span class='f1'><font color='red'>Pinnacle</font>标准走势</span>
    cm = re.search(r"<span class='f1'><font color='red'>([^<]+)</font>", html)
    # 或另一格式：<span class='f1'><span class='style1'><font color='red'>...</font></span></span>
    if not cm:
        cm = re.search(r"<font color='red'>([^<]+)</font>.*?标准走势", html)
    if not cm:
        cm = re.search(r"<span class='style1'><font color='red'>([^<]+)</font>", html)
    company_name = cm.group(1).strip() if cm else ''

    # 检查是否有数据：找赔率数字行
    if not re.search(r'<td[^>]*height=22', html):
        return None

    # 找所有赔率行 <tr ... bgcolor=#FFFFFF>
    # 每行: 11个td, 前3个为win/draw/loss赔率
    rows = []
    for tr_m in re.finditer(r'<tr\s+align=center\s+bgcolor=#FFFFFF>(.*?)</tr>', html, re.DOTALL):
        row_html = tr_m.group(1)
        
        # 提取3个赔率值：<font color=...>value</font>
        odds_cells = re.findall(r'<font\s+color=[^>]*>([\d.]+)</font>', row_html)
        if len(odds_cells) < 3:
            # fallback: 行内纯数字
            odds_cells = re.findall(r'>([\d]+\.[\d]+)<', row_html)
        
        if len(odds_cells) >= 3:
            win = _clean_odds_val(odds_cells[0])
            draw = _clean_odds_val(odds_cells[1])
            loss = _clean_odds_val(odds_cells[2])
            
            # 提取时间：最后一列 (class=font12)
            time_m = re.search(r"class=font12[^>]*>([^<]+)", row_html)
            time_val = time_m.group(1).strip().replace('\xa0', '') if time_m else ''
            
            # 是否为开盘？
            is_opening = '(初盘)' in row_html or '(初檥)' in row_html or '初盘' in row_html
            
            row_data = {
                'win': win,
                'draw': draw,
                'loss': loss,
                'time': time_val,
                'is_opening': is_opening,
            }
            rows.append(row_data)

    if not rows:
        return None

    # 最新=第一行，开盘=含(初盘)的最后一行 或 最后一行
    latest = rows[0]
    opening = None
    for r in reversed(rows):
        if r['is_opening']:
            opening = r
            break
    if not opening:
        opening = rows[-1]

    return {
        'open': {'win': opening['win'], 'draw': opening['draw'], 'loss': opening['loss']},
        'latest': {'win': latest['win'], 'draw': latest['draw'], 'loss': latest['loss']},
        'history': rows,
        'company_name': company_name,
        'changes': len(rows),
    }


def get_odds_history(sid, cid='432'):
    """
    获取指定公司(sid+cid)的赔率历史
    
    参数:
      sid: 赛程ID (如 '2920917')
      cid: 公司ID ('432'=平博, '177'=HKJC)
    
    返回: parse_odds_table()的字典，或 None
    """
    oddsid = sid_to_oddsid(sid)
    url = f'{OP_BASE}/OddsHistory.aspx?id={oddsid}&sid={sid}&cid={cid}&l=1'
    try:
        html = fetch_url(url)
        result = parse_odds_table(html)
        return result
    except Exception as e:
        print(f'[WARN] get_odds_history(sid={sid},cid={cid}): {e}')
        return None


def get_pinnacle_odds(sid):
    """获取平博(Pinnacle)赔率"""
    return get_odds_history(sid, CID_PINNACLE)


def get_hkjc_odds(sid):
    """获取香港马会(HKJC)赔率"""
    return get_odds_history(sid, CID_HKJC)


def get_all_odds(sid):
    """获取平博+HKJC赔率，返回合并字典"""
    result = {}
    p = get_pinnacle_odds(sid)
    if p:
        result['pinnacle'] = p
    h = get_hkjc_odds(sid)
    if h:
        result['hkjc'] = h
    return result


# ═══════════ 3. 分析页（H2H+近期战绩） ═══════════

def parse_analysis_vars(html):
    """
    解析分析页的JS变量
    
    提取:
      v_data: 历史交锋
      h_data: 主队近期战绩
      a_data: 客队近期战绩
      Vs_eOdds: 欧洲赔率（多家公司）
      home_name, away_name: 队伍名
      league_name: 联赛名
    """
    import ast
    data = {}
    
    def _safe_json(val_str):
        """安全的JSON/array解析：先尝试json，失败则清理HTML后重试"""
        # 尝试直接JSON解析
        try:
            return json.loads(val_str)
        except json.JSONDecodeError:
            pass
        
        # 清理HTML标签中的双引号冲突，再解析
        # 先清理所有<span ...> → <span>，再去掉标签
        cleaned = re.sub(r'<span[^>]*>', '<span>', val_str)
        cleaned = re.sub(r'</?span>', '', cleaned)
        cleaned = re.sub(r'<br\s*/?>', '', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        
        # 终极手段：安全 eval（仅限数组+字面量）
        # 清理HTML标签
        cleaned = re.sub(r'<[^>]+>', '', val_str)  
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        try:
            return ast.literal_eval(cleaned)
        except (ValueError, SyntaxError):
            pass
        
        return []
    
    # 找 v_data (历史交锋)
    vm = re.search(r'var\s+v_data\s*=\s*(\[.*?\])\s*;', html, re.DOTALL)
    if vm:
        vd = _safe_json(vm.group(1))
        data['h2h_raw'] = vd if isinstance(vd, list) else []
    
    # 找 h_data / a_data
    for var_name in ['h_data', 'a_data']:
        vm = re.search(r'var\s+' + var_name + r'\s*=\s*(\[.*?\])\s*;', html, re.DOTALL)
        if vm:
            vd = _safe_json(vm.group(1))
            data[var_name] = vd if isinstance(vd, list) else []

    # 找 Vs_eOdds (欧洲赔率)
    em = re.search(r'var\s+Vs_eOdds\s*=\s*(\[.*?\])\s*;', html, re.DOTALL)
    if em:
        d = _safe_json(em.group(1))
        data['eOdds'] = d if isinstance(d, list) else []

    # 找 Vs_hOdds (亚洲赔率)
    hm = re.search(r'var\s+Vs_hOdds\s*=\s*(\[.*?\])\s*;', html, re.DOTALL)
    if hm:
        d = _safe_json(hm.group(1))
        data['hOdds'] = d if isinstance(d, list) else []

    # 比赛时间 strTime: "YYYY-MM-DD HH:MM"
    mtm = re.search(r'var\s+strTime\s*=\s*"([^"]+)"', html)
    if not mtm:
        mtm = re.search(r"var\s+strTime\s*=\s*'([^']+)'", html)
    if mtm:
        data['match_time'] = mtm.group(1)
    # 队伍名和联赛 - titan007 变量名
    for var_search in [('home_name', ['home_name', 'hometeam', 'home_team']),
                       ('away_name', ['away_name', 'guestteam', 'guest_team', 'away_team']),
                       ('league_name', ['league_name', 'League', 'leaguename'])]:
        target = var_search[0]
        for varname in var_search[1]:
            m = re.search(rf'var\s+{varname}\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE)
            if m:
                data[target] = m.group(1)
                break

    return data




def parse_h2h_from_vdata(v_data):
    """从v_data解析历史交锋
    
    v_data条目格式:
      [date_num, league_id, 'league_name', '#color', team1_id, 'team1_name', 
       team2_id, 'team2_name', score1, score2, 'full_score', ...]
    index5=左侧队伍, index7=右侧队伍, index8=左队分, index9=右队分
    """
    results = []
    for entry in v_data:
        if not isinstance(entry, list) or len(entry) < 10:
            continue
        try:
            hs = int(entry[8]) if entry[8] is not None else 0
            as_ = int(entry[9]) if entry[9] is not None else 0
        except (ValueError, TypeError):
            continue
        
        # 清理队名中的标记如 (中)
        h_name = re.sub(r'\\(中\\)|\\(客\\)|\\(主\\)', '', str(entry[5])).strip()
        a_name = re.sub(r'\\(中\\)|\\(客\\)|\\(主\\)', '', str(entry[7])).strip()
        
        results.append({
            'home': h_name,
            'away': a_name,
            'home_score': hs,
            'away_score': as_,
            'league': str(entry[2]),
            'date': str(entry[0]),
            'is_neutral': '(中)' in str(entry[5]) or '(中)' in str(entry[7]),
        })
    return results


def parse_form_from_analysis(html, team_side='home'):
    """
    从分析页的h_data或a_data解析近期战绩
    team_side: 'home' 从h_data取, 'away' 从a_data取
    
    格式同v_data: [date, lid, league, color, tid1, t1name, tid2, t2name, s1, s2, half, handicap, ...]
    队名可能在index5或index7，根据哪个包含当前队名
    """
    var_name = 'h_data' if team_side == 'home' else 'a_data'
    
    import ast
    vm = re.search(r'var\s+' + var_name + r'\s*=\s*(\[.*?\])\s*;', html, re.DOTALL)
    if not vm:
        return []
    
    # 解析（清理HTML）
    raw = vm.group(1)
    cleaned = re.sub(r'<[^>]+>', '', raw)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    try:
        parsed = ast.literal_eval(cleaned)
    except (ValueError, SyntaxError):
        return []
    
    if not isinstance(parsed, list):
        return []
    
    # 获取当前队名以判断哪一侧是该队
    # 找当前队名从hometeam或guestteam
    home_team_var = re.search(r'var\s+hometeam\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE)
    away_team_var = re.search(r'var\s+guestteam\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE)
    hname = home_team_var.group(1) if home_team_var else ''
    aname = away_team_var.group(1) if away_team_var else ''
    
    current_team = hname if team_side == 'home' else aname
    # 清理 (中) 标记
    current_team_clean = re.sub(r'\\(中\\)|\\(主\\)|\\(客\\)', '', current_team).strip()
    
    results = []
    for entry in parsed:
        if not isinstance(entry, list) or len(entry) < 10:
            continue
        try:
            s1 = int(entry[8]) if entry[8] is not None else 0
            s2 = int(entry[9]) if entry[9] is not None else 0
        except (ValueError, TypeError):
            continue
        
        t1 = re.sub(r'\\(中\\)|\\(主\\)|\\(客\\)', '', str(entry[5])).strip()
        t2 = re.sub(r'\\(中\\)|\\(主\\)|\\(客\\)', '', str(entry[7])).strip()
        
        # 判断该队在哪一侧
        team_is_left = (t1 == current_team or t1 == current_team_clean)
        team_is_right = (t2 == current_team or t2 == current_team_clean)
        
        if team_is_left:
            team_score = s1
            opp_score = s2
            opponent = t2
            is_home_in_match = True
        elif team_is_right:
            team_score = s2
            opp_score = s1
            opponent = t1
            is_home_in_match = False
        else:
            continue  # 该场比赛不含当前队
        
        results.append({
            'team': current_team,
            'opponent': opponent,
            'team_score': team_score,
            'opponent_score': opp_score,
            'is_home': is_home_in_match,
            'result': 'W' if team_score > opp_score else ('D' if team_score == opp_score else 'L'),
            'league': str(entry[2]),
            'date': str(entry[0]),
        })
    
    return results


def get_analysis_data(sid):
    """
    获取分析页面全部数据
    返回 {h2h, home_form, away_form, eOdds, home_name, away_name, league_name}
    """
    url = f'{ANALYSIS_BASE}/analysis/{sid}.htm'
    try:
        html = fetch_url(url, timeout=20)
        vars_data = parse_analysis_vars(html)
        
        result = {
            'sid': sid,
            'h2h': parse_h2h_from_vdata(vars_data.get('h2h_raw', [])),
            'home_form': parse_form_from_analysis(html, 'home'),
            'away_form': parse_form_from_analysis(html, 'away'),
            'eOdds': vars_data.get('eOdds', []),
            'hOdds': vars_data.get('hOdds', []),
            'home_name': vars_data.get('home_name', ''),
            'away_name': vars_data.get('away_name', ''),
            'league_name': vars_data.get('league_name', ''),
            'match_time': vars_data.get('match_time', ''),
        }
        return result
    except Exception as e:
        print(f'[WARN] get_analysis_data(sid={sid}): {e}')
        return None


# ═══════════ 4. 队伍名映射 ═══════════

# 手动维护的队伍名映射（500.com简中 → titan007繁中/英文）
# 这是最关键的映射表
TEAM_NAME_MAP = {
    # 常见外国球队（英文名保留一致）
    'Manchester City': '曼城',  # same in both
    '利物浦': '利物浦',
    '阿森纳': '阿仙奴',
    '切尔西': '車路士',
    '热刺': '熱刺',
    '曼联': '曼聯',
    '纽卡斯尔': '紐卡素',
    '阿斯顿维拉': '阿士東維拉',
    '西汉姆': '韋斯咸',
    '水晶宫': '水晶宮',
    '布伦特福德': '賓福特',
    '狼队': '狼隊',
    '伯恩茅斯': '般尼茅夫',
    '富勒姆': '富咸',
    '布莱顿': '白禮頓',
    '埃弗顿': '愛華頓',
    '诺丁汉': '諾丁漢森林',
    '莱斯特城': '李斯特城',
    '伊普斯维奇': '葉士域治',
    '南安普顿': '修咸頓',
    '沃特福德': '屈福特',
    '利兹联': '列斯聯',
    '谢菲联': '錫菲聯',
    '伯恩利': '般尼',
    '米德尔斯堡': '米杜士堡',
    '桑德兰': '新特蘭',
    '西布朗': '西布朗',
    '斯托克城': '史篤城',
    '考文垂': '高雲地利',
    '斯旺西': '史雲斯',
    '加的夫城': '卡迪夫城',
    '诺维奇': '諾域治',
    '赫尔城': '侯城',
    '女王公园': '昆士柏流浪',
    '布莱克本': '布力般流浪',
    '朴茨茅斯': '樸茨茅夫',
    '博尔顿': '保頓',
    '普雷斯顿': '普雷斯頓',
    '德比郡': '打比郡',
    '牛津联': '牛津聯',
    '维冈': '韋根',
    # 西甲
    '巴塞罗那': '巴塞隆拿',
    '皇家马德里': '皇家馬德里',
    '马德里竞技': '馬德里體育會',
    '毕尔巴鄂': '畢爾包',
    '塞维利亚': '西維爾',
    '比利亚雷亚尔': '維拉利爾',
    '皇家社会': '皇家蘇斯達',
    '瓦伦西亚': '華倫西亞',
    '奥萨苏纳': '奧沙辛拿',
    '贝蒂斯': '貝迪斯',
    '塞尔塔': '切爾達',
    '马洛卡': '馬略卡',
    '赫罗纳': '傑羅納',
    '巴列卡诺': '華歷簡奴',
    '塞维利亚': '西維爾',
    '莱加内斯': '雷加利斯',
    '拉帕马斯': '拉斯彭馬斯',
    '阿拉维斯': '艾拉維斯',
    '赫塔费': '基達菲',
    '西班牙人': '愛斯賓奴',
    '巴拉多利德': '華拉度列',
    '埃尔切': '艾爾切',
    '加的斯': '加的斯',
    '阿尔梅里亚': '艾美利亞',
    '格拉纳达': '格蘭納達',
    # 意甲
    '国际米兰': '國際米蘭',
    'AC米兰': 'AC米蘭',
    '尤文图斯': '祖雲達斯',
    '那不勒斯': '拿玻里',
    '罗马': '羅馬',
    '拉齐奥': '拉素',
    '亚特兰大': '阿特蘭大',
    '佛罗伦萨': '費倫天拿',
    '博洛尼亚': '博洛尼亞',
    '都灵': '拖連奴',
    '乌迪内斯': '烏甸尼斯',
    '蒙扎': '蒙沙',
    '热那亚': '熱拿亞',
    '卡利亚里': '卡利亞里',
    '莱切': '萊切',
    '维罗纳': '維羅納',
    '恩波利': '安玻里',
    '帕尔马': '帕爾馬',
    '威尼斯': '威尼斯',
    '科莫': '科木',
    '斯佩齐亚': '史比斯亞',
    '萨勒尼塔纳': '沙蘭力坦拿',
    '克雷莫纳': '克雷莫納',
    '桑普多利亚': '森多利亞',
    '弗罗西诺内': '費辛隆尼',
    '萨索洛': '薩斯索羅',
    # 德甲
    '拜仁': '拜仁慕尼黑',
    '多特蒙德': '多蒙特',
    'RB莱比锡': 'RB萊比錫',
    '勒沃库森': '利華古遜',
    '法兰克福': '法蘭克福',
    '斯图加特': '史特加',
    '门兴': '慕遜加柏',
    '沃尔夫斯堡': '禾夫斯堡',
    '霍芬海姆': '賀芬咸',
    '柏林联合': '柏林聯',
    '弗赖堡': '弗賴堡',
    '美因茨': '緬恩斯',
    '奥格斯堡': '奧格斯堡',
    '波鸿': '波琴',
    '不莱梅': '雲達不萊梅',
    '海登海姆': '海登咸',
    '圣保利': '聖保利',
    '基尔': '基爾',
    '达姆施塔特': '達斯泰特',
    '科隆': '科隆',
    # 法甲
    '巴黎圣日耳曼': '巴黎聖日門',
    '马赛': '馬賽',
    '摩纳哥': '摩納哥',
    '里尔': '里爾',
    '尼斯': '尼斯',
    '里昂': '里昂',
    '雷恩': '雷恩',
    '朗斯': '朗斯',
    '斯特拉斯堡': '斯特拉斯堡',
    '图卢兹': '圖盧茲',
    '布雷斯特': '比斯特',
    '兰斯': '蘭斯',
    '欧塞尔': '歐塞爾',
    '南特': '南特',
    '蒙彼利埃': '蒙彼利埃',
    '圣埃蒂安': '聖伊天',
    '勒阿弗尔': '勒哈弗爾',
    '昂热': '昂熱',
    '克莱蒙': '克萊蒙特',
    '洛里昂': '羅連安特',
    '特鲁瓦': '特魯瓦',
    '阿雅克肖': '阿些斯奧',
    # 葡超
    '本菲卡': '賓菲加',
    '波尔图': '波圖',
    '里斯本': '士砵亭',
    '布拉加': '布拉加',
    # 荷甲
    '阿贾克斯': '阿積士',
    '埃因霍温': 'PSV燕豪芬',
    '费耶诺德': '飛燕諾',
    '阿尔克马尔': '阿爾克馬爾',
    # 苏超
    '凯尔特人': '些路迪',
    '流浪者': '格拉斯哥流浪',
    # 日职
    '神户胜利船': '神戶勝利船',
    '横滨水手': '橫濱水手',
    '川崎前锋': '川崎前鋒',
    '鹿岛鹿角': '鹿島鹿角',
    '浦和红钻': '浦和紅鑽',
    # 澳超
    '墨尔本城': '墨爾本城',
    '悉尼FC': '悉尼FC',
    '墨尔本胜利': '墨爾本勝利',
    '西悉尼': '西悉尼流浪者',
    # 美职
    '迈阿密国际': '國際邁亞密',
    '洛杉矶FC': '洛杉磯FC',
    '洛杉矶银河': '洛杉磯銀河',
}

# ═══════════ 英→中 队名映射 ═══════════
# titan007 的 CommonInterface 接口返回的队名，部分联赛是英文，部分中文
TEAM_CN_MAP = {
    # 世界杯国家
    'Norway': '挪威',
    'England': '英格兰',
    'Argentina': '阿根廷',
    'Switzerland': '瑞士',
    'France': '法国',
    'Spain': '西班牙',
    'Portugal': '葡萄牙',
    'Netherlands': '荷兰',
    'Germany': '德国',
    'Italy': '意大利',
    'Brazil': '巴西',
    'Belgium': '比利时',
    'Croatia': '克罗地亚',
    'Denmark': '丹麦',
    'Sweden': '瑞典',
    'Poland': '波兰',
    'Uruguay': '乌拉圭',
    'Japan': '日本',
    'South Korea': '韩国',
    'Australia': '澳大利亚',
    'USA': '美国',
    'Mexico': '墨西哥',
    'Canada': '加拿大',
    'Morocco': '摩洛哥',
    'Senegal': '塞内加尔',
    'Cameroon': '喀麦隆',
    'Ghana': '加纳',
    'Nigeria': '尼日利亚',
    'Tunisia': '突尼斯',
    'Algeria': '阿尔及利亚',
    'Egypt': '埃及',
    'South Africa': '南非',
    'Costa Rica': '哥斯达黎加',
    'Saudi Arabia': '沙特阿拉伯',
    'Iran': '伊朗',
    'Ecuador': '厄瓜多尔',
    'Chile': '智利',
    'Colombia': '哥伦比亚',
    'Peru': '秘鲁',
    'Paraguay': '巴拉圭',
    'Bolivia': '玻利维亚',
    'Venezuela': '委内瑞拉',
    'China': '中国',
    'Iceland': '冰岛',
    'Wales': '威尔士',
    'Scotland': '苏格兰',
    'Northern Ireland': '北爱尔兰',
    'Republic of Ireland': '爱尔兰',
    'Czech Republic': '捷克',
    'Turkey': '土耳其',
    'Russia': '俄罗斯',
    'Ukraine': '乌克兰',
    'Austria': '奥地利',
    'Hungary': '匈牙利',
    'Sweden': '瑞典',
    'Norway': '挪威',
    'Finland': '芬兰',
    'Romania': '罗马尼亚',
    'Serbia': '塞尔维亚',
    'Greece': '希腊',
    'Slovakia': '斯洛伐克',
    'Slovenia': '斯洛文尼亚',
    'Bulgaria': '保加利亚',
    'Croatia': '克罗地亚',
    'Bosnia': '波黑',
    'Albania': '阿尔巴尼亚',
    'Montenegro': '黑山',
    'North Macedonia': '北马其顿',
    'Luxembourg': '卢森堡',
    'Cyprus': '塞浦路斯',
    'Israel': '以色列',
    'Georgia': '格鲁吉亚',
    'Armenia': '亚美尼亚',
    'Azerbaijan': '阿塞拜疆',
    'Kazakhstan': '哈萨克斯坦',
    # MLS 球队
    'St. Louis City': '圣路易斯城',
    'Sporting Kansas City': '堪萨斯城竞技',
    'Seattle Sounders': '西雅图海湾人',
    'Portland Timbers': '波特兰伐木者',
    'Nashville SC': '纳什维尔',
    'Atlanta United': '亚特兰大联',
    'Los Angeles Galaxy': '洛杉矶银河',
    'Los Angeles FC': '洛杉矶FC',
    'Inter Miami': '迈阿密国际',
    'New York City FC': '纽约城',
    'New York Red Bulls': '纽约红牛',
    'Philadelphia Union': '费城联合',
    'New England Revolution': '新英格兰革命',
    'Toronto FC': '多伦多FC',
    'Chicago Fire': '芝加哥火焰',
    'Columbus Crew': '哥伦布机员',
    'FC Cincinnati': '辛辛那提FC',
    'DC United': '华盛顿联',
    'Orlando City': '奥兰多城',
    'Minnesota United': '明尼苏达联',
    'CF Montreal': '蒙特利尔CF',
    'Austin FC': '奥斯汀FC',
    'Colorado Rapids': '科罗拉多急流',
    'Real Salt Lake': '皇家盐湖城',
    'San Jose Earthquakes': '圣何塞地震',
    'Portland Timbers': '波特兰伐木者',
    'Houston Dynamo': '休斯顿迪纳摩',
    'FC Dallas': '达拉斯FC',
    'Vancouver Whitecaps': '温哥华白帽',
    'Charlotte FC': '夏洛特FC',
    # 澳系
    'Gold Coast United': '黄金海岸联',
    'Queensland Lions SC': '昆士兰狮队',
    'Adelaide City FC': '阿德莱德城',
    'Adelaide Comets FC': '阿德莱德彗星',
    'Adelaide Olympic': '阿德莱德奥林匹克',
    'Adelaide Raiders': '阿德莱德突击者',
    'Adelaide Blue Eagles': '阿德莱德蓝鹰',
    'Campbelltown City SC': '坎贝尔敦城',
    'Croydon Kings': '克罗伊登国王',
    'West Torrens Birkalla': '西托伦斯',
    'Para Hills Knights': '帕拉山骑士',
    'Melbourne Knights': '墨尔本骑士',
    'Melbourne City': '墨尔本城',
    'Melbourne Victory': '墨尔本胜利',
    'Sydney FC': '悉尼FC',
    'Western Sydney Wanderers': '西悉尼流浪者',
    'Brisbane Roar': '布里斯班狮吼',
    'Perth Glory': '珀斯光荣',
    'Newcastle Jets': '纽卡斯尔喷气机',
    'Central Coast Mariners': '中央海岸水手',
    'Wellington Phoenix': '惠灵顿凤凰',
    'Western United': '西部联',
    'Macarthur FC': '麦克阿瑟FC',
    'Auckland City': '奥克兰城',
    'Bentleigh Greens': '本特利绿茵',
    'Bayswater City': '贝斯沃特城',
    'Floreat Athena': '弗洛雷特雅典娜',
    'Sorrento FC': '索伦托FC',
    'Balcatta FC': '巴尔卡塔FC',
    'Inglewood United': '英格尔伍德联',
    'Perth SC': '珀斯SC',
    'Cockburn City': '科克本城',
    'Rockingham City': '罗金厄姆城',
    'Armadale SC': '阿马代尔SC',
    'Fremantle City': '弗里曼特尔城',
    'Joondalup United': '君达乐联',
    'Olympic Kingsway': '奥运金斯威',
    'Mandurah City': '曼哲拉城',
    # 巴甲常见球队
    'Bahia': '巴伊亚',
    'Chapecoense SC': '沙佩科恩斯',
    'Fluminense RJ': '弗鲁米嫩塞',
    'Bragantino': '巴甘蒂诺',
    'Mirassol': '米拉索尔',
    'Gremio (RS)': '格雷米奥',
    'Flamengo': '弗拉门戈',
    'Palmeiras': '帕尔梅拉斯',
    'Corinthians': '科林蒂安',
    'Sao Paulo': '圣保罗',
    'Santos': '桑托斯',
    'Internacional': '巴西国际',
    'Atletico Mineiro': '米内罗竞技',
    'Fortaleza': '福塔雷萨',
    'Cuiaba': '库亚巴',
    'Atletico Paranaense': '巴拉纳竞技',
    'Botafogo': '博塔弗戈',
    'Vasco da Gama': '瓦斯科达伽马',
    'Cruzeiro': '克鲁塞罗',
    'America MG': '米内罗美洲',
    'Coritiba': '科里蒂巴',
    'Goias': '戈亚斯',
    'Avai FC': '阿瓦伊',
    'Ceara': '塞阿拉',
    'Sport Recife': '累西腓体育',
    'Vitoria BA': '维多利亚',
    'Juventude': '尤文图德',
    'Londrina PR': '隆德里纳',
    'Guarani SP': '瓜拉尼',
    'CRB': 'CRB',
    'Novorizontino': '新奥里藏特诺',
    'Ponte Preta': '庞特普雷塔',
    'Sao Bernardo': '圣贝尔纳多',
    'Botafogo SP': '博塔弗戈SP',
    'Ituano': '伊图阿诺',
    'Criciuma': '克里丘马',
    # 芬甲
    'PK-35': 'PK-35',
    'JIPPO': '吉波',
    'TPS Turku': 'TPS图尔库',
    'Jaro Pietarsaari': '雅罗',
    'KTP Kotka': 'KTP科特卡',
    'JaPS': 'JäPS',
    'MP Mikkeli': 'MP米凯利',
    'Kajaani': '卡加尼',
    'FCV': 'FCV',
    # 瑞典超
    'IFK Goteborg': 'IFK哥德堡',
    'Brommapojkarna': '布洛马波卡纳',
    'Mjallby AIF': '米亚尔比',
    'Vasteras SK FK': '韦斯特罗斯',
    'Hammarby IF': '哈马比',
    'Malmo FF': '马尔默',
    'Djurgardens IF': '尤尔加登',
    'AIK': 'AIK索尔纳',
    'Elfsborg': '埃尔夫斯堡',
    'Norrkoping': '北雪平',
    'Hacken': '赫根',
    'Sirius': '天狼星',
    'GAIS': 'GAIS哥德堡',
    'Halmstad BK': '哈尔姆斯塔德',
    'IFK Varnamo': '韦纳穆',
    'Kalmar FF': '卡尔马',
    # 挪超
    'Bodo Glimt': '博德闪耀',
    'Fredrikstad': '腓特烈斯塔',
    'Viking FK': '维京',
    'Brann': '布兰',
    'Molde': '莫尔德',
    'Rosenborg': '罗森博格',
    'Lillestrom': '利勒斯特罗姆',
    'Sarpsborg 08': '萨尔普斯堡',
    'Stromsgodset': '斯特罗姆加斯特',
    'Odd Grenland': '奥德',
    'HamKam': '汉坎',
    'Sandefjord': '桑德菲杰',
    'KFUM Oslo': 'KFUM奥斯陆',
    'Kristiansund': '克里斯蒂安松',
    'Haugesund': '海于格松',
    'Tromso': '特罗姆瑟',
}

def translate_team_name(en_name):
    """英→中 队伍名"""
    return TEAM_CN_MAP.get(en_name, en_name)

def translate_match_list(matches):
    """将全英文队名的 match list 转为中文"""
    for m in matches:
        if 'home_team' in m and all(ord(c) < 128 for c in m['home_team']):
            m['home_team'] = translate_team_name(m['home_team'])
        if 'away_team' in m and all(ord(c) < 128 for c in m['away_team']):
            m['away_team'] = translate_team_name(m['away_team'])
        # 联赛名也翻译
        if 'league' in m:
            league_cn = {
                'World Cup': '世界杯',
                'MLS': '美职联',
                'AUS QSL': '澳昆超',
                'BRA D1': '巴甲',
                'BRA D2': '巴乙',
                'SWE D1': '瑞典超',
                'NOR D1': '挪超',
                'FIN D1': '芬超',
                'FIN D2': '芬甲',
                'AUS SASL': '澳南超',
                'AUS VPL': '澳维超',
                'INT CF': '国际友谊赛',
            }.get(m['league'], m['league'])
            m['league'] = league_cn
    return matches



def normalize_team_name(name):
    """归一化队伍名用于匹配"""
    n = name.strip()
    # 已经匹配的就不用动了
    return n


def match_team_names(titan007_name, known_name):
    """
    判断两个队伍名是否匹配
    支持完全匹配、映射匹配、子串匹配
    """
    if not titan007_name or not known_name:
        return False
    
    # 完全匹配
    if titan007_name == known_name:
        return True
    
    # 映射匹配（简中→繁中）
    if known_name in TEAM_NAME_MAP:
        if TEAM_NAME_MAP[known_name] == titan007_name:
            return True
    
    # 反向映射
    for sc, tc in TEAM_NAME_MAP.items():
        if tc == titan007_name and sc == known_name:
            return True
    
    # 子串匹配（处理长名）
    if len(known_name) >= 3 and len(titan007_name) >= 3:
        if known_name in titan007_name or titan007_name in known_name:
            return True
    
    return False


# ═══════════ 5. 数据格式转换 ═══════════

def titan007_to_match_dict(titan007_match, pinnacle_odds=None, hkjc_odds=None, analysis_data=None):
    """
    将titan007比赛数据转换为与现有matches_*.json兼容的格式
    
    字段映射:
      titan007 sid → fid (保留原fid字段名以便兼容)
    """
    match = {
        'fid': titan007_match['sid'],
        'sid': titan007_match['sid'],
        'date': datetime.now().strftime('%Y-%m-%d'),
        'match_time': '',
        'event': titan007_match.get('league', ''),
        'home_team': titan007_match.get('home_team', ''),
        'away_team': titan007_match.get('away_team', ''),
        'score': '',
        'status': '0',
        'source': 'titan007',
        'home_rank': 0,
        'away_rank': 0,
    }

    # 追加平博赔率
    if pinnacle_odds:
        match['odds_pinnacle_open_win'] = pinnacle_odds['open']['win']
        match['odds_pinnacle_open_draw'] = pinnacle_odds['open']['draw']
        match['odds_pinnacle_open_loss'] = pinnacle_odds['open']['loss']
        match['odds_pinnacle_win'] = pinnacle_odds['latest']['win']
        match['odds_pinnacle_draw'] = pinnacle_odds['latest']['draw']
        match['odds_pinnacle_loss'] = pinnacle_odds['latest']['loss']

    # 追加HKJC赔率
    if hkjc_odds:
        match['odds_hkjc_open_win'] = hkjc_odds['open']['win']
        match['odds_hkjc_open_draw'] = hkjc_odds['open']['draw']
        match['odds_hkjc_open_loss'] = hkjc_odds['open']['loss']
        match['odds_hkjc_win'] = hkjc_odds['latest']['win']
        match['odds_hkjc_draw'] = hkjc_odds['latest']['draw']
        match['odds_hkjc_loss'] = hkjc_odds['latest']['loss']

    # 追加分析数据
    if analysis_data:
        match['analysis'] = {
            'h2h': analysis_data.get('h2h', []),
            'home_form': analysis_data.get('home_form', []),
            'away_form': analysis_data.get('away_form', []),
        }

    return match


# ═══════════ 自测 ═══════════

if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] in ('--help', '-h'):
        print('用法:')
        print('  python3 titan007_utils.py [sid]')
        print('  python3 titan007_utils.py list [date]  # 获取比赛列表')
        print('  python3 titan007_utils.py odds <sid> [cid]  # 获取赔率')
        print('  python3 titan007_utils.py analysis <sid>  # 获取分析数据')
        sys.exit(0)
    
    if len(sys.argv) >= 2 and sys.argv[1] == 'list':
        date_str = sys.argv[2] if len(sys.argv) > 2 else datetime.now().strftime('%Y-%m-%d')
        matches = get_match_list(date_str)
        print(f'[OK] {date_str}: {len(matches)} 场比赛')
        for m in matches[:10]:
            print(f'  sid={m["sid"]} {m["home_team"]} vs {m["away_team"]} [{m["league"]}]')
        if len(matches) > 10:
            print(f'  ... ({len(matches)-10} more)')
    
    elif len(sys.argv) >= 3 and sys.argv[1] == 'odds':
        sid = sys.argv[2]
        cid = sys.argv[3] if len(sys.argv) > 3 else '432'
        odds = get_odds_history(sid, cid)
        if odds:
            print(f'[OK] sid={sid} cid={cid} 开盘: {odds["open"]["win"]}/{odds["open"]["draw"]}/{odds["open"]["loss"]}')
            print(f'     最新: {odds["latest"]["win"]}/{odds["latest"]["draw"]}/{odds["latest"]["loss"]} ({odds["changes"]}次变化)')
        else:
            print(f'[NO DATA] sid={sid} cid={cid}')
    
    elif len(sys.argv) >= 2 and sys.argv[1] == 'analysis':
        sid = sys.argv[2] if len(sys.argv) > 2 else '2920917'
        data = get_analysis_data(sid)
        if data:
            print(f'[OK] sid={sid}')
            print(f'  {data["home_name"]} vs {data["away_name"]} [{data["league_name"]}]')
            print(f'  H2H: {len(data["h2h"])} 场')
            print(f'  {data["home_name"]}近期: {len(data["home_form"])} 场')
            print(f'  {data["away_name"]}近期: {len(data["away_form"])} 场')
            print(f'  eOdds: {len(data["eOdds"])} 家公司')
        else:
            print(f'[FAIL] sid={sid}')
    
    else:
        # 默认测试
        sid = sys.argv[1] if len(sys.argv) > 1 else '2920917'
        print(f'=== 测试 sid={sid} ===')
        
        # 平博
        p = get_odds_history(sid, '432')
        if p:
            print(f'平博: 开盘 {p["open"]["win"]}/{p["open"]["draw"]}/{p["open"]["loss"]}  '
                  f'→ 最新 {p["latest"]["win"]}/{p["latest"]["draw"]}/{p["latest"]["loss"]} ({p["changes"]}次变化)')
        
        # HKJC
        h = get_odds_history(sid, '177')
        if h:
            print(f'HKJC: 开盘 {h["open"]["win"]}/{h["open"]["draw"]}/{h["open"]["loss"]}  '
                  f'→ 最新 {h["latest"]["win"]}/{h["latest"]["draw"]}/{h["latest"]["loss"]} ({h["changes"]}次变化)')
        
        # 分析页
        a = get_analysis_data(sid)
        if a:
            print(f'分析页: {a["home_name"]} vs {a["away_name"]} [{a["league_name"]}]')
            print(f'  H2H: {len(a["h2h"])}场, 主近期: {len(a["home_form"])}场, 客近期: {len(a["away_form"])}场')
