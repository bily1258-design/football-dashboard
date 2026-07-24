import urllib.request, re

url = "https://bf.titan007.com/football/Over_20260721.htm"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
resp = urllib.request.urlopen(req, timeout=10)
raw = resp.read()
html = raw.decode("gb2312", errors="replace")

# Find rows with 完 (finished) score
parts = html.split("完</td>")
print(f"Total finished matches: {len(parts)-1}")

# Sample a few rows - look for SID in links
for i in range(1, min(8, len(parts))):
    row_context = parts[i][-400:]
    # Find any links containing sid or numbers
    sids = re.findall(r'A[^>]*href=[\"\']?([^\"\'> ]+)', row_context)
    scores = re.findall(r'>(\d+)[:-](\d+)<', row_context)
    teams = re.findall(r'title=[\"\']([^\"\']+)[\"\']', row_context)
    
    # Also look for numeric patterns that look like SIDs
    nums = re.findall(r'(?:Analysis|Detail|js\?id=|sid=)(\d+)', row_context)
    plain_nums = re.findall(r'[?&](\d+)\.htm', row_context)
    
    print(f"\n--- Row {i} ---")
    print(f"  SIDs from links: {sids}")
    print(f"  Analysis/Detail nums: {nums}")
    print(f"  Numbers in hrefs: {plain_nums}")
    print(f"  Scores: {scores}")
    print(f"  Teams: {teams[:4]}")
    print(f"  Context: {row_context[:300]}")

