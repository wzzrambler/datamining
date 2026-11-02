"""P4 synthetic behavior tests: no production gold answers embedded."""
import copy
import contextlib
import csv
import hashlib
import importlib.util
import json
import io
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'src/P4共指消解'


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, BASE/filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = load('p4_test_core', 'core.py')
evaluation = load('p4_test_evaluation', 'evaluate.py')
sys.path.insert(0, str(BASE))
# pipeline imports core; avoid collisions with other stages' pipeline module.
pipeline = load('p4_test_pipeline', 'pipeline.py')
sys.path.pop(0)
CONFIG = json.loads((ROOT/'tests/P4/legacy_config.json').read_text('utf-8'))


def setUpModule():
    (ROOT/'.test_tmp').mkdir(parents=True, exist_ok=True)


def document(text, doc_id='UNSEEN-123'):
    paras, start = [], 0
    for i, value in enumerate(text.split('\n'), 1):
        paras.append({'i': i, 'start': start, 'end': start+len(value), 'text': value})
        start += len(value)+1
    return {'doc_id': doc_id, 'scenic_code': 'NEW', 'title': '合成验证', 'content': text, 'paragraphs': paras}


def mention(doc, word, typ='POI', subtype='spot', entity='E1', start=None):
    a = doc['content'].index(word) if start is None else start
    return {'text': word, 'start': a, 'end': a+len(word), 'type': typ, 'subtype': subtype,
            'mention_id': 'M'+str(a), 'entity_id': entity, 'canonical_name': word,
            'poi_role': 'attraction' if typ == 'POI' else ''}


def predict(doc, mentions=(), sources=None, config=None):
    return core.process_document(doc, {'mentions': list(mentions)}, sources or {}, config or CONFIG)


def target(rr, text, index=0):
    return [r for r in rr if r['reference']['text'] == text][index]


