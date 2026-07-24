import urllib.request, re

url = "https://bf.titan007.com/football/Over_20260721.htm"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
resp = urllib.request.urlopen(req, timeout=10)
raw = resp.read()
html = raw.decode("gb2312", errors="replace")

# Find a complete finished match from the beginning
# Look for both "完" and the full table structure
# The structure seems to be: tr1 (league/time etc.) + tr2 (teams/score/HALF_SCORE)
idx = html.find("完")
# Show 500 chars before and 500 after the first 完
start = max(0, idx - 1000)
end = min(len(html), idx + 1000)
print(f"=== Around first '完' (pos {idx}) ===")
print(html[start:end])
