import urllib.request, re

url = "https://bf.titan007.com/football/Over_20260721.htm"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
resp = urllib.request.urlopen(req, timeout=10)
raw = resp.read()
html = raw.decode("gb2312", errors="replace")

# Find actual score lines: look for "完</td>" specifically (完 in a td context)
# Find the actual match table area
# The table should have rows with scores like "2-1" or "3-0"
matches_found = 0
for m in re.finditer(r'<td[^>]*class=[\"\']?style\d[\"\']?[^>]*>(?:<b>)?(\d+)[-:](\d+)(?:</b>)?</td>', html):
    score = f"{m.group(1)}-{m.group(2)}"
    # Get context: 200 chars before the score
    ctx_start = max(0, m.start() - 300)
    ctx = html[ctx_start:m.start()]
    
    # Find team names in this context
    teams = re.findall(r'title="([^"]+)"', ctx)
    sids = re.findall(r"sId='(\d+)'", ctx)
    
    matches_found += 1
    print(f"Score: {score}  SIDs: {sids}  Teams: {teams[:4]}")
    if matches_found >= 10:
        break

print(f"\nTotal score patterns found: {matches_found}")
