"""Read-only P5 plan-B -> P6 consistency preview (NO writes, LLM calls or Neo4j)."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/P6知识图谱'))
from clean_routes import curate
from quality_pipeline import build

CANDIDATE = ROOT / 'outputs/P5/semantic_revision_candidate_plan_b_v1'
UPSTREAM = ROOT / 'processed/P5条件建议抽取/runs/full-final-20261101'
DOCUMENTS = ROOT / 'processed/P1爬虫数据清洗/documents.json'


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def preview(candidate):
    candidate = Path(candidate).resolve()
    manifest = load(candidate / 'revision_manifest.json')
    if manifest.get('status') != 'candidate_only_NOT_a_P5_P6_release':
        raise ValueError('Only a review candidate may be previewed; no production release')
    for key, expected in manifest.get('source_input_sha256', {}).items():
        if key.startswith('integrated/'):
            source = UPSTREAM / key
        elif key == 'formal_mapping.csv':
            source = ROOT / 'processed/P5条件建议抽取/表格/条件建议映射表.csv'
        else:
            raise ValueError('Unexpected manifest path: ' + key)
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError('P5 source has changed; review must be regenerated: ' + key)
    graph = load(candidate / 'graph.json')
    routes = load(candidate / 'routes.json')
    rules = load(candidate / 'conditions.json')
    if len(rules) != manifest['rule_count_after'] or len(graph['nodes']) != manifest['graph_node_count_after'] or len(graph['edges']) != manifest['graph_edge_count_after']:
        raise ValueError('Candidate files no longer match revision manifest')
    observations = {r['record_id']: r for r in load(UPSTREAM / 'raw_observations.json')}
    documents = {d['doc_id']: d for d in load(DOCUMENTS)}
    cleaned_graph, cleaned_routes, *_ = curate(graph, routes, observations, documents)
    p6_graph, _, summaries, _ = build(cleaned_graph, observations, documents, theme_results=None)
    items_by_id = {}
    for group in summaries:
        for item in group['items']:
            for rid in item['source_record_ids']:
                items_by_id.setdefault(rid, []).append(item)
    changed = manifest['rule_id_map']
    for old_id, new_id in changed.items():
        if old_id in items_by_id or new_id not in items_by_id:
            raise ValueError('Old/new rule references inconsistent: ' + old_id)
        for item in items_by_id[new_id]:
            if item['category'] != 'conditional_facts' or item.get('semantic_review', {}).get('recommendation_strength') != 'not_applicable':
                raise ValueError('Reclassified observation lost descriptive label: ' + new_id)
    for rid in manifest['id_retained_subjective_rules']:
        if rid not in items_by_id:
            raise ValueError('Subjective recommendation missing in P6 summaries: ' + rid)
        for item in items_by_id[rid]:
            review = item.get('semantic_review', {})
            if (item['category'] != 'recommendations' or review.get('recommendation_strength') != 'subjective_weak' or
                    review.get('source_claim_status') != 'author_opinion' or 'author_opinion_not_objective_fact' not in item['review_flags']):
                raise ValueError('Subjective recommendation is not safely labelled: ' + rid)
    return {'status': 'read_only_preview_passed', 'p5_rules': len(rules),
            'p6_retained_routes': len(cleaned_routes), 'p6_nodes': len(p6_graph['nodes']),
            'p6_edges': len(p6_graph['edges']), 'p6_advice_groups': len(summaries),
            'reclassified_rule_ids': changed,
            'subjective_rule_ids': manifest['id_retained_subjective_rules'],
            'notice': 'No files, production databases, old P6 release, or LLM summaries were modified.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, default=CANDIDATE)
    args = parser.parse_args()
    result = preview(args.candidate)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
