# -*- coding: utf-8 -*-
"""汇总核对底表的人工判定；判断列未填完时以非零码退出。"""
import csv, json, sys
from collections import Counter
from pathlib import Path

_DIR = Path(__file__).resolve().parents[2] / 'outputs/P5/校验'
PATH = _DIR / '核对底表50条_判定.csv'
if not PATH.exists():
    PATH = _DIR / '核对底表50条.csv'
rows = list(csv.DictReader(open(PATH, encoding='utf-8-sig')))
pending = [r['序号'] for r in rows if not r['总体判定(合格/不合格)'].strip()]
if pending:
    print(json.dumps({'filled': len(rows) - len(pending), 'pending': pending}, ensure_ascii=False))
    sys.exit(1)
by_kind = Counter()
by_scene = Counter()
bad = []
for r in rows:
    verdict = r['总体判定(合格/不合格)'].strip()
    by_kind[(r['景区'], r['类型'], verdict)] += 1
    by_scene[(r['景区'], verdict)] += 1
    if verdict != '合格':
        bad.append({'序号': r['序号'], '规则ID': r['规则ID'], '类型': r['类型'], '备注': r['备注']})
print(json.dumps({'total': len(rows), 'verdict': dict(Counter(r['总体判定(合格/不合格)'] for r in rows)),
                  'bad': bad, 'by_scene': {f'{k[0]}/{k[1]}': v for k, v in by_scene.items()}},
                 ensure_ascii=False, indent=1))
