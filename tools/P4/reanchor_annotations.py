# -*- coding: utf-8 -*-
"""P1 语料更新后，把 P4 的 51 行人工标注重新锚到新正文上。

标注里的 reference/antecedent 都是"某篇 content 的第 a..b 字符"。P1 剔除截图复读行
以后正文短了、偏移前移，原来的区间会切到别的字上，甚至越界。这里不重标，只重定位：

    指代     先用它所在的整句在新正文里定位，再按句内偏移还原
    先行词   按原文片段在全文里找；多处命中时取"在指代之前且最近"的那处
    上下文   标注时的窗口规则是 [ref.start-65, ref.end+85]（51 行全部吻合），
             指代挪了位置就按同一规则重算，不保留旧窗口里的已删文字
    多先行词 antecedents_json 里的每段也按片段重新定位

找不到的（原文正好被删掉）直接报出来，不猜。落盘前用 evaluate.py 的同一套
回切规则把每行验一遍，验不过就整批拒绝写入。

    python -X utf8 tools/P4/reanchor_annotations.py --check
    python -X utf8 tools/P4/reanchor_annotations.py --apply
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / 'outputs/P4/P4样本原文与段落.json'
CSV_PATH = ROOT / 'outputs/P4/P4人工标注样本.csv'
P1 = ROOT / 'processed/P1爬虫数据清洗/documents.json'

# 标注时的上下文窗口：指代前 65 字、后 85 字，已对全部 51 行验证吻合。
CTX_BEFORE, CTX_AFTER = 65, 85


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_p1():
    with open(P1, encoding='utf-8') as f:
        return {d['doc_id']: d for d in json.load(f)}


def load_samples():
    with open(SAMPLES, encoding='utf-8') as f:
        return json.load(f)


def load_rows():
    with io.open(CSV_PATH, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def locate_sentence(content, sentence, old_start, old_end):
    """把整句挪到新正文，返回新的起始偏移；多处命中时取离旧位置最近的。"""
    hits, pos = [], content.find(sentence)
    while pos >= 0:
        hits.append(pos)
        pos = content.find(sentence, pos + 1)
    if not hits:
        return None
    return min(hits, key=lambda p: abs(p - old_start))


def locate_span(content, text, before, old_start):
    """定位一个片段；多处命中时取 before 之前最近的一处。"""
    hits, pos = [], content.find(text)
    while pos >= 0:
        hits.append(pos)
        pos = content.find(text, pos + 1)
    if not hits:
        return None
    front = [p for p in hits if p < before] or hits
    return min(front, key=lambda p: abs(p - old_start))


def verify(rows, p1):
    """用 evaluate.py 同一套规则把每行回切一遍，验不过的不许落盘。"""
    bad = []
    for row in rows:
        stem = row['annotation_id']
        doc = p1.get(row['doc_id'])
        if doc is None:
            bad.append('%s: 文档不存在' % stem)
            continue
        content = doc['content']
        try:
            ref = (int(row['reference_start']), int(row['reference_end']))
            if not 0 <= ref[0] < ref[1] <= len(content) or content[ref[0]:ref[1]] != row['reference_text']:
                raise ValueError('reference回切失败')
            for prefix in ('sentence', 'context'):
                a, b = int(row[prefix+'_start']), int(row[prefix+'_end'])
                if not 0 <= a < b <= len(content) or content[a:b] != row[prefix+'_text']:
                    raise ValueError(prefix+'回切失败')
                if not a <= ref[0] < ref[1] <= b:
                    raise ValueError(prefix+'不含指代')
            ants = json.loads(row['antecedents_json']) if row['antecedents_json'].strip() else []
            for a in ants:
                if not 0 <= a['start'] < a['end'] <= len(content) or content[a['start']:a['end']] != a['text']:
                    raise ValueError('多先行词回切失败')
            if row['antecedent_text']:
                a, b = int(row['antecedent_start']), int(row['antecedent_end'])
                if not 0 <= a < b <= len(content) or content[a:b] != row['antecedent_text']:
                    raise ValueError('先行词回切失败')
            pid = int(row['para_id'])
            para = next((p for p in doc['paragraphs'] if p.get('para_id', p.get('i')) == pid), None)
            if para is None or not para['start'] <= ref[0] < ref[1] <= para['end']:
                raise ValueError('段落不含指代')
        except (ValueError, KeyError) as exc:
            bad.append('%s: %s' % (stem, exc))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    p1 = load_p1()
    samples = load_samples()
    rows = load_rows()
    moved, failed = 0, []

    for row in rows:
        doc_id = row['doc_id']
        doc = p1.get(doc_id)
        if doc is None:
            failed.append({'id': row['annotation_id'], 'why': '文档不存在'})
            continue
        content = doc['content']
        rs, re_ = int(row['reference_start']), int(row['reference_end'])
        ref_text = row['reference_text']
        stem = row['annotation_id']
        old = dict(row)

        # 1) 整句定位
        sent = row['sentence_text']
        s_start = locate_sentence(content, sent, int(row['sentence_start']), int(row['sentence_end']))
        if s_start is None:
            failed.append({'id': stem, 'doc': doc_id, 'why': '整句在新正文里找不到', 'sentence': sent[:30]})
            continue
        delta = s_start - int(row['sentence_start'])
        n_rs, n_re = rs + delta, re_ + delta
        if content[n_rs:n_re] != ref_text:
            # 句内偏移对不上时退回全文定位
            hit = locate_span(content, ref_text, n_rs + 200, n_rs)
            if hit is None:
                failed.append({'id': stem, 'doc': doc_id, 'why': '指代片段对不上', 'text': ref_text})
                continue
            n_rs, n_re = hit, hit + len(ref_text)

        updates = {'reference_start': n_rs, 'reference_end': n_re,
                   'sentence_start': s_start, 'sentence_end': s_start + len(sent)}

        # 2) 先行词（可能不在同一句）
        a_text = row.get('antecedent_text') or ''
        if a_text:
            a_hit = locate_span(content, a_text, n_rs, int(row['antecedent_start']))
            if a_hit is None:
                failed.append({'id': stem, 'doc': doc_id, 'why': '先行词在新正文里找不到', 'text': a_text})
                continue
            updates['antecedent_start'], updates['antecedent_end'] = a_hit, a_hit + len(a_text)

        # 3) 多个先行词（antecedents_json）逐段重定位
        multi = json.loads(row['antecedents_json']) if row['antecedents_json'].strip() else []
        if multi:
            fixed = []
            for a in multi:
                a_hit = locate_span(content, a['text'], n_rs, int(a['start']))
                if a_hit is None:
                    failed.append({'id': stem, 'doc': doc_id, 'why': '多先行词在新正文里找不到',
                                   'text': a['text'][:20]})
                    break
                fixed.append({**a, 'start': a_hit, 'end': a_hit + len(a['text'])})
            else:
                if fixed != multi:
                    updates['antecedents_json'] = json.dumps(fixed, ensure_ascii=False)
            if len(fixed) != len(multi):
                continue

        # 4) 上下文窗口按同一规则重算（旧窗口里可能含被 P1 删掉的行）
        c_start = max(0, n_rs - CTX_BEFORE)
        c_end = min(len(content), n_re + CTX_AFTER)
        updates['context_start'] = c_start
        updates['context_end'] = c_end
        updates['context_text'] = content[c_start:c_end]

        # 5) 段落必须仍包住指代
        pid = int(row['para_id'])
        para = next((p for p in doc['paragraphs'] if p.get('para_id', p.get('i')) == pid), None)
        if para is None:
            failed.append({'id': stem, 'doc': doc_id, 'why': '原段落编号已不存在'})
            continue
        if not para['start'] <= n_rs < n_re <= para['end']:
            failed.append({'id': stem, 'doc': doc_id, 'why': '段落不再包住指代区间',
                           'ref': [n_rs, n_re], 'para': [para['start'], para['end']]})
            continue

        row.update({k: str(v) for k, v in updates.items()})
        if any(str(updates[k]) != old[k] for k in updates if k in old):
            moved += 1

    print(json.dumps({'rows': len(rows), 'moved': moved, 'failed': len(failed),
                      'failed_detail': failed}, ensure_ascii=False, indent=1))
    if failed:
        print('\n有 %d 行锚不回去，说明原文片段被 P1 删掉了。先别落盘。' % len(failed))
        return 1

    bad = verify(rows, p1)
    print('自检：%d/%d 行通过 evaluate.py 同规则回切' % (len(rows) - len(bad), len(rows)))
    if bad:
        print(json.dumps(bad, ensure_ascii=False, indent=1))
        print('\n自检不过，拒绝落盘。')
        return 1
    if not args.apply:
        print('\n（未写入；确认后用 --apply 落盘）')
        return 0

    # 回写 CSV
    with io.open(CSV_PATH, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    # 回写样本 JSON：换掉 5 篇的正文、段落与哈希
    for sdoc in samples['documents']:
        doc = p1[sdoc['doc_id']]
        sdoc['content'] = doc['content']
        sdoc['content_length'] = len(doc['content'])
        sdoc['paragraphs'] = doc['paragraphs']
        sdoc['content_sha256'] = hashlib.sha256(doc['content'].encode('utf-8')).hexdigest()
    samples['source_sha256'] = sha(P1)
    with open(SAMPLES, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(samples, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print('\n已写入：标注 %d 行，样本 %d 篇，source_sha256=%s' % (
        len(rows), len(samples['documents']), samples['source_sha256'][:12]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
