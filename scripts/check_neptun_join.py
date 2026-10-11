import urllib.request, json, pandas as pd

req = urllib.request.Request('https://neptun.in.ua/raions.geojson', headers={'User-Agent': 'Mozilla/5.0'})
d = json.load(urllib.request.urlopen(req))
nep_keys = [f['properties']['key'] for f in d['features']]

df = pd.read_csv('data/raw/official_data_en.csv')
csv_raions = sorted(df[(df['started_at'] >= '2025-12-01') & (df['level'] == 'raion')]['raion'].unique())

M = {'а':'a','б':'b','в':'v','г':'h','ґ':'g','д':'d','е':'e','є':'ie','ж':'zh','з':'z',
     'и':'y','і':'i','ї':'i','й':'i','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p',
     'р':'r','с':'s','т':'t','у':'u','ф':'f','х':'kh','ц':'ts','ч':'ch','ш':'sh',
     'щ':'shch','ь':'','ю':'iu','я':'ia',"'":'','-':'-'}
tr = lambda s: ''.join(M.get(c, c) for c in s)

nep_lat = {(tr(k) + ' raion').lower(): k for k in nep_keys}
ok = [r for r in csv_raions if r.lower() in nep_lat]
miss = [r for r in csv_raions if r.lower() not in nep_lat]
print('matched:', len(ok), '/', len(csv_raions))
print('sample matches:', ok[:10])
print('unmatched CSV raions:', miss)
