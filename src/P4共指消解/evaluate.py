"""Read-only annotation validation and confirmed-only evaluation.

This module is never imported by the prediction pipeline.
"""
import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
TYPES = {'person', 'animal', 'location', 'facility', 'transport', 'event', 'discourse', 'other'}
STATUSES = {'antecedent_found', 'non_entity_reference', 'no_clear_antecedent', 'unjudgeable'}
CONFIRMED = {'confirmed', 'user_confirmed'}
REQUIRED = {'annotation_id', 'doc_id', 'scenic_code', 'para_id', 'sample_origin', 'reference_start',
            'reference_end', 'reference_text', 'sentence_start', 'sentence_end', 'sentence_text',
            'context_start', 'context_end', 'context_text', 'is_target', 'gold_status',
            'antecedent_start', 'antecedent_end', 'antecedent_text', 'antecedent_type',
            'antecedents_json', 'review_status'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def span(row, prefix, content, optional=False):
    vals = [row.get(prefix+'_'+k, '') for k in ['start', 'end', 'text']]
    if optional and not any(vals):
        return None
    if not all(vals):
        raise ValueError(prefix+'区间字段不完整')
    a, b = int(vals[0]), int(vals[1])
    if not 0 <= a < b <= len(content) or content[a:b] != vals[2]:
        raise ValueError(prefix+'区间无法回切原文')
    return {'start': a, 'end': b, 'text': vals[2]}


def load_annotations(sample_path, csv_path, p1_path):
    """Return parsed rows plus diagnostics without changing either input file."""
    hashes = {str(p): digest(p) for p in [sample_path, csv_path, p1_path]}
    snapshot = json.loads(Path(sample_path).read_text('utf-8-sig'))
    p1_list = json.loads(Path(p1_path).read_text('utf-8-sig'))
    p1 = {d['doc_id']: d for d in p1_list}
    docs = {d['doc_id']: d for d in snapshot['documents']}
    errors, warnings = [], []
    if len(docs) != len(snapshot['documents']):
        errors.append('样本文档ID重复')
    if snapshot.get('source_sha256') != digest(p1_path):
        errors.append('样本JSON的P1文件哈希不匹配')
    for key, d in docs.items():
        if key not in p1 or d['content'] != p1[key]['content'] or d.get('scenic_code') != p1[key]['scenic_code']:
            errors.append(key+': 样本正文/景区与P1不一致')
        if d.get('content_sha256') != hashlib.sha256(d['content'].encode('utf-8')).hexdigest():
            errors.append(key+': 逐篇正文哈希错误或缺失')
    with Path(csv_path).open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        missing = REQUIRED-set(reader.fieldnames or [])
        if missing:
            errors.append('缺少字段: '+','.join(sorted(missing)))
        rows = list(reader)
    ids, locations, parsed = set(), set(), []
    for row in rows:
        label = row.get('annotation_id', '(missing)')
        try:
            if None in row or any(v is None for v in row.values()):
                raise ValueError('CSV列数不一致')
            if not label or label in ids:
                raise ValueError('标注ID缺失或重复')
            ids.add(label)
            if row['doc_id'] not in docs:
                raise ValueError('doc_id不在样本JSON中')
            doc = docs[row['doc_id']]
            content = doc['content']
            if row['scenic_code'] != doc['scenic_code']:
                raise ValueError('景区不一致')
            ref = span(row, 'reference', content)
            sentence, context = span(row, 'sentence', content), span(row, 'context', content)
            for s in [sentence, context]:
                if not s['start'] <= ref['start'] < ref['end'] <= s['end']:
                    raise ValueError('句子/上下文不包含指代区间')
            para = next((p for p in doc['paragraphs'] if p.get('para_id', p.get('i')) == int(row['para_id'])), None)
            if para is None or not para['start'] <= ref['start'] < ref['end'] <= para['end']:
                raise ValueError('段落与指代位置不一致')
            key = row['doc_id'], ref['start'], ref['end']
            if key in locations:
                raise ValueError('同一指代位置重复标注')
            locations.add(key)
            target, status = row['is_target'], row['gold_status']
            if target not in {'yes', 'no', 'uncertain'}:
                raise ValueError('is_target非法')
            if (target == 'no' and status) or (target == 'yes' and status not in STATUSES):
                raise ValueError('is_target与gold_status不一致')
            if target == 'uncertain' and status and status not in STATUSES:
                raise ValueError('gold_status非法')
            if row['sample_origin'] not in {'auto_detected', 'manual_added'}:
                raise ValueError('sample_origin非法')
            one = span(row, 'antecedent', content, optional=True)
            multiple = json.loads(row['antecedents_json']) if row['antecedents_json'].strip() else []
            if not isinstance(multiple, list) or (multiple and len(multiple) < 2):
                raise ValueError('多先行词字段必须为至少两项的数组，或留空')
            if multiple and (one or row['antecedent_type']):
                raise ValueError('多个先行词与标量字段不互斥')
            if row['antecedent_type'] and row['antecedent_type'] not in TYPES:
                raise ValueError('antecedent_type非法')
            ants = []
            if one:
                if row['antecedent_type'] not in TYPES:
                    raise ValueError('先行词缺少类型')
                ants.append({**one, 'type': row['antecedent_type']})
            for a in multiple:
                if not isinstance(a, dict) or not {'start', 'end', 'text', 'type'} <= a.keys():
                    raise ValueError('多先行词结构不完整')
                if type(a['start']) is not int or type(a['end']) is not int or not 0 <= a['start'] < a['end'] <= len(content):
                    raise ValueError('多先行词偏移非法')
                if content[a['start']:a['end']] != a['text'] or a['type'] not in TYPES:
                    raise ValueError('多先行词回切/类型错误')
                ants.append(a)
            if len({(a['start'], a['end']) for a in ants}) != len(ants):
                raise ValueError('先行词区间重复')
            if status == 'antecedent_found' and not ants:
                raise ValueError('找到先行词但未提供区间')
            if status in {'no_clear_antecedent', 'unjudgeable'} and (ants or row['antecedent_type']):
                raise ValueError('无明确答案状态不应填确定先行词')
            if target == 'no' and (ants or row['antecedent_type']):
                raise ValueError('排除项不应填写先行词')
            # Gold can be outside the algorithm window or follow the reference (cataphora).
            if not row.get('source_unit'):
                warnings.append(label+': 无source_unit列/值，可从P1来源映射派生，未伪造')
            if not row.get('sentence_id'):
                warnings.append(label+': 无sentence_id，评估以文档和全文区间定位')
            parsed.append({**row, '_reference': ref, '_antecedents': ants})
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f'{label}: {exc}')
    report = {'schema_version': 'p4-annotation-check-1', 'input_sha256': hashes, 'rows': len(rows),
              'valid_rows': len(parsed), 'errors': errors, 'warnings': warnings,
              'review_counts': dict(Counter(r.get('review_status', '') for r in rows)),
              'confirmed_rows': sum(r.get('review_status') in CONFIRMED for r in parsed),
              'source_files_unchanged': all(digest(p) == h for p, h in hashes.items())}
    return parsed, report, docs


