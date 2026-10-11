#!/usr/bin/env python3
"""Parse monitoring-channel Telegram messages into structured Threat Events.

Hybrid: regex-first extraction; LLM fallback (DashScope qwen-flash) for messages
with threat keywords but no regex region hit.

Usage: python parse_telegram.py <in.jsonl> <out.jsonl> [--llm on|off|auto]
  auto (default) = on if DASHSCOPE_API_KEY is set, else regex-only.

Input : JSONL {channel, msg_id, datetime_utc, text, ...}
Output: JSONL {channel, msg_id, ts, threat_type, raion_keys[], oblasts_ua[],
               cities[], heading, count, confidence}
  - raion_keys   : NEPTUN raions.geojson properties.key (canonical, straight ')
  - oblasts_ua   : "<X> область" (raions.csv naming) | "АР Крим"
  - cities       : matched city mentions (parent raion also added when known)
  - heading      : 8-wind compass enum N/NE/E/SE/S/SW/W/NW, "/" if two stated
  - count        : stated group size or None ("кілька груп" -> None)
  - confidence   : 'regex' | 'llm'
"""
import argparse, csv, hashlib, json, os, re, sys, time
import urllib.request

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def dp(*p): return os.path.join(ROOT, *p)

APO = {0x2019: "'", 0x2018: "'"}  # curly -> straight apostrophe
def norm(s): return s.translate(APO).lower()

# ---------------------------------------------------------------- gazetteer
GEOJSON_URL = 'https://neptun.in.ua/raions.geojson'

def load_raions():
    """-> list of (raion_key, oblast_ua). NEPTUN key is canonical."""
    p = dp('data/processed/raions.csv')
    if os.path.exists(p):
        return [(norm(r['key']), r['oblast_ua']) for r in
                csv.DictReader(open(p, encoding='utf-8'))]
    p = dp('data/raw/official_data_uk.csv')
    if os.path.exists(p):  # derive keys from UA raion names
        seen = {}
        for r in csv.DictReader(open(p, encoding='utf-8')):
            if r.get('raion'):
                seen.setdefault(norm(r['raion']).replace(' район', '').strip(), r['oblast'])
        return sorted(seen.items())
    cache = dp('data/raw/neptun_raions.geojson')
    if not os.path.exists(cache):
        req = urllib.request.Request(GEOJSON_URL, headers={'User-Agent': 'Mozilla/5.0'})
        json.dump(json.load(urllib.request.urlopen(req, timeout=30)),
                  open(cache, 'w'), ensure_ascii=False)
    d = json.load(open(cache, encoding='utf-8'))
    return [(norm(f['properties']['key']), '') for f in d['features']]

def flex(stem):  # apostrophe/hyphen-tolerant regex fragment
    s = re.escape(stem)
    return s.replace("'", "['’]?").replace(r'\-', '[-‑ ]?')

WORD = 'а-яіїєґ'
def raion_re(key):
    stem = flex(key[:-2])  # all NEPTUN keys end in 'ий'
    return re.compile(rf"(?<![{WORD}'-]){stem}(?:ий|ого|ому|ім|ій)(?![{WORD}])")

COLLQ = lambda s: flex(s) + rf'(?:ою|ією|[аиуіє])(?![{WORD}])'            # сумщин+а/и/у/і/ою
ADJF  = lambda s: (rf'{flex(s)}(?:[аю]|ої|ою|ій)(?![{WORD}])'             # feminine adj
                   rf'|{flex(s)}[{WORD}]*\s+обл(?:аст[{WORD}]*|\.)')      # any adj + обл./область
