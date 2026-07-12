import json
with open('docs/data/results.json') as f:
    d = json.load(f)

pipeline_fids = set()
for recs in d['matches'].values():
    for r in recs:
        if 'fusion_prob' in r:
            pipeline_fids.add(r['id'])

beidan_fids = set()
for recs in d['matches'].values():
    for r in recs:
        if r.get('source') == 'beidan':
            beidan_fids.add(r['id'])

overlap = pipeline_fids & beidan_fids
only_beidan = beidan_fids - pipeline_fids
only_pipeline = pipeline_fids - beidan_fids

print(f"管线fids: {len(pipeline_fids)}")
print(f"北单fids: {len(beidan_fids)}")
print(f"重叠: {len(overlap)} -> {sorted(overlap)}")
print(f"仅北单: {len(only_beidan)} -> {sorted(only_beidan)}")
print(f"仅管线: {len(only_pipeline)}")
