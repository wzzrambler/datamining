"""Import the published P6 graph and source evidence as one PostgreSQL release."""
import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import create_engine, text

from .db import REPO, database_url, execute, row, setting

sys.path.insert(0, str(REPO / 'src'))
from artifact_integrity import matches_sha256, repository_path

AREAS = {
    'JZG': ('九寨沟风景名胜区', ['九寨沟', '九寨']),
    'GZCL': ('广州长隆旅游度假区', ['广州长隆', '野生动物世界', '长隆欢乐世界']),
    'ZHCL': ('珠海横琴长隆国际海洋度假区', ['珠海长隆', '海洋王国', '横琴长隆', '飞船乐园']),
}


def load(path):
    return json.loads(path.read_text('utf-8-sig'))


def digest(path, normalize=False):
    content = path.read_bytes()
    if normalize:
        content = content.replace(b'\r\n', b'\n')
    return hashlib.sha256(content).hexdigest()


def import_release(conn, root):
    release_path = root / 'release.json'
    release = load(release_path)
    version = release['dataset']
    graph_path = repository_path(root, release['input_graph'])
    manifest_path = repository_path(root, release['quality_manifest'])
    document_path = REPO / 'processed/P1爬虫数据清洗/documents.json'
    manifest = load(manifest_path)
    if not manifest['all_stages_completed'] or not matches_sha256(graph_path, manifest['graph_sha256']):
        raise ValueError('P6 图谱与质量清单不一致')
    if not matches_sha256(document_path, manifest['documents_sha256']):
        raise ValueError('P6 来源文档与质量清单不一致')
    graph = load(graph_path)
    docs = load(document_path)
    docs = docs['documents'] if isinstance(docs, dict) else docs
    nodes, edges = graph['nodes'], graph['edges']
    if (len(nodes), len(edges)) != (release['nodes'], release['edges']):
        raise ValueError('P6 图谱数量与发布清单不一致')
    counts = Counter(n['label'] for n in nodes)
    if counts['ROUTE'] != release['routes']:
        raise ValueError('正式路线数量与发布清单不一致')
    artifacts = [(release_path, None), (manifest_path, None), (graph_path, len(nodes)), (document_path, len(docs))]
    node_index = {n['id']: n for n in nodes}
    projections = {}
    for file, label, key in (
        ('routes', 'ROUTE', 'route_id'), ('transport_legs', 'TRANSPORT_LEG', 'leg_id'),
        ('entities', None, 'id'), ('entity_time_summaries', 'TIME_SUMMARY', 'time_summary_id'),
        ('condition_recommendation_summaries', 'ADVICE_SUMMARY', 'advice_summary_id'),
        ('conditions', 'CONDITIONAL_RULE', 'rule_id')
    ):
        path = graph_path.with_name(file + '.json')
        items = load(path)
        artifacts.append((path, len(items)))
        if label and {n['id'] for n in nodes if n['label'] == label} != {item[key] for item in items}:
            raise ValueError(f'{file} 的 ID 集合与图谱不一致')
        for item in items:
            source = node_index.get(item[key])
            if not source or (label and source['label'] != label) or any(
                value != source['properties'].get(field) for field, value in item.items()
            ):
                raise ValueError(f'{file} 记录与正式图属性不一致：{item[key]}')
        projections[file] = items
    theme_path = graph_path.with_name('entity_advice_theme_summaries.json')
    theme_groups = load(theme_path)
    theme_count = sum(len(g['topics']) for g in theme_groups)
    if theme_count != manifest['llm_theme_count']:
        raise ValueError('主题数量与质量清单不一致')
    artifacts.append((theme_path, theme_count))
    artifact_rows = [{'dataset_id': version, 'artifact_key': f.stem,
                      'source_relative_path': f.relative_to(REPO).as_posix(),
                      'raw_sha256': digest(f), 'normalized_sha256': digest(f, True),
                      'record_count': count} for f, count in artifacts]
    fingerprint = digest(graph_path, True)
    conn.exec_driver_sql("SELECT pg_advisory_xact_lock(78156001)")
    existing = row(conn, 'SELECT graph_sha256 FROM p6_kg.dataset WHERE dataset_id=:v', v=version)
    if existing:
        actual = conn.execute(text(
            'SELECT artifact_key, normalized_sha256 FROM p6_kg.artifact WHERE dataset_id=:v'), {'v': version}
        ).all()
        if existing['graph_sha256'] != fingerprint or dict(actual) != {
            x['artifact_key']: x['normalized_sha256'] for x in artifact_rows
        }:
            raise ValueError('相同发布 ID 的文件已变化，需发布新版本')
    else:
        insert(conn, 'dataset', [{'dataset_id': version, 'graph_sha256': fingerprint,
                                  'node_count': len(nodes), 'edge_count': len(edges), 'manifest': manifest}], 'manifest')
        insert(conn, 'artifact', artifact_rows)
        insert(conn, 'source_document', [{
            'dataset_id': version, 'doc_id': d['doc_id'], 'scenic_code': d['scenic_code'],
            'title': d.get('title') or d['doc_id'], 'source_url': d.get('source_url'),
            'published_text': d.get('publish_date'), 'content': d['content'],
            'paragraphs': d.get('paragraphs') or [],
        } for d in docs], 'paragraphs')
        insert(conn, 'node', [dict(dataset_id=version, node_id=n['id'], label=n['label'],
                                    properties=n['properties']) for n in nodes], 'properties')
        insert(conn, 'edge', [dict(dataset_id=version, edge_no=i, source_id=e['source'],
                                    target_id=e['target'], type=e['type'], properties=e['properties'])
                              for i, e in enumerate(edges, 1)], 'properties')
        load_projections(conn, version, nodes, projections, theme_groups)

    expected = {'node': len(nodes), 'edge': len(edges), 'route': counts['ROUTE'],
                'route_step': counts['ROUTE_STEP'], 'poi': counts['POI'],
                'transport_leg': counts['TRANSPORT_LEG'], 'advice_theme': theme_count,
                'source_document': len(docs)}
    for table, count in expected.items():
        found = row(conn, f'SELECT count(*) AS n FROM p6_kg.{table} WHERE dataset_id=:v', v=version)
        if found['n'] != count:
            raise ValueError(f'{table} 导入数量不一致')
    execute(conn, 'UPDATE p6_kg.dataset SET is_active=false WHERE is_active AND dataset_id<>:v', {'v': version})
    execute(conn, 'UPDATE p6_kg.dataset SET is_active=true WHERE dataset_id=:v', {'v': version})
    return {'data_version': version, **expected, 'already_imported': bool(existing)}


