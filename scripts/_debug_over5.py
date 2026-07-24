import urllib.request, re

url = "https://bf.titan007.com/football/Over_20260721.htm"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
resp = urllib.request.urlopen(req, timeout=10)
raw = resp.read()
html = raw.decode("gb2312", errors="replace")

# Search for actual score patterns more broadly
# On the titan007 Over page, the score format in the table is like:
# <td class=style1>3-2</td> where the 3-2 is the score
# But we need to find the right context

# Find "完</td>" within a table row context
for m in re.finditer(r'完</td>', html):
    ctx = html[max(0,m.start()-300):m.end()+100]
    # Check for sId
    sid_m = re.search(r"sId='(\d+)'", ctx)
    # Check for score: often there's a <td> with digits before the "半" or after
    score_m = re.search(r'<td[^>]*class=[\"\']?style\d[\"\']?[^>]*>(\d+)[:-](\d+)</td>', ctx)
    team_m = re.findall(r'title="([^"]+)"', ctx)
    
    if sid_m or team_m:
        print(f"SID: {sid_m.group(1) if sid_m else 'N/A'}")
        print(f"Score: {score_m.group(0) if score_m else 'N/A'}")
        print(f"Teams: {team_m[:4]}")
        # Show the 完 area 
        print(f"完 area: ...{ctx[-200:]}")
        print("---")
        break
