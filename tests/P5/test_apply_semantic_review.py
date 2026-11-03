"""P5 approved semantic revision is isolated and keeps graph references valid."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('p5_semantic_apply_tool', ROOT / 'tools/P5/apply_semantic_review.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class SemanticApplyTests(unittest.TestCase):
    def test_prepare_does_not_assume_approval(self):
        decision = tool.prepare(ROOT)
        self.assertEqual(len(decision['items']), 3)
        self.assertTrue(all(i['human_decision'] == 'pending' for i in decision['items']))
        self.assertTrue(all(not i['reviewer'] for i in decision['items']))

    def test_reject_pending_and_prevent_official_output(self):
        decision = tool.prepare(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / 'decision.json'
            tool.write_json(inp, decision)
            with self.assertRaisesRegex(ValueError, '未批准'):
                tool.apply(ROOT, inp, Path(tmp) / 'candidate')
            for item in decision['items']:
                item['human_decision'] = 'approved'; item['reviewer'] = '人工核验测试'
            tool.write_json(inp, decision)
            with self.assertRaisesRegex(ValueError, '拒绝在正式数据'):
                tool.apply(ROOT, inp, ROOT / tool.RUN / 'incorrect_overwrite')

    def test_build_full_candidate_without_changing_original(self):
        decision = tool.prepare(ROOT)
        for item in decision['items']:
            item['human_decision'] = 'approved'; item['reviewer'] = '人工审核测试'
        src = ROOT / tool.RUN / 'conditions.json'
        original_sha = tool.sha256(src)
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / 'decision.json'
            dest = Path(tmp) / 'candidate'
            tool.write_json(inp, decision)
            result = tool.apply(ROOT, inp, dest)
            self.assertEqual(result['rule_count'], 1358)
            self.assertEqual(len(result['changed_rules']), 2)
            self.assertEqual(result['reviewed_rule_count'], 3)
            self.assertEqual(result['subjective_weak_advice_count'], 1)
            self.assertEqual(tool.sha256(src), original_sha)
            self.assertEqual(len(tool.read_json(dest / 'graph.json')['nodes']), result['nodes'])
            new_rules = tool.read_json(dest / 'conditions.json')
            ids = set(result['changed_rules'].values())
            self.assertTrue(all(r['statement_kind'] == 'conditional_fact' and r['force'] == 'descriptive'
                                and not r['p5_complete'] for r in new_rules if r['rule_id'] in ids))
            self.assertEqual(len([r for r in new_rules if r['rule_id'] in ids]), 2)
            subjective_id = 'RULE-2b7dd1bc24a1e219d6be'
            subjective = next(r for r in new_rules if r['rule_id'] == subjective_id)
            self.assertEqual(subjective['statement_kind'], 'conditional_advice')
            self.assertEqual(subjective['force'], 'recommended')
            self.assertTrue(subjective['p5_complete'])
            self.assertEqual(subjective['semantic_review']['evidence_category'], 'subjective_evaluation')
            self.assertEqual(subjective['semantic_review']['recommendation_strength'], 'subjective_weak')
            self.assertEqual(subjective['semantic_review']['source_claim_status'], 'author_opinion')
            graph = tool.read_json(dest / 'graph.json')
            subjective_graph_node = next(n for n in graph['nodes'] if n['id'] == subjective_id)
            self.assertEqual(subjective_graph_node['properties']['semantic_review']['recommendation_strength'], 'subjective_weak')
            self.assertEqual(len(tool.read_csv(dest / 'formal_mapping_candidate.csv')[1]), 291)
            self.assertEqual(len(tool.read_csv(dest / 'valid_advice_candidate.csv')[1]), 289)
            self.assertEqual(len(tool.read_csv(dest / 'reclassified_observations.csv')[1]), 2)
            self.assertEqual(len(tool.read_csv(dest / 'subjective_weak_advice.csv')[1]), 1)
            candidate_fields, candidate_csv = tool.read_csv(dest / 'conditions.csv')
            self.assertIn('recommendation_strength', candidate_fields)
            subjective_csv = next(row for row in candidate_csv if row['rule_id'] == subjective_id)
            self.assertEqual(subjective_csv['recommendation_strength'], 'subjective_weak')
            self.assertEqual(subjective_csv['source_claim_status'], 'author_opinion')
            manifest = tool.read_json(dest / 'revision_manifest.json')
            self.assertEqual(manifest['id_retained_subjective_rules'], [subjective_id])
            self.assertEqual(manifest['reclassified_observation_count'], 2)
            self.assertEqual(manifest['graph_edge_count_before'], manifest['graph_edge_count_after'])
            with self.assertRaises(FileExistsError):
                tool.apply(ROOT, inp, dest)

    def test_modified_decision_contents_rejected(self):
        decision = tool.prepare(ROOT)
        decision['items'][0]['suggested_statement_kind'] = 'general_advice'
        for item in decision['items']:
            item['human_decision'] = 'approved'; item['reviewer'] = '人工核验测试'
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'd.json'; tool.write_json(p, decision)
            with self.assertRaisesRegex(ValueError, '修订方案被修改'):
                tool.apply(ROOT, p, Path(tmp) / 'out')


if __name__ == '__main__':
    unittest.main()