def insert(conn, table, values, *json_fields):
    if values:
        fields = list(values[0])
        execute(conn, f'INSERT INTO p6_kg.{table} ({",".join(fields)}) VALUES ({",".join(":"+f for f in fields)})',
                values, *json_fields)


def load_projections(conn, version, nodes, p, themes):
    def write(table, records, *fields):
        insert(conn, table, [dict(dataset_id=version, **record) for record in records], *fields)

    write('scenic_area', [dict(scenic_code=code, name=name, aliases=aliases)
                         for code, (name, aliases) in AREAS.items()], 'aliases')
    write('poi', [dict(poi_id=n['id'], **{k: n['properties'].get(k) for k in
                                        ('scenic_code', 'name', 'role', 'subtype')})
                  for n in nodes if n['label'] == 'POI'])
    write('route', [dict(
        route_id=r['route_id'], scenic_code=r['scenic_code'], route_class=r['p6_curation']['route_class'],
        itinerary_scope=r['p6_curation']['itinerary_scope'],
        summary_text=next((s['text'] for s in r.get('summaries', []) if s.get('text')), None),
        step_count=len(r['steps']), mapped_stage_count=r['p6_curation']['ordered_mapped_stage_count'],
        hierarchy=r.get('hierarchy') or []
    ) for r in p['routes']], 'hierarchy')
    write('route_step', [dict(step_id=s['step_id'], route_id=r['route_id'], seq=i,
                              source_day_no=(s.get('time') or {}).get('day'),
                              period=(s.get('time') or {}).get('period'), order_group=s.get('order_group'),
                              time_meta=s.get('time') or {})
                         for r in p['routes'] for i, s in enumerate(r['steps'], 1)], 'time_meta')
    write('step_poi', [dict(step_id=s['step_id'], poi_id=poi)
                       for r in p['routes'] for s in r['steps'] for poi in dict.fromkeys(s.get('poi_ids') or [])])
    write('transport_leg', [dict(leg_id=x['leg_id'], scenic_code=x['scenic_code'], from_poi_id=x['from_entity_id'],
                                 to_poi_id=x['to_entity_id'], mode_id=x.get('transport_entity_id'),
                                 service_name=x.get('service_name_text')) for x in p['transport_legs']])
    write('advice_theme', [dict(theme_id=t['topic_id'], target_id=g['entity_id'],
                               scenic_code=g['scenic_code'], advice_summary_id=g['advice_summary_id'],
                               title=t['label'], summary=t['summary'], source_item_ids=t['source_item_ids'],
                               source_record_ids=t['source_record_ids'],
                               current_validity_verified=t['current_validity_verified'])
                           for g in themes for t in g['topics']], 'source_item_ids', 'source_record_ids')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=REPO / 'processed/P6知识图谱')
    parser.add_argument('--grant-local', action='store_true')
    args = parser.parse_args()
    db = create_engine(setting('P7_1_MIGRATION_URL') or database_url(), hide_parameters=True)
    with db.begin() as conn:
        result = import_release(conn, args.source.resolve())
        if args.grant_local:
            conn.exec_driver_sql('GRANT USAGE ON SCHEMA p6_kg,p7_app TO p7_1_runtime')
            conn.exec_driver_sql('GRANT SELECT ON ALL TABLES IN SCHEMA p6_kg TO p7_1_runtime')
            conn.exec_driver_sql('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA p7_app TO p7_1_runtime')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
