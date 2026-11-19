#!/usr/bin/env python3
import argparse
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor

p = argparse.ArgumentParser()
p.add_argument('variants', help='comma-separated variant names; live is the serving prompt')
p.add_argument('--cases', default='manifests/dispatch-route-cases.json')
p.add_argument('--runs', type=int, default=3)
p.add_argument('--out')
p.add_argument('--url', default='http://dispatch.ssint-main-coding:8080/classify')
a = p.parse_args()

cases = json.load(open(a.cases))
variants = a.variants.split(',')


def call(job):
    v, i, r = job
    body = json.dumps({'text': cases[i]['text'], 'variant': v}).encode()
    req = urllib.request.Request(a.url, data=body, headers={'content-type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            doc = json.loads(resp.read())
    except Exception as e:
        doc = {'error': str(e)}
    return {'variant': v, 'case': i, 'run': r, 'expect': cases[i]['expect'], **doc}


jobs = [(v, i, r) for v in variants for i in range(len(cases)) for r in range(a.runs)]
with ThreadPoolExecutor(8) as ex:
    results = list(ex.map(call, jobs))
if a.out:
    json.dump(results, open(a.out, 'w'), indent=1)

print(f"{'case':<5}" + ''.join(f'{v[:14]:>16}' for v in variants))
for i, c in enumerate(cases):
    row = f'{i:<5}'
    for v in variants:
        got = [x.get('builder') for x in results if x['variant'] == v and x['case'] == i and 'error' not in x]
        row += f"{sum(g == c['expect'] for g in got)}/{len(got)}".rjust(16)
    print(row + f"   {c['expect']:<12} {c['text'][:50]}")
print('total' + ''.join(
    f"{sum(x.get('builder') == x['expect'] for x in results if x['variant'] == v and 'error' not in x)}"
    f"/{sum(1 for x in results if x['variant'] == v and 'error' not in x)}".rjust(16) for v in variants))
print('failed calls: ' + ', '.join(f"{v} {sum(1 for x in results if x['variant'] == v and 'error' in x)}" for v in variants))
