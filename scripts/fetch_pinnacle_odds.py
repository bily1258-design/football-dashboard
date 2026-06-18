#!/usr/bin/env python3
"""从中国足彩网(zgzcw.com)百家指数页面抓取各家欧赔数据

【重要】数据源说明：
- 本脚本所有赔率数据来自【中国足彩网 zgzcw.com】，不是500.com，也不是中国竞彩网(sporttery.cn)
- 500.com (fetch_500com_results.py) 只抓赛果，不抓赔率
- 中国竞彩网 (sporttery.cn) 是体彩官方平台，本脚本未使用
- 中国足彩网 (zgzcw.com) 是第一视频集团旗下彩票资讯平台，提供百家指数赔率对比

数据源页面：
1. 列表页 https://plzx.zgzcw.com/bjzs - GET请求，返回百家平均欧赔
2. POST请求同一URL + company参数 - 返回指定公司赔率
   - aid=106: 真Pinnacle(平博)
   - aid=56: Betfair(必发)
   - aid=3: SB(明升)
   - aid=136: HKJC(香港马会)
3. 详情页 http://fenxi.zgzcw.com/{match_id}/bjop - 被CloudWAF拦截，需fetch_web

【HHAD让球盘问题】：
- 北单(type=bd)页面返回让球盘(HHAD)赔率而非标准盘(HAD)，数据不可信
- 让球盘特征：d值(本应为平局赔率)异常低(<2.0)，实际是让球方胜赔
- 竞彩(type=jc)页面正常，无此问题
- 处理方式：已将include_beidan默认改为False，直接禁用北单赔率抓取
- 北单场次赔率降级为百家平均或竞彩HAD
- d<2.0检测仍保留作为保险，防止有人手动开启include_beidan
"""

import re
import json
import os
import time
import subprocess
import sqlite3
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 智能检测仓库结构
_REPO_DIR = os.path.dirname(BASE_DIR)
if os.path.isdir(os.path.join(_REPO_DIR, 'data')):
    DATA_BASE_DIR = _REPO_DIR
else:
    DATA_BASE_DIR = BASE_DIR
DB_PATH = os.path.join(DATA_BASE_DIR, "data/football.db")
DB_JINGCAI = DB_PATH  # 统一用football.db
DB_BEIDAN = DB_PATH   # 统一用football.db

BASE_URL_LIST = "https://plzx.zgzcw.com/bjzs"
BASE_URL_DETAIL = "http://fenxi.zgzcw.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml",
}


# ========== 队名映射表（oddsmagnet → DB竞彩标准名）==========
# 来源：oddsmagnet缓存 vs football.db 对比，仅收录高置信度映射
# 规则：key=oddsmagnet用名, value=DB中竞彩/体彩标准名
TEAM_NAME_ALIASES = {
    # ===== 日本J联赛 =====
    '町田泽维亚': '町田泽维',
    '神户胜利船': '神户胜利',
    '名古屋鲸鱼': '名古屋鲸',
    '长崎成功丸': '长崎航海',
    '清水心跳': '清水鼓动',
    # ===== 韩国K联赛 =====
    '安山小绿人': '安山新军',
    '坡州前线': '坡州市民',
    # ===== 瑞典超/甲 =====
    '哥德堡盖斯': '盖斯',
    '瓦斯特拉斯': '韦斯特罗',
    '布洛马波卡纳': '布鲁马波',
    '法尔肯堡': '法尔肯贝里',
    '兰斯科罗纳': '兰斯科罗',
    # ===== 丹麦超/甲 =====
    '奥胡斯费马': '奥胡斯',
    '希勒罗德': '希勒勒',
    'B93哥本哈根': 'B93哥本',
    # ===== 芬兰超 =====
    'EIF埃克纳斯': '埃克奈斯',
    'VPS瓦萨': '瓦萨',
    '奥卢': 'AC奥卢',
    'TPS图尔库': 'TPS图尔',
    'KA阿克雷里': 'KA阿古雷',
    '托尔阿克雷里': '托尔',
    'PK35万塔': '万塔',
    # ===== 冰岛超 =====
    '凯夫拉维克': '凯夫拉维',
    '维京古': '维京古尔',
    # ===== 巴西甲/乙 =====
    '布拉干蒂诺RB': '布拉干蒂诺',
    '诺瓦里桑蒂诺': '诺瓦桑蒂诺',
    '戈亚尼亚竞技': '戈亚尼亚',
    '圣贝尔纳多': '圣贝纳多',
    '米拉索': '米拉索尔',
    # ===== 阿甲 =====
    '罗萨里奥中央': '罗萨里奥',
    # ===== 其他 =====
    '安道尔FC': '安道尔',
}

# ========== 队名相似度函数 ==========

def team_name_similarity(name1, name2):
    """计算两队名的相似度 (0-1)
    
    匹配策略（按优先级）：
    0. 静态映射表 TEAM_NAME_ALIASES 精确匹配
    1. 完全匹配
    2. 包含匹配（一个包含另一个）
    3. 常见简称映射后匹配
    4. 去掉常见前缀/后缀后匹配
    5. 编辑距离模糊匹配
    """
    if not name1 or not name2:
        return 0.0
    
    # 策略0: 静态映射表查表（oddsmagnet名→DB名）
    n1_alias = TEAM_NAME_ALIASES.get(name1.strip(), name1.strip())
    n2_alias = TEAM_NAME_ALIASES.get(name2.strip(), name2.strip())
    if n1_alias == n2_alias:
        return 1.0
    if n1_alias in n2_alias or n2_alias in n1_alias:
        return 0.95
    
    # 标准化：去除空格、转小写
    n1 = name1.strip().lower()
    n2 = name2.strip().lower()
    
    # 策略1: 完全匹配
    if n1 == n2:
        return 1.0
    
    # 策略2: 包含匹配
    if n1 in n2 or n2 in n1:
        # 短名包含在长名中，得分根据长度比例
        ratio = max(len(n1), len(n2)) / min(len(n1), len(n2)) if min(len(n1), len(n2)) > 0 else 1
        # 如果比例接近1（差异小于50%），认为是同一队
        if ratio < 1.5:
            return 0.9
        elif ratio < 2.0:
            return 0.85
        else:
            return 0.75
    
    # 策略3: 常见简称映射
    abbr_map = {
        # 曼城系
        '曼城': '曼彻斯特城', '曼彻斯特城': '曼彻斯特城',
        '曼联': '曼彻斯特联', '曼彻斯特联': '曼彻斯特联',
        # 热刺系
        '热刺': '托特纳姆热刺', '托特纳姆热刺': '托特纳姆热刺',
        # 皇马系
        '皇马': '皇家马德里', '皇家马德里': '皇家马德里',
        # 巴萨系
        '巴萨': '巴塞罗那', '巴塞罗那': '巴塞罗那',
        # 马竞系
        '马竞': '马德里竞技', '马德里竞技': '马德里竞技',
        # 拜仁系
        '拜仁': '拜仁慕尼黑', '拜仁慕尼黑': '拜仁慕尼黑',
        # 国米系
        '国米': '国际米兰', '国际米兰': '国际米兰',
        # 尤文系
        '尤文': '尤文图斯', '尤文图斯': '尤文图斯',
        # 多特系
        '多特': '多特蒙德', '多特蒙德': '多特蒙德',
        # 切尔西系
        '切尔西': '切尔西',
        # 阿森纳系
        '阿森纳': '阿森纳',
        # 利物浦系
        '利物浦': '利物浦',
        # 米兰系
        'AC米兰': 'AC米兰', '米兰': 'AC米兰',
        # 罗马系
        '罗马': '罗马',
        # 那不勒斯系
        '那不勒斯': '那不勒斯',
        # 勒沃库森
        '勒沃': '勒沃库森', '勒沃库森': '勒沃库森',
        # 莱比锡
        '莱比锡': 'RB莱比锡', 'RB莱比锡': 'RB莱比锡',
        # 法甲
        '里昂': '里昂', '马赛': '马赛', '摩纳哥': '摩纳哥',
        # 中超
        '上港': '上海海港', '上海海港': '上海海港',
        '国安': '北京国安', '北京国安': '北京国安',
        '泰山': '山东泰山', '山东泰山': '山东泰山',
        '恒大': '广州恒大', '广州恒大': '广州恒大',
        '恒大': '广州队', '广州队': '广州队',
        '海港': '上海海港',
        '三镇': '武汉三镇', '武汉三镇': '武汉三镇',
        '蓉城': '成都蓉城', '成都蓉城': '成都蓉城',
        '河南': '河南队', '河南队': '河南队',
        # 日职
        '川崎': '川崎前锋', '川崎前锋': '川崎前锋',
        '横滨': '横滨水手', '横滨水手': '横滨水手',
        '鹿岛': '鹿岛鹿角', '鹿岛鹿角': '鹿岛鹿角',
        '樱花': '大阪樱花', '大阪樱花': '大阪樱花',
        '钢巴': '大阪钢巴', '大阪钢巴': '大阪钢巴',
        # 瑞超同队不同译名（映射到统一标准名）
        '佐加顿斯': '佐加顿斯', '尤尔加登': '佐加顿斯',
        '厄格里特': '厄格里特', '奥尔格里特': '厄格里特',
        '哥德堡': '哥德堡', 'IFK哥德堡': '哥德堡',
        '索尔纳': '索尔纳', 'AIK索尔纳': '索尔纳',
        # 韩K
        '全北': '全北现代', '全北现代': '全北现代',
        '蔚山': '蔚山现代', '蔚山现代': '蔚山现代',
        '水原': '水原三星', '水原三星': '水原三星',
        # 南美 - 巴西
        '弗鲁米嫩': '弗鲁米嫩塞', '弗鲁米嫩塞': '弗鲁米嫩塞',
        '弗拉门戈': '弗拉门戈', '弗拉门': '弗拉门戈',
        '帕尔梅拉斯': '帕尔梅拉斯', '帕尔梅': '帕尔梅拉斯',
        '科林蒂安': '科林蒂安', '科林蒂': '科林蒂安',
        '圣保罗': '圣保罗',
        '桑托斯': '桑托斯',
        '格雷米奥': '格雷米奥', '格雷米': '格雷米奥',
        '国际体育': '国际体育会', '国际体育会': '国际体育会',
        '巴伊亚': '巴伊亚',
        '福塔雷萨': '福塔雷萨',
        '布拉干蒂诺': '布拉干蒂诺',
        '库亚巴': '库亚巴',
        '尤文图德': '尤文图德',
        '戈亚尼亚': '戈亚尼亚竞技', '戈亚尼亚竞技': '戈亚尼亚竞技',
        '阿瓦伊': '阿瓦伊',
        # 南美 - 阿根廷
        '罗萨里奥': '罗萨里奥中央', '罗萨里奥中央': '罗萨里奥中央',
        '博卡青年': '博卡青年', '博卡': '博卡青年',
        '河床': '河床',
        '竞技俱乐部': '竞技俱乐部', '竞技': '竞技俱乐部',
        '独立': '独立队', '独立队': '独立队',
        '圣洛伦索': '圣洛伦索',
        '拉普拉塔': '拉普拉塔大学生', '拉普拉塔大学生': '拉普拉塔大学生',
        '防御与正义': '防御与正义',
        '飓风': '飓风队', '飓风队': '飓风队',
        '塔勒雷斯': '塔勒雷斯',
        '贝尔格拉诺': '贝尔格拉诺',
        '萨斯菲尔德': '萨斯菲尔德',
        '班菲尔德': '班菲尔德',
        '阿根廷青年': '阿根廷青年人', '阿根廷青年人': '阿根廷青年人',
        # 南美 - 玻利维亚/其他
        '时刻准备': '时刻准备', '拉巴斯准备': '时刻准备', '斯特朗est': '时刻准备',
        '玻利瓦尔': '玻利瓦尔',
        '奥尔良': '奥尔良',
        '威斯特曼': '威斯特曼',
        # 南美 - 解放者杯/南美杯常见
        '佩纳罗尔': '佩纳罗尔',
        '民族': '民族队', '民族队': '民族队',
        '自由': '自由队', '自由队': '自由队',
        '奥林匹亚': '奥林匹亚',
        '瓜拉尼': '瓜拉尼',
        '亚松森': '亚松森自由', '亚松森自由': '亚松森自由',
        # 墨西哥
        '美洲': '美洲队', '美洲队': '美洲队',
        '蓝十字': '蓝十字',
        '瓜达拉哈拉': '瓜达拉哈拉',
        '蒙特雷': '蒙特雷',
        '老虎大学': '老虎大学',
        # 哥伦比亚
        '百万富翁': '百万富翁',
        '卡利体育': '卡利体育',
        '国民竞技': '国民竞技',
        # 智利
        '科洛科洛': '科洛科洛',
        '天主教大学': '天主教大学',
        # 厄瓜多尔
        '基多大学': '基多大学',
        '基多民族': '基多民族',
    }
    
    n1_mapped = abbr_map.get(n1, n1)
    n2_mapped = abbr_map.get(n2, n2)
    
    # 如果简称映射后完全匹配，返回高相似度
    if n1_mapped == n2_mapped:
        return 0.95
    
    # 如果一个名称被映射后包含另一个，也返回高相似度
    if n1_mapped in n2_mapped or n2_mapped in n1_mapped:
        return 0.90
    
    # 策略4: 去掉常见前缀/后缀后匹配
    prefixes = ['fc', 'cf', 'sc', 'ac', 'rc', '体育', '足球', '俱乐部']
    suffixes = ['fc', 'cf', 'sc', 'ac', 'rc', '体育', '足球', '俱乐部', '队']
    
    def strip_prefix_suffix(name):
        n = name.lower()
        for p in prefixes:
            if n.startswith(p):
                n = n[len(p):].strip()
        for s in suffixes:
            if n.endswith(s):
                n = n[:-len(s)].strip()
        return n
    
    n1_stripped = strip_prefix_suffix(n1)
    n2_stripped = strip_prefix_suffix(n2)
    
    if n1_stripped and n2_stripped:
        if n1_stripped == n2_stripped:
            return 0.9
        if n1_stripped in n2_stripped or n2_stripped in n1_stripped:
            return 0.85
    
    # 策略5: 编辑距离模糊匹配
    dist = levenshtein_distance(n1_stripped, n2_stripped)
    max_len = max(len(n1_stripped), len(n2_stripped))
    if max_len == 0:
        return 0.0
    
    similarity = 1.0 - (dist / max_len)
    
    # 如果编辑距离相似度超过阈值，认为是同一队
    if similarity >= 0.8:
        return similarity * 0.9  # 稍微降低权重
    elif similarity >= 0.6:
        return similarity * 0.8
    else:
        return similarity * 0.5  # 低于0.6的相似度权重更低


