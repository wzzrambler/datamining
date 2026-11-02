"""Unseen synthetic cases for POS, event structure, boundaries and evaluation."""
import copy
import json
import sys
import unittest
from pathlib import Path

from test_coreference import core, evaluation, pipeline, document, mention, target

BASE=Path(__file__).resolve().parents[2]/'src/P4共指消解'
sys.path.insert(0,str(BASE))
from tokenization import Tagger
from event_candidates import spans
CONFIG=json.loads((BASE/'config.json').read_text('utf-8'))


def predict(d,mm=(),sources=None,config=None):
    return core.process_document(d,{'mentions':list(mm)},sources or {},config or CONFIG)


class V2Tests(unittest.TestCase):
    def test_lossless_repetition_whitespace_unicode(self):
        t=Tagger([],CONFIG)
        text='😀甲 湖\n湖  \t甲'
        rows=t.cut(text,13)
        self.assertEqual(''.join(r['text'] for r in rows),text)
        for row in rows:
            self.assertEqual(text[row['start']-13:row['end']-13],row['text'])

    def test_p2_injection_and_isolation(self):
        d=document('紫琉璃梦境观景廊')
        a=Tagger([mention(d,d['content'])],CONFIG)
        b=Tagger([],CONFIG)
        self.assertEqual(a.cut(d['content'])[0]['text'],d['content'])
        self.assertNotIn(d['content'],b.words)
        self.assertEqual(a.metadata()['version'],'0.42.1')
        self.assertFalse(a.metadata()['HMM'])

    def test_unknown_noun_is_not_poi(self):
        d=document('策略和智商都重要，这里安静。')
        r=target(predict(d),'这里')
        self.assertEqual(r['status'],'unresolved')
        self.assertTrue(all(c['semantic_type']=='other' for c in r['candidates'] if c['text'] in {'策略','智商'}))

    def test_quantified_animal_personification(self):
        r=target(predict(document('一头成年大象，他们非常聪明。')),'他们')
        self.assertEqual(r['antecedent']['text'],'一头成年大象')

    def test_animal_in_venue_not_independent(self):
        d=document('参观蓝色天鹅馆，它正在散步。')
        r=target(predict(d,[mention(d,'蓝色天鹅馆',subtype='venue')]),'它')
        self.assertFalse(any(c['semantic_type']=='animal' for c in r['candidates']))

    def test_sequence_arrangement(self):
        d=document('先坐小火车，再去熊猫乐园。这个安排比较省力。')
        r=target(predict(d),'这个')
        self.assertEqual(r['reference_phrase']['text'],'这个安排')
        self.assertEqual(r['status'],'resolved')
        self.assertEqual(r['antecedent']['text'],'先坐小火车，再去熊猫乐园')
        pipeline.validate_records([r],[d])

    def test_negation_and_condition_retained(self):
        d=document('如果下雨，不要走山路。这样比较安全。')
        r=target(predict(d),'这样')
        events=[c for c in r['candidates'] if c['semantic_type']=='event']
        self.assertTrue(any(c['text']=='如果下雨，不要走山路' for c in events))

    def test_route_previous_body_paragraph(self):
        d=document('蓝月湖 → 紫云台 → 南门\n这段风景漂亮。')
        sources={(d['doc_id'],i):{'kind':'body','source':'notes.content'} for i in [1,2]}
        r=target(predict(d,sources=sources),'这段')
        self.assertEqual(r['status'],'resolved')
        self.assertEqual(r['antecedent']['text'],'蓝月湖 → 紫云台 → 南门')

    def test_legacy_cross_paragraph_stays_blocked(self):
        d=document('蓝月湖 → 紫云台\n这段很漂亮。')
        r=target(predict(d),'这段')
        self.assertEqual(r['status'],'unresolved')
        self.assertIn('topic_or_unverified_continuity',r['blocked_candidate_counts'])

    def test_cross_image_prohibited(self):
        d=document('蓝月湖 → 紫云台\n这段很漂亮。')
        sources={(d['doc_id'],i):{'kind':'image','source':str(i)} for i in [1,2]}
        r=target(predict(d,sources=sources),'这段')
        self.assertEqual(r['status'],'unresolved')
        self.assertIn('source_boundary',r['blocked_candidate_counts'])

    def test_cross_comment_prohibited(self):
        d=document('蓝月湖。\n这里很漂亮。')
        sources={(d['doc_id'],i):{'kind':'comment','source':str(i)} for i in [1,2]}
        r=target(predict(d,[mention(d,'蓝月湖')],sources),'这里')
        self.assertEqual(r['status'],'unresolved')

    def test_body_previous_fallback(self):
        d=document('走到蓝月湖。\n这里很漂亮。')
        sources={(d['doc_id'],i):{'kind':'body','source':'notes.content'} for i in [1,2]}
        r=target(predict(d,[mention(d,'蓝月湖')],sources),'这里')
        self.assertTrue(any(c['text']=='蓝月湖' and c['retrieval_tier']=='previous_continuous_body_paragraph' for c in r['candidates']))

    def test_new_heading_blocks(self):
        d=document('走到蓝月湖。\n第二天：这里很漂亮。')
        sources={(d['doc_id'],i):{'kind':'body','source':'notes.content'} for i in [1,2]}
        r=target(predict(d,[mention(d,'蓝月湖')],sources),'这里')
        self.assertEqual(r['status'],'unresolved')

    def test_local_entity_prevents_previous_paragraph_fallback(self):
        d=document('先到蓝月湖。\n再到紫云台，这里风景好。')
        sources={(d['doc_id'],i):{'kind':'body','source':'notes.content'} for i in [1,2]}
        r=target(predict(d,[mention(d,'蓝月湖',entity='A'),mention(d,'紫云台',entity='B')],sources),'这里')
        self.assertEqual(r['antecedent']['text'],'紫云台')
        self.assertIn('local_candidate_available',r['blocked_candidate_counts'])

    def test_route_reference_rejects_unrelated_action(self):
        r=target(predict(document('我们看着小鸟。这个路线不合适。')),'这个')
        self.assertEqual(r['status'],'unresolved')
        self.assertTrue(any(c.get('type_rule')=='event_focus_mismatch' for c in r['candidates']))

    def test_source_kind_transition_blocks(self):
        d=document('蓝月湖 → 南门\n这段很漂亮。')
        sources={(d['doc_id'],1):{'kind':'body','source':'notes.content'},
                 (d['doc_id'],2):{'kind':'image','source':'image_a'}}
        r=target(predict(d,sources=sources),'这段')
        self.assertEqual(r['status'],'unresolved')
        self.assertIn('source_boundary',r['blocked_candidate_counts'])

    def test_long_event_limits_invalid_config(self):
        c=copy.deepcopy(CONFIG);c['v2']['event_max_chars']=0
        with self.assertRaises(ValueError):
            predict(document('去蓝月湖。这样很方便。'),config=c)

    def test_long_blocks_not_truncated(self):
        d=document('去'+('很'*650)+'蓝月湖。这个安排很好。')
        r=target(predict(d),'这个')
        self.assertFalse(any(c['semantic_type']=='event' for c in r['candidates']))

    def test_no_future_event(self):
        r=target(predict(document('这个安排很好：先去蓝月湖再去南门。')),'这个')
        self.assertEqual(r['status'],'unresolved')

    def test_same_sentence_repeated_reference_offsets(self):
        d=document('蓝月湖，这里安静，这里好看。')
        rr=predict(d,[mention(d,'蓝月湖')])
        self.assertEqual(len(set(r['reference_id'] for r in rr)),2)
        pipeline.validate_records(rr,[d])

    def test_multilink_requires_full_set(self):
        d=document('甲地→乙地。丙地→丁地。这两条路线都可以。')
        r=target(predict(d),'这')
        self.assertEqual(r['status'],'resolved')
        self.assertIsNone(r['antecedent'])
        self.assertEqual(len(r['antecedents']),2)
        pipeline.validate_records([r],[d])
        gold={'annotation_id':'NEW','doc_id':d['doc_id'],'review_status':'confirmed','is_target':'yes',
              'gold_status':'antecedent_found','_reference':r['reference'],'_antecedents':
              [{**{k:a[k] for k in ['start','end','text']},'type':'event'} for a in r['antecedents']]}
        result=evaluation.evaluate_rows([gold],[r])
        self.assertEqual(result['metrics']['multi_antecedent_success_rate'],1)
        self.assertIsNone(result['metrics']['single_antecedent_success_rate'])
        partial=copy.deepcopy(r);partial['antecedents']=partial['antecedents'][:1];partial['antecedent']=partial['antecedents'][0]
        self.assertEqual(evaluation.evaluate_rows([gold],[partial])['counts']['correct_links'],0)

    def test_boundary_overlap_never_gets_credit(self):
        d=document('先去蓝月湖，再去南门。这个安排很好。')
        r=target(predict(d),'这个')
        gold={'annotation_id':'BOUND','doc_id':d['doc_id'],'review_status':'confirmed','is_target':'yes',
              'gold_status':'no_clear_antecedent','_reference':r['reference_phrase'],'_antecedents':[]}
        result=evaluation.evaluate_rows([gold],[r])
        self.assertEqual(result['counts']['detected_targets'],0)
        self.assertEqual(len(result['boundary_only_diagnostics_not_strict_credit']),1)

    def test_check_text_supports_multiple(self):
        d=document('甲地→乙地。丙地→丁地。这两条路线都可以。')
        rr=predict(d)
        self.assertIn('甲地→乙地',core.annotated_content(d['content'],rr))


if __name__=='__main__':
    unittest.main()
