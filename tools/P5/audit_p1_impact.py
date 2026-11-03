# -*- coding: utf-8 -*-
"""P1 修复对下游 P5 证据的影响分析。

P1 剔除了 196 行"截图复读正文"的段落，段落编号和字符偏移整体重排。
P5 的观察按 package 存 provenance（doc_id + start/end），这些偏移现在指向错的地方。

这个脚本不改任何产物，只回答一个问题：
    把每条观察的原文片段拿到新语料里重新找，还能找到多少条？

找不到的说明它的依据正好是那 196 行重复文本，属于应该失效的观察。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BEFORE = ROOT / '.work/p1_before/documents.json'
AFTER = ROOT / 'processed/P1爬虫数据清洗/documents.json'
_RUN = (ROOT / 'processed/P5条件建议抽取/ACTIVE_RUN')
OBS = ROOT / 'processed/P5条件建议抽取/runs' / (
    _RUN.read_text('utf-8').strip() if _RUN.exists() else 'full-final-20261101') / 'raw_observations.json'


def load_docs(path):
    return {d['doc_id']: d for d in json.loads(path.read_text(encoding='utf-8'))}


def main():
    old, new = load_docs(BEFORE), load_docs(AFTER)
    observations = json.loads(OBS.read_text(encoding='utf-8'))

    stats = {'total': 0, 'anchor_ok_same_offset': 0, 'anchor_ok_moved': 0,
             'missing_text': 0, 'missing_doc': 0, 'no_block': 0}
    missing = []

    for ob in observations:
        stats['total'] += 1
        prov = ob.get('provenance') or {}
        doc_id = prov.get('doc_id')
        blocks = prov.get('blocks') or []
        if not blocks:
            stats['no_block'] += 1
            continue
        before, after = old.get(doc_id), new.get(doc_id)
        if not before or not after:
            stats['missing_doc'] += 1
            continue

        verdicts = []
        for b in blocks:
            snippet = before['content'][b['start']:b['end']]
            pos = after['content'].find(snippet) if snippet else -1
            if not snippet:
                verdicts.append('empty')
            elif pos < 0:
                verdicts.append('missing')
            elif pos == b['start']:
                verdicts.append('same')
            else:
                verdicts.append('moved')
        if 'missing' in verdicts:
            stats['missing_text'] += 1
            missing.append({'record_id': ob['record_id'], 'doc_id': doc_id,
                            'package_key': ob.get('package_key'),
                            'text': (ob['arguments'].get('consequence_text') or '')[:40]})
        elif all(v == 'same' for v in verdicts):
            stats['anchor_ok_same_offset'] += 1
        else:
            stats['anchor_ok_moved'] += 1

    stats['missing_list'] = missing
    stats['anchor_rate'] = round(
        (stats['anchor_ok_same_offset'] + stats['anchor_ok_moved']) / max(1, stats['total'] - stats['no_block']), 4)
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
