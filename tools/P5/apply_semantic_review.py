"""Create an ISOLATED reviewed P5 candidate from explicitly approved audit decisions.

Never edits source runs, raw model observations, the formal tables, or P6/Neo4j.
The output is a versioned comparison candidate, NOT a published P5/P6 release.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN = Path('processed/P5条件建议抽取/runs/full-final-20261101/integrated')
FORMAL = Path('processed/P5条件建议抽取/表格/条件建议映射表.csv')
REVIEW = Path('outputs/P5/校验/P5语义修订建议.json')
DEFAULT_DECISIONS = Path('outputs/P5/校验/P5语义修订决定.json')
REQUIRED_FILES = ('conditions.json', 'conditions.csv', 'graph.json', 'entities.json',
                  'routes.json', 'transport_legs.json')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def read_csv(path):
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path, fields, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fields, extrasaction='raise')
        writer.writeheader()
        writer.writerows(rows)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rule_id(rule):
    # Same field order and JSON compactness as p5_agent/export.py -> common.digest.
    identity = {k: rule[k] for k in
                ('statement_kind', 'condition', 'consequence_text', 'force', 'targets', 'scenic_code')}
    h = hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(',', ':'),
                                   allow_nan=False).encode('utf-8')).hexdigest()
    return 'RULE-' + h[:20]


def prepare(root=ROOT):
    # Build the current audit from its checked-in read-only source rather than
    # requiring the optional generated report JSON to have been committed.
    spec = importlib.util.spec_from_file_location('p5_review_source', root / 'tools/P5/semantic_review.py')
    reviewer_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reviewer_module)
    review = reviewer_module.build_report(root)
    generated = root / REVIEW
    if generated.exists():
        prior = read_json(generated)
        selected = lambda doc: [(i['rule_id'], i['original_statement_kind'], i['suggested_statement_kind'],
                                 i['original_force'], i['suggested_force'], i['suggested_evidence_category'],
                                 i['suggested_recommendation_strength'])
                                for i in doc['items']]
        if selected(prior) != selected(review):
            raise ValueError('已保存的审核建议与当前规则源不一致，拒绝继续')
    if review.get('read_only') is not True or review.get('approved_for_export') is True:
        raise ValueError('需要使用只读审核建议作为输入')
    items = review.get('items', [])
    if not items or len({i['rule_id'] for i in items}) != len(items):
        raise ValueError('待审规则为空或有重复 ID')
    base = root / RUN
    rules = {r['rule_id']: r for r in read_json(base / 'conditions.json')}
    _, formal_rows = read_csv(root / FORMAL)
    formal = {r['rule_id']: r for r in formal_rows}
    if len(formal) != len(formal_rows):
        raise ValueError('正式映射表存在重复 rule_id')
    result = []
    allowed_categories = {'time_dependent_observation', 'personal_itinerary_choice', 'subjective_evaluation'}
    for item in items:
        old = rules.get(item['rule_id'])
        if not old or item['rule_id'] not in formal:
            raise ValueError('审核建议的规则在正式表或 P5 集成结果中缺失: ' + item['rule_id'])
        if (old['statement_kind'] != item['original_statement_kind'] or
                old['force'] != item['original_force'] or
                old['scenic_code'] != item['scenic_code'] or
                old['condition']['text'] != item['condition_text'] or
                old['consequence_text'] != item['consequence_text']):
            raise ValueError('审核建议与正式集成结果不匹配: ' + item['rule_id'])
        if item['suggested_evidence_category'] not in allowed_categories:
            raise ValueError('未知语义类别: ' + item['rule_id'])
        result.append({
            'rule_id': item['rule_id'],
            'expected_statement_kind': item['original_statement_kind'],
            'expected_force': item['original_force'],
            'suggested_statement_kind': item['suggested_statement_kind'],
            'suggested_force': item['suggested_force'],
            'suggested_evidence_category': item['suggested_evidence_category'],
            'suggested_recommendation_strength': item['suggested_recommendation_strength'],
            'reason': item['reason'],
            'human_decision': 'pending',   # CHANGE TO 'approved' only AFTER inspecting source.
            'reviewer': '',
        })
    return {
        'document_type': 'p5_semantic_revision_decisions_v2',
        'notice': '尚未批准修订；仅在人工作出明确确认后将每条 human_decision 改为 approved 并填写 reviewer。',
        'input_sha256': {
            'integrated/' + name: sha256(base / name) for name in REQUIRED_FILES
        } | {'formal_mapping.csv': sha256(root / FORMAL)},
        'items': result,
    }


def replace_rule_references(value, mapping):
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [replace_rule_references(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: replace_rule_references(v, mapping) for k, v in value.items()}
    return value


def recheck_reference_lists(value, kinds):
    if isinstance(value, dict):
        for k in ('recommendation_rule_ids', 'restriction_rule_ids'):
            if k in value:
                keep = {'recommended', 'optional'} if k == 'recommendation_rule_ids' else {'required', 'prohibited', 'avoid'}
                value[k] = [rid for rid in value[k] if rid not in kinds or kinds[rid] in keep]
        for child in value.values():
            recheck_reference_lists(child, kinds)
    elif isinstance(value, list):
        for child in value:
            recheck_reference_lists(child, kinds)


def check_graph(graph, rules, old_ids):
    nodes = {n['id']: n for n in graph['nodes']}
    if len(nodes) != len(graph['nodes']):
        raise ValueError('候选图有重复节点 ID')
    ids = {r['rule_id'] for r in rules}
    if len(ids) != len(rules):
        raise ValueError('候选规则有重复 ID')
    for rid in ids:
        if rid not in nodes or nodes[rid]['label'] != 'CONDITIONAL_RULE':
            raise ValueError('候选图缺少规则节点: ' + rid)
    for edge in graph['edges']:
        if edge['source'] not in nodes or edge['target'] not in nodes:
            raise ValueError('候选图出现悬空边')
    if any(old in nodes or old in ids for old in old_ids):
        raise ValueError('旧规则 ID 仍留在候选图/规则表中')
    for n in graph['nodes']:
        p = n['properties']
        for k in ('rule_ids', 'recommendation_rule_ids', 'restriction_rule_ids'):
            for rid in p.get(k, []):
                if rid not in ids:
                    raise ValueError('规则引用不在候选规则表: ' + rid)


def apply(root, decisions_file, out):
    root, decisions_file, out = Path(root).resolve(), Path(decisions_file).resolve(), Path(out).resolve()
    protected = [root / 'processed', root / 'src', root / 'tools', root / 'tests']
    if out == root or any(out == p or p in out.parents for p in protected):
        raise ValueError('拒绝在正式数据、代码或测试目录写入候选结果')
    if out.exists():
        raise FileExistsError('候选输出目录已存在，拒绝覆盖: ' + str(out))
    doc = read_json(decisions_file)
    if doc.get('document_type') != 'p5_semantic_revision_decisions_v2':
        raise ValueError('修订清单格式不正确')
    expected = prepare(root)
    if doc['input_sha256'] != expected['input_sha256']:
        raise ValueError('输入 SHA256 已变化；必须重新审核当前版本，不允许混用旧决策')
    by_id = {i['rule_id']: i for i in doc.get('items', [])}
    expected_items = {i['rule_id']: i for i in expected['items']}
    if len(by_id) != len(doc.get('items', [])) or set(by_id) != set(expected_items):
        raise ValueError('修订清单出现重复、缺失或额外规则')
    for rid, item in by_id.items():
        baseline = expected_items[rid]
        for field in ('expected_statement_kind', 'expected_force', 'suggested_statement_kind',
                      'suggested_force', 'suggested_evidence_category',
                      'suggested_recommendation_strength', 'reason'):
            if item.get(field) != baseline[field]:
                raise ValueError('修订方案被修改，请先重新审核: ' + rid + ' / ' + field)
        if item.get('human_decision') != 'approved' or not str(item.get('reviewer', '')).strip():
            raise ValueError('仍有未批准或未填写审核人的记录: ' + rid)
    base = root / RUN
    source = {name: read_json(base / name) for name in REQUIRED_FILES if name.endswith('.json')}
    rules = deepcopy(source['conditions.json'])
    old_index = {r['rule_id']: r for r in rules}
    id_map, reviewed_rules, revised_force = {}, {}, {}
    for old_id, item in by_id.items():
        r = old_index[old_id]
        r['statement_kind'] = item['suggested_statement_kind']
        r['force'] = item['suggested_force']
        r['p5_complete'] = r['statement_kind'] in {'conditional_advice', 'conditional_restriction'}
        new_id = rule_id(r)
        # A/B change semantic identity -> new IDs. C only adds a subjective flag
        # and must keep its original deterministic ID and recommendation status.
        if new_id != old_id:
            if new_id in old_index or new_id in id_map.values():
                raise ValueError('新规则 ID 与现存或其他修订规则冲突: ' + new_id)
            id_map[old_id] = new_id
        r['rule_id'] = new_id
        category = item['suggested_evidence_category']
        strength = item['suggested_recommendation_strength']
        if (category == 'subjective_evaluation') != (strength == 'subjective_weak'):
            raise ValueError('主观评价和弱建议标签不一致: ' + old_id)
        if category == 'subjective_evaluation' and not r['p5_complete']:
            raise ValueError('主观隐含建议必须保留为条件建议: ' + old_id)
        if category != 'subjective_evaluation' and r['p5_complete']:
            raise ValueError('描述性观察不能保留为完整建议: ' + old_id)
        r['semantic_review'] = {
            'original_rule_id': old_id,
            'evidence_category': category,
            'recommendation_strength': strength,
            'source_claim_status': 'author_opinion' if strength == 'subjective_weak' else 'source_observation_not_independently_verified',
            'status': 'human_approved_candidate',
            'reviewer': item['reviewer'],
            'reason': item['reason'],
        }
        reviewed_rules[new_id] = r
        revised_force[new_id] = r['force']
    graph = replace_rule_references(source['graph.json'], id_map)
    for n in graph['nodes']:
        if n['label'] == 'CONDITIONAL_RULE' and n['id'] in reviewed_rules:
            n['properties'] = deepcopy(reviewed_rules[n['id']])
    entities = replace_rule_references(source['entities.json'], id_map)
    routes = replace_rule_references(source['routes.json'], id_map)
    legs = replace_rule_references(source['transport_legs.json'], id_map)
    for value in (graph, entities, routes, legs):
        recheck_reference_lists(value, revised_force)
    check_graph(graph, rules, set(id_map))
    # Reconcile referenced rule IDs in P5 integrated tables, not just graph edges.
    fields, rows = read_csv(base / 'conditions.csv')
    for row in rows:
        if row['rule_id'] in by_id:
            old_id = row['rule_id']
            item = by_id[old_id]
            row['rule_id'] = id_map.get(old_id, old_id)
            row['statement_kind'] = item['suggested_statement_kind']
            row['force'] = item['suggested_force']
            row['p5_complete'] = str(item['suggested_statement_kind'] in {'conditional_advice', 'conditional_restriction'})
            row['semantic_evidence_category'] = item['suggested_evidence_category']
            row['recommendation_strength'] = item['suggested_recommendation_strength']
            row['source_claim_status'] = ('author_opinion' if item['suggested_recommendation_strength'] == 'subjective_weak'
                                          else 'source_observation_not_independently_verified')
    # Explicit metadata columns ensure CSV consumers can distinguish weak advice
    # without losing the nested semantic_review stored in the JSON and graph.
    for field in ('semantic_evidence_category', 'recommendation_strength', 'source_claim_status'):
        if field not in fields:
            fields.append(field)
        for row in rows:
            row.setdefault(field, '')
    formal_fields, formal_rows = read_csv(root / FORMAL)
    for row in formal_rows:
        if row['rule_id'] in by_id:
            old_id = row['rule_id']
            item = by_id[old_id]
            is_subjective = item['suggested_evidence_category'] == 'subjective_evaluation'
            row.update(rule_id=id_map.get(old_id, old_id), statement_kind=item['suggested_statement_kind'],
                       force=item['suggested_force'],
                       对象类别='主观条件建议（隐含）' if is_subjective else '条件性观察（非普适建议）',
                       check_status='已人工审阅：候选版修订', revision_status='候选版完成，尚未发布')
            row['original_rule_id'] = old_id
            row['semantic_evidence_category'] = item['suggested_evidence_category']
            row['recommendation_strength'] = item['suggested_recommendation_strength']
    if len(rules) != len(source['conditions.json']) or len(rows) != len(source['conditions.json']):
        raise ValueError('候选规则数量发生非预期变化')
    for field in ('original_rule_id', 'semantic_evidence_category', 'recommendation_strength'):
        if field not in formal_fields:
            formal_fields.append(field)
        for row in formal_rows:
            row.setdefault(field, '')
    if len({x['rule_id'] for x in formal_rows}) != len(formal_rows):
        raise ValueError('候选正式表有重复 ID')
    advice_rows = [r for r in formal_rows if r['statement_kind'] in {'conditional_advice', 'conditional_restriction'}]
    observation_rows = [r for r in formal_rows if r['semantic_evidence_category'] in
                        {'time_dependent_observation', 'personal_itinerary_choice'}]
    subjective_rows = [r for r in formal_rows if r['semantic_evidence_category'] == 'subjective_evaluation']
    if (len(advice_rows) != len(formal_rows) - len(observation_rows) or
            len(observation_rows) != 2 or len(subjective_rows) != 1 or len(id_map) != 2):
        raise ValueError('候选建议表的分类数量不一致')
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.p5-candidate-', dir=out.parent))
    try:
        write_json(stage / 'conditions.json', rules)
        write_json(stage / 'graph.json', graph)
        write_json(stage / 'entities.json', entities)
        write_json(stage / 'routes.json', routes)
        write_json(stage / 'transport_legs.json', legs)
        write_csv(stage / 'conditions.csv', fields, rows)
        write_csv(stage / 'formal_mapping_candidate.csv', formal_fields, formal_rows)
        write_csv(stage / 'valid_advice_candidate.csv', formal_fields, advice_rows)
        write_csv(stage / 'reclassified_observations.csv', formal_fields, observation_rows)
        write_csv(stage / 'subjective_weak_advice.csv', formal_fields, subjective_rows)
        write_json(stage / 'revision_manifest.json', {
            'status': 'candidate_only_NOT_a_P5_P6_release',
            'source_run': str(RUN),
            'source_input_sha256': doc['input_sha256'],
            'decisions_sha256': sha256(decisions_file),
            'rule_id_map': id_map,
            'reviewed_rule_count': len(reviewed_rules),
            'id_retained_subjective_rules': [r['rule_id'] for r in subjective_rows],
            'rule_count_before': len(source['conditions.json']),
            'rule_count_after': len(rules),
            'graph_node_count_before': len(source['graph.json']['nodes']),
            'graph_node_count_after': len(graph['nodes']),
            'graph_edge_count_before': len(source['graph.json']['edges']),
            'graph_edge_count_after': len(graph['edges']),
            'formal_mapping_count': len(formal_rows),
            'valid_advice_candidate_count': len(advice_rows),
            'reclassified_observation_count': len(observation_rows),
            'subjective_weak_advice_count': len(subjective_rows),
            'warnings': ['不会自动更新P6的固定输入路径、导入库和发布哈希',
                         'P5原始模型记录未改变；这是可追溯的人工修订候选层',
                         '主观弱建议仍为conditional_advice；下游P6必须先识别semantic_review标签，禁止当作客观事实',
                         '候选完整性检查不等于P6重建验证，也不等于语义准确率评估'],
        })
        stage.rename(out)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return {'output': str(out), 'changed_rules': id_map, 'reviewed_rule_count': len(reviewed_rules),
            'subjective_weak_advice_count': len(subjective_rows), 'rule_count': len(rules),
            'nodes': len(graph['nodes']), 'edges': len(graph['edges'])}


def main():
    parser = argparse.ArgumentParser(description='P5 语义复核：只创建独立候选，不修改正式数据或 Neo4j')
    parser.add_argument('--prepare', action='store_true', help='创建待人工审核的决定文件（默认 pending）')
    parser.add_argument('--apply', action='store_true', help='只处理人工批准的决定，生成独立候选结果')
    parser.add_argument('--decisions', type=Path, default=ROOT / DEFAULT_DECISIONS)
    parser.add_argument('--out', type=Path, default=ROOT / 'outputs/P5/semantic_revision_candidate_plan_b_v1')
    args = parser.parse_args()
    if args.prepare == args.apply:
        parser.error('必须且只能选择 --prepare 或 --apply')
    if args.prepare:
        if args.decisions.exists():
            parser.error('决定文件已存在，拒绝覆盖：' + str(args.decisions))
        doc = prepare(ROOT)
        write_json(args.decisions, doc)
        print('已创建待审核决定文件（未修改任何正式数据）：', args.decisions)
        print('需要人工核对三条记录，修改 human_decision 为 approved 并填写 reviewer。')
    else:
        result = apply(ROOT, args.decisions, args.out)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
