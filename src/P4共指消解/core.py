"""Deterministic antecedent rules. No annotation input, models or tokenizers."""
import hashlib
import re
from collections import Counter


def stable_id(prefix, *parts):
    return prefix + hashlib.sha256('|'.join(map(str, parts)).encode('utf-8')).hexdigest()[:16]


def validate_config(config):
    options = config.get('v2', {})
    if options.get('enabled'):
        if options['event_max_chars'] <= 0 or options['event_previous_blocks'] <= 0:
            raise ValueError('事件结构窗口和块数必须为正')
        if not isinstance(options['noun_pos'], list) or not options['noun_pos']:
            raise ValueError('必须提供明确的名词词性白名单')
    if config['window_chars'] <= 0 or config['previous_sentences'] < 0:
        raise ValueError('窗口必须为正，回看句数不能为负')
    if config.get('same_paragraph') is not True and not config.get('v2', {}).get('enabled'):
        raise ValueError('首版只支持同段候选；不能静默扩展跨段承接')
    w = config['weights']
    if set(w) != {'type', 'distance', 'sentence'} or any(v < 0 for v in w.values()) or abs(sum(w.values()) - 1) > 1e-9:
        raise ValueError('评分权重必须非负且总和为1')
    if not 0 <= config['accept_threshold'] <= 1 or not 0 <= config['min_margin'] <= 1:
        raise ValueError('阈值与分差应位于0到1')


def source_for(doc_id, para_id, sources):
    row = sources.get((doc_id, para_id))
    if row is None:
        return {'kind': 'legacy_or_unmapped', 'source': None,
                'unit_id': stable_id('U-', doc_id, 'legacy_or_unmapped')}
    kind, src = row['kind'], row.get('source')
    # Missing identifiers must not merge different image/comment units.
    key = src if src is not None else ('paragraph', para_id)
    return {'kind': kind, 'source': src, 'unit_id': stable_id('U-', doc_id, kind, key)}


def sentences(paragraph, source_unit):
    """Keep original spans, including punctuation; do not normalize the text."""
    result = []
    for m in re.finditer(r'[^。！？!?；;\n]+(?:[。！？!?；;]+|\n|$)|[。！？!?；;\n]+', paragraph['text']):
        a, b = paragraph['start'] + m.start(), paragraph['start'] + m.end()
        result.append({'start': a, 'end': b, 'text': m.group(),
                       'sentence_id': stable_id('S-', source_unit['unit_id'], paragraph['i'], a, b),
                       'index': len(result)})
    return result


EXPRESSION = re.compile('|'.join(sorted([
    '它们', '他们', '她们', '这里', '那里', '这儿', '那儿', '这边', '那边', '此处',
    '该景点', '该园区', '该景区', '该设施', '该项目', '这个', '那个', '这些', '那些',
    '这种', '那种', '这条', '那条', '这座', '那座', '这只', '那只', '它', '他', '她', '这', '那'
], key=lambda x: (-len(x), x))))


def reference_kind(expression, suffix):
    if expression in {'这里', '那里', '这儿', '那儿', '这边', '那边', '此处', '该景点', '该园区', '该景区', '这座', '那座'}:
        return 'location'
    if expression in {'他', '她', '他们', '她们'}:
        return 'person'
    if expression in {'它', '它们'}:
        return 'nonperson'
    if expression in {'这只', '那只'}:
        return 'animal'
    if expression in {'该设施', '该项目'}:
        return 'object'
    if re.match(r'(?:个|座)?(?:景点|景区|园区|地方|区域)', suffix):
        return 'location'
    if re.match(r'(?:个)?(?:设施|缆车|小火车|交通工具)', suffix):
        return 'object'
    if re.match(r'(?:个)?(?:人|游客|工作人员)', suffix):
        return 'person'
    return 'unknown'


