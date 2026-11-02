"""保存活动、类型未明及省略展开候选；不冒充实体提及。"""
from .extractor import origin_index, origin_for, term_pattern, normalized


def collect_candidates(result, documents, origins, specs):
    source_index = origin_index(origins)
    candidates = {'activity_candidates': [], 'unresolved_candidates': [], 'ellipsis_candidates': []}
    for doc in documents:
        for para in doc['paragraphs']:
            text = para['text']
            for kind, terms in specs[doc['scenic_code']].items():
                for term in terms:
                    for match in term_pattern(term).finditer(normalized(text)):
                        start, end = match.span()
                        key = 'activity_candidates' if kind == 'activity' else 'unresolved_candidates'
                        candidates[key].append({'doc_id': doc['doc_id'], 'para_id': para['i'],
                                                'text': text[start:end], 'start': para['start'] + start,
                                                'end': para['start'] + end, 'status': 'candidate_only',
                                                'reason': '节目/活动不归POI' if kind == 'activity' else '类型或地点边界证据不足',
                                                'origin': origin_for(doc['doc_id'], para['i'], source_index)})
            for match in term_pattern('大小金铃海').finditer(text):
                candidates['ellipsis_candidates'].append({'doc_id': doc['doc_id'], 'para_id': para['i'],
                                                          'source_text': match.group(), 'start': para['start'] + match.start(),
                                                          'end': para['start'] + match.end(),
                                                          'suggested_names': ['大金铃海', '小金铃海'],
                                                          'status': 'not_entity_mentions',
                                                          'note': '省略展开候选，不为大金铃海伪造连续offset；不并入实体文件',
                                                          'origin': origin_for(doc['doc_id'], para['i'], source_index)})
    return candidates
