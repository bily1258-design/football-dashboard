import cProfile, pstats, io, os, time
import ai_analysis as A

matches = A.load_raw_matches()
print(f"[prof] loaded {len(matches)} matches")

# 1. analyze_matches
t0 = time.time()
pr = cProfile.Profile()
pr.enable()
results = A.analyze_matches(matches, {})
pr.disable()
t1 = time.time()
print(f"[prof] analyze_matches done: {len(results)} results in {t1-t0:.1f}s")
s = io.StringIO()
pstats.Stats(pr, stream=s).sort_stats('cumulative').print_stats(20)
outp = '/data/data/com.termux/files/home/football-dashboard/prof_analyze.txt'
with open(outp, 'w') as f:
    f.write(f"=== analyze_matches {t1-t0:.1f}s ===\n")
    f.write(s.getvalue())
print(f"[prof] wrote {outp}")

# 2. generate_frontend
t2 = time.time()
pr2 = cProfile.Profile()
pr2.enable()
A.generate_frontend(results)
pr2.disable()
t3 = time.time()
print(f"[prof] generate_frontend done in {t3-t2:.1f}s")
s2 = io.StringIO()
pstats.Stats(pr2, stream=s2).sort_stats('cumulative').print_stats(20)
outp2 = '/data/data/com.termux/files/home/football-dashboard/prof_frontend.txt'
with open(outp2, 'w') as f:
    f.write(f"=== generate_frontend {t3-t2:.1f}s ===\n")
    f.write(s2.getvalue())
print(f"[prof] wrote {outp2}")
print("[prof] ALL DONE")