def detect(doc, paragraph, ss, source_unit):
    text = doc['content']
    records = []
    for m in EXPRESSION.finditer(paragraph['text']):
        a, b = paragraph['start'] + m.start(), paragraph['start'] + m.end()
        expr = m.group()
        sent = next(s for s in ss if s['start'] <= a < s['end'])
        suffix = text[b: min(b + 20, paragraph['end'])]
        excluded = None
        if expr in {'他', '它'} and a > 0 and text[a - 1] == '其':
            excluded = 'lexical_non_reference'
        if expr in {'这', '那'} and re.match(r'[次天年月时样么]', suffix):
            excluded = 'lexical_non_reference'
        discourse = bool(re.match(r'(?:感谢|重点(?:提醒|提示)|提醒(?:大家|一下)|说明一下|强调)', suffix)) and expr in {'这里', '这儿', '此处'}
        kind = reference_kind(expr, suffix)
        event = expr in {'这种', '那种', '这条', '那条', '这个', '那个', '这', '那'} and bool(re.match(r'(?:个|条|种)?(?:路线|行程|安排|情况|方式|方法|建议|攻略)', suffix))
        ctx_a, ctx_b = max(paragraph['start'], a - 60), min(paragraph['end'], b + 60)
        records.append({
            'reference_id': stable_id('R-', doc['doc_id'], hashlib.sha256(text.encode()).hexdigest(), a, b),
            'doc_id': doc['doc_id'], 'scenic_code': doc['scenic_code'], 'title': doc.get('title', ''),
            'para_id': paragraph['i'], 'source_unit': source_unit,
            'sentence_id': sent['sentence_id'], 'sentence_index': sent['index'],
            'reference': {'start': a, 'end': b, 'text': expr, 'type': kind},
            'sentence': {k: sent[k] for k in ('start', 'end', 'text')},
            'context': {'start': ctx_a, 'end': ctx_b, 'text': text[ctx_a:ctx_b]},
            'detection_exclusion': excluded,
            'non_entity_rule': 'discourse_expression' if discourse else ('event_reference' if event else None),
        })
    return records


def p2_semantic(m):
    if m['type'] == 'TRANSPORT':
        return 'transport'
    if m['type'] == 'POI':
        if m.get('subtype') in {'ride', 'station', 'gate', 'restaurant', 'hotel', 'parking', 'shop', 'service_center', 'venue'}:
            return 'facility'
        return 'location'
    return None


def build_mentions(doc, p2_doc, config, sources):
    """Generate local noun occurrences; not a general-purpose noun tagger."""
    text = doc['content']
    pool = {}

    def add(a, b, typ, method, rule, p2=None):
        para = next((p for p in doc['paragraphs'] if p['start'] <= a and b <= p['end']), None)
        if para is None:
            return
        key = (a, b, typ)
        evidence = {'method': method, 'rule_id': rule}
        if key in pool:
            pool[key]['evidence'].append(evidence)
            return
        pool[key] = {'candidate_id': stable_id('N-', doc['doc_id'], a, b, typ),
                     'start': a, 'end': b, 'text': text[a:b], 'semantic_type': typ,
                     'para_id': para['i'], 'source_unit': source_for(doc['doc_id'], para['i'], sources),
                     'p2_mention_id': p2.get('mention_id') if p2 else None,
                     'entity_id': p2.get('entity_id') or None if p2 else None,
                     'canonical_name': p2.get('canonical_name') or None if p2 else None,
                     'p2_type': p2.get('type') if p2 else None,
                     'poi_role': p2.get('poi_role') if p2 else None,
                     'subtype': p2.get('subtype') if p2 else None,
                     'evidence': [evidence]}

    for m in p2_doc.get('mentions', []):
        if not (0 <= m['start'] < m['end'] <= len(text)) or text[m['start']:m['end']] != m['text']:
            raise ValueError(f"P2偏移不匹配: {doc['doc_id']} {m.get('mention_id')}")
        typ = p2_semantic(m)
        if typ:
            add(m['start'], m['end'], typ, 'p2', 'P2_ENTITY', m)
    p2_spans = [(c['start'], c['end']) for c in pool.values()]
    for typ, words in config['nouns'].items():
        for word in words:
            for match in re.finditer(re.escape(word), text):
                a, b = match.span()
                # Animal names inside a named venue are not animal referents.
                if any(x <= a and b <= y for x, y in p2_spans):
                    continue
                add(a, b, typ, 'lexicon', 'NOUN_LEXICON')
                for modifier in config['compound_modifiers']:
                    if text[max(0, a-len(modifier)):a] == modifier:
                        add(a-len(modifier), b, typ, 'compound', 'MODIFIER_HEAD')
    # Join a known noun to an adjacent, explicitly allowed head only once.
    for base in list(pool.values()):
        if base['semantic_type'] not in {'animal', 'location'}:
            continue
        for typ, heads in [('location', config['compound_location_heads']), ('facility', config['compound_facility_heads'])]:
            for head in heads:
                if text.startswith(head, base['end']):
                    add(base['start'], base['end']+len(head), typ, 'compound', 'KNOWN_NOUN_HEAD')
    # Avoid treating a shorter lexical head inside a longer coherent mention as a separate object.
    items = list(pool.values())
    return sorted([c for c in items if c['p2_mention_id'] or not any(
        d['start'] <= c['start'] and c['end'] <= d['end'] and
        (d['end']-d['start']) > (c['end']-c['start']) and
        (d['p2_mention_id'] or d['semantic_type'] == c['semantic_type']) for d in items
    )], key=lambda c: (c['start'], c['end'], c['candidate_id']))