_O = ' область'
OB_DEF = [
    ('Вінницька',      ['вінниччин'],                ['вінницьк'],              None),
    ('Волинська',      ['волинщин'],                 ['волинськ'],              r'волин[ьію](?![а-я])'),
    ('Дніпропетровська',['дніпропетровщин'],         ['дніпропетровськ'],       None),
    ('Донецька',       ['донеччин'],                 ['донецьк'],               None),
    ('Житомирська',    ['житомирщин'],               ['житомирськ'],            None),
    ('Закарпатська',   [],                           ['закарпатськ'],           r'закарпатт[яію]'),
    ('Запорізька',     [],                           ['запорізьк'],             None),
    ('Івано-Франківська',['івано-франківщин','франківщин'], ['івано-франківськ'], r'прикарпатт[яію]'),
    ('Київська',       ['київщин'],                  ['київськ'],               None),
    ('Кіровоградська', ['кіровоградщин'],            ['кіровоградськ'],         None),
    ('Луганська',      ['луганщин'],                 ['луганськ'],              None),
    ('Львівська',      ['львівщин'],                 ['львівськ'],              None),
    ('Миколаївська',   ['миколаївщин'],              ['миколаївськ'],           None),
    ('Одеська',        ['одещин'],                   ['одеськ'],                None),
    ('Полтавська',     ['полтавщин'],                ['полтавськ'],             None),
    ('Рівненська',     ['рівненщин'],                ['рівненськ'],             None),
    ('Сумська',        ['сумщин'],                   ['сумськ'],                None),
    ('Тернопільська',  ['тернопільщин'],             ['тернопільськ'],          None),
    ('Харківська',     ['харківщин'],                ['харківськ'],             None),
    ('Херсонська',     ['херсонщин'],                ['херсонськ'],             None),
    ('Хмельницька',    ['хмельниччин'],              ['хмельницьк'],            None),
    ('Черкаська',      ['черкащин'],                 ['черкаськ'],              None),
    ('Чернівецька',    ['чернівеччин'],              ['чернівецьк'],            None),
    ('Чернігівська',   ['чернігівщин'],              ['чернігівськ'],           None),
    ('АР Крим',        [],                           ['кримськ'],               r'\bкрим[ауі]?'),
]
OBLAST_RES = [(re.compile('|'.join([*(COLLQ(s) for s in c), *(ADJF(s) for s in a),
                                    *([x] if x else [])])), name + _O if name != 'АР Крим' else name)
              for name, c, a, x in OB_DEF]

CITIES = [  # (regex, city_key, parent_raion_key or None)
    (r'\bки[їі]в(?:а|у|ом|і|е)?\b|\bкиев\w*|\bстолиц\w+', 'київ', None),  # місто, не Київська обл
    (r'\bхарків(?:а|у|ом|і|е)?\b', 'харків', 'харківський'),
    (r'\bодес(?:а|и|у|ою|і)\b', 'одеса', 'одеський'),
    (r'\bдніпр(?:о|а|у|ом|і|е)\b', 'дніпро', 'дніпровський'),
    (r'\bзапоріжж\w*', 'запоріжжя', 'запорізький'),
    (r'\bмикола[їі]в(?:а|у|ом|і|е)?\b', 'миколаїв', 'миколаївський'),
    (r'\bкрив\w*\s+р(?:іг|огу|озі)\w*', 'кривий ріг', 'криворізький'),
    (r'\bсум(?:и|ами|ах)\b', 'суми', 'сумський'),
    (r'\bполтав(?:а|и|у|ою|і)\b', 'полтава', 'полтавський'),
    (r'\bчернігів(?:а|у|ом|і|е)?\b', 'чернігів', 'чернігівський'),
    (r'\bчеркас\w*', 'черкаси', 'черкаський'),
    (r'\bкропивницьк\w*|\bкіровоград(?:а|у|ом|і|е)?\b', 'кропивницький', 'кропивницький'),
    (r'\bхмельницьк(?:ого|ому|им|ий|а|у)\b', 'хмельницький', 'хмельницький'),
    (r'\bжитомир(?:а|у|ом|і|е)?\b', 'житомир', 'житомирський'),
    (r'\bрівн(?:е|ого|ому|им)\b', 'рівне', 'рівненський'),
    (r'\bлуцьк(?:а|у|ом|і|е)?\b', 'луцьк', 'луцький'),
    (r'\bтернопол\w*', 'тернопіль', 'тернопільський'),
    (r'\bужгород\w*', 'ужгород', 'ужгородський'),
    (r'\bчернівці\b|\bчернівц(?:ів|ям|ями|ях)\b', 'чернівці', 'чернівецький'),
    (r'\bівано[- ]франківськ(?:а|у|ом|і|е)?\b', 'івано-франківськ', 'івано-франківський'),
    (r'\bвінниц\w*', 'вінниця', 'вінницький'),
    (r'\bхерсон(?:а|у|ом|і|е)?\b', 'херсон', 'херсонський'),
    (r'\bкраматорськ(?:а|у|ом|і|е)?\b', 'краматорськ', 'краматорський'),
    (r"\bслов['’]?янськ(?:а|у|ом|і|е)?\b", "слов'янськ", 'краматорський'),
    (r'\bбіл\w*\s+церкв\w*', 'біла церква', 'білоцерківський'),
    (r'\bбровар\w*', 'бровари', 'броварський'),
    (r'\bкременчук\w*', 'кременчук', 'кременчуцький'),
    (r'\bнікопол\w*', 'нікополь', 'нікопольський'),
    (r'\bмарган\w*', 'марганець', 'нікопольський'),
    (r"\bкам['’]?янськ(?:е|ого|ому|им)\b", "кам'янське", "кам'янський"),
    (r'\bпавлоград\w*', 'павлоград', 'павлоградський'),
    (r'\bдубн(?:о|а|у|ом|і)\b', 'дубно', 'рівненський'),
    (r'\bльвов\w*|\bльвів(?:а|у|ом|і|е)?\b', 'львів', 'львівський'),
]
CITY_RES = [(re.compile(p), c, k) for p, c, k in CITIES]

