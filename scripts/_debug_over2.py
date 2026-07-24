import urllib.request, re

url = "https://bf.titan007.com/football/Over_20260721.htm"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
resp = urllib.request.urlopen(req, timeout=10)
raw = resp.read()
html = raw.decode("gb2312", errors="replace")

# Split by "完</td>" to get each finished-match row
parts = html.split("完</td>")
print(f"Total finished match rows: {len(parts)-1}")

# For the first few rows, print the FULL row content before the split point
for i in range(1, min(6, len(parts))):
    # The row content is right before the "完</td>"
    # Let's look at the last 800 chars before 完
    row = parts[i][-800:]
    # Find sId
    sid_m = re.search(r"sId='(\d+)'", row)
    # Find score - it should be right before 完
    score_m = re.search(r'<td[^>]*>(\d+)[-:](\d+)</td>', row)
    # Find team names
    teams = re.findall(r'title="([^"]+)"', row)
    
    print(f"\n=== Row {i} ===")
    if sid_m: print(f"  SID: {sid_m.group(1)}")
    if score_m: print(f"  Score: {score_m.group(1)}-{score_m.group(2)}")
    if teams: print(f"  Teams: {teams[:4]}")
    
    # Show the key parts
    # Find the score td
    idx = row.rfind("<td")
    print(f"  Last score area: ...{row[-200:]}")
    print()

