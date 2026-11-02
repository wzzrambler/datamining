"""Build the only importable P6 graph: route filter → times → advice."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from p6_quality.advice import summarize_advice
from p6_quality.common import compact
from p6_quality.times import summarize_times
from p6_quality.llm_themes import attach_results, sha as theme_sha

ROOT = Path(__file__).resolve().parents[2]
P6 = ROOT / 'processed/P6知识图谱'
P5 = ROOT / 'processed/P5条件建议抽取/runs/full-final-20261101'
OUT = P6 / 'cleaned-v1'
VERSION = 'p6-quality-v1'


def load(p):
    return json.loads(p.read_text('utf-8'))


def save(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', 'utf-8')


def build(graph, observations, documents, theme_results=None):
    graph = json.loads(json.dumps(graph, ensure_ascii=False))
    if theme_results is None:
        cached=[n['properties']['llm_theme_result'] for n in graph['nodes']
                if n['label']=='ADVICE_SUMMARY' and n['properties'].get('llm_theme_result')]
        theme_results=cached or None
    # Historical P5/P6 snapshots may contain cached summaries. Never stack old
    # aggregates on new aggregates; the raw FACT/RULE records are authoritative.
    derived = {n['id'] for n in graph['nodes'] if n['label'] in {'TIME_SUMMARY', 'ADVICE_SUMMARY'}}
    graph['nodes'] = [n for n in graph['nodes'] if n['id'] not in derived]
    graph['edges'] = [e for e in graph['edges'] if e['source'] not in derived and e['target'] not in derived]
    nodes = {n['id']: n for n in graph['nodes']}
    for n in nodes.values():
        for k in ('time_summary_ids', 'advice_summary_ids', 'advice_themes'):
            n['properties'].pop(k, None)
    facts = []
    for n in nodes.values():
        if n['label'] == 'FACT':
            fact = dict(n['properties'], source_fact_id=n['id'])
            target = nodes[fact.get('entity_id') or fact['leg_id']]
            fact['scenic_code'] = target['properties'].get('scenic_code')
            facts.append(fact)
    rules = [n['properties'] for n in nodes.values() if n['label'] == 'CONDITIONAL_RULE']
    times = summarize_times(facts, observations, rules)
    advice, advice_audit = summarize_advice(graph, documents)
    theme_audit=attach_results(graph,advice,theme_results) if theme_results is not None else None

    def edge(source, kind, target):
        graph['edges'].append({'source': source, 'type': kind, 'target': target, 'properties': {}})

    for t in times:
        sid = t['time_summary_id']
        graph['nodes'].append({'id': sid, 'label': 'TIME_SUMMARY', 'properties': t})
        edge(t['target_id'], 'HAS_TIME_SUMMARY', sid)
        nodes[t['target_id']]['properties'].setdefault('time_summary_ids', []).append(sid)
        for oid in sorted({oid for o in t['observations'] for oid in o['observation_ids']}):
            edge(sid, 'SUPPORTED_BY', oid)
    for a in advice:
        sid = a['advice_summary_id']
        graph['nodes'].append({'id': sid, 'label': 'ADVICE_SUMMARY', 'properties': a})
        if a['target_id']:
            edge(a['target_id'], 'HAS_ADVICE_SUMMARY', sid)
            nodes[a['target_id']]['properties'].setdefault('advice_summary_ids', []).append(sid)
        for rid in a['source_record_ids']:
            edge(sid, 'DERIVED_FROM', rid)
    for node in nodes.values():
        for key in ('time_summary_ids', 'advice_summary_ids'):
            if key in node['properties']:
                node['properties'][key].sort()
    graph['p6_cleaning']['route_filter_version'] = graph['p6_cleaning'].get('route_filter_version', graph['p6_cleaning']['version'])
    graph['p6_cleaning']['version'] = 'p6-quality-themes-v1' if theme_audit else VERSION
    stages = ['route_quality_filter', 'historical_time_summary', 'condition_recommendation_summary']
    if theme_audit:
        stages.append('entity_scoped_llm_theme_summary')
    graph['p6_quality'] = {'version': VERSION, 'stages': stages,
                           'all_stages_completed': True, 'time_summary_owner': 'P6', 'advice_summary_method': 'lossless_rule_aggregation_v1',
                           'semantic_merge_completed': False, 'llm_calls': 0}
    if theme_audit:
        graph['p6_quality'].update(version='p6-quality-themes-v1',advice_summary_method=theme_audit['method'],
            semantic_merge_completed=True,llm_entity_themes=theme_audit,
            llm_calls='recorded_in_llm_advice_release_usage; offline_rebuild_makes_no_calls')
    graph['nodes'].sort(key=lambda n: n['id'])
    graph['edges'].sort(key=lambda e: (e['source'], e['type'], e['target'], compact(e['properties'])))
    ids = {n['id'] for n in graph['nodes']}
    assert len(ids) == len(graph['nodes'])
    assert len({compact(e) for e in graph['edges']}) == len(graph['edges'])
    assert all(e['source'] in ids and e['target'] in ids for e in graph['edges'])
    return graph, times, advice, advice_audit


def main():
    stage = P6 / 'quality-stages/route-filtered'
    subprocess.run([sys.executable, str(Path(__file__).with_name('clean_routes.py')), '--output', str(stage)],
                   check=True, capture_output=True, text=True, encoding='utf-8')
    observations = {r['record_id']: r for r in load(P5 / 'raw_observations.json')}
    docs = {d['doc_id']: d for d in load(ROOT / 'processed/P1爬虫数据清洗/documents.json')}
    theme_results=None
    theme_release=P6/'llm_advice/release.json'
    if theme_release.exists():
        spec=load(theme_release)
        theme_results=load(P6/'llm_advice'/spec['results'])
        if theme_sha(theme_results)!=spec['results_sha256']:
            raise ValueError('LLM总结结果哈希不一致')
    graph, times, advice, audit = build(load(stage / 'graph.json'), observations, docs, theme_results)
    # Keep all route-filter decisions alongside the final quality products.
    for p in stage.iterdir():
        if p.is_file() and p.name not in {'graph.json', 'entities.json', 'transport_legs.json', 'entity_time_summaries.json'}:
            (OUT / p.name).parent.mkdir(parents=True, exist_ok=True)
            (OUT / p.name).write_bytes(p.read_bytes())
    for name, value in {'graph.json': graph, 'entity_time_summaries.json': times,
                         'condition_recommendation_summaries.json': advice, 'advice_summary_audit.json': audit}.items():
        save(OUT / name, value)
    if theme_results is not None:
        save(OUT/'entity_advice_theme_summaries.json',theme_results)
    for name, labels in {'entities.json': {'POI', 'TRANSPORT'}, 'transport_legs.json': {'TRANSPORT_LEG'}}.items():
        save(OUT / name, [n['properties'] for n in graph['nodes'] if n['label'] in labels])
    unions = {compact([t['target_id'], t['attribute'], o['scope']]): [] for t in times for o in t['observations']}
    for t in times:
        for o in t['observations']:
            unions[compact([t['target_id'], t['attribute'], o['scope']])].append(o)
    save(OUT / 'time_observation_unions.json', unions)
    manifest = {'version': graph['p6_cleaning']['version'], 'stages': graph['p6_quality']['stages'], 'all_stages_completed': True,
                'source_p5_graph_sha256': hashlib.sha256((P5 / 'integrated/graph.json').read_bytes()).hexdigest(),
                'raw_observations_sha256': hashlib.sha256((P5 / 'raw_observations.json').read_bytes()).hexdigest(),
                'documents_sha256': hashlib.sha256((ROOT / 'processed/P1爬虫数据清洗/documents.json').read_bytes()).hexdigest(),
                'graph_sha256': hashlib.sha256((OUT / 'graph.json').read_bytes()).hexdigest(),
                'nodes': len(graph['nodes']), 'edges': len(graph['edges']), 'time_summaries': len(times),
                'advice_summaries': len(advice), 'advice_audit': audit, 'llm_calls': 0}
    if theme_results is not None:
        manifest['llm_theme_result_sha256']=theme_sha(theme_results)
        manifest['llm_theme_entity_scene_groups']=len(theme_results)
        manifest['llm_theme_unique_entity_ids']=len({r['entity_id'] for r in theme_results})
        manifest['llm_theme_count']=sum(len(r['topics']) for r in theme_results)
    save(OUT / 'quality_manifest.json', manifest)
    print(json.dumps({k:v for k,v in manifest.items() if k != 'advice_audit'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
