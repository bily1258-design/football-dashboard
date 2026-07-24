import urllib.request, re, json

url = "https://bf.titan007.com/football/Over_20260721.htm"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
resp = urllib.request.urlopen(req, timeout=10)
raw = resp.read()
html = raw.decode("gb2312", errors="replace")

# Strategy: find all <table> or structured match entries
# The Over page has a specific table structure for completed matches
# Each match has a main row with sId, and a sub-row with score info

# Let me look for the HTML between two consecutive sId occurrences
# This should give us one complete match entry
sids = list(re.finditer(r"sId='(\d+)'", html))
print(f"Found {len(sids)} matches with sId")

# Get the text between first and second sId match
if len(sids) >= 2:
    ctx = html[sids[0].start()-50:sids[1].start()]
    print("=== Full context between first two sIds ===")
    print(ctx[:2000])
    print("...")
    # Also look for scores near the first sId
    print("\n=== Content 2000 chars after first sId ===")
    print(html[sids[0].start():sids[0].start()+2000])
