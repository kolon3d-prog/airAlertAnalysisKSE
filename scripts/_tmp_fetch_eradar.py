import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scrape_tme import fetch

ch = 'eRadarrua'
page1 = fetch(ch)
print(f'page1: {len(page1)} msgs, ids {page1[0]["msg_id"]}..{page1[-1]["msg_id"]}')
page2 = fetch(ch, before=page1[0]['msg_id'])
print(f'page2: {len(page2)} msgs, ids {page2[0]["msg_id"]}..{page2[-1]["msg_id"]}')
recs = sorted(page1 + page2, key=lambda r: r['msg_id'])
out = 'data/raw/eradarrua_sample.jsonl'
with open(out, 'w', encoding='utf-8') as f:
    for r in recs:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')
print(f'wrote {len(recs)} -> {out}')
