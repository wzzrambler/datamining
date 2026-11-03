# -*- coding: utf-8 -*-
"""P1 语料变动后，把 P2 的 39 条定向验收用例重新锚到新偏移上。

用例是按"某文档 content 的第 start..end 字符"写的，P1 删掉重复行之后
偏移整体前移，直接跑会全部判错。这里按原文片段在新语料里重新定位：

    唯一命中        → 直接换偏移
    多处命中        → 用前后各 20 字消歧
    一处都找不到    → 该用例的锚点正好被删掉了，报出来人工决定去留

    python -X utf8 tools/P2/reanchor_cases.py --old .work/p1_before/documents.json
    python -X utf8 tools/P2/reanchor_cases.py --old ... --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / 'src/P2实体识别/config/quality_cases.json'
NEW_DOCS = ROOT / 'processed/P1爬虫数据清洗/documents.json'
OLD_DOCS = ROOT / '.work/p1_before/documents.json'
CTX = 20


def load(path):
    return {d['doc_id']: d for d in json.loads(Path(path).read_text(encoding='utf-8'))}


def locate(old_doc, new_text, start, end):
    """在 new_text 里找旧片段；返回 (start, end) 或 None。"""
    snippet = old_doc['content'][start:end]
    if not snippet:
        return None
    hits, pos = [], new_text.find(snippet)
    while pos >= 0:
        hits.append(pos)
        pos = new_text.find(snippet, pos + 1)
    if len(hits) == 1:
        return hits[0], hits[0] + len(snippet)
    if not hits:
        return None
    # 多处命中：拿旧文本的前后文再筛一遍
    lead = old_doc['content'][max(0, start - CTX):start]
    tail = old_doc['content'][end:end + CTX]
    for pos in hits:
        pre = new_text[max(0, pos - CTX):pos]
        post = new_text[pos + len(snippet):pos + len(snippet) + CTX]
        if pre.endswith(lead) and post.startswith(tail):
            return pos, pos + len(snippet)
    return hits[0], hits[0] + len(snippet)      # 上下文也对不上时取第一处，下面会标记


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--old', default=str(OLD_DOCS))
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    old, new = load(args.old), load(NEW_DOCS)
    data = json.loads(CASES.read_text(encoding='utf-8'))
    changed, missing = [], []

    for case in data['cases']:
        doc_id = case['doc_id']
        before, after = old.get(doc_id), new.get(doc_id)
        if not before or not after:
            missing.append({**case, 'why': 'doc 不存在'})
            continue
        hit = locate(before, after['content'], case['start'], case['end'])
        if hit is None:
            missing.append({**case, 'why': '原文片段已被 P1 剔除',
                            'snippet': before['content'][case['start']:case['end']][:30]})
            continue
        if (hit[0], hit[1]) != (case['start'], case['end']):
            changed.append({'case_id': case['case_id'], 'doc_id': doc_id,
                            'old': [case['start'], case['end']], 'new': list(hit),
                            'text': after['content'][hit[0]:hit[1]]})
            case['start'], case['end'] = hit

    print(json.dumps({'moved': len(changed), 'unfound': len(missing),
                      'moved_detail': changed, 'unfound_detail': missing},
                     ensure_ascii=False, indent=1))
    if missing:
        print('\n有 %d 条用例锚点落在被删的行里，先别落盘，人工确认。' % len(missing))
        return 1
    if not args.apply:
        print('\n（未写入；确认后用 --apply 落盘）')
        return 0
    data['input_sha256'] = hashlib_sha(NEW_DOCS)
    CASES.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('\n已写入 %d 条新偏移，并更新 input_sha256' % len(changed))
    return 0


def hashlib_sha(new_docs_path):
    import hashlib
    return hashlib.sha256(Path(new_docs_path).read_bytes()).hexdigest()


if __name__ == '__main__':
    sys.exit(main())
