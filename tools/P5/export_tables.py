# -*- coding: utf-8 -*-
"""把第 5 章要用的统计口径固化下来。

报告里的数字如果靠手抄，改一次数据就要重新对一遍。这里统一从 P6 收下的
cleaned-v1（P5 只负责抽取，汇总归 P6）现算，导出 CSV 和一份带哈希的统计依据。
人群粗分只用关键词，是给人看的粗分类，不当成语义判定。

    python -X utf8 tools/P5/export_tables.py
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLEAN = ROOT / 'processed/P6知识图谱/cleaned-v1'
OUT = ROOT / 'outputs/P5'
SCENE = ['GZCL', 'JZG', 'ZHCL']
KIND = {'general_advice': '一般建议', 'conditional_advice': '条件建议', 'conditional_fact': '条件事实',
        'incomplete_condition': '条件不完整', 'general_restriction': '一般限制',
        'conditional_restriction': '条件限制'}
CAT = {'date_time': '日期时段', 'other': '其他', 'audience': '人群', 'ticket': '门票',
       'access': '进出园', 'crowding': '拥挤', 'time_budget': '时间预算', 'weather': '天气',
       'service_availability': '服务可用', 'fitness': '体力'}
AUD = [('带小孩', '宝宝|娃|小孩|儿童|孩子|身高|亲子|小朋友|小盆友'),
       ('住店客人', '住客|酒店|入住|住宿'),
       ('带长辈', '老人|父母|长辈|爸妈'),
       ('其他人群', None)]


def load(name):
    return json.loads((CLEAN / name).read_text('utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def bucket(text):
    t = text or ''
    for name, pattern in AUD:
        if pattern and re.search(pattern, t):
            return name
    return '其他人群'


def write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main():
    rules = load('conditions.json')
    graph = load('graph.json')
    docs = json.loads((ROOT / 'processed/P1爬虫数据清洗/documents.json').read_text('utf-8'))

    by_scene_kind = {s: Counter(r['statement_kind'] for r in rules if r['scenic_code'] == s) for s in SCENE}
    write_csv(OUT / '表格/陈述类型统计.csv',
              ['陈述类型', 'code'] + SCENE + ['合计'],
              [[KIND[k], k] + [by_scene_kind[s].get(k, 0) for s in SCENE]
               + [sum(by_scene_kind[s].get(k, 0) for s in SCENE)] for k in KIND])

    cat_scene = defaultdict(Counter)
    for r in rules:
        for cl in ((r.get('condition') or {}).get('clauses') or []):
            cat_scene[cl.get('category')][r['scenic_code']] += 1
    write_csv(OUT / '表格/条件类型统计.csv',
              ['条件类别', 'code'] + SCENE + ['合计'],
              [[CAT.get(k, k), k] + [cat_scene[k].get(s, 0) for s in SCENE] + [sum(cat_scene[k].values())]
               for k in sorted(cat_scene, key=lambda k: -sum(cat_scene[k].values()))])

    aud_scene = defaultdict(Counter)
    aud_texts = defaultdict(list)
    for r in rules:
        for cl in ((r.get('condition') or {}).get('clauses') or []):
            if cl.get('category') == 'audience':
                b = bucket(cl.get('text'))
                aud_scene[b][r['scenic_code']] += 1
                aud_texts[b].append(cl.get('text'))
    order = [name for name, _ in AUD]
    write_csv(OUT / '表格/人群条件粗分.csv',
              ['人群', *SCENE, '合计'],
              [[b] + [aud_scene[b].get(s, 0) for s in SCENE] + [sum(aud_scene[b].values())] for b in order])

    force = Counter(r['force'] for r in rules)
    complete = sum(1 for r in rules if r.get('p5_complete'))
    rules_by_scene = Counter(r['scenic_code'] for r in rules)
    nodes = Counter(n['label'] for n in graph['nodes'])
    edges = Counter(e['type'] for e in graph['edges'])
    run = ROOT / 'processed/P5条件建议抽取/ACTIVE_RUN'
    run_id = run.read_text('utf-8').strip() if run.exists() else ''
    summary_path = ROOT / 'processed/P5条件建议抽取/runs' / run_id / 'summary.json'
    summary = json.loads(summary_path.read_text('utf-8')) if summary_path.exists() else {}

    basis = {
        'schema': 'p5-chapter-stats-1',
        'source': {'p5_run': run_id, 'summary_sha256': sha(summary_path) if summary_path.exists() else None,
                   'graph_sha256': sha(CLEAN / 'graph.json'),
                   'conditions_sha256': sha(CLEAN / 'conditions.json')},
        'packages': {k: summary.get(k) for k in
                     ('run_id', 'packages_prepared', 'packages_attempted', 'status_counts',
                      'active_completed_observations', 'conditions', 'p5_complete_conditions',
                      'transport_legs', 'p3_routes_inherited', 'new_routes_extracted')},
        'usage': summary.get('usage'),
        'max_actual_prompt_tokens': summary.get('max_actual_prompt_tokens'),
        'reference_scopes': summary.get('reference_scopes'),
        'rules': {'total': len(rules), 'by_scene': dict(rules_by_scene), 'by_force': dict(force),
                  'statement_kinds': dict(Counter(r['statement_kind'] for r in rules)),
                  'p5_complete': complete,
                  'complete_rate': round(complete / len(rules), 4)},
        'clause_categories': {k: sum(v.values()) for k, v in cat_scene.items()},
        'audience_buckets': {k: sum(v.values()) for k, v in aud_scene.items()},
        'audience_samples': {k: sorted(set(v))[:6] for k, v in aud_texts.items()},
        'graph': {'nodes': len(graph['nodes']), 'edges': len(graph['edges']),
                  'node_labels': dict(nodes), 'edge_types': dict(edges)},
        'documents': dict(Counter('%s:%s' % (d['scenic_code'], d['text_type']) for d in docs)),
        'rule': '人群粗分按关键词归并，仅用于描述性统计，不参与任何抽取判定',
    }
    path = OUT / '校验/章节统计依据.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(basis, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'rules': len(rules), 'complete': complete, 'tables': 3,
                      'basis': str(path.relative_to(ROOT))}, ensure_ascii=False))


if __name__ == '__main__':
    main()