def levenshtein_distance(s1, s2):
    """计算两个字符串的编辑距离"""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    
    if len(s2) == 0:
        return len(s1)
    
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    
    return previous_row[-1]


def fetch_page_requests(url, timeout=30):
    """用requests抓取页面，带WAF重试"""
    import requests
    import random
    max_retries = 2
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            # WAF检测
            if resp.status_code == 418:
                wait = 5 + random.randint(3, 8)
                print(f"[WARN] GET WAF拦截(418), 等待{wait}秒后重试({attempt+1}/{max_retries})")
                time.sleep(wait)
                continue
            resp.encoding = 'utf-8'
            if 'CloudWAF' in resp.text or '华为云' in resp.text:
                wait = 5 + random.randint(3, 8)
                print(f"[WARN] GET CloudWAF拦截, 等待{wait}秒后重试({attempt+1}/{max_retries})")
                time.sleep(wait)
                continue
            return resp.text
        except Exception as e:
            wait = 3 + random.randint(2, 5)
            print(f"[ERROR] requests {url}: {e}, 等待{wait}秒后重试({attempt+1}/{max_retries})")
            time.sleep(wait)
    print(f"[ERROR] GET {url} {max_retries}次重试均失败")
    return None


def fetch_page_fetch_web(url):
    """用fetch_web工具抓取页面（绕过CloudWAF）
    
    由于详情页被CloudWAF拦截，此函数尝试：
    1. 直接requests（可能被拦截）
    2. 读取由coze fetch_web工具预抓取的HTML缓存文件
    """
    import requests
    # 尝试1：直接请求
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
            "Accept": "text/html,application/xhtml+xml",
            "Referer": "https://plzx.zgzcw.com/",
        }
        resp = requests.get(url, headers=headers, timeout=30)
        resp.encoding = 'utf-8'
        if 'CloudWAF' not in resp.text and '足彩网' in resp.text:
            # 保存到缓存
            match_id = url.split('/')[-2] if '/' in url else ''
            cache_file = os.path.join(DATA_BASE_DIR, f"data/cache/bjop_{match_id}.html")
            os.makedirs(os.path.dirname(cache_file), exist_ok=True)
            with open(cache_file, 'w', encoding='utf-8') as f:
                f.write(resp.text)
            return resp.text
    except Exception as e:
        print(f"[WARN] requests {url} failed: {e}")
    
    # 尝试2：读取缓存文件（由coze fetch_web预抓取保存）
    match_id = url.split('/')[-2] if '/' in url else ''
    cache_file = os.path.join(DATA_BASE_DIR, f"data/cache/bjop_{match_id}.html")
    if os.path.exists(cache_file):
        with open(cache_file, 'r', encoding='utf-8') as f:
            content = f.read()
            if '足彩网' in content:
                return content
    
    print(f"[WARN] 详情页无法抓取且无缓存: {url}")
    return None


def parse_odds_value(text):
    """解析赔率值，去掉箭头标记。有效赔率范围1.01-50.0"""
    if not text:
        return 0.0
    text = str(text).strip().replace('↑', '').replace('↓', '').replace('→', '').replace('＊', '*')
    try:
        val = float(text)
        # 赔率合理范围：1.01~50.0，超出视为非赔率数据（如行号72）
        if val < 1.01 or val > 50.0:
            return 0.0
        return val
    except:
        return 0.0


def parse_movement(text):
    """解析赔率变动方向"""
    if not text:
        return 'stable'
    text = str(text).strip()
    if '↑' in text:
        return 'up'
    elif '↓' in text:
        return 'down'
    return 'stable'


def calc_implied_prob(win, draw, loss):
    """计算去抽水真实概率"""
    if win <= 0 or draw <= 0 or loss <= 0:
        return {'w': 0, 'd': 0, 'l': 0}, 0.0
    
    imp_w = 1.0 / win
    imp_d = 1.0 / draw
    imp_l = 1.0 / loss
    total = imp_w + imp_d + imp_l
    margin = total - 1.0
    
    true_w = imp_w / total
    true_d = imp_d / total
    true_l = imp_l / total
    
    return {'w': round(true_w, 4), 'd': round(true_d, 4), 'l': round(true_l, 4)}, round(margin, 4)


def fetch_match_list(date_str=None, page_type=None, aid=None):
    """抓取百家指数列表页，返回当日所有场次（GET方式，返回百家平均赔率）
    
    Args:
        date_str: 日期字符串
        page_type: None=竞彩, 'bd'=北单
        aid: 公司ID（注意：GET方式的aid参数实际不影响页面数据，始终返回百家平均）
    """
    url = BASE_URL_LIST
    params = []
    if date_str:
        params.append(f"date={date_str}")
    if page_type:
        params.append(f"type={page_type}")
    if params:
        url = url + "?" + "&".join(params)
    
    html = fetch_page_requests(url)
    if not html:
        return []
    
    matches = []
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
    
    for row in rows:
        # 提取对阵
        teams = re.findall(r'<a[^>]*class="t[12]"[^>]*>([^<]+)</a>', row)
        if len(teams) < 2:
            continue
        
        # 提取match_id
        match_ids = re.findall(r'fenxi\.zgzcw\.com/(\d+)/bjop', row)
        if not match_ids:
            continue
        
        # 提取时间
        kickoff = re.findall(r'(\d{2}-\d{2}\s+\d{2}:\d{2})', row)
        
        # 提取赔率（初盘3个+最新3个）
        # 格式: 4.420 4.250 1.700 4.37→ 4.26→ 1.68↓
        all_odds_raw = re.findall(r'>(\d+\.\d+(?:[↑↓→])?)<', row)
        
        # 也从td中提取
        td_contents = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        
        odds_from_td = []
        for td in td_contents:
            td_clean = re.sub(r'<[^>]+>', '', td).strip()
            val = parse_odds_value(td_clean)
            if val > 0:
                odds_from_td.append((val, td_clean))
        
        # 初盘和最新赔率
        open_w = open_d = open_l = close_w = close_d = close_l = 0
        close_w_raw = close_d_raw = close_l_raw = ''
        
        if len(odds_from_td) >= 6:
            open_w = odds_from_td[0][0]
            open_d = odds_from_td[1][0]
            open_l = odds_from_td[2][0]
            close_w = odds_from_td[3][0]
            close_d = odds_from_td[4][0]
            close_l = odds_from_td[5][0]
            close_w_raw = odds_from_td[3][1]
            close_d_raw = odds_from_td[4][1]
            close_l_raw = odds_from_td[5][1]
        
        match = {
            'home': teams[0],
            'away': teams[1],
            'match_id': match_ids[0],
            'kickoff': kickoff[0] if kickoff else '',
            'avg_open': {'w': open_w, 'd': open_d, 'l': open_l},
            'avg_close': {'w': close_w, 'd': close_d, 'l': close_l},
            'avg_movement': {
                'w': parse_movement(close_w_raw),
                'd': parse_movement(close_d_raw),
                'l': parse_movement(close_l_raw),
            }
        }
        matches.append(match)
    
    return matches


def fetch_company_odds(date_str=None, page_type=None, company='106'):
    """通过POST方式抓取指定公司赔率
    
    足彩网百家指数公司ID映射（aid）:
    - 0: 平均*(百家平均), 3: SB(明升), 22: 平*(假Pinnacle), 56: 必*(Betfair)
    - 106: 平*(真Pinnacle)
    
    Args:
        date_str: 日期字符串
        page_type: None=竞彩, 'bd'=北单
        company: 公司aid，'106'=真Pinnacle, '56'=Betfair, '3'=SB
    Returns:
        dict: {match_id: {open: {w,d,l}, close: {w,d,l}, movement: {w,d,l}}}
    """
    import requests as req
    import random
    
    company_names = {'106': 'Pinnacle', '56': 'Betfair', '3': 'SB', '22': 'Pinnacle(旧)', '0': '平均', '136': 'HKJC'}
    company_name = company_names.get(company, f'aid={company}')
    
    data = {
        'type': page_type if page_type else 'jc',
        'issue': '',
        'company': company,
        'companyType': 'b',
        'date': date_str or datetime.now().strftime('%Y-%m-%d'),
        'fg': '1'
    }
    
    # WAF重试机制：最多重试3次，间隔5-8秒
    max_retries = 2
    for attempt in range(max_retries):
        try:
            # 使用Session复用连接
            session = req.Session()
            session.headers.update(HEADERS)
            # 先GET一次首页建立cookie
            if attempt == 0:
                try:
                    session.get(BASE_URL_LIST, timeout=10)
                    time.sleep(2)
                except:
                    pass
            
            resp = session.post(BASE_URL_LIST, data=data, timeout=15)
            
            # WAF检测
            if resp.status_code == 418:
                wait = 5 + random.randint(3, 8)
                print(f"[WARN] {company_name} WAF拦截(418), 等待{wait}秒后重试({attempt+1}/{max_retries})")
                time.sleep(wait)
                continue
            
            if 'CloudWAF' in resp.text or '华为云' in resp.text:
                wait = 5 + random.randint(3, 8)
                print(f"[WARN] {company_name} CloudWAF拦截, 等待{wait}秒后重试({attempt+1}/{max_retries})")
                time.sleep(wait)
                continue
            
            if resp.status_code != 200:
                print(f"[WARN] {company_name} POST请求失败: status={resp.status_code}")
                return {}
            
            html = resp.text
            if not html:
                return {}
            
            # 成功，跳出重试
            break
            
        except Exception as e:
            wait = 3 + random.randint(2, 5)
            print(f"[WARN] {company_name} POST请求异常: {e}, 等待{wait}秒后重试({attempt+1}/{max_retries})")
            time.sleep(wait)
    else:
        print(f"[ERROR] {company_name} {max_retries}次重试均失败")
        return {}
    
    result = {}
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
    
    for row in rows:
        teams = re.findall(r'<a[^>]*class="t[12]"[^>]*>([^<]+)</a>', row)
        if len(teams) < 2:
            continue
        
        match_ids = re.findall(r'fenxi\.zgzcw\.com/(\d+)/bjop', row)
        if not match_ids:
            continue
        
        # 提取赔率
        td_contents = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        odds_from_td = []
        for td in td_contents:
            td_clean = re.sub(r'<[^>]+>', '', td).strip()
            val = parse_odds_value(td_clean)
            if val > 0:
                odds_from_td.append((val, td_clean))
        
        open_w = open_d = open_l = close_w = close_d = close_l = 0
        close_w_raw = close_d_raw = close_l_raw = ''
        
        if len(odds_from_td) >= 6:
            open_w = odds_from_td[0][0]
            open_d = odds_from_td[1][0]
            open_l = odds_from_td[2][0]
            close_w = odds_from_td[3][0]
            close_d = odds_from_td[4][0]
            close_l = odds_from_td[5][0]
            close_w_raw = odds_from_td[3][1]
            close_d_raw = odds_from_td[4][1]
            close_l_raw = odds_from_td[5][1]
        
        result[match_ids[0]] = {
            'home': teams[0],
            'away': teams[1],
            'open': {'w': open_w, 'd': open_d, 'l': open_l},
            'close': {'w': close_w, 'd': close_d, 'l': close_l},
            'movement': {
                'w': parse_movement(close_w_raw),
                'd': parse_movement(close_d_raw),
                'l': parse_movement(close_l_raw),
            }
        }
    
    print(f"[INFO] {company_name} POST: {len(result)} 场")
    return result


def fetch_sb_odds(date_str=None, page_type=None):
    """通过POST方式抓取SB公司赔率（company=3）- 保留兼容接口"""
    return fetch_company_odds(date_str, page_type, company='3')


def parse_ah_value(text):
    """解析亚盘水位/盘口值，去掉箭头标记
    
    与parse_odds_value不同：
    - 水位范围0~3.0（如0.750, 1.67）
    - 盘口值可为负数（如-0.250, -1.0, 0.5）
    """
    if not text:
        return 0.0, 'stable'
    text = str(text).strip()
    movement = 'stable'
    if '↑' in text:
        movement = 'up'
    elif '↓' in text:
        movement = 'down'
    elif '→' in text:
        movement = 'stable'
    text = text.replace('↑', '').replace('↓', '').replace('→', '').replace('＊', '*').strip()
    try:
        val = float(text)
        return val, movement
    except:
        return 0.0, 'stable'