def ratio(a, b):
    return a/b if b else None


def evaluate_rows(rows, predictions, selected_docs=None, method='rules'):
    """Strict mention-span evaluation, including missed manually-added targets."""
    eligible = [r for r in rows if r['review_status'] in CONFIRMED and r['is_target'] != 'uncertain'
                and r['gold_status'] != 'unjudgeable' and (selected_docs is None or r['doc_id'] in selected_docs)]
    pred = {(p['doc_id'], p['reference']['start'], p['reference']['end']): p for p in predictions}
    counts = Counter({k: 0 for k in ['rows', 'targets', 'detected_targets', 'detected_exclusions',
                                   'emitted_links', 'target_emitted_links', 'correct_links',
                                   'single_antecedent_targets', 'multi_antecedent_targets', 'correct_single_links', 'correct_multi_links', 'multi_candidate_hits', 'candidate_hits', 'correct_status']})
    typed_counts = {}
    details = []
    keys = set()
    for r in eligible:
        key = r['doc_id'], r['_reference']['start'], r['_reference']['end']
        keys.add(key)
        p = pred.get(key)
        if method == 'baseline' and p:
            p = {**p, **p['baseline_result']}
            if pred[key]['status'] == 'excluded':
                p.update(status='excluded', antecedent=None)
        target = r['is_target'] == 'yes'
        detected = bool(p and p['status'] != 'excluded')
        link = p.get('antecedent') if p and p['status'] == 'resolved' else None
        links = ((p.get('antecedents', []) or ([link] if link else [])) if method == 'rules' else ([link] if link else [])) if p and p['status'] == 'resolved' else []
        ants = r['_antecedents']
        correct = bool(target and r['gold_status'] == 'antecedent_found' and links
                       and {(a['start'], a['end']) for a in links} == {(a['start'], a['end']) for a in ants})
        counts['correct_single_links'] += correct and len(ants) == 1
        counts['correct_multi_links'] += correct and len(ants) > 1
        counts['rows'] += 1
        counts['targets'] += target
        counts['detected_targets'] += target and detected
        counts['detected_exclusions'] += not target and detected
        counts['emitted_links'] += bool(links)
        counts['target_emitted_links'] += bool(target and links)
        counts['correct_links'] += correct
        single = target and r['gold_status'] == 'antecedent_found' and len(ants) == 1
        counts['single_antecedent_targets'] += single
        counts['multi_antecedent_targets'] += target and len(ants) > 1
        candidates = (p.get('candidates', []) if method == 'rules' else
                      [c for c in p.get('candidates', []) if c.get('p2_mention_id')]) if p else []
        candidate_hit = bool(single and any((c['start'], c['end']) == (ants[0]['start'], ants[0]['end']) for c in candidates))
        counts['candidate_hits'] += candidate_hit
        counts['multi_candidate_hits'] += bool(target and len(ants)>1 and all(any((c['start'],c['end'])==(a['start'],a['end']) for c in candidates) for a in ants))
        if single:
            by_type = typed_counts.setdefault(ants[0]['type'], Counter(targets=0, correct_links=0, candidate_hits=0))
            by_type['targets'] += 1
            by_type['correct_links'] += correct
            by_type['candidate_hits'] += candidate_hit
        expected = 'excluded' if not target else {'antecedent_found': 'resolved', 'non_entity_reference': 'non_entity_reference', 'no_clear_antecedent': 'unresolved'}[r['gold_status']]
        counts['correct_status'] += (bool(p and p['status'] == expected) if target else not detected)
        reason = ('correct' if correct else ('not_detected' if target and not detected else
                  ('candidate_missing' if single and not candidate_hit else p.get('reason_code', 'no_prediction') if p else 'no_prediction')))
        details.append({'annotation_id': r['annotation_id'], 'doc_id': r['doc_id'], 'reference': r['_reference'],
                        'gold_status': r['gold_status'], 'gold_antecedents': ants,
                        'predicted_status': p['status'] if p else 'not_detected',
                        'predicted_antecedent': link, 'predicted_antecedents': links, 'correct_link': correct, 'diagnosis': reason})
    boundary_only = []
    for r in eligible:
        a = r['_reference']
        key = r['doc_id'], a['start'], a['end']
        if key not in pred:
            overlaps = [p['reference'] for p in predictions if p['doc_id'] == r['doc_id'] and p['reference']['start'] < a['end'] and a['start'] < p['reference']['end']]
            if overlaps:
                boundary_only.append({'annotation_id': r['annotation_id'], 'gold': a, 'overlapping_predictions': overlaps})
    evaluated_docs = {r['doc_id'] for r in eligible}
    unmatched = sum((p['doc_id'], p['reference']['start'], p['reference']['end']) not in keys and p['status'] != 'excluded'
                    for p in predictions if p['doc_id'] in evaluated_docs)
    return {'method': method, 'boundary_only_diagnostics_not_strict_credit': boundary_only, 'counts': dict(counts), 'metrics': {
        'annotation_aligned_link_precision': ratio(counts['correct_links'], counts['emitted_links']),
        'annotation_aligned_link_coverage': ratio(counts['target_emitted_links'], counts['targets']),
        'single_antecedent_success_rate': ratio(counts['correct_single_links'], counts['single_antecedent_targets']),
        'multi_antecedent_success_rate': ratio(counts['correct_multi_links'], counts['multi_antecedent_targets']),
        'candidate_recall_multi_antecedent': ratio(counts['multi_candidate_hits'], counts['multi_antecedent_targets']),
        'candidate_recall_single_antecedent': ratio(counts['candidate_hits'], counts['single_antecedent_targets']),
        'detection_recall_on_annotated_targets': ratio(counts['detected_targets'], counts['targets']),
        'status_accuracy': ratio(counts['correct_status'], counts['rows'])},
        'single_antecedent_by_gold_type': {k: {**dict(c), 'success_rate': ratio(c['correct_links'], c['targets']),
                                              'candidate_recall': ratio(c['candidate_hits'], c['targets'])} for k, c in typed_counts.items()},
        'unmatched_detected_spans_in_evaluated_documents': unmatched,
        'metric_scope': '仅已确认可判定标注行；未对齐的自动命中不擅自判错，现有标注未覆盖或边界不同，无法判定；非全文准确率',
        'details': details}


