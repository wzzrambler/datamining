import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('curation', ROOT / 'src/P6知识图谱/clean_routes.py')
curation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(curation)


class RouteCurationTests(unittest.TestCase):
    def test_repeated_visits_count_as_ordered_stages(self):
        route = {'steps': [{'poi_ids': [p]} for p in ['A', 'B', 'A', 'B', 'A', 'B']]}
        self.assertEqual(len(curation.mapped_stages(route)), 6)
        route.update(route_id='repeat-visit', scenic_code='JZG', summaries=[])
        nodes = {x: {'properties': {'role': 'attraction'}} for x in ['A', 'B']}
        self.assertEqual(curation.classify(route, '', nodes)['status'], 'retained')
        self.assertEqual(len(curation.mapped_stages({'steps': [{'poi_ids': ['B', 'A']}, {'poi_ids': ['A', 'B']}]})), 1)

    def test_affiliation_requires_article_and_source_not_shared_pois(self):
        child = {'route_id': 'C', 'steps': [{'poi_ids': ['B']}, {'poi_ids': ['C']}]}
        parent = {'route_id': 'P', 'steps': [{'poi_ids': [x]} for x in ['A', 'B', 'C', 'D']]}
        spans = {'C': [('doc1', 20, 30)], 'P': [('doc1', 0, 100)]}
        self.assertIsNotNone(curation.affiliation(child, parent, spans))
        spans['C'] = [('doc2', 20, 30)]
        self.assertIsNone(curation.affiliation(child, parent, spans))
        spans['C'] = [('doc1', 120, 130)]
        self.assertIsNone(curation.affiliation(child, parent, spans))

    def test_fragment_scoped_fact_not_promoted_to_global(self):
        payload = {'attribute': 'arrival_time', 'route_id': 'R', 'step_id': 'S', 'value_text': '9点'}
        cleaned = curation.clean_reference_payload(payload, {'R'}, {'S'}, [], 'F')
        self.assertIsNone(cleaned['route_id'])
        self.assertEqual(cleaned['scope_status'], 'excluded_route_fragment_scope')
        self.assertEqual(cleaned['upstream_route_id'], 'R')
        self.assertEqual(payload['route_id'], 'R')

    def test_production_graph_preserves_transport_and_retained_sequences(self):
        p = ROOT / 'processed/P6知识图谱/quality-stages/route-filtered'
        before = curation.load(curation.P5 / 'integrated/graph.json')
        after = curation.load(p / 'graph.json')
        raw_routes = {r['route_id']: r for r in curation.load(curation.P5 / 'integrated/routes.json')}
        for label in ['POI', 'TRANSPORT', 'TRANSPORT_LEG', 'CONDITIONAL_RULE', 'FACT', 'TIME_SUMMARY', 'OBSERVATION']:
            self.assertEqual({n['id'] for n in before['nodes'] if n['label'] == label}, {n['id'] for n in after['nodes'] if n['label'] == label})
        for route in curation.load(p / 'routes.json'):
            self.assertEqual(route['steps'], raw_routes[route['route_id']]['steps'])
            self.assertEqual(route['order_relations'], raw_routes[route['route_id']]['order_relations'])
        ids = {n['id'] for n in after['nodes']}
        self.assertTrue(all(e['source'] in ids and e['target'] in ids for e in after['edges']))
        kept = {r['route_id'] for r in curation.load(p / 'routes.json')}
        excluded = {r['route_id'] for r in curation.load(p / 'route_decisions.json') if r['status'] == 'excluded'}
        self.assertFalse(ids & excluded)
        self.assertEqual(kept, {n['id'] for n in after['nodes'] if n['label'] == 'ROUTE'})

    def test_rebuild_is_deterministic_and_p5_frozen(self):
        p = ROOT / 'processed/P6知识图谱/quality-stages/route-filtered'
        summary = curation.load(p / 'cleaning_summary.json')
        curation.verify_protected_p5(summary['protected_p5_hashes'])
        observations = {r['record_id']: r for r in curation.load(curation.P5 / 'raw_observations.json')}
        docs = {d['doc_id']: d for d in curation.load(ROOT / 'processed/P1爬虫数据清洗/documents.json')}
        result = curation.curate(curation.load(curation.P5 / 'integrated/graph.json'), curation.load(curation.P5 / 'integrated/routes.json'), observations, docs)
        self.assertEqual(result[0], curation.load(p / 'graph.json'))


if __name__ == '__main__':
    unittest.main()