class AlgorithmTests(unittest.TestCase):
    def test_complete_poi_and_roundtrip(self):
        d = document('走进蓝月湖，那里风景很好。')
        rr = predict(d, [mention(d, '蓝月湖')])
        r = target(rr, '那里')
        self.assertEqual(r['antecedent']['text'], '蓝月湖')
        self.assertEqual(r['status'], 'resolved')
        pipeline.validate_records(rr, [d])

    def test_repeated_pronouns_are_separate(self):
        d = document('走进蓝月湖，这里安静，这里漂亮。')
        rr = predict(d, [mention(d, '蓝月湖')])
        self.assertEqual(len({r['reference_id'] for r in rr}), 2)
        self.assertEqual(len({r['reference']['start'] for r in rr}), 2)

    def test_no_forward_antecedent(self):
        d = document('这里是蓝月湖。')
        self.assertEqual(target(predict(d, [mention(d, '蓝月湖')]), '这里')['status'], 'unresolved')

    def test_discourse_not_spatial(self):
        d = document('参观蓝月湖，在这里感谢朋友。')
        r = target(predict(d, [mention(d, '蓝月湖')]), '这里')
        self.assertEqual(r['status'], 'non_entity_reference')
        self.assertIsNone(r['antecedent'])

    def test_lexical_exclusion(self):
        rr = predict(document('其它动物，其他游客，这次出行。'))
        self.assertTrue(all(r['status'] == 'excluded' for r in rr))

    def test_source_boundary(self):
        d = document('参观蓝月湖。\n这里很漂亮。')
        sources = {(d['doc_id'], 1): {'kind': 'image', 'source': 'image_a'},
                   (d['doc_id'], 2): {'kind': 'image', 'source': 'image_b'}}
        r = target(predict(d, [mention(d, '蓝月湖')], sources), '这里')
        self.assertEqual(r['status'], 'unresolved')
        self.assertEqual(r['blocked_candidate_counts']['source_boundary'], 1)

    def test_same_source_different_paragraph(self):
        d = document('参观蓝月湖。\n这里很漂亮。')
        r = target(predict(d, [mention(d, '蓝月湖')]), '这里')
        self.assertEqual(r['blocked_candidate_counts']['paragraph_boundary'], 1)

    def test_char_window_not_extended(self):
        d = document('蓝月湖'+('很'*65)+'这里安静。')
        r = target(predict(d, [mention(d, '蓝月湖')]), '这里')
        self.assertEqual(r['reason_code'], 'no_candidate')

    def test_sentence_window(self):
        d = document('蓝月湖。好。好。好。这里安静。')
        r = target(predict(d, [mention(d, '蓝月湖')]), '这里')
        self.assertEqual(r['status'], 'unresolved')

    def test_animal_noun_without_p2(self):
        r = target(predict(document('黑颈天鹅，它正在散步。')), '它')
        self.assertEqual(r['antecedent']['text'], '黑颈天鹅')
        self.assertIsNone(r['antecedent']['entity_id'])

    def test_named_animal_venue_not_animal(self):
        d = document('走进黑颈天鹅馆，它正在散步。')
        r = target(predict(d, [mention(d, '黑颈天鹅馆', subtype='venue')]), '它')
        self.assertFalse(any(c['semantic_type'] == 'animal' for c in r['candidates']))

    def test_compound_without_frequency_threshold(self):
        d = document('参观成年大象，它正在散步。')
        r = target(predict(d), '它')
        self.assertEqual(r['antecedent']['text'], '成年大象')
        self.assertIn('MODIFIER_HEAD', r['rule_ids'])

    def test_known_noun_plus_head(self):
        d = document('经过天鹅湖缆车，它很方便。')
        # Known P2 place ends directly before an allowed facility head.
        pool = core.build_mentions(d, {'mentions': [mention(d, '天鹅湖')]}, CONFIG, {})
        self.assertTrue(any(c['text'] == '天鹅湖缆车' for c in pool))
        self.assertTrue(any(c['p2_mention_id'] for c in pool))

    def test_person_does_not_choose_poi(self):
        d = document('工作人员走进蓝月湖，他们正在解说。')
        r = target(predict(d, [mention(d, '蓝月湖')]), '他们')
        self.assertEqual(r['antecedent']['text'], '工作人员')

    def test_personification_requires_evidence(self):
        d = document('看到成年大象，他们会写毛笔字。')
        r = target(predict(d), '他们')
        self.assertEqual(r['antecedent']['semantic_type'], 'animal')
        r2 = target(predict(document('看到成年大象，他们正在解说。')), '他们')
        self.assertEqual(r2['status'], 'unresolved')

    def test_close_distinct_candidates_remain_ambiguous(self):
        d = document('蓝月湖、碧云湖，这里非常安静。')
        r = target(predict(d, [mention(d, '蓝月湖', entity='A'), mention(d, '碧云湖', entity='B')]), '这里')
        self.assertEqual(r['reason_code'], 'ambiguous')

    def test_same_entity_uses_last_occurrence(self):
        d = document('蓝月湖，再次走进蓝月湖，这里安静。')
        last = d['content'].rindex('蓝月湖')
        r = target(predict(d, [mention(d, '蓝月湖'), mention(d, '蓝月湖', start=last)]), '这里')
        self.assertEqual(r['antecedent']['start'], last)

    def test_no_same_name_identity_inference(self):
        d = document('蓝月湖，再次走进蓝月湖，这里安静。')
        rr = predict(d, [mention(d, '蓝月湖', entity=''), mention(d, '蓝月湖', entity='', start=d['content'].rindex('蓝月湖'))])
        self.assertEqual(target(rr, '这里')['reason_code'], 'ambiguous')

    def test_unknown_reference_retained(self):
        r = target(predict(document('这很好。')), '这')
        self.assertEqual(r['reason_code'], 'type_unknown')

    def test_annotation_text_keeps_positions(self):
        d = document('蓝月湖，这里安静，这里漂亮。')
        rr = predict(d, [mention(d, '蓝月湖')])
        result = core.annotated_content(d['content'], rr)
        self.assertEqual(result.count('〔先行词：蓝月湖'), 2)
        self.assertEqual(d['content'], '蓝月湖，这里安静，这里漂亮。')

    def test_input_shift_and_new_ids(self):
        for prefix in ['', '😀今天出发。']:
            d = document(prefix+'参观蓝月湖，这里安静。', 'NEW-ID-'+str(len(prefix)))
            rr = predict(d, [mention(d, '蓝月湖')])
            self.assertEqual(target(rr, '这里')['antecedent']['start'], d['content'].index('蓝月湖'))
            pipeline.validate_records(rr, [d])

    def test_bad_input_and_config_rejected(self):
        d = document('蓝月湖，这里安静。')
        m = mention(d, '蓝月湖')
        m['end'] += 1
        with self.assertRaises(ValueError):
            predict(d, [m])
        config = copy.deepcopy(CONFIG)
        config['weights']['type'] = 2
        with self.assertRaises(ValueError):
            predict(d, config=config)