# ---------------------------------------------------------------- threats
THREATS = [(t, re.compile(p)) for t, p in [
    ('ballistic',      r'балісти|аеробаліст|[іи]скандер|к[іи]нджал|х-47|циркон'),
    ('mig31k',         r'м[іi]г[\s-]*31'),
    ('kab',            r'\bкаб\w{0,3}\b|авіаційн\w+\s+засоб|тактичн\w+\s+авіац'),
    ('cruise_missile', r'крилат|ракет|х-\d{2,3}|калібр'),
    ('uav',            r'шахед|shahed|бпла|bpla|дрон|безпілотн|геран|мопед'),
]]
SKIP_RE = re.compile(r'відбій|знешкоджено|атакував\s+\d')  # all-clear / Air Force Report
COUNT_RE = re.compile(r'(\d{1,3})\s*[xх×]?\s*(?:шахед\w*|дрон\w*|бпла|ракет\w*|каб\w*)')
BULLET = re.compile('^[🛵🚀🛸☄🛫🛬✈💥]\ufe0f?$')

# ---------------------------------------------------------------- headings
WINDS8 = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
_DIRECT = {'се': 'NE', 'св': 'NE', 'юв': 'SE', 'юз': 'SW', 'сз': 'NW'}
def wind_of(tok):
    if tok in _DIRECT: return _DIRECT[tok]
    if re.fullmatch(r'пн|північ\w*|півночі|сев\w*', tok): return 'N'
    if re.fullmatch(r'пд|півд\w*|юг\w*|ю', tok): return 'S'
    if re.fullmatch(r'сх|схід\w*|сход\w*|вост\w*|в', tok): return 'E'
    if re.fullmatch(r'зх|зах\w*|зап\w*|з', tok): return 'W'
    return None
COMB = {('N', 'E'): 'NE', ('N', 'W'): 'NW', ('S', 'E'): 'SE', ('S', 'W'): 'SW'}

def parse_winds(s):
    out = []
    for part in s.split('/'):
        ws = [w for w in (wind_of(t) for t in re.findall(rf'[{WORD}]+', part)) if w][:2]
        if ws:
            out.append(ws[0] if len(ws) == 1 else
                       COMB.get(tuple(ws)) or COMB.get(tuple(ws[::-1])) or ws[0])
        if len(out) == 2:
            break
    return out

SEP = r'(?:[\s➡→:\-=]|\ufe0f|на\b)*'
HEAD_PATS = [re.compile(p) for p in [
    rf'курс\w*{SEP}([^.,;!?]{{0,40}})',
    rf'([{WORD}]+(?:[- ][{WORD}]+)?)\s+курсом',
    rf'на\s+([{WORD}]+(?:[- ][{WORD}]+)?)\s+напрямк\w*',
    rf'напрямк\w*{SEP}([^.,;!?]{{0,30}})',
    rf'(?:руха\w*|летить|летять|йде|йдуть)[^.,;!?]{{0,40}}?\bна\s+([{WORD}]+(?:\s+[{WORD}]+)?)',
]]
def find_heading(low):
    for p in HEAD_PATS:
        m = p.search(low)
        if m:
            ws = parse_winds(m.group(1))
            if ws:
                return '/'.join(ws)
    return None

# ---------------------------------------------------------------- segments
def segments(text):
    lines = text.split('\n')
    idx = [i for i, l in enumerate(lines) if BULLET.match(l.strip())]
    if len(idx) < 2:
        return [text]
    out = []
    for j, i in enumerate(idx):
        seg = '\n'.join(lines[i + 1: idx[j + 1] if j + 1 < len(idx) else len(lines)])
        if seg.strip():
            out.append(seg)
    return out

