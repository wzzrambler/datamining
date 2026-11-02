"""P4 batch pipeline; deliberately has no annotation-file dependency."""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

from core import annotated_content, process_document, validate_config

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def validate_records(records, docs):
    by_id = {d['doc_id']: d for d in docs}
    ids = set()
    for r in records:
        if r['reference_id'] in ids:
            raise ValueError('重复指代ID')
        ids.add(r['reference_id'])
        text = by_id[r['doc_id']]['content']
        for s in [r['reference'], r['sentence'], r['context'], *r['candidates']]:
            if not (0 <= s['start'] < s['end'] <= len(text)) or text[s['start']:s['end']] != s['text']:
                raise ValueError('输出区间无法回切')
        ants = r.get('antecedents', []) or ([r['antecedent']] if r.get('antecedent') else [])
        if r['status'] == 'resolved':
            if not ants or (len(ants) == 1 and r['antecedent'] != ants[0]) or (len(ants) > 1 and r['antecedent'] is not None):
                raise ValueError('先行词列表与兼容标量不一致')
            for a in ants:
                if a['end'] > r['reference']['start'] or a['source_unit']['unit_id'] != r['source_unit']['unit_id'] or text[a['start']:a['end']] != a['text']:
                    raise ValueError('先行词位置或来源错误')
                if a['para_id'] != r['para_id'] and a.get('retrieval_tier') != 'previous_continuous_body_paragraph':
                    raise ValueError('跨段关联没有连续性证据')
        elif r['antecedent'] is not None or ants:
            raise ValueError('未消解项不得填确定先行词')
    return {'records': len(records), 'unique_ids': True, 'all_spans_roundtrip': True,
            'all_links_preceding_same_source_with_continuity_evidence': True,
            'semantic_accuracy': None, 'note': '结构校验，不代表人工语义准确率'}


