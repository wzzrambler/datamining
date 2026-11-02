import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("p6", ROOT / "src/P6知识图谱/run.py")
p6 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p6)


class P6ContractTests(unittest.TestCase):
    def test_production_quality_manifest_is_valid(self):
        path = ROOT / 'processed/P6知识图谱/cleaned-v1/graph.json'
        graph = json.loads(path.read_text('utf-8'))
        p6.validate_quality(path, graph)

    def test_changed_graph_is_rejected_before_import(self):
        source = ROOT / 'processed/P6知识图谱/cleaned-v1'
        parent = ROOT / '.test_tmp'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temp:
            folder = Path(temp)
            graph = json.loads((source / 'graph.json').read_text('utf-8'))
            graph['nodes'][0]['properties']['tampered'] = True
            path = folder / 'graph.json'
            path.write_bytes((json.dumps(graph, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
            (folder / 'quality_manifest.json').write_bytes((source / 'quality_manifest.json').read_bytes())
            with self.assertRaisesRegex(ValueError, '图谱或输入已变化'):
                p6.validate_quality(path, graph)

    def test_nested_properties_roundtrip_and_scoped_import(self):
        props = {"name": "剧场", "facts": [{"attribute": "visit_time", "value_text": "11:00"}], "rule_ids": ["RULE-A"], "empty": [], "none": None}
        encoded = p6.properties(props)
        self.assertEqual(json.loads(encoded["payload_json"]), props)
        self.assertEqual(json.loads(encoded["facts_json"]), props["facts"])
        self.assertEqual(encoded["rule_ids"], ["RULE-A"])
        graph = {"nodes": [{"id": "A", "label": "POI", "properties": props}, {"id": "B", "label": "FACT", "properties": {}}],
                 "edges": [{"source": "A", "target": "B", "type": "HAS_FACT", "properties": {}}]}
        bundle = p6.prepare(graph, "test-snapshot")
        self.assertEqual(bundle["expected_edges"], 1)
        for query in bundle["queries"][1:]:
            self.assertEqual(query["parameters"]["dataset"], "test-snapshot")
            self.assertNotIn("DELETE", query["query"])
        graph["edges"][0]["target"] = "missing"
        with self.assertRaises(ValueError):
            p6.prepare(graph, "test")

    def test_import_checks_counts(self):
        class Driver:
            def execute_query(self, query, parameters_=None, **kwargs):
                if parameters_ and "rows" in parameters_:
                    count = len(parameters_["rows"])
                elif query.startswith("CREATE CONSTRAINT"):
                    return [], None, None
                else:
                    count = 1
                return [{"count": count}], None, None
        graph = {"nodes": [{"id": "A", "label": "POI", "properties": {}}],
                 "edges": [{"source": "A", "target": "A", "type": "VISITS", "properties": {}}]}
        result = p6.import_bundle(p6.prepare(graph, "test"), Driver(), "neo4j")
        self.assertTrue(result["verified"])