# ---------------------------------------------------------------- regex pass
def parse_segment(seg, raions, key2obl):
    low = ' '.join(norm(seg).split())
    tt = next((t for t, r in THREATS if r.search(low)), None)
    if not tt:
        return None
    cities, rkeys, obls = [], set(), set()
    for rx, cname, rkey in CITY_RES:
        if rx.search(low):
            cities.append(cname)
            if rkey and rkey in key2obl:
                rkeys.add(rkey)
                obls.add(key2obl[rkey])
    for rkey, rx in raions:
        if rx.search(low):
            rkeys.add(rkey)
            if key2obl.get(rkey):
                obls.add(key2obl[rkey])
    for rx, canon in OBLAST_RES:
        if rx.search(low):
            obls.add(canon)
    m = COUNT_RE.search(low)
    return {
        'threat_type': tt,
        'raion_keys': sorted(rkeys),
        'oblasts_ua': sorted(obls),
        'cities': sorted(cities),
        'heading': find_heading(low),
        'count': int(m.group(1)) if m else None,
        'confidence': 'regex',
        '_text': seg,
    }

# ---------------------------------------------------------------- LLM pass
LLM_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'
COST_PER_IN = 0.10 / 1e6   # qwen-flash ~$0.10 / 1M input tokens
COST_CAP = 15.0
CACHE_DIR = dp('data/cache/llm')

def llm_prompt(items, valid_keys, valid_obls):
    sys_p = (
        'Ти парсиш короткі повідомлення українських/російських моніторингових телеграм-каналів '
        'про повітряні загрози (шахеди, ракети, КАБи). Для КОЖНОГО повідомлення витягни:\n'
        '- threat_type: uav | cruise_missile | ballistic | kab | mig31k\n'
        f'- raion_keys: райони України, ТІЛЬКИ ключі зі списку: {" ".join(sorted(valid_keys))}\n'
        f'- oblasts_ua: ТІЛЬКИ зі списку: {"; ".join(sorted(valid_obls))}\n'
        '- heading: напрямок руху N|NE|E|SE|S|SW|W|NW або null\n'
        '- count: кількість апаратів (int) або null\n'
        'Якщо регіон не вказаний — порожні масиви. Не вигадуй. Вітчизняні (рф) регіони ігноруй.\n'
        'Відповідь СТРОГО одним JSON: {"events": [{"i": <індекс>, "threat_type": ..., '
        '"raion_keys": [...], "oblasts_ua": [...], "heading": ..., "count": ...}]}')
    usr = json.dumps([{'i': i, 'text': t} for i, t in items], ensure_ascii=False)
    return [{'role': 'system', 'content': sys_p}, {'role': 'user', 'content': usr}]

def llm_call(batch, api_key):
    r = requests.post(LLM_URL, timeout=60,
                      headers={'Authorization': f'Bearer {api_key}'},
                      json={'model': 'qwen-flash', 'messages': llm_prompt(*batch),
                            'response_format': {'type': 'json_object'}})
    if r.status_code == 400:  # some builds reject response_format
        r = requests.post(LLM_URL, timeout=60,
                          headers={'Authorization': f'Bearer {api_key}'},
                          json={'model': 'qwen-flash', 'messages': llm_prompt(*batch)})
    r.raise_for_status()
    d = r.json()
    u = d.get('usage', {})
    return d['choices'][0]['message']['content'], u.get('prompt_tokens', 0), u.get('completion_tokens', 0)

def clean_llm(ev, valid_keys, valid_obls):
    def _obl(o):
        o = str(o).strip()
        for cand in (o, o + _O, o.replace('область', '').strip() + _O):
            if cand in valid_obls:
                return cand
        return None
    hd = ev.get('heading')
    cnt = ev.get('count')
    return {
        'threat_type': ev.get('threat_type') if ev.get('threat_type') in
                     {'uav', 'cruise_missile', 'ballistic', 'kab', 'mig31k'} else None,
        'raion_keys': sorted({norm(str(k)) for k in ev.get('raion_keys', []) if norm(str(k)) in valid_keys}),
        'oblasts_ua': sorted({x for x in (_obl(o) for o in ev.get('oblasts_ua', [])) if x}),
        'heading': hd if hd in WINDS8 else None,
        'count': int(cnt) if isinstance(cnt, (int, float)) or (isinstance(cnt, str) and cnt.isdigit()) else None,
    }

