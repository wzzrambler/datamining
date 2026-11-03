"""Build the only importable P6 graph: route filter → times → advice."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from p6_quality.advice import summarize_advice
from p6_quality.common import compact, require
from p6_quality.times import summarize_times
from p6_quality.llm_themes import attach_results, sha as theme_sha
import p5run

ROOT = Path(__file__).resolve().parents[2]
P6 = ROOT / 'processed/P6知识图谱'
P5 = p5run.resolve()
OUT = P6 / 'cleaned-v1'
VERSION = 'p6-quality-v1'


def load(p):
    return json.loads(p.read_text('utf-8'))


def save(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', 'utf-8')


def build(graph, observations, documents, theme_results=None, theme_gap=''):
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
                           'all_stages_completed': bool(theme_audit), 'time_summary_owner': 'P6',
                           'advice_summary_method': 'lossless_rule_aggregation_v1',
                           'semantic_merge_completed': False, 'llm_calls': 0}
    if not theme_audit:
        # 缺主题总结时以前照样写 all_stages_completed=True，产物看着完整，
        # 到导库那一步才报错。这里把缺口写在产物里，别让它悄悄溜过去。
        graph['p6_quality']['missing_stage'] = 'entity_scoped_llm_theme_summary'
        graph['p6_quality']['missing_reason'] = theme_gap or '未提供主题总结结果'
    if theme_audit:
        graph['p6_quality'].update(version='p6-quality-themes-v1',advice_summary_method=theme_audit['method'],
            semantic_merge_completed=True,llm_entity_themes=theme_audit,
            llm_calls='recorded_in_llm_advice_release_usage; offline_rebuild_makes_no_calls')
    graph['nodes'].sort(key=lambda n: n['id'])
    graph['edges'].sort(key=lambda e: (e['source'], e['type'], e['target'], compact(e['properties'])))
    ids = {n['id'] for n in graph['nodes']}
    require(len(ids) == len(graph['nodes']), '节点ID重复')
    require(len({compact(e) for e in graph['edges']}) == len(graph['edges']), '边重复')
    require(all(e['source'] in ids and e['target'] in ids for e in graph['edges']), '边指向不存在的节点')
    return graph, times, advice, advice_audit


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--require-themes', action='store_true',
                    help='缺主题总结时以非零码退出（CI 用；正常重建允许缺）')
    ap.add_argument('--p5-run', help='P5 批次名；默认按 p5run.py 的规则解析')
    ap.add_argument('--no-themes', action='store_true',
                    help='跳过 LLM 主题挂载，先产出可用的中间图（新批次第一遍用）')
    args = ap.parse_args()
    require_themes = args.require_themes
    global P5
    if args.p5_run:
        P5 = p5run.resolve(run_id=args.p5_run)
    stage = P6 / 'quality-stages/route-filtered'
    subprocess.run([sys.executable, str(Path(__file__).with_name('clean_routes.py')),
                    '--output', str(stage), '--p5-run', P5.name],
                   check=True, capture_output=True, text=True, encoding='utf-8')
    observations = {r['record_id']: r for r in load(P5 / 'raw_observations.json')}
    docs = {d['doc_id']: d for d in load(ROOT / 'processed/P1爬虫数据清洗/documents.json')}
    theme_results, theme_gap = None, ''
    theme_release=P6/'llm_advice/release.json'
    if args.no_themes:
        theme_gap = '本次以 --no-themes 构建，未挂载主题总结'
    elif not theme_release.exists():
        theme_gap = '缺少 llm_advice/release.json'
    else:
        spec=load(theme_release)
        path=P6/'llm_advice'/spec['results']
        if not path.exists():
            theme_gap = '发布清单指向的主题结果文件不存在：%s' % spec['results']
        else:
            theme_results=load(path)
            if theme_sha(theme_results)!=spec['results_sha256']:
                raise ValueError('LLM总结结果哈希不一致')
            if not theme_results:
                theme_gap = '主题结果为空'
    graph, times, advice, audit = build(load(stage / 'graph.json'), observations, docs, theme_results, theme_gap)
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
    manifest = {'version': graph['p6_cleaning']['version'], 'stages': graph['p6_quality']['stages'],
                'all_stages_completed': graph['p6_quality']['all_stages_completed'],
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
    if not graph['p6_quality']['all_stages_completed']:
        print('\n[缺阶段] %s：%s' % (graph['p6_quality']['missing_stage'],
                                    graph['p6_quality']['missing_reason']), file=sys.stderr)
        if require_themes:
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
