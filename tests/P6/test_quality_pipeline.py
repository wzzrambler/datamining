import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/P6知识图谱'))
from p6_quality.advice import summarize_advice
from quality_pipeline import build


def fixture():
    nodes = [dict(id='T', label='TRANSPORT', properties={'name':'步行','scenic_code':None}),
             dict(id='A', label='POI', properties={'name':'A','scenic_code':'JZG'}),
             dict(id='B', label='POI', properties={'name':'B','scenic_code':'JZG'})]
    def rule(nid, scene='JZG', targets=None, condition=None, force='recommended', text='提前到达', kind='conditional_advice', **extra):
        nodes.append(dict(id=nid,label='CONDITIONAL_RULE',properties=dict(rule_id=nid,scenic_code=scene,
            targets=targets if targets is not None else [{'kind':'TRANSPORT','id':'T'}],condition=condition,
            force=force,consequence_text=text,statement_kind=kind,observation_ids=[],**extra)))
    return nodes, rule


class QualityPipelineTests(unittest.TestCase):
    def test_same_action_different_condition_force_and_scene_do_not_merge(self):
        nodes, rule = fixture()
        rule('R1',condition={'text':'下雨','logic':'single'})
        rule('R2',condition={'text':'晴天','logic':'single'})
        rule('R3',force='prohibited')
        rule('R4',scene='ZHCL')
        groups,_=summarize_advice({'nodes':nodes,'edges':[]},{})
        self.assertEqual(len(groups),2)
        self.assertEqual(sorted(g['summary_item_count'] for g in groups),[1,3])

    def test_joint_targets_and_missing_consequence_preserved(self):
        nodes,rule=fixture()
        targets=[{'kind':'POI','id':'A'},{'kind':'POI','id':'B'}]
        rule('R',targets=targets,text=None,kind='incomplete_condition',force='unspecified',condition={'text':'下雨'})
        groups,_=summarize_advice({'nodes':nodes,'edges':[]},{})
        for g in groups:
            item=g['items'][0]
            self.assertIsNone(item['text'])
            self.assertEqual(item['joint_targets'],targets)
            self.assertEqual(item['category'],'incomplete')

    def test_unlocated_and_excluded_fragment_not_promoted_to_scene_wide(self):
        nodes,rule=fixture()
        rule('R1',targets=[])
        rule('R2',targets=[],scope_status='excluded_route_fragment_scope')
        groups,audit=summarize_advice({'nodes':nodes,'edges':[]},{})
        self.assertEqual(len(groups),2)
        self.assertTrue(all(g['target_id'] is None for g in groups))
        self.assertTrue(all(g['scope_meaning']=='source_scene_unlocated_not_global' for g in groups))
        self.assertEqual(len(audit['quarantined_records']),1)

    def test_exact_merge_keeps_sources_and_decimal_values(self):
        nodes,rule=fixture()
        rule('R1',text='身高1.0米。')
        rule('R2',text='身高1.0米')
        rule('R3',text='身高10米')
        groups,_=summarize_advice({'nodes':nodes,'edges':[]},{})
        self.assertEqual(groups[0]['summary_item_count'],2)
        merged=next(i for i in groups[0]['items'] if len(i['source_record_ids'])==2)
        self.assertEqual(merged['source_record_ids'],['R1','R2'])

    def test_production_rebuild_deterministic_and_every_derived_id_registered(self):
        p=ROOT/'processed/P6知识图谱/cleaned-v1'
        graph=json.loads((p/'graph.json').read_text('utf-8'))
        run=(ROOT/'processed/P5条件建议抽取/ACTIVE_RUN').read_text('utf-8').strip()
        obs={r['record_id']:r for r in json.loads((ROOT/'processed/P5条件建议抽取/runs'/run/'raw_observations.json').read_text('utf-8'))}
        docs={d['doc_id']:d for d in json.loads((ROOT/'processed/P1爬虫数据清洗/documents.json').read_text('utf-8'))}
        rebuilt=build(graph,obs,docs)[0]
        self.assertEqual(graph,rebuilt)
        nodes={n['id']:n for n in graph['nodes']}
        for n in nodes.values():
            if n['label']=='ADVICE_SUMMARY':
                self.assertTrue(all(nodes[r]['label'] in {'FACT','CONDITIONAL_RULE'} for r in n['properties']['source_record_ids']))
        self.assertEqual(sum(n['label']=='ROUTE' for n in nodes.values()),29)
        p5=json.loads((ROOT/'processed/P5条件建议抽取/runs'/run/'integrated/graph.json').read_text('utf-8'))
        self.assertFalse(any(n['label'] in {'TIME_SUMMARY','ADVICE_SUMMARY'} for n in p5['nodes']))


if __name__=='__main__':
    unittest.main()