def suitability(record, candidate, text):
    kind, typ = record['reference']['type'], candidate['semantic_type']
    r = record['reference']
    after = text[r['end']:min(record['sentence']['end'], r['end']+24)]
    # A predicate naming a place constrains otherwise generic "it" references.
    if kind == 'nonperson' and re.match(r'(?:也|就|正)?是[^。！？!?；;]{0,16}(?:园区|景区|景点|场馆|地方)', after):
        kind = 'location'
    if kind == 'location':
        if typ not in {'location', 'facility'}:
            return None, 'type_mismatch'
        precise = candidate['p2_mention_id'] or any(e['method'] == 'compound' for e in candidate['evidence'])
        return (1 if precise else 0.5), 'LOCATION_MATCH'
    if kind == 'person':
        if typ == 'person':
            return 1, 'PERSON_MATCH'
        if typ == 'animal' and re.search(r'聪明|饲养|喂|动物|毛笔|踢球|撒娇', after):
            return 1, 'ANIMAL_PERSONIFICATION'
        return None, 'type_mismatch'
    if kind == 'animal':
        return (1, 'ANIMAL_MATCH') if typ == 'animal' else (None, 'type_mismatch')
    if kind in {'nonperson', 'object'}:
        if typ not in {'animal', 'facility', 'transport'}:
            return None, 'type_mismatch'
        if re.search(r'乘坐|换乘|上车|下车', after) and typ == 'animal':
            return None, 'predicate_mismatch'
        return (1 if typ == 'animal' or kind == 'object' else 0.5), 'OBJECT_MATCH'
    return None, 'type_unknown'


