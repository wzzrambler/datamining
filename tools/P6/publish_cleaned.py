"""Import verified P6 curation, back up and remove only known project snapshots."""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("p6_import", ROOT / "src/P6知识图谱/run.py")
p6 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p6)
OLD_DATASETS = ["regression-v6-20261101-19b062f44ee2", "full-final-20261101-2d888f54232f", "full-final-20261101-34845d3ec7b0"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument('--replace-dataset', help='只允许替换release.json当前指向的项目版本')
    args = parser.parse_args()
    if args.replace_dataset:
        current = json.loads((ROOT / 'processed/P6知识图谱/release.json').read_text('utf-8'))
        if current['dataset'] != args.replace_dataset:
            parser.error('替换版本必须等于当前release；拒绝影响其他数据库数据')
        OLD_DATASETS.append(args.replace_dataset)
    graph_path = ROOT / "processed/P6知识图谱/cleaned-v1/graph.json"
    graph = json.loads(graph_path.read_text("utf-8"))
    assert graph.get("p6_cleaning", {}).get("route_fragments_in_production_graph") is False
    assert graph.get('p6_quality', {}).get('all_stages_completed'), 'Run quality_pipeline.py before publishing'
    p6.validate_quality(graph_path, graph)
    dataset = graph["p6_cleaning"]["version"] + "-" + hashlib.sha256(p6.compact(graph).encode()).hexdigest()[:12]
    folder = ROOT / "processed/P6知识图谱" / dataset
    bundle = p6.prepare(graph, dataset)
    p6.write(folder / "import_bundle.json", bundle)
    from neo4j import GraphDatabase
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    with GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]), max_transaction_retry_time=0) as driver:
        driver.verify_connectivity()
        def query(q, **params):
            return [dict(r) for r in driver.execute_query(q, parameters_=params, database_=database)[0]]
        # Back up exact known datasets before any database deletion.
        old_nodes = query("MATCH (n:P5Node) WHERE n.dataset IN $old RETURN n.id AS id,labels(n) AS labels,properties(n) AS properties", old=OLD_DATASETS)
        old_edges = query("MATCH (a)-[r]->(b) WHERE r.dataset IN $old RETURN a.id AS source,b.id AS target,type(r) AS type,properties(r) AS properties", old=OLD_DATASETS)
        if old_nodes:
            args.backup.mkdir(parents=True, exist_ok=True)
            backup = args.backup / "neo4j_previous_datasets.json"
            if backup.exists():
                raise ValueError("Do not overwrite existing database backup; choose a fresh directory")
            p6.write(backup, {"datasets": OLD_DATASETS, "nodes": old_nodes, "edges": old_edges})
            backup_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
        else:
            backup_hash = None
        result = p6.import_bundle(bundle, driver, database)
        p6.import_bundle(bundle, driver, database)
        readback = query("MATCH (n:P5Node {dataset:$dataset}) RETURN n.id AS id,n.payload_json AS payload", dataset=dataset)
        expected = {n["id"]: n["properties"] for n in graph["nodes"]}
        assert len(readback) == len(expected)
        assert all(json.loads(n["payload"]) == expected[n["id"]] for n in readback)
        edge_back = query("MATCH (a:P5Node {dataset:$dataset})-[r]->(b:P5Node {dataset:$dataset}) WHERE r.dataset=$dataset RETURN a.id AS source,type(r) AS type,b.id AS target,r.payload_json AS payload", dataset=dataset)
        edge_signature = lambda e: (e['source'], e['type'], e['target'], p6.compact(e['properties']))
        assert Counter(edge_signature(e) for e in graph['edges']) == Counter((e['source'],e['type'],e['target'],p6.compact(json.loads(e['payload']))) for e in edge_back)
        queries = {
            "route_catalog": "MATCH (r:ROUTE {dataset:$dataset}) RETURN r.id AS route_id,r.scenic_code AS scenic_code,r.p6_curation_json AS curation ORDER BY scenic_code,route_id",
            "official_route_steps": "MATCH (r:ROUTE {dataset:$dataset,id:'JZG-OFF-01-R-5489d330c8'})-[:HAS_STEP]->(s:ROUTE_STEP) OPTIONAL MATCH (s)-[:VISITS]->(p:POI) RETURN s.index AS step_index,s.id AS step_id,collect(p.name) AS pois ORDER BY step_index",
            "commute": "MATCH (l:TRANSPORT_LEG {dataset:$dataset})-[:FROM]->(a:POI {name:'成都天府机场'}) MATCH (l)-[:TO]->(b:POI) OPTIONAL MATCH (l)-[:HAS_FACT]->(f:FACT) RETURN l.id AS leg_id,a.name AS origin,b.name AS destination,collect([f.attribute,f.value_text]) AS facts ORDER BY leg_id",
            "node_types": "MATCH (n:P5Node {dataset:$dataset}) UNWIND [l IN labels(n) WHERE l <> 'P5Node'] AS label RETURN label,count(n) AS count ORDER BY label",
            "edge_types": "MATCH ()-[r]->() WHERE r.dataset=$dataset RETURN type(r) AS type,count(r) AS count ORDER BY type",
            "advice_summaries": "MATCH (s:ADVICE_SUMMARY {dataset:$dataset}) RETURN count(s) AS total,sum(CASE WHEN s.scope_status IS NULL AND s.target_id IS NOT NULL THEN 1 ELSE 0 END) AS default_targeted,sum(CASE WHEN s.scope_status IS NOT NULL THEN 1 ELSE 0 END) AS quarantined,sum(CASE WHEN s.scope_status IS NULL AND s.target_id IS NULL THEN 1 ELSE 0 END) AS unlocated",
            "entity_themes": "MATCH (n:P5Node {dataset:$dataset}) WHERE n.advice_themes_json IS NOT NULL RETURN count(n) AS entities_with_themes",
        }
        proof = {name: {"query": q, "parameters": {"dataset": dataset}, "rows": query(q, dataset=dataset)} for name, q in queries.items()}
        assert len(proof["official_route_steps"]["rows"]) == 15
        route_count = sum(n['label'] == 'ROUTE' for n in graph['nodes'])
        assert len(proof["route_catalog"]["rows"]) == route_count
        assert len(proof["commute"]["rows"]) > 0
        assert proof["entity_themes"]["rows"][0]["entities_with_themes"] > 0
        excluded = json.loads((graph_path.parent / "route_decisions.json").read_text("utf-8"))
        excluded_ids = [r["route_id"] for r in excluded if r["status"] == "excluded"]
        assert query("MATCH (r:ROUTE {dataset:$dataset}) WHERE r.id IN $ids RETURN count(r) AS count", dataset=dataset, ids=excluded_ids)[0]["count"] == 0
        # Refuse to detach anything linked to unrelated data.
        cross = query("MATCH (n:P5Node)-[r]-(m) WHERE n.dataset IN $old AND NOT coalesce(m.dataset,'') IN $old RETURN count(r) AS count", old=OLD_DATASETS)[0]["count"]
        assert cross == 0, "Historical data has links to an unrelated dataset; leave it untouched"
        # User requested that only the cleaned graph remain in this project DB.
        removed = query("MATCH (n:P5Node) WHERE n.dataset IN $old DETACH DELETE n RETURN count(n) AS removed", old=OLD_DATASETS)[0]["removed"]
        inventory = query("MATCH (n:P5Node) RETURN n.dataset AS dataset,count(n) AS nodes ORDER BY dataset")
        assert all(row["dataset"] not in OLD_DATASETS for row in inventory)
        result.update(dataset=dataset, reimport_no_duplicates=True, all_node_payloads_roundtrip=True,
                      all_edge_payloads_roundtrip=True, excluded_routes_in_database=0,
                      removed_old_project_nodes=removed, old_database_backup_sha256=backup_hash,
                      old_database_backup=str(args.backup / "neo4j_previous_datasets.json") if backup_hash else None,
                      remaining_datasets=inventory, source_graph_sha256=hashlib.sha256(graph_path.read_bytes()).hexdigest())
    p6.write(folder / "live_verification.json", result)
    p6.write(folder / "validation.json", {**{k:v for k,v in bundle.items() if k != 'queries'}, "neo4j_imported": True, "validation": result})
    p6.write(folder / "cleaned_queries.json", proof)
    p6.write(ROOT / "processed/P6知识图谱/release.json", {"dataset": dataset, "input_graph": "cleaned-v1/graph.json", "snapshot_directory": dataset,
              "nodes": len(graph['nodes']), "edges": len(graph['edges']), "routes": route_count, "verified": True, "cleaning_version": graph['p6_cleaning']['version'],
              "p5_raw_observations_unchanged": True, "quality_ownership_migrated_to_p6": True,
              "quality_manifest": "cleaned-v1/quality_manifest.json", "reports_regenerated": False})
    p6.write(ROOT / "outputs/P6/验证/completion.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
