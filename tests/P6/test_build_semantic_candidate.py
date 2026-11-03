"""Offline tests for the isolated P5 plan-B -> P6 candidate builder."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[2] / 'tools/P6/build_semantic_candidate.py'
SPEC = importlib.util.spec_from_file_location('p6_semantic_candidate_builder', SCRIPT)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


class CandidateSafetyTests(unittest.TestCase):
    def test_output_is_new_direct_child_of_candidate_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / 'outputs/P6'
            base.mkdir(parents=True)
            accepted = base / 'plan_b_v1'
            self.assertEqual(builder.validate_output(accepted, base), accepted)
            for rejected in (base, base / 'nested/subdir', Path(tmp) / 'processed/P6/cleaned-v1'):
                with self.assertRaises(ValueError):
                    builder.validate_output(rejected, base)
            accepted.mkdir()
            with self.assertRaises(FileExistsError):
                builder.validate_output(accepted, base)

    def test_approved_decision_hash_must_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            decisions = Path(tmp) / 'decisions.json'
            decisions.write_bytes(b'{"items":[]}')
            expected = hashlib.sha256(decisions.read_bytes()).hexdigest()
            builder.validate_decisions({'decisions_sha256': expected}, decisions)
            decisions.write_bytes(b'{"items":[1]}')
            with self.assertRaises(ValueError):
                builder.validate_decisions({'decisions_sha256': expected}, decisions)

    def test_reclassified_and_subjective_labels_are_required(self):
        old, new, subjective = 'RULE-old', 'RULE-new', 'RULE-subjective'
        graph = {'nodes': [
            {'id': new, 'label': 'CONDITIONAL_RULE', 'properties': {
                'statement_kind': 'conditional_fact', 'force': 'descriptive',
                'semantic_review': {'recommendation_strength': 'not_applicable',
                                    'source_claim_status': 'source_observation_not_independently_verified'}}},
            {'id': subjective, 'label': 'CONDITIONAL_RULE', 'properties': {
                'statement_kind': 'conditional_advice',
                'semantic_review': {'recommendation_strength': 'subjective_weak',
                                    'source_claim_status': 'author_opinion'}}},
        ]}
        summaries = [{'items': [
            {'source_record_ids': [new], 'category': 'conditional_facts',
             'semantic_review': {'recommendation_strength': 'not_applicable'}},
            {'source_record_ids': [subjective], 'category': 'recommendations',
             'semantic_review': {'recommendation_strength': 'subjective_weak',
                                 'source_claim_status': 'author_opinion'},
             'review_flags': ['author_opinion_not_objective_fact']},
        ]}]
        manifest = {'rule_id_map': {old: new}, 'id_retained_subjective_rules': [subjective]}
        self.assertEqual(builder.check_semantics(graph, summaries, manifest)['reclassified_rule_ids'], {old: new})
        summaries[0]['items'][1]['review_flags'] = []
        with self.assertRaisesRegex(ValueError, 'Subjective P6 summary'):
            builder.check_semantics(graph, summaries, manifest)

    def test_builder_writes_only_isolated_candidate_and_never_production(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_root = root / 'outputs/P6'
            output = output_root / 'plan_b_v1'
            p5 = root / 'outputs/P5/candidate'
            upstream = root / 'upstream'
            docs = root / 'documents.json'
            protected = root / 'processed/P6知识图谱/cleaned-v1/graph.json'
            write_json(protected, {'old_release': True})
            before = protected.read_bytes()
            write_json(p5 / 'revision_manifest.json', {'decisions_sha256': 'mock',
                       'rule_id_map': {}, 'id_retained_subjective_rules': []})
            write_json(p5 / 'graph.json', {'nodes': [], 'edges': []})
            write_json(p5 / 'routes.json', [])
            write_json(upstream / 'raw_observations.json', [])
            write_json(docs, [])
            mock_preview = types.ModuleType('preview_p5_semantics')
            mock_preview.UPSTREAM = upstream
            mock_preview.DOCUMENTS = docs
            mock_preview.preview = lambda _: {'p5_rules': 0, 'p6_retained_routes': 0,
                                                'p6_nodes': 0, 'p6_edges': 0, 'p6_advice_groups': 0}
            mock_routes = types.ModuleType('clean_routes')
            mock_routes.curate = lambda *_: ({'nodes': [], 'edges': []}, [], [], [], [], {'after': {}})
            mock_quality = types.ModuleType('quality_pipeline')
            mock_quality.compact = lambda x: json.dumps(x, sort_keys=True)
            mock_quality.build = lambda *_a, **_k: (
                {'nodes': [], 'edges': [], 'p6_quality': {'stages': []}}, [], [], {})
            with (patch.object(builder, 'OUTPUT_ROOT', output_root),
                  patch.object(builder, 'validate_decisions'),
                  patch.object(builder, 'check_semantics', return_value={}),
                  patch.dict(sys.modules, {'preview_p5_semantics': mock_preview,
                                           'clean_routes': mock_routes, 'quality_pipeline': mock_quality})):
                result = builder.create_candidate(p5, output)
            self.assertEqual(result['status'], 'candidate_only_NOT_a_P6_release')
            self.assertTrue((output / 'graph.json').is_file())
            self.assertTrue((output / 'candidate_manifest.json').is_file())
            self.assertFalse((output / 'quality_manifest.json').exists())
            self.assertFalse((output / 'release.json').exists())
            self.assertEqual(protected.read_bytes(), before)
            with self.assertRaises(FileExistsError):
                builder.validate_output(output, output_root)


if __name__ == '__main__':
    unittest.main()
