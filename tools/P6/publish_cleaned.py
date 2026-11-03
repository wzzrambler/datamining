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


def require(condition, message):
    """Runtime safety checks must remain active when Python uses -O."""
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, help="Backup directory required with --apply")
    parser.add_argument("--apply", action="store_true", help="Actually import and clean up old datasets (writes Neo4j)")
    parser.add_argument('--replace-dataset', help='只允许替换release.json当前指向的项目版本')
    args = parser.parse_args()
    if args.apply and args.backup is None:
        parser.error("--apply requires --backup DIR; refusing any database changes")
    old_datasets = list(OLD_DATASETS)
    if args.replace_dataset:
        current = json.loads((ROOT / 'processed/P6知识图谱/release.json').read_text('utf-8'))
        if current['dataset'] != args.replace_dataset:
            parser.error('替换版本必须等于当前release；拒绝影响其他数据库数据')
        old_datasets.append(args.replace_dataset)
    graph_path = ROOT / "processed/P6知识图谱/cleaned-v1/graph.json"
    graph = json.loads(graph_path.read_text("utf-8"))
    require(graph.get("p6_cleaning", {}).get("route_fragments_in_production_graph") is False,
            "The production graph must exclude route fragments")
    require(graph.get('p6_quality', {}).get('all_stages_completed'),
            'Run quality_pipeline.py before publishing')
    p6.validate_quality(graph_path, graph)
    dataset = graph["p6_cleaning"]["version"] + "-" + hashlib.sha256(p6.compact(graph).encode()).hexdigest()[:12]
    if dataset in old_datasets:
        parser.error("Refusing to delete the same dataset that would be imported; choose a new verified graph version first")
    folder = ROOT / "processed/P6知识图谱" / dataset
    bundle = p6.prepare(graph, dataset)
    if not args.apply:
        # A local-only preview. No database connection or file writes.
        print(json.dumps({"mode": "preview_only", "dataset_to_import": dataset,
                          "planned_old_datasets": old_datasets,
                          "new_nodes": bundle["expected_nodes"], "new_edges": bundle["expected_edges"],
                          "note": "No Neo4j connection, import, or deletion was performed."},
                         ensure_ascii=False, indent=2))
        return
    p6.write(folder / "import_bundle.json", bundle)
    from neo4j import GraphDatabase
    database = os.environ.get("NEO4J_DATABASE", "neo4j")
    with GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]), max_transaction_retry_time=0) as driver:
        driver.verify_connectivity()
        def query(q, **params):
            return [dict(r) for r in driver.execute_query(q, parameters_=params, database_=database)[0]]
        # Refuse a dangerous cleanup before any import or deletion.
        cross_query = ("MATCH (n:P5Node)-[r]-(m) WHERE n.dataset IN $old "
                       "AND NOT coalesce(m.dataset,'') IN $old RETURN count(r) AS count")
        cross = query(cross_query, old=old_datasets)[0]["count"]
        require(cross == 0, "Old project nodes have relationships to unrelated datasets; no cleanup allowed")
        # Back up all edges incident to old nodes, not only edges tagged with old dataset IDs.
        old_nodes = query("MATCH (n:P5Node) WHERE n.dataset IN $old RETURN n.id AS id,labels(n) AS labels,properties(n) AS properties", old=old_datasets)
        old_edges = query(
            "MATCH (a)-[r]->(b) WHERE (a:P5Node AND a.dataset IN $old) "
            "OR (b:P5Node AND b.dataset IN $old) "
            "RETURN a.id AS source,a.dataset AS source_dataset,"
            "b.id AS target,b.dataset AS target_dataset,"
            "type(r) AS type,properties(r) AS properties", old=old_datasets)
        require(bool(old_nodes) or not old_edges,
                "Unexpected old relationships without matching old nodes; refuse cleanup")
        if old_nodes:
            args.backup.mkdir(parents=True, exist_ok=True)
            backup = args.backup / "neo4j_previous_datasets.json"
            if backup.exists():
                raise ValueError("Do not overwrite existing database backup; choose a fresh directory")
            p6.write(backup, {"datasets": old_datasets, "nodes": old_nodes, "edges": old_edges})
            saved = json.loads(backup.read_text(encoding="utf-8"))
            require(saved == {"datasets": old_datasets, "nodes": old_nodes, "edges": old_edges},
                    "Backup round-trip verification failed; refusing database modifications")
            backup_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
        else:
            backup_hash = None
        result = p6.import_bundle(bundle, driver, database)
        first = query("MATCH (n:P5Node {dataset:$dataset}) RETURN count(n) AS nodes", dataset=dataset)[0]
        first_edges = query("MATCH ()-[r]->() WHERE r.dataset=$dataset RETURN count(r) AS edges", dataset=dataset)[0]
        # 同一份 bundle 再导一次。MERGE 键如果不幂等，节点或关系会在这里变多。
        p6.import_bundle(bundle, driver, database)
        second = query("MATCH (n:P5Node {dataset:$dataset}) RETURN count(n) AS nodes", dataset=dataset)[0]
        second_edges = query("MATCH ()-[r]->() WHERE r.dataset=$dataset RETURN count(r) AS edges", dataset=dataset)[0]
        reimport_no_duplicates = (first["nodes"], first_edges["edges"]) == (second["nodes"], second_edges["edges"])
        require(reimport_no_duplicates, "同一 bundle 重复导入后节点或关系数发生变化，导入不幂等")
        readback = query("MATCH (n:P5Node {dataset:$dataset}) RETURN n.id AS id,n.payload_json AS payload", dataset=dataset)
        expected = {n["id"]: n["properties"] for n in graph["nodes"]}
        node_count_matches = len(readback) == len(expected)
        require(node_count_matches, "Imported node count mismatch")
        node_roundtrip = all(json.loads(n["payload"]) == expected[n["id"]] for n in readback)
        require(node_roundtrip, "Imported node payloads do not match source")
        edge_back = query("MATCH (a:P5Node {dataset:$dataset})-[r]->(b:P5Node {dataset:$dataset}) WHERE r.dataset=$dataset RETURN a.id AS source,type(r) AS type,b.id AS target,r.payload_json AS payload", dataset=dataset)
        edge_signature = lambda e: (e['source'], e['type'], e['target'], p6.compact(e['properties']))
        edge_roundtrip = Counter(edge_signature(e) for e in graph['edges']) == \
            Counter((e['source'], e['type'], e['target'], p6.compact(json.loads(e['payload'])))
                    for e in edge_back)
        require(edge_roundtrip, 'Imported edges do not match source')
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
        require(len(proof["official_route_steps"]["rows"]) == 15,
                "Official route steps check failed")
        route_count = sum(n['label'] == 'ROUTE' for n in graph['nodes'])
        require(len(proof["route_catalog"]["rows"]) == route_count,
                "Route catalog count mismatch")
        require(len(proof["commute"]["rows"]) > 0, "Commuting facts are missing")
        require(proof["entity_themes"]["rows"] and
                proof["entity_themes"]["rows"][0]["entities_with_themes"] > 0,
                "Entity themes are missing")
        excluded = json.loads((graph_path.parent / "route_decisions.json").read_text("utf-8"))
        excluded_ids = [r["route_id"] for r in excluded if r["status"] == "excluded"]
        excluded_in_database = query("MATCH (r:ROUTE {dataset:$dataset}) WHERE r.id IN $ids RETURN count(r) AS count",
                                     dataset=dataset, ids=excluded_ids)[0]["count"]
        require(excluded_in_database == 0, "Excluded routes unexpectedly exist in production graph")
        # Recheck cross-dataset relationships immediately before deleting.
        cross = query(cross_query, old=old_datasets)[0]["count"]
        require(cross == 0, "Historical data is linked to unrelated datasets; refusing to delete")
        # User requested that only the cleaned graph remain in this project DB.
        removed = query("MATCH (n:P5Node) WHERE n.dataset IN $old DETACH DELETE n RETURN count(n) AS removed", old=old_datasets)[0]["removed"]
        inventory = query("MATCH (n:P5Node) RETURN n.dataset AS dataset,count(n) AS nodes ORDER BY dataset")
        require(all(row["dataset"] not in old_datasets for row in inventory),
                "Old datasets still appear after cleanup")
        result.update(dataset=dataset, reimport_no_duplicates=reimport_no_duplicates,
                      all_node_payloads_roundtrip=node_count_matches and node_roundtrip,
                      all_edge_payloads_roundtrip=edge_roundtrip,
                      excluded_routes_in_database=excluded_in_database,
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