def fetch_asian_handicap(date_str=None, page_type=None, company='0'):
    """通过POST方式抓取亚盘让球盘数据 (companyType=y)
    
    亚盘数据格式与欧赔不同：
    - 欧赔: w/d/l (胜/平/负赔率)
    - 亚盘: home_water / handicap_line / away_water (主队水位/盘口/客队水位)
    
    HTML结构示例（百家平均亚盘，company=0）：
    TDs: ['', '周五003', '世界杯', '06-13 03:00', '加拿大VS波黑',
          '0.750', '-0.250', '0.990',   ← 初盘: 主水/盘口/客水
          '1.67↑', '-0.250→', '1.41↓',  ← 收盘: 主水/盘口/客水
          '欧 亚 析']
    
    Args:
        date_str: 日期字符串
        page_type: None=竞彩, 'bd'=北单
        company: '0'=百家平均, '136'=HKJC
    Returns:
        dict: {match_id: {open: {home_w, handicap, away_w}, close: {home_w, handicap, away_w}}}
    """
    import requests as req
    import random
    
    company_names = {'0': '百家平均', '136': 'HKJC'}
    company_name = company_names.get(company, f'aid={company}')
    
    data = {
        'type': page_type if page_type else 'jc',
        'issue': '',
        'company': company,
        'companyType': 'y',  # y=亚盘
        'date': date_str or datetime.now().strftime('%Y-%m-%d'),
        'fg': '1'
    }
    
    # WAF重试机制：最多重试2次
    max_retries = 2
    for attempt in range(max_retries):
        try:
            session = req.Session()
            session.headers.update(HEADERS)
            if attempt == 0:
                try:
                    session.get(BASE_URL_LIST, timeout=10)
                    time.sleep(2)
                except:
                    pass
            
            resp = session.post(BASE_URL_LIST, data=data, timeout=15)
            
            if resp.status_code == 418:
                wait = 5 + random.randint(3, 8)
                print(f"[WARN] 亚盘{company_name} WAF拦截(418), 等待{wait}秒后重试({attempt+1}/{max_retries})")
                time.sleep(wait)
                continue
            
            if 'CloudWAF' in resp.text or '华为云' in resp.text:
                wait = 5 + random.randint(3, 8)
                print(f"[WARN] 亚盘{company_name} CloudWAF拦截, 等待{wait}秒后重试({attempt+1}/{max_retries})")
                time.sleep(wait)
                continue
            
            if resp.status_code != 200:
                print(f"[WARN] 亚盘{company_name} POST请求失败: status={resp.status_code}")
                return {}
            
            html = resp.text
            if not html:
                return {}
            
            break
            
        except Exception as e:
            wait = 3 + random.randint(2, 5)
            print(f"[WARN] 亚盘{company_name} POST请求异常: {e}, 等待{wait}秒后重试({attempt+1}/{max_retries})")
            time.sleep(wait)
    else:
        print(f"[ERROR] 亚盘{company_name} {max_retries}次重试均失败")
        return {}
    
    result = {}
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
    
    for row in rows:
        teams = re.findall(r'<a[^>]*class="t[12]"[^>]*>([^<]+)</a>', row)
        if len(teams) < 2:
            continue
        
        match_ids = re.findall(r'fenxi\.zgzcw\.com/(\d+)/bjop', row)
        if not match_ids:
            continue
        
        # 提取TD内容
        td_contents = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        td_values = []
        for td in td_contents:
            td_clean = re.sub(r'<[^>]+>', '', td).strip()
            td_values.append(td_clean)
        
        # 亚盘数据解析：TD中有6个赔率值（初盘3+收盘3）
        # 格式: 初盘主水, 初盘盘口, 初盘客水, 收盘主水, 收盘盘口, 收盘客水
        ah_values = []
        for tv in td_values:
            # 跳过空TD、编号、联赛名、时间、队名、操作链接等
            val, movement = parse_ah_value(tv)
            if val != 0.0 or tv.startswith('-') or tv.startswith('0.') or tv.startswith('1.') or tv.startswith('2.'):
                # 检查是否像水位或盘口值
                try:
                    float(tv.replace('↑','').replace('↓','').replace('→','').strip())
                    ah_values.append((val, movement, tv))
                except:
                    continue
        
        open_home_w = open_handicap = open_away_w = 0.0
        close_home_w = close_handicap = close_away_w = 0.0
        
        if len(ah_values) >= 6:
            open_home_w = ah_values[0][0]
            open_handicap = ah_values[1][0]
            open_away_w = ah_values[2][0]
            close_home_w = ah_values[3][0]
            close_handicap = ah_values[4][0]
            close_away_w = ah_values[5][0]
        
        # 基本校验：盘口和水位不能全为0
        if open_home_w == 0 and open_away_w == 0 and close_home_w == 0 and close_away_w == 0:
            continue
        
        result[match_ids[0]] = {
            'home': teams[0],
            'away': teams[1],
            'open': {'home_w': open_home_w, 'handicap': open_handicap, 'away_w': open_away_w},
            'close': {'home_w': close_home_w, 'handicap': close_handicap, 'away_w': close_away_w},
        }
    
    print(f"[INFO] 亚盘{company_name} POST: {len(result)} 场")
    return result


