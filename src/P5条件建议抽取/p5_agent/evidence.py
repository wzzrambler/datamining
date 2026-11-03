"""把抽取结果里的引文定位到原文区间。

记录原先只挂到输入包（evidence_precision=package），核对一条建议要回原文翻。
这里在导出阶段做一次确定性定位：拿模型抄回来的那句话，在该包的段落里找，
唯一命中就记下起止位置，判为 span；找不到或多处命中就退回 package，不猜。
"""
from __future__ import annotations

MIN_QUOTE = 4


def _norm(text):
    """去掉标点空白，返回（规范化串，原位置索引）。"""
    chars, index = [], []
    for i, ch in enumerate(text):
        if ch.isalnum():
            chars.append(ch.casefold())
            index.append(i)
    return ''.join(chars), index


def _texts_of(record):
    """列出这条记录里值得定位的引文。"""
    args = record.get('arguments') or {}
    tool = record.get('tool_name')
    out = []
    if tool == 'submit_conditional_advice':
        if args.get('consequence_text'):
            out.append(('consequence_text', args['consequence_text']))
        cond = (args.get('condition') or {}).get('text')
        if cond:
            out.append(('condition', cond))
    elif tool == 'submit_poi_facts':
        for fact in args.get('facts') or []:
            if fact.get('value_text'):
                out.append(('value_text', fact['value_text']))
            cond = (fact.get('condition') or {})
            if isinstance(cond, dict) and cond.get('text'):
                out.append(('fact_condition', cond['text']))
    return out


def locate(text, content, blocks):
    """在给定段落里定位引文；唯一命中返回 (start, end, para_id)，否则 None。"""
    quote = (text or '').strip()
    if len(quote) < MIN_QUOTE:
        return None
    hits = []
    for block in blocks:
        seg = content[block['start']:block['end']]
        pos = seg.find(quote)
        while pos >= 0:
            hits.append((block['start'] + pos, block['start'] + pos + len(quote), block.get('para_id')))
            pos = seg.find(quote, pos + 1)
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        return None

    # 标点、空格、全半角不一致时退一步，用规范化串定位，再映射回原位置
    norm_quote, _ = _norm(quote)
    if len(norm_quote) < MIN_QUOTE:
        return None
    for block in blocks:
        seg = content[block['start']:block['end']]
        norm_seg, index = _norm(seg)
        pos = norm_seg.find(norm_quote)
        if pos < 0:
            continue
        if norm_seg.find(norm_quote, pos + 1) >= 0:
            return None
        start = block['start'] + index[pos]
        end = block['start'] + index[pos + len(norm_quote) - 1] + 1
        return (start, end, block.get('para_id'))
    return None


def annotate(records, documents):
    """就地补充 provenance.quotes 与 evidence_precision，返回统计。

    分母只算"本条记录确实抄了话"的那些。整个包没抽出东西时模型会提交空数组，
    这类记录没有引文可定位，把它们算进未命中会把命中率压低三成。
    """
    total = spanned = quotes_total = with_text = 0
    for record in records:
        prov = record.get('provenance') or {}
        doc = documents.get(prov.get('doc_id'))
        blocks = prov.get('blocks') or []
        quotes = []
        if doc and blocks:
            total += 1
            texts = [(f, t) for f, t in _texts_of(record) if t and len(t.strip()) >= MIN_QUOTE]
            if texts:
                with_text += 1
            for field, text in texts:
                hit = locate(text, doc['content'], blocks)
                if hit:
                    quotes.append({'field': field, 'text': text,
                                   'start': hit[0], 'end': hit[1], 'para_id': hit[2]})
        # 同一条记录里可能抄了同一句话两次，按区间去重
        seen, unique = set(), []
        for q in quotes:
            key = (q['start'], q['end'])
            if key not in seen:
                seen.add(key)
                unique.append(q)
        record['provenance'] = dict(prov, quotes=unique,
                                    evidence_precision='span' if unique else 'package')
        if unique:
            spanned += 1
            quotes_total += len(unique)
    return {'records': total, 'records_with_quote': with_text, 'span_records': spanned,
            'quotes': quotes_total,
            'span_share': round(spanned / total, 4) if total else 0.0,
            'span_share_of_quote_records': round(spanned / with_text, 4) if with_text else 0.0}
