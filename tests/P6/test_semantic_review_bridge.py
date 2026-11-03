"""Ensure P5 human-reviewed semantics survive into P6 advice summaries."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/P6知识图谱'))
from p6_quality.advice import summarize_advice


def sample(kind, force, review=None, nid='RULE-TEST'):
    target = {'id': 'POI-TEST', 'label': 'POI', 'properties': {'scenic_code': 'JZG'}}
    rule = {
        'id': nid, 'label': 'CONDITIONAL_RULE', 'properties': {
            'rule_id': nid, 'scenic_code': 'JZG', 'statement_kind': kind,
            'force': force, 'condition': {'text': '如果不怕晒'},
            'consequence_text': '室外比室内好玩',
            'targets': [{'kind': 'POI', 'id': 'POI-TEST'}], 'observation_ids': [],
        }
    }
    if review is not None:
        rule['properties']['semantic_review'] = review
    return {'nodes': [target, rule], 'edges': []}


class SemanticReviewBridgeTests(unittest.TestCase):
    def test_subjective_advice_is_marked_and_remains_a_recommendation(self):
        review = {'evidence_category': 'subjective_evaluation',
                  'recommendation_strength': 'subjective_weak',
                  'source_claim_status': 'author_opinion',
                  'reviewer': '小组人工审核'}
        groups, _ = summarize_advice(sample('conditional_advice', 'recommended', review), {})
        item = groups[0]['items'][0]
        self.assertEqual(item['category'], 'recommendations')
        self.assertEqual(item['semantic_review'], review)
        self.assertIn('author_opinion_not_objective_fact', item['review_flags'])

    def test_descriptive_observation_is_not_marked_as_an_action(self):
        review = {'evidence_category': 'time_dependent_observation',
                  'recommendation_strength': 'not_applicable',
                  'source_claim_status': 'source_observation_not_independently_verified'}
        groups, _ = summarize_advice(sample('conditional_fact', 'descriptive', review), {})
        item = groups[0]['items'][0]
        self.assertEqual(item['category'], 'conditional_facts')
        self.assertIn('source_observation_not_independently_verified', item['review_flags'])
        self.assertEqual(item['semantic_review']['evidence_category'], 'time_dependent_observation')

    def test_invalid_subjective_tag_cannot_be_attached_to_fact(self):
        review = {'evidence_category': 'subjective_evaluation',
                  'recommendation_strength': 'subjective_weak',
                  'source_claim_status': 'author_opinion'}
        with self.assertRaisesRegex(ValueError, 'Invalid subjective'):
            summarize_advice(sample('conditional_fact', 'descriptive', review), {})

    def test_reviewed_and_unreviewed_records_do_not_merge(self):
        review = {'evidence_category': 'subjective_evaluation',
                  'recommendation_strength': 'subjective_weak',
                  'source_claim_status': 'author_opinion'}
        graph = sample('conditional_advice', 'recommended', review)
        other = sample('conditional_advice', 'recommended', nid='RULE-OTHER')['nodes'][-1]
        graph['nodes'].append(other)
        groups, _ = summarize_advice(graph, {})
        self.assertEqual(groups[0]['summary_item_count'], 2)
        self.assertEqual(sum('semantic_review' in item for item in groups[0]['items']), 1)

    def test_legacy_records_keep_their_existing_schema(self):
        groups, _ = summarize_advice(sample('conditional_advice', 'recommended'), {})
        self.assertNotIn('semantic_review', groups[0]['items'][0])


if __name__ == '__main__':
    unittest.main()