def parse_detail_page(html):
    """解析详情页，提取平博和平均欧赔
    
    HTML表格结构（从实际页面分析）：
    | 序号 | 公司 | 胜(初) | 平(初) | 负(初) | 胜(最新) | 平(最新) | 负(最新) | ...
    
    平博行示例（第25行）：
    | 25 | 平* | 4.63 | 4.34 | 1.61 | [4.59↑] | [4.42] | [1.69] | ...
    
    初盘赔率在纯文本中，最新赔率在链接中。
    """
    result = {
        'pinnacle_open': {'w': 0, 'd': 0, 'l': 0},
        'pinnacle_close': {'w': 0, 'd': 0, 'l': 0},
        'pinnacle_movement': {'w': 'stable', 'd': 'stable', 'l': 'stable'},
        'pinnacle_margin': 0,
        'implied_prob': {'w': 0, 'd': 0, 'l': 0},
        'williamhill': {'w': 0, 'd': 0, 'l': 0},
        'bet365': {'w': 0, 'd': 0, 'l': 0},
        'sb_odds': {'w': 0, 'd': 0, 'l': 0},  # SB公司最新赔率
    }
    
    if not html or 'CloudWAF' in html:
        return result
    
    # 方法：解析每行表格数据
    # HTML格式：<tr>...<td>公司名</td><td>胜(初)</td><td>平(初)</td><td>负(初)</td>...
    #                          <td>胜(最新)</td><td>平(最新)</td><td>负(最新)</td>...
    
    # 匹配表格行
    tr_pattern = r'<tr[^>]*>(.*?)</tr>'
    rows = re.findall(tr_pattern, html, re.DOTALL | re.IGNORECASE)
    
    for row in rows:
        # 提取公司ID
        company_id_match = re.search(r'company[_-]?id[=:]?\s*["\']?(\d+)', row, re.IGNORECASE)
        company_id = int(company_id_match.group(1)) if company_id_match else None
        
        # 提取公司名称（用于识别平博、威廉希尔、Bet365等）
        company_name = ''
        # 提取公司名称 - 先剥离内部HTML标签（如<font>），再匹配
        td_name_match = re.search(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        if td_name_match:
            company_name = re.sub(r'<[^>]+>', '', td_name_match.group(1)).strip()
        else:
            company_name = ''
        
        # 判断是哪家公司
        is_pinnacle = (company_id == 106) or ('平' in company_name and '均' not in company_name)
        is_william = (company_id == 9) or '威' in company_name
        is_bet365 = (company_id == 2) or '36' in company_name
        is_avg = (company_id == 0) or '平均' in company_name
        is_sb = ('ＳＢ' in company_name or 'SB' in company_name or '沙' in company_name)
        
        if not (is_pinnacle or is_william or is_bet365 or is_avg or is_sb):
            continue
        
        # 提取该行中的所有td
        tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        
        if len(tds) < 7:
            continue
        
        # 初盘赔率在td[2], td[3], td[4] (序号、公司、胜、平、负)
        # 最新赔率在td[5], td[6], td[7]
        
        # 提取初盘赔率（纯文本数字）
        open_odds = []
        for i in range(2, 5):  # td[2], td[3], td[4]
            td_text = re.sub(r'<[^>]+>', '', tds[i]).strip()
            val = parse_odds_value(td_text)
            if 1.0 <= val <= 50.0:
                open_odds.append(val)
        
        # 提取最新赔率（从链接中提取数字+方向）
        close_odds = []
        close_raw = []
        for i in range(5, 8):  # td[5], td[6], td[7]
            td_html = tds[i]
            # 先提取标签内容
            td_text = re.sub(r'<[^>]+>', '', td_html).strip()
            # 从纯文本中提取赔率数字
            match = re.search(r'(\d+\.?\d*)([↑↓])?', td_text)
            if match:
                val = float(match.group(1))
                direction = match.group(2) or ''
                if 1.0 <= val <= 50.0:
                    close_odds.append(val)
                    close_raw.append(f"{val}{direction}")
            else:
                # 尝试直接从td内容提取
                val = parse_odds_value(td_text)
                if 1.0 <= val <= 50.0:
                    close_odds.append(val)
                    close_raw.append(str(val))
        
        # 需要3个初盘 + 3个最新赔率
        if len(open_odds) >= 3 and len(close_odds) >= 3:
            open_w, open_d, open_l = open_odds[0], open_odds[1], open_odds[2]
            close_w, close_d, close_l = close_odds[0], close_odds[1], close_odds[2]
            
            if is_pinnacle:
                result['pinnacle_open'] = {'w': open_w, 'd': open_d, 'l': open_l}
                result['pinnacle_close'] = {'w': close_w, 'd': close_d, 'l': close_l}
                result['pinnacle_movement'] = {
                    'w': parse_movement(close_raw[0] if len(close_raw) > 0 else ''),
                    'd': parse_movement(close_raw[1] if len(close_raw) > 1 else ''),
                    'l': parse_movement(close_raw[2] if len(close_raw) > 2 else ''),
                }
            elif is_william:
                result['williamhill'] = {'w': open_w, 'd': open_d, 'l': open_l}
            elif is_bet365:
                result['bet365'] = {'w': open_w, 'd': open_d, 'l': open_l}
            elif is_avg and result['pinnacle_open']['w'] == 0:
                # 如果没有平博数据，用平均欧赔作为fallback
                result['pinnacle_open'] = {'w': open_w, 'd': open_d, 'l': open_l}
                result['pinnacle_close'] = {'w': close_w, 'd': close_d, 'l': close_l}
                result['pinnacle_movement'] = {
                    'w': parse_movement(close_raw[0] if len(close_raw) > 0 else ''),
                    'd': parse_movement(close_raw[1] if len(close_raw) > 1 else ''),
                    'l': parse_movement(close_raw[2] if len(close_raw) > 2 else ''),
                }
            
            # SB公司赔率保存
            if is_sb:
                result['sb_odds'] = {'w': close_w, 'd': close_d, 'l': close_l}
            
            # 无论什么公司，平均欧赔都单独保存
            if is_avg:
                result['avg_odds_open'] = {'w': open_w, 'd': open_d, 'l': open_l}
                result['avg_odds_close'] = {'w': close_w, 'd': close_d, 'l': close_l}
    
    # 如果没有平博数据，用SB赔率作为fallback（SB覆盖更全，抽水约5-8%）
    if result['pinnacle_open']['w'] == 0 and result['sb_odds']['w'] > 0:
        result['pinnacle_open'] = result['sb_odds']
        result['pinnacle_close'] = result['sb_odds']
        result['pinnacle_movement'] = {'w': 'stable', 'd': 'stable', 'l': 'stable'}
        print(f"  [SB fallback] 使用SB赔率: {result['sb_odds']}")
    
    # 计算去抽水概率
    if result['pinnacle_open']['w'] > 0:
        implied, margin = calc_implied_prob(
            result['pinnacle_open']['w'],
            result['pinnacle_open']['d'],
            result['pinnacle_open']['l']
        )
        result['implied_prob'] = implied
        result['pinnacle_margin'] = margin
    
    return result


# ========== api-football Pinnacle 亚盘+大小球 ==========

API_FOOTBALL_BASE = "https://v3.football.api-sports.io"
API_FOOTBALL_KEY = "c942c5c572a6e946b196acd5321427c3"
PINNACLE_BOOKMAKER_ID = 4  # api-football中Pinnacle的bookmaker_id

# 中英文队名映射（足彩网中文名 → api-football英文名）
# 后续可通过LLM模糊匹配补充
TEAM_CN_EN_MAP = {
    # 常见队名，后续可通过搜索扩充
}


def fetch_api_football_fixtures(date_str):
    """获取api-football当天所有比赛的fixture_id
    
    Args:
        date_str: YYYY-MM-DD格式
    Returns:
        list[dict]: [{fixture_id, home_team, away_team, league, kickoff}]
    """
    import requests
    url = f"{API_FOOTBALL_BASE}/fixtures"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    params = {"date": date_str, "timezone": "Asia/Shanghai"}
    
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            print(f"[WARN] api-football fixtures HTTP {resp.status_code}")
            return []
        data = resp.json()
        if data.get("errors"):
            print(f"[WARN] api-football fixtures errors: {data['errors']}")
            return []
        
        fixtures = []
        for item in data.get("response", []):
            fixture = item.get("fixture", {})
            teams = item.get("teams", {})
            league = item.get("league", {})
            
            fixtures.append({
                "fixture_id": fixture.get("id"),
                "home_team": teams.get("home", {}).get("name", ""),
                "away_team": teams.get("away", {}).get("name", ""),
                "home_team_cn": "",  # 待模糊匹配填充
                "away_team_cn": "",
                "league": league.get("name", ""),
                "kickoff": fixture.get("date", ""),
            })
        
        print(f"[INFO] api-football fixtures: {len(fixtures)} 场 ({date_str})")
        return fixtures
    except Exception as e:
        print(f"[WARN] api-football fixtures 请求失败: {e}")
        return []


def fetch_api_football_pinnacle(fixture_id, bet_type):
    """从api-football获取Pinnacle的亚盘或大小球赔率
    
    Args:
        fixture_id: api-football的fixture ID
        bet_type: 4=Asian Handicap, 5=Goals Over/Under
    
    Returns:
        For AH (bet=4): {handicap_line: {home_odd, away_odd}} 全部盘口线
        For OU (bet=5): {line: {over, under}} 全部盘口线
        失败返回空dict
    """
    import requests
    url = f"{API_FOOTBALL_BASE}/odds"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    params = {
        "fixture": fixture_id,
        "bookmaker": PINNACLE_BOOKMAKER_ID,
        "bet": bet_type,
    }
    
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        if data.get("errors"):
            return {}
        
        result = {}
        for item in data.get("response", []):
            for bk in item.get("bookmakers", []):
                if bk.get("id") != PINNACLE_BOOKMAKER_ID:
                    continue
                for bet in bk.get("bets", []):
                    if str(bet.get("id")) != str(bet_type):
                        continue
                    for val in bet.get("values", []):
                        if bet_type == 4:
                            # Asian Handicap: value=handicap_line, odd=home_odd, odd2=away_odd
                            handicap = val.get("value", "")
                            home_odd = _safe_float_api(val.get("odd", 0))
                            away_odd = _safe_float_api(val.get("odd2", 0))
                            if handicap and home_odd > 0 and away_odd > 0:
                                try:
                                    h = float(handicap)
                                    result[handicap] = {"home_odd": home_odd, "away_odd": away_odd, "handicap": h}
                                except ValueError:
                                    pass
                        elif bet_type == 5:
                            # Goals Over/Under: value="Over/Under 2.5", odd=over/under
                            value_str = val.get("value", "")
                            odd = _safe_float_api(val.get("odd", 0))
                            # Parse: "Over 2.5" or "Under 2.5"
                            if "Over" in value_str:
                                try:
                                    line = float(value_str.replace("Over", "").strip())
                                    if str(line) not in result:
                                        result[str(line)] = {"over": odd, "under": 0, "line": line}
                                    else:
                                        result[str(line)]["over"] = odd
                                except ValueError:
                                    pass
                            elif "Under" in value_str:
                                try:
                                    line = float(value_str.replace("Under", "").strip())
                                    if str(line) not in result:
                                        result[str(line)] = {"over": 0, "under": odd, "line": line}
                                    else:
                                        result[str(line)]["under"] = odd
                                except ValueError:
                                    pass
        return result
    except Exception as e:
        print(f"[WARN] api-football odds (fixture={fixture_id}, bet={bet_type}): {e}")
        return {}


def _safe_float_api(val, default=0.0):
    """安全转换api-football返回的赔率值"""
    if not val:
        return default
    try:
        return float(str(val).strip())
    except (ValueError, TypeError):
        return default


def extract_pinnacle_ah_main(ah_data):
    """从Pinnacle AH数据中选取主盘口线（handicap绝对值最小的非零行）
    
    Args:
        ah_data: {handicap_str: {home_odd, away_odd, handicap}}
    Returns:
        {handicap, home_odd, away_odd} 或空dict
    """
    if not ah_data:
        return {}
    
    best = None
    best_abs = float('inf')
    for key, val in ah_data.items():
        h = val.get("handicap", 0)
        if h == 0:
            continue  # 跳过平手盘
        abs_h = abs(h)
        if abs_h < best_abs:
            best_abs = abs_h
            best = val
    
    if not best and ah_data:
        # 所有盘口线都是0，取第一条
        best = list(ah_data.values())[0]
    
    if not best:
        return {}
    
    return {
        "handicap": best.get("handicap", 0),
        "home_odd": best.get("home_odd", 0),
        "away_odd": best.get("away_odd", 0),
    }


def extract_pinnacle_ou_main(ou_data):
    """从Pinnacle OU数据中选取主盘口线（line最接近2.5的行）
    
    Args:
        ou_data: {line_str: {over, under, line}}
    Returns:
        {line, over, under} 或空dict
    """
    if not ou_data:
        return {}
    
    best = None
    best_diff = float('inf')
    for key, val in ou_data.items():
        line = val.get("line", 0)
        if line <= 0:
            continue
        diff = abs(line - 2.5)
        if diff < best_diff:
            best_diff = diff
            best = val
    
    if not best and ou_data:
        best = list(ou_data.values())[0]
    
    if not best:
        return {}
    
    return {
        "line": best.get("line", 0),
        "over": best.get("over", 0),
        "under": best.get("under", 0),
    }


def match_team_cn_to_en(cn_name, fixtures):
    """将中文队名模糊匹配到api-football的英文队名
    
    Returns: fixture dict 或 None
    """
    # 简单包含匹配（后续可增强为LLM翻译+模糊匹配）
    for fx in fixtures:
        en_home = fx.get("home_team", "").lower()
        en_away = fx.get("away_team", "").lower()
        cn_lower = cn_name.lower()
        
        # 直接尝试映射表
        en_mapped = TEAM_CN_EN_MAP.get(cn_name.strip(), "")
        if en_mapped:
            if en_mapped.lower() in en_home or en_home in en_mapped.lower():
                return fx
            if en_mapped.lower() in en_away or en_away in en_mapped.lower():
                return fx
        
        # 模糊包含匹配（中文名可能包含英文名的部分）
        # 例如："利物浦" 可能匹配 "Liverpool"
        # 这里用简单的字符级匹配作为fallback
    
    return None


def fetch_pinnacle_ah_ou_from_api(match_list, date_str):
    """从api-football获取Pinnacle亚盘和大小球数据
    
    流程:
    1. 用fixtures?date=获取当天所有比赛
    2. 通过队名模糊匹配找到对应fixture_id
    3. 逐场调用odds?fixture={id}&bookmaker=4&bet=4 获取Pinnacle AH
    4. 逐场调用odds?fixture={id}&bookmaker=4&bet=5 获取Pinnacle OU
    
    API日限100次，每天8-10场比赛需要约 1+8+8=17次请求
    
    Returns:
        dict: {match_key: {pin_ah: {handicap, home_odd, away_odd}, pin_ou: {line, over, under}}}
    """
    import time as _time
    
    result = {}
    
    # Step 1: 获取当天所有fixtures
    fixtures = fetch_api_football_fixtures(date_str)
    if not fixtures:
        print("[WARN] api-football: 无fixtures数据，跳过Pinnacle AH/OU")
        return result
    
    _time.sleep(7)  # 10次/分钟限制，间隔7秒
    
    # Step 2: 逐场匹配并获取Pinnacle AH/OU
    # 匹配策略：开赛时间精确匹配（同一分钟开赛的跨联赛比赛极少）
    # fallback: 队名相似度匹配
    api_call_count = 1  # 已用1次获取fixtures
    
    # 构建fixtures时间索引
    fx_by_time = {}
    for fx in fixtures:
        kickoff = fx.get("kickoff", "")
        if kickoff:
            hm = kickoff[11:16] if len(kickoff) >= 16 else ""
            if hm:
                fx_by_time.setdefault(hm, []).append(fx)
    
    for m in match_list:
        home_cn = m.get("home", "")
        away_cn = m.get("away", "")
        match_id = m.get("match_id", "")
        match_key = f"{home_cn}_{away_cn}"
        kickoff_raw = m.get("kickoff", "")  # 格式: "2026-06-18 17:59" 或 "06-19 00:00"
        
        # 从kickoff提取HH:MM
        match_hm = ""
        if kickoff_raw:
            # 尝试不同格式
            for fmt in ["%Y-%m-%d %H:%M", "%m-%d %H:%M"]:
                try:
                    from datetime import datetime as _dt
                    kt = _dt.strptime(kickoff_raw.strip(), fmt)
                    match_hm = kt.strftime("%H:%M")
                    break
                except ValueError:
                    continue
            # 简单提取
            if not match_hm and len(kickoff_raw) >= 5:
                match_hm = kickoff_raw[-5:]
        
        matched_fx = None
        match_method = ""
        
        # 策略1: 按开赛时间精确匹配
        if match_hm and match_hm in fx_by_time:
            candidates = fx_by_time[match_hm]
            if len(candidates) == 1:
                matched_fx = candidates[0]
                match_method = f"time={match_hm}"
            else:
                # 多场同时间，用队名辅助区分
                best_sim = 0
                for fx in candidates:
                    sim_h = team_name_similarity(home_cn, fx.get("home_team", ""))
                    sim_a = team_name_similarity(away_cn, fx.get("away_team", ""))
                    avg = (sim_h + sim_a) / 2
                    if avg > best_sim:
                        best_sim = avg
                        matched_fx = fx
                if matched_fx:
                    match_method = f"time={match_hm}+name(sim={best_sim:.2f})"
        
        # 策略2: 时间匹配失败，±30分钟内搜索
        if not matched_fx and match_hm:
            try:
                from datetime import datetime as _dt, timedelta as _td
                base = _dt.strptime(match_hm, "%H:%M")
                for delta_min in [1, -1, 2, -2, 5, -5, 15, -15, 30, -30]:
                    check_time = (base + _td(minutes=delta_min)).strftime("%H:%M")
                    if check_time in fx_by_time:
                        candidates = fx_by_time[check_time]
                        if len(candidates) == 1:
                            matched_fx = candidates[0]
                            match_method = f"time≈{check_time}(±{abs(delta_min)}min)"
                            break
                        else:
                            best_sim = 0
                            for fx in candidates:
                                sim_h = team_name_similarity(home_cn, fx.get("home_team", ""))
                                sim_a = team_name_similarity(away_cn, fx.get("away_team", ""))
                                avg = (sim_h + sim_a) / 2
                                if avg > best_sim:
                                    best_sim = avg
                                    matched_fx = fx
                            if matched_fx and best_sim > 0.15:
                                match_method = f"time≈{check_time}+name(sim={best_sim:.2f})"
                                break
            except Exception:
                pass
        
        # 策略3: fallback纯队名匹配
        if not matched_fx:
            best_score = 0
            for fx in fixtures:
                sim_home = team_name_similarity(home_cn, fx.get("home_team", ""))
                sim_away = team_name_similarity(away_cn, fx.get("away_team", ""))
                avg_sim = (sim_home + sim_away) / 2
                if avg_sim > best_score:
                    best_score = avg_sim
                    matched_fx = fx
            if matched_fx and best_score >= 0.3:
                match_method = f"name(sim={best_score:.2f})"
            else:
                matched_fx = None
        
        if not matched_fx:
            print(f"  [API] {home_cn} vs {away_cn} 无匹配 (kickoff={match_hm})")
            continue
        
        fixture_id = matched_fx.get("fixture_id")
        print(f"  [API] {home_cn} vs {away_cn} -> fixture={fixture_id} ({matched_fx['home_team']} vs {matched_fx['away_team']}) [{match_method}]")
        
        # Step 3: 获取Pinnacle AH
        ah_raw = fetch_api_football_pinnacle(fixture_id, bet_type=4)
        api_call_count += 1
        _time.sleep(7)  # 频率限制
        
        pin_ah = extract_pinnacle_ah_main(ah_raw)
        if pin_ah:
            print(f"    Pinnacle AH: 让球={pin_ah['handicap']} 主水={pin_ah['home_odd']:.2f} 客水={pin_ah['away_odd']:.2f}")
        else:
            print(f"    Pinnacle AH: 无数据")
        
        # Step 4: 获取Pinnacle OU
        ou_raw = fetch_api_football_pinnacle(fixture_id, bet_type=5)
        api_call_count += 1
        _time.sleep(7)  # 频率限制
        
        pin_ou = extract_pinnacle_ou_main(ou_raw)
        if pin_ou:
            print(f"    Pinnacle OU: 盘口={pin_ou['line']} 大球={pin_ou['over']:.2f} 小球={pin_ou['under']:.2f}")
        else:
            print(f"    Pinnacle OU: 无数据")
        
        result[match_id] = {
            "pin_ah": pin_ah,
            "pin_ou": pin_ou,
        }
    
    print(f"[INFO] api-football Pinnacle AH/OU: {len(result)}/{len(match_list)} 场匹配 (API调用{api_call_count}次)")
    return result


def fetch_pinnacle_odds(date_str=None, include_beidan=False):
    """主函数：获取指定日期的赔率数据
    
    【重要】北单(include_beidan)默认关闭：
    - 中国足彩网北单(type=bd)页面返回让球盘(HHAD)赔率，非标准盘(HAD)
    - 即使d<2.0检测能过滤部分，仍不可信，故直接禁用北单赔率抓取
    - 北单场次赔率降级为百家平均或竞彩HAD
    
    数据源优先级：
    1. SB公司赔率（POST company=3，抽水5-8%）→ 作为pinnacle字段（主市场参照）
    2. 百家平均赔率（GET默认页面）→ 作为avg_odds字段
    3. 详情页平博数据（如果可访问）→ 覆盖pinnacle字段
    
    Args:
        date_str: 日期字符串，格式 YYYY-MM-DD，默认今天
        include_beidan: 是否同时抓取北单(type=bd)的百家指数
    
    Returns:
        list[dict]: 每场比赛的赔率数据
    """
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')
    
    print(f"[INFO] 抓取赔率数据: {date_str}")
    
    # 同时抓取前一天的数据（覆盖次日00:00-11:59的凌晨比赛）
    prev_day = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    
    # Step 0: 检测zgzcw.com是否可用（WAF检测）
    zgzcw_available = False
    try:
        import requests as req
        test_resp = req.get(BASE_URL_LIST, headers=HEADERS, timeout=10)
        if test_resp.status_code == 200 and 'CloudWAF' not in test_resp.text and '华为云' not in test_resp.text:
            zgzcw_available = True
        else:
            print(f"[WARN] zgzcw.com WAF拦截(status={test_resp.status_code})，启用oddsmagnet fallback")
    except Exception as e:
        print(f"[WARN] zgzcw.com不可达: {e}，启用oddsmagnet fallback")
    
    if not zgzcw_available:
        # WAF拦截 → 直接从oddsmagnet缓存读取赔率
        results = load_oddsmagnet_fallback(date_str)
        if results:
            # 保存缓存文件（格式与正常流程一致）
            cache_dir = os.path.join(DATA_BASE_DIR, "data", "cache")
            os.makedirs(cache_dir, exist_ok=True)
            now_str = datetime.now().strftime('%H%M')
            cache_path = os.path.join(cache_dir, f"pinnacle_odds_{date_str.replace('-','')}_{now_str}.json")
            cache_data = {
                'date': date_str,
                'fetch_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'source': 'oddsmagnet_fallback',
                'summary': {
                    'total': len(results),
                    'pinnacle': sum(1 for r in results if r.get('odds_source') == 'Pinnacle'),
                    'avg': sum(1 for r in results if r.get('odds_source') == '百家平均'),
                },
                'matches': {f"{r.get('home','')}_{r.get('away','')}": r for r in results}
            }
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2, default=str)
            print(f"[INFO] 缓存已保存: {cache_path}")
        if not results:
            # oddsmagnet fallback也失败 → 从odds_api.py输出加载match_list
            print("[INFO] oddsmagnet fallback失败，从odds_api.py缓存加载match_list")
            match_list = []
            for om_date in [prev_day, date_str]:
                om_file = os.path.join(DATA_BASE_DIR, "data", "raw", "oddsmagnet", f"{om_date.replace('-', '')}.json")
                if os.path.exists(om_file):
                    try:
                        with open(om_file, 'r', encoding='utf-8') as f:
                            om_data = json.load(f)
                        om_matches = om_data.get('matches', {})
                        if isinstance(om_matches, dict):
                            for key, m in om_matches.items():
                                info = m.get('info', {})
                                odds_data = m.get('odds', {})
                                avg_o = odds_data.get('avg', {})
                                pin_o = odds_data.get('pinnacle', {})
                                match_list.append({
                                    'match_id': f"{info.get('home', '')}_{info.get('away', '')}",
                                    'number': info.get('number', ''),
                                    'league': info.get('league', ''),
                                    'kickoff': info.get('kickoff', ''),
                                    'home': info.get('home', ''),
                                    'away': info.get('away', ''),
                                    'date': om_data.get('date', om_date),
                                    'odds_source': '百家平均',
                                    'avg_open': {'w': avg_o.get('odds_w', 0), 'd': avg_o.get('odds_d', 0), 'l': avg_o.get('odds_l', 0)},
                                    'avg_close': {'w': avg_o.get('odds_w', 0), 'd': avg_o.get('odds_d', 0), 'l': avg_o.get('odds_l', 0)},
                                    'avg_movement': {'w': 'stable', 'd': 'stable', 'l': 'stable'},
                                    'pinnacle_open': {'w': pin_o.get('odds_w', 0), 'd': pin_o.get('odds_d', 0), 'l': pin_o.get('odds_l', 0)},
                                    'pinnacle_close': {'w': pin_o.get('odds_w', 0), 'd': pin_o.get('odds_d', 0), 'l': pin_o.get('odds_l', 0)},
                                    'pinnacle_movement': {'w': 'stable', 'd': 'stable', 'l': 'stable'},
                                    'pinnacle_margin': pin_o.get('margin', 0),
                                })
                            print(f"[INFO] 从{os.path.basename(om_file)}加载{len(om_matches)}场")
                    except Exception as e:
                        print(f"[WARN] 加载{os.path.basename(om_file)}失败: {e}")
            
            if not match_list:
                print("[WARN] 无法获取任何赔率数据")
                return []
            
            print(f"[INFO] 从odds_api.py缓存加载match_list: {len(match_list)}场")
            # WAF拦截，跳过zgzcw.com POST请求，直接设置空结果
            pin_all = {}
            bf_all = {}
            sb_all = {}
            hkjc_all = {}
            ah_avg_all = {}
            ah_hkjc_all = {}
            om_fallback = {}
        else:
            return results
    else:
            # Step 1: 百家平均赔率（GET方式）- 当天+前一天
        match_list = fetch_match_list(date_str)
        match_list_prev = fetch_match_list(prev_day)
        print(f"[INFO] 竞彩百家平均: 当天{len(match_list)}场, 前天{len(match_list_prev)}场")
        match_list = match_list_prev + match_list  # 前一天放前面
    
        if include_beidan:
            match_list_bd = fetch_match_list(date_str, page_type='bd')
            match_list_bd_prev = fetch_match_list(prev_day, page_type='bd')
            print(f"[INFO] 北单百家平均: 当天{len(match_list_bd)}场, 前天{len(match_list_bd_prev)}场")
            match_list_bd = match_list_bd_prev + match_list_bd
            # 去重合并：按match_id去重
            existing_ids = {m.get('match_id','') for m in match_list}
            for m in match_list_bd:
                mid = m.get('match_id','')
                if mid and mid not in existing_ids:
                    match_list.append(m)
                    existing_ids.add(mid)
            print(f"[INFO] 去重合并后总计 {len(match_list)} 场比赛")
    
        # Step 2: 平博赔率（POST方式，aid=106=真Pinnacle）- 当天+前一天（最高优先级）
        pin_jc = fetch_company_odds(date_str, page_type='jc', company='106')
        time.sleep(3)
        pin_jc_prev = fetch_company_odds(prev_day, page_type='jc', company='106')
        time.sleep(3)
        pin_bd = fetch_company_odds(date_str, page_type='bd', company='106') if include_beidan else {}
        time.sleep(3)
        pin_bd_prev = fetch_company_odds(prev_day, page_type='bd', company='106') if include_beidan else {}
        pin_all = {**pin_jc_prev, **pin_bd_prev, **pin_jc, **pin_bd}  # match_id -> pin_odds
        print(f"[INFO] Pinnacle赔率: {len(pin_all)} 场 (前天jc{len(pin_jc_prev)}/bd{len(pin_bd_prev)}, 当天jc{len(pin_jc)}/bd{len(pin_bd)})")
    
        # Step 3: 必发赔率（POST方式，aid=56=Betfair）- 当天+前一天（辅助参考）
        bf_jc = fetch_company_odds(date_str, page_type='jc', company='56')
        time.sleep(3)
        bf_jc_prev = fetch_company_odds(prev_day, page_type='jc', company='56')
        time.sleep(3)
        bf_bd = fetch_company_odds(date_str, page_type='bd', company='56') if include_beidan else {}
        time.sleep(3)
        bf_bd_prev = fetch_company_odds(prev_day, page_type='bd', company='56') if include_beidan else {}
        bf_all = {**bf_jc_prev, **bf_bd_prev, **bf_jc, **bf_bd}
        print(f"[INFO] Betfair赔率: {len(bf_all)} 场")
    
        # Step 4: SB公司赔率（POST方式，aid=3）- 已取消抓取
        # sb_jc = fetch_sb_odds(date_str, page_type='jc')
        # time.sleep(3)
        # sb_jc_prev = fetch_sb_odds(prev_day, page_type='jc')
        # time.sleep(3)
        # sb_bd = fetch_sb_odds(date_str, page_type='bd') if include_beidan else {}
        # time.sleep(3)
        # sb_bd_prev = fetch_sb_odds(prev_day, page_type='bd') if include_beidan else {}
        # sb_all = {**sb_jc_prev, **sb_bd_prev, **sb_jc, **sb_bd}
        # print(f"[INFO] SB公司赔率: {len(sb_all)} 场")
        sb_all = {}
        print(f"[INFO] SB公司赔率: 已取消抓取")

        # Step 5: 香港马会赔率（POST方式，aid=136）- 已取消抓取
        # hkjc_jc = fetch_company_odds(date_str, page_type='jc', company='136')
        # time.sleep(3)
        # hkjc_jc_prev = fetch_company_odds(prev_day, page_type='jc', company='136')
        # time.sleep(3)
        # hkjc_bd = fetch_company_odds(date_str, page_type='bd', company='136') if include_beidan else {}
        # time.sleep(3)
        # hkjc_bd_prev = fetch_company_odds(prev_day, page_type='bd', company='136') if include_beidan else {}
        # hkjc_all = {**hkjc_jc_prev, **hkjc_bd_prev, **hkjc_jc, **hkjc_bd}
        # print(f"[INFO] 香港马会赔率: {len(hkjc_all)} 场")
        hkjc_all = {}
        print(f"[INFO] 香港马会赔率: 已取消抓取")
    
        # Step 5.8: 亚盘让球盘（POST companyType=y）— 仅百家平均，HKJC已取消
        ah_avg_jc = fetch_asian_handicap(date_str, page_type='jc', company='0')
        time.sleep(3)
        ah_avg_jc_prev = fetch_asian_handicap(prev_day, page_type='jc', company='0')
        time.sleep(3)
        # ah_hkjc_jc = fetch_asian_handicap(date_str, page_type='jc', company='136')  # 已取消
        # time.sleep(3)
        # ah_hkjc_jc_prev = fetch_asian_handicap(prev_day, page_type='jc', company='136')  # 已取消
        # time.sleep(3)
        ah_avg_all = {**ah_avg_jc_prev, **ah_avg_jc}
        ah_hkjc_all = {}  # HKJC亚盘已取消
        print(f"[INFO] 亚盘百家平均: {len(ah_avg_all)} 场, 亚盘HKJC: 已取消")
    
    # Step 5.5: 加载oddsmagnet缓存补充POST失败的赔源
    om_fallback = {}
    missing_sources = []
    if len(pin_all) == 0: missing_sources.append('Pinnacle')
    if len(bf_all) == 0: missing_sources.append('Betfair')
    if len(sb_all) == 0: missing_sources.append('SB')
    if len(hkjc_all) == 0: missing_sources.append('HKJC')
    
    if missing_sources:
        print(f"[INFO] POST失败赔源，尝试oddsmagnet补充: {', '.join(missing_sources)}")
        om_cache_path = os.path.join(DATA_BASE_DIR, "data", "cache", "real_odds.json")
        if os.path.exists(om_cache_path):
            try:
                with open(om_cache_path, 'r', encoding='utf-8') as f:
                    om_data = json.load(f)
                if isinstance(om_data, dict):
                    for key, item in om_data.items():
                        match_date = item.get('matchDate', '')
                        if date_str and match_date != date_str and match_date != prev_day:
                            continue
                        parts = key.split(' vs ')
                        if len(parts) != 2: continue
                        om_fallback[f"{parts[0].strip()}_{parts[1].strip()}"] = item
                    print(f"[INFO] oddsmagnet fallback: {len(om_fallback)} 场")
            except Exception as e:
                print(f"[WARN] oddsmagnet fallback加载失败: {e}")

    # Step 5.9: 从api-football获取Pinnacle亚盘和大小球
    pin_ah_ou_data = {}
    try:
        pin_ah_ou_data = fetch_pinnacle_ah_ou_from_api(match_list, date_str)
    except Exception as e:
        print(f"[WARN] api-football Pinnacle AH/OU 获取失败: {e}")

    # Step 5.95: 大小球数据 — 足彩网companyType=d返回亚盘数据，已禁用
    # 大小球数据仅从api-football Pinnacle OU获取（Step 5.9的pin_ah_ou_data）
    ou_data_all = {}  # {match_key: {ou, ou_liji, ou_ms}} — 暂为空

    # Step 6: 合并数据，赔率优先级：平博 > 百家平均 (SB已取消)
    results = []
    for i, m in enumerate(match_list):
        match_id = m.get('match_id', '')
        home = m.get('home', '?')
        away = m.get('away', '?')
        print(f"[INFO] [{i+1}/{len(match_list)}] {home} vs {away} (id={match_id})")
        
        # 查找oddsmagnet fallback数据（按队名匹配）
        om_key = f"{home}_{away}"
        om_item = om_fallback.get(om_key, {})
        # 也尝试模糊匹配
        if not om_item:
            for fk, fv in om_fallback.items():
                if home in fk and away in fk:
                    om_item = fv
                    break
        
        # 百家平均赔率（列表页GET获取）
        avg_open = m.get('avg_open', {})
        avg_close = m.get('avg_close', {})
        avg_movement = m.get('avg_movement', {})
        
        # oddsmagnet补充百家平均（GET失败时）
        if avg_open.get('w', 0) == 0 and om_item:
            avg_w = om_item.get('home', 0) or 0
            avg_d = om_item.get('draw', 0) or 0
            avg_l = om_item.get('away', 0) or 0
            if avg_w > 0:
                avg_open = {'w': avg_w, 'd': avg_d, 'l': avg_l}
                avg_close = {'w': avg_w, 'd': avg_d, 'l': avg_l}
                avg_movement = {'w': 'stable', 'd': 'stable', 'l': 'stable'}
        
        if avg_open.get('w', 0) > 0:
            print(f"  百家初盘: {avg_open['w']:.2f}/{avg_open['d']:.2f}/{avg_open['l']:.2f}")
            print(f"  百家最新: {avg_close['w']:.2f}/{avg_close['d']:.2f}/{avg_close['l']:.2f}")
        
        # 平博赔率（POST获取，最高优先级）
        pin_data = pin_all.get(match_id, {})
        pinnacle_open = pin_data.get('open', {})
        pinnacle_close = pin_data.get('close', {})
        pinnacle_movement = pin_data.get('movement', {})
        
        # HHAD让球盘检测
        if pinnacle_close.get('d', 0) > 0 and pinnacle_close.get('d', 0) < 2.0:
            print(f"  [WARN] Pinnacle疑似让球盘(d={pinnacle_close['d']:.2f}<2.0)，已丢弃")
            pinnacle_open = {}
            pinnacle_close = {}
            pinnacle_movement = {}
        if pinnacle_open.get('d', 0) > 0 and pinnacle_open.get('d', 0) < 2.0:
            pinnacle_open = {}
        
        # oddsmagnet补充Pinnacle（POST失败时）
        if pinnacle_open.get('w', 0) == 0 and om_item:
            pin_ow = om_item.get('pinnacle_open_w', 0) or 0
            pin_od = om_item.get('pinnacle_open_d', 0) or 0
            pin_ol = om_item.get('pinnacle_open_l', 0) or 0
            if pin_ow > 0:
                pinnacle_open = {'w': pin_ow, 'd': pin_od, 'l': pin_ol}
                pinnacle_close = {'w': pin_ow, 'd': pin_od, 'l': pin_ol}
                pinnacle_movement = {'w': 'stable', 'd': 'stable', 'l': 'stable'}
                print(f"  Pinnacle(oddsmagnet): {pin_ow:.2f}/{pin_od:.2f}/{pin_ol:.2f}")
        
        if pinnacle_open.get('w', 0) > 0 and not om_item.get('pinnacle_open_w'):
            print(f"  Pinnacle初盘: {pinnacle_open['w']:.2f}/{pinnacle_open['d']:.2f}/{pinnacle_open['l']:.2f}")
            print(f"  Pinnacle最新: {pinnacle_close['w']:.2f}/{pinnacle_close['d']:.2f}/{pinnacle_close['l']:.2f}")
        
        # 必发赔率（POST获取，辅助参考）
        bf_data = bf_all.get(match_id, {})
        betfair_open = bf_data.get('open', {})
        betfair_close = bf_data.get('close', {})
        
        # HHAD让球盘检测
        if betfair_close.get('d', 0) > 0 and betfair_close.get('d', 0) < 2.0:
            print(f"  [WARN] Betfair疑似让球盘(d={betfair_close['d']:.2f}<2.0)，已丢弃")
            betfair_open = {}
            betfair_close = {}
        
        # oddsmagnet补充Betfair
        if betfair_open.get('w', 0) == 0 and om_item:
            bf_ow = om_item.get('betfair_open_w', 0) or 0
            bf_od = om_item.get('betfair_open_d', 0) or 0
            bf_ol = om_item.get('betfair_open_l', 0) or 0
            if bf_ow > 0:
                betfair_open = {'w': bf_ow, 'd': bf_od, 'l': bf_ol}
                betfair_close = {'w': bf_ow, 'd': bf_od, 'l': bf_ol}
                print(f"  Betfair(oddsmagnet): {bf_ow:.2f}/{bf_od:.2f}/{bf_ol:.2f}")
        
        if betfair_open.get('w', 0) > 0:
            print(f"  Betfair初盘: {betfair_open['w']:.2f}/{betfair_open['d']:.2f}/{betfair_open['l']:.2f}")
            print(f"  Betfair最新: {betfair_close['w']:.2f}/{betfair_close['d']:.2f}/{betfair_close['l']:.2f}")
        
        # SB公司赔率（POST获取，次优先级）
        sb_data = sb_all.get(match_id, {})
        sb_open = sb_data.get('open', {})
        sb_close = sb_data.get('close', {})
        sb_movement = sb_data.get('movement', {})
        
        # HHAD让球盘检测
        if sb_close.get('d', 0) > 0 and sb_close.get('d', 0) < 2.0:
            print(f"  [WARN] SB疑似让球盘(d={sb_close['d']:.2f}<2.0)，已丢弃")
            sb_open = {}
            sb_close = {}
            sb_movement = {}
        
        if sb_open.get('w', 0) > 0:
            print(f"  SB初盘: {sb_open['w']:.2f}/{sb_open['d']:.2f}/{sb_open['l']:.2f}")
            print(f"  SB最新: {sb_close['w']:.2f}/{sb_close['d']:.2f}/{sb_close['l']:.2f}")
        
        # 确定主市场参照（pinnacle字段）：优先 平博 > 百家平均 (SB已取消)
        if pinnacle_open.get('w', 0) > 0:
            m['pinnacle_open'] = pinnacle_open
            m['pinnacle_close'] = pinnacle_close
            m['pinnacle_movement'] = pinnacle_movement
            odds_source = 'Pinnacle'
        elif avg_open.get('w', 0) > 0:
            m['pinnacle_open'] = avg_open
            m['pinnacle_close'] = avg_close
            m['pinnacle_movement'] = avg_movement
            odds_source = '百家平均'
        else:
            odds_source = '无'
        
        # 百家平均赔率单独保存到avg_odds字段
        m['avg_odds_open'] = avg_open
        m['avg_odds_close'] = avg_close
        
        # 必发赔率单独保存到betfair字段
        m['betfair_open'] = betfair_open
        m['betfair_close'] = betfair_close
        
        # 香港马会赔率（交叉验证参考）
        hkjc_data = hkjc_all.get(match_id, {})
        hkjc_open = hkjc_data.get('open', {})
        hkjc_close = hkjc_data.get('close', {})
        
        # HHAD让球盘检测
        if hkjc_close.get('d', 0) > 0 and hkjc_close.get('d', 0) < 2.0:
            print(f"  [WARN] HKJC疑似让球盘(d={hkjc_close['d']:.2f}<2.0)，已丢弃")
            hkjc_open = {}
            hkjc_close = {}
        
        # oddsmagnet补充HKJC — 已取消，不再补充
        # if hkjc_open.get('w', 0) == 0 and om_item:
        #     hkjc_ow = om_item.get('hkjc_open_w', 0) or 0
        #     hkjc_od = om_item.get('hkjc_open_d', 0) or 0
        #     hkjc_ol = om_item.get('hkjc_open_l', 0) or 0
        #     if hkjc_ow > 0:
        #         hkjc_open = {'w': hkjc_ow, 'd': hkjc_od, 'l': hkjc_ol}
        #         hkjc_close = {'w': hkjc_ow, 'd': hkjc_od, 'l': hkjc_ol}
        #         print(f"  HKJC(oddsmagnet): {hkjc_ow:.2f}/{hkjc_od:.2f}/{hkjc_ol:.2f}")
        
        m['hkjc_open'] = hkjc_open
        m['hkjc_close'] = hkjc_close
        
        if hkjc_open.get('w', 0) > 0:
            print(f"  HKJC初盘: {hkjc_open['w']:.2f}/{hkjc_open['d']:.2f}/{hkjc_open['l']:.2f}")
            print(f"  HKJC最新: {hkjc_close['w']:.2f}/{hkjc_close['d']:.2f}/{hkjc_close['l']:.2f}")
        
        # 亚盘让球盘
        ah_avg_data = ah_avg_all.get(match_id, {})
        ah_hkjc_data = ah_hkjc_all.get(match_id, {})
        m['ah_avg_open'] = ah_avg_data.get('open', {})
        m['ah_avg_close'] = ah_avg_data.get('close', {})
        m['ah_hkjc_open'] = ah_hkjc_data.get('open', {})
        m['ah_hkjc_close'] = ah_hkjc_data.get('close', {})
        
        if ah_avg_data.get('close', {}).get('handicap', 0) != 0:
            ah_c = ah_avg_data['close']
            print(f"  亚盘(百家): 盘口{ah_c['handicap']} 主水{ah_c['home_w']:.2f} 客水{ah_c['away_w']:.2f}")
        elif ah_hkjc_data.get('close', {}).get('handicap', 0) != 0:
            ah_c = ah_hkjc_data['close']
            print(f"  亚盘(HKJC): 盘口{ah_c['handicap']} 主水{ah_c['home_w']:.2f} 客水{ah_c['away_w']:.2f}")

        # Pinnacle亚盘和大小球 (from api-football)
        pin_ah_ou = pin_ah_ou_data.get(match_id, {})
        m['pin_ah'] = pin_ah_ou.get('pin_ah', {})
        m['pin_ou'] = pin_ah_ou.get('pin_ou', {})

        # 大小球数据 (from odds_api.py ah_YYYYMMDD.json)
        ou_key = f"{home}_{away}"
        ou_item = ou_data_all.get(ou_key, {})
        if not ou_item:
            # 模糊匹配
            for fk, fv in ou_data_all.items():
                if home in fk and away in fk:
                    ou_item = fv
                    break
        m['ou'] = ou_item.get('ou', {})
        m['ou_liji'] = ou_item.get('ou_liji', {})
        m['ou_ms'] = ou_item.get('ou_ms', {})

        # 计算去抽水概率（基于主市场参照）
        pin_open = m.get('pinnacle_open', {})
        if pin_open.get('w', 0) > 0:
            implied, margin = calc_implied_prob(
                pin_open['w'], pin_open['d'], pin_open['l']
            )
            m['implied_prob'] = implied
            m['pinnacle_margin'] = margin
            print(f"  市场参照: {odds_source} | 抽水: {margin:.2%} | 隐含概率: {implied['w']:.1%}/{implied['d']:.1%}/{implied['l']:.1%}")
        else:
            print(f"  市场参照: {odds_source}")
        
        m['odds_source'] = odds_source
        m['date'] = date_str
        results.append(m)
    
    print(f"\n[INFO] 完成，共 {len(results)} 场")
    pin_count = sum(1 for r in results if r.get('odds_source') == 'Pinnacle')
    sb_count = sum(1 for r in results if r.get('odds_source') == 'SB')
    avg_count = sum(1 for r in results if r.get('odds_source') == '百家平均')
    none_count = sum(1 for r in results if r.get('odds_source') == '无')
    
    # Step 7: 补充oddsmagnet中不在match_list里的场次
    if om_fallback:
        existing_teams = set()
        for r in results:
            h = r.get('home', '')
            a = r.get('away', '')
            existing_teams.add(f"{h}_{a}")
        
        om_added = 0
        for om_key, om_item in om_fallback.items():
            if om_key in existing_teams:
                continue
            parts = om_key.split('_')
            if len(parts) != 2:
                continue
            home, away = parts[0], parts[1]
            
            pin_ow = om_item.get('pinnacle_open_w', 0) or 0
            pin_od = om_item.get('pinnacle_open_d', 0) or 0
            pin_ol = om_item.get('pinnacle_open_l', 0) or 0
            avg_w = om_item.get('home', 0) or 0
            avg_d = om_item.get('draw', 0) or 0
            avg_l = om_item.get('away', 0) or 0
            hkjc_ow = om_item.get('hkjc_open_w', 0) or 0
            hkjc_od = om_item.get('hkjc_open_d', 0) or 0
            hkjc_ol = om_item.get('hkjc_open_l', 0) or 0
            
            if pin_ow == 0 and avg_w == 0:
                continue
            
            m = {
                'home': home, 'away': away,
                'match_id': om_item.get('matchNum', ''),
                'number': om_item.get('matchNum', ''),
                'league': om_item.get('league', ''),
                'kickoff': om_item.get('kickoff', ''),
                'date': om_item.get('matchDate', ''),
                'odds_source': 'oddsmagnet',
                'avg_open': {'w': avg_w, 'd': avg_d, 'l': avg_l},
                'avg_close': {'w': avg_w, 'd': avg_d, 'l': avg_l},
                'avg_movement': {'w': 'stable', 'd': 'stable', 'l': 'stable'},
                'pinnacle_open': {'w': pin_ow, 'd': pin_od, 'l': pin_ol},
                'pinnacle_close': {'w': pin_ow, 'd': pin_od, 'l': pin_ol},
                'pinnacle_movement': {'w': 'stable', 'd': 'stable', 'l': 'stable'},
                'hkjc_open': {'w': hkjc_ow, 'd': hkjc_od, 'l': hkjc_ol},
                'hkjc_close': {'w': hkjc_ow, 'd': hkjc_od, 'l': hkjc_ol},
                'betfair_open': {}, 'betfair_close': {},
                'avg_odds_open': {'w': avg_w, 'd': avg_d, 'l': avg_l},
                'avg_odds_close': {'w': avg_w, 'd': avg_d, 'l': avg_l},
            }
            
            if pin_ow > 0:
                implied, margin = calc_implied_prob(pin_ow, pin_od, pin_ol)
                m['implied_prob'] = implied
                m['pinnacle_margin'] = margin
                m['odds_source'] = 'Pinnacle'
            elif avg_w > 0:
                implied, margin = calc_implied_prob(avg_w, avg_d, avg_l)
                m['implied_prob'] = implied
                m['pinnacle_margin'] = margin
                m['odds_source'] = '百家平均'
            
            results.append(m)
            om_added += 1
        
        if om_added > 0:
            print(f"[INFO] oddsmagnet补充{om_added}场缺失场次")
            # 重新计数
            pin_count = sum(1 for r in results if r.get('odds_source') == 'Pinnacle')
            sb_count = sum(1 for r in results if r.get('odds_source') == 'SB')
            avg_count = sum(1 for r in results if r.get('odds_source') == '百家平均')
            none_count = sum(1 for r in results if r.get('odds_source') == '无')
    
    print(f"[INFO] 平博: {pin_count}场, SB: {sb_count}场, 百家平均: {avg_count}场, 无赔率: {none_count}场")
    
    # 保存缓存文件
    cache_dir = os.path.join(DATA_BASE_DIR, "data", "cache")
    os.makedirs(cache_dir, exist_ok=True)
    now_str = datetime.now().strftime('%H%M')
    cache_path = os.path.join(cache_dir, f"pinnacle_odds_{date_str.replace('-','')}_{now_str}.json")
    cache_data = {
        'date': date_str,
        'fetch_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'summary': {
            'total': len(results),
            'pinnacle': pin_count,
            'sb': sb_count,
            'avg': avg_count,
            'none': none_count,
        },
        'matches': results
    }
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2, default=str)
    print(f"[INFO] 缓存已保存: {cache_path}")
    
    return results


