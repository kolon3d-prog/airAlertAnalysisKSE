import json, sys
evs = [json.loads(l) for l in open('data/processed/threat_events_sample.jsonl', encoding='utf-8')]
print('total events:', len(evs))

# digest msg 35995 should produce 9 segments
d = [e for e in evs if e['msg_id'] == 35995]
print('\n== msg 35995 segments:', len(d))
for e in d:
    print(' ', e['threat_type'], e['oblasts_ua'], e['raion_keys'], e['cities'], 'hdg=', e['heading'])

print('\n== headings != None:')
for e in evs:
    if e['heading']:
        print(' ', e['msg_id'], e['heading'], '|', e['threat_type'], e['oblasts_ua'], e['cities'])

print('\n== counts != None:')
for e in evs:
    if e['count']:
        print(' ', e['msg_id'], e['count'], e['threat_type'])

print('\n== region-less (deferred):')
src = {json.loads(l)['msg_id']: json.loads(l)['text'] for l in open('data/raw/kpszsu_sample.jsonl', encoding='utf-8')}
for e in evs:
    if not (e['raion_keys'] or e['oblasts_ua'] or e['cities']):
        seg = [s for s in src[e['msg_id']].split('\n')]
        print(' ', e['msg_id'], e['threat_type'], '|', ' '.join(src[e['msg_id']].split())[:110])

print('\n== threat_type distribution:')
from collections import Counter
print(Counter(e['threat_type'] for e in evs))
