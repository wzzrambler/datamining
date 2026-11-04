# -*- coding: utf-8 -*-
"""汇总主题复核底表的人工判定；判定列未填完时以非零码退出。"""
import csv, json, sys
from collections import Counter
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / 'outputs/P6/主题复核底表525条.csv'
rows = list(csv.DictReader(open(PATH, encoding='utf-8-sig')))
pending = [r['序号'] for r in rows if not r['判定(成立/牵强/不成立)'].strip()]
if pending:
    print(json.dumps({'filled': len(rows) - len(pending), 'pending_count': len(pending)}, ensure_ascii=False))
    sys.exit(1)
print(json.dumps({'total': len(rows),
                  'verdict': dict(Counter(r['判定(成立/牵强/不成立)'] for r in rows)),
                  'by_scene': {k: dict(v) for k, v in
                               ((sc, Counter(r['判定(成立/牵强/不成立)'] for r in rows if r['景区'] == sc))
                                for sc in sorted({r['景区'] for r in rows})},
                  'not_holds': [{'序号': r['序号'], '主题': r['主题标签'], '实体': r['实体名称'],
                                 '备注': r['备注']} for r in rows
                                if r['判定(成立/牵强/不成立)'].strip() == '不成立']},
                 ensure_ascii=False, indent=1))