def run(root=ROOT, config_path=BASE/'config.json'):
    root, config_path = Path(root), Path(config_path)
    inputs = {'p1': root/'processed/P1爬虫数据清洗/documents.json',
              'p2': root/'processed/P2实体识别/entities.json',
              'sources': root/'processed/P1爬虫数据清洗/辅助资料/paragraph_sources.json',
              'config': config_path}
    hashes = {k: digest(p) for k, p in inputs.items()}
    docs = json.loads(inputs['p1'].read_text('utf-8-sig'))
    p2 = json.loads(inputs['p2'].read_text('utf-8-sig'))
    source_rows = json.loads(inputs['sources'].read_text('utf-8-sig'))
    config = json.loads(config_path.read_text('utf-8-sig'))
    validate_config(config)
    if p2['config_version'] != 'v2' or p2['input_sha256'] != hashes['p1']:
        raise ValueError('P2 V2与P1输入不对应')
    if p2.get('origin_sha256') != hashes['sources']:
        raise ValueError('P2与来源映射不对应')
    edocs = {d['doc_id']: d for d in p2['documents']}
    if len(edocs) != len(p2['documents']) or len({d['doc_id'] for d in docs}) != len(docs) or set(edocs) != {d['doc_id'] for d in docs}:
        raise ValueError('P1/P2文档ID重复或集合不一致')
    sources = {(s['doc_id'], s['para_id']): s for s in source_rows}
    if len(sources) != len(source_rows):
        raise ValueError('来源映射重复段落')
    records, doc_summary, linguistic_artifacts = [], [], []
    for d in docs:
        if d['scenic_code'] != edocs[d['doc_id']]['scenic_code']:
            raise ValueError('P1/P2景区不一致')
        artifacts = {}
        rr = process_document(d, edocs[d['doc_id']], sources, config, artifacts)
        if artifacts:
            linguistic_artifacts.append(artifacts)
        records.extend(rr)
        doc_summary.append({'doc_id': d['doc_id'], 'scenic_code': d['scenic_code'],
                            'content_sha256': hashlib.sha256(d['content'].encode('utf-8')).hexdigest(),
                            'records': len(rr), 'status_counts': dict(Counter(r['status'] for r in rr))})
    validation = validate_records(records, docs)
    summary = {'documents_scanned': len(docs), 'records': len(records),
               'status_counts': dict(Counter(r['status'] for r in records)),
               'reason_counts': dict(Counter(r['reason_code'] for r in records)),
               'reference_type_counts': dict(Counter(r['reference']['type'] for r in records)),
               'baseline_links_on_detected_targets': sum(r['baseline_result']['status'] == 'resolved' for r in records if r['status'] != 'excluded'),
               'parameter_status': config['parameter_status'], 'independent_semantic_accuracy': None,
               'uses_annotations_for_prediction': False, 'documents': doc_summary}
    out, display = root/'processed/P4共指消解', root/'outputs/P4'
    dump(out/'coreferences.json', {'schema_version': 'p4-coreferences-2' if config.get('v2', {}).get('enabled') else 'p4-coreferences-1', 'input_sha256': hashes,
                                  'implementation_sha256': {p.name: digest(p) for p in sorted(BASE.glob('*.py'))},
                                  'config': config, 'records': records})
    if linguistic_artifacts:
        dump(out/'tokenization.json', {'schema_version': 'p4-tokenization-1', 'input_sha256': hashes,
                                       'documents': linguistic_artifacts})
    dump(out/'summary.json', summary)
    dump(out/'validation.json', validation)
    display.mkdir(parents=True, exist_ok=True)
    fields = ['reference_id', 'doc_id', 'scenic_code', 'para_id', 'source_kind', 'source_unit',
              'reference_start', 'reference_end', 'reference_text', 'reference_type', 'sentence_text',
              'status', 'reason_code', 'antecedent_start', 'antecedent_end', 'antecedent_text',
              'antecedent_type', 'antecedents_json', 'entity_id', 'score', 'runner_up_score', 'baseline_antecedent_text']
    with (display/'P4自动消解检查表.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fields)
        writer.writeheader()
        for r in records:
            a, b = r['antecedent'] or {}, r['baseline_result']['antecedent'] or {}
            row = {k: r.get(k) for k in ['reference_id', 'doc_id', 'scenic_code', 'para_id', 'status', 'reason_code', 'score', 'runner_up_score']}
            row.update(source_kind=r['source_unit']['kind'], source_unit=r['source_unit']['unit_id'],
                       sentence_text=r['sentence']['text'], entity_id=a.get('entity_id'),
                       baseline_antecedent_text=b.get('text'))
            row.update({f'reference_{k}': r['reference'][k] for k in ['start', 'end', 'text', 'type']})
            row.update({f'antecedent_{k}': a.get(k) for k in ['start', 'end', 'text']})
            row['antecedent_type'] = a.get('semantic_type')
            row['antecedents_json'] = json.dumps(r.get('antecedents', []) or ([a] if a else []), ensure_ascii=False)
            writer.writerow(row)
    inspection = []
    for d in docs:
        rr = [r for r in records if r['doc_id'] == d['doc_id']]
        inspection.append({'doc_id': d['doc_id'], 'original_content_sha256': hashlib.sha256(d['content'].encode()).hexdigest(),
                           'inspection_only': True, 'annotated_content': annotated_content(d['content'], rr)})
    dump(display/'P4消解文本_仅供检查.json', inspection)
    lines = ['# P4自动消解运行结果', '', '规则消解；V2使用独立Jieba词性与P2词典补充候选，没有读取人工答案。以下是自动运行统计，不是人工准确率。', '',
             f"扫描{len(docs)}篇，检测记录{len(records)}条（含排除项）。", '',
             '|状态|数量|', '|---|---|']
    lines.extend(f'|{k}|{v}|' for k, v in summary['status_counts'].items())
    lines += ['', '实体窗口及事件独立结构上限见coreferences.json的config；跨段仅允许有连续性依据的同来源正文前一段。',
              '实体和事件分通道排序；支持可定位事件及明确双路线集合，未消解保留原因；未进行独立留出验证。',
              'P6读取coreferences.json中的resolved关联及原文位置；检查文本不作为原文替代。',
              '人工标注校验与评估使用独立evaluate.py入口，未复核标注不计入准确率。', '']
    (display/'P4自动结果说明.md').write_text('\n'.join(lines), encoding='utf-8')
    # Ensure no input mutated during the run, including concurrent edits.
    if any(digest(p) != hashes[k] for k, p in inputs.items()):
        raise ValueError('运行期间输入发生变化，请重跑以确保快照一致')
    print(json.dumps({k: v for k, v in summary.items() if k != 'documents'}, ensure_ascii=False, indent=2))
    return summary
