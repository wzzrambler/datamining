"""Validate P5 graph, produce parameterized import bundle, optionally import Neo4j."""
import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from artifact_integrity import matches_sha256
sys.path.insert(0, str(Path(__file__).resolve().parent))
import p5run
LABELS = {"POI", "TRANSPORT", "ROUTE", "ROUTE_STEP", "TRANSPORT_LEG", "CONDITIONAL_RULE", "FACT", "OBSERVATION", "TIME_SUMMARY", "ADVICE_SUMMARY"}
RELATIONS = {"HAS_STEP", "VISITS", "MENTIONS_MODE", "PRECEDES", "FROM", "TO", "USES_MODE", "USES_LEG", "APPLIES_TO", "PART_OF", "HAS_FACT", "SUPPORTED_BY", "HAS_TIME_SUMMARY", "HAS_ADVICE_SUMMARY", "DERIVED_FROM"}
P5 = p5run.resolve()
CONSTRAINT = "CREATE CONSTRAINT p5_scoped_id IF NOT EXISTS FOR (n:P5Node) REQUIRE (n.dataset, n.id) IS UNIQUE"


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False, sort_keys=True)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def validate_quality(graph_path, graph):
    if not graph.get('p6_quality', {}).get('all_stages_completed'):
        raise ValueError('入库前必须完成quality_pipeline.py三个处理阶段')
    path = graph_path.parent / 'quality_manifest.json'
    if not path.exists():
        raise ValueError('缺少质量流水线清单')
    manifest = json.loads(path.read_text('utf-8'))
    checks = {'graph_sha256': graph_path,
              'source_p5_graph_sha256': P5 / 'integrated/graph.json',
              'raw_observations_sha256': P5 / 'raw_observations.json',
              'documents_sha256': ROOT / 'processed/P1爬虫数据清洗/documents.json'}
    if any(not matches_sha256(p, manifest.get(k)) for k,p in checks.items()):
        raise ValueError('质量流水线图谱或输入已变化，重新运行quality_pipeline.py')
    theme_release=ROOT/'processed/P6知识图谱/llm_advice/release.json'
    if theme_release.exists():
        theme_spec=json.loads(theme_release.read_text('utf-8'))
        if not graph.get('p6_quality',{}).get('llm_entity_themes') or manifest.get('llm_theme_result_sha256')!=theme_spec['results_sha256']:
            raise ValueError('正式图没有包含当前全量LLM主题总结，重新运行quality_pipeline.py')
    return manifest


def properties(value):
    # Neo4j properties cannot be maps or lists of maps. Preserve the complete
    # payload plus JSON-encoded nested fields; keep scalar fields queryable.
    out = {"payload_json": compact(value)}
    for key, item in value.items():
        if key in {"dataset", "id", "edge_id", "payload_json", "json_fields"}:
            continue
        if item is None:
            continue
        if isinstance(item, (str, bool, int, float)):
            out[key] = item
        elif isinstance(item, list) and item and all(type(x) is type(item[0]) and isinstance(x, (str, bool, int, float)) for x in item):
            out[key] = item
        else:
            out[key + "_json"] = compact(item)
    return out


def prepare(graph, dataset):
    identifiers = [n["id"] for n in graph["nodes"]]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Duplicate node IDs")
    ids = set(identifiers)
    nodes, edges = defaultdict(list), defaultdict(list)
    for node in graph["nodes"]:
        if node["label"] not in LABELS:
            raise ValueError("Unregistered node label")
        nodes[node["label"]].append({"id": node["id"], "props": properties(node["properties"])})
    seen = set()
    for edge in graph["edges"]:
        if edge["type"] not in RELATIONS or edge["source"] not in ids or edge["target"] not in ids:
            raise ValueError("Unregistered relation or dangling reference")
        eid = hashlib.sha256(compact(edge).encode()).hexdigest()
        if eid in seen:
            raise ValueError("Duplicate edge")
        seen.add(eid)
        edges[edge["type"]].append({"source": edge["source"], "target": edge["target"],
                                   "edge_id": eid, "props": properties(edge["properties"])})
    queries = [{"query": CONSTRAINT, "parameters": {}}]
    for label, rows in sorted(nodes.items()):
        for start in range(0, len(rows), 500):
            queries.append({"query": f"UNWIND $rows AS row MERGE (n:P5Node {{dataset:$dataset,id:row.id}}) SET n:{label} SET n += row.props RETURN count(n) AS count",
                            "parameters": {"dataset": dataset, "rows": rows[start:start+500]}})
    for relation, rows in sorted(edges.items()):
        for start in range(0, len(rows), 500):
            queries.append({"query": f"UNWIND $rows AS row MATCH (a:P5Node {{dataset:$dataset,id:row.source}}), (b:P5Node {{dataset:$dataset,id:row.target}}) MERGE (a)-[r:{relation} {{dataset:$dataset,edge_id:row.edge_id}}]->(b) SET r += row.props RETURN count(r) AS count",
                            "parameters": {"dataset": dataset, "rows": rows[start:start+500]}})
    return {"dataset": dataset, "queries": queries, "expected_nodes": len(ids), "expected_edges": len(seen),
            "node_labels": dict(Counter(n["label"] for n in graph["nodes"])),
            "edge_types": dict(Counter(e["type"] for e in graph["edges"]))}


