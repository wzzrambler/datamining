"""Build an isolated P6 *review candidate* from a reviewed P5 plan-B candidate.

This entry point never runs the production P6 pipeline, imports Neo4j, calls an
LLM, creates release.json or writes quality_manifest.json.  Its output is not
an importable P6 release.  Existing production files are never overwritten.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_P5 = ROOT / 'outputs/P5/semantic_revision_candidate_plan_b_v1'
OUTPUT_ROOT = ROOT / 'outputs/P6'
DEFAULT_OUTPUT = OUTPUT_ROOT / 'semantic_revision_candidate_plan_b_v1'
DECISIONS = ROOT / 'outputs/P5/校验/P5语义修订决定.json'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               allow_nan=False) + '\n', encoding='utf-8')


def validate_output(output, output_root=None):
    """Restrict writes to one new direct child of outputs/P6."""
    original_base = Path(output_root if output_root is not None else OUTPUT_ROOT)
    if original_base.is_symlink() or original_base.parent.is_symlink():
        raise ValueError('Candidate output root must not be a symbolic link')
    base = original_base.resolve()
    dest = Path(output).resolve()
    if dest.parent != base or dest.name.startswith('.'):
        raise ValueError('Output must be one new directory directly under outputs/P6')
    if dest.exists():
        raise FileExistsError('Candidate output already exists; nothing was overwritten: ' + str(dest))
    return dest


def validate_decisions(candidate_manifest, decisions=DECISIONS):
    expected = candidate_manifest.get('decisions_sha256')
    if not expected or not Path(decisions).is_file() or sha(decisions) != expected:
        raise ValueError('P5 human decisions changed or are unavailable; regenerate the review')


def check_semantics(graph, summaries, manifest):
    """Check actual materialized rule nodes *and* their P6 aggregate items."""
    rule_nodes = {n['id']: n['properties'] for n in graph['nodes']
                  if n['label'] == 'CONDITIONAL_RULE'}
    by_id = {}
    for group in summaries:
        for item in group['items']:
            for rid in item['source_record_ids']:
                by_id.setdefault(rid, []).append(item)
    for old_id, new_id in manifest['rule_id_map'].items():
        if old_id in rule_nodes or old_id in by_id or new_id not in rule_nodes or new_id not in by_id:
            raise ValueError('Reclassified rule reference mismatch: ' + old_id)
        properties = rule_nodes[new_id]
        review = properties.get('semantic_review', {})
        if (properties.get('statement_kind') != 'conditional_fact' or
            properties.get('force') != 'descriptive' or
            review.get('recommendation_strength') != 'not_applicable' or
            review.get('source_claim_status') != 'source_observation_not_independently_verified'):
            raise ValueError('Reclassified rule lost its descriptive identity: ' + new_id)
        if any(item['category'] != 'conditional_facts' or
               item.get('semantic_review', {}).get('recommendation_strength') != 'not_applicable'
               for item in by_id[new_id]):
            raise ValueError('Reclassified P6 summary is not a conditional fact: ' + new_id)
    for rid in manifest['id_retained_subjective_rules']:
        if rid not in rule_nodes or rid not in by_id:
            raise ValueError('Subjective rule missing: ' + rid)
        properties = rule_nodes[rid]
        review = properties.get('semantic_review', {})
        if (properties.get('statement_kind') != 'conditional_advice' or
            review.get('recommendation_strength') != 'subjective_weak' or
            review.get('source_claim_status') != 'author_opinion'):
            raise ValueError('Subjective P5 rule is not labelled safely: ' + rid)
        for item in by_id[rid]:
            item_review = item.get('semantic_review', {})
            if (item['category'] != 'recommendations' or
                item_review.get('recommendation_strength') != 'subjective_weak' or
                item_review.get('source_claim_status') != 'author_opinion' or
                'author_opinion_not_objective_fact' not in item['review_flags']):
                raise ValueError('Subjective P6 summary is not labelled safely: ' + rid)
    return {'reclassified_rule_ids': manifest['rule_id_map'],
            'retained_subjective_rule_ids': manifest['id_retained_subjective_rules']}


def create_candidate(candidate, output):
    """Validate in memory first, then atomically publish only a new candidate folder."""
    destination = validate_output(output)
    candidate = Path(candidate).resolve()
    # The real P5 sources, approved decision hash, and semantic rules are checked
    # by this already-tested read-only preflight before any output is created.
    sys.path.insert(0, str(ROOT / 'src/P6知识图谱'))
    from preview_p5_semantics import preview, UPSTREAM, DOCUMENTS
    from clean_routes import curate
    from quality_pipeline import build, compact

    p5_manifest = load(candidate / 'revision_manifest.json')
    validate_decisions(p5_manifest)
    expected = preview(candidate)
    graph = load(candidate / 'graph.json')
    routes = load(candidate / 'routes.json')
    observations = {r['record_id']: r for r in load(UPSTREAM / 'raw_observations.json')}
    documents = {d['doc_id']: d for d in load(DOCUMENTS)}

    cleaned, retained_routes, fragments, decisions, bindings, route_summary = curate(
        graph, routes, observations, documents)
    p6_graph, times, advice, advice_audit = build(
        cleaned, observations, documents, theme_results=None)
    # Never silently carry historical LLM themes into a newly reviewed release.
    if 'entity_scoped_llm_theme_summary' in p6_graph['p6_quality']['stages']:
        raise ValueError('Historical cached LLM themes detected; do not present them as reviewed')
    if (len(retained_routes) != expected['p6_retained_routes'] or
        len(p6_graph['nodes']) != expected['p6_nodes'] or
        len(p6_graph['edges']) != expected['p6_edges'] or
        len(advice) != expected['p6_advice_groups']):
        raise ValueError('Materialized P6 results differ from read-only preview')
    reviewed = check_semantics(p6_graph, advice, p5_manifest)

    # These are review artifacts, deliberately NOT a production quality manifest
    # or a release descriptor accepted by src/P6知识图谱/run.py.
    output_data = {
        'graph.json': p6_graph,
        'routes.json': retained_routes,
        'conditions.json': [n['properties'] for n in p6_graph['nodes'] if n['label'] == 'CONDITIONAL_RULE'],
        'entities.json': [n['properties'] for n in p6_graph['nodes'] if n['label'] in {'POI', 'TRANSPORT'}],
        'transport_legs.json': [n['properties'] for n in p6_graph['nodes'] if n['label'] == 'TRANSPORT_LEG'],
        'entity_time_summaries.json': times,
        'condition_recommendation_summaries.json': advice,
        'advice_summary_audit.json': advice_audit,
        'route_fragments_audit.json': fragments,
        'route_decisions.json': decisions,
        'scope_cleanup_audit.json': bindings,
        'cleaning_summary.json': route_summary,
    }
    unions = {}
    for t in times:
        for observation in t['observations']:
            key = compact([t['target_id'], t['attribute'], observation['scope']])
            unions.setdefault(key, []).append(observation)
    output_data['time_observation_unions.json'] = unions

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.p6-plan-b-staging-', dir=OUTPUT_ROOT) as tmp:
        staging = Path(tmp)
        for name, value in output_data.items():
            save(staging / name, value)
        manifest = {
            'status': 'candidate_only_NOT_a_P6_release',
            'source_p5_revision_manifest_sha256': sha(candidate / 'revision_manifest.json'),
            'source_p5_candidate_graph_sha256': sha(candidate / 'graph.json'),
            'source_p5_decisions_sha256': p5_manifest['decisions_sha256'],
            'p5_rules': expected['p5_rules'],
            'p6_retained_routes': len(retained_routes),
            'p6_nodes': len(p6_graph['nodes']),
            'p6_edges': len(p6_graph['edges']),
            'p6_advice_groups': len(advice),
            **reviewed,
            'llm_theme_status': 'NOT_ATTACHED_NOT_REVALIDATED',
            'neo4j_imported': False,
            'production_p6_modified': False,
            'output_file_sha256': {name: sha(staging / name) for name in sorted(output_data)},
            'limitations': [
                'Not a release or an import-ready quality manifest',
                'Historical LLM theme summaries have not been revalidated or attached',
                'Does not independently certify semantic truth of all P5/P6 records',
                'Production cleaned-v1, release.json and Neo4j were not modified',
            ],
        }
        save(staging / 'candidate_manifest.json', manifest)
        # A second existence check closes the common accidental overwrite race.
        if destination.exists():
            raise FileExistsError('Candidate output appeared during build: ' + str(destination))
        staging.rename(destination)
    return {'status': manifest['status'], 'output': str(destination),
            'p6_nodes': manifest['p6_nodes'], 'p6_edges': manifest['p6_edges'],
            'p6_retained_routes': manifest['p6_retained_routes'],
            'p6_advice_groups': manifest['p6_advice_groups'],
            'notice': 'Candidate only. No production release, LLM run or Neo4j import.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, default=DEFAULT_P5)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(create_candidate(args.candidate, args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
