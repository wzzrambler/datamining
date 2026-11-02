"""Lossless entity/scene advice aggregation, not an LLM semantic rewrite."""
from collections import Counter, defaultdict
import json
import re

from .common import digest

FACT_ATTRIBUTES = {'entry_restriction', 'reservation_requirement', 'limitation', 'recommended_stay'}
SCOPE_FIELDS = ('route_id', 'step_id', 'from_step_id', 'to_step_id', 'scope_status',
                'upstream_route_id', 'upstream_step_id', 'upstream_from_step_id',
                'upstream_to_step_id', 'upstream_fragment_targets', 'upstream_fragment_bindings')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def text_key(text):
    # Do not remove decimal points, logical operators, negation or internal
    # punctuation. Only whitespace and terminal punctuation are insignificant.
    return re.sub(r'\s+', '', text or '').rstrip('。！!；;')


def category(kind, force, attribute=None):
    if kind == 'incomplete_condition':
        return 'incomplete'
    if force == 'descriptive' or kind == 'conditional_fact':
        return 'conditional_facts' if kind == 'conditional_fact' else 'descriptive_information'
    if force in {'prohibited', 'avoid'}:
        return 'prohibitions_and_cautions'
    if force == 'required' or attribute in {'entry_restriction', 'reservation_requirement'}:
        return 'requirements'
    if force in {'recommended', 'optional'} or kind in {'general_advice', 'conditional_advice'} or attribute == 'recommended_stay':
        return 'recommendations'
    return 'other_information'


def topic(text, attribute=None):
    if attribute in {'entry_restriction', 'reservation_requirement'} or re.search('预约|入园|入场|门票|安检|身高|免票', text):
        return 'reservation_and_entry'
    if re.search('乘车|小火车|缆车|巴士|公交|地铁|高铁|换乘|自驾|步行|接驳', text):
        return 'transport_and_order'
    if re.search('吃|餐厅|用餐|食物|饮食', text):
        return 'food_and_dining'
    if re.search('雨|晒|天气|帽|墨镜|伞|防寒', text):
        return 'weather_and_equipment'
    if re.search('儿童|亲子|孩子|老人|老年|宝宝|娃', text):
        return 'audience'
    if re.search('点|时间|分钟|小时|排队|提前|上午|下午', text):
        return 'time_and_queue'
    return 'other'


