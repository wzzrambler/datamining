"""Verify real imports, idempotence, graph queries and nested property round-trip."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("p6", ROOT / "src/P6知识图谱/run.py")
p6 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p6)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
    if not bundle["dataset"].startswith("p6-quality-"):
        parser.error("仅验证/导入清理后P6正式包，禁止将旧P5图重新入库。")
    graph_path = ROOT / 'processed/P6知识图谱/cleaned-v1/graph.json'
    graph = json.loads(graph_path.read_text('utf-8'))
    p6.validate_quality(graph_path, graph)
    expected_dataset = graph['p6_cleaning']['version'] + '-' + hashlib.sha256(p6.compact(graph).encode()).hexdigest()[:12]
    if bundle != p6.prepare(graph, expected_dataset):
        parser.error('导入包与当前三阶段质量图不一致，重新prepare')
    from neo4j import GraphDatabase
    with GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])) as driver:
        driver.verify_connectivity()
        result = p6.import_bundle(bundle, driver, os.environ.get("NEO4J_DATABASE", "neo4j"))
        result["reimport_no_duplicates"] = True
        queries = {
            "route_steps": "MATCH (r:ROUTE {dataset:$dataset})-[:HAS_STEP]->(s:ROUTE_STEP) OPTIONAL MATCH (s)-[:VISITS]->(p:POI) RETURN r.id AS route_id,s.index AS step_index,collect(p.name) AS pois ORDER BY route_id,step_index LIMIT 30",
            "facts": "MATCH (p:POI {dataset:$dataset})-[:HAS_FACT]->(f:FACT) RETURN p.name AS name,f.attribute AS attribute,f.value_text AS value LIMIT 20",
            "conditions": "MATCH (c:CONDITIONAL_RULE {dataset:$dataset}) OPTIONAL MATCH (c)-[:APPLIES_TO]->(target) RETURN c.id AS rule_id,c.condition_json AS condition,c.consequence_text AS consequence,collect(target.id) AS targets LIMIT 20",
            "time_summaries": "MATCH (p:POI {dataset:$dataset})-[:HAS_TIME_SUMMARY]->(t:TIME_SUMMARY) RETURN p.name AS name,t.attribute AS attribute,t.text_union_json AS text_union,t.interval_unions_json AS interval_unions LIMIT 20",
            "advice_summaries": "MATCH (p:P5Node {dataset:$dataset})-[:HAS_ADVICE_SUMMARY]->(s:ADVICE_SUMMARY) WHERE s.scope_status IS NULL RETURN p.id AS target_id,s.scenic_code AS scenic_code,s.category_counts_json AS categories LIMIT 20",
            "shared_pois": "MATCH (r:ROUTE {dataset:$dataset})-[:HAS_STEP]->(:ROUTE_STEP)-[:VISITS]->(p:POI) WITH p,collect(DISTINCT r.id) AS routes WHERE size(routes)>1 RETURN p.name AS name,routes LIMIT 20",
        }
        for name, query in queries.items():
            rows, _, _ = driver.execute_query(query, dataset=bundle["dataset"], database_=os.environ.get("NEO4J_DATABASE", "neo4j"))
            result[name] = [dict(r) for r in rows]
        rows, _, _ = driver.execute_query("MATCH (n:P5Node {dataset:$dataset}) RETURN n.id AS id,n.payload_json AS payload", dataset=bundle["dataset"], database_=os.environ.get("NEO4J_DATABASE", "neo4j"))
        expected = {r["id"]: r["props"]["payload_json"] for q in bundle["queries"] for r in q["parameters"].get("rows", []) if "id" in r}
        assert all(json.loads(r["payload"]) == json.loads(expected[r["id"]]) for r in rows)
        result["all_node_payloads_roundtrip"] = True
    p6.write(args.bundle.parent / "live_verification.json", result)
    print(json.dumps({k: v for k, v in result.items() if not isinstance(v, list)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