class EvaluationTests(unittest.TestCase):
    def gold(self, review='confirmed', origin='manual_added'):
        d = document('蓝月湖，这里安静。')
        row = {'annotation_id': 'X', 'doc_id': d['doc_id'], 'review_status': review,
               'is_target': 'yes', 'gold_status': 'antecedent_found', 'sample_origin': origin,
               '_reference': {'start': 4, 'end': 6, 'text': '这里'},
               '_antecedents': [{'start': 0, 'end': 3, 'text': '蓝月湖', 'type': 'location'}]}
        return d, row

    def test_unconfirmed_has_no_accuracy(self):
        _, row = self.gold('needs_user_review')
        result = evaluation.evaluate_rows([row], [])
        self.assertIsNone(result['metrics']['annotation_aligned_link_precision'])
        self.assertEqual(result['counts']['rows'], 0)

    def test_manual_added_miss_counts_as_failure(self):
        _, row = self.gold()
        result = evaluation.evaluate_rows([row], [])
        self.assertEqual(result['metrics']['single_antecedent_success_rate'], 0)
        self.assertEqual(result['metrics']['detection_recall_on_annotated_targets'], 0)

    def test_confirmed_correct_span(self):
        d, row = self.gold('user_confirmed')
        rr = predict(d, [mention(d, '蓝月湖')])
        result = evaluation.evaluate_rows([row], rr)
        self.assertEqual(result['metrics']['annotation_aligned_link_precision'], 1)

    def test_prediction_without_target_is_wrong(self):
        d, row = self.gold()
        row.update(is_target='no', gold_status='', _antecedents=[])
        result = evaluation.evaluate_rows([row], predict(d, [mention(d, '蓝月湖')]))
        self.assertEqual(result['metrics']['annotation_aligned_link_precision'], 0)

    def test_multiple_antecedents_not_counted_as_single_success(self):
        d, row = self.gold()
        row['_antecedents'].append({'start': 6, 'end': 8, 'text': '安静', 'type': 'other'})
        result = evaluation.evaluate_rows([row], predict(d, [mention(d, '蓝月湖')]))
        self.assertEqual(result['counts']['multi_antecedent_targets'], 1)
        self.assertEqual(result['counts']['correct_links'], 0)

    def test_unknown_human_judgment_excluded(self):
        _, row = self.gold()
        row.update(gold_status='unjudgeable', _antecedents=[])
        self.assertEqual(evaluation.evaluate_rows([row], [])['counts']['rows'], 0)

    def fixture(self, root):
        d = document('蓝月湖，这里安静。')
        p1, sample, annotations = root/'p1.json', root/'sample.json', root/'gold.csv'
        pipeline.dump(p1, [d])
        sample_doc = {**d, 'content_sha256': hashlib.sha256(d['content'].encode()).hexdigest(),
                      'paragraphs': [{'para_id': 1, 'start': 0, 'end': len(d['content']), 'text': d['content']}]}
        pipeline.dump(sample, {'source_sha256': pipeline.digest(p1), 'documents': [sample_doc]})
        row = {k: '' for k in evaluation.REQUIRED}
        row.update(annotation_id='A', doc_id=d['doc_id'], scenic_code=d['scenic_code'], para_id='1',
                   sample_origin='manual_added', reference_start='4', reference_end='6', reference_text='这里',
                   sentence_start='0', sentence_end=str(len(d['content'])), sentence_text=d['content'],
                   context_start='0', context_end=str(len(d['content'])), context_text=d['content'],
                   is_target='yes', gold_status='antecedent_found', antecedent_start='0', antecedent_end='3',
                   antecedent_text='蓝月湖', antecedent_type='location', review_status='needs_user_review')
        return d, p1, sample, annotations, row

    def write_rows(self, path, rows):
        with path.open('w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, sorted(evaluation.REQUIRED))
            w.writeheader()
            w.writerows(rows)

    def test_annotation_schema_and_inputs_untouched(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.test_tmp') as temp:
            d, p1, sample, annotations, row = self.fixture(Path(temp))
            self.write_rows(annotations, [row])
            parsed, report, _ = evaluation.load_annotations(sample, annotations, p1)
            self.assertEqual(report['errors'], [])
            self.assertTrue(report['source_files_unchanged'])
            self.assertEqual(report['confirmed_rows'], 0)
            self.assertEqual(parsed[0]['_antecedents'][0]['start'], 0)

    def test_annotation_ambiguous_scalar_and_array_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.test_tmp') as temp:
            _, p1, sample, annotations, row = self.fixture(Path(temp))
            row['antecedents_json'] = json.dumps([{'start': 0, 'end': 3, 'text': '蓝月湖', 'type': 'location'},
                                                {'start': 4, 'end': 6, 'text': '这里', 'type': 'location'}])
            self.write_rows(annotations, [row])
            _, report, _ = evaluation.load_annotations(sample, annotations, p1)
            self.assertTrue(any('互斥' in e for e in report['errors']))

    def test_annotation_wrong_offset_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.test_tmp') as temp:
            _, p1, sample, annotations, row = self.fixture(Path(temp))
            row['reference_start'] = '3'
            self.write_rows(annotations, [row])
            _, report, _ = evaluation.load_annotations(sample, annotations, p1)
            self.assertTrue(any('回切' in e for e in report['errors']))

    def test_annotation_wrong_hash_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.test_tmp') as temp:
            _, p1, sample, annotations, row = self.fixture(Path(temp))
            self.write_rows(annotations, [row])
            data = json.loads(sample.read_text('utf-8'))
            data['documents'][0]['content_sha256'] = 'wrong'
            pipeline.dump(sample, data)
            _, report, _ = evaluation.load_annotations(sample, annotations, p1)
            self.assertTrue(any('正文哈希' in e for e in report['errors']))

    def test_full_evaluation_pending_returns_null_metrics(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.test_tmp') as temp:
            root = Path(temp)
            d, p1, sample, annotations, row = self.fixture(root)
            self.write_rows(annotations, [row])
            pred = root/'pred.json'
            pipeline.dump(pred, {'input_sha256': {'p1': pipeline.digest(p1)},
                                 'records': predict(d, [mention(d, '蓝月湖')])})
            with contextlib.redirect_stdout(io.StringIO()):
                report = evaluation.run_evaluation(sample, annotations, p1, pred, root/'eval')
            self.assertEqual(report['status'], 'awaiting_confirmed_annotations')
            self.assertTrue(all(v is None for v in report['rules']['metrics'].values()))


class BatchTests(unittest.TestCase):
    def test_pipeline_no_annotation_dependency_and_deterministic(self):
        # Entire new repository fixture; no fixed production IDs/paragraphs.
        with tempfile.TemporaryDirectory(dir=ROOT/'.test_tmp') as tmp:
            root = Path(tmp)
            d = document('参观蓝月湖，这里安静。', 'FRESH-DOC')
            p1 = root/'processed/P1爬虫数据清洗/documents.json'
            src = p1.parent/'辅助资料/paragraph_sources.json'
            e = root/'processed/P2实体识别/entities.json'
            pipeline.dump(p1, [d])
            pipeline.dump(src, [])
            pipeline.dump(e, {'config_version': 'v2', 'input_sha256': pipeline.digest(p1),
                              'origin_sha256': pipeline.digest(src),
                              'documents': [{**d, 'mentions': [mention(d, '蓝月湖')]}]})
            before = {p: pipeline.digest(p) for p in [p1, src, e]}
            with contextlib.redirect_stdout(io.StringIO()):
                pipeline.run(root)
            out = root/'processed/P4共指消解/coreferences.json'
            first = out.read_bytes()
            # Even a malformed gold CSV must not affect automatic prediction.
            (root/'outputs/P4/P4人工标注样本.csv').write_text('broken,gold', encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                pipeline.run(root)
            self.assertEqual(first, out.read_bytes())
            self.assertEqual(before, {p: pipeline.digest(p) for p in before})


if __name__ == '__main__':
    unittest.main()