def resolve(record, pool, doc, ss, config):
    text, r = doc['content'], record['reference']
    current = record['sentence_index']
    prior = ss[max(0, current-config['previous_sentences'])]['start']
    lower = max(r['start']-config['window_chars'], prior)
    candidates, blocked, blocked_details = [], Counter(), []
    for c in pool:
        if c['end'] > r['start'] or c['end'] < r['start']-config['window_chars']:
            continue
        why = None
        if c['source_unit']['unit_id'] != record['source_unit']['unit_id']:
            why = 'source_boundary'
        elif c['para_id'] != record['para_id']:
            why = 'paragraph_boundary'
        elif c['start'] < lower:
            why = 'window_or_sentence_boundary'
        if why:
            blocked[why] += 1
            blocked_details.append({k: c[k] for k in ['candidate_id', 'start', 'end', 'text', 'para_id', 'source_unit']})
            blocked_details[-1]['reason_code'] = why
            continue
        cand = dict(c)
        cand['distance'] = r['start']-c['end']
        cand['sentence_index'] = next(s['index'] for s in ss if s['start'] <= c['start'] < s['end'])
        candidates.append(cand)
    baseline_candidates = [c for c in candidates if c['p2_mention_id']]
    nearest = max(baseline_candidates, key=lambda c: (c['end'], c['start']), default=None)
    record['baseline_result'] = {'status': 'resolved' if nearest else 'unresolved', 'antecedent': nearest}
    record['candidates'], record['blocked_candidate_counts'] = candidates, dict(blocked)
    record['blocked_candidates'] = blocked_details
    record.update(antecedent=None, status='unresolved', reason_code='no_candidate', score=None, runner_up_score=None, rule_ids=[])
    if record['detection_exclusion']:
        record.update(status='excluded', reason_code=record['detection_exclusion'])
        return record
    if record['non_entity_rule']:
        record.update(status='non_entity_reference', reason_code=record['non_entity_rule'], rule_ids=[record['non_entity_rule']])
        return record
    if r['type'] == 'unknown':
        record['reason_code'] = 'type_unknown'
        return record
    ranked = []
    for c in candidates:
        fit, rule = suitability(record, c, text)
        c['type_rule'], c['eligible'] = rule, fit is not None
        if fit is None:
            continue
        # No number inflection inference: require plural/group evidence for plural references.
        if r['text'] in {'他们', '她们', '它们'}:
            group = re.search(r'动物|游客|工作人员|朋友|孩子|大家|鸟类|人群|群|们', c['text'])
            surround = text[max(lower, c['start']-5):c['start']]
            generic_animal = c['semantic_type'] == 'animal' and '一头' not in surround and '一只' not in surround
            personified = rule == 'ANIMAL_PERSONIFICATION'
            if not (group or generic_animal or personified or re.search(r'一群|几只|多只|多头|几位|多位', surround)):
                c.update(eligible=False, type_rule='collective_unsupported')
                continue
        gap = current-c['sentence_index']
        locality = 1 if gap == 0 else (0.5 if gap == 1 else 0)
        dist = max(0, 1-c['distance']/config['window_chars'])
        parts = {'type': fit, 'distance': dist, 'sentence': locality}
        c['score_parts'] = parts
        c['score'] = sum(config['weights'][k]*v for k, v in parts.items())
        # Only confirmed entity identity can merge different mentions.
        c['object_key'] = ('entity:'+doc['scenic_code']+':'+c['entity_id']) if c['entity_id'] else c['candidate_id']
        ranked.append(c)
    objects = {}
    for c in ranked:
        key = c['object_key']
        if key not in objects or c['end'] > objects[key]['end']:
            objects[key] = c
    ranked = sorted(objects.values(), key=lambda c: (-c['score'], c['candidate_id']))
    record['ranked_candidate_ids'] = [c['candidate_id'] for c in ranked]
    if not ranked:
        record['reason_code'] = 'collective_unsupported' if any(c.get('type_rule') == 'collective_unsupported' for c in candidates) else ('type_mismatch' if candidates else 'no_candidate')
        return record
    first = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    record.update(score=first['score'], runner_up_score=second['score'] if second else None)
    if first['score'] < config['accept_threshold']:
        record['reason_code'] = 'low_score'
    elif second and first['score']-second['score'] < config['min_margin']:
        record['reason_code'] = 'ambiguous'
    else:
        record.update(status='resolved', reason_code='type_distance_rule', antecedent=dict(first),
                      rule_ids=[first['type_rule'], *[x['rule_id'] for x in first['evidence']]])
    return record


def process_document(doc, p2_doc, sources, config, artifacts=None):
    validate_config(config)
    if config.get("v2", {}).get("enabled"):
        import sys
        from pathlib import Path
        base = str(Path(__file__).resolve().parent)
        if base not in sys.path:
            sys.path.insert(0, base)
        import v2
        return v2.run(doc, p2_doc, sources, config, sys.modules[__name__] if __name__ in sys.modules else __import__("core"), artifacts)
    pool = build_mentions(doc, p2_doc, config, sources)
    records = []
    for p in doc['paragraphs']:
        if doc['content'][p['start']:p['end']] != p['text']:
            raise ValueError('P1段落偏移不一致')
        unit = source_for(doc['doc_id'], p['i'], sources)
        ss = sentences(p, unit)
        for ref in detect(doc, p, ss, unit):
            records.append(resolve(ref, pool, doc, ss, config))
    return records


def annotated_content(content, records):
    result = content
    for r in sorted(records, key=lambda x: x['reference']['start'], reverse=True):
        if r['status'] == 'resolved':
            ref = r['reference']
            ants = r.get('antecedents', []) or [r['antecedent']]
            label = '〔先行词：' + '；'.join(f"{ant['text']}；{ant['start']}:{ant['end']}" for ant in ants) + '〕'
            result = result[:ref['end']] + label + result[ref['end']:]
    return result