def load_oddsmagnet_fallback(date_str=None):
    """当zgzcw.com被WAF拦截时，从oddsmagnet缓存(real_odds.json)提取赔率数据
    
    Returns:
        list: 与fetch_odds_data格式兼容的match列表
    """
    cache_path = os.path.join(DATA_BASE_DIR, "data", "cache", "real_odds.json")
    if not os.path.exists(cache_path):
        print("[WARN] oddsmagnet缓存不存在，无法fallback")
        return []
    
    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            odds_data = json.load(f)
    except Exception as e:
        print(f"[WARN] 读取oddsmagnet缓存失败: {e}")
        return []
    
    if not isinstance(odds_data, dict):
        print("[WARN] oddsmagnet缓存格式异常")
        return []
    
    results = []
    for key, item in odds_data.items():
        # 过滤日期
        match_date = item.get('matchDate', '')
        if date_str and match_date != date_str:
            # 也包含前一天（覆盖凌晨比赛）
            try:
                from datetime import datetime, timedelta
                target = datetime.strptime(date_str, '%Y-%m-%d')
                prev_day = (target - timedelta(days=1)).strftime('%Y-%m-%d')
                if match_date != prev_day:
                    continue
            except:
                continue
        
        # 从key解析队名 "主队 vs 客队"
        parts = key.split(' vs ')
        if len(parts) != 2:
            continue
        home = parts[0].strip()
        away = parts[1].strip()
        
        # 提取Pinnacle赔率
        pin_ow = item.get('pinnacle_open_w', 0) or 0
        pin_od = item.get('pinnacle_open_d', 0) or 0
        pin_ol = item.get('pinnacle_open_l', 0) or 0
        
        # 提取百家平均赔率
        avg_w = item.get('home', 0) or 0
        avg_d = item.get('draw', 0) or 0
        avg_l = item.get('away', 0) or 0
        
        # 提取HKJC赔率
        hkjc_ow = item.get('hkjc_open_w', 0) or 0
        hkjc_od = item.get('hkjc_open_d', 0) or 0
        hkjc_ol = item.get('hkjc_open_l', 0) or 0
        
        match = {
            'home': home,
            'away': away,
            'match_id': item.get('matchNum', ''),
            'number': item.get('matchNum', ''),
            'league': item.get('league', ''),
            'kickoff': item.get('kickoff', ''),
            'date': match_date,
            'odds_source': 'oddsmagnet_fallback',
            # 百家平均
            'avg_open': {'w': avg_w, 'd': avg_d, 'l': avg_l},
            'avg_close': {'w': avg_w, 'd': avg_d, 'l': avg_l},
            'avg_movement': {'w': 'stable', 'd': 'stable', 'l': 'stable'},
            # Pinnacle
            'pinnacle_open': {'w': pin_ow, 'd': pin_od, 'l': pin_ol},
            'pinnacle_close': {'w': pin_ow, 'd': pin_od, 'l': pin_ol},
            'pinnacle_movement': {'w': 'stable', 'd': 'stable', 'l': 'stable'},
            'pinnacle_margin': item.get('avg_margin', 0),
            # HKJC
            'hkjc_open': {'w': hkjc_ow, 'd': hkjc_od, 'l': hkjc_ol},
            'hkjc_close': {'w': hkjc_ow, 'd': hkjc_od, 'l': hkjc_ol},
        }
        
        # 确定主市场参照
        if pin_ow > 0:
            implied, margin = calc_implied_prob(pin_ow, pin_od, pin_ol)
            match['implied_prob'] = implied
            match['pinnacle_margin'] = margin
            match['odds_source'] = 'Pinnacle'
        elif avg_w > 0:
            implied, margin = calc_implied_prob(avg_w, avg_d, avg_l)
            match['implied_prob'] = implied
            match['pinnacle_margin'] = margin
            match['odds_source'] = '百家平均'
        else:
            match['implied_prob'] = {'w': 0, 'd': 0, 'l': 0}
        
        results.append(match)
    
    print(f"[INFO] oddsmagnet fallback: {len(results)} 场 (含Pinnacle: {sum(1 for r in results if r.get('odds_source')=='Pinnacle')})")
    return results