def llm_pass(pending, api_key, valid_keys, valid_obls):
    """pending: list of event dicts with '_text'. Mutates them. Returns #answered."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cfile = lambda t: os.path.join(CACHE_DIR, hashlib.sha1(norm(t).encode()).hexdigest() + '.json')
    todo = []
    for ev in pending:
        f = cfile(ev['_text'])
        if os.path.exists(f):
            ev.update(clean_llm(json.load(open(f, encoding='utf-8')), valid_keys, valid_obls))
            ev['confidence'] = 'llm'
        else:
            todo.append(ev)
    answered = len(pending) - len(todo)
    tin = tout = 0
    cost = 0.0
    for off in range(0, len(todo), 20):
        if cost >= COST_CAP:
            print(f'llm: COST CAP ${COST_CAP} reached, {len(todo) - off} left deferred', file=sys.stderr)
            break
        chunk = todo[off:off + 20]
        batch = ([(i, ev['_text']) for i, ev in enumerate(chunk)], valid_keys, valid_obls)
        try:
            raw, i_in, i_out = llm_call(batch, api_key)
        except Exception as e:
            print(f'llm: call failed ({e}), {len(chunk)} left as regex', file=sys.stderr)
            continue
        tin, tout = tin + i_in, tout + i_out
        cost += i_in * COST_PER_IN
        print(f'llm: +{i_in} in /{i_out} out tok | cum {tin} in, ${cost:.4f}', file=sys.stderr)
        try:
            events = json.loads(raw)['events']
        except Exception:
            m = re.search(r'\{.*\}', raw, re.S)
            events = json.loads(m.group(0))['events'] if m else []
        by_i = {e.get('i'): e for e in events if isinstance(e, dict)}
        for i, ev in enumerate(chunk):
            ce = clean_llm(by_i.get(i, {}), valid_keys, valid_obls)
            json.dump(ce, open(cfile(ev['_text']), 'w', encoding='utf-8'), ensure_ascii=False)
            if ce['threat_type']:
                ev['threat_type'] = ce['threat_type']
            for k in ('raion_keys', 'oblasts_ua', 'heading', 'count'):
                if ce[k]:
                    ev[k] = ce[k]
            ev['confidence'] = 'llm'
            answered += 1
    print(f'llm: answered {answered}/{len(pending)}, tokens {tin} in /{tout} out, ~${cost:.4f} spent',
          file=sys.stderr)
    return answered

# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('inp'); ap.add_argument('out')
    ap.add_argument('--llm', default='auto', choices=['on', 'off', 'auto'])
    a = ap.parse_args()
    api_key = os.environ.get('DASHSCOPE_API_KEY')
    use_llm = a.llm == 'on' or (a.llm == 'auto' and bool(api_key))
    if use_llm and not api_key:
        print('warn: --llm on but DASHSCOPE_API_KEY missing; regex-only', file=sys.stderr)
        use_llm = False

    pairs = load_raions()
    key2obl = {k: o for k, o in pairs}
    raions = [(k, raion_re(k)) for k, _ in pairs]
    valid_obls = {o for _, o in pairs if o} | {n + _O for n, *_ in OB_DEF} | {'АР Крим'}
    print(f'gazetteer: {len(raions)} raions, {len(OBLAST_RES)} oblasts, {len(CITY_RES)} cities',
          file=sys.stderr)

    msgs = [json.loads(l) for l in open(a.inp, encoding='utf-8') if l.strip()]
    events, pending, n_skip = [], [], 0
    for m in msgs:
        text = m.get('text') or ''
        if not text or SKIP_RE.search(norm(text)):
            n_skip += bool(text)
            continue
        for seg in segments(text):
            ev = parse_segment(seg, raions, key2obl)
            if not ev:
                continue
            ev.update(channel=m['channel'], msg_id=m['msg_id'], ts=m.get('datetime_utc'))
            events.append(ev)
            if not (ev['raion_keys'] or ev['oblasts_ua'] or ev['cities']):
                pending.append(ev)

    regex_hit = len(events) - len(pending)
    print(f'{len(msgs)} msgs ({n_skip} skipped all-clear/report) -> {len(events)} threat segments; '
          f'regex region hit {regex_hit} ({100 * regex_hit / max(1, len(events)):.1f}%)',
          file=sys.stderr)

    if pending and use_llm:
        llm_pass(pending, api_key, set(key2obl), valid_obls)
    elif pending:
        print(f'llm off: {len(pending)} threat segments deferred (no regex region hit)', file=sys.stderr)

    with open(a.out, 'w', encoding='utf-8') as f:
        for ev in events:
            ev.pop('_text', None)
            f.write(json.dumps(ev, ensure_ascii=False) + '\n')
    print(f'wrote {len(events)} events to {a.out}', file=sys.stderr)

if __name__ == '__main__':
    main()