def import_bundle(bundle, driver, database):
    for item in bundle["queries"]:
        rows, _, _ = driver.execute_query(item["query"], parameters_=item["parameters"], database_=database)
        expected = len(item["parameters"].get("rows", []))
        if expected and (not rows or rows[0]["count"] != expected):
            raise ValueError("Import dropped nodes/edges; database validation failed")
    query = "MATCH (n:P5Node {dataset:$dataset}) RETURN count(n) AS count"
    rows, _, _ = driver.execute_query(query, dataset=bundle["dataset"], database_=database)
    if rows[0]["count"] != bundle["expected_nodes"]:
        raise ValueError("Node count mismatch")
    rows, _, _ = driver.execute_query("MATCH ()-[r]->() WHERE r.dataset=$dataset RETURN count(r) AS count", dataset=bundle["dataset"], database_=database)
    if rows[0]["count"] != bundle["expected_edges"]:
        raise ValueError("Edge count mismatch")
    return {"nodes": bundle["expected_nodes"], "edges": bundle["expected_edges"], "verified": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "import"])
    parser.add_argument("--graph", type=Path, default=ROOT / "processed/P6知识图谱/cleaned-v1/graph.json")
    parser.add_argument("--output", type=Path, default=ROOT / "processed/P6知识图谱")
    args = parser.parse_args()
    graph = json.loads(args.graph.read_text(encoding="utf-8"))
    if not graph.get("p6_cleaning", {}).get("version"):
        parser.error("正式P6仅接收二次清理后的图谱。先运行 src/P6知识图谱/clean_routes.py；P5原始图不得直接入库。")
    try:
        validate_quality(args.graph, graph)
    except ValueError as error:
        parser.error(str(error))
    # Stable snapshot isolation: changed graph never leaves old edges in view.
    dataset = graph["p6_cleaning"]["version"] + "-" + hashlib.sha256(compact(graph).encode()).hexdigest()[:12]
    bundle = prepare(graph, dataset)
    out = args.output / dataset
    write(out / "import_bundle.json", bundle)
    report = {k: v for k, v in bundle.items() if k != "queries"}
    report["neo4j_imported"] = False
    if args.command == "import":
        from neo4j import GraphDatabase
        uri, user, password = (os.environ.get(k) for k in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"))
        if not uri or not user or not password:
            parser.error("Configure NEO4J_URI, NEO4J_USER and NEO4J_PASSWORD locally")
        with GraphDatabase.driver(uri, auth=(user, password)) as driver:
            driver.verify_connectivity()
            report["validation"] = import_bundle(bundle, driver, os.environ.get("NEO4J_DATABASE", "neo4j"))
        report["neo4j_imported"] = True
    write(out / "validation.json", report)
    (out / "queries.cypher").write_text(
        "// Set $dataset to: " + dataset + "\n"
        "MATCH (r:ROUTE {dataset:$dataset})-[:HAS_STEP]->(s:ROUTE_STEP) OPTIONAL MATCH (s)-[:VISITS]->(p:POI) RETURN r.id,s.index,s.step_id,collect(p.name) ORDER BY r.id,s.index;\n"
        "MATCH (p:POI {dataset:$dataset})-[:HAS_FACT]->(f:FACT) WHERE f.scope_status IS NULL AND f.route_id IS NULL RETURN p.name,f.attribute,f.value_text;\n"
        "MATCH (c:CONDITIONAL_RULE {dataset:$dataset}) WHERE c.scope_status IS NULL OPTIONAL MATCH (c)-[:APPLIES_TO]->(target) RETURN c.id,c.condition_json,c.consequence_text,collect(target.id);\n"
        "MATCH (p:POI {dataset:$dataset})-[:HAS_TIME_SUMMARY]->(t:TIME_SUMMARY) RETURN p.name,t.attribute,t.text_union_json,t.interval_unions_json;\n"
        "MATCH (e:P5Node {dataset:$dataset}) WHERE e.advice_themes_json IS NOT NULL RETURN e.id,e.name,e.scenic_code,e.advice_themes_json ORDER BY e.scenic_code,e.name;\n", encoding="utf-8")
    print(compact(report))


if __name__ == "__main__":
    main()