def save_to_db(matches, db_path, date_str=None):
    """将赔率数据写入数据库
    
    匹配策略（v2）：
    1. 按开赛时间分组，同时间段内做队名匹配（容忍不同中文译名）
    2. 队名匹配：正向+反向，阈值0.3（跨数据源中文译名差异大）
    3. 时间精确匹配优先，时间±30分钟兜底
    """
    if not matches:
        return 0
    
    # 确保目录存在
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else '.', exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    updated = 0
    
    # 确保亚盘字段存在
    for col, ctype in [('ah_handicap', 'REAL'), ('ah_home_water', 'REAL'), ('ah_away_water', 'REAL'), ('ah_source', 'TEXT')]:
        try:
            cursor.execute(f"ALTER TABLE poisson_predictions ADD COLUMN {col} {ctype}")
        except:
            pass  # 列已存在

    # 确保Pinnacle亚盘/大小球 + 百家平均/利记/明升大小球字段存在
    new_columns = [
        ('pin_ah_handicap', 'REAL'), ('pin_ah_home_water', 'REAL'), ('pin_ah_away_water', 'REAL'),
        ('pin_ou_line', 'REAL'), ('pin_ou_over', 'REAL'), ('pin_ou_under', 'REAL'),
        ('ou_over', 'REAL'), ('ou_line', 'REAL'), ('ou_under', 'REAL'),
        ('ou_open_over', 'REAL'), ('ou_open_line', 'REAL'), ('ou_open_under', 'REAL'),
        ('liji_ou_over', 'REAL'), ('liji_ou_line', 'REAL'), ('liji_ou_under', 'REAL'),
        ('liji_ou_open_over', 'REAL'), ('liji_ou_open_line', 'REAL'), ('liji_ou_open_under', 'REAL'),
        ('ms_ou_over', 'REAL'), ('ms_ou_line', 'REAL'), ('ms_ou_under', 'REAL'),
        ('ms_ou_open_over', 'REAL'), ('ms_ou_open_line', 'REAL'), ('ms_ou_open_under', 'REAL'),
    ]
    for col, ctype in new_columns:
        try:
            cursor.execute(f"ALTER TABLE poisson_predictions ADD COLUMN {col} {ctype}")
        except:
            pass  # 列已存在
    
    # 获取目标日期，使用时间窗口匹配（12:00~次日11:59）
    target_date = date_str or (matches[0].get('date') if matches else None)
    if not target_date:
        conn.close()
        return 0
    
    # 计算时间窗口：前一天12:00 ~ 次日11:59（覆盖日职联早场等跨日比赛）
    prev_day = (datetime.strptime(target_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    next_day = (datetime.strptime(target_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    window_start = f"{prev_day} 12:00"
    window_end = f"{next_day} 11:59"
    
    # 从数据库读取时间窗口内的所有记录
    cursor.execute("""
        SELECT id, home_team, away_team, kickoff_time 
        FROM poisson_predictions 
        WHERE kickoff_time >= ? AND kickoff_time <= ?
    """, (window_start, window_end))
    db_records = cursor.fetchall()
    
    if not db_records:
        print(f"[WARN] 数据库中没有 {target_date} 或 {next_day} 的记录")
        conn.close()
        return 0
    
    print(f"[INFO] 数据库中 {window_start}~{window_end} 共有 {len(db_records)} 条记录")
    
    # 已匹配的DB记录ID，防止重复匹配
    matched_db_ids = set()
    
    for m in matches:
        home = m.get('home', '')
        away = m.get('away', '')
        kickoff = m.get('kickoff', '')
        
        # 解析web页面的开赛时间 -> 标准datetime
        web_time = None
        if kickoff:
            try:
                # 格式: "05-21 01:00"
                web_time = datetime.strptime(f"{target_date[:4]}-{kickoff}", "%Y-%m-%d %H:%M")
            except:
                pass
        
        # 策略1: 精确时间匹配 + 弱队名验证
        best_match = None
        best_score = 0
        match_method = ''
        
        for record in db_records:
            record_id, db_home, db_away, db_time = record
            if record_id in matched_db_ids:
                continue
            
            # 计算队名相似度
            sim_home = team_name_similarity(home, db_home)
            sim_away = team_name_similarity(away, db_away)
            avg_sim = (sim_home + sim_away) / 2
            
            # 反向匹配
            sim_home_rev = team_name_similarity(home, db_away)
            sim_away_rev = team_name_similarity(away, db_home)
            avg_sim_rev = (sim_home_rev + sim_away_rev) / 2
            
            name_sim = max(avg_sim, avg_sim_rev)
            
            # 时间匹配度
            time_match = 0
            if web_time and db_time:
                try:
                    db_dt = datetime.strptime(db_time, "%Y-%m-%d %H:%M")
                    diff_minutes = abs((web_time - db_dt).total_seconds()) / 60
                    if diff_minutes <= 5:
                        time_match = 1.0  # 精确
                    elif diff_minutes <= 30:
                        time_match = 0.7  # 接近
                    elif diff_minutes <= 60:
                        time_match = 0.3  # 较远
                except:
                    pass
            
            # 综合评分：时间权重高，队名验证
            if time_match >= 1.0 and name_sim >= 0.3:
                score = 0.6 * time_match + 0.4 * name_sim
                if score > best_score:
                    best_score = score
                    best_match = record
                    match_method = f'时间精确+队名{name_sim:.2f}'
            elif time_match >= 0.7 and name_sim >= 0.2:
                score = 0.6 * time_match + 0.4 * name_sim
                if score > best_score:
                    best_score = score
                    best_match = record
                    match_method = f'时间近+队名{name_sim:.2f}'
            elif name_sim >= 0.6:
                # 队名强匹配（旧逻辑兜底）
                score = 0.3 * time_match + 0.7 * name_sim
                if score > best_score:
                    best_score = score
                    best_match = record
                    match_method = f'队名强{name_sim:.2f}'
        
        # 兜底：宽松队名匹配（sim>=0.4），无需时间匹配
        if not best_match or best_score < 0.3:
            for record in db_records:
                record_id, db_home, db_away, db_time = record
                if record_id in matched_db_ids:
                    continue
                sim_home = team_name_similarity(home, db_home)
                sim_away = team_name_similarity(away, db_away)
                avg_sim = (sim_home + sim_away) / 2
                # 反向
                sim_home_rev = team_name_similarity(home, db_away)
                sim_away_rev = team_name_similarity(away, db_home)
                avg_sim_rev = (sim_home_rev + sim_away_rev) / 2
                name_sim = max(avg_sim, avg_sim_rev)
                if name_sim >= 0.4 and name_sim > best_score:
                    best_score = name_sim
                    best_match = record
                    match_method = f'队名宽松{name_sim:.2f}'
        
        if not best_match or best_score < 0.3:
            print(f"[WARN] {home} vs {away} ({kickoff}) 无匹配，跳过")
            continue
        
        record_id = best_match[0]
        db_home = best_match[1]
        db_away = best_match[2]
        matched_db_ids.add(record_id)
        
        print(f"[MATCH] {home} vs {away} -> DB {db_home} vs {db_away} [{match_method}] 评分={best_score:.2f}")
        
        record_id = best_match[0]
        db_home = best_match[1]
        db_away = best_match[2]
        
        pin_open = m.get('pinnacle_open', {})
        pin_close = m.get('pinnacle_close', {})
        pin_movement = m.get('pinnacle_movement', {})
        implied = m.get('implied_prob', {})
        margin = m.get('pinnacle_margin', 0)
        
        # 百家平均欧赔
        avg_open = m.get('avg_odds_open', m.get('avg_open', {}))
        avg_close = m.get('avg_odds_close', m.get('avg_close', {}))
        
        # 香港马会欧赔 — 已取消抓取，值全为0（保留DB列兼容旧数据）
        # hkjc_open = m.get('hkjc_open', {})
        # hkjc_close = m.get('hkjc_close', {})
        hkjc_open = {}
        hkjc_close = {}

        # 亚盘让球盘（仅百家平均，HKJC已取消）
        ah_close = m.get('ah_avg_close') or {}
        ah_source = 'avg' if m.get('ah_avg_close') else ''

        # Pinnacle亚盘和大小球 (from api-football)
        pin_ah = m.get('pin_ah', {})
        pin_ou = m.get('pin_ou', {})

        # 大小球数据 (from odds_api.py ah file)
        ou_data_m = m.get('ou', {})
        ou_liji_m = m.get('ou_liji', {})
        ou_ms_m = m.get('ou_ms', {})

        # 提取大小球即时/初盘数据
        ou_close = ou_data_m.get('close', {})
        ou_open = ou_data_m.get('open', {})
        liji_ou_close = ou_liji_m.get('close', {})
        liji_ou_open = ou_liji_m.get('open', {})
        ms_ou_close = ou_ms_m.get('close', {})
        ms_ou_open = ou_ms_m.get('open', {})

        # 保护已有非零ah数据：只在DB原值为0/NULL时才写入新值（防止WAF拦截0值覆盖Termux完整数据）
        ah_hc_val = ah_close.get('handicap', 0) or 0
        ah_hw_val = ah_close.get('home_w', 0) or 0
        ah_aw_val = ah_close.get('away_w', 0) or 0
        pin_ah_hc_val = pin_ah.get('handicap', 0) or 0
        pin_ah_hw_val = pin_ah.get('home_odd', 0) or 0
        pin_ah_aw_val = pin_ah.get('away_odd', 0) or 0
        pin_ou_line_val = pin_ou.get('line', 0) or 0
        pin_ou_over_val = pin_ou.get('over', 0) or 0
        pin_ou_under_val = pin_ou.get('under', 0) or 0

        cursor.execute("""
            UPDATE poisson_predictions SET
                pinnacle_open_w = ?, pinnacle_open_d = ?, pinnacle_open_l = ?,
                pinnacle_close_w = ?, pinnacle_close_d = ?, pinnacle_close_l = ?,
                pinnacle_movement = ?, pinnacle_margin = ?,
                implied_prob_w = ?, implied_prob_d = ?, implied_prob_l = ?,
                avg_odds_open_w = ?, avg_odds_open_d = ?, avg_odds_open_l = ?,
                avg_odds_close_w = ?, avg_odds_close_d = ?, avg_odds_close_l = ?,
                hkjc_open_w = ?, hkjc_open_d = ?, hkjc_open_l = ?,
                hkjc_close_w = ?, hkjc_close_d = ?, hkjc_close_l = ?,
                odds_source = ?,
                ah_handicap = CASE WHEN ah_handicap IS NOT NULL AND ah_handicap != 0 THEN ah_handicap ELSE ? END,
                ah_home_water = CASE WHEN ah_handicap IS NOT NULL AND ah_handicap != 0 THEN ah_home_water ELSE ? END,
                ah_away_water = CASE WHEN ah_handicap IS NOT NULL AND ah_handicap != 0 THEN ah_away_water ELSE ? END,
                ah_source = CASE WHEN ah_handicap IS NOT NULL AND ah_handicap != 0 THEN ah_source ELSE ? END,
                pin_ah_handicap = CASE WHEN pin_ah_handicap IS NOT NULL AND pin_ah_handicap != 0 THEN pin_ah_handicap ELSE ? END,
                pin_ah_home_water = CASE WHEN pin_ah_handicap IS NOT NULL AND pin_ah_handicap != 0 THEN pin_ah_home_water ELSE ? END,
                pin_ah_away_water = CASE WHEN pin_ah_handicap IS NOT NULL AND pin_ah_handicap != 0 THEN pin_ah_away_water ELSE ? END,
                pin_ou_line = CASE WHEN pin_ou_line IS NOT NULL AND pin_ou_line != 0 THEN pin_ou_line ELSE ? END,
                pin_ou_over = CASE WHEN pin_ou_line IS NOT NULL AND pin_ou_line != 0 THEN pin_ou_over ELSE ? END,
                pin_ou_under = CASE WHEN pin_ou_line IS NOT NULL AND pin_ou_line != 0 THEN pin_ou_under ELSE ? END,
                ou_over = ?, ou_line = ?, ou_under = ?,
                ou_open_over = ?, ou_open_line = ?, ou_open_under = ?,
                liji_ou_over = ?, liji_ou_line = ?, liji_ou_under = ?,
                liji_ou_open_over = ?, liji_ou_open_line = ?, liji_ou_open_under = ?,
                ms_ou_over = ?, ms_ou_line = ?, ms_ou_under = ?,
                ms_ou_open_over = ?, ms_ou_open_line = ?, ms_ou_open_under = ?
            WHERE id = ?
        """, (
            pin_open.get('w', 0), pin_open.get('d', 0), pin_open.get('l', 0),
            pin_close.get('w', 0), pin_close.get('d', 0), pin_close.get('l', 0),
            json.dumps(pin_movement, ensure_ascii=False),
            margin,
            implied.get('w', 0), implied.get('d', 0), implied.get('l', 0),
            avg_open.get('w', 0), avg_open.get('d', 0), avg_open.get('l', 0),
            avg_close.get('w', 0), avg_close.get('d', 0), avg_close.get('l', 0),
            hkjc_open.get('w', 0), hkjc_open.get('d', 0), hkjc_open.get('l', 0),
            hkjc_close.get('w', 0), hkjc_close.get('d', 0), hkjc_close.get('l', 0),
            m.get('odds_source', ''),
            ah_hc_val, ah_hw_val, ah_aw_val, ah_source,
            pin_ah_hc_val, pin_ah_hw_val, pin_ah_aw_val,
            pin_ou_line_val, pin_ou_over_val, pin_ou_under_val,
            ou_close.get('over', 0) or 0, ou_close.get('line', 0) or 0, ou_close.get('under', 0) or 0,
            ou_open.get('over', 0) or 0, ou_open.get('line', 0) or 0, ou_open.get('under', 0) or 0,
            liji_ou_close.get('over', 0) or 0, liji_ou_close.get('line', 0) or 0, liji_ou_close.get('under', 0) or 0,
            liji_ou_open.get('over', 0) or 0, liji_ou_open.get('line', 0) or 0, liji_ou_open.get('under', 0) or 0,
            ms_ou_close.get('over', 0) or 0, ms_ou_close.get('line', 0) or 0, ms_ou_close.get('under', 0) or 0,
            ms_ou_open.get('over', 0) or 0, ms_ou_open.get('line', 0) or 0, ms_ou_open.get('under', 0) or 0,
            record_id
        ))
        updated += 1
    
    # 第二遍：用oddsmagnet缓存补充未匹配的DB记录
    om_cache_path = os.path.join(DATA_BASE_DIR, "data", "cache", "real_odds.json")
    unmatched_records = [r for r in db_records if r[0] not in matched_db_ids]
    
    if unmatched_records and os.path.exists(om_cache_path):
        try:
            with open(om_cache_path, 'r', encoding='utf-8') as f:
                om_data = json.load(f)
            if isinstance(om_data, dict):
                # 构建oddsmagnet的match列表
                om_matches = []
                for key, item in om_data.items():
                    match_date = item.get('matchDate', '')
                    if target_date and match_date != target_date and match_date != prev_day:
                        continue
                    parts = key.split(' vs ')
                    if len(parts) != 2: continue
                    om_matches.append({
                        'home': parts[0].strip(),
                        'away': parts[1].strip(),
                        'pinnacle_open': {'w': item.get('pinnacle_open_w', 0) or 0, 'd': item.get('pinnacle_open_d', 0) or 0, 'l': item.get('pinnacle_open_l', 0) or 0},
                        'pinnacle_close': {'w': item.get('pinnacle_open_w', 0) or 0, 'd': item.get('pinnacle_open_d', 0) or 0, 'l': item.get('pinnacle_open_l', 0) or 0},
                        'pinnacle_movement': {'w': 'stable', 'd': 'stable', 'l': 'stable'},
                        'implied_prob': {},
                        'pinnacle_margin': item.get('avg_margin', 0),
                        'avg_odds_open': {'w': item.get('home', 0) or 0, 'd': item.get('draw', 0) or 0, 'l': item.get('away', 0) or 0},
                        'avg_odds_close': {'w': item.get('home', 0) or 0, 'd': item.get('draw', 0) or 0, 'l': item.get('away', 0) or 0},
                        'hkjc_open': {'w': item.get('hkjc_open_w', 0) or 0, 'd': item.get('hkjc_open_d', 0) or 0, 'l': item.get('hkjc_open_l', 0) or 0},
                        'hkjc_close': {'w': item.get('hkjc_open_w', 0) or 0, 'd': item.get('hkjc_open_d', 0) or 0, 'l': item.get('hkjc_open_l', 0) or 0},
                        'odds_source': 'Pinnacle' if (item.get('pinnacle_open_w', 0) or 0) > 0 else '百家平均',
                    })
                
                om_matched = 0
                for record in unmatched_records:
                    record_id, db_home, db_away, db_time = record
                    best_match = None
                    best_sim = 0
                    for om_m in om_matches:
                        sim_h = team_name_similarity(om_m['home'], db_home)
                        sim_a = team_name_similarity(om_m['away'], db_away)
                        avg_sim = (sim_h + sim_a) / 2
                        if avg_sim > best_sim:
                            best_sim = avg_sim
                            best_match = om_m
                    
                    if best_match and best_sim >= 0.4:
                        pin_open = best_match.get('pinnacle_open', {})
                        pin_close = best_match.get('pinnacle_close', {})
                        implied = best_match.get('implied_prob', {})
                        margin = best_match.get('pinnacle_margin', 0)
                        avg_open = best_match.get('avg_odds_open', {})
                        avg_close = best_match.get('avg_odds_close', {})
                        hkjc_open = best_match.get('hkjc_open', {})
                        hkjc_close = best_match.get('hkjc_close', {})
                        
                        # 亚盘让球盘（仅百家平均，HKJC已取消）
                        ah_close = best_match.get('ah_avg_close') or {}
                        ah_source = 'avg' if best_match.get('ah_avg_close') else ''

                        # Pinnacle亚盘/大小球和大小球数据 — oddsmagnet fallback无此数据
                        pin_ah = {}
                        pin_ou = {}
                        ou_close = {}
                        ou_open = {}
                        liji_ou_close = {}
                        liji_ou_open = {}
                        ms_ou_close = {}
                        ms_ou_open = {}

                        # 保护已有非零ah数据：只在DB原值为0/NULL时才写入新值
                        om_ah_hc = ah_close.get('handicap', 0) or 0
                        om_ah_hw = ah_close.get('home_w', 0) or 0
                        om_ah_aw = ah_close.get('away_w', 0) or 0

                        cursor.execute("""
                            UPDATE poisson_predictions SET
                                pinnacle_open_w = ?, pinnacle_open_d = ?, pinnacle_open_l = ?,
                                pinnacle_close_w = ?, pinnacle_close_d = ?, pinnacle_close_l = ?,
                                pinnacle_movement = ?, pinnacle_margin = ?,
                                implied_prob_w = ?, implied_prob_d = ?, implied_prob_l = ?,
                                avg_odds_open_w = ?, avg_odds_open_d = ?, avg_odds_open_l = ?,
                                avg_odds_close_w = ?, avg_odds_close_d = ?, avg_odds_close_l = ?,
                                hkjc_open_w = ?, hkjc_open_d = ?, hkjc_open_l = ?,
                                hkjc_close_w = ?, hkjc_close_d = ?, hkjc_close_l = ?,
                                odds_source = ?,
                                ah_handicap = CASE WHEN ah_handicap IS NOT NULL AND ah_handicap != 0 THEN ah_handicap ELSE ? END,
                                ah_home_water = CASE WHEN ah_handicap IS NOT NULL AND ah_handicap != 0 THEN ah_home_water ELSE ? END,
                                ah_away_water = CASE WHEN ah_handicap IS NOT NULL AND ah_handicap != 0 THEN ah_away_water ELSE ? END,
                                ah_source = CASE WHEN ah_handicap IS NOT NULL AND ah_handicap != 0 THEN ah_source ELSE ? END,
                                pin_ah_handicap = CASE WHEN pin_ah_handicap IS NOT NULL AND pin_ah_handicap != 0 THEN pin_ah_handicap ELSE ? END,
                                pin_ah_home_water = CASE WHEN pin_ah_handicap IS NOT NULL AND pin_ah_handicap != 0 THEN pin_ah_home_water ELSE ? END,
                                pin_ah_away_water = CASE WHEN pin_ah_handicap IS NOT NULL AND pin_ah_handicap != 0 THEN pin_ah_away_water ELSE ? END,
                                pin_ou_line = CASE WHEN pin_ou_line IS NOT NULL AND pin_ou_line != 0 THEN pin_ou_line ELSE ? END,
                                pin_ou_over = CASE WHEN pin_ou_line IS NOT NULL AND pin_ou_line != 0 THEN pin_ou_over ELSE ? END,
                                pin_ou_under = CASE WHEN pin_ou_line IS NOT NULL AND pin_ou_line != 0 THEN pin_ou_under ELSE ? END,
                                ou_over = ?, ou_line = ?, ou_under = ?,
                                ou_open_over = ?, ou_open_line = ?, ou_open_under = ?,
                                liji_ou_over = ?, liji_ou_line = ?, liji_ou_under = ?,
                                liji_ou_open_over = ?, liji_ou_open_line = ?, liji_ou_open_under = ?,
                                ms_ou_over = ?, ms_ou_line = ?, ms_ou_under = ?,
                                ms_ou_open_over = ?, ms_ou_open_line = ?, ms_ou_open_under = ?
                            WHERE id = ?
                        """, (
                            pin_open.get('w', 0), pin_open.get('d', 0), pin_open.get('l', 0),
                            pin_close.get('w', 0), pin_close.get('d', 0), pin_close.get('l', 0),
                            json.dumps(best_match.get('pinnacle_movement', {}), ensure_ascii=False),
                            margin,
                            implied.get('w', 0), implied.get('d', 0), implied.get('l', 0),
                            avg_open.get('w', 0), avg_open.get('d', 0), avg_open.get('l', 0),
                            avg_close.get('w', 0), avg_close.get('d', 0), avg_close.get('l', 0),
                            0, 0, 0,  # hkjc_open — 已取消，值全为0
                            0, 0, 0,  # hkjc_close — 已取消，值全为0
                            best_match.get('odds_source', ''),
                            om_ah_hc, om_ah_hw, om_ah_aw, ah_source,
                            0, 0, 0,  # pin_ah — OM fallback无此数据，仅补空值
                            0, 0, 0,  # pin_ou — OM fallback无此数据
                            0, 0, 0,  # ou close
                            0, 0, 0,  # ou open
                            0, 0, 0,  # liji_ou close
                            0, 0, 0,  # liji_ou open
                            0, 0, 0,  # ms_ou close
                            0, 0, 0,  # ms_ou open
                            record_id
                        ))
                        om_matched += 1
                        updated += 1
                        if pin_open.get('w', 0) > 0:
                            print(f"  [OM] {db_home} vs {db_away} <- {best_match['home']} vs {best_match['away']} (sim={best_sim:.2f}) Pin={pin_open['w']:.2f}/{pin_open['d']:.2f}/{pin_open['l']:.2f}")
                
                if om_matched > 0:
                    print(f"[INFO] oddsmagnet补充写入: {om_matched} 条")
        except Exception as e:
            print(f"[WARN] oddsmagnet补充失败: {e}")
    
    conn.commit()
    conn.close()
    print(f"[INFO] 写入完成，更新 {updated} 条记录")
    return updated




# ========== 缓存读取 + DB写入 ==========

def load_odds_cache(date_str, latest=True):
    """加载赔率缓存文件
    
    Args:
        date_str: 日期字符串 YYYY-MM-DD
        latest: True=返回最新的缓存，False=返回所有缓存（按时间排序）
    
    Returns:
        latest=True: 单个缓存dict或None
        latest=False: 缓存列表
    """
    cache_dir = os.path.join(DATA_BASE_DIR, "data", "cache")
    date_tag = date_str.replace('-', '')
    
    # 查找所有匹配的缓存文件
    cache_files = []
    if os.path.exists(cache_dir):
        for f in os.listdir(cache_dir):
            if f.startswith(f"pinnacle_odds_{date_tag}_") and f.endswith(".json"):
                cache_files.append(os.path.join(cache_dir, f))
    
    if not cache_files:
        return None if latest else []
    
    # 按文件名排序（含时间戳，最新的在后面）
    cache_files.sort(reverse=True)
    
    if latest:
        # 返回pinnacle场次最多的缓存（最新缓存可能0场，旧缓存有数据）
        best_data = None
        best_pin = -1
        for cf in cache_files:
            with open(cf, 'r', encoding='utf-8') as f:
                data = json.load(f)
            pin_count = data.get('summary', {}).get('pinnacle', 0)
            total_count = data.get('summary', {}).get('total', 0)
            if pin_count > best_pin or (pin_count == best_pin and total_count > (best_data.get('summary',{}).get('total',0) if best_data else 0)):
                best_data = data
                best_pin = pin_count
                data['_cache_file'] = cf
        return best_data
    else:
        results = []
        for cf in cache_files:
            with open(cf, 'r', encoding='utf-8') as f:
                d = json.load(f)
            d['_cache_file'] = cf
            results.append(d)
        return results


def apply_odds_to_db(db_path, date_str=None, cache_path=None):
    """从缓存读取赔率数据并写入DB
    
    Args:
        db_path: 数据库路径
        date_str: 日期字符串（自动查找最新缓存）
        cache_path: 指定缓存文件路径（优先级高于date_str）
    
    Returns:
        int: 更新的记录数
    """
    if cache_path:
        # 指定缓存文件
        with open(cache_path, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)
        matches = cache_data.get('matches', [])
        date_str = cache_data.get('date', date_str)
    elif date_str:
        # 自动查找最新缓存
        cache_data = load_odds_cache(date_str, latest=True)
        if not cache_data:
            print(f"[WARN] 未找到 {date_str} 的赔率缓存")
            return 0
        matches = cache_data.get('matches', [])
        print(f"[INFO] 使用缓存: {cache_data.get('_cache_file', '?')} (拉取时间: {cache_data.get('fetch_time', '?')})")
    else:
        print("[ERROR] 必须指定 date_str 或 cache_path")
        return 0
    
    return save_to_db(matches, db_path, date_str)

if __name__ == '__main__':
    import sys
    import argparse
    parser = argparse.ArgumentParser(description="赔率数据拉取与写入")
    parser.add_argument('--date', help='目标日期 YYYY-MM-DD，默认今天')
    parser.add_argument('--fetch-only', action='store_true', help='仅拉取保存缓存，不写DB')
    parser.add_argument('--apply', help='从缓存写入DB，指定日期 YYYY-MM-DD')
    parser.add_argument('--cache', help='从指定缓存文件写入DB')
    parser.add_argument('--db', help='指定DB路径（配合--apply使用）')
    parser.add_argument('--list-cache', help='列出指定日期的所有缓存')
    args = parser.parse_args()
    
    if args.list_cache:
        caches = load_odds_cache(args.list_cache, latest=False)
        if not caches:
            print(f"未找到 {args.list_cache} 的缓存")
        else:
            for c in caches:
                s = c.get('summary', {})
                print(f"  {c.get('_cache_file', '?').split('/')[-1]} | 拉取: {c.get('fetch_time', '?')} | "
                      f"共{s.get('total',0)}场 pin:{s.get('pinnacle',0)} sb:{s.get('sb',0)}")
    elif args.apply or args.cache:
        db = args.db or DB_JINGCAI
        updated = apply_odds_to_db(db, date_str=args.apply, cache_path=args.cache)
        print(f"✅ 写入完成: {updated} 条")
    elif args.fetch_only:
        results = fetch_pinnacle_odds(args.date)
        print(f"✅ 拉取完成: {len(results)} 场（仅缓存，未写DB）")
    else:
        # 默认：拉取+缓存+写DB（兼容旧用法）
        date_str = args.date
        results = fetch_pinnacle_odds(date_str)
        
        # 写DB
        if results:
            date_for_db = date_str or datetime.now().strftime('%Y-%m-%d')
            jc_updated = apply_odds_to_db(DB_JINGCAI, date_str=date_for_db)
            print(f"✅ 竞彩DB写入: {jc_updated} 条")
            bd_updated = apply_odds_to_db(DB_BEIDAN, date_str=date_for_db)
            print(f"✅ 北单DB写入: {bd_updated} 条")