def summarize_advice(graph, documents):
    nodes = {n['id']: n for n in graph['nodes']}
    incoming = defaultdict(list)
    for e in graph['edges']:
        if e['type'] == 'HAS_FACT':
            incoming[e['target']].append(e['source'])
    buckets, audit = {}, []
    expected_records = set()
    references = set()
    for n in sorted(graph['nodes'], key=lambda n: n['id']):
        p, nid = n['properties'], n['id']
        if n['label'] == 'CONDITIONAL_RULE':
            targets = sorted(p.get('targets', []), key=lambda x: (x['kind'], x['id']))
            scene = p['scenic_code']
            kind, force, attribute = p['statement_kind'], p['force'], None
            text = p.get('consequence_text')
        elif n['label'] == 'FACT' and (p.get('condition') or p.get('attribute') in FACT_ATTRIBUTES):
            owners = incoming[nid]
            if len(owners) != 1:
                raise ValueError('FACT must have exactly one registered owner: ' + nid)
            owner = nodes[owners[0]]
            targets = [{'kind': owner['label'], 'id': owner['id']}]
            scene = owner['properties']['scenic_code']
            kind, force, attribute = 'attribute_observation', p.get('modality', 'unspecified'), p['attribute']
            text = p['value_text']
        else:
            continue
        expected_records.add(nid)
        scope = {k: p[k] for k in SCOPE_FIELDS if p.get(k) is not None}
        quarantined = bool(p.get('scope_status'))
        for t in targets:
            if t['id'] not in nodes or nodes[t['id']]['label'] not in {'POI', 'TRANSPORT', 'TRANSPORT_LEG', 'ROUTE'}:
                raise ValueError('Unregistered advice target: ' + t['id'])
            target_scene = nodes[t['id']]['properties'].get('scenic_code')
            if target_scene is not None and target_scene != scene:
                raise ValueError('Cross-scene advice target: ' + nid)
        citations = []
        for oid in sorted(set(p.get('observation_ids', []))):
            obs = nodes.get(oid)
            if not obs or obs['label'] != 'OBSERVATION':
                raise ValueError('Unknown evidence ID: ' + oid)
            provenance = obs['properties']['provenance']
            doc_id = provenance['doc_id']
            doc = documents.get(doc_id, {})
            citations.append({'observation_id': oid, 'doc_id': doc_id,
                              'source_kind': 'official' if '-OFF-' in doc_id else 'visitor',
                              'publish_date': doc.get('publish_date') or None,
                              'evidence_precision': provenance.get('evidence_precision', 'package')})
        flags = []
        if kind == 'general_advice' and force in {'descriptive', 'required', 'prohibited', 'avoid', 'unspecified'}:
            flags.append('statement_kind_and_force_need_review')
        if len(targets) > 1:
            flags.append('joint_targets_preserved_do_not_split_action')
        if not targets:
            flags.append('target_not_located_not_scene_wide_claim')
        if quarantined:
            flags.append('excluded_fragment_not_default_recommendation')
        if kind == 'incomplete_condition':
            flags.append('missing_consequence_not_completed')
        scopes = targets or [{'kind': 'UNLOCATED', 'id': None}]
        for t in scopes:
            key = (scene, t['id'], quarantined)
            if key not in buckets:
                sid = 'ADVICE-' + digest(list(key))[:20]
                buckets[key] = {'advice_summary_id': sid, 'scenic_code': scene,
                                'target_id': t['id'], 'target_kind': t['kind'],
                                'scope_status': 'excluded_route_fragment_scope' if quarantined else None,
                                'scope_meaning': 'registered_target' if t['id'] else 'source_scene_unlocated_not_global',
                                'method': 'lossless_rule_aggregation_v1', 'semantic_merge_completed': False,
                                'current_validity_verified': False, 'items': {}, 'source_record_ids': []}
            group = buckets[key]
            identity = {'condition': p.get('condition'), 'text_key': text_key(text), 'kind': kind,
                        'force': force, 'attribute': attribute, 'targets': targets, 'scope': scope}
            item_key = canonical(identity)
            if item_key not in group['items']:
                group['items'][item_key] = {'item_id': 'ADVICEITEM-' + digest([list(key), identity])[:20],
                    'category': category(kind, force, attribute), 'topic': topic((p.get('condition') or {}).get('text', '') + (text or ''), attribute),
                    'condition': p.get('condition'), 'text': text, 'original_text_variants': [],
                    'original_statement_kind': kind, 'force': force, 'attribute': attribute,
                    'joint_targets': targets, 'scope': scope, 'source_record_ids': [],
                    'sources': [], 'review_flags': flags,
                    'semantic_conflict_checked': False}
            item = group['items'][item_key]
            if text not in item['original_text_variants']:
                item['original_text_variants'].append(text)
            item['source_record_ids'].append(nid)
            for citation in citations:
                if citation not in item['sources']:
                    item['sources'].append(citation)
            group['source_record_ids'].append(nid)
            references.add(nid)
        if quarantined:
            audit.append({'source_record_id': nid, 'reason': p['scope_status'], 'included_in_default_recommendations': False})
    summaries = []
    for group in buckets.values():
        group['items'] = sorted(group['items'].values(), key=lambda x: (x['category'], x['topic'], x['item_id']))
        group['source_record_ids'] = sorted(set(group['source_record_ids']))
        group['category_counts'] = dict(Counter(i['category'] for i in group['items']))
        group['source_document_count'] = len({s['doc_id'] for i in group['items'] for s in i['sources']})
        group['summary_item_count'] = len(group['items'])
        summaries.append(group)
    assert references == expected_records, 'Advice aggregation silently lost records'
    item_count = sum(len(g['items']) for g in summaries)
    memberships = sum(len(i['source_record_ids']) for g in summaries for i in g['items'])
    return sorted(summaries, key=lambda x: x['advice_summary_id']), {
        'input_records': len(expected_records), 'rules': sum(nodes[x]['label'] == 'CONDITIONAL_RULE' for x in expected_records),
        'attribute_records': sum(nodes[x]['label'] == 'FACT' for x in expected_records),
        'summaries': len(summaries), 'quarantined_records': audit,
        'input_target_memberships': memberships, 'summary_items': item_count,
        'exact_duplicate_memberships_merged': memberships - item_count,
        'semantic_merge_completed': False, 'llm_calls': 0,
        'limitations': ['主题及分栏是程序标签，不是语义核真', '仅合并同条件、同语气、同目标及同作用域的明确重复',
                        '尚未进行LLM同义归并或全面矛盾检测；不将多来源数量视为可信度']}
