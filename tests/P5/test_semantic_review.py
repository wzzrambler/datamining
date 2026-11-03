"""Offline tests; never connect to an API or database."""
import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('p5_semantic_review_tool', ROOT / 'tools/P5/semantic_review.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class SemanticReviewTests(unittest.TestCase):
    def test_real_formal_mapping_and_review_rows_match(self):
        result = tool.build_report(ROOT)
        self.assertEqual(result['formal_mapping_records'], 291)
        self.assertEqual(result['pending_review_count'], 3)
        self.assertEqual({x['rule_id'] for x in result['items']}, set(tool.PROPOSALS))

    def test_every_suggestion_is_explicitly_pending(self):
        entries = tool.build_report(ROOT)['items']
        self.assertTrue(all(x['human_decision'] == 'pending' and not x['approved_for_export'] for x in entries))
        self.assertIn('subjective_evaluation', {x['suggested_evidence_category'] for x in entries})
        c = next(x for x in entries if x['rule_id'] == 'RULE-2b7dd1bc24a1e219d6be')
        self.assertEqual((c['suggested_statement_kind'], c['suggested_force'],
                          c['suggested_recommendation_strength']),
                         ('conditional_advice', 'recommended', 'subjective_weak'))

    def test_missing_formal_id_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            f = root / tool.FORMAL
            r = root / tool.REVIEW
            f.parent.mkdir(parents=True)
            r.parent.mkdir(parents=True)
            with f.open('w', encoding='utf-8-sig', newline='') as h:
                w = csv.DictWriter(h, fieldnames=['rule_id']); w.writeheader(); w.writerow({'rule_id': 'NOT-FOUND'})
            columns = ['rule_id', 'check_status', 'revision_status']
            with r.open('w', encoding='utf-8-sig', newline='') as h:
                w = csv.DictWriter(h, fieldnames=columns); w.writeheader()
                w.writerow({'rule_id': next(iter(tool.PROPOSALS)), 'check_status':'存疑', 'revision_status':'待修订'})
            with self.assertRaisesRegex(ValueError, '不存在'):
                tool.build_report(root)

    def test_proposals_are_not_automatic_facts(self):
        result = tool.build_report(ROOT)
        self.assertTrue(result['read_only'])
        self.assertFalse(result['official_data_modified'])
        self.assertEqual(len(result['items']), 3)
        self.assertTrue(all('source_excerpt' in x and 'reason' in x for x in result['items']))


if __name__ == '__main__':
    unittest.main()
