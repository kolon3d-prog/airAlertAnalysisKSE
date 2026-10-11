import csv, json
M = {'а':'a','б':'b','в':'v','г':'h','ґ':'g','д':'d','е':'e','є':'ie','ж':'zh','з':'z',
     'и':'y','і':'i','ї':'i','й':'i','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p',
     'р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts','ч':'ch','ш':'sh',
     'щ':'shch','ь':'','ю':'iu','я':'ia',"'":'','\u2019':'','-':'-'}
tr = lambda s: ''.join(M.get(c, c) for c in s)
d = json.load(open('data/raw/neptun_raions.geojson', encoding='utf-8'))
keys = [f['properties']['key'] for f in d['features']]

def variants(lat):
    vs = {lat}
    if lat.startswith('ia'):
        vs.add('y' + lat[2:])
    return vs

nep_lat = {}
for k in keys:
    for v in variants(tr(k)):
        nep_lat[v + ' raion'] = k

RENAME = {'zviahelskyi raion': 'новоград-волинський'}
pairs = set()
with open('data/raw/official_data_en.csv', encoding='utf-8') as f:
    for row in csv.DictReader(f):
        if row['raion']:
            pairs.add((row['oblast'], row['raion']))

key2obl = {}
for o, r in pairs:
    rl = r.lower()
    k = RENAME.get(rl) or nep_lat.get(rl)
    if k:
        key2obl.setdefault(k, set()).add(o)

unmatched_keys = [k for k in keys if k not in key2obl]
print('NEPTUN keys without oblast:', len(unmatched_keys))
print(unmatched_keys)
print('multi-oblast keys:', {k: v for k, v in key2obl.items() if len(v) > 1})