def run_evaluation(sample_path, csv_path, p1_path, prediction_path, output_dir, split_path=None):
    rows, validation, docs = load_annotations(sample_path, csv_path, p1_path)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out/'annotation_validation.json').write_text(json.dumps(validation, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    if validation['errors'] or not validation['source_files_unchanged']:
        (out/'evaluation.json').write_text(json.dumps({'status': 'blocked_invalid_annotations', 'validation': validation}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        raise ValueError('人工标注格式/版本校验失败，详见annotation_validation.json')
    payload = json.loads(Path(prediction_path).read_text('utf-8-sig'))
    if payload['input_sha256']['p1'] != digest(p1_path):
        raise ValueError('预测与人工原文版本不同')
    selected = None
    mode = 'confirmed_sample_check_not_independent'
    split_hash = None
    if split_path:
        split = json.loads(Path(split_path).read_text('utf-8-sig'))
        if not split or any(k not in docs or v not in {'development', 'evaluation'} for k, v in split.items()):
            raise ValueError('划分文件需为样本文档ID到development/evaluation的映射')
        selected = {k for k, v in split.items() if v == 'evaluation'}
        if not selected:
            raise ValueError('划分文件没有evaluation文档')
        mode = 'declared_document_split_not_proof_of_independence'
        split_hash = digest(split_path)
    report = {'schema_version': 'p4-evaluation-1', 'mode': mode,
              'review_requirement': '只接受review_status=confirmed或user_confirmed；依据文件声明，不自动修改状态',
              'annotation_sha256': digest(csv_path), 'prediction_sha256': digest(prediction_path),
              'split_sha256': split_hash, 'validation': validation,
              'rules': evaluate_rows(rows, payload['records'], selected),
              'baseline': evaluate_rows(rows, payload['records'], selected, 'baseline')}
    report['status'] = 'evaluated_confirmed_rows' if report['rules']['counts'].get('rows', 0) else 'awaiting_confirmed_annotations'
    if not all(digest(p) == h for p, h in validation['input_sha256'].items()):
        raise ValueError('标注或原文在评估期间发生变化，请基于最新完整快照重跑')
    (out/'evaluation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    counts = report['rules']['counts']
    lines = ['# P4样本核验', '', '仅评估文件中声明为confirmed/user_confirmed的可判定行；未改写人工标注。',
             '这些样本在项目开发中已被接触，当前没有独立留出的测试集，不宣称独立泛化准确率。', '',
             f"状态：{report['status']}；纳入{counts['rows']}行，其中目标指代{counts['targets']}行。", '',
             '|指标|规则方法|最近实体基线|', '|---|---|---|']
    labels = {'annotation_aligned_link_precision': '已对齐标注行的关联正确率',
              'annotation_aligned_link_coverage': '目标指代自动关联覆盖率',
              'single_antecedent_success_rate': '单先行词成功率',
              'candidate_recall_single_antecedent': '单先行词候选召回率',
              'detection_recall_on_annotated_targets': '标注目标严格区间检测召回率',
              'status_accuracy': '状态判断正确率'}
    for key, label in labels.items():
        values = [report[m]['metrics'][key] for m in ['rules', 'baseline']]
        cells = ['不适用' if v is None else f'{v:.1%}' for v in values]
        lines.append(f'|{label}|{cells[0]}|{cells[1]}|')
    lines += ['', f"规则输出{counts['emitted_links']}条关联，其中{counts['correct_links']}条严格区间匹配正确。",
              '正确率分母仅为已对齐标注行的自动关联，不能推广为全部文本准确率；人工无法判断、未复核项不纳入。',
              f"评估文档内另有{report['rules']['unmatched_detected_spans_in_evaluated_documents']}个自动命中区间未对齐到纳入的标注行，保持无法判定，不新增人工任务。",
              '单先行词分母包括gold_status=antecedent_found的事件/篇章内容，不偷换为仅实体样本；按人工类型的细分结果见JSON。',
              '多先行词按完整区间集合严格匹配，不能部分命中算正确；严格与边界重叠诊断分开。',
              '完整分母、逐行预测与失败诊断见evaluation.json；字段兼容提醒见annotation_validation.json。', '']
    (out/'P4样本核验说明.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'review_counts': validation['review_counts'],
                      'metrics': report['rules']['metrics']}, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='只读校验P4人工标注，仅评估已确认答案；不修改标注文件')
    parser.add_argument('--samples', type=Path, default=ROOT/'outputs/P4/P4样本原文与段落.json')
    parser.add_argument('--annotations', type=Path, default=ROOT/'outputs/P4/P4人工标注样本.csv')
    parser.add_argument('--p1', type=Path, default=ROOT/'processed/P1爬虫数据清洗/documents.json')
    parser.add_argument('--predictions', type=Path, default=ROOT/'processed/P4共指消解/coreferences.json')
    parser.add_argument('--output-dir', type=Path, default=ROOT/'outputs/P4/评估')
    parser.add_argument('--split', type=Path, help='可选文档级development/evaluation划分JSON')
    args = parser.parse_args()
    run_evaluation(args.samples, args.annotations, args.p1, args.predictions, args.output_dir, args.split)
